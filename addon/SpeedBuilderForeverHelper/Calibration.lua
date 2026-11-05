local columns, rows = 32, 19
local interval = 0.8
local marker = CreateFrame("Frame", nil, UIParent)
local preview = CreateFrame("Frame", nil, UIParent)
marker:SetFrameStrata("TOOLTIP")
preview:SetFrameStrata("TOOLTIP")
marker:SetFrameLevel(10)
local icon = preview:CreateTexture(nil, "ARTWORK")
icon:SetAllPoints()
marker:Hide()
preview:Hide()

local cells = {}
local entries, current, elapsed
local function newSession()
    return math.random(1, 65535) * 65536 + math.random(0, 65535)
end
local session = newSession()
local bounds, spec, primary
local lastChatState, lastGeometry
local idleElapsed, completed = 0, 0
local reloadPending
local reloadRemaining = 2
local layoutWidth, layoutHeight
local chatPrefixes = {
    ["/sbf"] = 6, ["/sbf reload"] = 12,
}

local function say(message)
    print("SpeedBuilderForever: " .. message)
end

local function stop(message)
    entries = nil
    lastChatState = nil
    completed = 0
    marker:Hide()
    preview:Hide()
    if message then say(message) end
end

local function uint(value, count)
    local result = ""
    for i = 1, count do
        result = string.char(value % 256) .. result
        value = math.floor(value / 256)
    end
    return result
end

local function emit(index, count, action, key, geometry, name)
    -- Index zero carries chat state. Other packets carry an icon and its suggested key.
    name = name or ""
    local endByte = 41
    -- Trim long names without splitting a UTF-8 character.
    while name:byte(endByte) and name:byte(endByte) >= 128 and name:byte(endByte) < 192 do
        endByte = endByte - 1
    end
    name = name:sub(1, endByte - 1)
    local payload = "SBF2" .. uint(session, 4) .. string.char(index, count)
        .. uint(action, 4) .. key .. string.rep("\0", 12 - #key) .. geometry
        .. name .. string.rep("\0", 40 - #name)
    local a, b = 0, 0
    for i = 1, #payload do
        a = (a + payload:byte(i)) % 255
        b = (b + a) % 255
    end
    payload = payload .. string.char(b, a)

    for i = 1, #cells do
        local byte = payload:byte(math.floor((i - 1) / 8) + 1)
        local bit = math.floor(byte / 2 ^ (7 - (i - 1) % 8)) % 2
        cells[i]:SetColorTexture(bit, bit, bit, 1)
    end
end

local function draw(entry)
    icon:SetTexture(nil)
    icon:SetTexture(entry.texture)
    if not icon:GetTexture() then
        stop("Could not display " .. entry.name .. ". Calibration stopped.")
        return
    end
    emit(current, #entries, entry.id, entry.key, bounds, entry.name)
end

local function layoutMarker()
    local width, height = UIParent:GetSize()
    if width == layoutWidth and height == layoutHeight then return end
    layoutWidth, layoutHeight = width, height
    local cellWidth, cellHeight = width / 320, height / 180
    marker:ClearAllPoints()
    marker:SetPoint("TOPLEFT", UIParent, "TOPLEFT", cellWidth, -cellHeight)
    marker:SetSize(columns * cellWidth, rows * cellHeight)
    for i = 1, columns * rows do
        local cell = cells[i] or marker:CreateTexture(nil, "ARTWORK")
        cell:ClearAllPoints()
        cell:SetPoint("TOPLEFT", marker, "TOPLEFT",
            ((i - 1) % columns) * cellWidth, -math.floor((i - 1) / columns) * cellHeight)
        cell:SetSize(cellWidth, cellHeight)
        cells[i] = cell
    end
end

local function locate(button)
    local width, height = UIParent:GetSize()
    local scale = button:GetEffectiveScale() / UIParent:GetEffectiveScale()
    local left, bottom, iconWidth, iconHeight = button:GetRect()
    if not left or width <= 0 or height <= 0 then return end
    local x0, y0 = left * scale / width, 1 - (bottom + iconHeight) * scale / height
    local x1, y1 = (left + iconWidth) * scale / width, 1 - bottom * scale / height
    if x0 < 0 or y0 < 0 or x1 > 1 or y1 > 1 or x1 <= x0 or y1 <= y0
        or (x0 < (columns + 1) / 320 and y0 < (rows + 1) / 180) then
        return
    end
    return uint(math.floor(x0 * 65535), 2) .. uint(math.floor(y0 * 65535), 2)
        .. uint(math.floor(x1 * 65535), 2) .. uint(math.floor(y1 * 65535), 2)
end

local function bindingFor(action, config, overrides)
    local option = overrides and rawget(overrides, action)
    if option and option.keybind and option.keybind ~= "" then
        return "!" .. option.keybind
    end

    local key = Hekili:GetBindingForAction(action, config, 1)
    local detected = Hekili.KeybindInfo[action]
    if not detected or key == "" then return "" end

    -- A fixed profile cannot follow different bindings on different bar pages.
    for _, candidate in pairs(detected.upper) do
        if candidate:upper() ~= key:upper() then return "" end
    end
    return key
end

local function buildEntries(config)
    local catalog = {}
    local registry = Hekili.Engine.Registry
    local kit = registry.Kit()
    local abilityOpts = Hekili:GetActiveSpecOption("abilities")
    local itemOpts = Hekili:GetActiveSpecOption("items")
    for action, ability in pairs(registry.Abilities()) do
        -- Hekili's GCD tracker has a real spell ID and is always "known", but
        -- is not a player action (Forever exposes it as TEST Scorch).
        if type(action) == "string" and action == ability.key and action ~= "global_cooldown"
            and not ability.unlisted and not ability.passive then
            -- Class actions are registered once with their base spell ID; rank
            -- aliases point back to the same action and must not be previewed again.
            -- Shared actions (racials and items) still have to belong to this character.
            local classAbility = kit and ability.from == kit.id and not ability.item
            if classAbility or Hekili.State:IsKnown(action) then
                local id = ability.item or ability.id
                if type(id) == "number" and id > 0 and id < 2147483648 then
                    local override = abilityOpts and rawget(abilityOpts, action)
                    local texture
                    if ability.item then
                        texture = ability.texture or select(5, C_Item.GetItemInfoInstant(id))
                        id = id + 2147483648
                    else
                        texture = override and override.icon or ability.texture or C_Spell.GetSpellTexture(id)
                    end
                    if texture then
                        local key = bindingFor(action, config, ability.item and itemOpts or abilityOpts)
                        if #key > 12 or key:find("[^ -~]") then key = "" end
                        catalog[#catalog + 1] = { action = action, id = id, texture = texture,
                            name = ability.name or action, key = key }
                    end
                end
            end
        end
    end
    table.sort(catalog, function(a, b) return a.action < b.action end)
    return catalog
end

local function primaryIcon()
    local display = Hekili and Hekili.DisplayPool and Hekili.DisplayPool.Primary
    local config = display and Hekili.DB.profile.displays.Primary
    local button = display and display.Buttons and display.Buttons[1]
    if config and config.style == "classic" and button and button.texCoords then
        return button, config
    end
end

local function start()
    if InCombatLockdown() then
        say("Run /sbf out of combat.")
        return
    end
    local engine = Hekili and Hekili.Engine
    local button, config = primaryIcon()
    if not (engine and engine.Registry and engine.Registry.Kit and engine.Output and Hekili.KeybindInfo and button) then
        say("Enable Hekili Forever's Classic Primary display before running /sbf.")
        return
    end
    if C_AddOns.IsAddOnLoaded("Masque") then
        say("Disable Masque for calibration. Skinned icons are not supported by this preview.")
        return
    end
    bounds = locate(button)
    if not bounds then
        say("Move the Hekili Primary icon fully into the game window, clear of the upper-left corner.")
        return
    end

    local catalog = buildEntries(config)
    if #catalog == 0 or #catalog > 255 then
        say("No supported calibration list for this spec (" .. #catalog .. " entries).")
        return
    end

    layoutMarker()

    preview:ClearAllPoints()
    preview:SetAllPoints(button)
    icon:SetTexCoord(unpack(button.texCoords))
    entries, current, elapsed = catalog, 1, 0
    session = newSession()
    spec, primary = engine.Output.status.specKey, button
    draw(entries[current])
    if entries then
        preview:Show()
        marker:Show()
        say("Calibrating " .. #catalog .. " icons. Keep WoW focused. /sbf stops calibration.")
    end
end

local function chatState()
    if not Hekili or not Hekili.PLAYER_ENTERING_WORLD or InCombatLockdown()
        or not (GetCurrentKeyBoardFocus and ChatFrameUtil and ChatFrameUtil.GetActiveWindow)
        or (GameMenuFrame and GameMenuFrame:IsShown())
        or GetBindingAction("ENTER", true) ~= "OPENCHAT" then
        return
    end
    local focus = GetCurrentKeyBoardFocus()
    local chat = ChatFrameUtil.GetActiveWindow()
    if chat and focus == chat and chat:HasFocus() then
        local text = chat:GetText()
        if not issecretvalue or not issecretvalue(text) then return chatPrefixes[text] end
    end
end

local driver = CreateFrame("Frame")
driver:SetScript("OnUpdate", function(_, delta)
    if not entries then
        if reloadPending and Hekili and Hekili.PLAYER_ENTERING_WORLD and not InCombatLockdown() then
            reloadRemaining = reloadRemaining - delta
            if reloadRemaining <= 0 then reloadPending = nil end
        end
        if completed > 0 then
            completed = completed - delta
            if completed > 0 then return end
            marker:Hide()
        end
        idleElapsed = idleElapsed + delta
        if idleElapsed < 0.05 then return end
        idleElapsed = 0
        local state = chatState()
        if reloadPending and Hekili and Hekili.PLAYER_ENTERING_WORLD and not InCombatLockdown() then
            state = 1
        end
        local button = state and primaryIcon()
        local geometry = button and locate(button)
        if state ~= lastChatState or geometry ~= lastGeometry then
            lastChatState, lastGeometry = state, geometry
            if geometry then
                layoutMarker()
                emit(0, 0, state, "setup", geometry)
                marker:Show()
            else
                marker:Hide()
            end
        end
        return
    end
    elapsed = elapsed + delta
    if elapsed < interval then return end
    if Hekili.Engine.Output.status.specKey ~= spec
        or primaryIcon() ~= primary or locate(primary) ~= bounds then
        stop("The spec or icon position changed. Start a new batch.")
        return
    end
    elapsed = 0
    current = current + 1
    if current > #entries then
        stop("Icon preview complete. Check the result in SpeedBuilder.")
        emit(0, 1, 1, "", bounds)
        marker:Show()
        completed = interval
    else
        draw(entries[current])
    end
end)

for _, event in ipairs({ "ADDON_LOADED", "PLAYER_REGEN_DISABLED", "PLAYER_ENTERING_WORLD", "UI_SCALE_CHANGED",
    "DISPLAY_SIZE_CHANGED", "UPDATE_BINDINGS", "SPELLS_CHANGED", "ACTIONBAR_SLOT_CHANGED",
    "ACTIONBAR_PAGE_CHANGED", "UPDATE_BONUS_ACTIONBAR", "UPDATE_SHAPESHIFT_FORM" }) do
    driver:RegisterEvent(event)
end
driver:SetScript("OnEvent", function(_, event, addon)
    if event == "ADDON_LOADED" then
        if addon == "SpeedBuilderForeverHelper" then
            reloadPending = SpeedBuilderForeverReloadPending
            SpeedBuilderForeverReloadPending = nil
        end
        return
    end
    if entries then stop("Game state changed. Start a new batch when ready.") end
    completed = 0
    marker:Hide()
    lastChatState = nil
    if event == "PLAYER_REGEN_DISABLED" then reloadPending = nil end
end)

SLASH_SPEEDBUILDERFOREVER1 = "/sbf"
SlashCmdList.SPEEDBUILDERFOREVER = function(command)
    if command == "reload" then
        if InCombatLockdown() then
            say("Reload the helper out of combat.")
            return
        end
        SpeedBuilderForeverReloadPending = true
        ReloadUI()
        return
    end
    if command ~= "" then
        say("Use /sbf to calibrate or /sbf reload to reload the helper.")
        return
    end
    if entries then
        stop("Calibration cancelled.")
        return
    end
    local ok, problem = pcall(start)
    if not ok then stop("Calibration could not start: " .. tostring(problem)) end
end
