"""PvP help OFF by default: nothing said about enemy players; monsters still called."""

from treeaicoach.wow.coach import WowVoiceCoach


def test_enemy_player_ignored_without_pvp():
    from treeaicoach.wow import coach as C
    assert C._enemy_player(0x400 | 0x40)                 # hostile player
    assert not C._enemy_player(0x800 | 0x40)             # hostile NPC (monster)
    said = []
    c = WowVoiceCoach(lambda *a, **k: said.append(a), max_lag=None)
    assert c.pvp is False
    c2 = WowVoiceCoach(lambda *a, **k: said.append(a), max_lag=None, pvp=True)
    assert c2.pvp is True
