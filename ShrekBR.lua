local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Players = game:GetService("Players")
local localPlayer = Players.LocalPlayer
local Mouse = localPlayer:GetMouse()
local BulletWeapon = require(ReplicatedStorage.WeaponSystem.WeaponType.BulletWeapon)
local NetworkModule = require(ReplicatedStorage.Network)
 getgenv().MasterConfig = {
     SilentAim    =  true,
       InfiniteAmmo  =  true,
         RapidFire  =  true,
           Wallbang  =  true,
             Visuals  =  true}
local VECTOR_UP = Vector3.new(0, 1, 0)
local function getClosestPlayer()
    local closest = nil
    local shortestDist = math.huge
    local cam = workspace.CurrentCamera
    for _, player in pairs(Players:GetPlayers()) do
        if player ~= localPlayer and player.Character and player.Character:FindFirstChild("Head") then
            local pos, onScreen = cam:WorldToViewportPoint(player.Character.Head.Position)
            if onScreen then
                local dist = (Vector2.new(pos.X, pos.Y) - Vector2.new(Mouse.X, Mouse.Y)).Magnitude
                if dist < shortestDist then
                    closest = player
                    shortestDist = dist
                end
            end
        end
    end
    return closest
end
local oldCanShoot = BulletWeapon._canShoot
BulletWeapon._canShoot = function(self)
    if getgenv().MasterConfig.InfiniteAmmo then
        self.Ammo = self.AmmoCapacity or 30
        return true
    end
    return oldCanShoot(self)
end
local oldFireDeb = BulletWeapon._fireDeb
BulletWeapon._fireDeb = function(self)
    if getgenv().MasterConfig.RapidFire then
        self.FireDebounce = false
        return
    end
    return oldFireDeb(self)
end
local oldShoot = BulletWeapon._Shoot
BulletWeapon._Shoot = function(self)
    if not getgenv().MasterConfig.SilentAim then 
        return oldShoot(self) 
    end
    local target = getClosestPlayer()
    if target and target.Character and target.Character:FindFirstChild("Head") then
        local targetPart = target.Character.Head
        local weapon = self.Tool
        task.spawn(function()
            NetworkModule:FireServer("DamageReplication", weapon, targetPart, targetPart.Position)
        end)
        task.spawn(function()
            local visualData = {
                [1] = targetPart.Position,
                [2] = VECTOR_UP,
                [3] = targetPart,
                [4] = true
            }
            NetworkModule:FireServer("BulletReplication", weapon, visualData)
        end)
        if self.customFunctions and self.customFunctions.OnHit then
             NetworkModule:FireServer("OtherReplication", "OnHit", weapon, targetPart, targetPart.Position)
        end
        if getgenv().MasterConfig.Visuals then
            self:_startMuzzleFlash()
            self:_playSound("OnFire")
            self:_animationAction("OnFire", "player", "Play")
            self:_animationAction("OnFire", "weapon", "Play")
        end
        self.Ammo = self.Ammo - 1
    else
        return oldShoot(self)
    end
end
