"""postgame_edge.py: lane diffs, item timing, turning point, deaths map, profile, champion pool,
team recap (+ the report / progress / engine / bot Discord hooks). Pure and offline."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from treeaicoach import postgame_edge as pe
from treeaicoach import progress, report

FIX = Path(__file__).resolve().parent / "fixtures" / "game_record_sample.json"
EM = chr(0x2014)


def _record() -> dict:
    return json.loads(FIX.read_text(encoding="utf-8"))


def _truth() -> dict:
    """Minimal client truth: me = 1 (ORDER top), lane opponent = 6."""
    def row(g: int, xp: int, cs: int, lvl: int) -> list:
        return [0.2, 0.2, g, xp, cs, lvl]
    return {"me": 1, "lane_opponent": 6, "participants": [{"id": 1, "alias": "Garen", "name": "Garen"},
                                                          {"id": 6, "alias": "Darius", "name": "Darius"}],
            "frames": [[600.0, {"1": row(3000, 4000, 60, 8), "6": row(4200, 4600, 82, 9)}],
                       [900.0, {"1": row(5000, 7000, 100, 10), "6": row(6500, 7600, 130, 11)}]]}


# ------------------------------------------------------------------------------ 14 lane diffs
def test_lane_diffs_from_client_truth() -> None:
    d = pe.lane_diffs(_record(), _truth())
    assert d["source"] == "client" and d["opponent"] == "Darius"
    r10, r15 = d["rows"]
    assert (r10["minute"], r10["gold_diff"], r10["xp_diff"], r10["cs_diff"], r10["level_diff"]) == (10, -1200, -600, -22, -1)
    assert r15["gold_diff"] == -1500 and r15["cs_diff"] == -30
    assert d["verdict"].startswith("Voie perdue") and "30 sbires" in d["verdict"] and EM not in d["verdict"]


def test_lane_diffs_from_tab_scoreboard_and_verdicts() -> None:
    rec = _record()
    rec["scoreboard"] = {"timeline": [[598.0, 500, {"TOP": [650, 12, 1]}], [905.0, 900, {"TOP": [900, 20, 1]}]]}
    d = pe.lane_diffs(rec)
    assert d["source"] == "tab" and [r["minute"] for r in d["rows"]] == [10, 15]
    assert d["rows"][1]["xp_diff"] is None and d["verdict"].startswith("Voie gagnée")
    assert pe.lane_diffs({})["rows"] == [] and pe.lane_diffs(None)["verdict"] == ""
    assert pe._lane_verdict([{"minute": 15, "gold_diff": -100, "cs_diff": -12}], "TOP").startswith("Voie égale")


# ------------------------------------------------------------------------------ 15 item timing
def test_item_timing_and_unspent_gold() -> None:
    legend = {3078, 3071}
    snaps = []
    for i in range(0, 130):                        # one snapshot every 10 s, 0..21:30
        t = i * 10.0
        items = [1055] + ([3078] if t >= 960 else []) + ([3071] if t >= 1260 else [])
        gold = 1600 if 600 <= t < 840 else 300     # 4 min with 1600 gold in pocket while alive
        snaps.append({"game_time": t, "items": items, "gold": gold, "is_dead": False})
    rec = {"meta": {"position": "TOP"}, "snapshots": snaps}
    d = pe.item_timing(rec, "debutant", is_legendary=lambda i: i in legend)
    assert d["first_min"] == 16.0 and d["second_min"] == 21.0
    assert d["first_target"] == 14.0 and d["idle_min"] == 4.0 and d["peak_gold"] == 1600
    assert any("1er objet" in x and "plus tard" in x for x in d["lines"])
    assert any("Or qui dort" in x for x in d["lines"])
    assert pe.item_timing({})["lines"] == []
    # the real item table knows Force de la trinité (3078) as legendary
    assert pe.item_timing(rec)["first_min"] == 16.0


# ------------------------------------------------------------------------------ 19 turning point
def test_turning_point_on_sample_record() -> None:
    rec = _record()
    tp = pe.turning_point(rec)
    assert tp is not None and tp["text"].startswith("Tournant de la partie à ")
    assert "Chances de victoire estimées" in tp["text"] and EM not in tp["text"]
    if rec.get("result") == "Win":
        assert tp["after"] > tp["before"]
    assert pe.turning_point({}) is None and pe.turning_point(None) is None


def test_turning_point_baron_after_lost_fight() -> None:
    roster = [{"alias": a, "name": a, "team": t, "riot_id": f"{a}P#EUW"} for a, t in
              (("Garen", "ORDER"), ("Lux", "ORDER"), ("Darius", "CHAOS"), ("Zed", "CHAOS"))]
    roster[0]["is_me"] = True
    ev = [{"EventName": "ChampionKill", "EventTime": 300.0, "KillerName": "GarenP", "VictimName": "DariusP"},
          {"EventName": "ChampionKill", "EventTime": 1500.0, "KillerName": "ZedP", "VictimName": "GarenP"},
          {"EventName": "ChampionKill", "EventTime": 1504.0, "KillerName": "ZedP", "VictimName": "LuxP"},
          {"EventName": "BaronKill", "EventTime": 1530.0, "KillerName": "DariusP"},
          {"EventName": "TurretKilled", "EventTime": 1700.0, "KillerName": "Minion_T200",
           "TurretKilled": "Turret_TOrder_L1_P2_1"}]
    rec = {"meta": {"team": "ORDER", "riot_id": "GarenP#EUW"}, "roster": roster, "events": ev, "result": "Lose"}
    tp = pe.turning_point(rec)
    assert tp is not None and tp["time"] == "25:00" and not tp["ours"]
    assert "combat perdu 0 à 2 (tu es mort dedans) puis le Baron pour l'ennemi" in tp["text"]
    assert 0.0 < pe.win_prob(-3000, 1500) < 0.5 < pe.win_prob(3000, 600) < 1.0


# ------------------------------------------------------------------------------ many games
def _row(i: int, **kw) -> dict:
    r = {"file": f"g{i:02d}.json", "champion": "Garen", "champion_name": "Garen", "position": "TOP",
         "result": "Win" if i % 3 else "Lose", "duration": 1800.0, "cs_per_min": 5.0 + 0.1 * i, "deaths": 6,
         "vision_per_min": 0.4, "kp": 0.4, "kills": 4, "assists": 3, "obj_presence": 50.0, "enemy_half": 0.2,
         "team": "ORDER", "game_mode": "CLASSIC", "opponent": "Darius", "opponent_name": "Darius",
         "death_pts": [[0.88, 0.88, 300, 1, 0, 1], [0.86, 0.9, 500, 1, 0, 1]]}
    r.update(kw)
    return r


def test_death_zones_top_zone_sentence() -> None:
    rows = [_row(i) for i in range(5)] + [_row(9, team="CHAOS", death_pts=[[0.12, 0.12, 900, 0, 1, 0]])]
    d = pe.death_zones(rows)
    assert d["total"] == 11 and d["games"] == 6 and len(d["points"]) == 11
    assert d["points"][-1] == (0.88, 0.88)          # CHAOS game seen from the player's side
    assert d["top"]["count"] == 10
    assert d["sentence"].startswith("91 % de tes morts (10 sur 11, 6 parties) : ")
    assert "jungler ennemi invisible" in d["sentence"] and EM not in d["sentence"]
    assert pe.death_zones([])["sentence"] == ""


def test_profile_six_axes_and_trend() -> None:
    rows = [_row(i, deaths=8 - i) for i in range(8)] + [_row(99, game_mode="ARAM")]
    p = pe.profile(rows)
    assert p["games"] == 8 and [a["key"] for a in p["axes"]] == [k for k, _ in pe.AXES]
    surv = next(a for a in p["axes"] if a["key"] == "survival")
    assert surv["trend"] is not None and surv["trend"] > 0
    assert all(a["value"] is None or 0 <= a["value"] <= 100 for a in p["axes"])
    assert p["sentence"].startswith("Score global ") and "Survie : en hausse" in p["sentence"]
    sup = pe.axis_scores({"position": "UTILITY", "duration": 1800, "gold_per_min": 260, "vision_per_min": 2.0})
    assert sup["farm"] == 100 and sup["vision"] == 100 and sup["fight"] is None
    assert pe.profile([])["axes"] == []


def test_champion_pool_lines() -> None:
    rows = [_row(i, result="Win") for i in range(8)]
    rows += [_row(20 + i, result="Lose", opponent="Vayne", opponent_name="Vayne") for i in range(3)]
    rows += [_row(30 + i, champion="Swain", champion_name="Swain", result="Lose") for i in range(5)]
    pool = pe.champion_pool(rows)
    garen = pool["champions"][0]
    assert garen["games"] == 11 and garen["wins"] == 8
    assert garen["line"] == "Garen : 73 % sur 11 parties, KDA 1,2, évite contre Vayne (0/3)"
    assert "Joue Garen en priorité" in pool["advice"]
    assert pe.champion_pool([])["champions"] == []


def test_progress_rows_carry_features_without_riot_ids(tmp_path: Path) -> None:
    rec = _record()
    (tmp_path / "2026-09-12_2031_Garen.json").write_text(json.dumps(rec), encoding="utf-8")
    rows = progress.collect(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["team"] == "ORDER" and r["game_mode"] == "CLASSIC" and isinstance(r["death_pts"], list)
    assert r["death_pts"] and r["kp"] is not None and r["enemy_half"] is not None
    cache = (tmp_path / progress.CACHE_NAME).read_text(encoding="utf-8")
    for p in rec["roster"]:
        for k in ("riot_id", "summoner_name"):
            if p.get(k) and p[k] != p.get("name"):
                assert p[k] not in cache and p[k].split("#")[0] not in cache
    h = pe.load_history(tmp_path)
    assert h["games"] == 1 and h["pool"]["champions"][0]["champion"] == "Garen"
    assert pe.load_history(tmp_path, until="2026-01-01")["games"] == 0


# ------------------------------------------------------------------------------ 20 team recap
def _recap_record() -> dict:
    rec = _record()
    rec["scoreboard"] = {"final": {"ally_kills": 20, "players": [
        {"alias": "Garen", "name": "Garen", "side": "self", "role": "TOP", "kills": 5, "deaths": 2, "assists": 4,
         "cs_per_min": 7.1, "ward_score": 15},
        {"alias": "MonkeyKing", "name": "Wukong", "side": "ally", "role": "JUNGLE", "kills": 8, "deaths": 1,
         "assists": 6, "cs_per_min": 5.9, "ward_score": 30},
        {"alias": "Jinx", "name": "Jinx", "side": "ally", "role": "BOTTOM", "kills": 2, "deaths": 9,
         "assists": 2, "cs_per_min": 4.0, "ward_score": 8},
        {"alias": "Darius", "name": "Darius", "side": "enemy", "role": "TOP", "kills": 9, "deaths": 1,
         "assists": 1, "cs_per_min": 8.0, "ward_score": 8}]}}
    return rec


def test_team_recap_message() -> None:
    rec = _recap_record()
    msg = pe.team_recap(rec, {"MonkeyKing": "Paul", "Jinx": "@everyone", "Darius": "Ennemi", "Lux": "Absent"})
    lines = msg.splitlines()
    assert lines[0].startswith("**TreeAI Coach · ")
    assert any(x.startswith("• **Paul** (Wukong, 8/1/6) : ") for x in lines)
    jinx = next(x for x in lines if "(Jinx," in x)
    assert "@​everyone" in jinx and "à corriger : " in jinx and "9 morts" in jinx
    assert "Ennemi" not in msg and "Absent" not in msg       # enemies / absent champions never listed
    for p in rec["roster"]:
        assert not p.get("riot_id") or p["riot_id"] not in msg
    assert len(msg) <= 1900 and EM not in msg
    assert pe.team_recap(rec, {}) == "" and pe.team_recap({}, {"Jinx": "x"}) == ""


def test_engine_posts_team_recap(tmp_path: Path) -> None:
    from treeaicoach.engine_postgame import PostgameMixin

    path = tmp_path / "g.json"
    path.write_text(json.dumps(_recap_record()), encoding="utf-8")
    posted: list[str] = []
    eng = SimpleNamespace(_cfg=SimpleNamespace(discord_team_recap=True), _demo=False,
                          recap_friends=lambda: {"Jinx": "Léa"}, post_text=posted.append)
    friends = PostgameMixin._recap_friends(eng)   # type: ignore[arg-type]
    assert friends == {"Jinx": "Léa"}
    PostgameMixin._post_team_recap(eng, path, friends)   # type: ignore[arg-type]
    assert len(posted) == 1 and "**Léa** (Jinx" in posted[0]
    eng._cfg.discord_team_recap = False
    assert PostgameMixin._recap_friends(eng) == {}   # type: ignore[arg-type]
    assert not path.with_suffix(".html").exists()      # nothing else written


class _PostBackend:
    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []
        self.current = "vocal"

    def current_channel(self):  # noqa: ANN201
        return self.current

    async def start(self, token: str) -> tuple[str, str]:
        return "TreeAI", "123456789012345678"

    def alive(self) -> bool:
        return True

    def guild_count(self) -> int:
        return 1

    def find_channel(self, user_id: str):  # noqa: ANN201
        return self.current

    def channel_name(self, ch) -> str:  # noqa: ANN001
        return str(ch)

    def can_speak(self, ch) -> bool:  # noqa: ANN001
        return True

    async def join(self, ch) -> None:  # noqa: ANN001
        self.current = ch

    async def play(self, pcm: bytes) -> None:
        await asyncio.sleep(0)

    async def post(self, ch, text: str) -> None:  # noqa: ANN001
        self.posts.append((str(ch), text))

    async def leave(self) -> None:
        self.current = None

    async def close(self) -> None:
        return None


def test_bot_posts_text_in_voice_channel_chat() -> None:
    from treeaicoach import discord_bot as db

    backends: list[_PostBackend] = []

    def factory() -> _PostBackend:
        backends.append(_PostBackend())
        return backends[-1]
    c = db.DiscordCaller(backend_factory=factory, speech=lambda *_a: b"", beep=lambda: b"")
    try:
        assert c.post_text("x") is False                    # bot off
        assert c.teammates() == {}
        c.configure(SimpleNamespace(discord_enabled=True, discord_token="x" * 24 + "." + "y" * 6 + "." + "z" * 27,
                                    discord_user_id="123456789012345678", neural_voice="", neural_rate=""))
        assert c.post_text("Bilan **test**") is True
        end = time.monotonic() + 5
        while time.monotonic() < end and not (backends and backends[-1].posts):
            time.sleep(0.02)
        assert backends and backends[-1].posts == [("vocal", "Bilan **test**")]
        assert c.post_text("   ") is False
    finally:
        c.shutdown(2.0)


# ------------------------------------------------------------------------------ report
def test_report_renders_edge_sections(tmp_path: Path) -> None:
    rec = _recap_record()
    html = report.render_report_html(rec)
    assert "Ta partie en chiffres" in html and "Tournant de la partie" in html
    a = dict(edge=pe.game_edge(rec, _truth()),
             edge_history=pe.history_edge([_row(i) for i in range(6)]))
    from treeaicoach.analysis import analyze_game

    full = report.render_report_html(rec, dict(analyze_game(rec), **a))
    assert "Ta progression" in full and "Où tu meurs" in full and "Tes champions" in full
    assert "chiffres exacts du client" in full and "Voie perdue" in full
    png = report.render_death_heat_png(rec, [(0.5, 0.5), (0.52, 0.5)], 120)
    assert png is not None and png[:4] == b"\x89PNG"
    assert report.render_death_heat_png(rec, []) is None
    p = tmp_path / "2026-09-12_2031_Garen.json"
    p.write_text(json.dumps(rec), encoding="utf-8")
    out = report.write_report(p)
    assert out is not None and "Ta progression" in out.read_text(encoding="utf-8")


# ------------------------------------------------------------------------------ 21 farm gap / time dead / levels
def _snap_record(role: str = "TOP", dur: float = 1500.0, cs_per_min: float = 4.0, dead=(), start: float = 0.0,
                 **meta) -> dict:
    snaps = []
    t = start
    while t <= dur:
        snaps.append({"game_time": t, "cs": int(cs_per_min * t / 60.0), "level": min(18, 1 + int(t // 90)),
                      "gold": 300, "items": [], "deaths": len(dead), "is_dead": any(a <= t < b for a, b in dead)})
        t += 10.0
    return {"meta": dict({"position": role, "team": "ORDER", "game_mode": "CLASSIC"}, **meta), "duration": dur,
            "snapshots": snaps}


def test_farm_gap_in_gold_with_the_live_targets() -> None:
    d = pe.farm_gap(_snap_record(cs_per_min=4.0), "debutant")
    assert d["target"] == 5.5 and d["cs_per_min"] == 4.0 and d["missed_cs"] == 38   # 25 min x 1,5
    assert d["missed_gold"] == 750 and d["cs10"] == 40 and d["cs10_target"] == 47
    assert d["line"].startswith("Farm : 4,0 CS/min pour une cible de 5,5 : environ 38 sbires, soit 800 PO")
    assert "À 10 min : 40 sbires (cible 47)" in d["line"] and EM not in d["line"]
    assert pe.farm_gap(_snap_record(cs_per_min=6.0))["line"] == ""                  # on target: nothing
    assert pe.farm_gap(_snap_record("UTILITY")) == {}                               # no farm target
    assert pe.farm_gap(_snap_record(start=600.0))["cs10"] is None                    # app started mid-game
    assert pe.farm_gap({}) == {} and pe.farm_gap(None) == {}


def test_time_dead_and_farm_lost() -> None:
    d = pe.time_dead(_snap_record(dead=((300, 330), (600, 640), (900, 950), (1200, 1260))))
    assert d["dead_s"] == 180 and d["deaths"] == 4 and d["farm_lost_cs"] == 14    # 100 CS in 22 min alive
    assert d["line"].startswith("Temps mort : 3:00 au total (12 % de la partie, 4 morts)") and "14 sbires" in d["line"]
    assert pe.time_dead(_snap_record(dead=((300, 330),)))["line"] == ""              # one short death
    assert "sbires" not in pe.time_dead(_snap_record("UTILITY", dead=((300, 400), (600, 700))))["line"]
    assert pe.time_dead({}) == {}


def test_level_race_truth_and_tab() -> None:
    def row(lvl: int) -> list:
        return [0.2, 0.2, 0, 0, 0, lvl]
    frames = [[60.0 * m, {"1": row(min(m, 9)), "6": row(min(m + 1, 10))}] for m in range(1, 16)]
    truth = {"me": 1, "lane_opponent": 6, "frames": frames,
             "participants": [{"id": 1, "name": "Garen"}, {"id": 6, "name": "Darius"}]}
    d = pe.level_race(_snap_record(), truth)
    assert d["behind"] == (1, 15) and d["line"].startswith("Niveaux : en retard sur Darius de 1 à 15 min.")
    rec = _snap_record()
    rec["roster"] = [{"team": "CHAOS", "position": "TOP", "name": "Darius"}]
    rec["scoreboard"] = {"timeline": [[60.0 * m, 0, {"TOP": [0, 0, 1 if 3 <= m <= 8 else 0]}] for m in range(1, 16)]}
    d2 = pe.level_race(rec)
    assert d2["ahead"] == (3, 8) and "en avance sur Darius de 3 à 8 min" in d2["line"]
    assert pe.level_race(_snap_record("JUNGLE"), truth) == {} and pe.level_race({}) == {}


def test_game_edge_off_for_remakes_and_other_modes() -> None:
    remake = _snap_record(dur=200.0)
    e = pe.game_edge(remake)
    assert e["lines"] == [] and e["items"]["lines"] == [] and e["turning_point"] is None
    assert pe.game_edge(_snap_record(game_mode="ARAM"))["lines"] == []
    assert pe.game_edge(_snap_record(game_mode="SWIFTPLAY"))["farm"]["target"] == 5.5
    # a surrender at 13 min: no "Aucun objet légendaire terminé" (the target is 14 min)
    assert not any("Aucun objet" in x for x in pe.game_edge(_snap_record(dur=780.0))["items"]["lines"])
    assert any("Aucun objet" in x for x in pe.game_edge(_snap_record(dur=1500.0))["items"]["lines"])


def test_item_timing_unknown_when_the_app_started_late() -> None:
    legend = {3078}
    rec = _snap_record(start=900.0)
    for s in rec["snapshots"]:
        s["items"] = [3078]
    d = pe.item_timing(rec, is_legendary=lambda i: i in legend)
    assert d["first_min"] is None and not any("1er objet" in x or "Aucun objet" in x for x in d["lines"])


def test_profile_counts_swiftplay_games() -> None:
    rows = [_row(i, game_mode="SWIFTPLAY") for i in range(4)] + [_row(9, game_mode="ARAM")]
    assert pe.profile(rows)["games"] == 4


def test_report_shows_farm_dead_levels_block() -> None:
    rec = _snap_record(dead=((300, 360), (600, 660), (900, 960), (1200, 1260)))
    html = report.render_report_html(rec, {"edge": pe.game_edge(rec)})
    assert "Farm, morts et niveaux" in html and "Temps mort : 4:00" in html
    assert "Farm, morts et niveaux" not in report.render_report_html(_snap_record(dur=200.0))
