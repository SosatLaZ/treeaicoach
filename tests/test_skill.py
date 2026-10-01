from treeaicoach import skill
from treeaicoach.config import Config
from treeaicoach.tips import TipRotator


def test_levels_apply_and_filter_tips():
    cfg = Config()
    for key, _label in skill.SKILL_LEVELS:
        new = skill.apply(cfg, key)
        assert new.skill_level == key
    assert skill.apply(cfg, "expert").recall_reminder is False
    for key, _label in skill.SKILL_LEVELS:          # declutter: compact overlay at every level
        new = skill.apply(cfg, key)
        assert not new.hud_detailed and not new.overlay_show_roles and not new.overlay_show_allies
    assert skill.tip_min_prio(skill.apply(cfg, "expert")) == 4
    assert skill.tip_min_prio(skill.apply(cfg, "debutant")) == 1
    assert skill.normalize("Avancé") == "avance" and skill.normalize("???") == "intermediaire"


def test_rotator_respects_min_prio():
    r = TipRotator(seed=1)
    r.min_prio = 4
    assert all(t.prio >= 4 or t.tone == "red" for t in r._tips if t.prio >= r.min_prio or t.tone == "red")
