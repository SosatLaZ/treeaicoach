-- Harness scenario (4.2, agent FIX42), run by tests/wow/test_fix42.py on forever + modern (HUNTER). Proves, with what
-- the player sees dumped as « FIX42 … » lines:
--   1. hunter auto-shot bar: the duration comes from PLAYER_SWING (type 2), readable in combat (never the 2.0 s fallback
--      once a real value was seen); our bar stays silent when the game's own swing bars are on (CVar showSwingTimer);
--      the extra helpers (trap / Feign Death bar, « ennemi qui fuit ») are off by default; the flee alert works when on;
--   2. purchases / crafts / withdrawals go through T:ShopCall: refused outside a click and in combat; one click = one
--      purchase; « mode sûr » = nothing bought, the item shown in the merchant window; a block right after a TreeAI
--      purchase switches the safe mode on by itself; the self-check (/tai bloque achats) lines;
--   3. « Composants de métier »: nothing while questing (no NPC, no session); at a merchant: one line per profession
--      with plan quantities and their source; « Apprends X chez ton maître » / « Apprendre X (dès N) » wording;
--   4. the strip's « Métiers » button (professions only): click = Personnage → Métiers, right click = session;
--   5. sell only the surplus: « garde 12, vends 18 » (KeepOf), the AH never lists / flips a needed item; bank
--      « Sortir à vendre » moves only surplus stacks, one per click; the mailbox card warns about cash on delivery;
--   6. scheduler: deferred jobs measured as heavy (≥ 0.1 ms) never two in one frame (the rest in the next frames);
--   7. Conseils pro lists the hunter helpers (OFF) and their macros; nothing in the chat, zero blocked action.
-- Fake names only.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local function out(s) io.write("FIX42 ", s, "\n") end
  local chat0, v0 = #M.output, #M.violations
  local function blocked() return #(M.blocked or {}) end
  local b0 = blocked()
  M.LeaveCombat()

  -- ------------------------------------------------------------------ 1. hunter auto-shot bar
  local H = T.modules.Hunter
  check(H and H.OnSwing and H.ShotState, "Hunter loaded with PLAYER_SWING support")
  if H and H.OnSwing then
    check(H.db.trapBar == false and H.db.fdTimer == false and H.db.fleeAlert == false, "extra hunter helpers off by default")
    M.EnterCombat()
    M.FireEvent("START_AUTOREPEAT_SPELL")
    M.FireEvent("PLAYER_SWING", 2.83, 2)
    local last, speed, read = H.ShotState()
    check(last and math.abs(speed - 2.83) < 1e-6 and math.abs(read - 2.83) < 1e-6, "in combat: shot duration from PLAYER_SWING (" .. tostring(speed) .. ")")
    M.FireEvent("PLAYER_SWING", 3.1, 0)   -- main hand: not the ranged bar
    local _, s2 = H.ShotState()
    check(math.abs(s2 - 2.83) < 1e-6, "PLAYER_SWING type 0 (main hand) ignored")
    M.Advance(0.5, 0.1)
    M.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-auto", 75)   -- the spell event right after: no 2.0 s fallback
    local _, s3 = H.ShotState()
    check(math.abs(s3 - 2.83) < 1e-6, "auto-shot event keeps the last readable speed (" .. tostring(s3) .. ")")
    M.cvars.showSwingTimer = "1"
    check(H.NativeSwing(), "native swing bars detected (CVar showSwingTimer)")
    out(("tir auto : %.2f s (PLAYER_SWING), barres du jeu : %s"):format(s3, H.NativeSwing() and "oui (la nôtre se tait)" or "non"))
    M.cvars.showSwingTimer = "0"
    M.FireEvent("STOP_AUTOREPEAT_SPELL")
    -- flee alert: off = nothing; on = one line in « Maintenant »
    local function fleeLine()
      for _, e in ipairs(T:GetSuggestions()) do if tostring(e.id) == "Hunter:flee" then return e end end
    end
    M.target = { exists = true, name = "Gnoll-test", level = 28, health = 100, healthMax = 900, hostile = true }
    M.FireEvent("CHAT_MSG_MONSTER_EMOTE", "%s tente de fuir, pris de panique !", "Gnoll-test")
    check(not fleeLine(), "flee alert off by default")
    H.db.fleeAlert = true
    M.FireEvent("CHAT_MSG_MONSTER_EMOTE", "%s tente de fuir, pris de panique !", "Gnoll-test")
    local fl = fleeLine()
    check(fl and fl.text:find("Gnoll%-test") and fl.text:find("s'enfuit"), "flee alert when on: " .. tostring(fl and fl.text))
    out("fuite : " .. tostring(fl and fl.text))
    H.db.fleeAlert = false
    M.LeaveCombat()
    M.Advance(1, 0.2)
  end

  -- ------------------------------------------------------------------ 2. purchases: one path, clicks only, safe mode
  check(T.ShopCall and T.ShopCheck and T.SetShopSafe, "Core.ShopCall + self-check")
  local calls = 0
  local fn = function() calls = calls + 1 end
  local ok, why = T:ShopCall("BuyMerchantItem", fn)
  check(not ok and why == "noclick" and calls == 0, "outside a click: refused (" .. tostring(why) .. ")")
  M.EnterCombat()
  ok, why = M.Hw(function() local r1, r2 T:UserAction(function() r1, r2 = T:ShopCall("BuyMerchantItem", fn) end) return r1, r2 end)
  check(not ok and why == "combat" and calls == 0, "in combat: refused (" .. tostring(why) .. ")")
  M.LeaveCombat()
  ok = M.Hw(function() local r T:UserAction(function() r = T:ShopCall("BuyMerchantItem", fn) end) return r end)
  check(ok and calls == 1, "in a click, out of combat: called once")
  T:SetShopSafe(true)
  ok, why = M.Hw(function() local r1, r2 T:UserAction(function() r1, r2 = T:ShopCall("BuyMerchantItem", fn) end) return r1, r2 end)
  check(not ok and why == "safe" and calls == 1, "safe mode: nothing called")
  T:SetShopSafe(false)
  -- a block reported right after a TreeAI purchase: safe mode on by itself
  M.Hw(function() T:UserAction(function() T:ShopCall("BuyMerchantItem", fn) end) end)
  T:ADDON_ACTION_BLOCKED("ADDON_ACTION_BLOCKED", "TreeAI", "UseAction")
  check(T:ShopSafe(), "block right after a TreeAI purchase → safe mode")
  for _, l in ipairs(T:ShopCheck()) do out("autocontrôle : " .. l) end
  check(table.concat(T:ShopCheck(), " "):find("mode sûr allumé", 1, true), "self-check names the block and the safe mode")
  T:SetShopSafe(false)
  T:ResetBlocked()
  M.output = {}   -- (the simulated block above printed its one-line notice: not part of this check)
  chat0 = 0

  -- ------------------------------------------------------------------ 3. « Composants de métier » + 5. keep / sell
  local P = T.modules.Prof
  local PL = P and P.Level
  check(PL and PL.ComponentLines and PL.KeepOf and PL.NpcModel, "ProfLevel: components + keep + NPC card")
  M.player.level = 29
  M.skills = { { name = "Travail du cuir", rank = 143, max = 150 }, { name = "Dépeçage", rank = 140, max = 150 } }
  for id, t in pairs({ [2319] = "Cuir moyen", [4234] = "Cuir lourd", [2321] = "Bobine de fil raffiné", [4289] = "Sel" }) do
    M.AddItem(id, { name = t, stack = 20, sellPrice = 50, classID = 7 })
  end
  M.SetBagItem(0, 8, 2319, 6)
  P.Update(true)
  M.Advance(1, 0.2)
  check(#PL.ComponentLines() == 0, "questing (no NPC, no session): no « Composants de métier » line")
  local card = PL.GetCard()
  check(not (card and card:IsShown()), "no card on screen while questing")
  M.units.npc = { name = "Fournisseur-Test" }
  M.merchantItems = { { id = 2321, price = 100, stack = 1, maxStack = 20 }, { id = 4289, price = 50, stack = 1, maxStack = 20 } }
  M.SetMerchant(true)
  M.Advance(1, 0.2)
  local lines = PL.ComponentLines()
  for _, l in ipairs(lines) do out("composants : " .. l) end
  check(#lines >= 1 and lines[1]:find("^Travail du cuir %d+ → %d+ : "), "at a merchant: one line per profession: " .. tostring(lines[1]))
  local joined = table.concat(lines, "\n")
  check(joined:find("%((marchand)%)") or joined:find("%(HV%)") or joined:find("%(à farmer%)") or joined:find("rien à acheter"),
    "quantities with their source (marchand / HV / à farmer)")
  check(not joined:find("Apprendre [^\n]- à %d+"), "never « Apprendre X à 67 »")
  for _, l in ipairs(lines) do
    if l:find("Apprendre") then check(l:find("%(dès %d+%)"), "« Apprendre X (dès N) » only when later: " .. l) end
  end
  card = PL.GetCard()
  check(card and card:IsShown(), "merchant: one small card under the merchant window")
  out("carte marchand : " .. tostring(card and card.title and card.title:GetText()) .. " | " .. tostring(card and card.body and card.body:GetText()))
  M.SetMerchant(false)
  M.Advance(1, 0.2)
  check(#PL.ComponentLines() == 0, "merchant closed: the lines go away")
  -- keep / sell: a verdict « needed 12 of 30 »
  local CR = T.CityRun
  local realV = CR.Verdicts
  CR.Verdicts = function() return { [4234] = { need = 12, have = 30, extra = 18, text = "Besoin : 12" } } end
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(6, 0.5)
  local kt = PL.KeepText(4234, 30)
  check(kt == "garde 12, vends 18", "keep text: " .. tostring(kt))
  out("garder / vendre : Cuir lourd — " .. tostring(kt))
  local A = T.modules.Auction
  if A and A.KeepOf then
    check(A.KeepOf(4234) == 12, "the AH reads the same keep count")
    A.deals = { { id = 4234, kind = "flip", buy = 100, count = 5, profit = 50 } }
    local plan = A.Plan()
    check(#plan == 0, "the AH never proposes to buy-to-resell a needed item")
  end
  -- bank « Sortir à vendre »: surplus only, one stack per click
  local realX = CR.ExtraList
  CR.ExtraList = function() return { { id = 4234, n = 18, bags = 0, sell = 50, value = 900, why = "Besoin : 12" } } end
  local BANK = M.BankBagIDs()[1]
  M.SetBankItem(BANK, 1, 4234, 20)   -- bigger than the surplus: never taken
  M.SetBankItem(BANK, 2, 4234, 10)
  M.withdrawn = {}
  M.SetBank(true)
  M.Advance(1, 0.2)
  local bl = CR.BankSellList()
  check(bl[1] and bl[1].id == 4234 and bl[1].n == 18, "bank: 18 surplus listed")
  card = PL.GetCard()
  local outBtn
  for _, b in ipairs(card and card.more or {}) do if b:IsShown() and b.fn then outBtn = b end end
  if card and card.btn and card.label and card.label:find("^Sortir") then outBtn = card.btn end
  check(outBtn, "bank card: « Sortir à vendre (n) » button")
  out("carte banque : " .. tostring(card and card.body and card.body:GetText()))
  if outBtn then
    outBtn:Click()
    check(#M.withdrawn == 1 and M.withdrawn[1].count == 10, "one click = one surplus stack (10, never the 20)")
  end
  M.SetBank(false)
  M.Advance(1, 0.2)
  CR.Verdicts, CR.ExtraList = realV, realX
  -- mailbox: what waits + cash on delivery warning
  local realH = rawget(_G, "GetInboxHeaderInfo")
  rawset(_G, "GetInboxNumItems", function() return 2, 2 end)
  rawset(_G, "GetInboxHeaderInfo", function(i)
    if i == 1 then return nil, nil, "Amie", "Potions", 0, 0, 20, 1, false end
    return nil, nil, "Inconnu", "Arc rare", 0, 25000, 20, 1, false
  end)
  M.FireEvent("MAIL_SHOW")
  M.Advance(1, 0.2)
  local mm = PL.NpcModel()
  local mt = mm and table.concat(mm.lines, " | ") or ""
  check(mt:find("Courrier : 2 lettres") and mt:find("Contre%-remboursement : Arc rare"), "mailbox card: " .. mt)
  out("carte courrier : " .. mt)
  M.FireEvent("MAIL_CLOSED")
  rawset(_G, "GetInboxHeaderInfo", realH)
  M.Advance(1, 0.2)

  -- ------------------------------------------------------------------ 4. the strip's « Métiers » button
  local S = T.Shortcuts
  if S and S.State then
    S.Refresh(true)
    local dump = table.concat(S.Dump(), " · ")
    out("bande : " .. dump)
    check(dump:find("Métiers", 1, true), "strip: « Métiers » shown with a crafting profession")
    local W = T.Window
    S.Do("metiers", "LeftButton")
    check(W and W.IsOpen and W.IsOpen() and W.CurrentPage and (W.CurrentPage() == "prof" or W.CurrentPage() == "perso"),
      "click: Personnage → Métiers (" .. tostring(W and W.CurrentPage and W.CurrentPage()) .. ")")
    if W and W.Close then W.Close() end
    S.Do("metiers", "RightButton")
    check(PL.focus ~= nil, "right click: the profession session starts")
    S.Do("metiers", "RightButton")
    check(PL.focus == nil, "right click again: the session stops")
  end

  -- ------------------------------------------------------------------ 6. scheduler: heavy deferred jobs spread
  -- (the mock's clock gives every job a 0-ms cost: here the core's jobs are told they cost 1 ms each, as measured in game)
  local ran = {}
  local cost0 = T._onceCost[T]
  T._onceCost[T] = 1.0
  for i = 1, 5 do T:After(0.3, function() T._onceCost[T] = 2.0 ran[#ran + 1] = { i = i, t = M.now } end) end
  M.Advance(0.32, 0.02)
  local firstT = ran[1] and ran[1].t
  local inFirst = 0
  for _, r in ipairs(ran) do if r.t == firstT then inFirst = inFirst + 1 end end
  M.Advance(0.2, 0.02)
  T._onceCost[T] = cost0
  check(#ran == 5 and inFirst == 1, ("5 heavy deferred jobs (1 ms each): %d in the first frame, all done (%d)"):format(inFirst, #ran))
  out(("ordonnanceur : 5 rafraîchissements de 1 ms dus ensemble → %d dans la 1re image, le reste aux suivantes"):format(inFirst))

  -- ------------------------------------------------------------------ 7. Conseils pro lists the hunter helpers
  local Co = T.modules.Coach
  if Co and Co.RenderHunterHelpers then
    local rows = {}
    local Ly = { header = function() return true end, row = function(e) rows[#rows + 1] = e.text .. " [" .. tostring(e.right) .. "]" end }
    Co.RenderHunterHelpers(Ly, T.Window)
    local txt = table.concat(rows, "\n")
    check(txt:find("Feindre la mort", 1, true) and txt:find("fuit", 1, true) and txt:find("TAI FD Piege", 1, true), "Conseils pro: hunter helpers + macros")
    check(not txt:find("Feindre la mort[^\n]*%[ON%]"), "helpers OFF by default")
    out("conseils pro : " .. #rows .. " lignes chasseur")
  end

  -- ------------------------------------------------------------------ safety
  local said = {}
  for i = chat0 + 1, #M.output do
    local l = M.output[i]
    if not l:find("^%[UIError%]") and not l:find("^%[RaidNotice%]") then said[#said + 1] = l end
  end
  check(#said == 0, "nothing in the chat: " .. table.concat(said, " / "))
  check(blocked() == b0, "zero blocked action")
  check(#M.violations == v0, "zero violation")
end
