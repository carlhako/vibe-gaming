// config.js - All game constants, canvas/context setup, and global state variables

const W = 960, H = 640;
const canvas = document.getElementById('game'), ctx = canvas.getContext('2d');
canvas.width = W; canvas.height = H;
canvas.setAttribute('tabindex', '0');
canvas.focus({ preventScroll: true });

const TILE = 40, MAP_COLS = 31, MAP_ROWS = 31, WORLD_W = MAP_COLS * TILE, WORLD_H = MAP_ROWS * TILE;
const BASE_MAX_HP = 100, INVULN_TIME = 0.5, TOKEN_MAGNET_RANGE = 90, TOKEN_FLY_SPEED = 350;
const PLAYER_SPEED = 180, DASH_SPEED = 600, DASH_DURATION = 0.18, DASH_COOLDOWN = 1.1;
const MAX_MINIONS = 14, MINION_AGGRO_RANGE = 320, ENEMY_AGGRO_RANGE = 340, MINION_DISENGAGE_DIST = 240, MINION_LIFETIME = 15;
const STUCK_CHECK_INTERVAL = 0.5, STUCK_DIST_THRESHOLD = 10, WALL_DESTROY_TOKEN_CHANCE = 0.25;
const WALL_DESTROY_AMULET_CHANCE = 0.02; // 1 in 50
const GAME_VIEW_H = H - 90, HUD_BAR_H = 90;
const PURPLE_BLOCK_HP = 100, PURPLE_BLOCK_GOLD_MIN = 50, PURPLE_BLOCK_GOLD_MAX = 100;
const PURPLE_BLOCK_GOLD_PER_WAVE = 10, PURPLE_BLOCK_ENEMIES_BASE = 5, PURPLE_BLOCK_ENEMIES_PER_WAVE = 1;
const PURPLE_BLOCK_INVULN_TIME = 0.05;
const BONE_SHARD_SPEED = 220, BONE_SHARD_LIFE = 0.7, BONE_SHARD_MIN_SPREAD = 200, BONE_SHARD_MAX_SPREAD = 380;
const SHARD_PARTICLES = 8;

function ensureFocus(e) { if (document.activeElement !== canvas) { e.preventDefault(); canvas.focus({ preventScroll: true }); } }

let gameState = 'CHARACTER_SELECT'; // CHARACTER_SELECT, PLAYING, WAVE_CLEAR, SHOP, GAME_OVER, AMULET_DIALOG
let selectedCharacter = 'sorcerer';
let wave = 0, tokens = 0, paused = false, unpauseCountdown = 0;
let player = null, mapTiles = [], enemies = [], minions = [], projectiles = [], particles = [];
let damageNumbers = [], droppedTokens = [], boneShards = [], beamEffects = [];
let playerUpgrades = {};
let gameOverTimer = 0, waveClearTimer = 0;
let screenShake = 0, shakeX = 0, shakeY = 0;
let purpleBlock = null;

// Amulet system
let droppedAmulets = [];
let amuletCompareState = null; // { current: amulet|null, new: amulet, buttons: [{x,y,w,h,action}] }
const AMULET_DEFS = [
    { id: 'regen', name: 'Amulet of Regeneration', iconGlyph: '\u2764', color: '#ff4444', desc: 'Health regen 5/sec', powers: [{ type: 'regen', value: 5 }] },
    { id: 'might', name: 'Amulet of Might', iconGlyph: '\u2694', color: '#ff8800', desc: 'Double damage', powers: [{ type: 'doubleDamage', value: 2 }] },
    { id: 'necro', name: 'Amulet of the Dead', iconGlyph: '\uD83D\uDC80', color: '#bb88ff', desc: '+2 skeleton cap', powers: [{ type: 'extraSkeletons', value: 2 }] },
    { id: 'swift', name: 'Amulet of Swiftness', iconGlyph: '\uD83D\uDC5F', color: '#44dd44', desc: '+30% move speed', powers: [{ type: 'moveSpeed', value: 0.3 }] },
    { id: 'ward', name: 'Amulet of Warding', iconGlyph: '\uD83D\uDEE1', color: '#8888ff', desc: '30% dmg reduction', powers: [{ type: 'damageReduction', value: 0.3 }] },
    { id: 'flame', name: 'Amulet of Flames', iconGlyph: '\uD83D\uDD25', color: '#ff6622', desc: '+8 fire dmg & 20% cd', powers: [{ type: 'fireDamage', value: 8 }, { type: 'cdReduction', value: 0.2 }] },
    { id: 'leech', name: 'Amulet of Leeching', iconGlyph: '\uD83E\uDE78', color: '#cc1111', desc: '10% life steal', powers: [{ type: 'lifeSteal', value: 0.1 }] },
    { id: 'fort', name: 'Amulet of Fortitude', iconGlyph: '\uD83D\uDCAA', color: '#ffdd00', desc: '+50 max HP & +2 regen', powers: [{ type: 'maxHealth', value: 50 }, { type: 'regen', value: 2 }] }
];

function generateRandomAmulet() {
    const def = AMULET_DEFS[Math.floor(Math.random() * AMULET_DEFS.length)];
    return { id: def.id, name: def.name, iconGlyph: def.iconGlyph, color: def.color, desc: def.desc, powers: def.powers.map(p => ({ type: p.type, value: p.value })) };
}

function getAmuletPower(playerOrNull, powerType) {
    // Returns the total value for a power type from the player's amulet, or 0
    if (!playerOrNull || !playerOrNull.amulet) return 0;
    let total = 0;
    for (const p of playerOrNull.amulet.powers) {
        if (p.type === powerType) total += p.value;
    }
    return total;
}

function hasAmuletPower(playerOrNull, powerType) {
    return getAmuletPower(playerOrNull, powerType) > 0;
}
