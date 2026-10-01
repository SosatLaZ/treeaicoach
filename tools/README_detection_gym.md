# Detection gym: how we measure and improve minimap detection

Every change to detection or tracking is measured by these tools before it is kept. **Rule:** keep a change only if the gym shows a win and no regression, on both the tune suite and the holdout suite, and the real crops are not worse.

| Tool | Purpose | Time |
|---|---|---|
| `tools/det_gym.py` | Scoreboard: synthetic games on real 2026 map art, run through the REAL engine path, plus the real crops | ~40 s (`--quick` ~15 s) |
| `tools/det_micro.py` | Micro-gyms, one stage at a time: `ring`, `identity`, `stacks`, `tracker` | 1–10 s each |
| `tools/det_mine.py` | Finds the hard cases (random search of scenario parameters) and writes `det_gym_hard.json` | ~1 min / 20 trials |
| `tools/det_tune.py` | Auto-tuning of `treeaicoach/det_params.py: TUNABLE`; validated on holdout + real before it writes anything | 10+ min |
| `tools/diag_to_gym.py` | Turns a user diagnostic bundle (`diag_*.zip`) into a real test case (pseudo-labels + "needs check") | seconds |
| `tools/det_sim2real.py` | Gap between the synthetic and real images (ring colours, sizes, background, blur, JPEG) | ~5 s |
| `tools/real_minimap_bench.py` | The real labelled crops (`--dir` for any case folder) | ~10 s |
| `tools/camera_motion_bench.py` | Camera pan / jump / lock, crossings (matcher + tracker only) | ~1 min |

## 1. Run, read, compare

```bash
python tools/det_gym.py --compare --note "what I changed"      # main suite + real crops, recorded
python tools/det_gym.py --suite holdout --no-real --compare    # same kinds of games, other seeds / sides / sizes
python tools/det_gym.py --suite hard --no-real --compare       # the mined worst cases
python tools/det_gym.py -v --gallery /tmp/gal                  # failure examples + contact sheets
```

- **Recording:** every run is appended to `tools/det_gym_history.jsonl` with the git rev, suite, renderer version (`GYM_VERSION`), all metrics, the cost and two scores. `score` includes the cost; `quality` does not. Use `--no-record` for throwaway runs.
- **`--compare`:** prints the delta against the previous run and the best run of the same suite and renderer version, and flags `REGRESSION` per metric using the tolerances in `det_gym.TRACKED`. Timing is shown but never flagged, because the machines are shared.
- **Regression guard:** `tests/test_det_tools.py::test_committed_history_does_not_regress` fails when the last recorded full main run is worse than the best one beyond tolerance. So record a full run before committing a detection change.
- **Metrics:** listed in the `det_gym.py` docstring. The ones the user notices:
  - `g_live`: a visible enemy drawn as a ghost or last-seen mark.
  - `g_dead`: a dead champion drawn at all.
  - `prec`: wrong champions drawn live.
  - `me*`: my position.
  - `rec`: visible icons missed.

## 2. Understand failures in seconds: the gallery

`--gallery DIR` writes one folder per cause, with every failing frame as a ×4 annotated crop:

| Mark | Meaning |
|---|---|
| green disc + name + visible fraction | ground truth |
| yellow ring + label | what the overlay draws live |
| magenta ring + `:kind` | ghost or last-seen mark |
| red cross | the failure point |

It also writes one contact sheet per cause, `DIR/<cause>.png`. The causes are:
- `miss_nodet`: not detected at all.
- `miss_anon`: detected but not identified.
- `miss_wrongid`, `miss_elsewhere`.
- `fp`, `id`, `team`, `idsw`, `g_live`, `g_dead`, `me`.

## 3. Suites and holdout discipline

| Suite | What it contains | How to use it |
|---|---|---|
| `main` | 5 games: laning, bot fight, base siege, 3 img/s, custom-skin me | The stable comparison base. Tune here, never edit it lightly. If the renderer changes, bump `GYM_VERSION`. |
| `holdout` | The same kinds of games with other seeds, rosters, sizes and the other side | A change must also improve here; `det_tune.py` enforces it. |
| `hard` | Worst cases mined by `tools/det_mine.py`; `--merge` keeps the old ones (max 10) | Never tune on it alone. It shows where the system breaks. |

## 4. Add a case

- **Synthetic:** add a `Scenario` in `scenarios()`. Then bump `GYM_VERSION`, because the main suite changed, or put it in the hard file instead. Behaviours are the `events` entries: `goto`, `mill`, `die`, `flash`, `recall`, `camera`. The renderer knobs are `size`, `jpeg` (0 = lossless), `blur`, `ring_dark`, `duo_close`, `labels`, `pings`, `distractors` and `cam_px`.
- **Real (a user bug report):**
  1. Run `python tools/diag_to_gym.py diag_XXXX.zip`. This creates `tests/fixtures/real_cases/<name>/`.
  2. Review the `needs_check` entries in its `ground_truth.json`. Move the correct ones into `champions`, fix names, delete the wrong ones.
  3. The gym then scores every case folder ("REAL CASE" lines), and `real_minimap_bench.py --dir` runs one case.
- **Known positions:** `--positions` cross-checks the pseudo-labels against them (for example the League Client match timeline).

## 5. Tune (validated)

```bash
python tools/det_tune.py --minutes 15 [--params roster_matcher.TRACK_RELAX ...] [--write]
```

- **Search:** coordinate search on the quick main suite, using the cost-free `quality` score.
- **Validation:** the full holdout suite plus the real crops, defaults vs. best.
- **Writing:** `treeaicoach/assets/model/det_params.json` is written only with `--write` and only when validated. The matcher, tracker and detector apply it at import (`det_params.apply`); without the file the code defaults stand.
- **Log:** every evaluation is logged to `tools/det_tune_log.jsonl`.

## 6. Micro-gyms (fast loop on one stage)

```bash
python tools/det_micro.py ring        # team from ring colour vs patch verifier vs combined rule
python tools/det_micro.py identity    # portrait top-1 among the team at 200-320 px
python tools/det_micro.py stacks      # 2-4 overlapping icons, static scene: recall / identity / FP
python tools/det_micro.py tracker     # crossings with label noise: icons drawn at the wrong place, lag
```

## 7. Sim-to-real

`python tools/det_sim2real.py` compares the real crops (and the real cases) with the synthetic frames, line by line. A `DRIFT` flag means the synthetic median is outside the real [p10, p90]. Fix the renderer (`det_gym.Sim.render` and the `ENEMY_RING_RGB` / `ALLY_RING_RGB` / `FOG_DIM` constants) and bump `GYM_VERSION` before trusting gains on that aspect. v2 came from this check: v1's fog was too dark, its enemy rings too grey, and every game was JPEG'd while the app captures losslessly.

## 8. Loop

1. Run the gym with `--compare` and look at the worst metric.
2. Open the gallery for that cause and form one hypothesis.
3. Iterate on the matching micro-gym if there is one.
4. Rerun main, holdout and real (and hard).
5. Keep the change only if it is a measured win with no flagged regression. Record it with `--note`.
6. Every few rounds, mine new hard cases (`det_mine.py --merge`) and check `det_sim2real.py`.
