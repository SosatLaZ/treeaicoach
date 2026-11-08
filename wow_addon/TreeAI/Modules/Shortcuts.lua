-- TreeAI — module Shortcuts (agent UIREDO) : UNE barre « Raccourcis » à l'écran au lieu de barres séparées.
-- Les modules gardent leur logique (quels boutons servent maintenant, attributs sécurisés, clics) ; ils amarrent
-- leur barre ici comme un « segment » : T:RegisterShortcutBar(frame, { id, label, order, inset, chrome, onUndock }).
-- La barre « Raccourcis » (cadre TreeAIShortcuts) :
--   * un seul fond (verre sombre, liseré 1 px), une poignée à gauche (glisser = déplacer, hors combat, sauf verrou),
--     un titre discret par groupe (« Quêtes · voyage », « Combat · familier »), un filet entre les groupes ;
--   * les segments vides disparaissent, la barre aussi quand plus rien ne sert (visibilité selon le contexte) ;
--   * déplacée comme un tout (mode édition : un seul cadre « Raccourcis »), position gardée par personnage ;
--   * même style de bouton partout (Theme:ShortcutStyle : fond, liseré, enfoncé, touche liée affichée).
-- Taint : les segments contiennent des boutons sécurisés → tout changement de parent / point / taille / affichage
-- se fait HORS COMBAT (T:RunOutOfCombat) ; en combat la barre ne bouge pas. Aucun appel protégé ici.
-- Coût : événementiel (taille / affichage d'un segment, options), aucune minuterie. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local S = T:RegisterModule("Shortcuts", {
  title = "Barre de raccourcis",
  description = "Une seule barre « Raccourcis » : quêtes, voyage, combat et familier, seulement les boutons utiles.",
  defaults = { enabled = true, dock = true, locked = false, labels = true, scale = 100 },
  charDefaults = { pos = nil },
  options = {
    { section = "Barre « Raccourcis »", key = "dock", type = "toggle", label = "Réunir les raccourcis dans une seule barre" },
    { key = "locked", type = "toggle", label = "Verrouiller la barre (elle ne bouge plus)" },
    { key = "labels", type = "toggle", label = "Titre de chaque groupe au-dessus des boutons" },
    { key = "scale", type = "range", label = "Taille de la barre (%)", min = 70, max = 150, step = 5 },
  },
})
T.Shortcuts = S

local floor, max = math.floor, math.max
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function meth(o, m, ...) local f = type(o) == "table" and o[m] if type(f) == "function" then return pass(pcall(f, o, ...)) end end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function Th() local t = T.Theme if type(t) == "table" and t.Color then return t end end
local function tok() local th = Th() return (th and th.SHORTCUT) or { size = 30, gap = 4, pad = 4, handle = 10 } end

local segs, segOrder = {}, {}       -- id -> { frame, label, order, inset, chrome, onUndock, docked }
S.segs = segs
local dock, handle, dividers, labels = nil, nil, {}, {}

local function docking() return S._active and S.db and S.db.dock ~= false end

--- Un module amarre sa barre (appelé à la création de la barre, hors combat). opts = { id, label, order,
--- inset (px masqués à gauche : poignée propre au module), chrome = { régions à cacher une fois amarrée },
--- movable = id de déplaçable à retirer, onUndock = fn (remettre la barre seule) }. Renvoie vrai si amarrée.
function T:RegisterShortcutBar(frame, opts)
  if type(frame) ~= "table" or type(opts) ~= "table" or not opts.id then return false end
  local sg = segs[opts.id]
  if not sg then
    sg = {}
    segs[opts.id] = sg
    segOrder[#segOrder + 1] = opts.id
    table.sort(segOrder, function(a, b) return (segs[a].order or 9) < (segs[b].order or 9) end)
  end
  sg.frame, sg.label, sg.order, sg.inset = frame, opts.label or opts.id, opts.order or 9, opts.inset or 0
  sg.chrome, sg.onUndock, sg.movable = opts.chrome or {}, opts.onUndock, opts.movable
  table.sort(segOrder, function(a, b) return (segs[a].order or 9) < (segs[b].order or 9) end)
  if not sg.hooked and type(frame.HookScript) == "function" then
    sg.hooked = true
    local function changed() S.Changed() end
    pcall(frame.HookScript, frame, "OnSizeChanged", changed)
    pcall(frame.HookScript, frame, "OnShow", changed)
    pcall(frame.HookScript, frame, "OnHide", changed)
  end
  if docking() then S.Changed() end
  return docking()
end
--- La barre d'un module est-elle amarrée ? (le module ne la place plus lui-même)
function S.Docked(frame)
  for _, sg in pairs(segs) do if sg.frame == frame and sg.docked then return true end end
  return false
end
function T:ShortcutDocked(frame) return S.Docked(frame) end

-- ===================================================================== cadre
local function locked() return (S.db and S.db.locked) or (T.FramesLocked and T:FramesLocked()) or false end
local function okAnchor(p, x, y) return T.ValidAnchor and T.ValidAnchor(p, x, y) or false end
local function savePos()
  if not dock then return end
  -- (PLACEBUG 4.0.1) le crochet du cœur a reposé la barre sur UIParent depuis son rectangle réel (ancrage explicite) ;
  -- sans lui, même calcul ici — jamais l'ancrage du client relu tel quel, jamais « nil → 0 »
  if T.AnchorFromRect then T:AnchorFromRect(dock) end
  local pt, _, rp, x, y = meth(dock, "GetPoint")
  if S.char and okAnchor(pt, x, y) then S.char.pos = { pt, okAnchor(rp, 0, 0) and rp or pt, floor(x + 0.5), floor(y + 0.5) } end
end
local function placeDock()
  if not dock or inCombat() then return end
  local p = S.char and S.char.pos
  meth(dock, "SetScale", max(0.7, math.min(1.5, (tonumber(S.db and S.db.scale) or 100) / 100)))
  -- (PLACEBUG) place incomplète / invalide = la place par défaut ; un SetPoint raté ne laisse jamais la barre sans ancrage
  if type(p) == "table" and not okAnchor(p[1], p[3], p[4]) then S.char.pos, p = nil, nil end
  if type(p) == "table" then
    local rp = okAnchor(p[2], 0, 0) and p[2] or p[1]
    meth(dock, "ClearAllPoints")
    if pcall(dock.SetPoint, dock, p[1], G.UIParent, rp, p[3], p[4]) then return end
  end
  if T.SetAnchor then T:SetAnchor(dock, "BOTTOM", 0, 190)
  else meth(dock, "ClearAllPoints") meth(dock, "SetPoint", "BOTTOM", G.UIParent, "BOTTOM", 0, 190) end
end
S.Place = placeDock

local function tip(owner)
  local tt = G.GameTooltip
  if not tt then return end
  meth(tt, "SetOwner", owner, "ANCHOR_TOP")
  meth(tt, "AddLine", "Raccourcis", 1, 0.82, 0.2)
  meth(tt, "AddLine", "Seulement les boutons utiles maintenant : quêtes, voyage, combat, familier.", 0.85, 0.85, 0.85, true)
  meth(tt, "AddLine", "Survole un bouton : ce qu'il fait, pourquoi maintenant, sa touche.", 0.85, 0.85, 0.85, true)
  meth(tt, "AddLine", locked() and "Barre verrouillée (Options > Barre de raccourcis)" or "Glisser : déplacer (hors combat)", 0.4, 0.8, 1)
  meth(tt, "AddLine", "Clic droit : réglages de la barre", 0.4, 0.8, 1)
  meth(tt, "Show")
end

local function ensureDock()
  if dock then return dock end
  if inCombat() or type(G.CreateFrame) ~= "function" then return nil end
  local k = tok()
  local ok, f = pcall(G.CreateFrame, "Frame", "TreeAIShortcuts", G.UIParent, G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  if not ok or not f then return nil end
  dock = f
  meth(f, "SetSize", k.handle + 2 * k.pad, k.size + 2 * k.pad)
  meth(f, "SetFrameStrata", "MEDIUM")
  meth(f, "SetClampedToScreen", true)
  meth(f, "SetMovable", true)
  local th = Th()
  if th then th:Skin(f, "card") end
  -- poignée : trois points or, glisser = déplacer, clic droit = options
  local okH, h = pcall(G.CreateFrame, "Button", nil, f)
  if okH and h then
    handle = h
    meth(h, "SetSize", k.handle, k.size)
    meth(h, "SetPoint", "LEFT", f, "LEFT", k.pad, 0)
    meth(h, "RegisterForClicks", "RightButtonUp")
    meth(h, "RegisterForDrag", "LeftButton")
    h.dots = {}
    for i = 1, 3 do
      local d = meth(h, "CreateTexture", nil, "ARTWORK")
      if d then
        meth(d, "SetSize", 3, 3)
        meth(d, "SetPoint", "CENTER", h, "CENTER", 0, (2 - i) * 7)
        if th then meth(d, "SetColorTexture", th:Color("gold")) else meth(d, "SetColorTexture", 1, 0.82, 0.2, 1) end
        h.dots[i] = d
      end
    end
    local hl = meth(h, "CreateTexture", nil, "HIGHLIGHT")
    if hl then meth(hl, "SetAllPoints", h); meth(hl, "SetColorTexture", 1, 1, 1, 0.08) end
    meth(h, "SetScript", "OnDragStart", function() if not inCombat() and not locked() then meth(f, "StartMoving") end end)
    meth(h, "SetScript", "OnDragStop", function() meth(f, "StopMovingOrSizing") savePos() end)
    meth(h, "SetScript", "OnClick", function()
      local Wn = T.Window
      if Wn and Wn._active and Wn.Open then
        if Wn.char then Wn.char.optPage = "Shortcuts" end
        Wn.Open("options")
      elseif T.OpenOptions then T:OpenOptions("Shortcuts") end
    end)
    meth(h, "SetScript", "OnEnter", tip)
    meth(h, "SetScript", "OnLeave", function() local tt = G.GameTooltip if tt then meth(tt, "Hide") end end)
  end
  placeDock()
  if T.RegisterMovable then
    T:RegisterMovable(f, "shortcuts", "Raccourcis", {
      onMoved = function(point, x, y, sc)
        if S.char then S.char.pos = { point, point, floor(x or 0), floor(y or 0) } end
        if sc and S.db then S.db.scale = floor(sc * 100 + 0.5) end
      end,
      onReset = function() if S.char then S.char.pos = nil end placeDock() end,
      -- (PLACEBUG) glissée par sa poignée (même avant 4.0.1) = posée par le joueur : rien ne la reprend
      isPlaced = function() return S.char and type(S.char.pos) == "table" or false end,
    })
  end
  meth(f, "Hide")
  return dock
end
S.Ensure = ensureDock
function S.Frame() return dock end

local function divider(i)
  local d = dividers[i]
  if d or not dock then return d end
  d = meth(dock, "CreateTexture", nil, "ARTWORK")
  if d then
    meth(d, "SetWidth", 1)
    local th = Th()
    if th then meth(d, "SetColorTexture", th:Color("borderSoft")) else meth(d, "SetColorTexture", 0.78, 0.62, 0.28, 0.35) end
    dividers[i] = d
  end
  return d
end
-- titre d'un groupe dans `w` px (petite police ≈ 5,4 px / lettre) : entier, sinon sa première partie, sinon rien
local function fitLabel(txt, w)
  txt = tostring(txt or "")
  local function fits(t) local _, n = t:gsub("[^\128-\191]", "") return n * 5.4 <= w end
  if txt == "" then return nil end
  if fits(txt) then return txt end
  local head = txt:match("^(.-) · ")
  if head and fits(head) then return head end
  return nil
end
S.FitLabel = fitLabel
local function label(i)
  local l = labels[i]
  if l or not dock then return l end
  local th = Th()
  l = th and th:Text(dock, "S", "muted") or meth(dock, "CreateFontString", nil, "OVERLAY", "GameFontDisableSmall")
  if l then meth(l, "SetJustifyH", "LEFT"); meth(l, "SetWordWrap", false) end
  labels[i] = l
  return l
end

-- ===================================================================== amarrage / disposition (hors combat)
local function dockSeg(sg)
  local f = sg.frame
  if sg.docked or not f then return end
  meth(f, "SetParent", dock)
  meth(f, "SetFrameStrata", "MEDIUM")
  if meth(f, "GetFrameLevel") and meth(dock, "GetFrameLevel") then meth(f, "SetFrameLevel", (meth(dock, "GetFrameLevel") or 1) + 1) end
  meth(f, "SetScale", 1)
  for _, r in ipairs(sg.chrome) do meth(r, "Hide") end
  local th = Th()
  if th and th.IsSkinned and th:IsSkinned(f) then th:Skin(f, "hud", { noBg = true, border = false }) end
  if sg.movable and T.UnregisterMovable then T:UnregisterMovable(sg.movable) end
  sg.docked = true
end
local function undockSeg(sg)
  local f = sg.frame
  if not (sg.docked and f) then return end
  meth(f, "SetParent", G.UIParent)
  for _, r in ipairs(sg.chrome) do meth(r, "Show") end
  local th = Th()
  if th and th.IsSkinned and th:IsSkinned(f) then th:Skin(f, "hud") end
  sg.docked = false
  if sg.onUndock then T.safecall(sg.onUndock) end
end

local lastSig, segSig   -- (définies plus bas) dernière disposition faite
--- Dispose les segments amarrés côte à côte ; cache la barre quand aucun ne sert.
function S.Layout()
  if inCombat() then T:RunOutOfCombat("Shortcuts:layout", S.Layout) return end
  if not docking() then
    for _, id in ipairs(segOrder) do undockSeg(segs[id]) end
    if dock then meth(dock, "Hide") end
    return
  end
  if not ensureDock() then return end
  local k = tok()
  local x = k.pad + k.handle + k.gap
  local n, h = 0, k.size + 2 * k.pad
  local showLabels = S.db.labels ~= false
  for _, id in ipairs(segOrder) do
    local sg = segs[id]
    local f = sg.frame
    if f then
      dockSeg(sg)
      local w = tonumber(meth(f, "GetWidth")) or 0
      local vis = (meth(f, "IsShown") and w - sg.inset > 8) and true or false
      if vis then
        n = n + 1
        if n > 1 then
          local d = divider(n - 1)
          if d then
            meth(d, "ClearAllPoints")
            meth(d, "SetPoint", "TOPLEFT", dock, "TOPLEFT", x, -k.pad)
            meth(d, "SetPoint", "BOTTOMLEFT", dock, "BOTTOMLEFT", x, k.pad)
            meth(d, "Show")
          end
          x = x + 1 + k.gap
        end
        meth(f, "ClearAllPoints")
        meth(f, "SetPoint", "LEFT", dock, "LEFT", x - sg.inset, 0)
        local l = label(n)
        if l then
          -- (4.0 QA) le titre tient dans la largeur de son groupe (sinon il déborde sur le suivant) : titre court
          -- (« Quêtes » de « Quêtes · voyage ») quand c'est étroit, rien s'il ne tient pas
          local wl = w - sg.inset
          local txt = fitLabel(sg.label, wl)
          meth(l, "ClearAllPoints")
          meth(l, "SetPoint", "BOTTOMLEFT", dock, "TOPLEFT", x, 3)
          meth(l, "SetWidth", max(1, wl))
          meth(l, "SetText", txt or "")
          if showLabels and txt then meth(l, "Show") else meth(l, "Hide") end
        end
        x = x + (w - sg.inset)
        h = max(h, (tonumber(meth(f, "GetHeight")) or 0))
      end
    end
  end
  for i = n, #dividers do if i >= n and dividers[i] then meth(dividers[i], "Hide") end end
  for i = n + 1, #labels do meth(labels[i], "Hide") end
  S.shown = n
  if n == 0 then meth(dock, "Hide") lastSig = segSig() return end
  meth(dock, "SetSize", x + k.pad, h)
  meth(dock, "Show")
  lastSig = segSig()
end
local pending = false
-- signature bon marché des segments (affiché, largeur, hauteur) + réglages : rien de neuf = rien à refaire
function segSig()
  local t = { S.db and S.db.labels ~= false and "L" or "l", dock and (meth(dock, "IsShown") and "S" or "s") or "-" }
  for _, id in ipairs(segOrder) do
    local f = segs[id].frame
    t[#t + 1] = id .. (meth(f, "IsShown") and "1" or "0") .. ":" .. floor(tonumber(meth(f, "GetWidth")) or 0)
      .. "x" .. floor(tonumber(meth(f, "GetHeight")) or 0) .. (segs[id].docked and "d" or "u")
  end
  return table.concat(t, "|")
end
S.Sig = segSig
--- Un segment a changé (taille, affichage) : une disposition, regroupée, hors combat — seulement si quelque
--- chose a vraiment changé (QuickFight se redessine jusqu'à 4 fois par seconde).
local pendingAt = -10
function S.Changed()
  if not docking() then return end
  -- (4.0 QA) un rappel perdu (demandé pendant le code d'un module coupé entre-temps : T:After le saute) ne bloque
  -- plus la barre pour toujours : au-delà de 2 s, on redemande
  local t = tonumber((G.GetTime and G.GetTime())) or 0
  if pending and t - pendingAt < 2 then return end
  if segSig() == lastSig then return end
  pending, pendingAt = true, t
  T:After(0, function() pending = false S.Layout() end)
end

function S:OnEnable()
  T:After(0.5, S.Layout)
  -- (ONBOARD) la visite guidée montre LA barre « Raccourcis » quand les barres y sont amarrées
  local Tr = T.Tour
  if type(Tr) == "table" and type(Tr.Target) == "function" then
    Tr.Target("quick", function() return (dock and meth(dock, "IsVisible")) and dock or nil end)
  end
end
function S:OnDisable()
  T:RunOutOfCombat("Shortcuts:layout", function()
    for _, id in ipairs(segOrder) do undockSeg(segs[id]) end
    if dock then meth(dock, "Hide") end
  end)
end
function S:OnOptionChanged(key)
  if key == "scale" then placeDock() end
  S.Layout()
end
