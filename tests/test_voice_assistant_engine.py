"""The Vosk engine is downloaded on first use (not bundled: GitHub's 100 MB file limit)."""

from __future__ import annotations

from treeaicoach import voice_assistant as va


def test_wheel_pin_is_https_and_hashed() -> None:
    assert va.VOSK_WHEEL_URL.startswith("https://files.pythonhosted.org/") and va.VOSK_WHEEL_URL.endswith("win_amd64.whl")
    assert len(va.VOSK_WHEEL_SHA256) == 64


def test_import_vosk_never_raises() -> None:
    mod = va.import_vosk()
    assert mod is None or hasattr(mod, "Model")
