-- Scenario MACRO2 (« revois les macros, parfaites ») — run on forever × HUNTER / WARRIOR / PRIEST / MAGE.
--   • the class spells of the macro base are seeded (French client names) → « Installer tout » (one click)
--     creates the advised set: every body ≤ 255 bytes, valid, no token left, no duplicate, nothing in chat
--   • Hunter: Auto Shot always « !Tir automatique », aspects with « ! », Feign Death + trap without sequence
--   • « Glisser sur la barre » puts the next macro not on a bar on the cursor; a list row can be dragged
--   • « Diagnostic » shows ✓ / ✗ + reason for each macro in the panel (not in chat)
--   • the macro sheet is stacked by one engine: no overlap, never under the button grid
--   • bag change in combat → nothing written; out of combat → re-rendered (or « clique pour mettre à jour »)
--   • druid forms are remapped to the real stance-bar index
return function(M, ctx)
  local T = TreeAI
  local Mc = T.modules and T.modules.Macros
  local D = T.Data and T.Data.Macros
  ctx.check(Mc and D and Mc.PickToBar and Mc.PanelRows and Mc.RemapForms, "Macros module (MACRO2 API)")
  if not (Mc and D) then return end
  M.LeaveCombat()
  local cls = T:PlayerClass()

  -- seed every spell of the class macro base as known (names = French client names of the base)
  for _, def in ipairs(D.classes[cls] or {}) do
    for _, v in ipairs(def.v or {}) do
      for sid in (v.b or ""):gmatch("{(%d+)}") do
        sid = tonumber(sid)
        if not M.spells[sid] and D.spells[sid] then
          M.spells[sid] = { name = D.spells[sid], rank = "", icon = 132000 + sid, castTime = 0, known = true }
        end
      end
    end
  end
  M.macros = { global = {}, char = {} }
  local c = Mc.char or {}
  c.installed, c.bodies, c.setup = {}, {}, false
  Mc:ClearBlocked(); Mc:ClearSpellCache()
  local out0 = #M.output

  Mc:OpenPanel()
  local P = Mc.panel
  ctx.check(P and P:IsShown(), "panel opens")
  if not P then return end
  P.all:Click()
  local all, seen, dup = {}, {}, false
  for _, l in ipairs({ M.macros.global, M.macros.char }) do
    for _, m in ipairs(l) do
      all[#all + 1] = m
      if seen[m.name] then dup = true end
      seen[m.name] = m
    end
  end
  ctx.check(#all >= 8, cls .. ": « Installer tout » creates the advised set (" .. #all .. ")")
  ctx.check(not dup, "no duplicate macro")
  local bad
  for _, m in ipairs(all) do
    local ok, why = Mc.ValidBody(m.body)
    if not ok or #m.body > 255 or m.body:find("[{}]") or m.name:sub(1, 4) ~= "TAI " then bad = m.name .. " " .. tostring(why) end
  end
  ctx.check(not bad, "every body valid and ≤ 255 bytes" .. (bad and (" — " .. bad) or ""))

  if cls == "HUNTER" then
    local tir = seen["TAI Tir"]
    ctx.check(tir and tir.body:find("/cast !Tir automatique", 1, true) and tir.body:find("/petattack", 1, true),
      "Tir: « !Tir automatique » never toggles Auto Shot off + pet attack")
    local asp = seen["TAI Aspects"]
    ctx.check(asp and not asp.body:find("[^!]Aspect du") , "Aspects: every aspect cast with « ! »")
    local fd = seen["TAI FD Piege"]
    ctx.check(fd and fd.body:find("/cast [combat] Feindre la mort; Piège givrant", 1, true)
      and not fd.body:find("castsequence", 1, true), "Feign Death + trap: [combat] switch, no sequence")
    local pet = seen["TAI Familier"]
    ctx.check(pet and pet.body:find("[@pet,dead] Ressusciter le familier; [nopet] Appel du familier; Guérison du familier", 1, true),
      "pet: revive if dead, call if absent, mend otherwise " .. tostring(pet and pet.body))
  end

  -- « Glisser sur la barre » : next macro not on a bar → cursor; placing it moves on to the next one
  M.cursor = nil
  P.pick:Click()
  ctx.check(M.cursor and M.cursor.type == "macro", "« Glisser sur la barre » puts a macro on the cursor")
  local first = M.cursor and M.cursor.id
  if first then M.actions = M.actions or {}; M.actions[1] = { type = "macro", id = first }; M.cursor = nil end
  local nextDef, nextIdx = Mc:NextToPlace()
  ctx.check(nextDef and nextIdx ~= first, "the placed macro is skipped: next one proposed")
  -- drag a list row
  local row
  for _, r in ipairs(P.rows) do if r.def and Mc.MacroIndex(r.def.n) then row = r; break end end
  M.cursor = nil
  if row then M.Hw(function() M.Script(row, "OnDragStart") end) end
  ctx.check(row and M.cursor and M.cursor.type == "macro", "dragging a list row picks the macro up")
  M.cursor = nil

  -- diagnostic in the panel: ✓ / ✗ + reason
  P.diag:Click()
  local d = Mc.lastDiag
  ctx.check(Mc._view == "diag" and d and #d.items > 0, "« Diagnostic » switches the sheet to the list")
  local txt = P.body:GetText() or ""
  ctx.check(txt:find("ReadyCheck%-Ready") and select(2, txt:gsub("\n", "")) + 1 == #d.items,
    "diagnostic: one ✓/✗ line per macro")
  local noReason
  for _, it in ipairs(d.items) do if type(it.reason) ~= "string" or it.reason == "" then noReason = it.n end end
  ctx.check(not noReason, "every diagnostic line has a reason")
  P.diag:Click()
  ctx.check(Mc._view == nil, "second click: back to the macro sheet")

  -- layout: stacked sheet, no overlap, above the button grid
  local rects, overlap, low = P._rects or {}, false, false
  for i = 2, #rects do if rects[i].y < rects[i - 1].y + rects[i - 1].h then overlap = true end end
  local last = rects[#rects]
  if last and last.y + last.h > 486 - P.contentBottom then low = true end
  ctx.check(#rects >= 6 and not overlap and not low, "sheet stacked without overlap, above the buttons")
  local headers = 0
  for _, r in ipairs(Mc:PanelRows(Mc.NewContext())) do if r.header then headers = headers + 1 end end
  ctx.check(headers >= 1, "list grouped under headers")

  -- no chat output from the macro module
  local chat = false
  for i = out0 + 1, #M.output do if tostring(M.output[i]):find("Macros", 1, true) then chat = true end end
  ctx.check(not chat, "nothing printed in chat")

  -- bag change in combat → no write; after combat → refreshed out of combat
  local writes = 0
  local edit = rawget(_G, "EditMacro")
  rawset(_G, "EditMacro", function(...) writes = writes + 1; return edit(...) end)
  M.EnterCombat()
  M.SetBagItem(2, 1, 1205, 5)   -- better drink (req 15)
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(3)
  ctx.check(writes == 0, "no macro written in combat")
  M.LeaveCombat()
  M.Advance(3)
  rawset(_G, "EditMacro", edit)

  -- druid forms: canonical [form:3] (cat) follows the real stance bar
  local map = { 1, 9, 2, 3, 9 }   -- no aquatic form: cat = 2, travel = 3
  ctx.check(Mc.RemapForms("/cast [form:3] X; [noform:1/3] Y", map) == "/cast [form:2] X; [noform:1/2] Y",
    "druid [form:N] remapped to the real stance index")
  P:Hide()
end
