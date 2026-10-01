"""Declutter of the in-game overlay: strict hierarchy danger > one action line > nothing."""
from __future__ import annotations

import json
from types import SimpleNamespace

from treeaicoach import config as C
from treeaicoach import overlay as ov
from treeaicoach import overlay_render as orr
from treeaicoach import toasts as T

E = orr.EnemyView


def _state(**kw):
    base = dict(game_time=62.0, me_uv=(0.12, 0.30), my_team="ORDER", gauge=0, my_role="TOP",
                tip="Pose ta balise dans la rivière : premier gank vers 2:30", tip_tone="info",
                objectives=[SimpleNamespace(name="Dragon", key="dragon", next_spawn=300.0, alive=False)],
                enemies=[E("MasterYi", "MasterYi", "Maître Yi", False, None, None, True)],
                ai_counter="IA 0/5", jungler_line="Jungler : Maître Yi — pas encore vu")
    base.update(kw)
    return orr.OverlayState(**base)


def test_action_text_is_short_and_verb_first():
    assert orr.action_text("Pose ta balise dans la rivière : premier gank vers 2:30") == "Pose ta balise dans la rivière"
    assert orr.action_text("Ne t'avance pas : Maître Yi invisible depuis 113 s") == "Ne t'avance pas : Maître Yi invisible depuis 113 s"[:51] \
        or len(orr.action_text("Ne t'avance pas : Maître Yi invisible depuis 113 s")) <= orr.ACTION_MAX_CHARS
    assert len(orr.action_text("mot " * 40)) <= orr.ACTION_MAX_CHARS
    assert orr.action_text("+1 200 PO · victoire 55 %") == ""
    assert orr.action_text(None) == ""


def test_compact_card_size_and_content():
    st = _state()
    w, h = orr.hud_size(st, 300)
    assert w == 300 and h <= 70
    c = orr.compact_content(st, now=0.0)
    assert c["mode"] == "normal" and c["word"] == "NORMAL" and c["line"] == "Pose ta balise dans la rivière"
    assert c["note"] is None                     # dragon 3:58 away and Garen is top: nothing
    img = orr.render_hud(st, 300, now=0.0)
    assert img.shape[1] == 300 and img[..., 3].any()
    # detailed mode (setting / hold key): the full card is still reachable
    assert orr.hud_size(orr.OverlayState(**{**st.__dict__, "hud_detailed": True}), 300)[1] > h


def test_danger_is_the_only_thing():
    st = _state(threat_level=2, threat_text="DANGER — GANK !")
    c = orr.compact_content(st, now=0.0)
    assert c["mode"] == "danger" and c["word"] == "GANK !" and c["line"] == ""
    st = _state(threat_level=2, threat_text="DANGER — TA BASE EST ATTAQUÉE", tip="Recule vers ta tour",
                tip_tone="danger")
    c = orr.compact_content(st, now=0.0)
    assert c["word"] == "BASE ATTAQUÉE" and c["line"] == "Recule vers ta tour"
    assert orr.hud_size(st, 300)[1] <= 80


def test_levels_and_hidden_card():
    st = _state(skill_level="expert")
    assert orr.compact_content(st, now=0.0) is None and not orr.hud_visible(st, now=0.0)
    assert orr.hud_visible(_state(skill_level="expert", gauge=-2), now=0.0)
    inter = _state(skill_level="intermediaire", tip_since=0.0)
    assert orr.compact_content(inter, now=5.0)["line"]
    assert not orr.compact_content(inter, now=60.0)["line"]                  # shown 30 s
    deb = _state(skill_level="debutant", tip_since=0.0)
    assert orr.compact_content(deb, now=600.0)["line"]                       # beginners: while valid
    dead = _state(me_dead=True, respawn_s=8.0, tip=None)
    assert orr.compact_content(dead, now=0.0) is None                        # the game shows the timer


def test_context_note_only_when_it_matters():
    st = _state(game_time=260.0, my_role="BOTTOM", skill_level="debutant")
    assert orr.compact_content(st, now=0.0)["note"][0] == "Dragon 0:40"
    assert orr.compact_content(_state(game_time=260.0, my_role="TOP"), now=0.0)["note"] is None
    yi = E("MasterYi", "MasterYi", "Maître Yi", False, (0.3, 0.3), 45.0, True)
    st = _state(enemies=[yi], tip="Farme sous ta tour")
    assert orr.compact_content(st, now=0.0)["note"][0] == "Maître Yi caché 45 s"
    st = _state(enemies=[yi], tip="Ne t'avance pas : Maître Yi invisible")
    assert orr.compact_content(st, now=0.0)["note"] is None                  # no repeat


def test_minimap_compact_keeps_jungler_arrows_one_guide():
    yi = E("MasterYi", "MasterYi", "Maître Yi", False, (0.5, 0.5), 10.0, True)
    vis = E("Darius", "Darius", "Darius", True, (0.8, 0.8), 0.0, role="TOP")
    st = orr.OverlayState(me_uv=(0.2, 0.2), enemies=[yi, vis], roles={"Darius": "TOP"}, show_roles=True)
    img = orr.render_minimap(st, 256, 256, now=0.0)
    assert img[128, 128 + int(orr.MM_MARKER_R * 256), 3] > 0                # jungler ghost
    assert not img[180:230, 180:230].any()                                   # calm visible enemy: nothing
    come = E("Darius", "Darius", "Darius", True, (0.3, 0.3), 0.0, approaching=True, velocity=(-0.02, -0.02))
    st2 = orr.OverlayState(me_uv=(0.2, 0.2), enemies=[come])
    assert orr.render_minimap(st2, 256, 256, now=0.0)[..., 3].any()         # danger arrow


def test_toasts_one_at_a_time_and_fight_rule():
    mk = lambda kind, title, sub="", age=0.5: T.ToastView(T.Toast(kind, title, sub), age)
    views = [mk("insight", "ASTUCE", "Pose ta balise"), mk("warning", "OBJECTIF", "Dragon"), mk("danger", "GANK", "")]
    assert [v.toast.kind for v in T.select_views(views, _state(), "debutant")] == ["danger"]
    calm = [mk("insight", "ASTUCE", "x"), mk("warning", "OBJECTIF", "Dragon")]
    assert [v.toast.kind for v in T.select_views(calm, _state(), "debutant")] == ["warning"]
    assert T.select_views([mk("insight", "ASTUCE", "x")], _state(), "expert") == []
    fight = _state(enemies=[E("Darius", "Darius", "Darius", True, (0.14, 0.31), 0.0)])
    assert T.in_fight(fight) and T.select_views(calm, fight, "debutant") == []
    dup = [mk("insight", "ASTUCE", "Pose ta balise dans la rivière : premier gank vers 2:30")]
    assert T.select_views(dup, _state(), "debutant") == []                  # repeats the HUD line
    assert T.select_views([mk("warning", "X", "y", age=5.0)], _state(), "debutant") == []
    q = T.ToastQueue(clock=lambda: 0.0)
    q.push("warning", "A", "b", t=0.0, duration=30.0)
    assert q.active(0.0)[0].toast.duration <= T.MAX_DURATION_S


def test_details_hold_key_and_decorate():
    down = {0x75}                                           # F6
    assert ov.details_key_held("F6", lambda vk: vk in down)
    assert not ov.details_key_held("F6", lambda vk: False)
    assert not ov.details_key_held("Ctrl+F6", lambda vk: vk in down)
    assert ov.details_key_held("Ctrl+F6", lambda vk: vk in {0x75, 0x11})
    assert not ov.details_key_held("", lambda vk: True)
    cfg = SimpleNamespace(hud_detailed=False, skill_level="expert")
    st = ov.decorate_state(_state(), cfg, details_held=True)
    assert st.hud_detailed and st.skill_level == "expert"
    assert not ov.decorate_state(_state(hud_detailed=True), cfg).hud_detailed


def test_config_declutter_migration(tmp_path):
    assert C.Config().hotkey_details == "F6" and not C.Config().hud_detailed
    old = tmp_path / "config.json"
    old.write_text(json.dumps({"hud_detailed": True, "overlay_show_roles": True, "overlay_show_allies": True,
                               "skill_level": "debutant"}), encoding="utf-8")
    cfg = C.load_config(old)
    assert not cfg.hud_detailed and not cfg.overlay_show_roles and not cfg.overlay_show_allies
    assert cfg.skill_level == "debutant"
    assert C.save_config(C.Config(**{**cfg.to_dict(), "hud_detailed": True}), old)
    assert C.load_config(old).hud_detailed                     # once: a later choice is kept


def test_outnumbered_is_never_normal():
    a = E("Darius", "Darius", "Darius", True, (0.14, 0.31), 0.0)
    b = E("MasterYi", "MasterYi", "Maître Yi", True, (0.10, 0.28), 0.0, True)
    c = orr.compact_content(_state(enemies=[a, b]), now=0.0)
    assert c["mode"] == "warning" and c["word"].startswith("2 CONTRE 1")
    one = orr.compact_content(_state(enemies=[a]), now=0.0)
    assert one["word"] != "NORMAL"
