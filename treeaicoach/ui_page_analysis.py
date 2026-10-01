"""Analysis page ("Analyses"): game list and reports, progress tab, replay tab.

Mixin of :class:`treeaicoach.ui.CoachApp` (split out of ``ui.py`` without any behaviour
change): the methods use the app state (``self.cfg``, ``self.ctk``, widgets ...) created in
``CoachApp.__init__`` and run on the Tk thread only. Not meant to be used on its own.
"""

from __future__ import annotations

import logging
import webbrowser
from pathlib import Path
from typing import Any

from PIL import Image

from treeaicoach import paths
from treeaicoach.ui_common import (
    _POSITION_FR,
    ACCENT,
    BG,
    BTN_H_SMALL,
    DANGER,
    DIM,
    GAMES_PAGE,
    LINE,
    LINE_STRONG,
    MUTED,
    ON_ACCENT,
    PANEL,
    PANEL_HI,
    RADIUS,
    SAFE,
    TEXT,
    WARNING,
    Dropdown,
    Segmented,
    _game_html_path,
    _game_json_path,
    _guarded,
    _hex_rgb,
    _int_or_none,
    flat_placeholder,
    fmt_clock,
    fmt_decimal_fr,
    fmt_game_date,
    game_datetime,
    game_field,
    game_result,
    open_path,
    precision_color,
    radar_placeholder,
    session_stats,
    square_icon,
    ui_text,
)

log = logging.getLogger("treeaicoach.ui")   # same logger as before the split


def _report_function(name: str) -> Any:
    """Resolved through :mod:`treeaicoach.ui` at call time (tests patch ``ui._report_function``)."""
    from treeaicoach import ui  # noqa: PLC0415 - circular at import time

    return ui._report_function(name)


class AnalysisPageMixin:
    """Analysis page ("Analyses"): game list and reports, progress tab, replay tab."""

    # ------------------------------------------------------------------ analysis page
    def _build_analysis_page(self) -> Any:
        ctk = self.ctk
        page, right, body = self._page("Analyses", "Parties, progrès et replay", max_width=1120)
        b = self._button(right, "Dernier rapport", self.open_last_report, "primary", icon="report", height=26)
        b.grid(row=0, column=0, padx=(0, 6))
        self._tip(b, "Ouvre le rapport de ta dernière partie dans le navigateur.")
        b = self._button(right, "", self.refresh_games, "ghost", icon="refresh", width=30, height=26)
        b.grid(row=0, column=1, padx=(0, 2))
        self._tip(b, "Actualiser la liste")
        b = self._button(right, "", self.open_games_dir, "ghost", icon="folder", width=30, height=26)
        b.grid(row=0, column=2, padx=(0, 2))
        self._tip(b, "Ouvrir le dossier des parties et des rapports")
        b = self._button(right, "", self.copy_share_summary, "ghost", icon="copy", width=30, height=26)
        b.grid(row=0, column=3)
        self._tip(b, "Copier un résumé de ta dernière partie à partager (Discord, réseaux).")
        body._sections = []  # type: ignore[attr-defined]

        # ---------------------------------------------------------------- tab "Parties"
        games = self._frame(body)
        games.grid(row=0, column=0, sticky="ew")
        games.grid_columnconfigure(0, weight=1)
        games.title = "Parties"  # type: ignore[attr-defined]
        body._sections.append(games)
        top = self._frame(games)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        top.grid_columnconfigure(0, weight=1)
        self.session_scope = self._label(top, "SESSION", self.fonts.caps, DIM, anchor="w")
        self.session_scope.grid(row=0, column=0, sticky="w")
        self.lcu_status = self._label(top, "Client LoL : …", self.fonts.tiny, DIM, anchor="e")
        self.lcu_status.grid(row=0, column=1, sticky="e")
        self._tip(self.lcu_status, "Après chaque partie, TreeAI lit l'historique du client League of Legends "
                                   "(API locale officielle, lecture seule) : positions exactes, écarts d'or et "
                                   "fiabilité de ses alertes dans le rapport.")
        strip = ctk.CTkFrame(games, fg_color=PANEL, corner_radius=RADIUS, border_width=1, border_color=LINE)
        strip.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self.stat_labels: dict[str, tuple[Any, Any]] = {}
        for i, (key, label, col) in enumerate((("games", "Parties", TEXT), ("wins", "Victoires", SAFE),
                                               ("deaths", "Morts / partie", DANGER),
                                               ("avoided", "Ganks évités", TEXT))):
            strip.grid_columnconfigure(2 * i, weight=1, uniform="stat")
            if i:
                ctk.CTkFrame(strip, width=1, height=1, fg_color=LINE, corner_radius=0).grid(
                    row=0, column=2 * i - 1, sticky="ns", pady=10)
            cell = self._frame(strip)
            cell.grid(row=0, column=2 * i, sticky="ew", padx=16, pady=10)
            self._caption(cell, label, DIM, anchor="w").grid(row=0, column=0, sticky="w")
            v = self._label(cell, "-", self.fonts.stat, col, anchor="w")
            v.grid(row=1, column=0, sticky="w")
            sub = self._label(cell, " ", self.fonts.tiny, MUTED, anchor="w")
            sub.grid(row=2, column=0, sticky="w")
            self.stat_labels[key] = (v, sub)
        self.games_box = self._frame(games)
        self.games_box.grid(row=2, column=0, sticky="ew")
        self.games_box.grid_columnconfigure(1, weight=1)
        self._games_empty(text="Chargement…")

        # ---------------------------------------------------------------- tab "Progrès"
        prog = self._frame(body)
        prog.grid(row=1, column=0, sticky="ew")
        prog.grid_columnconfigure(0, weight=1)
        prog.title = "Progrès"  # type: ignore[attr-defined]
        body._sections.append(prog)
        self.progress_box = prog
        self._progress_sig: Any = None
        self._label(prog, "Chargement…", self.fonts.small, DIM, anchor="w").grid(row=0, column=0, sticky="w")

        # ---------------------------------------------------------------- tab "Replay"
        rp = self._frame(body)
        rp.grid(row=2, column=0, sticky="ew")
        rp.grid_columnconfigure(1, weight=1)
        rp.title = "Replay"  # type: ignore[attr-defined]
        body._sections.append(rp)
        self._build_replay(rp)

        def on_tab(label: str) -> None:
            if label == "Progrès":
                self.refresh_progress()
            elif label == "Replay":
                self._replay_ensure_loaded()
        self._analysis_tabs = self._tabs(page, body, (("Parties", ("Parties",)), ("Progrès", ("Progrès",)),
                                                      ("Replay", ("Replay",))))
        self._analysis_page = page
        for lbl, btn in self._analysis_tabs.items():
            btn.configure(command=self.cb(lambda ll=lbl: (page.select_tab(ll), on_tab(ll))))
        return page

    def _games_empty(self, text: str | None = None) -> None:
        for w in self.games_box.winfo_children():
            w.destroy()
        msg = text or "Aucune partie enregistrée pour l'instant."
        self._label(self.games_box, msg, self.fonts.body, TEXT, anchor="w").grid(
            row=0, column=0, columnspan=8, sticky="w", pady=(8, 2))
        if text is None:
            self._label(self.games_box, "Joue une partie avec l'analyse active : le rapport (morts, ganks, jungler "
                                        "adverse, conseils) apparaît ici.", self.fonts.small, MUTED, anchor="w",
                        wraplength=560, justify="left").grid(row=1, column=0, columnspan=8, sticky="w")

    @_guarded
    def refresh_games(self) -> None:
        """Reload the game history and the champion portraits (background thread)."""
        def job() -> tuple[list[dict], dict[str, Any]]:
            fn = _report_function("list_games")
            if fn is None:
                return [], {}
            games = [g for g in (fn(50) or []) if isinstance(g, dict)]
            icons: dict[str, Any] = {}
            try:
                from treeaicoach.champions import get_default_db  # noqa: PLC0415

                db = get_default_db()
                for g in games[:50]:
                    alias = str(game_field(g, "champion", "alias", default="") or "")
                    if alias and alias not in icons and alias not in self._game_icons:
                        try:
                            icons[alias] = square_icon(db.load_icon(alias), 56, bg=BG)
                        except Exception:
                            icons[alias] = None
            except Exception:
                log.debug("champion portraits unavailable", exc_info=True)
            return games, icons

        def done(res: tuple[list[dict], dict[str, Any]]) -> None:
            games, icons = res
            self._game_icons.update(icons)
            self._show_games(games)

        self._dispatcher.run(job, done, self.cb(lambda e: self._games_empty(
            text="Impossible de lire l'historique des parties")), name="TreeAI-ui-games")
        if "analysis" in self._built:
            self._refresh_lcu_status()
        self._progress_sig = None

    @_guarded
    def _refresh_lcu_status(self) -> None:
        """"Client LoL : connecté / non trouvé" on the Analyses page (probed on a worker thread)."""
        lbl = getattr(self, "lcu_status", None)
        if lbl is None:
            return

        def job() -> str:
            if not getattr(self.cfg, "lcu_enabled", True):
                return "Client LoL : désactivé"
            from treeaicoach.lcu import get_default_client  # noqa: PLC0415

            return get_default_client().status_text()

        def done(text: str) -> None:
            self._lcu_text = str(text or "")
            try:
                lbl.configure(text=ui_text(text), text_color=SAFE if text.endswith("connecté") else DIM)
            except Exception:
                pass

        self._dispatcher.run(job, done, None, name="TreeAI-ui-lcu")

    @_guarded
    def _show_games(self, games: list[dict]) -> None:
        """New history: dashboard "avant la partie"; the Analyses table is redrawn only when it is
        on screen and the history changed (50 rows of widgets are not rebuilt for nothing)."""
        self._games = games
        self._games_sig = tuple((str(game_field(g, "start", "date", default="") or ""), game_result(g),
                                 game_field(g, "precision"), str(_game_json_path(g) or "")) for g in games[:50])
        self.refresh_pregame()
        if "analysis" in self._built and self._current_page == "analysis" and self._games_sig != self._games_shown_sig:
            self._show_games_table()

    @_guarded
    def _show_games_table(self) -> None:
        if "analysis" not in self._built:
            return
        games = self._games
        self._games_shown_sig = self._games_sig
        st = session_stats(games)
        self.session_scope.configure(text=f"SESSION · {st['scope'].upper()}")
        wr = st["winrate"]
        dpg = st["deaths_per_game"]
        vals = {
            "games": (str(st["games"]), "parties analysées"),
            "wins": (str(st["wins"]), f"{round(100 * wr)} % de victoires" if wr is not None else " "),
            "deaths": (fmt_decimal_fr(dpg, 1) if dpg is not None else "-", "en moyenne"),
            "avoided": (str(st["ganks_avoided"]), f"sur {st['ganks']} ganks subis" if st["ganks"] else "aucun gank"),
        }
        for key, (v, sub) in vals.items():
            self.stat_labels[key][0].configure(text=v)
            self.stat_labels[key][1].configure(text=sub)
        for w in self.games_box.winfo_children():
            w.destroy()
        self._replay_games_menu(games)
        if not games:
            self._games_empty()
            return
        box = self.games_box
        for c, (txt, anchor) in enumerate((("", "w"), ("Champion", "w"), ("Résultat", "w"), ("K / D / A", "e"),
                                           ("Ganks", "e"), ("Précision", "e"), ("Durée", "e"), ("", "e"))):
            if txt:
                cap = self._caption(box, txt, DIM, anchor=anchor)
                cap.grid(row=0, column=c, sticky=anchor, padx=(0, 12), pady=(12, 8))
                if txt == "Précision":
                    self._tip(cap, "Précision des coups notés (sur 100) : coups de maître, erreurs, gaffes… "
                                   "« - » : partie non notée.")
        for c, w in ((0, 48), (2, 84), (3, 84), (4, 56), (5, 76), (6, 56)):
            box.grid_columnconfigure(c, minsize=self._scaled(w))
        self._hline(box, LINE_STRONG).grid(row=1, column=0, columnspan=8, sticky="ew")
        shown = games[: self._games_limit]
        for i, g in enumerate(shown):
            self._game_row(i, g)
        if len(games) > len(shown):
            more = self._button(box, f"Afficher plus ({len(games) - len(shown)})", self._more_games, "ghost",
                                height=30)
            more.grid(row=2 + 2 * len(shown), column=0, columnspan=8, sticky="w", pady=(10, 4))

    def _more_games(self) -> None:
        self._games_limit += GAMES_PAGE * 2
        self._show_games_table()

    def _game_row(self, i: int, g: dict) -> None:
        ctk = self.ctk
        box = self.games_box
        r = 2 + 2 * i
        alias = str(game_field(g, "champion", "alias", default="") or "")
        name = str(game_field(g, "champion_name", "name", default="") or alias or "Champion inconnu")
        res = game_result(g)
        pil = self._game_icons.get(alias)
        if pil is None:
            pil = square_icon(None, 56, bg=BG)
        img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(36, 36))
        self._images[f"game-{i}"] = img
        ctk.CTkLabel(box, text="", image=img, fg_color="transparent").grid(row=r, column=0, sticky="w", pady=8)
        cell = self._frame(box)
        cell.grid(row=r, column=1, sticky="w", padx=(0, 12))
        self._label(cell, name, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        when = fmt_game_date(game_datetime(g))
        pos = game_field(g, "position")
        sub = when + (f" · {_POSITION_FR.get(pos.upper(), pos.title())}" if isinstance(pos, str) and pos else "")
        self._label(cell, sub, self.fonts.tiny, DIM, anchor="w").grid(row=1, column=0, sticky="w")
        rtxt, rcol = {"win": ("Victoire", SAFE), "lose": ("Défaite", DANGER)}.get(
            res or "", ("Inachevée" if game_field(g, "incomplete") else "-", MUTED))
        self._label(box, rtxt, self.fonts.h3, rcol, anchor="w").grid(row=r, column=2, sticky="w", padx=(0, 12))
        k, d, a = (_int_or_none(game_field(g, x)) for x in ("kills", "deaths", "assists"))
        kda = f"{k if k is not None else '?'} / {d if d is not None else '?'} / {a if a is not None else '?'}"
        self._label(box, kda, self.fonts.num, TEXT, anchor="e").grid(row=r, column=3, sticky="e", padx=(0, 12))
        ganks = _int_or_none(game_field(g, "ganks"))
        surv = _int_or_none(game_field(g, "ganks_survived"))
        gtxt = "-" if ganks is None else (f"{surv}/{ganks}" if surv is not None and ganks else str(ganks))
        gl = self._label(box, gtxt, self.fonts.num, TEXT, anchor="e")
        gl.grid(row=r, column=4, sticky="e", padx=(0, 12))
        self._tip(gl, "Ganks évités / ganks subis")
        prec = _int_or_none(game_field(g, "precision"))
        pl = self._label(box, "-" if prec is None else str(prec), self.fonts.num, precision_color(prec), anchor="e")
        pl.grid(row=r, column=5, sticky="e", padx=(0, 12))
        self._tip(pl, "Précision des coups notés (sur 100)" if prec is not None else "Partie non notée")
        dur = game_field(g, "duration")
        self._label(box, fmt_clock(dur) if isinstance(dur, (int, float)) and dur > 0 else "-", self.fonts.small,
                    MUTED, anchor="e").grid(row=r, column=6, sticky="e", padx=(0, 12))
        btns = self._frame(box)
        btns.grid(row=r, column=7, sticky="e")
        self._button(btns, "Rapport", lambda gg=g: self.open_report(gg), "secondary", width=70,
                     height=24).grid(row=0, column=0, padx=(0, 4))
        self._button(btns, "Replay", lambda gg=g: self.open_replay(gg), "secondary", width=62,
                     height=24).grid(row=0, column=1, padx=(0, 4))
        fb = self._button(btns, "", lambda gg=g: self.open_game_folder(gg), "ghost", icon="folder", width=26,
                          height=24)
        fb.grid(row=0, column=2)
        self._tip(fb, "Afficher le fichier")
        self._hline(box).grid(row=r + 1, column=0, columnspan=8, sticky="ew")

    # ------------------------------------------------------------------ progress tab (progress.py)
    @_guarded
    def refresh_progress(self) -> None:
        """Compute the trends of the last games on a worker thread, then draw the tab."""
        if self._progress_sig == "loading":
            return
        if self._progress_sig is not None:
            return
        self._progress_sig = "loading"

        def job() -> list[dict]:
            from treeaicoach import progress  # noqa: PLC0415

            return progress.collect(paths.user_data_dir() / "games", last=20)

        def failed(exc: BaseException) -> None:
            self._progress_sig = None
            self.show_error(f"Progrès indisponibles : {exc}")

        self._dispatcher.run(job, self._show_progress, self.cb(failed), name="TreeAI-ui-progress")

    @_guarded
    def _show_progress(self, rows: list[dict]) -> None:
        from treeaicoach import progress  # noqa: PLC0415

        self._progress_sig = len(rows)
        box = self.progress_box
        for w in box.winfo_children():
            w.destroy()
        ctk = self.ctk
        if len(rows) < 2:
            self._label(box, "Pas encore assez de parties.", self.fonts.body, TEXT, anchor="w").grid(
                row=0, column=0, sticky="w")
            self._label(box, "Les courbes apparaissent après 2 parties enregistrées (5 min minimum).",
                        self.fonts.small, MUTED, anchor="w").grid(row=1, column=0, sticky="w")
            return
        tr = progress.trends(rows)
        pts = progress.focus_points(rows, 3)
        self._caption(box, f"Tes 3 points à travailler · {len(rows)} dernières parties", MUTED, anchor="w").grid(
            row=0, column=0, sticky="w")
        self._hline(box, LINE_STRONG).grid(row=1, column=0, sticky="ew", pady=(4, 6))
        fp = self._frame(box)
        fp.grid(row=2, column=0, sticky="ew", pady=(0, 18))
        fp.grid_columnconfigure(1, weight=1)
        for i, (title, text) in enumerate(pts):
            self._label(fp, str(i + 1), self.fonts.stat, ACCENT, anchor="n", width=28).grid(
                row=i, column=0, sticky="n", pady=(0, 8))
            cell = self._frame(fp)
            cell.grid(row=i, column=1, sticky="ew", pady=(2, 8))
            self._label(cell, title, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
            self._label(cell, ui_text(text), self.fonts.small, MUTED, anchor="w", justify="left",
                        wraplength=600).grid(row=1, column=0, sticky="w")
        self._caption(box, "Tendances", MUTED, anchor="w").grid(row=3, column=0, sticky="w")
        self._hline(box, LINE_STRONG).grid(row=4, column=0, sticky="ew", pady=(4, 0))
        tab = self._frame(box)
        tab.grid(row=5, column=0, sticky="ew")
        tab.grid_columnconfigure(1, weight=1)
        for c, (txt, anchor) in enumerate((("Mesure", "w"), ("Parties", "w"), ("Dernière", "e"),
                                           ("Moyenne", "e"), ("Tendance", "e"))):
            self._caption(tab, txt, DIM, anchor=anchor).grid(row=0, column=c, sticky=anchor, padx=(0, 16),
                                                            pady=(6, 2))
        r = 1
        for key, (label, unit, _higher, dec) in progress.METRICS.items():
            t = tr.get(key) or {}
            if t.get("avg") is None:
                continue
            self._label(tab, label, self.fonts.body, TEXT, anchor="w").grid(row=r, column=0, sticky="w",
                                                                            padx=(0, 16), pady=4)
            base = 0.0 if key.startswith("gold_diff") else None
            pil = progress.sparkline(t["values"], 360, 30, progress.metric_color(t), baseline=base,
                                     bg=_hex_rgb(BG))
            img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(180, 30))
            self._images[f"spark-{key}"] = img
            ctk.CTkLabel(tab, text="", image=img, fg_color="transparent").grid(row=r, column=1, sticky="w",
                                                                               padx=(0, 16))
            signed = key.startswith("gold_diff")
            suffix = f" {unit}" if unit else ""
            self._label(tab, progress.fmt_num(t["last"], dec, signed) + suffix, self.fonts.num, TEXT,
                        anchor="e").grid(row=r, column=2, sticky="e", padx=(0, 16))
            self._label(tab, progress.fmt_num(t["avg"], dec, signed) + suffix, self.fonts.small, MUTED,
                        anchor="e").grid(row=r, column=3, sticky="e", padx=(0, 16))
            arrow = {"up": "▲ en hausse", "down": "▼ en baisse"}.get(t["direction"], "stable")
            col = SAFE if t.get("better") is True else DANGER if t.get("better") is False else DIM
            self._label(tab, arrow, self.fonts.small, col, anchor="e").grid(row=r, column=4, sticky="e")
            self._hline(tab).grid(row=r + 1, column=0, columnspan=5, sticky="ew")
            r += 2
        if tr.get("gold_diff10", {}).get("avg") is None:
            self._label(box, "Or à 10/15 min et fiabilité TreeAI : disponibles quand le client LoL est ouvert après "
                             "la partie.", self.fonts.tiny, DIM, anchor="w").grid(row=6, column=0, sticky="w",
                                                                                 pady=(8, 0))

    # ------------------------------------------------------------------ replay tab (replay.py)
    def _build_replay(self, rp: Any) -> None:
        import tkinter as tk  # noqa: PLC0415

        ctk = self.ctk
        self._replay: dict[str, Any] = {"model": None, "t": 0.0, "playing": False, "speed": 30.0, "job": None,
                                        "path": None, "loading": False, "icons": {}}
        bar = self._frame(rp)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        bar.grid_columnconfigure(1, weight=1)
        self._caption(bar, "Partie", DIM, anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.replay_menu = Dropdown(self, bar, ["Aucune partie"], self.cb(self._replay_menu_pick), width=340,
                                    height=BTN_H_SMALL)
        self.replay_menu.grid(row=0, column=1, sticky="w")
        self._replay_choices: dict[str, dict] = {}
        size = self._scaled(300)
        self._replay_size = size
        holder = tk.Frame(rp, bg=BG, width=size, height=size)
        holder.grid(row=1, column=0, sticky="nw", padx=(0, 16))
        holder.grid_propagate(False)
        from PIL import ImageTk  # noqa: PLC0415

        self._replay_photo = ImageTk.PhotoImage(flat_placeholder(size), master=self.root)

        def textured(img: Image.Image) -> None:
            if self._replay.get("model") is None:
                self._replay_photo.paste(img)
        self._dispatcher.run(lambda: radar_placeholder(size), textured, None, name="TreeAI-ui-replay-placeholder")
        self.replay_img = tk.Label(holder, image=self._replay_photo, bg=BG, bd=0, highlightthickness=0)
        self.replay_img.place(x=0, y=0, relwidth=1, relheight=1)
        side = self._frame(rp)
        side.grid(row=1, column=1, sticky="nsew")
        side.grid_columnconfigure(0, weight=1)
        self.replay_clock = self._label(side, "--:--", self.fonts.clock, TEXT, anchor="w")
        self.replay_clock.grid(row=0, column=0, sticky="w")
        self.replay_caption = self._label(side, "Choisis une partie.", self.fonts.small, MUTED, anchor="w",
                                          justify="left", wraplength=380)
        self.replay_caption.grid(row=1, column=0, sticky="w", pady=(0, 2))
        self.replay_plays_lbl = self._label(side, "", self.fonts.tiny_bold, ACCENT, anchor="w")
        self.replay_plays_lbl.grid(row=6, column=0, sticky="w", pady=(6, 0))
        self._tip(self.replay_plays_lbl, "Coups notés de la partie (style échecs) : précision sur 100.")
        ctl = self._frame(side)
        ctl.grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.replay_play = self._button(ctl, "Lecture", self.replay_toggle, "primary", icon="play", width=86,
                                        height=26)
        self.replay_play.grid(row=0, column=0, padx=(0, 6))
        b = self._button(ctl, "<", lambda: self.replay_jump(-1), "secondary", width=28, height=26)
        b.grid(row=0, column=1, padx=(0, 2))
        self._tip(b, "Moment clé précédent (mort, gank)")
        b = self._button(ctl, ">", lambda: self.replay_jump(1), "secondary", width=28, height=26)
        b.grid(row=0, column=2, padx=(0, 8))
        self._tip(b, "Moment clé suivant (mort, gank)")
        self.replay_speed = Segmented(self, ctl, ["x10", "x30", "x60", "x120"], self.cb(self._replay_speed),
                                      height=BTN_H_SMALL, font=self.fonts.tiny_bold)
        self.replay_speed.grid(row=0, column=3)
        self.replay_speed.set("x30")
        self._tip(self.replay_speed, "Vitesse : secondes de jeu par seconde")
        self._caption(side, "Moments clés", DIM, anchor="w").grid(row=3, column=0, sticky="w", pady=(4, 0))
        self._hline(side, LINE_STRONG).grid(row=4, column=0, sticky="ew", pady=(4, 0))
        self.replay_moments = ctk.CTkScrollableFrame(side, fg_color="transparent", height=200, corner_radius=0)
        self.replay_moments.grid(row=5, column=0, sticky="ew")
        self.replay_moments.grid_columnconfigure(1, weight=1)
        tl_w = self._scaled(640)
        self._replay_tl_w = tl_w
        self.replay_tl = tk.Label(rp, bg=BG, bd=0, highlightthickness=0, cursor="hand2")
        self.replay_tl.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.replay_tl.bind("<Button-1>", lambda e: self._replay_click(e.x), add="+")
        self.replay_tl.bind("<B1-Motion>", lambda e: self._replay_click(e.x), add="+")
        self._replay_tl_photo: Any = None
        self._replay_legend = self._label(rp, "x mort · ▲ gank · ■ kill · | objectif · ◆ coup noté", self.fonts.tiny,
                                          DIM, anchor="w")
        self._replay_legend.grid(row=3, column=0, columnspan=2, sticky="w")

    def _replay_games_menu(self, games: list[dict]) -> None:
        menu = getattr(self, "replay_menu", None)
        if menu is None:
            return
        self._replay_choices = {}
        for g in games[:50]:
            p = _game_json_path(g)
            if p is None:
                continue
            name = str(game_field(g, "champion_name", "champion", default="") or "?")
            res = {"win": "V", "lose": "D"}.get(game_result(g) or "", "-")
            label = f"{fmt_game_date(game_datetime(g))} · {name} · {res}"
            while label in self._replay_choices:
                label += " "
            self._replay_choices[label] = g
        values = list(self._replay_choices) or ["Aucune partie"]
        try:
            menu.configure(values=values)
            if self._replay.get("path") is None:
                menu.set(values[0])
        except Exception:
            pass

    def _replay_ensure_loaded(self) -> None:
        if self._replay.get("model") is None and not self._replay.get("loading") and self._replay_choices:
            self._replay_menu_pick(next(iter(self._replay_choices)))

    def _replay_menu_pick(self, label: str) -> None:
        g = self._replay_choices.get(label)
        if g is not None:
            self._replay_load(g)

    @_guarded
    def open_replay(self, game: dict) -> None:
        """Replay button of a game row: switch to the Replay tab and load that game."""
        page = getattr(self, "_analysis_page", None)
        if page is not None and hasattr(page, "select_tab"):
            page.select_tab("Replay")
        for label, g in self._replay_choices.items():
            if g is game:
                self.replay_menu.set(label)
        self._replay_load(game)

    def _replay_load(self, game: dict) -> None:
        p = _game_json_path(game)
        if p is None or self._replay.get("loading"):
            return
        self._replay_stop()
        self._replay["loading"] = True
        self.replay_caption.configure(text="Chargement de la partie…")

        def job() -> Any:
            import json  # noqa: PLC0415

            from treeaicoach import replay  # noqa: PLC0415

            rec = json.loads(Path(p).read_text(encoding="utf-8"))
            icons: dict[str, Any] = {}
            try:     # champion portraits read here, not on the Tk thread while drawing
                from treeaicoach.champions import get_default_db  # noqa: PLC0415

                db = get_default_db()
                for r in rec.get("roster") or []:
                    alias = str((r or {}).get("alias") or "")
                    if alias and "?" not in alias and alias not in icons:
                        icons[alias] = db.load_icon(alias)
            except Exception:
                log.debug("replay portraits unavailable", exc_info=True)
            return replay.ReplayModel(rec), icons

        def done(res: Any) -> None:
            model, icons = res
            self._replay["loading"] = False
            self._replay["model"] = model
            self._replay["path"] = p
            self._replay["t"] = model.start
            self._replay["icons"] = dict(icons)
            self._replay_fill_moments(model)
            m = model.next_marker(model.start - 1)
            self._replay_seek(m.t - 8 if m is not None else model.start)

        def failed(exc: BaseException) -> None:
            self._replay["loading"] = False
            self.replay_caption.configure(text=f"Impossible de lire cette partie : {exc}")

        self._dispatcher.run(job, self.cb(done), self.cb(failed), name="TreeAI-ui-replay")

    def _replay_fill_moments(self, model: Any) -> None:
        box = self.replay_moments
        for w in box.winfo_children():
            w.destroy()
        from treeaicoach import replay  # noqa: PLC0415

        cols = {"death": DANGER, "gank": WARNING, "kill": SAFE, "objective": MUTED}
        rows = [m for m in model.markers if m.kind in ("death", "gank", "kill", "play")][:80]
        summ = None
        try:
            from treeaicoach import plays as _plays  # noqa: PLC0415

            summ = _plays.summary_from_record(model.record)
            self.replay_plays_lbl.configure(text=ui_text(_plays.summary_line(summ)) if summ else "")
        except Exception:
            log.debug("no play summary", exc_info=True)
        if not rows:
            self._label(box, "Aucun moment clé enregistré.", self.fonts.small, DIM, anchor="w").grid(
                row=0, column=0, columnspan=2, sticky="w")
        for i, m in enumerate(rows):
            col = cols.get(m.kind, MUTED)
            if m.kind == "play":
                col = "#%02X%02X%02X" % replay.play_rgb(m.cls)
            b = self.ctk.CTkButton(box, text=replay.fmt_clock(m.t), width=44, height=20, corner_radius=RADIUS,
                                   font=self.fonts.tiny_bold, fg_color="transparent", hover_color=PANEL_HI,
                                   text_color=col, anchor="w",
                                   command=self.cb(lambda tt=m.t: self._replay_seek(tt - 6)))
            b.grid(row=i, column=0, sticky="w")
            self._label(box, ui_text(m.label), self.fonts.small, TEXT, anchor="w").grid(row=i, column=1, sticky="w")

    def _replay_icon(self, alias: str) -> Any:
        cache = self._replay["icons"]
        if alias not in cache:
            try:
                from treeaicoach.champions import get_default_db  # noqa: PLC0415

                cache[alias] = get_default_db().load_icon(alias) if alias and "?" not in alias else None
            except Exception:
                cache[alias] = None
        return cache[alias]

    def _replay_seek(self, t: float) -> None:
        model = self._replay.get("model")
        if model is None:
            return
        t = min(max(float(t), model.start), model.end)
        self._replay["t"] = t
        self._replay_draw()

    def _replay_draw(self) -> None:
        model = self._replay.get("model")
        if model is None:
            return
        from PIL import ImageTk  # noqa: PLC0415

        from treeaicoach import replay  # noqa: PLC0415

        t = self._replay["t"]
        try:
            im = replay.render_frame(model, t, self._replay_size, icon_loader=self._replay_icon)
            self._replay_photo = ImageTk.PhotoImage(im, master=self.root)
            self.replay_img.configure(image=self._replay_photo)
            tl = replay.render_timeline(model, self._replay_tl_w, self._scaled(34), t)
            self._replay_tl_photo = ImageTk.PhotoImage(tl, master=self.root)
            self.replay_tl.configure(image=self._replay_tl_photo)
        except Exception:
            log.exception("Replay rendering failed")
        self.replay_clock.configure(text=f"{replay.fmt_clock(t)} / {replay.fmt_clock(model.end)}")
        self.replay_caption.configure(text=ui_text(replay.frame_caption(model, t)))

    def _replay_click(self, x: int) -> None:
        model = self._replay.get("model")
        if model is None:
            return
        from treeaicoach import replay  # noqa: PLC0415

        self._replay_seek(replay.time_at_x(model, x, self._replay_tl_w))

    def _replay_speed(self, label: str) -> None:
        try:
            self._replay["speed"] = float(str(label).lstrip("x"))
        except ValueError:
            pass

    @_guarded
    def replay_toggle(self) -> None:
        if self._replay.get("model") is None:
            self._replay_ensure_loaded()
            return
        if self._replay["playing"]:
            self._replay_stop()
        else:
            if self._replay["t"] >= self._replay["model"].end - 0.5:
                self._replay["t"] = self._replay["model"].start
            self._replay["playing"] = True
            self.replay_play.configure(text="Pause", image=self._icon("stop", 12, ON_ACCENT))
            self._replay_tick()

    def _replay_stop(self) -> None:
        self._replay["playing"] = False
        job = self._replay.get("job")
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._replay["job"] = None
        try:
            self.replay_play.configure(text="Lecture", image=self._icon("play", 12, ON_ACCENT))
        except Exception:
            pass

    def _replay_tick(self) -> None:
        self._replay["job"] = None
        if not self._replay["playing"] or self._closing:
            return
        model = self._replay["model"]
        step_ms = 200
        self._replay["t"] = min(model.end, self._replay["t"] + self._replay["speed"] * step_ms / 1000)
        self._replay_draw()
        if self._replay["t"] >= model.end or self._current_page != "analysis":
            self._replay_stop()
            return
        self._replay["job"] = self.root.after(step_ms, self._replay_tick)

    @_guarded
    def replay_jump(self, direction: int) -> None:
        model = self._replay.get("model")
        if model is None:
            return
        t = self._replay["t"] + 6
        m = model.next_marker(t) if direction > 0 else model.prev_marker(t - 1)
        if m is not None:
            self._replay_seek(m.t - 6)

    @_guarded
    def open_report(self, game: dict) -> None:
        """Open the HTML report of a game (generated first when missing)."""
        src = _game_json_path(game)
        html = _game_html_path(game, src)

        def job() -> Path | None:
            if html is not None and html.is_file():
                return html
            if src is None:
                return None
            fn = _report_function("write_report")
            if fn is None:
                return None
            out = fn(src)
            return Path(out) if out else None

        def done(p: Path | None) -> None:
            if p is None or not Path(p).is_file():
                self.show_error("Impossible de générer le rapport de cette partie.")
                return
            webbrowser.open(Path(p).resolve().as_uri())
            self.show_toast("Rapport ouvert dans le navigateur.")

        self.show_toast("Préparation du rapport…")
        self._dispatcher.run(job, done, self.cb(lambda e: self.show_error(f"Rapport impossible : {e}")),
                             name="TreeAI-ui-report")

    @_guarded
    def open_game_folder(self, game: dict) -> None:
        p = _game_json_path(game)
        if p is not None and p.exists():
            open_path(p, select=True)
        else:
            self.open_games_dir()

    @_guarded
    def open_games_dir(self) -> None:
        d = paths.user_data_dir() / "games"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not open_path(d):
            self.show_error("Impossible d'ouvrir le dossier des parties.")
