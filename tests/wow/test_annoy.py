"""ANNOY (round 5): everything that gets on the player's nerves, journey by journey (addon + app).

Addon side: tests/wow/annoy_scenario.lua (first login, masquer qui tient, donjons, actions bloquées, alerte
rejouée au /reload, combat, visites, ville, SavedVariables sur 40 jours). App side: no endless « Mettre à
jour l'addon », no full re-hash every 3 s, the character card does not blink once a minute."""
# (5.1 CLEAN) harness default = the player's profile (« sur ») ; this file tests modules that are off there: « tout »
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from treeaicoach.wow import install

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
TESTS = ROOT / "tests"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR"])
def test_annoy_scenario(flavor: str, cls: str) -> None:
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--profile", "tout", "--no-storm", "--flavor", flavor,
                        "--class", cls, "--scenario", str(ROOT / "tests/wow/annoy_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_right_click_hides_for_real() -> None:
    ui = (ADDON / "UI.lua").read_text(encoding="utf-8")
    assert "TreeAI:DismissSuggestion(e.id)" in ui


def _wow(tmp_path: Path) -> install.WowInstall:
    root = tmp_path / "World of Warcraft"
    (root / "_forever_" / "Interface" / "AddOns").mkdir(parents=True)
    (root / "_forever_" / "WowClassic.exe").write_bytes(b"")
    inst = install.find_wow(roots=[(root, "x")])
    assert inst is not None
    return inst


def _src(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "TreeAI"
    (src / "Modules").mkdir(parents=True)
    (src / "TreeAI.toc").write_text("## Interface: 11507\n## Version: 9.9\nCore.lua\n", encoding="utf-8")
    (src / "Core.lua").write_text("TreeAI = TreeAI or {}\n", encoding="utf-8")
    (src / "Modules" / "Hunter.lua").write_text("-- h\n", encoding="utf-8")
    return src


def test_no_endless_update_prompt(tmp_path: Path) -> None:
    """Files the install never copies (.DS_Store, __pycache__, .git…) made needs_update true forever."""
    inst, src = _wow(tmp_path), _src(tmp_path)
    (src / ".DS_Store").write_bytes(b"x")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "a.cpython-311.pyc").write_bytes(b"x")
    (src / "Modules" / "old.pyc").write_bytes(b"x")
    assert install.install_addon(inst, src).ok
    assert not install.needs_update(inst, src)
    assert install.install_addon(inst, src).status == "up_to_date"
    (src / "Core.lua").write_text("TreeAI = TreeAI or { v = 2 }\n", encoding="utf-8")
    assert install.needs_update(inst, src)


def test_signature_not_rehashed_every_poll(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inst, src = _wow(tmp_path), _src(tmp_path)
    install.install_addon(inst, src)
    reads = {"n": 0}
    real = Path.read_bytes

    def counting(self: Path) -> bytes:
        reads["n"] += 1
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", counting)
    assert not install.needs_update(inst, src)
    first = reads["n"]
    for _ in range(5):
        assert not install.needs_update(inst, src)
    assert reads["n"] == first                       # stat only, no re-read while nothing changed
    (src / "Core.lua").write_text("-- changed, longer\n", encoding="utf-8")
    assert install.needs_update(inst, src)


def test_character_card_does_not_blink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PySide6")
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    import test_launcher as tl

    from treeaicoach import ui as app_ui

    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(app_ui, "_report_function", lambda name: (lambda n=50: []) if name == "list_games" else None)
    app, _v, _e = tl._build(tmp_path, start_backend=False)
    try:
        app.show_page("wow")
        view = app.page_views["wow"]
        inst = SimpleNamespace(flavor_dir="X:/Jeux/World of Warcraft/_forever_", source="registre")
        rows = [("Personnage", "Chasseur niveau 26"), ("Or", "1 po")]
        # (4.0) the character lives in the « Maintenant » card: updated in place, only the age badge changes
        snap = {"summary": {"className": "Chasseur", "level": 26, "xp": 100, "xpMax": 1000, "zone": "Zone Test"},
                "char_key": "Testeur-Royaume", "sv_age": 200.0}
        view.state = {"install": inst, "has_src": True, "installed": True, "log": ("ok", "x"), "rows": rows,
                      "mode": {}, "sv_age": 200, "live": snap}
        view._render()
        hero, title, foot = view.hero, view.hero.title, view.hero.foot.text()
        assert hero.badge.text() == "/reload il y a 3 min" and "Chasseur 26" in foot
        view.state = {**view.state, "sv_age": 400, "live": {**snap, "sv_age": 400.0}}
        view._render()
        assert view.hero is hero and hero.title is title and hero.foot.text() == foot   # nothing rebuilt
        assert hero.badge.text() == "/reload il y a 6 min"
        view._installing = True
        view._render()
        assert not view.install_btn.isEnabled()        # the 3 s refresh does not re-enable it mid-install
        view._installing = False
    finally:
        app.close()
