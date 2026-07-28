'use strict';

// DOM ELEMENT REFERENCES
const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');
canvas.width = CANVAS;
canvas.height = CANVAS;

const moneyEl = document.getElementById('money-val');
const livesEl = document.getElementById('lives-val');
const waveEl = document.getElementById('wave-val');
const killEl = document.getElementById('kill-val');
const towerCountEl = document.getElementById('tower-count-val');
const dpsEl = document.getElementById('dps-val');
const infoBox = document.getElementById('info-box');
const upgradeBtn = document.getElementById('upgrade-btn');
const targetBtns = document.getElementById('target-btns');
const deleteBtn = document.getElementById('delete-btn');
const waveBtn = document.getElementById('wave-btn');
const newMapBtn = document.getElementById('new-map-btn');
const pauseBtn = document.getElementById('pause-btn');
const speedBtn = document.getElementById('speed-btn');
const restartBtn = document.getElementById('restart-btn');
const gameOverOverlay = document.getElementById('game-over-overlay');
const finalWaveEl = document.getElementById('final-wave');
const difficultyOverlay = document.getElementById('difficulty-overlay');
const difficultyBadge = document.getElementById('difficulty-badge');
const difficultyLabel = document.getElementById('difficulty-label');
const diffBtns = document.querySelectorAll('.diff-btn');
const upgradeAllCheapBtn = document.getElementById('upgrade-all-cheap-btn');
const upgradeAllExpensiveBtn = document.getElementById('upgrade-all-expensive-btn');

const seedInputOverlay = document.getElementById('seed-input-overlay');
const seedLoadOverlay = document.getElementById('seed-load-overlay');
const seedRandomOverlay = document.getElementById('seed-random-overlay');
const seedErrorOverlay = document.getElementById('seed-error-overlay');
const seedDisplayPanel = document.getElementById('seed-display-panel');
const seedDisplayText = document.getElementById('seed-display-text');
const seedCopyBtn = document.getElementById('seed-copy-btn');

function deselectTowerType() {
    if (!game) return;
    game.selectedTowerType = null;
    document.querySelectorAll('.tower-btn').forEach(b => b.classList.remove('selected'));
}

function updateUpgradeBtnState() {
    const tower = game.selectedTower;
    if (!tower) return;
    const defs = TOWER_TYPES[tower.type]; if (!defs) return;
    const nl = tower.level + 1;
    if (nl >= defs.levels.length) { upgradeBtn.disabled = true; upgradeBtn.style.opacity = '0.5'; upgradeBtn.textContent = '⭐ Max Level'; return; }
    const cost = game.sandboxMode ? 0 : defs.levels[nl].cost;
    const canAfford = game.sandboxMode || game.money >= cost;
    upgradeBtn.disabled = !canAfford; upgradeBtn.style.opacity = canAfford ? '1' : '0.4';
    upgradeBtn.textContent = '⬆ Upgrade to Lv.' + (tower.level + 2) + ' ($' + cost + ')';
}

function updateUI() {
    moneyEl.textContent = Math.floor(game.money);
    livesEl.textContent = game.lives; waveEl.textContent = game.wave;
    killEl.textContent = game.kills; towerCountEl.textContent = game.towers.length;
    const totalDps = computeTotalDps();
    dpsEl.textContent = totalDps >= 1000 ? (totalDps / 1000).toFixed(1) + 'k' : Math.round(totalDps);
    document.querySelectorAll('.tower-btn').forEach(btn => {
        const defs = TOWER_TYPES[btn.dataset.type];
        if (defs && defs.levels[0]) {
            let disabled = false;
            if (!game.sandboxMode && game.money < defs.levels[0].cost) disabled = true;
            if (btn.dataset.type === 'steamRoller') {
                const rollerCount = game.towers.filter(t => t.type === 'steamRoller').length;
                if (rollerCount >= MAX_STEAM_ROLLERS) disabled = true;
            }
            btn.classList.toggle('disabled', disabled);
        }
    });
    updateUpgradeBtnState();
}

function showEnemyInfo(enemy) {
    if (!enemy || !enemy.alive) { showInfo(null); return; }
    game.selectedTower = null;
    upgradeBtn.style.display = 'none';
    targetBtns.style.display = 'none';
    deleteBtn.style.display = 'none';

    let abilities = [];
    if (enemy.type === 'boss') {
        abilities.push('⚡ Stuns nearest attack tower periodically (2.5s)');
        abilities.push('👾 Spawns minions periodically');
    } else if (enemy.type === 'fast') {
        abilities.push('🏃 High movement speed (2.4 base)');
    } else if (enemy.type === 'heavy') {
        abilities.push('🛡️ High HP (2.5×), slow speed');
    } else {
        abilities.push('Standard enemy — no special abilities');
    }

    let html = '<div class="enemy-highlight">' + enemy.name + ' Enemy</div>';
    html += '<div>❤️ HP: <span style="color:#f0f4ff;font-weight:600;">' + Math.ceil(enemy.hp) + '</span> / ' + enemy.maxHp + '</div>';
    if (enemy.slowFactor < 0.9) {
        html += '<div style="color:#85c1e9;">❄️ Slowed: ' + Math.round((1 - enemy.slowFactor) * 100) + '%</div>';
    }
    if (enemy.onBranch) {
        html += '<div style="color:#f39c12;">🔀 On alternate split path</div>';
    }
    html += '<div style="margin-top:4px;font-weight:600;">Abilities:</div>';
    for (const ab of abilities) {
        html += '<div style="font-size:10px;margin-left:4px;">' + ab + '</div>';
    }
    infoBox.innerHTML = html;
}

function getTowerBuffDetails(tower) {
    const towerIdx = game.towers.indexOf(tower);
    if (towerIdx === -1 || !isAttackTower(tower.type)) return null;
    const towerBuffs = computeTowerBuffs();
    const b = towerBuffs[towerIdx];
    if (b.rangeBuff === 0 && b.speedBuff === 0 && b.damageBuff === 0) return null;
    return b;
}

function showInfo(tower) {
    targetBtns.style.display = 'none'; deleteBtn.style.display = 'none';
    game.selectedEnemy = null;
    if (!tower) { infoBox.innerHTML = '<div>Select a tower to place it on the grid. Each tower has a unique attack style. Buff towers boost nearby attack towers. Hold and drag to build rows!<br><br>New: ⬆¢ / ⬆$ upgrade all towers at once!</div>'; upgradeBtn.style.display = 'none'; return; }
    const defs = TOWER_TYPES[tower.type]; if (!defs) return;
    const nextLvl = tower.level + 1 < defs.levels.length ? defs.levels[tower.level + 1] : null;

    if (isBuffTower(tower.type)) {
        let html = '<div class="buff-highlight">' + defs.emoji + ' ' + defs.name + ' (Lv.' + (tower.level + 1) + ')</div>';
        html += '<div>📡 Buff Range: ' + tower.range.toFixed(1) + ' cells</div>';
        const buffPct = Math.round(tower.buffValue * 100);
        if (tower.type === 'rangeBuff') html += '<div>📏 +' + buffPct + '% range to nearby attack towers</div>';
        else if (tower.type === 'speedBuff') html += '<div>⏩ +' + buffPct + '% fire rate to nearby attack towers</div>';
        else html += '<div>💥 +' + buffPct + '% damage to nearby attack towers</div>';
        const affected = countAffectedTowers(tower);
        html += '<div>🎯 Affecting: <span style="color:#f0f4ff;font-weight:600;">' + affected + '</span> attack tower' + (affected !== 1 ? 's' : '') + '</div>';
        if (game.sandboxMode) html += '<div style="color:#c39bdb;font-size:10px;">🛠️ Sandbox mode — all costs $0</div>';
        else if (game.difficulty === 'hard') html += '<div style="color:#e74c3c;font-size:10px;">⚠ Hard mode: 20% income penalty</div>';
        infoBox.innerHTML = html;
        deleteBtn.style.display = 'block';
        deleteBtn.onclick = function() { deleteTower(tower); };
        if (nextLvl) {
            upgradeBtn.style.display = 'block';
            const cost = game.sandboxMode ? 0 : nextLvl.cost;
            const canAfford = game.sandboxMode || game.money >= cost;
            upgradeBtn.disabled = !canAfford; upgradeBtn.textContent = '⬆ Upgrade to Lv.' + (tower.level + 2) + ' ($' + cost + ')';
            upgradeBtn.style.opacity = canAfford ? '1' : '0.4'; game.selectedTower = tower;
            upgradeBtn.onclick = function() { if (upgradeTower(tower)) { showInfo(tower); render(); } else showInfo(tower); };
        } else { upgradeBtn.style.display = 'block'; upgradeBtn.disabled = true; upgradeBtn.textContent = '⭐ Max Level'; upgradeBtn.style.opacity = '0.5'; game.selectedTower = tower; upgradeBtn.onclick = null; }
        return;
    }

    if (tower.type === 'mint') {
        let html = '<div class="highlight">' + defs.emoji + ' ' + defs.name + ' (Lv.' + (tower.level + 1) + ')</div>';
        html += '<div>💰 Income: $' + tower.income + ' per wave</div>';
        if (game.sandboxMode) html += '<div style="color:#c39bdb;font-size:10px;">🛠️ Sandbox mode — all costs $0</div>';
        else if (game.difficulty === 'hard') html += '<div style="color:#e74c3c;font-size:10px;">⚠ Hard mode: 20% income penalty</div>';
        if (tower.stunTimer > 0) html += '<div style="color:#c39bdb;font-weight:600;">⚡ Stunned: ' + tower.stunTimer.toFixed(1) + 's remaining</div>';
        infoBox.innerHTML = html; deleteBtn.style.display = 'block';
        deleteBtn.onclick = function() { deleteTower(tower); };
        if (nextLvl) {
            upgradeBtn.style.display = 'block';
            const cost = game.sandboxMode ? 0 : nextLvl.cost;
            const canAfford = game.sandboxMode || game.money >= cost;
            upgradeBtn.disabled = !canAfford; upgradeBtn.textContent = '⬆ Upgrade to Lv.' + (tower.level + 2) + ' ($' + cost + ')';
            upgradeBtn.style.opacity = canAfford ? '1' : '0.4'; game.selectedTower = tower;
            upgradeBtn.onclick = function() { if (upgradeTower(tower)) { showInfo(tower); render(); } else showInfo(tower); };
        } else { upgradeBtn.style.display = 'block'; upgradeBtn.disabled = true; upgradeBtn.textContent = '⭐ Max Level'; upgradeBtn.style.opacity = '0.5'; game.selectedTower = tower; upgradeBtn.onclick = null; }
        return;
    }

    if (tower.type === 'steamRoller') {
        const activeRollers = game.steamRollers.filter(r => r.towerId === tower.id && r.alive);
        const rollerCount = game.towers.filter(t => t.type === 'steamRoller').length;
        let html = '<div class="roller-highlight">' + defs.emoji + ' ' + defs.name + ' (Lv.' + (tower.level + 1) + ')</div>';
        html += '<div>🚂 Roller HP: <span style="color:#f0f4ff;font-weight:600;">' + tower.rollerHp + '</span></div>';
        html += '<div>⚡ Speed: ' + tower.rollerSpeed.toFixed(1) + ' cells/s</div>';
        html += '<div>⏱ Spawns every: ' + tower.rollerCooldown + 's</div>';
        const cdRemaining = Math.max(0, tower.rollerSpawnTimer);
        html += '<div style="color:#f39c12;">⏳ Next roller in: ' + cdRemaining.toFixed(1) + 's</div>';
        html += '<div>💥 Total Damage: <span style="color:#f0f4ff;font-weight:600;">' + Math.floor(tower.totalDamage || 0) + '</span></div>';
        html += '<div>🏗️ Roller towers placed: <span style="color:#f0f4ff;font-weight:600;">' + rollerCount + ' / ' + MAX_STEAM_ROLLERS + '</span></div>';
        if (activeRollers.length > 0) {
            html += '<div style="color:#2ecc71;font-weight:600;">▶ ' + activeRollers.length + ' roller(s) active:</div>';
            for (const ar of activeRollers) {
                html += '<div style="font-size:10px;margin-left:6px;">• HP: ' + Math.ceil(ar.hp) + ' / ' + ar.maxHp + '</div>';
            }
        } else {
            html += '<div style="color:#888;">No active rollers</div>';
        }
        html += '<div style="margin-top:4px;font-size:10px;">💥 Spawns a new roller every ' + tower.rollerCooldown + 's at the path end. Travels backward, crushing enemies. Deals damage = enemy HP on contact and loses that much HP. Max ' + MAX_STEAM_ROLLERS + ' per map.</div>';
        if (game.sandboxMode) html += '<div style="color:#c39bdb;font-size:10px;">🛠️ Sandbox mode — all costs $0</div>';
        else if (game.difficulty === 'hard') html += '<div style="color:#e74c3c;font-size:10px;">⚠ Hard mode: 20% income penalty</div>';
        infoBox.innerHTML = html; deleteBtn.style.display = 'block';
        deleteBtn.onclick = function() { deleteTower(tower); };
        if (nextLvl) {
            upgradeBtn.style.display = 'block';
            const cost = game.sandboxMode ? 0 : nextLvl.cost;
            const canAfford = game.sandboxMode || game.money >= cost;
            upgradeBtn.disabled = !canAfford; upgradeBtn.textContent = '⬆ Upgrade to Lv.' + (tower.level + 2) + ' ($' + cost + ')';
            upgradeBtn.style.opacity = canAfford ? '1' : '0.4'; game.selectedTower = tower;
            upgradeBtn.onclick = function() { if (upgradeTower(tower)) { showInfo(tower); render(); } else showInfo(tower); };
        } else { upgradeBtn.style.display = 'block'; upgradeBtn.disabled = true; upgradeBtn.textContent = '⭐ Max Level'; upgradeBtn.style.opacity = '0.5'; game.selectedTower = tower; upgradeBtn.onclick = null; }
        return;
    }

    const modeLabels = { nearest: 'Nearest 🎯', first: 'First 🚩', last: 'Last 🏁', random: 'Random 🎲' };
    const buffs = getTowerBuffDetails(tower);

    let html = '<div class="highlight">' + defs.emoji + ' ' + defs.name + ' (Lv.' + (tower.level + 1) + ')</div>';
    if (tower.stunTimer > 0) html += '<div style="color:#c39bdb;font-weight:600;">⚡ STUNNED — ' + tower.stunTimer.toFixed(1) + 's remaining</div>';

    const effDmg = tower.effDamage || tower.damage;
    const effRng = tower.effRange || tower.range;
    const effRate = tower.effFireRate || tower.fireRate;

    let dmgStr = effDmg.toFixed(1);
    let rngStr = effRng.toFixed(2);
    let rateStr = effRate.toFixed(2);

    if (buffs) {
        if (buffs.damageBuff > 0) dmgStr += ' <span style="color:#f1948a;">(+' + Math.round(buffs.damageBuff * 100) + '%)</span>';
        if (buffs.rangeBuff > 0) rngStr += ' <span style="color:#5dade2;">(+' + Math.round(buffs.rangeBuff * 100) + '%)</span>';
        if (buffs.speedBuff > 0) rateStr += ' <span style="color:#2ecc71;">(+' + Math.round(buffs.speedBuff * 100) + '%)</span>';
    }

    html += '<div>DMG: ' + dmgStr + '  Range: ' + rngStr + '  Rate: ' + rateStr + '/s</div>';
    html += '<div>💥 Total Damage: <span style="color:#f0f4ff;font-weight:600;">' + Math.floor(tower.totalDamage || 0) + '</span></div>';
    if (tower.type === 'arrow') html += '<div>🎯 Fast single-target shots — pierces through 3 enemies!</div>';
    else if (tower.type === 'cannon') html += '<div>💥 Splash: ' + (tower.splash || 0).toFixed(1) + ' tile radius — pierces through 2 enemies!</div>';
    else if (tower.type === 'ice') html += '<div>❄️ AOE pulse — Slows ' + Math.round((tower.slow || 0) * 100) + '%</div>';
    else if (tower.type === 'sniper') {
        const falloffPct = Math.round((1 - tower.pierceFalloff) * 100);
        html += '<div>🔫 Piercing beam — ' + falloffPct + '% damage falloff per target. Beam stops at last enemy hit.</div>';
    }
    else if (tower.type === 'tesla') {
        const falloffPct = Math.round((1 - tower.damageFalloff) * 100);
        html += '<div>⚡ Chain lightning — jumps to ' + tower.chain + ' more, ' + falloffPct + '% falloff per jump</div>';
    }
    html += '<div>Target: ' + (modeLabels[tower.targetMode] || 'First') + '</div>';
    infoBox.innerHTML = html;

    if (tower.type !== 'ice') {
        targetBtns.style.display = 'flex';
        targetBtns.querySelectorAll('button').forEach(btn => btn.classList.toggle('active', btn.dataset.mode === tower.targetMode));
    }
    deleteBtn.style.display = 'block'; deleteBtn.onclick = function() { deleteTower(tower); };
    if (nextLvl) {
        upgradeBtn.style.display = 'block';
        const cost = game.sandboxMode ? 0 : nextLvl.cost;
        const canAfford = game.sandboxMode || game.money >= cost;
        upgradeBtn.disabled = !canAfford; upgradeBtn.textContent = '⬆ Upgrade to Lv.' + (tower.level + 2) + ' ($' + cost + ')';
        upgradeBtn.style.opacity = canAfford ? '1' : '0.4'; game.selectedTower = tower;
        upgradeBtn.onclick = function() { if (upgradeTower(tower)) { showInfo(tower); render(); } else showInfo(tower); };
    } else { upgradeBtn.style.display = 'block'; upgradeBtn.disabled = true; upgradeBtn.textContent = '⭐ Max Level'; upgradeBtn.style.opacity = '0.5'; game.selectedTower = tower; upgradeBtn.onclick = null; }
}

function refreshSelectedInfo() {
    if (game.selectedTower) {
        if (game.towers.includes(game.selectedTower)) {
            showInfo(game.selectedTower);
        } else {
            game.selectedTower = null;
            showInfo(null);
        }
    } else if (game.selectedEnemy) {
        if (game.selectedEnemy.alive && game.enemies.includes(game.selectedEnemy)) {
            showEnemyInfo(game.selectedEnemy);
        } else {
            game.selectedEnemy = null;
            showInfo(null);
        }
    }
}

function showSeedError(msg) { seedErrorOverlay.textContent = msg; seedInputOverlay.classList.add('invalid'); }
function clearSeedError() { seedErrorOverlay.textContent = ''; seedInputOverlay.classList.remove('invalid'); }
function updateSeedDisplay() {
    if (!game || !game.difficulty) { seedDisplayPanel.style.display = 'none'; return; }
    seedDisplayPanel.style.display = 'flex'; seedDisplayText.textContent = seedToHex(game.seed);
}
function refreshOverlaySeed() { seedInputOverlay.value = seedToHex(pendingSeed); clearSeedError(); seedInputOverlay.classList.remove('invalid'); }

function updateTowerCostLabels() {
    document.querySelectorAll('.tower-btn').forEach(btn => {
        const type = btn.dataset.type;
        const defs = TOWER_TYPES[type];
        if (!defs) return;
        const costSpan = btn.querySelector('.cost');
        if (costSpan) {
            costSpan.textContent = (game && game.sandboxMode) ? '$0' : ('$' + defs.levels[0].cost);
        }
    });
}

function fallbackCopy(text) {
    const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px';
    document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); } catch(e) {}
    document.body.removeChild(ta); seedCopyBtn.textContent = '✓'; seedCopyBtn.classList.add('copied');
    setTimeout(function() { seedCopyBtn.textContent = '📋'; seedCopyBtn.classList.remove('copied'); }, 1500);
}
