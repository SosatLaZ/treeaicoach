local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
-- TreeAI — module Now « Maintenant » (5.1, agent LOOK) : CE QU'IL Y A À FAIRE MAINTENANT, en 5 lignes au plus, chacune
-- avec UN clic qui fait la chose ou qui y mène. Un seul modèle (N.Model) pour :
--  * la page d'accueil de la fenêtre TreeAI (« Maintenant », 5 lignes) ;
--  * le petit bloc posé contre la colonne « Suivi Forever » de Shortest Path Forever (3 lignes, même largeur, même
--    style : titre or, petit texte blanc, fond presque transparent) quand la place est libre. Le bloc est posé sur
--    UIParent à la place LUE de la colonne : jamais ancré à un cadre d'un autre addon, jamais sur un cadre du jeu ;
--    sous la colonne si c'est libre, sinon juste au-dessus, sinon pas de bloc (tout reste dans la fenêtre).
-- Chaque ligne = { id, text, btn, fn, tip, icon, prio } : « quoi », le mot du bouton, l'action. Les lignes viennent
-- des modules qui savent (cerveau des quêtes, Sacs, Équipement, Métiers, Groupe) par leurs contrats publics, gardés.
-- Rien n'est inventé : pas de donnée = pas de ligne. Rien d'urgent = une seule ligne « continue ».
-- « Plus tard » (clic droit sur une question) est retenu par personnage : la question ne revient pas toute seule.
-- Coût : aucun OnUpdate, aucune minuterie au repos ; le bloc se repeint sur événements regroupés (≥ 0,6 s), jamais en
-- combat (il s'estompe) ; la fenêtre le lit au rendu (≤ 1 / s). Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

-- mode sûr : ce module ne fait que lire les contrats des autres et poser un petit cadre à nous → allumé d'office
if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.Now = true end
if type(T.MODULE_INFO) == "table" and not T.MODULE_INFO.Now then
  T.MODULE_INFO.Now = { lite = true, desc = "« Maintenant » : quoi faire tout de suite, 5 lignes au plus, un clic chacune (fenêtre et petit bloc)" }
end

local N = T:RegisterModule("Now", {
  title = "Maintenant",
  description = "Quoi faire maintenant, en 5 lignes au plus, un clic chacune : page d'accueil de la fenêtre et petit bloc contre la colonne Suivi Forever.",
  defaults = { enabled = true, block = true, lines = 3 },
  charDefaults = { later = {} },
  options = {
    { section = "Maintenant", key = "block", type = "toggle", label = "Petit bloc « Maintenant » contre la colonne Suivi Forever (quand la place est libre)" },
    { key = "lines", type = "range", label = "Lignes du petit bloc", min = 1, max = 5, step = 1 },
  },
})
T.Now = N
N.autoIdleSafe = true
N.MAX = 5

local floor, max, min = math.floor, math.max, math.min
local fmt = string.format
local function pass(ok, ...) if ok then return ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw((...), "Now") end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function meth(o, m, ...) local f = type(o) == "table" and o[m] if type(f) == "function" then return pass(pcall(f, o, ...)) end end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function Th() local t = T.Theme if type(t) == "table" and t.Color then return t end end
local function secret(v) local f = rawget(G, "issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function plain(s)
  s = T.PlainText and T.PlainText(tostring(s or "")) or tostring(s or "")
  return (s:gsub("|T.-|t", ""):gsub("%s+", " "):gsub("^%s+", ""):gsub("%s+$", ""))
end
local function mod(name) local m = T.modules and T.modules[name] return (m and m._active) and m or nil end
local function plural(n, one, many) return fmt("%d %s", n, n > 1 and many or one) end
local function money(c) return (T.Fmt and T.Fmt.Money and T.Fmt.Money(c)) or (tostring(floor((tonumber(c) or 0) / 100)) .. " pa") end
local ICON = "Interface\\Icons\\"

-- ===================================================================== « plus tard » (retenu par personnage)
-- (le calme commun : T.Calm de Tour.lua — « plus tard » vaut pour la ligne de connexion, la question et cette ligne)
function N.Later(id)
  local Q = T.Calm
  if type(Q) == "table" and type(Q.IsLater) == "function" then return Q.IsLater(id) end
  return (N.char and type(N.char.later) == "table" and N.char.later[id]) and true or false
end
function N.SetLater(id, on)
  local Q = T.Calm
  if type(Q) == "table" and type(Q.Later) == "function" then Q.Later(id, on)
  elseif N.char then
    if type(N.char.later) ~= "table" then N.char.later = {} end
    N.char.later[id] = on ~= false and 1 or nil
  end
  N.Changed()
end

-- ===================================================================== le modèle : au plus `maxN` lignes, une action chacune
local function go(target) local A = T.Addons if A and A.GoTo then return A.GoTo(target) end end
local function goHow() local A = T.Addons return A and A.GoHow and A.GoHow() or "la boussole t'y mène" end
local function act(fn, ...) local a = { ... } return function() if T.UserAction then T:UserAction(function() fn(unpack(a)) end) else pcall(fn, unpack(a)) end end end

--- les sources, dans l'ordre d'importance ; chacune ajoute 0 ou 1 ligne (tout est gardé : un module absent = rien)
local SOURCES = {}
N.SOURCES = SOURCES
-- 1. l'urgent du moment (danger, mort → corps, sacs pleins, munitions…) : les lignes prio 3 de « Maintenant »
SOURCES[#SOURCES + 1] = function(out)
  local W = T.Window
  if not (W and W.NowActions) then return end
  for _, e in ipairs(try(W.NowActions, 2) or {}) do
    if (e.prio or 1) >= 3 or e.kind == "death" then
      out[#out + 1] = { id = "urgent:" .. tostring(e.id), text = plain(e.text), prio = 3, icon = e.icon,
        btn = e.onClick and (e.btn or "Voir") or nil, fn = e.onClick and function() W.Run(e) end or nil,
        tip = type(e.tooltip) == "table" and e.tooltip or (type(e.tooltip) == "string" and { e.tooltip } or nil), entry = e }
    end
  end
end
-- 2. les quêtes : rendre ce qui est fini, sinon l'étape en cours (avec « Y aller »)
SOURCES[#SOURCES + 1] = function(out)
  local S = T.Shortcuts
  if not (S and S._active and S.GuideOn and S.GuideOn()) then return end
  local nx = S.NextAction and try(S.NextAction) or nil
  if type(nx) == "table" and nx.id and nx.id ~= "suivant" then
    local label = { rendre = "Rendre", prendre = "Prendre", zone = "Ma zone" }
    local txt = nx.why and (nx.why:sub(1, 1):upper() .. nx.why:sub(2)) or "Quêtes"
    out[#out + 1] = { id = "quest:" .. nx.id, text = txt, prio = 2, icon = nx.id == "prendre" and "Interface\\GossipFrame\\AvailableQuestIcon"
      or "Interface\\GossipFrame\\ActiveQuestIcon", btn = label[nx.id] or "Faire", fn = act(S.Do, nx.id),
      tip = { "Un clic : " .. (nx.id == "rendre" and "le trajet passe par les PNJ qui attendent tes quêtes finies." or nx.id == "prendre"
        and "le trajet va chercher les meilleures quêtes près de toi." or "retour aux quêtes de ta zone.") } }
    return
  end
  local W = T.Window
  local it = W and W.NextItem and try(W.NextItem) or nil
  if type(it) == "table" and type(it.title) == "string" and it.title ~= "" then
    local C = T.Compass
    local cur = C and C.Current and try(C.Current) or nil
    local can = type(cur) == "table" and cur.mapID and cur.x and cur.y
    local sub = plain(it.sub)
    out[#out + 1] = { id = "quest:step", text = plain(it.title) .. (sub ~= "" and (" · " .. sub) or ""), prio = 2,
      icon = it.mark == "turn" and "Interface\\GossipFrame\\ActiveQuestIcon" or ICON .. "INV_Misc_Note_02",
      btn = can and "Y aller" or nil, fn = can and act(go, cur) or nil, tip = can and { "Un clic : " .. goHow() .. "." } or nil }
  end
end
-- 3. les sacs : chez le marchand = vendre ; presque pleins = le dire, avec le clic qui ouvre les sacs triés
SOURCES[#SOURCES + 1] = function(out)
  local B = T.Bags
  if not (type(B) == "table" and B._active and B.Summary) then return end
  local s = try(B.Summary)
  if type(s) ~= "table" or not s.ready then return end
  local free = tonumber(s.free)
  local S = T.Shortcuts
  if s.merchant and (s.sellN or 0) > 0 and B.SellClick then
    out[#out + 1] = { id = "bags:sell", text = fmt("Marchand : %s à vendre · %s", plural(s.sellN, "objet", "objets"), money(s.sellValue)), prio = 2,
      icon = ICON .. "INV_Misc_Coin_01", btn = "Vendre", fn = act(B.SellClick),
      tip = { "Un clic : vend ce que personne ne réclame (quêtes, métiers, équipement gardés).", "Annulable : onglet de rachat du marchand." } }
  elseif free and free <= 3 then
    out[#out + 1] = { id = "bags:full", text = fmt("Sacs : %s%s", plural(free, "place libre", "places libres"),
        (s.sellN or 0) > 0 and fmt(" · %s à vendre (%s)", plural(s.sellN, "objet", "objets"), money(s.sellValue)) or ""), prio = free <= 1 and 3 or 2,
      icon = ICON .. "INV_Misc_Bag_08", btn = "Sacs", fn = S and S.Do and act(S.Do, "sacs") or nil,
      tip = { "Un clic : tes sacs s'ouvrent, chaque objet marqué (vendre, garder, HV, banque)." } }
  end
end
-- 4. l'équipement : une pièce meilleure dans les sacs = l'équiper ; sinon la 1re pièce à aller chercher
SOURCES[#SOURCES + 1] = function(out)
  local Gm = mod("Gear")
  if not (Gm and Gm.NowRows) then return end
  local rows = try(Gm.NowRows)
  local r = type(rows) == "table" and rows[1] or nil
  if type(r) ~= "table" or type(r.onClick) ~= "function" then return end
  local bag = r.srcKind == "bag"
  out[#out + 1] = { id = "gear:" .. tostring(r.slot), text = (bag and "À équiper : " or "Mieux pour toi : ") .. plain(r.text), prio = bag and 2 or 1,
    icon = ICON .. "INV_Chest_Chain", btn = bag and "Équiper" or "Y aller", fn = act(r.onClick, r),
    tip = type(r.tooltip) == "table" and r.tooltip or nil }
end
-- 5. les réparations : usure forte = le réparateur le plus proche
SOURCES[#SOURCES + 1] = function(out)
  local Inv = mod("Inventory")
  local W = T.Window
  if not (Inv and Inv.Durability and W and W.GoRepair) then return end
  local p, broken = try(Inv.Durability)
  p, broken = tonumber(p), tonumber(broken) or 0
  if not p or (p > 30 and broken == 0) then return end
  out[#out + 1] = { id = "gear:repair", text = broken > 0 and fmt("Réparations : %s", plural(broken, "pièce cassée", "pièces cassées"))
      or fmt("Réparations : équipement à %d %%", p), prio = (broken > 0 or p <= 15) and 3 or 2, icon = ICON .. "Trade_BlackSmithing",
    btn = "Réparateur", fn = act(W.GoRepair), tip = { "Un clic : " .. goHow() .. " (réparateur le plus proche connu)." } }
end
-- 6. le groupe : des quêtes à partager
SOURCES[#SOURCES + 1] = function(out)
  local W = T.Window
  if not (W and W.ShareList and W.ShareAll) then return end
  local list = try(W.ShareList)
  local n = type(list) == "table" and #list or 0
  if n == 0 then return end
  out[#out + 1] = { id = "group:share", text = fmt("Groupe : %s à partager", plural(n, "quête", "quêtes")), prio = 1,
    icon = ICON .. "Spell_Holy_PrayerOfHealing", btn = "Partager", fn = act(W.ShareAll),
    tip = { "Un clic : partage avec ton groupe les quêtes qu'un membre n'a pas." } }
end
-- 7. les métiers : des achats en attente dans le plan (sauf si SkillUp tient déjà la liste)
SOURCES[#SOURCES + 1] = function(out)
  local S, A = T.Shortcuts, T.Addons
  if not (S and S.ProfBuys and S.HasProf and S.HasProf()) then return end
  if A and A.Link and A.Link("skillup", "prof") then return end
  local n = tonumber(S.ProfBuys())
  if not n or n <= 0 then return end
  out[#out + 1] = { id = "prof:buys", text = fmt("Métier : %s en attente", plural(n, "achat", "achats")), prio = 1,
    icon = ICON .. "Trade_BlackSmithing", btn = "Courses", fn = act(S.Do, "metiers"),
    tip = { "Un clic : la session métier (le bon PNJ, étape par étape)." } }
end
-- les questions (spé, réglage) : jamais pendant la 1re minute ni en combat, jamais une fois remises à plus tard
local function canAsk(kind)
  local Q = T.Calm
  if type(Q) == "table" and type(Q.Can) == "function" then return Q.Can(kind) and true or false end
  return not N.Later(kind)
end
-- 8. une question à la fois (jamais une fenêtre) : ta spé si elle n'a pas pu être lue ; clic droit = plus tard
SOURCES[#SOURCES + 1] = function(out)
  if not canAsk("spec") then return end
  local Gm = T.Gear
  local s = type(Gm) == "table" and Gm.Spec and try(Gm.Spec) or nil
  if type(s) ~= "table" or not s.ask then return end
  local W = T.Window
  out[#out + 1] = { id = "ask:spec", text = "Spé illisible : dis-la une fois", prio = 1, icon = ICON .. "INV_Misc_Book_08", ask = "spec",
    btn = "Choisir", fn = W and W.Open and act(W.Open, "gear") or nil,
    tip = { "TreeAI règle ses conseils d'équipement sur ta spé.", "Clic droit : plus tard (la question ne reviendra pas toute seule)." } }
end
-- 9. le réglage en 1 minute, tant qu'il n'a été ni fait ni remis à plus tard
SOURCES[#SOURCES + 1] = function(out)
  if not canAsk("setup") or not (T.db and not T.db.onboarded and T.ShowWizard) then return end
  out[#out + 1] = { id = "ask:setup", text = "Réglage en 1 minute : profil, affichage, touches", prio = 1, icon = ICON .. "INV_Misc_Gear_01", ask = "setup",
    btn = "Régler", fn = act(function() T:ShowWizard(1) end),
    tip = { "Trois étapes, « Passer » à tout moment.", "Clic droit : plus tard (/tai bienvenue le rouvre)." } }
end

local memo, memoAt, memoGen = nil, -10, -1
--- les lignes de « Maintenant » : au plus maxN (5), les plus importantes d'abord ; vide = rien d'urgent
function N.Model(maxN)
  maxN = max(1, min(N.MAX, tonumber(maxN) or N.MAX))
  local t = tonumber(try(rawget(G, "GetTime"))) or 0
  local gen = T.DataGen and T:DataGen() or 0
  if not (memo and t - memoAt < 1 and gen == memoGen) then
    local out = {}
    for _, src in ipairs(SOURCES) do
      local ok = pcall(src, out)
      if not ok and T.Debug then T:Debug("Now", "source en erreur") end
    end
    -- une ligne illisible (valeur secrète) n'est jamais montrée ; UNE question à la fois ; l'ordre : priorité, puis
    -- l'ordre des sources
    local keep, asked = {}, false
    for i, l in ipairs(out) do
      if type(l.text) == "string" and l.text ~= "" and not secret(l.text) and not (l.ask and asked) then
        if l.ask then asked = true end
        l.order = i
        keep[#keep + 1] = l
      end
    end
    table.sort(keep, function(a, b) if (a.prio or 1) ~= (b.prio or 1) then return (a.prio or 1) > (b.prio or 1) end return a.order < b.order end)
    memo, memoAt, memoGen = keep, t, gen
  end
  local res = {}
  for i = 1, min(maxN, #memo) do res[i] = memo[i] end
  return res, #memo
end
function N.Forget() memo = nil end
--- fait l'action d'une ligne (clic), ou la remet à plus tard (clic droit sur une question)
function N.Run(line, button)
  if type(line) ~= "table" then return false end
  if button == "RightButton" then
    if line.ask then N.SetLater(line.ask, true) return "later" end
    if line.entry and T.Window and T.Window.Run then T.Window.Run(line.entry, "RightButton") N.Changed() return "dismiss" end
    return false
  end
  if type(line.fn) ~= "function" then return false end
  local ok = pcall(line.fn)
  N.Changed()
  return ok and "done" or false
end
--- ce que le joueur voit (tests, research/look6.md) : « texte  [Bouton] » par ligne
function N.Dump(maxN)
  local out = {}
  for _, l in ipairs((N.Model(maxN))) do out[#out + 1] = l.text .. (l.btn and ("  [" .. l.btn .. "]") or "") end
  if #out == 0 then out[1] = N.IdleText() end
  return out
end
function N.IdleText()
  local S = T.Shortcuts
  if S and S.GuideOn and not S.GuideOn() then return "Guidage coupé : tu joues à ta façon. Rien d'urgent." end
  return "Rien d'urgent : continue."
end

-- ===================================================================== le petit bloc (contre la colonne Suivi Forever)
local block, rows, title, rule, more = nil, {}, nil, nil, nil
local ROW_H, HEAD_H, PAD = 15, 19, 10
local function blockWanted()
  if not (N._active and N.db and N.db.block ~= false) then return false end
  local A = T.Addons
  return (A and A.Link and A.Link("spf", "dock")) and true or false
end
local function color(tex, r, g, b, a)
  if not tex then return end
  if type(tex.SetColorTexture) == "function" then meth(tex, "SetColorTexture", r, g, b, a or 1)
  else meth(tex, "SetTexture", "Interface\\Buttons\\WHITE8X8") meth(tex, "SetVertexColor", r, g, b, a or 1) end
end
local function fstring(parent, size, token)
  local th = Th()
  local fs = th and th:Text(parent, size, token) or meth(parent, "CreateFontString", nil, "OVERLAY", "GameFontHighlightSmall")
  if fs then meth(fs, "SetWordWrap", false) meth(fs, "SetJustifyH", "LEFT") end
  return fs
end
local function hideTip() local tt = G.GameTooltip if tt then meth(tt, "Hide") end end
local function rowTip(self)
  local l = self.line
  local tt = G.GameTooltip
  if not (l and tt) then return end
  meth(tt, "SetOwner", self, "ANCHOR_RIGHT")
  meth(tt, "AddLine", l.text, 1, 1, 1, true)
  for _, s in ipairs(type(l.tip) == "table" and l.tip or {}) do
    if type(s) == "string" then meth(tt, "AddLine", s, 0.8, 0.8, 0.8, true) elseif type(s) == "table" and s[1] then meth(tt, "AddLine", tostring(s[1]), 0.8, 0.8, 0.8, true) end
  end
  if l.btn then meth(tt, "AddLine", "Clic : " .. l.btn, 0.45, 0.74, 1) end
  meth(tt, "Show")
end
local function ensureBlock()
  if block or type(G.CreateFrame) ~= "function" then return block end
  local ok, f = pcall(G.CreateFrame, "Frame", "TreeAINow", G.UIParent)
  if not ok or not f then return nil end
  block = f
  meth(f, "SetSize", 240, HEAD_H + ROW_H + PAD)
  meth(f, "SetFrameStrata", "LOW")
  meth(f, "SetClampedToScreen", true)
  f.bg = meth(f, "CreateTexture", nil, "BACKGROUND")
  if f.bg then meth(f.bg, "SetAllPoints", f) color(f.bg, 0, 0, 0, 0.20) end
  -- en-tête comme une section de la colonne : titre or, à droite « TreeAI » (clic = la fenêtre), un trait fin dessous
  title = fstring(f, "M", "gold")
  if title then meth(title, "SetPoint", "TOPLEFT", f, "TOPLEFT", PAD, -3) meth(title, "SetText", "Maintenant") end
  local okB, mb = pcall(G.CreateFrame, "Button", "TreeAINowOpen", f)
  if okB and mb then
    more = mb
    meth(mb, "SetSize", 46, HEAD_H - 4)
    meth(mb, "SetPoint", "TOPRIGHT", f, "TOPRIGHT", -PAD + 2, -2)
    mb.t = fstring(mb, "S", "muted")
    if mb.t then meth(mb.t, "SetPoint", "RIGHT", mb, "RIGHT", -2, 0) meth(mb.t, "SetJustifyH", "RIGHT") meth(mb.t, "SetText", "TreeAI") end
    meth(mb, "SetScript", "OnClick", function() local W = T.Window if W and W.Toggle then T:UserAction(function() W.Toggle("home") end) end end)
    meth(mb, "SetScript", "OnEnter", function(self)
      local tt = G.GameTooltip
      if not tt then return end
      meth(tt, "SetOwner", self, "ANCHOR_RIGHT")
      meth(tt, "AddLine", "TreeAI · Maintenant", 1, 0.82, 0.2)
      meth(tt, "AddLine", "Clic : la fenêtre TreeAI (tout le reste y est).", 0.8, 0.8, 0.8, true)
      meth(tt, "AddLine", "Ce bloc se coupe dans Réglages > Autres addons.", 0.6, 0.6, 0.6, true)
      meth(tt, "Show")
    end)
    meth(mb, "SetScript", "OnLeave", hideTip)
  end
  rule = meth(f, "CreateTexture", nil, "ARTWORK")
  if rule then
    meth(rule, "SetHeight", 1)
    meth(rule, "SetPoint", "TOPLEFT", f, "TOPLEFT", PAD, -HEAD_H + 1)
    meth(rule, "SetPoint", "TOPRIGHT", f, "TOPRIGHT", -PAD, -HEAD_H + 1)
    color(rule, 1, 0.82, 0.2, 0.22)
  end
  meth(f, "Hide")
  return block
end
local function row(i)
  local r = rows[i]
  if r then return r end
  local ok, b = pcall(G.CreateFrame, "Button", "TreeAINowRow" .. i, block)
  if not ok or not b then return nil end
  r = b
  rows[i] = r
  meth(r, "SetHeight", ROW_H)
  meth(r, "RegisterForClicks", "LeftButtonUp", "RightButtonUp")
  local hl = meth(r, "CreateTexture", nil, "HIGHLIGHT")
  if hl then meth(hl, "SetAllPoints", r) color(hl, 1, 1, 1, 0.07) end
  r.btn = fstring(r, "N", "accent")
  if r.btn then meth(r.btn, "SetPoint", "RIGHT", r, "RIGHT", -PAD, 0) meth(r.btn, "SetJustifyH", "RIGHT") end
  r.text = fstring(r, "N", "text")
  if r.text then
    meth(r.text, "SetPoint", "LEFT", r, "LEFT", PAD, 0)
    meth(r.text, "SetPoint", "RIGHT", r.btn or r, r.btn and "LEFT" or "RIGHT", -6, 0)
  end
  meth(r, "SetScript", "OnClick", function(self, button) hideTip() N.Run(self.line, button) end)
  meth(r, "SetScript", "OnEnter", rowTip)
  meth(r, "SetScript", "OnLeave", hideTip)
  r.entry = { id = "mod:now:" .. i }   -- (l'orage de clics du harnais laisse ces lignes : elles agissent pour de bon)
  return r
end
function N.Block() return block end
function N.BlockRows() return rows end

--- où poser le bloc : sous la colonne si la place est libre, sinon juste au-dessus ; nil = pas de place
function N.Spot(w, h)
  local A = T.Addons
  local col = A and A.Column and A.Column() or nil
  if not col then return nil end
  local W, H
  if T._ScreenSize then W, H = T._ScreenSize() end
  if not (W and H) then return nil end
  local gap = 6
  local function free(l, b)
    if l < 0 or b < 2 or l + w > W or b + h > H - 2 then return false end
    if type(T.SpotIsFree) ~= "function" then return true end
    -- la colonne elle-même est un obstacle connu : on teste la place rétrécie d'un pixel côté colonne
    local ok, isFree = pcall(T.SpotIsFree, T, l, b, w, h, 0)
    return ok and isFree and true or false
  end
  local under = { l = col.l, b = col.b - gap - h }
  if free(under.l, under.b) then return under.l, under.b, "under", col end
  local above = { l = col.l, b = col.b + col.h + gap }
  if free(above.l, above.b) then return above.l, above.b, "above", col end
  return nil
end
local lastSig
--- repeint et repose le bloc (hors combat) ; le cache s'il n'a rien à dire ou pas de place
function N.RenderBlock()
  if not blockWanted() then if block then meth(block, "Hide") end lastSig = nil return false end
  if inCombat() then return false end
  local A = T.Addons
  local col = A and A.Column and A.Column() or nil
  if not col then if block then meth(block, "Hide") end lastSig = nil return false end
  if not ensureBlock() then return false end
  local lines = N.Model(tonumber(N.db.lines) or 3)
  if #lines == 0 then meth(block, "Hide") lastSig = nil return false end
  -- même taille réelle que la bande (plancher en pixels réels aux petites échelles d'interface) ; largeur = la colonne
  local Sc = T.Shortcuts
  local k = (Sc and Sc.AutoScale and tonumber(try(Sc.AutoScale))) or 1
  if not (k and k > 0.5 and k < 2) then k = 1 end
  meth(block, "SetScale", k)
  local wReal = max(180, min(340, floor(col.w + 0.5)))
  local w = floor(wReal / k + 0.5)
  local h = HEAD_H + #lines * ROW_H + 3
  local l, b, where = N.Spot(wReal, h * k)
  if not l then meth(block, "Hide") lastSig = nil return false end
  local parts = { where, floor(l), floor(b), w }
  for i, line in ipairs(lines) do
    local r = row(i)
    if r then
      r.line = line
      meth(r, "ClearAllPoints")
      meth(r, "SetPoint", "TOPLEFT", block, "TOPLEFT", 0, -HEAD_H - (i - 1) * ROW_H)
      meth(r, "SetPoint", "TOPRIGHT", block, "TOPRIGHT", 0, -HEAD_H - (i - 1) * ROW_H)
      if r.btn then meth(r.btn, "SetText", line.btn and (line.btn .. " »") or "") end
      if r.text then
        meth(r.text, "SetText", line.text)
        local th = Th()
        if th then meth(r.text, "SetTextColor", th:Color((line.prio or 1) >= 3 and "prio3" or "text")) end
      end
      meth(r, "Show")
      parts[#parts + 1] = line.text .. "|" .. tostring(line.btn)
    end
  end
  for i = #lines + 1, #rows do if rows[i] then meth(rows[i], "Hide") end end
  local sig = table.concat(parts, "\n")
  if sig ~= lastSig then
    lastSig = sig
    meth(block, "SetSize", w, h)
    meth(block, "ClearAllPoints")
    -- posé sur UIParent à la place LUE : jamais ancré au cadre de l'autre addon
    meth(block, "SetPoint", "BOTTOMLEFT", G.UIParent, "BOTTOMLEFT", floor(l / k + 0.5), floor(b / k + 0.5))
  end
  meth(block, "SetAlpha", 1)
  meth(block, "Show")
  return true
end
local pending = false
--- quelque chose a changé : un seul repeint, 0,6 s plus tard (regroupé) ; la fenêtre ouverte se redessine aussi
function N.Changed()
  memo = nil
  local W = T.Window
  if W and W.IsOpen and W.IsOpen() and W.Dirty then pcall(W.Dirty) end
  if pending or not N._active then return end
  pending = true
  T:After(0.6, function()
    pending = false
    if N._active then pcall(N.RenderBlock) end
  end)
end

function N:OnEnable()
  local function soon() N.Changed() end
  if type(T.OnQuestLog) == "function" then pcall(T.OnQuestLog, T, self, soon) end
  if type(T.OnBags) == "function" then pcall(T.OnBags, T, self, soon) end
  if type(T.OnRoster) == "function" then pcall(T.OnRoster, T, self, soon) end
  for _, ev in ipairs({ "ZONE_CHANGED_NEW_AREA", "MERCHANT_SHOW", "MERCHANT_CLOSED", "UI_SCALE_CHANGED", "DISPLAY_SIZE_CHANGED", "PLAYER_ENTERING_WORLD" }) do
    self:RegisterEvent(ev, soon)
  end
  for _, msg in ipairs({ "QUEST_MODE_CHANGED", "QUEST_GUIDE_CHANGED", "NEXT_CHANGED", "ADDON_LINKS", "LAYOUT_DONE", "FOCUS_CHANGED" }) do self:On(msg, soon) end
  -- en combat : le bloc s'estompe (rien n'est recalculé) ; à la sortie il se repeint
  self:On("COMBAT_START", function() if block and meth(block, "IsShown") then meth(block, "SetAlpha", 0.3) end end)
  self:On("COMBAT_END", soon)
  -- les colonnes des autres addons se posent dans les secondes qui suivent la connexion : deux regards, puis plus rien
  T:After(4, soon)
  T:After(14, soon)
  if T.RegisterMovable then pcall(function() ensureBlock() end) end
end
function N:OnDisable()
  if block then meth(block, "Hide") end
  lastSig, memo = nil, nil
end
function N:OnOptionChanged() lastSig = nil N.Changed() end

T:RegisterSlash("maintenant", function()
  local W = T.Window
  if W and W.Open then W.Open("home") end
end, "maintenant : quoi faire tout de suite (5 lignes, un clic chacune)")
