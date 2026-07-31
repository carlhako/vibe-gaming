# Tower Maze Defense

A browser-based tower defense game where you place towers on a grid to defend against waves of enemies traveling along a procedurally generated maze path. Features 10 tower types (attack, buff, economic, and special), branching/split paths, drag-to-build, and persistent seed-based map generation.

## Gameplay

Enemies spawn at the left edge of a 22×22 grid and follow a winding path to the right edge. The player places towers on buildable (non-path) cells to destroy enemies before they reach the end. Each enemy that escapes costs a life; the game ends when lives reach zero.

Towers auto-target enemies within range. Attack towers deal damage via projectiles, beams, area pulses, or chain lightning. Buff towers boost nearby attack towers' range, fire rate, or damage. Mint towers generate bonus income after each wave. Steam Roller towers spawn rolling units that travel backward along the path, crushing enemies on contact.

The path is generated from a seed (8 hex digits), producing a unique maze with optional loops and alternate split/branch paths. Players can share seeds or generate random ones.

Three difficulty levels (Easy/Medium/Hard) adjust enemy HP and income, plus a Sandbox mode that makes all purchases free for experimentation.

## Files

| File | Purpose |
|------|---------|
| `index.html` | HTML shell: canvas, overlays (difficulty picker, game-over), bottom dock with tower buttons, right sidebar with action bar, stats, info box, tower controls. References all sibling CSS/JS files. |
| `style.css` | All visual styling: layout, tower buttons, action bar, difficulty badges, overlays, info box, responsive breakpoints. |
| `config.js` | Game constants (`SIZE`, `CELL`, `CANVAS`), tower type definitions with per-level stats, enemy type definitions, difficulty settings, helper predicates (`isBuffTower`, `isAttackTower`, etc.), and shared grid math (`cellCx`, `cellCy`). Also declares the global `game` and `pendingSeed` variables. |
| `utils.js` | Pseudo-random number generator (`mulberry32`), seed conversion functions (`generateRandomSeed`, `seedToHex`, `hexToSeed`, `isValidHexFormat`). |
| `pathgen.js` | Procedural path/maze generation: base sine-wave path, loop insertion, split/branch paths, validation. Exports `generatePath` and `validatePathData`. |
| `entities.js` | Entity factory functions: `createGame` (fresh state object), `createTower`, `spawnEnemy`, `spawnMinionAt`, `spawnSteamRoller`, `spawnParticles`, `generateWave`, `addMoney`. |
| `combat.js` | Combat subsystem: `recordDamage`, `computeTotalDps`, `computeTowerBuffs`, `applyEffectiveStats`, `countAffectedTowers`, `applyDamage`, `checkEnemyDeaths`. |
| `render.js` | All canvas drawing: grid, path lines (main and branch), direction arrows, split indicators, tower models (10 per-tower-type draw functions), steam roller entities, enemies with HP bars, projectiles, beam effects, lightning arcs, particles, pulse effects, money notes, range circles, stun visuals. Exports `render()`. |
| `ui.js` | DOM element references, UI update functions: `deselectTowerType`, `updateUI`, `updateUpgradeBtnState`, `showEnemyInfo`, `getTowerBuffDetails`, `showInfo`, `refreshSelectedInfo`, seed display/error helpers, `updateTowerCostLabels`, `fallbackCopy`. |
| `input.js` | All event handling: mouse and touch listeners on canvas (click, drag-to-build, tower/enemy selection), tower button clicks, target mode buttons, action bar buttons (wave, new map, pause, speed, upgrade-all), seed overlay controls, difficulty buttons. Exports `getGridPos` and drag-state variables. |
| `game.js` | Core game loop and state management: `updateStats` (main per-frame update driving enemy movement, tower cooldowns, projectile physics, boss abilities), `gameLoop`, `init`. Also: `attemptPlaceTower`, `upgradeTower`, `deleteTower`, `startWave`, `checkWaveComplete`, `gameOver`, `restartGame`, `applyDifficulty`, `applySandbox`, `regenerateMap`, `upgradeAllCheapestFirst`, `upgradeAllMostExpensiveFirst`, `tryLoadSeed`, `applyMapDataToGame`. |
