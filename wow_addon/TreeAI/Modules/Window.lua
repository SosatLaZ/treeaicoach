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
-- Pages : Modules/WindowPages.lua (Quêtes — avec le trajet —, Donjons).
-- (4.1 LAYOUT) 7 pages : Quêtes (les 4 modes, prochaine étape, trajet, à prendre, ménage), Groupe (Partager),
-- Donjons, Personnage (Macros · Talents · Équipement · Métiers), HV, Conseils pro, Réglages (Raccourcis · Affichage ·
-- Fonctions · Mes addons · Options · Outils) ; anciennes pages = sous-pages ou alias (W.ALIAS / W.PARENT / W.SUBS).
-- 1re ouverture sur la moitié droite de l'écran, ensuite là où le joueur l'a laissée.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G
local L = T.L or setmetatable({}, { __index = function(_, k) return k end })

local W = T:RegisterModule("Window", {
  title = "Fenêtre TreeAI",
  description = "La fenêtre « TreeAI » (pastille de la minimap ou touche) et la petite carte « Prochaine étape ».",
  -- (4.1 STRIP) carte coupée d'office : les boutons rapides (Shortcuts.lua) prennent sa place en haut à gauche
  defaults = { enabled = true, card = false, replacePanel = true, cardSteps = 3, cardScale = 100 },
  charDefaults = { page = "home", collapsed = {}, dungeon = nil, sub = {} },
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
-- glyphes absents des polices du jeu (« ▾ », « → »…) : la même table que le panneau (Core.SafeText)
local function safe(s)
  s = tostring(s or "")
  if type(T.SafeText) ~= "function" then return s end
  local ok, r = pcall(T.SafeText, s)   -- (une valeur secrète du jeu passe telle quelle : le jeu sait l'afficher)
  return (ok and r) or s
end
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
local LINE_H = { S = 13, M = 15, L = 17, H = 19 }       -- (5.1) L = 14 px, H = 16 px (Theme V6)
local AVG = { S = 5.4, M = 6.2, L = 7.2, H = 8.2 }
local function text(parent, size, tokName, layer)
  local th = Th()
  local fs
  if th then fs = th:Text(parent, size, tokName, layer)
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
  -- (4.0 QA) « → », « ✓ »… : carrés vides dans les polices du jeu → même table que le panneau (SafeText)
  meth(tt, "AddLine", safe(strip(title or "")), 1, 0.82, 0.2, true)
  if type(lines) == "string" then
    for l in lines:gmatch("[^\n]+") do meth(tt, "AddLine", safe(l), 0.85, 0.85, 0.85, true) end
  elseif type(lines) == "table" then
    for _, l in ipairs(lines) do meth(tt, "AddLine", safe(tostring(l)), 0.85, 0.85, 0.85, true) end
  end
  if hint then meth(tt, "AddLine", safe(hint), 0.4, 0.8, 1, true) end
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
      color(e, r, g, bl, 0.12)   -- (5.1) survol : un liseré blanc discret (plus de cadre doré)
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
  f.num = text(f, "M", "text")
  meth(f.num, "SetPoint", "CENTER", f, "CENTER", 0, 0)
  meth(f.num, "SetJustifyH", "CENTER")
  f.mark = meth(f, "CreateTexture", nil, "OVERLAY")
  meth(f.mark, "SetSize", floor(size * 0.55), floor(size * 0.55))
  meth(f.mark, "SetPoint", "BOTTOMRIGHT", f, "BOTTOMRIGHT", 4, -3)
  -- (4.0 QA) ligne sans numéro (pas une étape du trajet) : l'icône de la ligne dans la pastille, jamais un faux n°
  f.icon = meth(f, "CreateTexture", nil, "ARTWORK", nil, 3)
  meth(f.icon, "SetPoint", "TOPLEFT", f, "TOPLEFT", 6, -6)
  meth(f.icon, "SetPoint", "BOTTOMRIGHT", f, "BOTTOMRIGHT", -6, 6)
  meth(f.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
  meth(f.icon, "Hide")
  function f.Set(self2, n, mark, hot, icon)
    local r, g, b = tok(hot and "accent" or "muted", 0.6, 0.6, 0.63)   -- (5.1) anneau gris ; accent pour l'étape en cours
    meth(self2.ring, "SetVertexColor", r, g, b, hot and 0.9 or 0.45)
    meth(self2.disc, "SetVertexColor", 0.04, 0.04, 0.05, 1)
    meth(self2.num, "SetText", n and tostring(n) or "")
    if mark then meth(self2.mark, "SetTexture", mark); meth(self2.mark, "Show") else meth(self2.mark, "Hide") end
    if icon and not n then meth(self2.icon, "SetTexture", icon); meth(self2.icon, "Show") else meth(self2.icon, "Hide") end
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
-- (4.0 QA, FIX-QUEST) jamais gardée au-delà d'un saut : pierre de foyer, fin de vol, entrée / sortie d'instance
-- (W.ForgetStops sur ces évènements) ni d'un déplacement de plus de 400 yd (les trajets partent d'où tu es)
local stopsKeep = {}
function W.StopsCached(P, R)
  local t = try(G.GetTime) or 0
  local k = stopsKeep
  local n = P.steps and #P.steps or 0
  local here
  local Gp = T.Geo and T.Geo.Player
  if Gp and R.World then
    local m, x, y = Gp()
    if m and x then here = R.World({ map = m, x = x, y = y }) end
  end
  local moved = false
  if here and k.here0 then
    if here.c ~= k.here0.c then moved = true
    elseif here.n and k.here0.n then
      local dn, dw = here.n - k.here0.n, (here.w or 0) - (k.here0.w or 0)
      moved = dn * dn + dw * dw > 160000
    end
  end
  local st
  if k.steps == P.steps and k.cur == P.cursor and k.n == n and k.rev == P.stepsRev and k.st and not moved and t - k.at < 30 then
    st = k.st
  else
    st = R:Stops(64) or {}
    stopsKeep = { steps = P.steps, cur = P.cursor, n = n, rev = P.stepsRev, st = st, at = t, here0 = here }
  end
  if here then st.here = here end
  return st
end
function W.ForgetStops() stopsKeep = {} end
--- (ZONEFIX) UNE ligne d'arrêt, la même sur la carte « Prochaine étape » et sur la page Trajet (avant : deux
--- formats, et « objectif · ≈ ≈ 5 min sur place » sur la page). e = entrée de Route:Stops ; via = un transport (vol,
--- bateau, foyer) avant elle ; k = rang affiché. Titre : « Prendre « Titre entier » », « Ramasse « X » » ; sous-titre :
--- « objectif 0/4 · « Quête » · 640 m au nord · ≈ 3 min » pour l'arrêt 1, « … · dans ≈ 12 min » ensuite (le temps du
--- trajet, transports compris : jamais une ligne droite à pied vers un arrêt lointain).
--- (4.1 INTEG) l'arrêt que vise la boussole : SA ligne de distance (même texte, même arrondi que la boussole), sinon nil
function W.CompassWhere(step)
  local Cm = T.Compass
  if not (step and type(Cm) == "table" and Cm.Current and Cm.Widgets) then return nil end
  local cur = try(Cm.Current)
  if not (type(cur) == "table" and cur.plan and cur.step == step) then return nil end
  local wd = try(Cm.Widgets)
  if not (type(wd) == "table" and wd.frame and not wd.mini and meth(wd.frame, "IsShown")) then return nil end
  local d = strip(meth(wd.dist, "GetText") or ""):gsub("%s+", " ")
  return d ~= "" and d or nil
end
function W.StopItem(P, e, i, st, grp, via, k)
  local s = e.step
  local step = { n = (grp and grp[i]) or k, kind = s and s.k or e.kind, q = s and s.q }
  if s then
    -- (4.0 QA) Prendre / Rendre : le titre entier (le libellé du plan le coupe à 32 lettres) ; la carte le
    -- raccourcit elle-même une seule fois, la page Quêtes / Maintenant a la place de l'écrire en entier
    local full = (s.k == "pick" or s.k == "turn") and not (s.label and s.label ~= "") and s.q and questTitle(s.q)
    step.title = full and ((s.k == "pick" and "Prendre « " or "Rendre « ") .. strip(full) .. " »") or strip(P:StepLabel(s))
    local lq = P.log and s.q and P.log[s.q]
    local o = lq and lq.obj and lq.obj[tonumber(s.o) or 0]
    local prog = (o and (o.n or 0) > 0) and fmt("%d/%d", o.have or 0, o.n) or nil
    local where
    if via or (k or 1) > 1 then
      local m = minutesTxt(max(0, (tonumber(e.at) or 0) - (tonumber(e.sec) or 0)))
      where = m and ("dans " .. m) or nil
    else where = W.CompassWhere(s) or whereTxt(st.here, e.place) end
    local parts = {}
    -- (4.0 QA) « nouvelle quête » / « quête finie » : la pastille « ! » / « ? » le dit déjà
    if s.k == "obj" then
      local qt = s.q and questTitle(s.q)
      parts[#parts + 1] = prog and ("objectif " .. prog) or "objectif"
      if qt then parts[#parts + 1] = "« " .. strip(qt) .. " »" end
    end
    if where then parts[#parts + 1] = where end
    if s.out then parts[#parts + 1] = s.onWay and "hors zone, en chemin" or "hors zone" end
    step.sub = tconcat(parts, " · ")
    step.mark = MARK[s.k]
    step.icon = KIND_ICON[s.k] or KIND_ICON.obj
    -- (4.0 QA) clic offert seulement quand l'étape a une position (sinon rien à viser) ; la pierre de foyer ne se
    -- rejoint pas à pied : pas de guidage, un rappel
    if s.k == "hs" then
      step.hint = "Pierre de foyer : utilise-la (sacs ou barre de quête)"
    elseif s.map and s.x then
      step.onClick = function() P:Navigate(s) end
      step.actionText = (k or 1) == 1 and "la boussole vise cet arrêt" or "guider la boussole vers cette étape"
    end
    local Zp = T.ZonePlan
    if Zp and Zp.StopMenu and Zp.IsEnabled and Zp:IsEnabled() then step.onRightClick = function() Zp:StopMenu(s) end end
  else
    step.title = strip(e.text or "?")
    step.sub = "trajet · " .. (minutesTxt(e.sec) or "≈ 1 min")
    step.icon = KIND_ICON[e.kind] or KIND_ICON.fly
    -- (QA-QUEST F4) vol / bateau / zeppelin : guider vers le DÉPART (maître de vol, quai : `from`), pas vers
    -- l'arrivée ; pierre de foyer : rien à rejoindre, un rappel au survol
    local p = e.kind ~= "hs" and (e.from or e.place) or nil
    if e.kind == "hs" then
      step.hint = "Pierre de foyer : utilise-la (sacs ou barre de quête)"
    elseif p and p.map then
      step.onClick = function() P:Navigate({ k = "fly", map = p.map, x = p.x, y = p.y, label = e.text }) end
      step.actionText = "guider vers ce départ"
    end
  end
  return step
end
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
    -- (QUESTQA F7) le même horizon que l'onglet « Trajet » (1 h 30 / 30 arrêts) : une seule durée pour un trajet
    local keepN, total = #st, tonumber(st.total) or 0
    if type(R.Horizon) == "function" then
      local okH, kn, tt = pcall(R.Horizon, st)
      if okH and tonumber(kn) then keepN, total = kn, tonumber(tt) or total end
    end
    g.total = total
    -- (QA-QUEST F18) mêmes numéros d'arrêt que l'onglet, la page Trajet et la carte du monde (R.Groups)
    local grp
    if type(R.Groups) == "function" then
      local okG, gg = pcall(R.Groups, st, keepN)
      if okG and type(gg) == "table" then grp = gg end
    end
    local seenQ, k, via = {}, 0, false
    for i, e in ipairs(st) do
      if i > keepN then break end
      local s = e.step
      -- (QUESTS 4.1) une ligne par ARRÊT : les étapes d'un même arrêt (même quête au même endroit, même PNJ) sont dans
      -- le titre de la 1re (Route.StopLabel), plus 3 lignes « dans ~13 min » pour le même lieu
      local sameStop = grp and i > 1 and grp[i] == grp[i - 1] and s and st[i - 1].step
      if sameStop then
        -- rien : déjà dans la ligne de l'arrêt
      elseif k < n then
        k = k + 1
        local step = W.StopItem(P, e, i, st, grp, via, k)
        if s and grp and grp[i + 1] == grp[i] and type(R.StopLabel) == "function" then
          local okL, gl = pcall(R.StopLabel, P, st, i, grp)
          if okL and type(gl) == "string" then step.title = strip(gl) end
        end
        g.steps[#g.steps + 1] = step
      elseif full and s and s.k == "pick" and s.q and not seenQ[s.q] and #g.upcoming < 8 then
        seenQ[s.q] = true
        local lv, t2 = questLevel(s.q)
        g.upcoming[#g.upcoming + 1] = { q = s.q, level = lv, title = questTitle(s.q) or t2 or ("Quête " .. s.q),
          onClick = (s.map and s.x) and function() P:Navigate(s) end or nil }
      end
      local pl, h = e.place, st.here
      if s and s.q and not via and pl and h and pl.c == h.c and pl.n and h.n then
        local dn, dw = pl.n - h.n, pl.w - h.w
        if dn * dn + dw * dw < 490000 then g.near = g.near + 1 end     -- < 700 yd (≈ 640 m, 3 min à pied)
      end
      if e.kind and e.kind ~= "quest" then via = true end           -- vol, bateau, zeppelin, pierre de foyer
    end
    g.count = keepN
  end
  if #g.steps == 0 and not g.paused then
    -- pas de trajet : les lignes de « Maintenant » (l'étape épinglée de Quests / Planner d'abord), SANS numéro
    -- (ce ne sont pas des étapes) et sans les avis du cœur (« Mode sûr… », profil) qui ne sont pas des choses à faire
    for _, e in ipairs(T.GetTabLines and T:GetTabLines("now") or {}) do
      if #g.steps >= n then break end
      local id = tostring(e.id or "")
      if not e.header and not e.control and e.kind ~= "always" and not id:find("^core:") then
        g.steps[#g.steps + 1] = { n = nil, title = strip(e.text), sub = e.right and strip(e.right) or nil,
          icon = e.icon, danger = (e.prio or 1) >= 3, entry = e, onClick = e.onClick, onRightClick = e.onRightClick }
      end
    end
  end
  return g
end

--- « 12 dans le journal · 3 arrêts proches · ≈ 3 min » (rien d'inventé : chaque morceau absent est omis) ;
--- short = la carte (place comptée) : avec un trajet, le journal est laissé à la page « Quêtes »
function W.GuideSummary(g, short)
  local p = {}
  local m = minutesTxt(g.total)
  if g.inLog > 0 and not (short and (g.near > 0 or m)) then p[#p + 1] = fmt("%d dans le journal", g.inLog) end
  if g.near > 0 then p[#p + 1] = fmt("%d arrêt%s proche%s", g.near, g.near > 1 and "s" or "", g.near > 1 and "s" or "") end
  if m then p[#p + 1] = m end
  return tconcat(p, " · ")
end

-- ===================================================================== carte « Prochaine étape »
local card
local CARD_W, HERO_H, STEP_H = 280, 48, 36          -- grille de 4 px
local function cardOn() return W:IsEnabled() and W.db and W.db.card ~= false end
-- (4.1 STRIP) l'affichage de l'étape = la carte, ou à défaut les boutons rapides : le panneau texte reste caché
local function hudOn()
  if cardOn() then return true end
  local Sc = T.Shortcuts
  return (W:IsEnabled() and type(Sc) == "table" and type(Sc.IsHUD) == "function" and Sc.IsHUD()) and true or false
end
local function buildCard()
  if card or not G.CreateFrame then return card end
  card = G.CreateFrame("Frame", "TreeAICard", G.UIParent, G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  meth(card, "SetSize", CARD_W, HERO_H + 10)
  meth(card, "SetFrameStrata", "MEDIUM")
  meth(card, "SetClampedToScreen", true)
  meth(card, "SetMovable", true)
  meth(card, "EnableMouse", true)
  local th = Th()
  -- (4.0 QA) thème refusé (Options > Thème décoché) : notre fond sombre, jamais un texte posé sur le monde
  if not (th and th:Skin(card, "card")) then
    card.bgt = meth(card, "CreateTexture", nil, "BACKGROUND"); meth(card.bgt, "SetAllPoints", card); color(card.bgt, 0.05, 0.06, 0.08, 0.9)
  end
  if th and th.Shadow then th:Shadow(card, 8) end
  -- en-tête : icône + titre + résumé ; clic = fenêtre TreeAI (page Quêtes), glisser = déplacer
  local hero = newButton(card)
  meth(hero, "SetPoint", "TOPLEFT", card, "TOPLEFT", 4, -4)
  meth(hero, "SetPoint", "TOPRIGHT", card, "TOPRIGHT", -4, -4)
  meth(hero, "SetHeight", HERO_H)
  color(hero.bg, tok("tile", 1, 1, 1, 0.045))
  border(hero, tok("borderSoft", 1, 1, 1, 0.06))
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
  -- (ZONEFIX) en trajet : clic = mode suivant (Le plus rapide → Finir la zone → Par histoire → Récompenses → Groupe) ;
  -- « Ouvrir » ouvre la fenêtre ; en donjon / mort : la fenêtre, comme avant
  local function styleable() local Zp = T.ZonePlan return card.mode == "route" and card.routed and Zp and Zp.CycleStyle and Zp end
  meth(hero, "SetScript", "OnClick", function(self, btn)
    if btn == "RightButton" then
      if W.char then W.char.optPage = "Window" end
      W.Open("options")
      return
    end
    local Zp = styleable()
    if Zp then
      T:UserAction(function() Zp:CycleStyle() end)
      W.RenderCard(true)
      local oe = self and self:GetScript("OnEnter")
      if oe and self.IsMouseOver and self:IsMouseOver() then oe(self) end
    else W.Open((card.mode == "dungeon" and "dungeon") or (card.mode == "focus" and "prof") or "quests") end
  end)
  meth(hero, "SetScript", "OnEnter", function(self)
    if card.mode == "focus" then   -- (PROFLEVEL) session métier
      W.Tooltip(self, "Session métier", { "Le trajet de quêtes est en pause : chaque étape t'amène au bon PNJ.",
        "Glisser : déplacer la carte" }, "Clic : la page « Métiers » (quitter la session)\nClic droit : réglages de la carte")
      return
    end
    if card.mode == "dungeon" then
      W.Tooltip(self, "Prochaine étape", { "En donjon : le prochain boss, les objets de quête, la préparation.",
        "Glisser : déplacer la carte" }, "Clic : ouvrir la fenêtre TreeAI (Donjons)\nClic droit : réglages de la carte")
      return
    end
    local Zp = styleable()
    if Zp then
      local th = Zp.ThemeParts and select(2, pcall(Zp.ThemeParts, Zp))
      local lines = {}
      if type(th) == "table" and th.full then lines[#lines + 1] = th.full end
      for l in tostring(Zp.StyleTip and Zp:StyleTip() or ""):gmatch("[^\n]+") do lines[#lines + 1] = l end
      lines[#lines + 1] = "Glisser : déplacer la carte"
      W.Tooltip(self, "Mode du trajet : " .. tostring(Zp:StyleName()), lines,
        "Clic : mode suivant\nOuvrir : la fenêtre TreeAI · Clic droit : réglages de la carte")
      return
    end
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
  mb.t = text(mb, "S", "accent")
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
      -- (PLACEBUG) glissée par son en-tête (même avant 4.0.1) = posée par le joueur : rien ne la reprend
      isPlaced = function() return W.char and type(W.char.cardPos) == "table" or false end,
      -- (4.0 QA) mode édition : la carte est LE cadre à placer (affichée avant que son calque soit posé)
      show = function()
        if not cardOn() or inCombat() then return end
        W.RenderCard(true)
        meth(card, "Show")
      end,
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
    if s.entry then return entryTip(self, s.entry) end   -- ligne de « Maintenant » / du donjon : texte entier + clic
    local hint = s.onClick and ("Clic : " .. (s.actionText or "faire cette étape")) or s.hint
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
-- (4.0 QA) la carte coupe elle-même ses textes (224 px, petite police) : UNE ellipse, et dans « … » le titre de
-- quête est raccourci en gardant le guillemet fermant (jamais « Prendre « Les salines : le venin acide de… » »)
local CARD_TXT = 224
local function ucut(s, n) return (T.Cut(s, n, ""):gsub("%s+$", "")) end   -- n caractères UTF-8 (Core)
local function fitCard(s)
  s = strip(s or "")
  if ulen(s) * AVG.S <= CARD_TXT then return s end
  local maxG = floor(CARD_TXT / AVG.S)
  local pre, inner, post = s:match("^(.-« )(.-)( »)$")
  if pre then
    inner = inner:gsub("…$", "")
    local room = maxG - ulen(pre) - ulen(post) - 1
    if room >= 6 then return pre .. ucut(inner, room) .. "…" .. post end
  end
  return ucut(s:gsub("…$", ""), maxG - 1) .. "…"
end
W.FitCard, W.UCut = fitCard, ucut
-- une ligne trop longue pour la carte (224 px, petite police) : début en titre, la suite en sous-titre
local function splitLine(s)
  s = strip(s)
  if ulen(s) * AVG.S <= 220 then return s, nil end
  for _, sep in ipairs({ " — ", " · ", " : " }) do
    local a = s:find(sep, 1, true)
    if a and a > 6 then return s:sub(1, a - 1), s:sub(a + #sep) end
  end
  return s, nil
end
local function lineRow(e, n, extra)
  local t1, t2 = splitLine(e.text)
  local r = { n = n, title = t1, sub = t2 or (e.right and strip(e.right)) or nil, entry = e, icon = e.icon,
    danger = (e.prio or 1) >= 3 }
  for k, v in pairs(extra or {}) do r[k] = v end
  return r
end
-- (4.0 QA) mort / fantôme : la course au corps passe avant tout (ligne « Cadavre : … » du module Speed, contrat
-- d'id « corpse », quelle que soit sa priorité) ; jamais une étape de quête à un fantôme
local function deathRows(now, ps)
  for _, e in ipairs(now) do
    if tostring(e.id or ""):find("corpse", 1, true) and not e.header and not e.control then
      return { lineRow(e, "!", { danger = true }) }
    end
  end
  if ps.ghost then
    return { { n = "!", title = "Fantôme : retourne à ton corps", sub = "la boussole le vise", danger = true } }
  end
  return { { n = "!", title = "Mort : attends un rez ou libère ton esprit", sub = "ensuite, la boussole vise ton corps",
    danger = true } }
end
-- (4.0 QA) en donjon : les lignes du module Donjon (prochain boss, objets de quête, préparation), jamais les
-- étapes de quête du dehors. Contrat : T:GetTabLines("dungeon"), une source par bloc (Dungeon.BLOCKS).
local DG_ORDER = { ["Prochain boss"] = 1, ["Quêtes du donjon"] = 2, ["Préparation"] = 3, ["Dungeon"] = 4, ["Chasseur"] = 5 }
local function inDungeon()
  local D = T.Dungeon
  if D and type(D.Instance) == "function" then
    local ok, inInst = pcall(D.Instance)
    return ok and inInst and true or false
  end
  local inInst, kind = try(G.IsInInstance)
  return (inInst == true and (kind == "party" or kind == "raid" or kind == "scenario")) and true or false
end
W.InDungeon = inDungeon
local function dungeonRows(n)
  local D = T.Dungeon
  if not (D and D._active) then
    return { { title = "Suivi en donjon coupé (bêta)", sub = "clic : l'allumer (prochain boss, objets de quête)",
      icon = ICON .. "INV_Misc_Key_03", actionText = "allumer le module Donjon (sans /reload)",
      onClick = function() T:SetModuleEnabled("Dungeon", true) end } }
  end
  local cand = {}
  for i, e in ipairs(T.GetTabLines and T:GetTabLines("dungeon") or {}) do
    local rank = DG_ORDER[e.source or ""]
    if rank and not e.header and not e.control and (e.source ~= "Dungeon" or (e.prio or 1) >= 3) then
      cand[#cand + 1] = { e = e, r = rank, i = i }
    end
  end
  tsort(cand, function(a, b) if a.r ~= b.r then return a.r < b.r end return a.i < b.i end)
  local rows = {}
  for _, c in ipairs(cand) do
    if #rows >= n then break end
    rows[#rows + 1] = lineRow(c.e, nil, { icon = c.e.icon or (ICON .. "INV_Misc_Key_03") })
  end
  return rows
end
local function dungeonSub()
  local D = T.Dungeon
  local d = D and D._active and try(D.Current)
  if d and type(D.NextBoss) == "function" then
    local ok, _, nk, ntot = pcall(D.NextBoss, d)
    if ok and tonumber(ntot) and ntot > 0 then return fmt("Donjon · %d/%d boss", tonumber(nk) or 0, ntot) end
  end
  return "Donjon"
end

-- avis / questions d'une fois montrés en tête de la carte (badge « i ») : sans eux, des lignes disparaîtraient sans
-- un mot (mode sûr) ou un automatisme resterait inconnu (quêtes automatiques, proposées une fois)
W.CARD_NOTICE = {
  ["core:safe"] = { "Mode sûr : fonctions bêta coupées", "clic : les voir et les allumer" },
  ["mod:quick:auto"] = { "Accepter et rendre les quêtes tout seul ?", "clic : oui · clic droit : non" },
}
--- (4.0 QA) ce que montre la carte, calculé une fois pour tous (carte, infobulle de la pastille) :
--- rows, mode ("route" | "dungeon" | "death"), titre, résumé, en pause. `now` = lignes de « Maintenant ».
function W.CardModel(nSteps, now, ps)
  now = now or (T.GetTabLines and T:GetTabLines("now")) or {}
  ps = ps or (T.PlayerState and T:PlayerState()) or {}
  nSteps = nSteps or 3
  local dead = (ps.dead or ps.ghost) and true or false
  local danger, notice
  for _, e in ipairs(now) do
    if not e.header and not e.control then
      if not danger and (e.prio or 1) >= 3 then danger = e end
      if not notice and W.CARD_NOTICE[e.id or ""] then notice = e end
    end
  end
  local rows, mode, title, sum, paused = {}, "route", zoneName() or "?", nil, false
  if dead then
    mode, sum = "death", ps.ghost and "Fantôme : course au corps" or "Mort"
    rows = deathRows(now, ps)
  else
    if notice then
      local nt = W.CARD_NOTICE[notice.id] or {}
      rows[#rows + 1] = lineRow(notice, "i", { notice = true, danger = false, title = nt[1], sub = nt[2] })
    end
    if danger then rows[#rows + 1] = lineRow(danger, "!", { danger = true }) end
    -- (PROFLEVEL 4.0.2) mode focus (contrat T.focus, ex. « Session métier ») : ses étapes à la place du trajet en pause
    local F = T.focus
    local frows, ftitle, fsum
    if not inDungeon() and type(F) == "table" and type(F.card) == "function" then
      local okF
      okF, frows, ftitle, fsum = pcall(F.card, nSteps)
      if not okF or type(frows) ~= "table" then frows = nil end
    end
    if frows then
      mode, title, sum = "focus", ftitle or F.label or "Focus", fsum or ""
      for _, r in ipairs(frows) do rows[#rows + 1] = r end
    elseif inDungeon() then
      mode, sum = "dungeon", dungeonSub()
      for _, r in ipairs(dungeonRows(nSteps)) do rows[#rows + 1] = r end
    else
      local g = W.Guide(nSteps)
      paused = g.paused and true or false
      if paused then title = "Trajet en pause" end
      sum = W.GuideSummary(g, true)
      if sum == "" then sum = "Aucune quête en cours ici" end
      -- (ZONEFIX) en-tête = le thème en cours (« Orneval · Bois-brisé : la flamme sacrée (3 quêtes) », raccourci pour
      -- tenir) ; résumé = le mode du trajet (clic sur l'en-tête : le suivant) + la durée
      local first = g.steps[1]
      local Zp = T.ZonePlan
      if not paused and first and not first.entry and Zp and Zp.ThemeParts then
        local ok, th = pcall(Zp.ThemeParts, Zp)
        title = (ok and W.CardTitle(th)) or title
        local style = Zp.StyleName and Zp:StyleName()
        local tm = minutesTxt(g.total)
        if style then sum = style .. (tm and (" · " .. tm) or "") end
      end
      for _, r in ipairs(g.steps) do rows[#rows + 1] = r end
    end
  end
  return rows, mode, title, sum, paused, danger, notice
end
--- (ZONEFIX) titre de la carte : le thème le plus complet qui tient sur la ligne (zone · camp : histoire (n), puis
--- camp : histoire, histoire (n quêtes), zone · camp, zone) ; th = ZonePlan:ThemeParts()
local TITLE_PX = 166
function W.CardTitle(th)
  if type(th) ~= "table" or not th.full then return nil end
  local z, hub, story, n = th.zone, th.hub, th.story, th.n
  local cands = {}
  if th.full then cands[#cands + 1] = th.full end
  if hub and story then cands[#cands + 1] = hub .. " : " .. story .. ((n and n >= 2) and fmt(" (%d)", n) or "") end
  if story then
    local cap = story:gsub("^%l", string.upper)
    cands[#cands + 1] = cap .. ((n and n >= 2) and fmt(" (%d quêtes)", n) or "")
    cands[#cands + 1] = cap
  end
  if z and hub then cands[#cands + 1] = z .. " · " .. hub end
  if z then cands[#cands + 1] = z end
  for _, c in ipairs(cands) do if ulen(c) * (AVG.M or 7) <= TITLE_PX then return c end end
  return cands[#cands]
end
--- (4.0 QA) la première vraie ligne de la carte (pas un avis) : l'infobulle de la pastille dit la même chose
function W.TopLine()
  local rows = W.CardModel(1)
  for _, r in ipairs(rows) do if not r.notice then return r end end
  return nil
end

-- (4.0.1) la carte suit les rendus du panneau à l'image suivante (jamais dans la même image que le calcul qui l'a
-- changée) et au plus une fois toutes les 2 s : un changement arrivé pendant ces 2 s est redessiné à leur fin (avant,
-- il attendait le rendu suivant, parfois longtemps)
local cardSoon = false
function W.RenderCardSoon()
  if cardSoon then return end
  cardSoon = true
  local wait = max(0, 2 - ((try(G.GetTime) or 0) - cardAt))
  T:After(wait, function()
    cardSoon = false
    if W._active and card and meth(card, "IsShown") then T.safecall(W.RenderCard) end
  end)
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
  -- danger d'abord (familier mort, sacs pleins…) : une ligne rouge au-dessus des étapes ; l'avis « mode sûr »
  -- une fois en tête (badge « i ») : sans lui, les lignes des fonctions bêta disparaîtraient sans un mot
  local now = T.GetTabLines and T:GetTabLines("now") or {}     -- déjà en cache après un rendu du panneau
  local ps = T.PlayerState and T:PlayerState() or {}
  local dead = (ps.dead or ps.ghost) and true or false
  local dg = not dead and inDungeon()
  local danger, notice
  for _, e in ipairs(now) do
    if not e.header and not e.control then
      if not danger and (e.prio or 1) >= 3 then danger = e end
      if not notice and W.CARD_NOTICE[e.id or ""] then notice = e end
    end
  end
  -- clé : avec un trajet, seules les étapes / le journal / la position / le danger comptent (pas les autres
  -- onglets) ; sans trajet, la carte montre « Maintenant » → toute nouvelle ligne compte ; en donjon / mort : idem
  local routed = P and P.steps and R and R.IsEnabled and R:IsEnabled()
  local key = ((routed and not dg and not dead) and "" or tostring(T.DataGen and T:DataGen())) .. tostring(P and P.steps)
    .. tostring(P and P.cursor) .. tostring(P and P.steps and #P.steps) .. tostring(Q and Q.lastList) .. playerBucket()
    .. tostring(R and R.char and R.char.paused) .. (danger and tostring(danger.text) or "") .. (notice and "N" or "")
    .. (ps.ghost and "G" or (ps.dead and "D" or "")) .. (dg and "I" or "")
    .. ((type(T.focus) == "table") and ("F" .. tostring(T.focus.gen)) or "")   -- (PROFLEVEL) étapes du mode focus
  if not force and key == cardKey then return end
  cardKey, cardAt = key, t
  local nSteps = min(5, max(1, tonumber(W.db.cardSteps) or 3))
  local rows, mode, title, sum, paused = W.CardModel(nSteps, now, ps)
  card.mode = mode
  card.routed = false                                -- de vrais arrêts numérotés (pas des lignes de « Maintenant »)
  for _, r in ipairs(rows) do if type(r.n) == "number" and not r.entry then card.routed = true break end end
  local sigT = { mode, title, sum }
  for _, s in ipairs(rows) do sigT[#sigT + 1] = tostring(s.n) .. "|" .. (s.title or "") .. "|" .. (s.sub or "") end
  local sig = tconcat(sigT, "\n")
  if sig == cardSig and not force then return end
  cardSig = sig
  local hero = card.hero
  meth(hero.title, "SetText", safe(title))
  meth(hero.sub, "SetText", safe(sum))
  local y = 4 + HERO_H + 4
  local first = true
  for i, s in ipairs(rows) do
    local r = stepRow(i)
    r.step = s
    meth(r, "ClearAllPoints")
    meth(r, "SetPoint", "TOPLEFT", card, "TOPLEFT", 4, -y)
    meth(r, "SetPoint", "TOPRIGHT", card, "TOPRIGHT", -4, -y)
    local lead = first and not s.danger and not s.notice
    r.badge:Set(s.n, s.mark, i == 1 or lead, s.icon)
    meth(r.title, "SetText", fitCard(safe(s.title or "")))
    if s.danger then meth(r.title, "SetTextColor", tok("prio3", 1, 0.3, 0.25))
    elseif lead then meth(r.title, "SetTextColor", tok("accent", 0.45, 0.74, 1))
    else meth(r.title, "SetTextColor", tok("text", 0.92, 0.92, 0.92)) end
    if lead then first = false end
    meth(r.sub, "SetText", fitCard(safe(s.sub or "")))
    local h = (s.sub and s.sub ~= "") and STEP_H or 24
    meth(r, "SetHeight", h)
    meth(r, "Show")
    y = y + h + 4
  end
  for i = #rows + 1, #card.steps do meth(card.steps[i], "Hide") end
  if #rows == 0 then
    meth(card.empty, "SetText", (mode == "dungeon" and "En donjon : TreeAI n'a pas de fiche pour celui-ci.")
      or (paused and "Trajet en pause : « Ouvrir », puis « Reprendre ».")
      or "Rien à suivre ici : prends des quêtes (PNJ marqués « ! ») ou ouvre « Donjons ».")
    meth(card.empty, "Show")
    y = y + 32
  else meth(card.empty, "Hide") end
  meth(card, "SetHeight", y + 2)
end

-- position : la place posée par le joueur (glissée, mode édition) ; sinon celle que le placement automatique a
-- choisie pour le panneau (zone libre d'un bord, loin des suivis de quêtes et des cadres de groupe) ; sinon sa place
-- par défaut. (PLACEBUG 4.0.1) Jamais une place incomplète lue « 0, 0 », jamais ClearAllPoints sans SetPoint réussi.
local function okAnchor(p, x, y) return T.ValidAnchor and T.ValidAnchor(p, x, y) or false end
local function anchorTo(f, p, x, y)
  if T.SetAnchor then return T:SetAnchor(f, p, x, y) end
  meth(f, "ClearAllPoints")
  return meth(f, "SetPoint", p, G.UIParent, p, x, y) ~= false
end
function W.SaveCardPosition()
  if not card then return end
  -- le crochet du cœur (StopMovingOrSizing) a déjà reposé la carte sur UIParent avec un ancrage explicite ; sans lui
  -- (client sans hooksecurefunc), même calcul ici depuis le rectangle réel — jamais l'ancrage du client relu tel quel
  if T.AnchorFromRect then T:AnchorFromRect(card) end
  local pt, _, _, x, y = meth(card, "GetPoint")
  if W.char and okAnchor(pt, x, y) then W.char.cardPos = { point = pt, x = floor(x + 0.5), y = floor(y + 0.5) } end
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
  if type(pos) == "table" and not okAnchor(pos.point, pos.x, pos.y) then W.char.cardPos, pos = nil, nil end
  if type(pos) == "table" then anchorTo(card, pos.point, pos.x, pos.y) return end   -- posée par le joueur
  local c = T.PanelChar and T:PanelChar()
  -- la place automatique du panneau seulement si le placement a déjà tourné (sinon la place par défaut du
  -- panneau, au centre, n'est pas une place pour la carte) ou si le joueur a placé le panneau
  local auto = c and okAnchor(c.point, c.x, c.y) and (c.manual or not T.LayoutPlaced or T:LayoutPlaced())
  if not (auto and anchorTo(card, c.point, c.x, c.y)) then anchorTo(card, "RIGHT", -40, 60) end
  -- garde-fou : posée sur un suivi de quêtes (Questie, Suivi Forever…) → descend sous lui, sinon côté opposé ;
  -- seulement quand l'écran est posé et contrôlé (à la connexion, les suivis ne sont pas encore mesurables), et
  -- jamais vers une place hors de l'écran ou dans un coin
  if T.ScreenStable and not T:ScreenStable() then return end
  local hit, r = overlapsGuide()
  if hit and r then
    local P = G.UIParent
    local Wd, Hd = tonumber(meth(P, "GetWidth")) or 0, tonumber(meth(P, "GetHeight")) or 0
    local cw, ch = (tonumber(meth(card, "GetWidth")) or CARD_W) * k, (tonumber(meth(card, "GetHeight")) or 120) * k
    local l, top
    if r.b - ch - 10 > 120 then l, top = r.l, r.b - 10
    else l, top = ((r.l + r.w / 2 > Wd / 2) and 40 or (Wd - cw - 40)), r.b + r.h end
    local inside = Wd > 200 and Hd > 200 and l >= 8 and l + cw <= Wd - 8 and top - ch >= 8 and top <= Hd - 8
    if inside then anchorTo(card, "TOPLEFT", floor(l / k + 0.5), floor((top - Hd) / k + 0.5)) end
  end
end

-- le panneau texte : caché quand la carte le remplace (sauf si le joueur l'ouvre lui-même)
local userPanel = false
local function hidePanelForCard()
  if not (hudOn() and W.db.replacePanel ~= false) or userPanel then return end
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
  -- (4.0 QA) dessinée d'abord, posée ensuite : le garde-fou « pas sur un suivi de quêtes » mesure sa vraie taille
  W.RenderCard(true)
  W.PlaceCard()
  meth(card, "Show")
  hidePanelForCard()
end
function W.HideCard() if card then meth(card, "Hide") end end
--- (4.0 QA) la carte est-elle l'affichage de l'étape (le panneau texte n'apparaît que si le joueur l'a ouvert) ?
function W.CardIsHUD() return (W._active and hudOn() and W.db.replacePanel ~= false and not userPanel) and true or false end
T.CardIsHUD = function() return W.CardIsHUD() end
--- (4.0 QA) « Réinitialiser l'interface », assistant de démarrage : la carte revient (à la place automatique du
--- panneau si resetPos), le panneau texte se cache ; faux = la carte ne remplace pas le panneau (l'appelant le montre)
function W.ShowHUD(resetPos)
  if not (W._active and hudOn() and W.db.replacePanel ~= false) then return false end
  userPanel = false
  if resetPos and W.char then W.char.cardPos = nil end
  local p = T.GetPanel and T:GetPanel()
  if p and meth(p, "IsShown") then meth(p, "Hide") end
  if cardOn() then W.ShowCard() else T.safecall(T.Shortcuts.ShowHUD, resetPos) end
  return true
end
--- ligne « Zone : … » de la page Trajet (choix de la zone de travail) si elle est à l'écran, sinon nil (visite guidée)
function W.ZoneRow()
  local b = W.zoneRow
  if type(b) == "table" and type(b.entry) == "table" and b.entry.id == "zone:pick" and meth(b, "IsVisible") then return b end
  return nil
end
function W.GetCard() return card end

-- ===================================================================== fenêtre principale
local win
W.PAGES = {}       -- { id, label, icon, render(ctx), action(ctx) -> label, fn, tip }
W.pageIndex = {}
-- (4.3 V43) menu re-trié en 11 catégories claires, chacune avec ses 1 à 3 actions en tête : Quêtes · Trajet · Groupe ·
-- Donjons · Équipement · Métiers · HV · Chasseur (familier, talents) · Conseils · Macros & touches · Réglages. Les
-- anciennes pages gardent leur id : W.Open("perso" / "journal" / "tools" / "macros" / "keys"…) mène au bon endroit.
-- (5.0 LOOK) 11 catégories au plus, chacune avec son icône : Quêtes · Trajet · Groupe · Donjons · Équipement · Sacs ·
-- Métiers · HV · Chasseur · Conseils · Réglages. Une catégorie n'apparaît que si son module a inscrit sa page
-- (W.AddPage depuis son fichier) : « Sacs » arrive avec le module Sacs. « Macros & touches » est rangé dans Réglages.
-- (5.1 LOOK) « trop compliqué » → 9 catégories au plus, en mots simples, et UNE page d'accueil : Maintenant · Quêtes ·
-- Groupe · Équipement · Sacs · Métiers · HV · Chasseur · Réglages. « Trajet » est un onglet de Quêtes, « Donjons » un
-- onglet de Groupe, « Conseils » une ligne des Réglages : rien n'a disparu (W.Open("route" / "dungeon" / "coach"),
-- la recherche et les touches y mènent toujours), il y a seulement moins de portes d'entrée.
W.NAV = { "home", "quests", "group", "gear", "bags", "prof", "hv", "hunter", "settings" }
-- onglets en haut d'une page de la navigation : { page mère = { elle-même, ses pages sœurs } }
W.TABS = { quests = { "quests", "route" }, group = { "group", "dungeon" } }
-- ancienne page fondue dans une autre (même contenu, même endroit) + noms français tapés à la main (/tai fenetre sacs)
W.ALIAS = { now = "home", maintenant = "home", accueil = "home", perso = "gear", binds = "keys", sacs = "bags", inventory = "bags", inventaire = "bags",
  equipement = "gear", metiers = "prof", trajet = "route", groupe = "group", donjons = "dungeon", quetes = "quests",
  reglages = "settings", affichage = "display", touches = "keys", profil = "profile", conseils = "coach" }
-- page pas (encore) inscrite par son module → la page la plus proche qui existe (jamais une fenêtre vide)
W.FALLBACK = { bags = "gear", profile = "display" }
-- page rangée sous une page de la navigation (hors navigation, surlignée sous sa page mère)
W.PARENT = { journal = "quests", brain = "quests", route = "quests", dungeon = "group", coach = "settings",
  pet = "hunter", talents = "hunter", macros = "settings", keys = "settings", spellkeys = "settings",
  display = "settings", profile = "settings", modules = "settings", addons = "settings", options = "settings",
  tools = "settings", navlog = "settings", tab = "settings" }
-- sous-pages de la page mère (dans cet ordre) ; la mère ouvre la dernière choisie. Onglets en haut de page, sauf
-- W.SIDE : sous-liste à gauche façon Leatrix Plus (Réglages)
W.SUBS = { hunter = { "pet", "talents" },
  settings = { "display", "addons", "profile", "keys", "macros", "spellkeys", "coach", "modules", "options", "tools" } }
W.SIDE = { settings = true }
--- id demandé → id de la page réellement dessinée (alias résolus, page absente → la plus proche, page mère → sa sous-page)
function W.Resolve(id)
  id = W.ALIAS[id] or id
  if not W.pageIndex[id] and W.FALLBACK[id] and W.pageIndex[W.FALLBACK[id]] then id = W.FALLBACK[id] end
  local subs = W.SUBS[id]
  if subs then
    local last = W.char and type(W.char.sub) == "table" and W.char.sub[id]
    if last and W.pageIndex[last] then return last end
    for _, c in ipairs(subs) do if W.pageIndex[c] then return c end end
  end
  return id
end
--- page de la navigation qui contient `id` (elle-même si elle y est)
function W.NavOf(id)
  id = W.ALIAS[id] or id
  local p = W.pageIndex[id]
  return (p and p.parent) or W.PARENT[id] or id
end
--- Ajoute une page à la navigation (ordre d'appel) ; render(L) dessine avec la mise en page `L`.
function W.AddPage(p)
  if type(p) ~= "table" or not p.id then return end
  if W.PARENT[p.id] then p.parent, p.hidden = W.PARENT[p.id], true end
  -- (5.0) une catégorie de la navigation (W.NAV : « bags »…) y a sa place, même si son module l'inscrit encore comme
  -- sous-page cachée d'une autre (« parent = "hv", hidden = true » d'avant la 5.0)
  for _, n in ipairs(W.NAV) do if n == p.id then p.parent, p.hidden = nil, nil end end
  if W.pageIndex[p.id] then
    for k, v in pairs(p) do W.pageIndex[p.id][k] = v end
    for _, n in ipairs(W.NAV) do if n == p.id then W.pageIndex[p.id].parent, W.pageIndex[p.id].hidden = nil, nil end end
    return
  end
  -- (MACRO3) p.before = id d'une page : insérée juste avant elle (sinon à la fin)
  local at = #W.PAGES + 1
  if p.before then for i, q in ipairs(W.PAGES) do if q.id == p.before then at = i break end end end
  table.insert(W.PAGES, at, p)
  W.pageIndex[p.id] = p
end
local NAV_W, TOP_H, FOOT_H, PAD = 176, 56, 40, 16      -- grille de 4 px (Theme.GRID)
W.NAV_W = NAV_W

local function version()
  local v = T.API and T.API.AddOnMeta and T.API.AddOnMeta("TreeAI", "Version")
  if not (type(v) == "string" and v ~= "") then v = T.version end
  v = tostring(v or "?")
  return v:match("^(%d+%.%d+)%.0$") or v           -- « 4.0.0 » → « 4.0 »
end
W.Version = version

local pools = {}
local content, scroll
local function pool(kind, make) local p = pools[kind] if not p then p = newPool(make); pools[kind] = p end return p end
-- (4.0 QA) réserve enregistrée : vidée (cachée, compteur remis à 0) à chaque rendu et à chaque changement de page
-- → les pages (cartes de donjon…) recyclent leurs cadres au lieu d'en créer à chaque rendu
W._pool = pool

-- -------------------------------------------------- moteur de mise en page (vertical, une colonne, sans chevauchement)
-- Lay.y avance à chaque widget ; Lay.rects garde chaque rectangle (le harnais vérifie : rien ne se chevauche).
-- (5.0 LOOK) x0 = décalage de la colonne (la sous-liste des Réglages est à gauche) ; tous les widgets en héritent.
-- API stable (les pages des autres modules l'utilisent) : gap, text, header, row, hero, step, quest, buttons, empty,
-- tabs, tiles + V5 : actions (grosses tuiles), toggle (bascule), choice, card, note, kv, legend.
local function newLay(width, x0)
  x0 = tonumber(x0) or 0
  local PAD0 = PAD
  local PAD = PAD0 + x0   -- luacheck: ignore (masque voulu : la marge gauche de CETTE mise en page)
  local Ly = { y = 8, W = width, col = width - x0 - 2 * PAD0, rects = {}, x0 = x0, pad = PAD }
  local function rec(kind, x, y, w, h, label) Ly.rects[#Ly.rects + 1] = { kind = kind, x = x, y = y, w = w, h = h, label = strip(label) } end
  local function at(f, x, y) meth(f, "ClearAllPoints"); meth(f, "SetPoint", "TOPLEFT", content, "TOPLEFT", x, -y) end
  Ly.at, Ly.rec = at, rec
  function Ly.gap(h) Ly.y = Ly.y + (h or sp().m) end
  -- paragraphe
  function Ly.text(str, size, tokName, indent)
    local fs = acquire(pool("text" .. (size or "S"), function() return text(content, size or "S", "text") end))
    local x = PAD + (indent or 0)
    local w = Ly.col - (indent or 0)
    str = safe(str)
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
      -- (5.1) un titre de section comme ceux de la colonne « Suivi Forever » : mot or, filet fin dessous, pas de boîte
      color(f.bg, 0, 0, 0, 0)
      f.rule = meth(f, "CreateTexture", nil, "ARTWORK")
      meth(f.rule, "SetHeight", 1)
      meth(f.rule, "SetPoint", "BOTTOMLEFT", f, "BOTTOMLEFT", 0, 1)
      meth(f.rule, "SetPoint", "BOTTOMRIGHT", f, "BOTTOMRIGHT", 0, 1)
      color(f.rule, tok("border", 1, 1, 1, 0.10))
      f.icon = meth(f, "CreateTexture", nil, "ARTWORK")
      meth(f.icon, "SetSize", 14, 14)
      meth(f.icon, "SetPoint", "LEFT", f, "LEFT", 2, 0)
      f.t = text(f, "M", "gold")
      meth(f.t, "SetWordWrap", false)
      f.n = text(f, "S", "muted")
      meth(f.n, "SetPoint", "RIGHT", f, "RIGHT", -26, 0)
      meth(f.n, "SetJustifyH", "RIGHT")
      f.pm = text(f, "M", "muted")
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
    meth(b.t, "SetPoint", "LEFT", b, "LEFT", icon and 22 or 2, 0)
    meth(b.t, "SetPoint", "RIGHT", b.n, "LEFT", -6, 0)
    meth(b.t, "SetText", safe(str))
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
    -- (5.1) pas d'impasse : une ligne de conseil sans clic reçoit le clic évident (W.DefaultAction), s'il y en a un
    if not e.onClick and W.DefaultAction then
      local okD, fn, hint = pcall(W.DefaultAction, e)
      if okD and type(fn) == "function" then e.onClick, e.clickHint, e.autoAction = fn, e.clickHint or hint, true end
    end
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
    meth(b.t, "SetText", safe(e.text))
    meth(b.t, "SetTextColor", r, g, bl)
    meth(b.r, "SetText", safe(e.right or ""))
    local h = max(24, measure(b.t, safe(e.text), tw, "S") + 12)
    meth(b, "SetSize", Ly.col, h)
    if e.onClick then color(b.bg, tok("tile", 1, 1, 1, 0.045)) else color(b.bg, 0, 0, 0, 0) end   -- (5.1) seule une ligne cliquable a un fond
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
      f.t = text(f, "L", "text")
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
    color(b.bg, tok("tile", 1, 1, 1, 0.045))
    border(b, tok("borderSoft", 1, 1, 1, 0.06))
    meth(b.icon, "SetTexture", icon or (ICON .. "INV_Misc_Book_09"))
    for _, fs in ipairs({ b.t, b.s, b.s2 }) do meth(fs, "SetWidth", Ly.col - 72) end
    meth(b.t, "SetText", safe(title or ""))
    meth(b.s, "SetText", safe(sub or ""))
    meth(b.s2, "SetText", safe(sub2 or ""))
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
        local hint = st.onClick and ("Clic : " .. (st.actionText or "faire cette étape")) or st.hint
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
    meth(b.t, "SetText", safe(s.title))
    if s.danger then meth(b.t, "SetTextColor", tok("prio3", 1, 0.3, 0.25))
    elseif hot then meth(b.t, "SetTextColor", tok("accent", 0.45, 0.74, 1))
    else meth(b.t, "SetTextColor", tok("text", 0.92, 0.92, 0.92)) end
    meth(b.s, "SetText", safe(s.sub or ""))
    local h = BLOCK.step
    meth(b, "SetSize", Ly.col, h)
    if hot then color(b.bg, tok("selected", 0.45, 0.74, 1, 0.18)); border(b, 0, 0, 0, 0)
    else color(b.bg, tok("tile", 1, 1, 1, 0.045)); border(b, 0, 0, 0, 0) end
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
    meth(b.t, "SetText", safe(label))
    meth(b.t, "SetTextColor", r, g, bl)
    meth(b.s, "SetText", safe(sub or ""))
    meth(b, "SetSize", Ly.col, BLOCK.quest)
    color(b.bg, tok("tile", 1, 1, 1, 0.045))
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
    -- (5.0) jamais un mot coupé : colonne étroite (sous-liste des Réglages, petite fenêtre) → moins de boutons par ligne
    local need = 0
    for _, it in ipairs(list) do need = max(need, ulen(it[1]) * 6.2 + 8) end
    while cols > 1 and bw < need do
      cols = cols - 1
      bw = floor((Ly.col - (cols - 1) * 8) / cols)
    end
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
      meth(b.label, "SetText", safe(it[1]))
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
  -- (4.1 LAYOUT) onglets d'une page (sous-pages) : list = { {id, label, badge} }, cur = id choisi ; clic = W.Select
  function Ly.tabs(list, cur)
    if #list == 0 then return end
    local n = #list
    local bw = floor((Ly.col - (n - 1) * 4) / n)
    local p = pool("subtab", function()
      local f = newButton(content)
      f.t = text(f, "M", "text")
      meth(f.t, "SetPoint", "CENTER", f, "CENTER", 0, 0)
      meth(f.t, "SetWordWrap", false)
      meth(f.t, "SetJustifyH", "CENTER")
      f.line = meth(f, "CreateTexture", nil, "ARTWORK")
      meth(f.line, "SetHeight", 1)
      meth(f.line, "SetPoint", "BOTTOMLEFT", f, "BOTTOMLEFT", 0, 0)
      meth(f.line, "SetPoint", "BOTTOMRIGHT", f, "BOTTOMRIGHT", 0, 0)
      color(f.line, tok("accent", 0.45, 0.74, 1, 1))
      meth(f, "SetScript", "OnClick", function(self) if self.page then W.Select(self.page) end end)
      meth(f, "SetScript", "OnEnter", function(self) W.Tooltip(self, self.labelText, self.desc and { self.desc } or nil, "Clic : ouvrir") end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end)
    for i, it in ipairs(list) do
      local b = acquire(p)
      local on = it[1] == cur
      local label = it[2] .. (it[3] and (" (" .. tostring(it[3]) .. ")") or "")
      meth(b, "SetSize", bw, 24)
      meth(b.t, "SetWidth", bw - 8)
      meth(b.t, "SetText", safe(label))
      -- (5.1) onglets : des mots, celui qui est choisi en accent avec un trait dessous — pas de boîtes
      meth(b.t, "SetTextColor", tok(on and "accent" or "muted", 0.6, 0.6, 0.63))
      color(b.bg, 0, 0, 0, 0)
      if on then meth(b.line, "Show") else meth(b.line, "Hide") end
      b.page, b.labelText, b.desc = it[1], it[2], it[4]
      local x = PAD + (i - 1) * (bw + 4)
      at(b, x, Ly.y)
      rec("tab", x, Ly.y, bw, 24, label)
    end
    Ly.y = Ly.y + 24 + sp().m
  end
  -- (4.1 LAYOUT) grandes tuiles (les modes de quêtes) : list = { {title, sub, fn, tip, active, hint} }, 2 colonnes
  function Ly.tiles(list, cols)
    cols = cols or 2
    if #list == 0 then return end
    local bw = floor((Ly.col - (cols - 1) * 8) / cols)
    local th = 46
    local p = pool("tile", function()
      local f = newButton(content)
      f.t = text(f, "M", "text")
      meth(f.t, "SetPoint", "TOPLEFT", f, "TOPLEFT", 10, -7)
      meth(f.t, "SetWordWrap", false)
      f.s = text(f, "S", "muted")
      meth(f.s, "SetPoint", "TOPLEFT", f.t, "BOTTOMLEFT", 0, -4)
      meth(f.s, "SetWordWrap", false)
      meth(f, "SetScript", "OnClick", function(self) if self.fn then T:UserAction(self.fn) W.Dirty() W.Render() end end)
      meth(f, "SetScript", "OnEnter", function(self) W.Tooltip(self, self.titleText, self.tipL, self.hint) end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end)
    for i, it in ipairs(list) do
      local c = (i - 1) % cols
      if c == 0 and i > 1 then Ly.y = Ly.y + th + 6 end
      local b = acquire(p)
      meth(b, "SetSize", bw, th)
      meth(b.t, "SetWidth", bw - 20); meth(b.s, "SetWidth", bw - 20)
      meth(b.t, "SetText", safe(it[1]))
      meth(b.s, "SetText", safe(it[2] or ""))
      if it[5] then
        color(b.bg, tok("selected", 0.45, 0.74, 1, 0.18)); border(b, 0, 0, 0, 0)
        meth(b.t, "SetTextColor", tok("accent", 0.45, 0.74, 1))
      else
        color(b.bg, tok("tile", 1, 1, 1, 0.045)); border(b, 0, 0, 0, 0)
        meth(b.t, "SetTextColor", tok("text", 0.9, 0.9, 0.9))
      end
      b.fn, b.titleText, b.tipL, b.hint = it[3], it[1], it[4], it[6]
      local x = PAD + c * (bw + 8)
      at(b, x, Ly.y)
      rec("tile", x, Ly.y, bw, th, it[1])
    end
    Ly.y = Ly.y + th + sp().m
  end
  -- ================================================================ (5.0 LOOK) widgets V5 — hérités par toutes les pages
  -- GROSSES TUILES D'ACTION (≤ 3 par ligne) : list = { { titre, fn, infobulle, icon = , sub = , count = , active = ,
  -- disabled = }, … } — les 3 premiers champs sont ceux de Ly.buttons (compatibles). Un clic = l'action.
  function Ly.actions(list, cols)
    local keep = {}
    for _, it in ipairs(type(list) == "table" and list or {}) do if type(it) == "table" and it[1] then keep[#keep + 1] = it end end
    if #keep == 0 then return 0 end
    cols = max(1, min(cols or 3, #keep, 3))
    local bw = floor((Ly.col - (cols - 1) * 8) / cols)
    -- (5.1) UNE action principale par page (la 1re : teinte d'accent), les autres discrètes ; tuiles moins hautes
    local anySub, anyPrimary = false, false
    for _, it in ipairs(keep) do
      if type(it.sub) == "string" and it.sub ~= "" then anySub = true end
      if it.primary ~= nil or it.active then anyPrimary = true end   -- (la page dit elle-même laquelle est LA principale)
    end
    local bh = anySub and 38 or 28
    local p = pool("action", function()
      local f = newButton(content)
      f.icon = meth(f, "CreateTexture", nil, "ARTWORK")
      meth(f.icon, "SetSize", 18, 18)
      meth(f.icon, "SetPoint", "LEFT", f, "LEFT", 8, 0)
      f.t = text(f, "M", "text")
      meth(f.t, "SetWordWrap", false)
      f.s = text(f, "S", "muted")
      meth(f.s, "SetWordWrap", false)
      f.n = text(f, "S", "text")
      meth(f.n, "SetJustifyH", "RIGHT")
      meth(f.n, "SetPoint", "TOPRIGHT", f, "TOPRIGHT", -8, -6)
      meth(f, "SetScript", "OnClick", function(self)
        if self.off or not meth(self, "IsShown") then return end   -- (tuile rangée dans la réserve : plus d'action)
        if self.fn then T:UserAction(self.fn) W.Dirty() W.Render() end
      end)
      meth(f, "SetScript", "OnEnter", function(self) W.Tooltip(self, self.titleText, self.tipL, self.hint) end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end)
    for i, it in ipairs(keep) do
      local c = (i - 1) % cols
      if c == 0 and i > 1 then Ly.y = Ly.y + bh + 8 end
      local b = acquire(p)
      local icon = it.icon
      meth(b, "SetSize", bw, bh)
      meth(b.icon, "SetTexture", icon)
      if icon then
        meth(b.icon, "Show")
        if tostring(icon):find("Icons\\", 1, true) then meth(b.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92) else meth(b.icon, "SetTexCoord", 0, 1, 0, 1) end
      else meth(b.icon, "Hide") end
      local tx = icon and 32 or 10
      local hasSub = type(it.sub) == "string" and it.sub ~= ""
      meth(b.t, "ClearAllPoints"); meth(b.s, "ClearAllPoints")
      meth(b.t, "SetPoint", "TOPLEFT", b, "TOPLEFT", tx, hasSub and -5 or -floor((bh - 12) / 2))
      meth(b.s, "SetPoint", "TOPLEFT", b, "TOPLEFT", tx, -21)
      local cnt = tonumber(it.count)
      local cw = (cnt and cnt > 0) and 26 or 0
      meth(b.t, "SetWidth", max(20, bw - tx - 8 - cw)); meth(b.s, "SetWidth", max(20, bw - tx - 8))
      meth(b.t, "SetText", safe(it[1]))
      meth(b.s, "SetText", hasSub and safe(it.sub) or "")
      meth(b.n, "SetText", (cnt and cnt > 0) and tostring(cnt) or "")
      b.off = it.disabled and true or false
      if b.off then
        color(b.bg, tok("button", 0.14, 0.15, 0.19, 0.45)); border(b, 0, 0, 0, 0)
        meth(b.t, "SetTextColor", tok("disabled", 0.5, 0.5, 0.52)); meth(b.icon, "SetDesaturated", true)
      elseif it.active then
        color(b.bg, tok("selected", 0.45, 0.74, 1, 0.18)); border(b, 0, 0, 0, 0)
        meth(b.t, "SetTextColor", tok("accent", 0.45, 0.74, 1)); meth(b.icon, "SetDesaturated", false)
      elseif (it.primary == true) or (i == 1 and it.primary == nil and not anyPrimary) then
        local ar, ag, ab = tok("accent", 0.45, 0.74, 1)
        color(b.bg, ar, ag, ab, 0.20); border(b, ar, ag, ab, 0.45)
        meth(b.t, "SetTextColor", 1, 1, 1); meth(b.icon, "SetDesaturated", false)
      else
        color(b.bg, tok("tile", 1, 1, 1, 0.045)); border(b, 0, 0, 0, 0)
        meth(b.t, "SetTextColor", tok("text", 0.9, 0.9, 0.9)); meth(b.icon, "SetDesaturated", false)
      end
      b.primary = (not b.off and not it.active and ((it.primary == true) or (i == 1 and it.primary == nil and not anyPrimary))) or false
      b.fn, b.titleText = it[2], it[1]
      local tip3 = it[3]   -- une phrase (le geste) ou des lignes (contrat des lignes d'onglet : tooltip = { … })
      b.tipL = (type(tip3) == "table" and tip3) or (hasSub and { it.sub }) or nil
      b.hint = (type(tip3) == "string" and tip3) or (it[2] and "Clic : " .. strip(it[1])) or nil
      local x = PAD + c * (bw + 8)
      at(b, x, Ly.y)
      rec("action", x, Ly.y, bw, bh, it[1])
    end
    Ly.y = Ly.y + bh + sp().m
    return #keep
  end
  -- (5.1 LOOK) LIGNE « À FAIRE » (page Maintenant) : quoi + UN bouton qui le fait. line = { id, text, btn, fn, tip,
  -- icon, prio, ask } (T.Now.Model). Clic sur la ligne ou sur son bouton = l'action ; clic droit = plus tard / masquer.
  function Ly.todo(line)
    if type(line) ~= "table" then return end
    local b = acquire(pool("todo", function()
      local f = newButton(content)
      f.icon = meth(f, "CreateTexture", nil, "ARTWORK")
      meth(f.icon, "SetSize", 16, 16)
      meth(f.icon, "SetPoint", "LEFT", f, "LEFT", 8, 0)
      f.t = text(f, "M", "text")
      meth(f.t, "SetWordWrap", false)
      meth(f.t, "SetJustifyV", "MIDDLE")
      f.go = meth(f, "CreateTexture", nil, "ARTWORK")
      f.g = text(f, "M", "accent")
      meth(f.g, "SetJustifyH", "CENTER"); meth(f.g, "SetJustifyV", "MIDDLE")
      meth(f.g, "SetWordWrap", false)
      meth(f, "SetScript", "OnClick", function(self, btn)
        if not (self.line and meth(self, "IsVisible")) then return end
        local N = T.Now
        if N and N.Run then N.Run(self.line, btn) elseif btn ~= "RightButton" and type(self.line.fn) == "function" then pcall(self.line.fn) end
        W.Dirty() W.Render()
      end)
      meth(f, "SetScript", "OnEnter", function(self)
        local l = self.line
        if not l then return end
        local lines = {}
        for _, x in ipairs(type(l.tip) == "table" and l.tip or {}) do lines[#lines + 1] = type(x) == "table" and tostring(x[1] or "") or tostring(x) end
        W.Tooltip(self, l.text, lines, l.btn and ("Clic : " .. l.btn) or nil)
      end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end))
    b.line = line
    b.entry = { id = "mod:now:" .. tostring(line.id), text = line.text, onClick = line.fn }   -- (une action réelle : l'orage de clics du harnais la laisse)
    local h = 30
    meth(b, "SetSize", Ly.col, h)
    color(b.bg, tok("tile", 1, 1, 1, 0.045))
    meth(b.icon, "SetTexture", line.icon or (ICON .. "INV_Misc_Note_01"))
    if tostring(line.icon or "Icons\\"):find("Icons\\", 1, true) then meth(b.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92) else meth(b.icon, "SetTexCoord", 0, 1, 0, 1) end
    local gw = 0
    if line.btn then
      gw = max(64, floor(ulen(line.btn) * AVG.M + 22))
      meth(b.go, "ClearAllPoints")
      meth(b.go, "SetPoint", "RIGHT", b, "RIGHT", -4, 0)
      meth(b.go, "SetSize", gw, h - 8)
      local ar, ag, ab = tok("accent", 0.45, 0.74, 1)
      color(b.go, ar, ag, ab, 0.16)
      meth(b.go, "Show")
      meth(b.g, "ClearAllPoints")
      meth(b.g, "SetPoint", "CENTER", b.go, "CENTER", 0, 0)
      meth(b.g, "SetText", safe(line.btn))
      meth(b.g, "Show")
    else meth(b.go, "Hide") meth(b.g, "Hide") end
    meth(b.t, "ClearAllPoints")
    meth(b.t, "SetPoint", "LEFT", b, "LEFT", 32, 0)
    meth(b.t, "SetWidth", max(40, Ly.col - 32 - gw - 14))
    meth(b.t, "SetText", safe(line.text))
    meth(b.t, "SetTextColor", tok((line.prio or 1) >= 3 and "prio3" or "text", 0.9, 0.9, 0.9))
    at(b, PAD, Ly.y)
    rec("todo", PAD, Ly.y, Ly.col, h, line.text)
    Ly.y = Ly.y + h + 4
    return b
  end
  -- BASCULE façon Leatrix : case + libellé + UNE ligne d'explication + « allumé / coupé » à droite.
  -- e = { label, desc, on = bool, set = fn(bool), id, disabled, tip = { lignes } }
  function Ly.toggle(e)
    if type(e) ~= "table" then return end
    local b = acquire(pool("toggle", function()
      local f = newButton(content)
      f.box = meth(f, "CreateTexture", nil, "ARTWORK")
      meth(f.box, "SetSize", 14, 14)
      meth(f.box, "SetPoint", "TOPLEFT", f, "TOPLEFT", 8, -8)
      f.boxIn = meth(f, "CreateTexture", nil, "OVERLAY")
      meth(f.boxIn, "SetSize", 8, 8)
      meth(f.boxIn, "SetPoint", "CENTER", f.box, "CENTER", 0, 0)
      f.t = text(f, "M", "text")
      meth(f.t, "SetPoint", "TOPLEFT", f, "TOPLEFT", 30, -6)
      meth(f.t, "SetWordWrap", false)
      f.s = text(f, "S", "muted")
      meth(f.s, "SetPoint", "TOPLEFT", f, "TOPLEFT", 30, -22)
      meth(f.s, "SetWordWrap", false)
      f.r = text(f, "S", "muted")
      meth(f.r, "SetJustifyH", "RIGHT")
      meth(f.r, "SetPoint", "TOPRIGHT", f, "TOPRIGHT", -8, -8)
      meth(f, "SetScript", "OnClick", function(self)
        local en = self.entry
        if not en or en.disabled or type(en.set) ~= "function" or not meth(self, "IsShown") then return end
        if inCombat() and not en.combatOK then W.Flash("Impossible en combat : réessaie après.") return end
        T:UserAction(function() en.set(not self.on) end)
        W.Dirty() W.Render()
      end)
      meth(f, "SetScript", "OnEnter", function(self)
        local en = self.entry
        if not en then return end
        local lines = {}
        if en.desc then lines[#lines + 1] = en.desc end
        for _, l in ipairs(type(en.tip) == "table" and en.tip or {}) do lines[#lines + 1] = l end
        W.Tooltip(self, en.label, lines, en.disabled and (en.why or "Indisponible pour l'instant") or (self.on and "Clic : couper" or "Clic : allumer"))
      end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end))
    local on = e.on and true or false
    b.entry, b.on = e, on
    -- (même contrat que les lignes Ly.row : text / right / onClick — ce que lisent la recherche, la visite et les tests)
    e.text = e.text or e.label
    e.right = e.state or (on and "allumé" or "coupé")
    if type(e.set) == "function" then e.onClick = function() e.set(not on) end end
    -- (« mod: » : une bascule — l'orage de clics du harnais la laisse tranquille, comme les lignes « Fonctions »)
    e.id = tostring(e.id or ("toggle:" .. tostring(e.label)))
    if not e.id:find("^mod:") then e.id = "mod:" .. e.id end
    local hasDesc = type(e.desc) == "string" and e.desc ~= ""
    local h = hasDesc and 38 or 28
    meth(b, "SetSize", Ly.col, h)
    color(b.bg, 0, 0, 0, 0)   -- (5.1) une bascule = une ligne, pas une boîte
    color(b.box, tok(on and "on" or "off", 0.5, 0.5, 0.5, 1))
    if on then color(b.boxIn, 0.04, 0.05, 0.07, 0) else color(b.boxIn, 0.05, 0.06, 0.08, 1) end
    meth(b.t, "SetWidth", max(40, Ly.col - 30 - 70)); meth(b.s, "SetWidth", max(40, Ly.col - 38))
    meth(b.t, "SetText", safe(e.label or ""))
    meth(b.t, "SetTextColor", tok(e.disabled and "disabled" or "text", 0.92, 0.92, 0.92))
    meth(b.s, "SetText", hasDesc and safe(e.desc) or "")
    meth(b.r, "SetText", e.state or (on and "allumé" or "coupé"))
    meth(b.r, "SetTextColor", tok(on and "on" or "off", 0.5, 0.5, 0.5))
    at(b, PAD, Ly.y)
    rec("toggle", PAD, Ly.y, Ly.col, h, e.label)
    Ly.y = Ly.y + h + 2
    return b
  end
  -- CHOIX (2 ou 3 grandes cases, une phrase d'explication chacune) : list = { { titre, phrase, fn, active = } }
  function Ly.choice(list)
    local n = type(list) == "table" and #list or 0
    if n == 0 then return end
    local cols = min(n, 3)
    local bw = floor((Ly.col - (cols - 1) * 8) / cols)
    local p = pool("choice", function()
      local f = newButton(content)
      f.dot = meth(f, "CreateTexture", nil, "ARTWORK")
      meth(f.dot, "SetSize", 10, 10)
      meth(f.dot, "SetPoint", "TOPLEFT", f, "TOPLEFT", 10, -11)
      f.t = text(f, "L", "text")
      meth(f.t, "SetPoint", "TOPLEFT", f, "TOPLEFT", 28, -7)
      meth(f.t, "SetWordWrap", false)
      f.s = text(f, "S", "text")
      meth(f.s, "SetPoint", "TOPLEFT", f, "TOPLEFT", 10, -30)
      meth(f.s, "SetWordWrap", true)
      meth(f, "SetScript", "OnClick", function(self) if self.fn and meth(self, "IsShown") then T:UserAction(self.fn) W.Dirty() W.Render() end end)
      meth(f, "SetScript", "OnEnter", function(self) W.Tooltip(self, self.titleText, self.subL, self.on and "Choisi" or "Clic : choisir") end)
      meth(f, "SetScript", "OnLeave", hideTip)
      return f
    end)
    local hs, used = 0, {}
    for i = 1, cols do
      local it = list[i]
      local b = acquire(p)
      used[i] = b
      meth(b.s, "SetWidth", bw - 20)
      meth(b.s, "SetText", safe(it[2] or ""))
      hs = max(hs, measure(b.s, safe(it[2] or ""), bw - 20, "S"))
    end
    local bh = 30 + hs + 10
    for i = 1, cols do
      local it, b = list[i], used[i]
      local on = it.active and true or false
      meth(b, "SetSize", bw, bh)
      meth(b.t, "SetWidth", bw - 36)
      meth(b.t, "SetText", safe(it[1]))
      b.fn, b.titleText, b.subL, b.on = it[3], it[1], it[2] and { it[2] } or nil, on
      b.entry = { id = "mod:choice:" .. tostring(it[1]) }   -- (un choix de profil : l'orage de clics du harnais le laisse)
      if on then
        color(b.bg, tok("selected", 0.45, 0.74, 1, 0.18)); border(b, 0, 0, 0, 0); color(b.dot, tok("on", 0.45, 0.74, 1, 1))
      else
        color(b.bg, tok("tile", 1, 1, 1, 0.045)); border(b, 0, 0, 0, 0); color(b.dot, tok("off", 0.58, 0.58, 0.6, 1))
      end
      local x = PAD + (i - 1) * (bw + 8)
      at(b, x, Ly.y)
      rec("choice", x, Ly.y, bw, bh, it[1])
    end
    Ly.y = Ly.y + bh + sp().m
  end
  -- CARTE (liseré 1 px, titre or, lignes de texte) : Ly.card(titre, { lignes }[, { tokens par ligne }])
  function Ly.card(title, lines, tokens)
    lines = type(lines) == "table" and lines or { tostring(lines or "") }
    local f = acquire(pool("card", function()
      local c = G.CreateFrame("Frame", nil, content)
      c.bg = meth(c, "CreateTexture", nil, "BACKGROUND")
      meth(c.bg, "SetAllPoints", c)
      c.t = text(c, "M", "gold")
      meth(c.t, "SetPoint", "TOPLEFT", c, "TOPLEFT", 10, -8)
      meth(c.t, "SetWordWrap", false)
      c.lines = {}
      return c
    end))
    color(f.bg, tok("tile", 1, 1, 1, 0.045))
    border(f, 0, 0, 0, 0)
    local w = Ly.col
    local y = 8
    if title and title ~= "" then
      meth(f.t, "SetWidth", w - 20); meth(f.t, "SetText", safe(title)); meth(f.t, "Show")
      y = y + 18
    else meth(f.t, "Hide") end
    for i, l in ipairs(lines) do
      local fs = f.lines[i]
      if not fs then fs = text(f, "S", "text") meth(fs, "SetWordWrap", true) f.lines[i] = fs end
      local str = safe(l)
      meth(fs, "ClearAllPoints")
      meth(fs, "SetPoint", "TOPLEFT", f, "TOPLEFT", 10, -y)
      meth(fs, "SetWidth", w - 20)
      meth(fs, "SetText", str)
      meth(fs, "SetTextColor", tok(type(tokens) == "table" and tokens[i] or "text", 0.9, 0.9, 0.9))
      meth(fs, "Show")
      y = y + measure(fs, str, w - 20, "S") + 3
    end
    for i = #lines + 1, #f.lines do meth(f.lines[i], "Hide") end
    local h = y + 6
    meth(f, "SetSize", w, h)
    at(f, PAD, Ly.y)
    rec("card", PAD, Ly.y, w, h, title or lines[1])
    Ly.y = Ly.y + h + sp().m
    return f
  end
  -- NOTE (une barre de couleur à gauche + une phrase) : kind = "info" | "warn" | "good"
  function Ly.note(str, kind)
    local f = acquire(pool("note", function()
      local c = G.CreateFrame("Frame", nil, content)
      c.bg = meth(c, "CreateTexture", nil, "BACKGROUND")
      meth(c.bg, "SetAllPoints", c)
      c.bar = meth(c, "CreateTexture", nil, "ARTWORK")
      meth(c.bar, "SetWidth", 3)
      meth(c.bar, "SetPoint", "TOPLEFT", c, "TOPLEFT", 0, 0)
      meth(c.bar, "SetPoint", "BOTTOMLEFT", c, "BOTTOMLEFT", 0, 0)
      c.t = text(c, "S", "text")
      meth(c.t, "SetPoint", "TOPLEFT", c, "TOPLEFT", 10, -6)
      meth(c.t, "SetWordWrap", true)
      return c
    end))
    str = safe(str)
    color(f.bg, tok("button", 0.14, 0.15, 0.19, 0.45))
    color(f.bar, tok(kind == "warn" and "warn" or kind == "good" and "on" or "key", 0.55, 0.8, 1, 1))
    meth(f.t, "SetWidth", Ly.col - 18)
    meth(f.t, "SetText", str)
    local h = measure(f.t, str, Ly.col - 18, "S") + 12
    meth(f, "SetSize", Ly.col, h)
    at(f, PAD, Ly.y)
    rec("note", PAD, Ly.y, Ly.col, h, str)
    Ly.y = Ly.y + h + sp().s
    return f
  end
  -- DEUX COLONNES « nom … valeur » (une ligne, sans clic)
  function Ly.kv(label, value, tokName)
    return Ly.row({ text = tostring(label or ""), right = tostring(value or ""), kind = "info", prio = tokName == "muted" and 1 or 2 })
  end
  -- LÉGENDE (carré de couleur + mot + explication) : items = Theme.LEGEND.bags
  function Ly.legend(items)
    local th = Th()
    for _, it in ipairs(type(items) == "table" and items or {}) do
      local f = acquire(pool("legend", function()
        local c = G.CreateFrame("Frame", nil, content)
        c.sw = meth(c, "CreateTexture", nil, "ARTWORK")
        meth(c.sw, "SetSize", 10, 10)
        meth(c.sw, "SetPoint", "LEFT", c, "LEFT", 8, 0)
        c.t = text(c, "S", "text")
        meth(c.t, "SetPoint", "LEFT", c, "LEFT", 26, 0)
        meth(c.t, "SetWordWrap", false)
        return c
      end))
      if th then color(f.sw, th:Color(it.token or "text")) else color(f.sw, 1, 1, 1, 1) end
      local str = safe(tostring(it.label or "") .. (it.tip and ("  —  " .. it.tip) or ""))
      meth(f.t, "SetWidth", Ly.col - 34)
      meth(f.t, "SetText", str)
      meth(f, "SetSize", Ly.col, 18)
      at(f, PAD, Ly.y)
      rec("legend", PAD, Ly.y, Ly.col, 18, it.label)
      Ly.y = Ly.y + 18
    end
    Ly.y = Ly.y + sp().s
  end
  function Ly.finish() meth(content, "SetHeight", Ly.y + 16); meth(content, "SetWidth", Ly.W) end
  return Ly
end
W._newLay = newLay

--- (4.1 LAYOUT) en haut d'une sous-page : les onglets de sa page mère (Personnage, Réglages) ; sous-page des Quêtes
--- (Journal, Pilote auto) : un lien « ← Quêtes »
function W.SubBar(Ly, id)
  local p = W.pageIndex[id]
  local par = p and p.parent
  -- (5.1) page de la navigation avec des pages sœurs (Quêtes | Trajet, Groupe | Donjons) : leurs onglets sont dans
  -- l'en-tête de la fenêtre (W.HeadTabs), à droite du titre — rien de plus dans la page
  if W.TabFamily(id) then return end
  if not par then return end
  local subs = W.SUBS[par]
  if W.SIDE[par] then
    -- (5.0) Réglages : la sous-liste est à gauche (W.SideList) ; « Tous les réglages » a sa propre liste → un retour
    local pp = W.pageIndex[par]
    Ly.buttons({ { "← " .. tostring(pp and pp.label or par), function() W.Select("display") end,
      "Clic : revenir aux réglages simples (Affichage, Profil, Touches…)" } }, 3)
  elseif subs then
    local list = {}
    local inList = false
    for _, c in ipairs(subs) do
      local cp = W.pageIndex[c]
      if cp then
        list[#list + 1] = { c, cp.short or cp.label, W.Badge(c), cp.desc }
        if c == id then inList = true end
      end
    end
    if not inList then list[#list + 1] = { id, p.short or p.label, nil, p.desc } end
    Ly.tabs(list, id)
  else
    local pp = W.pageIndex[par]
    Ly.buttons({ { "← " .. tostring(pp and pp.label or par), function() W.Select(par) end, "Clic : revenir à la page « " ..
      tostring(pp and pp.label or par) .. " »" } }, 3)
  end
end
--- (5.1) la famille d'onglets de `id` (W.TABS) : { {id, libellé, pastille, description} } s'il y a au moins 2 pages
function W.TabFamily(id)
  local p = W.pageIndex[id]
  local par = p and p.parent
  local fam = W.TABS[id] or (par and W.TABS[par])
  if not fam then return nil end
  local inFam, list = false, {}
  for _, c in ipairs(fam) do
    if c == id then inFam = true end
    local cp = W.pageIndex[c]
    if cp and (c == id or not cp.guard or W.PageOn({ guard = cp.guard })) then list[#list + 1] = { c, cp.short or cp.label, W.Badge(c), cp.desc } end
  end
  if not inFam or #list < 2 then return nil end
  return list
end
--- (5.1) les onglets d'en-tête (à droite du titre de la page) : des mots, celui qui est choisi en accent, un trait dessous
local headTabs = {}
W.headTabList = {}
function W.HeadTabs(id)
  local list = (win and W.TabFamily(id)) or {}
  W.headTabList = {}
  if not win then return 0 end
  local tw = tonumber(meth(win.pageTitle, "GetStringWidth")) or 0
  if tw <= 0 then tw = ulen(meth(win.pageTitle, "GetText") or "") * AVG.L end
  local x = floor(tw + 20)
  for i, it in ipairs(list) do
    local b = headTabs[i]
    if not b then
      b = newButton(win.top)
      meth(b, "SetHeight", 20)
      b.t = text(b, "M", "muted")
      meth(b.t, "SetPoint", "CENTER", b, "CENTER", 0, 0)
      meth(b.t, "SetJustifyH", "CENTER") meth(b.t, "SetJustifyV", "MIDDLE") meth(b.t, "SetWordWrap", false)
      b.line = meth(b, "CreateTexture", nil, "ARTWORK")
      meth(b.line, "SetHeight", 1)
      meth(b.line, "SetPoint", "BOTTOMLEFT", b, "BOTTOMLEFT", 2, 0)
      meth(b.line, "SetPoint", "BOTTOMRIGHT", b, "BOTTOMRIGHT", -2, 0)
      color(b.line, tok("accent", 0.45, 0.74, 1, 1))
      meth(b, "SetScript", "OnClick", function(self) if self.page and meth(self, "IsShown") then W.Select(self.page) end end)
      meth(b, "SetScript", "OnEnter", function(self) W.Tooltip(self, self.labelText, self.desc and { self.desc } or nil, "Clic : ouvrir") end)
      meth(b, "SetScript", "OnLeave", hideTip)
      headTabs[i] = b
    end
    local on = it[1] == id
    local label = it[2] .. (it[3] and (" (" .. tostring(it[3]) .. ")") or "")
    local bw = floor(ulen(label) * AVG.M + 16)
    meth(b, "SetWidth", bw)
    meth(b.t, "SetText", safe(label))
    meth(b.t, "SetTextColor", tok(on and "accent" or "muted", 0.6, 0.6, 0.63))
    color(b.bg, 0, 0, 0, 0)
    if on then meth(b.line, "Show") else meth(b.line, "Hide") end
    b.page, b.labelText, b.desc = it[1], it[2], it[4]
    meth(b, "ClearAllPoints")
    meth(b, "SetPoint", "TOPLEFT", win.pageTitle, "TOPLEFT", x, 2)
    meth(b, "Show")
    x = x + bw + 4
    W.headTabList[i] = { id = it[1], label = label, on = on }
  end
  for i = #list + 1, #headTabs do meth(headTabs[i], "Hide") headTabs[i].page = nil end
  return #list
end
--- (4.1 LAYOUT) en tête de page : ses 1 à 3 actions les plus utiles, en gros boutons (p.top() → { {label, fn, tip} })
function W.TopActions(Ly, p)
  if type(p.top) ~= "function" or W.Query() ~= "" then return 0 end
  local ok, list = pcall(p.top)
  if not (ok and type(list) == "table") then return 0 end
  local keep = {}
  for _, it in ipairs(list) do if type(it) == "table" and it[1] and it[2] and #keep < 3 then keep[#keep + 1] = it end end
  if #keep > 0 then Ly.actions(keep, #keep) end   -- (5.0) ≤ 3 grosses tuiles d'action en tête de page
  Ly.topN = #keep
  Ly.topList = keep
  return #keep
end
--- (5.1 LOOK) le bouton du pied redit-il une action déjà en tête (même mot, ou le mot de la tête + une précision) ?
function W.DupOfTop(label, top)
  if type(label) ~= "string" or type(top) ~= "table" then return false end
  local b = low(label)
  for _, it in ipairs(top) do
    local a = type(it[1]) == "string" and low(it[1]) or nil
    if a and a ~= "" and (a == b or (b:sub(1, #a) == a and b:sub(#a + 1, #a + 1) == " ")) then return true end
  end
  return false
end
--- (5.0 LOOK) la recherche trouve N'IMPORTE QUELLE action et N'IMPORTE QUEL réglage : pages, actions rapides de la
--- bande, éléments d'écran (case allumer / couper), profil, chaque réglage de chaque fonction, outils de la pastille.
--- W.SearchHits(cur) → { { text, icon, kind, onClick, clickHint, _k } } triés (actions d'abord) ; cur = page en cours
--- (ses propres lignes sont déjà filtrées au-dessus).
function W.SearchHits(cur)
  local hits = {}
  if W.Query() == "" then return hits end
  local Sc = T.Shortcuts
  if type(Sc) == "table" and type(Sc.DEFS) == "table" and type(Sc.Do) == "function" then
    for _, d in ipairs(Sc.DEFS) do
      if W.Match(d.label, d.title, d.tip, "action rapide") then
        local id = d.id
        hits[#hits + 1] = { text = "Action : " .. tostring(d.title or d.label), icon = d.icon, kind = "info",
          clickHint = "Clic : le faire tout de suite", tooltip = d.tip, onClick = function() Sc.Do(id) end, _k = "0" .. tostring(d.label) }
      end
    end
    if type(Sc.ToggleGuide) == "function" and W.Match("guidage des quêtes suivi de quêtes allumer couper désactiver", "guide") then
      hits[#hits + 1] = { text = "Action : guidage des quêtes — " .. (Sc.GuideOn() and "le couper" or "le rallumer"),
        icon = ICON .. "Ability_Tracking", kind = "info", clickHint = "Clic : allumer / couper tout le guidage de quêtes",
        onClick = function() Sc.ToggleGuide() end, _k = "0 guidage" }
    end
  end
  for id, pg in pairs(W.pageIndex) do
    if id ~= cur and id ~= "tab" and not (pg.hidden and not pg.parent) and W.Match(pg.label, pg.title, pg.desc, pg.side) then
      local par = pg.parent and W.pageIndex[pg.parent]
      hits[#hits + 1] = { text = "Page : " .. (par and (par.label .. " → ") or "") .. tostring(pg.label), icon = pg.icon, kind = "info",
        clickHint = "Clic : ouvrir", onClick = function() W.Select(id) end, _k = "1" .. tostring(pg.label) }
    end
  end
  if cur ~= "display" then
    for _, d in ipairs(W.DISPLAY or {}) do
      if W.Match(d.label, d.preview, "affichage écran") then
        local okOn, on = pcall(d.on)
        on = okOn and on and true or false
        hits[#hits + 1] = { text = "Affichage : " .. d.label .. (on and "  (à l'écran)" or "  (coupé)"), kind = "info",
          icon = on and "Interface\\RaidFrame\\ReadyCheck-Ready" or "Interface\\RaidFrame\\ReadyCheck-NotReady",
          tooltip = { d.preview }, clickHint = on and "Clic : le couper" or "Clic : l'afficher", id = "mod:search:" .. d.id,
          onClick = function() if inCombat() and not d.combatOK then W.Flash("Impossible en combat : réessaie après.") return end pcall(d.set, not on) end,
          _k = "2" .. d.label }
      end
    end
  end
  if cur ~= "profile" then
    for _, pr in ipairs(W.PROFILES or {}) do
      if W.Match(pr.label, pr.text, "profil") then
        local id = pr.id
        hits[#hits + 1] = { text = "Profil : " .. pr.label .. (W.Profile() == id and "  (choisi)" or ""), kind = "info",
          icon = ICON .. "INV_Misc_EngGizmos_20", tooltip = { pr.text }, clickHint = "Clic : la page Profil",
          onClick = function() W.Select("profile") end, _k = "2 profil " .. pr.label }
      end
    end
  end
  -- chaque réglage de chaque fonction (libellé de l'option) → la page de réglages de sa fonction
  if cur ~= "options" then
    local n = 0
    for _, e in ipairs(T.OptionEntries and T:OptionEntries() or {}) do
      local mod = e.module and T.modules and T.modules[e.module]
      if mod and type(mod.options) == "table" then
        for _, o in ipairs(mod.options) do
          if n < 20 and type(o.label) == "string" and W.Match(o.label, o.section) then
            n = n + 1
            local key = e.key
            hits[#hits + 1] = { text = "Réglage : " .. tostring(e.label) .. " → " .. o.label, kind = "info", icon = ICON .. "INV_Misc_Gear_01",
              clickHint = "Clic : ouvrir ce réglage", onClick = function() if W.char then W.char.optPage = key end W.Select("options") end,
              _k = "3" .. tostring(e.label) .. o.label }
          end
        end
      end
    end
  end
  local H = T.Hub
  if H and H.Entries then
    local m = H.mode
    H.mode = "main"
    local ok, list = pcall(H.Entries)
    H.mode = m
    for _, e in ipairs(ok and type(list) == "table" and list or {}) do
      if e.fn and e.title and not tostring(e.id or ""):find("^tab:") and e.group ~= "rapide" and W.Match(e.title, e.desc, e.tip) then
        hits[#hits + 1] = { text = "Outil : " .. tostring(e.title), icon = e.icon, kind = "info", clickHint = e.tip or "Clic : ouvrir",
          onClick = function() e.fn() end, _k = "4" .. tostring(e.title) }
      end
    end
  end
  -- le mot cherché en début de mot du nom passe devant (« rendre » : « Rendre mes quêtes » avant « Quêtes à prendre »)
  local q = W.Fold(W.Query()):match("%S+") or ""
  for _, e in ipairs(hits) do
    local name = " " .. W.Fold((tostring(e.text):gsub("^[^:]+: ", "")))
    e._r = (q ~= "" and name:find(" " .. q, 1, true)) and 0 or 1
  end
  tsort(hits, function(a, b)
    local ca, cb = a._k:sub(1, 1), b._k:sub(1, 1)
    if ca ~= cb then return ca < cb end
    if a._r ~= b._r then return a._r < b._r end
    return a._k < b._k
  end)
  return hits
end
function W.SearchElsewhere(Ly, cur)
  local hits = W.SearchHits(cur)
  if #hits == 0 then return end
  if Ly.header("Partout dans TreeAI", nil, #hits, ICON .. "INV_Misc_Spyglass_02") then
    for i, e in ipairs(hits) do if i <= 14 then Ly.row(e) end end
  end
end
--- (4.1 LAYOUT) les avis non demandés d'un module (Core : ownPage) : seulement sur SA page, en tête
function W.OwnNotices(Ly, id)
  local list = {}
  for _, e in ipairs(T.GetSuggestions and T:GetSuggestions() or {}) do
    if e.ownPage == id and W.Match(e.text) then list[#list + 1] = e end
  end
  for _, e in ipairs(list) do Ly.row(e) end
  if #list > 0 then Ly.gap(sp().s) end
  return #list
end

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
  ["ï"] = "i", ["ô"] = "o", ["ö"] = "o", ["ù"] = "u", ["û"] = "u", ["ü"] = "u", ["ç"] = "c", ["œ"] = "oe",
  -- (5.0) majuscules accentuées (« Équipement » : string.lower ne les connaît pas)
  ["É"] = "e", ["È"] = "e", ["Ê"] = "e", ["À"] = "a", ["Â"] = "a", ["Ç"] = "c", ["Î"] = "i", ["Ô"] = "o", ["Û"] = "u", ["Œ"] = "oe" }
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
      ctrls[#ctrls + 1] = { e.label or strip(e.text), function() W.Run(e) end, e.tooltip, icon = e.icon, sub = e.sub }
    end
  end
  if #ctrls > 0 then
    -- (5.0 LOOK) une page sans tuiles d'action à elle : ses 3 premiers boutons deviennent les grosses tuiles du haut
    -- (une icône quand la ligne en donne une) ; le reste en boutons, comme avant
    if (Ly.topN or 0) == 0 and query == "" then
      local big, rest = {}, {}
      for i, c in ipairs(ctrls) do if i <= 3 then big[#big + 1] = c else rest[#rest + 1] = c end end
      Ly.topN = Ly.actions(big, #big)
      if #rest > 0 then Ly.buttons(rest, min(3, #rest)); Ly.gap(sp().s) end
    else
      Ly.buttons(ctrls, min(3, #ctrls)); Ly.gap(sp().s)
    end
  end
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
  if shown == 0 then   -- (4.0 QA) même avec des boutons au-dessus : une phrase qui dit ce qu'il en est
    Ly.empty(query ~= "" and ("Rien ne correspond à « " .. query .. " ».") or emptyText or "Rien pour l'instant.", emptyAction)
  end
  return shown
end

-- -------------------------------------------------- construction
local navButtons = {}
local navSig
local SIDE_W = 132        -- sous-liste des Réglages (façon Leatrix Plus)
W.SIDE_W = SIDE_W
local function selectedPage()
  local id = W.Resolve(W.char and W.char.page or "home")
  return W.pageIndex[id] and id or (W.pageIndex.home and "home") or (W.pageIndex.quests and "quests") or (W.PAGES[1] and W.PAGES[1].id)
end
--- (4.1 LAYOUT) les pages de la navigation, dans l'ordre de W.NAV (puis toute autre page visible ajoutée par un
--- module) ; W.PAGES est remis dans cet ordre (la pastille et les tests le lisent)
function W.NavPages()
  local out, seen = {}, {}
  for _, id in ipairs(W.NAV) do
    local p = W.pageIndex[id]
    if p and not p.hidden then out[#out + 1] = p seen[p] = true end
  end
  for _, p in ipairs(W.PAGES) do if not p.hidden and not seen[p] then out[#out + 1] = p seen[p] = true end end
  local rest = {}
  for _, p in ipairs(W.PAGES) do if not seen[p] then rest[#rest + 1] = p end end
  for i = #W.PAGES, 1, -1 do W.PAGES[i] = nil end
  for _, p in ipairs(out) do W.PAGES[#W.PAGES + 1] = p end
  for _, p in ipairs(rest) do W.PAGES[#W.PAGES + 1] = p end
  return out
end
--- (5.0) une page est-elle montrable ? (inscrite par son module, pas cachée, garde éventuelle p.guard() vraie)
function W.PageOn(p)
  if type(p) ~= "table" or p.hidden then return false end
  if type(p.guard) == "function" then local ok, v = pcall(p.guard) if not (ok and v) then return false end end
  return true
end
--- (5.0) boutons de la navigation : icône + nom + pastille ; refaits seulement quand la liste des pages change
function W.BuildNav()
  if not (win and win.nav) then return end
  local pages = W.NavPages()
  local ids = {}
  for _, p in ipairs(pages) do ids[#ids + 1] = p.id end
  local sig = tconcat(ids, ",")
  if sig == navSig then return end
  navSig = sig
  local nav = win.nav
  local y = 72
  for i, p in ipairs(pages) do
    local b = navButtons[i]
    if not b then
      b = newButton(nav)
      meth(b, "SetSize", NAV_W - 16, 24)
      -- liseré gauche or quand la page est choisie (comme les listes de Leatrix / ElvUI)
      b.mark = meth(b, "CreateTexture", nil, "ARTWORK")
      meth(b.mark, "SetWidth", 3)
      meth(b.mark, "SetPoint", "TOPLEFT", b, "TOPLEFT", 0, 0)
      meth(b.mark, "SetPoint", "BOTTOMLEFT", b, "BOTTOMLEFT", 0, 0)
      color(b.mark, tok("accent", 0.45, 0.74, 1, 1))
      b.icon = meth(b, "CreateTexture", nil, "ARTWORK")
      meth(b.icon, "SetSize", 16, 16)
      meth(b.icon, "SetPoint", "LEFT", b, "LEFT", 9, 0)
      meth(b.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
      b.t = text(b, "M", "text")
      meth(b.t, "SetPoint", "LEFT", b, "LEFT", 32, 0)
      meth(b.t, "SetJustifyV", "MIDDLE")
      meth(b.t, "SetWordWrap", false)
      b.badge = text(b, "S", "accent")
      meth(b.badge, "SetPoint", "RIGHT", b, "RIGHT", -6, 0)
      meth(b, "SetScript", "OnClick", function(self) if self.page then W.Select(self.page) end end)
      meth(b, "SetScript", "OnEnter", function(self)
        local pg = W.pageIndex[self.page or ""]
        if pg then W.Tooltip(self, pg.label, pg.desc and { pg.desc } or nil, "Clic : ouvrir la page") end
      end)
      meth(b, "SetScript", "OnLeave", hideTip)
      navButtons[i] = b
    end
    meth(b, "ClearAllPoints")
    meth(b, "SetPoint", "TOPLEFT", nav, "TOPLEFT", 8, -y)
    meth(b.icon, "SetTexture", p.icon)
    meth(b.t, "SetText", p.label)
    meth(b.t, "SetWidth", NAV_W - 16 - 32 - 22)
    b.page = p.id
    meth(b, "Show")
    y = y + 26
    if p.gapAfter then y = y + 10 end
  end
  for i = #pages + 1, #navButtons do meth(navButtons[i], "Hide") navButtons[i].page = nil end
  W.navCount = #pages
end
--- la page mère à sous-liste (Réglages) de `id`, ou nil — « Tous les réglages » a déjà sa liste : pas de 2e colonne
function W.SideOf(id)
  local p = W.pageIndex[id]
  local par = p and p.parent
  if par and W.SIDE[par] and id ~= "options" and id ~= "tab" and id ~= "navlog" then return par end
  return nil
end
--- (5.0) sous-liste à gauche façon Leatrix Plus : une ligne par sous-page, la page choisie surlignée
function W.SideList(Ly, par, cur)
  local p = pool("side", function()
    local f = newButton(content)
    meth(f, "SetHeight", 24)
    f.mark = meth(f, "CreateTexture", nil, "ARTWORK")
    meth(f.mark, "SetWidth", 2)
    meth(f.mark, "SetPoint", "TOPLEFT", f, "TOPLEFT", 0, 0)
    meth(f.mark, "SetPoint", "BOTTOMLEFT", f, "BOTTOMLEFT", 0, 0)
    color(f.mark, tok("accent", 0.45, 0.74, 1, 1))
    f.t = text(f, "S", "text")
    meth(f.t, "SetPoint", "LEFT", f, "LEFT", 8, 0)
    meth(f.t, "SetWordWrap", false)
    f.n = text(f, "S", "accent")
    meth(f.n, "SetPoint", "RIGHT", f, "RIGHT", -4, 0)
    meth(f, "SetScript", "OnClick", function(self) if self.page and meth(self, "IsShown") then W.Select(self.page) end end)
    meth(f, "SetScript", "OnEnter", function(self) W.Tooltip(self, self.labelText, self.desc and { self.desc } or nil, "Clic : ouvrir") end)
    meth(f, "SetScript", "OnLeave", hideTip)
    return f
  end)
  local y = 8
  for _, c in ipairs(W.SUBS[par] or {}) do
    local cp = W.pageIndex[c]
    if cp then
      local b = acquire(p)
      local on = c == cur
      local label = cp.side or cp.short or cp.label
      meth(b, "SetWidth", SIDE_W)
      meth(b.t, "SetWidth", SIDE_W - 28)
      meth(b.t, "SetText", safe(label))
      meth(b.t, "SetTextColor", tok(on and "accent" or "text", 0.92, 0.92, 0.92))
      if on then color(b.bg, tok("selected", 0.45, 0.74, 1, 0.18)) meth(b.mark, "Show") else color(b.bg, 0, 0, 0, 0) meth(b.mark, "Hide") end
      local n = W.Badge(c)
      meth(b.n, "SetText", n and tostring(n) or "")
      b.page, b.labelText, b.desc = c, cp.label, cp.desc
      Ly.at(b, PAD, y)
      Ly.rec("side", PAD, y, SIDE_W, 24, label)
      y = y + 26
    end
  end
  return y
end
local function contentWidth()
  local sw = tonumber(meth(scroll, "GetWidth"))
  if sw and sw > 100 then return max(360, floor(sw)) end
  local ww = tonumber(meth(win, "GetWidth")) or 820
  return max(360, floor(ww - NAV_W - 4 - 3 - 26))     -- bords + colonne + barre de défilement
end

--- (4.1 LAYOUT) première ouverture : la moitié droite de l'écran (jamais au milieu, sur le personnage), largeur
--- ajustée pour tenir dans cette moitié ; ensuite la fenêtre revient où le joueur l'a laissée (WindowState « main »)
W.FIRST = { minW = 640, maxW = 780, h = 480, right = 12, y = 10 }
function W.FirstRect()
  local sw = tonumber(meth(G.UIParent, "GetWidth")) or 0
  local sh = tonumber(meth(G.UIParent, "GetHeight")) or 0
  if sw < 200 then sw = tonumber(try(G.GetScreenWidth)) or 0 end
  if sh < 200 then sh = tonumber(try(G.GetScreenHeight)) or 0 end
  local F = W.FIRST
  local w = F.maxW
  if sw > 200 then w = max(F.minW, min(F.maxW, floor(sw / 2 - F.right))) end
  local h = F.h
  if sh > 200 then h = max(420, min(F.h, floor(sh - 160))) end
  return w, h, sw, sh
end
--- (5.1) le suivi de quêtes du jeu (ou de Questie) à droite de l'écran : la fenêtre s'ouvre à SA gauche, jamais dessus
--- (« garde le suivi de quêtes normal du jeu »), en restant dans la moitié droite (jamais sur le personnage) : elle
--- rétrécit s'il le faut. → décalage depuis le bord droit + largeur, ou nil (rien à éviter / pas la place)
function W.TrackerInset(sw)
  local Sc = T.Shortcuts
  if not (type(Sc) == "table" and type(Sc.RectOf) == "function" and sw and sw > 200) then return nil end
  local left
  for _, n in ipairs({ "ObjectiveTrackerFrame", "QuestWatchFrame", "WatchFrame", "Questie_BaseFrame", "QuestieTrackerBaseFrame" }) do
    local r = try(Sc.RectOf, n)
    if type(r) == "table" and r.l > sw * 0.6 and r.w > 40 and r.h > 40 then left = min(left or r.l, r.l) end
  end
  if not left then return nil end
  local inset = floor(sw - left + 8)
  local room = floor(sw / 2 - inset)
  if room < W.FIRST.minW then return nil end            -- pas la place entre le personnage et le suivi : la place d'origine
  return inset, min(W.FIRST.maxW, room)
end
function W.PlaceFirstTime(f)
  local w, h, sw = W.FirstRect()
  local inset, w2 = W.TrackerInset(sw)
  meth(f, "SetSize", w2 or w, h)
  anchorTo(f, "RIGHT", -(inset or W.FIRST.right), W.FIRST.y)
end
--- (5.0) « /tai interface reset » : la fenêtre oublie sa place et sa taille (elle rouvrira à droite, taille d'origine)
function W.ResetPlace()
  local th = Th()
  local st = th and th:WindowState("main")
  if st then st.point, st.x, st.y, st.w, st.h = nil, nil, nil, nil, nil end
  if win then
    W.PlaceFirstTime(win)
    if meth(win, "IsShown") then W.Render() end
  end
  return true
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
  end
  -- (4.0 QA) thème refusé (Options > Thème décoché) : notre fond sombre quand même
  if not (th and th.IsSkinned and th:IsSkinned(win)) then
    win.bgt = meth(win, "CreateTexture", nil, "BACKGROUND"); meth(win.bgt, "SetAllPoints", win); color(win.bgt, 0.05, 0.06, 0.08, 0.95)
  end
  -- (PLACEBUG) place gardée seulement si elle est complète (jamais « nil → 0 → coin »)
  if not (st and okAnchor(st.point, st.x, st.y) and anchorTo(win, st.point, st.x, st.y)) then
    W.PlaceFirstTime(win)
  end
  -- colonne de gauche (fond un peu plus sombre) : titre + version + navigation
  local nav = G.CreateFrame("Frame", nil, win)
  meth(nav, "SetPoint", "TOPLEFT", win, "TOPLEFT", 3, -3)
  meth(nav, "SetPoint", "BOTTOMLEFT", win, "BOTTOMLEFT", 3, 3)
  meth(nav, "SetWidth", NAV_W)
  nav.bg = meth(nav, "CreateTexture", nil, "BACKGROUND")
  meth(nav.bg, "SetAllPoints", nav)
  color(nav.bg, 0, 0, 0, 0.22)
  win.nav = nav
  local drag = function(f)
    meth(f, "EnableMouse", true)
    meth(f, "RegisterForDrag", "LeftButton")
    meth(f, "SetScript", "OnDragStart", function() meth(win, "StartMoving") end)
    meth(f, "SetScript", "OnDragStop", function()
      meth(win, "StopMovingOrSizing")
      -- (PLACEBUG) ancrage explicite sur UIParent depuis le rectangle réel (pas l'ancrage que le client vient d'y mettre)
      if T.AnchorFromRect then T:AnchorFromRect(win) end
      local pt, _, _, x, y = meth(win, "GetPoint")
      local s2 = th and th:WindowState("main")
      if s2 and okAnchor(pt, x, y) then s2.point, s2.x, s2.y = pt, floor(x + 0.5), floor(y + 0.5) end
    end)
  end
  drag(nav)
  win.logo = meth(nav, "CreateTexture", nil, "ARTWORK")
  meth(win.logo, "SetSize", 24, 24)
  meth(win.logo, "SetPoint", "TOPLEFT", nav, "TOPLEFT", PAD - 4, -16)
  meth(win.logo, "SetTexture", ICON .. "Ability_Hunter_BeastCall")
  meth(win.logo, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
  win.title = text(nav, "H", "gold")
  meth(win.title, "SetPoint", "TOPLEFT", nav, "TOPLEFT", PAD + 28, -14)
  meth(win.title, "SetText", "TreeAI")
  win.ver = text(nav, "S", "muted")
  meth(win.ver, "SetPoint", "TOPLEFT", win.title, "BOTTOMLEFT", 1, -2)
  meth(win.ver, "SetText", "Coach · v" .. version())
  local navRule = meth(nav, "CreateTexture", nil, "ARTWORK")
  meth(navRule, "SetHeight", 1)
  meth(navRule, "SetPoint", "TOPLEFT", nav, "TOPLEFT", 8, -60)
  meth(navRule, "SetPoint", "TOPRIGHT", nav, "TOPRIGHT", -8, -60)
  color(navRule, tok("borderSoft", 0.78, 0.62, 0.28, 0.35))
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
  top.bg = meth(top, "CreateTexture", nil, "BACKGROUND")
  meth(top.bg, "SetAllPoints", top)
  color(top.bg, 0, 0, 0, 0)
  local topRule = meth(top, "CreateTexture", nil, "ARTWORK")
  meth(topRule, "SetHeight", 1)
  meth(topRule, "SetPoint", "BOTTOMLEFT", top, "BOTTOMLEFT", 0, 0)
  meth(topRule, "SetPoint", "BOTTOMRIGHT", top, "BOTTOMRIGHT", 0, 0)
  color(topRule, tok("borderSoft", 0.78, 0.62, 0.28, 0.35))
  win.pageIcon = meth(top, "CreateTexture", nil, "ARTWORK")
  meth(win.pageIcon, "SetSize", 24, 24)
  meth(win.pageIcon, "SetPoint", "TOPLEFT", top, "TOPLEFT", PAD, -15)
  meth(win.pageIcon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
  win.pageTitle = text(top, "L", "gold")
  meth(win.pageTitle, "SetPoint", "TOPLEFT", top, "TOPLEFT", PAD + 32, -13)
  meth(win.pageTitle, "SetWordWrap", false)
  win.pageSub = text(top, "S", "muted")
  meth(win.pageSub, "SetPoint", "TOPLEFT", win.pageTitle, "BOTTOMLEFT", 0, -3)
  meth(win.pageSub, "SetWordWrap", false)
  local close = newButton(top)
  meth(close, "SetSize", 24, 24)
  meth(close, "SetPoint", "TOPRIGHT", top, "TOPRIGHT", -8, -8)
  close.t = text(close, "L", "muted")
  meth(close.t, "SetPoint", "CENTER", close, "CENTER", 0, 0)
  meth(close.t, "SetJustifyH", "CENTER")
  meth(close.t, "SetText", "×")
  meth(close, "SetScript", "OnClick", function() W.Close() end)
  meth(close, "SetScript", "OnEnter", function(self) W.Tooltip(self, "Fermer", nil, "Clic : fermer (Échap aussi)") end)
  meth(close, "SetScript", "OnLeave", hideTip)
  local ok, eb = pcall(G.CreateFrame, "EditBox", "TreeAIMainSearch", top, "InputBoxTemplate")
  if not ok or not eb then ok, eb = pcall(G.CreateFrame, "EditBox", "TreeAIMainSearch", top) end
  if ok and eb then
    meth(eb, "SetSize", 220, 24)
    meth(eb, "SetPoint", "TOPRIGHT", close, "TOPLEFT", -16, 0)
    meth(eb, "SetAutoFocus", false)
    meth(eb, "SetMaxLetters", 40)
    if not eb.Left then   -- sans modèle : fond à nous
      eb.bgt = meth(eb, "CreateTexture", nil, "BACKGROUND"); meth(eb.bgt, "SetAllPoints", eb); color(eb.bgt, 0, 0, 0, 0.5)
      meth(eb, "SetFontObject", G.GameFontHighlightSmall)
    end
    eb.hint = text(eb, "S", "muted")
    meth(eb.hint, "SetPoint", "LEFT", eb, "LEFT", 6, 0)
    meth(eb.hint, "SetText", "Rechercher une action, un réglage…")
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
  -- navigation : boutons créés / relus par buildNav (une page inscrite plus tard par son module y entre)
  W.BuildNav()
  meth(win, "SetScript", "OnShow", function() W.Render() end)
  meth(win, "SetScript", "OnHide", function() hideTip(); if win.search then meth(win.search, "ClearFocus") end end)
  return win
end

--- pastille de la page (nombre ou « ! ») ; page mère : la somme des pastilles de ses sous-pages
function W.Badge(id)
  local p = W.pageIndex[id]
  local n = p and p.badge and try(p.badge)
  if n == nil and W.SUBS[id] then
    local sum, bang = 0, false
    for _, c in ipairs(W.SUBS[id]) do
      local v = W.Badge(c)
      if tonumber(v) then sum = sum + tonumber(v) elseif v then bang = true end
    end
    n = (sum > 0 and sum) or (bang and "!") or nil
  end
  return n
end
local function paintNav(cur)
  local navCur = W.NavOf(cur)
  for _, b in ipairs(navButtons) do
    local on = b.page == cur or b.page == navCur
    if on then color(b.bg, tok("selected", 0.45, 0.74, 1, 0.18)) else color(b.bg, 0, 0, 0, 0) end
    if on then meth(b.mark, "Show") else meth(b.mark, "Hide") end
    meth(b.t, "SetTextColor", tok(on and "accent" or "text", 0.9, 0.9, 0.9))
    local n = b.page and W.Badge(b.page) or nil
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
  local navPage = W.pageIndex[W.NavOf(id)]
  meth(win.pageIcon, "SetTexture", p.icon or (navPage and navPage.icon) or (ICON .. "INV_Misc_Book_09"))
  W.BuildNav()
  paintNav(id)
  W.HeadTabs(id)
  -- (5.0) Réglages : sous-liste à gauche (façon Leatrix Plus), la page à droite ; ailleurs : onglets / lien de retour
  local side = W.SideOf(id)
  local Ly = newLay(contentWidth(), side and (SIDE_W + 8) or 0)
  W.lastLay = Ly
  local sideY = 0
  if side then sideY = W.SideList(Ly, side, id) else W.SubBar(Ly, id) end
  W.OwnNotices(Ly, id)
  W.TopActions(Ly, p)
  local ok, err = pcall(p.render, Ly)
  if not ok then Ly.text("|cffff5555Erreur d'affichage : " .. tostring(err) .. "|r") if T.Debug then T:Debug("Window", err) end end
  if query ~= "" then pcall(W.SearchElsewhere, Ly, id) end
  if Ly.y < sideY then Ly.y = sideY end
  Ly.finish()
  -- bouton d'action de la page (un seul, en bas à droite)
  local label, fn, tip
  if p.action then label, fn, tip = try(p.action) end
  -- (5.1 LOOK) pas de doublon : le bouton du pied qui redit une action déjà en tête de page (« Me concentrer » /
  -- « Me concentrer sur les métiers ») n'est pas montré une 2e fois
  if label and fn and W.DupOfTop(label, Ly.topList) then label = nil end
  if label and fn then
    win.action:SetLabel(label)
    win.action.fn, win.action.tip, win.action.labelText = fn, tip, label
    meth(win.action, "Show")
  else meth(win.action, "Hide") end
  local status = p.status and (try(p.status) or "") or ""
  if W._flash and lastRender < W._flash.t then status = W._flash.text else W._flash = nil end
  meth(win.status, "SetText", safe(status))
end
--- (4.0 QA) le résultat d'un clic (erreurs prêtes, blocages, combat…) dans le pied de la fenêtre, jamais dans le chat
function W.Flash(text, sec)
  W._flash = text and { text = tostring(text), t = (try(G.GetTime) or 0) + (tonumber(sec) or 10) } or nil
  if win and meth(win, "IsShown") then meth(win.status, "SetText", safe(text or "")) end
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

local function remember(id)
  if not W.char then return end
  W.char.page = id
  local par = W.pageIndex[id] and W.pageIndex[id].parent
  if par and W.SUBS[par] then
    local listed = false
    for _, c in ipairs(W.SUBS[par]) do if c == id then listed = true end end
    if listed then   -- (une page générique ouverte depuis Outils n'est pas « la dernière sous-page »)
      if type(W.char.sub) ~= "table" then W.char.sub = {} end
      W.char.sub[par] = id
    end
  end
end
function W.Select(id)
  id = W.Resolve(id)
  if not W.pageIndex[id] then return end
  remember(id)
  query = ""
  W._flash = nil
  if win and win.search then meth(win.search, "SetText", "") end
  if scroll then meth(scroll, "SetVerticalScroll", 0) end
  W.Render()
end
function W.Open(id)
  if not W:IsEnabled() then if T.ShowPanel then T:ShowPanel() end return end
  buildWindow()
  if not win then return end
  -- (5.1) la fenêtre s'ouvre TOUJOURS sur « Maintenant » (une page, 5 lignes) ; une page précise seulement si on la
  -- demande (bouton de la bande, lien, /tai fenetre sacs). Déjà ouverte : elle reste où le joueur est.
  if not id and not (win and meth(win, "IsShown")) and W.pageIndex.home then id = "home" end
  if id then
    id = W.Resolve(id)
    if W.pageIndex[id] then remember(id) end
  end
  -- (5.0) rouverte pendant son fondu de fermeture (un clic « fermer » puis un clic sur la bande dans le dixième de
  -- seconde) : le fondu est coupé, sinon elle se refermait toute seule. Dans la même image que la fermeture (aucun
  -- joueur ne clique deux fois par image), la fermeture en cours garde la main.
  local th = Th()
  if th and th.StopAnim and W._closeAt and ((try(G.GetTime) or 0) - W._closeAt) > 0.001 then th:StopAnim(win, 1) end
  local was = meth(win, "IsShown")
  meth(win, "Show")
  if was then W.Render() end      -- fenêtre cachée : son OnShow la dessine déjà (pas deux rendus)
end
function W.Close()
  if not win then return end
  W._closeAt = try(G.GetTime) or 0
  local th = Th()
  if th then th:FadeOut(win) else meth(win, "Hide") end
end
function W.Toggle(id)
  if win and meth(win, "IsShown") and (not id or selectedPage() == W.Resolve(id)) then W.Close() else W.Open(id) end
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

-- (4.0 QA) « Tout va bien (…) » seulement pour ce qui est vraiment vérifié : sacs / réparation (Inventaire) et
-- familier (Chasseur) sont des fonctions bêta, coupées en mode sûr
local function allGoodText()
  local chk = {}
  local Inv, Hu = T.modules and T.modules.Inventory, T.modules and T.modules.Hunter
  if Inv and Inv._active then chk[#chk + 1] = "sacs, réparation" end
  if Hu and Hu._active then chk[#chk + 1] = "familier" end
  return "Rien d'urgent : continue ton étape." .. (#chk > 0 and (" Tout va bien (" .. tconcat(chk, ", ") .. ").") or "")
end
W.AllGoodText = allGoodText
--- (4.1 LAYOUT) « Maintenant » est fondu dans la page Quêtes : au plus 3 lignes utiles (danger, mort, courses…), jamais
--- les avis non demandés (Core : ownPage) ni l'étape (déjà en tête de page) ; le reste vit sur la page de son module
-- ce qui compte maintenant (LAYOUT : danger, mort → corps, sacs pleins, munitions, réparation, familier, quête finie)
W.NOW_TOPICS = { bags = true, ammo = true, repair = true, petfood = true, pet = true, death = true, corpse = true }
W.NOW_MODULES = { Speed = true, Inventory = true, Hunter = true, Errands = true }
function W.NowActions(n)
  local out = {}
  local steps = T.STEP_SOURCES or {}
  for _, e in ipairs(T.GetTabLines and T:GetTabLines("now") or {}) do
    if #out >= (n or 3) then break end
    local isStep = e.pin and steps[e.source or ""] and (e.prio or 1) < 3
    if not isStep and not e.header and not e.control and not e.ownPage and e.kind ~= "always"
        and ((e.prio or 1) >= 3 or e.kind == "death" or W.NOW_TOPICS[e.topic or ""] or (e.module and W.NOW_MODULES[e.module])) then
      out[#out + 1] = e
    end
  end
  return out
end
-- (Quêtes, Donjons : WindowPages.lua, insérés ici dans l'ordre de la navigation)
-- (5.1 LOOK) LA page d'accueil : « Maintenant » — 5 lignes au plus (T.Now.Model), chacune avec UN bouton qui fait la
-- chose ou qui y mène. Rien d'autre sur la page : le reste est à un clic dans la colonne de gauche.
function W.HomeLines()
  local N = T.Now
  if type(N) == "table" and N._active and N.Model then
    local ok, lines = pcall(N.Model, 5)
    if ok and type(lines) == "table" then return lines end
  end
  -- module « Maintenant » coupé : les lignes urgentes du cœur (danger, mort, sacs pleins), au plus 5
  local out = {}
  for _, e in ipairs(W.NowActions(5)) do
    out[#out + 1] = { id = "urgent:" .. tostring(e.id), text = plain(e.text), prio = e.prio, icon = e.icon, btn = e.onClick and "Voir" or nil,
      fn = e.onClick and function() W.Run(e) end or nil, entry = e }
  end
  return out
end
function W.RenderHome(Ly)
  local lines = W.HomeLines()
  local shown = 0
  for _, l in ipairs(lines) do
    if shown < 5 and W.Match(l.text, l.btn) then Ly.todo(l) shown = shown + 1 end
  end
  if shown == 0 and W.Query() == "" then
    local N = T.Now
    Ly.gap(sp().m)
    Ly.text((N and N.IdleText and N.IdleText()) or allGoodText(), "M", "muted")
  end
  W.homeShown = shown
end
W.AddPage({ id = "home", label = "Maintenant", icon = TAB_ICON.now, title = "Maintenant",
  desc = "Quoi faire tout de suite : 5 lignes au plus, un clic chacune",
  sub = function()
    local Sc = T.Shortcuts
    local guide = not (Sc and Sc.GuideOn) or Sc.GuideOn()
    return "Ce qu'il y a à faire tout de suite — un clic par ligne" .. (guide and "" or " · guidage des quêtes coupé")
  end,
  render = function(Ly) W.RenderHome(Ly) end })
W.AddPage({ id = "quests", label = "Quêtes", icon = TAB_ICON.quests, title = "Quêtes", render = function(Ly) W.RenderTab(Ly, "quests") end })
--- (4.1 LAYOUT) quêtes de mon journal qu'un membre du groupe peut prendre : { {title, questID|index} }, source
--- (le cerveau des quêtes s'il est là, sinon la barre de quête : même règle « un membre ne l'a pas »)
function W.ShareList()
  local out = {}
  local QB = T.QuestBrain
  if type(QB) == "table" and type(QB.GroupList) == "function" then
    local ok, l = pcall(QB.GroupList)
    if ok and type(l) == "table" then
      for _, e in ipairs(l) do if type(e) == "table" and e.share then out[#out + 1] = { title = e.title, id = e.questID } end end
      return out, "brain"
    end
  end
  local Qk = T.Quick
  if type(Qk) == "table" and type(Qk.ShareCandidates) == "function" then
    local ok, l = pcall(Qk.ShareCandidates)
    if ok and type(l) == "table" then
      for _, e in ipairs(l) do if type(e) == "table" then out[#out + 1] = { title = e.title, id = e.id, index = e.index } end end
    end
  end
  return out, "quick"
end
--- un clic : chaque quête de la liste partagée au groupe (QuestLogPushQuest, hors combat, jamais sans clic)
function W.ShareAll()
  if inCombat() then W.Flash("Impossible en combat : réessaie après.") return 0 end
  local Qk = T.Quick
  local list, src = W.ShareList()
  if #list == 0 then W.Flash("Rien à partager : tes amis ont déjà tes quêtes (ou ne peuvent pas les prendre).") return 0 end
  local n = 0
  if src == "quick" and type(Qk) == "table" and type(Qk.ShareAll) == "function" then
    n = tonumber((try(Qk.ShareAll))) or #list     -- (index du journal, une quête toutes les 0,6 s, jamais la sélection)
  else
    local QL = G.C_QuestLog
    local push = G.QuestLogPushQuest
    for _, e in ipairs(list) do
      local idx = e.index or (e.id and QL and QL.GetLogIndexForQuestID and try(QL.GetLogIndexForQuestID, e.id))
      if not idx and e.id then   -- (sans C_QuestLog : l'index du journal relevé par le module Quêtes)
        local Qm = T.modules and T.modules.Quests
        for _, q in ipairs(Qm and Qm.lastList or {}) do if q.id == e.id then idx = q.index end end
      end
      if idx and push and try(push, idx) ~= false then n = n + 1 end
    end
  end
  W.Flash(fmt("%d quête%s partagée%s au groupe.", n, n > 1 and "s" or "", n > 1 and "s" or ""))
  return n
end
function W.ShareBlock(Ly)
  if (tonumber(try(G.GetNumGroupMembers)) or 0) < 2 then return end
  local list = W.ShareList()
  if Ly.header("Partager", "group:share", #list, ICON .. "INV_Letter_15") then
    if #list == 0 then
      Ly.text("Rien à partager : tes amis ont déjà tes quêtes (ou ne peuvent pas les prendre).", "S", "muted", 6)
    else
      local names = {}
      for i, e in ipairs(list) do if i <= 4 then names[#names + 1] = "« " .. strip(e.title or "?") .. " »" end end
      Ly.text(tconcat(names, ", ") .. (#list > 4 and fmt(" et %d autre%s", #list - 4, #list > 5 and "s" or "") or ""), "S", "text", 6)
      Ly.buttons({ { fmt("Partager (%d)", #list), function() W.ShareAll() end,
        "Clic : partage ces quêtes à ton groupe (hors combat ; chacun accepte de son côté)" } }, 2)
    end
  end
end
W.AddPage({ id = "group", label = "Groupe", icon = TAB_ICON.group, title = "Groupe", desc = "Tes amis, les missions, qui fait quoi",
  sub = function()
    local n = tonumber(try(G.GetNumGroupMembers)) or 0
    return n > 1 and fmt("Groupe de %d", n) or "Seul pour l'instant"
  end,
  top = function()
    local out = {}
    local n = (tonumber(try(G.GetNumGroupMembers)) or 0) > 1 and #W.ShareList() or 0
    if n > 0 then out[#out + 1] = { fmt("Partager mes quêtes (%d)", n), function() W.ShareAll() end,
      "Clic : partage à ton groupe les quêtes qu'un membre peut prendre (hors combat)" } end
    local QB = T.QuestBrain
    if type(QB) == "table" and type(QB.SetMode) == "function" then
      out[#out + 1] = { "Mode « Avec le groupe »", function() pcall(QB.SetMode, "group") end,
        "Clic : le trajet d'abord sur les quêtes que ton groupe a aussi" }
    end
    return out
  end,
  render = function(Ly)
    -- (4.1 LAYOUT) « Partager » : les quêtes qu'un membre peut prendre, un clic (hors combat)
    W.ShareBlock(Ly)
    -- (4.1, agent GEARTRADE) équipement du groupe / échanges, s'il est là
    local GG = T.GroupGear
    if type(GG) == "table" and type(GG.Section) == "function" then
      local okG, errG = pcall(GG.Section, Ly)
      if not okG and T.Debug then T:Debug("Window", errG) end
    end
    -- (4.0 QA) l'état vide dit la vérité : en groupe, module Groupe coupé (bêta) → un bouton pour l'allumer
    local inG = (tonumber(try(G.GetNumGroupMembers)) or 0) > 1
    local Gm = T.modules and T.modules.Group
    if inG and Gm and not Gm._active then
      W.RenderTab(Ly, "group", "Le suivi du groupe est coupé (fonction bêta) : allume-le pour voir la vie, la zone et les quêtes de tes amis.",
        nil, { "Allumer « Groupe »", function() T:SetModuleEnabled("Group", true) end, "Clic : allume le module Groupe (sans /reload)" })
    elseif inG then
      W.RenderTab(Ly, "group", "Tes amis n'ont pas TreeAI : leurs quêtes n'apparaissent qu'avec l'addon.")
    else
      W.RenderTab(Ly, "group", "Seul pour l'instant : quand tu seras en groupe, TreeAI suivra vos quêtes et vos trajets.")
    end
  end })
W.AddPage({ id = "dungeon", label = "Donjons", icon = TAB_ICON.dungeon, title = "Donjons", render = function(Ly) W.RenderTab(Ly, "dungeon") end })
W.AddPage({ id = "hv", label = "HV", icon = TAB_ICON.hv, title = "Hôtel des ventes", desc = "Bénéfice : acheter-revendre, sacs, métiers",
  render = function(Ly)
    local A = T.modules and T.modules.Auction
    -- (4.1 AHPROFIT) la page dessinée par le module : 3 chiffres, 4 gros boutons, la liste choisie
    if A and A._active and A.RenderPage then A.RenderPage(Ly, W) return end
    -- (4.0 QA) « Allumer l'HV (bêta) » : le bouton du pied de page, pas deux fois le même
    W.RenderTab(Ly, "hv", (A and A._active) and "Pas encore de prix : à l'hôtel des ventes, lance un scan complet."
      or "Le module Hôtel des ventes est coupé (fonction bêta) : « Allumer l'HV (bêta) », en bas à droite.")
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
-- (4.0 QA) un onglet sans titre (créé par une ligne d'un module coupé) n'est pas une page à proposer
local function hasTitle(id) local t = T.tabs and T.tabs[id] return t and type(t.title) == "string" and t.title ~= "" end
-- ===================================================================== (4.1 LAYOUT) Personnage et Réglages
-- pages mères : elles ouvrent la sous-page choisie la dernière fois (onglets en haut, W.SubBar)
local function mother(id)
  return function(Ly)   -- (seulement si aucune sous-page n'existe : sinon la sous-page est dessinée à sa place)
    Ly.empty("Rien ici pour l'instant : les fonctions de cette page sont coupées (Réglages → Fonctions).")
  end
end
-- (4.3 V43) Chasseur (familier, talents) et Macros & touches : deux pages mères au lieu de « Personnage »
W.AddPage({ id = "hunter", label = "Chasseur", icon = ICON .. "Ability_Hunter_BeastCall", title = "Chasseur",
  desc = "Familier, aides de chasseur, talents", render = mother("hunter") })
local function modOff(name) local m = T.modules and T.modules[name] return not (m and m._active) end
--- (4.3 V43) Chasseur → Familier : l'état du familier (lu dans le jeu), les aides de chasseur et les conseils
--- « Familier » de Conseils pro ; rien d'automatique (nourrir, appeler = tes touches)
function W.PetLines()
  local out = {}
  local function sv(v) if v == nil or (G.issecretvalue and G.issecretvalue(v)) then return nil end return v end
  local has = sv(try(G.UnitExists, "pet"))
  if not has then
    out[#out + 1] = { text = "Pas de familier appelé : Appel du familier (ou Ressusciter le familier s'il est mort).", kind = "warn" }
  else
    local nm = sv(try(G.UnitName, "pet"))
    local hp, hpm = tonumber(sv(try(G.UnitHealth, "pet"))), tonumber(sv(try(G.UnitHealthMax, "pet")))
    local dead = sv(try(G.UnitIsDead, "pet"))
    out[#out + 1] = { text = fmt("Familier : %s%s", type(nm) == "string" and nm or "?",
      dead and " (mort)" or ((hp and hpm and hpm > 0) and fmt(" · vie %d %%", floor(hp * 100 / hpm + 0.5)) or "")),
      kind = dead and "warn" or "info" }
    local hap = tonumber(sv(try(G.GetPetHappiness)))
    if hap then
      local txt = ({ "Mécontent : nourris-le (dégâts -25 %, il peut s'enfuir)", "Content : nourris-le bientôt",
        "Heureux : dégâts +25 %" })[hap]
      if txt then out[#out + 1] = { text = "Humeur : " .. txt, kind = hap < 3 and "warn" or "info" } end
    end
  end
  return out
end
W.AddPage({ id = "pet", label = "Familier", icon = ICON .. "Ability_Hunter_BeastCall", title = "Familier",
  desc = "Ton familier, ses besoins, les aides de chasseur", render = function(Ly)
    for _, e in ipairs(W.PetLines()) do Ly.row({ text = e.text, kind = e.kind, icon = ICON .. "Ability_Hunter_BeastCall" }) end
    local C = T.modules and T.modules.Coach
    if C and C.RenderPage then
      local ok, err = pcall(C.RenderPage, Ly, "pet")
      if not ok and T.Debug then T:Debug("Window", err) end
    end
  end })
W.AddPage({ id = "talents", label = "Talents", icon = ICON .. "Ability_Marksmanship", title = "Talents",
  desc = "Où mettre tes points", render = function(Ly)
    W.RenderTab(Ly, "talents", modOff("Talents") and "Le conseiller de talents est coupé (fonction bêta) : « Allumer Talents », en bas à droite."
      or "Rien à dépenser : ton prochain point de talent viendra au prochain niveau.")
  end,
  action = function()
    if modOff("Talents") and T.modules and T.modules.Talents then
      return "Allumer Talents", function() T:SetModuleEnabled("Talents", true) end, "Clic : allume le conseiller de talents (sans /reload)"
    end
  end })
-- (4.3 V43) Équipement : « Le meilleur maintenant » (un objet par emplacement, gain réel, coût, temps, clic = y aller)
function W.GearNow(Ly)
  local Gm = T.modules and T.modules.Gear
  if not (Gm and Gm._active and Gm.NowRows) then return end
  local ok, rows = pcall(Gm.NowRows)
  if not (ok and type(rows) == "table") or #rows == 0 then return end
  local keep = {}
  for _, e in ipairs(rows) do if W.Match(e.text, e.sub) then keep[#keep + 1] = e end end
  local tot = {}
  if (rows.cost or 0) > 0 then tot[#tot + 1] = (T.Fmt and T.Fmt.Money and T.Fmt.Money(rows.cost)) or (rows.cost .. " pc") end
  if (rows.min or 0) > 0 then tot[#tot + 1] = (W.MinutesText and W.MinutesText(rows.min * 60)) or fmt("%d min", rows.min) end
  if Ly.header("Le meilleur maintenant" .. (#tot > 0 and (" · " .. tconcat(tot, " · ")) or ""), "gear:now", #keep, ICON .. "INV_Chest_Chain") then
    for _, e in ipairs(keep) do Ly.row(e) end
  end
end
--- (4.3 V43) réparations : l'usure la plus forte (lue dans le jeu), clic = la boussole vers le réparateur
function W.RepairRow(Ly)
  local Inv = T.modules and T.modules.Inventory
  local okD, p, broken = false, nil, 0
  if Inv and Inv.Durability then okD, p, broken = pcall(Inv.Durability) end
  p = okD and tonumber(p) or nil
  if not p or (p > 80 and (tonumber(broken) or 0) == 0) or W.Query() ~= "" then return end
  local txt = (tonumber(broken) or 0) > 0 and fmt("Réparations : %d pièce%s cassée%s (plus de stats) — répare vite", broken,
    broken > 1 and "s" or "", broken > 1 and "s" or "") or fmt("Réparations : équipement à %d %%", p)
  Ly.row({ id = "gear:repair", text = txt, kind = ((tonumber(broken) or 0) > 0 or p <= 30) and "warn" or "info",
    prio = ((tonumber(broken) or 0) > 0 or p <= 30) and 3 or 1, icon = ICON .. "Trade_BlackSmithing",
    tooltip = { "Le prix exact s'affiche chez un marchand qui répare (jamais estimé).",
      "Une pièce à 0 % ne donne plus ses stats." },
    clickHint = "Clic : la boussole vers le réparateur le plus proche", onClick = function() W.GoRepair() end })
end
--- (4.3 V43) stock de potions de soins : « 2 sur 5 », la meilleure de ton niveau, clic = la chercher à l'HV
function W.PotionRow(Ly)
  local Gm = T.modules and T.modules.Gear
  if not (Gm and Gm.PotionStock) or W.Query() ~= "" then return end
  local ok, st = pcall(Gm.PotionStock)
  if not (ok and type(st) == "table") or st.have >= st.want then return end
  Ly.row({ id = "gear:potions", text = fmt("Potions de soins : %d sur %d conseillées%s", st.have, st.want,
      st.name and (" — la meilleure pour toi : " .. st.name) or ""), kind = st.have == 0 and "warn" or "info",
    icon = ICON .. "INV_Potion_51", tooltip = { "Une potion sauve un pull raté ; en donjon, garde-en au moins " .. st.want .. ".",
      st.name and fmt("%s : tu en as %d.", st.name, st.haveBest) or "Ta meilleure potion n'est pas encore connue du jeu (survole-la une fois).",
      "Où : l'HV ou un alchimiste (ami, groupe)." },
    clickHint = "Clic : la chercher à l'HV (ouverte), sinon la page HV",
    onClick = function() if Gm.GoSource then Gm.GoSource({ kind = "ah" }, { id = st.best }) end end })
end
function W.GearTop()
  local Gm = T.modules and T.modules.Gear
  if not (Gm and Gm._active) then return {} end
  local out = { { "Recalculer", function() T:UserAction(function() Gm.MarkDirty() Gm:ScanNow() end) end,
    "Clic : recalcule le meilleur objet par emplacement (hors combat)" } }
  local ok, rows = pcall(Gm.NowRows)
  local first = ok and type(rows) == "table" and rows[1]
  if first then
    out[#out + 1] = { "Aller au 1er objet", first.onClick, "Clic : " .. strip(first.text) .. " — " .. strip(first.sub) }
  end
  out[#out + 1] = { "Réparateur", function() W.GoRepair() end, "Clic : la boussole vers le réparateur le plus proche (connu)" }
  return out
end
--- (4.3 V43) boussole vers le réparateur le plus proche (vu ou connu) ; faux si aucun
function W.GoRepair() return W.GoService("repair") end
--- (5.1 LOOK) « y aller » vers le service le plus proche connu (repair, vendor, trainer, auction, bank, innkeeper…) —
--- la destination part chez Suivi Forever / le repère du jeu quand ils tiennent la flèche (Addons.HookCompass)
W.SERVICE_NONE = { repair = "Aucun réparateur connu ici : parle à un forgeron / armurier, TreeAI le retiendra.",
  vendor = "Aucun marchand connu ici : parle à un marchand, TreeAI le retiendra.",
  auction = "Aucun hôtel des ventes connu ici : il y en a un dans chaque capitale.",
  trainer = "Aucun maître connu ici : parle-lui une fois, TreeAI le retiendra." }
function W.GoService(kind)
  local Q = T.QuickTravel
  local svc = type(Q) == "table" and Q.FindService and select(2, pcall(Q.FindService, kind))
  if type(svc) == "table" and Q.GoTo then
    pcall(Q.GoTo, svc)
    if W.Flash and svc.name then W.Flash(fmt("%s : c'est parti.", strip(svc.name)), 5) end
    return true
  end
  if W.Flash then W.Flash(W.SERVICE_NONE[kind] or "Rien de connu ici pour l'instant.", 6) end
  return false
end
--- (5.1 LOOK) un objet qui manque (composant, potion, munition) : HV ouvert = le chercher ; sinon y aller
function W.FindItem(itemID)
  local Am = T.modules and T.modules.Auction
  local f = rawget(G, "AuctionHouseFrame") or rawget(G, "AuctionFrame")
  local open = type(f) == "table" and meth(f, "IsShown")
  if open and Am and Am._active and type(Am.SearchItem) == "function" and itemID and try(Am.SearchItem, itemID) then return "ah" end
  return W.GoService("auction") and "go" or false
end
--- (5.1 LOOK) CONNECTEURS : une ligne de conseil sans clic reçoit le clic évident — celui qui fait la chose ou qui y
--- mène. Lu sur ce que la ligne déclare (service = "repair" | "vendor" | "trainer" | "auction", itemID, page), sinon
--- sur son sujet (topic) ou ses mots (réparer → réparateur ; refaire le plein / munitions → marchand ; talents → la
--- fenêtre de talents du jeu). Rien d'évident = rien (jamais un clic qui ne fait rien). → fn, phrase du clic
W.TOPIC_SERVICE = { repair = "repair", ammo = "vendor", food = "vendor", petfood = "vendor", drink = "vendor", water = "vendor" }
function W.DefaultAction(e)
  if type(e) ~= "table" or e.onClick or e.header or e.control then return nil end
  local svc = e.service or W.TOPIC_SERVICE[e.topic or ""]
  local txt = low(e.text or "")
  if not svc and not e.itemID and not e.page then
    if e.id == "inv_repair" or txt:find("répare", 1, true) or txt:find("réparation", 1, true) then svc = "repair"
    elseif txt:find("refais le plein", 1, true) or txt:find("chez un marchand", 1, true) or txt:find("flèche", 1, true) or txt:find("munition", 1, true) then svc = "vendor"
    elseif txt:find("vérifie tes talents", 1, true) then
      return function()
        if inCombat() then W.Flash("Tes talents : après le combat.") return end
        local f = rawget(G, "ToggleTalentFrame")
        if type(f) == "function" then pcall(f) else W.Open("talents") end
      end, "Clic : la fenêtre de talents du jeu"
    end
  end
  if svc == "repair" then return function() W.GoRepair() end, "Clic : le réparateur le plus proche (connu)" end
  if svc then return function() W.GoService(svc) end, "Clic : y aller (le plus proche connu)" end
  if e.itemID then return function() W.FindItem(e.itemID) end, "Clic : le chercher à l'HV (ouvert), sinon y aller" end
  if e.page and W.pageIndex[W.Resolve(e.page)] then return function() W.Select(e.page) end, "Clic : ouvrir la page" end
  return nil
end
W.AddPage({ id = "gear", label = "Équipement", short = "Équipement", icon = ICON .. "INV_Chest_Chain", title = "Équipement",
  desc = "Le meilleur objet par emplacement, où l'obtenir, réparations",
  top = function() return W.GearTop and W.GearTop() or {} end,
  render = function(Ly)
    W.RepairRow(Ly)
    W.PotionRow(Ly)
    W.GearNow(Ly)
    W.RenderTab(Ly, "gear", modOff("Gear") and "Le module Équipement est coupé (fonction bêta) : « Allumer Équipement », en bas à droite."
      or "Rien de mieux pour l'instant : TreeAI regarde tes sacs, tes récompenses de quête et l'HV.")
  end,
  action = function()
    if modOff("Gear") and T.modules and T.modules.Gear then
      return "Allumer Équipement", function() T:SetModuleEnabled("Gear", true) end, "Clic : allume le module Équipement (sans /reload)"
    end
  end })
W.AddPage({ id = "settings", label = "Réglages", icon = TAB_ICON.options, title = "Réglages",
  desc = "Ce qui est à l'écran, fonctions, mes addons, tous les réglages, outils", render = mother("settings") })

-- -------------------------------------------------- Réglages → Raccourcis : toutes les touches de TreeAI
local KEY_FR = { ALT = "Alt", CTRL = "Ctrl", SHIFT = "Maj", META = "Cmd", BUTTON3 = "Clic milieu", MOUSEWHEELUP = "Molette haut",
  MOUSEWHEELDOWN = "Molette bas", SPACE = "Espace", ESCAPE = "Échap", NUMPAD = "Pavé num. " }
function W.KeyText(k)
  if type(k) ~= "string" or k == "" then return nil end
  local parts = {}
  for part in k:gmatch("[^%-]+") do
    local fr = KEY_FR[part]
    if not fr then
      local np = part:match("^NUMPAD(.+)$")
      fr = np and ("Pavé num. " .. np) or part
    end
    parts[#parts + 1] = fr
  end
  return tconcat(parts, "-")
end
local function isOurs(cmd)
  return type(cmd) == "string" and (cmd:find("^TREEAI") ~= nil or cmd:find("^CLICK TreeAI") ~= nil)
end
--- tous les raccourcis de TreeAI : { {cmd, name, key1, key2} } (liste des raccourcis du jeu, sinon nos noms connus)
local bindMemo, bindAt = nil, -100
function W.Bindings()
  local t = try(G.GetTime) or 0
  if bindMemo and t - bindAt < 5 then
    for _, b in ipairs(bindMemo) do   -- (touches relues : elles changent dans Échap → Raccourcis)
      local k1, k2 = try(G.GetBindingKey, b.cmd)
      b.key1, b.key2 = type(k1) == "string" and k1 ~= "" and k1 or nil, type(k2) == "string" and k2 ~= "" and k2 or nil
    end
    return bindMemo
  end
  local out, seen = {}, {}
  local function add(cmd)
    if seen[cmd] or not isOurs(cmd) then return end
    seen[cmd] = true
    local name = rawget(G, "BINDING_NAME_" .. cmd)
    if type(name) ~= "string" or name == "" then return end
    local k1, k2 = try(G.GetBindingKey, cmd)
    out[#out + 1] = { cmd = cmd, name = name, key1 = type(k1) == "string" and k1 ~= "" and k1 or nil,
      key2 = type(k2) == "string" and k2 ~= "" and k2 or nil }
  end
  local n = tonumber(try(G.GetNumBindings)) or 0
  for i = 1, n do add((try(G.GetBinding, i))) end
  -- (le jeu ne liste que les raccourcis de Bindings.xml ; nos boutons « CLICK » n'existent que par leur nom)
  for k in pairs(G) do
    if type(k) == "string" and k:sub(1, 13) == "BINDING_NAME_" then add(k:sub(14)) end
  end
  tsort(out, function(a, b) return a.name < b.name end)
  bindMemo, bindAt = out, t
  return out
end
local function stripApi() local Sc = T.Shortcuts return type(Sc) == "table" and Sc or nil end
W.AddPage({ id = "keys", label = "Touches", icon = ICON .. "INV_Misc_Key_14", title = "Touches",
  top = function()
    local Sc = stripApi()
    if Sc and type(Sc.SuggestKeys) == "function" then
      local miss = type(Sc.MissingKeys) == "function" and #Sc.MissingKeys() or 0
      return { { "Touches conseillées", function() Sc.SuggestKeys() W.Dirty() end,
        "Clic : une touche libre pour chaque action rapide (Alt-1…), jamais une touche déjà prise ; hors combat",
        icon = ICON .. "INV_Misc_Key_03", sub = miss > 0 and fmt("%d action%s sans touche", miss, miss > 1 and "s" or "") or "Toutes les actions ont une touche",
        count = miss } }
    end
  end,
  desc = "Chaque touche de TreeAI, et les raccourcis conseillés en un clic",
  sub = "Échap → Options → Raccourcis → AddOns → TreeAI pour changer une touche",
  render = function(Ly)
    local list = W.Bindings()
    local free = 0
    for _, b in ipairs(list) do if not b.key1 then free = free + 1 end end
    Ly.text(fmt("%d raccourcis TreeAI, %d sans touche. « Touches conseillées » pose une touche libre sur les plus utiles "
      .. "(jamais une touche déjà prise).", #list, free), "S", "muted")
    local shown = 0
    if Ly.header("Toutes les touches", "keys:all", #list, ICON .. "INV_Misc_Key_14") then
      for _, b in ipairs(list) do
        if W.Match(b.name, b.cmd) then
          shown = shown + 1
          local key = W.KeyText(b.key1)
          Ly.row({ text = b.name, right = key or "aucune", kind = key and "info" or "quest",
            tooltip = { key and ("Touche : " .. key .. (b.key2 and (" · " .. W.KeyText(b.key2)) or "")) or "Pas de touche",
              "Échap → Options → Raccourcis → AddOns → TreeAI pour la changer." } })
        end
      end
    end
    if shown == 0 and #list > 0 then Ly.empty("Rien ne correspond à « " .. W.Query() .. " ».") end
    if #list == 0 then Ly.empty("Aucun raccourci TreeAI trouvé (le jeu les liste au chargement).") end
  end,
  action = function()
    local Sc = stripApi()
    if Sc and type(Sc.SuggestKeys) == "function" then
      return "Touches conseillées", function() Sc.SuggestKeys() W.Dirty() end,
        "Clic : une touche libre pour chaque action rapide (Alt-1…), jamais une touche déjà prise ; hors combat"
    end
    local B = T.modules and T.modules.Binds
    if B and B.Refresh then
      return "Touches conseillées", function() T:SetModuleEnabled("Binds", true) W.OpenTab("binds") end,
        "Clic : les touches conseillées pour tes sorts et macros (Sorts & barres)"
    end
  end })

-- (4.3 V43) Macros & touches → Sorts & barres : le conseiller de touches (module Binds) rangé avec les touches,
-- plus en « Onglet » sous Réglages
W.AddPage({ id = "spellkeys", label = "Sorts & barres", icon = ICON .. "INV_Misc_Key_03", title = "Sorts & barres",
  desc = "Cases vides, touches libres, sorts sans touche, où poser tes macros", render = function(Ly)
    W.RenderTab(Ly, "binds", modOff("Binds") and "Le conseiller de touches est coupé : « Allumer Touches », en bas à droite."
      or "Rien à corriger : tes sorts importants ont une touche.")
  end,
  action = function()
    if modOff("Binds") and T.modules and T.modules.Binds then
      return "Allumer Touches", function() T:SetModuleEnabled("Binds", true) end, "Clic : allume le conseiller de touches (sans /reload)"
    end
  end })

-- -------------------------------------------------- Réglages → Affichage : ce qui est à l'écran, un clic chacun
local function modOn(name) local m = T.modules and T.modules[name] return (m and m.db and m.db.enabled ~= false and m._active ~= false) and true or false end
local function dbOf(name) local m = T.modules and T.modules[name] return m and m.db end
local function setMod(name, on) if T.SetModuleEnabled and T.modules and T.modules[name] then T:SetModuleEnabled(name, on) end end
--- éléments d'écran : { id, label, preview, on(), set(bool) } — l'ordre = de haut en bas de l'écran
W.DISPLAY = {
  { id = "guide", sec = "main", label = "Guidage des quêtes", preview = "Trajet, boussole et repères de quêtes : tout allumer ou tout couper d'un clic",
    on = function() local Sc = T.Shortcuts return not (Sc and Sc.GuideOn) or Sc.GuideOn() end,
    set = function(v) local Sc = T.Shortcuts if Sc and Sc.SetGuide then Sc.SetGuide(v) end end, combatOK = true },
  { id = "strip", sec = "main", label = "Bande d'actions rapides", preview = "Une rangée fine : Guide ON · Rendre · Prendre · Zone | Sacs · Équip. · Métiers | Groupe",
    on = function() local d = dbOf("Shortcuts") return modOn("Shortcuts") and not (d and d.strip == false) end,
    set = function(v) local d = dbOf("Shortcuts") if v and not modOn("Shortcuts") then setMod("Shortcuts", true) end
      if d then d.strip = v and true or false end
      local Sc = T.Shortcuts if Sc and Sc.OnOptionChanged then pcall(Sc.OnOptionChanged, Sc, "strip") end end },
  { id = "stripCompact", sec = "main", label = "Bande en mode compact", preview = "Icônes seules, sans les mots (elle prend deux fois moins de place)",
    on = function() local d = dbOf("Shortcuts") return (d and d.text == false) and true or false end,
    set = function(v) local d = dbOf("Shortcuts") if d then d.text = not v end
      local Sc = T.Shortcuts if Sc and Sc.OnOptionChanged then pcall(Sc.OnOptionChanged, Sc, "text") end end, combatOK = true },
  { id = "stripCombat", sec = "main", label = "Cacher la bande en combat", preview = "Elle s'efface quand le combat commence et revient à la fin",
    on = function() local d = dbOf("Shortcuts") return (d and d.hideCombat) and true or false end,
    set = function(v) local d = dbOf("Shortcuts") if d then d.hideCombat = v and true or false end
      local Sc = T.Shortcuts if Sc and Sc.OnOptionChanged then pcall(Sc.OnOptionChanged, Sc, "hideCombat") end end, combatOK = true },
  { id = "stripFade", sec = "main", label = "Bande estompée au repos et en combat", preview = "60 % au repos, 30 % en combat, pleine dès que la souris passe dessus",
    on = function() local d = dbOf("Shortcuts") return not (d and d.fade == false) end,
    set = function(v) local d = dbOf("Shortcuts") if d then d.fade = v and true or false end
      local Sc = T.Shortcuts if Sc and Sc.OnOptionChanged then pcall(Sc.OnOptionChanged, Sc, "fade") end end, combatOK = true },
  { id = "nowBlock", sec = "main", label = "Petit bloc « Maintenant » (contre la colonne Suivi Forever)", preview = "3 lignes : quoi faire, un clic chacune — seulement si la place est libre",
    on = function() local d = dbOf("Now") return modOn("Now") and not (d and d.block == false) end,
    set = function(v) local d = dbOf("Now") if v and not modOn("Now") then setMod("Now", true) end
      if d then d.block = v and true or false end
      local Nw = T.Now if Nw and Nw.OnOptionChanged then pcall(Nw.OnOptionChanged, Nw, "block") end end, combatOK = true },
  { id = "alert", sec = "main", label = "Alertes (une à la fois)", preview = "Danger, mort, sacs pleins, quête finie : une petite ligne qui s'efface seule",
    on = function() local a = T.settings and T.settings.alert return not (a and a.enabled == false) end,
    set = function(v) local a = T.settings and T.settings.alert if a then a.enabled = v and true or false end end, combatOK = true },
  { id = "alertQuiet", sec = "main", label = "Alertes discrètes", preview = "Petites, 5 à 8 s, sans couleur vive (décoché : 8 à 15 s, texte rouge)",
    on = function() local a = T.settings and T.settings.alert return not (a and a.quiet == false) end,
    set = function(v) local a = T.settings and T.settings.alert if a then a.quiet = v and true or false end end, combatOK = true },
  { id = "compass", sec = "main", label = "Boussole TreeAI (en haut, au centre)", preview = "flèche + « 708 m · 2 min » + une ligne — coupée quand Suivi Forever guide",
    on = function() local d = dbOf("Compass") return modOn("Compass") and not (d and d.shown == false) end,
    set = function(v) local d = dbOf("Compass") if v and not modOn("Compass") then setMod("Compass", true) end
      if d then d.shown = v and true or false end
      local Cm = T.Compass if Cm and Cm.OnOptionChanged then pcall(Cm.OnOptionChanged, Cm, "shown") end end },
  { id = "horizon", label = "Bande d'horizon (2e flèche, en haut)", preview = "N · NE · E… avec les 3 prochains arrêts",
    on = function() local d = dbOf("Horizon") return modOn("Horizon") and d and d.shown == true or false end,
    set = function(v) local d = dbOf("Horizon") if v and not modOn("Horizon") then setMod("Horizon", true) end
      if d then d.shown = v and true or false end
      local Hz = T.Horizon if Hz and Hz.OnOptionChanged then pcall(Hz.OnOptionChanged, Hz, "shown") end end },
  { id = "card", label = "Carte « Prochaine étape »", preview = "les 3 prochaines étapes dans une petite carte",
    on = function() return (W.db and W.db.card ~= false) and true or false end,
    set = function(v) if W.db then W.db.card = v and true or false W:OnOptionChanged("card") end end },
  { id = "watch", label = "Suivi TreeAI (à côté du suivi du jeu)", preview = "quêtes du trajet avec distance et durée",
    on = function() local d = dbOf("Watch") return modOn("Watch") and not (d and d.shown == false) end,
    set = function(v) local d = dbOf("Watch") if d then d.shown = v and true or false end setMod("Watch", v) end },
  { id = "meter", label = "Fenêtre « Dégâts »", preview = "dégâts du groupe après chaque combat",
    on = function() local d = dbOf("Meter") return modOn("Meter") and d and d.shown == true or false end,
    set = function(v) local d = dbOf("Meter") if v and not modOn("Meter") then setMod("Meter", true) end
      local Dm = T.Meter
      if Dm and type(Dm.Show) == "function" then pcall(Dm.Show, v) elseif d then d.shown = v and true or false end
      if d then d.shown = v and true or false end end },
  { id = "quickbar", label = "Barre de quête (objet, cible, partage, foyer)", preview = "petite barre d'icônes déplaçable",
    on = function() local d = dbOf("Quick") return modOn("Quick") and not (d and d.bar == false) end,
    set = function(v) local d = dbOf("Quick") if d then d.bar = v and true or false end setMod("Quick", v) end },
  { id = "fightbar", label = "Barre de combat (manger, familier, pull…)", preview = "boutons « c-1… » au-dessus des barres d'action",
    on = function() return modOn("QuickFight") end, set = function(v) setMod("QuickFight", v) end },
  { id = "radar", label = "Radar rond", preview = "petit radar des objectifs",
    on = function() local d = dbOf("Radar") return modOn("Radar") and d and d.shown == true or false end,
    set = function(v) local d = dbOf("Radar") if v and not modOn("Radar") then setMod("Radar", true) end
      local Rd = T.modules and T.modules.Radar
      if Rd and type(Rd.Show) == "function" then pcall(Rd.Show, v) elseif d then d.shown = v and true or false end end },
  { id = "minimap", sec = "map", label = "Pastille de la minimap", preview = "clic : cette fenêtre ; clic droit : le menu",
    on = function() local d = dbOf("Hub") return modOn("Hub") and not (d and d.button == false) end,
    set = function(v) local d = dbOf("Hub") if d then d.button = v and true or false end
      local H = T.Hub if H and H.OnOptionChanged then pcall(H.OnOptionChanged, H, "button") end end },
  { id = "pins", sec = "map", label = "Icônes de quêtes (« ! » à prendre, « ? » à rendre)", preview = "carte + minimap ; muettes si Questie dessine",
    on = function() return modOn("QuestPins") end, set = function(v) setMod("QuestPins", v) end },
  { id = "bagglow", sec = "bags", label = "Surbrillance dans les sacs", preview = "Objets de quête, à vendre, à garder : un liseré de couleur sur l'objet",
    on = function() return modOn("Highlight") end, set = function(v) setMod("Highlight", v) end },
  { id = "tooltips", sec = "bags", label = "Lignes TreeAI dans les infobulles d'objets", preview = "Amélioration, valeur, quête, métier : sous l'infobulle du jeu",
    on = function() return modOn("ItemInfo") end, set = function(v) setMod("ItemInfo", v) end },
  { id = "minipins", sec = "map", label = "Points en plus sur la minimap", preview = "rares, élites, maîtres de vol, services",
    on = function() return modOn("MinimapPins") end, set = function(v) setMod("MinimapPins", v) end },
  { id = "path", sec = "map", label = "Chemin pointillé (carte + minimap)", preview = "le trajet appris, en pointillés",
    on = function() return modOn("PathDraw") end, set = function(v) setMod("PathDraw", v) end },
}
-- sections de la page Affichage (sec d'un élément ; sans sec : « En plus »)
W.DISPLAY_SECTIONS = {
  { "main", "Toujours à l'écran", ICON .. "INV_Misc_Spyglass_02" },
  { "map", "Carte et minimap", ICON .. "INV_Misc_Map_01" },
  { "bags", "Sacs et infobulles", ICON .. "INV_Misc_Bag_08" },
  { "more", "En plus (coupés d'office)", ICON .. "INV_Misc_EngGizmos_20" },
}
--- (5.0) l'état de chaque élément d'écran : { { id, label, on } } (tests, visite, research/look5.md)
function W.DisplayState()
  local out = {}
  for _, d in ipairs(W.DISPLAY) do
    local okOn, on = pcall(d.on)
    out[#out + 1] = { id = d.id, label = d.label, on = okOn and on and true or false, sec = d.sec or "more" }
  end
  return out
end
W.AddPage({ id = "display", label = "Affichage", icon = ICON .. "INV_Misc_Spyglass_02", title = "Affichage",
  desc = "Chaque élément que TreeAI pose à l'écran : une case pour l'allumer ou le couper",
  sub = "Une case par élément. D'office : guidage, bande fine, petit bloc « Maintenant », pastille et ses repères.",
  top = function()
    local out = {}
    if T.SetEditMode then
      out[#out + 1] = { "Déplacer les cadres", function() T:SetEditMode(not T:IsEditMode()) end,
        "Clic : mode édition (glisser les cadres, molette = taille)", icon = ICON .. "INV_Misc_Wrench_01", sub = "Glisser, molette = taille" }
    end
    local Sc = T.Shortcuts
    if Sc and type(Sc.ResetInterface) == "function" then
      out[#out + 1] = { "Tout remettre en place", function() Sc.ResetInterface("reset") end,
        "Clic : chaque cadre TreeAI revient à sa place et à sa taille d'origine (/tai interface reset)",
        icon = "Interface\\Buttons\\UI-RefreshButton", sub = "/tai interface reset" }
    end
    return out
  end,
  render = function(Ly)
    local shown = 0
    for _, sec in ipairs(W.DISPLAY_SECTIONS) do
      local items = {}
      for _, d in ipairs(W.DISPLAY) do
        if (d.sec or "more") == sec[1] and W.Match(d.label, d.preview) then items[#items + 1] = d end
      end
      if #items > 0 and Ly.header(sec[2], "display:" .. sec[1], #items, sec[3], sec[4]) then
        for _, d in ipairs(items) do
          local okOn, on = pcall(d.on)
          on = okOn and on and true or false
          shown = shown + 1
          Ly.toggle({ id = "display:" .. d.id, label = d.label, desc = d.preview, on = on, combatOK = d.combatOK,
            state = on and "à l'écran" or "coupé", set = function(v) pcall(d.set, v) end })
        end
        if sec[1] == "bags" then
          local th = Th()
          if th and th.LEGEND and W.Query() == "" then
            Ly.text("Légende des couleurs dans les sacs :", "S", "muted")
            Ly.legend(th.LEGEND.bags)
          end
        end
      end
    end
    if shown == 0 and W.Query() ~= "" then Ly.empty("Rien ne correspond à « " .. W.Query() .. " ».") end
  end })

-- -------------------------------------------------- Réglages → Profil : Simple / Complet, une phrase chacun
W.PROFILES = {
  { id = "sur", label = "Simple",
    text = "Seulement ce qui est fiable et utile tout le temps : quêtes, trajet, boussole, bande d'actions, métiers, groupe. Rien de bêta, écran léger." },
  { id = "tout", label = "Complet",
    text = "Tout TreeAI, fonctions bêta comprises (HV, combat, radar, conseils avancés…) : plus d'aides, plus de choses à lire et à l'écran." },
}
--- le profil en cours : « sur » (Simple) | « tout » (Complet) | « essentiel » (léger : réglage avancé)
function W.Profile() return T.ModuleProfile and T:ModuleProfile() or "tout" end
function W.SetProfile(id)
  if inCombat() then W.Flash("Impossible en combat : réessaie après.") return false end
  if not (T.SetModuleProfile and (id == "sur" or id == "tout" or id == "essentiel")) then return false end
  T:SetModuleProfile(id)
  W.Flash(id == "sur" and "Profil Simple : seules les fonctions fiables tournent." or id == "tout"
    and "Profil Complet : tout est allumé, fonctions bêta comprises." or "Profil léger : seul l'essentiel tourne.")
  return true
end
W.AddPage({ id = "profile", label = "Profil", side = "Profil : Simple / Complet", icon = ICON .. "INV_Misc_EngGizmos_20", title = "Profil",
  desc = "Simple ou Complet : ce que TreeAI fait tourner",
  sub = "Deux profils. Tu peux changer quand tu veux, sans recharger.",
  render = function(Ly)
    local cur = W.Profile()
    local list = {}
    for _, pr in ipairs(W.PROFILES) do
      if W.Match(pr.label, pr.text) then
        local id = pr.id
        list[#list + 1] = { pr.label, pr.text, function() W.SetProfile(id) end, active = cur == id }
      end
    end
    if #list > 0 then Ly.choice(list) end
    if cur == "essentiel" and W.Query() == "" then
      Ly.note("Profil léger en cours (réglage avancé) : seul l'essentiel tourne. Choisis Simple ou Complet pour en sortir.", "info")
    end
    if W.Query() == "" then
      Ly.text("Une fonction précise ? « Fonctions », à gauche : une ligne par fonction, un clic pour l'allumer ou la couper.", "S", "muted")
      local n, on = 0, 0
      for _, name in ipairs(T.moduleOrder or {}) do
        local m = T.modules[name]
        if m and m._classOK ~= false then n = n + 1 if m._active then on = on + 1 end end
      end
      Ly.kv("Fonctions allumées", fmt("%d sur %d", on, n))
    end
  end,
  action = function()
    return "Voir les fonctions", function() W.Select("modules") end, "Clic : la liste des fonctions, une par une"
  end })

-- Outils : le lanceur de la pastille, en cartes (icône + titre + description), groupé
W.AddPage({ id = "tools", label = "Outils", icon = TAB_ICON.tools, title = "Outils", desc = "Recherche, macros, boussole, cadres, erreurs",
  gapAfter = false,
  render = function(Ly)
    local H = T.Hub
    -- (4.0 QA) toujours les entrées du menu principal (pas la liste bêta restée ouverte dans la pastille)
    local list = {}
    if H and H.Entries then
      local m = H.mode
      H.mode = "main"
      list = try(H.Entries) or {}
      H.mode = m
    end
    local groups = { { "outils", "Outils" }, { "reglages", "Réglages et diagnostic" } }
    local any = false
    for _, gp in ipairs(groups) do
      local items = {}
      for _, e in ipairs(list) do
        -- « Afficher / cacher le panneau » : une seule fois, dans « Panneau texte (ancien) » plus bas
        if e.group == gp[1] and e.fn and e.id ~= "options" and e.id ~= "panel" and W.Match(e.title, e.desc, e.tip) then
          items[#items + 1] = e
        end
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
    local covered = { now = true, quests = true, group = true, hv = true, dungeon = true, modules = true, talents = true,
      gear = true, prof = true }
    local others = {}
    for _, t in ipairs(T.GetTabs and T:GetTabs() or {}) do
      if not covered[t.id] and hasTitle(t.id) and W.Match(tabTitle(t.id)) then others[#others + 1] = t end
    end

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
    if not any and query ~= "" then Ly.empty("Rien ne correspond à « " .. query .. " ».") end
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
local TAB_PAGE = { now = "quests", quests = "quests", group = "group", hv = "hv", dungeon = "dungeon", modules = "modules",
  talents = "talents", gear = "gear", prof = "prof", macros = "macros", coach = "coach", journal = "journal", binds = "spellkeys" }
function W.OpenTab(id)
  local pid = TAB_PAGE[id]
  if not pid and T.tabs and T.tabs[id] then
    if W.char then W.char.tab = id end
    pid = "tab"
  end
  W.Open(pid or "quests")
end
-- Modules (bêta compris) : la liste « Plus → Modules »
W.AddPage({ id = "modules", label = "Fonctions", icon = ICON .. "INV_Misc_EngGizmos_20", title = "Fonctions",
  desc = "Allumer / couper chaque fonction (bêta comprises)", gapAfter = true,
  render = function(Ly)
    -- (4.0 QA) l'intro suit le profil en cours (pas « Mode sûr… » quand tout est allumé)
    local prof = T.ModuleProfile and T:ModuleProfile()
    local intro = (prof == "tout" and "Profil Complet. Tout est allumé, fonctions bêta comprises (testées hors du jeu seulement).")
      or (prof == "essentiel" and "Profil léger : seul l'essentiel tourne.")
      or "Profil Simple (mode sûr) : seules les fonctions fiables tournent. Les fonctions « bêta » sont testées hors du jeu : allume-les une par une."
    Ly.text(intro, "S", "muted")
    if W._perf then   -- « Coût des fonctions » (Options, ligne de cette page) : ici, jamais dans le chat
      local t = try(G.GetTime) or 0
      if not W._perfLines or t - (W._perfAt or -100) > 10 then   -- mémoire des addons relue ≤ 1 fois / 10 s
        W._perfLines, W._perfAt = T.PerfLines and (try(T.PerfLines, T) or {}) or {}, t
      end
      if Ly.header("Coût des fonctions", "fn:perf", nil, ICON .. "INV_Misc_PocketWatch_01") then
        for _, l in ipairs(W._perfLines) do Ly.text(strip(l), "S", "text", 6) end
      end
    end
    W.RenderTab(Ly, "modules")
  end,
  action = function()
    local prof = T.ModuleProfile and T:ModuleProfile()
    if prof == "sur" then
      return "Profil Complet (tout allumer)", function() T:SetModuleProfile("tout") end,
        "Clic : allumer toutes les fonctions bêta (les automatismes de quête restent à cocher dans Options > Barre de quête)"
    end
    return "Profil Simple (mode sûr)", function() T:SetModuleProfile("sur") end, "Clic : seulement les fonctions fiables"
  end })
--- (4.0 QA) « Coût des fonctions » : la page « Fonctions » de la fenêtre le montre (au lieu du chat)
function W.ShowPerf()
  if not W._active then return false end
  W._perf = true
  W.Open("modules")
  return true
end

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
    local listW = max(120, min(168, Ly.W - 300 - PAD * 2 - 4))   -- (4.1) fenêtre plus étroite (moitié droite)
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
        if on then color(b.bg, tok("selected", 0.45, 0.74, 1, 0.18)) else color(b.bg, 0, 0, 0, 0) end
        meth(b.t, "SetTextColor", tok(on and "accent" or "text", 0.9, 0.9, 0.9))
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
    local pw = max(260, min(max(300, Ly.W - listW - PAD * 2), Ly.W - (PAD + listW + 4) - 4))   -- (5.0) jamais hors de la colonne
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
    return "Recharger l'interface", function()
      if inCombat() then W.Flash("Impossible en combat : réessaie après.") return end   -- (4.0 QA) jamais muet
      try(G.ReloadUI)
    end, "Clic : recharger l'interface (certains réglages en ont besoin)"
  end })

-- ===================================================================== branchements (gardés)
-- un rendu du panneau (≤ 2/s) = la carte et la fenêtre se mettent à jour (signature : rien si rien n'a changé)
if type(T.RefreshPanel) == "function" and not W._wrapped then
  W._wrapped = true
  local orig = T.RefreshPanel
  T.RefreshPanel = function(self, ...)
    local r = orig(self, ...)
    if W._active then
      if card and meth(card, "IsShown") then W.RenderCardSoon() end
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
        if id == cur or (cur == "quests" and (id == "now" or id == "quests")) then return true end
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
    -- (4.0 QA) en mode édition, la carte reste (son calque se pose dessus) : le joueur place les deux
    if card and meth(card, "IsShown") and not (T.IsEditMode and T:IsEditMode()) then meth(card, "Hide") end
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
    if W._active and hudOn() and W.db.replacePanel ~= false and not userPanel then
      local p = self.GetPanel and self:GetPanel()
      if not (p and p:IsShown()) then W.OpenTab(id) return end
    end
    return orig(self, id, ...)
  end
end

-- mode édition : la carte s'y montre avec son calque (le panneau texte seulement s'il est ouvert à la main) ; en
-- sortant, chacun reprend son état
if type(T.SetEditMode) == "function" and not W._wrappedEdit then
  W._wrappedEdit = true
  local orig = T.SetEditMode
  T.SetEditMode = function(self, on, ...)
    local before = userPanel
    local r = orig(self, on, ...)
    userPanel = before
    if on and r == false then return r end   -- (4.0 QA) refusé (combat) : rien ne bouge, la carte ne réapparaît pas
    if card and on and cardOn() then meth(card, "Show") end
    if not on then
      if userPanel then if card then meth(card, "Hide") end else hidePanelForCard() end
    end
    return r
  end
end

function W:OnEnable()
  -- (4.1 STRIP) une fois : la carte coupée aussi chez ceux qui l'avaient (le joueur peut la rallumer dans les options)
  if W.db and not W.db.cardOff41 then W.db.cardOff41 = true W.db.card = false end
  if cardOn() then
    T:After(1.5, function() if W._active then W.ShowCard() end end)
  elseif hudOn() then
    T:After(1.5, function() if W._active then hidePanelForCard() end end)
  end
  -- (PLACEBUG) le placement automatique ne passe qu'une fois l'écran posé (1er lancement, résolution changée) : la
  -- carte non posée par le joueur suit alors la place trouvée pour le panneau
  self:On("LAYOUT_DONE", function() if card and meth(card, "IsShown") then W.PlaceCard() end end)
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() if win and meth(win, "IsShown") and W.db.closeInCombat then W.Close() end end)
  -- pas de minuterie à nous : la carte suit les rendus du panneau (contenu changé, étape épinglée qui bouge avec
  -- le joueur), les changements de zone et le journal ; ≤ 1 rendu / 2 s, signature comparée
  local function soon() if card and meth(card, "IsShown") then W.RenderCard() end end
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() W.ForgetStops() soon() end)
  -- (4.0 QA) pierre de foyer / instance (écran de chargement), fin de vol : les trajets repartent d'ailleurs
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function() W.ForgetStops() soon() end)
  self:RegisterEvent("PLAYER_CONTROL_GAINED", function() W.ForgetStops() soon() end)
  self:RegisterEvent("QUEST_LOG_UPDATE", soon)
  -- (ONBOARD) la visite guidée montre NOS cadres : la carte « Prochaine étape » et la ligne « Zone » du trajet
  local Tr = T.Tour
  if type(Tr) == "table" and type(Tr.Target) == "function" then
    Tr.Target("next", function() return (card and meth(card, "IsVisible")) and card or nil end)
    Tr.Target("zone", function() return W.ZoneRow() end)
  end
  if T.HUB_EXTRA and not W._hub then
    W._hub = true
    table.insert(T.HUB_EXTRA, 1, { group = "jeu", id = "window", title = "Fenêtre TreeAI", icon = ICON .. "INV_Misc_Book_09",
      tip = "Clic : la fenêtre TreeAI (Quêtes, Trajet, Groupe, Donjons, Équipement, Sacs, Métiers, HV, Chasseur, Conseils, Réglages)", fn = function() W.Toggle() end })
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
    -- (4.0 QA) carte cachée : le panneau texte ne revient pas tout seul (il reste dans Outils, ouvert à la main)
    if cardOn() then W.ShowCard() else W.HideCard() end
  elseif key == "cardScale" then W.PlaceCard()
  end
  W.RenderCard(true)
end

T:RegisterSlash("fenetre", function(arg)
  arg = tostring(arg or ""):lower()
  if arg == "carte" then if card and meth(card, "IsShown") then W.HideCard() else W.ShowCard() end return end
  W.Toggle(arg ~= "" and arg or nil)
end, "fenêtre TreeAI (/tai fenetre [quests|group|dungeon|perso|hv|coach|settings|carte])")
