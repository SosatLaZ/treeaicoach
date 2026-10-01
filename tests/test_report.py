"""Tests of treeaicoach.report (HTML report, map image, write_report, list_games)."""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treeaicoach import report  # noqa: E402
from treeaicoach.analysis import analyze_game  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "game_record_sample.json"


@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def html_page(record: dict) -> str:
    return report.render_report_html(record, analyze_game(record))


def test_html_is_self_contained(html_page: str) -> None:
    assert html_page.startswith("<!DOCTYPE html>") and '<html lang="fr">' in html_page
    assert '<meta charset="utf-8">' in html_page
    # no external request of any kind
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html_page)
    assert "@import" not in html_page and "url(http" not in html_page
    assert "<script" not in html_page
    assert len(html_page) < 3_000_000


def test_html_content(html_page: str) -> None:
    for text in ("Victoire", "Garen", "28:10", "Mes morts", "Ganks subis", "Parcours du jungler ennemi",
                 "Conseils pour la prochaine partie", "Alerte donnée ?", "alerte ignorée", "mort sans alerte",
                 "rapport généré localement", "Lee Sin", "K'Santé", "Mon temps par zone", "Objectifs"):
        assert report.html.escape(text, quote=True) in html_page or text in html_page, text
    for color in ("#010A13", "#0A1428", "#C8AA6E", "#F0E6D2", "#0AC8B9", "#E84057", "#2DC66B"):
        assert color in html_page
    # the 4 deaths are listed with their game time
    for t in ("4:10", "9:20", "14:30", "21:05"):
        assert f">{t}</td>" in html_page


def test_embedded_images_decode(html_page: str) -> None:
    from PIL import Image

    uris = re.findall(r'src="data:image/png;base64,([A-Za-z0-9+/=]+)"', html_page)
    assert len(uris) >= 5           # champion icon + map + 3 phase maps (+ killer icons)
    sizes = []
    for b64 in uris:
        with Image.open(io.BytesIO(base64.b64decode(b64))) as im:
            im.load()
            sizes.append(im.size)
    assert (report.MAP_SIZE, report.MAP_SIZE) in sizes
    assert (64, 64) in sizes or any(s[0] == s[1] for s in sizes)


def test_map_png_draws_overlays(record: dict) -> None:
    import numpy as np
    from PIL import Image

    a = analyze_game(record)
    png = report.render_map_png(record, a)
    assert png is not None
    img = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    assert img.shape == (report.MAP_SIZE, report.MAP_SIZE, 3)
    # a red cross at my first death (4:10, top lane)
    u, v = a["deaths"][0]["uv"]
    x, y = int(u * report.MAP_SIZE), int(v * report.MAP_SIZE)
    patch = img[max(0, y - 6): y + 7, max(0, x - 6): x + 7]
    red = (patch[..., 0] > 180) & (patch[..., 1] < 110) & (patch[..., 2] < 130)
    assert red.sum() >= 6
    # the heat map brightens the top lane where I spent most of the game
    empty = report.render_map_png({"meta": record["meta"]}, {"summary": {}, "deaths": []})
    base = np.asarray(Image.open(io.BytesIO(empty)).convert("RGB")).astype(int)
    lane = (slice(int(0.20 * 512), int(0.30 * 512)), slice(int(0.06 * 512), int(0.11 * 512)))
    assert img[lane].mean() > base[lane].mean() + 15


def test_degraded_inputs_never_raise() -> None:
    for rec, an in (({}, {}), ({}, None), (None, None), ({"meta": {"champion": "Inconnu"}}, {"summary": None}),
                    ({"sightings": "x", "my_positions": [[1, 2]]}, {"deaths": [{"uv": [2, "x"]}]})):
        page = report.render_report_html(rec, an)  # type: ignore[arg-type]
        assert page.startswith("<!DOCTYPE html>")
    page = report.render_report_html({}, {})
    assert "Partie non terminée" in page


def test_defeat_badge_and_incomplete_banner(record: dict) -> None:
    r = json.loads(json.dumps(record))
    r["result"] = "Lose"
    r["events"] = [e for e in r["events"] if e.get("EventName") != "GameEnd"]
    page = report.render_report_html(r, analyze_game(r))
    assert "Défaite" in page and "Victoire" not in page
    r["result"] = None
    r["incomplete"] = True
    page = report.render_report_html(r, analyze_game(r))
    assert "Enregistrement incomplet" in page


def test_write_report_and_list_games(tmp_path: Path, record: dict) -> None:
    src = tmp_path / "2026-09-12_2031_Garen.json"
    shutil.copy(FIXTURE, src)
    t0 = time.perf_counter()
    out = report.write_report(src)
    elapsed = time.perf_counter() - t0
    assert out == tmp_path / "2026-09-12_2031_Garen.html" and out.is_file()
    assert elapsed < 10.0      # ~0.5 s typically (shared CPU: generous bound)
    assert "Garen" in out.read_text(encoding="utf-8")

    # an older game and a crashed (partial) one
    older = json.loads(json.dumps(record))
    older["summary"]["start"] = "2026-09-10T18:05:00+02:00"
    older["summary"]["result"] = "Lose"
    older["summary"]["champion"] = "Darius"
    (tmp_path / "2026-09-10_1805_Darius.json").write_text(json.dumps(older), encoding="utf-8")
    crashed = json.loads(json.dumps(record))
    crashed["summary"].update(start="2026-09-13T21:00:00+02:00", result=None, incomplete=True)
    crashed["incomplete"] = True
    (tmp_path / "2026-09-13_2100_Garen.partial.json").write_text(json.dumps(crashed), encoding="utf-8")
    # a partial whose final file exists is hidden; garbage is skipped
    (tmp_path / "2026-09-12_2031_Garen.partial.json").write_text(json.dumps(crashed), encoding="utf-8")
    (tmp_path / "2026-01-01_0000_Broken.json").write_text("{not json", encoding="utf-8")

    games = report.list_games(limit=50, games_dir=tmp_path)
    assert [g["path"].name for g in games] == ["2026-09-13_2100_Garen.partial.json", "2026-09-12_2031_Garen.json",
                                               "2026-09-10_1805_Darius.json"]
    g0, g1, g2 = games
    assert g0["incomplete"] and g0["result"] is None and g0["result_label"] == "Inachevée"
    assert g1["result"] == "Win" and g1["kda"] == "3/4/5" and g1["duration_text"] == "28:10"
    assert g1["report_path"] == out and g1["ganks"] == 4 and g1["ganks_survived"] == 2
    assert g1["champion"] == "Garen" and g1["date_label"] == "12 sept. 2026, 20:31"
    assert g2["result"] == "Lose" and g2["report_path"] is None and not g2["incomplete"]
    assert len(report.list_games(limit=1, games_dir=tmp_path)) == 1

    # the partial report gets the same base name
    rp = report.write_report(tmp_path / "2026-09-13_2100_Garen.partial.json")
    assert rp == tmp_path / "2026-09-13_2100_Garen.html"
    assert "Enregistrement incomplet" in rp.read_text(encoding="utf-8")


def test_list_games_reads_only_the_head(tmp_path: Path, record: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copy(FIXTURE, tmp_path / "2026-09-12_2031_Garen.json")
    calls = []
    orig = json.loads

    def spy(*a, **k):  # type: ignore[no-untyped-def]
        calls.append(len(a[0]) if a else 0)
        return orig(*a, **k)

    monkeypatch.setattr(report.json, "loads", spy)
    games = report.list_games(games_dir=tmp_path)
    assert len(games) == 1 and not calls        # fast path: no full parse of the record


def test_list_games_missing_dir_and_write_report_errors(tmp_path: Path) -> None:
    assert report.list_games(games_dir=tmp_path / "nope") == []
    assert report.write_report(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    assert report.write_report(bad) is None


def test_icons_and_textures() -> None:
    assert report.icon_data_uri("Garen", ).startswith("data:image/png;base64,")
    assert report.icon_data_uri("leesin") is not None           # case-insensitive
    assert report.icon_data_uri("NotAChampion") is None
    assert report._texture_name("Infernal") == "2dlevelminimap_infernal_baron1.png"
    assert report._texture_name(None) == "2dlevelminimap_base_baron1.png"


# ------------------------------------------------------------------------------ v2 coaching sections
def _png_ok(data: bytes | None, size: int) -> None:
    from PIL import Image

    assert data and data[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (size, size)


def test_v2_sections_present(html_page: str) -> None:
    for title in ("Résumé vocal de fin de partie", "Partie phase par phase", "Présence sur la carte : toi vs idéal",
                  "Score d'exposition aux ganks", "Parcours en début de partie", "Tendances (minute par minute)",
                  "objectifs pris par ton équipe", "Idéal pour ton rôle", "invisible depuis 2:19"):
        assert title in html_page, title
    assert "Victoire en 28 minutes" in html_page
    assert html_page.count("<svg class=\"spark\"") == 3
    assert "Phase de voie" in html_page and "Milieu de partie" in html_page and "Fin de partie" in html_page


def test_v2_images(record: dict) -> None:
    a = analyze_game(record)
    _png_ok(report.render_ideal_png(record, "TOP"), report.SMALL_MAP)
    _png_ok(report.render_ideal_png(record, "JUNGLE", "mid", size=120), 120)
    _png_ok(report.render_phase_heat_png(record, 0, 840), report.SMALL_MAP)
    _png_ok(report.render_exposure_png(record, a), report.SMALL_MAP)
    _png_ok(report.render_pathing_png(record, a), report.PATH_MAP)
    # degraded inputs: still an image or None, never an exception
    assert report.render_pathing_png({}, {}) is None or isinstance(report.render_pathing_png({}, {}), bytes)
    assert isinstance(report._spark_svg([], "cs", "#fff", str), str)


def test_v2_sections_degrade_gracefully() -> None:
    page = report.render_report_html({}, analyze_game({}))
    assert page.startswith("<!DOCTYPE html>")
    assert "Le rapport n'a pas pu être généré" not in page
