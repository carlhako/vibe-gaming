'use strict';

// ── Tower operations ──────────────────────────────────────────────
function attemptPlaceTower(col, row) {
    if (!game || !game.selectedTowerType) return false;
    const key = col + ',' + row;
    if (dragPlacedCells && dragPlacedCells.has(key)) return false;

    if (!game.buildable[row] || !game.buildable[row][col]) return false;
    if (game.towers.some(t => t.col === col && t.row === row)) return false;

    if (game.selectedTowerType === 'steamRoller') {
        const rollerCount = game.towers.filter(t => t.type === 'steamRoller').length;
        if (rollerCount >= MAX_STEAM_ROLLERS) return false;
    }

    const defs = TOWER_TYPES[game.selectedTowerType];
    const placementCost = game.sandboxMode ? 0 : defs.levels[0].cost;
    if (game.money < placementCost) return false;

    game.money -= placementCost;
    const tower = createTower(game.selectedTowerType, col, row, placementCost);
    game.towers.push(tower);
    game.selectedEnemy = null;

    if (dragPlacedCells) dragPlacedCells.add(key);

    if (tower.type === 'steamRoller') {
        spawnSteamRoller(tower);
        tower.rollerSpawnTimer = tower.rollerCooldown;
    }

    updateUI();
    return true;
}

function upgradeTower(tower) {
    const defs = TOWER_TYPES[tower.type]; if (!defs) return false;
    const nl = tower.level + 1; if (nl >= defs.levels.length) return false;
    const lvl = defs.levels[nl];
    const actualCost = game.sandboxMode ? 0 : lvl.cost;
    if (game.money < actualCost) return false;
    game.money -= actualCost; tower.level = nl;
    if (isBuffTower(tower.type)) {
        tower.buffValue = lvl.buffValue || tower.buffValue;
        tower.range = lvl.range || tower.range;
        tower.color = lvl.color;
        tower.baseRange = tower.range;
    } else if (tower.type === 'mint') {
        tower.income = lvl.income || tower.income;
        tower.color = lvl.color;
    } else if (tower.type === 'steamRoller') {
        tower.rollerHp = lvl.hp || tower.rollerHp;
        tower.rollerSpeed = lvl.speed || tower.rollerSpeed;
        tower.rollerCooldown = lvl.cooldown || tower.rollerCooldown;
        tower.rollerColor = lvl.color;
        tower.color = lvl.color;
    } else {
        tower.damage = lvl.damage || tower.damage;
        tower.range = lvl.range || tower.range;
        tower.fireRate = lvl.fireRate || tower.fireRate;
        tower.color = lvl.color;
        tower.splash = lvl.splash || 0;
        tower.slow = lvl.slow || 0;
        tower.chain = lvl.chain || 1;
        tower.damageFalloff = lvl.damageFalloff !== undefined ? lvl.damageFalloff : tower.damageFalloff;
        tower.pierceFalloff = lvl.pierceFalloff !== undefined ? lvl.pierceFalloff : tower.pierceFalloff;
        tower.baseDamage = tower.damage;
        tower.baseRange = tower.range;
        tower.baseFireRate = tower.fireRate;
    }
    tower.totalCost += actualCost;
    tower.effRange = tower.range;
    tower.effDamage = tower.damage;
    tower.effFireRate = tower.fireRate;
    updateUI(); showInfo(tower); return true;
}

function deleteTower(tower) {
    const refund = Math.floor(tower.totalCost * 0.5); game.money += refund;
    const idx = game.towers.indexOf(tower); if (idx !== -1) game.towers.splice(idx, 1);
    for (let i = game.steamRollers.length - 1; i >= 0; i--) {
        if (game.steamRollers[i].towerId === tower.id) {
            spawnParticles(game.steamRollers[i].x, game.steamRollers[i].y, '#e74c3c', 8);
            game.steamRollers.splice(i, 1);
        }
    }
    if (game.selectedTower === tower) { game.selectedTower = null; showInfo(null); }
    updateUI(); render();
}

// ── Wave management ───────────────────────────────────────────────
function startWave() {
    if (game.gameOver || !game.difficulty || game.waveActive || game.waveCooldown > 0) return;
    game.wave++; game.waveEnemyQueue = generateWave(game.wave);
    game.waveTimer = 0; game.waveActive = true;
    waveBtn.textContent = '⚔'; waveBtn.style.opacity = '0.6'; waveBtn.disabled = true;
    newMapBtn.style.display = 'none';
    for (const tower of game.towers) {
        if (tower.type === 'steamRoller') {
            const hasActiveRoller = game.steamRollers.some(r => r.towerId === tower.id && r.alive);
            if (!hasActiveRoller) {
                spawnSteamRoller(tower);
                tower.rollerSpawnTimer = tower.rollerCooldown;
            }
        }
    }
    updateUI();
}

function checkWaveComplete() {
    if (!game.waveActive || game.waveEnemyQueue.length > 0 || game.enemies.length > 0) return;
    game.waveActive = false; waveBtn.textContent = '▶'; waveBtn.style.opacity = '1'; waveBtn.disabled = false;
    game.waveCooldown = 0.5; addMoney(15 + game.wave * 2);
    let totalMintIncome = 0;
    for (const tower of game.towers) {
        if (tower.type === 'mint' && tower.income > 0) {
            addMoney(tower.income);
            totalMintIncome += tower.income;
        }
    }
    if (totalMintIncome > 0) {
        const noteValue = 50;
        const noteCount = Math.min(Math.ceil(totalMintIncome / noteValue), 200);
        const cx = CANVAS / 2;
        const cy = CANVAS / 2;
        game.moneyNoteQueue = [];
        for (let i = 0; i < noteCount; i++) {
            game.moneyNoteQueue.push({
                x: cx,
                y: cy,
                vx: (Math.random() - 0.5) * 70,
                vy: -(200 + Math.random() * 320),
                life: 2.0 + Math.random() * 1.0,
                maxLife: 2.0 + Math.random() * 1.0,
                size: 7 + Math.random() * 8,
                rotation: Math.random() * Math.PI * 2,
                rotationSpeed: (Math.random() - 0.5) * 8
            });
        }
        game.moneyNoteSpawnTimer = 0;
        const totalDuration = noteCount * 0.028 + 2.2;
        game.moneyNoteTotal = {
            text: '+$' + totalMintIncome + ' from Mints',
            life: totalDuration,
            maxLife: totalDuration,
            x: cx,
            y: cy - 10
        };
        spawnParticles(cx, cy, '#f1c40f', 25);
    }
    updateUI();
}

// ── Game lifecycle ────────────────────────────────────────────────
function gameOver() { game.gameOver = true; game.paused = true; waveBtn.disabled = true; finalWaveEl.textContent = 'Survived to Wave ' + game.wave + ' · Kills: ' + game.kills; gameOverOverlay.classList.add('show'); }

function restartGame() {
    gameOverOverlay.classList.remove('show'); game = createGame();
    pendingSeed = generateRandomSeed(); game.seed = pendingSeed;
    const prng = mulberry32(game.seed); const mapData = generatePath(prng);
    game.path = mapData.path; game.buildable = mapData.buildable;
    game.pathStart = mapData.start; game.pathEnd = mapData.end;
    game.splits = mapData.splits || []; game.splitMap = mapData.splitMap || {};
    game.allPathCellSet = mapData.allPathCellSet || new Set(game.path.map(([x, y]) => x + ',' + y));
    game.loopCount = mapData.loopCount || 0; game.splitCount = mapData.splitCount || 0;
    game.selectedTowerType = null; game.selectedTower = null; game.selectedEnemy = null;
    waveBtn.disabled = false; newMapBtn.style.display = '';
    speedBtn.textContent = '1×'; game.speedIndex = 0; game.speed = 1;
    game.difficulty = null; updateUI(); showInfo(null);
    waveBtn.textContent = '▶'; waveBtn.style.opacity = '1';
    deselectTowerType(); difficultyBadge.style.display = 'none';
    difficultyOverlay.classList.add('show'); gameOverOverlay.classList.remove('show');
    seedDisplayPanel.style.display = 'none';
    updateTowerCostLabels();
    refreshOverlaySeed();
    isDragging = false; dragPlacedCells = null; justDragged = false;
    render();
}

function applyDifficulty(diff) {
    if (!game) return; game.difficulty = diff; game.sandboxMode = false;
    const s = DIFFICULTY_SETTINGS[diff];
    difficultyLabel.textContent = s.label; difficultyBadge.className = s.badgeClass;
    difficultyBadge.style.display = 'block'; difficultyOverlay.classList.remove('show');
    updateTowerCostLabels();
    updateSeedDisplay(); updateUI(); render();
}

function applySandbox() {
    if (!game) {
        game = createGame();
        game.seed = pendingSeed;
        const prng = mulberry32(pendingSeed);
        const mapData = generatePath(prng);
        const validation = validatePathData(mapData);
        if (!validation.valid) {
            const fb = generatePath(null);
            game.path = fb.path; game.buildable = fb.buildable;
            game.pathStart = fb.start; game.pathEnd = fb.end;
            game.splits = fb.splits || []; game.splitMap = fb.splitMap || {};
            game.allPathCellSet = fb.allPathCellSet || new Set(game.path.map(([x, y]) => x + ',' + y));
            game.loopCount = fb.loopCount || 0; game.splitCount = fb.splitCount || 0;
            game.seed = generateRandomSeed(); pendingSeed = game.seed;
        } else {
            game.path = mapData.path; game.buildable = mapData.buildable;
            game.pathStart = mapData.start; game.pathEnd = mapData.end;
            game.splits = mapData.splits || []; game.splitMap = mapData.splitMap || {};
            game.allPathCellSet = mapData.allPathCellSet || new Set(game.path.map(([x, y]) => x + ',' + y));
            game.loopCount = mapData.loopCount || 0; game.splitCount = mapData.splitCount || 0;
        }
    }
    game.difficulty = 'medium';
    game.sandboxMode = true;
    difficultyLabel.textContent = 'Sandbox';
    difficultyBadge.className = 'sandbox';
    difficultyBadge.style.display = 'block';
    difficultyOverlay.classList.remove('show');
    updateTowerCostLabels();
    updateSeedDisplay();
    updateUI();
    render();
}

function regenerateMap(newSeed) {
    if (!game || !game.difficulty) return;
    const diff = game.difficulty; const sandbox = game.sandboxMode;
    const seed = newSeed !== undefined ? newSeed : generateRandomSeed();
    game.seed = seed; const prng = mulberry32(seed); const mapData = generatePath(prng);
    const validation = validatePathData(mapData);
    if (!validation.valid) {
        const fb = generatePath(null);
        game.path = fb.path; game.buildable = fb.buildable;
        game.pathStart = fb.start; game.pathEnd = fb.end;
        game.splits = fb.splits || []; game.splitMap = fb.splitMap || {};
        game.allPathCellSet = fb.allPathCellSet || new Set(game.path.map(([x, y]) => x + ',' + y));
        game.loopCount = fb.loopCount || 0; game.splitCount = fb.splitCount || 0;
        game.seed = generateRandomSeed();
    } else {
        game.path = mapData.path; game.buildable = mapData.buildable;
        game.pathStart = mapData.start; game.pathEnd = mapData.end;
        game.splits = mapData.splits || []; game.splitMap = mapData.splitMap || {};
        game.allPathCellSet = mapData.allPathCellSet || new Set(game.path.map(([x, y]) => x + ',' + y));
        game.loopCount = mapData.loopCount || 0; game.splitCount = mapData.splitCount || 0;
    }
    game.towers = []; game.enemies = []; game.projectiles = []; game.particles = [];
    game.stunEffects = []; game.beamEffects = []; game.lightningArcs = []; game.pulseEffects = [];
    game.moneyNotes = []; game.moneyNoteTotal = null;
    game.moneyNoteQueue = []; game.moneyNoteSpawnTimer = 0;
    game.steamRollers = [];
    game.damageEvents = [];
    game.waveEnemyQueue = []; game.waveTimer = 0; game.waveCooldown = 0; game.difficulty = diff;
    game.sandboxMode = sandbox;
    game.selectedEnemy = null;
    game.projectileIdCounter = 0;
    deselectTowerType(); game.selectedTower = null;
    updateSeedDisplay(); updateUI(); showInfo(null); render();
}

// ── Upgrade-all helpers ───────────────────────────────────────────
function upgradeAllCheapestFirst() {
    if (!game || !game.difficulty || game.gameOver) return;
    let anyUpgraded = false;
    let safety = 0;
    while (safety < 500) {
        safety++;
        const candidates = [];
        for (const tower of game.towers) {
            const defs = TOWER_TYPES[tower.type];
            if (!defs) continue;
            const nl = tower.level + 1;
            if (nl >= defs.levels.length) continue;
            const cost = game.sandboxMode ? 0 : defs.levels[nl].cost;
            if (game.money >= cost) {
                candidates.push({ tower, cost });
            }
        }
        if (candidates.length === 0) break;
        candidates.sort((a, b) => a.cost - b.cost);
        const { tower } = candidates[0];
        if (upgradeTower(tower)) {
            anyUpgraded = true;
        } else {
            break;
        }
    }
    if (anyUpgraded) {
        updateUI();
        if (game.selectedTower && game.towers.includes(game.selectedTower)) {
            showInfo(game.selectedTower);
        }
        render();
    }
}

function upgradeAllMostExpensiveFirst() {
    if (!game || !game.difficulty || game.gameOver) return;
    let anyUpgraded = false;
    let safety = 0;
    while (safety < 500) {
        safety++;
        const candidates = [];
        for (const tower of game.towers) {
            const defs = TOWER_TYPES[tower.type];
            if (!defs) continue;
            const nl = tower.level + 1;
            if (nl >= defs.levels.length) continue;
            const cost = game.sandboxMode ? 0 : defs.levels[nl].cost;
            if (game.money >= cost) {
                candidates.push({ tower, cost });
            }
        }
        if (candidates.length === 0) break;
        candidates.sort((a, b) => b.cost - a.cost);
        const { tower } = candidates[0];
        if (upgradeTower(tower)) {
            anyUpgraded = true;
        } else {
            break;
        }
    }
    if (anyUpgraded) {
        updateUI();
        if (game.selectedTower && game.towers.includes(game.selectedTower)) {
            showInfo(game.selectedTower);
        }
        render();
    }
}

// ── Seed helpers ──────────────────────────────────────────────────
function tryLoadSeed(hexStr, isPreGame) {
    const trimmed = hexStr.trim().toLowerCase();
    if (!isValidHexFormat(trimmed)) { showSeedError('Invalid seed — use 1–8 hex digits.'); return false; }
    const seed = hexToSeed(trimmed);
    if (seed === null) { showSeedError('Could not parse seed.'); return false; }
    const prng = mulberry32(seed);
    const data = generatePath(prng);
    const validation = validatePathData(data);
    if (!validation.valid) { showSeedError('Bad seed: ' + validation.reason); return false; }
    clearSeedError();
    if (isPreGame || !game) { pendingSeed = seed; seedInputOverlay.value = seedToHex(seed); seedInputOverlay.classList.remove('invalid'); return true; }
    if (game.wave > 0 || game.waveActive) { showSeedError('Cannot change seed mid-wave.'); return false; }
    applyMapDataToGame(data, seed);
    return true;
}

function applyMapDataToGame(data, seed) {
    game.seed = seed;
    game.path = data.path;
    game.buildable = data.buildable;
    game.pathStart = data.start;
    game.pathEnd = data.end;
    game.splits = data.splits || [];
    game.splitMap = data.splitMap || {};
    game.allPathCellSet = data.allPathCellSet || new Set(data.path.map(([x, y]) => x + ',' + y));
    game.loopCount = data.loopCount || 0;
    game.splitCount = data.splitCount || 0;
    game.towers = []; game.enemies = []; game.projectiles = []; game.particles = [];
    game.stunEffects = []; game.beamEffects = []; game.lightningArcs = []; game.pulseEffects = [];
    game.moneyNotes = []; game.moneyNoteTotal = null;
    game.moneyNoteQueue = []; game.moneyNoteSpawnTimer = 0;
    game.steamRollers = [];
    game.waveEnemyQueue = []; game.waveTimer = 0; game.waveCooldown = 0;
    game.selectedEnemy = null;
    game.damageEvents = [];
    game.projectileIdCounter = 0;
    deselectTowerType(); game.selectedTower = null;
    updateSeedDisplay(); updateUI(); showInfo(null); render();
}

// ── Main update loop ──────────────────────────────────────────────
function updateStats(dt) {
    if (game.gameOver || game.paused || !game.difficulty) return;
    const dtScaled = dt * game.speed;
    game.animTime += dtScaled;

    if (game.waveActive && game.waveEnemyQueue.length > 0) {
        game.waveTimer += dtScaled;
        while (game.waveEnemyQueue.length > 0 && game.waveTimer >= game.waveEnemyQueue[0].spawnTime) spawnEnemy(game.waveEnemyQueue.shift());
    }

    const mainPath = game.path;

    for (const tower of game.towers) {
        if (tower.type !== 'steamRoller') continue;
        tower.rollerSpawnTimer -= dtScaled;
        if (tower.rollerSpawnTimer <= 0) {
            tower.rollerSpawnTimer = tower.rollerCooldown;
            spawnSteamRoller(tower);
        }
    }

    for (let ri = game.steamRollers.length - 1; ri >= 0; ri--) {
        const roller = game.steamRollers[ri];
        if (!roller.alive) continue;

        roller.wheelAngle += roller.speed * dtScaled * 3;

        if (roller.pathIndex > 0) {
            const targetCell = mainPath[roller.pathIndex - 1];
            const tx = targetCell[0] * CELL + CELL / 2;
            const ty = targetCell[1] * CELL + CELL / 2;
            const dx = tx - roller.x;
            const dy = ty - roller.y;
            const dist = Math.hypot(dx, dy);
            if (dist < 2) {
                roller.x = tx;
                roller.y = ty;
                roller.pathIndex--;
            } else {
                const move = Math.min(roller.speed * CELL * 1.2 * dtScaled, dist);
                roller.x += (dx / dist) * move;
                roller.y += (dy / dist) * move;
            }
        } else {
            roller.alive = false;
            spawnParticles(roller.x, roller.y, '#e74c3c', 10);
            continue;
        }

        const collisionRange = roller.size + 8;
        for (const enemy of game.enemies) {
            if (!enemy.alive) continue;
            const edx = enemy.x - roller.x;
            const edy = enemy.y - roller.y;
            const edist = Math.hypot(edx, edy);
            if (edist <= collisionRange + enemy.size) {
                const dmg = Math.min(roller.hp, enemy.hp);
                if (dmg <= 0) continue;
                enemy.hp -= dmg;
                roller.hp -= dmg;
                const rollerTower = game.towers.find(t => t.id === roller.towerId);
                if (rollerTower) rollerTower.totalDamage += dmg;
                recordDamage(dmg);
                spawnParticles(enemy.x, enemy.y, '#e74c3c', 4);
                if (roller.hp <= 0) {
                    roller.hp = 0;
                    roller.alive = false;
                    spawnParticles(roller.x, roller.y, '#ff6b6b', 20);
                    break;
                }
            }
        }
        if (!roller.alive) continue;
    }

    for (let i = game.steamRollers.length - 1; i >= 0; i--) {
        if (!game.steamRollers[i].alive) {
            game.steamRollers.splice(i, 1);
        }
    }

    for (let i = game.enemies.length - 1; i >= 0; i--) {
        const e = game.enemies[i]; if (!e.alive) continue;
        if (e.slowTimer > 0) { e.slowTimer -= dtScaled; if (e.slowTimer < 0) { e.slowTimer = 0; e.slowFactor = 1.0; } }
        const speed = e.speed * e.slowFactor * CELL * 1.2;

        let currentPath, targetIdx;
        if (e.onBranch) {
            currentPath = e.branchSplit.branchPath;
            targetIdx = e.branchIndex;
        } else {
            currentPath = mainPath;
            targetIdx = e.pathIndex + 1;
        }

        if (targetIdx >= currentPath.length) {
            if (e.onBranch) {
                e.onBranch = false;
                e.pathIndex = e.branchSplit.mergeMainIndex;
                e.branchSplit = null;
                currentPath = mainPath;
                targetIdx = e.pathIndex + 1;
                if (targetIdx >= mainPath.length) {
                    e.alive = false; e.reachedEnd = true;
                    if (game.selectedEnemy === e) { game.selectedEnemy = null; showInfo(null); }
                    game.lives--;
                    if (game.lives <= 0) { game.lives = 0; gameOver(); }
                    updateUI(); continue;
                }
            } else {
                e.alive = false; e.reachedEnd = true;
                if (game.selectedEnemy === e) { game.selectedEnemy = null; showInfo(null); }
                game.lives--;
                if (game.lives <= 0) { game.lives = 0; gameOver(); }
                updateUI(); continue;
            }
        }

        const target = currentPath[targetIdx];
        const tx = target[0] * CELL + CELL / 2, ty = target[1] * CELL + CELL / 2;
        const dx = tx - e.x, dy = ty - e.y, dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < 1) {
            e.x = tx; e.y = ty;
            if (e.onBranch) {
                e.branchIndex = targetIdx + 1;
            } else {
                e.pathIndex = targetIdx;
                if (game.splitMap && game.splitMap[e.pathIndex] !== undefined) {
                    const split = game.splitMap[e.pathIndex];
                    if (Math.random() < 0.5) {
                        e.onBranch = true;
                        e.branchSplit = split;
                        e.branchIndex = 0;
                    }
                }
            }
        } else {
            const move = Math.min(speed * dtScaled, dist);
            e.x += (dx / dist) * move;
            e.y += (dy / dist) * move;
        }
    }

    for (let i = game.enemies.length - 1; i >= 0; i--) {
        if (!game.enemies[i].alive) {
            if (!game.enemies[i].reachedEnd) { addMoney(game.enemies[i].reward); game.kills++; spawnParticles(game.enemies[i].x, game.enemies[i].y, '#f1c40f', 5); }
            if (game.selectedEnemy === game.enemies[i]) { game.selectedEnemy = null; showInfo(null); }
            game.enemies.splice(i, 1);
        }
    }
    updateUI(); checkWaveComplete();
    if (game.waveCooldown > 0) { game.waveCooldown -= dtScaled; if (game.waveCooldown < 0) game.waveCooldown = 0; }

    for (let i = 0; i < game.enemies.length; i++) {
        const enemy = game.enemies[i]; if (!enemy.alive || enemy.type !== 'boss') continue;
        if (enemy.stunCooldown !== undefined) {
            enemy.stunCooldown -= dtScaled;
            if (enemy.stunCooldown <= 0) {
                let ct = null, cd = Infinity;
                for (const t of game.towers) {
                    if (!isAttackTower(t.type)) continue;
                    const tcx = t.col * CELL + CELL / 2, tcy = t.row * CELL + CELL / 2;
                    const d = Math.hypot(tcx - enemy.x, tcy - enemy.y);
                    if (d < cd) { cd = d; ct = t; }
                }
                if (ct) {
                    ct.stunTimer = 2.5;
                    const tcx = ct.col * CELL + CELL / 2, tcy = ct.row * CELL + CELL / 2;
                    game.stunEffects.push({ x1: enemy.x, y1: enemy.y, x2: tcx, y2: tcy, life: 0.4, maxLife: 0.4 });
                    spawnParticles(enemy.x, enemy.y, '#9b59b6', 12); spawnParticles(tcx, tcy, '#c39bdb', 15);
                }
                enemy.stunCooldown = enemy.stunCooldownMax;
            }
        }
        if (enemy.spawnCooldown !== undefined) {
            enemy.spawnCooldown -= dtScaled;
            if (enemy.spawnCooldown <= 0) { spawnMinionAt(enemy); spawnParticles(enemy.x, enemy.y, '#e74c3c', 20); enemy.spawnCooldown = enemy.spawnCooldownMax; }
        }
    }

    const towerBuffs = computeTowerBuffs();
    applyEffectiveStats(towerBuffs);

    for (let ti = 0; ti < game.towers.length; ti++) {
        const tower = game.towers[ti];
        if (tower.stunTimer > 0) tower.stunTimer -= dtScaled;
        if (tower.type === 'mint' || isBuffTower(tower.type) || tower.type === 'steamRoller') continue;
        if (tower.stunTimer > 0) continue;
        tower.cooldown -= dtScaled;
        const cx = tower.col * CELL + CELL / 2, cy = tower.row * CELL + CELL / 2;

        if (tower.type === 'ice') {
            if (tower.cooldown <= 0) {
                const range = tower.effRange * CELL; let hitAny = false;
                for (const enemy of game.enemies) {
                    if (!enemy.alive) continue;
                    if (Math.hypot(enemy.x - cx, enemy.y - cy) <= range) {
                        enemy.hp -= tower.effDamage;
                        tower.totalDamage += tower.effDamage;
                        recordDamage(tower.effDamage);
                        enemy.slowFactor = 1.0 - tower.slow; enemy.slowTimer = 1.5;
                        hitAny = true; spawnParticles(enemy.x, enemy.y, '#85c1e9', 2);
                    }
                }
                if (hitAny) game.pulseEffects.push({ x: cx, y: cy, maxRadius: range, life: 0.65, maxLife: 0.65, color: tower.color });
                tower.cooldown = 1.0 / tower.effFireRate;
            }
            continue;
        }

        const range = tower.effRange * CELL;
        const inRange = [];
        for (const enemy of game.enemies) {
            if (!enemy.alive) continue;
            const d = Math.hypot(enemy.x - cx, enemy.y - cy);
            if (d <= range) inRange.push({ enemy, dist: d });
        }

        let bestEnemy = null;
        if (inRange.length > 0) {
            switch (tower.targetMode) {
                case 'nearest': inRange.sort((a, b) => a.dist - b.dist); bestEnemy = inRange[0].enemy; break;
                case 'first': { let b = null, bp = -1; for (const it of inRange) { if (it.enemy.pathIndex > bp) { bp = it.enemy.pathIndex; b = it.enemy; } } bestEnemy = b; break; }
                case 'last': { let b = null, bp = Infinity; for (const it of inRange) { if (it.enemy.pathIndex < bp) { bp = it.enemy.pathIndex; b = it.enemy; } } bestEnemy = b; break; }
                case 'random': bestEnemy = inRange[Math.floor(Math.random() * inRange.length)].enemy; break;
            }
        }
        tower.target = bestEnemy;
        if (bestEnemy) { const dx = bestEnemy.x - cx, dy = bestEnemy.y - cy; tower.angle = Math.atan2(dy, dx); }
        if (!bestEnemy || tower.cooldown > 0) continue;

        if (tower.type === 'arrow' || tower.type === 'cannon') {
            const pdx = bestEnemy.x - cx;
            const pdy = bestEnemy.y - cy;
            const pdist = Math.hypot(pdx, pdy);
            const speed = 350 + Math.random() * 50;
            const vx = (pdx / pdist) * speed;
            const vy = (pdy / pdist) * speed;
            const pierceCount = tower.type === 'arrow' ? 3 : 2;
            game.projectiles.push({
                id: game.projectileIdCounter++,
                x: cx, y: cy, target: bestEnemy, targetId: bestEnemy.id,
                speed: speed, damage: tower.effDamage,
                splash: tower.splash || 0, slow: 0, chain: 1,
                towerType: tower.type, color: tower.color, alive: true,
                radius: tower.type === 'cannon' ? 6 : 3,
                towerId: tower.id,
                pierceCount: pierceCount,
                hitEnemyIds: new Set(),
                maxTravelDist: CANVAS * 1.5,
                startX: cx, startY: cy,
                vx: vx, vy: vy
            });
            tower.cooldown = 1.0 / tower.effFireRate;
            spawnParticles(cx, cy, '#ffffff', 2);
            continue;
        }

        if (tower.type === 'sniper') {
            const dx = bestEnemy.x - cx, dy = bestEnemy.y - cy;
            const dist = Math.hypot(dx, dy);
            const dirX = dx / dist, dirY = dy / dist;

            let tMax = CANVAS * 1.5;
            if (dirX > 0.0001) tMax = Math.min(tMax, (CANVAS - cx) / dirX);
            else if (dirX < -0.0001) tMax = Math.min(tMax, -cx / dirX);
            if (dirY > 0.0001) tMax = Math.min(tMax, (CANVAS - cy) / dirY);
            else if (dirY < -0.0001) tMax = Math.min(tMax, -cy / dirY);
            const beamLength = tMax;

            const hitEnemies = [];
            for (const enemy of game.enemies) {
                if (!enemy.alive) continue;
                const ex = enemy.x - cx, ey = enemy.y - cy;
                const projDist = ex * dirX + ey * dirY;
                if (projDist < 0 || projDist > beamLength) continue;
                const perpDist = Math.abs(-dirY * ex + dirX * ey);
                if (perpDist <= enemy.size + 5) {
                    hitEnemies.push({ enemy, dist: projDist });
                }
            }
            hitEnemies.sort((a, b) => a.dist - b.dist);
            for (let j = 0; j < hitEnemies.length; j++) {
                const dmg = tower.effDamage * Math.pow(tower.pierceFalloff, j);
                hitEnemies[j].enemy.hp -= dmg;
                tower.totalDamage += dmg;
                recordDamage(dmg);
                spawnParticles(hitEnemies[j].enemy.x, hitEnemies[j].enemy.y, tower.color, 3);
            }

            let actualBeamLength = beamLength;
            if (hitEnemies.length > 0) {
                actualBeamLength = hitEnemies[hitEnemies.length - 1].dist;
            }
            const beamEndX = cx + dirX * actualBeamLength;
            const beamEndY = cy + dirY * actualBeamLength;

            game.beamEffects.push({ x1: cx, y1: cy, x2: beamEndX, y2: beamEndY, life: 0.18, maxLife: 0.18, color: tower.color });
            tower.cooldown = 1.0 / tower.effFireRate;
            continue;
        }

        if (tower.type === 'tesla') {
            let chainTargets = [bestEnemy], currentTarget = bestEnemy;
            for (let c = 0; c < tower.chain; c++) {
                let nearest = null, nearestDist = Infinity;
                for (const enemy of game.enemies) {
                    if (!enemy.alive || chainTargets.includes(enemy)) continue;
                    const d = Math.hypot(enemy.x - currentTarget.x, enemy.y - currentTarget.y);
                    if (d <= CELL * 2 && d < nearestDist) { nearestDist = d; nearest = enemy; }
                }
                if (!nearest) break;
                chainTargets.push(nearest); currentTarget = nearest;
            }
            for (let j = 0; j < chainTargets.length; j++) {
                const dmg = tower.effDamage * Math.pow(tower.damageFalloff, j);
                chainTargets[j].hp -= dmg;
                tower.totalDamage += dmg;
                recordDamage(dmg);
                spawnParticles(chainTargets[j].x, chainTargets[j].y, '#a569bd', 6);
            }
            for (let j = 0; j < chainTargets.length; j++) {
                const from = j === 0 ? { x: cx, y: cy } : chainTargets[j - 1];
                game.lightningArcs.push({ x1: from.x, y1: from.y, x2: chainTargets[j].x, y2: chainTargets[j].y, life: 0.25, maxLife: 0.25 });
            }
            tower.cooldown = 1.0 / tower.effFireRate;
            continue;
        }
    }

    checkEnemyDeaths();

    // ── Projectile update with pass-through ────────────────────────
    for (let i = game.projectiles.length - 1; i >= 0; i--) {
        const p = game.projectiles[i];
        if (!p.alive) { game.projectiles.splice(i, 1); continue; }

        const traveled = Math.hypot(p.x - p.startX, p.y - p.startY);
        if (traveled > p.maxTravelDist) {
            p.alive = false;
            game.projectiles.splice(i, 1);
            continue;
        }

        let target = null;
        if (p.target && p.target.alive && !p.hitEnemyIds.has(p.target.id)) {
            target = p.target;
        } else {
            let bestDist = Infinity;
            for (const enemy of game.enemies) {
                if (!enemy.alive || p.hitEnemyIds.has(enemy.id)) continue;
                const d = Math.hypot(enemy.x - p.x, enemy.y - p.y);
                if (d < bestDist) {
                    bestDist = d;
                    target = enemy;
                }
            }
            if (target) {
                p.target = target;
                p.targetId = target.id;
            }
        }

        if (!target) {
            p.x += p.vx * dtScaled;
            p.y += p.vy * dtScaled;
            if (p.x < -30 || p.x > CANVAS + 30 || p.y < -30 || p.y > CANVAS + 30) {
                p.alive = false;
                game.projectiles.splice(i, 1);
            }
            continue;
        }

        const dx = target.x - p.x;
        const dy = target.y - p.y;
        const dist = Math.hypot(dx, dy);

        if (dist < p.radius + target.size + 4) {
            applyDamage(p, target);
            p.hitEnemyIds.add(target.id);
            p.pierceCount--;
            p.target = null;

            if (p.pierceCount <= 0) {
                p.alive = false;
                game.projectiles.splice(i, 1);
                continue;
            }
            p.x += p.vx * dtScaled;
            p.y += p.vy * dtScaled;
        } else {
            const move = Math.min(p.speed * dtScaled, dist);
            p.x += (dx / dist) * move;
            p.y += (dy / dist) * move;
            p.vx = (dx / dist) * p.speed;
            p.vy = (dy / dist) * p.speed;

            for (const enemy of game.enemies) {
                if (!enemy.alive || enemy === target || p.hitEnemyIds.has(enemy.id)) continue;
                const edx = enemy.x - p.x;
                const edy = enemy.y - p.y;
                const edist = Math.hypot(edx, edy);
                if (edist <= p.radius + enemy.size + 3) {
                    applyDamage(p, enemy);
                    p.hitEnemyIds.add(enemy.id);
                    p.pierceCount--;
                    if (p.pierceCount <= 0) {
                        p.alive = false;
                        break;
                    }
                }
            }
            if (!p.alive || p.pierceCount <= 0) {
                game.projectiles.splice(i, 1);
            }
        }
    }

    for (let i = game.particles.length - 1; i >= 0; i--) {
        const p = game.particles[i]; p.life -= dtScaled;
        p.x += p.vx * dtScaled; p.y += p.vy * dtScaled; p.vy += 80 * dtScaled;
        p.size *= (1 - dtScaled * 2);
        if (p.life <= 0 || p.size < 0.5) game.particles.splice(i, 1);
    }
    for (let i = game.stunEffects.length - 1; i >= 0; i--) { game.stunEffects[i].life -= dtScaled; if (game.stunEffects[i].life <= 0) game.stunEffects.splice(i, 1); }
    for (let i = game.beamEffects.length - 1; i >= 0; i--) { game.beamEffects[i].life -= dtScaled; if (game.beamEffects[i].life <= 0) game.beamEffects.splice(i, 1); }
    for (let i = game.lightningArcs.length - 1; i >= 0; i--) { game.lightningArcs[i].life -= dtScaled; if (game.lightningArcs[i].life <= 0) game.lightningArcs.splice(i, 1); }
    for (let i = game.pulseEffects.length - 1; i >= 0; i--) { game.pulseEffects[i].life -= dtScaled; if (game.pulseEffects[i].life <= 0) game.pulseEffects.splice(i, 1); }

    if (game.moneyNoteQueue && game.moneyNoteQueue.length > 0) {
        game.moneyNoteSpawnTimer -= dt;
        while (game.moneyNoteSpawnTimer <= 0 && game.moneyNoteQueue.length > 0) {
            const noteData = game.moneyNoteQueue.shift();
            game.moneyNotes.push({
                x: noteData.x,
                y: noteData.y,
                vx: noteData.vx,
                vy: noteData.vy,
                life: noteData.life,
                maxLife: noteData.maxLife,
                size: noteData.size,
                rotation: noteData.rotation,
                rotationSpeed: noteData.rotationSpeed
            });
            game.moneyNoteSpawnTimer += 0.028;
        }
    }

    for (let i = game.moneyNotes.length - 1; i >= 0; i--) {
        const n = game.moneyNotes[i];
        n.life -= dt;
        n.x += n.vx * dt;
        n.y += n.vy * dt;
        n.vy += 140 * dt;
        n.rotation += n.rotationSpeed * dt;
        if (n.life <= 0) game.moneyNotes.splice(i, 1);
    }
    if (game.moneyNoteTotal) {
        game.moneyNoteTotal.life -= dt;
        game.moneyNoteTotal.y -= 20 * dt;
        if (game.moneyNoteTotal.life <= 0) game.moneyNoteTotal = null;
    }

    refreshSelectedInfo();
}

// ── Game loop ─────────────────────────────────────────────────────
function gameLoop(timestamp) {
    if (!game) { ctx.fillStyle = '#1a2332'; ctx.fillRect(0, 0, CANVAS, CANVAS); requestAnimationFrame(gameLoop); return; }
    const dt = Math.min((timestamp - lastTime) / 1000, 0.05); lastTime = timestamp;
    if (!game.gameOver) updateStats(dt);
    render(); requestAnimationFrame(gameLoop);
}

// ── Initialisation ────────────────────────────────────────────────
function init() {
    difficultyOverlay.classList.add('show'); gameOverOverlay.classList.remove('show');
    difficultyBadge.style.display = 'none'; seedDisplayPanel.style.display = 'none';
    refreshOverlaySeed(); ctx.fillStyle = '#1a2332'; ctx.fillRect(0, 0, CANVAS, CANVAS);
    lastTime = performance.now(); requestAnimationFrame(gameLoop);
}

// ═══════════════════════════════════════════════════════════════════
// EVENT LISTENERS
// ═══════════════════════════════════════════════════════════════════

// ── Canvas mouse handlers ─────────────────────────────────────────
canvas.addEventListener('mousedown', function(e) {
    if (!game || !game.difficulty || game.gameOver) return;
    if (e.button !== 0) return;

    const pos = getGridPos(e);
    if (!pos.valid) return;

    const clickedEnemy = game.enemies.find(en => {
        if (!en.alive) return false;
        const dx = pos.x - en.x;
        const dy = pos.y - en.y;
        return Math.hypot(dx, dy) <= en.size + 6;
    });
    if (clickedEnemy) {
        deselectTowerType();
        game.selectedTower = null;
        game.selectedEnemy = clickedEnemy;
        showEnemyInfo(clickedEnemy);
        render();
        return;
    }

    const clickedTower = game.towers.find(t => t.col === pos.col && t.row === pos.row);
    if (clickedTower) {
        game.selectedEnemy = null;
        deselectTowerType();
        showInfo(clickedTower);
        render();
        return;
    }

    if (!game.selectedTowerType) {
        if (game.selectedEnemy) {
            game.selectedEnemy = null;
            showInfo(null);
            render();
        }
        return;
    }

    if (!game.buildable[pos.row] || !game.buildable[pos.row][pos.col]) return;
    if (game.towers.some(t => t.col === pos.col && t.row === pos.row)) return;

    if (game.selectedTowerType === 'steamRoller') {
        const rollerCount = game.towers.filter(t => t.type === 'steamRoller').length;
        if (rollerCount >= MAX_STEAM_ROLLERS) return;
    }

    const defs = TOWER_TYPES[game.selectedTowerType];
    const placementCost = game.sandboxMode ? 0 : defs.levels[0].cost;
    if (game.money < placementCost) return;

    isDragging = true;
    dragPlacedCells = new Set();
    justDragged = false;
    game.selectedEnemy = null;
    attemptPlaceTower(pos.col, pos.row);
    render();
});

canvas.addEventListener('mousedown', function(e) {
    if (game && game.selectedTowerType) {
        e.preventDefault();
    }
});

canvas.addEventListener('mousemove', function(e) {
    if (!game || !game.difficulty || game.gameOver) return;
    const pos = getGridPos(e);
    mouseGrid = pos;

    if (isDragging && game.selectedTowerType && pos.valid) {
        attemptPlaceTower(pos.col, pos.row);
        render();
        if (game.selectedTowerType) {
            const canPlace = game.buildable[pos.row] && game.buildable[pos.row][pos.col];
            const occupied = game.towers.some(t => t.col === pos.col && t.row === pos.row);
            let limitReached = false;
            if (game.selectedTowerType === 'steamRoller') {
                const rollerCount = game.towers.filter(t => t.type === 'steamRoller').length;
                limitReached = rollerCount >= MAX_STEAM_ROLLERS;
            }
            const defs = TOWER_TYPES[game.selectedTowerType];
            const placementCost = game.sandboxMode ? 0 : defs.levels[0].cost;
            const canAfford = game.money >= placementCost;
            previewValid = canPlace && !occupied && !limitReached && canAfford;
            const cx = pos.col * CELL + CELL / 2, cy = pos.row * CELL + CELL / 2;
            const alpha = 0.25;
            if (limitReached || !canAfford) {
                ctx.fillStyle = 'rgba(231, 76, 60, ' + alpha + ')';
            } else {
                ctx.fillStyle = previewValid ? 'rgba(46, 204, 113, ' + alpha + ')' : 'rgba(231, 76, 60, ' + alpha + ')';
            }
            ctx.fillRect(pos.col * CELL, pos.row * CELL, CELL, CELL);
            if (previewValid) {
                ctx.strokeStyle = 'rgba(46, 204, 113, 0.6)'; ctx.setLineDash([4, 4]);
                ctx.strokeRect(pos.col * CELL, pos.row * CELL, CELL, CELL); ctx.setLineDash([]);
            }
            if (limitReached) {
                ctx.fillStyle = 'rgba(231,76,60,0.9)';
                ctx.font = 'bold 10px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText('MAX ' + MAX_STEAM_ROLLERS, cx, cy);
            } else if (!canAfford) {
                ctx.fillStyle = 'rgba(231,76,60,0.9)';
                ctx.font = 'bold 10px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText('$' + placementCost, cx, cy);
            }
        }
        return;
    }

    if (game.selectedTowerType && pos.valid && !isDragging) {
        const canPlace = game.buildable[pos.row] && game.buildable[pos.row][pos.col];
        const occupied = game.towers.some(t => t.col === pos.col && t.row === pos.row);
        let limitReached = false;
        if (game.selectedTowerType === 'steamRoller') {
            const rollerCount = game.towers.filter(t => t.type === 'steamRoller').length;
            limitReached = rollerCount >= MAX_STEAM_ROLLERS;
        }
        previewValid = canPlace && !occupied && !limitReached;
        render();
        const cx = pos.col * CELL + CELL / 2, cy = pos.row * CELL + CELL / 2;
        if (limitReached) {
            ctx.fillStyle = 'rgba(231, 76, 60, 0.35)';
        } else {
            ctx.fillStyle = previewValid ? 'rgba(46, 204, 113, 0.25)' : 'rgba(231, 76, 60, 0.25)';
        }
        ctx.fillRect(pos.col * CELL, pos.row * CELL, CELL, CELL);
        if (previewValid) {
            ctx.strokeStyle = 'rgba(46, 204, 113, 0.6)'; ctx.setLineDash([4, 4]);
            ctx.strokeRect(pos.col * CELL, pos.row * CELL, CELL, CELL); ctx.setLineDash([]);
            const defs = TOWER_TYPES[game.selectedTowerType];
            if (defs && defs.levels[0].range && game.selectedTowerType !== 'steamRoller') {
                const range = defs.levels[0].range * CELL;
                ctx.beginPath(); ctx.arc(cx, cy, range, 0, Math.PI * 2);
                if (isBuffTower(game.selectedTowerType)) {
                    ctx.strokeStyle = 'rgba(200, 150, 220, 0.25)';
                    ctx.lineWidth = 1.5;
                    ctx.setLineDash([3, 5]);
                } else if (game.selectedTowerType === 'ice') {
                    ctx.strokeStyle = 'rgba(133,193,233,0.2)';
                    ctx.lineWidth = 1.5;
                    ctx.setLineDash([4, 6]);
                } else {
                    ctx.strokeStyle = 'rgba(255,255,255,0.1)';
                    ctx.lineWidth = 1;
                }
                ctx.stroke(); ctx.setLineDash([]);
            }
        }
        if (limitReached) {
            ctx.fillStyle = 'rgba(231,76,60,0.9)';
            ctx.font = 'bold 10px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('MAX ' + MAX_STEAM_ROLLERS, cx, cy);
        }
    }
});

canvas.addEventListener('mouseup', function(e) {
    if (isDragging) {
        justDragged = true;
        if (dragPlacedCells && dragPlacedCells.size > 0) {
            const lastKey = [...dragPlacedCells].pop();
            if (lastKey) {
                const [col, row] = lastKey.split(',').map(Number);
                const lastTower = game.towers.find(t => t.col === col && t.row === row);
                if (lastTower) {
                    showInfo(lastTower);
                }
            }
        }
        setTimeout(function() { justDragged = false; }, 0);
    }
    isDragging = false;
    dragPlacedCells = null;
});

canvas.addEventListener('mouseleave', function() {
    if (isDragging) {
        isDragging = false;
        dragPlacedCells = null;
    }
    mouseGrid.valid = false;
    render();
});

canvas.addEventListener('click', function(e) {
    if (!game || !game.difficulty || game.gameOver) return;
    if (justDragged) return;

    const pos = getGridPos(e);
    if (!pos.valid) return;

    const clickedEnemy = game.enemies.find(en => {
        if (!en.alive) return false;
        const dx = pos.x - en.x;
        const dy = pos.y - en.y;
        return Math.hypot(dx, dy) <= en.size + 6;
    });
    if (clickedEnemy) {
        deselectTowerType();
        game.selectedTower = null;
        game.selectedEnemy = clickedEnemy;
        showEnemyInfo(clickedEnemy);
        render();
        return;
    }

    const clickedTower = game.towers.find(t => t.col === pos.col && t.row === pos.row);
    if (clickedTower) {
        game.selectedEnemy = null;
        deselectTowerType();
        showInfo(clickedTower);
        render();
        return;
    }

    if (!game.selectedTowerType) {
        if (game.selectedEnemy) {
            game.selectedEnemy = null;
            showInfo(null);
            render();
        }
    }
});

// ── Canvas touch handlers ─────────────────────────────────────────
canvas.addEventListener('touchstart', function(e) {
    if (!game || !game.difficulty || game.gameOver) return;
    if (!game.selectedTowerType) return;
    e.preventDefault();

    const touch = e.touches[0];
    const pos = getGridPos(touch);
    if (!pos.valid) return;

    const clickedEnemy = game.enemies.find(en => {
        if (!en.alive) return false;
        const dx = pos.x - en.x;
        const dy = pos.y - en.y;
        return Math.hypot(dx, dy) <= en.size + 6;
    });
    if (clickedEnemy) {
        deselectTowerType();
        game.selectedTower = null;
        game.selectedEnemy = clickedEnemy;
        showEnemyInfo(clickedEnemy);
        render();
        return;
    }

    const clickedTower = game.towers.find(t => t.col === pos.col && t.row === pos.row);
    if (clickedTower) {
        game.selectedEnemy = null;
        deselectTowerType();
        showInfo(clickedTower);
        render();
        return;
    }

    if (!game.buildable[pos.row] || !game.buildable[pos.row][pos.col]) return;
    if (game.towers.some(t => t.col === pos.col && t.row === pos.row)) return;

    if (game.selectedTowerType === 'steamRoller') {
        const rollerCount = game.towers.filter(t => t.type === 'steamRoller').length;
        if (rollerCount >= MAX_STEAM_ROLLERS) return;
    }

    const defs = TOWER_TYPES[game.selectedTowerType];
    const placementCost = game.sandboxMode ? 0 : defs.levels[0].cost;
    if (game.money < placementCost) return;

    isDragging = true;
    dragPlacedCells = new Set();
    justDragged = false;
    game.selectedEnemy = null;
    attemptPlaceTower(pos.col, pos.row);
    render();
}, { passive: false });

canvas.addEventListener('touchmove', function(e) {
    if (!isDragging) return;
    e.preventDefault();

    const touch = e.touches[0];
    const pos = getGridPos(touch);
    if (!pos.valid) return;
    mouseGrid = pos;

    if (game && game.selectedTowerType) {
        attemptPlaceTower(pos.col, pos.row);
        render();

        if (game.selectedTowerType) {
            const canPlace = game.buildable[pos.row] && game.buildable[pos.row][pos.col];
            const occupied = game.towers.some(t => t.col === pos.col && t.row === pos.row);
            const defs = TOWER_TYPES[game.selectedTowerType];
            const placementCost = game.sandboxMode ? 0 : defs.levels[0].cost;
            const canAfford = game.money >= placementCost;
            previewValid = canPlace && !occupied && canAfford;
            ctx.fillStyle = previewValid ? 'rgba(46, 204, 113, 0.25)' : 'rgba(231, 76, 60, 0.25)';
            ctx.fillRect(pos.col * CELL, pos.row * CELL, CELL, CELL);
        }
    }
}, { passive: false });

canvas.addEventListener('touchend', function(e) {
    if (isDragging) {
        justDragged = true;
        if (dragPlacedCells && dragPlacedCells.size > 0) {
            const lastKey = [...dragPlacedCells].pop();
            if (lastKey) {
                const [col, row] = lastKey.split(',').map(Number);
                const lastTower = game.towers.find(t => t.col === col && t.row === row);
                if (lastTower) {
                    showInfo(lastTower);
                }
            }
        }
        setTimeout(function() { justDragged = false; }, 0);
    }
    isDragging = false;
    dragPlacedCells = null;
});

// ── Tower button handlers ─────────────────────────────────────────
document.querySelectorAll('.tower-btn').forEach(btn => {
    btn.addEventListener('click', function() {
        if (!game || !game.difficulty || game.gameOver) return;
        const type = this.dataset.type;
        const defs = TOWER_TYPES[type]; if (!defs) return;
        const checkCost = game.sandboxMode ? 0 : defs.levels[0].cost;
        if (game.money < checkCost) return;

        if (type === 'steamRoller') {
            const rollerCount = game.towers.filter(t => t.type === 'steamRoller').length;
            if (rollerCount >= MAX_STEAM_ROLLERS) {
                infoBox.innerHTML = '<div style="color:#e74c3c;">🚫 Maximum of ' + MAX_STEAM_ROLLERS + ' Steam Roller towers reached! Delete an existing one to place another.</div>';
                return;
            }
        }

        if (game.selectedTowerType === type) { deselectTowerType(); showInfo(null); }
        else {
            deselectTowerType(); game.selectedTowerType = type; this.classList.add('selected');
            game.selectedTower = null; game.selectedEnemy = null;
            upgradeBtn.style.display = 'none';
            targetBtns.style.display = 'none'; deleteBtn.style.display = 'none';
            const lvl0 = defs.levels[0];
            const displayCost = game.sandboxMode ? 0 : lvl0.cost;
            let extra = '';
            if (isBuffTower(type)) {
                const buffPct = Math.round(lvl0.buffValue * 100);
                extra = '<div>📡 Buff Range: ' + lvl0.range.toFixed(1) + ' cells</div>';
                if (type === 'rangeBuff') extra += '<div>📏 +' + buffPct + '% range to nearby attack towers</div>';
                else if (type === 'speedBuff') extra += '<div>⏩ +' + buffPct + '% fire rate to nearby attack towers</div>';
                else extra += '<div>💥 +' + buffPct + '% damage to nearby attack towers</div>';
            } else if (type === 'mint') {
                extra = '<div>💰 Income: $' + lvl0.income + ' per wave</div>';
                if (game.sandboxMode) extra += '<div style="color:#c39bdb;font-size:10px;">🛠️ Sandbox mode</div>';
                else if (game.difficulty === 'hard') extra += '<div style="color:#e74c3c;font-size:10px;">⚠ Hard mode: 20% income penalty</div>';
            } else if (type === 'steamRoller') {
                const rollerCount = game.towers.filter(t => t.type === 'steamRoller').length;
                extra = '<div>🚂 Roller HP: ' + lvl0.hp + '</div>';
                extra += '<div>⏱ Spawns every: ' + lvl0.cooldown + 's</div>';
                extra += '<div>🏗️ Roller towers: ' + rollerCount + ' / ' + MAX_STEAM_ROLLERS + '</div>';
                extra += '<div style="margin-top:3px;font-size:10px;">💥 Spawns a new roller every ' + lvl0.cooldown + 's at the path end. Multiple rollers can be active at once. Crushes enemies on contact. Max ' + MAX_STEAM_ROLLERS + ' per map.</div>';
            }
            else if (type === 'arrow') extra = '<div>DMG: ' + lvl0.damage + '  Range: ' + lvl0.range + '</div><div>🎯 Fast piercing shots — passes through 3 enemies.</div>';
            else if (type === 'cannon') extra = '<div>DMG: ' + lvl0.damage + '  Range: ' + lvl0.range + '</div><div>💥 Splash damage — ' + lvl0.splash.toFixed(1) + ' tile radius. Pierces through 2 enemies.</div>';
            else if (type === 'ice') extra = '<div>DMG: ' + lvl0.damage + '  Range: ' + lvl0.range + '</div><div>❄️ AOE pulse — slows all enemies in range.</div>';
            else if (type === 'sniper') extra = '<div>DMG: ' + lvl0.damage + '  Range: ' + lvl0.range + '</div><div>🔫 Piercing beam — ' + Math.round((1 - lvl0.pierceFalloff) * 100) + '% falloff per target. Beam stops at last enemy hit.</div>';
            else if (type === 'tesla') extra = '<div>DMG: ' + lvl0.damage + '  Range: ' + lvl0.range + '</div><div>⚡ Chain lightning — jumps to ' + lvl0.chain + ' more targets, ' + Math.round((1 - lvl0.damageFalloff) * 100) + '% falloff per jump.</div>';
            const costLabel = game.sandboxMode ? '$0 (Sandbox)' : '$' + displayCost;
            infoBox.innerHTML = '<div class="highlight">' + defs.emoji + ' ' + defs.name + ' Tower</div><div>Cost: <span style="color:#f1c40f;">' + costLabel + '</span></div>' + extra + '<div>Hold and drag on empty grid cells to build rows!</div>';
        }
        render();
    });
});

// ── Target mode buttons ───────────────────────────────────────────
targetBtns.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', function() {
        if (!game || !game.difficulty || !game.selectedTower) return;
        game.selectedTower.targetMode = this.dataset.mode; showInfo(game.selectedTower); render();
    });
});

// ── UI button handlers ────────────────────────────────────────────
waveBtn.addEventListener('click', startWave);
newMapBtn.addEventListener('click', function() { if (game && game.difficulty && !game.waveActive && game.wave === 0 && !game.gameOver) regenerateMap(generateRandomSeed()); });
pauseBtn.addEventListener('click', function() { if (!game || !game.difficulty || game.gameOver) return; game.paused = !game.paused; this.textContent = game.paused ? '▶' : '⏸'; });
speedBtn.addEventListener('click', function() { if (!game || !game.difficulty || game.gameOver) return; game.speedIndex = (game.speedIndex + 1) % SPEED_PRESETS.length; game.speed = SPEED_PRESETS[game.speedIndex]; this.textContent = SPEED_LABELS[game.speedIndex]; });
restartBtn.addEventListener('click', restartGame);

upgradeAllCheapBtn.addEventListener('click', upgradeAllCheapestFirst);
upgradeAllExpensiveBtn.addEventListener('click', upgradeAllMostExpensiveFirst);

// ── Seed overlay handlers ─────────────────────────────────────────
seedLoadOverlay.addEventListener('click', function() { const h = seedInputOverlay.value.trim(); if (!h) { showSeedError('Enter a seed value.'); return; } tryLoadSeed(h, true); });
seedRandomOverlay.addEventListener('click', function() { pendingSeed = generateRandomSeed(); refreshOverlaySeed(); });
seedInputOverlay.addEventListener('input', function() { clearSeedError(); seedInputOverlay.classList.remove('invalid'); });
seedInputOverlay.addEventListener('keydown', function(e) { if (e.key === 'Enter') { e.preventDefault(); const h = seedInputOverlay.value.trim(); if (!h) { showSeedError('Enter a seed value.'); return; } tryLoadSeed(h, true); } });
seedCopyBtn.addEventListener('click', function() {
    if (!game || !game.seed) return; const hex = seedToHex(game.seed);
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(hex).then(function() {
            seedCopyBtn.textContent = '✓'; seedCopyBtn.classList.add('copied');
            setTimeout(function() { seedCopyBtn.textContent = '📋'; seedCopyBtn.classList.remove('copied'); }, 1500);
        }).catch(function() { fallbackCopy(hex); });
    } else fallbackCopy(hex);
});

// ── Difficulty selection handlers ─────────────────────────────────
diffBtns.forEach(btn => {
    btn.addEventListener('click', function() {
        let diff;
        if (this.classList.contains('easy-btn')) diff = 'easy';
        else if (this.classList.contains('med-btn')) diff = 'medium';
        else if (this.classList.contains('hard-btn')) diff = 'hard';
        else if (this.classList.contains('sandbox-btn')) { applySandbox(); return; }
        else return;
        if (!game) {
            game = createGame(); game.seed = pendingSeed;
            const prng = mulberry32(pendingSeed); const mapData = generatePath(prng);
            const validation = validatePathData(mapData);
            if (!validation.valid) {
                const fb = generatePath(null);
                game.path = fb.path; game.buildable = fb.buildable;
                game.pathStart = fb.start; game.pathEnd = fb.end;
                game.splits = fb.splits || []; game.splitMap = fb.splitMap || {};
                game.allPathCellSet = fb.allPathCellSet || new Set(game.path.map(([x, y]) => x + ',' + y));
                game.loopCount = fb.loopCount || 0; game.splitCount = fb.splitCount || 0;
                game.seed = generateRandomSeed(); pendingSeed = game.seed;
            } else {
                game.path = mapData.path; game.buildable = mapData.buildable;
                game.pathStart = mapData.start; game.pathEnd = mapData.end;
                game.splits = mapData.splits || []; game.splitMap = mapData.splitMap || {};
                game.allPathCellSet = mapData.allPathCellSet || new Set(game.path.map(([x, y]) => x + ',' + y));
                game.loopCount = mapData.loopCount || 0; game.splitCount = mapData.splitCount || 0;
            }
        }
        applyDifficulty(diff);
    });
});

// ── Start ─────────────────────────────────────────────────────────
init();
