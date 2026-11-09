-- TreeAI Lite · Chasseur — déclencheur personnalisé (TSU) : munitions + humeur du familier.
-- (Hunter.lua de TreeAI : slot 0 = munitions, 18 = arme à distance ; humeur 1 = mécontent.)
function(allstates, event)
  local E = aura_env
  local function S(v) local f = issecretvalue if v ~= nil and type(f) == "function" and f(v) then return nil end return v end
  local warn = 200
  local lines = {}
  -- munitions : arme à distance qui en tire (arc/arbalète = flèches, arme à feu = balles)
  local ranged = GetInventoryItemID and S(GetInventoryItemID("player", 18))
  local info = C_Item and C_Item.GetItemInfoInstant or GetItemInfoInstant
  if ranged and info then
    local _, _, _, _, _, cls, sub = info(ranged)
    local want = (cls == 2 and (sub == 2 or sub == 18)) and 2 or ((cls == 2 and sub == 3) and 3 or nil)
    if want then
      local word = want == 2 and "flèches" or "balles"
      local ammo = GetInventoryItemID and S(GetInventoryItemID("player", 0))
      local n = ammo and GetInventoryItemCount and S(GetInventoryItemCount("player", 0))
      if not ammo then
        lines[#lines + 1] = "|cffff5555Munitions : emplacement vide|r (équipe tes " .. word .. ")"
      elseif not n then -- valeur secrète / inconnue : on ne dit rien
      elseif n < 50 then
        lines[#lines + 1] = ("|cffff5555Munitions : %d %s !|r Rachète vite"):format(n, word)
      elseif n < warn then
        lines[#lines + 1] = ("|cffffcc00Munitions basses : %d %s|r"):format(n, word)
      end
    end
  end
  -- familier : humeur
  local PI = C_PetInfo
  local happy = (PI and PI.GetPetHappiness) or GetPetHappiness
  if UnitExists and UnitExists("pet") and not (UnitIsDead and UnitIsDead("pet")) and happy then
    local h = S(happy())
    if type(h) == "number" and h < 3 then
      local diet = ""
      local ft = (PI and PI.GetPetFoodTypes) or GetPetFoodTypes
      if ft then
        local t = { ft() }
        if type(t[1]) == "table" then t = t[1] end
        local names = {}
        for i = 1, math.min(#t, 3) do if type(S(t[i])) == "string" then names[#names + 1] = t[i] end end
        if #names > 0 then diet = " (" .. table.concat(names, ", ") .. ")" end
      end
      if h == 1 then
        lines[#lines + 1] = "|cffff5555Familier mécontent|r : nourris-le" .. diet
      else
        lines[#lines + 1] = "|cffffcc00Familier content|r : nourris-le pour qu'il soit heureux" .. diet
      end
    end
  end
  local txt = #lines > 0 and table.concat(lines, "\n") or nil
  local s = allstates[""]
  if not txt then
    if s and s.show then s.show, s.changed = false, true return true end
    return false
  end
  if s and s.show and s.name == txt then return false end
  allstates[""] = { show = true, changed = true, name = txt }
  return true
end
