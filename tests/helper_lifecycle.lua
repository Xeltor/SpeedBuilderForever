-- Run from the repository root: lua tests/helper_lifecycle.lua

frames = {}
function CreateFrame()
    local f = { scripts = {}, textures = {}, visible = true }
    setmetatable(f, { __index = function(_, name) return function() end end })
    function f:Hide() self.visible = false end
    function f:Show() self.visible = true end
    function f:SetScript(name, callback) self.scripts[name] = callback end
    function f:CreateTexture()
        local texture = CreateFrame()
        self.textures[#self.textures + 1] = texture
        return texture
    end
    function f:SetColorTexture(red) self.red = red end
    function f:GetTexture() return true end
    frames[#frames+1] = f
    return f
end
UIParent = { GetSize = function() return 2560, 1440 end, GetEffectiveScale = function() return 1 end }
button = { GetEffectiveScale = function() return 1 end, GetRect = function() return 870, 518, 60, 60 end, texCoords = {0,1,0,1} }
Hekili = {
    PLAYER_ENTERING_WORLD = true,
    DisplayPool = { Primary = { Buttons = {button} } },
    DB = { profile = { displays = { Primary = {style = 'classic'} } } },
    Engine = { Registry = { Kit = function() return {id = 100070} end, Abilities = function() return {
        bolt = {key = 'bolt', id = 403, texture = 1, name = 'Lightning Bolt'},
        global_cooldown = {key = 'global_cooldown', id = 29515, texture = 2, name = 'TEST Scorch', unlisted = true},
    } end }, Output = { status = {specKey = 1} } },
    State = { IsKnown = function() return true end }, KeybindInfo = {},
    GetActiveSpecOption = function() return {} end, GetBindingForAction = function() return '' end,
}
combat = false
function InCombatLockdown() return combat end
focus = nil
chat = { text = '', HasFocus = function() return true end, GetText = function(self) return self.text end }
function GetCurrentKeyBoardFocus() return focus end
ChatFrameUtil = { GetActiveWindow = function() return chat end }
function GetBindingAction() return 'OPENCHAT' end
C_AddOns = { IsAddOnLoaded = function() return false end }
C_Spell = { GetSpellTexture = function() return 1 end }
SlashCmdList = {}
unpack = table.unpack
reloaded = false
function ReloadUI() reloaded = true end

dofile('addon/SpeedBuilderForeverHelper/Calibration.lua')

marker, preview, driver = frames[1], frames[2], frames[4]
assert(driver.scripts.OnUpdate)
driver.scripts.OnEvent(driver, 'ADDON_LOADED', 'SpeedBuilderForeverHelper')
function tick(delta) driver.scripts.OnUpdate(driver, delta or .06) end
for i = 1, 200 do tick() end
assert(not marker.visible, 'idle marker is visible')
focus = chat
for _, text in ipairs({'', '/', '/s', '/reload', 'ordinary chat'}) do
    chat.text = text; tick(); assert(not marker.visible, 'non-setup text shows marker: '..text)
end
chat.text = '/sbf'; tick(); assert(marker.visible, 'setup command not acknowledged')
focus = nil; tick(); assert(not marker.visible, 'closing chat must hide marker')
SlashCmdList.SPEEDBUILDERFOREVER(''); assert(marker.visible and preview.visible, 'preview did not start')
tick(.81); assert(marker.visible and not preview.visible, 'completion packet missing')
tick(.81); assert(not marker.visible, 'completion did not hide marker')
for i=1,100 do tick() end; assert(not marker.visible, 'idle marker reappeared')
SlashCmdList.SPEEDBUILDERFOREVER(''); SlashCmdList.SPEEDBUILDERFOREVER('')
assert(not marker.visible and not preview.visible, 'cancel did not hide marker')
SlashCmdList.SPEEDBUILDERFOREVER('')
combat = true; driver.scripts.OnEvent(driver, 'PLAYER_REGEN_DISABLED')
assert(not marker.visible and not preview.visible, 'combat did not hide marker')
combat = false; tick(); assert(not marker.visible, 'marker returned after combat')
SlashCmdList.SPEEDBUILDERFOREVER('reload')
assert(reloaded and SpeedBuilderForeverReloadPending, 'setup reload not acknowledged')

frames = {}
SpeedBuilderForeverReloadPending = true
dofile('addon/SpeedBuilderForeverHelper/Calibration.lua')
local marker, driver = frames[1], frames[4]
assert(not marker.visible, 'reload marker shown before addon initialization')
driver.scripts.OnEvent(driver, 'ADDON_LOADED', 'OtherAddon')
assert(SpeedBuilderForeverReloadPending, 'another addon consumed reload flag')
driver.scripts.OnEvent(driver, 'ADDON_LOADED', 'SpeedBuilderForeverHelper')
assert(SpeedBuilderForeverReloadPending == nil, 'reload flag not consumed')
driver.scripts.OnUpdate(driver,.06)
assert(marker.visible, 'reload acknowledgement missing')
driver.scripts.OnEvent(driver, 'SPELLS_CHANGED')
driver.scripts.OnUpdate(driver,.06)
assert(marker.visible, 'startup events interrupted reload acknowledgement')
for i=1,100 do driver.scripts.OnUpdate(driver,.06) end
assert(not marker.visible, 'reload acknowledgement did not hide')
print('Helper lifecycle checks passed.')
