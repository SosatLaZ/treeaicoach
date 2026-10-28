"""analysis_report.py (« Rapport d'analyse ») + tools/analyze_report.py on a synthetic game."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from treeaicoach import analysis_report as ar
from treeaicoach import player_intel as pi
from treeaicoach import stt_local
from treeaicoach.threat_gate import ThreatGate

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("analyze_report", ROOT / "tools" / "analyze_report.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

SECRET = ".".join(["MTAxMjM0NTY3ODkwMTIzNDU2", "Gabcde", "abcdefghijklmnopqrstuvwxyz0123"])   # fake, built at run time


@dataclasses.dataclass
class Cfg:
    player_intel: bool = True
    lcu_enabled: bool = True
    target_fps: int = 10
    discord_token: str = SECRET
    ai_api_key: str = "sk-secret-123"
    summoner_name: str = "Joueur#EUW"
    voice_level: str = "normal"


def P(alias, team, pos, level=5):
    return SimpleNamespace(champion_alias=alias, champion_name=alias, team=team, position=pos, level=level,
                           is_dead=False, items=[], riot_id="Joueur#EUW", summoner_name="Quelqu'un")


class Track:
    def __init__(self, alias, rel, pos, visible=True):
        self.key, self.alias, self.relation, self.visible = alias, alias, rel, visible
        self._pos, self.last_seen, self.score, self.id_score, self.stacked_with = pos, 0.0, 0.9, 0.8, None

    def position(self):
        return self._pos


class Engine:
    def __init__(self):
        self._lock = threading.Lock()
        self._cfg = Cfg()
        self._demo = False
        self._frame = np.full((300, 300, 3), 40, np.uint8)
        allies = [P("Garen", "ORDER", "TOP"), P("LeeSin", "ORDER", "JUNGLE"), P("Ahri", "ORDER", "MIDDLE"),
                  P("Jinx", "ORDER", "BOTTOM"), P("Lulu", "ORDER", "UTILITY")]
        enemies = [P("Darius", "CHAOS", "TOP"), P("MasterYi", "CHAOS", "JUNGLE"), P("Leblanc", "CHAOS", "MIDDLE"),
                   P("Caitlyn", "CHAOS", "BOTTOM"), P("Thresh", "CHAOS", "UTILITY")]
        self._game = SimpleNamespace(game_time=600.0, game_mode="CLASSIC", my_team="ORDER", me=allies[0],
                                     current_gold=1200, all_players=lambda: allies + enemies,
                                     allies=allies[1:], enemies=enemies)
        self._identified = []
        self._tracker = SimpleNamespace(tracks=lambda: self.tracks)
        self.tracks = []
        self._fps, self._tick_ms = 9.5, 31.0
        self._thread_cpu = SimpleNamespace(percent={"TreeAI-analysis": 34.0, "TreeAI-overlay": 6.0})
        self._threat_gate = ThreatGate()
        self._recent = [(600.0 + i, "Recule, Darius arrive" if i % 4 == 0 else f"Ligne {i}", 2, "gank")
                        for i in range(40)]

    def health(self):
        return {"performance": {"text": "Impact sur le jeu : faible", "cpu_pct": 12.0}}


def tick_game(eng, buf, n=70):
    for i in range(n):
        gt = 600.0 + i
        eng._game.game_time = gt
        x = 0.1 + 0.002 * i if i != 30 else 0.9                       # one jump of LeeSin
        eng.tracks = [Track("Garen", "me", [0.2, 0.8]), Track("LeeSin", "ally", [x, 0.5]),
                      Track("Ahri", "ally", [0.5, 0.5]), Track("Jinx", "ally", [0.8, 0.9]),
                      Track("Darius", "enemy", [0.3, 0.3])]
        if i % 10 == 0:
            eng.tracks.append(Track("Darius", "enemy", [0.6, 0.6]))      # duplicate Darius
        eng._identified = [SimpleNamespace(det=SimpleNamespace(u=0.2, v=0.8, r=6.0, score=0.93, cls=0, alias=None),
                                           alias="Garen", relation="me", id_score=0.9),
                           SimpleNamespace(det=SimpleNamespace(u=0.4, v=0.4, r=6.0, score=0.41, cls=0, alias=None),
                                           alias=None, relation=None, id_score=0.1)]
        ar.engine_tick(eng, gt, buf)


class FakeWorker:
    def transcribe(self, pcm, *, key="", prompt=None, timeout=20.0):
        return "hey coach on fait le drake"

    def submit(self, pcm, *, key="", done=None):
        if done:
            done("")


def test_report_contents_size_and_no_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(stt_local, "_WORKER", FakeWorker())
    w = SimpleNamespace(_queue_id=420, snapshot=lambda: (
        pi.PlayerIntel("Darius", "Darius", False, "TOP", True, "PLATINUM", 2, 61, 20, 17),
        pi.PlayerIntel("MasterYi", "Maître Yi", False, "JUNGLE", True, "", 0, 0, 0, 0, True, 12, -4, 0)))
    monkeypatch.setattr(pi, "_default", w)
    eng, buf = Engine(), ar.ReportBuffer()
    tick_game(eng, buf)
    assert len(buf.frames) == ar.BUFFER_S                                   # the LAST 60 s only
    eng._threat_gate.offer(605.0, SimpleNamespace(text="Darius arrive"), "gank", 2, ("Darius",), 0.1)
    eng._threat_gate.offer(605.5, SimpleNamespace(text="Darius arrive"), "gank", 2, ("Darius",), 0.1)
    stt_local._WORKER.transcribe(b"\0\0", key="pc")
    stt_local._WORKER.submit(b"\0\0", key="1234567890", done=lambda t: None)
    path = ar.request(eng, out_dir=tmp_path, buf=buf, wait_s=0.0, block=True)
    assert path is not None and path.parent == tmp_path and path.stat().st_size <= ar.MAX_BYTES
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert {"frames.json", "state.json", "frames/000.jpg", "frames/059.jpg"} <= names
        blob = b"".join(zf.read(n) for n in names if n.endswith(".json")).decode("utf-8")
        state = json.loads(zf.read("state.json"))
    for bad in ("puuid", "Joueur#EUW", SECRET, "sk-secret", "Quelqu'un", "1234567890", "riot_id"):
        assert bad not in blob, bad
    assert state["settings"]["target_fps"] == 10 and "discord_token" not in state["settings"]
    assert len(state["live_client"]["players"]) == 10
    assert len(state["coach_lines"]) == ar.COACH_LINES and state["coach_lines"][0]["rule"] == "gank"
    gate = state["threat_gate"]
    assert len(gate) == 2 and gate[0]["ok"] and gate[0]["why"] == "dite" and gate[0]["members"] == ["Darius"]
    assert all(g["why"] == "dite" or "bloquée" in g["why"] or "retenue" in g["why"] for g in gate)
    assert [t["source"] for t in state["voice"]["transcripts"]] == ["pc", "discord"]
    assert state["ranks"]["players"][0]["shown"].startswith("Darius (top) · Platine 2 · 54 % (37 parties)")
    assert state["perf"]["samples"][0]["threads"]["TreeAI-analysis"] == 34.0


def test_size_cap_thins_frames(tmp_path):
    eng, buf = Engine(), ar.ReportBuffer()
    rng = np.random.default_rng(1)
    for i in range(60):                                                      # incompressible crops
        buf.add_frame(rng.integers(0, 255, 40_000, dtype=np.uint8).tobytes(), {"t": float(i)})
    path = ar.write_report(eng, tmp_path, buf, max_bytes=1_000_000)
    assert path.stat().st_size <= 1_000_000
    st = json.loads(zipfile.ZipFile(path).read("state.json"))
    assert st["report"]["frame_step"] > 1


def test_scrub_masks_ids_and_secret_keys():
    s = ar.scrub({"puuid": "x", "champion": "Darius", "text": "vu Joueur#EUW1 et 0f8fad5b-d9cb-469f-a165-70867728950e",
                  "nested": [{"summonerName": "n", "ok": 1}]})
    assert s == {"champion": "Darius", "text": "vu <joueur> et <id>", "nested": [{"ok": 1}]}


def test_analyze_report_short_diagnosis(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "_default", SimpleNamespace(_queue_id=420, snapshot=lambda: (
        pi.PlayerIntel("Darius", "Darius", False, "TOP", True, "", 0, 0, 245, 230),
        pi.PlayerIntel("Annie", "Annie", False, "MIDDLE", True, "GOLD", 1, 0, 3, 2))))
    eng, buf = Engine(), ar.ReportBuffer()
    tick_game(eng, buf)
    path = ar.write_report(eng, tmp_path, buf)
    lines = tool.analyze(path)
    text = "\n".join(lines)
    assert len(lines) <= tool.MAX_LINES
    assert "Lulu 0 %" in text                                    # an ally never seen
    assert "doublons" in text and "Darius ×6" in text
    assert "sauts" in text and "LeeSin" in text
    assert "Darius non classé mais 475 parties" in text and "Annie n'est pas dans la partie" in text
    assert "répété ×7" in text                                  # "Recule, Darius arrive" spam
    assert "threads lourds : TreeAI-analysis 34 %" in text
    assert "pas de capture plein écran" in text
    assert tool.main([str(path)]) == 0
