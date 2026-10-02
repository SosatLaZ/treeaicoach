# TreeAI Coach — architecture & contrats entre modules

> Document de référence **obligatoire** pour toute personne (ou agent) qui code un module.
> Les signatures ci-dessous sont des contrats : ne les changez pas sans mettre à jour ce fichier.
> Langue du code : anglais (identifiants, docstrings). Langue de l'interface et des phrases vocales : **français**.

## Sommaire

* [1. Objectif](#1-objectif)
* [2. Arborescence](#2-arborescence)
* [3. Conventions communes](#3-conventions-communes)
* [4. Contrats par module](#4-contrats-par-module)
* [5. Entraînement (`training/`)](#5-entraînement-training)
* [6. Fonctions d'aide supplémentaires (v1.1)](#6-fonctions-daide-supplémentaires-v11)
* [7. Indicateurs visuels (overlay) — v1.2](#7-indicateurs-visuels-overlay--v12)
* [8. Interface & livraison — v1.3](#8-interface--livraison--v13)
* [9. v3 — chef d'orchestre (combat, phases, positionnement, balises, voix)](#9-v3--chef-dorchestre-combat-phases-positionnement-balises-voix)
* [10. Client LoL (LCU) — vérité terrain d'après-partie (optionnel)](#10-client-lol-lcu--vérité-terrain-daprès-partie-optionnel)
* [11. Détection v4 — a priori gratuits (API officielle + fichiers de config du jeu)](#11-détection-v4--a-priori-gratuits-api-officielle--fichiers-de-config-du-jeu)
* [12. Coups notés (style chess.com) + plans IA v2](#12-coups-notés-style-chesscom--plans-ia-v2)
* [13. Coaching extras (v1.9) — pics de puissance, plan de voie, objectif de partie, cause de mort](#13-coaching-extras-v19--pics-de-puissance-plan-de-voie-objectif-de-partie-cause-de-mort)
* [14. COUPS DE GÉNIE — planificateur macro (`macro.py`, 100 % règles, zéro appel IA)](#14-coups-de-génie--planificateur-macro-macropy-100--règles-zéro-appel-ia)
* [15. Détection v5 — piles d'icônes, mon icône, trajet du jungler, robustesse, coût](#15-détection-v5--piles-dicônes-mon-icône-trajet-du-jungler-robustesse-coût)
* [16. V2 — audit pro des conseils, liste blanche de la voix, cohérence entre systèmes](#16-v2--audit-pro-des-conseils-liste-blanche-de-la-voix-cohérence-entre-systèmes)
* [17. Danger personnel, ganks plus tôt, revue IA ancrée (retour de la 1re vraie partie)](#17-danger-personnel-ganks-plus-tôt-revue-ia-ancrée-retour-de-la-1re-vraie-partie)
* [18. Pipeline v2 — capture, cadence, overlay fluide, diagnostic (systèmes)](#18-pipeline-v2--capture-cadence-overlay-fluide-diagnostic-systèmes)
* [19. Données de jeu vivantes (Data Dragon) + carte d'avant-partie (sélection des champions)](#19-données-de-jeu-vivantes-data-dragon--carte-davant-partie-sélection-des-champions)
* [20. Overlay épuré : une seule chose à la fois + routeur de présentation](#20-overlay-épuré--une-seule-chose-à-la-fois--routeur-de-présentation)
* [21. Auto-diagnostic : TreeAI détecte ses propres problèmes et agit](#21-auto-diagnostic--treeai-détecte-ses-propres-problèmes-et-agit)
* [22. Placement : les zones du jeu + un seul solveur (`layout.py`)](#22-placement--les-zones-du-jeu--un-seul-solveur-layoutpy)

## 1. Objectif

Application Windows (`TreeAICoach.exe`) qui, pendant une partie de League of Legends (Faille de l'invocateur) :

1. capture **uniquement l'écran** (comme OBS/Discord) — la minimap en bas à droite ;
2. détecte les icônes de champions sur la minimap avec un petit réseau de neurones (ONNX) entraîné
   sur des minimaps synthétiques générées à partir des textures officielles du jeu ;
3. identifie chaque icône (quel champion) grâce à la **Live Client Data API** officielle de Riot
   (`https://127.0.0.1:2999/liveclientdata/allgamedata`, fournie par le jeu lui-même) ;
4. suit les positions dans le temps, repère les **ganks** (jungler ennemi / roamer qui s'approche,
   plusieurs ennemis qui convergent, jungler aperçu) et **l'annonce à l'oral** (synthèse vocale Windows, en français).

### Règles de sécurité (non négociables)

* **Aucune** lecture/écriture de la mémoire du jeu, **aucune** injection, **aucun** hook DirectX,
  **aucune** entrée clavier/souris simulée, aucun overlay dessiné dans le jeu.
* Seules sources d'information : capture d'écran (pixels déjà visibles par le joueur) + Live Client Data API officielle.
* Pas de fonctionnalité de dissimulation / contournement d'anti-cheat (pas de renommage de processus, pas d'obfuscation).
* L'application ne doit **jamais** planter : chaque thread attrape ses exceptions, journalise et continue.

## 2. Arborescence

Le paquet est plat (un module par fichier, aucun sous-paquet : PyInstaller embarque tout
`treeaicoach/*.py` via `packaging/treeaicoach.spec`). Regroupement logique :

```
treeaicoach/                    package Python (runtime, embarqué dans le .exe)
  __init__.py  __main__.py  main.py      version, `python -m treeaicoach`, CLI / point d'entrée (.exe)
  paths.py  config.py  logging_setup.py  chemins, réglages (dataclass + JSON), journaux
  fmtutil.py                    petites aides partagées : nombre fini toléré, chrono m:ss, durée parlée

  # --- moteur (threads, cycle de partie, une étape d'analyse) ----------------------------
  engine.py                     CoachEngine : threads, cycle de partie, step(), API publique
  engine_base.py                réglages internes, messages d'état, EngineState / EngineStatus, siège / ace
  engine_capture.py             mixin : fenêtre du jeu, localisation / vérification de la minimap, capture
  engine_vision.py              mixin : mon icône, stabilisation des identités, tracker, menace, danger perso
  engine_coaching.py            mixin : directeur, macro, balises, voix, Tab, objets, IA, coups, ligne HUD
  engine_overlay_state.py       mixin : get_overlay_state() / aperçu / F9 (côté lecture pour l'UI et l'overlay)
  engine_postgame.py            mixin : fin de partie, rapport en arrière-plan, vérité LCU
  engine_selfcheck.py           mixin : auto-diagnostic branché sur le pipeline (mesures, actions, avis)
  selfcheck.py                  auto-diagnostic : règles symptôme -> action -> état, « Santé TreeAI »
  scheduler.py  sysperf.py      cadence adaptative, budget de performance, santé

  # --- perception (écran + API officielle) -----------------------------------------------
  capture.py  dxgi_capture.py  game_settings.py   capture (DXGI / mss), fenêtre, DPI, réglages du jeu
  minimap_locator.py            localisation automatique de la minimap
  detector.py  roster_matcher.py  patch_classifier.py  identifier.py  det_params.py   détection
  self_icon.py  hud_reader.py  camera_proj.py      mon icône (skins perso), HUD du bas, rectangle caméra
  hud_abilities.py              MA barre de sorts (Q W E R, D F, objets, balise) : recharge, prêt, charges
  tracker.py  fog_tracker.py  jungle_intel.py  jungle_path.py   suivi, brouillard, jungler ennemi
  live_client.py  champions.py  game_data.py  meta.py  lcu.py  champ_select.py   données de partie
  render.py  demo.py            rendu de minimaps (entraînement, démo, autotest), partie simulée

  # --- conseils (règles) ------------------------------------------------------------------
  alerts.py  gank.py  danger.py  objectives.py  reminders.py   alertes, ganks, danger perso, objectifs
  roles.py  phase.py  fight.py  positioning.py  wards.py  waves.py  macro.py  tactics.py
  coach.py  coach_plus.py  tips.py  spikes.py  goals.py  game_plan.py  death_cause.py  itemization.py
  scoreboard.py  praise.py  plays.py  hype.py  ai_advisor.py  skill.py
  presenter.py  voice_policy.py   LE routeur de présentation et LA porte de la voix
  coach_sim.py                  simulateur de partie complète (tests, tools/ux_replay.py)

  # --- sortie : voix, overlay, interface ---------------------------------------------------
  voice.py  tts_neural.py  hotkeys.py
  overlay.py  overlay_render.py  toasts.py  fx_overlay.py  fx_render.py  ward_guide.py
  layout.py                     zones de l'UI de LoL + solveur de placement de tout ce qu'on dessine (§22)
  ui.py                         CoachApp : fenêtre, cycle du moteur, rafraîchissement, journal, run_app()
  ui_common.py                  jetons de design, polices, widgets (Toggle, Dropdown, Segmented, bandeau)
  ui_page_dashboard.py  ui_page_alerts.py  ui_page_overlay.py  ui_page_analysis.py  ui_page_settings.py
  ui_dialogs.py                 page Aide, toasts, dialogues, préréglages, diagnostic, raccourcis
  ui_kit.py  ui_preview.py  calibration.py   préréglages / VoiceGate, aperçu de l'overlay, calibration

  # --- après la partie ------------------------------------------------------------------------
  recorder.py  analysis.py  ground_truth.py  report.py  replay.py  progress.py  diag.py  updater.py
  selftest.py                   autotest (--selftest), utilisé par la CI Windows

  assets/                       ressources embarquées
    manifest.json               (généré par tools/fetch_assets.py)
    items.json                  (tools/fetch_items.py, même conversion que game_data.py)
    minimap/2dlevelminimap_<variant>_baron<n>.png, fogofwaroverlay*.png
    icons/champions/<Alias>.png (64x64 RGBA) + index.json
    icons/minimap/*.png, icons/pings/*.png
    model/minimap_detector.onnx + model/model_meta.json   (généré par training/export_onnx.py)
    selftest/*.png + selftest/labels.json                  (généré par training/evaluate.py)
    sounds/*.wav                                           (généré par voice.py au 1er besoin si absent)
training/                       entraînement (PyTorch, hors .exe)
tools/                          fetch_assets / fetch_items / fetch_meta, ux_replay (juge des consignes),
                                latency_bench, det_* (banc de détection), camera_motion_bench
tests/                          pytest (tourne sous Linux ET Windows ; UI sous Xvfb)
packaging/                      PyInstaller (.spec, build_exe.bat, icon.ico)
release/                        l'exe courant + version.json (mise à jour intégrée) + SHA256.txt, rien d'autre
.github/workflows/build-windows.yml
```

Les mixins du moteur et de l'interface ne portent aucun état : tout est créé dans
`CoachEngine.__init__` / `CoachApp.__init__` ; `engine.py` et `ui.py` restent les seuls chemins
d'import (ils ré-exportent les noms publics de `engine_base.py` / `ui_common.py`).

Dépendances runtime : `numpy`, `opencv-python-headless`, `onnxruntime`, `mss`, `pillow`, `pywin32` (Windows seulement).
Tkinter vient avec Python. **Aucune** autre dépendance runtime (pas de `requests` : utiliser `urllib`).
Tout module doit être **importable sous Linux** (les appels Windows sont protégés par `sys.platform == "win32"`
et des imports paresseux) pour que les tests tournent partout.

## 3. Conventions communes

* **Coordonnées minimap normalisées** `(u, v)` ∈ [0, 1] : `u` = gauche→droite, `v` = haut→bas.
  Base bleue (équipe `ORDER`) en bas à gauche (~(0.07, 0.93)), base rouge (`CHAOS`) en haut à droite (~(0.93, 0.07)).
  Rivière ≈ diagonale `u = v` ; voie du milieu ≈ diagonale `u + v = 1`.
  Côté bleu : `v > u` ; moitié haute de la carte : `u + v < 1`.
* Rayon `r` d'une icône = rayon **normalisé par la largeur de la minimap**.
* Images : `numpy.ndarray` `uint8`, ordre **BGR** (convention OpenCV), sauf mention contraire (RGBA pour les icônes).
* Temps : `time.monotonic()` en secondes (`t`), sauf `game_time` (secondes de jeu, API Riot).
* Relation d'un champion au joueur : `"self" | "ally" | "enemy"`. Équipe Riot : `"ORDER" | "CHAOS"`.
* Classes du détecteur (ordre fixe) : `CLASSES = ("enemy", "ally", "self")`.
* Journalisation : `log = logging.getLogger(__name__)` ; jamais de `print` dans le runtime (sauf selftest/CLI).

## 4. Contrats par module

### 4.1 `paths.py`
```python
def package_dir() -> Path            # dossier du package (fonctionne aussi dans le .exe PyInstaller, via sys._MEIPASS)
def asset_path(*parts: str) -> Path  # package_dir()/"assets"/...
def user_data_dir() -> Path          # %APPDATA%/TreeAICoach (Windows) ou ~/.treeaicoach ; créé si absent
def logs_dir() -> Path               # user_data_dir()/"logs"
def cache_dir() -> Path              # user_data_dir()/"cache"  (icônes de skins téléchargées)
def collect_dir() -> Path            # user_data_dir()/"collect" (captures pour ré-entraînement)
def config_path() -> Path            # user_data_dir()/"config.json"
```

### 4.2 `config.py`
```python
@dataclass
class Config:
    # voix
    voice_name: str = ""            # "" = meilleure voix française disponible
    voice_rate: int = 2             # -10..10 (SAPI)
    voice_volume: int = 100         # 0..100
    beep_on_danger: bool = True
    # alertes
    alert_jungler_approach: bool = True
    alert_roam: bool = True
    alert_collapse: bool = True
    alert_jungler_spotted: bool = True
    alert_laner_mia: bool = False
    sensitivity: float = 1.0        # 0.6..1.6, multiplie les rayons
    warn_radius: float = 0.22       # normalisé minimap (~3300 unités de jeu)
    danger_radius: float = 0.12     # (~1800 unités)
    # capture / détection
    target_fps: float = 8.0         # 2..20
    detector_backend: str = "auto"  # "auto" | "onnx" | "classic"
    detection_threshold: float = 0.0  # 0 = valeur de model_meta.json
    minimap_mode: str = "auto"      # "auto" | "manual"
    minimap_side: str = "auto"      # "auto" | "right" | "left"
    manual_minimap_rect: dict | None = None   # {"screen_w","screen_h","x","y","w","h"} en pixels écran
    download_skin_icons: bool = True
    # divers
    autostart: bool = True          # démarre l'analyse au lancement
    collect_samples: bool = False   # enregistre des minimaps pour ré-entraîner
    collect_interval_s: float = 2.0
    def effective_warn_radius(self) -> float      # warn_radius * sensitivity
    def effective_danger_radius(self) -> float    # danger_radius * sensitivity
    def validated(self) -> "Config"               # copie avec valeurs bornées / corrigées

def load_config(path: Path | None = None) -> Config   # jamais d'exception : fichier absent/corrompu -> défauts
                                                      # (le fichier corrompu est renommé .bak), clés inconnues ignorées
def save_config(cfg: Config, path: Path | None = None) -> None  # écriture atomique (tmp + os.replace)
```

### 4.3 `geometry.py`
```python
class Zone(str, Enum):
    BLUE_BASE, RED_BASE, TOP_LANE, MID_LANE, BOT_LANE, TOP_RIVER, BOT_RIVER,
    BLUE_JUNGLE_TOP, BLUE_JUNGLE_BOT, RED_JUNGLE_TOP, RED_JUNGLE_BOT
MAP_GAME_UNITS: float = 14870.0      # largeur de la carte en unités de jeu
def classify_zone(u: float, v: float) -> Zone
def lane_of(zone: Zone) -> str | None           # "top" | "mid" | "bot" | None
def is_base(zone: Zone) -> bool
def side_of(u: float, v: float) -> str          # "top" (u+v<1) | "bot"
def zone_label_fr(zone: Zone, my_team: str | None) -> str
    # ex: "en haut", "au milieu", "en bas", "dans la rivière du haut", "dans la rivière du bas",
    #     "dans ta jungle du haut", "dans la jungle ennemie du bas", "dans ta base", "dans la base ennemie"
    # my_team None -> "dans la jungle bleue du haut" etc.
def dist(a: tuple[float, float], b: tuple[float, float]) -> float
def to_game_units(d: float) -> float
```
Les polylignes des voies (top : bord gauche puis bord haut ; bot : bord bas puis bord droit ; mid : diagonale)
doivent être **mesurées sur la texture** `assets/minimap/2dlevelminimap_base_baron1.png` (couleur kaki des voies).

### 4.4 `live_client.py`
```python
LIVE_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
@dataclass
class PlayerInfo:
    riot_id: str; summoner_name: str
    champion_alias: str        # "MonkeyKing" (depuis rawChampionName "game_character_displayname_MonkeyKing")
    champion_name: str         # nom localisé ("Wukong")
    team: str                  # "ORDER" | "CHAOS"
    position: str              # "TOP" | "JUNGLE" | "MIDDLE" | "BOTTOM" | "UTILITY" | ""
    is_dead: bool; respawn_timer: float; level: int; skin_id: int
    has_smite: bool            # un des sorts a "Smite" dans rawDisplayName/displayName, ou displayName == "Châtiment"
    is_bot: bool
@dataclass
class GameInfo:
    game_time: float; game_mode: str; map_number: int; map_terrain: str   # "Default", "Infernal", ...
    team_relative_colors: bool
    me: PlayerInfo | None      # None en spectateur
    allies: list[PlayerInfo]   # sans moi
    enemies: list[PlayerInfo]
    events: list[dict]
    fetched_at: float          # time.monotonic()
    def enemy_jungler(self) -> PlayerInfo | None   # ennemi avec Châtiment, sinon position == "JUNGLE"
    def player_by_alias(self, alias: str) -> PlayerInfo | None
    def all_players(self) -> list[PlayerInfo]
    @property is_summoners_rift -> bool   # map_number == 11
def parse_allgamedata(data: dict, now: float | None = None) -> GameInfo | None  # pur, sans réseau, jamais d'exception
class LiveClient:
    def __init__(self, url: str = LIVE_URL, timeout: float = 1.0)
    def fetch(self) -> GameInfo | None   # None si pas en jeu / écran de chargement / erreur ; jamais d'exception
```
HTTP : `urllib`, contexte SSL **non vérifié uniquement pour 127.0.0.1**, **sans proxy** (`ProxyHandler({})`).
Correspondance « moi » : `activePlayer.riotId` == `allPlayers[i].riotId`, sinon `summonerName`, sinon `riotIdGameName`.

### 4.5 `champions.py`
```python
@dataclass
class ChampionEntry: alias: str; key: int; name_en: str; name_fr: str; icon_file: str
def alias_from_raw(raw: str) -> str      # "game_character_displayname_MonkeyKing" -> "MonkeyKing" ; alias déjà propre -> inchangé
class ChampionDB:
    def __init__(self, assets_dir: Path | None = None, cache_dir: Path | None = None)
    def get(self, alias_or_raw: str) -> ChampionEntry | None      # insensible à la casse
    def all(self) -> list[ChampionEntry]
    def load_icon(self, alias: str, skin_id: int = 0) -> np.ndarray | None
        # RGBA uint8 ; cherche d'abord cache/<alias>_<skin>.png (skin téléchargé), sinon icône de base embarquée
    def prefetch_skin_icons(self, players: list[PlayerInfo], allow_network: bool = True) -> None
        # thread de fond : télécharge https://raw.communitydragon.org/latest/game/assets/characters/<alias minuscule>/hud/<alias minuscule>_circle_<skin>.png
        # (User-Agent explicite, timeout 5 s, échec silencieux, 404 mémorisé pour ne pas réessayer)
```

### 4.6 `render.py` (partagé : entraînement + démo + selftest)
```python
RING_BGR: dict[str, tuple[int, int, int]]   # couleurs d'anneau par défaut {"enemy":..., "ally":..., "self":...}
RING_FRAC: float                            # épaisseur de l'anneau / rayon
def load_rgba(path) -> np.ndarray
def alpha_blit(dst_bgr, src_rgba, cx, cy, scale=1.0, opacity=1.0) -> None      # centré, clip aux bords
def draw_champion_icon(dst_bgr, cx: float, cy: float, radius_px: float, icon_rgba, ring_bgr, ring_frac=RING_FRAC,
                       grey: bool = False) -> None
@dataclass
class ChampionSprite: u: float; v: float; r: float; relation: str; icon: np.ndarray; ring_bgr: tuple | None = None
                      recall: bool = False; label_class: str | None = None   # None = ne pas étiqueter la classe
@dataclass
class Scene:
    texture: str                              # nom de fichier dans assets/minimap
    size: int                                 # taille de rendu (px)
    fog_alpha: float = 0.55                   # assombrissement brouillard (0 = pas de brouillard)
    vision: list[tuple[float, float, float]]  # cercles de vision (u, v, rayon) non assombris
    champions: list[ChampionSprite]
    minions: list[tuple[float, float, str]]   # (u, v, "ally"|"enemy")
    structures: bool = True                   # tourelles/inhibiteurs/nexus aux positions officielles
    wards: list[tuple[float, float, str]]     # (u, v, nom d'icône)
    pings: list[tuple[float, float, str]]
    camera: tuple[float, float, float, float] | None   # rectangle caméra (u0, v0, u1, v1), blanc
    camps: bool = True
class MinimapRenderer:
    def __init__(self, assets_dir: Path | None = None)
    def textures(self) -> list[str]
    def render(self, scene: Scene) -> np.ndarray             # BGR size x size
STRUCTURES: list[tuple[float, float, str, str]]   # (u, v, kind, team) positions normalisées des tours/inhibs/nexus
CAMPS: list[tuple[float, float, str]]             # positions des camps de la jungle
```

### 4.7 `capture.py`
```python
@dataclass(frozen=True)
class Rect: x: int; y: int; w: int; h: int     # pixels écran (physiques)
def set_dpi_awareness() -> None                 # Windows : SetProcessDpiAwareness(2) / fallback ; ailleurs : no-op
def find_game_window() -> Rect | None           # zone client de "League of Legends (TM) Client" (classe RiotWindowClass) ; None si absente/minimisée
def monitor_rects() -> list[Rect]               # moniteurs physiques (mss)
class ScreenCapture:                            # une instance PAR thread (mss n'est pas thread-safe)
    def __init__(self)
    def grab(self, rect: Rect) -> np.ndarray | None   # BGR ; None si échec ; jamais d'exception
    def close(self) -> None
def is_black_frame(img) -> bool                 # capture noire (plein écran exclusif) -> conseiller "Sans bordure"
```

### 4.8 `minimap_locator.py`
```python
@dataclass
class MinimapLocation: rect: Rect; score: float; method: str   # "auto" | "manual" | "fallback"
class MinimapLocator:
    def __init__(self, assets_dir: Path | None = None)
    def locate(self, screen_bgr: np.ndarray, origin: Rect, side: str = "auto") -> MinimapLocation | None
        # screen_bgr = capture de la fenêtre/écran dont le coin haut-gauche est origin (x, y).
        # Cherche un carré de côté ∈ [0.14, 0.50] * hauteur, collé (marge ≤ 4 % de la hauteur) au coin bas-droit
        # (ou bas-gauche si side == "left" ; "auto" teste les deux), par corrélation normalisée multi-échelle
        # avec les textures de la minimap (floutées, robustes au brouillard et aux icônes).
        # Renvoie None si score < LOCATE_MIN_SCORE. Temps < 400 ms sur une capture 1920x1080.
    def verify(self, minimap_bgr: np.ndarray) -> float   # similarité 0..1 d'un recadrage avec la texture
def fallback_rect(window: Rect, side: str = "right") -> Rect   # heuristique si tout échoue
```

### 4.9 `detector.py`
```python
CLASSES = ("enemy", "ally", "self")
@dataclass
class Detection:
    u: float; v: float; r: float        # centre + rayon normalisés
    score: float                        # confiance « c'est une icône de champion »
    cls: str                            # classe la plus probable
    cls_probs: tuple[float, float, float]
class BaseDetector:
    name: str
    def detect(self, minimap_bgr: np.ndarray) -> list[Detection]
def preprocess(minimap_bgr, input_size) -> np.ndarray      # -> float32 [1,3,S,S] RGB /255 (resize INTER_AREA)
def decode_outputs(heatmap, cls, offset, radius, threshold, stride, input_size, max_det=20) -> list[Detection]
    # heatmap [1,1,H,W] (sigmoïde), cls [1,3,H,W] (softmax), offset [1,2,H,W] (0..1 en cellules), radius [1,1,H,W] (rayon/S)
    # NMS par max-pool 3x3 ; pics > threshold ; u = (x + off_x) * stride / S ; v idem ; r = radius
class OnnxDetector(BaseDetector):
    def __init__(self, model_path: Path | None = None, meta_path: Path | None = None, threshold: float = 0.0, threads: int = 2)
class ClassicDetector(BaseDetector):     # secours sans modèle : cercles de Hough + couleur de l'anneau (rouge/bleu/jaune)
    def __init__(self, r_min: float = 0.025, r_max: float = 0.075)
def create_detector(backend: str = "auto", threshold: float = 0.0) -> BaseDetector   # ONNX sinon classique ; jamais d'exception
```
**Contrat du modèle ONNX** (produit par `training/export_onnx.py`) : entrée `input` float32 `[1,3,S,S]` (RGB, 0..1),
sorties nommées `heatmap` `[1,1,S/4,S/4]` (sigmoïde appliquée), `cls` `[1,3,S/4,S/4]` (softmax appliquée),
`offset` `[1,2,S/4,S/4]` (sigmoïde appliquée), `radius` `[1,1,S/4,S/4]` (rayon/S, ≥ 0).
`model_meta.json` : `{"input_size": 256, "stride": 4, "classes": ["enemy","ally","self"], "threshold": 0.35,
"version": "...", "metrics": {...}}`.

> **Mise à jour (faits réels, voir MINIMAP_FACTS.md)** : l'icône du joueur local a le **même anneau bleu que les alliés**.
> Le détecteur garde 3 sorties de classe pour la compatibilité, mais la classe `self` n'est **pas** apprise visuellement
> (les icônes « self » sont étiquetées `ally` à l'entraînement). `identifier.py` attribue la relation `self` par identité
> (champion + skin du joueur actif) ; secours : icône alliée la plus proche du centre du rectangle caméra.

### 4.10 `identifier.py`
```python
@dataclass
class Identified:
    det: Detection
    alias: str | None          # champion reconnu (None si incertain)
    relation: str              # "self" | "ally" | "enemy"  (déduit de l'identité si connue, sinon de det.cls)
    team: str | None           # "ORDER" | "CHAOS" si connu
    id_score: float
class ChampionIdentifier:
    def __init__(self, db: ChampionDB)
    def set_roster(self, game: GameInfo | None) -> None     # prépare les gabarits des 10 champions (skins) ; idempotent
    def identify(self, minimap_bgr: np.ndarray, detections: list[Detection]) -> list[Identified]
        # compare le disque intérieur (≈ 75 % du rayon, sans l'anneau) à chaque gabarit : NCC niveaux de gris + histogramme HSV ;
        # a priori selon det.cls_probs ; affectation unique par champion (glouton sur le score) ; seuil minimal.
```

### 4.10b Skins personnalisés : `self_icon.py` + `hud_reader.py`
Un mod de skin (côté client) dessine sur la minimap une icône qui n'est AUCUN portrait officiel : le
`RosterMatcher` ne trouve alors pas « moi ». `self_icon.IconLearner` (possédé par le matcher, appelé à
chaque image) : anneaux de la couleur alliée / ennemie / turquoise « moi » non expliqués par un portrait
reconnu (`ring_candidates`, ~3 ms, uniquement quand il en faut), suivis dans le temps ; chaque piste garde
l'INTERSECTION des champions de son camp non reconnus (élimination cohérente) ; pour moi : point de caméra
verrouillée (64 % de la hauteur du rectangle caméra), contour turquoise, continuité → position « bootstrap »
immédiate. Puis capture de l'icône (médiane alignée de ≥ 8 recadrages isolés, cohérence ≥ 0,75) enregistrée
comme gabarit (`RosterMatcher.register_icon`), rafraîchie en EMA, désapprise si elle ne correspond plus
(25 s vivant sans correspondance, ou décentrée 3 fois). Mon icône est gardée dans
`<cache>/learned_icons/<Alias>_<skin>.png` (partie suivante, même champion + skin). Les autres champions :
élimination stricte (déplacement ≥ 0,06, non reconnus depuis 20 s). `SkinGuesser` : le portrait du HUD
(bas-centre, `hud_reader.HudReader`, calibré une fois par taille de fenêtre puis lecture d'un petit patch
< 1 ms, état mort = portrait grisé) est comparé aux icônes officielles des skins (téléchargées à la demande) ;
l'icône apprise reste toujours le recours. `engine.my_observed_lane()` → `RoleResolver.my_lane_hook`.

### 4.11 `tracker.py`
```python
@dataclass
class Track:
    key: str                   # alias ou "enemy?1" (anonyme)
    alias: str | None; relation: str; team: str | None
    first_seen: float; last_seen: float
    visible: bool              # vu il y a < HIDE_AFTER s
    appeared_at: float | None  # instant où il est redevenu visible
    hidden_since: float | None
    def position(self) -> tuple[float, float] | None     # lissée (médiane des 3 dernières)
    def velocity(self) -> tuple[float, float]            # régression linéaire sur ~1.2 s, (0,0) si insuffisant
    def zone(self) -> Zone | None
    def zone_fraction(self, lane: str, window_s: float, now: float) -> float   # part du temps passé dans une voie
class Tracker:
    HIDE_AFTER = 0.6
    def __init__(self)
    def update(self, t: float, identified: list[Identified]) -> None
    def tracks(self) -> list[Track]
    def me(self) -> Track | None
    def enemies(self, visible_only: bool = True) -> list[Track]
    def get(self, key: str) -> Track | None
    def reset(self) -> None
```
Anonymes : association au plus proche (< 0.08). Téléportation/rappel : saut > 0.15 en < 0.3 s → historique remis à zéro.

### 4.12 `alerts.py`
```python
class AlertKind(str, Enum): JUNGLER_APPROACH, ROAM_APPROACH, COLLAPSE, JUNGLER_SPOTTED, LANER_MIA
class Level(IntEnum): INFO = 0, WARNING = 1, DANGER = 2
@dataclass
class Alert: kind: AlertKind; level: Level; text: str; key: str; t: float; alias: str | None = None
def phrase(kind: AlertKind, level: Level, champ: str | None, zone_label: str | None = None, count: int = 0) -> str
    # phrases COURTES (< 2 s à l'oral), ex. : "Attention, Lee Sin approche." / "Gank ! Lee Sin, recule !" /
    # "Danger, 3 ennemis arrivent, recule !" / "Jungler ennemi vu en haut." / "Darius a disparu."
class AlertThrottler:
    def __init__(self, min_gap_s: float = 1.2)
    def filter(self, alerts: list[Alert], t: float) -> list[Alert]
        # cooldown par clé : INFO 30 s, WARNING 8 s, DANGER 6 s ; escalade WARNING -> DANGER immédiate ;
        # un seul message par tick (le plus grave) ; écart global min_gap_s sauf DANGER.
```

### 4.13 `gank.py`
```python
class GankAnalyzer:
    def __init__(self, cfg: Config)
    def apply_config(self, cfg: Config) -> None
    def update(self, t: float, tracker: Tracker, game: GameInfo | None) -> list[Alert]   # alertes brutes (non filtrées)
    def reset(self) -> None
```
Règles (rayons = `cfg.effective_*`) :
* Pas d'alerte si je suis mort, dans ma **fontaine** (un siège de ma base est annoncé), si `game` indique un mode ≠ Faille, ou si ma position est inconnue depuis > 3 s.
* Jungler qui vient clairement sur moi : WARNING dès `JUNGLER_EARLY_FACTOR` (1,25) × le rayon d'alerte (~2 s plus tôt, ETA ~9,5 s).
* Roam (laner non-jungler) : « Roam ! Ekko, recule ! » / « Roam : Ekko arrive ... ! » seulement s'il vient sur moi (approche,
  sortie du brouillard tout près, ou très proche) ; jamais quand il farme SA voie ; pas répété avant 25 s (`ROAM_REPEAT_S`).
* **JUNGLER_APPROACH** : jungler ennemi visible à `d < warn` ET (se rapproche : vitesse radiale < −0.006/s OU vient d'apparaître < 1 s) → WARNING ; `d < danger` → DANGER.
* **ROAM_APPROACH** : idem pour un ennemi non-jungler qui n'est **pas** mon adversaire de voie
  (adversaire de voie = même `position` Riot, ou ≥ 50 % de son temps visible des 90 dernières s dans ma voie ; BOTTOM et UTILITY sont regroupés).
* **COLLAPSE** : ≥ 2 ennemis à `d < warn` dont au moins un non-adversaire de voie, et au moins un qui se rapproche → DANGER.
* **JUNGLER_SPOTTED** : le jungler ennemi réapparaît après ≥ 25 s caché (ou 1re apparition après 1:30 de jeu), `d ≥ warn` → INFO avec la zone.
* **LANER_MIA** (option) : adversaire de voie caché ≥ 6 s alors que je suis en voie, après 3:00 → INFO, une fois par disparition.

### 4.14 `voice.py`
```python
class VoiceEngine:
    def __init__(self, voice_name: str = "", rate: int = 2, volume: int = 100, beep_on_danger: bool = True)
    backend: str                           # "sapi" | "print"
    def start(self) -> None; def stop(self) -> None
    def say(self, text: str, level: int = 1) -> None   # non bloquant ; DANGER coupe le message en cours ; messages > 2.5 s en file = jetés
    def list_voices(self) -> list[str]
    def set_params(self, voice_name=None, rate=None, volume=None, beep_on_danger=None) -> None
```
SAPI via `win32com.client.Dispatch("SAPI.SpVoice")` dans un thread dédié (`pythoncom.CoInitialize()`), voix française préférée
(langue 40C / nom contenant "French"/"Français"/"Hortense"/"Julie"/"Paul"). Hors Windows ou si SAPI échoue : backend "print" (journalise).

### 4.15 `engine.py`
```python
class EngineState(str, Enum): STOPPED, WAITING_GAME, LOCATING, RUNNING, UNSUPPORTED_MODE, CAPTURE_BLACK, ERROR
@dataclass
class EngineStatus:
    state: EngineState; message: str (FR); fps: float; game_time: float | None
    minimap_rect: Rect | None; enemies_visible: int; last_alert: str | None; detector: str; voice: str
class FrameSource(Protocol):            # pour la démo et les tests : remplace capture + API
    def next(self, t: float) -> tuple[np.ndarray | None, GameInfo | None]   # (minimap BGR, infos de jeu)
class CoachEngine:
    def __init__(self, cfg: Config, voice: VoiceEngine, detector: BaseDetector | None = None,
                 live_client: LiveClient | None = None, frame_source: FrameSource | None = None,
                 clock: Callable[[], float] = time.monotonic)
    def start(self) -> None; def stop(self, timeout: float = 3.0) -> None; def is_running(self) -> bool
    def get_status(self) -> EngineStatus
    def get_preview(self) -> np.ndarray | None        # minimap annotée (BGR) pour l'aperçu
    def request_relocate(self) -> None
    def apply_config(self, cfg: Config) -> None
    def step(self, t: float) -> list[Alert]           # 1 itération synchrone (utilisée par les tests / la démo accélérée)
```
Threads : poller Live Client (1 Hz, 0.5 Hz hors partie), boucle d'analyse à `target_fps`. Toute exception est attrapée,
journalisée (limitée en fréquence) et la boucle continue. Hors partie : aucune capture (CPU ≈ 0).

Découpage (aucun changement de comportement) : `CoachEngine(PostgameMixin, CoachingMixin, VisionMixin,
CaptureMixin, OverlayStateMixin)`. `engine.py` garde le constructeur, les threads, le cycle de partie,
`step()` / `_step()`, la santé et le diagnostic ; chaque `engine_*.py` regroupe une étape (voir §2).
Les mixins utilisent le même journal (`treeaicoach.engine`) et les mêmes attributs privés qu'avant
(les tests y accèdent : ne pas les renommer). Réglages internes et messages : `engine_base.py`.

### 4.16 `demo.py`
```python
class DemoSource(FrameSource):
    def __init__(self, renderer: MinimapRenderer | None = None, db: ChampionDB | None = None, size: int = 280, seed: int = 0)
    SCENARIO_LENGTH: float                 # ~75 s puis boucle
    GANK_WINDOW: tuple[float, float]       # intervalle (s du scénario) où une alerte DANGER de gank doit sortir
    def next(self, t: float) -> tuple[np.ndarray, GameInfo]
```
Scénario : je suis toplaner allié ; adversaire de voie en top ; le jungler ennemi (Châtiment) apparaît dans sa jungle,
remonte par la rivière du haut et gank vers ~40 s ; pas d'alerte de gank avant qu'il n'approche.

### 4.17 `selftest.py`, `main.py`, `ui.py`, `calibration.py`
* `run_selftest(out_path: Path | None = None, voice: bool = False) -> int` : 0 si OK. Vérifie ressources, config, modèle ONNX
  (précision/rappel sur `assets/selftest`), détecteur classique, localisation de la minimap sur une capture synthétique,
  parsing Live Client, scénario démo accéléré (alerte DANGER dans `GANK_WINDOW`, aucune alerte de gank avant).
* `main.py` : `--selftest [--selftest-out FICHIER]`, `--demo`, `--nogui`, `--debug`, `--config FICHIER`.
  `.exe` fenêtré : `sys.stdout` peut être `None` → ne jamais `print` sans garde. Instance unique (mutex nommé Windows).
* `ui.py` : fenêtre Tkinter FR (statut, démarrer/arrêter, tester la voix, démo, calibrer, aperçu, réglages).
* `calibration.py` : capture l'écran, l'utilisateur trace un carré sur la minimap → `cfg.manual_minimap_rect`.

## 5. Entraînement (`training/`)
* `synth.py` : `generate_sample(rng, size=256) -> (img_bgr, labels)` avec
  `labels = [{"u","v","r","cls": "enemy"|"ally"|"self", "cls_valid": bool}]`. Utilise `treeaicoach.render`.
  Diversité : textures (toutes variantes), brouillard + cercles de vision, 0–10 champions (dont chevauchements et bords),
  skins (training/cache), sbires, tours, balises, pings, rectangle caméra, icônes de rappel, taille de minimap simulée
  (180–420 px puis remise à l'échelle), flou, JPEG, bruit, gamma/luminosité ; 10 % d'anneaux de teinte aléatoire (`cls_valid=False`).
* `model.py` : `MinimapNet` (CenterNet léger, stride 4, ~0.3–1 M paramètres, opérateurs ONNX simples).
* `train.py`, `export_onnx.py`, `evaluate.py` (métriques : précision/rappel à distance < 0.5 rayon, exactitude de classe).

## 6. Fonctions d'aide supplémentaires (v1.1)

Toutes restent dans le cadre « écran + API officielle » : **aucun** suivi des sorts/ultimes ennemis (interdit par Riot
depuis mars 2025), aucune prédiction de position dans le brouillard (seulement « dernière position vue »).

### 6.1 `alerts.py` — nouveaux types
`AlertKind` gagne : `OBJECTIVE_SOON`, `RECALL_GOLD`, `CONTROL_WARD`, `JUNGLER_WHERE`, `DEATH_RECAP`.
Tous de niveau `INFO` (sauf mention). Les messages `JUNGLER_WHERE` (réponse à la touche) et `DEATH_RECAP`
ne sont jamais filtrés par l'écart global du throttler (mais gardent leur cooldown par clé, 3 s pour JUNGLER_WHERE).

### 6.2 `objectives.py`
```python
@dataclass
class ObjectiveState: name: str (FR: "Dragon", "Baron", "Héraut", "Larves", "Dragon ancestral")
                      next_spawn: float | None (game_time) ; alive: bool ; source: "schedule" | "event"
class ObjectiveTimers:
    def __init__(self, cfg: Config, schedule: dict | None = None)   # schedule par défaut = OBJECTIVE_SCHEDULE (modifiable)
    def update(self, game: GameInfo | None, t: float) -> list[Alert]   # annonce à 60 s et 20 s avant l'apparition (configurable)
    def states(self) -> list[ObjectiveState]
    def reset(self) -> None
OBJECTIVE_SCHEDULE = {  # saison 2026 (patch 26.1+), surchargeables dans assets/objectives.json
  "dragon":  {"first": 300, "respawn": 300, "soul": 4},  "elder": {"respawn": 360},
  "grubs":   {"first": 480, "respawn": None, "despawn": 885, "count": 3}, "herald": {"first": 900, "despawn": 1185},
  "baron": {"first": 1200, "respawn": 360}      # Atakhan supprimé en 26.1 (clé "atakhan" ignorée)
}
```
Événements Live Client utilisés : `DragonKill` (`DragonType` = "Elder" → elder), `BaronKill`, `HeraldKill`, `HordeKill`
(larves), `GameStart` (`AtakhanKill` est ignoré : Atakhan n'existe plus depuis 26.1). Aucune annonce si le mode n'est pas la Faille, ou si l'option est désactivée.
Les réapparitions après un kill (dragon 5:00, baron 6:00, ancestral 6:00) sont fiables ; les apparitions initiales viennent
du tableau (qui peut changer selon les patchs → fichier JSON modifiable).

### 6.3 `reminders.py`
```python
class PersonalReminders:
    def __init__(self, cfg: Config)
    def update(self, t: float, game: GameInfo | None, me_pos: tuple[float, float] | None, in_base: bool) -> list[Alert]
```
Uniquement **mes propres** données (API officielle) :
* `RECALL_GOLD` : or courant ≥ `cfg.recall_gold_threshold` (défaut 1300) alors que je ne suis pas en base,
  rappel au plus toutes les 90 s, pas si un ennemi est proche (géré par l'engine : ne pas parler par-dessus un gank).
* `CONTROL_WARD` : je suis en base, pas de balise de contrôle (itemID 2055) dans l'inventaire, or ≥ 75, après 3:00 ; 1 fois par passage en base.
`GameInfo` doit exposer pour moi : `current_gold` (activePlayer.currentGold), `items` (liste d'itemID), `scores`
(`kills`, `deaths`, `assists`, `creepScore`, `wardScore`) → ajouter ces champs à `PlayerInfo`/`GameInfo` (valeurs par défaut sûres).

### 6.4 `hotkeys.py`
```python
class HotkeyListener:
    def __init__(self, bindings: dict[str, Callable[[], None]])   # ex. {"F9": callback}
    def start(self) -> None; def stop(self) -> None                # Windows : RegisterHotKey + boucle GetMessage dans un thread
    ok: bool                                                       # False si enregistrement impossible (touche prise) ; ailleurs no-op
```
N'utilise **que** `RegisterHotKey` (API standard utilisée par Discord/OBS) : pas de hook clavier bas niveau, pas d'envoi de touches.

### 6.5 `recorder.py` + `analysis.py` + `report.py` (analyse d'après-partie)
```python
class GameRecorder:
    def __init__(self, out_dir: Path | None = None)     # défaut user_data_dir()/"games"
    def on_game_info(self, game: GameInfo, t: float) -> None     # instantanés (scores/or/objets/niveau) toutes les 10 s, événements dédupliqués par EventID
    def on_tracks(self, tracker: Tracker, t: float, game_time: float | None) -> None   # ma position 1 Hz ; apparitions ennemies (≤ 2 Hz / champion)
    def on_alert(self, alert: Alert, game_time: float | None) -> None
    def finish(self) -> Path | None     # écrit games/<AAAA-MM-JJ_HHMM>_<champion>.json ; appelé à la fin de partie ; idempotent
    def autosave(self) -> None          # toutes les 60 s (fichier .partial.json) ; jamais d'exception
    active: bool
def analyze_game(record: dict) -> dict      # pur ; voir ci-dessous
def render_report_html(record: dict, analysis: dict) -> str   # HTML autonome FR (CSS inline, images PNG base64)
def write_report(record_path: Path) -> Path | None            # lit le JSON, écrit <même nom>.html, renvoie son chemin
def list_games(limit: int = 50) -> list[dict]                 # résumé des parties enregistrées (pour l'onglet Historique)
```
`analyze_game` produit : résumé (champion, durée, K/D/A, CS/min, vision/min, niveau final), **morts** (heure de jeu, zone,
ennemis vus à < 0.2 dans les 8 s avant, jungler impliqué ?, alerte (gank ou danger perso) dans les 12 s avant ?,
**avance de l'alerte** `alert_lead_s` = 1re alerte de la chaîne ; `verdict_key` : `ignored` « alerte ignorée » (≥ 3 s) /
`late` « alerte trop tardive » (< 3 s, faute de l'app) / `duel` « 1v1 perdu » (seulement mon adversaire de voie) /
`missed` « l'app n'a pas prévenu » (ennemis visibles près de moi) / `unseen` « ennemis invisibles » / `tower` ;
`death_verdicts`, `alert_lead` ; `deaths_warned` ne compte que les alertes données à temps), **ganks subis** (alertes DANGER + issue : mort / survie), **jungler ennemi** (1re apparition, répartition
des apparitions par zone et par phase 0–10 / 10–20 / 20+ min, voies gankées d'après les kills où il participe),
**temps par zone** pour moi, **objectifs** (kills par équipe), et une liste de **conseils** en français générés par règles
(ex. « 3 morts dans les 10 s après une alerte : recule dès l'annonce », « CS/min 5,8 : objectif 7+ »,
« le jungler ennemi a ganké 4 fois en bas : balise la rivière du bas vers 3:00 »). Le rapport contient une carte
(texture minimap) avec ma heatmap de position, mes morts (croix rouges), les apparitions du jungler ennemi (points colorés par minute).

### 6.6 Récap de mort (`analysis.death_recap(record_so_far, death_event) -> str | None`)
Phrase courte prononcée ~2 s après ma mort (alerte `DEATH_RECAP`), ex. : « Mort face à 2 ennemis, dont le jungler.
L'alerte avait été donnée 5 secondes avant. » ou « Mort sans alerte : personne n'était visible sur la minimap. »

### 6.7 Config — nouveaux champs
`objective_timers: bool = True`, `objective_lead_s: list[int] = [60, 20]`, `recall_reminder: bool = True`,
`recall_gold_threshold: int = 1300`, `control_ward_reminder: bool = True`, `hotkey_jungler: str = "F9"` ("" = désactivé),
`death_recap: bool = True`, `post_game_report: bool = True`, `open_report_automatically: bool = True`.

## 7. Indicateurs visuels (overlay) — v1.2

### 7.0 Principe et sécurité
* Fenêtres Windows **séparées** (processus TreeAICoach), transparentes, toujours au premier plan, **traversées par la souris**
  (`WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST`), affichées avec
  `UpdateLayeredWindow` (alpha par pixel). Aucune injection, aucun hook DirectX : fonctionne en mode d'affichage **Sans bordure / Fenêtré**.
* On ne dessine **jamais par-dessus la zone de la minimap capturée** (sinon on capturerait nos propres dessins et le détecteur
  serait pollué) et on n'utilise **pas** `SetWindowDisplayAffinity`. Le « radar » est donc une copie agrandie de la minimap,
  placée **à côté** (par défaut juste au-dessus) de la vraie minimap.
* Tout le rendu est fait en numpy/PIL dans `overlay_render.py` (pur, testable sous Linux) ; `overlay.py` ne fait que
  l'affichage Win32 (ctypes). Si quoi que ce soit échoue → overlay désactivé proprement, l'app continue (voix seule).

### 7.1 `fog_tracker.py` — cercle de position possible (« zone où il peut être »)
```python
WALK_GRID = 128                      # grille de calcul (cellules)
def walkable_mask(texture_rgba: np.ndarray, grid: int = WALK_GRID) -> np.ndarray   # bool [grid,grid] (alpha > 0 = zone praticable)
class Reachability:
    def __init__(self, walkable: np.ndarray)
    def distance_field(self, start_uv: tuple[float, float]) -> np.ndarray   # distance géodésique normalisée (float32, inf = inatteignable)
        # propagation par dilatations successives contraintes au masque (alternance noyau croix / carré ≈ octogonal), < 10 ms
@dataclass
class FogEstimate:
    key: str; alias: str | None; name: str | None
    last_uv: tuple[float, float]; last_seen: float (t) ; elapsed: float
    speed: float                      # vitesse retenue (normalisée / s)
    radius: float                     # speed * elapsed (+ marge), cercle « à vol d'oiseau »
    region: np.ndarray | None         # masque bool [grid,grid] des cellules atteignables (géodésique + marge flash 0.027)
    confidence: float                 # 1 → 0 quand la zone devient trop grande (elapsed → FOG_MAX_S)
class FogTracker:
    FOG_MAX_S = 60.0                  # au-delà : zone trop grande, affichage estompé puis retiré
    def __init__(self, texture_rgba: np.ndarray | None = None)
    def update(self, t: float, tracker: Tracker, game: GameInfo | None, mode: str = "jungler") -> list[FogEstimate]
        # mode "jungler" | "all" | "off". Démarre une estimation quand un ennemi suivi devient invisible, la supprime dès qu'il
        # réapparaît. Vitesse = vitesse observée juste avant la disparition, bornée à [0.85, 1.35] × vitesse nominale
        # (nominale : 345 u/s avant 2:30, 390 u/s après ; convertie en normalisé / MAP_GAME_UNITS).
```

### 7.2 `overlay_render.py` (pur : numpy + PIL, polices Segoe UI → DejaVu → police PIL par défaut ; accents FR corrects)
```python
@dataclass
class EnemyView: key: str; alias: str | None; name: str; visible: bool; uv: tuple[float,float] | None
                 last_seen_ago: float | None; is_jungler: bool; approaching: bool; icon: np.ndarray | None  # RGBA
@dataclass
class OverlayState:
    minimap_rect: Rect | None; screen_rect: Rect | None
    me_uv: tuple[float, float] | None; my_team: str | None
    enemies: list[EnemyView]; fogs: list[FogEstimate]
    threat_level: int                 # 0 sûr, 1 attention, 2 danger
    threat_text: str                  # ex. "SÛR", "ATTENTION — Lee Sin approche", "DANGER — GANK !"
    last_alert: tuple[str, int, float] | None   # (texte, niveau, âge en s)
    objectives: list[ObjectiveState]; game_time: float | None
    warn_radius: float; danger_radius: float
    flash: float                      # 0..1 intensité du flash de bord d'écran (DANGER)
    jungler_line: str | None          # ex. "Jungler : Lee Sin — vu il y a 23 s, rivière du haut"
    hint: str | None                  # ex. "1 450 PO — pense à rentrer"
def render_radar(state: OverlayState, size: int, texture_bgr: np.ndarray) -> np.ndarray   # BGRA premultiplié size x size
    # texture minimap assombrie ; cercles warn (jaune) / danger (rouge) autour de moi ; mon icône ;
    # ennemis visibles (anneau rouge, jungler avec halo pulsé), flèche de vitesse si il se rapproche ;
    # zones de brouillard : région atteignable remplie rouge translucide + contour + cercle + minuteur "23 s" au dernier point vu ;
    # icônes grisées + "?" + secondes aux dernières positions des ennemis invisibles (< 60 s).
def render_hud(state: OverlayState, width: int = 340) -> np.ndarray     # BGRA premultiplié
    # jauge de menace (vert/orange/rouge), ligne jungler, rangée des 5 ennemis (icône + "vu" ou "MIA 23 s"),
    # objectifs (Dragon 1:24, Baron 4:10...), dernière alerte (s'estompe en 4 s), astuce (or).
def render_flash(w: int, h: int, intensity: float, exclude: Rect | None, thickness: int = 10) -> np.ndarray
    # cadre rouge sur les bords de l'écran, sans jamais recouvrir `exclude` (rect de la minimap, relatif à l'écran)
def to_premultiplied_bgra(rgba: np.ndarray) -> np.ndarray
```

### 7.3 `overlay.py` (Windows, ctypes)
```python
class LayeredWindow:     # une fenêtre popup layered ; méthodes appelées depuis le thread de l'overlay uniquement
    def __init__(self, name: str, click_through: bool = True)
    def update(self, bgra_premul: np.ndarray, x: int, y: int) -> None   # UpdateLayeredWindow (DIB 32 bits)
    def hide(self) -> None; def show(self) -> None; def destroy(self) -> None
    def set_click_through(self, on: bool) -> None
class OverlayManager:
    def __init__(self, cfg: Config, state_provider: Callable[[], OverlayState | None])
    def start(self) -> None; def stop(self) -> None; def apply_config(self, cfg: Config) -> None
    ok: bool                 # False si non supporté (hors Windows, erreur) → aucune exception
```
Thread dédié avec boucle de messages (`PeekMessageW`), rafraîchissement ~12 Hz, fenêtres cachées hors partie ou si l'état est None.
Placement : radar `cfg.radar_position` ∈ {"above_minimap" (défaut), "left_of_minimap", "top_left", "custom"} avec
`cfg.radar_scale` (défaut 1.0 = même taille que la minimap, max 2.0) ; HUD `cfg.hud_position` ∈ {"top_left" (défaut),
"top_right", "left_middle", "custom"} ; positions « custom » en pixels écran (`cfg.radar_xy`, `cfg.hud_xy`).
Mode « déplacer » (depuis l'UI) : fenêtres non traversées par la souris, déplaçables à la souris, position sauvegardée.

### 7.4 Config — nouveaux champs
`overlay_enabled: bool = True`, `radar_enabled: bool = True`, `radar_position: str = "above_minimap"`, `radar_scale: float = 1.0`,
`radar_xy: list[int] | None = None`, `hud_enabled: bool = True`, `hud_position: str = "top_left"`, `hud_xy: list[int] | None = None`,
`danger_flash: bool = True`, `fog_mode: str = "jungler"` ("jungler" | "all" | "off"), `fog_max_s: float = 60.0`,
`hotkey_mute: str = "F10"`, `hotkey_overlay: str = "F11"`, `break_reminder: bool = True`.

### 7.5 Engine — ajouts
`CoachEngine.get_overlay_state() -> OverlayState | None` (instantané thread-safe, None hors partie) ;
`CoachEngine.jungler_status_text() -> str` (réponse à F9, depuis FogTracker/Tracker) ; `mute(bool)`, `toggle_overlay()`.
Menace courante = niveau max des alertes brutes du GankAnalyzer du dernier tick (même filtrées par le throttler), maintenu 2 s.

### 7.6 Session (bien-être)
`break_reminder` : à la fin d'une partie perdue (événement `GameEnd` `Result == "Lose"`), si 3 défaites d'affilée dans la session
→ message vocal et bandeau UI « 3 défaites d'affilée : une pause de 10 minutes aide à rester concentré. »

## 8. Interface & livraison — v1.3

### 8.1 Un seul exécutable
* **`TreeAICoach.exe`** unique (PyInstaller *one-file*, fenêtré, icône de l'app, métadonnées de version Windows :
  ProductName « TreeAI Coach », FileDescription « TreeAI Coach — coach vocal anti-gank pour League of Legends »).
* Tout est dedans (modèle ONNX, textures, icônes, polices éventuelles). Les données utilisateur vont dans `%APPDATA%\TreeAICoach`.
* Double-clic → l'interface s'ouvre, l'analyse démarre (autostart) et attend une partie. Rien à installer.
* CI GitHub Actions (windows-latest) : tests → build → autotest de l'exe → artefact + **release** GitHub avec l'exe.

### 8.2 `ui.py` — interface CustomTkinter (direction visuelle : `docs/DESIGN.md`)
> Mise à jour v1.9 : l'ancien thème « hextech » (or / marine du client du jeu) est remplacé par la direction
> « régie esport » de **`docs/DESIGN.md`** (graphite vert-noir, un seul accent vert sève `#9BD84A`, rouge / ambre
> pour le sens, séparateurs 1 px, rayon 4 px, Bahnschrift pour titres et chiffres, Segoe UI pour le texte).
> `tests/test_design_rules.py` vérifie les interdits (tiret cadratin, couleurs / polices bannies, emoji).
Structure (v2.3, « tri pratique ») : **barre latérale** (logo ; 4 pages ; accès rapide Voix / Overlay / Mode sûr ;
**Ton niveau** toujours visible ; état de l'analyse, cliquable ; bouton « Nouvelle version » quand une mise à jour
existe) + 4 pages (`ui_common.PAGES`, Ctrl+1 … 4 ; l'application s'ouvre toujours sur « En jeu ») :
1. **En jeu** : bandeau d'état = **une** ligne d'état (`ui_kit.status_line` : « En attente d'une partie », « En jeu :
   Garen top », « Capture noire »… + un message utile + **un bouton de correction** : Calibrer / Aide / Diagnostic),
   face-à-face (en partie seulement), jauge de menace, minuteurs d'objectifs, chrono, Démarrer/Arrêter ; en-tête :
   Tester la voix, Tester l'overlay (états d'exemple 10 s), Mode démo. **Avant la partie** : carte de la sélection
   des champions (`champ_select.pregame_card`, sondée aussi depuis les autres pages, toast une fois), dernière
   partie (résultat, K/D/A, précision, meilleur coup / pire erreur, boutons Rapport / Replay / Progrès), objectif
   de la partie (`goals.pick_goal`), point à travailler (`progress.focus_points`), session ; sans aucune partie :
   3 vérifications (Sans bordure lu dans les réglages du jeu, overlay, voix). **En partie** seulement : ligne coach,
   ennemis + alliés, journal, radar. Colonne de droite : panneau **Système** (Jeu / Minimap / Client LoL /
   Détection / IA conseil / Voix, correction en un clic, `ui_kit.subsystem_rows` ; une ligne ajoutée à
   `subsystem_rows` apparaît toute seule, `_add_sys_row`), ligne de santé (`health_text`, sinon le CPU de
   l'application), « Diagnostic complet » avec sa touche en jeu (`hotkey_diag`). Après une partie : toast « Partie
   enregistrée » quand le nouveau fichier apparaît.
2. **Analyses** (onglets) : **Parties** (bandeau de session + tableau avec colonne **Précision** des coups notés,
   lue à la fin du fichier de la partie par `report.read_plays_brief`, en cache), **Progrès** (`progress.py` : courbes des
   20 dernières parties, CS/min, morts, or à 10/15 min contre l'adversaire et fiabilité TreeAI quand le client LoL
   est disponible, « tes 3 points à travailler »), **Replay** (`replay.py` : minimap minute par minute, dernières
   positions connues, cercle du jungler, frise des morts / ganks / kills, lecture x10 à x120). Les onglets chargent
   leur contenu à la sélection, cliquée ou programmée (`_tabs(on_select=...)`).
3. **Réglages** : **une** page, onglets par intention (`ui_common.SETTINGS_TABS`) : Général (démarrage, après la
   partie, fenêtre) · **Affichage** (ce que tu vois en jeu : aperçu `ui_preview.py` en tête avec Tester / Déplacer,
   overlay, minimap, panneau, écran, radar en mode radar seulement ; `ui_page_overlay.py`) · **Voix** (ce que tu
   entends : quantité `voice_level`, « Annonce d'un danger » = `beep_on_danger` + `danger_voice`, alertes de gank,
   rappels, moteur de voix ; `ui_page_alerts.py`) · Détection · IA · Mises à jour (progression, erreur + lien
   direct) · Avancé (touches en jeu, performance, maintenance). Anciennes clés de page : `PAGE_ALIASES`
   (« alerts » → Réglages > Voix, « overlay » → Réglages > Affichage). Chaque champ de `Config` a un contrôle ou
   figure dans `ui_common.HIDDEN_SETTINGS` avec sa raison (vérifié par `tests/test_ui_settings.py`). Réglages
   supprimés en 2.3 (rien ne les lisait) : `config.REMOVED_KEYS`, ignorés en silence au chargement ;
   `layer_roles` / `layer_ghosts` fusionnés dans `overlay_show_roles` / `overlay_show_ghosts` (`MERGED_KEYS`).
4. **Aide** : mode d'emploi en 5 étapes (mode Sans bordure, lancer l'app, jouer…), sécurité / règles Riot, touches en
   jeu avec leur réglage **actuel** (`ui_kit.game_keys`) et raccourcis de la fenêtre, dépannage.
**Mode guidé** (premier lancement, relançable dans Réglages > Général et dans l'Aide) : 3 étapes courtes (ton niveau,
jeu en Sans bordure vérifié dans les fichiers de réglages du jeu, test de l'overlay et de la voix).
Polices : `ui.pick_font` (insensible à la casse) ; titres / chiffres « Bahnschrift SemiBold » (instance nommée listée par
Tk sous Windows 10+), sinon Segoe UI Semibold, sinon une sans-serif condensée, sinon la police du corps.
Règles : toutes les mises à jour de widgets passent par `root.after` (jamais depuis un autre thread) ; toute action utilisateur
est protégée par try/except + message d'erreur FR (jamais de crash) ; fermeture propre (arrêt engine/voix/overlay, sauvegarde config) ;
fenêtre redimensionnable, taille min 980×640, se souvient de sa position ; icône de fenêtre = icône de l'app.
Découpage : `CoachApp(DashboardPageMixin, AlertsPageMixin, OverlayPageMixin, AnalysisPageMixin, SettingsPageMixin,
DialogsMixin)`, un module `ui_page_*.py` par page ou onglet de Réglages (alerts = onglet Voix, overlay = onglet
Affichage) + `ui_dialogs.py` ;
jetons, polices et widgets dans `ui_common.py` (ré-exportés par `ui.py`). Restent dans `ui.py` : les pages
paresseuses (`PAGE_METHODS`, `_page_attr_index` lit le bytecode des constructeurs de page, hérités compris),
`PREBUILD_DELAY_MS` et `_report_function` (les tests les remplacent sur `ui` : les pages appellent
`ui._report_function` au moment de l'appel). `tests/test_design_rules.py` contrôle tous les fichiers `ui*.py`.

## 9. v3 — chef d'orchestre (combat, phases, positionnement, balises, voix)

* `meta.py` + `assets/champion_meta.json` (généré par `tools/fetch_meta.py` : Meraki Analytics + Data Dragon, gratuits) :
  `profile(alias) -> ChampMeta` (dégâts P/M/X, portée, classes, notes, style engage/pick/poke/burst/dive/sustain/peel/splitpush,
  courbe early/mid/late).
* `fight.py` : `FightTracker.update(t, game, me_uv, allies, enemies, ...) -> FightUpdate` ; un combat exige un allié identifié
  à côté de moi (seul = GANK) ; calculateur `evaluate()` (niveau, or des objets, courbe, PV, arrivées possibles en 5 s, morts)
  -> `win` 0..1 ; décision FIGHT (>= 60 %) / RECULE (<= 40 % ou PV < 25 %) avec hystérésis et 4 s entre deux bascules.
* `phase.py` : `map_state(game) -> MapState` (phase laning/mid/late/end, tours / inhibs tombés, dragons / âme, buffs Baron /
  ancestral, morts + timers) ; `EndGameCaller` (ace -> Baron / finir, carrys morts, ancestral, Baron ennemi, âme, inhibs).
* `positioning.py` : `PositionCoach` (« Va en bas : dragon dans 60 s », retour en voie, seul en side en fin de partie,
  regroupement) + score par phase ; `analysis._positioning` / section « Positionnement » du rapport.
* `wards.py` : table des emplacements de balises (coordonnées minimap, côté bleu, miroir pour le rouge, vérifiées sur la
  texture) ; `recommend()` (1-3 spots) ; `WardAdvisor` (après un retour, toutes les ~2,5 min, avant un objectif).
* `voice_policy.VoiceGate` : LA porte unique de la voix (visuel d'abord) : **liste blanche V2** (§16) : gank réel,
  RECULE (`call:retreat`, seule voix pendant un combat), F9, objectif ≤ 20 s si je suis concerné ; « normal » + l'appel
  chiffré d'après-combat ; tout le reste est écrit ; règles de concentration (combat, PV bas, ennemis sur moi) ;
  `SpeechBudget` (1 message / 20 s, 3 / min, file à expiration) ; `triage_gank` (groupé, derrière mes alliés, opportunité ;
  un gank DANGER sur moi hors combat n'est jamais retardé).
* `tactics.TacticalDirector` : relie le tout pour `engine.py` (`_tactics_tick`, `_speech_budget`) ; guides minimap
  (`MapGuide` : flèches repli / objectif / regroupement, spots de balise, max 6 éléments) dans `OverlayState.guides` et
  grande bannière (`Banner` -> `toasts.render_banner` : « FIGHT 72 % » vert / « RECULE 28 % » rouge, en direct pendant le combat).

## 10. Client LoL (LCU) — vérité terrain d'après-partie (optionnel)

* **Source** : API locale officielle du client League of Legends (`LeagueClientUx.exe`, `https://127.0.0.1:<port>`,
  auth basique `riot:<mot de passe>`), autorisée par Riot pour les applications tierces. **Lecture seule** (GET),
  **uniquement après la partie**, jamais utilisée pour une décision en jeu. Désactivable (`Config.lcu_enabled`, défaut True).
* `lcu.py` : `discover()` (lockfile du dossier d'installation : `TREEAI_LCU_DIR`, `RiotClientInstalls.json`, registre,
  chemins par défaut ; sinon ligne de commande du processus via `wmic` puis PowerShell `Get-CimInstance`,
  `CREATE_NO_WINDOW`), `LcuClient` (urllib, sans proxy, certificat non vérifié **seulement** pour 127.0.0.1, jamais
  d'exception ; désactivé hors Windows), `fetch_postgame_truth(record)` : dernière partie
  (`/lol-match-history/v1/products/lol/current-summoner/matches?begIndex=0&endIndex=1`), contrôle qu'il s'agit bien de la
  partie enregistrée (début, durée, champion), puis `/lol-match-history/v1/games/{id}` et
  `/lol-match-history/v1/game-timelines/{id}`, avec nouvelles tentatives pendant ~2 min.
* `ground_truth.py` (pur) : `build_truth` (fichier compact `games/truth/<stem>.truth.json` : participants, positions
  minute par minute `u,v` + or/XP/CS/niveau, kills, monstres épiques, bâtiments, `score`), `analyze_truth` (morts exactes,
  vrai parcours du jungler ennemi + côté de départ, écarts d'or/XP/CS à 10 et 15 min contre l'adversaire de voie,
  **fiabilité de TreeAI** : précision des alertes de gank, ganks manqués, cercle du brouillard contenant le vrai jungler,
  identifications minimap correctes, suggestion de sensibilité), `aggregate_scores` (calibration sur les dernières parties).
* `recorder.py` ajoute `fog` (cercle du jungler, 1 Hz), `allies` (alliés visibles, 0,5 Hz, pour le replay) et `settings` (sensibilité…) au record ; `analysis.analyze_game(record,
  truth=None)` ; `report.write_report(path, lcu_pending=False)` lit la vérité si elle existe (section « Vérité terrain »).
* `engine._finish_job` : rapport écrit tout de suite (bannière « récupération en cours » + rechargement automatique quand le
  client est trouvé), puis récupération LCU, sauvegarde de la vérité et réécriture du rapport. UI : « Client LoL : connecté /
  non trouvé » sur la page Analyses.

## 11. Détection v4 — a priori gratuits (API officielle + fichiers de config du jeu)

* **Champions morts** (`isDead` + `respawnTimer` de la Live Client API) : `RosterMatcher.set_game_status(game)` (appelé à
  chaque image par `engine._vision` via `HybridDetector.set_game_status`) / `set_dead({alias: s avant réapparition})`.
  Un mort n'a pas d'icône : son portrait n'est **pas cherché** (zéro faux positif, recherche plus rapide) jusqu'à
  0,3 s avant la réapparition, puis sa piste est ré-amorcée à sa fontaine. `RosterMatcher.last_dead` ; les morts ne
  laissent pas de place aux détections anonymes du détecteur générique (`HybridDetector._extras`) ; `engine._stabilize`
  ne ré-étiquette jamais une icône vers un mort ; `gank.py` : un ennemi mort n'est ni une menace ni « disparu ».
* **`game_settings.py`** (lecture seule de `<install>/Config/PersistedSettings.json` puis `game.cfg`, dossiers trouvés comme
  `lcu.py` ou `TREEAI_LOL_DIR`) : `GameSettings` (résolution, `WindowMode`, `MinimapScale`, `GlobalScale`, `FlipMiniMap`,
  daltonien — clé non vérifiée, plusieurs orthographes —, `RelativeTeamColors`). `SettingsWatcher` (relu si les fichiers
  changent). Usage : `FlipMiniMap` → côté de la minimap pour le localisateur ; `RectCache` (`minimap_cache.json` dans les
  données utilisateur) mémorise le dernier rectangle trouvé par (taille de fenêtre + empreinte des réglages) et
  `MinimapLocator.locate(..., hint=)` le revérifie d'abord (~8 ms au lieu de 150–500 ms ; rejeté si le score baisse).
  Actif seulement sur le vrai écran (pas en démo / tests).
* **`jungle_intel.py`** (`JungleIntelTracker`, données publiques du Tab, ~1 Hz) : un **achat** (objet nouveau, hors
  améliorations automatiques / objets de runes, joueur vivant) = il est à sa fontaine → `FogTracker.anchor(..., "recall")`.
  Le **CS du jungler ennemi monte** = il farme : les camps / puits / points de voie qu'il pouvait atteindre depuis la
  dernière observation (même vitesse que le brouillard) deviennent une **ancre multi-points** ; la région du brouillard
  devient l'**intersection** de l'ancienne région et de la nouvelle (jamais plus grande ; le cercle affiché reste celui de
  la dernière observation). `JungleIntel` (`engine.jungle_intel()`) : `farming`, `farm_side` (« top »/« bot » si tous les
  candidats sont du même côté), `recalled`, `text` (« Lee Sin farme côté haut (il y a 4 s) », « … a rappelé (achat il y a
  12 s) »), `earliest_eta(uv, now)`. On n'utilise jamais la taille du saut de CS (souvent arrondi par dizaines).
* `FogTracker.anchor(alias, uv, t, reason, points=None)` accepte plusieurs points ; `Reachability.distance_field_multi` ;
  `FogEstimate.seeds`.

## 12. Coups notés (style chess.com) + plans IA v2

* `plays.py` (pur) : `PlayClassifier.update(PlayContext) -> list[Play]` note les moments clés à partir des
  événements Live Client, de mes stats (or, PV, score de vision), de la menace de gank, du cercle du jungler
  (fog_tracker), de l'appel RECULE (fight.py), des vagues (waves.py), du tableau Tab et des timers d'objectifs.
  Classes `CLASSES` : `brilliant` « COUP DE MAÎTRE !! », `great` « EXCELLENT ! », `best`, `good`,
  `inaccuracy` « IMPRÉCISION ?! », `mistake` « ERREUR ? », `blunder` « GAFFE ?? », `miss` « OCCASION RATÉE »,
  chacune avec une raison FR d'une ligne. Tout est compté ; l'affichage : 1 badge / 45 s max (sauf `brilliant`),
  rien pendant un combat / une menace de gank (sauf un petit badge `brilliant`), classes visibles selon
  `skill_level` (`SHOW_BY_SKILL`). `build_context(engine, t, gt, game, threat, me_uv)` construit le contexte.
* `fx_render.py` (pur) : badge à bords nets (DESIGN.md), icône colorée par classe, entrée avec dépassement,
  reflet, sortie en glissant ; `render_frame(cls, title, reason, age, size=, scale=)` (BGRA prémultiplié, ~1 ms).
  `fx_overlay.PlayFx` : fenêtre layered traversée par la souris **dédiée**, créée seulement pendant une
  animation (≤ 30 i/s, coût nul au repos), position `cfg.plays_position` ("top_center" | "minimap") ; sons courts
  générés (`plays_sound`, `plays_sound_negative` désactivé par défaut).
* Engine : `_plays_tick` (chaque tick), `plays_summary()`, `recent_plays` ; en fin de partie
  `plays.attach_to_record(path, summary)` écrit `record["plays"]` = `{"counts": {classe: n}, "total",
  "precision": 0-100, "best": [...3], "worst": [...3], "plays": [{"cls","rule","reason","gt","key","alias","title"}],
  "labels": {classe: libellé FR}}`. **Pour le rapport / l'UI** : `plays.summary_from_record(record)` (None pour
  une ancienne partie), `plays.summary_line(summary)` (« Précision 68 · 4 coups de maître · 4 gaffes »),
  couleurs `fx_render.CLASS_RGB`, image d'un badge `fx_render.render_frame(cls, title, reason, 1.2)`.
* `ai_advisor.py` v2 : réponse **JSON stricte** (`PLAN_SCHEMA_FR`, `parse_plan` : `plan` + ≤ 3 `etapes`,
  `objectif`, `urgence`) ; le snapshot contient `coups` (coups notés), `prec`, `diff` (or / niveau / CS),
  `prio` (vagues), `objt` (timers), `balises` (wards.recommend), `jint` (infos jungle si disponibles) ;
  moments : combat perdu (morts alliées > ennemies), 80 s avant un objectif majeur (dragon dès 14:00, Baron,
  ancestral), bascule d'or ±1 500 en 1-2,5 min, fenêtre de retour, 2 gaffes en 4 min ; budget
  inchangé (5 auto + 1 urgence) ; hors ligne / quota / JSON cassé → `rule_plan(moment, snapshot)`
  (`Advice.source == "rules"`, titre « PLAN », sans réseau, sans budget). `AIAdvisor.note_play(play)`.

## 13. Coaching extras (v1.9) — pics de puissance, plan de voie, objectif de partie, cause de mort

Tout est **visuel** (ligne du HUD via `TipRotator`, toasts), jamais dit à voix haute ; seules sources : Live Client
(niveaux / objets / scores publics, mes PV / sorts d'invocateur, événements) + faits minimap du coach.

* `spikes.py` : `level_spike(mon_niveau, son_niveau)`, `item_spike(mes_objets, ses_objets)` (purs, réutilisables) ;
  `SpikeTracker` : qui atteint le premier 2/3/6/11/16 ou finit un objet légendaire (≥ 2200 PO) dans ma voie ->
  fenêtre (35 s niveaux 2/3, 75 s niveau 6, 90 s objet) -> raisons de jauge (`(+1.5, "tu es 6 avant Darius")`).
* `game_plan.py` : `matchup_card(game, rôle, adversaire)` (profils `meta.py` : courbe, portée, mobilité, style) ->
  toast « PLAN DE VOIE : DARIUS » au début + 2 lignes de plan + ligne jungler (côté **probable** du 1er gank,
  heuristique) ; jungle : « Premier gank top : … » ; `map_fields(MapState)` (point d'âme, buff Baron / Ancestral).
* `goals.py` : `pick_goal(historique, rôle)` (dernières parties `report.list_games` : CS/min sous la cible du rôle ->
  objectif CS, sinon morts -> « N morts maximum ») ; `GoalTracker` : toast au début, conseil quand l'objectif est en
  danger (dernière mort permise, CS en retard à 10/15/20 min), toast de félicitation quand il est atteint.
* `death_cause.py` : `classify_death(DeathSnapshot) -> (cause, ligne)` (tour, en infériorité, jungler surprise,
  sous-niveau, PV bas, trop avancé ; None si rien de clair) + `DeathCoach` (état ~4 s avant la mort) -> toast
  « POURQUOI CETTE MORT ? » + ligne HUD, une fois par mort.
* `coach_plus.CoachPlus` : orchestre le tout pour `engine._coach_plus_tick` ; `factors()` -> `coach.stance_factors(...,
  extra=)` (jauge), `tip_fields()` + `buy_fields(rec)` -> `tips.build_context(..., extra=)` ; toasts filtrés par le
  niveau du joueur (`skill.tip_min_prio`), 1 toast / 20 s max, retenus pendant gank / combat.
  `CoachEngine.coach_extras()` : objectif + statut, plan, causes des morts (UI / rapport).
* Nouveaux conseils (`tips.py`) : pics de niveau (`spike_*`), « Rentre acheter Phage : tu as l'or » (`comp_ready`),
  « Rentre maintenant : tu reviendras à temps pour le Héraut » / « Ne rentre pas : Dragon dans 30 s » (selon le rôle),
  âme / Baron / Ancestral (les deux camps), objectif de partie, plan de voie. Le conseil TP exige la Téléportation.
* Anti-spam : toast « ASTUCE » seulement pour un NOUVEAU conseil de prio ≥ 3, 1 / min ; ligne HUD gardée ≥ 5 s
  (`HUD_DWELL_S`) sauf danger ; lignes hype / probabilité de victoire via la porte vocale (écrites en minimal /
  normal) ; conseil d'achat : 1 par passage en base ; « N ennemis disparus » ne prend plus la ligne HUD.
* Mesure : `python -m treeaicoach.coach_sim --level intermediaire` (partie scriptée de 30 min, moteur réel) ->
  voix / toasts / changements de ligne HUD par minute ; `tests/test_coach_plus.py` borne ces taux.

## 14. COUPS DE GÉNIE — planificateur macro (`macro.py`, 100 % règles, zéro appel IA)

* `macro.MacroPlanner` (un par `TacticalDirector`, appelé au rythme lourd par `tactics._macro_tick`) : **un seul appel
  actif** (`GeniusCall` : `text` impératif court « Va mid : ta tour du bas est tombée », `why` d'une ligne, `target` uv,
  `title`, `tier` basic/mid/high, `score` 0-1, `genius`) ; gardé ≥ 10 s (`HOLD_S`), revalidé à chaque tick avec des
  seuils relâchés (hystérésis, `MacroCtx.keep`) et annulé après 2 s invalide ; rien pendant un combat / une menace de
  gank / mort (l'appel actif est annulé) ; écart entre appels, score minimum et paliers par niveau (`LEVELS` :
  débutant tout, expert seulement `high`) ; situations « une fois » (`ident`), retour en base une fois par aller-retour.
* Contexte (`build_ctx`) : `phase.MapState` (tours, inhibs, morts + `respawnTimer`), `fight.snapshot` (alliés vus,
  ennemis + temps caché), timers d'objectifs, vagues (`coach.waves()`), `engine.jungle_intel()`, rôles, or d'équipe (Tab).
  Score : valeur de l'appel × confiance de l'info × avantage (`team_edge` : or, nombre en vie, niveaux) × part de la
  carte vue (`vision_share`) ; position probable du jungler ennemi (`jungler_location` : vu sur la minimap, décroît
  sur 40 s ; côté de farm Tab, décroît sur 30 s ; foule à un puits).
* Règles : `fight_won` (2-3 morts → Baron / ancestral / dragon / Héraut / inhibiteur / tour avec la fenêtre de
  réapparition ; ace / 4+ = `phase.EndGameCaller`), `fight_lost`, `jungler_dead` (envahir / objectif libre / jouer
  avancé), `plates` (adversaire mort ou en base : plaques avant 14:00, la tour après), `cross_trade` / `free_dragon`
  (jungler ou 3+ ennemis de l'autre côté → Héraut / larves / tour / dragon), `rotate_mid` (duo bot après la tour du
  bas), `side_wave` (« Change de voie : va top » ; règle de split sûr : jungler localisé ailleurs / mort ou 3+ ennemis
  vus de l'autre côté), `split_safe`, `lane_swap`, `wave_recall` / `wave_freeze` / `back_off` (vague du canon d'après le
  chrono : `next_cannon_arrival`). Le regroupement avant objectif reste `positioning.PositionCoach`.
* Visuel : flèche minimap `MapGuide("genie", label « VA ICI » / « TOUR » …)`, grande bannière (`title` + `why`), ligne
  HUD tenue tant que l'appel est actif (`engine._hud_line`), badge « COUP DE GÉNIE » (classe `brilliant` de
  `fx_overlay.PlayFx`, hors précision des coups notés) pour les appels `genius` de score ≥ 0,6, 1 / 4 min par type.
  Pas de voix (sauf `fight_won` en « normal », §16). Les conseils écrits redondants de `coach.MapCoach` sont retirés autour d'un appel
  (`OVERLAPS`, `TacticalDirector.drop_overlaps`). `engine.macro_calls` : historique ; `ai_advisor` reçoit l'appel actif
  (`snap["genie"]`) et `rule_plan` (plan hors ligne) le reprend en priorité.
* Achats (`itemization.situational_buys`) : avec l'or restant après le chemin d'objet, bottes (≥ 7:00), bottes de
  niveau 2 selon les dégâts ennemis (Coques / Mercure), balise de contrôle si aucune et objectif ≤ 2 min (support,
  jungle ou après 15:00) → `Recommendation.extras` + `buy_text` ; toujours 1 conseil par passage en base.
* Mesure : `python -m treeaicoach.coach_sim --level debutant` affiche « COUPS DE GÉNIE : n appels (x / min) » et chaque
  appel avec son POURQUOI ; `tests/test_macro.py` (états scriptés + partie simulée débutant / expert).

## 15. Détection v5 — piles d'icônes, mon icône, trajet du jungler, robustesse, coût

* **Piles** (`roster_matcher._stack_search`, étape 5b) : un champion suivi il y a < 3 s dont la position prédite
  touche une icône acceptée est cherché DESSOUS : l'arc visible de son anneau (couleur de son camp, hors des disques
  des icônes du dessus + leur halo mesuré côté opposé) est ajusté au rayon connu ; la partie visible du portrait
  (NCC masquée) ne doit pas le contredire (seuil croissant avec la surface visible). `MatchInfo.reason == "stacked"`.
  `RosterMatcher.stack_search` (défaut True). Une pile jamais vue séparée (départ de la fontaine) reste une limite.
* **Moi** : `CameraLock` (caméra verrouillée = mes correspondances fortes tombent sur le point caméra 4 images de
  suite) → ma position = point caméra + décalage appris quand mon icône n'est pas trouvée (≤ 6 s sans
  confirmation, annulé si le rectangle saute plus vite qu'un champion) ; `reason == "camlock"`.
  `self_icon.ring_candidates` : un anneau turquoise (contour « moi », `SELF_OUTLINE_MIN`) est toujours vérifié, même
  hors du budget des disques pleins (icône cyan perso, caméra libre).
* **`jungle_path.py`** (`JunglePathModel`, possédé par `JungleIntelTracker.path`) : routes standard du début de
  partie (4 full clears, 6 chemins niveau 3 + gank, crabe à 3:30) × allure × décalage ; vraisemblance des
  apparitions, des hausses de CS (Tab), achat / mort = modèle coupé ; `heat(gt, region)` = carte de probabilité
  (somme 1 sur la région du brouillard, 15 % uniforme). `FogEstimate.heat` (None = uniforme) via
  `FogTracker.heat_source` (`fog_active` garde l'estimation du jungler au-delà de `fog_max_s` tant que le modèle est
  informatif, confiance 0,2) ; ancre « start » à sa fontaine en début de partie. `overlay_render` peint la chaleur
  (radar + couche minimap) au lieu du remplissage uniforme.
* **Robustesse** : minimap grisée (filtre de mort, capture désaturée) → NCC luminance seule + test du dessin de
  l'anneau (`GREY_*`), `MinimapLocator.verify` en luminance seule (pas de relocalisation en boucle) ; minimap
  masquée (boutique, tableau des scores : `verify` < seuil) → aucune détection sur ces images
  (`MSG_MINIMAP_COVERED`), reprise à la première vérification bonne (vérifiée à chaque image tant qu'elle est
  mauvaise) ; mode non Faille → message avec la carte détectée (ARAM, Arène…). Daltonien : couleurs exactes non
  confirmées publiquement → pas de valeurs codées en dur, les couleurs d'anneau restent apprises en direct.
* **Coût** : porte de changement (`_changed` : différence d'image ouverte à 0,3 × diamètre, hors champions suivis)
  → les champions perdus ne sont cherchés sur toute la carte que si une icône est apparue ou toutes les
  `LOST_EVERY` images ; recherche complète par moitié de roster toutes les 12 images ; recalibrage sur
  correspondances faibles étroit (±12 %) et au plus toutes les 30 s ; échelle mémorisée → confirmation étroite ;
  détecteur ONNX d'appoint toutes les 4 images (ou sur changement) ; `cv2.setNumThreads(2)` ; `Track.copy` sans
  `dataclasses.replace`.

## 16. V2 — audit pro des conseils, liste blanche de la voix, cohérence entre systèmes

* **Liste blanche de la voix** (`voice_policy.route` / `VoiceGate.decide`, même liste à tous les niveaux) :
  1. alertes de gank après tri (`triage_gank`) ; 2. RECULE (`call:retreat`, et le « Recule ! » personnel de `danger.py`) —
  « Attaque ! » n'est jamais dit (bannière) ;
  3. réponse F9 ; 4. objectif à ≤ `OBJECTIVE_VOICE_MAX_LEAD_S` (20 s) **si je suis concerné** (`objective_involved` :
  rôle, ou près de la fosse ; Baron / ancestral pour tous après 20:00). `voice_level="normal"` (préréglage débutant)
  ajoute l'appel chiffré d'après-combat (`urgent:ace:` de `EndGameCaller`, `urgent:genie:` = `macro` `fight_won`) ;
  `"bavard"` ajoute tout le reste. Jamais pendant un combat sauf RECULE. `SpeechContext` porte `role`, `me_uv`, `gt`.
* **Pas de doublon voix / texte** : `VOICE_ONLY_PREFIXES` (`urgent:genie:`, `call:engage`, `stance:`, `hype:swing:`) =
  doublons d'un visuel déjà à l'écran (bannière, jauge, % de victoire) : abandonnés quand ils ne sont pas dits.
* **Un toast par sujet** (`voice_policy.topic_of`, `TOPIC_TOAST_S` = 40 s ; `engine._topic_seen`) : retour en base,
  adversaire mort, jungler mort, objectif, position du jungler, sbires, niveaux, adversaire disparu, nombre ; un appel
  macro réserve son sujet. La ligne HUD continue d'être mise à jour.
* **Cohérence** : l'appel macro actif entre dans la jauge (`engine._macro_factors`, ±2) ; une jauge SAFE (score ≤ −4)
  ou < 35 % de vie bloque les appels « vas-y » sauf `fight_won` (`MacroCtx.stance_score`) ; `TipContext.macro_tone`
  cache les conseils de ton opposé ; `TipContext.recall_said` + `engine._recall_consistency` : un seul « rentre » par
  aller-retour.
* **Fenêtres de réapparition** (`macro._window_ok`) : fenêtre + retour ennemi depuis sa fontaine ≥ notre trajet +
  temps de prise (`TAKE_S`) et plancher (`WINDOW_FLOOR_S`) ; `EndGameCaller` : Baron / finir ≥ 25 s, dragon ≥ 15 s.
* Toasts et bannières : un sous-titre trop long passe sur deux lignes plus petites au lieu d'être coupé.
* Audit complet + tests : `tests/test_v2_audit.py`.

## 17. Danger personnel, ganks plus tôt, revue IA ancrée (retour de la 1re vraie partie)

Rapport réel (Garen top, 1/15/5, 11 morts « sans alerte ») : morts en 1v1 face à l'adversaire de voie visible, jungler
visible à l'écran 7 s avant la mort sans alerte, alertes 0–4 s avant la mort, 8 « Roam Ekko » douteux, aucune alerte
dans ma base pendant le siège, revue IA avec des objets anglais hors méta.

* `danger.py` — `PersonalDanger.update(t, gt, game, tracker, lane_opponents, jungler, threat, gank_danger_t,
  in_fight, fog) -> list[Alert]` (kind `PERSONAL_DANGER`) : mes PV / niveau / objets (Live Client) contre les ennemis
  visibles autour de moi, **à l'écran ou non, gank ou non**. DANGER « Recule ! » (dit : PV ≤ 35 % et un ennemi sur moi,
  ou PV ≤ 55 % et en infériorité) ; WARNING écrits : « Vladimir te domine : ne trade pas, farme sous la tour. »
  (adversaire de voie avec +2 niveaux / 6 contre 5 / +900 PO d'objets / puissance ×1,3), « Peu de vie et X près de
  toi : recule. », « N ennemis près de toi : recule vers ta tour. », « Kindred peut arriver : recule vers ta tour. »
  (jungler invisible 8–60 s, ≥ 35 % de la chaleur `FogEstimate.heat` à ≤ 7 s de marche, moi au-delà du milieu).
  Anti-spam : 1 message / tick, « Recule ! » jamais 2 fois en 20 s, même ligne jamais en 20 s, cooldowns par règle,
  rien dans la fontaine / mort / en combat / juste après un gank DANGER. Branché dans `engine._personal_danger` et la
  voie rapide des ganks (`_say_gank_now`).
* `voice_policy` : `PERSONAL_DANGER` DANGER = voix critique (hors budget), WARNING = écrit (toast « DANGER »).
* `ground_truth` : `alert_lead_s` par mort, `lead_mean` / `lead_late` dans la fiabilité (carte « Avance des alertes »).
* `ai_advisor.postgame_review` : 1 requête ; le prompt porte le build final et la liste des objets autorisés
  (`review_item_candidates` : build + objets de base de la classe + contres, noms français) ; `ground_review` retire
  toute phrase qui nomme un autre objet (FR ou anglais) et francise les noms anglais autorisés ; réponse anglaise rejetée.

### 17 bis. IA v3 « mastermind » (audit en direct sur Groq, 2026-10)

* **Un seul instantané** (`build_snapshot`, ordre fixe `SNAP_ORDER`, < 1,5 k jetons, mesuré 600–1 000) :
  `t`, `ph` (voie / milieu / fin), `mo`, **`carte`** (appel actif de la carte : `macro_active()`), `me` (PV %,
  objets, or, réapparition), `voie` (adversaire, écarts niv / or / CS, 2 conseils de `matchups.json` en phase de
  voie), `eq` (or d'équipe + **tendance sur 2 min**, kills, somme des niveaux), `al` / `en` (une ligne par joueur,
  objets finis des ennemis, « mort Xs »), `compo` (`team_profile` : dégâts P/M, styles, courbe, contrôle + plan de
  victoire d'un module d'analyse de compo s'il expose `win_condition()` / `team_comp_summary()`), `effets` (objets
  légendaires ennemis : anti-soin, stase, armure x2…), `jgl` (vu où / il y a combien, Tab : farm, achat, niveau,
  côté probable < 6:00), `obj`, `carto` (dragons, âme, buffs, tours / inhibiteurs perdus), `morts` (+ cause de
  `death_cause.DeathCoach.log`), `ev`, `coups` + `prec`, `achat`, `objets_possibles`, `prio`, `balises`, `wp`,
  `plus` (**contrat ouvert** : `engine.ai_extra_context() -> dict` pour tout autre module). `engine_context(engine)`
  construit la partie « moteur ».
* **Plan JSON** (`PLAN_SCHEMA_FR`) : `plan` (verbe à l'impératif), `etapes` (détails nouveaux ; une étape qui répète
  le plan est retirée), `objectif`, `urgence`, **`carte`** (`suit` / `differe` / `aucune`) + `pourquoi` (1 ligne si
  `differe`). `frenchify` remplace le jargon anglais (farm, lane, last-hit, ward…). Réponse tronquée (`length`) :
  `plan` et étapes complètes récupérés (`_salvage_plan`).
* **Cohérence avec la carte** (`card_check`, `Advice.check_card`) : posture « recule » contre « prends / frappe »,
  ou deux objectifs différents → `conflict` (jamais affiché) ; désaccord expliqué → `explained` (affiché quand la
  carte n'est plus active, avec « Pourquoi pas la carte : … »).
* **Publication** (`engine_coaching._ai_publish`) : TOUT passe par le présentateur (avant : la ligne HUD était écrite
  directement, combats compris) ; retenu pendant un combat / une menace de gank, refusé s'il est périmé
  (`Advice.max_age` : 25 s, F8 60 s) ; réponse à F8 et appel bonus « urgence » en urgence 2 (passent la barre
  « expert » et une ligne ordinaire). `_ai_in_fight` lit `tactics.in_fight()` (avant : attributs inexistants,
  toujours faux).
* **Politique** : une seule requête en vol (drapeau réservé sous verrou : moteur + touche F8 ne lancent jamais
  deux requêtes), génération par partie (une réponse de la partie précédente est jetée), moment clé pendant un
  combat **différé** (`DEFER_S` = 30 s), fenêtres décisives (ace, carrys morts, surnombre, avance) sur le créneau
  « objective » ; une réponse inutilisable rend son créneau ; `GAME_HARD_CAP` compte les requêtes **envoyées**
  (`budget.sent`, jamais remboursé).
* **Fournisseurs** : 429 séparé en `rate` (limite par minute : attente donnée par le fournisseur, 1 nouvel essai
  si ≤ 3 s, recul 15–120 s, jamais bloquant pour l'auto-diagnostic) et `quota` (jour / compte) ; erreur dans un
  corps 200 (OpenRouter) ; modèle remplacé mémorisé par (fournisseur, modèle demandé) ; modèles parole / sécurité
  jamais choisis ; Gemini : modèle par défaut `gemini-3.5-flash-lite`, `thinkingConfig` (2.5 : budget 0, 3 :
  niveau bas), parties `thought` ignorées, `promptFeedback.blockReason` ; Ollama : `think` pour les modèles qui
  réfléchissent ; Anthropic : `claude-haiku-4-5`, `stop_reason: refusal`. « Tester » envoie une vraie requête de
  plan JSON ; « Tester la clé » : une limite par minute prouve que la clé marche.
* **Revue d'après-partie** : JSON (2 points forts, **exactement 3 axes** `axe` / `preuve` / `exercice`, `objets`),
  rendue en français ; ancrage objet par champ (un consommable comme la balise de contrôle est toujours permis),
  axe manquant complété depuis les chiffres du rapport (`_rule_axes`), verdicts des morts en français, déroulé de
  la partie (`AIAdvisor.timeline` : écart d'or / kills toutes les 2 min + plans donnés) dans le prompt.
* **Test en direct** : `GROQ_API_KEY=... python -m tools.ai_live_check` (clé lue dans l'environnement seulement,
  ~7 requêtes) ; le test pytest réel ne tourne qu'avec `TREEAICOACH_LIVE_AI=1`.

## 18. Pipeline v2 — capture, cadence, overlay fluide, diagnostic (systèmes)

Pourquoi les vraies parties échouaient là où nos tests passaient : capture GDI (mss) lente, noire
ou figée selon le mode d'affichage ; notre propre calque minimap capturé et relu par le détecteur ;
overlay redessiné à 4 Hz « au calme » avec des positions médianes déjà vieilles d'un ou deux ticks
(mesuré : 0,8 s de retard moyen, p95 2,8 s sur une machine chargée) ; overlay dessiné par-dessus le
client / le navigateur après un alt-tab ; carte HUD posée sur les portraits alliés et les votes.

* **Capture** (`capture.SmartCapture`, `dxgi_capture.py`) : Desktop Duplication DXGI en ctypes pur
  (aucune dépendance) — copie GPU du seul rectangle de la minimap (`CopySubresourceRegion`) vers une
  texture de staging, repli automatique sur mss (rectangle sur deux écrans, RDP, Windows 7, Wine).
  La 1re image DXGI est comparée à mss (désactivée si elles diffèrent). `check()` : images noires
  (3 de suite) ou figées (12 images et 4 s, seulement après 1:30 de jeu) → l'autre backend est
  essayé ; s'il voit une image vivante on bascule, sinon « Capture noire / figée : passe le jeu en
  Sans bordure ». `game.cfg` `WindowMode=0` → avertissement (bannière) « Plein écran ».
  `cfg.capture_backend` = auto | dxgi | mss.
* **Focus / occultation** : `capture.foreground_state()` (overlay, à chaque image, grâce 0,3 s :
  tout est masqué dès que le jeu n'est plus au premier plan, sauf nos propres fenêtres) ;
  `capture.rect_occluded()` (`WindowFromPoint` sur 5 points de la minimap) : une autre fenêtre
  couvre la minimap → tick gelé (aucune image lue, le tracker n'est pas mis à jour). Jeu réduit :
  pause (1 contrôle / s). Fenêtre déplacée (même taille) : rectangle décalé sans nouvelle recherche ;
  réglages du jeu modifiés (empreinte `game_settings`) : relocalisation.
* **Cadence** (`scheduler.py`, `sysperf.py`) : détection adaptative (`RateGovernor`) 6 img/s au
  calme, `target_fps` (12) pendant 3 s après une menace / un ennemi proche / une apparition ;
  2 img/s jeu en arrière-plan. Étapes de coaching étalées (`HeavyScheduler` : tactics, coach, Tab,
  conseils — un créneau par tick, jamais sur le tick de vérification de la minimap). Budget
  `PerfBudget` : « low_end » (≤ 4 CPU logiques, ou ticks mesurés > 30 ms en moyenne / 60 ms p95 sur
  les 30 premières secondes) → 4-8 img/s, coaching 1 Hz, ONNX d'appoint toutes les 16 images,
  1 thread OpenCV / onnxruntime, overlay 15 img/s. Processus en priorité « inférieure à la
  normale » + EcoQoS (`cfg.low_priority`, `cfg.eco_qos_v2` opt-in depuis 2.1.1), OpenCV ≤ 2 threads (`main.apply_process_policy`).
* **Overlay** (`overlay.py`) : boucle régulière `cfg.overlay_fps` (30, budget 15), `time.sleep`
  haute résolution ; positions **prédites à l'instant du rendu** (`engine.predict_positions` →
  `scheduler.MotionSnapshot` : position + vitesse de Kalman, amortissement 1,5 s, horizon 0,9 s) ;
  calques re-rendus seulement si leur signature change ; flash rendu une fois puis fondu par alpha
  global. Règles du calque minimap (`overlay_render`) : piste vieille > 0,7 s / empilée / anonyme =
  fantôme pointillé sans étiquette ; ≤ 4 étiquettes, une par champion et par rôle, jamais sur une
  icône vivante ; texte des fantômes seulement pour le jungler ennemi et ≤ 45 s ; aucun fantôme
  d'un ennemi mort ou dans sa fontaine ; carte de chaleur OU contour du brouillard, pas les deux ;
  plus de marque « TreeAI » (elle couvrait mon portrait). Carte HUD : défaut « left_of_minimap »
  (validé sur les captures réelles : portraits alliés, vote de reddition / Baron, boutons caméra,
  barre d'objets), état « MORT · retour dans 8 s », siège de base / ace en DANGER (événements
  `TurretKilled` / `InhibKilled` / `Ace`), jamais de conseil « à toi de jouer » sous une jauge
  PRUDENT, rien de la phase de voie avant 1:05 ou mort. Toasts / bannières au style `DESIGN.md`.
  `cfg.overlay_hide_from_capture` = True par défaut (migration des anciens fichiers).
* **Santé** : `CoachEngine.health()` (aussi `EngineStatus.health`) : backend et img/s de capture,
  ms p50/p95 de capture / détection / tick / coaching, overlay (img/s, ms par calque, ms
  `UpdateLayeredWindow`), champions vus / attendus, score de la minimap, cadence et budget,
  CPU % d'un cœur, état de la capture, pause.
* **Diagnostic** (`diag.py`) : `Ctrl+F8` (`cfg.hotkey_diag`, enregistré par le moteur) ou
  `CoachEngine.start_diagnostic()` (bouton de l'UI) : 60 s, une image / 2 s (minimap brute +
  annotée + JSON détections / pistes / santé / roster réduit), une vignette de la fenêtre,
  `meta.json` (système, réglages en liste blanche, réglages du jeu, écrans, DPI), fin du journal
  (chemins masqués) → `%APPDATA%\TreeAICoach\diagnostics\diag_AAAAMMJJ_HHMMSS.zip`, dossier ouvert.
  `diagnostic_status()` pour l'UI.

## 19. Données de jeu vivantes (Data Dragon) + carte d'avant-partie (sélection des champions)

* `game_data.py` : `items_data()` / `champions_data()` = la plus récente des données en cache
  (`user_data_dir()/ddragon/items.json`, `champions.json`) et des données embarquées (`assets/items.json`,
  `assets/icons/champions/index.json`), comparées par version Data Dragon ; `item_name(id)`, `champion_name(alias)`.
  `refresh_async(allow_network)` (lancé par `main.main`, sauf `--demo` / `--ui-smoke`, interrupteur
  `download_skin_icons`) : thread démon, au plus une fois par 24 h (`last_check.json`), urllib, User-Agent,
  timeouts, tailles bornées, échecs silencieux. Après une mise à jour : `add_listener` → itemization,
  scoreboard et `champions.get_default_db()` rechargent leurs tables. `coach.ITEM_NAMES_FR` lit les noms
  dans ces données (plus aucun nom d'objet codé en dur). Un champion plus récent que le build reçoit son icône
  de base CommunityDragon (`<cache>/<Alias>_0.png`) via `ChampionDB.prefetch_skin_icons`.
* `champ_select.py` : lecture seule (GET `/lol-champ-select/v1/session`, client LCU local) →
  `PregameCard` (titre « AHRI · MID », adversaire de voie probable, 3 conseils « action : raison »,
  objets de départ avec noms / prix des données vivantes, `lines` prêtes à afficher). Aucun nom de joueur
  lu ni gardé, aucune action sur le client. API pour l'UI : `champ_select.pregame_card()` (sondage limité à
  1 / 2 s) ou `ChampSelectWatcher(...).start()` + `.card()`. League Classic (ids ≥ 60000) ignoré.
* `live_client.GameInfo.is_league_classic` : carte 453 « Classic Rift » / mode « JADE » → non pris en charge.
* Saison 2026 dans les règles : sbires 0:30, camps 0:55, carapateurs 2:55, vagues toutes les 25 s dès
  14:00 (20 s dès ~30:00), canon toutes les 2 vagues dès 14:00 ; plaques permanentes (120 PO, −10/min de
  11:00 à 15:00 sur les tours extérieures) ; lampes féeriques (`wards.SPOTS` avec `faelight=True`,
  positions EXACTES des fichiers du jeu ; 4 n'existent qu'après la transformation de la Faille : `wards.rift_transformed`).

### 19.1 Base de données du jeu (patch 26.19) — `python tools/fetch_all.py`

Une commande régénère tout (cache de téléchargement `training/cache/gamedata`, `--offline` possible),
puis lance l'audit `tools/validate_data.py` (code de sortie 1 sur une ERREUR ; `--offline` = données
embarquées seules, ce que fait `tests/test_data_integrity.py`). Sources et licences : THIRD_PARTY_NOTICES.md.

| fichier (assets/) | outil | contenu |
|---|---|---|
| `items.json` | `fetch_items.py` | Data Dragon fr/en ; `p` = vendu en partie normale (filtre des listes de boutique CLASSIC des fichiers du jeu, ids exclus dans `not_sr`, conservés par la mise à jour au lancement) ; `k` (bottes niveau 3 = boots) ; `x` drapeaux d'effet lus dans la description anglaise |
| `champion_meta.json` (schéma 2) | `fetch_meta.py` + `tools/data/champion_curation.json` | un profil par champion avec la source de chaque champ (`src`) : stats Data Dragon, notes officielles du client Riot (dégâts, robustesse, contrôle, mobilité, utilité ; type de dégâts / d'attaque), postes recommandés par Riot (le principal d'abord, noms Live Client), rôles Meraki, courbe / pic niveau 6 / nettoyage de vague / split / soin / classe de voie / genre (curation) |
| `item_builds.json` | `fetch_builds.py` | chemins par classe (+ exceptions par champion), contres par besoin (confirmés par les drapeaux / stats), bottes, départs, amélioration de l'objet de support (quête finie) |
| `matchups.json` | à la main | conseils de voie (verbe en tête, « action : raison », ≤ 12 mots) : paire de champions > contre un champion > paire de classes > règles des profils > contre une classe |
| `ward_spots.json` | `fetch_map.py` (Faelights / camps) + à la main | balises en coordonnées ABSOLUES (`side` blue / red / rivière ; plus de miroir pour le côté rouge), rôles, phases, fenêtre `from_s` / `until_s` |
| `objectives.json` | à la main | minuteurs 2026, vérifiés contre `validate_data.OFFICIAL_2026` |

API : `meta.profile()` (champs `spike6`, `mobility`, `cc`, `sustain`, `waveclear`, `splitpush`,
`lane_class`, `female`, `source` "data" / "rule" — un champion sorti après le build reçoit un profil
déduit des tags et stats de la mise à jour Data Dragon) ; `game_data.item_effects(id)` /
`itemization.item_effects`, `inventory_effects`, `enemy_effects` (étiquettes d'effet : antiheal,
armorpen, magicpen, lifesteal, shield, tenacity, stasis, armor, mr, cleanse, anticrit, pcthp, health) ;
`game_data.data_versions()` / `data_versions_text()` (diagnostic `meta.json` → `system.game_data`).

## 20. Overlay épuré : une seule chose à la fois + routeur de présentation

Retour réel (« il y a trop de trucs ») : carte HUD avec jauge, conseil sur 2 lignes, puces « Dragon 3:58 »
et « IA 0/5 », ligne JGL « pas encore vu », 5 portraits « non vu / visible », plus Blitz à l'écran.
Règle (docs/LESSONS.md n° 5) : **danger > une action > rien**.

* **Carte compacte** (`overlay_render.compact_content` / `_render_compact`, défaut) : UNE instruction,
  verbe en tête, ≤ 2 lignes courtes, blanche, opaque, 15,5 px demi-gras à 1080p, sur plaque sombre pleine ;
  barre de gauche = état (vert ok / ambre prudence / rouge danger), pas de mot de jauge ni de glyphes.
  ≤ 300 × 64 px à 1080p. Danger (gank, siège, ace, menace ≥ 1) : plaque rouge / ambre avec le mot court
  (« GANK ! », « BASE ATTAQUÉE », « 2 CONTRE 1 ») et au plus une ligne de ton danger. En infériorité
  (≥ 2 ennemis frais à ≤ 0,09 de moi, plus que nous) : jamais « NORMAL ». Mort : seulement la leçon
  (le jeu affiche déjà le chrono). Rien d'utile : **pas de carte** (`hud_visible` → fenêtre cachée).
  Objectif : devient l'instruction (« Va bot : Dragon dans 0:45 » + icône) dans les 60 dernières
  secondes **si mon rôle le joue** (`voice_policy.objective_involved`, `OverlayState.my_role`).
  Par niveau (`OverlayState.skill_level`) : débutant = la ligne tant qu'elle est valable, intermédiaire
  30 s, avancé 12 s, expert seulement danger (+ ton « danger »). Supprimés en jeu : « IA x/5 » (dans
  l'app), « non vu », ligne JGL, portraits, puces, minuteurs du jeu, marque TreeAI.
* **Mode détaillé** (`cfg.hud_detailed` ou maintien de `cfg.hotkey_details`, F6 par défaut : LoL utilise
  F1-F5 et F12 ; touche lue par `GetAsyncKeyState` dans le fil de l'overlay, jamais enregistrée) :
  l'ancienne carte complète + anneaux / rôles / alliés / fantômes de la minimap.
* **Minimap compacte** : seulement (a) le fantôme / la chaleur du jungler ennemi invisible, (b) flèches
  et anneau de danger quand un ennemi arrive, (c) UN guide (« VA ICI » / balise).
* **Toasts** (`toasts.select_views`) : un seul visible, ≤ 2 lignes, 4 s max ; pendant un combat
  (bannière engage / retreat, menace ≥ 1, ennemi frais à ≤ 0,07) seulement danger / retreat ;
  sortes permises par niveau (`LEVEL_KINDS`) ; un toast qui répète la ligne HUD est retiré.
* **Migration** : un fichier de réglages sans `hotkey_details` (antérieur) repasse une fois aux
  valeurs compactes (`config.DECLUTTER_RESET`) ; les préréglages de niveau n'activent plus le détaillé.
* **Routeur de présentation** (`presenter.py`) : chaque message (`Message{kind, urgency, value, ttl,
  topic}`) reçoit exactement un canal selon le contexte (`Context{fight, gank, dead, siege, skill}`) :

  | sorte        | normal   | combat / gank | mort     | voix | valeur |
  |--------------|----------|---------------|----------|------|--------|
  | danger       | BANNER   | BANNER        | BANNER*  | oui  | 1,00   |
  | retreat      | BANNER   | BANNER        | DROP     | oui  | 0,95   |
  | engage       | BANNER   | BANNER        | DROP     | non  | 0,80   |
  | macro        | BANNER** | DROP          | PANEL    | non  | 0,70   |
  | plan         | BANNER** | DROP          | PANEL    | non  | 0,75   |
  | objective    | PANEL    | DROP          | PANEL    | oui***| 0,60  |
  | death_cause  | PANEL    | DROP          | PANEL    | non  | 0,65   |
  | warning      | PANEL    | DROP          | DROP     | non  | 0,55   |
  | ai           | PANEL    | DROP          | PANEL    | non  | 0,50   |
  | insight      | PANEL    | DROP          | DROP     | non  | 0,40   |
  | tip          | PANEL    | DROP          | DROP     | non  | 0,30   |
  | praise       | BADGE    | DROP          | DROP     | non  | 0,35   |
  | play         | BADGE    | BADGE (DROP sur gank) | BADGE | non | 0,45 |

  \* siège / ace seulement ; \*\* PANEL sous la barre du niveau ou à < 20 s d'une autre bannière ;
  \*\*\* ≤ 20 s et mon rôle (la `VoiceGate` décide toujours de la parole). Plafonds : la ligne change au
  plus toutes les 5 s sauf urgence plus haute ; une bannière non-danger toutes les 20 s ; un sujet par
  40 s ; valeur minimale par niveau (`PANEL_MIN_VALUE`, `BANNER_MIN_VALUE`). Branché dans
  `engine._toast`, `engine._hud_line` (combat / gank : seulement une ligne danger / prudence),
  `engine._overlay_toasts` (bannières du directeur) et les badges de coups. `coach_sim` (30 min,
  `--no-presenter` = avant) : bannières / toasts 2,13 → 0,47 / min (débutant et intermédiaire).

## 21. Auto-diagnostic : TreeAI détecte ses propres problèmes et agit

Retour réel : lag, minimap vide, alertes manquées, mauvaises détections, sans que nous puissions voir la
partie. `selfcheck.SelfCheck` est un chien de garde à règles, évalué ~1 Hz par le fil d'analyse en partie
(`engine_selfcheck.SelfCheckMixin._selfcheck_tick`, à la fin de `step()`), la règle 9 aussi depuis le
fil du Live Client hors partie. Chaque règle = **symptôme mesuré → action automatique → état court en
français** ; au plus **un** avis en jeu par problème et par partie, seulement quand le joueur doit agir,
par le chemin des toasts / du routeur (`engine._toast` → `presenter`, jamais en combat / gank / siège / mort,
texte « verbe d'abord » pour la carte), 90 s au moins entre deux avis.

| # | règle | symptôme | action automatique | état / avis |
|---|-------|----------|--------------------|-------------|
| 1 | `capture` | capture noire / figée ≥ 3 s (fenêtre là, non masquée) | autre backend (`SmartCapture.disable`), +6 s : nouvel objet de capture | +6 s : « Capture noire : passe le jeu en Sans bordure » (avis) |
| 2 | `minimap` | rectangle de secours, vérification basse ≥ 3 s, échecs | relocalisation : indice bon marché (dernier rectangle trouvé dans cette fenêtre) puis recherche complète, recul 3 / 6 / 12… s ; échec = on garde le dernier rectangle vérifié (pas un carré par défaut) ; score qui dérive (< 0,6 et < 0,75 × score trouvé, 15 s) : relocalisation (1 / min, 3 / partie) | 3 échecs : « Minimap introuvable : ouvre Réglages > Calibrer » (avis) |
| 3 | `perf` | < min(4, 0,75 × cible) img/s analysées ou p95 du tick > 60 ms pendant 20 s | niveau de charge normal → allégé → minimal (`sysperf.degraded` : ONNX d'appoint 1/4 → 1/16, propositions d'anneau / de pile 1/4 → 1/8, recherche des perdus 1/8 → 1/12, vérification minimap moins souvent, overlay 20 → 15 img/s, coaching 1 → 0,5 Hz) ; retour après 60 s sain (doublé à chaque rechute < 5 min) | « Analyse allégée : PC chargé » |
| 4 | `champions` | moi + alliés (toujours visibles pour mon équipe) vivants depuis 60 s : ≤ 40 % vus (dès 1:30, ≥ 3 attendus, ≥ 1 img/s, pas pendant ma mort) | recalibrage de la taille des icônes (balayage complet), +40 s : icônes rechargées (gabarits, échelle, icône apprise relue du cache), +40 s : diagnostic auto de 60 s (1 / partie, `cfg.selfcheck_auto_diag`, sans voix ni dossier ouvert) | « Détection faible : envoie un diagnostic (Ctrl+F8) » (avis) |
| 5 | `identity` | un champion à deux endroits éloignés en < 1 s (aller-retour), plus d'ennemis visibles que de vivants (2 s) | `Tracker.forget` de cette piste / des pistes anonymes en trop (recul après 3 essais en 2 min) | note ; « Identités instables » si répété |
| 6 | `overlay` | jeu pas au premier plan 8 s, minimap couverte par une fenêtre 5 s, fil de l'overlay arrêté 5 s | expliqué une fois par partie | « Overlay masqué … » (note), « Une fenêtre couvre la minimap … », « Overlay arrêté … » |
| 7 | `voice` | moteur de voix en échec (`print` sous Windows, 2 échecs / min) ou synthèse des alertes p95 > 1,2 s | dangers en bip seul (`engine._voice_override`, respecté par `_danger_beep` / `apply_config`) ; rétabli après 120 s sain | « Voix lente / indisponible : bips seulement pour les dangers » |
| 8 | `ai` | clé refusée / aucune clé / modèle (l'IA s'arrête d'elle-même), quota nouveau dans la partie, 2 nouveaux échecs | plus aucun appel IA pour la partie (`_blocked_until` = ∞, rétabli à la partie suivante) ; les plans par règles continuent | « IA indisponible (…) : plans par règles pour cette partie » |
| 9 | `api` | Live Client muet alors que la fenêtre du jeu existe (60 s hors partie, 5 s en partie) | sondage ralenti (3 puis 5 s), client HTTP recréé (1 / min) ; en partie, la partie est **gardée** jusqu'à 90 s (`API_OUTAGE_MAX_S`) au lieu de 8 s : une panne de l'API n'est pas une fin de partie | « API du jeu indisponible : coaching limité » |

* Hystérésis partout (`*_ON_S` pour déclencher, `*_OFF_S` pour effacer) : ni clignotement ni actions en
  boucle. Règles 1-9 testées sur des états de moteur simulés (`tests/test_selfcheck.py`).
* Démo / sources d'images (tests, `coach_sim`, `ux_replay`) : désactivé. Moteur avec capture injectée
  (tests) : tout sauf `perf` (mesuré en temps réel). `cfg.selfcheck_enabled` (interrupteur général).
* Lecture : `engine.selfcheck_summary()` = `health()["selfcheck"]` (`state` ok / degraded, `title`
  « Santé TreeAI : OK / dégradé », `reasons`, `notes`, `fixed`, `profile`, `overhead_ms`) ;
  `selfcheck.summary_text()` = la ligne du panneau Système (`ui_page_dashboard._add_health_summary`,
  autonome) ; fin de partie : `SelfCheck.game_report()` → `record["selfcheck"]` → section « Santé de
  TreeAI pendant la partie » du rapport (ce qui a mal tourné, ce qui a été corrigé seul) ; diagnostic :
  `selfcheck.json` + `selfcheck_log.txt` dans le zip.
* **Même version, analyse différente selon le PC** (retour réel) : rien ne change en silence. Règle 10
  `adapt` : profil PC faible choisi automatiquement, changement de backend de capture, taille des icônes
  recalibrée en cours de partie → note visible dans « Santé TreeAI » et dans le rapport d'après-partie.
  **Empreinte de config** (`fingerprint.py`, `engine.config_fingerprint()`, `fingerprint.txt/json` du zip de
  diagnostic, boutons « Copier l'empreinte » / « Comparer » du panneau Système) : version, système / CPU,
  réglages clés + tous les réglages ≠ défaut + empreinte de tous les réglages (clé IA / jeton exclus),
  profil / charge / cadences, backend de capture, fenêtre / DPI / taille de la minimap, réglages du jeu
  (mode fenêtre, échelles minimap / HUD, daltonien), modèle, `det_params.json` (hash), caches propres au PC
  (icônes apprises, `minimap_cache.json`, `icon_scale_by_res`), versions des données ; `compare(a, b)` liste
  les écarts, causes d'abord. Boutons « Réinitialiser la détection » (`engine.reset_detection()` : échelles
  mémorisées, icônes apprises, rectangles de minimap, calibrage / couleurs / pistes de la partie, backend,
  niveau de charge) et « Profil normal » (`engine.force_normal_profile()` + `perf_mode = "normal"` : plus
  aucun allègement automatique).
* Coût mesuré : 0,05 ms par tick d'analyse en moyenne (le tick de l'évaluation 1 Hz : ~0,25 ms), actions
  ponctuelles exclues (recalibrage ~0,1 s une fois par partie au plus).

## 22. Placement : les zones du jeu + un seul solveur (`layout.py`)

Retour réel (« plein de choses sont mal placées ») et audit sur les vraies captures
(`tools/layout_audit.py`, boîtes de l'UI de LoL mesurées à la main dans
`tests/fixtures/layout_real_ui.json` : 14 captures, 2560×1440 des joueurs, flux 1080p / 720p, recadrages
minimap 384 px) : les toasts couvraient l'annonceur des kills (y 4,5 % au lieu de sous 14 %), la colonne
des minuteurs était dessinée DANS le coin de la minimap (bases, tourelles) en ~10 px, les badges de coups
étaient dessinés dans la minimap (`fx_overlay` lisait `engine._screen_rects()` = (minimap, fenêtre) comme
(écran, minimap)), le petit badge prenait la place de la carte HUD, les étiquettes de la minimap se
posaient sur le bouton « ! » / zoom, le flash couvrait la barre de vie. Avant → après : 171 → 0
chevauchements avec l'UI du jeu sur les captures réelles, 50 → 0 entre nos éléments (38 écrans).

* **Zones de l'UI de LoL** (`layout.game_zones(screen, minimap, side, hud_scale)`) en *unités d'UI*
  `U = min(h, w·9/16)` (LoL met son HUD à l'échelle de la hauteur, ancré aux bords / au centre) :
  minimap + cadre + encoche « ! », boutons micro / caméra / réglages, portraits alliés (rangée au-dessus
  de la minimap ou colonne à gauche : les deux), votes (reddition, Baron), fil des kills, score / KDA /
  chrono, annonceur, sorts + objets, statistiques (C), chat, récap de mort, « RETOUR DANS », boutique
  (zone *souple* : modale). Les zones collées à la minimap suivent son rectangle mesuré et son côté
  (`FlipMiniMap` lu dans les réglages du jeu, sinon le côté de l'écran ; zones miroir *supposées*, pas de
  capture réelle avec minimap à gauche). `hud_scale` (1,0 = les captures mesurées) agrandit / réduit les
  zones du HUD ; la correspondance avec `GlobalScale` du jeu n'est pas vérifiée : non branchée.
  `tests/test_layout.py` vérifie que les zones couvrent toutes les boîtes mesurées.
* **Solveur** (`layout.solve` / `layout.layout_for(screen, minimap, cfg, detailed, radar, custom_card)`) :
  éléments par priorité (carte HUD, toast / bannière, minuteurs, badge de coup grand puis petit) ; chacun
  a des *rails* (segments où sa fenêtre peut glisser), groupés en paliers ; le premier palier qui a une
  position libre gagne (coût = distance parcourue + biais du rail, + 65 px·U/1080 si elle touche la
  boutique). Une zone qui n'existe que dans certains états (votes, récap de mort, « RETOUR DANS »,
  statistiques) n'est tolérée que pour la position nommée choisie par le joueur et la carte F6, en
  dernier recours. 4 px (à 1080p) d'air autour des zones et entre nos éléments ; jamais deux de nos
  éléments l'un sur l'autre. Chaque élément a un *emplacement* taillé pour son contenu le plus grand du
  mode (carte compacte 1-2 lignes / danger, carte F6, 3 ou 5 minuteurs ; enveloppes des animations :
  glissement des toasts / badges) et l'image s'y aligne (carte : bord bas fixe) : un texte qui change ne
  déplace rien. Mémoïsé (`LayoutCache`) : la disposition ne change qu'avec l'écran, le rectangle de la
  minimap, les réglages, le mode détaillé (F6 = sa propre variante) ou une fenêtre déplacée (mode
  « déplacer »). `layout.publish` / `layout.published` : la disposition courante du fil de l'overlay,
  relue par le fil des badges (`fx_overlay`).
* **Emplacements par défaut** (minimap à droite) : carte à gauche de la minimap, bas aligné au-dessus des
  boutons micro / caméra (monte si la barre d'objets est dessous, petits écrans) ; minuteurs **hors du
  cadre**, côté intérieur, en haut (fenêtre « timers », 13 px à 1080p, plaque opaque) ; toast / bannière
  centré **sous** l'annonceur ; grand badge sous le toast ; petit badge à côté de la minimap (entre les
  minuteurs et la carte, sinon à côté des minuteurs). Positions nommées (`hud_position`) : leur rail
  d'abord, puis la chaîne par défaut ; « custom » (`hud_xy`, fenêtre glissée) respectée telle quelle,
  hors de la minimap.
* **Autres consommateurs** : `toasts.toast_layer_rect` et `fx_render.fx_layer_rect` rendent l'emplacement
  du solveur (`fx_overlay.screen_and_minimap` remet les rectangles dans l'ordre) ; flash de danger :
  jamais sur la minimap, ses boutons ni la barre de sorts / objets (`Layout.flash_exclusions`, plusieurs
  rectangles) ; repères au sol / flèches de bord (balise) : `place_world_patch` déplace du plus petit
  décalage hors du HUD permanent et de nos éléments (la flèche de bord aussi hors du fil des kills, du
  chat, des votes) — un repère au sol reste sur son vrai point et peut croiser une zone passagère ;
  étiquettes de la minimap : jamais sur les boutons de coin (`MM_CORNER_BUTTONS`) ;
  `overlay_render.render_preview` et `ui_preview.compose` utilisent la même disposition.
* Radar (mode « radar », hérité) : sa position ne change pas (au-dessus de la minimap, donc sur les
  portraits / votes) ; il est un obstacle pour les autres éléments.


## 23. Coups décisifs (`game_changers.py`) : bibliothèque classée, voix, achats ennemis, juge de VALEUR

Retour réel (« les conseils sont nuls ») : la carte affichait des généralités (balise ×4 en 3 min,
« 4,5 sbires/min », « Achète Couperet noir » avec 900 PO). Désormais :

* **Bibliothèque** (`game_changers.RULES`, branchée dans `macro.evaluate` : même planificateur, donc
  carte = bannière = flèche) : `gc_level` (niveau 2/3/6 atteint avant / après l'adversaire de voie,
  fenêtre jusqu'à ce qu'il rattrape ; ligne d'ultime par champion `ULT`), `gc_jungler_far` (jungler vu
  ≤ 10 s de l'autre côté → « Joue agressif : Lee Sin est en bas »), `gc_jungler_unseen` (invisible ≥ 30 s
  + vague poussée, pas si l'or appelle un retour), `gc_baron_setup` (≥ 20:00, +2,5k d'or), `gc_fed_defense`
  (dans la boutique, ennemi 4+/… : composant d'armure / RM), `gc_facecheck`, `gc_enemy_buys`
  (`EnemyBuys` : achats publics des ennemis ; effets via `itemization.item_effects` : 1er gros objet de
  mon adversaire → recule, stase → « Fais utiliser son Sablier… avant ton combo » (jamais de minuteur),
  anti-soin contre mes soins, 2+ ennemis résistants → pénétration, ennemi nourri défensif → frapper le
  plus fragile).
* **Voix** (`voice_for`, clés `gc:big:` / `gc:lane:`) : phrases statiques ≤ 38 caractères pré-générées
  (`voice_phrases` → `tts_neural.static_phrases`) ; `big` (objectif maintenant, Baron) débutant +
  intermédiaire, `lane` débutant ; abandonnées en combat / concentration / mort (voix seule), budget
  2/min (écart 20 s pour `gc:`), un sujet ≤ 1 fois / 60 s (`VOICE_TOPIC_S`).
* **Cohérence** : un appel ne démarre que si la carte est stable depuis 5 s (`CARD_SETTLE_S`, sauf
  urgences / coups de génie) et ne contredit pas une ligne apparue < 10 s ; il prend la carte dès son
  début ; la ligne d'objectif ne passe plus devant l'appel actif ; un compte à rebours n'est jamais figé ;
  `phase:mid` muet si un objectif arrive ; « COMBAT » / « COMBAT PERDU » (jamais « GANK ») en 5v5 ;
  rappel en cours (`engine.note_recall`) : « Annule ton rappel : Lux peut l'interrompre » ou rien ;
  sous ma tour : « Reste sous ta tour », jamais « Recule vers ta tour ».
* **Juge** (`tools/ux_replay.py`, 17 scénarios × 4 niveaux) : règles de VALEUR `valeur:moment-manqué`,
  `voix:moment-manqué`, `valeur:générique`, `valeur:statistique`, `voix:budget`, `voix:sujet-répété`,
  `voix:longue`, `incohérence:carte-bandeau`, `état:objectif-périmé`, `valeur:leçon-mort`,
  `état:recule-pendant-rappel` ; scénarios `jungler_bot`, `laner_recall`, `baron_3v0`, `fed_enemy`,
  `recall_tower`, `enemy_buys`. Tests : `tests/test_game_changers.py`.

## 24. Ressources oubliées (`resources_coach.py` + `hud_abilities.py`)

Demande réelle : « capture tout, y compris les sorts que j'oublie d'utiliser ». Uniquement MON état
(politique Riot) : API Live Client (`activePlayer` : niveaux de compétences, niveau, or, PV ; mes objets
avec leur case et leur pile `item_slots` / `item_counts`, mes sorts d'invocateur) et MA barre de sorts à
l'écran. Jamais un temps de recharge ennemi, jamais une entrée envoyée au jeu.

* **Lecteur de barre** (`hud_abilities.AbilityBarReader`) : géométrie des cases mesurée sur de vraies
  captures 2000×1125 (`SLOTS`, décalages depuis le portrait de `hud_reader`), ajustée une fois par taille de
  fenêtre (échelle + décalage : chaque case est un carré, on maximise le côté le plus faible ; grossier sur
  gradient flouté puis fin ; ~170 ms, sur un fil à part). Lecture d'un petit patch ≤ 2 Hz (~0,65 ms) :
  recharge = bleu plat saturé (H 99-108, S ≥ 160) sur ≥ 5 % de l'icône, prêt = cadre doré, charges de la
  balise = chiffre blanc du coin (1 étroit / 2 large ; 0 si compte à rebours au centre), `valid` = bords
  des cases encore visibles (HUD masqué → rien). Mesure : 84/84 états Q W E R D F annotés à la main sur
  14 vraies images (10 scènes distinctes, 4 tailles de fenêtre, vivant / mort, vidéo floue) ; charges de
  la balise 13/14 (1 illisible en 1280×720 JPEG, 0 fausse).
* **Règles** (une ligne courte, verbe en tête, routée par le présentateur, type `resource` : PANEL, en
  combat seulement si urgence ≥ 2, jamais pendant un gank ; pas de voix) : point de compétence non dépensé
  (« Monte ton R : tu es niveau 6 », R d'abord), leçon de mort (« Utilise ton Soin avant de mourir : il
  était prêt », Flash / Soin / Barrière / Fantôme / Fatigue / Purge, actifs défensifs, R défensif ; après la
  ligne de cause de mort), potion à < 40 % PV, R prêt au début d'un combat, Téléportation prête pendant un
  combat de l'équipe loin de moi, or ≥ 1 500 pendant 60 s (coordonné avec les rappels existants : un seul
  message de retour par trajet), balise de contrôle gardée 3 min, 2 charges de balise pendant 45 s, balise
  rouge pour le support quête finie. Hors point de compétence et leçons de mort : débutant / intermédiaire.
* **Affichage confirmé** : une ligne compte comme montrée seulement quand la carte l'affiche (un autre
  système peut écrire la carte au même tick) ; sinon elle est reproposée 12 s plus tard. Une nouvelle ligne
  attend que la carte actuelle ait été lue 5,5 s.
* **Juge** : scénarios `skill_r6`, `death_heal`, `trinket_full` ; tests `tests/test_hud_abilities.py`
  (vraies bandes HUD `tests/fixtures/hud_bar/`, HUD synthétique, coût) et `tests/test_resources_coach.py`.
