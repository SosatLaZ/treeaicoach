# Training the minimap detector (MinimapNet)

Everything here runs on the CPU and lives outside the `.exe`. Training needs `torch` (CPU build),
`onnx` and `onnxruntime` in addition to `requirements.txt`. All commands run from the repository root.

| File | Role |
|---|---|
| `synth.py` | synthetic minimap generator (`generate_sample(rng, size)`), built on `treeaicoach/render.py` |
| `dataset.py` | endless `IterableDataset` (per-worker / per-run seeding), CenterNet targets at stride 4, fixed validation set cached in `training/data/*.npz` |
| `model.py` | `MinimapNet`: a light CenterNet with ~0.5 M parameters (inverted residuals at 24/48/96 ch, FPN at 64 ch, 4 heads) |
| `train.py` | training loop (focal + L1 + CE, AdamW, warm-up + cosine, EMA, gradient clipping, periodic validation, resumable) |
| `export_onnx.py` | EMA checkpoint -> `treeaicoach/assets/model/minimap_detector.onnx` + `model_meta.json` (verified against onnxruntime) |
| `evaluate.py` | evaluation **through the app's runtime detector** (`treeaicoach.detector.OnnxDetector`) + builds `treeaicoach/assets/selftest/` |

## 1. Quick run (~2 minutes)

```bash
python training/train.py --minutes 2 --batch 16 --workers 1 --threads 3 \
    --out training/runs/quick --val-size 200 --val-every 100000
python training/export_onnx.py --ckpt training/runs/quick/best.pt --version quick-2min-2026-09-30
python training/evaluate.py --val-size 200 --make-selftest
```

`--minutes M` caps the **training loop** at M minutes of wall-clock time, whatever `--steps` says.
Two things fall outside the cap. Before the loop, the validation set is rendered, about 5-15 s for
200 images the first time and near zero once it is in the `training/data/` cache. After the loop,
one final validation runs on the EMA weights, a few seconds for 200 images. With a budget, the
learning-rate schedule follows `progress = max(step / steps, elapsed / budget)`, so warm-up and
cosine decay finish inside the budget even though the run stops long before `--steps`. The run
still writes `last.pt`, `best.pt` and `log.csv` and records the best-F1 threshold. `--minutes`
combines with every other flag and with `--resume`: on a resume, the budget covers the new run and
the schedule continues from the saved progress.

A 2-minute run makes only ~100-200 steps at batch 16. The model learns roughly where icons are, but
it is far from shippable. Use it to check the pipeline end to end (train -> export -> runtime
evaluation -> app selftest), not as the release model.

## 2. Full training (~100 minutes, recommended)

```bash
python training/train.py --minutes 100 --steps 2400 --batch 32 --lr 2e-3 --warmup 0.03 \
    --workers 1 --threads 3 --val-every 300 --val-size 600 --w-rad 1 --w-off 1 --w-cls 1 \
    --ema-decay 0.999 --ema-tau 300 --out training/runs/main
python training/export_onnx.py --ckpt training/runs/main/best.pt --version minimapnet-2026-09-30
python training/evaluate.py --make-selftest            # + --real-dir <dir> if you have labelled crops
```

The run stops at 100 min or at 2400 steps, whichever comes first. Interrupt it with Ctrl+C
(`last.pt` is saved) and resume it with `--resume auto`.

### How these numbers were chosen (benchmark, 2026-09-30)

These were measured on a 4-core shared VM while other jobs were running (load average ~4.5), so
they are **noisy**. On an idle machine, expect them to be the same or better.

| Measurement | Result |
|---|---|
| `synth.generate_sample` + targets, 1 core | ~39 samples/s |
| model step at batch 32 (forward + backward + AdamW), 1 / 2 / 3 / 4 torch threads | 4.7 / 6.8 / 8.4 / 6.9 samples/s |
| end to end at batch 32, **1 worker + 3 threads** | **~11 samples/s** (≈ 0.34 steps/s) |
| end to end at batch 32, 2 workers + 2 threads | ~8.2 samples/s |
| end to end at batch 32, 0 workers + 4 threads | ~3.5 samples/s |

The model step is the bottleneck: one data worker keeps 3 compute threads fed. At ~11 samples/s,
100 minutes gives about **6000 s × 11 / 32 ≈ 2050 steps** at batch 32. `--steps 2400` sets the
cosine schedule for a slightly faster machine, and `--minutes 100` makes sure the schedule still
completes on a slower one. `--ema-tau 300` lets the EMA warm up faster than the default 1000,
which suits a run of only ~2000 steps.

## 3. Outputs and conventions

* `training/runs/<name>/`: `log.csv` (training and validation rows), `last.pt` (resumable),
  `best.pt` (best validation F1), `args.json`.
* Validation metrics: precision, recall and F1 at a matching distance < 0.5 × the icon radius, class
  accuracy (enemy vs ally), and mean position error in % of the minimap width. The threshold with
  the best F1 goes into `model_meta.json`.
* Classes stay `("enemy", "ally", "self")` for the ONNX contract. **"self" is trained as "ally"**:
  in the real game, the local player's ring is the same blue as the allies' rings (see
  `docs/MINIMAP_FACTS.md`). Icons with random-hue rings (`cls_valid = False`) count for detection
  but not for the class.
* DataLoader workers use the `spawn` start method. With `fork`, a worker could inherit a lock held
  by the parent's OpenMP/OpenCV thread pools and deadlock before producing its first sample.

## 4. Tests

```bash
python -m pytest tests/test_training_smoke.py -q     # ~1 min; TREEAI_SKIP_SLOW=1 skips the end-to-end part
```

The whole file is skipped when `torch` is missing (the Windows CI installs only the runtime
requirements).
