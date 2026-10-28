"""export.build_export: one zip with the captures, game records, their truth and the last
diagnostics (users sent dozens of files one by one)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from treeaicoach import export


def _touch(p: Path, data: bytes = b"x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_build_export_small_and_useful(tmp_path: Path, monkeypatch) -> None:
    import json
    import os

    base = tmp_path / "TreeAICoach"
    monkeypatch.setenv("TREEAICOACH_HOME", str(base))
    for d in range(5):
        stem = f"2026-10-0{d + 1}_2000_Swain"
        rec = {"schema": 1, "meta": {"riot_id": "Joueur#EUW", "champion": "Swain", "app_version": "3.3.0"},
               "roster": [{"alias": "Swain", "summoner_name": "Quelqu'un", "puuid": "p" * 70}],
               "summary": {"champion": "Swain", "result": "Win", "duration": 1800}, "duration": 1800,
               "alerts": [[60.0 * i, "jungler_approach", 2, "Gank ! Joueur#EUW arrive", "LeeSin"] for i in range(20)]}
        _touch(base / "games" / f"{stem}.json", json.dumps(rec).encode())
        _touch(base / "games" / f"{stem}.html", bytes(300_000))
        _touch(base / "games" / "truth" / f"abc-{stem}.truth.json", b'{"riotIdGameName": "x", "frames": []}')
    for i, tag in enumerate(("lowconf", "fight", "shop", "roster")):
        _touch(base / "collect" / f"20261009_2000{i:02d}_{i:04d}_{tag}.jpg", bytes(30_000))
    for i in range(5):
        _touch(base / "diagnostics" / f"diag_{i}.zip", b"PK")
        os.utime(base / "diagnostics" / f"diag_{i}.zip", (1e9 + i, 1e9 + i))
    _touch(base / "reports" / "rapport_1.zip", b"PK")
    _touch(base / "reports" / "rapport_big.zip", bytes(2_000_000))
    log = [f"2026-10-28 12:00:00 INFO treeaicoach.engine: tick {i}" for i in range(8000)]
    log[100] = "2026-10-28 12:00:00 ERROR treeaicoach.engine: boom C:\\Users\\Bob\\x"
    log[101] = "Traceback (most recent call last):"
    log[102] = "  File \"x.py\", line 1"
    log[200] = "2026-10-28 12:00:00 INFO treeaicoach.voice_assistant: Assistant vocal : mot-clé entendu"
    _touch(base / "logs" / "treeaicoach.log", "\n".join(log).encode())
    seen: list[tuple[int, int]] = []
    path, st = export.build_export(base, progress=lambda a, b: seen.append((a, b)), budget=1_000_000)
    assert path is not None and path.exists() and path.parent == base / "exports"
    assert (st["games"], st["truth"], st["captures"], st["diagnostics"], st["logs"]) == (3, 3, 4, 2, 1)
    assert st["reports"] == 1 and st["skipped"] == 1                       # the 2 Mo report is over budget
    assert st["bytes"] == path.stat().st_size < 300_000 and seen and seen[-1][0] == seen[-1][1]
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert {"info.json", "LISEZMOI.txt", "games/2026-10-05_2000_Swain.json",
                "games/truth/abc-2026-10-05_2000_Swain.truth.json"} <= names
        assert "games/2026-10-02_2000_Swain.json" not in names and not any(n.endswith(".html") for n in names)
        assert {"diagnostics/diag_4.zip", "diagnostics/diag_3.zip"} <= names
        assert zf.getinfo("collect/20261009_200000_0000_lowconf.jpg").compress_type == zipfile.ZIP_STORED
        blob = b"".join(zf.read(n) for n in names if n.endswith((".json", ".txt"))).decode("utf-8")
        for bad in ("Joueur#EUW", "Quelqu'un", "p" * 70, "riotIdGameName", "Bob"):
            assert bad not in blob, bad
        lg = zf.read("logs/treeaicoach.log.txt").decode("utf-8")
        assert "ERROR" in lg and "Traceback" in lg and "mot-clé" in lg and "tick 7999" in lg
        assert "tick 5000" not in lg and len(lg.splitlines()) < 320
        info = json.loads(zf.read("info.json"))
        assert info["game"]["running"] is False and "AUCUNE PARTIE" in zf.read("LISEZMOI.txt").decode("utf-8")
        assert info["last_game"]["summary"]["result"] == "Win" and "cpu" in info["pc"]
        assert "ai_api_key" not in info["settings"] and info["storage"]["games"]["files"] == 15
    assert not list((base / "exports").glob("*.part"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("ar_tool", Path(__file__).resolve().parents[1] / "tools" /
                                                  "analyze_report.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    lines = tool.analyze(path)
    text = "\n".join(lines)
    assert len(lines) <= tool.MAX_LINES and "aucune partie en cours" in text and "CAPTURES : 4" in text
    assert "1 réveils" in text and "ERREURS : 1 erreurs" in text and text.count("PARTIE 2026") == 3


def test_build_export_nothing_to_send(tmp_path: Path) -> None:
    path, st = export.build_export(tmp_path / "empty")
    assert path is None and "aucune" in st["error"]


def test_size_label() -> None:
    assert export.size_label(10) == "1 Ko"
    assert export.size_label(5 * 1024 * 1024 + 300 * 1024) == "5,3 Mo"
