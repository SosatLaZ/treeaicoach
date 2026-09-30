"""Tests for treeaicoach.config and treeaicoach.logging_setup."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from treeaicoach import config as config_mod
from treeaicoach import logging_setup, paths
from treeaicoach.config import Config, load_config, save_config

VALID_RECT = {"screen_w": 1920, "screen_h": 1080, "x": 1640, "y": 800, "w": 272, "h": 272}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "systemp"))
    (tmp_path / "systemp").mkdir()
    paths._reset_cache()
    yield
    paths._reset_cache()


# --------------------------------------------------------------------------- defaults


def test_defaults_match_contract():
    c = Config()
    assert c.voice_name == ""
    assert c.voice_rate == 2 and c.voice_volume == 100 and c.beep_on_danger is True
    assert (c.alert_jungler_approach, c.alert_roam, c.alert_collapse, c.alert_jungler_spotted) == (
        True, True, True, True)
    assert c.alert_laner_mia is False
    assert c.sensitivity == 1.0 and c.warn_radius == 0.22 and c.danger_radius == 0.12
    assert c.target_fps == 8.0 and c.detector_backend == "auto" and c.detection_threshold == 0.0
    assert c.minimap_mode == "auto" and c.minimap_side == "auto" and c.manual_minimap_rect is None
    assert c.download_skin_icons is True and c.autostart is True
    assert c.collect_samples is False and c.collect_interval_s == 2.0 and c.show_preview is False


def test_defaults_are_valid_and_validated_is_a_copy(caplog):
    c = Config()
    with caplog.at_level(logging.WARNING, logger="treeaicoach.config"):
        v = c.validated()
    assert v == c and v is not c
    assert not [r for r in caplog.records if r.name == "treeaicoach.config"]


def test_effective_radii():
    c = Config(sensitivity=1.5, warn_radius=0.2, danger_radius=0.1)
    assert c.effective_warn_radius() == pytest.approx(0.3)
    assert c.effective_danger_radius() == pytest.approx(0.15)
    # invalid values never break the per-frame helpers
    bad = Config(sensitivity=float("nan"), warn_radius="x")  # type: ignore[arg-type]
    assert bad.effective_warn_radius() == pytest.approx(0.22)
    assert bad.effective_danger_radius() == pytest.approx(0.12)
    assert Config(sensitivity=99).effective_warn_radius() == pytest.approx(0.22 * 1.6)


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("voice_rate", 50, 10),
        ("voice_rate", -50, -10),
        ("voice_rate", 3.6, 4),
        ("voice_rate", "fast", 2),
        ("voice_rate", True, 2),
        ("voice_rate", 10**400, 2),          # float() overflow -> default
        ("voice_volume", 150, 100),
        ("voice_volume", -1, 0),
        ("voice_volume", None, 100),
        ("sensitivity", 5.0, 1.6),
        ("sensitivity", 0.1, 0.6),
        ("sensitivity", float("nan"), 1.0),
        ("sensitivity", float("inf"), 1.0),
        ("warn_radius", 2.0, 0.5),
        ("target_fps", 0, 2.0),
        ("target_fps", 100, 20.0),
        ("target_fps", "12", 8.0),
        ("target_fps", 12, 12.0),
        ("detection_threshold", -1.0, 0.0),
        ("detection_threshold", 0.01, 0.05),
        ("detection_threshold", 0.4, 0.4),
        ("detection_threshold", 1.5, 0.95),
        ("collect_interval_s", 0.0, 0.5),
        ("collect_interval_s", 1e9, 60.0),
        ("detector_backend", " ONNX ", "onnx"),
        ("detector_backend", "tensorflow", "auto"),
        ("detector_backend", 3, "auto"),
        ("minimap_side", "Left", "left"),
        ("minimap_side", "middle", "auto"),
        ("minimap_mode", "MANUEL", "auto"),
        ("alert_laner_mia", "yes", False),
        ("beep_on_danger", 0, False),
        ("alert_laner_mia", 1, True),
        ("alert_laner_mia", 2, False),
        ("show_preview", None, False),
        ("voice_name", 42, ""),
        ("voice_name", "  Microsoft Hortense\x00 ", "Microsoft Hortense"),
        ("voice_name", "é" * 1000, "é" * config_mod.VOICE_NAME_MAX_LEN),
    ],
)
def test_validated_clamps_and_fixes(field, value, expected):
    v = Config(**{field: value}).validated()
    got = getattr(v, field)
    assert got == expected
    assert type(got) is type(expected)


def test_validated_numpy_scalars():
    np = pytest.importorskip("numpy")
    v = Config(voice_rate=np.int64(4), sensitivity=np.float32(1.2)).validated()  # type: ignore[arg-type]
    assert v.voice_rate == 4 and type(v.voice_rate) is int
    assert v.sensitivity == pytest.approx(1.2) and type(v.sensitivity) is float


def test_validated_logs_corrections(caplog):
    with caplog.at_level(logging.WARNING, logger="treeaicoach.config"):
        Config(voice_rate=99).validated()
    assert any("voice_rate" in r.getMessage() for r in caplog.records)


def test_danger_radius_not_above_warn_radius():
    v = Config(warn_radius=0.1, danger_radius=0.3).validated()
    assert v.warn_radius == 0.1 and v.danger_radius == 0.1


def test_manual_rect_valid_and_copied():
    rect = dict(VALID_RECT, x=1640.4, extra="ignored")
    c = Config(minimap_mode="manual", manual_minimap_rect=rect)
    v = c.validated()
    assert v.minimap_mode == "manual"
    assert v.manual_minimap_rect == VALID_RECT
    assert all(type(x) is int for x in v.manual_minimap_rect.values())
    v.manual_minimap_rect["x"] = 0
    assert rect["x"] == 1640.4  # the original dict is not shared


@pytest.mark.parametrize(
    "rect",
    [
        "1920x1080",
        [1, 2, 3, 4],
        {k: v for k, v in VALID_RECT.items() if k != "h"},       # missing key
        dict(VALID_RECT, w="272"),                                # wrong type
        dict(VALID_RECT, w=True),                                 # bool is not a number here
        dict(VALID_RECT, x=float("nan")),
        dict(VALID_RECT, w=5),                                    # too small
        dict(VALID_RECT, w=4000),                                 # wider than the screen
        dict(VALID_RECT, screen_w=0),
        dict(VALID_RECT, y=10**7),
    ],
)
def test_manual_rect_invalid_becomes_none_and_mode_auto(rect):
    v = Config(minimap_mode="manual", manual_minimap_rect=rect).validated()  # type: ignore[arg-type]
    assert v.manual_minimap_rect is None
    assert v.minimap_mode == "auto"


def test_manual_rect_negative_coords_allowed_multi_monitor():
    rect = dict(VALID_RECT, x=-280, y=800)
    assert Config(manual_minimap_rect=rect).validated().manual_minimap_rect == rect


def test_validated_never_raises_on_weird_objects():
    class Weird:
        def __eq__(self, other):
            raise RuntimeError("no compare")

        def __repr__(self):
            raise RuntimeError("no repr")

    c = Config()
    for f in dataclasses.fields(Config):
        setattr(c, f.name, Weird())
    v = c.validated()
    assert v == Config()


def test_from_dict_and_to_dict():
    assert Config.from_dict(None) == Config()
    assert Config.from_dict(["not", "a", "dict"]) == Config()  # type: ignore[arg-type]
    c = Config.from_dict({"voice_rate": 5, "unknown": 1, 3: "x", "config_version": 1})
    assert c.voice_rate == 5
    d = Config(manual_minimap_rect=dict(VALID_RECT)).to_dict()
    assert set(d) == {f.name for f in dataclasses.fields(Config)}
    json.dumps(d)  # serializable


# --------------------------------------------------------------------------- load / save


def test_load_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.json") == Config()
    assert not (tmp_path / "nope.json").exists()


def test_load_default_path_uses_user_dir(tmp_path):
    cfg = Config(voice_rate=-3)
    assert save_config(cfg) is True
    assert (tmp_path / "home" / "config.json").is_file()
    assert load_config() == cfg


@pytest.mark.parametrize(
    "content",
    [b"{ not json", b"", b"\xff\xfe\x00garbage", b"[1, 2, 3]", b"\"text\"", b"null",
     b"[" * 100000],
    ids=["syntax", "empty", "binary", "list", "string", "null", "deep-nesting"],
)
def test_load_corrupt_file_renamed_bak(tmp_path, content):
    p = tmp_path / "config.json"
    p.write_bytes(content)
    assert load_config(p) == Config()
    assert not p.exists()
    assert (tmp_path / "config.json.bak").read_bytes() == content


def test_load_corrupt_file_overwrites_old_bak(tmp_path):
    p = tmp_path / "config.json"
    (tmp_path / "config.json.bak").write_text("old backup")
    p.write_text("{broken")
    assert load_config(p) == Config()
    assert (tmp_path / "config.json.bak").read_text() == "{broken"


def test_load_too_large_file_is_corrupt(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"voice_name": "x" * (config_mod.MAX_CONFIG_BYTES + 10)}))
    assert load_config(p) == Config()
    assert (tmp_path / "config.json.bak").exists()


def test_load_ignores_unknown_keys_and_fixes_wrong_types(tmp_path):
    p = tmp_path / "config.json"
    data = {
        "voice_rate": "fast",          # wrong type -> default
        "voice_volume": 55,            # kept
        "sensitivity": 9,              # clamped
        "alert_roam": False,           # kept
        "detector_backend": "classic", # kept
        "future_option": {"a": 1},     # unknown -> ignored
        "target_fps": None,            # wrong type -> default
        "manual_minimap_rect": VALID_RECT,
        "minimap_mode": "manual",
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    c = load_config(p)
    assert c.voice_rate == 2
    assert c.voice_volume == 55
    assert c.sensitivity == 1.6
    assert c.alert_roam is False
    assert c.detector_backend == "classic"
    assert c.target_fps == 8.0
    assert c.minimap_mode == "manual" and c.manual_minimap_rect == VALID_RECT
    assert p.exists() and not (tmp_path / "config.json.bak").exists()  # valid JSON: not renamed


def test_load_accepts_utf8_bom_and_nan(tmp_path):
    p = tmp_path / "config.json"
    p.write_bytes(b"\xef\xbb\xbf" + '{"voice_name": "Hortense (Français)", "sensitivity": NaN}'.encode())
    c = load_config(p)
    assert c.voice_name == "Hortense (Français)"
    assert c.sensitivity == 1.0


def test_load_directory_path_does_not_raise(tmp_path):
    d = tmp_path / "config.json"
    d.mkdir()
    assert load_config(d) == Config()
    assert d.is_dir()


def test_save_roundtrip_utf8_and_no_temp_left(tmp_path):
    p = tmp_path / "sub" / "config.json"
    cfg = Config(
        voice_name="Microsoft Hortense Desktop - French (Fédération)",
        voice_rate=-4, voice_volume=70, beep_on_danger=False, alert_laner_mia=True,
        sensitivity=1.3, warn_radius=0.25, danger_radius=0.1, target_fps=10.0,
        detector_backend="onnx", detection_threshold=0.4, minimap_mode="manual",
        minimap_side="left", manual_minimap_rect=dict(VALID_RECT), download_skin_icons=False,
        autostart=False, collect_samples=True, collect_interval_s=5.0, show_preview=True,
    )
    assert save_config(cfg, p) is True
    raw = p.read_bytes()
    assert "Fédération".encode("utf-8") in raw        # UTF-8, not \u escapes
    data = json.loads(raw.decode("utf-8"))
    assert data["config_version"] == config_mod.CONFIG_VERSION
    assert load_config(p) == cfg
    assert sorted(x.name for x in p.parent.iterdir()) == ["config.json"]


def test_save_writes_validated_values(tmp_path):
    p = tmp_path / "config.json"
    assert save_config(Config(voice_rate=99, target_fps=float("nan")), p)  # type: ignore[arg-type]
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["voice_rate"] == 10 and data["target_fps"] == 8.0


def test_save_accepts_str_path_and_overwrites(tmp_path):
    p = str(tmp_path / "config.json")
    assert save_config(Config(voice_rate=1), p)
    assert save_config(Config(voice_rate=-1), p)
    assert load_config(p).voice_rate == -1


def test_save_is_atomic_when_replace_fails(tmp_path, monkeypatch):
    p = tmp_path / "cfg" / "config.json"
    assert save_config(Config(voice_rate=3), p)
    before = p.read_bytes()

    def failing_replace(src, dst):
        raise PermissionError("locked by antivirus")

    monkeypatch.setattr(config_mod.os, "replace", failing_replace)
    monkeypatch.setattr(config_mod.time, "sleep", lambda s: None)
    assert save_config(Config(voice_rate=-7), p) is False   # no exception
    assert p.read_bytes() == before                           # previous file intact
    assert sorted(x.name for x in p.parent.iterdir()) == ["config.json"]  # temp cleaned


def test_save_retries_transient_permission_error(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("busy")
        real_replace(src, dst)

    monkeypatch.setattr(config_mod.os, "replace", flaky_replace)
    monkeypatch.setattr(config_mod.time, "sleep", lambda s: None)
    assert save_config(Config(voice_volume=42), p) is True
    assert load_config(p).voice_volume == 42


def test_save_never_raises_on_unwritable_target(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert save_config(Config(), blocker / "config.json") is False


def test_concurrent_saves_leave_valid_file(tmp_path):
    p = tmp_path / "config.json"
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            for _ in range(5):
                save_config(Config(voice_volume=i), p)
                load_config(p)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors
    assert load_config(p).voice_volume in range(6)
    assert not (tmp_path / "config.json.bak").exists()


# --------------------------------------------------------------------------- logging


@pytest.fixture
def clean_logging(monkeypatch):
    """Restore the root logger and the exception hooks after a logging test."""
    root = logging.getLogger()
    level = root.level
    raise_exc = logging.raiseExceptions
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(sys, "unraisablehook", sys.unraisablehook)
    yield
    logging_setup.close_logging()
    logging.captureWarnings(False)
    root.setLevel(level)
    logging.raiseExceptions = raise_exc


def _ours(role: str | None = None) -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers
            if getattr(h, "_treeaicoach_role", None) and (role is None or h._treeaicoach_role == role)]


def _flush() -> None:
    for h in _ours():
        h.flush()


def test_setup_logging_writes_file(tmp_path, clean_logging):
    path = logging_setup.setup_logging(debug=False, console=False)
    assert path == tmp_path / "home" / "logs" / logging_setup.LOG_FILE_NAME
    assert logging_setup.current_log_file() == path
    logging.getLogger("treeaicoach.test").info("bonjour é à ç")
    logging.getLogger("treeaicoach.test").debug("hidden debug line")
    _flush()
    text = path.read_text(encoding="utf-8")
    assert "bonjour é à ç" in text
    assert "hidden debug line" not in text
    assert "INFO" in text and "[MainThread]" in text and "treeaicoach.test" in text
    fh = _ours("file")[0]
    assert fh.maxBytes == logging_setup.MAX_BYTES and fh.backupCount == 3


def test_setup_logging_idempotent(clean_logging):
    n_before = len(logging.getLogger().handlers)
    p1 = logging_setup.setup_logging(console=True)
    n_after_first = len(logging.getLogger().handlers)
    p2 = logging_setup.setup_logging(debug=True, console=True)
    p3 = logging_setup.setup_logging(debug=True, console=True)
    assert p1 == p2 == p3
    assert len(logging.getLogger().handlers) == n_after_first
    assert len(_ours("file")) == 1 and len(_ours("console")) == 1
    assert n_after_first - n_before == 2
    assert logging.getLogger().level == logging.DEBUG
    logging_setup.setup_logging(debug=False, console=False)
    assert len(_ours("console")) == 0 and len(_ours("file")) == 1
    assert logging.getLogger().level == logging.INFO


def test_setup_logging_debug_level(clean_logging):
    path = logging_setup.setup_logging(debug=True, console=False)
    logging.getLogger("treeaicoach.test").debug("visible debug line")
    _flush()
    assert "visible debug line" in path.read_text(encoding="utf-8")


def test_setup_logging_without_stderr(monkeypatch, clean_logging):
    monkeypatch.setattr(sys, "stderr", None)   # windowed PyInstaller exe
    monkeypatch.setattr(sys, "stdout", None)
    path = logging_setup.setup_logging(console=True)
    assert _ours("console") == []
    logging.getLogger("treeaicoach.test").warning("still works without console")
    _flush()
    assert "still works without console" in path.read_text(encoding="utf-8")


def test_setup_logging_follows_user_dir_change(tmp_path, monkeypatch, clean_logging):
    p1 = logging_setup.setup_logging(console=False)
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "other"))
    p2 = logging_setup.setup_logging(console=False)
    assert p1 != p2 and p2.parent == tmp_path / "other" / "logs"
    assert len(_ours("file")) == 1


def test_setup_logging_falls_back_when_log_dir_unusable(tmp_path, monkeypatch, clean_logging):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setattr(paths, "logs_dir", lambda: blocker)  # a file, not a directory
    path = logging_setup.setup_logging(console=False)
    assert path.parent == tmp_path / "systemp" / "TreeAICoach" / "logs"
    logging.getLogger("treeaicoach.test").info("fallback line")
    _flush()
    assert "fallback line" in path.read_text(encoding="utf-8")


def test_console_handler_survives_closed_stream(monkeypatch, clean_logging):
    import io

    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    logging_setup.setup_logging(console=True)
    logging.getLogger("treeaicoach.test").info("to console")
    assert "to console" in stream.getvalue()
    stream.close()
    logging.getLogger("treeaicoach.test").info("after close")  # must not raise


def test_rotation_happens(tmp_path, monkeypatch, clean_logging):
    monkeypatch.setattr(logging_setup, "MAX_BYTES", 2000)
    path = logging_setup.setup_logging(console=False)
    for i in range(200):
        logging.getLogger("treeaicoach.test").info("line %d %s", i, "x" * 50)
    _flush()
    assert path.with_name(path.name + ".1").exists()
    assert not path.with_name(path.name + ".4").exists()


def test_failed_rollover_is_not_fatal(tmp_path, monkeypatch, clean_logging):
    monkeypatch.setattr(logging_setup, "MAX_BYTES", 500)
    path = logging_setup.setup_logging(console=False)

    def locked(src, dst):
        raise PermissionError("file in use")

    fh = _ours("file")[0]
    monkeypatch.setattr(fh, "rotate", locked)
    for i in range(50):
        logging.getLogger("treeaicoach.test").info("line %d %s", i, "y" * 50)
    _flush()
    assert "line 49" in path.read_text(encoding="utf-8")


def test_sys_excepthook_logs(tmp_path, clean_logging):
    path = logging_setup.setup_logging(console=False)
    logging_setup.install_excepthooks(faulthandler_log=False)
    logging_setup.install_excepthooks(faulthandler_log=False)   # idempotent
    assert getattr(sys.excepthook, "_treeaicoach_hook", False)
    try:
        raise ValueError("boom in main")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    _flush()
    text = path.read_text(encoding="utf-8")
    assert "Uncaught exception" in text and "ValueError: boom in main" in text
    assert "Traceback" in text


def test_threading_excepthook_logs(tmp_path, clean_logging):
    path = logging_setup.setup_logging(console=False)
    logging_setup.install_excepthooks(faulthandler_log=False)

    def bad() -> None:
        raise RuntimeError("boom in thread")

    t = threading.Thread(target=bad, name="AnalysisLoop")
    t.start()
    t.join(5)
    _flush()
    text = path.read_text(encoding="utf-8")
    assert "Uncaught exception in thread AnalysisLoop" in text
    assert "RuntimeError: boom in thread" in text


def test_hooks_never_raise_on_garbage(clean_logging):
    logging_setup.setup_logging(console=False)
    logging_setup.install_excepthooks(faulthandler_log=False)
    sys.excepthook(None, None, None)  # type: ignore[arg-type]
    sys.excepthook("not a type", 42, "no tb")  # type: ignore[arg-type]
    threading.excepthook(object())  # type: ignore[arg-type]
    sys.unraisablehook(object())  # type: ignore[arg-type]
    sys.excepthook(SystemExit, SystemExit(0), None)


def test_unraisable_hook_logs(tmp_path, clean_logging):
    path = logging_setup.setup_logging(console=False)
    logging_setup.install_excepthooks(faulthandler_log=False)

    class Args:
        exc_type = OSError
        exc_value = OSError("in __del__")
        exc_traceback = None
        err_msg = "Exception ignored in"
        object = "SomeObject"

    sys.unraisablehook(Args())
    _flush()
    text = path.read_text(encoding="utf-8")
    assert "Exception ignored in" in text and "OSError: in __del__" in text


def test_faulthandler_log_file(tmp_path, clean_logging):
    import faulthandler

    was_enabled = faulthandler.is_enabled()
    try:
        logging_setup.setup_logging(console=False)
        logging_setup.install_excepthooks(faulthandler_log=True)
        assert faulthandler.is_enabled()
        assert (tmp_path / "home" / "logs" / logging_setup.FAULT_FILE_NAME).exists()
    finally:
        logging_setup.close_logging()
        if was_enabled and sys.__stderr__ is not None:
            try:
                faulthandler.enable(file=sys.__stderr__)
            except Exception:
                pass


def test_setup_logging_never_raises(monkeypatch, clean_logging):
    def boom():
        raise RuntimeError("no dir")

    monkeypatch.setattr(paths, "logs_dir", boom)
    p = logging_setup.setup_logging(console=False)
    assert isinstance(p, Path)
