// player.js - Player creation, attacks, damage, weapon swap, game start/restart

function createPlayer() {
    const cx = Math.floor(MAP_COLS / 2) * TILE + TILE / 2, cy = Math.floor(MAP_ROWS / 2) * TILE + TILE / 2;
    let activeWep = 'staff';
    if (hasAnnihilator()) activeWep = 'annihilator';
    else if (hasMinigun() && !hasRailgun()) activeWep = 'minigun';
    else if (hasRailgun() && !hasMinigun()) activeWep = 'railgun';
    else if (hasMinigun() && hasRailgun()) activeWep = 'railgun';
    const maxHp = BASE_MAX_HP + getVitalityBonus();
    return { x: cx, y: cy, vx: 0, vy: 0, angle: 0, health: maxHp, maxHealth: maxHp, primaryCooldown: 0, secondaryCooldown: 0, invulnTimer: 0, stunTimer: 0, alive: true, attackFlash: 0, secondaryFlash: 0, attackSwingTimer: 0, attackSwingArc: 0, minigunSpin: 0, minigunMuzzleFlash: 0, railgunCharge: 0, railgunCharging: false, annihilatorHeat: 0, annihilatorOverheated: false, activeWeapon: activeWep, swapDebounce: 0, swapFlash: 0, dashCooldown: 0, isDashing: false, dashTimer: 0, dashDx: 0, dashDy: 0, knockbackX: 0, knockbackY: 0 };
}

function getPrimaryDamage() {
    let dmg;
    if (hasMinigun() && player && player.activeWeapon === 'minigun') dmg = getPlayerStat(10, 'prim_dmg', 0.22);
    else dmg = getPlayerStat(characters[selectedCharacter].primary.damage, 'prim_dmg', 0.25);
    const mult = getAmuletPowerValue('damageMult');
    return mult !== null ? dmg * mult : dmg;
}
function getPrimaryCooldown() {
    let cd;
    if (hasMinigun() && player && player.activeWeapon === 'minigun') cd = 0.1;
    else cd = getPlayerStat(characters[selectedCharacter].primary.cooldown, 'prim_spd', -0.18);
    const mult = getAmuletPowerValue('cooldownMult');
    return mult !== null ? cd * mult : cd;
}
function getPrimaryRange() { if (hasMinigun() && player && player.activeWeapon === 'minigun') return 380; return getPlayerStat(characters[selectedCharacter].primary.range, 'prim_rng', 0.22); }
function getPrimarySpecLevel() { return playerUpgrades['prim_spec'] || 0; }
function getPrimarySpecChance() { return getPrimarySpecLevel() * 0.15; }
function getSecondaryDamage() {
    let dmg = getPlayerStat(characters[selectedCharacter].secondary.damage || 10, 'sec_dmg', 0.28);
    const mult = getAmuletPowerValue('damageMult');
    return mult !== null ? dmg * mult : dmg;
}
function getSecondaryCooldown() {
    let cd = getPlayerStat(characters[selectedCharacter].secondary.cooldown, 'sec_spd', -0.18);
    const mult = getAmuletPowerValue('cooldownMult');
    return mult !== null ? cd * mult : cd;
}
function getSecondarySpecialLevel() { return playerUpgrades['sec_special'] || 0; }
function getSecondaryAoeLevel() { return playerUpgrades['sec_aoe'] || 0; }
function getMinionHealth() { const ch = characters[selectedCharacter].secondary; return Math.floor((ch.minionHealth || 30) * (1 + (playerUpgrades['sec_dmg'] || 0) * 0.3)) + getVitalityBonus(); }
function getMinionDamage() {
    const ch = characters[selectedCharacter].secondary;
    let dmg = Math.floor((ch.minionDamage || 7) * (1 + (playerUpgrades['sec_dmg'] || 0) * 0.25));
    const mult = getAmuletPowerValue('damageMult');
    return mult !== null ? Math.floor(dmg * mult) : dmg;
}

function damagePlayer(amount, knockbackAngle) {
    if (!player || !player.alive || player.invulnTimer > 0) return;
    let finalAmount = amount;
    const resist = getAmuletPowerValue('damageResist');
    if (resist !== null) finalAmount = amount * (1 - resist);
    player.health -= finalAmount; player.invulnTimer = INVULN_TIME;
    if (knockbackAngle !== undefined) { player.knockbackX += Math.cos(knockbackAngle) * 200; player.knockbackY += Math.sin(knockbackAngle) * 200; if (player.isDashing) { player.isDashing = false; player.dashTimer = 0; } }
    screenShake = Math.max(screenShake, 4); spawnParticles(player.x, player.y, 6, '#ff4444', 80, 0.3); spawnDamageNumber(player.x, player.y, finalAmount, '#ff4444');
    if (player.health <= 0) { player.health = 0; player.alive = false; spawnParticles(player.x, player.y, 25, '#ff2222', 150, 0.7); gameState = 'GAME_OVER'; gameOverTimer = 1.5; }
}

function fireRailgunShot(chargeLevel) {
    if (!player || !player.alive) return;
    const clampedCharge = Math.max(0, Math.min(1, chargeLevel));
    const baseDamage = 55;
    const dmgMult = 1 + (playerUpgrades['prim_dmg'] || 0) * 0.25;
    const chargeDmgMult = 0.35 + clampedCharge * 7.0;
    let damage = baseDamage * dmgMult * chargeDmgMult;
    const amuletDmgMult = getAmuletPowerValue('damageMult');
    if (amuletDmgMult !== null) damage *= amuletDmgMult;
    const startX = player.x + Math.cos(player.angle) * 22, startY = player.y + Math.sin(player.angle) * 22;
    const endX = player.x + Math.cos(player.angle) * RAILGUN_BEAM_RANGE, endY = player.y + Math.sin(player.angle) * RAILGUN_BEAM_RANGE;
    const hitEnemies = [];
    for (const enemy of enemies) { if (enemy.health <= 0) continue; const dist = pointToSegmentDist(enemy.x, enemy.y, startX, startY, endX, endY); if (dist < enemy.size + 7) { const dot = ((enemy.x - startX) * (endX - startX) + (enemy.y - startY) * (endY - startY)) / (RAILGUN_BEAM_RANGE * RAILGUN_BEAM_RANGE); if (dot >= -0.15 && dot <= 1.15) hitEnemies.push(enemy); } }
    hitEnemies.sort((a, b) => { const da = (a.x - startX) ** 2 + (a.y - startY) ** 2, db = (b.x - startX) ** 2 + (b.y - startY) ** 2; return da - db; });
    for (const enemy of hitEnemies) damageEnemy(enemy, damage, player.angle);
    // Purple block hit check
    if (purpleBlock && purpleBlock.health > 0 && purpleBlock.invulnTimer <= 0) {
        const bdist = pointToSegmentDist(purpleBlock.x, purpleBlock.y, startX, startY, endX, endY);
        if (bdist < purpleBlock.size + 7) {
            const bdot = ((purpleBlock.x - startX) * (endX - startX) + (purpleBlock.y - startY) * (endY - startY)) / (RAILGUN_BEAM_RANGE * RAILGUN_BEAM_RANGE);
            if (bdot >= -0.15 && bdot <= 1.15) damagePurpleBlock();
        }
    }
    const steps = Math.ceil(RAILGUN_BEAM_RANGE / (TILE * 0.45));
    for (let i = 0; i <= steps; i++) { const t = i / steps, bx = startX + (endX - startX) * t, by = startY + (endY - startY) * t; const wallTile = getWallTileAt(bx, by); if (wallTile) damageWallTile(wallTile.tx, wallTile.ty, player.angle); }
    beamEffects.push({ x1: startX, y1: startY, x2: endX, y2: endY, life: 0.35, maxLife: 0.35, thickness: 3 + clampedCharge * 10, color: '#88ddff' });
    beamEffects.push({ x1: startX, y1: startY, x2: endX, y2: endY, life: 0.2, maxLife: 0.2, thickness: 1.5 + clampedCharge * 5, color: '#ffffff' });
    spawnParticles(startX, startY, 18, '#ffffff', 140, 0.35); spawnParticles(startX, startY, 25, '#44aaff', 200, 0.5);
    for (let i = 0; i < 20; i++) { const t = Math.random(), px2 = startX + (endX - startX) * t, py2 = startY + (endY - startY) * t; particles.push({ x: px2 + (Math.random() - 0.5) * 25, y: py2 + (Math.random() - 0.5) * 25, vx: (Math.random() - 0.5) * 30, vy: (Math.random() - 0.5) * 30, life: 0.3 + Math.random() * 0.25, maxLife: 0.55, color: Math.random() < 0.5 ? '#88ddff' : '#ffffff', size: 2 + Math.random() * 4 }); }
    screenShake = Math.max(screenShake, 2 + clampedCharge * 10);
    player.primaryCooldown = 0;
    spawnDamageNumber(startX, startY - 10, Math.round(damage), '#88ddff');
}

function fireMinionRailgunShot(minion, targetX, targetY) {
    const startX = minion.x + Math.cos(minion.angle) * (minion.size + 4), startY = minion.y + Math.sin(minion.angle) * (minion.size + 4);
    const dx = targetX - startX, dy = targetY - startY, dist = Math.hypot(dx, dy), range = minion.rangedRange, clampedDist = Math.min(range, dist);
    const endX = startX + (dx / (dist + 0.001)) * clampedDist, endY = startY + (dy / (dist + 0.001)) * clampedDist;
    for (const enemy of enemies) { if (enemy.health <= 0) continue; const beamDist = pointToSegmentDist(enemy.x, enemy.y, startX, startY, endX, endY); if (beamDist < enemy.size + 5) { const dot = ((enemy.x - startX) * (endX - startX) + (enemy.y - startY) * (endY - startY)) / (clampedDist * clampedDist + 1); if (dot >= -0.15 && dot <= 1.15) damageEnemy(enemy, minion.damage, minion.angle); } }
    // Purple block hit check
    if (purpleBlock && purpleBlock.health > 0 && purpleBlock.invulnTimer <= 0) {
        const bdist = pointToSegmentDist(purpleBlock.x, purpleBlock.y, startX, startY, endX, endY);
        if (bdist < purpleBlock.size + 5) {
            const bdot = ((purpleBlock.x - startX) * (endX - startX) + (purpleBlock.y - startY) * (endY - startY)) / (clampedDist * clampedDist + 1);
            if (bdot >= -0.15 && bdot <= 1.15) damagePurpleBlock();
        }
    }
    const steps = Math.ceil(clampedDist / (TILE * 0.45));
    for (let i = 0; i <= steps; i++) { const t = i / steps, bx = startX + (endX - startX) * t, by = startY + (endY - startY) * t; const wallTile = getWallTileAt(bx, by); if (wallTile) damageWallTile(wallTile.tx, wallTile.ty, minion.angle); }
    beamEffects.push({ x1: startX, y1: startY, x2: endX, y2: endY, life: 0.28, maxLife: 0.28, thickness: 2.8, color: '#6699ff' });
    beamEffects.push({ x1: startX, y1: startY, x2: endX, y2: endY, life: 0.16, maxLife: 0.16, thickness: 1.2, color: '#aaccff' });
    spawnParticles(startX, startY, 8, '#6699ff', 70, 0.22); spawnParticles(startX, startY, 5, '#aaccff', 50, 0.18);
    for (let i = 0; i < 8; i++) { const t = Math.random(), px2 = startX + (endX - startX) * t, py2 = startY + (endY - startY) * t; particles.push({ x: px2 + (Math.random() - 0.5) * 10, y: py2 + (Math.random() - 0.5) * 10, vx: (Math.random() - 0.5) * 20, vy: (Math.random() - 0.5) * 20, life: 0.2 + Math.random() * 0.2, maxLife: 0.4, color: Math.random() < 0.5 ? '#6699ff' : '#aaccff', size: 1.5 + Math.random() * 2.5 }); }
    screenShake = Math.max(screenShake, 1.2);
}

function fireAnnihilatorShot() {
    if (!player || !player.alive) return;
    // Fires a fully-charged railgun blast instantly
    const baseDamage = 55;
    const dmgMult = 1 + (playerUpgrades['prim_dmg'] || 0) * 0.25;
    const chargeDmgMult = 0.35 + 1.0 * 7.0; // Always fully charged
    let damage = baseDamage * dmgMult * chargeDmgMult;
    const amuletDmgMult = getAmuletPowerValue('damageMult');
    if (amuletDmgMult !== null) damage *= amuletDmgMult;
    const startX = player.x + Math.cos(player.angle) * 26, startY = player.y + Math.sin(player.angle) * 26;
    const endX = player.x + Math.cos(player.angle) * RAILGUN_BEAM_RANGE, endY = player.y + Math.sin(player.angle) * RAILGUN_BEAM_RANGE;
    const hitEnemies = [];
    for (const enemy of enemies) { if (enemy.health <= 0) continue; const dist = pointToSegmentDist(enemy.x, enemy.y, startX, startY, endX, endY); if (dist < enemy.size + 8) { const dot = ((enemy.x - startX) * (endX - startX) + (enemy.y - startY) * (endY - startY)) / (RAILGUN_BEAM_RANGE * RAILGUN_BEAM_RANGE); if (dot >= -0.15 && dot <= 1.15) hitEnemies.push(enemy); } }
    hitEnemies.sort((a, b) => { const da = (a.x - startX) ** 2 + (a.y - startY) ** 2, db = (b.x - startX) ** 2 + (b.y - startY) ** 2; return da - db; });
    for (const enemy of hitEnemies) damageEnemy(enemy, damage, player.angle);
    // Purple block hit check
    if (purpleBlock && purpleBlock.health > 0 && purpleBlock.invulnTimer <= 0) {
        const bdist = pointToSegmentDist(purpleBlock.x, purpleBlock.y, startX, startY, endX, endY);
        if (bdist < purpleBlock.size + 8) {
            const bdot = ((purpleBlock.x - startX) * (endX - startX) + (purpleBlock.y - startY) * (endY - startY)) / (RAILGUN_BEAM_RANGE * RAILGUN_BEAM_RANGE);
            if (bdot >= -0.15 && bdot <= 1.15) damagePurpleBlock();
        }
    }
    const steps = Math.ceil(RAILGUN_BEAM_RANGE / (TILE * 0.45));
    for (let i = 0; i <= steps; i++) { const t = i / steps, bx = startX + (endX - startX) * t, by = startY + (endY - startY) * t; const wallTile = getWallTileAt(bx, by); if (wallTile) damageWallTile(wallTile.tx, wallTile.ty, player.angle); }
    beamEffects.push({ x1: startX, y1: startY, x2: endX, y2: endY, life: 0.4, maxLife: 0.4, thickness: 13, color: '#ff4444' });
    beamEffects.push({ x1: startX, y1: startY, x2: endX, y2: endY, life: 0.3, maxLife: 0.3, thickness: 7, color: '#ff8800' });
    beamEffects.push({ x1: startX, y1: startY, x2: endX, y2: endY, life: 0.2, maxLife: 0.2, thickness: 3, color: '#ffffff' });
    spawnParticles(startX, startY, 25, '#ffffff', 160, 0.4); spawnParticles(startX, startY, 35, '#ff4400', 220, 0.55);
    for (let i = 0; i < 25; i++) { const t = Math.random(), px2 = startX + (endX - startX) * t, py2 = startY + (endY - startY) * t; particles.push({ x: px2 + (Math.random() - 0.5) * 30, y: py2 + (Math.random() - 0.5) * 30, vx: (Math.random() - 0.5) * 40, vy: (Math.random() - 0.5) * 40, life: 0.3 + Math.random() * 0.3, maxLife: 0.6, color: Math.random() < 0.5 ? '#ff4444' : '#ff8800', size: 2 + Math.random() * 5 }); }
    screenShake = Math.max(screenShake, 8);
    player.primaryCooldown = 0.15; // Short cooldown between shots
    spawnDamageNumber(startX, startY - 10, Math.round(damage), '#ff4444');
}

function playerPrimaryAttack() {
    if (!player || !player.alive || player.primaryCooldown > 0) return;
    const ch = characters[selectedCharacter];
    if (ch.primary.isRanged) {
        player.primaryCooldown = getPrimaryCooldown(); player.attackFlash = 0.08;
        const dmg = getPrimaryDamage(), range = getPrimaryRange(), specLvl = getPrimarySpecLevel(), homing = (hasMinigun() && player.activeWeapon === 'minigun') ? specLvl * 0.06 : specLvl * 0.05;
        let projSpeed = ch.primary.speed, projSize = ch.primary.size, projColor = ch.primary.color, fireAngle = player.angle;
        if (hasMinigun() && player.activeWeapon === 'minigun') { projSpeed = 520; projSize = 4; projColor = '#ff8833'; fireAngle = player.angle + (Math.random() - 0.5) * 0.07; player.minigunMuzzleFlash = 0.06; screenShake = Math.max(screenShake, 0.6); }
        const proj = spawnProjectile(player.x + Math.cos(player.angle) * 20, player.y + Math.sin(player.angle) * 20, fireAngle, projSpeed, dmg, projColor, projSize, true, 0, homing, 0, 'player');
        proj.rangeRemaining = range; projectiles.push(proj);
        if (hasMinigun() && player.activeWeapon === 'minigun') spawnParticles(proj.x, proj.y, 2, projColor, 30, 0.15); else spawnParticles(proj.x, proj.y, 5, ch.primary.color, 40, 0.25);
        return;
    }
    player.primaryCooldown = getPrimaryCooldown(); const range2 = getPrimaryRange(), dmg2 = getPrimaryDamage(), arc = ch.primary.arcAngle, halfArc = arc / 2;
    player.attackFlash = 0.12; player.attackSwingTimer = SWING_DURATION; player.attackSwingArc = arc;
    spawnParticles(player.x + Math.cos(player.angle) * range2 * 0.5, player.y + Math.sin(player.angle) * range2 * 0.5, 5, ch.primary.color, 60, 0.2);
    let hitAny = false;
    for (const enemy of enemies) { if (enemy.health <= 0) continue; const dx = enemy.x - player.x, dy = enemy.y - player.y, dist = Math.hypot(dx, dy); if (dist < range2 + enemy.size) { const angleToEnemy = Math.atan2(dy, dx); let diff = angleToEnemy - player.angle; while (diff > Math.PI) diff -= Math.PI * 2; while (diff < -Math.PI) diff += Math.PI * 2; if (Math.abs(diff) <= halfArc) { hitAny = true; damageEnemy(enemy, dmg2, player.angle); if (Math.random() < getPrimarySpecChance()) { enemy.stunTimer = 0.6; spawnParticles(enemy.x, enemy.y, 4, '#ffff88', 40, 0.3); } } } }
    // Purple block melee check
    if (purpleBlock && purpleBlock.health > 0 && purpleBlock.invulnTimer <= 0) {
        const bdx = purpleBlock.x - player.x, bdy = purpleBlock.y - player.y, bdist = Math.hypot(bdx, bdy);
        if (bdist < range2 + purpleBlock.size) {
            const angleToBlock = Math.atan2(bdy, bdx); let diff = angleToBlock - player.angle;
            while (diff > Math.PI) diff -= Math.PI * 2; while (diff < -Math.PI) diff += Math.PI * 2;
            if (Math.abs(diff) <= halfArc) { hitAny = true; damagePurpleBlock(); }
        }
    }
    if (hitAny) screenShake = Math.max(screenShake, 1.5);
    damageWallsInMeleeArc(player.x, player.y, player.angle, range2, halfArc);
}

function playerSecondaryAttack() {
    if (!player || !player.alive || player.secondaryCooldown > 0) return;
    const ch = characters[selectedCharacter]; player.secondaryCooldown = getSecondaryCooldown(); player.secondaryFlash = 0.15;
    if (ch.secondary.isSummon) {
        const specLvl = getSecondarySpecialLevel(), aoeLvl = getSecondaryAoeLevel();
        // Amulet: extra skeleton spawn bonus
        const extraSpawn = getAmuletPowerValue('extraSkeletons') || 0;
        let summonType = 'normal';
        if (player.activeWeapon === 'railgun') { if (hasMinionRailgun()) summonType = 'railgun'; else if (hasMinionMinigun()) summonType = 'minigun'; }
        else if (player.activeWeapon === 'minigun') { if (hasMinionMinigun()) summonType = 'minigun'; }
        if (summonType === 'railgun') {
            const totalCount = (2 + specLvl + extraSpawn) + aoeLvl, aliveCount = minions.filter(function(m) { return m.health > 0; }).length, spawnCount = Math.min(totalCount, MAX_MINIONS - aliveCount);
            for (let i = 0; i < spawnCount; i++) { const offsetAngle = player.angle + Math.PI + (i - (spawnCount - 1) / 2) * 0.45, sx = player.x + Math.cos(offsetAngle) * 30, sy = player.y + Math.sin(offsetAngle) * 30; if (!isWallCircle(sx, sy, 8)) { minions.push(spawnRailgunSkeleton(sx, sy)); spawnParticles(sx, sy, 6, '#6699ff', 55, 0.4); } else { minions.push(spawnRailgunSkeleton(player.x, player.y)); spawnParticles(player.x, player.y, 6, '#6699ff', 55, 0.4); } }
            spawnParticles(player.x, player.y, 14, '#4488ff', 70, 0.55); screenShake = Math.max(screenShake, 2);
        } else if (summonType === 'minigun') {
            const totalCount = (2 + specLvl + extraSpawn) + aoeLvl, aliveCount = minions.filter(function(m) { return m.health > 0; }).length, spawnCount = Math.min(totalCount, MAX_MINIONS - aliveCount);
            for (let i = 0; i < spawnCount; i++) { const offsetAngle = player.angle + Math.PI + (i - (spawnCount - 1) / 2) * 0.45, sx = player.x + Math.cos(offsetAngle) * 30, sy = player.y + Math.sin(offsetAngle) * 30; if (!isWallCircle(sx, sy, 8)) { minions.push(spawnMinigunSkeleton(sx, sy)); spawnParticles(sx, sy, 6, '#ff9944', 55, 0.4); } else { minions.push(spawnMinigunSkeleton(player.x, player.y)); spawnParticles(player.x, player.y, 6, '#ff9944', 55, 0.4); } }
            spawnParticles(player.x, player.y, 14, '#ff7733', 70, 0.55); screenShake = Math.max(screenShake, 2);
        } else {
            const skeletonCount = 2 + specLvl + extraSpawn + aoeLvl, aliveCount = minions.filter(function(m) { return m.health > 0; }).length, spawnSkeletons = Math.min(skeletonCount, MAX_MINIONS - aliveCount);
            for (let i = 0; i < spawnSkeletons; i++) { const offsetAngle = player.angle + Math.PI + (i - (spawnSkeletons - 1) / 2) * 0.5, sx = player.x + Math.cos(offsetAngle) * 28, sy = player.y + Math.sin(offsetAngle) * 28; if (!isWallCircle(sx, sy, 8)) { minions.push(spawnMinion(sx, sy)); spawnParticles(sx, sy, 6, '#c8e0d0', 50, 0.4); } else { minions.push(spawnMinion(player.x, player.y)); spawnParticles(player.x, player.y, 6, '#c8e0d0', 50, 0.4); } }
            spawnParticles(player.x, player.y, 12, '#8b9f8b', 60, 0.5);
        }
        return;
    }
    const dmg = getSecondaryDamage(), specLvl = getSecondarySpecialLevel(), aoeLvl = getSecondaryAoeLevel();
    if (ch.secondary.isRanged) { const speed = ch.secondary.speed, aoeRadius = aoeLvl * 38, proj = spawnProjectile(player.x + Math.cos(player.angle) * 18, player.y + Math.sin(player.angle) * 18, player.angle, speed, dmg, ch.secondary.color, ch.secondary.size, true, (selectedCharacter === 'wizard' ? specLvl : 0), 0, aoeRadius, 'player'); projectiles.push(proj); spawnParticles(proj.x, proj.y, 5, ch.secondary.color, 40, 0.25); }
    else {
        const range2 = getPlayerStat(ch.secondary.range, 'sec_spd', -0.04), arc = ch.secondary.arcAngle, halfArc = arc / 2;
        player.attackSwingTimer = SWING_DURATION * 1.3; player.attackSwingArc = arc; spawnParticles(player.x, player.y, 8, '#ffe080', 100, 0.3);
        let hitAny = false;
        for (const enemy of enemies) { if (enemy.health <= 0) continue; const dx = enemy.x - player.x, dy = enemy.y - player.y, dist = Math.hypot(dx, dy); if (dist < range2 + enemy.size) { const angleToEnemy = Math.atan2(dy, dx); let diff = angleToEnemy - player.angle; while (diff > Math.PI) diff -= Math.PI * 2; while (diff < -Math.PI) diff += Math.PI * 2; if (Math.abs(diff) <= halfArc) { hitAny = true; let finalDmg = dmg; if (aoeLvl > 0 && enemy.health / enemy.maxHealth < 0.3) finalDmg *= (1 + aoeLvl * 0.4); damageEnemy(enemy, finalDmg, player.angle); enemy.knockbackX += Math.cos(player.angle) * 80; enemy.knockbackY += Math.sin(player.angle) * 80; } } }
        // Purple block melee check
        if (purpleBlock && purpleBlock.health > 0 && purpleBlock.invulnTimer <= 0) {
            const bdx = purpleBlock.x - player.x, bdy = purpleBlock.y - player.y, bdist = Math.hypot(bdx, bdy);
            if (bdist < range2 + purpleBlock.size) {
                const angleToBlock = Math.atan2(bdy, bdx); let diff = angleToBlock - player.angle;
                while (diff > Math.PI) diff -= Math.PI * 2; while (diff < -Math.PI) diff += Math.PI * 2;
                if (Math.abs(diff) <= halfArc) { hitAny = true; damagePurpleBlock(); }
            }
        }
        if (hitAny) screenShake = Math.max(screenShake, 3);
        damageWallsInMeleeArc(player.x, player.y, player.angle, range2, halfArc);
        if (specLvl > 0) { const shockProj = spawnProjectile(player.x + Math.cos(player.angle) * 10, player.y + Math.sin(player.angle) * 10, player.angle, 200, dmg * 0.7, '#ffe8a0', 14, true, 0, 0, specLvl * 30, 'player'); shockProj.rangeRemaining = 180 + specLvl * 50; projectiles.push(shockProj); }
    }
}

function performWeaponSwap(newWeapon) {
    if (!player || !player.alive || player.swapDebounce > 0 || player.activeWeapon === newWeapon) return;
    player.activeWeapon = newWeapon; player.swapDebounce = 0.35; player.swapFlash = 0.2; player.railgunCharging = false; player.railgunCharge = 0; player.annihilatorHeat = 0; player.annihilatorOverheated = false; player.primaryCooldown = 0;
    const swapColor = newWeapon === 'annihilator' ? '#ff4444' : (newWeapon === 'railgun' ? '#44aaff' : '#ff8833'); spawnParticles(player.x, player.y, 10, swapColor, 100, 0.4); spawnParticles(player.x + Math.cos(player.angle) * 22, player.y + Math.sin(player.angle) * 22, 6, '#ffffff', 70, 0.3); spawnDamageNumber(player.x, player.y - 14, 0, swapColor);
}

// Amulet player-speed multiplier
function getPlayerSpeedMult() {
    const mult = getAmuletPowerValue('speedMult');
    return mult !== null ? mult : 1;
}

// Amulet health regen tracking
let regenAccumulator = 0;

function updatePlayer(dt) {
    if (!player || !player.alive) return;
    // Stun timer
    if (player.stunTimer > 0) {
        player.stunTimer -= dt;
        // Cancel railgun charging while stunned
        player.railgunCharging = false;
        player.railgunCharge = 0;
        player.annihilatorOverheated = false;
        if (player.isDashing) { player.isDashing = false; player.dashTimer = 0; }
        // Process knockback
        if (Math.abs(player.knockbackX) > 0.3 || Math.abs(player.knockbackY) > 0.3) { player.x += player.knockbackX * dt; player.y += player.knockbackY * dt; player.knockbackX *= Math.exp(-8 * dt); player.knockbackY *= Math.exp(-8 * dt); }
        resolveWallCollision(player, PLAYER_SIZE);
        player.angle = Math.atan2(mouseWorldY - player.y, mouseWorldX - player.x);
        // Still tick down cooldowns
        if (player.primaryCooldown > 0) player.primaryCooldown -= dt;
        if (player.secondaryCooldown > 0) player.secondaryCooldown -= dt;
        if (player.invulnTimer > 0) player.invulnTimer -= dt;
        if (player.dashCooldown > 0) player.dashCooldown -= dt;
        if (player.swapDebounce > 0) player.swapDebounce -= dt;
        if (player.swapFlash > 0) player.swapFlash -= dt;
        if (player.attackFlash > 0) player.attackFlash -= dt;
        if (player.secondaryFlash > 0) player.secondaryFlash -= dt;
        if (player.attackSwingTimer > 0) player.attackSwingTimer -= dt;
        if (player.minigunMuzzleFlash > 0) player.minigunMuzzleFlash -= dt;
        if ((hasMinigun() || hasAnnihilator()) && player.alive) player.minigunSpin += 4 * dt;
        return;
    }
    let mx = 0, my = 0;
    if (keys['w'] || keys['arrowup']) my -= 1; if (keys['s'] || keys['arrowdown']) my += 1; if (keys['a'] || keys['arrowleft']) mx -= 1; if (keys['d'] || keys['arrowright']) mx += 1;
    if ((keys[' '] || keys['q']) && player.dashCooldown <= 0 && !player.isDashing) { let ddx = 0, ddy = 0; if (mx !== 0 || my !== 0) { const len = Math.hypot(mx, my); ddx = mx / len; ddy = my / len; } else { ddx = Math.cos(player.angle); ddy = Math.sin(player.angle); } player.isDashing = true; player.dashTimer = DASH_DURATION; player.dashDx = ddx; player.dashDy = ddy; player.dashCooldown = DASH_COOLDOWN; keys[' '] = false; keys['q'] = false; spawnParticles(player.x, player.y, 14, '#ddeeff', 110, 0.35); spawnParticles(player.x, player.y, 8, '#ffffff', 80, 0.25); screenShake = Math.max(screenShake, 1.5); }
    if (Math.abs(player.knockbackX) > 0.3 || Math.abs(player.knockbackY) > 0.3) { player.x += player.knockbackX * dt; player.y += player.knockbackY * dt; player.knockbackX *= Math.exp(-8 * dt); player.knockbackY *= Math.exp(-8 * dt); if (player.isDashing && (Math.abs(player.knockbackX) > 5 || Math.abs(player.knockbackY) > 5)) { player.isDashing = false; player.dashTimer = 0; } }
    if (player.isDashing && player.dashTimer > 0) { player.dashTimer -= dt; player.x += player.dashDx * DASH_SPEED * dt; player.y += player.dashDy * DASH_SPEED * dt; if (Math.random() < 0.55) particles.push({ x: player.x - player.dashDx * 12 + (Math.random() - 0.5) * 16, y: player.y - player.dashDy * 12 + (Math.random() - 0.5) * 16, vx: (Math.random() - 0.5) * 35, vy: (Math.random() - 0.5) * 35, life: 0.25, maxLife: 0.25, color: '#aaccff', size: 1.5 + Math.random() * 3 }); if (player.dashTimer <= 0) { player.isDashing = false; spawnParticles(player.x, player.y, 6, '#ffffff', 50, 0.2); } }
    else {
        if (mx !== 0 || my !== 0) { const len = Math.hypot(mx, my); mx /= len; my /= len; }
        const speedMult = getPlayerSpeedMult();
        player.x += mx * PLAYER_SPEED * speedMult * dt;
        player.y += my * PLAYER_SPEED * speedMult * dt;
    }
    resolveWallCollision(player, PLAYER_SIZE); player.angle = Math.atan2(mouseWorldY - player.y, mouseWorldX - player.x);
    if (player.primaryCooldown > 0) player.primaryCooldown -= dt; if (player.secondaryCooldown > 0) player.secondaryCooldown -= dt; if (player.invulnTimer > 0) player.invulnTimer -= dt; if (player.attackFlash > 0) player.attackFlash -= dt; if (player.secondaryFlash > 0) player.secondaryFlash -= dt; if (player.attackSwingTimer > 0) player.attackSwingTimer -= dt; if (player.minigunMuzzleFlash > 0) player.minigunMuzzleFlash -= dt; if (player.swapDebounce > 0) player.swapDebounce -= dt; if (player.swapFlash > 0) player.swapFlash -= dt; if (player.dashCooldown > 0 && !player.isDashing) player.dashCooldown -= dt;
    if (player.swapDebounce <= 0) { if (keys['1'] && hasMinigun() && player.activeWeapon !== 'minigun') performWeaponSwap('minigun'); if (keys['2'] && hasRailgun() && player.activeWeapon !== 'railgun') performWeaponSwap('railgun'); if (keys['3'] && hasAnnihilator() && player.activeWeapon !== 'annihilator') performWeaponSwap('annihilator'); }
    if ((hasMinigun() || hasAnnihilator()) && player.alive) { const spinRate = (mouseDown.left && (player.activeWeapon === 'minigun' || player.activeWeapon === 'annihilator') && (player.activeWeapon !== 'annihilator' || !player.annihilatorOverheated)) ? 22 : (player.activeWeapon === 'annihilator' ? 6 : 4); player.minigunSpin += spinRate * dt; }
    // Amulet: health regeneration
    const regenRate = getAmuletPowerValue('regen');
    if (regenRate !== null && player.health < player.maxHealth) {
        regenAccumulator += regenRate * dt;
        if (regenAccumulator >= 1) {
            const healAmount = Math.floor(regenAccumulator);
            player.health = Math.min(player.maxHealth, player.health + healAmount);
            regenAccumulator -= healAmount;
        }
    } else { regenAccumulator = 0; }
    // Annihilator heat system
    if (hasAnnihilator() && player.activeWeapon === 'annihilator') {
        const ANNIHILATOR_HEAT_MAX = 1.0;
        const ANNIHILATOR_HEAT_PER_SHOT = 0.10;
        const ANNIHILATOR_COOL_RATE = 0.35;
        
        if (mouseDown.left) {
            // Player is holding the trigger
            if (!player.annihilatorOverheated && player.primaryCooldown <= 0) {
                fireAnnihilatorShot();
                player.attackFlash = 0.1;
                player.annihilatorHeat = Math.min(ANNIHILATOR_HEAT_MAX, player.annihilatorHeat + ANNIHILATOR_HEAT_PER_SHOT);
                if (player.annihilatorHeat >= ANNIHILATOR_HEAT_MAX) {
                    player.annihilatorOverheated = true;
                    spawnParticles(player.x, player.y, 15, '#ff4400', 120, 0.5);
                    spawnParticles(player.x, player.y, 10, '#ff8800', 90, 0.35);
                    screenShake = Math.max(screenShake, 3);
                }
            }
            // Heat does NOT cool while holding the trigger — even when overheated or on cooldown
        } else {
            // Cool down only when not holding the trigger
            if (player.annihilatorHeat > 0) {
                player.annihilatorHeat = Math.max(0, player.annihilatorHeat - ANNIHILATOR_COOL_RATE * dt);
                if (player.annihilatorOverheated && player.annihilatorHeat <= 0.1) {
                    player.annihilatorOverheated = false;
                    spawnParticles(player.x, player.y, 8, '#88ccff', 60, 0.3);
                }
            }
        }
        // Overheat smoke/glow particles
        if (player.annihilatorOverheated && Math.random() < 0.4) {
            const tipX = player.x + Math.cos(player.angle) * 30, tipY = player.y + Math.sin(player.angle) * 30;
            particles.push({ x: tipX + (Math.random() - 0.5) * 14, y: tipY + (Math.random() - 0.5) * 14, vx: (Math.random() - 0.5) * 25, vy: -20 + Math.random() * -30, life: 0.5, maxLife: 0.5, color: '#666666', size: 3 + Math.random() * 4 });
        }
        // Warn when getting close to overheat
        if (!player.annihilatorOverheated && player.annihilatorHeat > 0.65 && Math.random() < player.annihilatorHeat * 0.35) {
            const tipX = player.x + Math.cos(player.angle) * 48, tipY = player.y + Math.sin(player.angle) * 48;
            particles.push({ x: tipX + (Math.random() - 0.5) * 10, y: tipY + (Math.random() - 0.5) * 10, vx: (Math.random() - 0.5) * 40, vy: (Math.random() - 0.5) * 40, life: 0.25, maxLife: 0.25, color: '#ffaa44', size: 2 + Math.random() * 3 });
        }
    }
    if (hasRailgun() && player.activeWeapon === 'railgun') {
        if (mouseClicked.left && player.primaryCooldown <= 0 && !player.railgunCharging) { player.railgunCharging = true; player.railgunCharge = 0; }
        if (player.railgunCharging) {
            if (mouseDown.left) {
                player.railgunCharge = Math.min(1.0, player.railgunCharge + dt / RAILGUN_CHARGE_TIME);
                if (Math.random() < 0.5) { const tipX = player.x + Math.cos(player.angle) * 28, tipY = player.y + Math.sin(player.angle) * 28, orbitAngle = player.angle + Math.random() * Math.PI * 2, orbitDist = 8 + player.railgunCharge * 16; particles.push({ x: tipX + Math.cos(orbitAngle) * orbitDist, y: tipY + Math.sin(orbitAngle) * orbitDist, vx: (Math.random() - 0.5) * 40, vy: (Math.random() - 0.5) * 40, life: 0.35, maxLife: 0.35, color: player.railgunCharge > 0.7 ? '#ffffff' : '#44aaff', size: 1.5 + Math.random() * 2.5 }); }
                if (player.railgunCharge >= 1.0 && Math.random() < 0.35) { const tipX2 = player.x + Math.cos(player.angle) * 28, tipY2 = player.y + Math.sin(player.angle) * 28; particles.push({ x: tipX2 + (Math.random() - 0.5) * 18, y: tipY2 + (Math.random() - 0.5) * 18, vx: (Math.random() - 0.5) * 60, vy: (Math.random() - 0.5) * 60, life: 0.25, maxLife: 0.25, color: '#ffffff', size: 2 + Math.random() * 3 }); }
            } else { if (player.railgunCharge >= RAILGUN_MIN_CHARGE_TO_FIRE) { player.attackFlash = 0.15; fireRailgunShot(player.railgunCharge); } player.railgunCharging = false; player.railgunCharge = 0; }
        }
    } else if (mouseDown.left && player.primaryCooldown <= 0 && !(hasAnnihilator() && player.activeWeapon === 'annihilator')) playerPrimaryAttack();
    if (mouseDown.right && player.secondaryCooldown <= 0) playerSecondaryAttack();
}

function startGame(initialTokens = 0) { initUpgrades(); generateMap(); player = createPlayer(); wave = 1; tokens = initialTokens; enemies = []; projectiles = []; particles = []; droppedTokens = []; damageNumbers = []; minions = []; boneShards = []; beamEffects = []; shockwaves = []; purpleBlock = null; currentAmulet = null; droppedAmulets = []; amuletPickupState = null; regenAccumulator = 0; paused = false; unpauseCountdown = 0; camera = { x: player.x - W / 2, y: player.y - GAME_VIEW_H / 2 }; updateCamera(0.016); spawnWave(); gameState = 'PLAYING'; screenShake = 0; canvas.focus({ preventScroll: true }); }
function restartGame() { gameState = 'CHARACTER_SELECT'; wave = 1; tokens = 0; player = null; enemies = []; projectiles = []; particles = []; droppedTokens = []; damageNumbers = []; minions = []; boneShards = []; beamEffects = []; shockwaves = []; purpleBlock = null; currentAmulet = null; droppedAmulets = []; amuletPickupState = null; regenAccumulator = 0; paused = false; unpauseCountdown = 0; screenShake = 0; }
