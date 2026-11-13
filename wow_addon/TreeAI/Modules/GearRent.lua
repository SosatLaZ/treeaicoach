-- TreeAI — Équipement, partie « Stuff rentable » + page + aides en direct (agent GEAR 5.0). Fait partie du module
-- Gear (pas un module de plus). « l'assistant parfait pour avoir un stuff rentable » :
--  • Par emplacement, UN verdict clair : « Équipe » (dans tes sacs) · « Achète » (marchand / HV, dans ton budget) ·
--    « Fais la quête X » · « Donjon X » · « Fais fabriquer » · « Attends le niveau N (meilleur à venir) » · « Garde ».
--    Classé par gain pour le coût (or + temps), avec la durée d'utilité (niveau où un objet nettement meilleur des
--    tables du client devient portable). Un clic = y aller (boussole / recherche HV / page) ou équiper (hors combat).
--  • Budget : ton or moins ce que tu gardes pour la monture du niveau 40 (demandé UNE fois, réglable).
--  • Sacs : les améliorations sont lues en direct (verdict en cache par lien), flèche verte sur le bouton de l'objet
--    (calque ancré, jamais enfant d'un cadre du jeu), et gardées contre la vente (T.Bags.RegisterKeeper).
--  • Page « Équipement » (aides de mise en page de la fenêtre) : 3 gros boutons (Meilleur maintenant · Stuff rentable ·
--    Mon équipement), la spé et sa source, puis la liste ; « Mon équipement » = fiche par emplacement avec barres,
--    les plus faibles d'abord.
-- Rien n'est acheté, choisi ni jeté à ta place. Lua 5.1, aucune globale, API sous pcall.
local tonumber = function(v) return _G.tonumber(v) end
local T = _G.TreeAI
local M = T and T.Gear
if not (T and M and M.Value) then return end
local API = T.API or {}
local G = _G
local V = M.Value

local floor, max, min, abs = math.floor, math.max, math.min, math.abs
local fmt = string.format
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function secret(v) local f = g("issecretvalue") return v ~= nil and type(f) == "function" and try(f, v) and true or false end
local function pub(v) if v == nil or secret(v) then return nil end return v end
local function num(v) v = pub(v) if type(v) == "number" then return v end return nil end
local function str(v) v = pub(v) if type(v) == "string" and v ~= "" then return v end return nil end
local function level() return num(try(g("UnitLevel"), "player")) or 1 end
local function inCombat() return (T.InCombat and T:InCombat()) and true or false end
local function opt(k) return M.db and M.db[k] end
local function itemID(x) if type(x) == "number" then return x end return tonumber(type(x) == "string" and x:match("item:(%d+)") or nil) end
local function money(c)
  local F = T.Fmt
  if F and F.Money then return F.Money(c) end
  c = floor(tonumber(c) or 0)
  local gg, s, k = floor(c / 10000), floor(c / 100) % 100, c % 100
  if gg > 0 then return s > 0 and fmt("%d po %d pa", gg, s) or fmt("%d po", gg) end
  if s > 0 then return fmt("%d pa", s) end
  return fmt("%d pc", k)
end
local ICON = "Interface\\Icons\\"
local SLOT_LIST = { 1, 2, 3, 15, 5, 9, 10, 6, 7, 8, 11, 12, 13, 14, 16, 17, 18 }
local SLOT_FR = M.SLOT_FR
local SLOT_ICON = { [1] = "INV_Helmet_03", [2] = "INV_Jewelry_Necklace_01", [3] = "INV_Shoulder_02", [15] = "INV_Misc_Cape_02",
  [5] = "INV_Chest_Chain", [9] = "INV_Bracer_02", [10] = "INV_Gauntlets_04", [6] = "INV_Belt_01", [7] = "INV_Pants_02",
  [8] = "INV_Boots_05", [11] = "INV_Jewelry_Ring_01", [12] = "INV_Jewelry_Ring_01", [13] = "INV_Jewelry_Talisman_01",
  [14] = "INV_Jewelry_Talisman_01", [16] = "INV_Sword_04", [17] = "INV_Shield_04", [18] = "INV_Weapon_Bow_02" }

---------------------------------------------------------------------------------------------------
-- Budget : ton or moins la réserve « monture du niveau 40 » (ton choix, demandé une fois)
---------------------------------------------------------------------------------------------------
M.MOUNT_LEVEL = 40
M.MOUNT_STEPS = { 0, 500000, 1000000 }   -- 0, 50 po, 100 po (en Classic : monture + apprentissage ≈ 100 po)
function M.Budget()
  local c = type(M.char) == "table" and M.char or {}
  local have = (API.Money and tonumber((try(API.Money)))) or tonumber((try(g("GetMoney")))) or 0
  local reserve = (level() < M.MOUNT_LEVEL + 2) and (tonumber(c.mountReserve) or 0) or 0
  return { money = have, reserve = reserve, free = max(0, have - reserve), asked = c.mountAsked and true or false }
end
function M.SetMountReserve(copper)
  if type(M.char) ~= "table" then return end
  M.char.mountReserve, M.char.mountAsked = max(0, floor(tonumber(copper) or 0)), true
  M:Clear("mount")
  M._rent = nil
end
function M.CycleMountReserve()
  local cur = type(M.char) == "table" and tonumber(M.char.mountReserve) or 0
  local nxt = M.MOUNT_STEPS[1]
  for i, v in ipairs(M.MOUNT_STEPS) do if v == cur then nxt = M.MOUNT_STEPS[i % #M.MOUNT_STEPS + 1] end end
  M.SetMountReserve(nxt)
end
--- demandé UNE fois, entre les niveaux 30 et 39, quand un achat est en vue : ligne cliquable, jamais une fenêtre
function M.AskMountOnce()
  local c = type(M.char) == "table" and M.char or nil
  local lv = level()
  if not c or c.mountAsked or lv < 30 or lv >= M.MOUNT_LEVEL then return false end
  c.mountAsked = true
  M:Suggest("mount", "Garder de l'or pour la monture du niveau 40 ? (clic : 0, 50 ou 100 po)", 1,
    { ttl = 900, cooldown = 0, sound = false,
      tooltip = { "TreeAI ne conseillera un achat d'équipement qu'avec l'or qui reste après cette réserve.",
        "Tu changes le montant quand tu veux (page Équipement, « Stuff rentable »)." },
      onClick = function() M.view = "rent" if T.Window and T.Window.Open then T.Window.Open("gear") end end })
  return true
end

---------------------------------------------------------------------------------------------------
-- Sacs : améliorations lues en direct (cache par lien : une comparaison par objet NOUVEAU, rien sinon)
---------------------------------------------------------------------------------------------------
local equipKnown = {}   -- n° d'objet → équipable ? (un GetItemInfo par objet, une fois)
local bagUp = { list = {}, bySlot = {}, keep = {}, n = 0, at = -1 }
local function isEquip(id, link)
  local e = equipKnown[id]
  if e ~= nil then return e end
  if not API.ItemInfo then return false end
  local _, _, _, _, _, _, _, _, loc = API.ItemInfo(link or id)
  if loc == nil then return false end   -- pas encore en cache : on redemandera
  e = (type(loc) == "string" and loc ~= "" and M.Engine.EQUIP_SLOTS[loc] ~= nil) and true or false
  equipKnown[id] = e
  return e
end
local perBag, dirtyBags, bagsKey = {}, nil, nil   -- par sac : ses améliorations ; dirtyBags = nil → tout relire
--- un sac a changé (BAG_UPDATE) : lui seul sera relu au prochain relevé regroupé
function M.BagDirty(bag)
  bag = tonumber(bag)
  if dirtyBags and bag and bag >= 0 and bag <= 12 then dirtyBags[bag] = true else dirtyBags = nil end
end
--- n° d'objet et lien d'une case de sac, sans table intermédiaire (le n° d'abord : la plupart des objets ne sont pas
--- de l'équipement et s'arrêtent là)
local function slotID(bag, slot)
  local CC = g("C_Container")
  local f = (type(CC) == "table" and CC.GetContainerItemID) or g("GetContainerItemID")
  if type(f) == "function" then return num(try(f, bag, slot)), true end
  local info = API.BagItem and API.BagItem(bag, slot)
  return info and info.itemID, false, info and info.link
end
local function slotLink(bag, slot)
  local CC = g("C_Container")
  local f = (type(CC) == "table" and CC.GetContainerItemLink) or g("GetContainerItemLink")
  if type(f) == "function" then return str(try(f, bag, slot)) end
  local info = API.BagItem and API.BagItem(bag, slot)
  return info and str(info.link)
end
local function scanBag(bag, lv)
  local list = {}
  local n = API.BagSlots and tonumber((try(API.BagSlots, bag))) or 0
  for slot = 1, n do
    local id, direct, l0 = slotID(bag, slot)
    if id and equipKnown[id] ~= false then
      local link = direct and slotLink(bag, slot) or str(l0)
      if link and isEquip(id, link) then
        local sc = M.Score(link)
        if sc and sc.known and sc.usable and sc.kind == "good" and (sc.delta or 0) > 0 then
          list[#list + 1] = { bag = bag, slot = slot, id = id, link = link, sc = sc, wait = sc.req }
        elseif sc and sc.usable == false and sc.later and sc.later - lv <= 5 then
          list[#list + 1] = { bag = bag, slot = slot, id = id, link = link, later = sc.later }
        end
      end
    end
  end
  return list
end
--- améliorations portables (maintenant ou bientôt) dans les sacs → { list = { {bag, slot, id, link, sc} }, bySlot, keep }
--- Coût : seuls les sacs touchés depuis le dernier relevé sont relus ; verdict en cache par lien (GearValue).
function M.BagUpgrades(force)
  local key = (V.EquipGen and V.EquipGen() or 0) * 100003 + (V.Gen and V.Gen() or 0)
  local gen = (API.BagGen and API.BagGen() or 0)
  if not force and not M._bagStale and bagUp.gen == gen and bagsKey == key then return bagUp end
  local all = force or M._bagStale or bagsKey ~= key or dirtyBags == nil or next(dirtyBags) == nil
  M._bagStale = nil
  local lv = level()
  local nb = API.NumBags and tonumber((try(API.NumBags))) or 4
  if all then
    perBag = {}
    for bag = 0, nb do perBag[bag] = scanBag(bag, lv) end
  else
    for bag in pairs(dirtyBags) do perBag[bag] = scanBag(bag, lv) end
  end
  dirtyBags, bagsKey = {}, key
  local out = { list = {}, bySlot = {}, keep = {}, gen = gen, n = 0 }
  for bag = 0, nb do
    for _, e in ipairs(perBag[bag] or {}) do
      if e.sc then
        local sc = e.sc
        out.list[#out.list + 1] = e
        if not sc.req then
          local cur = out.bySlot[sc.slot]
          if not cur or (sc.delta or 0) > (cur.sc.delta or 0) then out.bySlot[sc.slot] = e end
        end
        out.keep[e.id] = sc.req and ("amélioration au niveau " .. sc.req) or "amélioration pour toi"
        out.n = out.n + 1
      elseif e.later then
        out.keep[e.id] = "portable au niveau " .. e.later   -- mailles / plaques proches : ne pas vendre
      end
    end
  end
  bagUp = out
  return out
end
--- garde-fou de vente (contrat BAGS) : nombre à garder et pourquoi, pour un n° d'objet
function M.KeepCount(id)
  local why = bagUp.keep[id]
  if why then return 1, why end
  local GG = T.GroupGear   -- utile à un ami du groupe (échanges conseillés) : jamais vendu non plus
  local tr = GG and GG._active and GG.TradeFor and try(GG.TradeFor, id)
  if type(tr) == "string" then return 1, tr end
  return 0
end
local keeperFor   -- la table T.Bags auprès de laquelle on s'est inscrit (une fois par table)
function M.RegisterKeeper()
  local B = T.Bags
  if type(B) ~= "table" or type(B.RegisterKeeper) ~= "function" then return false end
  if keeperFor == B then return true end
  if pcall(B.RegisterKeeper, "Gear", function(id) return M.KeepCount(id) end) then keeperFor = B return true end
  return false
end

-- flèches vertes sur les boutons de sac (calque à nous, ancré sur le bouton, jamais son enfant)
local arrows, arrowN = {}, 0
local function hideArrows() for i = 1, arrowN do if arrows[i] then arrows[i]:Hide() end end end
-- tant qu'une flèche est affichée : un coup d'œil toutes les 0,5 s (le sac peut se fermer sans passer par nos crochets) ;
-- plus de flèche = plus de minuterie
local arrowWatch
local function watchArrows()
  if arrowWatch or not T.NewTicker then return end
  arrowWatch = T:NewTicker(0.5, function()
    local shown = 0
    for i = 1, arrowN do
      local f = arrows[i]
      if f and f:IsShown() then
        local b = f.btn
        if b and type(b.IsVisible) == "function" and try(b.IsVisible, b) then shown = shown + 1 else f:Hide() end
      end
    end
    if shown == 0 and arrowWatch then arrowWatch:Cancel() arrowWatch = nil end
  end, M)
end
local function arrowAt(i, btn)
  local f = arrows[i]
  if not f then
    if not G.CreateFrame then return end
    f = G.CreateFrame("Frame", nil, G.UIParent)
    f:SetSize(14, 14)
    f.tex = f:CreateTexture(nil, "OVERLAY")
    f.tex:SetAllPoints()
    if not (f.tex.SetAtlas and pcall(f.tex.SetAtlas, f.tex, "bags-greenarrow")) or not (f.tex.GetAtlas and f.tex:GetAtlas()) then
      f.tex:SetTexture("Interface\\Buttons\\Arrow-Up-Up")
      if f.tex.SetVertexColor then f.tex:SetVertexColor(0.2, 1, 0.3) end
    end
    if f.EnableMouse then f:EnableMouse(false) end
    arrows[i] = f
    if i > arrowN then arrowN = i end
  end
  f.btn = btn
  f:ClearAllPoints()
  f:SetPoint("TOPLEFT", btn, "TOPLEFT", 1, -1)
  local lvl = type(btn.GetFrameLevel) == "function" and tonumber((try(btn.GetFrameLevel, btn)))
  local strata = type(btn.GetFrameStrata) == "function" and try(btn.GetFrameStrata, btn)
  if strata and f.SetFrameStrata then pcall(f.SetFrameStrata, f, strata) end
  if lvl and f.SetFrameLevel then pcall(f.SetFrameLevel, f, lvl + 3) end
  f:Show()
  return f
end
--- pose les flèches sur les boutons de sac visibles (rien si aucun sac n'est ouvert) → nombre de flèches
function M.RefreshArrows()
  hideArrows()
  if opt("arrows") == false or not M._active then return 0 end
  local H = T.Highlight
  local btns = type(H) == "table" and type(H.BlizzardBagButtons) == "function" and try(H.BlizzardBagButtons) or nil
  if type(btns) ~= "table" or #btns == 0 then return 0 end
  local up = M.BagUpgrades()
  if up.n == 0 then return 0 end
  local at = {}
  for _, e in ipairs(up.list) do if not e.wait then at[e.bag * 100 + e.slot] = e end end
  local n = 0
  for _, r in ipairs(btns) do
    if at[(tonumber(r[2]) or -9) * 100 + (tonumber(r[3]) or 0)] then
      n = n + 1
      arrowAt(n, r[1])
    end
  end
  if n > 0 then watchArrows() end
  return n
end
local arrowQueued = false
local function arrowsSoon()
  if arrowQueued or opt("arrows") == false then return end
  arrowQueued = true
  T:After(0.15, function() arrowQueued = false pcall(M.RefreshArrows) end)
end
M.ArrowsSoon = arrowsSoon

---------------------------------------------------------------------------------------------------
-- Verdicts par emplacement
---------------------------------------------------------------------------------------------------
local cut = T.Cut or function(x) return tostring(x or "") end   -- (Core) n caractères UTF-8, jamais un accent coupé
--- nom d'objet pour une ligne (≤ 30 caractères) ; jamais « ? » (un nom pas encore reçu = « objet »)
local function nm(x) x = tostring(x or "") if x == "" or x == "?" then return "objet" end return cut(x, 30) end
local function tlen(x) if T.TextLen then return T.TextLen(x) end local _, n = tostring(x):gsub("[^\128-\191]", "") return n end
--- une ligne tient en 96 caractères (règle du panneau) : sinon la dernière parenthèse part (elle est dans l'infobulle),
--- puis le texte est coupé proprement
local function fit(text)
  local max96 = T.LINE_MAX or 96
  if tlen(text) <= max96 then return text end
  text = text:gsub(" %(emplacement vide%)", "", 1)   -- « Taille : (vide) » le dit déjà dans l'infobulle
  if tlen(text) <= max96 then return text end
  local t2 = text:gsub(" %b()$", "")
  if tlen(t2) <= max96 then return t2 end
  return cut(t2, max96 - 1)
end
local VERB = { vendor = "Achète", ah = "Achète à l'HV", quest = "Fais la quête", boss = "Donjon", craft = "Fais fabriquer", drop = "Butin",
  world = "Surveille" }
local FREE = { quest = true, boss = true, drop = true, bag = true }
local function gainOf(cd, slot)
  if cd.cmp == nil then cd.cmp = (M.CompareCand and M.CompareCand(cd, slot)) or false end
  local cmp = cd.cmp
  if cmp and cmp.text then return cmp.text .. (cmp.comfortOnly and " (survie)" or ""), cmp end
  return (M.Verdict(cd.pct)) or "mieux", nil
end
local function lifeText(cd, c)
  if cd.life then return fmt("utile jusqu'au niveau %d", cd.life) end
  return fmt("rien de nettement mieux avant le niveau %d", c.level + c.horizon + 1)
end
local function srcShort(src)
  local t = tostring(src.text or "source")
  if src.kind == "craft" then return t:match("%(([^()]-~%d+)[^()]*%)") or (t:gsub(" — .*$", "")) end
  t = t:gsub("^récompense de ", ""):gsub(" %(dans ton journal%)", ""):gsub(" — .*$", "")
  local q, rest = t:match("^« (.-) »(.*)$")   -- titre de quête long : coupé, le reste (niveau) gardé
  if q then return "« " .. cut(q, 28) .. " »" .. rest end
  return cut(t, 40)
end
--- le verdict d'un emplacement → ligne { slot, action, text, right, tooltip, onClick, ratio, … }
local function verdictFor(slot, plan, bud, bags)
  local c = plan.ctx
  local cur = plan.cur[slot] or {}
  local head = SLOT_FR[slot] or tostring(slot)
  local row = { slot = slot, worn = cur.name, icon = ICON .. (SLOT_ICON[slot] or "INV_Misc_QuestionMark"), kind = "gear" }
  local function done(action, text, right, tip, onClick, hint, gain, cost, mins, prio)
    row.action, row.text, row.right, row.tooltip, row.onClick, row.clickHint = action, fit(text), right, tip, onClick, hint
    row.gain, row.cost, row.min, row.prio = gain or 0, cost or 0, mins or 0, prio or 1
    row.ratio = (gain or 0) / (1 + (mins or 0) + (cost or 0) / max(1, c.copperPerMin))
    return row
  end
  if cur.unknown then
    return done("unknown", fmt("%s : %s — inconnu (stats pas encore lues)", head, nm(cur.name)), nil,
      { "Le jeu n'a pas encore envoyé les stats de cet objet : survole-le une fois, TreeAI recalcule." }, nil, nil, 0, 0, 0, 1)
  end
  -- 1. dans les sacs : équiper (un clic, hors combat)
  local b = bags.bySlot[slot]
  if b then
    local sc = b.sc
    local name = nm(sc.item and sc.item.name)
    local tip = { fmt("%s : %s → %s", head, cur.name or "(vide)", name) }
    for _, l in ipairs(sc.lines or {}) do tip[#tip + 1] = l end
    tip[#tip + 1] = V.ModelText()
    return done("equip", fmt("Équipe %s · %s", name, sc.text or "mieux"), "dans tes sacs", tip,
      function() M.EquipFromBag(b) end, "Clic : l'équiper (hors combat)", sc.delta, 0, 0, 2)
  end
  local nowC, list = plan.now and plan.now[slot], plan.slots[slot] or {}
  if nowC and nowC.nowSrc and nowC.nowSrc.kind == "bag" then nowC = nil end   -- les sacs sont lus en direct ci-dessus
  -- le meilleur à venir (niveau pas encore atteint) parmi les pistes sûres
  local soon
  for _, cd in ipairs(list) do
    if (cd.need or 0) > c.level and cd.src and cd.src.kind ~= "world" and (not soon or cd.score > soon.score) then soon = cd end
  end
  if nowC then
    local src = nowC.nowSrc
    local gain, cmp = gainOf(nowC, slot)
    local cost = tonumber(src.cost) or 0
    local tip = { fmt("%s : %s → %s", head, cur.name or "(vide)", nm(nowC.name)), "Gain : " .. gain, "Où : " .. tostring(src.text or "?") }
    for _, l in ipairs(cmp and cmp.lines or {}) do tip[#tip + 1] = "  " .. l end
    tip[#tip + 1] = lifeText(nowC, c):gsub("^%l", string.upper) .. "."
    local wait = soon and soon.score >= nowC.score * 1.15 and (soon.need - c.level) <= 2
    if not wait and cost > 0 and nowC.life and nowC.life - c.level <= 1 then wait = true end
    if cost > 0 and wait then
      local g2 = soon and (gainOf(soon, slot)) or nil
      local wtext = soon and fmt("%s : attends le niveau %d — %s%s (%s)", head, soon.need, nm(soon.name), g2 and (" · " .. g2) or "", srcShort(soon.src))
        or fmt("%s : attends le niveau %d — %s serait remplacé aussitôt", head, nowC.life, nm(nowC.name))
      return done("wait", wtext, nil,
        { fmt("%s coûte %s et serait remplacé presque tout de suite.", nm(nowC.name), money(cost)),
          soon and fmt("Au niveau %d : %s (%s).", soon.need, nm(soon.name), tostring(soon.src.text)) or "Un objet nettement meilleur arrive au niveau suivant." },
        soon and function() M.GoSource(soon.src, soon) end or nil, soon and "Clic : voir où l'obtenir" or nil, 0, 0, 0, 1)
    end
    if cost > 0 and cost > bud.free then
      local why = bud.reserve > 0 and fmt("il te reste %s après la réserve monture", money(bud.free)) or fmt("tu as %s", money(bud.money))
      tip[#tip + 1] = "Trop cher pour l'instant : " .. why .. "."
      return done("save", fmt("%s : %s · %s — trop cher (%s)", head, nm(nowC.name), gain, money(cost)), money(cost), tip,
        function() M.GoSource(src, nowC) end, "Clic : voir où l'obtenir", 0, cost, src.min, 1)
    end
    local verb = VERB[src.kind] or "Vise"
    local text
    if src.kind == "quest" then text = fmt("%s %s → %s · %s", verb, srcShort(src), nm(nowC.name), gain)
    elseif src.kind == "boss" then text = fmt("%s : %s → %s · %s", verb, tostring(src.text):gsub(" — .*$", ""), nm(nowC.name), gain)
    else text = fmt("%s %s · %s", verb, nm(nowC.name), gain) end
    local right = cost > 0 and money(cost) or nil
    if FREE[src.kind] then tip[#tip + 1] = "Gratuit : il ne coûte que du temps." end
    return done(src.kind == "vendor" and "buy" or src.kind == "ah" and "buy" or src.kind, text, right, tip,
      function() M.GoSource(src, nowC) end, M.GoHint and M.GoHint(src.kind) or "Clic : y aller", nowC.delta or nowC.pct, cost, src.min,
      (nowC.pct or 0) >= 15 and 2 or 1)
  end
  -- portable aujourd'hui, mais sa source ne l'est pas encore (trop cher, donjon / quête trop haut, métier trop bas)
  local later
  for _, cd in ipairs(list) do
    if (cd.need or 0) <= c.level and cd.src and cd.src.kind ~= "world" and cd.src.kind ~= "bag" and (not later or cd.score > later.score) then later = cd end
  end
  -- « attends le niveau N » seulement si l'objet à venir est nettement meilleur que ce qu'on peut viser dès maintenant
  if soon and not (later and (not (cur.id or cur.link) or soon.score < later.score * 1.15)) then
    local gain = gainOf(soon, slot)
    return done("wait", fmt("%s : attends le niveau %d — %s · %s (%s)", head, soon.need, nm(soon.name), gain, srcShort(soon.src)), nil,
      { fmt("%s : %s → %s (niveau %d requis).", head, cur.name or "(vide)", nm(soon.name), soon.need), "Où : " .. tostring(soon.src.text) },
      function() M.GoSource(soon.src, soon) end, "Clic : voir où l'obtenir", 0, 0, 0, 1)
  end
  if later then
    local src = later.src
    local gain, cmp = gainOf(later, slot)
    local cost = tonumber(src.cost) or 0
    local tip = { fmt("%s : %s → %s", head, cur.name or "(vide)", nm(later.name)), "Gain : " .. gain, "Où : " .. tostring(src.text or "?") }
    for _, l in ipairs(cmp and cmp.lines or {}) do tip[#tip + 1] = "  " .. l end
    if (src.kind == "vendor" or src.kind == "ah") and cost > 0 then
      local why = bud.reserve > 0 and fmt("il te reste %s après la réserve monture", money(bud.free)) or fmt("tu as %s", money(bud.money))
      tip[#tip + 1] = "Trop cher pour l'instant : " .. why .. "."
      return done("save", fmt("%s : %s · %s — trop cher (%s)", head, nm(later.name), gain, money(cost)), money(cost), tip,
        function() M.GoSource(src, later) end, "Clic : voir où l'obtenir", 0, cost, src.min, 1)
    end
    local when = (src.kind == "boss" and tonumber(src.dmin)) and fmt("donjon dès le niveau %d", max(1, tonumber(src.dmin) - 2))
      or (src.kind == "quest" and "quête pas encore faisable") or (src.kind == "craft" and "artisan à trouver") or "pas encore faisable"
    tip[#tip + 1] = when:gsub("^%l", string.upper) .. "."
    if src.kind == "craft" then
      return done("later", fmt("%s : à faire fabriquer — %s · %s (%s)", head, nm(later.name), gain, srcShort(src)), nil, tip,
        function() M.GoSource(src, later) end, "Clic : la page Métiers", 0, 0, 0, 1)
    end
    return done("later", fmt("%s : plus tard — %s · %s (%s)", head, nm(later.name), gain, when), nil, tip,
      function() M.GoSource(src, later) end, "Clic : voir où l'obtenir", 0, 0, 0, 1)
  end
  local weak = list[1]
  if weak and weak.src and weak.src.kind == "world" then
    local tradable = weak.bind ~= 1   -- lié quand ramassé : pas à l'hôtel des ventes
    return done("watch", (cur.id or cur.link) and fmt("%s : garde %s — mieux existe (%s), source inconnue de TreeAI", head, nm(cur.name), nm(weak.name))
        or fmt("%s : vide — %s existe, source inconnue de TreeAI", head, nm(weak.name)), nil,
      { "TreeAI ne sait pas où il s'obtient (butin, réputation, JcJ…).", tradable and "Il s'échange : regarde à l'hôtel des ventes." or "Il est lié quand ramassé : pas à l'hôtel des ventes." },
      tradable and function() M.GoSource({ kind = "ah" }, weak) end or nil, tradable and "Clic : le chercher à l'HV (ouverte), sinon la page HV" or nil, 0, 0, 0, 1)
  end
  if not (cur.id or cur.link) then
    return done("keep", fmt("%s : vide — aucune piste sûre pour l'instant", head), nil,
      { fmt("Aucun objet avec une source connue jusqu'au niveau %d dans les objets du jeu.", c.level + c.horizon),
        "Garde un œil sur le butin, les récompenses de quête et l'hôtel des ventes." }, nil, nil, 0, 0, 0, 1)
  end
  return done("keep", fmt("%s : garde %s", head, nm(cur.name)), nil,
    { fmt("Rien de mieux et d'atteignable jusqu'au niveau %d d'après les objets du jeu.", c.level + c.horizon) }, nil, nil, 0, 0, 0, 1)
end

local ORDER = { equip = 1, buy = 2, quest = 2, boss = 2, craft = 2, drop = 2, save = 3, wait = 4, later = 4, watch = 5, unknown = 6, keep = 7 }
--- « Stuff rentable » : une ligne par emplacement, les plus rentables d'abord → rows (+ budget, compte par action)
function M.Rent(plan)
  plan = plan or M.plan
  local bags = M.BagUpgrades()
  local bud = M.Budget()
  local key = tostring(plan and plan.time) .. ":" .. tostring(bags.gen) .. ":" .. bud.free
  if M._rent and M._rent.key == key then return M._rent end
  local rows = { key = key, budget = bud, count = {} }
  if plan then
    local left = bud.free
    local buys = {}
    for _, slot in ipairs(SLOT_LIST) do
      local r = verdictFor(slot, plan, bud, bags)
      rows[#rows + 1] = r
      rows.count[r.action] = (rows.count[r.action] or 0) + 1
      if r.action == "buy" then buys[#buys + 1] = r end
    end
    -- le budget est UN : les achats les plus rentables d'abord, les autres deviennent « plus tard »
    table.sort(buys, function(a, b) if a.ratio ~= b.ratio then return a.ratio > b.ratio end return a.slot < b.slot end)
    for _, r in ipairs(buys) do
      if r.cost <= left then left = left - r.cost
      else
        r.action, r.prio = "save", 1
        r.text = r.text:gsub("^Achète à l'HV ", ""):gsub("^Achète ", "") .. " — après tes autres achats"
        rows.count.buy, rows.count.save = (rows.count.buy or 1) - 1, (rows.count.save or 0) + 1
      end
    end
    rows.left = left
    table.sort(rows, function(a, b)
      local oa, ob = ORDER[a.action] or 9, ORDER[b.action] or 9
      if oa ~= ob then return oa < ob end
      if a.ratio ~= b.ratio then return a.ratio > b.ratio end
      return a.slot < b.slot
    end)
  end
  M._rent = rows
  return rows
end

--- contrat ROUND 11 : le meilleur à faire MAINTENANT, par emplacement (équiper, acheter, quête, donjon, artisanat)
function M.BestNow()
  local out = {}
  local ok, rows = pcall(M.Rent)
  if not ok or type(rows) ~= "table" then return out end
  for _, r in ipairs(rows) do
    local o = ORDER[r.action] or 9
    if o <= 2 then out[#out + 1] = r end
  end
  out.budget = rows.budget
  return out
end

--- équiper un objet des sacs : un clic matériel, hors combat (protégé en combat) → vrai si demandé
function M.EquipFromBag(b)
  if type(b) ~= "table" or not b.link then return false end
  if inCombat() then
    if T.Window and T.Window.Flash then T.Window.Flash("En combat : équipe-le après le combat.", 5) end
    return false
  end
  local CI = g("C_Item")
  local f = (type(CI) == "table" and CI.EquipItemByName) or g("EquipItemByName")
  if type(f) ~= "function" then return false end
  local ok = pcall(f, b.link)
  return ok
end

---------------------------------------------------------------------------------------------------
-- « Mon équipement » : fiche par emplacement, barres, les plus faibles d'abord
---------------------------------------------------------------------------------------------------
function M.Doll(plan)
  plan = plan or M.plan
  local out = {}
  if not plan then return out end
  local w = V.Weights()
  local rent = M.Rent(plan)
  local bySlot = {}
  for _, r in ipairs(rent) do bySlot[r.slot] = r end
  for _, slot in ipairs(SLOT_LIST) do
    local cur = plan.cur[slot] or {}
    local top = max(cur.score or 0, plan.top and plan.top[slot] or 0)
    local b = M.BagUpgrades().bySlot[slot]
    if b and b.sc and b.sc.new then top = max(top, b.sc.new) end
    local ratio = (top > 0) and min(1, (cur.score or 0) / top) or 1
    if not (cur.id or cur.link) then ratio = 0 end   -- emplacement vide : le plus faible de tous
    local it = cur.link and V.Item(cur.link) or nil
    local dur, durMax = try(g("GetInventoryItemDurability"), slot)
    dur, durMax = num(dur), num(durMax)
    local r = bySlot[slot]
    local e = { slot = slot, name = cur.name, score = cur.score or 0, top = top, ratio = cur.unknown and 1 or ratio, unknown = cur.unknown,
      empty = not (cur.id or cur.link), verdict = r, icon = ICON .. (SLOT_ICON[slot] or "INV_Misc_QuestionMark"),
      wear = (dur and durMax and durMax > 0) and floor(dur * 100 / durMax + 0.5) or nil,
      unit = w and w.unit or "pts", src = it and it.src }
    out[#out + 1] = e
  end
  table.sort(out, function(a, b)
    if a.ratio ~= b.ratio then return a.ratio < b.ratio end
    return a.slot < b.slot
  end)
  return out
end

---------------------------------------------------------------------------------------------------
-- Page « Équipement » (aides de mise en page de la fenêtre : Ly.buttons / tiles / header / row / text)
---------------------------------------------------------------------------------------------------
M.view = M.view or "now"
local VIEWS = { { "now", "Meilleur maintenant" }, { "rent", "Stuff rentable" }, { "doll", "Mon équipement" } }
local function W() return T.Window end
local function setView(v) M.view = v local w = W() if w and w.Render then pcall(w.Render) end end
--- les 3 gros boutons du haut de la page
function M.PageTop()
  local out = {}
  for _, v in ipairs(VIEWS) do
    local id, label = v[1], v[2]
    out[#out + 1] = { (M.view == id and "» " or "") .. label, function() setView(id) end,
      id == "now" and "Ce que tu peux équiper, acheter ou aller chercher tout de suite"
        or id == "rent" and "Par emplacement : acheter, faire une quête, attendre ou garder — le plus rentable d'abord"
        or "Ta fiche : chaque emplacement avec sa barre, les plus faibles d'abord" }
  end
  return out
end

local barPool
local function barRow(Ly, e)
  local w = W()
  if not (w and w._pool and w._acquire and w.GetWindow and w._newButton and w._text) then return false end
  barPool = barPool or w._pool("gearbar", function(parent)
    local f = w._newButton(parent)
    f.icon = f:CreateTexture(nil, "ARTWORK")
    f.icon:SetSize(20, 20)
    f.icon:SetPoint("LEFT", f, "LEFT", 6, 0)
    if f.icon.SetTexCoord then f.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92) end
    f.t = w._text(f, "S", "text")
    f.t:SetPoint("TOPLEFT", f, "TOPLEFT", 32, -4)
    if f.t.SetWordWrap then f.t:SetWordWrap(false) end
    f.r = w._text(f, "S", "muted")
    f.r:SetPoint("TOPRIGHT", f, "TOPRIGHT", -8, -4)
    if f.r.SetJustifyH then f.r:SetJustifyH("RIGHT") end
    f.barBg = f:CreateTexture(nil, "BACKGROUND")
    f.barBg:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", 32, 5)
    f.barBg:SetHeight(6)
    f.bar = f:CreateTexture(nil, "ARTWORK")
    f.bar:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", 32, 5)
    f.bar:SetHeight(6)
    f:SetScript("OnClick", function(self) local e2 = self.entry if e2 and e2.onClick then T:UserAction(e2.onClick) if w.Dirty then w.Dirty() end end end)
    f:SetScript("OnEnter", function(self) local e2 = self.entry if e2 and w.Tooltip then w.Tooltip(self, e2.title, e2.tooltip, e2.clickHint) end end)
    f:SetScript("OnLeave", function() if w.HideTooltip then w.HideTooltip() end end)
    return f
  end)
  local win = w.GetWindow()
  local f = win and win.content and w._acquire(barPool, win.content)
  if not f then return false end
  local H = 34
  local bw = max(40, Ly.col - 32 - 12)
  f.entry = e
  f:SetSize(Ly.col, H)
  f.icon:SetTexture(e.icon)
  f.t:SetWidth(Ly.col - 32 - 110)
  f.t:SetText(e.title or "")
  f.r:SetWidth(100)
  f.r:SetText(e.right or "")
  f.barBg:SetWidth(bw)
  if f.barBg.SetColorTexture then f.barBg:SetColorTexture(0.2, 0.2, 0.24, 0.9) end
  f.bar:SetWidth(max(1, bw * max(0, min(1, e.ratio or 0))))
  local rr, gg, bb = 0.3, 0.85, 0.4
  if (e.ratio or 0) < 0.6 then rr, gg, bb = 1, 0.35, 0.3 elseif (e.ratio or 0) < 0.9 then rr, gg, bb = 1, 0.75, 0.2 end
  if f.bar.SetColorTexture then f.bar:SetColorTexture(rr, gg, bb, 1) end
  if w._color and f.bg then w._color(f.bg, w._tok("button", 0.14, 0.15, 0.19, e.onClick and 0.55 or 0.25)) end
  if f.EnableMouse then f:EnableMouse(true) end
  Ly.at(f, 16, Ly.y)
  Ly.rec("gearbar", 16, Ly.y, Ly.col, H, e.title)
  Ly.y = Ly.y + H + 2
  return true
end

local function specBlock(Ly)
  local s = M.Spec()
  local cls = s and s.class
  if not cls then return end
  local names = M.TreeNames(cls)
  local w = V.Weights()
  Ly.row({ id = "gear:spec", text = M.SpecText(s), kind = (s.ask or (s.conf or 1) < 0.6) and "warn" or "info",
    icon = ICON .. "Ability_Marksmanship", right = "changer",
    tooltip = { "TreeAI adapte les poids d'équipement à ta spé.", M.PriorityText and M.PriorityText(M.Ctx()) or nil,
      w and V.ModelText(w) or nil, s.source == "manual" and "Tu l'as choisie : clic pour revenir à la lecture automatique."
        or "Pas la bonne ? Clic : choisir toi-même." },
    clickHint = s.source == "manual" and "Clic : lecture automatique" or "Clic : choisir ma spé",
    onClick = function() if s.source == "manual" then M.SetSpec(nil) M.pickSpec = false else M.pickSpec = not M.pickSpec end end })
  if s.ask or M.pickSpec then
    local tiles = {}
    for i = 1, 3 do
      tiles[i] = { names[i], (s.tree == i) and "ta spé actuelle" or "clic : c'est ma spé", function() M.SetSpec(i) M.pickSpec = false end,
        { "TreeAI comptera ton équipement pour « " .. names[i] .. " »." }, s.tree == i, "Clic : choisir" }
    end
    Ly.tiles(tiles, 3)
  end
end

local function budgetRow(Ly, rows)
  local bud = rows.budget or M.Budget()
  if level() >= M.MOUNT_LEVEL + 2 then
    Ly.row({ id = "gear:budget", text = fmt("Budget : %s", money(bud.money)), kind = "money",
      tooltip = { "Les achats d'équipement ne sont conseillés que dans tes moyens." } })
    return
  end
  local txt
  if bud.reserve > 0 then txt = fmt("Budget : %s libres (%s, dont %s gardés pour la monture)", money(bud.free), money(bud.money), money(bud.reserve))
  else txt = fmt("Budget : %s — rien de gardé pour la monture du niveau 40", money(bud.money)) end
  Ly.row({ id = "gear:budget", text = txt, kind = "money", right = "changer",
    tooltip = { "Garder de côté pour la monture ? 0, 50 ou 100 po (en Classic : monture + apprentissage ≈ 100 po).",
      "Les achats d'équipement ne sont conseillés qu'avec l'or libre." },
    clickHint = "Clic : changer la réserve (0 → 50 → 100 po)", onClick = function() M.CycleMountReserve() end })
end

local function emptyState(Ly)
  local jobText = (M.Busy and M.Busy()) and "TreeAI regarde tes objets… (quelques secondes)"
    or inCombat() and "En combat : TreeAI regardera tes objets juste après."
    or "Pas encore calculé : clic sur « Recalculer »."
  Ly.empty(jobText, { "Recalculer", function() M.MarkDirty() M:Start() end, "Clic : compare ton équipement aux objets du jeu" })
end

--- rendu de la page (appelé par la fenêtre) ; les 3 gros boutons sont posés par la fenêtre (p.top = M.PageTop)
function M.RenderPage(Ly)
  local w = W()
  if not (M._active and w) then
    Ly.empty("Le module Équipement est coupé : « Allumer Équipement », en bas à droite.")
    return
  end
  local q = w.Query and w.Query() or ""
  if q == "" then specBlock(Ly) end
  if w.RepairRow then pcall(w.RepairRow, Ly) end
  local plan = M.plan
  if not plan then
    -- première ouverture : le plan se calcule maintenant (par tranches, hors combat), la page se remplit ensuite
    if not (M.Busy and M.Busy()) and not inCombat() then M.MarkDirty() M:Start() end
    emptyState(Ly)
    return
  end
  local function match(...) return not w.Match or w.Match(...) end
  if M.view == "doll" then
    if Ly.header("Mon équipement — les plus faibles d'abord", nil, nil, ICON .. "INV_Chest_Chain") then
      for _, e in ipairs(M.Doll(plan)) do
        local r = e.verdict
        local title = fmt("%s : %s", SLOT_FR[e.slot] or e.slot, e.unknown and ((e.name or "objet") .. " (inconnu)") or e.name or "(vide)")
        local right = e.unknown and "inconnu" or e.empty and "vide" or fmt("%d %%", floor(e.ratio * 100 + 0.5))
        if e.wear and e.wear < 50 then right = right .. fmt(" · usé %d %%", e.wear) end
        local tip = { e.unknown and "Stats pas encore lues par le jeu : survole l'objet une fois."
          or fmt("Valeur pour ta spé : %s sur %s (le meilleur objet portable connu pour cet emplacement).",
            (fmt("%.1f", e.score):gsub("%.", ",")), (fmt("%.1f", e.top):gsub("%.", ","))) }
        if r then tip[#tip + 1] = "Conseil : " .. tostring(r.text) end
        if e.wear then tip[#tip + 1] = fmt("Usure : %d %% (une pièce à 0 %% ne donne plus ses stats).", e.wear) end
        if match(title, r and r.text) then
          local entry = { title = title, right = right, ratio = e.ratio, icon = e.icon, tooltip = tip, onClick = r and r.onClick,
            clickHint = r and r.clickHint }
          if not barRow(Ly, entry) then Ly.row({ text = title, right = right, tooltip = tip, onClick = entry.onClick, clickHint = entry.clickHint, icon = e.icon }) end
        end
      end
    end
    return
  end
  local rows = M.Rent(plan)
  if M.view == "rent" then
    if q == "" then budgetRow(Ly, rows) end
    local shown = 0
    if Ly.header("Stuff rentable — le plus rentable d'abord", nil, nil, ICON .. "INV_Misc_Coin_02") then
      for _, r in ipairs(rows) do
        if match(r.text, r.right) then Ly.row(r) shown = shown + 1 end
      end
    end
    if shown == 0 then Ly.empty("Rien ne correspond.") end
    return
  end
  -- « Meilleur maintenant »
  local n = 0
  local nowRows = {}
  for _, r in ipairs(rows) do if (ORDER[r.action] or 9) <= 2 and match(r.text, r.right) then nowRows[#nowRows + 1] = r end end
  if #nowRows > 0 and Ly.header("Le meilleur maintenant", nil, #nowRows, ICON .. "INV_Chest_Chain") then
    for _, r in ipairs(nowRows) do Ly.row(r) n = n + 1 end
  end
  local later = (rows.count.wait or 0) + (rows.count.save or 0) + (rows.count.later or 0)
  if n == 0 then
    Ly.empty(later > 0 and fmt("Rien à prendre tout de suite ; %d piste%s à venir dans « Stuff rentable ».", later, later > 1 and "s" or "")
      or "Rien de mieux pour l'instant : ton équipement est le meilleur atteignable à ton niveau.",
      later > 0 and { "Stuff rentable", function() setView("rent") end, "Clic : voir les pistes à venir" } or nil)
  elseif later > 0 then
    Ly.text(fmt("%d autre%s piste%s pour plus tard dans « Stuff rentable ».", later, later > 1 and "s" or "", later > 1 and "s" or ""), "S", "muted")
  end
  if w.PotionRow then pcall(w.PotionRow, Ly) end
  local h = plan.hunter
  if h and q == "" then
    if h.ammoNeed and not h.ammoOK then
      Ly.row({ text = fmt("Munitions : ton arme tire des %s et tu n'en as pas !", h.ammoNeed == 2 and "flèches" or "balles"), prio = 3, kind = "warn" })
    end
    if h.mail and level() >= 37 then Ly.row({ text = h.mail, kind = "info", icon = ICON .. "INV_Chest_Chain_05" }) end
  end
end

--- branche la page sur la fenêtre (la page « gear » existe : seuls son rendu et ses boutons du haut sont remplacés)
function M.HookPage()
  local w = W()
  if not (w and w.AddPage) or M._pageHooked then return M._pageHooked or false end
  w.AddPage({ id = "gear", desc = "Ta spé, ce qui est mieux pour toi, stuff rentable, ta fiche par emplacement",
    top = function() return M._active and M.PageTop() or {} end,
    render = function(Ly) M.RenderPage(Ly) end })
  M._pageHooked = true
  return true
end

function M.RentEnable()
  M.HookPage()
  M.RegisterKeeper()
  local function onBags()
    M._rent = nil
    local up = M.BagUpgrades()
    M.RegisterKeeper()
    if up.n > 0 or arrowN > 0 then arrowsSoon() end
    if M.PublishNow then M.PublishNow() end
  end
  M.OnBagsChanged = onBags
  if T.OnBags then
    local ok = pcall(T.OnBags, T, M, onBags)
    if not ok then M._ownBags = true end
  else
    M._ownBags = true
  end
  M:RegisterEvent("BAG_UPDATE", function(_, _, bag) M.BagDirty(bag) end)   -- note le sac touché, rien d'autre
  if M._ownBags then   -- repli : notre propre regroupement (0,5 s après le dernier événement, jamais dans son image)
    local queued = false
    M:RegisterEvent("BAG_UPDATE_DELAYED", function()
      if queued then return end
      queued = true
      T:After(0.5, function() queued = false if not inCombat() then pcall(onBags) else M._bagsLater = true end end)
    end)
  end
  -- (fin de combat : rappel unique dans Gear.lua → M.RentCombatEnd)
  if type(G.hooksecurefunc) == "function" and not M._bagHooks then
    M._bagHooks = true
    for _, fn in ipairs({ "ToggleBag", "OpenBag", "CloseBag", "OpenAllBags", "ToggleAllBags", "OpenBackpack", "ToggleBackpack", "CloseAllBags", "CloseBackpack" }) do
      if type(rawget(G, fn)) == "function" then pcall(G.hooksecurefunc, fn, function() if M._active then arrowsSoon() end end) end
    end
  end
end
function M.RentCombatEnd()
  if M._bagsLater and M.OnBagsChanged then M._bagsLater = false T:After(1, function() pcall(M.OnBagsChanged) end) end
end
function M.RentDisable()
  hideArrows()
  if arrowWatch then arrowWatch:Cancel() arrowWatch = nil end
end
