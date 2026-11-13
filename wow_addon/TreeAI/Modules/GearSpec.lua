-- TreeAI — Équipement, partie « Spé » (agent GEAR 5.0). Fait partie du module Gear (pas un module de plus).
-- « des fois tu conseilles mal, faut que tu sois mieux renseigné sur la spé que j'ai » : les poids d'équipement
-- dépendent de l'arbre de talents. Avant 5.0 la spé venait de GetTalentTabInfo, qui N'EXISTE PAS sur WoW Forever
-- (d'où « TreeAI ne peut pas lire tes talents sur Forever » et des poids « par défaut » pour tout le monde).
-- Cause racine : Forever range les talents dans un arbre C_Traits à 3 GROUPES (un par arbre classique). La fenêtre
-- de talents de Blizzard (Blizzard_PlayerSpells/Camelot/ClassTalents) lit le nom et les points de chaque arbre par
--   C_ClassTalents.GetActiveConfigID() (ou C_SpecializationInfo.GetCombatConfigIDForSpecGroup(groupe actif))
--   → C_Traits.GetConfigInfo(config).treeIDs[1] → C_Traits.GetGroupDisplayInfoByTreeID(arbre) (displayName)
--   → C_Traits.GetGroupCurrencyInfo(config, groupes) (currencyInfos[1].spent = points dépensés dans l'arbre).
-- On lit donc exactement ce que la fenêtre du jeu affiche. Ordre des lectures (chacune sous pcall, jamais d'erreur) :
--   1. « api »    : GetTalentTabInfo (Classic Era) · groupes C_Traits (Forever) · nœuds C_Traits (rang par talent)
--                   · GetSpecialization (clients à spécialisations) ;
--   2. « spells » : sorts de talent connus (Data/TalentSpells.lua : tous les rangs de chaque talent, tables du
--                   client) par C_SpellBook.IsSpellKnown / IsPlayerSpell / IsSpellKnown, puis noms du grimoire
--                   (Visée → Précision, Intimidation / Courroux bestial → Maîtrise des bêtes…) ;
--   3. « manual » : le joueur choisit (3 boutons, page Équipement) ; demandé UNE fois, modifiable toujours.
-- T.Gear.Spec() → { class, tree, name, source = "api"|"spells"|"manual", conf, points } (contrat ROUND 11).
-- Coût : la lecture légère (≤ 6 appels) se fait à la demande après un événement de talents ; les lectures longues
-- (nœuds, sorts) passent par T:Spread, jamais dans l'image de l'événement ; rien au repos. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end
local T = _G.TreeAI
local M = T and T.Gear
if not (T and M) then return end
local API = T.API or {}
local G = _G

local floor, max, min = math.floor, math.max, math.min
local function pass(ok, ...) if ok then return ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw((...), "GearSpec") end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function secret(v) local f = g("issecretvalue") return v ~= nil and type(f) == "function" and try(f, v) and true or false end
local function pub(v) if v == nil or secret(v) then return nil end return v end
local function num(v) v = pub(v) if type(v) == "number" then return v end return nil end
local function str(v) v = pub(v) if type(v) == "string" and v ~= "" then return v end return nil end
local function tbl(v) v = pub(v) if type(v) == "table" then return v end return nil end
local function level() return num(try(g("UnitLevel"), "player")) or 1 end
local function class() return T.PlayerClass and T:PlayerClass() or nil end

local SP = { dirty = true, deep = nil, deepAt = -1, probe = {} }
M._spec = SP

-- noms d'arbre de repli (les noms du jeu ou de Data/Talents.lua passent avant)
local TREE_FR = {
  HUNTER = { "Maîtrise des bêtes", "Précision", "Survie" }, WARRIOR = { "Armes", "Fureur", "Protection" },
  ROGUE = { "Assassinat", "Combat", "Finesse" }, MAGE = { "Arcanes", "Feu", "Givre" },
  PRIEST = { "Discipline", "Sacré", "Ombre" }, WARLOCK = { "Affliction", "Démonologie", "Destruction" },
  DRUID = { "Équilibre", "Combat farouche", "Restauration" }, PALADIN = { "Sacré", "Protection", "Vindicte" },
  SHAMAN = { "Élémentaire", "Amélioration", "Restauration" },
}
-- clients à spécialisations : n° de spécialisation → arbre classique (le druide en a 4)
local SPEC_TO_TREE = { DRUID = { 1, 2, 2, 3 } }
local SOURCE_FR = { api = "lue dans tes talents", spells = "déduite de tes sorts", manual = "choisie par toi" }
M.SPEC_SOURCE_FR, M.SPEC_TREES = SOURCE_FR, TREE_FR

local ACC = { ["é"] = "e", ["è"] = "e", ["ê"] = "e", ["ë"] = "e", ["à"] = "a", ["â"] = "a", ["î"] = "i", ["ï"] = "i",
  ["ô"] = "o", ["û"] = "u", ["ù"] = "u", ["ü"] = "u", ["ç"] = "c", ["É"] = "e", ["È"] = "e", ["Ê"] = "e", ["À"] = "a",
  ["Â"] = "a", ["Î"] = "i", ["Ô"] = "o", ["Û"] = "u", ["Ç"] = "c", ["œ"] = "oe", ["Œ"] = "oe", ["’"] = "" }
--- nom comparable : sans accents, sans casse, lettres et chiffres seulement (« Equilibre » = « Équilibre »)
local function norm(s)
  s = tostring(s or ""):gsub("[\195\197\226][\128-\191]+", function(c) return ACC[c] or c end)
  return (s:lower():gsub("[^%w\128-\255]", ""))
end
M.NormName = norm

--- noms des 3 arbres de la classe : données du client (Data/Talents.lua) sinon repli
function M.TreeNames(cls)
  cls = cls or class()
  local D = T.Data and T.Data.Talents
  local trees = D and D.trees and D.trees[cls]
  local out = {}
  for i = 1, 3 do
    out[i] = (TREE_FR[cls] and TREE_FR[cls][i]) or (trees and trees[i] and trees[i].name) or ("Arbre " .. i)
  end
  return out
end
local function treeIndex(cls, name)
  local want = norm(name)
  if want == "" then return nil end
  local D = T.Data and T.Data.Talents
  local trees = D and D.trees and D.trees[cls]
  for i = 1, 3 do
    if (trees and trees[i] and norm(trees[i].name) == want) or (TREE_FR[cls] and norm(TREE_FR[cls][i]) == want) then return i end
  end
end

---------------------------------------------------------------------------------------------------
-- Index des sorts de talent (Data/TalentSpells.lua) : sort → arbre × 1000 + n° du talent × 10 + rang
---------------------------------------------------------------------------------------------------
local idxCls, idx, groups
local function spellIndex(cls)
  if idxCls == cls and idx then return idx, groups end
  idxCls, idx, groups = cls, {}, {}
  local D = T.Data and T.Data.TalentSpells
  local rows = D and D[cls]
  for tab = 1, 3 do
    groups[tab] = {}
    local n = 0
    for grp in tostring(rows and rows[tab] or ""):gmatch("[^;]+") do
      n = n + 1
      local ids, r = {}, 0
      for id in grp:gmatch("%d+") do
        r = r + 1
        ids[r] = tonumber(id)
        idx[ids[r]] = tab * 1000 + n * 10 + r
      end
      groups[tab][n] = ids
    end
  end
  return idx, groups
end
M.TalentSpellIndex = spellIndex
local function talentRow(cls, tab, n)
  local D = T.Data and T.Data.Talents
  local t = D and D.trees and D.trees[cls] and D.trees[cls][tab]
  return t and t.t and t.t[n]   -- { rang, colonne, rangs max, nom }
end

---------------------------------------------------------------------------------------------------
-- 1. « api » : ce que le jeu sait de tes points
---------------------------------------------------------------------------------------------------
local function pack(...) return { n = select("#", ...), ... } end
--- Classic Era : GetTalentTabInfo(onglet) → nom, icône, points… (ou id, nom, description, icône, points…)
local function classicTabs()
  local f, nf = g("GetTalentTabInfo"), g("GetNumTalentTabs")
  if type(f) ~= "function" then return nil end
  local n = num(try(nf)) or 3
  local points, names, seen = {}, {}, false
  for tab = 1, min(n, 3) do
    local r = pack(pcall(f, tab))
    if r[1] then
      local name, pts
      if type(pub(r[2])) == "string" then name, pts = r[2], num(r[4])
      elseif type(pub(r[3])) == "string" then name, pts = r[3], num(r[6]) end
      if name and pts and pts >= 0 and pts <= 71 then points[tab], names[tab], seen = pts, name, true end
    end
  end
  if not seen then return nil end
  for tab = 1, 3 do points[tab] = points[tab] or 0 end
  return points, names
end

--- Forever : configuration et arbre C_Traits actifs (ceux que la fenêtre de talents du jeu affiche)
local function traitConfig()
  local TR, CT, SI = tbl(g("C_Traits")), tbl(g("C_ClassTalents")), tbl(g("C_SpecializationInfo"))
  if not (TR and TR.GetConfigInfo) then return nil end
  local cfg = CT and num(try(CT.GetActiveConfigID))
  if not cfg and SI and SI.GetCombatConfigIDForSpecGroup then
    cfg = num(try(SI.GetCombatConfigIDForSpecGroup, num(try(SI.GetActiveSpecGroup)) or 1))
  end
  if not cfg then return nil end
  local info = tbl(try(TR.GetConfigInfo, cfg))
  local trees = info and tbl(info.treeIDs)
  local tree = trees and num(trees[1])
  if not tree then return nil end
  return TR, cfg, tree
end
--- Forever : points par arbre = monnaie dépensée par groupe de nœuds (l'en-tête de chaque arbre dans le jeu)
local function traitGroups(cls)
  local TR, cfg, tree = traitConfig()
  if not (TR and TR.GetGroupDisplayInfoByTreeID and TR.GetGroupCurrencyInfo) then return nil end
  local disp = tbl(try(TR.GetGroupDisplayInfoByTreeID, tree))
  if not disp or #disp < 2 then return nil end
  local ids = {}
  for i, d in ipairs(disp) do ids[i] = type(d) == "table" and num(d.groupID) or -1 end
  local cur = tbl(try(TR.GetGroupCurrencyInfo, cfg, ids))
  if not cur then return nil end
  local spent = {}
  for _, gi in ipairs(cur) do
    local ci = type(gi) == "table" and tbl(gi.currencyInfos)
    local c1 = ci and tbl(ci[1])
    if c1 and num(gi.traitNodeGroupID) then spent[gi.traitNodeGroupID] = num(c1.spent) or 0 end
  end
  -- chaque groupe → un des 3 arbres : par son nom (jeu = données du client), sinon par son rang d'affichage
  local points, names, used, byOrder, matched = {}, {}, {}, {}, 0
  for i, d in ipairs(disp) do
    if type(d) == "table" and ids[i] ~= -1 then
      local t = treeIndex(cls, str(d.displayName))
      if t and not used[t] then
        used[t], matched = true, matched + 1
        points[t], names[t] = spent[ids[i]] or 0, str(d.displayName)
      end
      byOrder[#byOrder + 1] = { o = num(d.orderIndex) or i, i = i, name = str(d.displayName), spent = spent[ids[i]] or 0 }
    end
  end
  if matched < 2 then
    if #byOrder ~= 3 then return nil end
    table.sort(byOrder, function(a, b) if a.o ~= b.o then return a.o < b.o end return a.i < b.i end)
    points, names = {}, {}
    for t = 1, 3 do points[t], names[t] = byOrder[t].spent, byOrder[t].name end
  end
  for t = 1, 3 do points[t] = points[t] or 0 end
  return points, names
end

local nodeLoc = {}   -- nœud → arbre × 1000 + n° du talent × 10 (fixe pour la session)
--- Forever : rang de chaque talent, nœud par nœud (sort de la définition → arbre / rang / colonne des données)
local function traitNodes(cls)
  local TR, cfg, tree = traitConfig()
  if not (TR and TR.GetTreeNodes and TR.GetNodeInfo) then return nil end
  local nodes = tbl(try(TR.GetTreeNodes, tree))
  if not nodes or #nodes == 0 then return nil end
  local sidx = spellIndex(cls)
  local byName
  local ranks, points, found = {}, { 0, 0, 0 }, 0
  for _, nodeID in ipairs(nodes) do
    local ni = tbl(try(TR.GetNodeInfo, cfg, nodeID))
    if ni then
      local loc = nodeLoc[nodeID]
      if loc == nil then
        loc = false
        local ae = tbl(ni.activeEntry)
        local entry = (ae and num(ae.entryID)) or (tbl(ni.entryIDs) and num(ni.entryIDs[1]))
        local ei = entry and TR.GetEntryInfo and tbl(try(TR.GetEntryInfo, cfg, entry))
        local di = ei and num(ei.definitionID) and TR.GetDefinitionInfo and tbl(try(TR.GetDefinitionInfo, ei.definitionID))
        local spell = di and (num(di.spellID) or num(di.overriddenSpellID))
        local code = spell and sidx[spell]
        if not code and spell and API.SpellName then   -- sort inconnu des données : par son nom (même client, même langue)
          if not byName then
            byName = {}
            local D = T.Data and T.Data.Talents
            for tab = 1, 3 do
              local t = D and D.trees and D.trees[cls] and D.trees[cls][tab]
              for n, row in ipairs(t and t.t or {}) do byName[norm(row[4])] = tab * 1000 + n * 10 end
            end
          end
          code = byName[norm(str(di.overrideName) or API.SpellName(spell))]
        end
        if code then loc = floor(code / 10) * 10 end
        nodeLoc[nodeID] = loc
      end
      if loc then
        local rank = num(ni.ranksPurchased) or num(ni.activeRank) or num(ni.currentRank) or 0
        local tab, n = floor(loc / 1000), floor(loc % 1000 / 10)
        local row = talentRow(cls, tab, n)
        found = found + 1
        if row and rank > 0 then
          ranks[tab .. ":" .. row[1] .. ":" .. row[2]] = rank
          points[tab] = points[tab] + rank
        end
      end
    end
  end
  if found < 6 then return nil end   -- trop peu de nœuds reconnus : on ne conclut rien
  return points, ranks
end

--- clients à spécialisations (une spé choisie parmi 2 à 4) ; sur Forever il n'y en a qu'UNE par classe → rien
local function retailSpec(cls)
  local SI = tbl(g("C_SpecializationInfo"))
  local n = num(try(g("GetNumSpecializations")))
  if n and n < 2 then return nil end
  local i = num(try(SI and SI.GetSpecialization)) or num(try(g("GetSpecialization")))
  if not i or i < 1 or i > 4 then return nil end
  local _, name = try((SI and SI.GetSpecializationInfo) or g("GetSpecializationInfo"), i)
  name = str(name)
  if not name then return nil end
  if not n and treeIndex(cls, name) == nil and (SI and SI.GetCombatConfigIDForSpecGroup) then return nil end   -- Forever : « spé » = la classe
  local map = SPEC_TO_TREE[cls]
  return (map and map[i]) or treeIndex(cls, name) or min(i, 3), name
end

---------------------------------------------------------------------------------------------------
-- 2. « spells » : sorts de talent connus
---------------------------------------------------------------------------------------------------
local function knownFn()
  local SB = tbl(g("C_SpellBook"))
  if SB and type(SB.IsSpellKnown) == "function" then return SB.IsSpellKnown end
  local f = g("IsPlayerSpell")
  if type(f) == "function" then return f end
  f = g("IsSpellKnown")
  if type(f) == "function" then return f end
end
--- indices par arbre : points (rang le plus haut connu de chaque talent) + profondeur ; puis noms du grimoire
local function spellEvidence(cls)
  local _, grp = spellIndex(cls)
  local ev, any = { 0, 0, 0 }, false
  local f = knownFn()
  if f then
    for tab = 1, 3 do
      for _, ids in ipairs(grp[tab] or {}) do
        for r = #ids, 1, -1 do
          if pub(try(f, ids[r])) == true then ev[tab], any = ev[tab] + r, true break end
        end
      end
    end
  end
  if not any and API.SpellBookSpells then   -- sorts actifs de talent dans le grimoire, par leur nom
    local D = T.Data and T.Data.Talents
    local names = {}
    for tab = 1, 3 do
      local t = D and D.trees and D.trees[cls] and D.trees[cls][tab]
      for _, row in ipairs(t and t.t or {}) do names[norm(row[4])] = tab * 10 + row[1] end
    end
    local seen = {}
    for _, s in ipairs(try(API.SpellBookSpells) or {}) do
      local code = names[norm(str(s.name))]
      if code and not seen[code] then
        seen[code] = true
        ev[floor(code / 10)], any = ev[floor(code / 10)] + (code % 10), true   -- un talent profond pèse plus
      end
    end
  end
  if not any then return nil end
  return ev
end

---------------------------------------------------------------------------------------------------
-- Décision
---------------------------------------------------------------------------------------------------
local function best3(p)
  local bi, bv, second = nil, -1, -1
  for i = 1, 3 do
    local v = tonumber(p[i]) or 0
    if v > bv then second, bi, bv = bv, i, v elseif v > second then second = v end
  end
  return bi, bv, max(0, second)
end
local function charDB() return type(M.char) == "table" and M.char or nil end

--- lecture légère (quelques appels) : points par arbre d'après le jeu → spec « api » ou nil
local function readApi(cls)
  local probe = SP.probe
  local points, names = classicTabs()
  probe.classic = points and table.concat(points, "/") or "absent"
  if not points then
    points, names = traitGroups(cls)
    probe.groups = points and table.concat(points, "/") or "absent"
  end
  if points then
    local t, v, second = best3(points)
    local nm = M.TreeNames(cls)
    if v <= 0 then return { class = cls, tree = nil, source = "api", conf = 1, points = points, none = true } end
    return { class = cls, tree = t, name = (names and names[t]) or nm[t], source = "api", points = points,
      conf = (v > second) and 1 or 0.5 }
  end
  local t, name = retailSpec(cls)
  probe.spec = t and (t .. ":" .. tostring(name)) or "absent"
  if t then return { class = cls, tree = t, name = name, source = "api", conf = 1 } end
  return nil
end
--- lectures longues (nœuds C_Traits, puis sorts connus) : faites hors de l'image de l'événement, gardées en mémoire
local function readDeep(cls)
  local probe = SP.probe
  local points, ranks = traitNodes(cls)
  probe.nodes = points and table.concat(points, "/") or "absent"
  if points then
    local t, v, second = best3(points)
    SP.ranks = { ranks = ranks, points = points }
    if v <= 0 then return { class = cls, tree = nil, source = "api", conf = 1, points = points, none = true } end
    return { class = cls, tree = t, name = M.TreeNames(cls)[t], source = "api", points = points, conf = (v > second) and 1 or 0.5 }
  end
  local ev = spellEvidence(cls)
  probe.spells = ev and table.concat(ev, "/") or "absent"
  if ev then
    local t, v, second = best3(ev)
    if v > 0 and v > second then
      return { class = cls, tree = t, name = M.TreeNames(cls)[t], source = "spells", evidence = ev,
        conf = max(0.3, min(0.9, (v - second) / v)) }
    end
  end
  return false   -- rien de sûr : on demandera au joueur
end

local function remember(s)
  local c = charDB()
  if c and s and s.tree and s.source ~= "manual" then c.specSeen = { tree = s.tree, source = s.source, name = s.name } end
end
local function manual(cls)
  local c = charDB()
  local t = c and tonumber(c.specManual)
  if t and t >= 1 and t <= 3 then
    return { class = cls, tree = t, name = M.TreeNames(cls)[t], source = "manual", conf = 1 }
  end
end
local function scheduleDeep(cls)
  if SP.deepQueued then return end
  SP.deepQueued = true
  local function run()
    SP.deepQueued = false
    if T.InCombat and T:InCombat() then SP.deepWanted = true return end
    local d = readDeep(cls)
    SP.deep, SP.deepCls = d, cls
    local before = SP.cur and SP.cur.tree
    SP.dirty = true
    local s = M.Spec()
    if s and s.tree ~= before then M.OnSpecChanged() elseif M.ResetWeights then M.ResetWeights() end
    -- la ligne « Spé : … » (lecture en cours → lue / à choisir) et la question unique
    if M.plan and M.Publish then M._pubSig = nil pcall(M.Publish, M, M.plan) elseif M.AskSpecOnce then M.AskSpecOnce() end
  end
  if T.Spread then T:Spread(M, run) elseif T.After then T:After(0.5, run) else run() end
end

--- LA spé du joueur : { class, tree (1..3 ou nil), name, source = "api"|"spells"|"manual", conf, points }.
--- tree = nil : pas encore de talents (niveau < 10 / aucun point) ou spé inconnue (ask = true → on la demande).
function M.Spec()
  if not SP.dirty and SP.cur then return SP.cur end
  local cls = class()
  if not cls then return { class = nil, tree = nil, source = "api", conf = 0 } end
  local s = manual(cls)
  if not s then
    s = readApi(cls)
    if not s then
      if SP.deepCls ~= cls or SP.deep == nil then
        scheduleDeep(cls)
        -- en attendant la lecture longue : la dernière spé connue de ce personnage (jamais une invention)
        local c = charDB()
        local seen = c and type(c.specSeen) == "table" and c.specSeen
        if seen and tonumber(seen.tree) then
          s = { class = cls, tree = tonumber(seen.tree), name = M.TreeNames(cls)[tonumber(seen.tree)],
            source = seen.source == "spells" and "spells" or "api", conf = 0.5, stale = true }
        else
          s = { class = cls, tree = nil, source = "api", conf = 0, pending = true }
        end
      elseif SP.deep then
        s = SP.deep
      else
        s = { class = cls, tree = nil, source = "manual", conf = 0, ask = level() >= 10 }
      end
    end
  end
  if s.none and level() < 10 then s.early = true end
  s.class = cls
  remember(s)
  if s.tree and s.source ~= "manual" and M.Clear then M:Clear("spec") end   -- lue : la question n'a plus lieu d'être
  SP.cur = s
  SP.dirty = false   -- en attente de la lecture longue : ce résultat sert tel quel ; c'est elle qui redemandera
  return s
end
--- n° de l'arbre (1..3) ou nil : pour les tables de poids
function M.SpecTree() local s = M.Spec() return s and s.tree or nil end

--- « Spé : Précision (lue dans tes talents) » — toujours affichée, avec sa source
function M.SpecText(s)
  s = s or M.Spec()
  if not s or not s.class then return "Spé : inconnue" end
  if s.tree then
    local pts = s.points and (" · " .. table.concat(s.points, "/")) or ""
    return ("Spé : %s (%s)%s"):format(s.name or M.TreeNames(s.class)[s.tree], SOURCE_FR[s.source] or "?", pts)
  end
  if s.pending then return "Spé : lecture en cours…" end
  if s.early then return "Spé : pas encore de talents (niveau 10)" end
  if s.none then return "Spé : aucun point de talent dépensé" end
  return "Spé : inconnue — choisis-la (3 boutons ci-dessous)"
end

function M.OnSpecChanged()
  if M.ResetWeights then M.ResetWeights() end
  if M.plan and M.MarkDirty then M.MarkDirty() end   -- (pas de premier plan pour un changement de spé)
  if T.ItemInfo and T.ItemInfo.Invalidate then T.ItemInfo.Invalidate() end
  if T.Fire then pcall(T.Fire, T, "GEAR_SPEC_CHANGED", M.Spec()) end
end
--- le joueur choisit sa spé (1..3) ou rend la main à la lecture automatique (nil)
function M.SetSpec(tree)
  local c = charDB()
  if not c then return false end
  tree = tonumber(tree)
  c.specManual = (tree and tree >= 1 and tree <= 3) and tree or nil
  c.specAsked = true
  SP.dirty = true
  if M.Clear then M:Clear("spec") end
  M.OnSpecChanged()
  return true
end
--- événement de talents / de sorts : relire à la prochaine demande (rien n'est calculé ici)
function M.SpecDirty(deep)
  SP.dirty = true
  if M.ResetWeights then M.ResetWeights() end   -- les poids suivent la spé : relus à la prochaine demande
  if deep then SP.deep, SP.deepCls, SP.ranks = nil, nil, nil end
end

--- rangs par talent pour le conseiller de talents (Forever) : { ok, ranks = { "arbre:rang:col" = n }, points }
--- ou, si seuls les points par arbre se lisent, { ok = false, points }
function M.TalentRanks()
  local cls = class()
  if not cls then return nil end
  local points, ranks = traitNodes(cls)
  if points then return { ok = true, ranks = ranks, points = points, names = M.TreeNames(cls) } end
  local p2, names = traitGroups(cls)
  if p2 then return { ok = false, points = p2, names = names or M.TreeNames(cls) } end
  return nil
end

--- demandé UNE fois quand rien ne se lit : une ligne dans « Maintenant », clic = la page Équipement (3 boutons)
function M.AskSpecOnce()
  local c = charDB()
  local s = M.Spec()
  if not (c and s and s.ask) or c.specAsked then return false end
  -- juste après la connexion le jeu n'a pas encore chargé les talents : on ne demande qu'après une 2e lecture
  local t = num(try(g("GetTime"))) or 0
  if SP.enabledAt and t - SP.enabledAt < M.SPEC_ASK_AFTER then return false end
  c.specAsked = true
  local names = M.TreeNames(s.class)
  M:Suggest("spec", ("Quelle est ta spé ? (clic : %s, %s ou %s)"):format(names[1], names[2], names[3]), 2,
    { ttl = 900, cooldown = 0, sound = false,
      tooltip = { "TreeAI n'a pas pu lire tes talents : dis-lui ta spé une fois,",
        "il adapte les poids d'équipement. Tu peux la changer quand tu veux (page Équipement)." },
      onClick = function() if T.Window and T.Window.Open then T.Window.Open("gear") end end })
  return true
end

--- ce que chaque lecture a rendu sur CE client (pour le rapport d'erreurs : le vrai jeu nous dit quelle API répond)
function M.SpecProbe()
  local cls = class()
  SP.probe = {}
  local a = cls and readApi(cls)
  local d = cls and not a and readDeep(cls) or nil
  local p = SP.probe
  local out = { "classe=" .. tostring(cls) .. " niv=" .. level() }
  for _, k in ipairs({ "classic", "groups", "spec", "nodes", "spells" }) do
    if p[k] ~= nil then out[#out + 1] = k .. "=" .. tostring(p[k]) end
  end
  local has = {}
  for _, n in ipairs({ "GetTalentTabInfo", "GetNumTalentTabs", "GetTalentInfo", "GetSpecialization", "GetNumSpecializations",
      "IsPlayerSpell", "IsSpellKnown", "GetNumUnspentTalents" }) do
    if type(g(n)) == "function" then has[#has + 1] = n end
  end
  for _, ns in ipairs({ { "C_Traits", "GetConfigInfo", "GetGroupDisplayInfoByTreeID", "GetGroupCurrencyInfo", "GetTreeNodes", "GetNodeInfo" },
      { "C_ClassTalents", "GetActiveConfigID" }, { "C_SpecializationInfo", "GetSpecialization", "GetCombatConfigIDForSpecGroup", "GetTalentInfo" },
      { "C_SpellBook", "IsSpellKnown" } }) do
    local t = tbl(g(ns[1]))
    for i = 2, #ns do if t and type(t[ns[i]]) == "function" then has[#has + 1] = ns[1] .. "." .. ns[i] end end
  end
  out[#out + 1] = "api: " .. table.concat(has, " ")
  local s = a or d or nil
  out[#out + 1] = "résultat: " .. (s and (tostring(s.tree) .. " " .. tostring(s.source)) or "rien")
  return out
end

--- fin de combat (appelé par le rappel unique de Gear.lua) : la lecture longue remise à plus tard peut se faire
function M.SpecCombatEnd()
  if SP.deepWanted then SP.deepWanted = false SP.dirty = true end
end
M.SPEC_ASK_AFTER = 25   -- secondes après l'activation avant de poser la question (talents chargés par le jeu)
--- branché par Gear:OnEnable
function M.SpecEnable()
  SP.enabledAt = num(try(g("GetTime"))) or 0
  -- relecture une fois le jeu installé : si la spé n'était pas lisible à la connexion, on réessaie avant de demander
  T:After(M.SPEC_ASK_AFTER + 1, function()
    local s = SP.cur
    if s and not s.tree and not s.none then M.SpecDirty(true) M.Spec() end
  end)
  local function hard()
    M.SpecDirty(true)
    if M.ResetWeights then M.ResetWeights() end
    if M.plan and M.MarkDirty then M.MarkDirty() end
  end
  for _, ev in ipairs({ "PLAYER_TALENT_UPDATE", "CHARACTER_POINTS_CHANGED", "ACTIVE_TALENT_GROUP_CHANGED", "TRAIT_CONFIG_UPDATED",
      "ACTIVE_COMBAT_CONFIG_CHANGED", "TRAIT_TREE_CURRENCY_INFO_UPDATED", "ACTIVE_PLAYER_SPECIALIZATION_CHANGED",
      "TRAIT_CONFIG_LIST_UPDATED" }) do
    M:RegisterEvent(ev, hard)
  end
  -- un sort appris ne compte que si la spé vient des sorts (ou n'est pas encore lue) : sinon rien
  M:RegisterEvent("SPELLS_CHANGED", function()
    local s = SP.cur
    if s and (s.source == "spells" or (not s.tree and not s.none)) then hard() end
  end)
  -- (un module = UN rappel par événement / message : PLAYER_LEVEL_UP et COMBAT_END sont branchés dans Gear.lua)
  if T.RegisterSlash then
    T:RegisterSlash("spe", function(rest)
      rest = norm(rest)
      local cls = class()
      local names = M.TreeNames(cls)
      local pick
      for i = 1, 3 do if rest ~= "" and (rest == tostring(i) or norm(names[i]):find(rest, 1, true)) then pick = i end end
      if rest == "auto" then M.SetSpec(nil) elseif pick then M.SetSpec(pick) end
      if T.Window and T.Window.Open then T.Window.Open("gear") end
    end, "spe [1|2|3|nom|auto] : ta spé pour les conseils d'équipement (page Équipement)")
  end
end
