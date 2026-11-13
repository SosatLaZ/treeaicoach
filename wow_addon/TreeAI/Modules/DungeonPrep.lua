-- TreeAI — fenêtre « Prêt pour <donjon> ? » (agent DUNGEON2), extension du module Dungeon (même module, même
-- coût mesuré). Tout à la souris : ◀ ▶ = choisir le donjon (prévu), sections Groupe / Quêtes du donjon /
-- Préparation, une ligne = un point (✓ / ! / ✗), bouton « Partager » sur une quête qu'un ami n'a pas.
-- Cadre simple enfant d'UIParent (aucun modèle sécurisé, jamais ancré à un cadre Blizzard) : s'ouvre même en
-- combat sans rien bloquer. Ouverte = elle se met à jour sur ses propres évènements (groupe, journal, sacs,
-- durabilité, familier), regroupés ≥ 0,5 s ; fermée = zéro coût. Les données viennent de Dungeon.Checklist /
-- Dungeon.Quests (un seul propriétaire de l'info, l'onglet « Donjon » montre les mêmes points).
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
-- (5.1 GLYPH) texte à l'écran : signes absents des polices du jeu (« ≈ », « → »…) remplacés (Core.SafeText ; une
-- valeur secrète passe telle quelle)
local function safe(s)
  s = tostring(s or "")
  local f, ok, hit = T.SafeText, pcall(string.find, s, "[\226\227]")   -- (le cas courant : rien à faire, sans appel au cœur)
  if not (f and ok and hit) then return s end
  ok, hit = pcall(f, s)
  return ok and hit or s
end
local M = T and T.Dungeon
if not M then return end
local G = _G
local fmt, floor, max = string.format, math.floor, math.max
local tconcat = table.concat

local W = { rows = {}, n = 0 }
M._prep = W
local ROWS, ROW_H, WIDTH = 22, 20, 440
local STATE_COL = { ok = { 0.4, 0.9, 0.4 }, warn = { 1, 0.75, 0.3 }, bad = { 1, 0.35, 0.3 }, info = { 0.75, 0.75, 0.75 } }

local function Th() local t = T.Theme if type(t) == "table" and t.Button then return t end end
local function button(parent, text, w, h, fn, tip)
  local th = Th()
  local b = th and th:Button(parent, text, w, h, fn, tip and { tip } or nil)
  if b then return b end
  b = G.CreateFrame("Button", nil, parent)
  b:SetSize(w, h)
  b.label = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.label:SetPoint("CENTER", b, "CENTER", 0, 0)
  b.label:SetText(safe(text))
  b.SetLabel = function(self2, t2) self2.label:SetText(safe(t2)) end
  b:SetScript("OnClick", fn)
  return b
end
local function tipOn(frame, fnLines)
  frame:SetScript("OnEnter", function(self)
    local tt = G.GameTooltip
    local lines = fnLines(self)
    if not (tt and lines and #lines > 0) then return end
    tt:SetOwner(self, "ANCHOR_RIGHT")
    tt:AddLine(safe(lines[1]), 1, 1, 1, true)
    for i = 2, #lines do tt:AddLine(safe(lines[i]), 0.85, 0.85, 0.85, true) end
    tt:Show()
  end)
  frame:SetScript("OnLeave", function() local tt = G.GameTooltip if tt then tt:Hide() end end)
end

---------------------------------------------------------------------------------------------------
-- données de la fenêtre (testables sans cadre) : { {header=true, text} | {state, text, tip, btn={label, fn}} }
---------------------------------------------------------------------------------------------------
function M.PrepRows(d)
  local out = {}
  if not d then
    out[1] = { state = "info", text = "Aucun donjon à ton niveau dans la liste : choisis-en un avec les flèches < >." }
    return out
  end
  local members = M.Members()
  local list = M.Checklist(d, members)
  local function section(name)
    out[#out + 1] = { header = true, text = name }
    for _, c in ipairs(list) do if c.sec == name then out[#out + 1] = { state = c.state, text = c.text, tip = c.tip } end end
  end
  section("Groupe")
  -- quêtes du donjon
  out[#out + 1] = { header = true, text = "Quêtes du donjon" }
  if M.db and M.db.quests then
    local qs = M.Quests(d, members)
    for _, q in ipairs(qs.inLog) do
      if q.done then
        out[#out + 1] = { state = "ok", text = fmt("« %s » : finie, à rendre", q.title), questID = q.id }
      elseif #q.missing > 0 then
        local can = q.pushable ~= false
        out[#out + 1] = { state = "warn", text = fmt("« %s » : manque à %s", q.title, tconcat(q.missing, ", ")), questID = q.id,
          tip = can and "Partage : la quête est proposée à ceux qui ne l'ont pas (hors combat)." or "Cette quête ne se partage pas : chacun la prend chez le donneur.",
          btn = can and { label = "Partager", fn = function() M:ShareQuests(q.id) end } or nil }
      else
        local parts = {}
        for _, o in ipairs(q.obj) do parts[#parts + 1] = fmt("%s %d/%d", o.name, o.have, o.need) end
        out[#out + 1] = { state = "ok", text = fmt("« %s »%s", q.title, #parts > 0 and (" : " .. tconcat(parts, ", ")) or " : tout le groupe l'a"),
          questID = q.id }
      end
    end
    for _, q in ipairs(qs.toGet) do
      local txt = fmt("Prends « %s » [%d]%s", q.title, q.level or 0, q.giver and (" chez " .. q.giver) or "")
      local tip
      if #q.holders > 0 then
        txt = txt .. fmt(" · %s l'a", tconcat(q.holders, ", "))
        tip = "Un ami l'a déjà : demande-lui de la partager avant d'entrer."
      end
      out[#out + 1] = { state = "warn", text = txt, tip = tip, questID = q.id }
    end
    if qs.pending then
      out[#out + 1] = { state = "info", text = "Lecture des quêtes dans Questie…" }
    elseif #qs.inLog == 0 and #qs.toGet == 0 then
      local why = (not M.Questie()) and "Questie absent : quêtes du donjon inconnues"
        or (not d.area and "Donjon de Forever : ses quêtes ne sont pas encore dans Questie")
        or "Aucune quête de ce donjon à prendre à ton niveau"
      out[#out + 1] = { state = "info", text = why }
    end
  else
    out[#out + 1] = { state = "info", text = "Quêtes du donjon coupées dans les options" }
  end
  section("Préparation")
  return out
end

---------------------------------------------------------------------------------------------------
-- cadre
---------------------------------------------------------------------------------------------------
local function chosen()
  local d, why = M.Active()
  if d then return d, why end
  if W.pick then return W.pick, "choix" end
  local list = M.ForLevel()
  return list[1], "suggéré"
end

local function cycle(dir)
  local d, why = M.Active()
  if why == "instance" then return end
  local list = M.ForLevel()
  if #list == 0 then list = M.Index().list end
  local cur = d or W.pick
  local idx = 0
  for i, x in ipairs(list) do if x == cur then idx = i end end
  idx = ((idx - 1 + dir) % #list) + 1
  W.pick = list[idx]
  M.SetPlanned(list[idx].key)
end

function M:CreateChecklist()
  if W.frame or type(G.CreateFrame) ~= "function" then return W.frame end
  local f = G.CreateFrame("Frame", "TreeAIDungeonPrep", G.UIParent)
  W.frame = f
  f:SetSize(WIDTH, 110 + ROWS * ROW_H)
  f:SetPoint("CENTER", G.UIParent, "CENTER", 0, 40)
  f:SetFrameStrata("MEDIUM")
  f:SetMovable(true)
  f:EnableMouse(true)
  f:SetClampedToScreen(true)
  local th = T.Theme
  local hd = th and th.Window and th:Window(f, { id = "dungeonprep", title = "Prêt pour le donjon ?", icon = M.ICONS.dungeon,
    min = false, onClose = function() M:ToggleChecklist(false) end })
  if hd then
    W.title = hd.title
  else
    f.bg = f:CreateTexture(nil, "BACKGROUND")
    f.bg:SetAllPoints(f)
    f.bg:SetColorTexture(0.05, 0.05, 0.07, 0.94)
    f:RegisterForDrag("LeftButton")
    f:SetScript("OnDragStart", function(self) self:StartMoving() end)
    f:SetScript("OnDragStop", function(self) self:StopMovingOrSizing() end)
    W.title = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    W.title:SetPoint("TOPLEFT", f, "TOPLEFT", 10, -8)
    local close = button(f, "×", 20, 18, function() M:ToggleChecklist(false) end, "Fermer")
    close:SetPoint("TOPRIGHT", f, "TOPRIGHT", -6, -6)
  end
  -- choix du donjon : < nom > (les polices du jeu n'ont ni ◀ ni ▶ : carrés vides)
  W.prev = button(f, "<", 24, 20, function() cycle(-1) end, "Donjon précédent à ton niveau")
  W.prev:SetPoint("TOPLEFT", f, "TOPLEFT", 10, -32)
  W.next = button(f, ">", 24, 20, function() cycle(1) end, "Donjon suivant à ton niveau")
  W.next:SetPoint("TOPRIGHT", f, "TOPRIGHT", -10, -32)
  W.name = f:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  W.name:SetPoint("TOP", f, "TOP", 0, -36)
  W.name:SetWidth(WIDTH - 80)
  W.verdict = f:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  W.verdict:SetPoint("TOP", f, "TOP", 0, -58)
  W.verdict:SetWidth(WIDTH - 20)
  for i = 1, ROWS do
    local row = G.CreateFrame("Button", nil, f)
    row:SetSize(WIDTH - 20, ROW_H - 1)
    row:SetPoint("TOPLEFT", f, "TOPLEFT", 10, -76 - (i - 1) * ROW_H)
    row.icon = row:CreateTexture(nil, "ARTWORK")
    row.icon:SetSize(14, 14)
    row.icon:SetPoint("LEFT", row, "LEFT", 2, 0)
    row.text = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    row.text:SetPoint("LEFT", row, "LEFT", 20, 0)
    row.text:SetWidth(WIDTH - 120)
    row.text:SetJustifyH("LEFT")
    if row.text.SetWordWrap then row.text:SetWordWrap(false) end
    row.btn = button(row, "Partager", 74, 17, function() if row.r and row.r.btn then row.r.btn.fn() end end)
    row.btn:SetPoint("RIGHT", row, "RIGHT", 0, 0)
    tipOn(row, function(self2)
      local r = self2.r
      if not r or r.header then return nil end
      return { r.text, r.tip }
    end)
    W.rows[i] = row
  end
  W.share = button(f, "Partager les quêtes", 140, 22, function() M:ShareQuests() end, "Propose les quêtes du donjon à ceux qui ne les ont pas (hors combat)")
  W.share:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", 10, 10)
  W.tab = button(f, "Page Donjons", 120, 22, function() if T.Window and T.Window._active then T.Window.Open("dungeon") else T:ShowPanel(); T:SelectTab(M.TAB) end end, "Boss, butin du groupe, qui fait quoi")
  W.tab:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -10, 10)
  -- mise à jour quand elle est ouverte (ses évènements à elle ; rien quand elle est fermée)
  f:SetScript("OnEvent", function() M:QueueChecklist() end)
  f:SetScript("OnHide", function(self)
    if self.UnregisterAllEvents then self:UnregisterAllEvents() end
  end)
  if not hd then   -- sans Theme : Échap ferme quand même
    local list = G.UISpecialFrames
    if type(list) == "table" then list[#list + 1] = "TreeAIDungeonPrep" end
  end
  f:Hide()
  return f
end

local PREP_EVENTS = { "GROUP_ROSTER_UPDATE", "QUEST_LOG_UPDATE", "BAG_UPDATE_DELAYED", "UPDATE_INVENTORY_DURABILITY",
  "UNIT_PET", "PLAYER_LEVEL_UP", "UNIT_QUEST_LOG_CHANGED" }

function M:QueueChecklist()
  if W.queued then return end
  W.queued = true
  T:After(0.5, function() W.queued = false; M:RenderChecklist() end)
end

function M:ChecklistShown() return W.frame and W.frame:IsShown() and true or false end

function M:ToggleChecklist(on)
  local f = W.frame
  if on == false then if f and f:IsShown() then f:Hide() end return false end
  f = f or self:CreateChecklist()
  if not f then return false end
  if on == nil then on = not f:IsShown() end
  if not on then f:Hide() return false end
  f:Show()
  if f.RegisterEvent then for _, e in ipairs(PREP_EVENTS) do pcall(f.RegisterEvent, f, e) end end
  self:RenderChecklist()
  if self.StartTicker and self:IsEnabled() then self:StartTicker() end
  return true
end

function M:RefreshChecklist() if W.frame and W.frame:IsShown() then self:RenderChecklist() end end

function M:RenderChecklist()
  local f = W.frame
  if not (f and f:IsShown()) then return end
  local d, why = chosen()
  local rows = M.PrepRows(d)
  W.last = rows
  local title = d and fmt("Prêt pour %s ?", d.n) or "Prêt pour le donjon ?"
  if W.title then W.title:SetText(safe(title)) end
  if W.name then
    W.name:SetText(d and fmt("%s · niv %d–%d%s%s", d.n, d.min or 1, d.max or 60, d.new and " · Forever" or "",
      why == "instance" and " · en cours" or (why == "suggéré" and " · clique < > pour choisir" or " · prévu")) or "—")
  end
  local inst = why == "instance"
  if W.prev then if inst then W.prev:Hide() else W.prev:Show() end end
  if W.next then if inst then W.next:Hide() else W.next:Show() end end
  local bad, warn = 0, 0
  local share = false
  for _, r in ipairs(rows) do
    if r.state == "bad" then bad = bad + 1 elseif r.state == "warn" then warn = warn + 1 end
    if r.btn then share = true end
  end
  if W.verdict then
    if bad > 0 then W.verdict:SetText(fmt("|cffff5a4d%d point%s bloquant%s|r%s", bad, bad > 1 and "s" or "", bad > 1 and "s" or "",
        warn > 0 and fmt(" · %d à régler", warn) or ""))
    elseif warn > 0 then W.verdict:SetText(fmt("|cffffbf4d%d point%s à régler avant d'entrer|r", warn, warn > 1 and "s" or ""))
    else W.verdict:SetText("|cff66e666Tout est prêt : bon donjon !|r") end
  end
  W.bad, W.warn = bad, warn
  for i, row in ipairs(W.rows) do
    local r = rows[i]
    row.r = r
    if r then
      if r.header then
        row.icon:Hide()
        row.text:SetText(safe("|cff33cc66" .. r.text .. "|r"))
        row.btn:Hide()
      else
        row.icon:SetTexture(M.STATE_ICON[r.state] or M.STATE_ICON.info)
        row.icon:Show()
        local c = STATE_COL[r.state] or STATE_COL.info
        row.text:SetText(safe(r.text))
        if row.text.SetTextColor then row.text:SetTextColor(c[1], c[2], c[3]) end
        if r.btn then row.btn:Show() else row.btn:Hide() end
      end
      row:Show()
    else
      row:Hide()
    end
  end
  W.n = #rows
  if W.share then if share then W.share:Show() else W.share:Hide() end end
  local h = 110 + max(4, math.min(ROWS, #rows)) * ROW_H
  f:SetHeight(floor(h))
end
