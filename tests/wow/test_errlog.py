"""« TreeAI-erreurs.txt » : Modules/Bugs.lua (/tai erreurs -> TreeAIDB.diag.export) + treeaicoach/wow/errlog.py.
Fake data only (no real account / character names)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow import errlog, live

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
BUGS = ADDON / "Modules" / "Bugs.lua"

DB = {
    "chars": {"Fakechar-Fakerealm": {"level": 12}},
    "diag": {
        "errors": [
            {"t": 1_800_000_000, "module": "Quests", "ctx": "Quests:QUEST_LOG_UPDATE", "msg": "Quests.lua:10: old error",
             "n": 1},
            {"t": 1_800_000_600, "module": "Hunter", "ctx": "Hunter:tick", "msg": "Hunter.lua:42: nil value", "n": 3,
             "combat": True},
        ],
        "blocked": [
            {"t": 1_800_000_100, "kind": "interdite", "func": "FakeProtected()", "ctx": "Macros:Install"},
            {"t": 1_800_000_200, "kind": "interdite", "func": "FakeProtected()", "ctx": "Macros:Install"},
            {"t": 1_800_000_300, "kind": "en combat", "func": "OtherFn()", "ctx": "UI:Show", "combat": True},
        ],
        "selftest": {"t": 1_800_000_500, "ok": 15, "warn": 1, "ko": 1, "version": "1.0.6", "build": "1.15.8.1",
                     "items": [{"id": "skills", "label": "Compétences", "status": "ko", "detail": "absent"},
                               {"id": "bags", "label": "Sacs", "status": "ok", "detail": "ok"}]},
        "macros": {"t": 1_800_000_400, "ok": 10, "ko": 2, "mode": "auto"},
        "export": {"at": 1_800_000_700, "version": "1.0.6", "build": "1.15.8.1", "interface": 11508,
                   "lines": ["TreeAI 1.0.6 · client", "== Autres addons (1) : FakeMeter 9.9"]},
    },
}


def test_build_text_french_newest_first_with_counts():
    txt = errlog.build_text(DB, now=1_800_001_000)
    assert txt.startswith("TreeAI — erreurs WoW")
    assert "/tai erreurs" in txt and "/reload" in txt
    assert "Erreurs Lua : 2 différentes, 4 au total" in txt
    assert "Actions bloquées par le jeu : 3" in txt
    assert txt.index("nil value") < txt.index("old error")  # newest first
    assert "Hunter ×3 (en combat)" in txt
    assert "FakeProtected() (interdite) ×2" in txt and "pendant : Macros:Install" in txt
    assert txt.index("OtherFn()") < txt.index("FakeProtected()")
    assert "ÉCHEC · Compétences : absent" in txt and "Sacs" not in txt
    assert "10 ok, 2 ko" in txt and "mode : auto" in txt
    assert "FakeMeter 9.9" in txt


def test_build_text_handles_lua_dict_arrays_and_garbage():
    db = {"diag": {"errors": {1: {"msg": "a", "t": "x"}, 2: "junk"}, "blocked": "nope", "selftest": 3}}
    txt = errlog.build_text(db)
    assert "Erreurs Lua : 1 différentes" in txt and "Autotest : jamais lancé" in txt
    assert "Aucune." in txt
    assert errlog.build_text(None).startswith("TreeAI")


def test_write_report_main_and_copy_only_on_change(tmp_path):
    errlog._reset()
    a, b = tmp_path / "data", tmp_path / "data" / "exports"
    p = errlog.write_report(DB, dirs=[a, b])
    assert p == a / errlog.FILE_NAME and p.is_file() and (b / errlog.FILE_NAME).is_file()
    assert "FakeProtected()" in p.read_text(encoding="utf-8")
    assert errlog.write_report(DB, dirs=[a, b]) is None            # same diagnostics: no rewrite
    db2 = {"diag": dict(DB["diag"], errors=DB["diag"]["errors"] + [{"t": 1_900_000_000, "msg": "new one"}])}
    assert errlog.write_report(db2, dirs=[a, b]) == p
    assert "new one" in p.read_text(encoding="utf-8")
    assert errlog.read_report([a]).startswith("TreeAI")


def test_write_report_needs_diag_and_never_raises(tmp_path):
    errlog._reset()
    assert errlog.write_report({}, dirs=[tmp_path]) is None
    assert errlog.write_report({"chars": {}}, dirs=[tmp_path]) is None
    assert errlog.write_report(DB, dirs=[]) is None
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert errlog.write_report(DB, dirs=[blocker / "sub"]) is None    # unwritable: None, no exception
    assert errlog.open_file(tmp_path / "missing.txt") is False


def test_default_dirs_use_user_data_folder(tmp_path, monkeypatch):
    from treeaicoach import paths

    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    paths._reset_cache()
    try:
        assert errlog.default_dirs() == [tmp_path]
        (tmp_path / "exports").mkdir()
        assert errlog.default_dirs() == [tmp_path, tmp_path / "exports"]
        assert errlog.report_path() == tmp_path / "TreeAI-erreurs.txt"
    finally:
        paths._reset_cache()


def test_livehub_writes_file_when_savedvars_change(tmp_path, monkeypatch):
    from treeaicoach import paths

    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    errlog._reset()
    try:
        sv = tmp_path / "TreeAI.lua"
        sv.write_text('TreeAIDB = {\n["diag"] = {\n["errors"] = {\n{ ["msg"] = "boom-fake", ["module"] = "Quests", '
                      '["t"] = 1800000000, ["n"] = 2 },\n},\n},\n}\n', encoding="utf-8")
        hub = live.LiveHub(savedvars=sv)
        hub.poll()
        out = tmp_path / "home" / "TreeAI-erreurs.txt"
        assert out.is_file() and "boom-fake" in out.read_text(encoding="utf-8")
    finally:
        paths._reset_cache()


def test_bugs_module_static():
    toc = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert "Modules\\Bugs.lua" in toc
    src = BUGS.read_text(encoding="utf-8")
    assert 'RegisterSlash("erreurs"' in src and 'RegisterSlash("export"' in src
    assert "TreeAI-erreurs.txt" in src and "seterrorhandler" in src and "prev(err" in src
    for bad in ("CreateMacro(", "EditMacro(", "SendChatMessage", "CastSpell"):
        assert bad not in src


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_bugs_no_globals():
    listing = subprocess.run(["luac5.1", "-l", "-p", str(BUGS)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor,cls", [("forever", "HUNTER"), ("modern", "MAGE"), ("legacy", "WARRIOR")])
def test_bugs_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/bugs_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=90, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
