# Detection gym: how we measure and improve minimap detection

Every change to detection or tracking is measured by these tools before it is kept. **Rule:** keep a change only if the gym shows a win and no regression, on both the tune suite and the holdout suite, and the real crops are not worse.

| Tool | Purpose | Time |
|---|---|---|
| `tools/det_gym.py` | Scoreboard: synthetic games on real 2026 map art, run through the REAL engine path, plus the real crops | ~40 s (`--quick` ~15 s) |
| `tools/det_fast.py` | The same numbers as `det_gym.py` + the real bench, from caches (rendered frames, detector stage tape, ONNX outputs, real images), 3 processes, exactly reproducible; parameter sweeps and a hard subset | warm: all suites + real in ~13 s (det_gym: ~9 min) |
| `tools/det_micro.py` | Micro-gyms, one stage at a time: `ring`, `identity`, `stacks`, `tracker` | 1–10 s each |
| `tools/det_mine.py` | Finds the hard cases (random search of scenario parameters) and writes `det_gym_hard.json` | ~1 min / 20 trials |
| `tools/det_tune.py` | Auto-tuning of `treeaicoach/det_params.py: TUNABLE`; validated on holdout + real before it writes anything | 10+ min |
| `tools/diag_to_gym.py` | Turns a user diagnostic bundle (`diag_*.zip`) into a real test case (pseudo-labels + "needs check") | seconds |
| `tools/det_sim2real.py` | Gap between the synthetic and real images (ring colours, sizes, background, blur, JPEG) | ~5 s |
| `tools/real_minimap_bench.py` | The real labelled crops (`--dir` for any case folder) | ~10 s |
| `tools/camera_motion_bench.py` | Camera pan / jump / lock, crossings, bot-lane stacks under a sweeping camera (`stackpan`), icons at the rectangle corners (`corner`), camera dragged over stacks (`dragstack`); `arc` = ring colour kept by the line cleaning | ~2 min |
| `tools/real_truth_case.py` | Real minimap captures of one game (`collect/*.png`, wall-clock file names) + its record + its LCU truth -> a labelled case folder (`tests/fixtures/real_cases/<name>`): wall clock -> game time offset fitted on the record's own sightings, truth positions (minute frames, kills, interpolation when the champion barely moved) snapped to the icon rings, bot-lane duo stacks named, `unsure` zones (truth says a champion is around, no icon labelled: ignored by the bench), `dead` from the kills. Sanitized labels only | ~4 min (detector pass, cached) |
| `tools/record_vs_truth.py` | Our own game records (`games/*.json`) vs the LCU truth (`games/truth/*.truth.json`): wrong identity, team swap, phantom, stale, drawn while dead, sighting gaps, my position (stacked or not), anonymous tracks; ranked failure modes. Never prints riot ids | seconds |

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

## 1b. Fast loop: `det_fast.py` (cached, parallel, reproducible)

```bash
export DET_FAST_CACHE=/somewhere/simcache        # default <tmp>/det_fast_cache; rebuilt on demand
python tools/det_fast.py run                      # main+hard+botlane+mirror+holdout + real crops + real cases
python tools/det_fast.py run --suites main --no-real --set tracker.X=0.5
python tools/det_fast.py sweep --param roster_matcher.X=a,b,c [--param tracker.Y=1,2]   # cartesian product
python tools/det_fast.py hard --build             # hard subset from the current run (HEAD)
python tools/det_fast.py sweep --subset hard --corr --param ...   # quick score + its correlation with the full one
python tools/det_fast.py verify --gym-json main.json hard.json ...   # equivalence + reproducibility
python tools/det_fast.py info | clean
```

- **What is cached** (all under `$DET_FAST_CACHE`, delete it any time, it is rebuilt on demand by the next run; `info` prints the sizes, ~560 MB for every suite + real):
  - `frames/`: every game rendered once (lossless PNG frames + truth + game info) per scenario, renderer source (`det_gym.py` up to the pipeline, `training/real_art.py`, `render.py`, `champions.py`), `GYM_VERSION` and library versions.
  - `tape/`: the detector stage of a game: every call the engine makes on the detector / roster matcher / icon learner, with its result, and every attribute it reads, per (frames, detector key). The **detector key** hashes the sources of `det_fast.DET_MODULES` (detector, roster_matcher, self_icon, ...), their current module constants, the model assets and the library versions. So a `tracker` / `engine` / overlay knob or a `tracker.py` edit replays the detector from the tape; a `roster_matcher` knob or a detector-side edit re-runs the detector (and records a new tape).
  - `onnx/`: raw ONNX outputs per (model hash, input tensor hash) (`detector.STAGE_CACHE` hook): a re-run of the detector skips the CNN.
  - `real/`: real-bench image results per (image bytes, labels, roster, detector key).
- **Safety:** the replay checks every call (name + hash of the arguments: the frame, the game info) and every attribute read against the tape. Any difference (e.g. a downstream change makes the engine call the detector differently) raises `Divergence` and that game is re-run for real (counted in the `divergences` line). A tape whose returned results were changed afterwards by the detector is flagged `unsafe`.
- **Reproducible:** the simulated clock (`det_gym.CLOCK_MODULES`: roster matcher, self icon, identifier, detector), `PYTHONHASHSEED=0` (the tool re-executes itself), one thread per process. Two runs give byte-identical metrics; `ms` / `cpu` / wall times are kept apart (`timing`, `stats`).
- **Equivalence (HEAD, 2026-10-29):** `verify` against `det_gym.py --json` of main (+ real crops + 5 real cases), hard, botlane, mirror, holdout: every game metric, every `fails` entry and every real number identical; the uncached pool run identical too; two cached runs byte-identical. Leak fixed 2026-10-29: `self_icon`'s colour tables (and their rebuild schedule) were a module-level cache shared by every game / image of a process, so a game's numbers depended on the games run before it (holdout `h_slow3`, real images); they now live on each game's ring colour model. `python tools/det_leak.py <suite>|real` runs every game / image in order, reversed and alone (detector re-run) and lists any difference (all suites + real: 0). So `det_fast` runs ONE game / real image per task (fresh fork): the parallelism is per game.
- **Times (this container, 4 shared cores, 3 processes):** det_gym.py main + real 316 s, hard 27 s, botlane 117 s, mirror 24 s, holdout 52 s = 535 s. det_fast cold (cache build) 256 s (2.1x, bound by the sequential real bench, 267 s alone), warm 11-14 s (~40x), main alone warm 4 s; a sweep point of a downstream knob (tracker / engine / overlay) on everything incl. real: 11.6 s (46x); a detector knob re-runs the detector (cold cost, ONNX skipped).
- **Hard subset:** windows around every frame where HEAD fails (any `fails` entry) plus a fixed 5 % random control set, each run by a fresh pipeline from `PRE_ROLL_S` = 2 s earlier. Built by `hard --build`; `sweep --subset hard --corr` prints the subset / full Pearson and Spearman over the sweep points (not measured yet on past variants: the history entries are other code revisions, whose tapes do not exist). **Measured 2026-10-29 (after the leak fix): do NOT use it.** HEAD fails on ~half the frames, so the windows + their 2 s pre-roll cover 90 % of the frames (`--fail-frac 0.1`: a fixed random 10 % of the failing frames, still 69 %): a point costs as much as the full suites (subset 110 s vs full 100 s per detector-knob point). Correlation with the full quality: tracker knobs (STACK_HOLD_S x ASSOC_DIST, 5 points) Pearson 1.00, but detector knobs (roster_matcher THR_MARGIN x STACKV_MIN, 5 points) Pearson -0.60 / Spearman -0.60: the fresh-pipeline windows miss the calibration / learning history the matcher knobs act on. Score every point on the full suites.
- **Not cached:** a detector-side knob still re-runs the roster matcher (the expensive part, ~65 % of a cold run) on every game: use `--subset hard` for a quick score first, then the full sweep point. `det_gym.py` stays the reference that records the history.

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
| `main` | (renderer v3: our overlay labels only on their first frame, as overlay_guard leaves them) 5 games: laning, bot fight, base siege, 3 img/s, custom-skin me | The stable comparison base. Tune here, never edit it lightly. If the renderer changes, bump `GYM_VERSION`. |
| `holdout` | The same kinds of games with other seeds, rosters, sizes and the other side | A change must also improve here; `det_tune.py` enforces it. |
| `hard` | Worst cases mined by `tools/det_mine.py`; `--merge` keeps the old ones (max 10) | Never tune on it alone. It shows where the system breaks. |
| `botlane` | Me = support glued on my ADC (0.1-0.7 icon diameter, measured on the LCU truth of real support games), enemy duo glued, camera locked / free / panning / jumping; two games replayed from the real Swain game (`tests/fixtures/lcu_truth`: roster, positions, the kills as fights + deaths) | Columns `stk` (champions mostly under another icon still reported at the stack), `rst` (self-check "X vu à deux endroits" resets), `meDd` (my position drawn while dead). |
| `start` | Game start (0:00, everybody in the fountain) with a wrong icon scale stored by a previous game (0.08-0.108) | Column `scl` = mean abs(log(scale / true scale)). |

## 4. Add a case

- **Synthetic:** add a `Scenario` in `scenarios()`. Then bump `GYM_VERSION`, because the main suite changed, or put it in the hard file instead. Behaviours are the `events` entries: `goto`, `mill`, `die`, `flash`, `recall`, `camera`. The renderer knobs are `size`, `jpeg` (0 = lossless), `blur`, `ring_dark`, `duo_close`, `labels`, `pings`, `distractors` and `cam_px`; game knobs `me_role`, `glue`, `me_draw`, `truth` + `truth_t` (replay an LCU truth file), `start="fountain"`, `scale_prior`; event `glue` (re-stack on the ADC). Every game also runs the self-check rule 5 (identity resets) as the engine does. `BENCH_PKG_ROOT=<dir>` runs another copy of the `treeaicoach` package (before / after).
- **Real (a user bug report):**
  1. Run `python tools/diag_to_gym.py diag_XXXX.zip`. This creates `tests/fixtures/real_cases/<name>/`.
  2. Review the `needs_check` entries in its `ground_truth.json`. Move the correct ones into `champions`, fix names, delete the wrong ones.
  3. The gym then scores every case folder ("REAL CASE" lines), and `real_minimap_bench.py --dir` runs one case.
- **Real with LCU truth (the app's own captures):** `python tools/real_truth_case.py collect/ --record games/X.json --truth games/truth/X.truth.json --out tests/fixtures/real_cases/X --keep 40 --out-all /scratch/X_all --preview /scratch/prev`, then LOOK at a few previews (labels on the icons). `--keep` limits the repo to the most useful frames (labels, duo stacks, anchored frames, spread over the game); `--out-all` keeps every frame outside the repo for `real_minimap_bench.py --dir`. The first set: `tk_botlane_20261005` (TahmKench support, 40 frames / 114 labels in the repo, 153 frames / 133 labels in the scratchpad). No record for that game (the app was off): `--start <ISO>` = capture file time - the in-game clock read on a full screenshot (`*_full.jpg`) of the same capture (checked on the Seraphine game: 0.5 s from the record's fit). A Yuumi snapped onto a teammate's icon becomes an `unsure` zone (attached = not drawn). Cases: `tk_botlane_20261005`, `garen_top_20261023`, `seraphine_support_20261028`, `nocturne_jungle_20261024`, `teemo_top_20261024` (the last three: captures every 60 s, kept frames <= 3 MB, all stacks reviewed).
- **Review the snapped labels:** the LCU truth gives one position per minute, so two or three champions near one icon are all snapped onto it as `stacked`. Look at the stacked / missed frames (one ring or two?) and write the corrections in `<case>/review.json` (`{"frames": {name: {"why", "champions", "unsure_add"}}}`); `python tools/real_truth_case.py --apply-review <case>` applies it, and a rebuild re-applies it. Both committed cases are reviewed (16 frames).
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
python tools/det_micro.py duo         # permanent duo stacks + split: together / me / resets / split_ok
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
