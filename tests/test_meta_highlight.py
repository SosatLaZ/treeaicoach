"""Build highlight (item card in game, champion select icons, report icons) and the meta build fixes
found on the user's real records (meta core before counter items, support upgrade of the meta build,
noisy U.GG data filtered, previous patch on patch day, stale cache refetched)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from treeaicoach import item_card as ic
from treeaicoach import item_icons
from treeaicoach import itemization as iz
from treeaicoach import meta_builds as mb
from treeaicoach import overlay_render as orr
from treeaicoach.live_client import GameInfo, PlayerInfo


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TREEAI_META_BUILDS", raising=False)
    mb._reset()
    item_icons._reset(lambda url: (_ for _ in ()).throw(OSError("offline")))
    yield
    mb._reset()
    item_icons._reset()


def _game(alias: str, pos: str, items: list[int], gold: float, gt: float = 700.0, enemies=(), dead=False):
    me = PlayerInfo(riot_id="me", summoner_name="me", champion_alias=alias, champion_name=alias, team="ORDER",
                    position=pos, level=9, items=items, is_dead=dead)
    ens = [PlayerInfo(riot_id=a, summoner_name=a, champion_alias=a, champion_name=a, team="CHAOS", position=p,
                      items=it, scores={"kills": k, "deaths": 0, "assists": 0}) for a, p, it, k in enemies]
    return GameInfo(game_time=gt, game_mode="CLASSIC", map_number=11, map_terrain="Default", team_relative_colors=True,
                    me=me, allies=[], enemies=ens, events=[], fetched_at=0.0, current_gold=gold)


# ----------------------------------------------------------------------------- recommendation (real records)
def test_meta_core_beats_counter_items_until_two_core_items():
    """Real record 2026-10-01 Garen: "Danse de la mort" (physical counter) was advised before his 2nd core item."""
    b = mb.get("Garen", "TOP")
    assert b is not None and b.has_items
    core = [i for i in mb.core_to_buy(b, iz.load_items()) if iz.load_items()[i].kind != "boots"]
    ad_team = [("Darius", "TOP", [], 0), ("Zed", "MIDDLE", [], 0), ("Draven", "BOTTOM", [], 0),
               ("Talon", "JUNGLE", [], 0), ("Pyke", "UTILITY", [], 0)]
    r = iz.recommend(_game("Garen", "TOP", [1055, 3006], 1200, enemies=ad_team), "TOP")
    assert r is not None and r.meta and r.item_id == core[0]
    r2 = iz.recommend(_game("Garen", "TOP", [1055, 3006, core[0]], 1200, enemies=ad_team), "TOP")
    assert r2 is not None and r2.item_id == core[1] and r2.meta


def test_fed_enemy_still_gets_its_counter_early():
    fed = [("Vladimir", "TOP", [3152, 3020, 3089], 9), ("Soraka", "UTILITY", [], 0)]
    r = iz.recommend(_game("Garen", "TOP", [1055, 3006], 1200, enemies=fed), "TOP")
    assert r is not None and r.need == "antiheal"


def test_support_upgrade_follows_the_meta_build():
    """Real records (Swain support): the quest upgrade was Zaz'Zak while the meta build names its own."""
    b = mb.get("Swain", "UTILITY")
    items = iz.load_items()
    ups = [i for i in b.core if i in iz.SUPPORT_UPGRADE.values() or (i in items and iz.BOUNTY_OF_WORLDS in items[i].parts)]
    if not ups:
        pytest.skip("this patch's Swain support build has no quest upgrade")
    r = iz.recommend(_game("Swain", "UTILITY", [iz.BOUNTY_OF_WORLDS], 300), "UTILITY")
    assert r is not None and r.item_id == ups[0]


def test_reason_singular_verb():
    assert iz.reason_text("antiheal", ["Aatrox"]) == "Aatrox se soigne beaucoup (Blessures graves)"
    assert "se soignent" in iz.reason_text("antiheal", ["Aatrox", "Soraka"])


# ----------------------------------------------------------------------------- noisy data / patch
def test_noise_filters():
    row = {"core": [6631, 3142], "slots": [[[3053, 100.0, 6], [3071, 57.0, 900]]], "core_wr": 62.5,
           "core_games": 112, "matches": 3000, "wins": 1500}
    b = mb.build_from_row("Garen", "TOP", row)
    assert [i for i, _w, _n in b.options] == [3071]          # 100 % on 6 games is dropped
    assert b.core_wr_ok == 0.0                                # a win rate on 112 games is not printed
    got = mb.next_item(b, [], iz.load_items())
    assert got is not None and "%" not in got[1]


def test_previous_patch_and_ugg_fallback(tmp_path):
    assert mb.previous_patch("16_20") == "16_19" and mb.previous_patch("17_1") == "16_24"
    assert mb.patch_key("16.20.718.4512") == "16_20"
    calls = []

    def fetch(url: str):
        calls.append(url)
        if "/16_21/" in url:
            raise OSError("404")
        if "stats2.u.gg" in url:
            return {"12": {"14": {"4": [[[400, 210, 8000, 8400, [8010, 9111, 9104, 8299, 8473, 8242]],
                                           [5000, 2600, [4, 14]], [5000, 2600, [1055, 2003]],
                                           [800, 440, [3006, 6631, 3142]], [3000, 1500, ["Q", "E", "W"], "EQW"],
                                           [], [2600, 5000], [], [400, 200, ["5008", "5008", "5001"]]], "2026-10-20"]}}}
        raise OSError("offline")
    st = mb.refresh("Garen", patch="16_21", force=True, fetch=fetch, now=time.time())
    assert st == "ugg" and any("/16_20/" in u for u in calls)
    b = mb.get("Garen", "TOP", patch="16_21")
    assert b is not None and b.core == (3006, 6631, 3142) and b.core_games == 800


def test_stale_cache_is_refetched(tmp_path):
    folder = mb.cache_folder("16_20")
    p = folder / "Garen.json"
    p.write_text('{"roles": {"TOP": {"core": [6631]}}}', encoding="utf-8")
    assert not mb._stale(folder, "Garen")
    old = time.time() - mb.STALE_S - 60
    os.utime(p, (old, old))
    assert mb._stale(folder, "Garen")


def test_tls_falls_back_to_certifi(monkeypatch):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b'{"ok": 1}'

    seen = []

    class Opener:
        def __init__(self, ctx):
            self.ctx = ctx

        def open(self, req, timeout=None):
            seen.append(self.ctx)
            if self.ctx is None:
                raise OSError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
            return Resp()

    def build_opener(*handlers):
        ctx = getattr(handlers[0], "_context", None) if handlers else None
        return Opener(ctx)

    monkeypatch.setattr(mb.urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(mb, "_tls_certifi", False)
    assert mb.get_bytes("https://stats2.u.gg/x.json") == b'{"ok": 1}'
    assert seen[0] is None and seen[1] is not None and mb._tls_certifi


# ----------------------------------------------------------------------------- icons
def test_bundled_icons_cover_the_user_builds():
    items = iz.load_items()
    for alias, role in (("Garen", "TOP"), ("Swain", "UTILITY"), ("Seraphine", "UTILITY"), ("Lux", "UTILITY"),
                        ("MissFortune", "BOTTOM"), ("Jinx", "BOTTOM")):
        b = mb.get(alias, role)
        assert b is not None and b.has_items, alias
        for iid in mb.core_to_buy(b, items):
            assert item_icons.item_rgba(iid) is not None, (alias, iid)
        for s in b.spells[:2]:
            assert item_icons.png_bytes("spell", s), (alias, s)
        assert item_icons.png_bytes("perk", b.runes[0]), alias


def test_icon_download_goes_to_cache(tmp_path):
    png = (Path(item_icons.__file__).parent / "assets" / "icons" / "item" / "6631.png").read_bytes()
    item_icons._reset(lambda url: png)
    t = item_icons.prefetch({"item": [999999]})
    assert t is not None
    t.join(5)
    assert (Path(os.environ["TREEAICOACH_HOME"]) / "cache" / "icons" / "item" / "999999.png").is_file() or \
        item_icons.png_path("item", 999999) is not None


# ----------------------------------------------------------------------------- item card
def _rec(iid=6631, meta=True, buy=(), completes=False, buy_ids=()):
    items = iz.load_items()
    return NS(item_id=iid, item_name=items[iid].name, reason="objet 2 du build du moment (55.4 % de victoires)",
              meta=meta, win_rate=55.4, buy_now_names=buy, buy_now=buy_ids, completes=completes)


def test_item_card_texts():
    c = ic.build_card(_rec(), 3400, [3006], shop=True)
    assert c.affordable and c.price == 3300 and c.sub == "objet 2 du build · 55 % de victoires"
    assert c.title == "Achète Estropieur" and c.icon_id == 6631 and c.shown_price == 3300
    c2 = ic.build_card(_rec(buy=("Phage",), buy_ids=(3044,)), 1300, [3006], shop=True)
    assert not c2.affordable and c2.buy_now and c2.title == "Achète Phage" and c2.icon_id == 3044
    c2b = ic.build_card(_rec(buy=("Phage", "Dague"), buy_ids=(3044, 1042)), 1500, [3006], shop=True)
    assert c2b.title == "Achète Phage" and c2b.sub.startswith("puis Dague, vers Estropieur")
    assert c2.sub.startswith("vers Estropieur") and c2.shown_price == 1100
    c3 = ic.build_card(_rec(), 300, [3006], shop=True)
    assert c3.sub.startswith("il manque 3") and c3.missing == 3000 and c3.title == "Vise Estropieur"
    assert not c3.buy_now


def test_item_card_state_shop_linger_and_bought():
    st = ic.ItemCardState()
    rec = _rec()
    assert st.update(0.0, rec, 500, [3006], in_base=False, dead=False) is None        # out of base, can't buy
    assert st.update(1.0, rec, 500, [3006], in_base=True, dead=False).shop
    assert st.update(1.0 + ic.LINGER_S - 0.5, rec, 500, [3006], in_base=False, dead=False) is not None
    assert st.update(1.0 + ic.LINGER_S + 1, rec, 500, [3006], in_base=False, dead=False) is None
    out = st.update(20.0, rec, 3400, [3006], in_base=False, dead=False)                # gold buys it
    assert out is not None and out.affordable and not out.shop
    assert st.update(21.0, rec, 3400, [3006, 6631], in_base=True, dead=False) is None  # just bought
    assert st.update(30.0, rec, 100, [3006], in_base=False, dead=True).shop            # dead: shop open
    # respawned in the fountain before the tracker sees me there: still shown
    assert st.update(30.0 + ic.RESPAWN_LINGER_S - 1, rec, 100, [3006], in_base=False, dead=False).shop
    # the start of the game in the fountain belongs to the team plan
    assert ic.ItemCardState().update(1.0, rec, 500, [], in_base=True, dead=False, game_time=30.0) is None


def test_compact_card_priorities():
    card = ic.build_card(_rec(), 3400, [3006], shop=True)
    st = orr.OverlayState(item_card=card, game_time=600.0, tip="Va bot : aide ton tireur", tip_curated=True)
    c = orr.compact_content(st)
    assert c["mode"] == "item" and c["line"] == "Achète Estropieur" and c["icon"] is not None
    # danger always wins
    st_d = orr.OverlayState(item_card=card, game_time=600.0, threat_level=2, threat_text="DANGER — GANK !")
    assert orr.compact_content(st_d)["mode"] == "danger"
    # out of base and affordable: only when the card has nothing else to say
    off = ic.ItemCard(**{**card.__dict__, "shop": False})
    assert orr.compact_content(orr.OverlayState(item_card=off, game_time=600.0, tip="Va bot : aide ton tireur",
                                                tip_curated=True))["mode"] != "item"
    assert orr.compact_content(orr.OverlayState(item_card=off, game_time=600.0, tip_curated=True))["mode"] == "item"
    # dead with a lesson to read: the lesson first
    dead = orr.OverlayState(item_card=card, game_time=600.0, me_dead=True, tip="Utilise ton Soin avant de mourir",
                            tip_curated=True)
    assert orr.compact_content(dead)["mode"] != "item"
    # the gold line naming the item gets its icon
    c4 = orr.compact_content(orr.OverlayState(item_card=off, game_time=600.0, tip="Rentre acheter Estropieur : 3 400 or",
                                              tip_curated=True))
    assert c4["mode"] == "ok" and c4["icon"] is not None


def test_item_card_fits_the_card_slot_and_renders():
    card = ic.build_card(_rec(3142), 300, [3006, 6631], shop=True)
    st = orr.OverlayState(item_card=card, game_time=600.0)
    img = orr.render_hud(st, 320, now=time.monotonic() + 5)
    assert img.shape[0] <= orr.card_max_height(320) and img[..., 3].max() > 0


def test_hud_signature_follows_the_item_card():
    from treeaicoach.overlay import hud_signature

    a = orr.OverlayState(item_card=ic.build_card(_rec(), 3400, [3006], shop=True))
    b = orr.OverlayState(item_card=ic.build_card(_rec(), 3400, [3006], shop=True))
    c = orr.OverlayState(item_card=ic.build_card(_rec(), 300, [3006], shop=True))
    assert hud_signature(a, 1.0) == hud_signature(b, 1.0) != hud_signature(c, 1.0)


# ----------------------------------------------------------------------------- champion select card
def test_champ_select_card_has_icon_ids():
    from treeaicoach import champ_select as cs

    st = cs.ChampSelectState(me=cs.Pick("Garen", "Garen", "TOP", True), allies=(), enemies=(), phase="BAN_PICK")
    card = cs.build_card(st)
    assert card is not None and card.core_ids and card.spell_ids and card.rune_ids
    assert card.lines[-1].endswith(" · monte " + card.skills) and len(card.lines) <= 7
    for i in card.core_ids:
        assert item_icons.png_bytes("item", i)


# ----------------------------------------------------------------------------- report
def test_report_meta_section_has_icons():
    from treeaicoach import report

    rec = {"meta": {"champion": "Garen", "position": "TOP"},
           "snapshots": [{"game_time": 900.0, "items": [1055, 3006, 6631, 3071]}]}
    html = report._meta_build_section(rec)
    assert "Ton build face à la méta" in html and html.count("<img") >= 5
    assert "(manque)" in html and "hors méta" in html


def test_a_game_changer_buy_line_wins_over_the_item_card():
    card = ic.build_card(_rec(buy=("Tiamat",), buy_ids=(3077,)), 1300, [3006], shop=True)
    other = orr.OverlayState(item_card=card, game_time=700.0, in_base=True,
                             tip="Achète Cotte de mailles : Darius est trop fort (5/0)", tip_curated=True)
    assert orr.compact_content(other)["line"].startswith("Achète Cotte de mailles")
    same = orr.OverlayState(item_card=card, game_time=700.0, in_base=True,
                            tip="Achète Tiamat : tu as l'or", tip_curated=True)
    assert orr.compact_content(same)["mode"] == "item"
    # after the shop: the stale shop line is hidden
    tail = orr.OverlayState(game_time=720.0, buy_tail=True, tip="Achète Tiamat : tu as l'or", tip_curated=True)
    assert orr.compact_content(tail) is None
