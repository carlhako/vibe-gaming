'use strict';

const SIZE = 22;
const CELL = 28;
const CANVAS = CELL * SIZE;
const CELL_CENTER = CELL / 2;

function cellCx(c) { return c * CELL + CELL_CENTER; }
function cellCy(r) { return r * CELL + CELL_CENTER; }

const MAX_STEAM_ROLLERS = 15;
const DPS_WINDOW = 1.5;
const MIN_BUILDABLE_CELLS = 60;
const WAVE_UNLOCK_L4 = 65;
const WAVE_UNLOCK_L5 = 110;

const SPEED_PRESETS = [1, 2, 5, 10, 15, 20];
const SPEED_LABELS = ['1×', '2×', '5×', '10×', '15×', '20×'];

const TOWER_TYPES = {
    arrow: {
        name: 'Arrow', emoji: '🏹',
        levels: [
            { cost: 65,  damage: 10, range: 3.0, fireRate: 1.8, color: '#5dade2' },
            { cost: 65,  damage: 18, range: 3.2, fireRate: 2.0, color: '#2e86de' },
            { cost: 95,  damage: 26, range: 3.8, fireRate: 2.2, color: '#1b4f72' },
            { cost: 250, damage: 40, range: 4.3, fireRate: 2.5, color: '#154360' },
            { cost: 500, damage: 60, range: 5.0, fireRate: 2.9, color: '#0d2b45' }
        ]
    },
    cannon: {
        name: 'Cannon', emoji: '💥',
        levels: [
            { cost: 130, damage: 30, range: 2.5, fireRate: 0.7,  splash: 1.0, color: '#e67e22' },
            { cost: 100, damage: 45, range: 2.7, fireRate: 0.75, splash: 1.1, color: '#d35400' },
            { cost: 130, damage: 60, range: 3.2, fireRate: 0.8,  splash: 1.3, color: '#a04000' },
            { cost: 350, damage: 90, range: 3.7, fireRate: 0.9,  splash: 1.6, color: '#7a2e00' },
            { cost: 700, damage: 135,range: 4.2, fireRate: 1.0,  splash: 2.0, color: '#5a1e00' }
        ]
    },
    ice: {
        name: 'Ice', emoji: '❄️',
        levels: [
            { cost: 95,  damage: 6,  range: 3.2, fireRate: 1.1, slow: 0.45, color: '#85c1e9' },
            { cost: 75,  damage: 10, range: 3.6, fireRate: 1.2, slow: 0.55, color: '#5dade2' },
            { cost: 100, damage: 14, range: 4.0, fireRate: 1.3, slow: 0.65, color: '#2e86de' },
            { cost: 280, damage: 22, range: 4.5, fireRate: 1.45,slow: 0.72, color: '#1b6ca8' },
            { cost: 550, damage: 32, range: 5.0, fireRate: 1.6, slow: 0.78, color: '#145080' }
        ]
    },
    sniper: {
        name: 'Laser', emoji: '🔫',
        levels: [
            { cost: 190, damage: 50, range: 5.5, fireRate: 0.35, pierceFalloff: 0.5, color: '#f1c40f' },
            { cost: 150, damage: 50, range: 6.0, fireRate: 0.38, pierceFalloff: 0.6, color: '#d4ac0d' },
            { cost: 190, damage: 50, range: 6.8, fireRate: 0.42, pierceFalloff: 0.8, color: '#b7950b' },
            { cost: 500, damage: 50, range: 7.8, fireRate: 0.48, pierceFalloff: 0.88,color: '#9a7d0a' },
            { cost: 1000,damage: 50, range: 9.0, fireRate: 0.55, pierceFalloff: 0.94,color: '#7d6408' }
        ]
    },
    tesla: {
        name: 'Tesla', emoji: '⚡',
        levels: [
            { cost: 260, damage: 35, range: 3.0, fireRate: 0.9,  chain: 2, damageFalloff: 0.5,  color: '#a569bd' },
            { cost: 190, damage: 35, range: 3.2, fireRate: 1.0,  chain: 3, damageFalloff: 0.6,  color: '#8e44ad' },
            { cost: 230, damage: 35, range: 3.8, fireRate: 1.1,  chain: 4, damageFalloff: 0.75, color: '#6c3483' },
            { cost: 600, damage: 35, range: 4.4, fireRate: 1.25, chain: 5, damageFalloff: 0.85, color: '#4a1d6e' },
            { cost: 1200,damage: 35, range: 5.2, fireRate: 1.4,  chain: 6, damageFalloff: 0.92, color: '#2e0f4a' }
        ]
    },
    rangeBuff: {
        name: 'Range+', emoji: '📡',
        levels: [
            { cost: 100, buffValue: 0.20, range: 4.5, color: '#5dade2' },
            { cost: 80,  buffValue: 0.28, range: 4.5, color: '#2e86de' },
            { cost: 110, buffValue: 0.38, range: 4.5, color: '#1b4f72' },
            { cost: 300, buffValue: 0.50, range: 5.2, color: '#154360' },
            { cost: 600, buffValue: 0.65, range: 6.0, color: '#0d2b45' }
        ]
    },
    speedBuff: {
        name: 'Speed+', emoji: '⏫',
        levels: [
            { cost: 110, buffValue: 0.12, range: 4.5, color: '#58d68d' },
            { cost: 85,  buffValue: 0.20, range: 4.5, color: '#2ecc71' },
            { cost: 115, buffValue: 0.28, range: 4.5, color: '#1e8449' },
            { cost: 320, buffValue: 0.38, range: 5.2, color: '#145a32' },
            { cost: 650, buffValue: 0.50, range: 6.0, color: '#0d3d1f' }
        ]
    },
    attackBuff: {
        name: 'Power+', emoji: '💪',
        levels: [
            { cost: 120, buffValue: 0.20, range: 4.5, color: '#f1948a' },
            { cost: 95,  buffValue: 0.28, range: 4.5, color: '#e74c3c' },
            { cost: 130, buffValue: 0.38, range: 4.5, color: '#c0392b' },
            { cost: 350, buffValue: 0.50, range: 5.2, color: '#922b21' },
            { cost: 700, buffValue: 0.65, range: 6.0, color: '#6b1a1a' }
        ]
    },
    mint: {
        name: 'Mint', emoji: '💰',
        levels: [
            { cost: 500,   income: 50,    color: '#f39c12' },
            { cost: 2500,  income: 1000,  color: '#e67e22' },
            { cost: 10000, income: 5000,  color: '#d35400' },
            { cost: 40000, income: 20000, color: '#b84500' },
            { cost: 150000,income: 80000, color: '#8a2e00' }
        ]
    },
    steamRoller: {
        name: 'Steam Roller', emoji: '🚂',
        levels: [
            { cost: 1000,  hp: 500,  cooldown: 20, speed: 1.8, color: '#c0392b' },
            { cost: 1000,  hp: 1000, cooldown: 20, speed: 1.8, color: '#e74c3c' },
            { cost: 5000,  hp: 2000, cooldown: 20, speed: 1.8, color: '#ff3b3b' },
            { cost: 20000, hp: 5000, cooldown: 18, speed: 2.0, color: '#ff1a1a' },
            { cost: 50000, hp: 12000,cooldown: 15, speed: 2.2, color: '#e60000' }
        ]
    }
};

function isBuffTower(type) { return type === 'rangeBuff' || type === 'speedBuff' || type === 'attackBuff'; }
function isAttackTower(type) { return type === 'arrow' || type === 'cannon' || type === 'ice' || type === 'sniper' || type === 'tesla'; }
function isSpecialTower(type) { return type === 'steamRoller'; }
function isProjectileTower(type) { return type === 'arrow' || type === 'cannon'; }

const ENEMY_TYPES = {
    normal: { hpMult: 1.0, speed: 1.3, reward: 8, color: '#2ecc71', size: 5, name: 'Normal' },
    fast: { hpMult: 0.6, speed: 2.4, reward: 12, color: '#3498db', size: 4, name: 'Fast' },
    heavy: { hpMult: 2.5, speed: 0.9, reward: 18, color: '#e74c3c', size: 7, name: 'Heavy' },
    boss: { hpMult: 20.0, speed: 0.45, reward: 100, color: '#9b59b6', size: 10, name: 'Boss' }
};

const DIFFICULTY_SETTINGS = {
    easy: { hpMult: 1.0, incomeMult: 1.0, label: 'Easy', badgeClass: 'easy' },
    medium: { hpMult: 1.2, incomeMult: 1.0, label: 'Medium', badgeClass: 'medium' },
    hard: { hpMult: 1.4, incomeMult: 0.8, label: 'Hard', badgeClass: 'hard' }
};

// ── Global mutable state shared across modules ────────────────────
let game = null;
let pendingSeed = 0;

let isDragging = false;
let dragPlacedCells = null;
let justDragged = false;
let mouseGrid = { col: -1, row: -1, valid: false };
let previewValid = false;
let lastTime = 0;
