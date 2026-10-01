"""Stand-alone launcher stress run (no pytest: works with the Windows Python under Wine).
usage: python stress_wine.py SRC_ROOT SEED STEPS [SWITCH_MAX_S]"""
import importlib, logging, os, sys, tempfile, time, types
from pathlib import Path

src, seed, steps = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
os.environ["TREEAI_STRESS_SWITCH_MAX"] = sys.argv[4] if len(sys.argv) > 4 else "0.3"
sys.path.insert(0, src)
sys.path.insert(0, os.path.join(src, "tests"))

# minimal pytest stand-in (decorators only)
pt = types.ModuleType("pytest")
class _Mark:
    def __getattr__(self, name):
        return lambda *a, **k: (lambda f: f)
pt.mark = _Mark()
pt.fixture = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
pt.importorskip = lambda name: importlib.import_module(name)
pt.approx = lambda v, **k: v
pt.MonkeyPatch = object
pt.LogCaptureFixture = object
sys.modules["pytest"] = pt

tmp = Path(tempfile.mkdtemp(prefix="treeai_stress_"))
os.environ["TREEAICOACH_HOME"] = str(tmp / "home")
from treeaicoach import paths
paths._reset_cache()
import test_ui as tu
import test_ui_stress as ts
from treeaicoach import ui

games_dir = tmp / "home" / "games"
games_dir.mkdir(parents=True, exist_ok=True)
games = ts._games(games_dir, 6)
real = ui._report_function
ui._report_function = lambda name: {"list_games": lambda n=50: games}.get(name) or real(name)
ui.webbrowser.open = lambda url: None
errors = ts._Errors()
logging.getLogger("treeaicoach").addHandler(errors)
tk_errors = []
t0 = time.perf_counter()
app, _voice, (engines, _ov) = tu._build(tmp)
print("window built in %.0f ms" % (1000 * (time.perf_counter() - t0)), flush=True)
orig = app._tk_exception
app.root.report_callback_exception = lambda et, ev, tb: (tk_errors.append(f"{et.__name__}: {ev}"), orig(et, ev, tb))
try:
    problems, slow = ts.run_stress(app, engines, seed, steps, games_dir)
finally:
    app.close()
print("TK ERRORS", len(tk_errors), *tk_errors[:8], sep="\n  ")
print("LOGGED ERRORS", len(errors.records), *[r[:600] for r in errors.records[:8]], sep="\n  ")
print("PROBLEMS", len(problems), *problems[:20], sep="\n  ")
print("total %.1f s" % (time.perf_counter() - t0))
