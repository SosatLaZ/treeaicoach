"""wow-A: TreeAI integration of the WoW Forever helper - SavedVariables parser, install finder / addon
installer, process names, WoW mode controller, config migration, the WoW page. Fake names only."""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from treeaicoach.config import CONFIG_VERSION, Config, load_config
from treeaicoach.wow import call, install, mode, optional, process, savedvars

TESTS = Path(__file__).resolve().parents[1]

SV_TEXT = """
TreeAIDB = {
	["version"] = 1,
	["chars"] = {
		["Testrealm-Fakehunter"] = {
			["class"] = "HUNTER",
			["level"] = 26,
			["zone"] = "Contreforts de Hautebrande",
			["money"] = 123456,
			["savedAt"] = 1762500000,
			["bags"] = { ["free"] = 4, ["total"] = 64, },
		},
	},
	["list"] = { "a", 'b\\'c', [[long
string]], -- [3]
	},
	["nums"] = { 0x10, -2.5, 1e3, -inf },
	["skip"] = nil,
	["flag"] = true,
	["esc"] = "\\65\\n\\"q\\"",
}
TreeAIOther = false
--[[ trailing
comment ]]
"""


# ------------------------------------------------------------------ savedvars
def test_savedvars_parse() -> None:
    data = savedvars.parse(SV_TEXT)
    db = data["TreeAIDB"]
    assert data["TreeAIOther"] is False
    hunter = db["chars"]["Testrealm-Fakehunter"]
    assert hunter["level"] == 26 and hunter["bags"] == {"free": 4, "total": 64}
    assert db["list"] == ["a", "b'c", "long\nstring"]
    assert db["nums"][:3] == [16, -2.5, 1000.0] and db["nums"][3] == -math.inf
    assert "skip" not in db and db["flag"] is True
    assert db["esc"] == 'A\n"q"'


@pytest.mark.parametrize("bad", ["X = os.execute('rm')", "X = { 1 2 }", "X = 'open", "X = {", "= 1",
                                 "X = function() end", "X = {" * 500 + "}" * 500])
def test_savedvars_rejects_code(bad: str) -> None:
    with pytest.raises(savedvars.SavedVarsError):
        savedvars.parse(bad)


def test_savedvars_load_never_raises(tmp_path: Path) -> None:
    p = tmp_path / "TreeAI.lua"
    assert savedvars.load(p) == {} and savedvars.read_db(None) == {}
    p.write_text("broken = {", encoding="utf-8")
    assert savedvars.load(p) == {}
    p.write_text("\ufeffTreeAIDB = { [1] = 1, [3] = 3 }", encoding="utf-8")
    assert savedvars.read_db(p) == {1: 1, 3: 3}


# ------------------------------------------------------------------ process
def test_process_names() -> None:
    assert process.is_wow_running(["explorer.exe", "WowClassic.exe"])
    assert process.wow_process(["Wow.exe"]) == "wow.exe"
    assert process.is_wow_running(["WowForever.exe"]) and process.is_wow_running(["WowClassicT.exe"])
    assert not process.is_wow_running(["wowhead.exe", "Battle.net.exe"])
    assert process.is_lol_running(["League of Legends.exe"]) and not process.is_lol_running(["LeagueClient.exe"])
    assert process.wow_mode_wanted(["Wow.exe", "LeagueClient.exe"])
    assert not process.wow_mode_wanted(["Wow.exe", "League of Legends.exe"])
    assert not process.wow_mode_wanted(["Wow.exe"], lol_in_game=True)
    assert isinstance(process.process_names(lambda: ["A.exe", None]), frozenset)
    assert process.process_names(lambda: 1 / 0) == frozenset()


# ------------------------------------------------------------------ install
def _fake_wow(tmp_path: Path, flavors: tuple[str, ...] = ("_retail_", "_forever_")) -> Path:
    root = tmp_path / "World of Warcraft"
    for fl in flavors:
        (root / fl / "Interface" / "AddOns").mkdir(parents=True)
        (root / fl / "WowClassic.exe").write_bytes(b"")
    (root / "_junk_").mkdir()                       # no exe / Interface: not a client
    return root


def _fake_addon(tmp_path: Path, version: str = "0.1.0") -> Path:
    src = tmp_path / "src" / "TreeAI"
    (src / "Modules").mkdir(parents=True)
    (src / "TreeAI.toc").write_text(f"## Interface: 11507\n## Title: TreeAI\n## Version: {version}\nCore.lua\n",
                                    encoding="utf-8")
    (src / "Core.lua").write_text("TreeAI = TreeAI or {}\n", encoding="utf-8")
    (src / "Modules" / "Hunter.lua").write_text("-- hunter\n", encoding="utf-8")
    return src


def test_find_wow_prefers_forever(tmp_path: Path) -> None:
    root = _fake_wow(tmp_path)
    assert [d.name for d in install.flavors(root)] == ["_forever_", "_retail_"]
    inst = install.find_wow(roots=[(root, "dossier courant")])
    assert inst is not None and inst.flavor == "_forever_" and inst.is_forever
    assert inst.addon_dir == root / "_forever_" / "Interface" / "AddOns" / "TreeAI"
    # the user's folder wins, even pointing inside Interface/AddOns
    user = install.find_wow(roots=[(root / "_retail_" / "Interface" / "AddOns", "réglages")])
    assert user is not None and user.flavor == "_retail_" and user.source == "réglages"
    assert install.find_wow(roots=[(tmp_path / "nope", "registre")]) is None


def test_paths_in_blob() -> None:
    blob = b"\x00\x12C:\\Program Files (x86)\\World of Warcraft\x00junk D:/Games/World of Warcraft\"x"
    assert install.paths_in_blob(blob) == ["C:\\Program Files (x86)\\World of Warcraft", "D:/Games/World of Warcraft"]


def test_install_and_update_addon(tmp_path: Path) -> None:
    root = _fake_wow(tmp_path)
    inst = install.find_wow(roots=[(root, "x")])
    src = _fake_addon(tmp_path)
    assert install.needs_update(inst, src)
    r = install.install_addon(inst, src)
    assert r.ok and r.status == "installed" and (inst.addon_dir / "Modules" / "Hunter.lua").is_file()
    assert install.installed_version(inst) == "0.1.0"
    assert install.install_addon(inst, src).status == "up_to_date"
    (src / "Core.lua").write_text("TreeAI = TreeAI or { v = 2 }\n", encoding="utf-8")
    (inst.addon_dir / "stale.lua").write_text("x", encoding="utf-8")
    r = install.install_addon(inst, src)
    assert r.status == "updated" and not (inst.addon_dir / "stale.lua").exists()
    assert not list(inst.addons_dir.glob(".TreeAI.*"))           # no leftovers
    assert install.install_addon(None, src).status == "no_wow"
    assert install.install_addon(inst, tmp_path / "missing").ok is False


def test_shipped_addon_found_when_present() -> None:
    src = install.addon_source_dir()
    if src is not None:                         # wow_addon/ is written by another part of the work
        assert (src / "TreeAI.toc").is_file()


def test_game_files(tmp_path: Path) -> None:
    root = _fake_wow(tmp_path, ("_forever_",))
    inst = install.find_wow(roots=[(root, "x")])
    assert install.combat_log_status(inst)[0] == "warning"
    inst.logs_dir.mkdir()
    inst.combat_log.write_text("11/7 20:00:00.000  SPELL_DAMAGE\n", encoding="utf-8")
    assert install.combat_log_status(inst)[0] == "ok"
    assert install.combat_log_status(inst, now=time.time() + 3600)[0] == "warning"
    sv = inst.flavor_dir / "WTF" / "Account" / "FAKEACCOUNT" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "TreeAI.lua").write_text(SV_TEXT, encoding="utf-8")
    files = install.savedvars_files(inst)
    assert files == [sv / "TreeAI.lua"]
    assert savedvars.read_db(files[0])["version"] == 1


# ------------------------------------------------------------------ package helpers
def test_optional_and_call() -> None:
    assert optional("does_not_exist") is None
    assert call("does_not_exist", "f", default=7) == 7
    assert call("process", "is_wow_running", ["Wow.exe"]) is True


# ------------------------------------------------------------------ mode
class _FakeCoach:
    def __init__(self, say=None, cfg=None):
        self.say, self.started, self.ticks, self.stopped = say, None, 0, False

    def start(self, path):
        self.started = path

    def tick(self, now):
        self.ticks += 1
        self.say("Familier mort", 2)

    def stop(self):
        self.stopped = True


def test_wow_mode_switches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _fake_wow(tmp_path, ("_forever_",))
    src = _fake_addon(tmp_path)
    monkeypatch.setattr(install, "addon_source_dir", lambda: src)
    monkeypatch.setitem(sys.modules, "treeaicoach.wow.coach", SimpleNamespace(WowCoach=_FakeCoach))
    names = ["explorer.exe"]
    spoken: list[tuple[str, int]] = []
    cfg = Config(wow_path=str(root), wow_voice=True)       # opt-in automatic callouts
    lol = {"on": False}
    m = mode.WowMode(lambda: cfg, say=lambda t, lv: spoken.append((t, lv)), lol_in_game=lambda: lol["on"],
                     names_fn=lambda: names)
    m.step()
    assert not m.active and m.message() is None
    names.append("WowClassic.exe")
    m.step()
    assert m.active and m.install.flavor == "_forever_"
    assert m.install_result.status == "installed" and m.install.addon_dir.is_dir()
    assert isinstance(m.coach, _FakeCoach) and m.coach.started == m.install.combat_log
    assert spoken == [("Familier mort", 2)]
    assert m.message() == mode.MSG_NO_LOG
    cfg = Config(wow_path=str(root), wow_voice=False)
    m.step()
    assert len(spoken) == 1                      # voice off: nothing said
    coach = m.coach
    lol["on"] = True                             # a League game starts: LoL has priority
    m._last_poll = -1e9
    m.step()
    assert not m.active and coach.stopped
    lol["on"] = False
    cfg = Config(wow_path=str(root), wow_enabled=False)
    m.step()
    assert not m.active
    assert m.snapshot()["active"] is False


def test_wow_mode_without_coach_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "treeaicoach.wow.coach", None)
    m = mode.WowMode(lambda: Config(wow_addon_auto_update=False), names_fn=lambda: ["Wow.exe"])
    monkeypatch.setattr(install, "find_wow", lambda p="": None)
    m.step()
    assert m.active and m.coach is None and m.message() == mode.MSG_ACTIVE


# ------------------------------------------------------------------ config
def test_config_fields_and_migration(tmp_path: Path) -> None:
    assert CONFIG_VERSION >= 10
    c = Config()
    assert c.wow_enabled and c.wow_combatlog and c.wow_addon_auto_update and c.wow_path == ""
    assert c.wow_voice is False and c.wow_pvp_help is False             # voice only answers « Hey coach »
    bad = Config(wow_enabled="yes", wow_path=123).validated()       # type: ignore[arg-type]
    assert bad.wow_enabled is True and bad.wow_path == ""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"config_version": 9, "voice_rate": 1}), encoding="utf-8")
    assert load_config(p).wow_enabled is True
    p.write_text(json.dumps({"config_version": 10, "wow_enabled": False, "wow_path": "D:\\Jeux\\World of Warcraft"}),
                 encoding="utf-8")
    loaded = load_config(p)
    assert loaded.wow_enabled is False and loaded.wow_path == "D:\\Jeux\\World of Warcraft"


# ------------------------------------------------------------------ page (pure + Qt)
def test_page_summary_pure() -> None:
    from treeaicoach import ui_page_wow as pw

    db = savedvars.parse(SV_TEXT)["TreeAIDB"]
    rows = dict(pw.basic_summary(db))
    assert rows["Personnage"] == "Chasseur niveau 26"
    assert rows["Or"] == "12 po 34 pa 56 pc" and rows["Sacs"].startswith("4 places")
    assert pw.basic_summary({}) == [] and pw._money(5) == "5 pc"
    assert pw.summary_rows(db)                   # leveling module or the basic fallback
    st = pw.read_state(Config(wow_path="/nonexistent"))
    assert st["install"] is None


def test_page_registered_and_builds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PySide6")
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    import test_launcher as tl

    from treeaicoach import ui

    assert ("wow", "WoW Forever", "wow") in ui.PAGES
    assert any("wow" in keys for _c, keys in ui.NAV_GROUPS)
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ui, "_report_function", lambda name: (lambda n=50: []) if name == "list_games" else None)
    app, _v, _e = tl._build(tmp_path, start_backend=False)
    try:
        app.show_page("wow")
        assert app._current_page == "wow" and "wow" not in app._failed_pages
        view = app.page_views["wow"]
        view.state = {"wow_exe": "wowclassic.exe", "install": None, "has_src": True, "installed": False,
                      "log": ("warning", "x"), "rows": [("Personnage", "Chasseur niveau 26")], "mode": {}}
        view._render()
        assert view.state_row.desc.text().startswith("WoW lancé")
        assert not view.install_btn.isEnabled()
    finally:
        app.close()


# ------------------------------------------------------------------ contracts used by E / G
class _FakeSession:
    def __init__(self, log_path, say=None, on_fight_end=None, *, voice=True):
        self.log_path, self.say, self.running, self.voice = log_path, say, False, voice

    def start(self):
        self.running = True
        if self.say is not None and self.voice:
            self.say("Aggro sur un ami", 1)

    def stop(self):
        self.running = False

    def facts(self):
        return "Combat 1 : 120 DPS\nFamilier mort 1 fois\n"

    def report_text(self):
        return "Rapport"


def test_session_coach_and_engine_attrs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import treeaicoach.wow as wow

    root = _fake_wow(tmp_path, ("_forever_",))
    sv = root / "_forever_" / "WTF" / "Account" / "FAKEACCOUNT" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "TreeAI.lua").write_text('TreeAIDB = { ["schema"] = 1, ["chars"] = { ["Fakehunter-Testrealm"] = '
                                   '{ ["summary"] = { ["class"] = "HUNTER", ["className"] = "Chasseur", '
                                   '["level"] = 26, ["bagsFree"] = 10, ["bagsTotal"] = 64, ["xp"] = 50, '
                                   '["xpMax"] = 100, ["updated"] = 1762500000 } } } }', encoding="utf-8")
    monkeypatch.setitem(sys.modules, "treeaicoach.wow.coach", SimpleNamespace(WowCombatSession=_FakeSession))
    spoken: list = []
    engine = SimpleNamespace(cfg=Config(wow_path=str(root), wow_addon_auto_update=False), _in_game=False,
                             _say=lambda t, lv: spoken.append((t, lv)))
    monkeypatch.setattr(mode, "_current", None)
    m = mode.WowMode(lambda: engine.cfg, say=engine._say, names_fn=lambda: ["WowForever.exe"],
                     on_change=lambda mm: (setattr(engine, "_wow_mode", mm.active),
                                           setattr(engine, "_wow_db", mm.db), setattr(engine, "_wow_coach", mm.coach)))
    monkeypatch.setattr(mode, "_current", m)
    assert wow.wow_mode_active() is False
    m.step()
    assert wow.wow_mode_active() is True and engine._wow_mode is True
    assert isinstance(engine._wow_coach, mode.SessionCoach)
    assert engine._wow_coach.session.log_path == m.install.combat_log
    assert spoken == [] and engine._wow_coach.session.voice is False      # never speaks on its own
    assert m.snapshot()["report"] == "Rapport"
    assert engine._wow_coach.recent_facts(1) == ["Familier mort 1 fois"]
    assert engine._wow_db["schema"] == 1 and savedvars.load_treeai_db()["schema"] == 1
    from treeaicoach import ui_page_wow as pw

    rows = dict(pw.basic_summary(engine._wow_db))
    assert rows["Personnage"] == "Chasseur niveau 26" and rows["Sacs"].startswith("10 places")
    assert rows["Expérience"] == "50 %"
    session = engine._wow_coach.session
    m.stop()
    assert not session.running and engine._wow_mode is False and engine._wow_db is None
