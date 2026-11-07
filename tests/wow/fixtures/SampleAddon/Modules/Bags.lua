-- Bag scanning through C_Container when present, legacy globals otherwise.
local A = SampleAddon
local M = A:RegisterModule("Bags", {})

local function slots(bag)
  if C_Container and C_Container.GetContainerNumSlots then return C_Container.GetContainerNumSlots(bag) end
  return GetContainerNumSlots and GetContainerNumSlots(bag) or 0
end
local function info(bag, slot)
  if C_Container and C_Container.GetContainerItemInfo then
    local t = C_Container.GetContainerItemInfo(bag, slot)
    if t then return t.itemID, t.stackCount, t.quality end
    return nil
  end
  if GetContainerItemInfo then
    local _, count, _, quality, _, _, _, _, _, id = GetContainerItemInfo(bag, slot)
    return id, count, quality
  end
end

function M:Scan()
  local free, grey, food = 0, 0, nil
  for bag = 0, NUM_BAG_SLOTS or 4 do
    for slot = 1, slots(bag) do
      local id, count, quality = info(bag, slot)
      if not id then free = free + 1
      else
        if quality == 0 then grey = grey + 1 end
        local _, _, _, _, _, _, subType = GetItemInfo(id)
        if subType == "Nourriture et boissons" then food = food or id end
      end
    end
  end
  self.free, self.grey, self.food = free, grey, food
  if free < 3 then A:Suggest("bags", "Sacs presque pleins", 3) end
  return free, grey, food
end

function M:OnLogin() self:Scan() end
A:On("BAG_UPDATE_DELAYED", function() M:Scan() end)
A:On("MERCHANT_SHOW", function()
  if InCombatLockdown() then return end
  for bag = 0, NUM_BAG_SLOTS or 4 do
    for slot = 1, slots(bag) do
      local id, _, quality = info(bag, slot)
      if id and quality == 0 then
        local use = (C_Container and C_Container.UseContainerItem) or UseContainerItem
        use(bag, slot)
      end
    end
  end
end)
