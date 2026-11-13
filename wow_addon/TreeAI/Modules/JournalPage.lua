-- TreeAI — page « Journal » de la fenêtre TreeAI (agent JOURNAL, 4.0.1) : « Nettoyer mon journal ».
-- Rendu seulement (extension du module Window, comme WindowPages.lua : même module, même coût mesuré) : la liste
-- vient de TreeAI.Abandon (faits du jeu rangés par raison : échouées, chaîne bloquée, doublons, grises, donjon déjà
-- fait, trop dures pour le groupe actuel, autre continent / très loin) ; l'abandon est fait par Abandon.Request /
-- Abandon.BatchRequest DANS le clic du joueur (2 clics, hors combat, une quête par clic).
--  * une ligne par quête : [niveau] titre (couleur de niveau), les faits, bouton « Abandonner » → « Confirmer » ;
--    en combat « Hors combat » ; sans abandon direct « Où cliquer ? » (la marche à suivre au pied de la fenêtre) ;
--  * pied : « Tout ce qui est gris (N) » → « Confirmer : N grises ? » → « Suivante : « X » » (une quête par clic) ;
--  * page « Quêtes » : la section « Nettoyer mon journal » (3 premières + « … et N autres ») renvoie ici.
-- Fenêtre fermée = rien ne tourne ; aucune fonction protégée appelée ici ; jamais le chat.
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
local W = T and T.Window
if not (W and W.AddPage) then return end
local G = _G
local fmt, floor = string.format, math.floor
local tconcat = table.concat
local function pass(ok, ...) if ok then return ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw((...), "JournalPage") end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function meth(o, m, ...) local f = type(o) == "table" and o[m] if type(f) == "function" then return pass(pcall(f, o, ...)) end end
local function strip(s) return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")) end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function Th() local t = T.Theme if type(t) == "table" and t.Color then return t end end
local function ucut(s, n) return (W.UCut and W.UCut(s, n)) or (T.Cut and T.Cut(s, n, "")) or tostring(s or "") end
local ICON = "Interface\\Icons\\"

local J = {}
T.JournalPage = J
J.ICON = ICON .. "INV_Scroll_03"
J.GROUP_ICON = {
  failed = "Interface\\RaidFrame\\ReadyCheck-NotReady", blocked = ICON .. "INV_Misc_Key_14",
  dup = ICON .. "INV_Misc_Note_04", grey = "Interface\\GossipFrame\\IncompleteQuestIcon",
  dungeon = ICON .. "INV_Misc_Key_03", hard = ICON .. "Ability_Warrior_BattleShout", far = ICON .. "INV_Misc_Map_01",
}
local BTN_W, ROW_H = 104, 40
local BTN_LABEL = { go = "Abandonner", confirm = "Confirmer", combat = "Hors combat", guided = "Où cliquer ?" }
J.BTN_LABEL = BTN_LABEL

local function A() local m = T.Abandon if type(m) == "table" and m.CleanupNow then return m end end
local function factsText(it)
  local t = {}
  for _, x in ipairs(it and it.facts or {}) do t[#t + 1] = x.text end
  return tconcat(t, " · ")
end

-- résultat d'un clic : refus en combat = le message rouge du jeu ; le reste au pied de la fenêtre (jamais le chat)
local function say(st, msg)
  if not msg then return end
  if st == "refused" and inCombat() and T.CombatRefused then T:CombatRefused(msg) return end
  if W.Flash then W.Flash(msg, 12) elseif T.ClickNote then T:ClickNote(msg) end
end

-- ===================================================================== une ligne : quête + faits + bouton
local rowPool
local function jrowPool()
  if rowPool then return rowPool end
  rowPool = W._pool("jrow", function(parent)
    local f = W._newButton(parent)          -- survol (liseré) ; pas de clic sur la ligne : seul le bouton agit
    f.t = W._text(f, "M", "text")
    meth(f.t, "SetPoint", "TOPLEFT", f, "TOPLEFT", 10, -5)
    meth(f.t, "SetWordWrap", false)
    f.s = W._text(f, "S", "muted")
    meth(f.s, "SetPoint", "TOPLEFT", f.t, "BOTTOMLEFT", 0, -3)
    meth(f.s, "SetWordWrap", false)
    local th = Th()
    local b = th and th.Button and th:Button(f, "", BTN_W, 22) or W._newButton(f)
    if not b.label then b.label = W._text(b, "S", "text"); meth(b.label, "SetPoint", "CENTER", b, "CENTER", 0, 0) end
    meth(b, "SetSize", BTN_W, 22)
    meth(b, "SetPoint", "RIGHT", f, "RIGHT", -8, 0)
    meth(b, "SetScript", "OnClick", function(self)
      local Ab = A()
      if not (Ab and self.qid) then return end
      T:UserAction(function() say(Ab.Request(self.qid, self.qtitle)) end)
      W.Render()
    end)
    meth(b, "SetScript", "OnEnter", function(self)
      W.Tooltip(self, BTN_LABEL[self.state] or "Abandonner", nil, self.tip)
    end)
    meth(b, "SetScript", "OnLeave", W.HideTooltip)
    f.btn = b
    meth(f, "SetScript", "OnEnter", function(self) W.Tooltip(self, self.titleText, self.tipL) end)
    meth(f, "SetScript", "OnLeave", W.HideTooltip)
    return f
  end)
  return rowPool
end

local BTN_TIP = {
  go = "Clic : demande confirmation. 2e clic (« Confirmer ») : la quête quitte ton journal. Hors combat, une quête par clic.",
  confirm = "Clic : abandonner cette quête maintenant (pas de retour).",
  combat = "En combat : l'abandon attend la fin du combat.",
  guided = "Clic : où cliquer dans ton journal (L) pour la retirer toi-même.",
}
local function jrow(Ly, it, gr)
  local Ab = A()
  local p = jrowPool()
  local f = W._acquire(p, W.GetWindow().content)
  local q = it.q
  local st = Ab and Ab.ButtonState(q.id) or "guided"
  local tw = Ly.col - BTN_W - 28
  local pl = tonumber(try(G.UnitLevel, "player")) or 1
  local th = Th()
  local r, g, b = 1, 0.82, 0
  if th and th.QuestDiffColor then r, g, b = th:QuestDiffColor(q.level, pl) end
  local title = ((tonumber(q.level) or 0) > 0 and ("[" .. q.level .. "] ") or "") .. strip(q.title)
  local sub = factsText(it)
  if st == "confirm" then
    title = "Confirmer l'abandon de « " .. strip(q.title) .. " » ?"
    sub = "Clic sur « Confirmer » : elle quitte ton journal (pas de retour)."
    r, g, b = W._tok("prio3", 1, 0.45, 0.3)
  end
  meth(f.t, "SetWidth", tw); meth(f.s, "SetWidth", tw)
  meth(f.t, "SetText", ucut(safe(title), floor(tw / 6.2)))
  meth(f.t, "SetTextColor", r, g, b)
  meth(f.s, "SetText", ucut(safe(sub), floor(tw / 5.4)))
  meth(f, "SetSize", Ly.col, ROW_H)
  W._color(f.bg, W._tok("button", 0.14, 0.15, 0.19, st == "confirm" and 0.85 or 0.55))
  if st == "confirm" then W._border(f, W._tok("prio3", 1, 0.45, 0.3, 0.9)) else W._border(f, 0, 0, 0, 0) end
  local btn = f.btn
  btn.qid, btn.qtitle, btn.state, btn.tip = q.id, q.title, st, BTN_TIP[st]
  if btn.SetLabel then btn:SetLabel(BTN_LABEL[st]) else meth(btn.label, "SetText", BTN_LABEL[st]) end
  if st == "combat" then meth(btn, "Disable") else meth(btn, "Enable") end
  f.titleText = strip(q.title)
  f.tipL = { (gr and gr.title or "") .. " : " .. sub, gr and gr.advice or nil }
  Ly.at(f, 16, Ly.y)
  Ly.rec("jrow", 16, Ly.y, Ly.col, ROW_H, title)
  Ly.y = Ly.y + ROW_H + 2
  return f
end
J.Row = jrow

-- ===================================================================== page
local function heroSub(cl)
  if cl.count == 0 then return "Rien à nettoyer" end
  local parts = {}
  for _, gr in ipairs(cl.groups) do
    if #parts < 3 then parts[#parts + 1] = fmt("%d %s", #gr.items, gr.title:lower()) end
  end
  return fmt("%d à nettoyer : %s%s", cl.count, tconcat(parts, ", "), #cl.groups > 3 and "…" or "")
end

local function render(Ly)
  local Ab = A()
  if not Ab then Ly.empty("Le module « Nettoyer mon journal » est absent.") return end
  local cl, ctx = Ab.CleanupNow()
  local n, cap = tonumber(ctx.logCount) or 0, tonumber(ctx.logMax) or 40
  local direct = Ab.CanDirect()
  local how = direct and "Abandon en 2 clics, hors combat (ta sélection du journal est remise aussitôt)"
    or ((Ab.db and Ab.db.refused) and "Le jeu a refusé l'abandon direct : « Où cliquer ? » te guide dans ton journal"
      or "Abandon direct coupé : « Où cliquer ? » te guide dans ton journal (L)")
  Ly.hero(J.ICON, fmt("Journal : %d/%d quêtes", n, cap), heroSub(cl), how, nil,
    { "Seulement des faits du jeu : grise, échouée, chaîne bloquée, doublon, donjon déjà fait, trop dure pour ton groupe,",
      "autre continent ou trajet mesuré. Jamais une durée estimée." })
  if not Ab._active and Ab.db and Ab.db.enabled == false then
    Ly.row({ text = "Rappel « journal chargé » coupé (Fonctions) : clic pour l'allumer.", kind = "info",
      onClick = function() T:SetModuleEnabled("Abandon", true) end, clickHint = "Clic : allumer « Nettoyer mon journal »" })
  end
  if inCombat() then Ly.text("En combat : l'abandon attend la fin du combat.", "S", "prio3") end
  local shown = 0
  for _, gr in ipairs(cl.groups) do
    local list = {}
    for _, it in ipairs(gr.items) do
      if W.Match(it.q.title, factsText(it), gr.title) then list[#list + 1] = it end
    end
    if #list > 0 then
      if Ly.header(gr.title, "j:" .. gr.key, #list, J.GROUP_ICON[gr.key]) then
        Ly.text(gr.advice, "S", "muted", 4)
        for _, it in ipairs(list) do jrow(Ly, it, gr) shown = shown + 1 end
      else
        shown = shown + #list
      end
    end
  end
  if shown == 0 then
    Ly.empty(W.Query() ~= "" and ("Rien ne correspond à « " .. W.Query() .. " ».")
      or (n == 0 and "Ton journal est vide." or "Rien à nettoyer : chaque quête de ton journal a encore une raison d'y être."))
  end
end

local function batchAction()
  local Ab = A()
  if not (Ab and Ab.CanDirect()) then return nil end
  local list = Ab.BatchItems("grey")
  local b = Ab.BatchState()
  local mine = b and b.kind == "grey"
  if #list < 2 and not mine then return nil end
  local fn = function() say(Ab.BatchRequest("grey")) end
  if inCombat() then
    return "Hors combat", function() say("refused", "Abandon : hors combat seulement.") end, "En combat : l'abandon attend la fin du combat."
  end
  if mine and not b.confirmed then
    return fmt("Confirmer : %d grise%s ?", #b.items, #b.items > 1 and "s" or ""), fn,
      "Clic : confirmer. Ensuite une quête grise par clic, jusqu'à la dernière."
  end
  if mine then
    local nxt
    for k = b.i + 1, #b.items do if not (Ab.gone and Ab.gone[b.items[k].id]) then nxt = b.items[k] break end end
    if nxt then
      return "Suivante : « " .. ucut(strip(nxt.title), 12) .. " »", fn, "Clic : abandonner « " .. strip(nxt.title) .. " »"
    end
  end
  return fmt("Tout ce qui est gris (%d)", #list), fn,
    "Retirer toutes les quêtes grises : une confirmation, puis une quête retirée par clic (hors combat)."
end

W.AddPage({ id = "journal", label = "Journal", icon = J.ICON, title = "Nettoyer mon journal",
  desc = "Les quêtes à retirer, avec la raison du jeu ; abandon en 2 clics",
  sub = function()
    local Ab = A()
    local cl = Ab and Ab.cleanup and Ab.Visible and Ab.Visible(Ab.cleanup)
    if cl and cl.count > 0 then return fmt("%d quête%s à retirer, avec la raison du jeu", cl.count, cl.count > 1 and "s" or "") end
    return "Les quêtes qui ne te servent plus, avec la raison du jeu"
  end,
  badge = function()
    local Ab = A()
    local ctx = Ab and Ab.ctx
    local cl = Ab and Ab.cleanup and Ab.Visible and Ab.Visible(Ab.cleanup)
    if not (ctx and cl and cl.strong > 0) then return nil end
    return ((tonumber(ctx.logMax) or 40) - (tonumber(ctx.logCount) or 0) <= 3) and cl.strong or nil   -- presque plein
  end,
  render = render,
  action = batchAction,
  status = function()
    local Ab = A()
    local ctx = Ab and Ab.ctx or {}
    return fmt("Journal : %d/%d", tonumber(ctx.logCount) or 0, tonumber(ctx.logMax) or 40)
  end,
})
-- (4.1 LAYOUT) sous-page de « Quêtes » (W.PARENT) : plus dans la navigation ; ordre gardé pour les anciennes fenêtres
do
  local pages, at, from = W.PAGES, nil, nil
  for i, p in ipairs(pages) do
    if p.id == "quests" then at = i end
    if p.id == "journal" then from = i end
  end
  if at and from and from ~= at + 1 then
    local p = table.remove(pages, from)
    if from < at then at = at - 1 end
    table.insert(pages, at + 1, p)
  end
end

-- ===================================================================== section de la page « Quêtes »
--- « Nettoyer mon journal » sur la page Quêtes : les 3 premières quêtes à retirer, clic = la page « Journal »
function J.QuestsSection(Ly)
  local Ab = A()
  if not Ab then return false end
  local cl = Ab.CleanupNow()
  if not cl or cl.count == 0 then return false end
  -- (4.1 LAYOUT) « Ménage du journal » : le compte + UN clic vers la page (rien d'autre à l'écran)
  local hdr = Ly.header("Ménage du journal", "q:clean", cl.count, "Interface\\GossipFrame\\IncompleteQuestIcon", true)
  Ly.buttons({ { fmt("Faire le ménage (%d)", cl.count), function() W.Select("journal") end,
    "Clic : la page « Nettoyer mon journal » (la raison du jeu pour chaque quête, abandon en 2 clics)" } }, 2)
  if hdr then
    local shown = 0
    for _, gr in ipairs(cl.groups) do
      for _, it in ipairs(gr.items) do
        if shown < 3 and W.Match(it.q.title) then
          Ly.quest(it.q.level, it.q.title, gr.title .. " · " .. it.facts[1].text, function() W.Select("journal") end,
            { gr.advice }, "Clic : la page « Journal » (abandon en 2 clics)")
          shown = shown + 1
        end
      end
    end
    if cl.count > shown then
      Ly.text(fmt("… et %d autre%s : page « Journal ».", cl.count - shown, cl.count - shown > 1 and "s" or ""), "S", "muted")
    end
  end
  return true
end
