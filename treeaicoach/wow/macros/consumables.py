"""Consumables knowledge base (Classic item IDs) for the auto-updated macros (Buffet-like).

Each item: (item_id, req, value, tag). ``req`` = required character level (First Aid skill for
bandages), ``value`` = restore amount used to rank (higher = better), ``tag`` = diet for food
(pet feeding: meat/fish/bread/cheese/fruit/fungus), "conj" for conjured, ammo type for ammo.
In game the addon picks, among the items present in the bags, the best usable one (conjured
first at equal tier, since it is free); unknown food/drink is detected by its Food/Drink spell.
"""
from __future__ import annotations

FOOD = [
    # bread
    (4540, 1, 61, "bread"), (4541, 5, 243, "bread"), (4542, 15, 552, "bread"),
    (4544, 25, 874, "bread"), (4601, 35, 1392, "bread"), (8950, 45, 2148, "bread"),
    # meat
    (117, 1, 61, "meat"), (2287, 5, 243, "meat"), (3770, 15, 552, "meat"),
    (3771, 25, 874, "meat"), (4599, 35, 1392, "meat"), (8952, 45, 2148, "meat"),
    # fruit
    (4536, 1, 61, "fruit"), (4537, 5, 243, "fruit"), (4538, 15, 552, "fruit"),
    (4539, 25, 874, "fruit"), (4602, 35, 1392, "fruit"), (8953, 45, 2148, "fruit"),
    # cheese
    (2070, 1, 61, "cheese"), (414, 5, 243, "cheese"), (422, 15, 552, "cheese"),
    (1707, 25, 874, "cheese"), (3927, 35, 1392, "cheese"), (8932, 45, 2148, "cheese"),
    # fish
    (787, 1, 61, "fish"), (4592, 5, 243, "fish"), (4593, 15, 552, "fish"),
    (4594, 25, 874, "fish"), (21552, 35, 1392, "fish"), (8957, 45, 2148, "fish"),
    # fungus
    (4604, 1, 61, "fungus"), (4605, 5, 243, "fungus"), (4606, 15, 552, "fungus"),
    (4607, 25, 874, "fungus"), (4608, 35, 1392, "fungus"), (8948, 45, 2148, "fungus"),
    # conjured (mage)
    (5349, 1, 61, "conj"), (1113, 5, 243, "conj"), (1114, 15, 552, "conj"),
    (1487, 25, 874, "conj"), (8075, 35, 1392, "conj"), (8076, 45, 2148, "conj"),
    (22895, 55, 3180, "conj"),
]

WATER = [
    (159, 1, 151, ""), (1179, 5, 436, ""), (1205, 15, 835, ""), (1708, 25, 1344, ""),
    (1645, 35, 1992, ""), (8766, 45, 2934, ""),
    (5350, 1, 151, "conj"), (2288, 5, 436, "conj"), (2136, 15, 835, "conj"),
    (3772, 25, 1344, "conj"), (8077, 35, 1992, "conj"), (8078, 45, 2934, "conj"),
    (8079, 55, 4200, "conj"),
]

HEALING_POTION = [
    (118, 1, 80, ""), (858, 3, 160, ""), (4596, 5, 160, ""), (929, 12, 320, ""),
    (1710, 21, 520, ""), (3928, 35, 800, ""), (13446, 45, 1400, ""),
]

MANA_POTION = [
    (2455, 5, 160, ""), (3385, 14, 320, ""), (3827, 22, 520, ""), (6149, 31, 800, ""),
    (13443, 41, 1100, ""), (13444, 49, 1500, ""),
]

HEALTHSTONE = [   # req = required level of the stone (ItemSparse.RequiredLevel), not the warlock's
    (5512, 1, 100, ""), (19004, 1, 110, ""), (19005, 1, 120, ""),
    (5511, 12, 250, ""), (19006, 12, 275, ""), (19007, 12, 300, ""),
    (5509, 24, 500, ""), (19008, 24, 550, ""), (19009, 24, 600, ""),
    (5510, 36, 800, ""), (19010, 36, 880, ""), (19011, 36, 960, ""),
    (9421, 48, 1200, ""), (19012, 48, 1320, ""), (19013, 48, 1440, ""),
]

# req = First Aid skill needed to USE the bandage (ItemSparse.RequiredSkillRank), not to craft it
BANDAGE = [
    (1251, 1, 66, ""), (2581, 20, 114, ""), (3530, 50, 161, ""), (3531, 75, 301, ""),
    (6450, 100, 400, ""), (6451, 125, 640, ""), (8544, 150, 800, ""), (8545, 175, 1104, ""),
    (14529, 200, 1360, ""), (14530, 225, 2000, ""),
]

AMMO = [
    (2512, 1, 1, "arrow"), (2515, 10, 2, "arrow"), (3030, 25, 3, "arrow"),
    (11285, 40, 4, "arrow"), (18042, 52, 5, "arrow"),
    (2516, 1, 1, "bullet"), (2519, 10, 2, "bullet"), (3033, 25, 3, "bullet"),
    (11284, 40, 4, "bullet"), (10512, 37, 4, "bullet"), (10513, 44, 5, "bullet"),
    (15997, 52, 6, "bullet"),
]

MANA_GEM = [(5514, 23, 400, ""), (5513, 38, 600, ""), (8007, 48, 900, ""), (8008, 58, 1200, "")]

# Rogue poisons: instant (main hand) and deadly (off hand), crippling as a fallback.
POISON_MH = [
    (6947, 20, 1, ""), (6949, 28, 2, ""), (6950, 36, 3, ""), (8926, 44, 4, ""),
    (8927, 52, 5, ""), (8928, 60, 6, ""),
]
POISON_OH = [
    (2892, 30, 10, ""), (2893, 38, 11, ""), (8984, 46, 12, ""), (8985, 54, 13, ""),
    (20844, 60, 14, ""), (3775, 20, 1, ""),
]

HEARTHSTONE = [(6948, 1, 1, "")]

# Mounts: no fixed list (racial / faction / event mounts vary); chosen in game among the bag items whose
# GetItemInfo class/subclass is Miscellaneous/Mount and whose required level is met.
MOUNT: list[tuple[int, int, int, str]] = []

CATEGORIES = {
    "food": FOOD, "water": WATER, "hpot": HEALING_POTION, "mpot": MANA_POTION,
    "hs": HEALTHSTONE, "bandage": BANDAGE, "ammo": AMMO, "managem": MANA_GEM,
    "poison_mh": POISON_MH, "poison_oh": POISON_OH, "hearth": HEARTHSTONE, "mount": MOUNT,
    # pet food reuses FOOD (diet tags), chosen in game for the pet's diet and level
    "petfood": FOOD,
}
