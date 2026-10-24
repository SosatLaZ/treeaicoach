"""Call evaluation loop (call_eval.py): followed or not, what followed, bounded auto-quiet, reset."""

from types import SimpleNamespace

from treeaicoach import call_eval as CE


def _record(calls=(), alerts=(), path=None, deaths=()):
    """ORDER player walking from mid (0.5, 0.5) along ``path(t) -> (u, v)``; deaths = game times."""
    path = path or (lambda t: (0.5, 0.5))
    pos = [[float(t), *path(t)] for t in range(60, 900)]
    snaps = []
    for t in range(60, 900, 10):
        snaps.append({"game_time": float(t), "deaths": sum(1 for d in deaths if d <= t), "gold": 500,
                      "ward_score": 5.0 if t < 400 else 6.0, "items": [], "cs": 0, "level": 5})
    return {"meta": {"team": "ORDER"}, "summary": {"team": "ORDER"}, "my_positions": pos, "snapshots": snaps,
            "calls": [list(c) for c in calls], "alerts": [list(a) for a in alerts], "events": [], "roster": [],
            "duration": 900.0}


def test_intents():
    assert CE.intent_of("personal_danger") == "retreat"
    assert CE.intent_of("macro_tip", "Recule vers ta tour : Yone sur toi") == "retreat"
    assert CE.intent_of("macro_tip", "Va en bas : dragon dans 40 s") == "goto"
    assert CE.intent_of("macro_tip", "Balise la rivière en bas : Dragon dans 1:00") == "ward"
    assert CE.intent_of("macro_tip", "Smolder seul et plus faible : tu peux le punir") is None
    assert CE.intent_of("wave_recall", "Rentre acheter Phage") == "recall"
    assert CE.intent_of("gc_obj_stay", "Ne rentre pas : Dragon dans 0:40") == "stay"
    assert CE.stat_kind("macro_tip", "Recule !") == "tip:retreat"


def test_followed_and_outcomes():
    # retreat at 200 s followed (walks home), goto dragon at 400 s ignored (stays mid), ward at 350 s followed
    def path(t):
        return (0.5 - 0.01 * (t - 200), 0.5 + 0.01 * (t - 200)) if 200 <= t <= 215 else \
            ((0.35, 0.65) if t > 215 else (0.5, 0.5))
    rec = _record(alerts=[[200.0, "personal_danger", 2, "Recule !", None],
                          [400.0, "macro_tip", 1, "Va en bas : dragon dans 40 s", None],
                          [350.0, "macro_tip", 1, "Pose des balises au Baron puis prends-le", None],
                          [500.0, "macro_tip", 1, "Smolder seul : tu peux le punir", None]], path=path)
    ev = {e["kind"]: e for e in CE.evaluate_record(rec)}
    assert ev["personal_danger"]["followed"] and ev["personal_danger"]["useful"]
    assert ev["tip:goto"]["followed"] is False and not ev["tip:goto"]["useful"]
    assert ev["tip:ward"]["followed"] is True
    assert len(ev) == 3
    head, tail = CE.summary_line(list(ev.values()))
    assert head == "Conseils utiles : 2/3"


def test_logged_call_replaces_its_alert_copy_and_death_is_not_a_retreat():
    rec = _record(calls=[[300.0, "rotate_mid", "Va mid : ta tour est tombée", 0.5, 0.5]],
                  alerts=[[300.0, "macro_tip", 1, "Va mid : ta tour est tombée", None]],
                  path=lambda t: (0.2, 0.8))
    calls = CE.calls_from_record(rec)
    assert [c["kind"] for c in calls] == ["rotate_mid"] and calls[0]["target"] == (0.5, 0.5)


def test_auto_quiet_bounded_slow_and_reset(tmp_path, monkeypatch):
    ignored = [{"kind": "tip:goto", "followed": False, "useful": False, "died": False} for _ in range(16)]
    danger = [{"kind": "personal_danger", "followed": False, "useful": False, "died": False} for _ in range(16)]
    for i in range(6):
        d = CE.update_stats(tmp_path, f"g{i}.json", ignored + danger, order=i)
    assert d["scale"]["tip:goto"] == CE.MAX_SCALE                  # 1.5, 2.25, 3.0 (cap)
    assert d["scale"]["personal_danger"] == CE.DANGER_MAX_SCALE   # never muted: x1.5 at most
    assert CE.update_stats(tmp_path, "g5.json", ignored)["scale"] == d["scale"]   # same game: no new step
    followed = [{"kind": "tip:goto", "followed": True, "useful": True, "died": False}] * 200
    d2 = CE.update_stats(tmp_path, "g6.json", followed, order=6)
    assert d2["scale"]["tip:goto"] == 2.0                          # one notch down per game
    monkeypatch.setattr(CE, "ENABLED", True)
    monkeypatch.setattr(CE, "stats_path", lambda truth_dir=None: tmp_path / CE.STATS_FILE)
    CE.reload()
    assert CE.cooldown_scale("macro_tip", "Va en bas : dragon dans 40 s") == 2.0
    assert CE.cooldown_scale("personal_danger") == 1.5
    assert CE.reset_stats(tmp_path) and CE.cooldown_scale("personal_danger") == 1.0
    CE.reload()


def test_worse_outcome_rule():
    s = {"n": 12, "followed": 6, "useful": 1, "died_f": 4, "died_i": 0}
    assert "précède plus de morts" in CE.quiet_reason(s)
    assert CE.quiet_reason({"n": 14, "followed": 0, "useful": 0, "died_f": 0, "died_i": 0}) is None   # < 15


def test_cooldowns_use_the_scale(monkeypatch):
    from treeaicoach import alerts, macro
    monkeypatch.setattr(CE, "ENABLED", True)
    monkeypatch.setattr(CE, "_scales", {"tip:retreat": 1.5, "wave_recall": 3.0})
    a = SimpleNamespace(kind=alerts.AlertKind.MACRO_TIP, level=alerts.Level.INFO, text="Recule !")
    assert alerts.cooldown_for(a) == alerts.COOLDOWN_S[alerts.Level.INFO] * 1.5
    assert macro._quiet_scale("wave_recall") == 3.0 and macro._quiet_scale("plates") == 1.0
    monkeypatch.setattr(CE, "_scales", None)


def test_recorder_logs_calls(tmp_path):
    from treeaicoach.recorder import GameRecorder
    r = GameRecorder(out_dir=tmp_path)
    r.on_call("rotate_mid", 100.0, "Va mid", (0.5, 0.5))           # inactive: ignored, never raises
    assert r._calls == []
    r.active = True
    r.on_call("rotate_mid", 100.0, "Va mid", (0.5, 0.5))
    r.on_call("back_off", 120.0, "Recule", None)
    assert r._calls == [[100.0, "rotate_mid", "Va mid", 0.5, 0.5], [120.0, "back_off", "Recule", None, None]]
