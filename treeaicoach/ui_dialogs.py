"""Small modal dialogs of the launcher (Qt): message with buttons, first run, what's new."""

from __future__ import annotations

import logging
from typing import Any, Sequence

from treeaicoach import ui_kit

log = logging.getLogger(__name__)


def _dialog(app: Any, title: str, width: int = 480) -> tuple[Any, Any, Any]:
    QtWidgets = app.QtWidgets
    dlg = QtWidgets.QDialog(app.win)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(width)
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.setContentsMargins(24, 22, 24, 18)
    lay.setSpacing(12)
    lay.addWidget(app.W.label(title, "headline"))
    bar = QtWidgets.QHBoxLayout()
    bar.addStretch(1)
    bar.setSpacing(8)
    return dlg, lay, bar


def message(app: Any, title: str, text: str, buttons: Sequence[tuple[str, str]]) -> str:
    """Modal message; returns the value of the clicked button ("" when closed). The last button is
    the default (accent)."""
    if getattr(app, "_closing", False):
        return ""
    dlg, lay, bar = _dialog(app, title)
    lay.addWidget(app.W.label(text, "secondary", wrap=True))
    lay.addSpacing(6)
    result = [""]
    for i, (label, value) in enumerate(buttons):
        b = app.W.button(label, None, "primary" if i == len(buttons) - 1 else None)
        b.clicked.connect(lambda _c=False, v=value: (result.__setitem__(0, v), dlg.accept()))
        if i == len(buttons) - 1:
            b.setDefault(True)
        bar.addWidget(b)
    lay.addLayout(bar)
    try:
        dlg.exec()
    finally:
        dlg.deleteLater()
    return result[0]


def first_run(app: Any) -> None:
    """Guided first run (once), then "what's new" after an update (once per version)."""
    if app._closing:
        return
    cfg = app.cfg
    if not cfg.ui_onboarding_done:
        steps = "\n\n".join(f"{i}. {t} : {d}" for i, (t, d) in enumerate(ui_kit.onboarding_steps(), start=1))
        choice = message(app, "Avant ta première partie", steps, [("Plus tard", "later"), ("Tester l'overlay", "test")])
        app.set_options(ui_onboarding_done=True, ui_seen_changelog=ui_kit.CHANGELOG_VERSION)
        if choice == "test":
            app.test_overlay()
            app.test_voice()
        return
    if cfg.ui_seen_changelog != ui_kit.CHANGELOG_VERSION:
        text = "\n\n".join(f"{t} : {d}" for t, d in ui_kit.CHANGELOG)
        app.set_option("ui_seen_changelog", ui_kit.CHANGELOG_VERSION)
        message(app, f"Nouveautés de la version {ui_kit.CHANGELOG_VERSION}", text, [("OK", "ok")])


__all__ = ["message", "first_run"]
