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

## 0. Fast recipe (~10 minutes on a 4-core CPU, recommended)

```bash
python training/fast_train.py --minutes 8 --holdout --out training/runs/fast   # cache + train + export + eval
cp training/runs/fast/onnx/* treeaicoach/assets/model/                        # or pass --install
python training/patch_train.py --holdout --out treeaicoach/assets/model/patch_verifier.npz \
    --eval-onnx treeaicoach/assets/model/minimap_detector.onnx                # verifier, ~1 min
```

| File | Role |
|---|---|
| `real_art.py` | clean backgrounds from the REAL 2026 crops (`tests/fixtures/real`): per-pixel median + each crop with its labelled icons painted over; real glyphs (towers with plate numbers, camps, markers) cut out as pasteable hard negatives; icons composited with ring colours measured on the crops, stacks, our overlay rings/labels, camera lines, then `synth._degrade` |
| `fast_train.py` | renders the dataset ONCE with a `multiprocessing` pool (synth + real-art, uint8 `.npz` cache in `training/data/`), trains a tiny MinimapNet (width 0.5, 97 k params) at 160 px input on 96 px random crops from RAM, OneCycle LR + EMA, exports ONNX (opset 17) and scores it through the app's `OnnxDetector` on synthetic / real / real-art / selftest sets |
| `patch_train.py` | trains `treeaicoach/patch_classifier.py` (icon vs distractor verifier, numpy MLP, 70 KB) in seconds from the same cache; `--eval-onnx` measures it as a re-scorer |

Why it is fast: the streaming trainer is data-bound (~40 samples/s from one core); the cache
renders at ~140 samples/s on 4 processes (6000 images in 43 s, reused afterwards), and the
loop then runs at ~1.6-2.6 steps/s at batch 64 (crops) even on a shared machine. Icons are
~14 px at 160 px input, enough for a stride-4 CenterNet, and the tiny net runs in ~3 ms per
frame (vs ~25 ms for the 256 px model).

`--holdout` keeps the crops shot8/9/10 out of the backgrounds, so the "real" score (shot2, 5,
8, 9, 10 = 21 icons) is measured on pixels the model never saw. Without it every 2026 crop
is used (more variety, but then the real score is in-sample).

Busy machine: `fast_train.py` sets `OMP_WAIT_POLICY=PASSIVE` and uses 2 torch threads. On a
4-core VM with a load average of 10-20, 4 spinning OpenMP threads made a training step
20x slower than 1 thread (measured).

Measured 2026-10-01 (shared 4-core VM; runtime path, best-F1 / operating threshold 0.40):

| Set | old 256 px model (2-min quick run) | fast recipe (8 min) |
|---|---|---|
| synthetic val (200 images, 1087 icons) | F1 0.674 (P 0.70 R 0.65) | **F1 0.793** (P 0.78 R 0.80) |
| real held-out crops (21 icons) | F1 0.432 (P 0.50 R 0.38) | **F1 0.900** (P 0.95 R 0.86) |
| real, all 9 crops (45 icons) | F1 0.462 | **F1 0.871** (P 0.93 R 0.82) |
| real-art composites on held-out backgrounds | F1 0.503 | **F1 0.814** |
| app selftest set (needs >= 0.8) | P 0.83 R 0.81 | P 0.85 R 0.96 |
| inference per frame | ~20-30 ms | ~3-6 ms |

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
