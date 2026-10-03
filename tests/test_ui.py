"""Tests of treeaicoach.ui and treeaicoach.calibration.

Pure helpers are tested everywhere. GUI tests build the real window with a FAKE engine
(animated in-game data rendered by overlay_render) and are skipped cleanly when there is no
display / no Tk (headless CI without Xvfb).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from treeaicoach import calibration
from treeaicoach import ui_common as ui
from treeaicoach.config import Config


# ======================================================================================
# Fake engine (implements the CoachEngine contract, ARCHITECTURE.md §4.15 / §7.5)
# ======================================================================================
class FakeState(str, enum.Enum):
    STOPPED = "stopped"
    WAITING_GAME = "waiting_game"
    RUNNING = "running"


@dataclasses.dataclass
class FakeStatus:
    state: Any
    message: str
    fps: float
    game_time: float | None
    minimap_rect: Any
    enemies_visible: int
    last_alert: str | None
    detector: str
    voice: str
    muted: bool = False
    banner: str | None = None


class FakeEngine:
    """Thread-safe stand-in: cycles through overlay_render.sample_states() while running."""

    ORDER = ("safe", "warning", "danger", "late")

    def __init__(self, cfg: Config, voice: Any = None, detector: Any = None, frame_source: Any = None,
                 period: float = 2.0, in_game: bool = True) -> None:
        from treeaicoach import overlay_render

        self.cfg = cfg
        self.voice = voice
        self.demo = frame_source is not None
        self._states = overlay_render.sample_states()
        self._lock = threading.Lock()
        self._running = False
        self._t0 = time.monotonic()
        self.period = period
        self.in_game = in_game
        self.calls: dict[str, int] = {}
        self.muted = False
        self.banner: str | None = None
        self.fixed_index: int | None = None

    def _count(self, name: str) -> None:
        with self._lock:
            self.calls[name] = self.calls.get(name, 0) + 1

    def start(self) -> None:
        self._count("start")
        with self._lock:
            self._running = True
            self._t0 = time.monotonic()

    def stop(self, timeout: float = 3.0) -> None:
        self._count("stop")
        with self._lock:
            self._running = False

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def _index(self) -> int:
        if self.fixed_index is not None:
            return self.fixed_index
        return int((time.monotonic() - self._t0) / self.period) % len(self.ORDER)

    def get_status(self) -> FakeStatus:
        with self._lock:
            running = self._running
        if not running:
            return FakeStatus(FakeState.STOPPED, "Analyse arrêtée.", 0.0, None, None, 0, None, "onnx", "sapi")
        if not self.in_game:
            return FakeStatus(FakeState.WAITING_GAME, "En attente d'une partie de League of Legends…",
                              0.0, None, None, 0, None, "onnx", "sapi")
        st = self._states[self.ORDER[self._index()]]
        nvis = sum(1 for e in st.enemies if e.visible)
        la = st.last_alert[0] if st.last_alert else None
        return FakeStatus(FakeState.RUNNING, "Minimap trouvée — analyse en cours (1920 × 1080).", 7.9,
                          (st.game_time or 0.0) + (time.monotonic() - self._t0) % 1.0, None, nvis, la,
                          "onnx", "sapi", self.muted, self.banner)

    def get_overlay_state(self) -> Any:
        with self._lock:
            running = self._running
        if not running or not self.in_game:
            return None
        st = self._states[self.ORDER[self._index()]]
        if st.last_alert:       # the alert "happened" when this state began
            age = (time.monotonic() - self._t0) % self.period
            st = dataclasses.replace(st, last_alert=(st.last_alert[0], st.last_alert[1], age))
        return st

    def get_preview(self) -> np.ndarray | None:
        return None

    def request_relocate(self) -> None:
        self._count("request_relocate")

    def apply_config(self, cfg: Config) -> None:
        self._count("apply_config")
        self.cfg = cfg

    def step(self, t: float) -> list:
        return []

    def jungler_status_text(self) -> str:
        return "Lee Sin vu il y a 14 secondes dans la rivière du haut."

    def mute(self, on: bool) -> None:
        self.muted = bool(on)

    def toggle_overlay(self) -> None:
        self._count("toggle_overlay")

    # dashboard coach strip (engine.play_gauge / top_tip / detected_role / ai_budget_text)
    def play_gauge(self) -> Any:
        from treeaicoach.coach import Gauge

        return Gauge(1, "+2 niveaux sur Darius", 0.0) if self.in_game else None

    def top_tip(self) -> tuple[str, str] | None:
        return ("Va taper Darius : tu as 2 niveaux d'avance", "go") if self.in_game else None

    def detected_role(self) -> tuple[str | None, str | None]:
        return "MID", "Rôle détecté : MID (échange de voie)"

    def ai_budget_text(self) -> str:
        return "IA 2/5"


class FakeVoice:
    backend = "sapi"

    def __init__(self) -> None:
        self.said: list[tuple[str, int]] = []
        self.params: list[dict] = []
        self.stopped = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True

    def say(self, text: str, level: int = 1) -> None:
        self.said.append((text, level))

    def list_voices(self) -> list[str]:
        return ["Microsoft Hortense - French (France)", "Microsoft Paul - French (France)"]

    def set_params(self, **kw: Any) -> None:
        self.params.append(kw)


class FakeOverlay:
    def __init__(self, cfg: Config, provider: Any) -> None:
        self.cfg = cfg
        self.provider = provider
        self.started = self.stopped = False
        self.move: list[bool] = []
        self.applied = 0

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def apply_config(self, cfg: Config) -> None:
        self.applied += 1
        self.cfg = cfg

    def set_move_mode(self, on: bool) -> None:
        self.move.append(on)

    def set_on_moved(self, cb: Any) -> None:
        self.on_moved = cb


# ======================================================================================
# Pure helpers (no display needed)
# ======================================================================================
def test_fmt_helpers() -> None:
    assert ui.fmt_clock(None) == "--:--"
    assert ui.fmt_clock(-3) == "--:--"
    assert ui.fmt_clock(float("nan")) == "--:--"
    assert ui.fmt_clock(84.9) == "1:24"
    assert ui.fmt_clock(3723) == "1:02:03"
    assert ui.fmt_int_fr(3300) == "3 300"
    assert ui.fmt_int_fr("x") == "?"
    assert ui.fmt_decimal_fr(1.25, 1) in ("1,2", "1,3")


def test_state_key_normalizes() -> None:
    assert ui.state_key(FakeState.RUNNING) == "RUNNING"
    assert ui.state_key("waiting_game") == "WAITING_GAME"
    assert ui.state_key("EngineState.LOCATING") == "LOCATING"
    assert ui.state_key(None) == "STOPPED"


def test_session_stats_and_game_fields() -> None:
    today = dt.date(2026, 9, 30)
    games = [
        {"summary": {"start": "2026-09-30T20:10:00", "champion": "Garen", "result": "Win", "kills": 5,
                     "deaths": 2, "assists": 7, "ganks": 4, "ganks_survived": 3}},
        {"start": "2026-09-30T18:00:00", "champion": "Darius", "result": "Lose", "deaths": 6,
         "ganks": 2, "ganks_survived": 0},
        {"start": "2026-09-28T18:00:00", "champion": "Ahri", "result": None, "deaths": 1},
    ]
    st = ui.session_stats(games, today=today)
    assert st["scope"] == "Aujourd'hui"
    assert st["games"] == 2 and st["wins"] == 1
    assert st["deaths_per_game"] == pytest.approx(4.0)
    assert st["ganks"] == 6 and st["ganks_avoided"] == 3
    assert ui.game_result(games[0]) == "win" and ui.game_result(games[2]) is None
    assert ui.fmt_game_date(ui.game_datetime(games[0]), today).startswith("Aujourd'hui 20:10")
    assert ui.fmt_game_date(ui.game_datetime(games[2]), today) == "28/09 18:00"
    empty = ui.session_stats([], today=today)
    assert empty["games"] == 0 and empty["winrate"] is None


def test_autostart_not_supported_off_windows_or_unfrozen() -> None:
    ok, reason = ui.autostart_support()
    if os.name != "nt" or not getattr(__import__("sys"), "frozen", False):
        assert not ok and reason
        assert ui.set_windows_autostart(True) is False
    assert ui.autostart_command().startswith('"')


def test_images_and_icon() -> None:
    icon = np.zeros((64, 64, 4), np.uint8)
    icon[..., 0] = 200
    icon[..., 3] = 255
    im = ui.circle_icon(icon, 44, ui.DANGER)
    assert im.size == (44, 44) and im.mode == "RGB"
    assert ui.circle_icon(None, 44, None, grey=True).size == (44, 44)
    for kind in ("dashboard", "voice", "overlay", "analysis", "settings", "help", "play", "stop", "target",
                 "demo", "folder", "report", "refresh", "move"):
        assert ui.nav_icon(kind, 18).size == (18, 18)
    assert ui.load_logo(40).size == (40, 40)
    assert ui.app_icon_path("png") is not None      # packaging/icon.png is in the repository


def test_calibration_geometry_helpers() -> None:
    from treeaicoach.capture import Rect

    # drag towards bottom-right / top-left, clamped to the image
    assert calibration.constrain_square(10, 10, 60, 30, 100, 100) == (10, 10, 50)
    x, y, s = calibration.constrain_square(50, 50, 0, 20, 100, 100)
    assert (x, y, s) == (0, 0, 50)
    assert calibration.constrain_square(90, 90, 200, 200, 100, 100) == (90, 90, 10)
    assert calibration.fit_scale(1920, 1080, 960, 1080) == pytest.approx(0.5)
    assert calibration.fit_scale(100, 100, 500, 500) == 1.0
    origin = Rect(100, 50, 1920, 1080)
    sel = calibration.Selection(1650.4, 810.6, 255.2)
    rect = calibration.selection_to_rect(sel, origin)
    assert rect == {"screen_w": 1920, "screen_h": 1080, "x": 1750, "y": 861, "w": 255, "h": 255}
    back = calibration.rect_to_selection(rect, origin)
    assert back is not None and back.x == pytest.approx(1650) and back.side == pytest.approx(255)
    assert calibration.rect_to_selection(dict(rect, screen_w=1280), origin) is None
    cfg = dataclasses.replace(Config(), manual_minimap_rect=rect, minimap_mode="manual").validated()
    assert cfg.manual_minimap_rect == rect and cfg.minimap_mode == "manual"
    c = calibration.clamp_selection(calibration.Selection(-5, 2000, 300), 1920, 1080)
    assert c.x == 0 and c.y == 1080 - 300


# ======================================================================================
# GUI tests
# ======================================================================================
@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    from treeaicoach import paths

    paths._reset_cache()
    yield tmp_path
    paths._reset_cache()
