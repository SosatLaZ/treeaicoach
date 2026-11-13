# STABLE (5.0) — « jouer sans que ça bugue » : ce que le cœur offre aux autres modules

Tout appel entre modules reste gardé (`if T.OnBags then … end`). Rien ici n'appelle une fonction protégée.

## Réagir aux rafales du jeu (jamais dans l'image de l'événement, une tournée d'abonnés par image, budget 1,5 ms)
| API | Quand `fn(owner, info)` est appelée | `info` |
|---|---|---|
| `T:OnBags(owner, fn[, {combat=true}])` | ≥ 0,3 s après le dernier BAG_UPDATE / BAG_UPDATE_DELAYED / UNIT_INVENTORY_CHANGED(player) / PLAYER_EQUIPMENT_CHANGED (≤ 1 s après le premier) | `bags[sac]`, `delayed`, `equip`, `slots[n]`, `bank`, `gen` |
| `T:OnQuestLog(owner, fn)` | ≥ 0,3 s après la rafale, **seulement si le journal a vraiment changé** | `structure` (quête prise / rendue / retirée / finie, objectif rempli), `counts`, `accepted[id]`, `removed[id]`, `turnedIn[id]`, `watch`, `watchSelf`, `sig` |
| `T:OnRoster(owner, fn)` | ≥ 0,5 s après, seulement si la composition a changé | `n`, `raid` |
| `T:OnItemInfo(owner, fn)` | ≥ 0,5 s après les GET_ITEM_INFO_RECEIVED (≤ 2 s) | `items[id]` (60 premiers), `many`, `n` |
| `T:OnPlates(owner, fn, gone)` | `fn(owner, units)` ≥ 0,1 s après l'apparition (`units[jeton] = true`, encore présentes) ; `gone(owner, jeton)` **tout de suite** au départ (O(1) : cacher son cadre) | — |
| `T:OnUnitTip(owner, fn[, {players=true, combat=true}])` | `fn(owner, unit, name, guid)` → `texte, r, g, b` : une fois par unité (réponse gardée 3 s, jetée si journal / sacs changent) | — |

Sans `{combat = true}` les flux sacs / journal / groupe / objets attendent la fin du combat (infos fusionnées).
Un module coupé ou mis en pause n'est pas appelé. `T:PlateCount()`, `T:PlateUnits()`.

## Événements bruts : ce qui a changé sans toucher aux modules
- `QUEST_LOG_UPDATE`, `UNIT_QUEST_LOG_CHANGED("player")` : remis seulement si l'état du journal (quêtes, finies,
  objectifs, compteurs, textes) a changé depuis LEUR dernière remise ; `QUEST_WATCH_UPDATE` toujours.
  `T:QuestGen()` n'avance que sur un vrai changement (les relevés partagés ne se refont plus à chaque image).
- `GROUP_ROSTER_UPDATE` / `PARTY_MEMBERS_CHANGED` : remis seulement si membres / connecté / niveau ont changé.
- `BAG_UPDATE_DELAYED`, `UNIT_INVENTORY_CHANGED` : **non filtrés** (invalider une mémoire doit rester immédiat).
- `UNIT_*` : `mod:RegisterUnitEvent(ev, "group" | "self" | "focus" | unité, nil, fn)` — une unité que personne ne veut ne
  coûte qu'une lecture de table. `CHAT_MSG_ADDON` d'un autre préfixe : jeté avant tout appel.
- `T.API.QuestieMemo(source, "Get", source.Get)` : une lecture QuestieDB par (id, champ), partagée. Tables en lecture seule.

## Mesure en jeu et filet de sécurité
- `T:Guard(owner, "nom", fn)` : enveloppe d'un crochet (infobulle, OnUpdate, hooksecurefunc) → compté dans `/tai perf`,
  `/tai pics`, muet si le module est coupé.
- `T.Spikes` : tout appel réparti > 8 ms (temps propre) et toute image > 12 ms de TreeAI sont notés (module, travail,
  ms, combat, barres de nom, groupe) : `TreeAIDB.diag.spikes` (30 plus gros + 30 derniers), `/tai pics`, `/tai erreurs`,
  TreeAI-erreurs.txt. Sonde d'API du client : `TreeAIDB.diag.api`.
- `T.Watchdog` : 5 erreurs / min, ou ≥ 4 pics totalisant ≥ 200 ms / min → module en pause jusqu'au /reload (une ligne
  « Fonction X mise en pause : trop lente ici »), `T.Watchdog.WakeAll()`. Fenêtre, Erreurs, Autotest : jamais coupés.
- `T:Spread(owner, fn, …)` : un travail lourd par image ; le cœur reporte aussi tout rappel `T:After` ≥ 0,25 s quand
  l'image a déjà dépensé 1,5 ms.
- `mod.autoIdleSafe = true` : au calme (aucun événement, joueur immobile) ton `:AutoRefresh()` n'est pas appelé (cycle
  complet toutes les 30 s) ; les modules qui ne le déclarent pas gardent un cycle étiré ×3.

## Guide de quêtes
`T:QuestGuideOn()`, `T:SetQuestGuide(on)`, message `QUEST_GUIDE_CHANGED(on)`, `/tai quetes on|off`, `TreeAIDB.questGuide`.
Coupé : `T.QUEST_GUIDE` (Tracker, Todo, Planner, Route, PathDraw, ZonePlan, ZoneWatch, QuestBrain, QuestPins, Watch,
Abandon, Horizon, Region) sont arrêtés (événements, minuteurs, cadres) ; Quêtes garde le relevé du journal sans conseil ;
boussole sans cible de quête ; pilote sans règle de quête ; plus rien n'est envoyé à Shortest Path Forever.

## Simulation
`lua5.1 tests/wow/session_sim.lua --crowd [--profile sur|tout] [--phases ville,butin] [-v] [--fine]` : login, ville
(40 barres de nom), hub, bg (combat), butin, quetes, repos. Une image = les événements du jeu + minuteurs + OnUpdate ;
coût estimé du jeu derrière les appels de widgets (µs) et les lectures QuestieDB (12 / 30 µs) ajouté. Limite : 200 kinstr
(4 ms) par image, ≤ 8 kinstr / s au repos, 0 pic relevé par `T.Spikes`.
