import cProfile, pstats, io, os, sys, tempfile, time, importlib, types
from pathlib import Path
src = sys.argv[1]
sys.path.insert(0, src); sys.path.insert(0, os.path.join(src, "tests"))
pt = types.ModuleType("pytest")
class _Mark:
    def __getattr__(self, n): return lambda *a, **k: (lambda f: f)
pt.mark = _Mark(); pt.fixture = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
pt.importorskip = lambda n: importlib.import_module(n); pt.approx = lambda v, **k: v
sys.modules["pytest"] = pt
tmp = Path(tempfile.mkdtemp()); os.environ["TREEAICOACH_HOME"] = str(tmp / "home")
import test_ui as tu, test_ui_stress as ts
from treeaicoach import ui
ui.PREBUILD_DELAY_MS = 0
gd = tmp / "home" / "games"; gd.mkdir(parents=True)
games = ts._games(gd, 15)
real = ui._report_function
ui._report_function = lambda name: {"list_games": lambda n=50: games}.get(name) or real(name)
app, _v, _ = tu._build(tmp)
tu._pump(app, 3.0)
for key, tab in (("analysis", None), ("help", None), ("settings", None), ("settings", "Voix"), ("settings", "Affichage"), ("settings", "Avancé")):
    pr = cProfile.Profile(); t0 = time.perf_counter(); pr.enable()
    app.show_page(key, tab); app.root.update_idletasks()
    pr.disable(); dt = time.perf_counter() - t0
    s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(12)
    print(f"===== {key}/{tab}: {1000*dt:.0f} ms"); print("\n".join(s.getvalue().splitlines()[6:22]))
    tu._pump(app, 1.0)
app.close()
