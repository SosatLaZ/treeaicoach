-- TreeAI — module « Infos objets » (agent ITEMINFO, 4.0.2) : quelques lignes VRAIES sous chaque infobulle d'objet
-- (sacs, butin, récompenses de quête, marchand, HV, liens du chat, objets portés). Une ligne par sujet, ≤ 6 :
--  • Amélioration ? comparé à ce que tu portes dans cet emplacement (poids de Gear) : Mieux / Pareil / Moins bien,
--    les stats qui changent, le DPS des armes, ≈ % de dégâts à distance pour le chasseur (estimation affichée),
--    munitions de ton arme, niveau requis, armure pas encore portable (mailles au niv 40 pour le chasseur) ;
--  • Groupe : à qui il sert (classe, armure, niveau, équipement inspecté), Besoin / Cupidité ;
--  • Valeur : marchand (la pile aussi), HV seulement quand le marché est fiable (modèle de liquidité de l'HV :
--    « se vend ~N/jour »), sinon « pas de marché fiable » → vends au marchand / vends à l'HV / Garder ;
--  • Quête : objet d'une quête du journal dont l'objectif n'est pas fini (x/y), à rendre, plus utile, commence une
--    quête (niv) ;
--  • Métiers : compo de tes recettes connues (combien tu peux en faire), sinon les métiers qui s'en servent ;
--  • Familier (chasseur) : il aime ça / il n'en mange pas ;
--  • Sources : où il tombe (taux MESURÉ par Stats seulement, sinon le nom des monstres), qui le vend, fabriqué, récolté.
-- Maj = détails (au prochain rafraîchissement de l'infobulle par le jeu). Une case par sujet dans les options.
-- Lit les autres modules par leurs API publiques seulement : Gear.Engine / Verdict / AMMO_OF, Auction.Liq / Stats /
-- PostPrice, Stats.Account (comptes de drops), Highlight.Data / TooltipGlow, Quests.ObjectiveFor, Dungeon.Members /
-- LootItem, Hunter:GetDiet / FOODS, Prof.ScanProfessions / Engine.Color, Gear.Rec / Usable (masque de classes),
-- Compass.Questie (QuestieDB en lecture).
-- Taint : TooltipDataProcessor.AddTooltipPostCall (repli : HookScript « OnTooltipSetItem »), seulement AddLine / Show
-- sur GameTooltip et ItemRefTooltip ; aucune méthode du jeu remplacée, aucun rafraîchissement forcé (la comparaison
-- d'objets est protégée sur Forever). Ne travaille QUE quand une infobulle d'objet s'affiche ; cache par objet invalidé
-- par les sacs, l'équipement, le journal, le groupe, le familier, les métiers. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}

local M = T:RegisterModule("ItemInfo", {
  title = "Infos objets",
  defaults = { enabled = true, upgrade = true, quest = true, group = true, pet = true, prof = true, value = true,
    source = true, shift = true },
  options = {
    { key = "upgrade", type = "toggle", label = "Amélioration ? Comparé à ce que tu portes (stats, DPS, niveau, armure)",
      section = "Infobulles d'objets" },
    { key = "quest", type = "toggle", label = "Quête : objectif (3/8), à rendre, plus utile, commence une quête" },
    { key = "group", type = "toggle", label = "Groupe : à qui il sert, Besoin / Cupidité" },
    { key = "pet", type = "toggle", label = "Familier : mange-t-il ça ? (chasseur)" },
    { key = "prof", type = "toggle", label = "Métiers : compo de tes recettes (combien tu peux en faire)" },
    { key = "value", type = "toggle", label = "Valeur : marchand, HV si le marché est fiable → vendre ou garder" },
    { key = "source", type = "toggle", label = "Sources : où il tombe (taux mesuré), qui le vend" },
    { key = "shift", type = "toggle", label = "Maj : détails en plus (stats complètes, raisons, toutes les sources)",
      section = "Affichage" },
  },
})
T.ItemInfo = M

local floor, max, min, abs = math.floor, math.max, math.min, math.abs
local fmt, tconcat, tsort = string.format, table.concat, table.sort
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function meth(o, k, ...) if type(o) == "table" and type(o[k]) == "function" then return try(o[k], o, ...) end end
local function secret(v) local f = rawget(_G, "issecretvalue") return v ~= nil and type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end   -- valeur secrète (combat) → nil
local function num(v) return tonumber(sv(v)) end
local function str(v) v = sv(v) if type(v) == "string" and v ~= "" then return v end return nil end
local function low(s) return tostring(s or ""):lower() end
local function opt(k) return M.db ~= nil and M.db[k] ~= false end
local function now() return num(try(_G.GetTime)) or 0 end
local function inCombat() return (T.InCombat and T:InCombat()) and true or false end
local function mod(n) local m = T.modules and T.modules[n] if type(m) == "table" then return m end return nil end
local function level() return num(try(_G.UnitLevel, "player")) or 1 end
local function myClass() return T.PlayerClass and T:PlayerClass() or nil end
local cut = T.Cut or function(s) return tostring(s or "") end   -- (Core) n caractères UTF-8, jamais un accent coupé
local function q(s) return "« " .. tostring(s) .. " »" end
local function dec(x) return (fmt("%.1f", x):gsub("%.", ",")) end
local function numTxt(x) if abs(x - floor(x + 0.5)) < 0.05 then return tostring(floor(x + 0.5)) end return dec(x) end
local function money(c)
  local F = T.Fmt
  if F and F.Money then return F.Money(c) end
  c = floor(tonumber(c) or 0)
  local g, s, k = floor(c / 10000), floor(c / 100) % 100, c % 100
  if g > 0 then return s > 0 and fmt("%d po %d pa", g, s) or fmt("%d po", g) end
  if s > 0 then return k > 0 and fmt("%d pa %d pc", s, k) or fmt("%d pa", s) end
  return fmt("%d pc", k)
end
local function andList(t, n)
  n = min(n or #t, #t)
  if n == 0 then return "" end
  local s = n == 1 and t[1] or (tconcat(t, ", ", 1, n - 1) .. " et " .. t[n])
  if #t > n then s = s .. fmt(" (+%d)", #t - n) end
  return s
end
local function itemIDOf(link)
  if type(link) == "number" then return link end
  return tonumber(tostring(link or ""):match("item:(%d+)"))
end
local function linkName(link) return type(link) == "string" and link:match("%[(.-)%]") or nil end
local function questie() local C = T.Compass return C and C.Questie and try(C.Questie) or nil end

-- couleurs : une par sens (thème TreeAI), le texte en clair, les détails en gris
local TONE = { good = "good", bad = "bad", warn = "warn", info = "gold" }
local TONE_RGB = { good = { 0.3, 0.9, 0.4 }, bad = { 1, 0.3, 0.25 }, warn = { 1, 0.6, 0.15 }, info = { 1, 0.82, 0.2 } }
local function hex(tone)
  local Th = T.Theme
  local h = Th and Th.Hex and try(Th.Hex, Th, TONE[tone] or "gold")
  if type(h) == "string" then return h end
  local c = TONE_RGB[tone] or TONE_RGB.info
  return fmt("ff%02x%02x%02x", floor(c[1] * 255 + 0.5), floor(c[2] * 255 + 0.5), floor(c[3] * 255 + 0.5))
end
local BODY, DETAIL = { 0.9, 0.9, 0.9 }, { 0.62, 0.62, 0.66 }
M.MAX_LINES, M.MAX_WITH_DETAILS = 6, 14

---------------------------------------------------------------------------------------------------
-- Lecture de l'objet et de l'endroit où il est (sac, butin, porté)
---------------------------------------------------------------------------------------------------
--- infos du jeu (valeurs secrètes écartées) ; nil = pas encore en cache (le serveur répond plus tard)
local function readItem(x)
  if x == nil or not API.ItemInfo then return nil end
  local name, link, quality, ilvl, minLevel, _, _, stack, equipLoc, _, sell, classID, subID, bind = API.ItemInfo(x)
  name = str(name)
  if not name then return nil end
  return { name = name, link = str(link), quality = num(quality) or 1, ilvl = num(ilvl) or 0,
    minLevel = num(minLevel) or 0, stack = num(stack) or 1, equipLoc = str(equipLoc) or "", sell = num(sell) or 0,
    classID = num(classID), subID = num(subID), bind = num(bind) }
end
M.ReadItem = readItem

--- l'objet montré par l'infobulle : ID, lien (suffixes aléatoires compris), GUID (objet des sacs / porté)
local function resolve(tt, data)
  local id, link, guid
  if type(data) == "table" then id, guid, link = num(data.id), str(data.guid), str(data.hyperlink) end
  if tt and tt.GetItem then
    local _, l, i = try(tt.GetItem, tt)
    l = str(l)
    if l then link = l end
    id = id or num(i)
  end
  if not link and guid then
    local CI = _G.C_Item
    link = CI and str(try(CI.GetItemLinkByGUID, guid)) or nil
  end
  if link and not link:find("item:", 1, true) then link = nil end
  return id or itemIDOf(link), link, guid
end

local function bagItem(bag, slot)
  local C = _G.C_Container
  if C and C.GetContainerItemInfo then
    local t = try(C.GetContainerItemInfo, bag, slot)
    if type(t) == "table" then return num(t.itemID), num(t.stackCount), sv(t.isBound) end
    return nil
  end
  local it = API.BagItem and API.BagItem(bag, slot)
  if it then return num(it.itemID), num(it.count), nil end
  return nil
end
local function bagQuest(bag, slot)
  local C = _G.C_Container
  if C and C.GetContainerItemQuestInfo then
    local t = try(C.GetContainerItemQuestInfo, bag, slot)
    if type(t) == "table" then return sv(t.isQuestItem), num(t.questID), sv(t.isActive) end
    return nil
  end
  local a, b, c = try(rawget(_G, "GetContainerItemQuestInfo"), bag, slot)
  return sv(a), num(b), sv(c)
end

--- contexte : nombre (pile), lié, sac / case, butin, porté, infos de quête du jeu. Tout est vérifié (l'objet de la
--- case doit être celui de l'infobulle), sinon inconnu.
local function context(tt, guid, id)
  local c = {}
  local CI = _G.C_Item
  if guid and CI and CI.GetItemLocation then
    local loc = try(CI.GetItemLocation, guid)
    if type(loc) == "table" then
      c.count = num(try(CI.GetStackCount, loc))
      c.bound = sv(try(CI.IsBound, loc))
      if meth(loc, "IsEquipmentSlot") then c.equipped = true
      elseif meth(loc, "IsBagAndSlot") then c.bag, c.slot = meth(loc, "GetBagAndSlot") c.bag, c.slot = num(c.bag), num(c.slot) end
    end
  end
  local owner = tt and tt.GetOwner and try(tt.GetOwner, tt)
  if type(owner) == "table" then
    local nm = str(meth(owner, "GetName"))
    if nm and nm:find("^Character.+Slot$") then c.equipped = true end
    if nm and nm:find("^Merchant") then c.merchant = true end                         -- objet à acheter
    if nm and nm:find("Quest") and nm:find("Item") then c.reward = true end          -- récompense de quête (un choix)
    if not c.bag and not c.equipped then
      local slot, bag = num(meth(owner, "GetID")), num(meth(owner, "GetBagID"))
      if bag == nil then bag = num(meth(meth(owner, "GetParent"), "GetID")) end
      if bag and slot and bag >= -1 and bag <= 12 and slot >= 1 then
        local bid, cnt, bound = bagItem(bag, slot)
        if bid and bid == id then
          c.bag, c.slot, c.count = bag, slot, c.count or cnt
          if c.bound == nil then c.bound = bound end
        end
      end
    end
    if not c.bag and not c.equipped then   -- fenêtre de butin (Classic : .slot, Mainline : :GetSlotIndex())
      local ls = num(rawget(owner, "slot")) or num(meth(owner, "GetSlotIndex"))
      local l = ls and str(try(rawget(_G, "GetLootSlotLink"), ls))
      if l and itemIDOf(l) == id then
        local _, _, qty, _, _, _, isQ, qid, act = try(rawget(_G, "GetLootSlotInfo"), ls)
        c.loot, c.count, c.isQuest, c.questID, c.questActive = ls, num(qty), sv(isQ), num(qid), sv(act)
      end
    end
  end
  if c.bag and c.slot then c.isQuest, c.questID, c.questActive = bagQuest(c.bag, c.slot) end
  if c.questID and c.questID <= 0 then c.questID = nil end
  return c
end
M.Context = context

---------------------------------------------------------------------------------------------------
-- Moteur d'équipement (Gear.Engine, mêmes poids que l'onglet « Équipement » et la récompense de quête)
---------------------------------------------------------------------------------------------------
local function gear() local Gm = T.Gear if type(Gm) == "table" and type(Gm.Engine) == "table" then return Gm, Gm.Engine end return nil end
local DPS = "ITEM_MOD_DAMAGE_PER_SECOND_SHORT"
local ABBR = { ITEM_MOD_AGILITY_SHORT = "Agi", ITEM_MOD_STRENGTH_SHORT = "For", ITEM_MOD_STAMINA_SHORT = "End",
  ITEM_MOD_INTELLECT_SHORT = "Int", ITEM_MOD_SPIRIT_SHORT = "Esp", ITEM_MOD_ATTACK_POWER_SHORT = "PA",
  ITEM_MOD_RANGED_ATTACK_POWER_SHORT = "PA à distance", ITEM_MOD_SPELL_POWER_SHORT = "Puissance des sorts",
  ITEM_MOD_SPELL_DAMAGE_DONE_SHORT = "Dégâts des sorts", ITEM_MOD_SPELL_HEALING_DONE_SHORT = "Soins",
  ITEM_MOD_HIT_RATING_SHORT = "Toucher", ITEM_MOD_CRIT_RATING_SHORT = "Critique",
  ITEM_MOD_DEFENSE_SKILL_RATING_SHORT = "Défense", RESISTANCE0_NAME = "Armure" }
-- stats sans effet sur les dégâts à distance du chasseur (le reste du modèle : Agi, PA, PA à distance, DPS de l'arc)
local NEUTRAL = { ITEM_MOD_STAMINA_SHORT = true, ITEM_MOD_INTELLECT_SHORT = true, ITEM_MOD_SPIRIT_SHORT = true,
  ITEM_MOD_STRENGTH_SHORT = true, RESISTANCE0_NAME = true, ITEM_MOD_DEFENSE_SKILL_RATING_SHORT = true,
  ITEM_MOD_SPELL_POWER_SHORT = true, ITEM_MOD_SPELL_DAMAGE_DONE_SHORT = true, ITEM_MOD_SPELL_HEALING_DONE_SHORT = true }
local ARMOR_FR = { [1] = "tissu", [2] = "cuir", [3] = "mailles", [4] = "plaques", [6] = "bouclier" }
local AMMO_FR = { [2] = "flèches", [3] = "balles" }
local RANGED_FR = { [2] = "arc", [3] = "fusil", [18] = "arbalète" }
local MIN_GAIN = 3   -- même seuil que Gear.Verdict (en dessous : équivalent)

local function wornLink(slot) return str(try(_G.GetInventoryItemLink, "player", slot)) end
--- objet réservé à d'autres classes que `cls` (masque de classes des données du client, règle de Gear.Usable)
local function reservedFor(Gm, id, cls)
  local rec = cls and Gm and Gm.Rec and Gm.Usable and try(Gm.Rec, id)
  if type(rec) ~= "table" or (tonumber(rec.mask) or 0) <= 0 then return false end
  local ok, _, why = try(Gm.Usable, rec, { class = cls, level = 999, horizon = 999 })
  return (not ok and why == "réservé à d'autres classes") and true or false
end
local function statsOf(E, link)
  local st = link and E.ItemStats and try(E.ItemStats, link)
  return type(st) == "table" and st or nil
end
--- l'objet porté que celui-ci remplacerait (même choix que Gear.UpgradePct : le plus faible des deux anneaux / bijoux
--- / armes à une main ; arme à deux mains = main droite + main gauche) -> liens, emplacement de score
local function replaced(E, it)
  local slots = E.EQUIP_SLOTS and E.EQUIP_SLOTS[it.equipLoc]
  if not slots then return nil end
  if it.equipLoc == "INVTYPE_2HWEAPON" then
    local out = {}
    for _, s in ipairs({ 16, 17 }) do local l = wornLink(s) if l then out[#out + 1] = l end end
    return out, 16
  end
  local cand = slots
  if it.equipLoc == "INVTYPE_WEAPON" then
    local d = E.DUAL_LEVEL and E.DUAL_LEVEL[myClass() or ""]
    cand = (d and level() >= d) and { 16, 17 } or { 16 }
  end
  local w = try(E.Weights)
  local bestL, bestS, bestSlot
  for _, s in ipairs(cand) do
    local l = wornLink(s)
    local sc = l and (try(E.ScoreLink, l, s, w) or 0) or 0
    if bestS == nil or sc < bestS then bestL, bestS, bestSlot = l, sc, s end
  end
  return { bestL }, bestSlot or slots[1]
end
local function addStats(into, st, sign)
  for k, v in pairs(st or {}) do
    v = tonumber(v)
    if v then into[k] = (into[k] or 0) + sign * v end
  end
end
local function statName(k) return ABBR[k] or str(rawget(_G, k)) end
--- « +6 Agi », triés par poids pour ta classe ; DPS à part
local function statParts(delta, w, n)
  local list = {}
  for k, v in pairs(delta) do
    if k ~= DPS and abs(v) >= 0.5 and statName(k) then
      local wt = abs(tonumber(w and w[k]) or 0)
      list[#list + 1] = { k = k, v = v, s = abs(v) * (wt > 0 and wt or 0.001) }
    end
  end
  tsort(list, function(a, b) if a.s ~= b.s then return a.s > b.s end return a.k < b.k end)
  local out = {}
  for i = 1, min(n or #list, #list) do
    local e = list[i]
    out[#out + 1] = (e.v > 0 and "+" or "-") .. numTxt(abs(e.v)) .. " " .. statName(e.k)
  end
  return out, #list
end

-- Chasseur : dégâts à distance ≈ DPS de l'arme + PA à distance / 14 (1 Agi = 2 PA à distance, la PA compte aussi).
-- Lu hors combat sur la fiche (UnitRangedDamage, secret en combat) ; en combat, le dernier relevé.
local model
function M.RangedModel()
  local bg = API.BagGen and API.BagGen() or 0
  if model and (inCombat() or (model.gen == bg and now() - model.t < 30)) then return model end
  if inCombat() then return nil end
  model = nil
  if not wornLink(18) then return nil end
  local speed, lo, hi = try(_G.UnitRangedDamage, "player")
  speed, lo, hi = num(speed), num(lo), num(hi)
  if not (speed and speed > 0 and lo and hi and hi > 0) then return nil end
  model = { dps = (lo + hi) / 2 / speed, gen = bg, t = now() }
  return model
end
---------------------------------------------------------------------------------------------------
-- Sections : chacune pose au plus UNE ligne (label, texte, ton) + des détails (Maj), et note ce qui rend l'objet
-- utile (st.keep) pour le verdict de la valeur
---------------------------------------------------------------------------------------------------
local SEC = {}
M.Sections = SEC

local function ammo(it, o, st)
  local Gm, E = gear()
  if myClass() ~= "HUNTER" or not (it.subID == 2 or it.subID == 3) or not Gm then return end
  local ri = readItem(wornLink(18))
  local want = ri and Gm.AMMO_OF and Gm.AMMO_OF[ri.subID]
  if not want then return end
  if want ~= it.subID then
    o.label, o.tone = "Pas pour ton arme", "bad"
    o.text = fmt("%s (ton %s tire des %s)", AMMO_FR[it.subID], RANGED_FR[ri.subID] or "arme", AMMO_FR[want])
    return
  end
  st.keep = st.keep or "munitions"
  if it.minLevel > level() then
    o.label, o.tone, o.text = "Pas encore", "warn", fmt("niv %d requis", it.minLevel)
    return
  end
  local cur = num(try(_G.GetInventoryItemID, "player", 0))
  if not cur or cur == it.id then return end
  local ci = readItem(cur)
  local a, b = statsOf(E, it.link), ci and statsOf(E, ci.link)
  local da, db = a and tonumber(a[DPS]), b and tonumber(b[DPS])
  if not (da and db) then return end
  local d = da - db
  if d > 0.05 then o.label, o.tone = "Mieux", "good"
  elseif d < -0.05 then o.label, o.tone = "Moins bien", "bad" st.keep = nil
  else o.label, o.tone = "Pareil", "info" end
  o.text = fmt("%s%s DPS par rapport à %s", d >= 0 and "+" or "-", dec(abs(d)), q(ci.name))
end

function SEC.upgrade(it, c, o, st)
  if it.classID == 6 then return ammo(it, o, st) end
  local Gm, E = gear()
  if not (Gm and E.EQUIP_SLOTS and E.CanUse and Gm.Score and Gm.ScoreLine) then return end
  if not E.EQUIP_SLOTS[it.equipLoc] or c.equipped or not it.link then return end
  local links = replaced(E, it)
  for _, l in ipairs(links or {}) do if l == it.link then return end end   -- c'est celui que tu portes
  if reservedFor(Gm, it.id, myClass()) then
    o.label, o.tone, o.text = "Pas pour toi", "bad", "réservé à d'autres classes"
    return
  end
  -- (5.0 GEAR) UN verdict, le même partout : T.Gear.Score (valeur pour TA spé, « ≈ +2,1 DPS » ou « +12 % »,
  -- détail par stat) ; stats illisibles → « inconnu », jamais un score deviné
  local sc = Gm.Score(it.link)
  if type(sc) ~= "table" or sc.equip == false then return end
  local label, text, kind = Gm.ScoreLine(sc)
  if not label then return end
  o.label, o.text = label, text or ""
  o.tone = (kind == "good" and "good") or (kind == "bad" and "bad") or (kind == "warn" and "warn") or "info"
  if sc.known == false then st.partial, st.unknown = true, true return end   -- relu à la prochaine infobulle (pas mis en cache)
  if sc.usable == false then
    if sc.later then
      st.later = sc.later
      o.text = fmt("%s portables au niv %d", ARMOR_FR[it.subID] or "armure", sc.later)
    end
    return
  end
  if sc.kind == "good" then st.keep, st.upgrade = st.keep or "amélioration", sc.pct end
  o.details[#o.details + 1] = "Comparé à : " .. ((sc.replaces and #sc.replaces > 0) and tconcat(sc.replaces, " + ") or "rien (emplacement vide)")
  for _, l in ipairs(sc.lines or {}) do o.details[#o.details + 1] = l end
  local V = Gm.Value
  local model = V and V.ModelText and try(V.ModelText)
  if model then o.details[#o.details + 1] = model end
  if sc.src and sc.src ~= "jeu" then
    o.details[#o.details + 1] = "Stats lues : " .. (sc.src == "données" and "tables du jeu" or "infobulle") .. "."
  end
end

-- quête : données de la surbrillance (journal + QuestieDB, règles DATA) ; repli : Quests.ObjectiveFor (nom exact)
local function questTitle(qid)
  local QL = _G.C_QuestLog
  local t = QL and str(try(QL.GetTitleForQuestID, qid))
  if not t then local db = questie() t = db and db.QueryQuestSingle and str(try(db.QueryQuestSingle, qid, "name")) end
  return t
end
local function questDone(qid)
  local QL = _G.C_QuestLog
  if QL and QL.IsQuestFlaggedCompleted then return sv(try(QL.IsQuestFlaggedCompleted, qid)) and true or false end
  return sv(try(rawget(_G, "IsQuestFlaggedCompleted"), qid)) and true or false
end
local function inLog(qid)
  local QL = _G.C_QuestLog
  if QL and QL.IsOnQuest then return sv(try(QL.IsOnQuest, qid)) and true or false end
  if QL and QL.GetLogIndexForQuestID then return num(try(QL.GetLogIndexForQuestID, qid)) ~= nil end
  return false
end
local function questKind(it, c)
  local H = T.Highlight
  local D = (type(H) == "table" and H._active and H.Data) and try(H.Data) or nil
  if type(D) == "table" then
    local e = (D.byID and D.byID[it.id]) or (D.byName and D.byName[low(it.name)])
    if e then return (e.use and "use") or ((e.ready or e.done) and "done") or "need", e end
  else
    local Q = mod("Quests")
    local qq, oo
    if Q and Q.ObjectiveFor then qq, oo = try(Q.ObjectiveFor, it.name, false) end
    if type(qq) == "table" and type(oo) == "table" then
      return (oo.finished or qq.complete) and "done" or "need",
        { quest = qq.title, qid = qq.id, have = oo.have, need = oo.need, ready = qq.complete, done = oo.finished }
    end
  end
  local db = questie()
  local qid, act = c.questID, c.questActive
  if not qid and db and db.QueryItemSingle then
    local s = try(db.QueryItemSingle, it.id, "startQuest")
    qid = tonumber(type(s) == "table" and s[1] or s)
  end
  if qid and qid > 0 and not act and not inLog(qid) and not questDone(qid) then
    local lvl = db and db.QueryQuestSingle and (tonumber((try(db.QueryQuestSingle, qid, "questLevel")))
      or tonumber((try(db.QueryQuestSingle, qid, "requiredLevel"))))
    return "starter", { quest = questTitle(qid), qid = qid, level = lvl and lvl > 0 and lvl or nil }
  end
  if type(D) == "table" and D.upcoming and D.upcoming[it.id] then return "upcoming", D.upcoming[it.id] end
  -- « plus utile » seulement quand on a pu voir qu'il ne lance rien (case du sac / butin lus, ou QuestieDB)
  if (c.isQuest or it.classID == 12) and not qid and (c.bag or c.loot or db) then return "useless", {} end
  return nil
end
M.QuestKind = questKind
local KIND_COL = { need = "colNeed", use = "colNeed", starter = "colStarter" }
function SEC.quest(it, c, o, st)
  local kind, e = questKind(it, c)
  if not kind then return end
  st.quest = kind
  local title = cut((e and e.quest) or "?", 34)
  local questieShows = type(rawget(_G, "QuestieLoader")) == "table"   -- Questie écrit déjà la progression
  if kind == "need" or kind == "done" or kind == "use" then
    st.keep = st.keep or "quête"
    if questieShows then return end
    o.label, o.tone = "Objet de quête", "info"
    if kind == "use" then o.text = q(title) .. " — à utiliser"
    elseif e.provided then o.text = q(title) .. " — objet requis"
    else
      local have, need = tonumber(e.have) or 0, tonumber(e.need) or 1
      o.text = fmt("%s (%d/%d)", q(title), have, need)
      if kind == "done" then o.text = o.text .. (e.ready and (need > 1 and " — à rendre, garde-les" or " — à rendre, garde-le") or " — objectif rempli") end
      if kind == "need" then o.details[#o.details + 1] = fmt("Il en manque %d.", max(0, need - have)) end
    end
  elseif kind == "starter" then
    st.keep = st.keep or "quête"
    o.label, o.tone = "! Commence une quête", "good"
    o.text = ((e and e.quest) and q(title) or "") .. ((e and e.level) and fmt(" (niv %d)", e.level) or "")
  elseif kind == "upcoming" then
    st.keep = st.keep or "quête"
    o.label, o.tone, o.text = "Pour une quête à venir", "info", q(title)
  elseif kind == "useless" then
    st.useless = true
    o.label, o.tone, o.text = "Objet de quête", "bad", "plus utile"
    o.details[#o.details + 1] = "La quête qui le demandait est rendue ou n'est plus dans ton journal."
  end
  local H = T.Highlight
  if KIND_COL[kind] and type(H) == "table" and H._active and H.TooltipGlow and H.TooltipActive and try(H.TooltipActive) then
    local col = H.COLORS and H.db and H.COLORS[floor(tonumber(H.db[KIND_COL[kind]]) or 1)]
    if type(col) == "table" then st.glow = { kind, col[1], col[2], col[3] } end
  end
end

-- groupe : pour qui l'objet est fait (armure de la classe à son niveau, arme maniée, stats), puis Dungeon.LootItem
-- (niveau requis, mailles / plaques au 40, gain pour toi et pour les amis inspectés)
local function bestArmor(E, class, lvl)
  local best = 0
  for sub, need in pairs((E.ARMOR_LEVEL or {})[class] or {}) do
    if sub <= 4 and (lvl or 0) >= need and sub > best then best = sub end
  end
  return best
end
local RANGED_SUB = { [2] = true, [3] = true, [18] = true }
local function madeFor(E, it, members, Gm)
  local set, cid, sid = {}, it.classID, it.subID
  local function add(cls) if cls then set[cls] = true end end
  -- 1. le type : armure la plus lourde de la classe à son niveau, bouclier, arme maniée (arc / fusil : le chasseur)
  if cid == 4 and sid and sid >= 1 and sid <= 4 and it.equipLoc ~= "INVTYPE_CLOAK" then
    for _, m in ipairs(members) do if m.class and bestArmor(E, m.class, m.level) == sid then add(m.class) end end
  elseif cid == 4 and sid == 6 then
    for _, m in ipairs(members) do if m.class and ((E.ARMOR_LEVEL or {})[m.class] or {})[6] then add(m.class) end end
  elseif cid == 2 and sid then
    local hunter = false
    for _, m in ipairs(members) do
      if m.class and ((E.WEAPON_TYPES or {})[m.class] or {})[sid] then add(m.class) if m.class == "HUNTER" then hunter = true end end
    end
    if RANGED_SUB[sid] and hunter then set = { HUNTER = true } end
  else   -- cape, cou, anneau, bijou, tenu en main : tout le monde, les stats trient
    for _, m in ipairs(members) do add(m.class) end
  end
  -- 2. les stats : parmi ceux-là, les classes qui valorisent ces stats (≥ moitié du meilleur ; bâton d'Intelligence ≠ chasseur)
  local st = it.link and E.ItemStats and try(E.ItemStats, it.link)
  local slots = E.EQUIP_SLOTS and E.EQUIP_SLOTS[it.equipLoc]
  if type(st) == "table" then
    -- des stats en plus du DPS : elles seules disent pour qui (le DPS d'une arme intéresse toute classe qui la manie)
    local only = {}
    for k, v in pairs(st) do if k ~= DPS then only[k] = v end end
    if next(only) ~= nil then st = only end
  end
  if type(st) == "table" and slots and E.WEIGHTS and E.ScoreStats and next(set) then
    local sc, best = {}, 0
    for cls in pairs(set) do
      local cw = E.WEIGHTS[cls]
      local v = cw and tonumber((try(E.ScoreStats, st, slots[1], cw.default))) or 0
      sc[cls] = v
      if v > best then best = v end
    end
    if best > 0 then for cls, v in pairs(sc) do if v < best * 0.5 then set[cls] = nil end end end
  end
  for cls in pairs(set) do if reservedFor(Gm, it.id, cls) then set[cls] = nil end end   -- « Classes : … » du jeu
  local out = {}
  for cls in pairs(set) do out[#out + 1] = cls end
  tsort(out)
  return tconcat(out, " ")
end
function SEC.group(it, c, o, st)
  if c.equipped or c.merchant or c.reward or it.equipLoc == "" or it.classID == 6 then return end
  if st.unknown then return end   -- (5.0 GEAR) stats illisibles : on ne dit pas « Besoin pour toi » sans savoir
  local Gm, E = gear()
  local Dg = T.Dungeon
  if not (E and E.EQUIP_SLOTS and E.EQUIP_SLOTS[it.equipLoc] and type(Dg) == "table" and Dg.Members and Dg.LootItem) then return end
  local members = try(Dg.Members)
  if type(members) ~= "table" or #members < 2 then return end
  local link = it.link or it.id
  local e = try(Dg.LootItem, { link, it.name, madeFor(E, it, members, Gm) }, nil, members)
  if type(e) ~= "table" then return end
  if e.pending then st.partial = true return end
  -- ton gain est connu (ligne du dessus) : une amélioration pour toi compte, même hors de ta « classe cible »
  local mine = tonumber(st.upgrade)
  if mine and mine >= MIN_GAIN then
    local listed = false
    for _, n in ipairs(e.need or {}) do if type(n.m) == "table" and n.m.me then listed = true end end
    if not listed then
      for _, m in ipairs(members) do if m.me then e.need = e.need or {} table.insert(e.need, 1, { m = m, gain = mine }) break end end
    end
  end
  local need, others, unknown, other = {}, {}, {}, nil
  for _, n in ipairs(e.need or {}) do
    local g = tonumber(n.gain)
    if type(n.m) == "table" and (g == nil or g >= MIN_GAIN) then
      local who = n.m.me and "toi" or tostring(n.m.name or "?")
      local wtxt = (g == nil and "") or (g >= 999 and " (vide)") or (g >= 15 and " (nettement mieux)") or " (mieux)"
      need[#need + 1] = who .. wtxt
      if not n.m.me then others[#others + 1] = who .. wtxt end
      if g == nil then unknown[#unknown + 1] = who end
      if not n.m.me and not other then other = who end
    end
  end
  o.label, o.tone = "Pour ton groupe", "info"
  if c.bag then
    -- déjà dans tes sacs : pas de jet ; seulement si un AMI en a besoin (ton gain est la ligne du dessus)
    if #others == 0 then o.label = nil return end
    o.text = "utile à " .. andList(others, 3)
  elseif #need == 0 then
    o.text = "personne n'en a besoin → Cupidité pour tous"
  else
    o.text = "Besoin pour " .. andList(need, 3) .. ((#need < #members) and " · Cupidité pour les autres" or "")
  end
  if other and not st.keep then st.give = other end
  for _, l in ipairs(e.later or {}) do
    if type(l.m) == "table" and l.lvl then
      o.details[#o.details + 1] = fmt("%s pourra s'en servir au niv %d.", l.m.me and "Toi" or tostring(l.m.name or "?"), l.lvl)
    end
  end
  if #unknown > 0 then
    o.details[#o.details + 1] = "Gain inconnu pour " .. andList(unknown, 3) .. " (équipement pas encore inspecté)."
  end
end

-- familier (chasseur) : régime du familier (Hunter:GetDiet) × table des nourritures (Hunter.FOODS + données client)
local DIET_FR = { meat = "viande", fish = "poisson", bread = "pain", cheese = "fromage", fruit = "fruit", fungus = "champignon" }
local DIETS = { "meat", "fish", "bread", "cheese", "fruit", "fungus" }
local DIET_N = {}
for i, k in ipairs(DIETS) do DIET_N[k] = i end
local foodIdx   -- [itemID] = niveau × 8 + régime (un nombre par nourriture)
local function foodOf(id)
  if not foodIdx then
    foodIdx = {}
    local H = mod("Hunter")
    local srcs = {}
    if type(H) == "table" and type(H.FOODS) == "table" then srcs[#srcs + 1] = H.FOODS end      -- la liste du chasseur d'abord
    if T.Data and type(T.Data.PetFood) == "table" then srcs[#srcs + 1] = T.Data.PetFood end   -- puis les données du client
    for _, src in ipairs(srcs) do
      if type(src) == "table" then
        for _, f in ipairs(src) do
          if type(f) == "table" and type(f[1]) == "number" and DIET_N[f[2]] and not foodIdx[f[1]] then
            foodIdx[f[1]] = floor(tonumber(f[3]) or 1) * 8 + DIET_N[f[2]]
          end
        end
      end
    end
  end
  local v = foodIdx[id]
  if not v then return nil end
  return { diet = DIETS[v % 8], level = floor(v / 8) }
end
function SEC.pet(it, c, o, st)
  if myClass() ~= "HUNTER" then return end
  local f = foodOf(it.id)
  if not f or not sv(try(_G.UnitExists, "pet")) then return end
  local H = mod("Hunter")
  local diet = type(H) == "table" and type(H.GetDiet) == "function" and try(H.GetDiet, H) or nil
  if type(diet) ~= "table" or next(diet) == nil then return end
  local pl = num(try(_G.UnitLevel, "pet"))
  local names = {}
  for k in pairs(diet) do names[#names + 1] = DIET_FR[k] or k end
  tsort(names)
  o.label = "Familier"
  if not diet[f.diet] then
    o.tone, o.text = "bad", "il n'en mange pas (il mange : " .. tconcat(names, ", ") .. ")"
  elseif pl and f.level < pl - 30 then
    o.tone, o.text = "bad", "trop faible pour lui"
    o.details[#o.details + 1] = fmt("Nourriture niv %d, ton familier niv %d : il lui faut du niv %d ou plus.", f.level, pl, pl - 10)
  elseif pl and f.level < pl - 10 then
    o.tone, o.text = "warn", "il en mange (" .. DIET_FR[f.diet] .. "), effet réduit"
    o.details[#o.details + 1] = fmt("Nourriture niv %d, ton familier niv %d : du niv %d ou plus pour l'effet plein.", f.level, pl, pl - 10)
    st.keep = st.keep or "familier"
  else
    o.tone, o.text = "good", "il aime ça (" .. DIET_FR[f.diet] .. ")"
    st.keep = st.keep or "familier"
  end
  o.details[#o.details + 1] = "Régime : " .. tconcat(names, ", ") .. "."
end

-- métiers (Data/Recipes.lua) : compo -> masque des métiers qui s'en servent (un nombre par compo, construit une fois,
-- à la première infobulle) ; compo -> recettes seulement pour TES métiers (construit à la demande, par métier)
local LINE_BIT, matLines, lineIdx = {}, nil, {}
local function recipes() local d = T.Data and T.Data.Recipes return type(d) == "table" and type(d.r) == "table" and d or nil end
local function linesUsing(id)
  if not matLines then
    matLines = {}
    local d, bit = recipes(), 1
    for line, rows in pairs(d and d.r or {}) do
      if not LINE_BIT[line] then LINE_BIT[line], bit = bit, bit * 2 end
      local b = LINE_BIT[line]
      for _, row in ipairs(rows) do
        local m = row[10]
        if type(m) == "table" then
          for k = 1, #m - 1, 2 do
            local cur = matLines[m[k]] or 0
            if floor(cur / b) % 2 == 0 then matLines[m[k]] = cur + b end
          end
        end
      end
    end
  end
  return matLines[id]
end
local function rowsFor(line, id)
  local idx = lineIdx[line]
  if not idx then
    idx = {}
    local d = recipes()
    for _, row in ipairs(d and d.r[line] or {}) do
      local m = row[10]
      if type(m) == "table" then
        for k = 1, #m - 1, 2 do
          local l = idx[m[k]]
          if not l then l = {} idx[m[k]] = l end
          l[#l + 1] = row
        end
      end
    end
    lineIdx[line] = idx
  end
  return idx[id]
end
local profMemo, profGen
local myGen = 0
local function myProfs()
  if profMemo and profGen == myGen then return profMemo end
  local P = mod("Prof")
  local list = P and P.ScanProfessions and try(P.ScanProfessions) or {}
  local out = {}
  for _, p in ipairs(type(list) == "table" and list or {}) do if p.line then out[p.line] = tonumber(p.rank) or 0 end end
  profMemo, profGen = out, myGen
  return out
end
local function crafts(row)
  local m, n = row[10] or {}, nil
  for k = 1, #m - 1, 2 do
    local have = API.ItemCount and tonumber(API.ItemCount(m[k])) or 0
    local can = floor(have / max(1, tonumber(m[k + 1]) or 1))
    if not n or can < n then n = can end
  end
  return n or 0
end
local COLOR_WORD = { orange = "monte à coup sûr", yellow = "monte souvent", green = "monte rarement", grey = "ne monte plus" }
function SEC.prof(it, c, o, st)
  -- (4.1 CITYRUN) verdict précis : « Besoin : 12 (il t'en reste 30 → 18 en trop) », « Pas besoin… », « Garde : quête X »
  local CR = T.CityRun
  local okV, v = false, nil
  if CR and type(CR.Verdict) == "function" then okV, v = pcall(CR.Verdict, it.id) end
  if okV and type(v) == "table" and v.text then
    o.label, o.tone, o.text = "Métiers", v.tone or "info", v.text
    for _, dtl in ipairs(v.details or {}) do o.details[#o.details + 1] = dtl end
    st.reagent = true
    if v.keep then st.keep = st.keep or "compo" end
    return
  end
  local mask = linesUsing(it.id)
  local d = recipes()
  if not mask or not d then return end
  local profs = myProfs()
  local P = mod("Prof")
  local known = P and type(P.char) == "table" and type(P.char.known) == "table" and P.char.known or {}
  local mine, reach, lines = {}, {}, {}
  for line, b in pairs(LINE_BIT) do
    if floor(mask / b) % 2 == 1 then
      lines[line] = true
      local rank = profs[line]
      local kn = known[line]
      for _, row in ipairs(rank and rowsFor(line, it.id) or {}) do
        if type(kn) == "table" and kn[row[1]] then mine[#mine + 1] = { line = line, row = row }
        elseif rank >= (tonumber(row[4]) or 1) then reach[line] = (reach[line] or 0) + 1 end
      end
    end
  end
  local function lineName(l) local v = d.lines and d.lines[l] return v and v[2] or ("métier " .. tostring(l)) end
  if #mine > 0 then
    for _, u in ipairs(mine) do u.n = crafts(u.row) end
    tsort(mine, function(a, b) if a.n ~= b.n then return a.n > b.n end return (a.row[5] or 0) > (b.row[5] or 0) end)
    local top = mine[1]
    o.label, o.tone = "Métiers", "info"
    o.text = fmt("compo de %s (×%d possible%s)", q(cut(top.row[9] or "?", 30)), top.n, top.n > 1 and "s" or "")
    if #mine > 1 then o.text = o.text .. fmt(" et %d autre%s recette%s", #mine - 1, #mine > 2 and "s" or "", #mine > 2 and "s" or "") end
    st.keep, st.reagent = st.keep or "compo", true
    local E = P and P.Engine
    for i = 1, min(6, #mine) do
      local u = mine[i]
      local col = E and E.Color and try(E.Color, profs[u.line] or 0, u.row)
      o.details[#o.details + 1] = fmt("%s : ×%d possible%s%s", q(cut(u.row[9] or "?", 32)), u.n, u.n > 1 and "s" or "",
        COLOR_WORD[col] and (" (" .. COLOR_WORD[col] .. ")") or "")
    end
    return
  end
  local mineNames = {}
  for l, n in pairs(reach) do mineNames[#mineNames + 1] = fmt("%s (%d recette%s à ta portée)", lineName(l), n, n > 1 and "s" or "") end
  tsort(mineNames)
  if #mineNames > 0 then
    o.label, o.tone, o.text = "Métiers", "info", "sert en " .. andList(mineNames, 2)
    st.keep, st.reagent = st.keep or "compo", true
    return
  end
  local all = {}
  for l in pairs(lines) do all[#all + 1] = lineName(l) end
  tsort(all)
  if #all == 0 then return end
  o.label, o.tone, o.text = "Métiers", "info", "sert en " .. andList(all, 3)
  o.compact = (it.classID == 7)   -- marchandise : utile à savoir (vendre ou garder) ; le reste : détails seulement
end

-- valeur : marchand (pile), HV seulement si le modèle de liquidité de l'HV dit que ça se vend vraiment
local function soldTxt(x)
  x = tonumber(x) or 0
  if x >= 1 then return tostring(floor(x + 0.5)) end
  return dec(x)
end
function SEC.value(it, c, o, st)
  if it.classID == 12 and it.sell <= 0 then return end
  local count = c.count or 1
  local parts = {}
  if it.sell > 0 then
    parts[1] = count > 1 and fmt("marchand %s (pile ×%d : %s)", money(it.sell), count, money(it.sell * count))
      or ("marchand " .. money(it.sell))
    o.details[#o.details + 1] = fmt("Marchand : %s l'unité%s.", money(it.sell), count > 1 and fmt(", %s la pile de %d", money(it.sell * count), count) or "")
  end
  local tradable = not (it.bind == 1 or it.bind == 4 or c.bound or it.classID == 12)
  local A = mod("Auction")
  local stA, liq
  if tradable and type(A) == "table" and A.Stats then   -- un objet jamais vu à l'HV : rien à dire (et rien à calculer)
    stA = try(A.Stats, it.id)
    if type(stA) ~= "table" or (tonumber(stA.market) or 0) <= 0 then stA = nil end
  end
  if stA and A.Liq then
    liq = try(A.Liq, it.id)
    if type(liq) ~= "table" then liq = nil end
  end
  local reliable = liq and liq.ok and (tonumber(liq.market) or 0) > 0
  if reliable then
    -- (4.1 AHPROFIT) « HV ≈ 1 po 20 pa (vendu ~6/j) · marchand 30 pa → vends à l'HV » : l'HV en premier
    table.insert(parts, 1, fmt("HV ≈ %s (vendu ~%s/j)", money(liq.market), soldTxt(liq.sold)))
    o.details[#o.details + 1] = fmt("HV : %d jours de scans, confiance %d %%.", tonumber(liq.days) or 0, floor((tonumber(liq.conf) or 0) * 100 + 0.5))
  elseif stA then
    parts[#parts + 1] = "HV : pas de marché fiable"
    local r = liq and liq.reasons and liq.reasons[1]
    o.details[#o.details + 1] = fmt("Dernier prix vu ~%s%s%s.", money(stA.market), stA.src and (" (" .. stA.src .. ")") or "",
      (type(r) == "table" and r.text) and (" — " .. r.text) or "")
  end
  local verdict
  if c.merchant or c.reward then verdict = nil   -- pas encore à toi : les faits seulement (prix, marché)
  elseif st.keep then verdict = "Garder"
  elseif st.give and tradable then verdict = "Donne-le à " .. st.give
  elseif reliable then
    local pp = A.PostPrice and try(A.PostPrice, it.id, count)
    if type(pp) == "table" and pp.better == "ah" then
      verdict = "vends à l'HV"
      if pp.unit then o.details[#o.details + 1] = fmt("Prix conseillé : %s l'unité, net ~%s après commission et dépôt.", money(pp.unit), money(pp.net or 0)) end
    elseif it.sell > 0 then verdict = "vends au marchand" end
  elseif it.sell > 0 and (it.quality == 0 or st.useless or (not tradable and (it.classID == 2 or it.classID == 4) and not st.later)) then
    verdict = "Vendre au marchand"
  end
  -- (5.0 BAGS) un objet de tes sacs : UN seul avis « vendre / garder », celui de l'assistant d'inventaire (sa ligne
  -- « TreeAI · Garde : … » juste dessous) — ici les faits seulement (prix marchand, HV)
  local BG = T.Bags
  if verdict and type(BG) == "table" and type(BG.HasTip) == "function" then
    local okB, has = pcall(BG.HasTip, it.id)
    if okB and has then verdict = nil end
  end
  if #parts == 0 and (not verdict or verdict == "Garder") then return end   -- « Garder » seul : les autres lignes disent pourquoi
  -- le prix unitaire seul, le jeu l'écrit déjà (« Prix de vente ») : dans les détails seulement
  if #parts == 1 and count <= 1 and it.sell > 0 and (not verdict or verdict == "Garder") then o.compact = false end
  o.label, o.tone = "Valeur", "info"
  o.text = (#parts > 0 and tconcat(parts, " · ") or "") .. (verdict and ((#parts > 0 and " → " or "→ ") .. verdict) or "")
end

-- sources : taux de drop MESURÉ (Stats, ≥ 10 corps), sinon les monstres de QuestieDB (sans taux), marchands, artisanat,
-- récolte ; ligne courte seulement si l'objet t'est utile (quête, compo, familier) ou si un taux est mesuré
local MIN_CORPSES = 10
local gatherIdx
local function gatherOf(id)
  if not gatherIdx then
    gatherIdx = {}
    local d = T.Data and T.Data.Recipes
    for line, nodes in pairs(d and d.gather or {}) do
      for _, n in ipairs(nodes) do if type(n) == "table" and n[1] and not gatherIdx[n[1]] then gatherIdx[n[1]] = { line, n[2] } end end
    end
  end
  return gatherIdx[id]
end
local function areaName(zone)
  local CM = _G.C_Map
  return zone and CM and CM.GetAreaInfo and str(try(CM.GetAreaInfo, zone)) or nil
end
--- taux de drop MESURÉ : les comptes de Stats (corps fouillés / objet trouvé), sans a priori ni estimation
local function measuredDrop(id)
  local Stt = T.Stats
  local A = type(Stt) == "table" and Stt.Account and try(Stt.Account)
  local drops = type(A) == "table" and type(A.drops) == "table" and A.drops or nil
  if not drops then return nil end
  local best, bk, bo, tk, to = nil, 0, 1, 0, 0
  for _, e in pairs(drops) do
    local r = type(e) == "table" and type(e.i) == "table" and e.i[id]
    if type(r) == "table" then
      local n, k = tonumber(r[1]) or 0, tonumber(r[2]) or 0
      tk, to = tk + k, to + n
      if n >= MIN_CORPSES and str(e.nm) and (not best or k / n > bk / bo) then best, bk, bo = e.nm, k, n end
    end
  end
  if best then return fmt("%s (%d sur %d corps, %d %%)", best, bk, bo, floor(bk * 100 / bo + 0.5)) end
  if to >= MIN_CORPSES then return fmt("tombé %d fois sur %d corps (%d %%)", tk, to, floor(tk * 100 / to + 0.5)) end
  return nil
end
function SEC.source(it, c, o, st)
  local found = {}
  local measured = measuredDrop(it.id)
  if measured then found[#found + 1] = measured end
  local db = questie()
  if db and db.QueryItemSingle then
    local drops = try(db.QueryItemSingle, it.id, "npcDrops")
    if type(drops) == "table" and #drops > 0 then
      if #drops <= 6 and db.QueryNPCSingle then
        local names = {}
        for i = 1, min(#drops, 4) do
          local nm = str(try(db.QueryNPCSingle, drops[i], "name"))
          if nm then names[#names + 1] = nm end
        end
        if #names > 0 and not measured then found[#found + 1] = "butin de " .. andList(names, 2) end
        if #names > 2 then o.details[#o.details + 1] = "Monstres : " .. tconcat(names, ", ") .. "." end
      elseif not measured then
        found[#found + 1] = fmt("butin de %d monstres différents", #drops)
      end
    end
    local vend = try(db.QueryItemSingle, it.id, "vendors")
    if type(vend) == "table" and #vend > 0 and db.QueryNPCSingle then
      local nm = str(try(db.QueryNPCSingle, vend[1], "name"))
      local z = areaName(num(try(db.QueryNPCSingle, vend[1], "zoneID")))
      if nm then found[#found + 1] = "vendu par " .. nm .. (z and (" (" .. z .. ")") or "") .. (#vend > 1 and fmt(" +%d", #vend - 1) or "") end
    end
  end
  local mats = T.Data and T.Data.Recipes and T.Data.Recipes.mats and T.Data.Recipes.mats[it.id]
  if type(mats) == "table" and mats[3] == 1 and not (db and #found > 0 and found[#found]:find("^vendu")) then
    found[#found + 1] = "vendu par les marchands" .. ((tonumber(mats[2]) or 0) > 0 and (" (" .. money(mats[2]) .. ")") or "")
  end
  local cr = T.Data and T.Data.Gear and T.Data.Gear.crafted and T.Data.Gear.crafted[it.id]
  if type(cr) == "table" then
    local pn = T.Data.Gear.profs and T.Data.Gear.profs[cr[1]] or "artisanat"
    found[#found + 1] = fmt("fabriqué (%s %d)", pn, tonumber(cr[2]) or 0)
  end
  local ga = gatherOf(it.id)
  if ga then
    local lines = T.Data.Recipes.lines or {}
    found[#found + 1] = fmt("récolte (%s %d)", lines[ga[1]] and lines[ga[1]][2] or "récolte", tonumber(ga[2]) or 0)
  end
  if #found == 0 then return end
  o.label, o.tone = "Source", "info"
  o.text = found[1]
  for i = 2, #found do o.details[#o.details + 1] = "Aussi : " .. found[i] .. "." end
  o.compact = measured ~= nil or st.quest == "need" or st.reagent or st.keep == "familier"
end

---------------------------------------------------------------------------------------------------
-- Rendu : ≤ 6 lignes (Maj : détails), cache par objet
---------------------------------------------------------------------------------------------------
local ORDER = { "upgrade", "quest", "group", "pet", "prof", "value", "source" }
M.ORDER = ORDER
M.stats = { built = 0, hits = 0, loading = 0 }

local function build(it, c, shift)
  local st, secs, hidden = {}, {}, false
  for _, k in ipairs(ORDER) do
    if opt(k) and SEC[k] then
      local o = { key = k, details = {} }
      local ok, err = pcall(SEC[k], it, c, o, st)
      if not ok then
        st.partial = true
        if T._errorHook then pcall(T._errorHook, err, "ItemInfo:" .. k) end
      elseif o.text and (o.compact ~= false or shift) then
        secs[#secs + 1] = o
      elseif o.text then
        hidden = true   -- ligne gardée pour Maj (sources d'un objet qui ne te sert pas, métiers des autres…)
      end
    end
  end
  while #secs > M.MAX_LINES do table.remove(secs) hidden = true end
  local lines, hasDetails = {}, hidden
  local cap = shift and M.MAX_WITH_DETAILS or M.MAX_LINES
  for _, o in ipairs(secs) do
    local body = o.text:gsub("^ ", "")
    if #lines < cap then
      lines[#lines + 1] = { "|c" .. hex(o.tone) .. o.label .. "|r" .. (body ~= "" and (" : " .. body) or ""), BODY[1], BODY[2], BODY[3] }
    end
    if #o.details > 0 then hasDetails = true end
    if shift then
      for _, dtl in ipairs(o.details) do
        if #lines < cap then lines[#lines + 1] = { "   " .. dtl, DETAIL[1], DETAIL[2], DETAIL[3] } end
      end
    end
  end
  if not shift and hasDetails and opt("shift") and #lines > 0 then
    local l = lines[#lines]
    l[1] = l[1] .. " |cff8c8c8c(Maj : détails)|r"
  end
  return lines, st
end
M.Build = build

local cache, cacheN = {}, 0
local function gens()
  local H = T.Highlight
  local D = type(H) == "table" and H.Data and try(H.Data)
  return (API.BagGen and API.BagGen()) or 0, (T.QuestGen and T:QuestGen()) or 0, (type(D) == "table" and D.ver) or 0, myGen
end
local function bump() myGen = myGen + 1 end
M.Invalidate = bump

-- chargement asynchrone : le jeu rafraîchit lui-même l'infobulle quand l'objet arrive (TOOLTIP_DATA_UPDATE) ;
-- ITEM_DATA_LOAD_RESULT / GET_ITEM_INFO_RECEIVED ne sont écoutés que pendant une attente
local pending, pendingN, loadingSeen = {}, 0, {}
local function stopWaiting()
  pending, pendingN = {}, 0
  M:UnregisterEvent("ITEM_DATA_LOAD_RESULT")
  M:UnregisterEvent("GET_ITEM_INFO_RECEIVED")
end
local function onLoaded(_, _, itemID)
  itemID = num(itemID)
  if not (itemID and pending[itemID]) then return end
  pending[itemID] = nil
  pendingN = max(0, pendingN - 1)
  bump()
  if pendingN == 0 then stopWaiting() end
end
local function requestLoad(id)
  if pending[id] then return end
  if pendingN >= 20 then stopWaiting() end
  pending[id], pendingN = true, pendingN + 1
  local CI = _G.C_Item
  if CI and CI.RequestLoadItemDataByID then try(CI.RequestLoadItemDataByID, id) end
  if pendingN == 1 then
    M:RegisterEvent("ITEM_DATA_LOAD_RESULT", onLoaded)
    M:RegisterEvent("GET_ITEM_INFO_RECEIVED", onLoaded)
  end
end
M.Pending = function() return pending, pendingN end

local function apply(tt, lines)
  if #lines == 0 then return end
  for _, l in ipairs(lines) do try(tt.AddLine, tt, l[1], l[2], l[3], l[4], true) end
  if tt.Show then try(tt.Show, tt) end   -- la taille de l'infobulle suit les lignes ajoutées
end

--- ajoute nos lignes à une infobulle d'objet ; renvoie les lignes (tests) ou nil
function M.Decorate(tt, data)
  if not (M._active and type(tt) == "table" and tt.AddLine) then return nil end
  if tt.IsForbidden and try(tt.IsForbidden, tt) then return nil end
  local id, link, guid = resolve(tt, data)
  if not id then return nil end
  local it = readItem(link or id)
  if not it then
    requestLoad(id)
    if loadingSeen[id] then return nil end
    loadingSeen[id] = true
    M.stats.loading = M.stats.loading + 1
    local lines = { { "TreeAI : chargement des infos de l'objet…", DETAIL[1], DETAIL[2], DETAIL[3] } }
    apply(tt, lines)
    return lines
  end
  it.id, it.link = id, link or it.link
  local c = context(tt, guid, id)
  local shift = opt("shift") and sv(try(_G.IsShiftKeyDown)) and true or false
  local where = (c.equipped and "E") or (c.bag and "b") or (c.loot and "l") or (c.merchant and "m") or (c.reward and "r") or "-"
  local key = tostring(it.link or id) .. "|" .. tostring(c.count or 0) .. (shift and "|S" or "") .. "|" .. where
    .. (c.bound and "|B" or "") .. "|" .. tostring(c.questID or "")
  local g1, g2, g3, g4 = gens()
  local t = now()
  local e = cache[key]
  if e and e[1] == g1 and e[2] == g2 and e[3] == g3 and e[4] == g4 and t - e.t < 60 then
    M.stats.hits = M.stats.hits + 1
  else
    local lines, st = build(it, c, shift)
    e = { g1, g2, g3, g4, t = t, lines = lines, glow = st.glow }
    M.stats.built = M.stats.built + 1
    if not st.partial then
      if cacheN >= 200 then cache, cacheN = {}, 0 end
      if not cache[key] then cacheN = cacheN + 1 end
      cache[key] = e
    end
  end
  apply(tt, e.lines)
  local H = T.Highlight   -- contour de la surbrillance (objet de quête), hors combat comme avant
  if e.glow and not inCombat() and type(H) == "table" and H.TooltipGlow then try(H.TooltipGlow, tt, e.glow[1], e.glow[2], e.glow[3], e.glow[4]) end
  return e.lines
end

--- vrai quand ce module écrit les lignes d'objet (Gear, Highlight et Quests se taisent alors : un seul propriétaire)
function M.Owns() return M._active == true and M:IsEnabled() end

local function onItem(tt, data)
  if tt ~= rawget(_G, "GameTooltip") and tt ~= rawget(_G, "ItemRefTooltip") then return end   -- pas les infobulles de comparaison
  if not M._active then return end
  local ok, err = pcall(M.Decorate, tt, data)
  if not ok and T._errorHook then pcall(T._errorHook, err, "ItemInfo:tooltip") end
end
local hookedBy = setmetatable({}, { __mode = "k" })
--- TooltipDataProcessor (Forever / Mainline) : un post-appel sur les infobulles d'objet (une seule fois par processeur)
function M.HookProcessor(TDP, E)
  if type(TDP) ~= "table" or type(TDP.AddTooltipPostCall) ~= "function" or type(E) ~= "table" or E.Item == nil then return false end
  if not hookedBy[TDP] then
    hookedBy[TDP] = true
    pcall(TDP.AddTooltipPostCall, E.Item, onItem)
  end
  return true
end
local function hookTooltips()
  local En = rawget(_G, "Enum")
  if M.HookProcessor(rawget(_G, "TooltipDataProcessor"), type(En) == "table" and En.TooltipDataType or nil) then return end
  for _, tt in ipairs({ rawget(_G, "GameTooltip"), rawget(_G, "ItemRefTooltip") }) do
    if type(tt) == "table" and tt.HookScript and not hookedBy[tt] then
      hookedBy[tt] = true
      pcall(tt.HookScript, tt, "OnTooltipSetItem", function(self) onItem(self, nil) end)
    end
  end
end

function M:OnEnable()
  hookTooltips()
  for _, ev in ipairs({ "PLAYER_LEVEL_UP", "GROUP_ROSTER_UPDATE", "SKILL_LINES_CHANGED", "TRADE_SKILL_LIST_UPDATE",
      "CHARACTER_POINTS_CHANGED", "PLAYER_REGEN_ENABLED" }) do
    self:RegisterEvent(ev, bump)
  end
  self:RegisterUnitEvent("UNIT_PET", "player", function() bump() end)
end

function M:OnDisable()
  cache, cacheN = {}, 0
  stopWaiting()
end

function M:OnOptionChanged() bump() end
