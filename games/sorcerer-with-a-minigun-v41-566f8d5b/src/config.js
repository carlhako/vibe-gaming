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

let gameState = 'CHARACTER_SELECT', wave = 1, tokens = 0, waveClearTimer = 0, gameOverTimer = 0;
let screenShake = 0, shakeX = 0, shakeY = 0, paused = false, unpauseCountdown = 0;
let selectedCharacter = 'sorcerer', playerUpgrades = {}, player = null;
let enemies = [], projectiles = [], particles = [], droppedTokens = [], damageNumbers = [], minions = [], boneShards = [], beamEffects = [];
let mapTiles = [];
let purpleBlock = null;
