"""Extra tests for the v1.1 live_client fields (§6.3): game_result normalization."""

from __future__ import annotations

from treeaicoach.live_client import GameInfo


def _end(result: object) -> GameInfo:
    return GameInfo(events=[{"EventID": 1, "EventName": "GameStart", "EventTime": 0.0},
                            {"EventID": 2, "EventName": "GameEnd", "EventTime": 1800.0, "Result": result}])


def test_game_result_is_normalized_to_win_or_lose():
    assert _end("Win").game_result == "Win"
    assert _end("Lose").game_result == "Lose"
    assert _end(" win ").game_result == "Win"
    assert _end("LOSE").game_result == "Lose"


def test_game_result_unknown_or_missing_is_none():
    assert _end("").game_result is None
    assert _end(None).game_result is None
    assert _end(3).game_result is None
    assert _end("Remake").game_result is None
    assert GameInfo().game_result is None
    assert GameInfo(events=[{"EventName": "GameStart"}]).game_result is None
