'use strict';

function recordDamage(amount) {
    if (!game || amount <= 0) return;
    game.damageEvents.push({ time: game.animTime, amount: amount });
}

function computeTotalDps() {
    if (!game) return 0;
    const now = game.animTime;
    const events = game.damageEvents;
    let cutoff = 0;
    while (cutoff < events.length && events[cutoff].time < now - DPS_WINDOW) {
        cutoff++;
    }
    if (cutoff > 0) {
        events.splice(0, cutoff);
    }
    let total = 0;
    for (let i = 0; i < events.length; i++) {
        total += events[i].amount;
    }
    return total / DPS_WINDOW;
}

function dealDamage(target, amount, tower) {
    if (!target) return;
    if (target.type === 'purpleBlock') {
        // Purple blocks take exactly 1 hit per attack regardless of damage
        target.hitsRemaining = Math.max(0, target.hitsRemaining - 1);
        if (target.hitsRemaining <= 0) {
            target.hp = 0;
        }
        if (tower) tower.totalDamage += 1;
        recordDamage(1);
        return;
    }
    target.hp -= amount;
    if (tower) tower.totalDamage += amount;
    recordDamage(amount);
}

function computeTowerBuffs() {
    const buffs = new Array(game.towers.length);
    for (let i = 0; i < game.towers.length; i++) {
        buffs[i] = { rangeBuff: 0, speedBuff: 0, damageBuff: 0 };
        const tower = game.towers[i];
        if (!isAttackTower(tower.type)) continue;
        const cx = tower.col * CELL + CELL / 2;
        const cy = tower.row * CELL + CELL / 2;
        for (let j = 0; j < game.towers.length; j++) {
            const bt = game.towers[j];
            if (!isBuffTower(bt.type)) continue;
            const bx = bt.col * CELL + CELL / 2;
            const by = bt.row * CELL + CELL / 2;
            const dist = Math.hypot(cx - bx, cy - by);
            if (dist <= bt.range * CELL + 0.5) {
                if (bt.type === 'rangeBuff') buffs[i].rangeBuff += bt.buffValue;
                else if (bt.type === 'speedBuff') buffs[i].speedBuff += bt.buffValue;
                else if (bt.type === 'attackBuff') buffs[i].damageBuff += bt.buffValue;
            }
        }
    }
    return buffs;
}

function applyEffectiveStats(towerBuffs) {
    for (let i = 0; i < game.towers.length; i++) {
        const tower = game.towers[i];
        const b = towerBuffs[i];
        if (isAttackTower(tower.type)) {
            tower.effRange = tower.baseRange * (1 + b.rangeBuff);
            tower.effDamage = tower.baseDamage * (1 + b.damageBuff);
            tower.effFireRate = tower.baseFireRate * (1 + b.speedBuff);
        } else {
            tower.effRange = tower.range || 0;
            tower.effDamage = tower.damage || 0;
            tower.effFireRate = tower.fireRate || 0;
        }
    }
}

function countAffectedTowers(buffTower) {
    let count = 0;
    const bx = buffTower.col * CELL + CELL / 2;
    const by = buffTower.row * CELL + CELL / 2;
    for (const tower of game.towers) {
        if (tower === buffTower) continue;
        if (!isAttackTower(tower.type)) continue;
        const cx = tower.col * CELL + CELL / 2;
        const cy = tower.row * CELL + CELL / 2;
        if (Math.hypot(cx - bx, cy - by) <= buffTower.range * CELL + 0.5) count++;
    }
    return count;
}

function applyDamage(proj, target) {
    const tower = game.towers.find(function(t) { return t.id === proj.towerId; });

    dealDamage(target, proj.damage, tower);

    if (proj.slow > 0) { target.slowFactor = 1.0 - proj.slow; target.slowTimer = 1.5; }
    if (proj.splash > 0) {
        const sr = proj.splash * CELL;
        for (const enemy of game.enemies) {
            if (enemy === target || !enemy.alive) continue;
            const d = Math.hypot(enemy.x - target.x, enemy.y - target.y);
            if (d <= sr) {
                const splashDmg = proj.damage * (1 - d / sr * 0.5) * 0.6;
                dealDamage(enemy, splashDmg, tower);
            }
        }
        spawnParticles(target.x, target.y, '#e67e22', 12);
    }
    if (proj.chain > 1) {
        let chainTargets = [target], ct = target;
        for (let c = 1; c < proj.chain; c++) {
            let nearest = null, nd = Infinity;
            for (const enemy of game.enemies) {
                if (!enemy.alive || chainTargets.includes(enemy)) continue;
                const d = Math.hypot(enemy.x - ct.x, enemy.y - ct.y);
                if (d <= CELL * 3 && d < nd) { nd = d; nearest = enemy; }
            }
            if (!nearest) break;
            const chainDmg = proj.damage * (1 - c * 0.15);
            dealDamage(nearest, chainDmg, tower);
            chainTargets.push(nearest); ct = nearest;
            spawnParticles(nearest.x, nearest.y, '#a569bd', 6);
        }
        spawnParticles(target.x, target.y, '#a569bd', 10);
    }
    for (let i = game.enemies.length - 1; i >= 0; i--) {
        const e = game.enemies[i];
        if (e.alive && e.hp <= 0) { e.alive = false; e.reachedEnd = false; if (game.selectedEnemy === e) { game.selectedEnemy = null; showInfo(null); } spawnParticles(e.x, e.y, e.color, 8); }
    }
    spawnParticles(target.x, target.y, '#f39c12', 4);
}

function checkEnemyDeaths() {
    for (let i = game.enemies.length - 1; i >= 0; i--) {
        const e = game.enemies[i];
        if (e.alive && e.hp <= 0) {
            e.alive = false; e.reachedEnd = false;
            if (game.selectedEnemy === e) { game.selectedEnemy = null; showInfo(null); }
            spawnParticles(e.x, e.y, e.color, 8);
        }
    }
}
