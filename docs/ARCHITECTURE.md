# TreeAI Coach — architecture & contrats entre modules

> Document de référence **obligatoire** pour toute personne (ou agent) qui code un module.
> Les signatures ci-dessous sont des contrats : ne les changez pas sans mettre à jour ce fichier.
> Langue du code : anglais (identifiants, docstrings). Langue de l'interface et des phrases vocales : **français**.

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

```
treeaicoach/                    package Python (runtime, embarqué dans le .exe)
  __init__.py                   __version__ = "1.0.0", APP_NAME = "TreeAI Coach"
  __main__.py                   `python -m treeaicoach` -> main.main()
  main.py                       CLI + point d'entrée (.exe)
  paths.py                      chemins ressources / données utilisateur
  config.py                     Config (dataclass) + load/save JSON
  logging_setup.py              logs rotatifs
  geometry.py                   zones de la carte, distances, libellés FR
  live_client.py                Live Client Data API (parse + client HTTP)
  champions.py                  base des champions + icônes (+ skins en cache)
  render.py                     rendu de minimaps (partagé : entraînement, démo, selftest)
  capture.py                    capture écran (mss), fenêtre du jeu, DPI
  minimap_locator.py            localisation automatique de la minimap à l'écran
  detector.py                   détecteur ONNX + détecteur classique de secours + décodage
  identifier.py                 identification des champions détectés
  tracker.py                    suivi temporel des champions
  alerts.py                     types d'alertes, phrases FR, anti-spam (throttler)
  gank.py                       logique de détection des ganks
  voice.py                      synthèse vocale (SAPI Windows) + bips
  engine.py                     boucle principale (threads)
  demo.py                       partie simulée (démo + test de bout en bout)
  selftest.py                   autotest (--selftest), utilisé par la CI Windows
  calibration.py                calibration manuelle de la minimap (Tkinter)
  ui.py                         interface Tkinter
  assets/                       ressources embarquées
    manifest.json               (généré par tools/fetch_assets.py)
    minimap/2dlevelminimap_<variant>_baron<n>.png, fogofwaroverlay*.png
    icons/champions/<Alias>.png (64x64 RGBA) + index.json
    icons/minimap/*.png, icons/pings/*.png
    model/minimap_detector.onnx + model/model_meta.json   (généré par training/export_onnx.py)
    selftest/*.png + selftest/labels.json                  (généré par training/evaluate.py)
    sounds/*.wav                                           (généré par voice.py au 1er besoin si absent)
training/                       entraînement (PyTorch, hors .exe)
  synth.py  dataset.py  model.py  train.py  export_onnx.py  evaluate.py
  cache/                        (gitignored) icônes de tous les skins
tools/fetch_assets.py           téléchargement des ressources officielles
tests/                          pytest (tourne sous Linux ET Windows)
packaging/                      PyInstaller (.spec, build_exe.bat, icon.ico)
.github/workflows/build-windows.yml
```

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
    show_preview: bool = False
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
* Pas d'alerte si je suis mort, dans ma base (fontaine), si `game` indique un mode ≠ Faille, ou si ma position est inconnue depuis > 3 s.
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
class ObjectiveState: name: str (FR: "Dragon", "Baron", "Héraut", "Larves", "Atakhan", "Dragon ancestral")
                      next_spawn: float | None (game_time) ; alive: bool ; source: "schedule" | "event"
class ObjectiveTimers:
    def __init__(self, cfg: Config, schedule: dict | None = None)   # schedule par défaut = OBJECTIVE_SCHEDULE (modifiable)
    def update(self, game: GameInfo | None, t: float) -> list[Alert]   # annonce à 60 s et 20 s avant l'apparition (configurable)
    def states(self) -> list[ObjectiveState]
    def reset(self) -> None
OBJECTIVE_SCHEDULE = {  # valeurs par défaut, surchargeables dans assets/objectives.json
  "dragon":  {"first": 300, "respawn": 300},  "elder": {"respawn": 360},
  "grubs":   {"first": 360, "respawn": None, "despawn": 840}, "herald": {"first": 900, "despawn": 1185},
  "atakhan": {"first": 1200}, "baron": {"first": 1500, "respawn": 360}
}
```
Événements Live Client utilisés : `DragonKill` (`DragonType` = "Elder" → elder), `BaronKill`, `HeraldKill`, `HordeKill`
(larves), `AtakhanKill`, `GameStart`. Aucune annonce si le mode n'est pas la Faille, ou si l'option est désactivée.
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
ennemis vus à < 0.2 dans les 8 s avant, jungler impliqué ?, alerte donnée dans les 12 s avant ? → « alerte ignorée » /
« mort sans alerte »), **ganks subis** (alertes DANGER + issue : mort / survie), **jungler ennemi** (1re apparition, répartition
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
