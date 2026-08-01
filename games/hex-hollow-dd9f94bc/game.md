# Hex & Hollow

A first-person 3D dungeon-crawler built with three.js. The player starts in a torch-lit stone lobby with a shop counter, buys and upgrades weapons with coins, then steps through an archway into a dark arena to fight waves of ghosts, skeletons, zombies, and witches. Between waves the player returns to the lobby to spend coins before the next countdown begins.

## Controls

- **WASD** — move
- **Mouse** — look around (pointer lock)
- **Left-click** — attack with equipped weapon
- **Space** — dodge roll (invincibility frames)
- **Escape** — release pointer lock

## Weapons

Nine weapons from a rusty dagger to the chain-lightning Archmage Staff. Melee weapons swing in an arc; ranged weapons fire projectiles. Upgrades boost damage and speed (max +5). The shop also sells health potions.

## Enemies

- **Ghosts** — float and phase through pillars, fast but fragile
- **Skeletons** — medium speed and health
- **Zombies** — slow, tough, hard-hitting
- **Witches** — boss enemies with ranged magic bolts; appear every 5th wave

Enemies scale with wave number. Coin drops magnet toward the player.

## Audio

Procedural WebAudio — drone, combat sfx, coin pickups, countdown beeps. Toggle mute with the speaker button.

## File structure

| File | Purpose |
|---|---|
| `index.html` | Shell HTML with all UI overlays (HUD, shop, game-over, countdown, crosshair, mute button) and `<script>` tags in dependency order |
| `style.css` | All CSS: layout, HUD bars, shop panel, overlays, crosshair, mute button |
| `audio.js` | WebAudio system: `AudioContext`, master gain, tone/noise synthesis, all `sfx*()` helpers, ambient drone, mute toggle |
| `world.js` | Three.js scene setup (renderer, camera, lights, fog), procedural canvas textures and materials, lobby geometry (walls, floor, ceiling, counter, archway, torches), arena geometry (walls, floor, ceiling, columns, torches), player mesh (body, head, helm, weapon attachment point), weapon mesh constructors (`makeDaggerMesh` through `makeStaffMesh`), `setWeaponMesh()`, `createEnemyMesh()` for ghost/zombie/skeleton/witch |
| `state.js` | Weapon definitions array (`weaponDefs`), game state object (`gameState` — coins, HP, owned/equipped weapons, upgrades, wave, arena flags, cooldowns, dodge state), weapon stat helpers (`getEquippedWeapon`, `getUpgradeLevel`, `getUpgradeCost`, `getWeaponDamage`, `getWeaponSpeed`), player movement variables (`playerYaw`, `playerPitch`, `keys`, `playerVelocity`, `playerDirection`), `resetPlayerPosition()`, shop UI builder (`updateShopUI`), HUD updater (`updateHUD`), `showGameOver()` |
| `game.js` | Enemy bookkeeping arrays (`enemies`, `enemyMeshes`), `spawnEnemy()`, `damageEnemy()`, `killEnemy()` with coin drops and death particles, projectile pool and `spawnProjectile()`, pillar/wall collision helpers, wave management (`getEnemyCountForWave`, `clearArena`, `spawnWave`, `allEnemiesDead`, `returnToLobby`, `startArena`), input handlers (pointer lock, keydown/keyup, mousemove, mousedown), `performAttack()` (melee swing arc / ranged fire), countdown timer (`startCountdown`, `cancelCountdown`), main `update()` loop (torch flicker, particles, coin magnet, projectiles with chain-lightning, player movement/dodge/collision, enemy AI with pillar avoidance and witch magic attacks, wave-clear detection, camera follow), `animate()` render loop, `init()` bootstrap, all top-level DOM event listener wiring |
