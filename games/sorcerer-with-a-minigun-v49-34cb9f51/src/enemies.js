// Enemy and minion spawning + AI

function spawnEnemy(typeKey, minDistFromPlayer, startX, startY) {
    const type = enemyTypes[typeKey]; let ex, ey;
    if (startX !== undefined && startY !== undefined) { ex = startX; ey = startY; }
    else { let attempts = 0; do { ex = TILE * 1.5 + Math.random() * (WORLD_W - TILE * 3); ey = TILE * 1.5 + Math.random() * (WORLD_H - TILE * 3); attempts++; } while (attempts < 60 && (isWallCircle(ex, ey, type.size + 4) || (player && Math.hypot(ex - player.x, ey - player.y) < minDistFromPlayer))); }
    const hpMult = 1 + (wave - 1) * 0.35, dmgMult = 1 + (wave - 1) * 0.22;
    const enemy = { x: ex, y: ey, type: typeKey, health: Math.floor(type.baseHP * hpMult), maxHealth: Math.floor(type.baseHP * hpMult), damage: Math.floor(type.baseDamage * dmgMult), speed: type.speed * (1 + (wave - 1) * 0.06), size: type.size, color: type.color, tokenDrop: type.tokenDrop, stunTimer: 0, knockbackX: 0, knockbackY: 0, hitFlash: 0, aggroed: false, idleAngle: Math.random() * Math.PI * 2, idleTimer: 0, stuckCheckTimer: 0, lastStuckX: ex, lastStuckY: ey, isStuck: false };
    if (typeKey === 'witch') { enemy.summonCooldown = 0.4; enemy.summonCooldownMax = WITCH_SUMMON_COOLDOWN; enemy.maxDevils = WITCH_MAX_DEVILS; enemy.devilsAlive = 0; enemy.witchAggroRange = WITCH_AGGRO_RANGE; }
    if (typeKey === 'earthshaker') {
        enemy.gsCooldown = EARTHSHAKER_GROUND_SLAM_MIN + Math.random() * (EARTHSHAKER_GROUND_SLAM_MAX - EARTHSHAKER_GROUND_SLAM_MIN);
        enemy.gsCharging = false;
        enemy.gsChargeTimer = 0;
        enemy.gsSlammed = false;
        enemy.tpCooldown = EARTHSHAKER_TELEPORT_MIN + Math.random() * (EARTHSHAKER_TELEPORT_MAX - EARTHSHAKER_TELEPORT_MIN);
        enemy.rageTriggered = false;
        enemy.baseSpeed = type.speed;
    }
    return enemy;
}

function spawnWave() {
    enemies = []; projectiles = []; particles = []; droppedTokens = []; damageNumbers = []; minions = []; boneShards = []; beamEffects = []; shockwaves = [];
    const spawnCounts = getWaveSpawns();
    for (const [typeKey, count] of Object.entries(spawnCounts)) for (let i = 0; i < count; i++) { const type = enemyTypes[typeKey]; let edgePos, edgeAttempts = 0; do { edgePos = getEdgeSpawnPosition(); edgeAttempts++; } while (edgeAttempts < 20 && isWallCircle(edgePos.x, edgePos.y, type.size + 4)); enemies.push(spawnEnemy(typeKey, 0, edgePos.x, edgePos.y)); }
    
    // Purple block spawn chance after wave 5
    if (wave >= 6 && !purpleBlock && Math.random() < PURPLE_BLOCK_SPAWN_CHANCE) {
        const pos = getRandomOpenPosition(200);
        purpleBlock = {
            x: pos.x,
            y: pos.y,
            health: PURPLE_BLOCK_HP,
            maxHealth: PURPLE_BLOCK_HP,
            size: PURPLE_BLOCK_SIZE,
            invulnTimer: 0,
            hitFlash: 0,
            glowPhase: Math.random() * Math.PI * 2,
            spawnedWave: wave
        };
        // Spawn visual particles for block appearing
        for (let i = 0; i < 20; i++) {
            const angle = Math.random() * Math.PI * 2;
            const dist = Math.random() * 30;
            particles.push({
                x: purpleBlock.x + Math.cos(angle) * dist,
                y: purpleBlock.y + Math.sin(angle) * dist,
                vx: Math.cos(angle) * (20 + Math.random() * 60),
                vy: Math.sin(angle) * (20 + Math.random() * 60),
                life: 0.6 + Math.random() * 0.4,
                maxLife: 0.6 + Math.random() * 0.4,
                color: '#bb66ff',
                size: 2 + Math.random() * 4
            });
        }
    }
}

function spawnEnemiesFromBlock(x, y, count, bossCount) {
    bossCount = bossCount || 0;
    const enemyPool = ['skeleton', 'ghost', 'demon', 'brute'];
    for (let i = 0; i < count; i++) {
        const typeKey = enemyPool[Math.floor(Math.random() * enemyPool.length)];
        const spawnAngle = Math.random() * Math.PI * 2;
        const spawnDist = PURPLE_BLOCK_SIZE + 15 + Math.random() * 25;
        const sx = x + Math.cos(spawnAngle) * spawnDist;
        const sy = y + Math.sin(spawnAngle) * spawnDist;
        if (!isWallCircle(sx, sy, enemyTypes[typeKey].size + 4)) {
            const enemy = spawnEnemy(typeKey, 0, sx, sy);
            enemy.aggroed = true;
            // Double HP for block-spawned enemies
            enemy.health *= 2;
            enemy.maxHealth *= 2;
            enemies.push(enemy);
        } else {
            // Try a closer position if blocked
            const sx2 = x + Math.cos(spawnAngle) * (PURPLE_BLOCK_SIZE + 8);
            const sy2 = y + Math.sin(spawnAngle) * (PURPLE_BLOCK_SIZE + 8);
            if (!isWallCircle(sx2, sy2, enemyTypes[typeKey].size + 4)) {
                const enemy = spawnEnemy(typeKey, 0, sx2, sy2);
                enemy.aggroed = true;
                // Double HP for block-spawned enemies
                enemy.health *= 2;
                enemy.maxHealth *= 2;
                enemies.push(enemy);
            }
        }
    }
    // Spawn boss: Witch for waves 5-12, Earthshaker for wave 13+
    const bossType = wave >= 13 ? 'earthshaker' : 'witch';
    const bossData = enemyTypes[bossType];
    for (let w = 0; w < bossCount; w++) {
        const bossAngle = Math.random() * Math.PI * 2;
        const bossDist = PURPLE_BLOCK_SIZE + 20 + Math.random() * 20;
        const bx = x + Math.cos(bossAngle) * bossDist;
        const by = y + Math.sin(bossAngle) * bossDist;
        if (!isWallCircle(bx, by, bossData.size + 4)) {
            const boss = spawnEnemy(bossType, 0, bx, by);
            boss.aggroed = true;
            // Double HP for block-spawned bosses
            boss.health *= 2;
            boss.maxHealth *= 2;
            enemies.push(boss);
        } else {
            const bx2 = x + Math.cos(bossAngle) * (PURPLE_BLOCK_SIZE + 10);
            const by2 = y + Math.sin(bossAngle) * (PURPLE_BLOCK_SIZE + 10);
            if (!isWallCircle(bx2, by2, bossData.size + 4)) {
                const boss = spawnEnemy(bossType, 0, bx2, by2);
                boss.aggroed = true;
                // Double HP for block-spawned bosses
                boss.health *= 2;
                boss.maxHealth *= 2;
                enemies.push(boss);
            }
        }
    }
}

function getWaveSpawns() {
    const counts = { skeleton: 0, ghost: 0, demon: 0, brute: 0, witch: 0, earthshaker: 0 };
    counts.skeleton = 4 + Math.floor(wave * 1.6);
    if (wave >= 2) counts.ghost = 1 + Math.floor((wave - 1) * 1.2);
    if (wave >= 4) counts.demon = Math.floor((wave - 3) * 0.9);
    if (wave >= 7) counts.brute = Math.floor((wave - 6) * 0.7);
    if (wave >= 5 && wave < 13) { if (wave >= 10) counts.witch = 3; else if (wave >= 7) counts.witch = 2; else counts.witch = 1; }
    if (wave >= 13) { if (wave >= 16) counts.earthshaker = 5; else if (wave >= 14) counts.earthshaker = 3; else counts.earthshaker = 2; }
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    if (total > 35) { const scale = 35 / total; for (const k of Object.keys(counts)) counts[k] = Math.floor(counts[k] * scale); }
    return counts;
}

function spawnMinion(x, y) { const ch = characters[selectedCharacter].secondary, spdMult = getSkeletonSpeedMultiplier(); return { x, y, health: getMinionHealth(), maxHealth: getMinionHealth(), damage: getMinionDamage(), speed: (ch.minionSpeed || 110) * spdMult, size: ch.minionSize || 12, color: ch.minionColor || '#e8dcc8', attackRange: (ch.minionAttackRange || 22), attackCooldown: 0, attackCooldownMax: (ch.minionAttackCooldown || 0.9), isRanged: false, rangedRange: 0, rangedSpeed: 200, rangedCooldownMax: 1.5, minionType: 'skeleton', lifetime: MINION_LIFETIME, maxLifetime: MINION_LIFETIME, angle: Math.random() * Math.PI * 2, hitFlash: 0, knockbackX: 0, knockbackY: 0, damageCooldown: 0, walkTimer: Math.random() * Math.PI * 2, isMoving: false, stuckCheckTimer: 0, lastStuckX: x, lastStuckY: y, isStuck: false, minigunSpin: 0, minigunMuzzleFlash: 0, railgunCharging: false, railgunCharge: 0 }; }
function spawnArcher(x, y) { const spdMult = getSkeletonSpeedMultiplier(); return { x, y, health: getMinionHealth(), maxHealth: getMinionHealth(), damage: Math.floor(getMinionDamage() * 1.15), speed: 85 * spdMult, size: 12, color: '#d4c8a8', attackRange: 24, attackCooldown: 0, attackCooldownMax: 1.1, isRanged: true, rangedRange: 560, rangedSpeed: 340, rangedCooldownMax: 1.4, minionType: 'archer', lifetime: MINION_LIFETIME, maxLifetime: MINION_LIFETIME, angle: Math.random() * Math.PI * 2, hitFlash: 0, knockbackX: 0, knockbackY: 0, damageCooldown: 0, walkTimer: Math.random() * Math.PI * 2, isMoving: false, stuckCheckTimer: 0, lastStuckX: x, lastStuckY: y, isStuck: false, minigunSpin: 0, minigunMuzzleFlash: 0, railgunCharging: false, railgunCharge: 0 }; }
function spawnMinigunSkeleton(x, y) { const spdMult = getSkeletonSpeedMultiplier(); return { x, y, health: getMinionHealth(), maxHealth: getMinionHealth(), damage: Math.floor(getMinionDamage() * 0.35), speed: 90 * spdMult, size: 13, color: '#e0ccb0', attackRange: 24, attackCooldown: 0, attackCooldownMax: 0.75, isRanged: true, rangedRange: 380, rangedSpeed: 520, rangedCooldownMax: 0.1, minionType: 'minigun', lifetime: MINION_LIFETIME, maxLifetime: MINION_LIFETIME, angle: Math.random() * Math.PI * 2, hitFlash: 0, knockbackX: 0, knockbackY: 0, damageCooldown: 0, walkTimer: Math.random() * Math.PI * 2, isMoving: false, stuckCheckTimer: 0, lastStuckX: x, lastStuckY: y, isStuck: false, minigunSpin: Math.random() * Math.PI * 2, minigunMuzzleFlash: 0, railgunCharging: false, railgunCharge: 0 }; }
function spawnRailgunSkeleton(x, y) { const spdMult = getSkeletonSpeedMultiplier(); return { x, y, health: getMinionHealth(), maxHealth: getMinionHealth(), damage: Math.floor(getMinionDamage() * 3.5), speed: 70 * spdMult, size: 14, color: '#b8c8f0', attackRange: 24, attackCooldown: 0, attackCooldownMax: 1.0, isRanged: true, rangedRange: 400, rangedSpeed: 0, rangedCooldownMax: 2.2, minionType: 'railgun', lifetime: MINION_LIFETIME, maxLifetime: MINION_LIFETIME, angle: Math.random() * Math.PI * 2, hitFlash: 0, knockbackX: 0, knockbackY: 0, damageCooldown: 0, walkTimer: Math.random() * Math.PI * 2, isMoving: false, stuckCheckTimer: 0, lastStuckX: x, lastStuckY: y, isStuck: false, minigunSpin: 0, minigunMuzzleFlash: 0, railgunCharging: false, railgunCharge: 0 }; }

function updateStuckDetection(entity, dt) { entity.stuckCheckTimer += dt; if (entity.stuckCheckTimer >= STUCK_CHECK_INTERVAL) { entity.stuckCheckTimer = 0; const moved = Math.hypot(entity.x - entity.lastStuckX, entity.y - entity.lastStuckY); entity.isStuck = moved < STUCK_DIST_THRESHOLD; entity.lastStuckX = entity.x; entity.lastStuckY = entity.y; } }

function updateEnemies(dt) {
    for (const enemy of enemies) {
        if (enemy.health <= 0) continue;
        const aggroRange = enemy.type === 'witch' ? (enemy.witchAggroRange || WITCH_AGGRO_RANGE) : ENEMY_AGGRO_RANGE;
        if (!enemy.aggroed && player && player.alive && Math.hypot(player.x - enemy.x, player.y - enemy.y) < aggroRange) enemy.aggroed = true;
        if (enemy.stunTimer > 0) { enemy.stunTimer -= dt; enemy.knockbackX *= 0.85; enemy.knockbackY *= 0.85; enemy.x += enemy.knockbackX * dt; enemy.y += enemy.knockbackY * dt; resolveWallCollision(enemy, enemy.size); continue; }
        if (enemy.hitFlash > 0) enemy.hitFlash -= dt;
        if (Math.abs(enemy.knockbackX) > 0.5 || Math.abs(enemy.knockbackY) > 0.5) { enemy.x += enemy.knockbackX * dt; enemy.y += enemy.knockbackY * dt; enemy.knockbackX *= Math.exp(-10 * dt); enemy.knockbackY *= Math.exp(-10 * dt); resolveWallCollision(enemy, enemy.size); }
        if (enemy.type === 'witch' && enemy.aggroed && player && player.alive) {
            const distToPlayer = Math.hypot(player.x - enemy.x, player.y - enemy.y); enemy.summonCooldown -= dt;
            if (enemy.summonCooldown <= 0 && distToPlayer <= WITCH_SUMMON_RANGE && (enemy.devilsAlive || 0) < enemy.maxDevils) { enemy.summonCooldown = enemy.summonCooldownMax; const spawnAngle = Math.random() * Math.PI * 2, spawnDist = enemy.size + 20, dx = enemy.x + Math.cos(spawnAngle) * spawnDist, dy = enemy.y + Math.sin(spawnAngle) * spawnDist; if (!isWallCircle(dx, dy, 6)) { const devil = spawnEnemy('devil', 0, dx, dy); devil.witchRef = enemy; devil.aggroed = true; enemies.push(devil); enemy.devilsAlive = (enemy.devilsAlive || 0) + 1; spawnParticles(dx, dy, 14, '#ff3311', 80, 0.5); spawnParticles(dx, dy, 8, '#ffaa22', 60, 0.35); spawnDamageNumber(dx, dy, 0, '#ff6644'); } }
            if (distToPlayer < WITCH_PREFERRED_DIST * 0.7 && distToPlayer > 0.01) { const awayX = enemy.x - player.x, awayY = enemy.y - player.y, awayDist = Math.hypot(awayX, awayY); enemy.x += (awayX / awayDist) * enemy.speed * 0.85 * dt; enemy.y += (awayY / awayDist) * enemy.speed * 0.85 * dt; resolveWallCollision(enemy, enemy.size); }
            else if (distToPlayer > WITCH_PREFERRED_DIST * 1.6) { updateStuckDetection(enemy, dt); const steering = getSteeringDirection(enemy.x, enemy.y, player.x, player.y, enemy.size, enemy.isStuck); enemy.x += steering.dx * enemy.speed * 0.7 * dt; enemy.y += steering.dy * enemy.speed * 0.7 * dt; resolveWallCollision(enemy, enemy.size); }
            else { updateStuckDetection(enemy, dt); const strafeAngle = Math.atan2(player.y - enemy.y, player.x - enemy.x) + Math.PI / 2, strafeDir = Math.sin(enemy.idleTimer * 1.3) > 0 ? 1 : -1; enemy.idleTimer += dt * 1.3; enemy.x += Math.cos(strafeAngle) * strafeDir * enemy.speed * 0.3 * dt; enemy.y += Math.sin(strafeAngle) * strafeDir * enemy.speed * 0.3 * dt; resolveWallCollision(enemy, enemy.size); }
        } else if (enemy.type === 'earthshaker' && enemy.aggroed && player && player.alive) {
            // Rage check
            if (!enemy.rageTriggered && enemy.health <= enemy.maxHealth * EARTHSHAKER_RAGE_HP_RATIO) {
                enemy.rageTriggered = true;
                enemy.speed = enemy.baseSpeed * (1 + (wave - 1) * 0.06) * EARTHSHAKER_RAGE_SPEED_MULT;
                spawnParticles(enemy.x, enemy.y, 20, '#ff6600', 120, 0.6);
                spawnParticles(enemy.x, enemy.y, 15, '#ff4400', 90, 0.45);
                screenShake = Math.max(screenShake, 4);
                spawnDamageNumber(enemy.x, enemy.y - enemy.size, 0, '#ff6600');
            }
            // Rage speed multiplier
            const rageSpeedMult = enemy.rageTriggered ? EARTHSHAKER_RAGE_SPEED_MULT : 1.0;
            const rageCDMult = enemy.rageTriggered ? EARTHSHAKER_RAGE_CD_MULT : 1.0;
            
            // Ground Slam state machine
            if (enemy.gsCharging) {
                // During charge: stand still, count up
                enemy.gsChargeTimer += dt;
                // Warning particles
                if (Math.random() < 0.6) {
                    const warnAngle = Math.random() * Math.PI * 2;
                    const warnDist = enemy.size + 8 + Math.random() * 25;
                    particles.push({
                        x: enemy.x + Math.cos(warnAngle) * warnDist,
                        y: enemy.y + Math.sin(warnAngle) * warnDist,
                        vx: (Math.random() - 0.5) * 30,
                        vy: (Math.random() - 0.5) * 30,
                        life: 0.5, maxLife: 0.5,
                        color: Math.random() < 0.5 ? '#ff8800' : '#ffcc44',
                        size: 2 + Math.random() * 3
                    });
                }
                if (enemy.gsChargeTimer >= EARTHSHAKER_GROUND_SLAM_CHARGE) {
                    // SLAM!
                    enemy.gsCharging = false;
                    enemy.gsChargeTimer = 0;
                    enemy.gsSlammed = true;
                    const cdRange = (EARTHSHAKER_GROUND_SLAM_MAX - EARTHSHAKER_GROUND_SLAM_MIN) * rageCDMult;
                    enemy.gsCooldown = EARTHSHAKER_GROUND_SLAM_MIN * rageCDMult + Math.random() * cdRange;
                    // Spawn shockwave
                    shockwaves.push({
                        x: enemy.x, y: enemy.y,
                        radius: enemy.size + 8,
                        maxRadius: EARTHSHAKER_GROUND_SLAM_RADIUS,
                        speed: EARTHSHAKER_SHOCKWAVE_SPEED,
                        damage: EARTHSHAKER_GROUND_SLAM_DAMAGE,
                        alive: true,
                        hitPlayer: false
                    });
                    spawnParticles(enemy.x, enemy.y, 25, '#ff8800', 180, 0.5);
                    spawnParticles(enemy.x, enemy.y, 20, '#ffcc44', 140, 0.4);
                    spawnParticles(enemy.x, enemy.y, 12, '#ffffff', 100, 0.3);
                    screenShake = Math.max(screenShake, 8);
                    // Damage walls near epicenter
                    const slamTileR = 2;
                    for (let wy = Math.floor((enemy.y - TILE * slamTileR) / TILE); wy <= Math.floor((enemy.y + TILE * slamTileR) / TILE); wy++) {
                        for (let wx = Math.floor((enemy.x - TILE * slamTileR) / TILE); wx <= Math.floor((enemy.x + TILE * slamTileR) / TILE); wx++) {
                            if (wx >= 0 && wx < MAP_COLS && wy >= 0 && wy < MAP_ROWS && mapTiles[wy][wx] >= 1) {
                                const wcx = wx * TILE + TILE / 2, wcy = wy * TILE + TILE / 2;
                                if (Math.hypot(wcx - enemy.x, wcy - enemy.y) < TILE * slamTileR) {
                                    damageWallTile(wx, wy, Math.atan2(wcy - enemy.y, wcx - enemy.x));
                                }
                            }
                        }
                    }
                }
                // Don't move during charge
                resolveWallCollision(enemy, enemy.size);
            } else {
                // Count down ground slam cooldown
                enemy.gsCooldown -= dt;
                if (enemy.gsCooldown <= 0 && !enemy.gsCharging) {
                    enemy.gsCharging = true;
                    enemy.gsChargeTimer = 0;
                }
                
                // Teleport timer
                const tpCD = enemy.tpCooldown;
                enemy.tpCooldown -= dt;
                if (enemy.tpCooldown <= 0) {
                    // Find valid teleport location
                    let tpFound = false;
                    for (let attempt = 0; attempt < 50; attempt++) {
                        const tpx = TILE * 2 + Math.random() * (WORLD_W - TILE * 4);
                        const tpy = TILE * 2 + Math.random() * (WORLD_H - TILE * 4);
                        if (isWallCircle(tpx, tpy, enemy.size + 6)) continue;
                        if (purpleBlock && Math.hypot(tpx - purpleBlock.x, tpy - purpleBlock.y) < enemy.size + purpleBlock.size + 10) continue;
                        if (player && Math.hypot(tpx - player.x, tpy - player.y) < EARTHSHAKER_TELEPORT_MIN_DIST) continue;
                        let tooCloseToEnemy = false;
                        for (const other of enemies) {
                            if (other === enemy || other.health <= 0) continue;
                            if (Math.hypot(tpx - other.x, tpy - other.y) < enemy.size + other.size + 20) { tooCloseToEnemy = true; break; }
                        }
                        if (tooCloseToEnemy) continue;
                        let tooCloseToMinion = false;
                        for (const minion of minions) {
                            if (minion.health <= 0) continue;
                            if (Math.hypot(tpx - minion.x, tpy - minion.y) < enemy.size + minion.size + 20) { tooCloseToMinion = true; break; }
                        }
                        if (tooCloseToMinion) continue;
                        // Valid spot found
                        spawnParticles(enemy.x, enemy.y, 18, '#9933cc', 120, 0.55);
                        spawnParticles(enemy.x, enemy.y, 10, '#bb66ff', 80, 0.4);
                        enemy.x = tpx;
                        enemy.y = tpy;
                        spawnParticles(enemy.x, enemy.y, 18, '#9933cc', 120, 0.55);
                        spawnParticles(enemy.x, enemy.y, 10, '#bb66ff', 80, 0.4);
                        spawnDamageNumber(enemy.x, enemy.y - enemy.size, 0, '#bb66ff');
                        tpFound = true;
                        break;
                    }
                    const tpRange = (EARTHSHAKER_TELEPORT_MAX - EARTHSHAKER_TELEPORT_MIN) * rageCDMult;
                    enemy.tpCooldown = EARTHSHAKER_TELEPORT_MIN * rageCDMult + Math.random() * tpRange;
                }
                
                // Normal movement: chase player
                const distToPlayer = Math.hypot(player.x - enemy.x, player.y - enemy.y);
                if (distToPlayer > enemy.size + PLAYER_SIZE) {
                    updateStuckDetection(enemy, dt);
                    const steering = getSteeringDirection(enemy.x, enemy.y, player.x, player.y, enemy.size, enemy.isStuck);
                    enemy.x += steering.dx * enemy.speed * rageSpeedMult * dt;
                    enemy.y += steering.dy * enemy.speed * rageSpeedMult * dt;
                }
                resolveWallCollision(enemy, enemy.size);
                
                // Contact damage
                const contactDist = enemy.size + PLAYER_SIZE;
                if (distToPlayer < contactDist && player.invulnTimer <= 0) {
                    const knockAngle = Math.atan2(player.y - enemy.y, player.x - enemy.x);
                    damagePlayer(enemy.damage, knockAngle);
                    const pushX = (player.x - enemy.x) / (distToPlayer + 0.01), pushY = (player.y - enemy.y) / (distToPlayer + 0.01);
                    enemy.knockbackX -= pushX * 80;
                    enemy.knockbackY -= pushY * 80;
                }
            }
        } else if (enemy.aggroed && player && player.alive) {
            let targetObj = { x: player.x, y: player.y, size: PLAYER_SIZE, type: 'player' }, closestDist = Math.hypot(player.x - enemy.x, player.y - enemy.y);
            for (const minion of minions) { if (minion.health <= 0) continue; const md = Math.hypot(minion.x - enemy.x, minion.y - enemy.y); if (md < closestDist) { closestDist = md; targetObj = { x: minion.x, y: minion.y, size: minion.size, type: 'minion', ref: minion }; } }
            const tdx = targetObj.x - enemy.x, tdy = targetObj.y - enemy.y, tdist = Math.hypot(tdx, tdy);
            if (tdist > 0.5) { updateStuckDetection(enemy, dt); const steering = getSteeringDirection(enemy.x, enemy.y, targetObj.x, targetObj.y, enemy.size, enemy.isStuck); enemy.x += steering.dx * enemy.speed * dt; enemy.y += steering.dy * enemy.speed * dt; resolveWallCollision(enemy, enemy.size); }
            const contactDist = targetObj.size + enemy.size;
            if (tdist < contactDist) {
                if (targetObj.type === 'player' && player.invulnTimer <= 0) { const knockAngle = Math.atan2(player.y - enemy.y, player.x - enemy.x); damagePlayer(enemy.damage, knockAngle); const pushX = (player.x - enemy.x) / (tdist + 0.01), pushY = (player.y - enemy.y) / (tdist + 0.01); enemy.knockbackX -= pushX * 100; enemy.knockbackY -= pushY * 100; }
                else if (targetObj.type === 'minion' && targetObj.ref.damageCooldown <= 0) { const tMinion = targetObj.ref; tMinion.health -= enemy.damage; tMinion.damageCooldown = 0.5; tMinion.hitFlash = 0.08; spawnParticles(tMinion.x, tMinion.y, 3, '#ffaa88', 40, 0.2); const pushMX = (tMinion.x - enemy.x) / (tdist + 0.01), pushMY = (tMinion.y - enemy.y) / (tdist + 0.01); tMinion.knockbackX += pushMX * 60; tMinion.knockbackY += pushMY * 60; if (tMinion.health <= 0) spawnParticles(tMinion.x, tMinion.y, 8, '#ddccbb', 80, 0.4); }
            }
        } else if (!enemy.aggroed) { enemy.idleTimer -= dt; if (enemy.idleTimer <= 0) { enemy.idleAngle += (Math.random() - 0.5) * 1.2; enemy.idleTimer = 1.0 + Math.random() * 2.0; } enemy.x += Math.cos(enemy.idleAngle) * enemy.speed * 0.3 * dt; enemy.y += Math.sin(enemy.idleAngle) * enemy.speed * 0.3 * dt; resolveWallCollision(enemy, enemy.size); }
        for (const other of enemies) { if (other === enemy || other.health <= 0) continue; const edx = enemy.x - other.x, edy = enemy.y - other.y, edist = Math.hypot(edx, edy), minDist = enemy.size + other.size; if (edist < minDist && edist > 0.01) { const sep = (minDist - edist) * 0.3; enemy.x += (edx / edist) * sep; enemy.y += (edy / edist) * sep; } }
    }
    enemies = enemies.filter(e => e.health > 0 || e.hitFlash > -0.5);
}

function updateMinions(dt) {
    for (const minion of minions) {
        if (minion.health <= 0) continue;
        minion.lifetime -= dt; if (minion.lifetime <= 0) { killMinionByTimeout(minion); continue; }
        minion.isMoving = false; if (minion.hitFlash > 0) minion.hitFlash -= dt; if (minion.damageCooldown > 0) minion.damageCooldown -= dt; if (minion.attackCooldown > 0) minion.attackCooldown -= dt; if (minion.minigunMuzzleFlash > 0) minion.minigunMuzzleFlash -= dt;
        if (minion.minionType === 'minigun') minion.minigunSpin += 18 * dt;
        if (Math.abs(minion.knockbackX) > 0.3 || Math.abs(minion.knockbackY) > 0.3) { minion.x += minion.knockbackX * dt; minion.y += minion.knockbackY * dt; minion.knockbackX *= Math.exp(-8 * dt); minion.knockbackY *= Math.exp(-8 * dt); resolveWallCollision(minion, minion.size); }
        const distToPlayer = player && player.alive ? Math.hypot(player.x - minion.x, player.y - minion.y) : Infinity;
        let nearestEnemy = null, nearestDist = Infinity;
        for (const enemy of enemies) { if (enemy.health <= 0) continue; const d = Math.hypot(enemy.x - minion.x, enemy.y - minion.y); if (d < nearestDist) { nearestDist = d; nearestEnemy = enemy; } }
        const shouldFollowPlayer = distToPlayer > MINION_DISENGAGE_DIST;
        const isRailgun = minion.minionType === 'railgun';
        const hasRailgunTarget = isRailgun && minion.railgunCharging && nearestEnemy && nearestDist < minion.rangedRange * 1.35;
        const chargingSpeedMult = hasRailgunTarget ? 0.35 : 1.0;
        if (nearestEnemy && nearestDist < MINION_AGGRO_RANGE && !shouldFollowPlayer) {
            const dx = nearestEnemy.x - minion.x, dy = nearestEnemy.y - minion.y, dist = Math.hypot(dx, dy); updateStuckDetection(minion, dt);
            if (minion.isRanged && dist < minion.rangedRange && dist > minion.attackRange) {
                if (minion.attackCooldown <= 0) {
                    const isMinigun = minion.minionType === 'minigun';
                    if (isRailgun) { if (!minion.railgunCharging) { minion.railgunCharging = true; minion.railgunCharge = 0; minion.attackCooldown = minion.rangedCooldownMax; minion.angle = Math.atan2(dy, dx); } }
                    else { minion.attackCooldown = minion.rangedCooldownMax; minion.angle = Math.atan2(dy, dx); const projColor = isMinigun ? '#ff8833' : (minion.minionType === 'archer' ? '#ffe8b0' : '#e8e0c8'), projSize = isMinigun ? 3 : (minion.minionType === 'archer' ? 4 : 3), homingStr = isMinigun ? 0.05 : 0; const bproj = spawnProjectile(minion.x + Math.cos(minion.angle) * (minion.size + 2), minion.y + Math.sin(minion.angle) * (minion.size + 2), minion.angle, minion.rangedSpeed, minion.damage, projColor, projSize, true, 0, homingStr, 0, 'minion'); bproj.rangeRemaining = minion.rangedRange; projectiles.push(bproj); if (isMinigun) { minion.minigunMuzzleFlash = 0.06; spawnParticles(minion.x + Math.cos(minion.angle) * (minion.size + 8), minion.y + Math.sin(minion.angle) * (minion.size + 8), 2, '#ff8833', 30, 0.12); } }
                }
                if (dist > minion.rangedRange * 0.7) { const steering = getSteeringDirection(minion.x, minion.y, nearestEnemy.x, nearestEnemy.y, minion.size, minion.isStuck); minion.x += steering.dx * minion.speed * chargingSpeedMult * dt; minion.y += steering.dy * minion.speed * chargingSpeedMult * dt; minion.isMoving = true; minion.angle = Math.atan2(dx, dy); resolveWallCollision(minion, minion.size); }
            } else if (dist > minion.attackRange) { const steering = getSteeringDirection(minion.x, minion.y, nearestEnemy.x, nearestEnemy.y, minion.size, minion.isStuck); minion.x += steering.dx * minion.speed * chargingSpeedMult * dt; minion.y += steering.dy * minion.speed * chargingSpeedMult * dt; minion.isMoving = true; minion.angle = Math.atan2(dx, dy); resolveWallCollision(minion, minion.size); }
            else if (minion.attackCooldown <= 0) { minion.attackCooldown = minion.attackCooldownMax; minion.angle = Math.atan2(dx, dy); damageEnemy(nearestEnemy, minion.damage, minion.angle); spawnParticles(nearestEnemy.x - Math.cos(minion.angle) * nearestEnemy.size * 0.5, nearestEnemy.y - Math.sin(minion.angle) * nearestEnemy.size * 0.5, 3, '#ffffff', 30, 0.15); }
        } else if (player && player.alive) { const dx = player.x - minion.x, dy = player.y - minion.y, dist = Math.hypot(dx, dy); if (dist > 45) { updateStuckDetection(minion, dt); const steering = getSteeringDirection(minion.x, minion.y, player.x, player.y, minion.size, minion.isStuck); minion.x += steering.dx * minion.speed * 0.75 * chargingSpeedMult * dt; minion.y += steering.dy * minion.speed * 0.75 * chargingSpeedMult * dt; minion.isMoving = true; minion.angle = Math.atan2(dx, dy); resolveWallCollision(minion, minion.size); } }
        // Railgun minions: always charge when not cooling down from a shot
        if (isRailgun && !minion.railgunCharging && minion.attackCooldown <= 0) {
            minion.railgunCharging = true;
            minion.railgunCharge = 0;
        }
        if (isRailgun && minion.railgunCharging) {
            let chargeTarget = null, chargeTargetDist = Infinity;
            for (const enemy of enemies) { if (enemy.health <= 0) continue; const d = Math.hypot(enemy.x - minion.x, enemy.y - minion.y); if (d < chargeTargetDist) { chargeTargetDist = d; chargeTarget = enemy; } }
            if (!chargeTarget || chargeTargetDist > minion.rangedRange * 1.35) {
                // No valid target — charge up to full and hold
                if (minion.railgunCharge < 1.0) {
                    minion.railgunCharge = Math.min(1.0, minion.railgunCharge + dt / MINION_RAILGUN_CHARGE_TIME);
                }
                // Spark particles when nearly/max charged while idle
                if (minion.railgunCharge > 0.65 && Math.random() < 0.25) {
                    const tipX = minion.x + Math.cos(minion.angle) * (minion.size + 8), tipY = minion.y + Math.sin(minion.angle) * (minion.size + 8);
                    const orbitAngle = minion.angle + Math.random() * Math.PI * 2, orbitDist = 4 + minion.railgunCharge * 9;
                    particles.push({ x: tipX + Math.cos(orbitAngle) * orbitDist, y: tipY + Math.sin(orbitAngle) * orbitDist, vx: (Math.random() - 0.5) * 20, vy: (Math.random() - 0.5) * 20, life: 0.3, maxLife: 0.3, color: minion.railgunCharge > 0.85 ? '#ffffff' : '#aaccff', size: 1 + Math.random() * 2 });
                }
            }
            else {
                const tdx = chargeTarget.x - minion.x, tdy = chargeTarget.y - minion.y; minion.angle = Math.atan2(tdy, tdx);
                minion.railgunCharge += dt / MINION_RAILGUN_CHARGE_TIME;
                if (Math.random() < 0.5) { const tipX = minion.x + Math.cos(minion.angle) * (minion.size + 8), tipY = minion.y + Math.sin(minion.angle) * (minion.size + 8), orbitAngle = minion.angle + Math.random() * Math.PI * 2, orbitDist = 4 + minion.railgunCharge * 9; particles.push({ x: tipX + Math.cos(orbitAngle) * orbitDist, y: tipY + Math.sin(orbitAngle) * orbitDist, vx: (Math.random() - 0.5) * 30, vy: (Math.random() - 0.5) * 30, life: 0.3, maxLife: 0.3, color: minion.railgunCharge > 0.65 ? '#aaccff' : '#6699ff', size: 1 + Math.random() * 2.5 }); }
                if (minion.railgunCharge >= 1.0) { fireMinionRailgunShot(minion, chargeTarget.x, chargeTarget.y); minion.minigunMuzzleFlash = 0.06; minion.railgunCharging = false; minion.railgunCharge = 0; minion.attackCooldown = minion.rangedCooldownMax; }
            }
        }
        if (minion.isMoving) minion.walkTimer += dt * 8 * (minion.speed / 110);
        for (const other of minions) { if (other === minion || other.health <= 0) continue; const mdx = minion.x - other.x, mdy = minion.y - other.y, mdist = Math.hypot(mdx, mdy), minSep = minion.size + other.size; if (mdist < minSep && mdist > 0.01) { const sep = (minSep - mdist) * 0.25; minion.x += (mdx / mdist) * sep; minion.y += (mdy / mdist) * sep; } }
    }
    minions = minions.filter(m => m.health > 0);
}
