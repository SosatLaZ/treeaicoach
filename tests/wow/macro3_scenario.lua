-- Scenario MACRO3 (« les macros auto : je ne vois pas si c'est actif ; plus de personnalisation ; l'aspect tout seul »)
-- Run on forever + modern × HUNTER (and forever × WARRIOR for the generic parts):
--   • « Macros » page of the TreeAI window: one badge per macro (vert actif · orange à mettre à jour · gris désactivé /
--     plus tard / modifiée · rouge erreur + raison), on a bar? key? auto + « mise à jour il y a … »
--   • « Tout mettre à jour » = one click (hardware event): every edit inside that click, nothing in combat, nothing
--     outside a click (the forever mock refuses it), zero blocked action
--   • personalisation per macro (modifiers, mouseover, pet commands, aspect guard, variant, tank, rename, icon,
--     auto on/off, enabled), presets Simple / Complet / Pro (two clicks), saved per character
--   • smart aspect: TAI Aspects = « [combat][harm,nodead] !Aspect du faucon; !Aspect du guépard » (Simple),
--     Marque puts the Hawk first; Raccourcis aspect button glows when the aspect is wrong (+ one sound only for the
--     real danger: Cheetah / Pack in combat — no sound « sans danger », ANNOY)
--     through our own casts (auras secret in combat on Forever)
--   • Pro « clic » macros: hidden secure button rewritten out of combat (rare spotted)
--   • Lua personalize() = Python generator.personalize() on the same inputs (tests/wow/test_macro3.py)
return function(M, ctx)
  local T = TreeAI
  local Mc, D, W = T.modules and T.modules.Macros, T.Data and T.Data.Macros, T.Window
  local check = ctx.check
  check(Mc and D and W and Mc.MacroState and Mc.UpdateAll and Mc.Personalize and Mc.ApplyPreset, "MACRO3 API")
  if not (Mc and D and W) then return end
  local hunter = T:PlayerClass() == "HUNTER"
  local function hc(cond, msg) if hunter then check(cond, msg) end end
  M.LeaveCombat()
  local blocked0, out0 = #M.blocked, #M.output

  -- ---------------------------------------------------------------- personalize parity (same cases as Python)
  local P = Mc.Personalize
  check(P("#showtooltip\n/cast ?[mod:shift] {2643}; [@mouseover,harm,nodead][] {3044}\n?/cast !{13165}\n/cast !{75}\n"
    .. "/petattack [harm,nodead,nomod:alt]\n/stopmacro [mod:shift]", { mods = false, mo = false })
    == "#showtooltip\n/cast {3044}\n?/cast !{13165}\n/cast !{75}\n/petattack [harm,nodead]", "personalize: mods + mouseover off")
  check(P("#showtooltip\n/cast ?[mod:shift] {2643}; [@mouseover,harm,nodead][] {3044}\n?/cast !{13165}\n/cast !{75}\n"
    .. "/petattack [harm,nodead,nomod:alt]\n/stopmacro [mod:shift]", { pet = false, guard = false })
    == "#showtooltip\n/cast ?[mod:shift] {2643}; [@mouseover,harm,nodead][] {3044}\n/cast !{75}\n/stopmacro [mod:shift]",
    "personalize: pet + guard off")
  check(P("/assist [@mouseover,help,nodead][help,nodead][@party1,exists,nodead]", { tank = 3, mo = false })
    == "/assist [help,nodead][@party3,exists,nodead]", "personalize: tank 3, no mouseover")
  check(P("#showtooltip {136}\n/petattack [nomod,@mouseover,harm,nodead][nomod,harm,nodead]\n/cast [mod:alt,@pet,exists,nodead] {136}",
    { mods = false }) == "/petattack [@mouseover,harm,nodead][harm,nodead]", "personalize: #showtooltip dropped without a cast")
  check(P("/cast [mod:shift][@pet,dead] {982}; [nopet] {883}; {136}", { mods = false })
    == "/cast [@pet,dead] {982}; [nopet] {883}; {136}", "personalize: one alternative group dropped")
  check(P("#showtooltip\n/use [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] {item:bandage}",
    { mods = false, mo = false }) == "#showtooltip\n/use [@target,help,nodead][@player] {item:bandage}", "personalize: friendly chain")
  check(P("/castsequence [nomod:shift] reset=target {1130}, null\n/cast [mod:shift] {2643}", { mods = false })
    == "/castsequence reset=target {1130}, null", "personalize: castsequence")

  -- ---------------------------------------------------------------- level-28 hunter (BM without the hawk talent)
  M.player.level = 28
  local KNOWN = hunter and { 75, 2973, 1494, 13163, 1978, 1130, 3044, 5116, 883, 1515, 13165, 6991, 19883, 2641, 982,
    2974, 136, 20736, 1513, 6197, 1002, 1495, 13795, 2643, 5118, 781, 1499, 3043, 1462, 3045, 13809, 20572 } or {}
  for _, sid in ipairs(KNOWN) do
    if D.spells[sid] then M.spells[sid] = { name = D.spells[sid], rank = "", icon = 132000 + sid, castTime = 0, known = true } end
  end
  if hunter then M.spells[5384] = nil end   -- Feindre la mort : niveau 30 (le mock le donne d'office)
  if not hunter then   -- other classes: every spell of their base
    for _, def in ipairs(D.classes[T:PlayerClass()] or {}) do
      for _, v in ipairs(def.v or {}) do
        for sid in (v.b or ""):gmatch("{(%d+)}") do
          sid = tonumber(sid)
          if not M.spells[sid] and D.spells[sid] then M.spells[sid] = { name = D.spells[sid], rank = "", icon = 132000 + sid, castTime = 0, known = true } end
        end
      end
    end
  end
  M.macros = { global = {}, char = {} }
  M.actions, M.bindings = {}, {}
  M.macroFail = nil
  local c = Mc.char
  c.installed, c.bodies, c.setup, c.opts, c.preset, c.stamps = {}, {}, false, {}, nil, {}
  Mc:ClearBlocked(); Mc:ClearSpellCache()
  local function def(name) for _, d in ipairs(Mc:Defs()) do if d.n == name then return d end end end
  local function body(name)
    local i = Mc.MacroIndex(name)
    local _, _, b = GetMacroInfo(i or 0)
    return b
  end
  local function state(name) local d = def(name) return d and Mc:MacroState(d) end
  local function click(fn) M.Hw(function() T:UserAction(fn) end) end

  -- ---------------------------------------------------------------- the page (TreeAI window)
  local ids = {}
  for i, p in ipairs(W.PAGES) do ids[p.id] = i end
  -- the harness storm may have folded/unfolded the page's sections: back to the defaults
  if W.char and W.char.collapsed then
    for _, k in ipairs({ "macros:list", "macros:off", "macros:set", "macros:more" }) do W.char.collapsed[k] = nil end
  end
  check(ids.macros and ids.tools and ids.modules and ids.tools < ids.macros and ids.macros < ids.modules,
    "« Macros » page registered between « Outils » and « Fonctions »")
  M.Hw(function() Mc:TogglePanel() end)
  check(W.IsOpen() and W.CurrentPage() == "macros", "« Macros » (minimap, Outils…) opens the page of the window")
  W.Render()
  local page = W.pageIndex.macros
  local label, fn = page.action()
  check(label and label:find("^Tout mettre à jour %(%d+%)") and fn, "page action: « Tout mettre à jour (N) » " .. tostring(label))
  local st0 = state(hunter and "TAI Tir" or "TAI Repas")
  check(st0 and st0.code == "install" and st0.badge == "warn", "before: orange « à installer » " .. tostring(st0 and st0.code))
  -- writes outside a click are refused by the addon (pending) — nothing is created
  check(Mc:Write(def(hunter and "TAI Tir" or "TAI Repas"), "/cast X") == "pending" and #M.macros.char == 0,
    "no click = nothing written (« clique pour mettre à jour »)")
  click(fn)
  local list, n = Mc:States()
  check(n.ok >= 8 and n.todo == 0, ("one click: %d active, %d still to do"):format(n.ok, n.todo))
  local bad
  for _, st in ipairs(list) do
    if st.on and st.code == "ok" and not (st.stamp and st.autoText and st.autoText:find("mise à jour il y a", 1, true)
        or st.def.c == "click") then bad = st.name end
  end
  check(not bad, "every active macro says « auto · mise à jour il y a … » " .. tostring(bad))
  W.Render()
  local rows = {}
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "row" then rows[#rows + 1] = r.label end end
  check(#rows >= 8 and (rows[1] or ""):find("active", 1, true), "page rows: badge + name + state: " .. tostring(rows[1]))
  check(page.action() == nil, "nothing to do: no action button")

  -- on a bar + key (slot 1 = ACTIONBUTTON1 = « 1 »)
  local main = hunter and "TAI Tir" or "TAI Repas"
  M.actions[1] = { type = "macro", id = Mc.MacroIndex(main), text = main }
  M.bindings.ACTIONBUTTON1 = "1"
  Mc.DirtyPlacement()
  local stm = state(main)
  check(stm.onBar and stm.key == "1", "on a bar with its key read: " .. tostring(stm.key))
  W.Render()
  local found
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "row" and r.label:find(main, 1, true) then found = r.label end end
  check(found and found:find("active", 1, true), "row of the macro on the bar: " .. tostring(found))

  -- ---------------------------------------------------------------- auto: better food -> orange, one click -> green
  if hunter then
    M.SetBagItem(2, 2, 4542, 6)   -- Pain aux 7 céréales... (bread req 15) : better than the current one
    M.FireEvent("BAG_UPDATE_DELAYED")
    M.Advance(3)
    local sr = state("TAI Repas")
    check(sr and sr.code == "update" and sr.text:find("meilleur objet", 1, true),
      "better food: TAI Repas orange « à mettre à jour (meilleur objet du sac) » " .. tostring(sr and sr.text))
    check(T._suggestions and T._suggestions["Macros:ready"], "one « Macros à mettre à jour : clic » line")
    W.Render()
    local l2, f2 = page.action()
    check(l2 and l2:find("%(1%)"), "action: « Tout mettre à jour (1) » " .. tostring(l2))
    click(f2)
    sr = state("TAI Repas")
    check(sr.code == "ok" and body("TAI Repas"):find("item:4542", 1, true), "after the click: green with the new food")
  end

  -- ---------------------------------------------------------------- grey / red badges
  local sd = def(hunter and "TAI Controle" or "TAI Repas")
  M.Hw(function() T:UserAction(function() Mc:SetOpt(sd, "on", false) end) end)
  local so = Mc:MacroState(sd)
  check(so.code == "off" and so.badge == "muted" and Mc.MacroIndex(Mc:GameName(sd)), "disabled: grey, the macro is NOT deleted")
  Mc:SetOpt(sd, "on", nil)
  if hunter then
    local i = Mc.MacroIndex("TAI Morsure")
    M.macros.char[i - 120].body = "/say perso"
    local su = state("TAI Morsure")
    check(su.code == "user" and su.badge == "muted", "edited by hand: grey « gardée telle quelle »")
    click(function() Mc:UpdateAll() end)
    check(body("TAI Morsure") == "/say perso", "« Tout mettre à jour » never overwrites a hand edit")
    local sl = state("TAI FD Piege")
    check(sl and sl.code == "later" and sl.text:find("30", 1, true), "Feign Death at 30: grey « au niveau 30 » "
      .. tostring(sl and sl.code) .. " " .. tostring(sl and sl.text))
  end
  -- the game refuses macro writes -> red with the reason
  M.macroFail = "forbidden"
  Mc:SetBlocked("test")
  local missing
  for _, st in ipairs((Mc:States())) do if st.on and not st.installed and st.expected and st.code ~= "empty" then missing = st end end
  if missing then
    check(missing.code == "blocked" and missing.badge == "bad" and missing.text:find("copier", 1, true),
      "blocked by the game: red « … copier-coller » (" .. missing.name .. ")")
  end
  M.macroFail = nil
  Mc:ClearBlocked()

  -- ---------------------------------------------------------------- personalisation (each change rewrites inside the click)
  if hunter then
    local tir = def("TAI Tir")
    check(body("TAI Tir"):find("[mod:shift] Flèches multiples", 1, true), "Complet: modifiers in TAI Tir")
    click(function() Mc:SetOpt(tir, "mods", false) Mc:ApplyNow(tir) end)
    check(not body("TAI Tir"):find("[mod:", 1, true) and state("TAI Tir").code == "ok", "modifiers off -> rewritten in the click")
    click(function() Mc:SetOpt(tir, "pet", false) Mc:ApplyNow(tir) end)
    check(not body("TAI Tir"):find("/petattack", 1, true), "pet commands off")
    local mq = def("TAI Marque")
    local mb = body("TAI Marque")
    check(mb:find("^#showtooltip Marque du chasseur\n/cast !Aspect du faucon\n/cast %[@mouseover,harm,nodead%]%[%] Marque du chasseur"),
      "TAI Marque: Hawk first (guard), then the mark on mouseover: " .. mb)
    click(function() Mc:SetOpt(mq, "mo", false) Mc:SetOpt(mq, "guard", false) Mc:ApplyNow(mq) end)
    mb = body("TAI Marque")
    check(not mb:find("@mouseover", 1, true) and not mb:find("Aspect", 1, true), "mouseover + aspect guard off: " .. mb)
    check(type(c.opts["TAI Marque"]) == "table" and c.opts["TAI Marque"].mo == false, "settings saved per character")
    -- rename (a click), then everything follows the new name
    click(function() Mc:Rename(tir, "TAI Tir 2") end)
    check(Mc.MacroIndex("TAI Tir 2") and not Mc.MacroIndex("TAI Tir") and Mc:GameName(tir) == "TAI Tir 2"
      and state("TAI Tir").code == "ok", "renamed: same macro, new name, still green")
    click(function() Mc:Rename(tir, "") end)
    check(Mc.MacroIndex("TAI Tir") and Mc:GameName(tir) == "TAI Tir", "empty name = original name back")
    -- smart aspect
    local asp = body("TAI Aspects")
    check(asp == "#showtooltip\n/cast [mod:shift] !Aspect du guépard; [mod:alt] !Aspect du singe; [combat][harm,nodead] !Aspect du faucon; !Aspect du guépard",
      "TAI Aspects (Complet, niv. 28): " .. tostring(asp))
  end

  -- ---------------------------------------------------------------- presets: two clicks, rewritten in the second
  click(function() Mc:ApplyPreset("simple") end)
  check(Mc:Preset() == "complet", "1st click on « Simple »: asks to confirm, nothing changed")
  W.Render()
  local conf
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "button" and r.label:find("Confirmer", 1, true) then conf = r.label end end
  check(conf, "the preset button says « Confirmer « Simple » »")
  click(function() Mc:ApplyPreset("simple") end)
  check(Mc:Preset() == "simple" and c.preset == "simple", "2nd click: preset Simple (saved per character)")
  if hunter then
    check(body("TAI Aspects") == "#showtooltip\n/cast [combat][harm,nodead] !Aspect du faucon; !Aspect du guépard",
      "Simple: the smart aspect macro " .. tostring(body("TAI Aspects")))
    check(c.opts["TAI Marque"] == nil or c.opts["TAI Marque"].mo == nil, "a preset resets the per-macro settings")
    check(Mc.MacroIndex("TAI Nourrir") and body("TAI Nourrir"):find("Nourrir le familier", 1, true), "Simple: TAI Nourrir installed")
    -- Pro
    click(function() Mc:ApplyPreset("pro") end)
    click(function() Mc:ApplyPreset("pro") end)
    check(Mc:Preset() == "pro", "preset Pro")
    local pt = body("TAI Pro Tir")
    check(pt and pt:find("/targetenemy [noharm][dead]", 1, true) and pt:find("reset=target Marque du chasseur, Morsure de serpent, null", 1, true)
      and pt:find("/cast !Tir automatique", 1, true) and #pt <= 255, "TAI Pro Tir: one-button pull " .. tostring(pt))
    check(state("TAI Tir").code == "off" and Mc.MacroIndex("TAI Tir"), "Pro: TAI Tir disabled, never deleted")
    local ptd = def("TAI Pro Tir")
    click(function() Mc:SetOpt(ptd, "var", 2) Mc:ApplyNow(ptd) end)
    check(body("TAI Pro Tir"):find("Marque du chasseur, null", 1, true), "variant chosen: « Marque, puis Arcanes »")
    local pa = def("TAI Pro Assist")
    if pa then
      click(function() Mc:SetOpt(pa, "on", true) Mc:UpdateOne(pa) Mc:SetOpt(pa, "tank", 3) Mc:ApplyNow(pa) end)
      check(body("TAI Pro Assist"):find("@party3", 1, true), "tank = groupe 3")
    end
    -- Pro « clic »: the hidden secure button follows the rare spotted (out of combat, no EditMacro)
    check(Mc.MacroIndex("TAI Pro Rare") and body("TAI Pro Rare") == "/click TreeAIMacro_rare", "TAI Pro Rare installed")
    local Dt = T.Detect
    local orig = Dt and Dt.Rares
    if Dt then Dt.Rares = function() return { { name = "Mufle-de-fer", kind = "rare" } } end end
    Mc:UpdateClickButtons()
    local rb = _G.TreeAIMacro_rare
    check(rb and rb.__protected and rb:GetAttribute("macrotext") == "/targetexact Mufle-de-fer"
      and rb:GetAttribute("useOnKeyDown") == false, "rare button: /targetexact <rare>, works with /click")
    M.EnterCombat()
    if Dt then Dt.Rares = function() return { { name = "Autre", kind = "rare" } } end end
    Mc:UpdateClickButtons()
    check(rb:GetAttribute("macrotext") == "/targetexact Mufle-de-fer", "in combat: nothing rewritten")
    M.LeaveCombat()
    if Dt then Dt.Rares = orig end
  end

  -- in combat: « Tout mettre à jour » refuses (nothing written)
  M.EnterCombat()
  local writes = #M.macros.char + #M.macros.global
  click(function() Mc:UpdateAll() end)
  check(#M.macros.char + #M.macros.global == writes, "in combat: nothing written")
  M.LeaveCombat()

  -- ---------------------------------------------------------------- Raccourcis: smart aspect button, glow, sound = danger only
  local Q = T.QuickFight
  if hunter and Q and Q.Plan then
    M.auras.player = { { name = "Aspect du guépard", spellId = 5118, duration = 0, expirationTime = 0, kind = "buff", source = "player" } }
    M.target = { exists = true, name = "Ours enragé", level = 27, health = 800, healthMax = 800, hostile = true,
      classification = "normal", creatureType = "Bête", guid = "Creature-0-0-0-0-2351-0001" }
    M.FireEvent("PLAYER_TARGET_CHANGED")
    M.player.health, M.player.power = 1000, 600
    T:ResetSoundGate()
    local s0 = #M.sounds
    Q:Refresh(); M.Advance(1)
    local QP = Q._lastPlan
    check(QP and QP.aspectWarn and QP.aspectWarn.level == "pull", "Cheetah + hostile target: « mauvais aspect avant le pull »")
    local ab = Q._buttons.aspect
    check(ab and ab:IsShown() and ab:GetAttribute("macrotext")
      == "/cast [mod:shift] !Aspect du guépard; [combat][harm,nodead] !Aspect du faucon; !Aspect du guépard",
      "aspect button = the smart macro: " .. tostring(ab and ab:GetAttribute("macrotext")))
    check(ab and ab._pulse and ab._pulse:IsShown(), "the aspect button glows")
    check(#M.sounds == s0, "before the pull: glow only, no sound (" .. (#M.sounds - s0) .. ")")
    check(ab.pip and ab.pip:IsShown(), "status pip on the Raccourcis button")
    -- Hawk cast out of combat -> the warning goes away
    M.auras.player = { { name = "Aspect du faucon", spellId = 13165, duration = 0, expirationTime = 0, kind = "buff", source = "player" } }
    Q:Refresh(); M.Advance(1)
    check(not Q._lastPlan.aspectWarn and not (ab._pulse and ab._pulse:IsShown()), "Hawk on: no glow")
    -- combat with SECRET auras: the aspect is followed through our own casts
    M.secretPlayer = true
    M.FireEvent("PLAYER_REGEN_DISABLED")   -- the real client locks down just after this event (combat buttons shown)
    M.EnterCombat()
    M.auras.player = { { name = "Aspect du guépard", spellId = 5118, duration = 0, expirationTime = 0, kind = "buff", source = "player" } }
    M.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-3-0-0-0-5118-1", 5118)
    M.FireEvent("UNIT_AURA", "player")
    M.Advance(1)
    Q:Refresh(); M.Advance(1)
    QP = Q._lastPlan
    check(QP.aspectWarn and QP.aspectWarn.level == "danger", "combat, secret auras, Cheetah cast: danger " .. tostring(QP.aspectWarn and QP.aspectWarn.level))
    check(ab._pulse and ab._pulse:IsShown(), "glows in combat (texture only)")
    check(Q._aspectSound == true and #M.sounds > s0, "danger (Cheetah in combat): one sound")
    local s1 = #M.sounds
    Q:Refresh(); M.Advance(1)
    check(#M.sounds == s1, "same episode: no second sound")
    M.auras.player = { { name = "Aspect du faucon", spellId = 13165, duration = 0, expirationTime = 0, kind = "buff", source = "player" } }
    M.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-3-0-0-0-13165-2", 13165)
    M.FireEvent("UNIT_AURA", "player")
    M.Advance(1)
    Q:Refresh(); M.Advance(1)
    check(not Q._lastPlan.aspectWarn, "Hawk cast in combat: warning gone")
    M.secretPlayer = nil
    M.LeaveCombat()
    M.target = { exists = false }
    M.FireEvent("PLAYER_TARGET_CHANGED")
    M.Advance(2)
  end

  -- ---------------------------------------------------------------- guarantees
  check(#M.blocked == blocked0, "zero blocked action (" .. (#M.blocked - blocked0) .. ")")
  local chat = false
  for i = out0 + 1, #M.output do if tostring(M.output[i]):find("Macros", 1, true) then chat = true end end
  check(not chat, "nothing printed in chat")
  for _, l in ipairs({ M.macros.global, M.macros.char }) do
    for _, m in ipairs(l) do
      local ok, why = Mc.ValidBody(m.body)
      if m.body ~= "/say perso" then check(ok and #m.body <= 255, m.name .. " valid ≤ 255 " .. tostring(why)) end
    end
  end
  c.opts, c.preset = {}, nil
  W.Close()
end
