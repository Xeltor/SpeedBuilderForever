-- Run from the repository root: lua tests/helper_catalog.lua
-- Reuse the game API fixture and verify its lifecycle checks as well.
dofile('tests/helper_lifecycle.lua')

local bolt = {key = 'lightning_bolt', id = 403, from = 100070, texture = 1, name = 'Lightning Bolt', copy = {529, 548}}
local abilities = {
    lightning_bolt = bolt, [403] = bolt, [529] = bolt, lightning_bolt_rank_2 = bolt,
    chain_lightning = {key = 'chain_lightning', id = 421, from = 100070, texture = 2, name = 'Chain Lightning'},
    stormstrike = {key = 'stormstrike', id = 17364, from = 100070, texture = 3, name = 'Stormstrike'},
    lookalike = {key = 'lookalike', id = 999, from = 100070, texture = 1, name = 'Different Spell With Same Icon'},
    stoneform = {key = 'stoneform', id = 20594, from = 0, texture = 4, name = 'Stoneform'},
    other_racial = {key = 'other_racial', id = 20572, from = 0, texture = 5, name = 'Other Race', unlisted = true},
    equipped_item = {key = 'equipped_item', item = 123, from = 0, texture = 6, name = 'Equipped Item'},
    absent_item = {key = 'absent_item', item = 456, from = 0, texture = 7, name = 'Absent Item'},
    shared_unavailable = {key = 'shared_unavailable', id = 1000, from = 0, texture = 8, name = 'Unavailable Shared Spell'},
    other_class = {key = 'other_class', id = 1001, from = 100010, texture = 9, name = 'Other Class'},
    global_cooldown = {key = 'global_cooldown', id = 29515, from = 0, texture = 10, name = 'TEST Scorch'},
    internal = {key = 'internal', id = 1002, from = 100070, texture = 11, name = 'Internal', unlisted = true},
    passive = {key = 'passive', id = 1003, from = 100070, texture = 12, name = 'Passive', passive = true},
}
local known = {lightning_bolt = true, stoneform = true, equipped_item = true, global_cooldown = true, internal = true, passive = true}
local keys = {lightning_bolt = '2', stoneform = '9', equipped_item = 'C4'}
Hekili.Engine.Registry.Abilities = function() return abilities end
Hekili.State.IsKnown = function(_, action) return known[action] or false end
Hekili.GetBindingForAction = function(_, action) return keys[action] or '' end
Hekili.KeybindInfo = {
    lightning_bolt = {upper = {'2'}}, stoneform = {upper = {'9'}}, equipped_item = {upper = {'C4'}},
}
frames = {}
dofile('addon/SpeedBuilderForeverHelper/Calibration.lua')
local marker, driver = frames[1], frames[4]
driver.scripts.OnEvent(driver, 'ADDON_LOADED', 'SpeedBuilderForeverHelper')

local function packet()
    local bytes = {}
    for offset = 1, #marker.textures, 8 do
        local byte = 0
        for bit = 0, 7 do byte = byte * 2 + marker.textures[offset + bit].red end
        bytes[#bytes + 1] = string.char(byte)
    end
    return table.concat(bytes)
end
local function uint(bytes)
    local result = 0
    for i = 1, #bytes do result = result * 256 + bytes:byte(i) end
    return result
end
local function capture()
    SlashCmdList.SPEEDBUILDERFOREVER('')
    local result = {}
    for index = 1, 6 do
        local data = packet()
        assert(data:byte(9) == index and data:byte(10) == 6, 'Wrong count: ranks or unrelated abilities imported')
        local id = uint(data:sub(11, 14))
        assert(not result[id], 'Spell ranks were imported more than once')
        result[id] = {key = data:sub(15, 26):gsub('%z+$', ''), name = data:sub(35, 74):gsub('%z+$', '')}
        driver.scripts.OnUpdate(driver, .81)
    end
    assert(not frames[2].visible, 'Unexpected additional abilities')
    driver.scripts.OnUpdate(driver, .81)
    assert(not marker.visible, 'Marker stayed visible after the full-class preview')
    return result
end
local before = capture()
assert(before[403].key == '2' and before[403].name == 'Lightning Bolt', 'Known spell or key lost')
assert(before[421].key == '' and before[17364].key == '', 'Future spells should not get invented bindings')
assert(before[999], 'Distinct actions sharing an icon must remain separate for collision checks')
assert(before[20594].key == '9', 'Own racial missing')
assert(before[2147483648 + 123].key == 'C4', 'Available item missing')
-- Learning a higher rank changes availability, not the canonical spell identity.
known.chain_lightning = true
known.lightning_bolt = true
local after = capture()
for id, entry in pairs(before) do
    assert(after[id] and after[id].name == entry.name and after[id].key == entry.key,
        'Learning a rank changed the catalog identity or binding')
end
print('Full-class catalog and rank deduplication checks passed.')
