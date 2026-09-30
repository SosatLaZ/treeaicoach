"""Train MinimapNet on synthetic minimaps (CPU friendly).

Example (from the repository root, see training/README.md for the recommended command)::

    python -m training.train --steps 12000 --batch 32 --lr 2e-3 --workers 2 --threads 2 \
        --out training/runs/main --val-every 500

    python training/train.py --minutes 2 --out training/runs/quick    # quick wall-clock-bounded run

``--minutes M`` bounds the training loop to M minutes of wall-clock time (the validation-set
build before the loop and the final validation after it are not counted). The learning-rate
schedule then follows ``progress = max(step / steps, elapsed / budget)``, so warm-up and
cosine decay complete within the budget whichever limit is hit first.

Outputs in ``--out``: ``log.csv`` (training + validation rows), ``last.pt`` (resumable, written
at every validation and at the end / on Ctrl+C), ``best.pt`` (best validation F1, EMA
weights), ``args.json``. Validation (EMA weights) reports precision / recall / F1 at a
centre distance < 0.5 r, class accuracy, mean position error (% of the minimap width) and
the best-F1 threshold, which ``export_onnx.py`` writes into ``model_meta.json``.

Losses (CenterNet): focal loss on the heatmap (alpha 2, beta 4), L1 on the sub-cell offset
and on the radius (in output cells) at the object centres, cross-entropy on the class at the
centres whose class is supervised (``cls_valid``). AdamW, linear warm-up then cosine decay,
gradient clipping, exponential moving average of the weights.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import logging
import math
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn
from torch.utils.data import DataLoader

if __package__ in (None, ""):  # allow "python training/train.py"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.dataset import (  # noqa: E402
    INPUT_SIZE,
    STRIDE,
    VAL_SEED_DEFAULT,
    MinimapStream,
    SampleFn,
    ValidationSet,
    build_validation_set,
    to_input,
    worker_init,
)
from training.evaluate import (  # noqa: E402
    SWEEP,
    SWEEP_MIN,
    MetricAccumulator,
    decode_numpy,
    gts_from_labels,
)
from training.model import CLASSES, MinimapNet, build_model, count_parameters  # noqa: E402

log = logging.getLogger("training.train")

CSV_FIELDS = ("kind", "step", "elapsed_s", "lr", "loss", "hm", "off", "rad", "cls", "samples_per_s",
              "precision", "recall", "f1", "threshold", "cls_acc", "pos_err_pct", "r_err_pct")


# ======================================================================================
# Losses
# ======================================================================================


def focal_loss(logits: torch.Tensor, gt: torch.Tensor, alpha: float = 2.0, beta: float = 4.0) -> torch.Tensor:
    """CenterNet penalty-reduced focal loss on heatmap logits, normalized by #positives."""
    pos = gt >= 1.0 - 1e-4
    p = torch.sigmoid(logits).clamp(1e-4, 1.0 - 1e-4)
    log_p = F.logsigmoid(logits)
    log_1mp = F.logsigmoid(-logits)
    pos_loss = (log_p * (1.0 - p).pow(alpha))[pos].sum()
    neg_loss = (log_1mp * p.pow(alpha) * (1.0 - gt).pow(beta))[~pos].sum()
    num_pos = pos.sum().clamp(min=1).to(logits.dtype)
    return -(pos_loss + neg_loss) / num_pos


def gather_at(feat: torch.Tensor, ind: torch.Tensor) -> torch.Tensor:
    """``feat [B,C,H,W]`` at flat indices ``ind [B,M]`` -> ``[B,M,C]``."""
    b, c = feat.shape[:2]
    flat = feat.reshape(b, c, -1)
    return flat.gather(2, ind.unsqueeze(1).expand(b, c, ind.shape[1])).permute(0, 2, 1)


def compute_losses(out: Any, targets: dict[str, torch.Tensor], weights: dict[str, float],
                   input_size: int = INPUT_SIZE, stride: int = STRIDE) -> dict[str, torch.Tensor]:
    """All loss terms (+ ``"total"``) for a batch of :class:`training.model.TrainOutputs`."""
    hm = focal_loss(out.heatmap_logits, targets["heatmap"])
    ind = targets["ind"]
    mask = targets["reg_mask"]
    n = mask.sum().clamp(min=1.0)
    off = gather_at(out.offset, ind)                                  # [B,M,2]
    l_off = ((off - targets["offset"]).abs().sum(-1) * mask).sum() / (2.0 * n)
    rad = gather_at(out.radius, ind)[..., 0]                          # [B,M]
    cells = float(input_size) / float(stride)                         # radius loss in cells
    l_rad = ((rad - targets["radius"]).abs() * cells * mask).sum() / n
    cls_logits = gather_at(out.cls_logits, ind)                       # [B,M,C]
    cmask = targets["cls_mask"] * mask
    ce = F.cross_entropy(cls_logits.reshape(-1, cls_logits.shape[-1]), targets["cls"].reshape(-1),
                         reduction="none").reshape(mask.shape)
    l_cls = (ce * cmask).sum() / cmask.sum().clamp(min=1.0)
    total = (weights["hm"] * hm + weights["off"] * l_off + weights["rad"] * l_rad
             + weights["cls"] * l_cls)
    return {"total": total, "hm": hm, "off": l_off, "rad": l_rad, "cls": l_cls}


# ======================================================================================
# EMA, schedule, checkpoints
# ======================================================================================


class ModelEMA:
    """Exponential moving average of all floating-point weights and buffers.

    ``decay_t = decay * (1 - exp(-updates / tau))`` so the average follows the model closely
    at the beginning of training.
    """

    def __init__(self, model: nn.Module, decay: float = 0.999, tau: float = 1000.0) -> None:
        self.module = copy.deepcopy(model).eval()
        for p in self.module.parameters():
            p.requires_grad_(False)
        self.decay = float(decay)
        self.tau = max(1.0, float(tau))
        self.updates = 0

    @torch.no_grad()
    def update(self, model: nn.Module) -> None:
        """Blend the current model weights into the average."""
        self.updates += 1
        d = self.decay * (1.0 - math.exp(-self.updates / self.tau))
        msd = model.state_dict()
        for k, v in self.module.state_dict().items():
            if v.dtype.is_floating_point:
                v.mul_(d).add_(msd[k].detach(), alpha=1.0 - d)
            else:
                v.copy_(msd[k])

    def state_dict(self) -> dict[str, Any]:
        return {"module": self.module.state_dict(), "updates": self.updates,
                "decay": self.decay, "tau": self.tau}

    def load_state_dict(self, sd: dict[str, Any]) -> None:
        self.module.load_state_dict(sd["module"])
        self.updates = int(sd.get("updates", 0))


def lr_factor(step: int, total: int, warmup: int, final: float = 0.02) -> float:
    """Linear warm-up to 1 then cosine decay to ``final``."""
    if warmup > 0 and step < warmup:
        return (step + 1) / warmup
    t = (step - warmup) / max(1, total - warmup)
    t = min(1.0, max(0.0, t))
    return final + (1.0 - final) * 0.5 * (1.0 + math.cos(math.pi * t))


def lr_factor_progress(progress: float, warmup_frac: float, final: float = 0.02) -> float:
    """Same shape as :func:`lr_factor` on a training progress fraction in [0, 1]."""
    p = min(1.0, max(0.0, float(progress)))
    w = min(0.5, max(0.0, float(warmup_frac)))
    if w > 0 and p < w:
        return max(1e-3, p / w)
    t = (p - w) / max(1e-9, 1.0 - w)
    return final + (1.0 - final) * 0.5 * (1.0 + math.cos(math.pi * min(1.0, t)))


def param_groups(model: nn.Module, weight_decay: float) -> list[dict[str, Any]]:
    """AdamW groups: no weight decay on biases and normalization parameters."""
    decay, no_decay = [], []
    for name, p in model.named_parameters():
        if not p.requires_grad:
            continue
        (no_decay if p.ndim <= 1 or name.endswith(".bias") else decay).append(p)
    return [{"params": decay, "weight_decay": weight_decay},
            {"params": no_decay, "weight_decay": 0.0}]


def save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    """Atomic ``torch.save`` (temporary file + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save(payload, tmp)
    os.replace(tmp, path)


def load_checkpoint(path: Path) -> dict[str, Any]:
    """Load a checkpoint written by :func:`save_checkpoint` (CPU tensors)."""
    return torch.load(path, map_location="cpu", weights_only=False)


# ======================================================================================
# Validation
# ======================================================================================


@torch.inference_mode()
def validate(model: nn.Module, valset: ValidationSet, batch: int = 32,
             thresholds: Sequence[float] = SWEEP) -> dict[str, Any]:
    """Metrics of ``model`` (eval mode) on ``valset``; best-F1 threshold + sweep."""
    was_training = model.training
    model.eval()
    acc = MetricAccumulator()
    n = len(valset)
    size = valset.images.shape[1]
    for i in range(0, n, batch):
        x = torch.from_numpy(np.stack([to_input(im, size) for im in valset.images[i:i + batch]]))
        hm, cl, off, rad = (t.numpy() for t in model(x))
        for j in range(hm.shape[0]):
            dets = decode_numpy(hm[j], cl[j], off[j], rad[j], SWEEP_MIN, STRIDE, size)
            acc.add(dets, gts_from_labels(valset.labels[i + j]))
    model.train(was_training)
    best = acc.best(thresholds)
    best["sweep"] = [{k: m[k] for k in ("threshold", "precision", "recall", "f1")}
                     for m in acc.sweep(thresholds)]
    return best


# ======================================================================================
# Training loop
# ======================================================================================


class _CsvLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        new = not path.is_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, "a", newline="", encoding="utf-8")  # noqa: SIM115
        self._w = csv.DictWriter(self._f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new:
            self._w.writeheader()

    def write(self, row: dict[str, Any]) -> None:
        self._w.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in row.items()})
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def _fmt_time(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def make_loader(args: argparse.Namespace, epoch: int, generator: SampleFn | None = None
                ) -> Iterator[Any]:
    """Endless iterator of training batches."""
    ds = MinimapStream(seed=args.seed, input_size=args.input_size, epoch=epoch, generator=generator)
    kw: dict[str, Any] = {"batch_size": args.batch, "num_workers": args.workers,
                          "worker_init_fn": worker_init}
    if args.workers > 0:
        kw.update(persistent_workers=True, prefetch_factor=4)
        if generator is None:
            # "spawn", never "fork": the parent already runs OpenMP / OpenCV thread pools (the
            # validation set is rendered before the loader starts) and a forked child can
            # inherit one of their locks held -> the worker deadlocks before its first sample.
            # (A custom in-process generator may be an unpicklable closure: default context.)
            kw["multiprocessing_context"] = "spawn"
    return iter(DataLoader(ds, **kw))


def train(args: argparse.Namespace, generator: SampleFn | None = None,
          valset: ValidationSet | None = None) -> dict[str, Any]:
    """Run a training; returns a summary dict (best F1, threshold, paths, throughput)."""
    torch.set_num_threads(max(1, args.threads))
    torch.manual_seed(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    weights = {"hm": args.w_hm, "off": args.w_off, "rad": args.w_rad, "cls": args.w_cls}

    model = MinimapNet(width=args.width)
    ema = ModelEMA(model, args.ema_decay, args.ema_tau)
    opt = torch.optim.AdamW(param_groups(model, args.wd), lr=args.lr, betas=(0.9, 0.99))
    start_step, best_f1, best_thr, best_metrics = 0, -1.0, 0.35, {}
    start_progress = 0.0

    resume = args.resume
    if resume == "auto":
        resume = str(out / "last.pt") if (out / "last.pt").is_file() else ""
    if resume:
        ck = load_checkpoint(Path(resume))
        model = build_model(ck.get("model_config"))
        model.load_state_dict(ck["model"])
        ema = ModelEMA(model, args.ema_decay, args.ema_tau)
        ema.load_state_dict(ck["ema"])
        opt = torch.optim.AdamW(param_groups(model, args.wd), lr=args.lr, betas=(0.9, 0.99))
        opt.load_state_dict(ck["optimizer"])
        start_step = int(ck["step"])
        best_f1 = float(ck.get("best_f1", -1.0))
        best_thr = float(ck.get("best_threshold", 0.35))
        best_metrics = ck.get("best_metrics", {})
        start_progress = float(ck.get("progress", start_step / max(1, args.steps)))
        if "torch_rng" in ck:
            torch.set_rng_state(ck["torch_rng"])
        log.info("Resumed from %s at step %d (best F1 %.4f)", resume, start_step, best_f1)

    n_params = count_parameters(model)
    (out / "args.json").write_text(json.dumps({**vars(args), "params": n_params}, indent=1),
                                   encoding="utf-8")
    log.info("MinimapNet: %d parameters; %d steps x batch %d; %d workers, %d threads",
             n_params, args.steps, args.batch, args.workers, args.threads)

    if valset is None and args.val_size > 0:
        valset = build_validation_set(args.val_size, args.val_seed, args.input_size,
                                      cache_dir=Path(args.cache_dir) if args.cache_dir else None,
                                      generator=generator)
    csv_log = _CsvLog(out / "log.csv")
    batches = make_loader(args, start_step, generator)
    warmup = int(args.warmup * args.steps) if args.warmup < 1 else int(args.warmup)
    warmup_frac = warmup / max(1, args.steps)
    budget_s = max(0.0, float(args.minutes)) * 60.0
    if budget_s > 0:
        log.info("Wall-clock budget: %.1f min (lr schedule follows time or steps, whichever is ahead)",
                 args.minutes)

    def checkpoint(step: int) -> dict[str, Any]:
        return {"model": model.state_dict(), "ema": ema.state_dict(), "optimizer": opt.state_dict(),
                "step": step, "best_f1": best_f1, "best_threshold": best_thr,
                "best_metrics": best_metrics, "model_config": model.config,
                "classes": list(CLASSES), "input_size": args.input_size, "stride": STRIDE,
                "args": vars(args), "torch_rng": torch.get_rng_state(), "progress": progress}

    stop = {"flag": False}

    def _on_sigterm(_sig: int, _frm: Any) -> None:
        stop["flag"] = True

    try:
        prev = signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):  # not in the main thread
        prev = None

    model.train()
    t_start = time.perf_counter()
    t_win, n_win = time.perf_counter(), 0
    ema_step_time: float | None = None
    run_loss: dict[str, float] = {}
    step = start_step
    samples_per_s = 0.0
    progress = start_progress
    out_of_time = False

    def current_progress() -> float:
        p = step / max(1, args.steps)
        if budget_s > 0:
            p = max(p, start_progress + (1.0 - start_progress) * (time.perf_counter() - t_start) / budget_s)
        return min(1.0, p)

    try:
        while step < args.steps and not stop["flag"] and not out_of_time:
            t0 = time.perf_counter()
            x, tg = next(batches)
            progress = current_progress()
            for g in opt.param_groups:
                if budget_s > 0:
                    g["lr"] = args.lr * lr_factor_progress(progress, warmup_frac, args.final_lr)
                else:
                    g["lr"] = args.lr * lr_factor(step, args.steps, warmup, args.final_lr)
            outp = model.forward_train(x)
            losses = compute_losses(outp, tg, weights, args.input_size, STRIDE)
            opt.zero_grad(set_to_none=True)
            losses["total"].backward()
            if args.clip > 0:
                nn.utils.clip_grad_norm_(model.parameters(), args.clip)
            opt.step()
            ema.update(model)
            step += 1
            n_win += x.shape[0]
            for k, v in losses.items():
                fv = float(v.detach())
                run_loss[k] = run_loss.get(k, fv) * 0.9 + fv * 0.1
            dt = time.perf_counter() - t0
            ema_step_time = dt if ema_step_time is None else 0.95 * ema_step_time + 0.05 * dt
            progress = current_progress()
            if budget_s > 0 and time.perf_counter() - t_start >= budget_s:
                out_of_time = True
            last = step == args.steps or out_of_time

            if step % args.log_every == 0 or last:
                now = time.perf_counter()
                samples_per_s = n_win / max(1e-9, now - t_win)
                t_win, n_win = now, 0
                eta = (args.steps - step) * (ema_step_time or 0.0)
                if budget_s > 0:
                    eta = min(eta, max(0.0, budget_s - (now - t_start)))
                lr = opt.param_groups[0]["lr"]
                print(f"step {step:6d}/{args.steps} | loss {run_loss['total']:.4f} (hm {run_loss['hm']:.3f}"
                      f" off {run_loss['off']:.3f} rad {run_loss['rad']:.3f} cls {run_loss['cls']:.3f})"
                      f" | lr {lr:.2e} | {samples_per_s:.1f} img/s | elapsed "
                      f"{_fmt_time(now - t_start)} | ETA {_fmt_time(eta)}", flush=True)
                csv_log.write({"kind": "train", "step": step, "elapsed_s": now - t_start, "lr": lr,
                               "loss": run_loss["total"], "hm": run_loss["hm"], "off": run_loss["off"],
                               "rad": run_loss["rad"], "cls": run_loss["cls"],
                               "samples_per_s": samples_per_s})

            if valset is not None and len(valset) and (step % args.val_every == 0 or last):
                tv = time.perf_counter()
                m = validate(ema.module, valset, args.val_batch)
                improved = m["f1"] > best_f1
                if improved:
                    best_f1, best_thr, best_metrics = m["f1"], m["threshold"], m
                print(f"  val @ {step}: P {m['precision']:.3f} R {m['recall']:.3f} F1 {m['f1']:.4f}"
                      f" (thr {m['threshold']:.2f}) | cls acc {m['cls_acc']:.3f} | pos err "
                      f"{m['pos_err_pct']:.2f} % | r err {m['r_err_pct']:.2f} % | "
                      f"{time.perf_counter() - tv:.1f} s{'  *best*' if improved else ''}", flush=True)
                csv_log.write({"kind": "val", "step": step, "elapsed_s": time.perf_counter() - t_start,
                               **{k: m[k] for k in ("precision", "recall", "f1", "threshold", "cls_acc",
                                                    "pos_err_pct", "r_err_pct")}})
                ck = checkpoint(step)
                save_checkpoint(out / "last.pt", ck)
                if improved:
                    save_checkpoint(out / "best.pt", ck)
                t_win = time.perf_counter()  # validation time is not training throughput
    except KeyboardInterrupt:
        print("Interrupted: saving last.pt", flush=True)
    finally:
        if out_of_time:
            print(f"Wall-clock budget of {args.minutes:g} min reached at step {step}.", flush=True)
        save_checkpoint(out / "last.pt", checkpoint(step))
        if not (out / "best.pt").is_file():
            save_checkpoint(out / "best.pt", checkpoint(step))
        csv_log.close()
        if prev is not None:
            try:
                signal.signal(signal.SIGTERM, prev)
            except (ValueError, OSError):
                pass
        shutdown = getattr(batches, "_shutdown_workers", None)
        if callable(shutdown):  # stop the DataLoader worker processes now
            shutdown()
        del batches

    total = time.perf_counter() - t_start
    return {"step": step, "best_f1": best_f1, "best_threshold": best_thr, "best_metrics": best_metrics,
            "last": str(out / "last.pt"), "best": str(out / "best.pt"), "seconds": total,
            "samples_per_s": samples_per_s, "params": n_params, "out_of_time": out_of_time}


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    p = argparse.ArgumentParser(description="Train MinimapNet (CenterNet) on synthetic minimaps.")
    p.add_argument("--steps", type=int, default=12000)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--lr", type=float, default=2e-3)
    p.add_argument("--minutes", type=float, default=0.0,
                   help="wall-clock budget of the training loop in minutes (0 = none); the lr "
                        "schedule then completes within the budget")
    p.add_argument("--wd", type=float, default=5e-4, help="AdamW weight decay (conv weights only)")
    p.add_argument("--warmup", type=float, default=0.03, help="warm-up steps (< 1: fraction of --steps)")
    p.add_argument("--final-lr", type=float, default=0.02, help="final lr as a fraction of --lr")
    p.add_argument("--clip", type=float, default=5.0, help="gradient norm clipping (0 = off)")
    p.add_argument("--ema-decay", type=float, default=0.999)
    p.add_argument("--ema-tau", type=float, default=1000.0)
    p.add_argument("--w-hm", type=float, default=1.0)
    p.add_argument("--w-off", type=float, default=1.0)
    p.add_argument("--w-rad", type=float, default=1.0, help="radius L1 weight (radius in output cells)")
    p.add_argument("--w-cls", type=float, default=1.0)
    p.add_argument("--width", type=float, default=1.0, help="channel width multiplier")
    p.add_argument("--workers", type=int, default=2, help="DataLoader worker processes")
    p.add_argument("--threads", type=int, default=2, help="torch intra-op threads (main process)")
    p.add_argument("--out", default="training/runs/main")
    p.add_argument("--val-every", type=int, default=500)
    p.add_argument("--val-size", type=int, default=600)
    p.add_argument("--val-seed", type=int, default=VAL_SEED_DEFAULT)
    p.add_argument("--val-batch", type=int, default=50)
    p.add_argument("--cache-dir", default="training/data", help="validation-set cache ('' = none)")
    p.add_argument("--log-every", type=int, default=50)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--input-size", type=int, default=INPUT_SIZE)
    p.add_argument("--resume", default="", help="checkpoint to resume, or 'auto' (<out>/last.pt)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    res = train(args)
    print(f"Done: step {res['step']}, best F1 {res['best_f1']:.4f} at threshold "
          f"{res['best_threshold']:.2f} ({_fmt_time(res['seconds'])}). Best: {res['best']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
