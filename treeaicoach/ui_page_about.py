"""À propos: version, updates (check / install with progress), help (borderless, keys), shortcuts,
what is new, legal text."""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach import APP_NAME, __version__, ui_kit

log = logging.getLogger(__name__)


class AboutPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W, QtWidgets, Qt = app.W, app.QtWidgets, app.QtCore.Qt
        self.page = W.Page("À propos")

        head = W.card()
        hl = QtWidgets.QHBoxLayout(head)
        hl.setContentsMargins(20, 18, 20, 18)
        hl.setSpacing(16)
        from treeaicoach.ui_common import app_icon_path  # noqa: PLC0415

        icon = app_icon_path("png")
        if icon:
            logo = QtWidgets.QLabel()
            pm = app.QtGui.QPixmap(str(icon))
            if not pm.isNull():
                logo.setPixmap(pm.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                hl.addWidget(logo)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(W.label(APP_NAME, "headline"))
        col.addWidget(W.label(f"Version {__version__}", "secondary"))
        hl.addLayout(col, 1)
        self.page.add(head)

        up = self.page.section("Mise à jour", "Les nouvelles versions sont publiées sur GitHub ; le fichier est "
                                              "vérifié avant d'être installé.")
        self.check_btn = W.button("Rechercher", lambda: app.check_updates(quiet=False))
        self.status_row = up.add(W.Row("Version installée : " + __version__, "Clique sur « Rechercher ».",
                                       self.check_btn))
        self.install_btn = W.button("Installer", app.install_update, "primary")
        self.install_btn.setEnabled(False)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(160)
        self.progress.hide()
        box = QtWidgets.QWidget()
        bl = QtWidgets.QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(12)
        bl.addWidget(self.progress)
        bl.addWidget(self.install_btn)
        up.add(W.Row("Installer la nouvelle version", "TreeAI redémarre tout seul.", box))
        self.manual = up.add(W.Row("Téléchargement manuel", "Si l'installation échoue : la page de la dernière "
                                   "version.", W.button("Ouvrir", app.open_manual_download)))
        self.manual.hide()
        up.group.sync_separators()
        app.switch_row(up, "check_updates_on_start", "Vérifier au démarrage", None)
        app.entry_row(up, "github_token", "Jeton GitHub", "Seulement pour un dépôt privé. Vide sinon.", secret=True,
                      placeholder="Aucun")

        helpsec = self.page.section("Avant ta première partie")
        for i, (title, text) in enumerate(ui_kit.onboarding_steps(), start=1):
            helpsec.add(W.Row(f"{i}. {title}", text))

        self.keys = self.page.section("Touches en jeu", "Réglables dans Réglages > Touches en jeu.")
        self._key_rows: list[Any] = []
        self._fill_keys()

        sc = self.page.section("Raccourcis de la fenêtre")
        for keys, what in ui_kit.SHORTCUTS:
            sc.add(W.Row(what, None, W.label(keys, "value")))

        new = self.page.section(f"Nouveautés de la version {ui_kit.CHANGELOG_VERSION}")
        for title, text in ui_kit.CHANGELOG:
            new.add(W.Row(title, text))

        legal = self.page.section("Mentions")
        box2 = QtWidgets.QWidget()
        l2 = QtWidgets.QVBoxLayout(box2)
        l2.setContentsMargins(16, 12, 16, 14)
        l2.addWidget(W.label(ui_kit.ABOUT_TEXT, "secondary", wrap=True))
        legal.add(box2)

    def _fill_keys(self) -> None:
        W = self.app.W
        for r in self._key_rows:
            r.hide()
        self._key_rows = []
        for key, what in ui_kit.game_keys(self.app.cfg):
            self._key_rows.append(self.keys.add(W.Row(what, None, W.label(key, "value"))))
        self.keys.group.sync_separators()

    def on_config(self, diff: set[str]) -> None:
        if any(f.startswith("hotkey_") for f in diff):
            self._fill_keys()

    def set_update_status(self, text: str, tone: str = "", manual: bool = False) -> None:
        self.status_row.set_desc(text)
        if self.status_row.desc is not None:
            self.app.W.set_prop(self.status_row.desc, "tone", tone)
        self.manual.setVisible(bool(manual))
        self.status_row.parentWidget().sync_separators()

    def set_install_enabled(self, on: bool) -> None:
        self.install_btn.setEnabled(bool(on))

    def set_progress(self, frac: float | None) -> None:
        if frac is None:
            self.progress.hide()
            return
        self.progress.show()
        self.progress.setValue(int(1000 * max(0.0, min(1.0, frac))))


def build(app: Any) -> AboutPage:
    return AboutPage(app)
