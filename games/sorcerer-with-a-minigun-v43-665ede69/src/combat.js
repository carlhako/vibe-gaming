// combat.js - projectiles, particles, damage, tokens, AOE, beams, bone shards, purple block, amulets

function spawnProjectile(x, y, angle, speed, damage, color, size, isRanged, pierceCount, homingStrength, aoeRadius, owner) {
    return { x, y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed, angle, speed, damage, color, size, isRanged, rangeRemaining: isRanged ? (characters[selectedCharacter].primary.range || 500) : 999, pierceCount: pierceCount || 0, homingStrength: homingStrength || 0, aoeRadius: aoeRadius || 0, owner: owner || 'player', alive: true, trail: [] };
}

function spawnParticles(x, y, count, color, speed, life) { for (let i = 0; i < count; i++) { const a = Math.random() * Math.PI * 2, s = speed * (0.4 + Math.random() * 0.6); particles.push({ x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, life, maxLife: life, color, size: 1.5 + Math.random() * 3.5 }); } }

function spawnDamageNumber(x, y, amount, color) { damageNumbers.push({ x: x + (Math.random() - 0.5) * 16, y: y - 8, text: Math.round(amount).toString(), life: 0.8, maxLife: 0.8, color }); }

function damageEnemy(enemy, amount, angle) { enemy.health -= amount; enemy.hitFlash = 0.08; enemy.knockbackX += Math.cos(angle) * 30; enemy.knockbackY += Math.sin(angle) * 30; spawnDamageNumber(enemy.x, enemy.y, amount, '#ffffff'); spawnParticles(enemy.x, enemy.y, 3, enemy.color, 50, 0.2); enemy.aggroed = true; if (enemy.health <= 0) killEnemy(enemy); }

function killEnemy(enemy) {
    enemy.health = 0; spawnParticles(enemy.x, enemy.y, 12, enemy.color, 120, 0.5);
    if (enemy.witchRef && typeof enemy.witchRef.devilsAlive === 'number') enemy.witchRef.devilsAlive = Math.max(0, enemy.witchRef.devilsAlive - 1);
    if (enemy.type === 'witch') { spawnParticles(enemy.x, enemy.y, 20, '#9933cc', 160, 0.7); spawnParticles(enemy.x, enemy.y, 14, '#ff44aa', 120, 0.55); screenShake = Math.max(screenShake, 5); }
    for (let i = 0; i < enemy.tokenDrop; i++) droppedTokens.push({ x: enemy.x + (Math.random() - 0.5) * 20, y: enemy.y + (Math.random() - 0.5) * 20, life: 12, maxLife: 12, collected: false, flyingToPlayer: false, flyStartX: 0, flyStartY: 0, flyProgress: 0 });
}

function killMinionByTimeout(minion) {
    minion.health = 0; spawnParticles(minion.x, minion.y, 14, '#e8dcc8', 140, 0.55); spawnParticles(minion.x, minion.y, 8, '#bba890', 100, 0.45); screenShake = Math.max(screenShake, 2.5);
    const blastRadius = TILE * 1.3, tileMinX = Math.floor((minion.x - blastRadius) / TILE), tileMaxX = Math.floor((minion.x + blastRadius) / TILE), tileMinY = Math.floor((minion.y - blastRadius) / TILE), tileMaxY = Math.floor((minion.y + blastRadius) / TILE);
    for (let wy = tileMinY; wy <= tileMaxY; wy++) for (let wx = tileMinX; wx <= tileMaxX; wx++) { if (wx < 0 || wx >= MAP_COLS || wy < 0 || wy >= MAP_ROWS || mapTiles[wy][wx] === 0) continue; const wcx = wx * TILE + TILE / 2, wcy = wy * TILE + TILE / 2; if (Math.hypot(wcx - minion.x, wcy - minion.y) < blastRadius) damageWallTile(wx, wy, Math.atan2(wcy - minion.y, wcx - minion.x)); }
    for (let i = 0; i < BONE_SHARD_COUNT; i++) { const a = (i / BONE_SHARD_COUNT) * Math.PI * 2 + (Math.random() - 0.5) * 0.3, speed = BONE_SHARD_SPEED * (0.7 + Math.random() * 0.6); boneShards.push({ x: minion.x, y: minion.y, vx: Math.cos(a) * speed, vy: Math.sin(a) * speed, damage: minion.damage * 0.75, life: BONE_SHARD_LIFE, maxLife: BONE_SHARD_LIFE, hitEnemies: new Set() }); }
}

function collectToken(token) { token.collected = true; tokens++; spawnParticles(token.x, token.y, 4, '#ffcc00', 30, 0.3); }

function startTokenFlyToPlayer() { if (!player || !player.alive) return; for (const token of droppedTokens) if (!token.collected && !token.flyingToPlayer) { token.flyingToPlayer = true; token.flyStartX = token.x; token.flyStartY = token.y; token.flyProgress = 0; token.flyTargetX = player.x; token.flyTargetY = player.y; } }

// Amulet functions
function dropAmulet(x, y) {
    const index = Math.floor(Math.random() * AMULET_TYPES.length);
    const template = AMULET_TYPES[index];
    const amulet = {
        type: template.id,
        name: template.name,
        desc: template.desc,
        icon: template.icon,
        iconColor: template.iconColor,
        powers: template.powers.map(p => ({ type: p.type, value: p.value })),
        x: x + (Math.random() - 0.5) * 30,
        y: y + (Math.random() - 0.5) * 30,
        life: 20,
        maxLife: 20,
        collected: false,
        bobOffset: Math.random() * Math.PI * 2
    };
    droppedAmulets.push(amulet);
    spawnParticles(amulet.x, amulet.y, 8, template.iconColor, 70, 0.4);
}

function collectAmulet(amulet) {
    amulet.collected = true;
    spawnParticles(amulet.x, amulet.y, 10, amulet.iconColor, 80, 0.5);
    
    // Pause and show comparison dialog
    amuletPickupState = {
        existingAmulet: currentAmulet ? {
            type: currentAmulet.type,
            name: currentAmulet.name,
            desc: currentAmulet.desc,
            icon: currentAmulet.icon,
            iconColor: currentAmulet.iconColor,
            powers: currentAmulet.powers.map(p => ({ type: p.type, value: p.value }))
        } : null,
        newAmulet: {
            type: amulet.type,
            name: amulet.name,
            desc: amulet.desc,
            icon: amulet.icon,
            iconColor: amulet.iconColor,
            powers: amulet.powers.map(p => ({ type: p.type, value: p.value }))
        },
        choiceTimer: 0
    };
    paused = true;
}

function equipAmulet(amuletData) {
    currentAmulet = {
        type: amuletData.type,
        name: amuletData.name,
        desc: amuletData.desc,
        icon: amuletData.icon,
        iconColor: amuletData.iconColor,
        powers: amuletData.powers.map(p => ({ type: p.type, value: p.value }))
    };
    amuletPickupState = null;
    paused = false;
}

function discardAmuletPickup() {
    // Just close the dialog, keep existing amulet
    amuletPickupState = null;
    paused = false;
}

function getAmuletPowerValue(powerType) {
    if (!currentAmulet) return null;
    for (const p of currentAmulet.powers) {
        if (p.type === powerType) return p.value;
    }
    return null;
}

// Purple Block functions
function damagePurpleBlock() {
    if (!purpleBlock || purpleBlock.health <= 0 || purpleBlock.invulnTimer > 0) return;
    purpleBlock.health--;
    purpleBlock.invulnTimer = PURPLE_BLOCK_INVULN;
    purpleBlock.hitFlash = 0.12;
    spawnParticles(purpleBlock.x, purpleBlock.y, 4, '#bb66ff', 60, 0.25);
    spawnDamageNumber(purpleBlock.x, purpleBlock.y, 1, '#cc88ff');
    if (purpleBlock.health <= 0) destroyPurpleBlock();
}

function destroyPurpleBlock() {
    const bx = purpleBlock.x, by = purpleBlock.y;
    // Explosion particles
    spawnParticles(bx, by, 30, '#9933cc', 180, 0.7);
    spawnParticles(bx, by, 20, '#bb66ff', 140, 0.55);
    spawnParticles(bx, by, 15, '#ff44ff', 120, 0.5);
    screenShake = Math.max(screenShake, 6);
    
    // Gold: base 50-100, +10 per round after 5
    const goldMin = PURPLE_BLOCK_BASE_GOLD_MIN + (wave - 6) * 10;
    const goldMax = PURPLE_BLOCK_BASE_GOLD_MAX + (wave - 6) * 10;
    const goldAmount = goldMin + Math.floor(Math.random() * (goldMax - goldMin + 1));
    for (let i = 0; i < goldAmount; i++) {
        const angle = Math.random() * Math.PI * 2;
        const dist = 10 + Math.random() * 60;
        droppedTokens.push({
            x: bx + Math.cos(angle) * dist,
            y: by + Math.sin(angle) * dist,
            life: 15, maxLife: 15,
            collected: false, flyingToPlayer: false,
            flyStartX: 0, flyStartY: 0, flyProgress: 0
        });
    }
    
    // Enemy count: base 5, +1 per round after 5
    const enemyCount = PURPLE_BLOCK_BASE_ENEMIES + (wave - 6);
    spawnEnemiesFromBlock(bx, by, enemyCount);
    
    // Boss witch
    spawnEnemiesFromBlock(bx, by, 0, true);
    
    purpleBlock = null;
}

function updateProjectiles(dt) {
    for (const proj of projectiles) {
        if (!proj.alive) continue;
        if (proj.homingStrength > 0 && proj.isRanged) { let closestEnemy = null, closestDist = 200; for (const enemy of enemies) { if (enemy.health <= 0) continue; const d = Math.hypot(enemy.x - proj.x, enemy.y - proj.y); if (d < closestDist) { closestDist = d; closestEnemy = enemy; } } if (closestEnemy) { const targetAngle = Math.atan2(closestEnemy.y - proj.y, closestEnemy.x - proj.x); let diff = targetAngle - proj.angle; while (diff > Math.PI) diff -= Math.PI * 2; while (diff < -Math.PI) diff += Math.PI * 2; proj.angle += diff * Math.min(1, proj.homingStrength * 8 * dt); proj.vx = Math.cos(proj.angle) * proj.speed; proj.vy = Math.sin(proj.angle) * proj.speed; } }
        proj.x += proj.vx * dt; proj.y += proj.vy * dt; proj.rangeRemaining -= proj.speed * dt; proj.trail.push({ x: proj.x, y: proj.y, life: 0.15 }); if (proj.trail.length > 12) proj.trail.shift();
        if (isWallCircle(proj.x, proj.y, proj.size)) { if (proj.owner === 'player' || proj.owner === 'minion') { const wallTile = getWallTileAt(proj.x, proj.y); if (wallTile) damageWallTile(wallTile.tx, wallTile.ty, proj.angle); } if (proj.aoeRadius > 0) applyAoE(proj.x, proj.y, proj.aoeRadius, proj.damage * 0.8, proj.color); spawnParticles(proj.x, proj.y, 6, proj.color, 80, 0.25); proj.alive = false; continue; }
        // Purple block collision
        if (purpleBlock && purpleBlock.health > 0) {
            const pd = Math.hypot(purpleBlock.x - proj.x, purpleBlock.y - proj.y);
            if (pd < proj.size + purpleBlock.size) {
                damagePurpleBlock();
                proj.alive = false;
                spawnParticles(proj.x, proj.y, 5, proj.color, 70, 0.25);
                continue;
            }
        }
        for (const enemy of enemies) { if (enemy.health <= 0) continue; const ed = Math.hypot(enemy.x - proj.x, enemy.y - proj.y); if (ed < proj.size + enemy.size) { damageEnemy(enemy, proj.damage, proj.angle); if (proj.aoeRadius > 0) applyAoE(proj.x, proj.y, proj.aoeRadius, proj.damage * 0.7, proj.color); if (proj.pierceCount > 0) { proj.pierceCount--; spawnParticles(proj.x, proj.y, 3, proj.color, 50, 0.2); } else { proj.alive = false; spawnParticles(proj.x, proj.y, 5, proj.color, 70, 0.25); break; } } }
        if (proj.rangeRemaining <= 0 && proj.isRanged) { if (proj.aoeRadius > 0 && proj.alive) applyAoE(proj.x, proj.y, proj.aoeRadius, proj.damage * 0.8, proj.color); proj.alive = false; spawnParticles(proj.x, proj.y, 4, proj.color, 40, 0.2); }
    }
    projectiles = projectiles.filter(p => p.alive);
}

function applyAoE(x, y, radius, damage, color) {
    spawnParticles(x, y, 12, color, 100, 0.4);
    for (const enemy of enemies) { if (enemy.health <= 0) continue; const ed = Math.hypot(enemy.x - x, enemy.y - y); if (ed < radius + enemy.size) damageEnemy(enemy, damage, Math.atan2(enemy.y - y, enemy.x - x)); }
    // Damage purple block in AoE range
    if (purpleBlock && purpleBlock.health > 0) {
        const bd = Math.hypot(purpleBlock.x - x, purpleBlock.y - y);
        if (bd < radius + purpleBlock.size) damagePurpleBlock();
    }
    const tileMinX = Math.floor((x - radius) / TILE), tileMaxX = Math.floor((x + radius) / TILE), tileMinY = Math.floor((y - radius) / TILE), tileMaxY = Math.floor((y + radius) / TILE);
    for (let wy = tileMinY; wy <= tileMaxY; wy++) for (let wx = tileMinX; wx <= tileMaxX; wx++) { if (wx < 0 || wx >= MAP_COLS || wy < 0 || wy >= MAP_ROWS || mapTiles[wy][wx] === 0) continue; const wcx = wx * TILE + TILE / 2, wcy = wy * TILE + TILE / 2; if (Math.hypot(wcx - x, wcy - y) < radius + TILE * 0.5) damageWallTile(wx, wy, Math.atan2(wcy - y, wcx - x)); }
    screenShake = Math.max(screenShake, 2);
}

function updateParticles(dt) { for (const p of particles) { p.x += p.vx * dt; p.y += p.vy * dt; p.life -= dt; p.vx *= 0.94; p.vy *= 0.94; } particles = particles.filter(p => p.life > 0); }

function updateDamageNumbers(dt) { for (const dn of damageNumbers) { dn.y -= 40 * dt; dn.life -= dt; } damageNumbers = damageNumbers.filter(dn => dn.life > 0); }

function updateDroppedTokens(dt) {
    // Apply amulet magnet range multiplier
    const magnetMult = getAmuletPowerValue('magnetMult');
    const effectiveMagnetRange = magnetMult ? TOKEN_MAGNET_RANGE * magnetMult : TOKEN_MAGNET_RANGE;
    
    for (const token of droppedTokens) {
        if (token.collected) continue;
        if (token.flyingToPlayer && player && player.alive) { token.flyProgress += dt * 2.2; if (token.flyProgress >= 1) collectToken(token); else { const t = token.flyProgress, easeT = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t; token.x = token.flyStartX + (player.x - token.flyStartX) * easeT; token.y = token.flyStartY + (player.y - token.flyStartY) * easeT; if (Math.random() < 0.4) particles.push({ x: token.x, y: token.y, vx: (Math.random() - 0.5) * 15, vy: (Math.random() - 0.5) * 15, life: 0.2, maxLife: 0.2, color: '#ffcc44', size: 1.5 + Math.random() * 2 }); } continue; }
        token.life -= dt;
        if (player && player.alive && !token.flyingToPlayer) { const dx = player.x - token.x, dy = player.y - token.y, dist = Math.hypot(dx, dy); if (dist < effectiveMagnetRange) { const spd = 200; token.x += (dx / (dist + 0.01)) * spd * dt; token.y += (dy / (dist + 0.01)) * spd * dt; } if (dist < PLAYER_SIZE + 10) collectToken(token); }
    }
    droppedTokens = droppedTokens.filter(t => t.life > 0 && !t.collected);
}

function updateDroppedAmulets(dt) {
    if (!player || !player.alive) return;
    for (const amulet of droppedAmulets) {
        if (amulet.collected) continue;
        amulet.life -= dt;
        // Bob animation
        amulet.bobOffset += dt * 2.5;
        // Check pickup distance
        const dx = player.x - amulet.x, dy = player.y - amulet.y, dist = Math.hypot(dx, dy);
        if (dist < PLAYER_SIZE + 14) {
            collectAmulet(amulet);
        }
    }
    droppedAmulets = droppedAmulets.filter(a => a.life > 0 && !a.collected);
}

function updateBoneShards(dt) { for (const shard of boneShards) { shard.x += shard.vx * dt; shard.y += shard.vy * dt; shard.life -= dt; shard.vx *= 0.92; shard.vy *= 0.92; for (const enemy of enemies) { if (enemy.health <= 0 || shard.hitEnemies.has(enemy)) continue; if (Math.hypot(enemy.x - shard.x, enemy.y - shard.y) < enemy.size + 5) { damageEnemy(enemy, shard.damage, Math.atan2(shard.vy, shard.vx)); shard.hitEnemies.add(enemy); } } } boneShards = boneShards.filter(s => s.life > 0); }

function updateBeamEffects(dt) { for (const b of beamEffects) b.life -= dt; beamEffects = beamEffects.filter(b => b.life > 0); }
