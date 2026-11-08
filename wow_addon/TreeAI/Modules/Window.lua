-- TreeAI — module Window (agent UIREDO) : la fenêtre principale « TreeAI » et la carte « Prochaine étape ».
-- Inspiré des addons que le joueur aime (motifs seulement, aucun code ni image repris) :
--  * Leatrix Plus : fenêtre sombre, titre + version en haut à gauche, liste de pages à gauche (ligne choisie
--    surlignée), grand titre de page, recherche en haut à droite, UN bouton d'action en bas à droite ;
--  * Adventure Guide : carte d'en-tête (icône, « Histoire : zone », « n en cours · m proches · durée »),
--    étapes numérotées (pastilles rondes, ! / ?, sous-titre gris), quêtes à venir aux couleurs de niveau,
--    sections repliables (« Nettoyer mon journal + », « Direction <zone> + ») ;
--  * Journal des donjons : cartes par donjon (dégradé de couleur + icône, jamais d'illustration copiée),
--    badge de niveau, compteur « x/y quêtes ».
-- La carte « Prochaine étape » remplace le panneau texte toujours ouvert : petite, posée à la place libre que le
-- placement automatique a trouvée (UI.lua, AutoLayout → rôle « panel »), jamais sur un suivi de quêtes.
-- Rendu seulement : les lignes viennent des contrats existants (GetTabLines / Route:Stops / Planner / Quests /
-- Dungeon / Hub) ; rien n'est calculé deux fois. Cadres à nous (enfants d'UIParent, nommés TreeAI…), rien de
-- protégé ; un clic = l'action de la ligne (T:UserAction). Coût : fenêtre fermée = 0 ; carte = signature
-- comparée, redessin seulement quand le texte change (≤ 1 / 2 s), aucune minuterie (suit les rendus du panneau).
-- Pages : Modules/WindowPages.lua (Quêtes, Trajet, Donjons).
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G
local L = T.L or setmetatable({}, { __index = function(_, k) return k end })

local W = T:RegisterModule("Window", {
  title = "Fenêtre TreeAI",
  description = "La fenêtre « TreeAI » (pastille de la minimap ou touche) et la petite carte « Prochaine étape ».",
  defaults = { enabled = true, card = true, replacePanel = true, cardSteps = 3, cardScale = 100 },
  charDefaults = { page = "now", collapsed = {}, dungeon = nil },
  options = {
    { section = "Carte « Prochaine étape »", key = "card", type = "toggle", label = "Afficher la carte « Prochaine étape »" },
    { key = "replacePanel", type = "toggle", label = "La carte remplace le panneau texte (le panneau reste dans Outils)" },
    { key = "cardSteps", type = "range", label = "Étapes sur la carte", min = 1, max = 5, step = 1 },
    { key = "cardScale", type = "range", label = "Taille de la carte (%)", min = 70, max = 150, step = 5 },
  },
})
T.Window = W

local fmt, floor, ceil, max, min = string.format, math.floor, math.ceil, math.max, math.min
local tconcat, tsort = table.concat, table.sort
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function meth(o, m, ...) local f = type(o) == "table" and o[m] if type(f) == "function" then return pass(pcall(f, o, ...)) end end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function Th() local t = T.Theme if type(t) == "table" and t.Color then return t end end
local function plain(s) return T.PlainText and T.PlainText(s) or tostring(s or "") end
local function strip(s) return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")) end
local function ulen(s) local _, n = strip(s):gsub("[^\128-\191]", "") return n end
local function low(s) return strip(s):lower() end
local WHITE = "Interface\\Buttons\\WHITE8X8"
local ICON = "Interface\\Icons\\"
local ROUND = "Interface\\CHARACTERFRAME\\TempPortraitAlphaMask"
W.ICON_ROUND = ROUND

-- ===================================================================== jetons de style (Theme, sinon repli)
local S = {}
W.STYLE = S
local function tok(name, r, g, b, a)
  local th = Th()
  if th then return th:Color(name) end
  return r, g, b, a or 1
end
local function color(tex, r, g, b, a)
  if not tex then return end
  if type(tex.SetColorTexture) == "function" then meth(tex, "SetColorTexture", r, g, b, a or 1)
  else meth(tex, "SetTexture", WHITE); meth(tex, "SetVertexColor", r, g, b, a or 1) end
end
local FONT = { S = "GameFontHighlightSmall", M = "GameFontHighlight", L = "GameFontNormalLarge", H = "GameFontNormalHuge" }
local LINE_H = { S = 13, M = 15, L = 19, H = 24 }
local AVG = { S = 5.4, M = 6.2, L = 8.2, H = 10.5 }
local function text(parent, size, tokName, layer)
  local th = Th()
  local fs
  if th and size ~= "H" then fs = th:Text(parent, size, tokName, layer)
  else
    fs = meth(parent, "CreateFontString", nil, layer or "OVERLAY", FONT[size] or FONT.M)
    if fs and th and tokName then meth(fs, "SetTextColor", th:Color(tokName)) end
  end
  if fs then meth(fs, "SetJustifyH", "LEFT"); meth(fs, "SetJustifyV", "TOP") end
  return fs
end
--- hauteur d'un texte à largeur fixe : la mesure du jeu, jamais moins que l'estimation
local function measure(fs, str, width, size)
  local lh = LINE_H[size or "M"] or 14
  local est = max(1, ceil(ulen(str) * (AVG[size or "M"] or 6) / max(40, width - 6))) * lh
  local got = tonumber(meth(fs, "GetStringHeight")) or 0
  return max(est, got, lh)
end
W.Measure = measure
local SP = { xs = 2, s = 4, m = 8, l = 12, xl = 16 }
local BLOCK = (T.Theme and T.Theme.BLOCK) or { hero = 66, step = 40, quest = 38, row = 24, header = 24, badge = 26, gap = 8 }
W.BLOCK = BLOCK
local function sp() local th = Th() return th and th.SPACE or SP end

-- ===================================================================== actions et infobulles (même contrat que le panneau)
local function isSuggestion(e) return e and e.source == nil and e.time ~= nil end
function W.Run(e, button)
  if type(e) ~= "table" then return end
  if button == "RightButton" then
    if e.onRightClick then T:UserAction(e.onRightClick, e)
    elseif isSuggestion(e) and T.DismissSuggestion then T:DismissSuggestion(e.id) end
  elseif e.onClick then
    T:UserAction(e.onClick, e)
  end
  W.Dirty()
end
local function clickHint(e)
  if e.clickHint then return e.clickHint end
  if e.onClick then return "Clic : " .. (e.actionText or "faire cette action") end
  return nil
end
function W.Tooltip(owner, title, lines, hint)
  local tt = G.GameTooltip
  if not tt then return end
  meth(tt, "SetOwner", owner, "ANCHOR_RIGHT")
  meth(tt, "AddLine", strip(title or ""), 1, 0.82, 0.2, true)
  if type(lines) == "string" then
    for l in lines:gmatch("[^\n]+") do meth(tt, "AddLine", l, 0.85, 0.85, 0.85, true) end
  elseif type(lines) == "table" then
    for _, l in ipairs(lines) do meth(tt, "AddLine", tostring(l), 0.85, 0.85, 0.85, true) end
  end
  if hint then meth(tt, "AddLine", hint, 0.4, 0.8, 1, true) end
  meth(tt, "Show")
end
local function hideTip() local tt = G.GameTooltip if tt then meth(tt, "Hide") end end
W.HideTooltip = hideTip
local function entryTip(owner, e)
  if not e then return end
  local hint = clickHint(e)
  if e.onRightClick then hint = (hint and (hint .. "\n") or "") .. "Clic droit : plus de choix"
  elseif isSuggestion(e) then hint = (hint and (hint .. "\n") or "") .. "Clic droit : masquer" end
  W.Tooltip(owner, plain(e.text), e.tooltip, hint)
end

-- ===================================================================== réserve de widgets (recyclés à chaque rendu)
local function newPool(make) return { make = make, list = {}, used = 0 } end
local function acquire(pool, parent)
  pool.used = pool.used + 1
  local f = pool.list[pool.used]
  if not f then f = pool.make(parent); pool.list[pool.used] = f end
  if f then meth(f, "ClearAllPoints"); meth(f, "Show") end
  return f
end
local function releaseAll(pools)
  for _, p in pairs(pools) do
    for i = 1, #p.list do meth(p.list[i], "Hide") end
    p.used = 0
  end
end
W._newPool, W._acquire, W._releaseAll = newPool, acquire, releaseAll

local function newButton(parent)
  local b = G.CreateFrame("Button", nil, parent)
  meth(b, "RegisterForClicks", "LeftButtonUp", "RightButtonUp")
  b.bg = meth(b, "CreateTexture", nil, "BACKGROUND")
  meth(b.bg, "SetAllPoints", b)
  b.hl = meth(b, "CreateTexture", nil, "HIGHLIGHT")
  meth(b.hl, "SetAllPoints", b)
  color(b.hl, tok("hover", 1, 1, 1, 0.08))
  -- survol : liseré 1 px (couche HIGHLIGHT : affiché par le jeu sous la souris, aucun script)
  local r, g, bl = tok("borderSoft", 0.78, 0.62, 0.28)
  local pts = { { "TOPLEFT", "TOPRIGHT", true }, { "BOTTOMLEFT", "BOTTOMRIGHT", true }, { "TOPLEFT", "BOTTOMLEFT" },
    { "TOPRIGHT", "BOTTOMRIGHT" } }
  for _, p in ipairs(pts) do
    local e = meth(b, "CreateTexture", nil, "HIGHLIGHT")
    if e then
      meth(e, "SetPoint", p[1], b, p[1]); meth(e, "SetPoint", p[2], b, p[2])
      if p[3] then meth(e, "SetHeight", 1) else meth(e, "SetWidth", 1) end
      color(e, r, g, bl, 0.9)
    end
  end
  return b
end
W._newButton = newButton
local function border(f, r, g, b, a)
  f.edges = f.edges or {}
  local pts = { { "TOPLEFT", "TOPRIGHT", "h" }, { "BOTTOMLEFT", "BOTTOMRIGHT", "h" }, { "TOPLEFT", "BOTTOMLEFT", "v" },
    { "TOPRIGHT", "BOTTOMRIGHT", "v" } }
  for i, p in ipairs(pts) do
    local e = f.edges[i]
    if not e then
      e = meth(f, "CreateTexture", nil, "BORDER")
      meth(e, "SetPoint", p[1], f, p[1]); meth(e, "SetPoint", p[2], f, p[2])
      if p[3] == "h" then meth(e, "SetHeight", 1) else meth(e, "SetWidth", 1) end
      f.edges[i] = e
    end
    color(e, r, g, b, a)
    if a and a > 0 then meth(e, "Show") else meth(e, "Hide") end
  end
end
W._border, W._color, W._text, W._tok = border, color, text, tok

-- pastille ronde numérotée (+ petite icône ! / ? en bas à droite)
local function newBadge(parent, size)
  local f = G.CreateFrame("Frame", nil, parent)
  meth(f, "SetSize", size, size)
  f.ring = meth(f, "CreateTexture", nil, "ARTWORK", nil, 1)
  meth(f.ring, "SetAllPoints", f)
  meth(f.ring, "SetTexture", ROUND)
  f.disc = meth(f, "CreateTexture", nil, "ARTWORK", nil, 2)
  meth(f.disc, "SetPoint", "TOPLEFT", f, "TOPLEFT", 2, -2)
  meth(f.disc, "SetPoint", "BOTTOMRIGHT", f, "BOTTOMRIGHT", -2, 2)
  meth(f.disc, "SetTexture", ROUND)
  f.num = text(f, "M", "gold")
  meth(f.num, "SetPoint", "CENTER", f, "CENTER", 0, 0)
  meth(f.num, "SetJustifyH", "CENTER")
  f.mark = meth(f, "CreateTexture", nil, "OVERLAY")
  meth(f.mark, "SetSize", floor(size * 0.55), floor(size * 0.55))
  meth(f.mark, "SetPoint", "BOTTOMRIGHT", f, "BOTTOMRIGHT", 4, -3)
  function f.Set(self2, n, mark, hot)
    local r, g, b = tok("gold", 1, 0.82, 0.2)
    meth(self2.ring, "SetVertexColor", r, g, b, hot and 1 or 0.7)
    meth(self2.disc, "SetVertexColor", 0.08, 0.07, 0.05, 1)
    meth(self2.num, "SetText", n and tostring(n) or "")
    if mark then meth(self2.mark, "SetTexture", mark); meth(self2.mark, "Show") else meth(self2.mark, "Hide") end
  end
  return f
end
W._newBadge = newBadge

-- ===================================================================== données : le guide (carte + page Quêtes)
local MARK = { pick = "Interface\\GossipFrame\\AvailableQuestIcon", turn = "Interface\\GossipFrame\\ActiveQuestIcon" }
W.MARK = MARK
local KIND_ICON = {
  pick = MARK.pick, turn = MARK.turn, obj = ICON .. "INV_Misc_Note_02", fly = "Interface\\Minimap\\Tracking\\FlightMaster",
  boat = ICON .. "INV_Misc_Spyglass_03", zeppelin = ICON .. "INV_Misc_Spyglass_03", zep = ICON .. "INV_Misc_Spyglass_03",
  hs = ICON .. "INV_Misc_Rune_01", train = ICON .. "INV_Misc_Book_09", vendor = ICON .. "INV_Misc_Bag_09",
}
W.KIND_ICON = KIND_ICON
local function minutesTxt(sec)
  sec = tonumber(sec) or 0
  if sec <= 0 then return nil end
  local Pl = T.Plain
  if Pl and Pl.Minutes then return Pl.Minutes(sec) end
  return fmt("%d min", max(1, floor(sec / 60 + 0.5)))
end
W.MinutesText = minutesTxt
local function zoneName()
  local z = try(G.GetRealZoneText) or try(G.GetZoneText)
  if type(z) ~= "string" or (G.issecretvalue and try(G.issecretvalue, z)) then return nil end
  return z ~= "" and z or nil
end
W.ZoneName = zoneName
local function questTitle(q)
  local P = T.Planner
  local lq = P and P.log and P.log[q]
  if lq and lq.t then return lq.t end
  local Q = T.modules and T.modules.Quests
  for _, x in ipairs(Q and Q.lastList or {}) do if x.id == q then return x.title end end
  return nil
end
local function questLevel(q)
  local P = T.Planner
  local lq = P and P.log and P.log[q]
  if lq and tonumber(lq.lv) then return tonumber(lq.lv) end
  local Q = T.modules and T.modules.Quests
  for _, x in ipairs(Q and Q.lastList or {}) do if x.id == q then return tonumber(x.level) end end
  local X = T.Export
  local d = X and X.Data and try(X.Data, X)
  for _, r in ipairs(d and d.quests or {}) do if r.id == q then return tonumber(r.lv), r.t end end
  return nil
end
W.QuestLevel = questLevel
local DIRS = { "au nord", "au nord-est", "à l'est", "au sud-est", "au sud", "au sud-ouest", "à l'ouest", "au nord-ouest" }
local function whereTxt(here, place)
  if not (here and place and place.c == here.c and place.n and here.n) then return nil end
  local dn, dw = place.n - here.n, place.w - here.w
  local d = math.sqrt(dn * dn + dw * dw)
  local ang = math.atan2 and math.atan2(-dw, dn) or 0
  local i = floor(((ang * 180 / math.pi) + 360 + 22.5) % 360 / 45) + 1
  local m = floor(d * 0.9144 + 0.5)
  if m < 40 then return "ici", d end
  local NT = T.NavTime
  local sec = (NT and NT.Model and try(NT.Model, d, place.map)) or d / 7
  local dist = m >= 1000 and (fmt("%.1f km", m / 1000):gsub("%.", ",")) or (m .. " m")
  return fmt("%s %s · %s", dist, DIRS[i] or "", minutesTxt(sec) or "1 min"), d
end

-- arrêts du trajet : même liste que la vue « Trajet » (Route:Stops(64)), gardée 30 s tant que les étapes et le
-- curseur ne changent pas ; la position du joueur (distances affichées) est relue à chaque fois, sans recalcul
local stopsKeep = {}
function W.StopsCached(P, R)
  local t = try(G.GetTime) or 0
  local k = stopsKeep
  local n = P.steps and #P.steps or 0
  local st
  if k.steps == P.steps and k.cur == P.cursor and k.n == n and k.st and t - k.at < 30 then st = k.st
  else
    st = R:Stops(64) or {}
    stopsKeep = { steps = P.steps, cur = P.cursor, n = n, st = st, at = t }
  end
  local Gp = T.Geo and T.Geo.Player
  if Gp and R.World then
    local m, x, y = Gp()
    if m and x then st.here = R.World({ map = m, x = x, y = y }) or st.here end
  end
  return st
end
function W.ForgetStops() stopsKeep = {} end
--- Le guide en cours : { zone, inLog, near, total (s), paused, steps = { {n, title, sub, mark, icon, kind, q,
--- onClick, onRightClick} }, count, upcoming = { {q, level, title} } }. `n` = nombre d'étapes voulues.
function W.Guide(n, full)
  n = n or 3
  local g = { zone = zoneName() or "?", inLog = 0, near = 0, total = 0, steps = {}, upcoming = {}, count = 0 }
  local Q = T.modules and T.modules.Quests
  local a = Q and Q.last
  if type(Q and Q.lastList) == "table" then g.inLog = #Q.lastList end
  if a then g.here = #(a.here or {}) end
  local P, R = T.Planner, T.Route
  local okR = R and R.IsEnabled and R:IsEnabled() and P and P.steps and P.cursor
  if okR and R.char and R.char.paused then g.paused = true end
  if okR and not g.paused then
    local st = W.StopsCached(P, R)
    g.total = tonumber(st.total) or 0
    local seenQ, k = {}, 0
    for _, e in ipairs(st) do
      local s = e.step
      if k < n then
        k = k + 1
        local step = { n = k, kind = s and s.k or e.kind, q = s and s.q }
        if s then
          step.title = strip(P:StepLabel(s))
          local lq = P.log and s.q and P.log[s.q]
          local o = lq and lq.obj and lq.obj[tonumber(s.o) or 0]
          local prog = (o and (o.n or 0) > 0) and fmt("%d/%d", o.have or 0, o.n) or nil
          local where = whereTxt(st.here, e.place)
          local parts = {}
          if s.k == "pick" then parts[#parts + 1] = "nouvelle quête"
          elseif s.k == "turn" then parts[#parts + 1] = "quête finie"
          elseif s.k == "obj" then
            local qt = s.q and questTitle(s.q)
            parts[#parts + 1] = prog and ("objectif " .. prog) or "objectif"
            if qt then parts[#parts + 1] = "« " .. strip(qt) .. " »" end
          end
          if where then parts[#parts + 1] = where end
          if s.out then parts[#parts + 1] = "hors zone" end
          step.sub = tconcat(parts, " · ")
          step.mark = MARK[s.k]
          step.icon = KIND_ICON[s.k] or KIND_ICON.obj
          step.onClick = function() P:Navigate(s) end
          step.actionText = "guider la boussole vers cette étape"
          local Zp = T.ZonePlan
          if Zp and Zp.StopMenu and Zp.IsEnabled and Zp:IsEnabled() then step.onRightClick = function() Zp:StopMenu(s) end end
        else
          step.title = strip(e.text or "?")
          step.sub = "trajet · " .. (minutesTxt(e.sec) or "≈ 1 min")
          step.icon = KIND_ICON[e.kind] or KIND_ICON.fly
          local p = e.place
          if p and p.map then
            step.onClick = function() P:Navigate({ k = "fly", map = p.map, x = p.x, y = p.y, label = e.text }) end
            step.actionText = "guider vers ce départ"
          end
        end
        g.steps[#g.steps + 1] = step
      elseif full and s and s.k == "pick" and s.q and not seenQ[s.q] and #g.upcoming < 8 then
        seenQ[s.q] = true
        local lv, t2 = questLevel(s.q)
        g.upcoming[#g.upcoming + 1] = { q = s.q, level = lv, title = questTitle(s.q) or t2 or ("Quête " .. s.q),
          onClick = function() P:Navigate(s) end }
      end
      local pl, h = e.place, st.here
      if s and s.q and pl and h and pl.c == h.c and pl.n and h.n then
        local dn, dw = pl.n - h.n, pl.w - h.w
        if dn * dn + dw * dw < 490000 then g.near = g.near + 1 end     -- < 700 yd (≈ 640 m, 3 min à pied)
      end
    end
    g.count = #st
  end
  if #g.steps == 0 and not g.paused then
    -- pas de trajet : les lignes de « Maintenant » (l'étape épinglée de Quests / Planner d'abord)
    for _, e in ipairs(T.GetTabLines and T:GetTabLines("now") or {}) do
      if #g.steps >= n then break end
      if not e.header and not e.control then
        g.steps[#g.steps + 1] = { n = #g.steps + 1, title = strip(e.text), sub = e.right and strip(e.right) or nil,
          icon = e.icon, danger = (e.prio or 1) >= 3, entry = e, onClick = e.onClick, onRightClick = e.onRightClick }
      end
    end
  end
  return g
end

--- « 1 en cours · 3 proches · 3 min » (rien d'inventé : chaque morceau absent est omis)
function W.GuideSummary(g)
  local p = {}
  if g.inLog > 0 then p[#p + 1] = fmt("%d en cours", g.inLog) end
  if g.near > 0 then p[#p + 1] = fmt("%d proche%s", g.near, g.near > 1 and "s" or "") end
  local m = minutesTxt(g.total)
  if m then p[#p + 1] = m end
  return tconcat(p, " · ")
end

-- ===================================================================== carte « Prochaine étape »
local card
local CARD_W, HERO_H, STEP_H = 280, 48, 36          -- grille de 4 px
local function cardOn() return W:IsEnabled() and W.db and W.db.card ~= false end
local function buildCard()
  if card or not G.CreateFrame then return card end
  card = G.CreateFrame("Frame", "TreeAICard", G.UIParent, G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  meth(card, "SetSize", CARD_W, HERO_H + 10)
  meth(card, "SetFrameStrata", "MEDIUM")
  meth(card, "SetClampedToScreen", true)
  meth(card, "SetMovable", true)
  meth(card, "EnableMouse", true)
  local th = Th()
  if th then th:Skin(card, "card"); if th.Shadow then th:Shadow(card, 8) end else
    card.bgt = meth(card, "CreateTexture", nil, "BACKGROUND"); meth(card.bgt, "SetAllPoints", card); color(card.bgt, 0.05, 0.06, 0.08, 0.9)
  end
  -- en-tête : icône + titre + résumé ; clic = fenêtre TreeAI (page Quêtes), glisser = déplacer
  local hero = newButton(card)
  meth(hero, "SetPoint", "TOPLEFT", card, "TOPLEFT", 4, -4)
  meth(hero, "SetPoint", "TOPRIGHT", card, "TOPRIGHT", -4, -4)
  meth(hero, "SetHeight", HERO_H)
  color(hero.bg, 0.30, 0.20, 0.06, 0.55)
  border(hero, tok("border", 0.78, 0.62, 0.28, 0.9))
  hero.icon = meth(hero, "CreateTexture", nil, "ARTWORK")
  meth(hero.icon, "SetSize", 32, 32)
  meth(hero.icon, "SetPoint", "LEFT", hero, "LEFT", 8, 0)
  meth(hero.icon, "SetTexture", ICON .. "INV_Misc_Book_09")
  meth(hero.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
  hero.title = text(hero, "M", "gold")
  meth(hero.title, "SetPoint", "TOPLEFT", hero, "TOPLEFT", 48, -8)
  meth(hero.title, "SetPoint", "RIGHT", hero, "RIGHT", -62, 0)
  meth(hero.title, "SetWordWrap", false)
  hero.sub = text(hero, "S", "muted")
  meth(hero.sub, "SetPoint", "TOPLEFT", hero.title, "BOTTOMLEFT", 0, -3)
  meth(hero.sub, "SetPoint", "RIGHT", hero, "RIGHT", -8, 0)
  meth(hero.sub, "SetWordWrap", false)
  meth(hero, "RegisterForDrag", "LeftButton")
  meth(hero, "SetScript", "OnDragStart", function() if not inCombat() then meth(card, "StartMoving") end end)
  meth(hero, "SetScript", "OnDragStop", function()
    meth(card, "StopMovingOrSizing")
    W.SaveCardPosition()
  end)
  meth(hero, "SetScript", "OnClick", function(_, btn)
    if btn == "RightButton" then
      if W.char then W.char.optPage = "Window" end
      W.Open("options")
    else W.Open("quests") end
  end)
  meth(hero, "SetScript", "OnEnter", function(self)
    W.Tooltip(self, "Prochaine étape", { "Ton trajet en cours : les prochaines étapes, dans l'ordre.",
      "Glisser : déplacer la carte" }, "Clic : ouvrir la fenêtre TreeAI (Quêtes)\nClic droit : réglages de la carte")
  end)
  meth(hero, "SetScript", "OnLeave", hideTip)
  card.hero = hero
  -- bouton écrit (pas d'icône muette) : la fenêtre TreeAI
  local mb = newButton(hero)
  meth(mb, "SetSize", 54, 16)
  meth(mb, "SetPoint", "TOPRIGHT", hero, "TOPRIGHT", -4, -4)
  color(mb.bg, tok("button", 0.14, 0.15, 0.19, 0.9))
  border(mb, tok("borderSoft", 0.78, 0.62, 0.28, 0.5))
  mb.t = text(mb, "S", "gold")
  meth(mb.t, "SetPoint", "CENTER", mb, "CENTER", 0, 0)
  meth(mb.t, "SetText", "Ouvrir")
  meth(mb, "SetScript", "OnClick", function() W.Toggle() end)
  meth(mb, "SetScript", "OnEnter", function(self) W.Tooltip(self, "Fenêtre TreeAI", nil, "Clic : ouvrir / fermer la fenêtre") end)
  meth(mb, "SetScript", "OnLeave", hideTip)
  card.menuBtn = mb
  card.steps = {}
  card.empty = text(card, "S", "muted")
  meth(card.empty, "SetPoint", "TOPLEFT", hero, "BOTTOMLEFT", 6, -6)
  meth(card.empty, "SetPoint", "RIGHT", card, "RIGHT", -10, 0)
  meth(card.empty, "SetWordWrap", true)
  meth(card, "Hide")
  if T.RegisterMovable then
    T:RegisterMovable(card, "card", "Carte « Prochaine étape »", {
      onMoved = function(point, x, y, sc)
        if W.char then W.char.cardPos = { point = point, x = x, y = y } end
        if sc and W.db then W.db.cardScale = floor(sc * 100 + 0.5) end
      end,
      onReset = function() if W.char then W.char.cardPos = nil end W.PlaceCard() end,
    })
  end
  return card
end
local function stepRow(i)
  local r = card.steps[i]
  if r then return r end
  r = newButton(card)
  meth(r, "SetHeight", STEP_H)
  color(r.bg, tok("button", 0.14, 0.15, 0.19, 0.6))
  r.badge = newBadge(r, 24)
  meth(r.badge, "SetPoint", "LEFT", r, "LEFT", 6, 0)
  r.title = text(r, "S", "text")
  meth(r.title, "SetPoint", "TOPLEFT", r, "TOPLEFT", 40, -5)
  meth(r.title, "SetPoint", "RIGHT", r, "RIGHT", -8, 0)
  meth(r.title, "SetWordWrap", false)
  r.sub = text(r, "S", "muted")
  meth(r.sub, "SetPoint", "TOPLEFT", r.title, "BOTTOMLEFT", 0, -3)
  meth(r.sub, "SetPoint", "RIGHT", r, "RIGHT", -8, 0)
  meth(r.sub, "SetWordWrap", false)
  meth(r, "SetScript", "OnClick", function(self, btn) if self.step then W.RunStep(self.step, btn) end end)
  meth(r, "SetScript", "OnEnter", function(self)
    local s = self.step
    if not s then return end
    local hint = s.onClick and ("Clic : " .. (s.actionText or "faire cette étape")) or nil
    if s.onRightClick then hint = (hint and hint .. "\n" or "") .. "Clic droit : ignorer cette quête…" end
    W.Tooltip(self, s.title, s.sub and { s.sub } or (s.entry and s.entry.tooltip), hint)
  end)
  meth(r, "SetScript", "OnLeave", hideTip)
  card.steps[i] = r
  return r
end
function W.RunStep(s, btn)
  if s.entry then return W.Run(s.entry, btn) end
  if btn == "RightButton" then if s.onRightClick then T:UserAction(s.onRightClick, s) end
  elseif s.onClick then T:UserAction(s.onClick, s) end
  W.Dirty()
end

local cardSig, cardAt, cardKey = nil, -10, nil
local function playerBucket()
  local Gp = T.Geo and T.Geo.Player
  if not Gp then return "" end
  local m, x, y = Gp()
  if not (m and x) then return "" end
  return m .. ":" .. floor(x / 1.5) .. ":" .. floor(y / 1.5)     -- ≈ 1,5 % de carte : la distance affichée change
end
function W.RenderCard(force)
  if not cardOn() then if card then meth(card, "Hide") end return end
  if not card then buildCard() end
  if not card then return end
  local t = try(G.GetTime) or 0
  if not force and t - cardAt < 2 then return end
  -- clé bon marché : rien n'a bougé (étapes, curseur, arrêts, lignes de « Maintenant », position) → rien à refaire
  local P, R = T.Planner, T.Route
  local Q = T.modules and T.modules.Quests
  local key = tostring(T.DataGen and T:DataGen()) .. tostring(P and P.steps) .. tostring(P and P.cursor) .. tostring(P and P.steps and #P.steps)
    .. tostring(Q and Q.lastList) .. playerBucket() .. tostring(R and R.char and R.char.paused)
  if not force and key == cardKey then return end
  cardKey, cardAt = key, t
  local now = T.GetTabLines and T:GetTabLines("now") or {}
  local nSteps = min(5, max(1, tonumber(W.db.cardSteps) or 3))
  local g = W.Guide(nSteps)
  -- danger d'abord (mort, familier mort, sacs pleins…) : une ligne rouge au-dessus des étapes
  local danger
  for _, e in ipairs(now) do
    if (e.prio or 1) >= 3 and not e.header and not e.control then danger = e break end
  end
  local sigT = { g.zone, g.inLog, g.near, floor((g.total or 0) / 60), g.paused and 1 or 0, danger and danger.text or "" }
  for _, s in ipairs(g.steps) do sigT[#sigT + 1] = (s.title or "") .. "|" .. (s.sub or "") end
  local sig = tconcat(sigT, "\n")
  if sig == cardSig and not force then return end
  cardSig = sig
  local hero = card.hero
  meth(hero.title, "SetText", (g.paused and "Trajet en pause" or ("Histoire : " .. g.zone)))
  local sum = W.GuideSummary(g)
  meth(hero.sub, "SetText", sum ~= "" and sum or "Aucune quête en cours ici")
  local y = 4 + HERO_H + 4
  local rows = {}
  if danger then rows[#rows + 1] = { n = "!", title = strip(danger.text), sub = danger.right and strip(danger.right),
    entry = danger, danger = true } end
  for _, s in ipairs(g.steps) do rows[#rows + 1] = s end
  for i, s in ipairs(rows) do
    local r = stepRow(i)
    r.step = s
    meth(r, "ClearAllPoints")
    meth(r, "SetPoint", "TOPLEFT", card, "TOPLEFT", 4, -y)
    meth(r, "SetPoint", "TOPRIGHT", card, "TOPRIGHT", -4, -y)
    r.badge:Set(s.n, s.mark, i == 1)
    meth(r.title, "SetText", s.title or "")
    if s.danger then meth(r.title, "SetTextColor", tok("prio3", 1, 0.3, 0.25))
    elseif i == 1 or (danger and i == 2) then meth(r.title, "SetTextColor", tok("gold", 1, 0.82, 0.2))
    else meth(r.title, "SetTextColor", tok("text", 0.92, 0.92, 0.92)) end
    meth(r.sub, "SetText", s.sub or "")
    local h = (s.sub and s.sub ~= "") and STEP_H or 24
    meth(r, "SetHeight", h)
    meth(r, "Show")
    y = y + h + 4
  end
  for i = #rows + 1, #card.steps do meth(card.steps[i], "Hide") end
  if #rows == 0 then
    meth(card.empty, "SetText", g.paused and "Clique sur l'en-tête pour reprendre le trajet." or
      "Rien à suivre ici. Clique « Ouvrir » pour choisir une zone ou un donjon.")
    meth(card.empty, "Show")
    y = y + 32
  else meth(card.empty, "Hide") end
  meth(card, "SetHeight", y + 2)
end

-- position : celle que le placement automatique a choisie pour le panneau (zone libre d'un bord, loin des
-- suivis de quêtes et des cadres de groupe), sauf si le joueur a glissé la carte ; vérifiée contre les suivis
function W.SaveCardPosition()
  if not card then return end
  local pt, _, _, x, y = meth(card, "GetPoint")
  if W.char and pt then W.char.cardPos = { point = pt, x = floor((x or 0) + 0.5), y = floor((y or 0) + 0.5) } end
end
local function overlapsGuide()
  if not (card and T.ScanScreen) then return false end
  local l, b, w, h = meth(card, "GetRect")
  if not (l and w) then return false end
  local scan = try(T.ScanScreen, T)
  for _, r in ipairs(scan and scan.rects or {}) do
    if r.grown or (r.name and tostring(r.name):find("Track")) then
      if l < r.l + r.w and r.l < l + w and b < r.b + r.h and r.b < b + h then return true, r end
    end
  end
  return false
end
W.CardOverlaps = overlapsGuide
function W.PlaceCard()
  if not card or inCombat() then return end
  local k = (tonumber(W.db and W.db.cardScale) or 100) / 100
  meth(card, "SetScale", k)
  local pos = W.char and W.char.cardPos
  meth(card, "ClearAllPoints")
  if type(pos) == "table" and pos.point then
    meth(card, "SetPoint", pos.point, G.UIParent, pos.point, pos.x or 0, pos.y or 0)
    return
  end
  local c = T.PanelChar and T:PanelChar()
  if c and c.point then
    meth(card, "SetPoint", c.point, G.UIParent, c.point, (c.x or 0), (c.y or 0))
  else
    meth(card, "SetPoint", "RIGHT", G.UIParent, "RIGHT", -40, 60)
  end
  -- garde-fou : posée sur un suivi de quêtes (Questie, Suivi Forever…) → descend sous lui, sinon côté opposé
  local hit, r = overlapsGuide()
  if hit and r then
    local P = G.UIParent
    local Wd = tonumber(meth(P, "GetWidth")) or 1920
    local cw, ch = (tonumber(meth(card, "GetWidth")) or CARD_W) * k, (tonumber(meth(card, "GetHeight")) or 120) * k
    meth(card, "ClearAllPoints")
    if r.b - ch - 10 > 120 then
      meth(card, "SetPoint", "TOPLEFT", P, "BOTTOMLEFT", r.l / k, (r.b - 10) / k)
    else
      local x = (r.l + r.w / 2 > Wd / 2) and 40 or (Wd - cw - 40)
      meth(card, "SetPoint", "TOPLEFT", P, "BOTTOMLEFT", x / k, ((r.b + r.h) / k))
    end
  end
end

-- le panneau texte : caché quand la carte le remplace (sauf si le joueur l'ouvre lui-même)
local userPanel = false
local function hidePanelForCard()
  if not (cardOn() and W.db.replacePanel ~= false) or userPanel then return end
  local p = T.GetPanel and T:GetPanel()
  if p and meth(p, "IsShown") then meth(p, "Hide") end
end
W.HidePanelForCard = hidePanelForCard
function W.ShowCard()
  if not cardOn() then return end
  local p = T.GetPanel and T:GetPanel()
  if userPanel and p and meth(p, "IsShown") then return end   -- le joueur lit le panneau : pas les deux
  buildCard()
  if not card then return end
  W.PlaceCard()
  W.RenderCard(true)
  meth(card, "Show")
  hidePanelForCard()
end
function W.HideCard() if card then meth(card, "Hide") end end
function W.GetCard() return card end

-- ===================================================================== fenêtre principale
local win
W.PAGES = {}       -- { id, label, icon, render(ctx), action(ctx) -> label, fn, tip }
W.pageIndex = {}
--- Ajoute une page à la navigation (ordre d'appel) ; render(L) dessine avec la mise en page `L`.
function W.AddPage(p)
  if type(p) ~= "table" or not p.id then return end
  if W.pageIndex[p.id] then
    for k, v in pairs(p) do W.pageIndex[p.id][k] = v end
    return
  end
  W.PAGES[#W.PAGES + 1] = p
  W.pageIndex[p.id] = p
end
local NAV_W, TOP_H, FOOT_H, PAD = 176, 56, 40, 16      -- grille de 4 px (Theme.GRID)
W.NAV_W = NAV_W

local function version()
  local v = T.API and T.API.AddOnMeta and T.API.AddOnMeta("TreeAI", "Version")
  return (type(v) == "string" and v ~= "") and v or "?"
end

local pools = {}
local content, scroll
local function pool(kind, make) local p = pools[kind] if not p then p = newPool(make); pools[kind] = p end return p end

-- -------------------------------------------------- moteur de mise en page (vertical, une colonne, sans chevauchement)
-- Lay.y avance à chaque widget ; Lay.rects garde chaque rectangle (le harnais vérifie : rien ne se chevauche).
local function newLay(width)
  local Ly = { y = 8, W = width, col = width - 2 * PAD, rects = {} }
  local function rec(kind, x, y, w, h, label) Ly.rects[#Ly.rects + 1] = { kind = kind, x = x, y = y, w = w, h = h, label = strip(label) } end
  local function at(f, x, y) meth(f, "ClearAllPoints"); meth(f, "SetPoint", "TOPLEFT", content, "TOPLEFT", x, -y) end
  Ly.at, Ly.rec = at, rec
  function Ly.gap(h) Ly.y = Ly.y + (h or sp().m) end
  -- paragraphe
  function Ly.text(str, size, tokName, indent)
    local fs = acquire(pool("text" .. (size or "S"), function() return text(content, size or "S", "text") end))
    local x = PAD + (indent or 0)
    local w = Ly.col - (indent or 0)
    meth(fs, "SetWidth", w)
    meth(fs, "SetWordWrap", true)
    meth(fs, "SetText", str)
    meth(fs, "SetTextColor", tok(tokName or "text", 0.9, 0.9, 0.9))
    at(fs, x, Ly.y)
    local h = measure(fs, str, w, size or "S")
    rec("text", x, Ly.y, w, h, str)
    Ly.y = Ly.y + h + sp().s
    return fs
  end
  -- en-tête de section (repliable si key) : titre or + filet + « + / – » à droite + compteur
  function Ly.header(str, key, count, icon, closedByDefault)
    if #Ly.rects > 0 then Ly.y = Ly.y + sp().m end
    local b = acquire(pool("header", function()
      local f = newButton(content)
      meth(f, "SetHeight", 24)
      color(f.bg, tok("button", 0.14, 0.15, 0.19, 0.95))
      f.icon = meth(f, "CreateTexture", nil, "ARTWORK")
      meth(f.icon, "SetSize", 16, 16)
      meth(f.icon, "SetPoint", "LEFT", f, "LEFT", 6, 0)
      f.t = text(f, "M", "gold")
      meth(f.t, "SetWordWrap", false)
      f.n = text(f, "S", "muted")
      meth(f.n, "SetPoint", "RIGHT", f, "RIGHT", -26, 0)
      meth(f.n, "SetJustifyH", "RIGHT")
      f.pm = text(f, "M", "gold")
      meth(f.pm, "SetPoint", "RIGHT", f, "RIGHT", -8, 0)
      meth(f, "SetScript", "OnClick", function(self)
        if self.key and W.char then
          W.char.collapsed[self.key] = not self.closed   -- true = replié, false = déplié (choix du joueur gardé)
          W.Render()
        end
      end)
      meth(f, "SetScript", "OnEnter", function(self)
        if self.key then W.Tooltip(self, self.label, nil, self.closed and "Clic : déplier" or "Clic : replier") end
      end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end))
    meth(b, "SetWidth", Ly.col)
    b.key, b.label = key, str
    meth(b.icon, "SetTexture", icon)
    if icon then meth(b.icon, "Show") else meth(b.icon, "Hide") end
    meth(b.t, "ClearAllPoints")
    meth(b.t, "SetPoint", "LEFT", b, "LEFT", icon and 28 or 8, 0)
    meth(b.t, "SetPoint", "RIGHT", b.n, "LEFT", -6, 0)
    meth(b.t, "SetText", str)
    meth(b.n, "SetText", count and tostring(count) or "")
    local closed = false
    if key then
      local v = W.char and W.char.collapsed[key]
      if v == nil then closed = closedByDefault and W.Query() == "" or false else closed = v and W.Query() == "" end
    end
    b.closed = closed
    meth(b.pm, "SetText", key and (closed and "+" or "–") or "")
    meth(b, "EnableMouse", key and true or false)
    at(b, PAD, Ly.y)
    rec("header", PAD, Ly.y, Ly.col, 24, str)
    Ly.y = Ly.y + 24 + sp().s
    return not closed
  end
  -- ligne d'un onglet (contrat SetTabLines) : icône + texte coupé sur plusieurs lignes + colonne de droite
  function Ly.row(e, opts)
    opts = opts or {}
    local b = acquire(pool("row", function()
      local f = newButton(content)
      f.icon = meth(f, "CreateTexture", nil, "ARTWORK")
      meth(f.icon, "SetSize", 16, 16)
      meth(f.icon, "SetPoint", "TOPLEFT", f, "TOPLEFT", 6, -4)
      meth(f.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
      f.t = text(f, "S", "text")
      meth(f.t, "SetWordWrap", true)
      f.r = text(f, "S", "muted")
      meth(f.r, "SetJustifyH", "RIGHT")
      meth(f.r, "SetWordWrap", false)
      meth(f.r, "SetPoint", "TOPRIGHT", f, "TOPRIGHT", -8, -6)
      meth(f, "SetScript", "OnClick", function(self, btn) W.Run(self.entry, btn) end)
      meth(f, "SetScript", "OnEnter", function(self) entryTip(self, self.entry) end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end))
    b.entry = e
    local th = Th()
    local icon, r, g, bl = e.icon, 0.85, 0.85, 0.85
    if th then
      local i2
      i2, r, g, bl = th:LineStyle(e.kind or ((e.prio or 1) >= 3 and "danger") or "info", e.prio)
      icon = icon or i2
    end
    meth(b.icon, "SetTexture", icon)
    local rw = e.right and min(120, ulen(e.right) * 6 + 10) or 0
    meth(b.r, "SetWidth", max(1, rw - 4))
    local tw = Ly.col - 36 - rw
    meth(b.t, "ClearAllPoints")
    meth(b.t, "SetPoint", "TOPLEFT", b, "TOPLEFT", 28, -6)
    meth(b.t, "SetWidth", tw)
    meth(b.t, "SetText", e.text or "")
    meth(b.t, "SetTextColor", r, g, bl)
    meth(b.r, "SetText", e.right or "")
    local h = max(24, measure(b.t, e.text, tw, "S") + 12)
    meth(b, "SetSize", Ly.col, h)
    color(b.bg, tok("button", 0.14, 0.15, 0.19, (e.onClick and 0.55) or 0.25))
    meth(b, "EnableMouse", true)
    at(b, PAD, Ly.y)
    rec("row", PAD, Ly.y, Ly.col, h, e.text)
    Ly.y = Ly.y + h + 2
    return b
  end
  -- carte d'en-tête façon Adventure Guide (icône, titre, 2 sous-titres)
  function Ly.hero(icon, title, sub, sub2, onClick, tipLines, hint)
    local b = acquire(pool("hero", function()
      local f = newButton(content)
      f.icon = meth(f, "CreateTexture", nil, "ARTWORK")
      meth(f.icon, "SetSize", 40, 40)
      meth(f.icon, "SetPoint", "LEFT", f, "LEFT", 10, 0)
      meth(f.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
      f.t = text(f, "L", "gold")
      meth(f.t, "SetPoint", "TOPLEFT", f, "TOPLEFT", 60, -9)
      meth(f.t, "SetWordWrap", false)
      f.s = text(f, "S", "text")
      meth(f.s, "SetPoint", "TOPLEFT", f.t, "BOTTOMLEFT", 0, -4)
      meth(f.s, "SetWordWrap", false)
      f.s2 = text(f, "S", "muted")
      meth(f.s2, "SetPoint", "TOPLEFT", f.s, "BOTTOMLEFT", 0, -3)
      meth(f.s2, "SetWordWrap", false)
      meth(f, "SetScript", "OnClick", function(self) if self.fn then T:UserAction(self.fn) W.Dirty() end end)
      meth(f, "SetScript", "OnEnter", function(self) if self.tipL or self.hint then W.Tooltip(self, self.titleText, self.tipL, self.hint) end end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end))
    meth(b, "SetSize", Ly.col, BLOCK.hero)
    color(b.bg, 0.32, 0.21, 0.06, 0.65)
    border(b, tok("border", 0.78, 0.62, 0.28, 0.9))
    meth(b.icon, "SetTexture", icon or (ICON .. "INV_Misc_Book_09"))
    for _, fs in ipairs({ b.t, b.s, b.s2 }) do meth(fs, "SetWidth", Ly.col - 72) end
    meth(b.t, "SetText", title or "")
    meth(b.s, "SetText", sub or "")
    meth(b.s2, "SetText", sub2 or "")
    b.fn, b.titleText, b.tipL, b.hint = onClick, title, tipLines, hint
    at(b, PAD, Ly.y)
    rec("hero", PAD, Ly.y, Ly.col, BLOCK.hero, title)
    Ly.y = Ly.y + BLOCK.hero + sp().m
    return b
  end
  -- étape numérotée : pastille ronde (n° + ! / ?), titre, sous-titre gris ; hot = étape en cours (cadre or)
  function Ly.step(s, hot)
    local b = acquire(pool("step", function()
      local f = newButton(content)
      f.badge = newBadge(f, 26)
      meth(f.badge, "SetPoint", "LEFT", f, "LEFT", 6, 0)
      f.t = text(f, "M", "gold")
      meth(f.t, "SetPoint", "TOPLEFT", f, "TOPLEFT", 42, -5)
      meth(f.t, "SetWordWrap", false)
      f.s = text(f, "S", "muted")
      meth(f.s, "SetPoint", "TOPLEFT", f.t, "BOTTOMLEFT", 0, -3)
      meth(f.s, "SetWordWrap", false)
      meth(f, "SetScript", "OnClick", function(self, btn) if self.step then W.RunStep(self.step, btn) end end)
      meth(f, "SetScript", "OnEnter", function(self)
        local st = self.step
        if not st then return end
        local hint = st.onClick and ("Clic : " .. (st.actionText or "faire cette étape")) or nil
        if st.onRightClick then hint = (hint and hint .. "\n" or "") .. "Clic droit : ignorer cette quête…" end
        W.Tooltip(self, st.title, st.sub and { st.sub } or nil, hint)
      end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end))
    b.step = s
    b.badge:Set(s.n, s.mark, hot)
    meth(b.t, "SetWidth", Ly.col - 52)
    meth(b.s, "SetWidth", Ly.col - 52)
    meth(b.t, "SetText", s.title or "")
    if s.danger then meth(b.t, "SetTextColor", tok("prio3", 1, 0.3, 0.25))
    elseif hot then meth(b.t, "SetTextColor", tok("gold", 1, 0.82, 0.2))
    else meth(b.t, "SetTextColor", tok("text", 0.92, 0.92, 0.92)) end
    meth(b.s, "SetText", s.sub or "")
    local h = BLOCK.step
    meth(b, "SetSize", Ly.col, h)
    if hot then color(b.bg, 0.40, 0.30, 0.05, 0.55); border(b, tok("gold", 1, 0.82, 0.2, 0.9))
    else color(b.bg, tok("button", 0.14, 0.15, 0.19, 0.75)); border(b, 0, 0, 0, 0) end
    at(b, PAD, Ly.y)
    rec("step", PAD, Ly.y, Ly.col, h, s.title)
    Ly.y = Ly.y + h + 3
    return b
  end
  -- quête « [24] Titre » aux couleurs de niveau + sous-titre gris
  function Ly.quest(level, title, sub, onClick, tip, hint)
    local b = acquire(pool("quest", function()
      local f = newButton(content)
      f.t = text(f, "M", "text")
      meth(f.t, "SetPoint", "TOPLEFT", f, "TOPLEFT", 42, -5)
      meth(f.t, "SetWordWrap", false)
      f.s = text(f, "S", "muted")
      meth(f.s, "SetPoint", "TOPLEFT", f.t, "BOTTOMLEFT", 0, -3)
      meth(f.s, "SetWordWrap", false)
      meth(f, "SetScript", "OnClick", function(self) if self.fn then T:UserAction(self.fn) W.Dirty() end end)
      meth(f, "SetScript", "OnEnter", function(self) W.Tooltip(self, self.titleText, self.tipL, self.hint) end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end))
    local pl = tonumber(try(G.UnitLevel, "player")) or 1
    local th = Th()
    local r, g, bl = 1, 0.82, 0
    if th and th.QuestDiffColor then r, g, bl = th:QuestDiffColor(level, pl) end
    local label = (level and ("[" .. level .. "] ") or "") .. strip(title)
    meth(b.t, "SetWidth", Ly.col - 52); meth(b.s, "SetWidth", Ly.col - 52)
    meth(b.t, "SetText", label)
    meth(b.t, "SetTextColor", r, g, bl)
    meth(b.s, "SetText", sub or "")
    meth(b, "SetSize", Ly.col, BLOCK.quest)
    color(b.bg, tok("button", 0.14, 0.15, 0.19, 0.55))
    b.fn, b.titleText = onClick, label
    b.tipL, b.hint = tip, hint or (onClick and "Clic : guider vers cette quête" or nil)
    at(b, PAD, Ly.y)
    rec("quest", PAD, Ly.y, Ly.col, BLOCK.quest, label)
    Ly.y = Ly.y + BLOCK.quest + 2
    return b
  end
  -- boutons de même largeur (grille de 2–3 colonnes) : list = { {label, fn, tip}, … }
  function Ly.buttons(list, cols)
    cols = cols or min(3, #list)
    if cols < 1 or #list == 0 then return end
    local bw = floor((Ly.col - (cols - 1) * 8) / cols)
    local p = pool("button", function()
      local th = Th()
      local f = th and th:Button(content, "", 120, 24) or newButton(content)
      if not f.label then f.label = text(f, "S", "text"); meth(f.label, "SetPoint", "CENTER", f, "CENTER", 0, 0) end
      meth(f, "SetScript", "OnClick", function(self) if self.fn then T:UserAction(self.fn) W.Dirty() end end)
      meth(f, "SetScript", "OnEnter", function(self) if self.tip then W.Tooltip(self, self.labelText, nil, self.tip) end end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end)
    for i, it in ipairs(list) do
      local c = (i - 1) % cols
      if c == 0 and i > 1 then Ly.y = Ly.y + 28 end
      local b = acquire(p)
      meth(b, "SetSize", bw, 24)
      meth(b.label, "SetText", it[1])
      b.fn, b.tip, b.labelText = it[2], it[3], it[1]
      local x = PAD + c * (bw + 8)
      at(b, x, Ly.y)
      rec("button", x, Ly.y, bw, 24, it[1])
    end
    if #list > 0 then Ly.y = Ly.y + 28 end
  end
  -- état vide : une phrase qui dit quoi faire
  -- état vide : une phrase qui dit quoi faire + (si possible) le bouton qui le fait ; recherche → « Effacer »
  function Ly.empty(str, action)
    Ly.gap(sp().l)
    local fs = Ly.text(str, "M", "muted")
    if W.Query() ~= "" then
      Ly.gap(sp().s)
      Ly.buttons({ { "Effacer la recherche", function() W.ClearSearch() end, "Clic : tout réafficher" } }, 2)
    elseif type(action) == "table" and action[1] and action[2] then
      Ly.gap(sp().s)
      Ly.buttons({ action }, 2)
    end
    return fs
  end
  function Ly.finish() meth(content, "SetHeight", Ly.y + 16); meth(content, "SetWidth", Ly.W) end
  return Ly
end
W._newLay = newLay

-- -------------------------------------------------- recherche
local query = ""
function W.Query() return query end
function W.ClearSearch()
  query = ""
  if win and win.search then meth(win.search, "SetText", ""); meth(win.search, "ClearFocus") end
  W.Render()
end
--- vrai si le texte correspond à la recherche (mots, accents et casse ignorés)
local ACC = { ["à"] = "a", ["â"] = "a", ["ä"] = "a", ["é"] = "e", ["è"] = "e", ["ê"] = "e", ["ë"] = "e", ["î"] = "i",
  ["ï"] = "i", ["ô"] = "o", ["ö"] = "o", ["ù"] = "u", ["û"] = "u", ["ü"] = "u", ["ç"] = "c", ["œ"] = "oe" }
local function fold(s)
  s = low(s)
  for k, v in pairs(ACC) do s = s:gsub(k, v) end
  return s
end
W.Fold = fold
function W.Match(...)
  if query == "" then return true end
  local hay = {}
  for i = 1, select("#", ...) do local v = select(i, ...) if v then hay[#hay + 1] = fold(v) end end
  hay = tconcat(hay, " ")
  for word in fold(query):gmatch("%S+") do
    if not hay:find(word, 1, true) then return false end
  end
  return true
end

-- -------------------------------------------------- pages simples : lignes d'un onglet du panneau
--- Dessine les lignes d'un onglet : boutons (control) en grille, en-têtes (header) = sections repliables.
function W.RenderTab(Ly, tabId, emptyText, skip, emptyAction)
  local lines = T.GetTabLines and T:GetTabLines(tabId) or {}
  local ctrls = {}
  for _, e in ipairs(lines) do
    if e.control and e.onClick then
      ctrls[#ctrls + 1] = { e.label or strip(e.text), function() W.Run(e) end, e.tooltip }
    end
  end
  if #ctrls > 0 then Ly.buttons(ctrls, min(3, #ctrls)); Ly.gap(sp().s) end
  local shown, open, secN = 0, true, 0
  for _, e in ipairs(lines) do
    if e.header then
      secN = secN + 1
      if W.Match(e.text) or query == "" then open = Ly.header(strip(e.text), tabId .. ":" .. secN) else open = true end
    elseif not e.control and open and not (skip and skip(e)) and W.Match(e.text, e.right, e.tooltip) then
      Ly.row(e)
      shown = shown + 1
    end
  end
  if shown == 0 and #ctrls == 0 then
    Ly.empty(query ~= "" and ("Rien ne correspond à « " .. query .. " ».") or emptyText or "Rien pour l'instant.", emptyAction)
  end
  return shown
end

-- -------------------------------------------------- construction
local navButtons = {}
local function selectedPage()
  local id = W.char and W.char.page or "now"
  return W.pageIndex[id] and id or (W.PAGES[1] and W.PAGES[1].id)
end
local function contentWidth()
  local sw = tonumber(meth(scroll, "GetWidth"))
  if sw and sw > 100 then return max(360, floor(sw)) end
  local ww = tonumber(meth(win, "GetWidth")) or 820
  return max(360, floor(ww - NAV_W - 4 - 3 - 26))     -- bords + colonne + barre de défilement
end

local function buildWindow()
  if win or not G.CreateFrame then return win end
  win = G.CreateFrame("Frame", "TreeAIMainWindow", G.UIParent, G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  meth(win, "SetSize", 820, 560)
  meth(win, "SetFrameStrata", "HIGH")
  meth(win, "SetToplevel", true)
  meth(win, "SetClampedToScreen", true)
  meth(win, "SetMovable", true)
  meth(win, "EnableMouse", true)
  meth(win, "Hide")
  local th = Th()
  local st = th and th:WindowState("main")
  if th and th.Shadow then th:Shadow(win, 10) end
  if th then
    th:Window(win, { id = "main", header = false, skin = "card",
      resize = { minW = 640, minH = 420, maxW = 1400, maxH = 1000, onResize = function() W.Render() end },
      onClose = function() W.Close() end })
  else
    win.bgt = meth(win, "CreateTexture", nil, "BACKGROUND"); meth(win.bgt, "SetAllPoints", win); color(win.bgt, 0.05, 0.06, 0.08, 0.95)
  end
  if st and st.point then meth(win, "ClearAllPoints"); meth(win, "SetPoint", st.point, G.UIParent, st.point, st.x or 0, st.y or 0)
  else meth(win, "SetPoint", "CENTER", G.UIParent, "CENTER", 0, 40) end
  -- colonne de gauche (fond un peu plus sombre) : titre + version + navigation
  local nav = G.CreateFrame("Frame", nil, win)
  meth(nav, "SetPoint", "TOPLEFT", win, "TOPLEFT", 3, -3)
  meth(nav, "SetPoint", "BOTTOMLEFT", win, "BOTTOMLEFT", 3, 3)
  meth(nav, "SetWidth", NAV_W)
  nav.bg = meth(nav, "CreateTexture", nil, "BACKGROUND")
  meth(nav.bg, "SetAllPoints", nav)
  color(nav.bg, 0, 0, 0, 0.35)
  win.nav = nav
  local drag = function(f)
    meth(f, "EnableMouse", true)
    meth(f, "RegisterForDrag", "LeftButton")
    meth(f, "SetScript", "OnDragStart", function() meth(win, "StartMoving") end)
    meth(f, "SetScript", "OnDragStop", function()
      meth(win, "StopMovingOrSizing")
      local pt, _, _, x, y = meth(win, "GetPoint")
      local s2 = th and th:WindowState("main")
      if s2 and pt then s2.point, s2.x, s2.y = pt, floor((x or 0) + 0.5), floor((y or 0) + 0.5) end
    end)
  end
  drag(nav)
  win.title = text(nav, "H", "gold")
  meth(win.title, "SetPoint", "TOPLEFT", nav, "TOPLEFT", PAD, -16)
  meth(win.title, "SetText", "TreeAI")
  win.ver = text(nav, "S", "muted")
  meth(win.ver, "SetPoint", "TOPLEFT", win.title, "BOTTOMLEFT", 1, -4)
  meth(win.ver, "SetText", "Coach · v" .. version())
  local sep = meth(win, "CreateTexture", nil, "ARTWORK")
  meth(sep, "SetWidth", 1)
  meth(sep, "SetPoint", "TOPLEFT", nav, "TOPRIGHT", 0, 0)
  meth(sep, "SetPoint", "BOTTOMLEFT", nav, "BOTTOMRIGHT", 0, 0)
  color(sep, tok("borderSoft", 0.78, 0.62, 0.28, 0.35))
  -- en-tête de page : grand titre + sous-titre, recherche, fermer
  local top = G.CreateFrame("Frame", nil, win)
  meth(top, "SetPoint", "TOPLEFT", nav, "TOPRIGHT", 1, 0)
  meth(top, "SetPoint", "TOPRIGHT", win, "TOPRIGHT", -3, -3)
  meth(top, "SetHeight", TOP_H)
  drag(top)
  win.top = top
  win.pageTitle = text(top, "L", "gold")
  meth(win.pageTitle, "SetPoint", "TOPLEFT", top, "TOPLEFT", PAD, -12)
  meth(win.pageTitle, "SetWordWrap", false)
  win.pageSub = text(top, "S", "muted")
  meth(win.pageSub, "SetPoint", "TOPLEFT", win.pageTitle, "BOTTOMLEFT", 0, -3)
  meth(win.pageSub, "SetWordWrap", false)
  local close = newButton(top)
  meth(close, "SetSize", 24, 24)
  meth(close, "SetPoint", "TOPRIGHT", top, "TOPRIGHT", -8, -8)
  close.t = text(close, "M", "bad")
  meth(close.t, "SetPoint", "CENTER", close, "CENTER", 0, 0)
  meth(close.t, "SetText", "×")
  border(close, tok("borderSoft", 0.78, 0.62, 0.28, 0.35))
  meth(close, "SetScript", "OnClick", function() W.Close() end)
  meth(close, "SetScript", "OnEnter", function(self) W.Tooltip(self, "Fermer", nil, "Clic : fermer (Échap aussi)") end)
  meth(close, "SetScript", "OnLeave", hideTip)
  local ok, eb = pcall(G.CreateFrame, "EditBox", "TreeAIMainSearch", top, "InputBoxTemplate")
  if not ok or not eb then ok, eb = pcall(G.CreateFrame, "EditBox", "TreeAIMainSearch", top) end
  if ok and eb then
    meth(eb, "SetSize", 200, 24)
    meth(eb, "SetPoint", "TOPRIGHT", close, "TOPLEFT", -16, 0)
    meth(eb, "SetAutoFocus", false)
    meth(eb, "SetMaxLetters", 40)
    if not eb.Left then   -- sans modèle : fond à nous
      eb.bgt = meth(eb, "CreateTexture", nil, "BACKGROUND"); meth(eb.bgt, "SetAllPoints", eb); color(eb.bgt, 0, 0, 0, 0.5)
      meth(eb, "SetFontObject", G.GameFontHighlightSmall)
    end
    eb.hint = text(eb, "S", "muted")
    meth(eb.hint, "SetPoint", "LEFT", eb, "LEFT", 6, 0)
    meth(eb.hint, "SetText", "Rechercher…")
    meth(eb, "SetScript", "OnTextChanged", function(self)
      local v = meth(self, "GetText") or ""
      if v ~= "" then meth(self.hint, "Hide") else meth(self.hint, "Show") end
      if v ~= query then query = v; W.Render() end
    end)
    meth(eb, "SetScript", "OnEscapePressed", function(self) meth(self, "SetText", ""); meth(self, "ClearFocus") end)
    meth(eb, "SetScript", "OnEnterPressed", function(self) meth(self, "ClearFocus") end)
    win.search = eb
  end
  meth(win.pageTitle, "SetPoint", "RIGHT", eb or close, "LEFT", -10, 0)
  -- contenu défilant
  local okS, sc = pcall(G.CreateFrame, "ScrollFrame", "TreeAIMainScroll", win, "UIPanelScrollFrameTemplate")
  if not okS or not sc then sc = G.CreateFrame("ScrollFrame", "TreeAIMainScroll", win) end
  meth(sc, "SetPoint", "TOPLEFT", top, "BOTTOMLEFT", 0, -2)
  meth(sc, "SetPoint", "BOTTOMRIGHT", win, "BOTTOMRIGHT", -26, FOOT_H)
  scroll = sc
  content = G.CreateFrame("Frame", nil, sc)
  meth(content, "SetSize", 600, 100)
  meth(sc, "SetScrollChild", content)
  meth(sc, "EnableMouseWheel", true)
  win.scroll, win.content = sc, content
  -- pied : statut à gauche, UN bouton d'action à droite
  local foot = G.CreateFrame("Frame", nil, win)
  meth(foot, "SetPoint", "BOTTOMLEFT", nav, "BOTTOMRIGHT", 1, 0)
  meth(foot, "SetPoint", "BOTTOMRIGHT", win, "BOTTOMRIGHT", -3, 3)
  meth(foot, "SetHeight", FOOT_H - 4)
  local rule = meth(foot, "CreateTexture", nil, "ARTWORK")
  meth(rule, "SetHeight", 1)
  meth(rule, "SetPoint", "TOPLEFT", foot, "TOPLEFT", 0, 0)
  meth(rule, "SetPoint", "TOPRIGHT", foot, "TOPRIGHT", 0, 0)
  color(rule, tok("borderSoft", 0.78, 0.62, 0.28, 0.35))
  win.status = text(foot, "S", "muted")
  meth(win.status, "SetPoint", "LEFT", foot, "LEFT", PAD, 0)
  meth(win.status, "SetWordWrap", false)
  local act = th and th:Button(foot, "", 170, 24) or newButton(foot)
  if not act.label then act.label = text(act, "S", "text"); meth(act.label, "SetPoint", "CENTER", act, "CENTER", 0, 0) end
  if not act.SetLabel then function act.SetLabel(self2, t2) meth(self2.label, "SetText", t2) end end
  meth(act, "SetSize", 170, 24)
  meth(act, "SetPoint", "RIGHT", foot, "RIGHT", -24, 0)
  meth(act, "SetScript", "OnClick", function(self) if self.fn then T:UserAction(self.fn) W.Dirty() W.Render() end end)
  meth(act, "SetScript", "OnEnter", function(self) if self.tip then W.Tooltip(self, self.labelText, nil, self.tip) end end)
  meth(act, "SetScript", "OnLeave", hideTip)
  win.action = act
  meth(win.status, "SetPoint", "RIGHT", act, "LEFT", -10, 0)
  -- navigation
  local y = 80
  local i = 0
  for _, p in ipairs(W.PAGES) do if not p.hidden then
    i = i + 1
    local b = newButton(nav)
    meth(b, "SetSize", NAV_W - 16, 24)
    meth(b, "SetPoint", "TOPLEFT", nav, "TOPLEFT", 8, -y)
    -- liseré gauche or quand la page est choisie (comme les listes de Leatrix / ElvUI)
    b.mark = meth(b, "CreateTexture", nil, "ARTWORK")
    meth(b.mark, "SetWidth", 2)
    meth(b.mark, "SetPoint", "TOPLEFT", b, "TOPLEFT", 0, 0)
    meth(b.mark, "SetPoint", "BOTTOMLEFT", b, "BOTTOMLEFT", 0, 0)
    color(b.mark, tok("gold", 1, 0.82, 0.2, 1))
    b.icon = meth(b, "CreateTexture", nil, "ARTWORK")
    meth(b.icon, "SetSize", 16, 16)
    meth(b.icon, "SetPoint", "LEFT", b, "LEFT", 6, 0)
    meth(b.icon, "SetTexture", p.icon)
    meth(b.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
    b.t = text(b, "M", "gold")
    meth(b.t, "SetPoint", "LEFT", b, "LEFT", 28, 0)
    meth(b.t, "SetText", p.label)
    b.badge = text(b, "S", "prio3")
    meth(b.badge, "SetPoint", "RIGHT", b, "RIGHT", -6, 0)
    meth(b, "SetScript", "OnClick", function() W.Select(p.id) end)
    meth(b, "SetScript", "OnEnter", function(self) W.Tooltip(self, p.label, p.desc and { p.desc } or nil, "Clic : ouvrir la page") end)
    meth(b, "SetScript", "OnLeave", hideTip)
    b.page = p.id
    navButtons[i] = b
    y = y + 28
    if p.gapAfter then y = y + 12 end
  end end
  meth(win, "SetScript", "OnShow", function() W.Render() end)
  meth(win, "SetScript", "OnHide", function() hideTip(); if win.search then meth(win.search, "ClearFocus") end end)
  return win
end

local function paintNav(cur)
  for _, b in ipairs(navButtons) do
    local on = b.page == cur
    if on then color(b.bg, tok("selected", 0.78, 0.62, 0.28, 0.30)) else color(b.bg, 0, 0, 0, 0) end
    if on then meth(b.mark, "Show") else meth(b.mark, "Hide") end
    meth(b.t, "SetTextColor", tok(on and "text" or "gold", 1, 0.82, 0.2))
    local p = W.pageIndex[b.page]
    local n = p and p.badge and try(p.badge)
    meth(b.badge, "SetText", n and tostring(n) or "")
  end
end

local renderPending, lastRender = false, -10
--- Redessine la page en cours (fenêtre ouverte seulement).
function W.Render()
  if not (win and meth(win, "IsShown")) then return end
  local id = selectedPage()
  local p = W.pageIndex[id]
  if not p then return end
  lastRender = try(G.GetTime) or 0
  releaseAll(pools)
  if W.HideOptionFrames then W.HideOptionFrames() end
  meth(scroll, "SetScrollChild", content)
  meth(content, "Show")
  local ttl = p.title
  if type(ttl) == "function" then ttl = try(ttl) end
  meth(win.pageTitle, "SetText", ttl or p.label)
  meth(win.pageSub, "SetText", p.sub and (type(p.sub) == "function" and (try(p.sub) or "") or p.sub) or "")
  paintNav(id)
  local Ly = newLay(contentWidth())
  W.lastLay = Ly
  local ok, err = pcall(p.render, Ly)
  if not ok then Ly.text("|cffff5555Erreur d'affichage : " .. tostring(err) .. "|r") if T.Debug then T:Debug("Window", err) end end
  Ly.finish()
  -- bouton d'action de la page (un seul, en bas à droite)
  local label, fn, tip
  if p.action then label, fn, tip = try(p.action) end
  if label and fn then
    win.action:SetLabel(label)
    win.action.fn, win.action.tip, win.action.labelText = fn, tip, label
    meth(win.action, "Show")
  else meth(win.action, "Hide") end
  meth(win.status, "SetText", p.status and (try(p.status) or "") or "")
end
--- Marque la page « à redessiner » (≤ 1 fois par seconde, fenêtre ouverte seulement).
function W.Dirty()
  cardSig, cardKey = nil, nil
  if not (win and meth(win, "IsShown")) or renderPending then return end
  local wait = 1 - ((try(G.GetTime) or 0) - lastRender)
  if wait <= 0 then W.Render() return end
  renderPending = true
  T:After(wait, function() renderPending = false W.Render() end)
end

function W.Select(id)
  if not W.pageIndex[id] then return end
  if W.char then W.char.page = id end
  query = ""
  if win and win.search then meth(win.search, "SetText", "") end
  if scroll then meth(scroll, "SetVerticalScroll", 0) end
  W.Render()
end
function W.Open(id)
  if not W:IsEnabled() then if T.ShowPanel then T:ShowPanel() end return end
  buildWindow()
  if not win then return end
  if id and W.pageIndex[id] and W.char then W.char.page = id end
  meth(win, "Show")
  W.Render()
end
function W.Close() if win then local th = Th() if th then th:FadeOut(win) else meth(win, "Hide") end end end
function W.Toggle(id)
  if win and meth(win, "IsShown") and (not id or selectedPage() == id) then W.Close() else W.Open(id) end
end
function W.IsOpen() return win and meth(win, "IsShown") and true or false end
function W.GetWindow() return win end
function W.CurrentPage() return selectedPage() end
T.ToggleMain = function(id) return W.Toggle(id) end   -- appelé par Bindings.xml
_G.BINDING_NAME_TREEAI_MAIN = "TreeAI : ouvrir / fermer la fenêtre"
-- noms lisibles des raccourcis (Bindings.xml : CLICK <bouton>:LeftButton), groupés par usage
for btn, label in pairs({
  TreeAIQuickItem = "Raccourci quête : utiliser l'objet de quête", TreeAIQuickTarget = "Raccourci quête : cibler l'objectif",
  TreeAIQuickMount = "Raccourci voyage : monture", TreeAIQuickHearth = "Raccourci voyage : pierre de foyer",
  TreeAIQuick_pull = "Raccourci combat : tirer (marque + familier)", TreeAIQuick_pet = "Raccourci combat : familier (appeler / ressusciter)",
  TreeAIQuick_mend = "Raccourci combat : soigner le familier", TreeAIQuick_eat = "Raccourci combat : manger / boire",
  TreeAIQuick_bandage = "Raccourci combat : bandage",
}) do _G["BINDING_NAME_CLICK " .. btn .. ":LeftButton"] = label end
_G.BINDING_HEADER_TREEAI = _G.BINDING_HEADER_TREEAI or "TreeAI"

-- ===================================================================== pages simples
local TAB_ICON = { now = ICON .. "Spell_Nature_TimeStop", quests = ICON .. "INV_Misc_Book_09", route = ICON .. "Ability_Rogue_Sprint",
  group = ICON .. "Spell_Holy_PrayerOfHealing", dungeon = ICON .. "INV_Misc_Key_03", hv = ICON .. "INV_Misc_Coin_01",
  tools = ICON .. "INV_Misc_Wrench_01", options = ICON .. "INV_Misc_Gear_01" }
W.TAB_ICON = TAB_ICON

W.AddPage({ id = "now", label = "Maintenant", icon = TAB_ICON.now, title = "Maintenant",
  desc = "Ce que tu peux faire tout de suite",
  sub = function() local g = W.Guide(1) local s = W.GuideSummary(g) return (g.zone or "") .. (s ~= "" and (" · " .. s) or "") end,
  badge = function()
    local n = 0
    for _, e in ipairs(T.GetTabLines and T:GetTabLines("now") or {}) do if (e.prio or 1) >= 3 then n = n + 1 end end
    return n > 0 and n or nil
  end,
  render = function(Ly)
    local g = W.Guide(3)
    local guided = #g.steps > 0 and not g.steps[1].entry
    if guided then
      Ly.header("Prochaine étape", nil, nil, ICON .. "INV_Misc_Book_09")
      for i, s in ipairs(g.steps) do Ly.step(s, i == 1) end
    end
    Ly.header("À faire maintenant", nil, nil, TAB_ICON.now)
    -- l'étape épinglée (trajet / « Étape ») est déjà dessinée au-dessus : pas deux fois
    local steps = T.STEP_SOURCES or {}
    W.RenderTab(Ly, "now", "Rien d'urgent : continue ton étape. Tout va bien (sacs, réparation, familier).",
      guided and function(e) return e.pin and steps[e.source or ""] and (e.prio or 1) < 3 end or nil)
  end,
  action = function()
    if W.db and W.db.card ~= false then
      return (card and meth(card, "IsShown")) and "Cacher la carte" or "Afficher la carte",
        function() if card and meth(card, "IsShown") then W.HideCard() else W.ShowCard() end end,
        "La petite carte « Prochaine étape » à l'écran"
    end
    return nil
  end,
})
-- (Quêtes, Trajet, Donjons : WindowPages.lua, insérés ici dans l'ordre de la navigation)
W.AddPage({ id = "quests", label = "Quêtes", icon = TAB_ICON.quests, title = "Quêtes", render = function(Ly) W.RenderTab(Ly, "quests") end })
W.AddPage({ id = "route", label = "Trajet", icon = TAB_ICON.route, title = "Trajet", render = function(Ly) W.RenderTab(Ly, "quests") end })
W.AddPage({ id = "group", label = "Groupe", icon = TAB_ICON.group, title = "Groupe", desc = "Tes amis, les missions, qui fait quoi",
  sub = function()
    local n = tonumber(try(G.GetNumGroupMembers)) or 0
    return n > 1 and fmt("Groupe de %d", n) or "Seul pour l'instant"
  end,
  render = function(Ly)
    local Q = T.QuickTravel
    local friends = (Q and Q._active and Q.FriendsToInvite) and (try(Q.FriendsToInvite) or {}) or {}
    W.RenderTab(Ly, "group", "Seul pour l'instant. Invite tes amis : TreeAI synchronise vos quêtes et vos trajets.", nil,
      (#friends > 0 and Q.InviteFriends) and { ("Inviter mes amis (%d)"):format(#friends), function() Q.InviteFriends() end,
        "Clic : invite les amis de ton dernier groupe" } or nil)
  end })
W.AddPage({ id = "dungeon", label = "Donjons", icon = TAB_ICON.dungeon, title = "Donjons", render = function(Ly) W.RenderTab(Ly, "dungeon") end })
W.AddPage({ id = "hv", label = "HV", icon = TAB_ICON.hv, title = "Hôtel des ventes", desc = "Achats-reventes sûrs, ventes, bilan",
  render = function(Ly)
    local A = T.modules and T.modules.Auction
    W.RenderTab(Ly, "hv", (A and A._active) and "Pas encore de prix : à l'hôtel des ventes, lance un scan complet."
      or "Le module Hôtel des ventes est coupé (fonction bêta).", nil,
      (A and not A._active) and { "Allumer l'HV (bêta)", function() T:SetModuleEnabled("Auction", true) end,
        "Clic : allume le module Hôtel des ventes" } or nil)
  end,
  action = function()
    local A = T.modules and T.modules.Auction
    if A and A.ShowWindow and A._active then return "Ouvrir la fenêtre HV", function() A.ShowWindow() end, "Clic : ouvrir la fenêtre de l'hôtel des ventes" end
    if A then return "Allumer l'HV (bêta)", function() T:SetModuleEnabled("Auction", true) end, "Clic : allumer le module Hôtel des ventes" end
  end })

local function tabTitle(id)
  local t = T.tabs and T.tabs[id]
  return t and (L[t.title or id] or t.title) or id
end
-- Outils : le lanceur de la pastille, en cartes (icône + titre + description), groupé
W.AddPage({ id = "tools", label = "Outils", icon = TAB_ICON.tools, title = "Outils", desc = "Recherche, macros, boussole, cadres, erreurs",
  gapAfter = false,
  render = function(Ly)
    local H = T.Hub
    local list = H and H.Entries and (try(H.Entries) or {}) or {}
    local groups = { { "outils", "Outils" }, { "reglages", "Réglages et diagnostic" } }
    local any = false
    for _, gp in ipairs(groups) do
      local items = {}
      for _, e in ipairs(list) do
        if e.group == gp[1] and e.fn and e.id ~= "options" and W.Match(e.title, e.desc, e.tip) then items[#items + 1] = e end
      end
      if #items > 0 then
        any = true
        if Ly.header(gp[2], "tools:" .. gp[1], #items) then
          for _, e in ipairs(items) do
            Ly.row({ text = e.title .. (e.state and ("  " .. e.state) or ""), right = nil, icon = e.icon,
              tooltip = e.desc, onClick = function() e.fn() if H.mode == "beta" then H.mode = "main" W.Select("modules") end end,
              clickHint = e.tip or "Clic : ouvrir" })
          end
        end
      end
    end
    local covered = { now = true, quests = true, group = true, hv = true, dungeon = true, modules = true }
    local others = {}
    for _, t in ipairs(T.GetTabs and T:GetTabs() or {}) do
      if not covered[t.id] and W.Match(tabTitle(t.id)) then others[#others + 1] = t end
    end
    if T.tabs and T.tabs.talents and not covered.talents and W.Match(tabTitle("talents")) then others[#others + 1] = T.tabs.talents end
    if #others > 0 and Ly.header("Autres pages", "tools:tabs", #others) then
      local H2 = T.Hub
      for _, t in ipairs(others) do
        local id = t.id
        Ly.row({ text = tabTitle(id), icon = (H2 and H2.TAB_ICON and H2.TAB_ICON[id]) or (ICON .. "INV_Misc_Note_01"),
          tooltip = H2 and H2.TAB_DESC and H2.TAB_DESC[id] or nil, onClick = function() W.OpenTab(id) end,
          clickHint = "Clic : ouvrir la page" })
      end
    end
    if Ly.header("Panneau texte (ancien)", "tools:panel", nil, nil, true) then
      Ly.row({ text = "Afficher / cacher le panneau à onglets", icon = ICON .. "INV_Misc_Note_06",
        tooltip = "L'ancien panneau texte, toujours là pour qui le préfère.",
        onClick = function() userPanel = true T:TogglePanel() end, clickHint = "Clic : afficher / cacher le panneau" })
    end
    if not any then Ly.empty("Rien ne correspond à « " .. query .. " ».") end
  end,
  action = function()
    if T.SetEditMode then return "Déplacer les cadres", function() T:SetEditMode(not T:IsEditMode()) end,
      "Clic : mode édition (glisser les cadres, molette = taille)" end
  end })
-- n'importe quel autre onglet du panneau (Infos, Talents, Équipement, Stats…) : page générique, hors navigation
W.AddPage({ id = "tab", hidden = true, label = "Onglet", icon = TAB_ICON.tools,
  title = function() return tabTitle(W.char and W.char.tab or "info") end,
  render = function(Ly) W.RenderTab(Ly, W.char and W.char.tab or "info") end })
--- Ouvre un onglet du panneau dans la fenêtre (sa page si elle existe, sinon la page générique).
local TAB_PAGE = { now = "now", quests = "quests", group = "group", hv = "hv", dungeon = "dungeon", modules = "modules" }
function W.OpenTab(id)
  local pid = TAB_PAGE[id]
  if not pid and T.tabs and T.tabs[id] then
    if W.char then W.char.tab = id end
    pid = "tab"
  end
  W.Open(pid or "now")
end
-- Modules (bêta compris) : la liste « Plus → Modules »
W.AddPage({ id = "modules", label = "Fonctions", icon = ICON .. "INV_Misc_EngGizmos_20", title = "Fonctions",
  desc = "Allumer / couper chaque fonction (bêta comprises)", gapAfter = true,
  render = function(Ly)
    Ly.text("Mode sûr : seules les fonctions fiables tournent. Les fonctions « bêta » sont testées hors du jeu : allume-les une par une.", "S", "muted")
    W.RenderTab(Ly, "modules")
  end,
  action = function()
    local prof = T.ModuleProfile and T:ModuleProfile()
    if prof == "sur" then return "Tout allumer", function() T:SetModuleProfile("tout") end, "Clic : allumer toutes les fonctions, bêta comprises" end
    return "Revenir au mode sûr", function() T:SetModuleProfile("sur") end, "Clic : seulement les fonctions fiables"
  end })

-- ===================================================================== Options : le moteur de mise en page de TIDY, dans notre fenêtre
local optFrames = {}
function W.HideOptionFrames() for _, f in pairs(optFrames) do meth(f, "Hide") end end
-- mots cherchés pour une page d'options : titre, description, libellés, sections
local function optionWords(e)
  local words = { e.label or "" }
  local mod = e.module and T.modules and T.modules[e.module]
  if mod then
    words[#words + 1] = mod.description or ""
    for _, o in ipairs(type(mod.options) == "table" and mod.options or {}) do
      words[#words + 1] = (o.label or "") .. " " .. (o.section or "")
    end
  end
  return tconcat(words, " ")
end
W.OptionWords = optionWords
W.AddPage({ id = "options", label = "Options", icon = TAB_ICON.options, title = "Options",
  desc = "Tous les réglages, rangés par famille",
  sub = "Choisis une page à gauche, ou cherche un réglage en haut à droite.",
  render = function(Ly)
    local list = T.OptionEntries and T:OptionEntries() or {}
    local sel = W.char and W.char.optPage
    -- liste des pages (familles) : à gauche du contenu ; la page choisie à droite (moteur TIDY)
    local listW = 168
    local shown, first = 0, nil
    local head
    local y0 = Ly.y
    local p = pool("optnav", function()
      local f = newButton(content)
      meth(f, "SetHeight", 20)
      f.t = text(f, "S", "text")
      meth(f.t, "SetPoint", "LEFT", f, "LEFT", 8, 0)
      meth(f.t, "SetWordWrap", false)
      meth(f, "SetScript", "OnClick", function(self) if W.char then W.char.optPage = self.key end W.Render() end)
      return f
    end)
    local ph = pool("opthead", function() local fs = text(content, "S", "gold") meth(fs, "SetWordWrap", false) return fs end)
    local yy = y0
    for _, e in ipairs(list) do
      if e.head then head = e.head
      elseif W.Match(optionWords(e)) then
        if head then
          local fs = acquire(ph)
          meth(fs, "SetWidth", listW - 8)
          meth(fs, "SetText", head)
          Ly.at(fs, PAD, yy + 6)
          Ly.rec("navhead", PAD, yy, listW, 20, head)
          yy, head = yy + 22, nil
        end
        local b = acquire(p)
        b.key = e.key
        meth(b, "SetWidth", listW - 8)
        meth(b.t, "SetWidth", listW - 16)
        meth(b.t, "SetText", e.label)
        local on = e.key == sel
        if on then color(b.bg, tok("selected", 0.78, 0.62, 0.28, 0.30)) else color(b.bg, 0, 0, 0, 0) end
        meth(b.t, "SetTextColor", tok(on and "gold" or "text", 0.9, 0.9, 0.9))
        Ly.at(b, PAD, yy)
        Ly.rec("navrow", PAD, yy, listW, 20, e.label)
        yy = yy + 20
        shown = shown + 1
        first = first or e
        if on then sel = e end
      end
    end
    if shown == 0 then Ly.empty("Aucun réglage ne correspond à « " .. query .. " ».") return end
    if type(sel) ~= "table" then sel = first end
    -- page choisie : construite une fois par le moteur TIDY (même rythme, rien ne se chevauche)
    local pw = max(300, Ly.W - listW - PAD * 2)
    local key = sel.key .. ":" .. pw
    local f = optFrames[key]
    if not f and sel.builder then
      f = G.CreateFrame("Frame", nil, content)
      meth(f, "SetSize", pw, 100)
      local okB, err = pcall(sel.builder, f, pw)
      if not okB and T.Debug then T:Debug("Window options", err) end
      optFrames[key] = f
    end
    for k2, f2 in pairs(optFrames) do if k2 ~= key then meth(f2, "Hide") end end
    if f then
      meth(f, "ClearAllPoints")
      meth(f, "SetPoint", "TOPLEFT", content, "TOPLEFT", PAD + listW + 4, -y0)
      meth(f, "Show")
      if T.RefreshOptionWidgets then T:RefreshOptionWidgets() end
      local fh = tonumber(meth(f, "GetHeight")) or 100
      Ly.rec("optpage", PAD + listW + 4, y0, pw, fh, sel.label)
      Ly.y = max(yy, y0 + fh)
    else
      Ly.y = yy
    end
    W.optSelected = sel.key
  end,
  action = function()
    return "Recharger l'interface", function() if not inCombat() then try(G.ReloadUI) end end,
      "Clic : recharger l'interface (certains réglages en ont besoin)"
  end })

-- ===================================================================== branchements (gardés)
-- un rendu du panneau (≤ 2/s) = la carte et la fenêtre se mettent à jour (signature : rien si rien n'a changé)
if type(T.RefreshPanel) == "function" and not W._wrapped then
  W._wrapped = true
  local orig = T.RefreshPanel
  T.RefreshPanel = function(self, ...)
    local r = orig(self, ...)
    if W._active then
      if card and meth(card, "IsShown") then T.safecall(W.RenderCard) end
      if win and meth(win, "IsShown") then W.Dirty() end
    end
    return r
  end
end
-- la carte / la fenêtre comptent comme « onglet à l'écran » (les modules redessinent pour elles)
if type(T.TabShown) == "function" and not W._wrappedShown then
  W._wrappedShown = true
  local orig = T.TabShown
  T.TabShown = function(self, id)
    if W._active then
      if card and id == "now" and meth(card, "IsShown") then return true end
      if win and meth(win, "IsShown") then
        local cur = selectedPage()
        if id == cur or (id == "quests" and (cur == "route" or cur == "now")) then return true end
      end
    end
    return orig(self, id)
  end
end
-- le joueur ouvre lui-même le panneau : on le laisse (jusqu'à ce qu'il le cache)
if type(T.ShowPanel) == "function" and not W._wrappedPanel then
  W._wrappedPanel = true
  local orig = T.ShowPanel
  -- panneau ouvert = la carte se retire (une seule vue de l'étape, jamais l'une sur l'autre) ; caché = elle revient
  T.ShowPanel = function(self, ...)
    userPanel = true
    if card and meth(card, "IsShown") then meth(card, "Hide") end
    return orig(self, ...)
  end
  local origH = T.HidePanel
  if type(origH) == "function" then
    T.HidePanel = function(self, ...)
      userPanel = false
      local r = origH(self, ...)
      if W._active and cardOn() and card then W.ShowCard() end
      return r
    end
  end
end

-- un clic qui ouvrait un onglet du panneau (encadré d'alerte, pastille…) ouvre la page de la fenêtre quand la
-- carte remplace le panneau (le panneau ouvert à la main garde ses onglets)
if type(T.SelectTab) == "function" and not W._wrappedTab then
  W._wrappedTab = true
  local orig = T.SelectTab
  T.SelectTab = function(self, id, ...)
    if W._active and cardOn() and W.db.replacePanel ~= false and not userPanel then
      local p = self.GetPanel and self:GetPanel()
      if not (p and p:IsShown()) then W.OpenTab(id) return end
    end
    return orig(self, id, ...)
  end
end

-- mode édition : le panneau s'y montre (le placer) ; en sortant, la carte reprend sa place
if type(T.SetEditMode) == "function" and not W._wrappedEdit then
  W._wrappedEdit = true
  local orig = T.SetEditMode
  T.SetEditMode = function(self, on, ...)
    local before = userPanel
    local r = orig(self, on, ...)
    userPanel = before
    if card and on and cardOn() then meth(card, "Show") end      -- en mode édition : les deux, pour les placer
    if not on then
      if userPanel then if card then meth(card, "Hide") end else hidePanelForCard() end
    end
    return r
  end
end

function W:OnEnable()
  if cardOn() then
    T:After(1.5, function() if W._active then W.ShowCard() end end)
  end
  self:On("LAYOUT_DONE", function() if card and meth(card, "IsShown") then W.PlaceCard() end end)
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() if win and meth(win, "IsShown") and W.db.closeInCombat then W.Close() end end)
  -- pas de minuterie à nous : la carte suit les rendus du panneau (contenu changé, étape épinglée qui bouge avec
  -- le joueur), les changements de zone et le journal ; ≤ 1 rendu / 2 s, signature comparée
  local function soon() if card and meth(card, "IsShown") then W.RenderCard() end end
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() W.ForgetStops() soon() end)
  self:RegisterEvent("QUEST_LOG_UPDATE", soon)
  if T.HUB_EXTRA and not W._hub then
    W._hub = true
    table.insert(T.HUB_EXTRA, 1, { group = "jeu", id = "window", title = "Fenêtre TreeAI", icon = ICON .. "INV_Misc_Book_09",
      tip = "Clic : la fenêtre TreeAI (Maintenant, Quêtes, Trajet, Donjons, Options)", fn = function() W.Toggle() end })
  end
end
function W:OnDisable()
  W.HideCard()
  W.Close()
  local s = T.settings and T.settings.panel
  if not (s and s.shown == false) and T.GetPanel and T:GetPanel() then meth(T:GetPanel(), "Show") end
end
function W:OnOptionChanged(key)
  if key == "card" or key == "replacePanel" then
    if cardOn() then W.ShowCard() else W.HideCard()
      local p = T.GetPanel and T:GetPanel()
      if p and not (T.settings and T.settings.panel and T.settings.panel.shown == false) then meth(p, "Show") end
    end
  elseif key == "cardScale" then W.PlaceCard()
  end
  W.RenderCard(true)
end

T:RegisterSlash("fenetre", function(arg)
  arg = tostring(arg or ""):lower()
  if arg == "carte" then if card and meth(card, "IsShown") then W.HideCard() else W.ShowCard() end return end
  W.Toggle(arg ~= "" and arg or nil)
end, "fenêtre TreeAI (/tai fenetre [now|quests|route|dungeon|options|carte])")
