"""One command to retrain the minimap CNN fast and keep it only if the benches say it is better.

    python tools/det_train_fast.py --minutes 8            # fine-tune the app model, gate, report
    python tools/det_train_fast.py --minutes 8 --install  # ... and install it when it wins

Steps (each timed in the report):

1. **Data.** The synthetic + real-art dataset is rendered once by every core
   (``training/fast_train.build_cache``, compressed ``.npz`` in ``training/data``, reused while
   the generators do not change) and loaded in RAM. The REAL labelled frames of
   ``tests/fixtures/real_cases/*`` (labels from ``ground_truth.json`` = reviewed LCU truth) are
   mixed into every batch (``--real-frac``, default 25 % of each batch for ~0.5 % of the
   images: heavy weighting). Their ``unsure`` zones are ignored by the heatmap loss (target
   0.999: the focal loss neither rewards nor punishes a detection there). One frame in three
   of each case is held out for early stopping (``--val-every``).
2. **Model.** Fine-tuned from the app's ONNX weights (``--from current``: BatchNorm folded into
   the convolutions at export, so the BN layers start as identities and keep frozen
   statistics), or ``--from scratch``. CUDA when torch sees a GPU (``--device``), else CPU.
3. **Early stopping** on the held-out real frames (F1 of the raw CNN at the model threshold,
   detections in ``unsure`` zones not counted), EMA weights, best state kept.
4. **Export** (opset 17, ``training/export_onnx`` checks) to ``<out>/minimap_detector.onnx``.
5. **Gate**: ``det_gym.py --quick`` (main suite + real crops + real cases, full engine path)
   with the candidate (``TREEAI_DET_MODEL``) vs the current model. Kept only when the quality
   score is higher, synthetic precision does not drop by more than 0.005 and no real case /
   the real crops lose recall + precision. ``--install`` then copies it into
   ``treeaicoach/assets/model`` (the meta keeps the old metrics + a ``fine_tune`` block).

Speed (this 4-core container, torch 2.14 CPU, model 97 k params, batch 64 of 96 px crops):
see training/README.md "Fast fine-tune".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

log = logging.getLogger("det_train_fast")
MODEL_DIR = ROOT / "treeaicoach" / "assets" / "model"
CASES_DIR = ROOT / "tests" / "fixtures" / "real_cases"
DEFAULT_R = 0.045
CFG = {"width": 0.5, "fpn_channels": 48, "head_channels": 16}
IGNORE = 0.999              # heatmap target in "unsure" zones: ~no loss either way


# ======================================================================================
# real frames
# ======================================================================================

def _radius_hints(img: np.ndarray, labels: list[dict]) -> list[float]:
    """Icon radius per label from the current CNN (nearest detection), median fallback."""
    try:
        from treeaicoach.detector import OnnxDetector

        det = _radius_hints.det = getattr(_radius_hints, "det", None) or OnnxDetector(threads=2)
        dets = det.detect(img)
    except Exception:
        dets = []
    rs: list[float | None] = []
    for lab in labels:
        best, bd = None, 0.03
        for d in dets:
            dd = math.hypot(d.u - lab["u"], d.v - lab["v"])
            if dd < bd:
                best, bd = d.r, dd
        rs.append(best)
    known = [r for r in rs if r]
    med = float(np.median(known)) if known else DEFAULT_R
    med = min(0.07, max(0.03, med))
    return [min(0.07, max(0.03, r)) if r else med for r in rs]


def load_real_cases(size: int, cases_dir: Path = CASES_DIR, val_mod: int = 3
                    ) -> tuple[list[tuple[np.ndarray, list[dict], list[dict]]], list[tuple[np.ndarray, list[dict], list[dict]]]]:
    """(train, val) lists of (image size x size BGR, labels, unsure zones).

    ``cases_dir``: a folder of case folders, or ONE case folder (``ground_truth.json`` inside,
    e.g. a ``treeaicoach.det_collect`` export). Frames flagged ``needs_review`` (collected
    uncertain / disputed frames not reviewed yet) are skipped. A frame with ``focus`` discs
    (collected hard negatives) is trained only inside them (zones with ``"focus": True``, see
    :func:`_ignore_mask`) and never used for validation."""
    train, val = [], []
    cases_dir = Path(cases_dir)
    paths = [cases_dir / "ground_truth.json"] if (cases_dir / "ground_truth.json").is_file() \
        else sorted(cases_dir.glob("*/ground_truth.json"))
    for gt_path in paths:
        try:
            data = json.loads(gt_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        images = data.get("images") or {}
        items = sorted(images.values(), key=lambda e: str(e.get("file", ""))) if isinstance(images, dict) else images
        for k, e in enumerate(items):
            if e.get("needs_review"):
                continue
            img = cv2.imread(str(gt_path.parent / str(e.get("file", ""))))
            if img is None:
                continue
            labs = [{"u": float(c["u"]), "v": float(c["v"]),
                     "cls": "enemy" if c.get("team") == "enemy" else "ally"}
                    for c in e.get("champions") or [] if "u" in c and "v" in c]
            for lab, r in zip(labs, _radius_hints(img, labs)):
                lab["r"] = r
            uns = [{"u": float(z["u"]), "v": float(z["v"]), "r": float(z.get("r", 0.05))}
                   for z in e.get("unsure") or [] if "u" in z and "v" in z]
            focus = [{"u": float(z["u"]), "v": float(z["v"]), "r": float(z.get("r", 0.06)), "focus": True}
                     for z in e.get("focus") or [] if "u" in z and "v" in z]
            small = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
            if focus:
                train.append((small, labs, uns + focus))
                continue
            (val if k % val_mod == val_mod - 1 else train).append((small, labs, uns))
    return train, val


def _ignore_mask(hm: np.ndarray, zones: list[dict], x0: float, y0: float, crop: float, full: float) -> None:
    """Raise the heatmap target to IGNORE inside the unsure discs (crop coordinates); with
    ``focus`` zones (collected hard negatives) everything OUTSIDE them is ignored too."""
    hs = hm.shape[-1]
    yy, xx = np.mgrid[0:hs, 0:hs].astype(np.float32) + 0.5
    foc = [z for z in zones if z.get("focus")]
    if foc:
        keep = np.zeros(hm.shape[-2:], bool)
        for z in foc:
            cu, cv = (z["u"] * full - x0) / crop * hs, (z["v"] * full - y0) / crop * hs
            rr = z["r"] * full / crop * hs
            keep |= (xx - cu) ** 2 + (yy - cv) ** 2 <= rr * rr
        hm[0][~keep] = np.maximum(hm[0][~keep], IGNORE)
    for z in zones:
        if z.get("focus"):
            continue
        cu, cv = (z["u"] * full - x0) / crop * hs, (z["v"] * full - y0) / crop * hs
        rr = z["r"] * full / crop * hs
        m = (xx - cu) ** 2 + (yy - cv) ** 2 <= rr * rr
        if m.any():
            hm[0][m] = np.maximum(hm[0][m], IGNORE)


def real_batch(real: list, idx: np.ndarray, rng: np.random.Generator, size: int, crop: int):
    """Like fast_train.make_batch for the real frames (flips + colour, crops, unsure ignored)."""
    import torch

    from training.dataset import STRIDE, encode_targets

    c = crop if 0 < crop < size else size
    xs = np.empty((len(idx), c, c, 3), np.uint8)
    tg: dict[str, list[np.ndarray]] = {}
    for k, i in enumerate(idx):
        img, labs, uns = real[int(i)]
        labs = [dict(l) for l in labs]
        uns = [dict(z) for z in uns]
        if rng.random() < 0.5:
            img = img[:, ::-1]
            for l in labs + uns:
                l["u"] = 1.0 - l["u"]
        if rng.random() < 0.6:
            img = cv2.convertScaleAbs(np.ascontiguousarray(img), alpha=rng.uniform(0.8, 1.2), beta=rng.uniform(-15, 15))
        anchors = [z for z in uns if z.get("focus")] or labs   # hard negatives: crop on them
        if anchors and (rng.random() < 0.7 or anchors is not labs):
            l = anchors[int(rng.integers(len(anchors)))]
            cx, cy = l["u"] * size + rng.uniform(-0.4, 0.4) * c, l["v"] * size + rng.uniform(-0.4, 0.4) * c
            x0, y0 = int(np.clip(cx - c / 2, 0, size - c)), int(np.clip(cy - c / 2, 0, size - c))
        else:
            x0, y0 = (int(v) for v in rng.integers(0, size - c + 1, 2))
        out = []
        for l in labs:
            u, v = (l["u"] * size - x0) / c, (l["v"] * size - y0) / c
            if 0.0 <= u < 1.0 and 0.0 <= v < 1.0:
                out.append({**l, "u": u, "v": v, "r": l["r"] * size / c})
        xs[k] = img[y0:y0 + c, x0:x0 + c]
        t = encode_targets(out, c, STRIDE)
        t["radius"] = t["radius"] * np.float32(c / size)
        _ignore_mask(t["heatmap"], uns, x0, y0, c, size)
        for name, v in t.items():
            tg.setdefault(name, []).append(v)
    x = torch.from_numpy(xs[..., ::-1].copy()).permute(0, 3, 1, 2).float().div_(255.0)
    return x, {k: torch.from_numpy(np.stack(v)) for k, v in tg.items()}


# ======================================================================================
# warm start from the ONNX weights
# ======================================================================================

def load_onnx_weights(model: Any, onnx_path: Path, size: int) -> None:
    """Copy the (BN-folded) ONNX convolutions into ``model``: each Conv+BN pair gets the folded
    kernel and a BN that is an identity plus the folded bias. Conv order = forward order."""
    import onnx
    import torch
    from onnx import numpy_helper
    from torch import nn

    from training.model import ScaledConv1x1

    m = onnx.load(str(onnx_path))
    init = {i.name: numpy_helper.to_array(i) for i in m.graph.initializer}
    for n in m.graph.node:
        if n.op_type == "Constant" and n.attribute:
            init[n.output[0]] = numpy_helper.to_array(n.attribute[0].t)
    convs = [n for n in m.graph.node if n.op_type == "Conv"]
    order: list[nn.Conv2d] = []
    hooks = [mod.register_forward_hook(lambda mo, i, o: order.append(mo))
             for mod in model.modules() if isinstance(mod, nn.Conv2d)]
    model.eval()
    with torch.no_grad():
        model(torch.zeros(1, 3, size, size))
    for h in hooks:
        h.remove()
    if len(order) != len(convs):
        raise ValueError(f"{len(convs)} ONNX convs vs {len(order)} torch convs")
    bn_after: dict[int, nn.BatchNorm2d] = {}
    for mod in model.modules():
        if isinstance(mod, nn.Sequential):
            ch = list(mod.children())
            for a, b in zip(ch, ch[1:]):
                if isinstance(a, nn.Conv2d) and isinstance(b, nn.BatchNorm2d):
                    bn_after[id(a)] = b
    with torch.no_grad():
        for node, conv in zip(convs, order):
            w = torch.from_numpy(init[node.input[1]].copy())
            b = torch.from_numpy(init[node.input[2]].copy()) if len(node.input) > 2 else torch.zeros(w.shape[0])
            if tuple(w.shape) != tuple(conv.weight.shape):
                raise ValueError(f"shape {tuple(w.shape)} != {tuple(conv.weight.shape)}")
            scale = conv.scale if isinstance(conv, ScaledConv1x1) else 1.0
            conv.weight.copy_(w / scale)
            bn = bn_after.get(id(conv))
            if bn is not None:
                bn.running_mean.zero_()
                bn.running_var.fill_(1.0)
                bn.weight.fill_(math.sqrt(1.0 + bn.eps))
                bn.bias.copy_(b)
            elif conv.bias is not None:
                conv.bias.copy_(b / scale)
            elif b.abs().max() > 0:
                raise ValueError("ONNX conv has a bias the torch conv cannot hold")


def freeze_bn(model: Any) -> None:
    from torch import nn

    for mod in model.modules():
        if isinstance(mod, nn.BatchNorm2d):
            mod.eval()


# ======================================================================================
# evaluation on held-out real frames (raw CNN)
# ======================================================================================

def eval_real(model: Any, val: list, size: int, threshold: float, device: str) -> dict[str, float]:
    import torch

    from treeaicoach.detector import decode_outputs

    tp = fp = fn = 0
    model.eval()
    with torch.no_grad():
        for img, labs, uns in val:
            x = torch.from_numpy(img[..., ::-1].copy()).permute(2, 0, 1)[None].float().div_(255.0).to(device)
            outs = [t.cpu().numpy() for t in model(x)]
            dets = decode_outputs(*outs, threshold=threshold, stride=4, input_size=size, max_det=24)
            used = set()
            for d in dets:
                j = min(range(len(labs)), key=lambda k: math.hypot(labs[k]["u"] - d.u, labs[k]["v"] - d.v),
                        default=None)
                if j is not None and j not in used and math.hypot(labs[j]["u"] - d.u, labs[j]["v"] - d.v) < 0.035:
                    used.add(j)
                    tp += 1
                elif any(math.hypot(z["u"] - d.u, z["v"] - d.v) < z["r"] + 0.02 for z in uns):
                    continue
                elif j is not None and math.hypot(labs[j]["u"] - d.u, labs[j]["v"] - d.v) < 0.035:
                    continue          # second detection on a labelled (stacked) icon: neutral
                else:
                    fp += 1
            fn += len(labs) - len(used)
    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    return {"p": p, "r": r, "f1": 2 * p * r / max(1e-9, p + r), "tp": tp, "fp": fp, "fn": fn}


# ======================================================================================
# gate (full engine path)
# ======================================================================================

def gym(model: Path | None, out_json: Path, timeout: float = 1800) -> dict:
    env = dict(os.environ)
    if model is not None:
        env["TREEAI_DET_MODEL"] = str(model)
    else:
        env.pop("TREEAI_DET_MODEL", None)
    subprocess.run([sys.executable, str(ROOT / "tools" / "det_gym.py"), "--quick", "--no-record", "--json",
                    str(out_json)], cwd=ROOT, env=env, timeout=timeout, check=True, capture_output=True)
    return json.loads(out_json.read_text(encoding="utf-8"))


def summarize(out: dict) -> dict:
    sys.path.insert(0, str(ROOT / "tools"))
    from det_gym import score

    real = out.get("real") or None
    s = {"quality": score(out["total"], real, cost=False), "prec": out["total"]["prec"],
         "rec": out["total"]["rec"]}
    if real:
        s["real"] = real["rec"] + real["prec"]
    for c, m in (out.get("real_cases") or {}).items():
        s[f"case:{c}"] = m["rec"] + m["prec"]
    return s


def gate(base: dict, cand: dict, prec_tol: float = 0.005) -> tuple[bool, list[str]]:
    why = []
    if cand["quality"] <= base["quality"]:
        why.append(f"quality {cand['quality']:.4f} <= {base['quality']:.4f}")
    if cand["prec"] < base["prec"] - prec_tol:
        why.append(f"precision {cand['prec']:.4f} < {base['prec']:.4f} - {prec_tol}")
    for k, v in base.items():
        if (k == "real" or k.startswith("case:")) and cand.get(k, -1.0) < v - 1e-9:
            why.append(f"{k} rec+prec {cand.get(k, -1.0):.3f} < {v:.3f}")
    return not why, why


# ======================================================================================
# main
# ======================================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--minutes", type=float, default=8.0, help="training budget")
    p.add_argument("--from", dest="init", choices=("current", "scratch"), default="current")
    p.add_argument("--n-synth", type=int, default=3000)
    p.add_argument("--n-real", type=int, default=3000, help="real-art composites (not the real frames)")
    p.add_argument("--size", type=int, default=160)
    p.add_argument("--crop", type=int, default=96)
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--real-frac", type=float, default=0.25, help="share of each batch from the real frames")
    p.add_argument("--lr", type=float, default=0.0)
    p.add_argument("--threads", type=int, default=0, help="torch CPU threads (0 = cores)")
    p.add_argument("--device", default="auto", help="auto | cpu | cuda")
    p.add_argument("--val-every", type=float, default=45.0, help="seconds between early-stopping evals")
    p.add_argument("--patience", type=int, default=4)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default="training/runs/fast_ft")
    p.add_argument("--no-gate", action="store_true", help="train + export only")
    p.add_argument("--install", action="store_true", help="install the model when the gate passes")
    p.add_argument("--case-backgrounds", action="store_true",
                   help="also composite on the real cases' backgrounds (icons + unsure zones painted out)")
    p.add_argument("--extra-cases", action="append", default=[],
                   help="more case folders (e.g. det_collect exports of the user's games), repeatable")
    p.add_argument("--max-steps", type=int, default=0, help="(tests) stop after this many steps")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    import torch

    from training import fast_train
    from training.dataset import STRIDE
    from training.export_onnx import check_graph, export_bytes, verify_onnxruntime
    from training.model import build_model
    from training.train import ModelEMA, compute_losses, param_groups

    dev = args.device
    if dev == "auto":
        dev = "cuda" if torch.cuda.is_available() else "cpu"
    torch.set_num_threads(args.threads or os.cpu_count() or 2)
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    out = (ROOT / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rep: dict[str, Any] = {"device": dev, "args": vars(args)}

    t = time.perf_counter()
    bgs: tuple = ()
    if args.case_backgrounds:   # idea 36: the real cases' backgrounds for the real-art composites
        from training import real_art
        bgs = tuple(real_art.BACKGROUND_SHOTS) + tuple(
            f"case:{p.parent.name}" for p in sorted(CASES_DIR.glob("*/ground_truth.json")))
    imgs, labels, gst = fast_train.build_cache(args.n_synth, args.n_real, args.size, 0, 0, bgs)
    real_tr, real_val = load_real_cases(args.size)
    for extra in args.extra_cases or []:   # user-collected frames (treeaicoach.det_collect exports)
        tr_x, val_x = load_real_cases(args.size, Path(extra))
        real_tr += tr_x
        real_val += val_x
    rep["data_s"] = round(time.perf_counter() - t, 1)
    rep["data"] = {"synthetic": len(imgs), "real_train": len(real_tr), "real_val": len(real_val),
                   "rendered": not gst.get("cached")}
    log.info("data: %d synthetic, %d / %d real frames (train / val) in %.1f s", len(imgs), len(real_tr),
             len(real_val), rep["data_s"])

    meta = json.loads((MODEL_DIR / "model_meta.json").read_text(encoding="utf-8"))
    thr = float(meta.get("threshold", 0.4))
    model = build_model(CFG)
    if args.init == "current":
        load_onnx_weights(model, MODEL_DIR / "minimap_detector.onnx", args.size)
    model = model.to(dev).to(memory_format=torch.channels_last)
    lr = args.lr or (1.5e-3 if args.init == "current" else 4e-3)
    ema = ModelEMA(model, decay=0.995 if args.init == "current" else 0.998, tau=100.0)
    opt = torch.optim.AdamW(param_groups(model, 5e-4), lr=lr)
    weights = {"hm": 1.0, "off": 1.0, "rad": 1.0, "cls": 1.0}

    base_val = eval_real(ema.module, real_val, args.size, thr, dev)
    best = (base_val["f1"], 0, {k: v.detach().clone() for k, v in ema.module.state_dict().items()})
    log.info("start: real val F1 %.3f (P %.3f R %.3f)", base_val["f1"], base_val["p"], base_val["r"])
    rep["val_start"] = base_val
    n_real = max(0, min(args.batch - 1, int(round(args.batch * args.real_frac)))) if real_tr else 0
    budget = args.minutes * 60.0
    t0 = time.perf_counter()
    t_batch = t_model = 0.0
    step, bad, next_eval = 0, 0, args.val_every
    history = []
    while True:
        el = time.perf_counter() - t0
        if el >= budget or (args.max_steps and step >= args.max_steps):
            break
        prog = el / budget
        f = (0.2 + 0.8 * prog / 0.05) if prog < 0.05 else 0.02 + 0.98 * 0.5 * (1 + math.cos(math.pi * (prog - 0.05) / 0.95))
        for g in opt.param_groups:
            g["lr"] = lr * f
        tb = time.perf_counter()
        x, tg = fast_train.make_batch(imgs, labels, rng.integers(0, len(imgs), args.batch - n_real), rng,
                                      args.size, crop=args.crop)
        if n_real:
            xr, tr = real_batch(real_tr, rng.integers(0, len(real_tr), n_real), rng, args.size, args.crop)
            x = torch.cat([x, xr])
            tg = {k: torch.cat([tg[k], tr[k]]) for k in tg}
        tm = time.perf_counter()
        t_batch += tm - tb
        x = x.to(dev).contiguous(memory_format=torch.channels_last)
        tg = {k: v.to(dev) for k, v in tg.items()}
        model.train()
        if args.init == "current":
            freeze_bn(model)
        losses = compute_losses(model.forward_train(x), tg, weights, args.size, STRIDE)
        opt.zero_grad(set_to_none=True)
        losses["total"].backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()
        ema.update(model)
        t_model += time.perf_counter() - tm
        step += 1
        if time.perf_counter() - t0 >= next_eval:
            next_eval += args.val_every
            v = eval_real(ema.module, real_val, args.size, thr, dev)
            history.append({"step": step, "s": round(time.perf_counter() - t0, 1), **v})
            log.info("step %d (%.0f s): loss %.3f, real val F1 %.3f (P %.3f R %.3f)", step,
                     time.perf_counter() - t0, float(losses["total"].detach()), v["f1"], v["p"], v["r"])
            if v["f1"] > best[0] + 1e-4:
                best, bad = (v["f1"], step, {k: w.detach().clone() for k, w in ema.module.state_dict().items()}), 0
            else:
                bad += 1
                if bad >= args.patience:
                    log.info("early stop (no real-val gain for %d evals)", bad)
                    break
    v = eval_real(ema.module, real_val, args.size, thr, dev)
    history.append({"step": step, "s": round(time.perf_counter() - t0, 1), **v})
    if v["f1"] > best[0] + 1e-4:
        best = (v["f1"], step, {k: w.detach().clone() for k, w in ema.module.state_dict().items()})
    train_s = time.perf_counter() - t0
    epoch_steps = (len(imgs) + len(real_tr)) / args.batch
    rep.update({"steps": step, "train_s": round(train_s, 1), "best_step": best[1], "val_best": best[0],
                "val_history": history,
                "ms_per_step": round(1000 * train_s / max(1, step), 1),
                "ms_batch_prep": round(1000 * t_batch / max(1, step), 1),
                "ms_model": round(1000 * t_model / max(1, step), 1),
                "s_per_epoch": round(train_s / max(1, step) * epoch_steps, 1)})
    log.info("trained %d steps in %.0f s (%.0f ms/step: batch %.0f + model %.0f; %.1f s/epoch); best real F1 "
             "%.3f at step %d (start %.3f)", step, train_s, rep["ms_per_step"], rep["ms_batch_prep"],
             rep["ms_model"], rep["s_per_epoch"], best[0], best[1], base_val["f1"])

    # ---- export --------------------------------------------------------------------
    final = build_model(CFG)
    final.load_state_dict({k: v.cpu() for k, v in best[2].items()})
    final.eval()
    data = export_bytes(final, args.size)
    check_graph(data)
    diff = verify_onnxruntime(data, final, [rng.random((1, 3, args.size, args.size), dtype=np.float32)
                                            for _ in range(2)])
    cand = out / "minimap_detector.onnx"
    cand.write_bytes(data)
    new_meta = dict(meta)
    new_meta.update({"version": f"{meta.get('version', 'minimapnet')}-ft{time.strftime('%Y%m%d')}",
                     "exported": time.strftime("%Y-%m-%d"), "onnx_max_abs_diff": float(diff),
                     "size_bytes": len(data),
                     "fine_tune": {"recipe": "tools/det_train_fast.py", "from": args.init, "steps": step,
                                   "train_s": rep["train_s"], "real_frames": len(real_tr), "real_frac": args.real_frac,
                                   "real_val_f1": [round(base_val["f1"], 4), round(best[0], 4)],
                                   "device": dev}})
    (out / "model_meta.json").write_text(json.dumps(new_meta, indent=1), encoding="utf-8")
    rep["candidate"] = str(cand)
    if best[1] == 0 and args.init == "current":
        log.info("no real-val gain over the current model: nothing to gate")
        rep["kept"] = False
        rep["why"] = ["no real-val gain"]
    elif not args.no_gate:
        # ---- gate (full engine path, quick suite + real) --------------------------
        tg0 = time.perf_counter()
        cur = MODEL_DIR / "minimap_detector.onnx"
        key = hashlib.sha1(cur.read_bytes()).hexdigest()[:10]
        base_json = out / f"gym_base_{key}.json"
        base = json.loads(base_json.read_text()) if base_json.is_file() else gym(None, base_json)
        cand_out = gym(cand, out / "gym_candidate.json")
        b, c = summarize(base), summarize(cand_out)
        ok, why = gate(b, c)
        rep.update({"gate_s": round(time.perf_counter() - tg0, 1), "gym_base": b, "gym_candidate": c,
                    "kept": ok, "why": why})
        log.info("gate: %s %s", "KEEP" if ok else "REJECT", "; ".join(why))
        for k in b:
            log.info("  %-34s %.4f -> %.4f", k, b[k], c.get(k, float("nan")))
        if ok and args.install:
            shutil.copy2(cand, MODEL_DIR / "minimap_detector.onnx")
            shutil.copy2(out / "model_meta.json", MODEL_DIR / "model_meta.json")
            log.info("installed in %s", MODEL_DIR)
    (out / "report.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
