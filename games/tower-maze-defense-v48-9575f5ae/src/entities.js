'use strict';

function createGame() {
    return {
        money: 120, lives: 10, wave: 0, kills: 0, paused: false,
        speed: 1, speedIndex: 0, gameOver: false, waveActive: false,
        enemies: [], towers: [], projectiles: [], particles: [],
        stunEffects: [], beamEffects: [], lightningArcs: [], pulseEffects: [],
        moneyNotes: [], moneyNoteTotal: null, moneyNoteQueue: [], moneyNoteSpawnTimer: 0,
        steamRollers: [], steamRollerIdCounter: 0,
        path: [], buildable: [], selectedTowerType: null, selectedTower: null,
        selectedEnemy: null,
        waveEnemyQueue: [], waveTimer: 0, enemyIdCounter: 0, towerIdCounter: 0,
        frameCount: 0, waveCooldown: 0, pathStart: null, pathEnd: null,
        animTime: 0, difficulty: null, seed: 0,
        splits: [], splitMap: null, allPathCellSet: null,
        loopCount: 0, splitCount: 0,
        sandboxMode: false,
        damageEvents: [],
        projectileIdCounter: 0
    };
}

function addMoney(amount) {
    if (!game) return;
    if (game.difficulty === 'hard') amount = Math.floor(amount * 0.8);
    game.money += amount;
}

function createTower(type, col, row, actualCost) {
    const defs = TOWER_TYPES[type]; if (!defs) return null;
    const l0 = defs.levels[0];
    const cost = actualCost !== undefined ? actualCost : l0.cost;
    const tower = {
        id: game.towerIdCounter++, type, col, row, level: 0,
        damage: l0.damage || 0, range: l0.range || 0, fireRate: l0.fireRate || 0,
        color: l0.color, splash: l0.splash || 0, slow: l0.slow || 0,
        chain: l0.chain || 1, income: l0.income || 0, cooldown: 0,
        angle: 0, target: null, fireTimer: 0, targetMode: 'first',
        totalCost: cost, totalDamage: 0, stunTimer: 0,
        damageFalloff: l0.damageFalloff || 0, pierceFalloff: l0.pierceFalloff || 0,
        buffValue: l0.buffValue || 0
    };
    if (isAttackTower(type)) {
        tower.baseDamage = l0.damage || 0;
        tower.baseRange = l0.range || 0;
        tower.baseFireRate = l0.fireRate || 0;
    }
    if (isBuffTower(type)) {
        tower.buffValue = l0.buffValue || 0;
        tower.baseDamage = 0; tower.baseRange = l0.range || 3.0; tower.baseFireRate = 0;
    }
    if (type === 'mint') {
        tower.baseDamage = 0; tower.baseRange = 0; tower.baseFireRate = 0;
    }
    if (type === 'steamRoller') {
        tower.rollerHp = l0.hp || 500;
        tower.rollerSpeed = l0.speed || 1.8;
        tower.rollerCooldown = l0.cooldown || 20;
        tower.rollerSpawnTimer = 0;
        tower.baseDamage = 0; tower.baseRange = 0; tower.baseFireRate = 0;
        tower.rollerColor = l0.color;
    }
    tower.effRange = tower.range;
    tower.effDamage = tower.damage;
    tower.effFireRate = tower.fireRate;
    return tower;
}

function spawnEnemy(we) {
    const e = we.enemy; const start = game.pathStart;
    const enemy = {
        id: game.enemyIdCounter++, type: e.type, hp: e.hp, maxHp: e.maxHp,
        speed: e.speed, reward: e.reward, color: e.color, size: e.size, name: e.name,
        slowTimer: 0, slowFactor: 1.0, pathIndex: 0,
        x: start[0] * CELL + CELL / 2, y: start[1] * CELL + CELL / 2, alive: true, reachedEnd: false,
        onBranch: false, branchSplit: null, branchIndex: 0
    };
    if (enemy.type === 'boss') { enemy.stunCooldown = 10; enemy.stunCooldownMax = 10; enemy.spawnCooldown = 15; enemy.spawnCooldownMax = 15; }
    game.enemies.push(enemy);
}

function spawnMinionAt(bossEnemy) {
    const spawnTypes = ['normal', 'fast', 'heavy'];
    const randomType = spawnTypes[Math.floor(Math.random() * spawnTypes.length)];
    const def = ENEMY_TYPES[randomType]; const waveNum = game.wave;
    const hpWaveMultiplier = Math.pow(1.03, waveNum);
    const diffSetting = game.difficulty ? DIFFICULTY_SETTINGS[game.difficulty] : DIFFICULTY_SETTINGS['easy'];
    const baseHp = 22 + waveNum * 11;
    const hp = Math.round(baseHp * def.hpMult * hpWaveMultiplier * diffSetting.hpMult);
    const speedMult = 1.0 + waveNum * 0.01;
    game.enemies.push({
        id: game.enemyIdCounter++, type: randomType, hp, maxHp: hp,
        speed: def.speed * speedMult * (0.95 + Math.random() * 0.1),
        reward: def.reward + Math.floor(waveNum / 3), color: def.color, size: def.size,
        name: def.name, slowTimer: 0, slowFactor: 1.0,
        pathIndex: bossEnemy.pathIndex, x: bossEnemy.x, y: bossEnemy.y, alive: true, reachedEnd: false,
        onBranch: bossEnemy.onBranch, branchSplit: bossEnemy.branchSplit, branchIndex: bossEnemy.branchIndex
    });
}

function spawnSteamRoller(tower) {
    if (!game || !game.path || game.path.length === 0) return;
    const defs = TOWER_TYPES['steamRoller'];
    if (!defs) return;
    const endCell = game.path[game.path.length - 1];
    const roller = {
        id: game.steamRollerIdCounter++,
        towerId: tower.id,
        hp: tower.rollerHp,
        maxHp: tower.rollerHp,
        x: endCell[0] * CELL + CELL / 2,
        y: endCell[1] * CELL + CELL / 2,
        pathIndex: game.path.length - 1,
        speed: tower.rollerSpeed,
        color: tower.rollerColor,
        alive: true,
        size: 9,
        wheelAngle: 0
    };
    game.steamRollers.push(roller);
    spawnParticles(roller.x, roller.y, '#e74c3c', 15);
}

function spawnParticles(x, y, color, count) {
    for (let i = 0; i < count; i++) {
        const angle = Math.random() * Math.PI * 2, speed = 30 + Math.random() * 80;
        game.particles.push({ x: x + (Math.random() - 0.5) * 4, y: y + (Math.random() - 0.5) * 4, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed - 20, size: 2 + Math.random() * 3, life: 0.4 + Math.random() * 0.6, color });
    }
}

function generateWave(waveNum) {
    const enemies = [];
    const count = Math.min(7 + waveNum * 3, 55);
    let types = ['normal'];
    if (waveNum >= 3) types.push('fast');
    if (waveNum >= 5) types.push('heavy');
    const hasBoss = waveNum >= 6;
    const hpWaveMultiplier = Math.pow(1.03, waveNum);
    const diffSetting = game.difficulty ? DIFFICULTY_SETTINGS[game.difficulty] : DIFFICULTY_SETTINGS['easy'];
    const difficultyHpMult = diffSetting.hpMult;
    for (let i = 0; i < count; i++) {
        let type;
        const r = Math.random();
        if (hasBoss && i === count - 1) type = 'boss';
        else if (waveNum >= 5 && r < 0.18) type = 'heavy';
        else if (waveNum >= 3 && r < 0.35) type = 'fast';
        else type = 'normal';
        const baseHp = 22 + waveNum * 11;
        const def = ENEMY_TYPES[type];
        const hp = Math.round(baseHp * def.hpMult * hpWaveMultiplier * difficultyHpMult);
        const speedMult = 1.0 + waveNum * 0.01;
        const speed = def.speed * speedMult * (0.95 + Math.random() * 0.1);
        enemies.push({ type, hp, maxHp: hp, speed, reward: def.reward + Math.floor(waveNum / 3), color: def.color, size: def.size, name: def.name, slowTimer: 0, slowFactor: 1.0 });
    }
    if (hasBoss) { const boss = enemies.pop(); for (let i = enemies.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [enemies[i], enemies[j]] = [enemies[j], enemies[i]]; } enemies.push(boss); }
    else { for (let i = enemies.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [enemies[i], enemies[j]] = [enemies[j], enemies[i]]; } }
    const spawnInterval = Math.max(0.25, 1.15 - waveNum * 0.02);
    return enemies.map((e, i) => ({ enemy: e, spawnTime: i * spawnInterval }));
}
