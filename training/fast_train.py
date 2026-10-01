"""Fast detector recipe: render the dataset ONCE in parallel, then train from RAM.

Why it is fast (see training/README.md, "Fast recipe"):

* **Data generation is the bottleneck of the streaming trainer** (``train.py``: one worker
  ~40 samples/s feeding 3 compute threads). Here the dataset is rendered once by a
  ``multiprocessing`` pool on every core (synthetic minimaps from ``synth.py`` + composites
  on the user's REAL minimap art from ``real_art.py``) into a uint8 ``.npz`` cache, then the
  training loop reads batches from RAM at full speed (all cores for the model) with cheap
  augmentation (flips, shifts, colour) applied on the fly.
* **Input size matched to the icons**: an icon is ~0.09 of the minimap, ~14 px at 160 px
  input, which is plenty for a stride-4 CenterNet. 160 px costs 2.6x less than 256 px.
* OneCycle learning rate (high peak, short warm-up), EMA of the weights, channels_last.

One command (cache + train + ONNX export + runtime evaluation)::

    python training/fast_train.py --minutes 10 --out training/runs/fast

The ONNX + ``model_meta.json`` land in ``<out>/onnx/``; ``--install`` copies them to
``treeaicoach/assets/model/`` (the app model). The cache is reused across runs
(``training/data/fast_<...>.npz``, keyed by the generator sources and the options).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import multiprocessing as mp
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

# Idle OpenMP threads must sleep, not spin: on a busy machine spinning threads made a
# 4-thread step 20x slower than a 1-thread one (measured). Must be set before torch loads.
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")

if __package__ in (None, ""):  # allow "python training/fast_train.py"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.dataset import (  # noqa: E402
    DATA_DIR,
    REPO_ROOT,
    STRIDE,
    _array_to_labels,
    _labels_to_array,
    encode_targets,
)

log = logging.getLogger("training.fast_train")

#: Real crops held out from the backgrounds for an honest real-image score (``--holdout``).
HOLDOUT_SHOTS = ("shot8", "shot9", "shot10")


# ======================================================================================
# 1. Parallel one-shot dataset rendering
# ======================================================================================

_W: dict[str, Any] = {}


def _worker_init(bg_names: Sequence[str] | None) -> None:
    cv2.setNumThreads(1)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    from training import real_art, synth  # noqa: PLC0415

    _W["synth"] = synth
    reals = real_art.load_real(names=list(bg_names) if bg_names else real_art.BACKGROUND_SHOTS)
    _W["art"] = real_art.RealArt(reals)


def _render_one(job: tuple[int, int, str, int]) -> tuple[int, np.ndarray, list[dict]]:
    idx, seed, kind, size = job
    rng = np.random.default_rng([0xFA57, seed, idx])
    for _ in range(5):
        try:
            if kind == "real" and _W["art"]:
                img, labels = _W["art"].sample(rng, size)
            else:
                img, labels = _W["synth"].generate_sample(rng, size)
            return idx, img, labels
        except Exception:  # a rare generator failure: draw again
            continue
    return idx, np.zeros((size, size, 3), np.uint8), []


def cache_key(n_synth: int, n_real: int, size: int, seed: int, bg_names: Sequence[str]) -> str:
    h = hashlib.sha1()
    for p in ("training/synth.py", "training/real_art.py", "treeaicoach/render.py"):
        try:
            h.update((REPO_ROOT / p).read_bytes())
        except OSError:
            h.update(p.encode())
    real = REPO_ROOT / "tests" / "fixtures" / "real" / "ground_truth.json"
    if real.is_file():
        h.update(real.read_bytes())
    h.update(json.dumps([n_synth, n_real, size, seed, sorted(bg_names)]).encode())
    return h.hexdigest()[:10]


def build_cache(n_synth: int, n_real: int, size: int, seed: int = 0, procs: int = 0,
                bg_names: Sequence[str] = (), cache_dir: Path = DATA_DIR
                ) -> tuple[np.ndarray, list[list[dict]], dict[str, float]]:
    """Render (or load) the dataset. Returns images ``[N,S,S,3]`` BGR uint8, labels, stats."""
    from training import real_art  # noqa: PLC0415

    bg_names = list(bg_names) or list(real_art.BACKGROUND_SHOTS)
    key = cache_key(n_synth, n_real, size, seed, bg_names)
    path = Path(cache_dir) / f"fast_{n_synth}s_{n_real}r_{size}_{key}.npz"
    if path.is_file():
        try:
            with np.load(path) as d:
                imgs, labels = d["images"], _array_to_labels(d["labels"])
            log.info("Dataset cache loaded: %s (%d images)", path, len(imgs))
            return imgs, labels, {"cached": 1.0, "gen_s": 0.0, "samples_per_s": 0.0}
        except Exception as exc:
            log.warning("Unreadable cache %s (%s): rebuilding", path, exc)
    n = n_synth + n_real
    jobs = [(i, seed, "synth" if i < n_synth else "real", size) for i in range(n)]
    procs = procs or os.cpu_count() or 2
    imgs = np.zeros((n, size, size, 3), np.uint8)
    labels: list[list[dict]] = [[] for _ in range(n)]
    t0 = time.perf_counter()
    ctx = mp.get_context("spawn")
    with ctx.Pool(procs, initializer=_worker_init, initargs=(bg_names,)) as pool:
        for k, (i, img, lab) in enumerate(pool.imap_unordered(_render_one, jobs, chunksize=16)):
            imgs[i] = img
            labels[i] = lab
            if (k + 1) % 2000 == 0:
                log.info("rendered %d / %d (%.0f samples/s)", k + 1, n,
                         (k + 1) / (time.perf_counter() - t0))
    dt = time.perf_counter() - t0
    stats = {"cached": 0.0, "gen_s": dt, "samples_per_s": n / dt}
    log.info("Dataset: %d images rendered in %.1f s with %d processes (%.0f samples/s)",
             n, dt, procs, n / dt)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # synth labels may carry "self"; keep the training contract (self -> ally)
        clean = [[{**l, "cls": "ally" if l.get("cls") == "self" else l.get("cls", "enemy")}
                  for l in ls] for ls in labels]
        tmp = path.with_suffix(".tmp.npz")
        np.savez(tmp, images=imgs, labels=_labels_to_array(clean))
        os.replace(tmp, path)
    except Exception as exc:
        log.warning("Cannot write the dataset cache: %s", exc)
    return imgs, labels, stats


# ======================================================================================
# 2. Augmentation + targets (cheap, on the fly)
# ======================================================================================


def augment(img: np.ndarray, labels: list[dict], rng: np.random.Generator
            ) -> tuple[np.ndarray, list[dict]]:
    """Flips, a small shift and colour jitter (labels follow)."""
    s = img.shape[0]
    labs = [dict(l) for l in labels]
    if rng.random() < 0.5:
        img = img[:, ::-1]
        for l in labs:
            l["u"] = 1.0 - l["u"]
    if rng.random() < 0.3:
        img = img[::-1]
        for l in labs:
            l["v"] = 1.0 - l["v"]
    if rng.random() < 0.5:
        dx, dy = (int(v) for v in rng.integers(-s // 16, s // 16 + 1, 2))
        img = np.roll(img, (dy, dx), axis=(0, 1))
        if dy > 0:
            img[:dy] = img[dy:dy + 1]
        elif dy < 0:
            img[dy:] = img[dy - 1:dy]
        if dx > 0:
            img[:, :dx] = img[:, dx:dx + 1]
        elif dx < 0:
            img[:, dx:] = img[:, dx - 1:dx]
        out = []
        for l in labs:
            u, v = l["u"] + dx / s, l["v"] + dy / s
            if 0.0 <= u < 1.0 and 0.0 <= v < 1.0:
                out.append({**l, "u": u, "v": v})
        labs = out
    img = np.ascontiguousarray(img)
    if rng.random() < 0.6:
        a = rng.uniform(0.75, 1.25)
        b = rng.uniform(-20, 20)
        img = cv2.convertScaleAbs(img, alpha=a, beta=b)
    return img, labs


def random_crop(img: np.ndarray, labels: list[dict], crop: int, rng: np.random.Generator
                ) -> tuple[np.ndarray, list[dict]]:
    """Square ``crop`` px window (the net is fully convolutional: icons are local), biased
    towards icons half of the time; labels re-normalized to the crop."""
    s = img.shape[0]
    if crop >= s:
        return img, labels
    if labels and rng.random() < 0.5:
        l = labels[int(rng.integers(len(labels)))]
        cx, cy = l["u"] * s + rng.uniform(-0.4, 0.4) * crop, l["v"] * s + rng.uniform(-0.4, 0.4) * crop
        x0 = int(np.clip(cx - crop / 2, 0, s - crop))
        y0 = int(np.clip(cy - crop / 2, 0, s - crop))
    else:
        x0, y0 = (int(v) for v in rng.integers(0, s - crop + 1, 2))
    k = s / crop
    out = []
    for l in labels:
        u, v = (l["u"] * s - x0) / crop, (l["v"] * s - y0) / crop
        if 0.0 <= u < 1.0 and 0.0 <= v < 1.0:
            out.append({**l, "u": u, "v": v, "r": l["r"] * k})
    return img[y0:y0 + crop, x0:x0 + crop], out


def make_batch(imgs: np.ndarray, labels: list[list[dict]], idx: np.ndarray,
               rng: np.random.Generator, size: int, stride: int = STRIDE,
               crop: int = 0) -> tuple[Any, dict]:
    import torch  # noqa: PLC0415

    full = size
    size = crop if 0 < crop < full else full
    xs = np.empty((len(idx), size, size, 3), np.uint8)
    tg: dict[str, list[np.ndarray]] = {}
    for k, i in enumerate(idx):
        img, labs = augment(imgs[i], labels[i], rng)
        img, labs = random_crop(img, labs, size, rng)
        xs[k] = img
        for name, v in encode_targets(labs, size, stride).items():
            tg.setdefault(name, []).append(v)
    x = torch.from_numpy(xs[..., ::-1].copy()).permute(0, 3, 1, 2).float().div_(255.0)
    return x, {k: torch.from_numpy(np.stack(v)) for k, v in tg.items()}


# ======================================================================================
# 3. Training
# ======================================================================================


def train(args: argparse.Namespace) -> dict[str, Any]:
    import torch  # noqa: PLC0415

    from training.model import build_model, count_parameters  # noqa: PLC0415
    from training.train import ModelEMA, compute_losses, param_groups  # noqa: PLC0415

    torch.set_num_threads(max(1, args.threads))
    torch.manual_seed(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    bg = [s for s in __import__("training.real_art", fromlist=["x"]).BACKGROUND_SHOTS
          if not (args.holdout and s in HOLDOUT_SHOTS)]
    t_all = time.perf_counter()
    imgs, labels, gstats = build_cache(args.n_synth, args.n_real, args.size, args.seed,
                                       args.procs, bg)
    n = len(imgs)
    cfg = {"width": args.width, "fpn_channels": args.fpn, "head_channels": args.head}
    model = build_model(cfg)
    model = model.to(memory_format=torch.channels_last)
    ema = ModelEMA(model, decay=args.ema_decay, tau=args.ema_tau)
    opt = torch.optim.AdamW(param_groups(model, args.wd), lr=args.lr)
    weights = {"hm": 1.0, "off": 1.0, "rad": 1.0, "cls": 1.0}
    rng = np.random.default_rng(args.seed)
    budget = args.minutes * 60.0
    steps = args.steps
    log.info("Model %s: %d params, input %d, %d images, budget %.1f min / %d steps",
             cfg, count_parameters(model), args.size, n, args.minutes, steps)
    t0 = time.perf_counter()
    step = 0
    run_loss = None
    while step < steps:
        el = time.perf_counter() - t0
        if budget > 0 and el >= budget:
            break
        prog = max(step / steps, el / budget if budget > 0 else 0.0)
        # OneCycle: linear warm-up to the peak, cosine down to ~0
        if prog < args.warmup:
            f = 0.1 + 0.9 * prog / args.warmup
        else:
            q = (prog - args.warmup) / max(1e-6, 1 - args.warmup)
            f = 0.01 + 0.99 * 0.5 * (1 + math.cos(math.pi * min(1.0, q)))
        for g in opt.param_groups:
            g["lr"] = args.lr * f
        idx = rng.integers(0, n, args.batch)
        x, t = make_batch(imgs, labels, idx, rng, args.size, crop=args.crop)
        x = x.contiguous(memory_format=torch.channels_last)
        o = model.forward_train(x)
        losses = compute_losses(o, t, weights, x.shape[-1], STRIDE)
        opt.zero_grad(set_to_none=True)
        losses["total"].backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()
        ema.update(model)
        step += 1
        lv = float(losses["total"].detach())
        run_loss = lv if run_loss is None else 0.95 * run_loss + 0.05 * lv
        if step % args.log_every == 0:
            el = time.perf_counter() - t0
            log.info("step %d  loss %.3f  lr %.2e  %.2f steps/s  %.0f samples/s  %.0f s",
                     step, run_loss, args.lr * f, step / el, step * args.batch / el, el)
    train_s = time.perf_counter() - t0
    ck = {"model_config": model.config if hasattr(model, "config") else cfg,
          "model": model.state_dict(), "ema": ema.state_dict(), "input_size": args.size,
          "stride": STRIDE, "step": step, "best_threshold": 0.35, "best_metrics": {}}
    torch.save(ck, out / "best.pt")
    stats = {"gen": gstats, "train_s": train_s, "steps": step,
             "steps_per_s": step / train_s if train_s else 0.0,
             "samples_per_s": step * args.batch / train_s if train_s else 0.0,
             "images": n, "params": count_parameters(model)}
    log.info("Trained %d steps in %.0f s (%.2f steps/s, %.0f samples/s)", step, train_s,
             stats["steps_per_s"], stats["samples_per_s"])

    # ---- export + runtime evaluation ------------------------------------------------
    from training.export_onnx import export  # noqa: PLC0415

    onnx_dir = out / "onnx"
    meta = export(out / "best.pt", onnx_dir, version=args.version or None, threshold=0.35)
    rep = evaluate_model(onnx_dir / "minimap_detector.onnx", holdout=args.holdout,
                         val_size=args.val_size)
    thr = rep["chosen_threshold"]
    meta["threshold"] = thr
    meta["metrics"] = rep["synthetic"]["at"]
    meta["metrics_real"] = rep["real"]["at"]
    meta["metrics_real_art"] = rep["real_art"]["at"]
    meta["train"] = {"recipe": "training/fast_train.py", "images": n,
                     "n_synth": args.n_synth, "n_real_art": args.n_real,
                     "gen_s": round(gstats["gen_s"], 1), "train_s": round(train_s, 1),
                     "steps": step, "batch": args.batch,
                     "total_s": round(time.perf_counter() - t_all, 1)}
    (onnx_dir / "model_meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    stats["eval"] = rep
    stats["total_s"] = time.perf_counter() - t_all
    (out / "report.json").write_text(json.dumps(stats, indent=1, default=float), encoding="utf-8")
    if args.install:
        dst = REPO_ROOT / "treeaicoach" / "assets" / "model"
        shutil.copy2(onnx_dir / "minimap_detector.onnx", dst / "minimap_detector.onnx")
        shutil.copy2(onnx_dir / "model_meta.json", dst / "model_meta.json")
        log.info("Installed into %s", dst)
    return stats


# ======================================================================================
# 4. Evaluation through the app's runtime detector
# ======================================================================================


def real_eval_set(holdout: bool) -> tuple[list[np.ndarray], list[list[Any]], list[str]]:
    from training import real_art  # noqa: PLC0415
    from training.evaluate import GT  # noqa: PLC0415

    names = None
    if holdout:   # crops whose pixels never entered the backgrounds (+ the older setup)
        names = list(HOLDOUT_SHOTS) + ["shot2", "shot5"]
    reals = real_art.load_real(names=names)
    imgs, gts = [], []
    for r in reals:
        imgs.append(r.img)
        gts.append([GT(u, v, real_art.REAL_ICON_R, "enemy" if t == "enemy" else "ally")
                    for u, v, t in r.gts])
    return imgs, gts, [r.name for r in reals]


def real_art_eval_set(n: int = 150, seed: int = 777) -> tuple[list[np.ndarray], list[list[Any]]]:
    """Held-out real-art composites: backgrounds of the held-out crops only, other seeds."""
    from training import real_art  # noqa: PLC0415
    from training.evaluate import gts_from_labels  # noqa: PLC0415

    art = real_art.RealArt(real_art.load_real(names=list(HOLDOUT_SHOTS)))
    rng = np.random.default_rng(seed)
    imgs, gts = [], []
    for _ in range(n):
        img, labs = art.sample(rng, 256)
        imgs.append(img)
        gts.append(gts_from_labels(labs))
    return imgs, gts


def evaluate_model(model_path: Path, holdout: bool = True, val_size: int = 200,
                   threads: int = 2) -> dict[str, Any]:
    from training.dataset import build_validation_set  # noqa: PLC0415
    from training.evaluate import (evaluate_images, gts_from_labels,  # noqa: PLC0415
                                   load_runtime_detector)

    det, _ = load_runtime_detector(model_path, None, 0.05, threads)
    out: dict[str, Any] = {}
    sets = {}
    vs = build_validation_set(val_size)
    sets["synthetic"] = (list(vs.images), [gts_from_labels(l) for l in vs.labels])
    ri, rg, _ = real_eval_set(holdout)
    sets["real"] = (ri, rg)
    sets["real_art"] = real_art_eval_set()
    accs = {}
    for name, (imgs, gts) in sets.items():
        acc, ms = evaluate_images(det, imgs, gts)
        accs[name] = acc
        out[name] = {"best": acc.best(), "ms": ms}
    # one operating threshold: best mean F1 over synthetic + real-art (real is too small)
    from training.evaluate import SWEEP  # noqa: PLC0415

    def f1s(t: float) -> float:
        return 0.5 * (accs["synthetic"].metrics(t)["f1"] + accs["real_art"].metrics(t)["f1"])

    thr = max(SWEEP, key=lambda t: (round(f1s(t), 4), t))
    out["chosen_threshold"] = float(thr)
    for name, acc in accs.items():
        m = acc.metrics(thr)
        out[name]["at"] = {k: m[k] for k in ("threshold", "gt", "det", "tp", "precision",
                                             "recall", "f1", "cls_acc", "pos_err_pct")}
        b = out[name]["best"]
        out[name]["best"] = {k: b[k] for k in ("threshold", "precision", "recall", "f1")}
        log.info("%-9s @%.2f  P %.3f R %.3f F1 %.3f cls %.3f | best F1 %.3f @%.2f | %.1f ms/img",
                 name, thr, m["precision"], m["recall"], m["f1"], m["cls_acc"], b["f1"],
                 b["threshold"], out[name]["ms"])
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fast detector recipe (cached dataset + RAM training).")
    p.add_argument("--n-synth", type=int, default=4000, help="synthetic minimaps in the cache")
    p.add_argument("--n-real", type=int, default=4000, help="real-art composites in the cache")
    p.add_argument("--size", type=int, default=160, help="network input size")
    p.add_argument("--procs", type=int, default=0, help="rendering processes (0 = all cores)")
    p.add_argument("--minutes", type=float, default=10.0, help="training-loop budget")
    p.add_argument("--steps", type=int, default=4000)
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--crop", type=int, default=96, help="train on random crops (0 = full image)")
    p.add_argument("--lr", type=float, default=4e-3)
    p.add_argument("--warmup", type=float, default=0.05)
    p.add_argument("--wd", type=float, default=5e-4)
    p.add_argument("--ema-decay", type=float, default=0.998)
    p.add_argument("--ema-tau", type=float, default=200.0)
    p.add_argument("--width", type=float, default=0.5)
    p.add_argument("--fpn", type=int, default=32)
    p.add_argument("--head", type=int, default=16)
    p.add_argument("--threads", type=int, default=2,
                   help="torch threads (2 is best on a busy 4-core machine; see README)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--val-size", type=int, default=200)
    p.add_argument("--log-every", type=int, default=50)
    p.add_argument("--holdout", action="store_true",
                   help=f"keep {', '.join(HOLDOUT_SHOTS)} out of the backgrounds and score them")
    p.add_argument("--version", default="")
    p.add_argument("--install", action="store_true", help="copy the ONNX + meta into the app")
    p.add_argument("--out", default="training/runs/fast")
    p.add_argument("--eval-only", default="", help="evaluate this ONNX model and exit")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    args = build_parser().parse_args(argv)
    if args.eval_only:
        rep = evaluate_model(Path(args.eval_only), holdout=args.holdout, val_size=args.val_size)
        print(json.dumps(rep, indent=1, default=float))
        return 0
    stats = train(args)
    print(json.dumps({k: v for k, v in stats.items() if k != "eval"}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
