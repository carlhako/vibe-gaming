const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d');
const W = 960, H = 640, HUD_BAR_H = 64, GAME_VIEW_H = H - HUD_BAR_H;
canvas.width = W; canvas.height = H; ctx.imageSmoothingEnabled = false;
function ensureFocus(e) { if (document.activeElement !== canvas) canvas.focus({ preventScroll: true }); }
canvas.focus({ preventScroll: true });

const TILE = 40, MAP_COLS = 35, MAP_ROWS = 28, WORLD_W = MAP_COLS * TILE, WORLD_H = MAP_ROWS * TILE;
const PLAYER_SPEED = 180, PLAYER_SIZE = 16, TOKEN_MAGNET_RANGE = 60, INVULN_TIME = 0.6, SWING_DURATION = 0.16;
const MAX_MINIONS = 14, MINION_AGGRO_RANGE = 320, ENEMY_AGGRO_RANGE = 340, MINION_DISENGAGE_DIST = 240, MINION_LIFETIME = 15;
const STUCK_CHECK_INTERVAL = 0.5, STUCK_DIST_THRESHOLD = 10, WALL_DESTROY_TOKEN_CHANCE = 0.25;
const BONE_SHARD_COUNT = 8, BONE_SHARD_SPEED = 260, BONE_SHARD_LIFE = 0.45;
const WITCH_AGGRO_RANGE = 420, WITCH_SUMMON_COOLDOWN = 0.85, WITCH_MAX_DEVILS = 3, WITCH_SUMMON_RANGE = 320, WITCH_PREFERRED_DIST = 260;

// Earthshaker constants
const EARTHSHAKER_GROUND_SLAM_MIN = 3.0;
const EARTHSHAKER_GROUND_SLAM_MAX = 4.5;
const EARTHSHAKER_GROUND_SLAM_CHARGE = 0.8;
const EARTHSHAKER_GROUND_SLAM_DAMAGE = 25;
const EARTHSHAKER_GROUND_SLAM_STUN = 1.5;
const EARTHSHAKER_GROUND_SLAM_RADIUS = 300;
const EARTHSHAKER_TELEPORT_MIN = 5.0;
const EARTHSHAKER_TELEPORT_MAX = 7.0;
const EARTHSHAKER_TELEPORT_MIN_DIST = 200;
const EARTHSHAKER_RAGE_HP_RATIO = 0.5;
const EARTHSHAKER_RAGE_CD_MULT = 0.6;
const EARTHSHAKER_RAGE_SPEED_MULT = 1.5;
const EARTHSHAKER_SHOCKWAVE_SPEED = 400;
const RAILGUN_CHARGE_TIME = 1.8, RAILGUN_BEAM_RANGE = 560, RAILGUN_MIN_CHARGE_TO_FIRE = 0.08;
const MINION_RAILGUN_CHARGE_TIME = 0.65;
const DASH_DURATION = 0.14, DASH_SPEED = 750, DASH_COOLDOWN = 5.0, BASE_MAX_HP = 100;

// Purple block constants
const PURPLE_BLOCK_HP = 100;
const PURPLE_BLOCK_SIZE = 20;
const PURPLE_BLOCK_INVULN = 0.05;
const PURPLE_BLOCK_BASE_ENEMIES = 5;
const PURPLE_BLOCK_BASE_GOLD_MIN = 50;
const PURPLE_BLOCK_BASE_GOLD_MAX = 100;
const PURPLE_BLOCK_SPAWN_CHANCE = 0.5;

// Amulet constants
const AMULET_DROP_CHANCE = 0.02; // 1 in 50
const AMULET_TYPES = [
    { id: 'regen', name: 'Amulet of Regeneration', desc: 'Regenerate 5 HP/sec', icon: '\u2665', iconColor: '#ff4444', glowColor: '#ff6666', powers: [{ type: 'regen', value: 5 }] },
    { id: 'power', name: 'Amulet of Power', desc: 'Double damage dealt', icon: '\u2605', iconColor: '#ffaa00', glowColor: '#ffcc44', powers: [{ type: 'damageMult', value: 2.0 }] },
    { id: 'necro', name: 'Amulet of the Necromancer', desc: '+2 skeletons & +20% minion dmg', icon: '\u2620', iconColor: '#bbcc88', glowColor: '#ddeeaa', powers: [{ type: 'extraSkeletons', value: 2 }, { type: 'minionDamageMult', value: 1.2 }] },
    { id: 'haste', name: 'Amulet of Haste', desc: '+30% movement speed', icon: '\u2601', iconColor: '#88ccff', glowColor: '#aaddff', powers: [{ type: 'speedMult', value: 1.3 }] },
    { id: 'protection', name: 'Amulet of Protection', desc: '25% dmg resist & 3 HP/sec', icon: '\u25C6', iconColor: '#aaaaff', glowColor: '#ccccff', powers: [{ type: 'damageResist', value: 0.25 }, { type: 'regen', value: 3 }] },
    { id: 'cooldown', name: 'Amulet of Swift Casting', desc: '25% reduced cooldowns', icon: '\u231B', iconColor: '#ffdd44', glowColor: '#ffee88', powers: [{ type: 'cdMult', value: 0.75 }] },
    { id: 'magnet', name: 'Amulet of Magnetism', desc: 'Double token pickup range', icon: '\u2B21', iconColor: '#ff88cc', glowColor: '#ffaaee', powers: [{ type: 'magnetMult', value: 2.0 }] },
    { id: 'fury', name: 'Amulet of Fury', desc: '+50% damage & +15% speed', icon: '\u2606', iconColor: '#ff6622', glowColor: '#ff8844', powers: [{ type: 'damageMult', value: 1.5 }, { type: 'speedMult', value: 1.15 }] },
    { id: 'greed', name: 'Amulet of Greed', desc: '2x tokens & +25% enemy HP', icon: '\u25C9', iconColor: '#ffd700', glowColor: '#ffd700', powers: [{ type: 'greedTokens', value: 2.0 }, { type: 'greedEnemyHP', value: 1.25 }] },
    { id: 'coolant', name: 'Amulet of Coolant', desc: 'Annihilator fires 2x longer & +100% dmg', icon: '\u2744', iconColor: '#88ccff', glowColor: '#aaeeff', powers: [{ type: 'coolantMult', value: 0.5 }, { type: 'annihilatorDamageMult', value: 2.0 }] },
    { id: 'wildfire', name: 'Amulet of Wildfire', desc: 'Fireballs explode on impact!', icon: '\ud83d\udd25', iconColor: '#ff4500', glowColor: '#ff6600', powers: [{ type: 'wildfire', value: 1 }], rarity: 'rare' },
    { id: 'sacrifice', name: 'Amulet of Sacrifice', desc: '+150% skel dmg, -50% lifetime', icon: '\u2694', iconColor: '#8b0000', glowColor: '#cc2222', powers: [{ type: 'sacrificeDmg', value: 2.5 }, { type: 'sacrificeLife', value: 0.5 }] }
];

let gameState = 'CHARACTER_SELECT', wave = 1, tokens = 0, waveClearTimer = 0, gameOverTimer = 0;
let screenShake = 0, shakeX = 0, shakeY = 0, paused = false, unpauseCountdown = 0;
let selectedCharacter = 'sorcerer', playerUpgrades = {}, player = null;
let enemies = [], projectiles = [], particles = [], droppedTokens = [], damageNumbers = [], minions = [], boneShards = [], beamEffects = [], shockwaves = [], zombies = [];
let mapTiles = [];
let purpleBlock = null;
let currentAmulet = null, droppedAmulets = [], amuletPickupState = null;
let sandboxMode = false;
let showAnnihilatorLore = false;
let annihilatorLoreScroll = 0;
