# Darkhold Arena - Wave RPG (v38)

A wave-based arena RPG where you play as a dark sorcerer (or other champions) fighting through procedurally generated dungeon levels. Use fireballs, summon skeletal minions, and unlock powerful weapons like the Minigun and Arcane Railgun. Collect tokens from defeated enemies to purchase upgrades between waves. Walls are destructible, enemies grow stronger each wave, and the arena map is regenerated every level.

## Controls
- **WASD / Arrow Keys**: Move
- **Mouse**: Aim
- **Left Mouse Button**: Primary attack
- **Right Mouse Button**: Secondary attack
- **Space / Q**: Dash
- **1 / 2**: Swap weapons (Sorcerer only - Minigun / Railgun)
- **P**: Pause (3-second countdown to unpause)
- **Space / Enter**: Enter shop after wave clear
- **Click**: Interact with UI (character select, shop, game over)

## src/ Files

| File | Purpose |
|------|---------|
| `index.html` | Shell HTML that loads the stylesheet and all JS modules in dependency order |
| `style.css` | Dark-themed CSS for the game page and canvas |
| `config.js` | All game constants (dimensions, speeds, ranges, cooldowns), canvas/context setup, `ensureFocus()`, and global state variables (`gameState`, `wave`, `tokens`, `player`, arrays, etc.) |
| `entities.js` | Character definitions (`characters` object), enemy type definitions (`enemyTypes`), and all upgrade-related functions (`initUpgrades`, `hasMinigun`, `hasRailgun`, `getUpgradeCost`, `canBuyUpgrade`, `getPlayerStat`, vitality/skeleton speed multipliers) |
| `map.js` | Procedural map generation (`generateMap`, `ensureMapConnectivity`), wall/collision functions (`isWall`, `isWallCircle`, `getWallTileAt`, `damageWallTile`, `damageWallsInMeleeArc`, `resolveWallCollision`), pathfinding/steering (`hasLineOfSight`, `getSteeringDirection`), and utility functions (`mulberry32`, `getEdgeSpawnPosition`, `pointToSegmentDist`, `rectCircleCollision`) |
| `input.js` | All input state (`keys`, `mouseX/Y`, `mouseDown`, `mouseClicked`), keyboard/mouse/touch event listeners, and the main click handler for UI interactions (character select, shop purchases, weapon swap, game over restart) |
| `combat.js` | Projectile/particle/damage-number spawning, damage application (`damageEnemy`, `killEnemy`, `killMinionByTimeout`), AoE explosions, token collection/magnetism, and update functions for projectiles, particles, damage numbers, dropped tokens, bone shards, and beam effects |
| `enemies.js` | Enemy spawning (`spawnEnemy`, `spawnWave`, `getWaveSpawns`), minion spawning (`spawnMinion`, `spawnArcher`, `spawnMinigunSkeleton`, `spawnRailgunSkeleton`), enemy AI update (`updateEnemies`), minion AI update (`updateMinions`), and stuck detection (`updateStuckDetection`) |
| `player.js` | Player entity creation (`createPlayer`), all player stat getters (primary/secondary damage/cooldown/range/spec), primary and secondary attack logic, railgun/minigun fire functions, weapon swap (`performWeaponSwap`), dash, player update (`updatePlayer`), and game start/restart functions |
| `render.js` | All rendering: world tiles and walls, beams, tokens, projectiles, particles, damage numbers, bone shards, player, minions, enemies, HUD bottom bar, pause overlay, wave clear screen, shop screen, character select screen, and game over screen. Contains `toScreenX`/`toScreenY` (renamed from `screenX`/`screenY` to avoid Window property collision) |
| `update.js` | Camera system (`camera`, `updateCamera`, `updateMouseWorld`) and the main `update()` dispatcher that orchestrates all per-frame updates based on game state |
| `main.js` | Entry point: initializes upgrades and map, sets up the camera, and starts the `requestAnimationFrame` game loop |
