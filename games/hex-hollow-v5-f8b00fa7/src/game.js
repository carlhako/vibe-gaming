// ─── ENEMIES ────────────────────────────────
const enemies = [];
const enemyMeshes = new Map();

function spawnEnemy(type, x, z) {
    const g = createEnemyMesh(type);
    g.position.set(x, 0, z);
    arenaGroup.add(g);
    const baseStats = {
        ghost: { hp: 35, speed: 5.5, damage: 8, coins: 8 },
        zombie: { hp: 65, speed: 2.2, damage: 16, coins: 10 },
        skeleton: { hp: 45, speed: 3.5, damage: 11, coins: 9 },
        witch: { hp: 180, speed: 2.8, damage: 22, coins: 55 }
    };
    const s = baseStats[type];
    const waveScale = 1 + (gameState.wave - 1) * .15;
    const enemy = {
        type, mesh: g,
        hp: Math.floor(s.hp * waveScale),
        maxHp: Math.floor(s.hp * waveScale),
        speed: s.speed * (1 + (gameState.wave - 1) * .06),
        damage: Math.floor(s.damage * waveScale),
        coins: s.coins + Math.floor(Math.random() * 6),
        attackCooldown: 0,
        attackRate: type === 'witch' ? .8 : 1.2,
        alive: true,
        isBoss: type === 'witch',
    };
    enemies.push(enemy);
    enemyMeshes.set(g, enemy);
    return enemy;
}

function damageEnemy(enemy, dmg) {
    if (!enemy.alive) return;
    enemy.hp -= dmg;
    // Flash white
    enemy.mesh.traverse(c => {
        if (c.material && c.material.color && !c.material.emissive) {
            c.material._origColor = c.material.color.getHex();
            c.material.color.set(0xffffff);
            setTimeout(() => {
                if (c.material && c.material._origColor !== undefined)
                    c.material.color.setHex(c.material._origColor);
            }, 60);
        }
    });
    if (enemy.hp <= 0) { killEnemy(enemy); }
}

function killEnemy(enemy) {
    enemy.alive = false;
    sfxCoin();
    // Spawn coin particles
    for (let i = 0; i < 5; i++) {
        const coinMesh = new THREE.Mesh(new THREE.CylinderGeometry(.08, .08, .04, 8), goldMat);
        coinMesh.position.copy(enemy.mesh.position);
        coinMesh.position.y += .5 + Math.random() * .5;
        coinMesh.position.x += (Math.random() - .5) * 1;
        coinMesh.position.z += (Math.random() - .5) * 1;
        coinMesh.userData = { collected: false, lifetime: 0, value: Math.ceil(enemy.coins / 5) };
        arenaGroup.add(coinMesh);
        coinDrops.push(coinMesh);
    }
    // Death effect
    const particles = [];
    for (let i = 0; i < 8; i++) {
        const p = new THREE.Mesh(new THREE.SphereGeometry(.06, 4, 4), new THREE.MeshBasicMaterial({
            color: enemy.type === 'ghost' ? 0x88aaff : enemy.type === 'witch' ? 0xaa44ff : 0xcccccc
        }));
        p.position.copy(enemy.mesh.position);
        p.position.y += .5;
        p.userData = {
            vel: new THREE.Vector3((Math.random() - .5) * 3, (Math.random() - .5) * 3 + 2, (Math.random() - .5) * 3),
            life: .6
        };
        arenaGroup.add(p);
        particles.push(p);
    }
    deathParticles.push(...particles);
    // Remove mesh
    arenaGroup.remove(enemy.mesh);
    enemyMeshes.delete(enemy.mesh);
}

const coinDrops = [];
const deathParticles = [];
const projectilePool = [];

function spawnProjectile(pos, dir, speed, dmg, color = 0xff4400, size = .15, isLightning = false) {
    const geo = isLightning ? new THREE.CylinderGeometry(.05, .05, 1.5, 6) : new THREE.SphereGeometry(size, 8, 8);
    const mat = isLightning ? new THREE.MeshBasicMaterial({ color }) : new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 2, roughness: .2 });
    const p = new THREE.Mesh(geo, mat);
    p.position.copy(pos);
    if (isLightning) { p.rotation.z = Math.PI / 2; p.lookAt(pos.clone().add(dir)); }
    arenaGroup.add(p);
    const proj = { mesh: p, dir: dir.clone().normalize(), speed, dmg, life: 2.5, isLightning, hitEnemies: new Set() };
    projectilePool.push(proj);
    return proj;
}

// ─── HELPER: COLLISION WITH PILLARS ─────────
function collidesWithPillars(x, z, radius) {
    for (const p of pillarPositions) {
        const dx = x - p.x, dz = z - p.z;
        if (Math.sqrt(dx * dx + dz * dz) < p.radius + radius) return true;
    }
    return false;
}
function collidesWithArenaWalls(x, z, radius) {
    const half = arenaSize / 2 - radius;
    return Math.abs(x) > half || Math.abs(z) > half;
}
function clampToArena(x, z, radius) {
    const half = arenaSize / 2 - radius;
    return { x: Math.max(-half, Math.min(half, x)), z: Math.max(-half, Math.min(half, z)) };
}

// ─── WAVE MANAGEMENT ────────────────────────
function getEnemyCountForWave(w) { return 3 + Math.floor(w * 2.2); }
function clearArena() {
    for (const e of enemies) {
        if (e.mesh) arenaGroup.remove(e.mesh);
        enemyMeshes.delete(e.mesh);
    }
    enemies.length = 0;
    for (const c of coinDrops) arenaGroup.remove(c);
    coinDrops.length = 0;
    for (const p of projectilePool) arenaGroup.remove(p.mesh);
    projectilePool.length = 0;
    for (const p of deathParticles) arenaGroup.remove(p);
    deathParticles.length = 0;
}

function spawnWave() {
    clearArena();
    gameState.wave++;
    const count = getEnemyCountForWave(gameState.wave);
    const types = ['ghost', 'skeleton', 'zombie'];
    const spawnList = [];
    for (let i = 0; i < count; i++) {
        let type;
        if (gameState.wave <= 2) { type = types[Math.floor(Math.random() * 2)]; }
        else { type = types[Math.floor(Math.random() * 3)]; }
        spawnList.push(type);
    }
    if (gameState.wave % 5 === 0) { spawnList.push('witch'); }
    for (const type of spawnList) {
        let sx, sz, tries = 0;
        do {
            sx = (Math.random() - .5) * (arenaSize - 4);
            sz = (Math.random() - .5) * (arenaSize - 4);
            tries++;
        } while (tries < 30 && (Math.abs(sx - playerGroup.position.x) < 5 && Math.abs(sz - playerGroup.position.z) < 5));
        spawnEnemy(type, sx, sz);
    }
}

function allEnemiesDead() { return enemies.every(e => !e.alive); }

function returnToLobby() {
    gameState.inArena = false;
    gameState.inLobby = true;
    gameState.hp = gameState.maxHp;
    gameState.totalWavesCleared = Math.max(gameState.totalWavesCleared, gameState.wave);
    clearArena();
    resetPlayerPosition();
    playerGroup.position.set(0, .1, -2);
    document.exitPointerLock();
    document.getElementById('crosshair').style.display = 'none';
    document.getElementById('shop-overlay').style.display = 'block';
    document.getElementById('wave-display').textContent = 'Lobby';
    updateShopUI();
    stopDrone();
}

function startArena() {
    gameState.inLobby = false;
    gameState.inArena = true;
    gameState.wave = 0;
    clearArena();
    playerGroup.position.set(0, .1, 0);
    spawnWave();
    document.getElementById('shop-overlay').style.display = 'none';
    document.getElementById('wave-display').textContent = 'Wave ' + gameState.wave;
    updateHUD();
    renderer.domElement.requestPointerLock();
    document.getElementById('crosshair').style.display = 'block';
    startDrone();
}

// ─── SHOP POTION BUTTON ─────────────────────
document.getElementById('shop-potion-btn').addEventListener('click', () => {
    if (gameState.coins >= 25 && gameState.hp < gameState.maxHp) {
        gameState.coins -= 25;
        gameState.hp = Math.min(gameState.maxHp, gameState.hp + 40);
        updateShopUI();
        updateHUD();
        sfxCoin();
    }
});

// ─── RESTART BUTTON ─────────────────────────
document.getElementById('restart-btn').addEventListener('click', () => {
    gameState.gameOver = false;
    gameState.coins = 100;
    gameState.hp = gameState.maxHp;
    gameState.wave = 0;
    gameState.inArena = false;
    gameState.inLobby = true;
    gameState.weaponUpgrades = {};
    gameState.equippedWeaponId = gameState.ownedWeapons.includes('rusty_dagger') ? 'rusty_dagger' : gameState.ownedWeapons[0] || 'rusty_dagger';
    if (!gameState.ownedWeapons.includes('rusty_dagger')) gameState.ownedWeapons.unshift('rusty_dagger');
    setWeaponMesh(getEquippedWeapon());
    resetPlayerPosition();
    playerGroup.position.set(0, .1, -2);
    document.getElementById('game-over-overlay').style.display = 'none';
    document.getElementById('shop-overlay').style.display = 'block';
    document.getElementById('wave-display').textContent = 'Lobby';
    updateShopUI();
    updateHUD();
});

// ─── INPUT ──────────────────────────────────
let pointerLocked = false;
document.addEventListener('pointerlockchange', () => {
    pointerLocked = document.pointerLockElement === renderer.domElement;
    if (!pointerLocked && gameState.inArena && !gameState.gameOver) {
        document.getElementById('crosshair').style.display = 'none';
    }
    if (pointerLocked && gameState.inArena) {
        document.getElementById('crosshair').style.display = 'block';
    }
});

document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && pointerLocked) {
        document.exitPointerLock();
        return;
    }
    if (gameState.gameOver) return;
    const k = e.key.toLowerCase();
    if (k in keys) keys[k] = true;
    if (k === ' ') { keys.space = true; e.preventDefault(); }
});
document.addEventListener('keyup', e => {
    const k = e.key.toLowerCase();
    if (k in keys) keys[k] = false;
    if (k === ' ') keys.space = false;
});

document.addEventListener('mousemove', e => {
    if (!pointerLocked || !gameState.inArena || gameState.gameOver) return;
    const sens = .003;
    playerYaw -= e.movementX * sens;
    playerPitch -= e.movementY * sens;
    playerPitch = Math.max(-1.2, Math.min(.3, playerPitch));
});

document.addEventListener('mousedown', e => {
    if (e.button === 0 && pointerLocked && gameState.inArena && !gameState.gameOver && gameState.attackCooldown <= 0) {
        performAttack();
    }
    if (e.button === 0 && !pointerLocked && gameState.inArena && !gameState.gameOver) {
        renderer.domElement.requestPointerLock();
    }
});

function performAttack() {
    const wDef = getEquippedWeapon();
    const spd = getWeaponSpeed(wDef);
    gameState.attackCooldown = .6 / spd;
    if (wDef.type === 'melee') {
        sfxClang();
        const dmg = getWeaponDamage(wDef);
        const swingRange = getWeaponSpeed(wDef) > 1.5 ? wDef.range * 1.3 : wDef.range;
        for (const enemy of enemies) {
            if (!enemy.alive) continue;
            const dx = enemy.mesh.position.x - playerGroup.position.x;
            const dz = enemy.mesh.position.z - playerGroup.position.z;
            const dist = Math.sqrt(dx * dx + dz * dz);
            if (dist < swingRange) {
                const forward = new THREE.Vector3(Math.sin(playerYaw), 0, Math.cos(playerYaw));
                const toEnemy = new THREE.Vector3(dx, 0, dz).normalize();
                const dot = forward.dot(toEnemy);
                if (dot > .3) {
                    damageEnemy(enemy, dmg);
                }
            }
        }
        // Swing visual
        const origRot = weaponGroup.rotation.z;
        const swingDur = 150;
        const start = performance.now();
        function animSwing(now) {
            const elapsed = now - start;
            const t = elapsed / swingDur;
            if (t < .5) { weaponGroup.rotation.z = origRot - Math.PI * .7 * Math.sin(t * Math.PI); }
            else if (t < 1) { weaponGroup.rotation.z = origRot - Math.PI * .7 * Math.sin(t * Math.PI); }
            else { weaponGroup.rotation.z = origRot; return; }
            requestAnimationFrame(animSwing);
        }
        requestAnimationFrame(animSwing);
    } else {
        // Ranged
        const forward = new THREE.Vector3(Math.sin(playerYaw), playerPitch * .3, Math.cos(playerYaw)).normalize();
        const spawnPos = playerGroup.position.clone().add(new THREE.Vector3(0, 1.2, 0)).add(forward.clone().multiplyScalar(.8));
        if (wDef.id === 'archmage_staff') {
            sfxLightning();
            spawnProjectile(spawnPos, forward, 30, getWeaponDamage(wDef), 0x8844ff, .12, true);
        } else if (wDef.id === 'staff_of_embers') {
            sfxFireball();
            spawnProjectile(spawnPos, forward, 18, getWeaponDamage(wDef), 0xff5500, .22, false);
        } else {
            sfxClang();
            spawnProjectile(spawnPos, forward, 25, getWeaponDamage(wDef), 0xccccaa, .08, false);
        }
    }
}

// ─── COUNTDOWN ──────────────────────────────
let countdownTimer = null;
function startCountdown() {
    if (gameState.countdownActive || !gameState.inLobby || gameState.gameOver) return;
    gameState.countdownActive = true;
    gameState.countdownValue = 5;
    document.getElementById('countdown-overlay').style.display = 'block';
    document.getElementById('countdown-overlay').textContent = '5';
    sfxCountdown();
    countdownTimer = setInterval(() => {
        gameState.countdownValue--;
        if (gameState.countdownValue > 0) {
            document.getElementById('countdown-overlay').textContent = gameState.countdownValue;
            sfxCountdown();
        } else {
            clearInterval(countdownTimer);
            countdownTimer = null;
            document.getElementById('countdown-overlay').textContent = 'GO!';
            sfxGo();
            setTimeout(() => {
                document.getElementById('countdown-overlay').style.display = 'none';
                gameState.countdownActive = false;
                startArena();
            }, 500);
        }
    }, 1000);
}
function cancelCountdown() {
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    gameState.countdownActive = false;
    document.getElementById('countdown-overlay').style.display = 'none';
}

// ─── GAME LOOP ──────────────────────────────
const clock = new THREE.Clock();
let lastTorchFlicker = 0;

function update(dt) {
    dt = Math.min(dt, .2);
    // Torch flicker
    const allTorches = [...lobbyTorches, ...arenaTorches];
    for (const t of allTorches) {
        const baseIntensity = t.light.intensity;
        const flicker = .85 + Math.random() * .3;
        t.light.intensity = baseIntensity * flicker;
        if (t.flame) t.flame.material.emissiveIntensity = 2 * flicker;
    }

    // Update death particles
    for (let i = deathParticles.length - 1; i >= 0; i--) {
        const p = deathParticles[i];
        p.userData.life -= dt;
        if (p.userData.life <= 0) { arenaGroup.remove(p); deathParticles.splice(i, 1); continue; }
        p.position.add(p.userData.vel.clone().multiplyScalar(dt));
        p.userData.vel.y -= 8 * dt;
        p.material.opacity = p.userData.life / .6;
    }

    // Update coin drops (magnet)
    for (let i = coinDrops.length - 1; i >= 0; i--) {
        const c = coinDrops[i];
        c.userData.lifetime += dt;
        if (c.userData.lifetime > 8) { arenaGroup.remove(c); coinDrops.splice(i, 1); continue; }
        const dx = playerGroup.position.x - c.position.x;
        const dz = playerGroup.position.z - c.position.z;
        const dy = playerGroup.position.y + .5 - c.position.y;
        const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
        const magnetRange = 7;
        if (dist < magnetRange) {
            const spd = Math.max(3, 12 * (1 - dist / magnetRange));
            const dir = new THREE.Vector3(dx, dy, dz).normalize();
            c.position.add(dir.multiplyScalar(spd * dt));
            if (dist < .8) {
                gameState.coins += c.userData.value || 1;
                arenaGroup.remove(c);
                coinDrops.splice(i, 1);
                sfxCoin();
                updateHUD();
                updateShopUI();
            }
        }
        c.rotation.y += dt * 5;
    }

    // Update projectiles
    for (let i = projectilePool.length - 1; i >= 0; i--) {
        const proj = projectilePool[i];
        proj.life -= dt;
        if (proj.life <= 0) { arenaGroup.remove(proj.mesh); projectilePool.splice(i, 1); continue; }
        proj.mesh.position.add(proj.dir.clone().multiplyScalar(proj.speed * dt));
        // Check arena bounds
        const pos = proj.mesh.position;
        if (Math.abs(pos.x) > arenaSize / 2 || Math.abs(pos.z) > arenaSize / 2 || pos.y < 0 || pos.y > arenaHeight) {
            if (proj.isLightning) {
                // Lightning chains on wall hit - find nearest enemy
                let nearest = null, nd = 6;
                for (const e of enemies) {
                    if (!e.alive || proj.hitEnemies.has(e)) continue;
                    const d = e.mesh.position.distanceTo(pos);
                    if (d < nd) { nd = d; nearest = e; }
                }
                if (nearest) { damageEnemy(nearest, proj.dmg * .6); proj.hitEnemies.add(nearest); }
            }
            arenaGroup.remove(proj.mesh);
            projectilePool.splice(i, 1);
            continue;
        }
        // Check pillar collision
        if (collidesWithPillars(pos.x, pos.z, .2)) {
            if (proj.isLightning) {
                let nearest = null, nd = 6;
                for (const e of enemies) {
                    if (!e.alive || proj.hitEnemies.has(e)) continue;
                    const d = e.mesh.position.distanceTo(pos);
                    if (d < nd) { nd = d; nearest = e; }
                }
                if (nearest) { damageEnemy(nearest, proj.dmg * .6); proj.hitEnemies.add(nearest); }
            }
            arenaGroup.remove(proj.mesh);
            projectilePool.splice(i, 1);
            continue;
        }
        // Check enemy hits
        for (const enemy of enemies) {
            if (!enemy.alive || proj.hitEnemies.has(enemy)) continue;
            const ed = enemy.mesh.position.distanceTo(proj.mesh.position);
            if (ed < 1.2) {
                damageEnemy(enemy, proj.dmg);
                proj.hitEnemies.add(enemy);
                if (proj.isLightning) {
                    // Chain to nearby enemies
                    const chainCount = 3;
                    let chained = 0;
                    const sorted = [...enemies].filter(e => e.alive && !proj.hitEnemies.has(e))
                        .sort((a, b) => a.mesh.position.distanceTo(enemy.mesh.position) - b.mesh.position.distanceTo(enemy.mesh.position));
                    for (const ce of sorted) {
                        if (chained >= chainCount) break;
                        const cd = ce.mesh.position.distanceTo(enemy.mesh.position);
                        if (cd < 7) {
                            damageEnemy(ce, Math.floor(proj.dmg * .55));
                            proj.hitEnemies.add(ce);
                            chained++;
                        }
                    }
                    arenaGroup.remove(proj.mesh);
                    projectilePool.splice(i, 1);
                    break;
                } else {
                    arenaGroup.remove(proj.mesh);
                    projectilePool.splice(i, 1);
                    break;
                }
            }
        }
    }

    if (gameState.gameOver) return;

    // Update player
    if (gameState.inArena || gameState.inLobby) {
        // Cooldowns
        gameState.attackCooldown = Math.max(0, gameState.attackCooldown - dt);
        gameState.dodgeCooldown = Math.max(0, gameState.dodgeCooldown - dt);
        gameState.invincibleTimer = Math.max(0, gameState.invincibleTimer - dt);
        gameState.invincible = gameState.invincibleTimer > 0 || gameState.isDodging;

        // Dodge
        if (gameState.isDodging) {
            gameState.dodgeTimer -= dt;
            if (gameState.dodgeTimer <= 0) {
                gameState.isDodging = false;
                gameState.invincibleTimer = .15;
            } else {
                playerGroup.position.add(gameState.dodgeDir.clone().multiplyScalar(16 * dt));
                if (gameState.inArena) {
                    const clamped = clampToArena(playerGroup.position.x, playerGroup.position.z, .4);
                    playerGroup.position.x = clamped.x;
                    playerGroup.position.z = clamped.z;
                }
            }
        }

        // Movement
        if (!gameState.isDodging && !gameState.countdownActive) {
            const forward = new THREE.Vector3(Math.sin(playerYaw), 0, Math.cos(playerYaw));
            const right = new THREE.Vector3(Math.cos(playerYaw), 0, -Math.sin(playerYaw));
            playerDirection.set(0, 0, 0);
            if (keys.w) playerDirection.add(forward);
            if (keys.s) playerDirection.sub(forward);
            if (keys.a) playerDirection.sub(right);
            if (keys.d) playerDirection.add(right);
            if (playerDirection.length() > 1) playerDirection.normalize();
            const spd = playerSpeed * (gameState.inLobby ? .7 : 1);
            playerGroup.position.x += playerDirection.x * spd * dt;
            playerGroup.position.z += playerDirection.z * spd * dt;

            // Collision
            if (gameState.inArena) {
                const clamped = clampToArena(playerGroup.position.x, playerGroup.position.z, .4);
                playerGroup.position.x = clamped.x;
                playerGroup.position.z = clamped.z;
                if (collidesWithPillars(playerGroup.position.x, playerGroup.position.z, .4)) {
                    playerGroup.position.x -= playerDirection.x * spd * dt;
                    playerGroup.position.z -= playerDirection.z * spd * dt;
                }
            } else if (gameState.inLobby) {
                playerGroup.position.x = Math.max(-lobbyFloor / 2 + .3, Math.min(lobbyFloor / 2 - .3, playerGroup.position.x));
                playerGroup.position.z = Math.max(-lobbyDepth / 2 + .3, Math.min(lobbyDepth / 2 - .3, playerGroup.position.z));
            }
        }

        // Dodge roll
        if (keys.space && gameState.dodgeCooldown <= 0 && !gameState.isDodging && !gameState.countdownActive && gameState.inArena) {
            const moveDir = playerDirection.length() > .1 ? playerDirection.clone().normalize() : new THREE.Vector3(Math.sin(playerYaw), 0, Math.cos(playerYaw));
            gameState.dodgeDir.copy(moveDir);
            gameState.isDodging = true;
            gameState.dodgeTimer = .28;
            gameState.dodgeCooldown = .8;
            sfxDodge();
        }

        // Check lobby doorway trigger
        if (gameState.inLobby && !gameState.countdownActive && !gameState.inArena) {
            if (playerGroup.position.z > lobbyDepth / 2 - 1.2 && Math.abs(playerGroup.position.x) < 1.5) {
                startCountdown();
            }
        }
        // Cancel countdown if player moves away
        if (gameState.countdownActive && gameState.inLobby) {
            if (!(playerGroup.position.z > lobbyDepth / 2 - 1.2 && Math.abs(playerGroup.position.x) < 1.5)) {
                cancelCountdown();
            }
        }

        // Update player facing
        if (gameState.inLobby && playerDirection.length() > .1) {
            playerYaw = Math.atan2(playerDirection.x, playerDirection.z);
        }
        playerGroup.rotation.y = playerYaw;
    }

    // Update enemies
    if (gameState.inArena) {
        for (const enemy of enemies) {
            if (!enemy.alive) continue;
            const dx = playerGroup.position.x - enemy.mesh.position.x;
            const dz = playerGroup.position.z - enemy.mesh.position.z;
            const dist = Math.sqrt(dx * dx + dz * dz);
            const toPlayer = new THREE.Vector3(dx, 0, dz).normalize();

            // Ghost floating
            if (enemy.type === 'ghost') {
                enemy.mesh.position.y = Math.sin(performance.now() * .004 + enemy.mesh.userData.floatOffset) * .25 + .7;
            }

            // Move toward player
            let moveX = toPlayer.x * enemy.speed * dt;
            let moveZ = toPlayer.z * enemy.speed * dt;
            const nx = enemy.mesh.position.x + moveX;
            const nz = enemy.mesh.position.z + moveZ;

            // Collision
            if (enemy.type === 'ghost') {
                // Ghosts phase through pillars
                const clamped = clampToArena(nx, nz, .35);
                enemy.mesh.position.x = clamped.x;
                enemy.mesh.position.z = clamped.z;
            } else {
                // Other enemies avoid pillars
                const clamped = clampToArena(nx, nz, .35);
                let tx = clamped.x, tz = clamped.z;
                if (collidesWithPillars(tx, tz, .35)) {
                    // Try sidestep
                    const sidestep = new THREE.Vector3(-toPlayer.z, 0, toPlayer.x);
                    const altX = enemy.mesh.position.x + sidestep.x * enemy.speed * dt;
                    const altZ = enemy.mesh.position.z + sidestep.z * enemy.speed * dt;
                    if (!collidesWithPillars(altX, altZ, .35) && !collidesWithArenaWalls(altX, altZ, .35)) {
                        tx = altX;
                        tz = altZ;
                    } else {
                        const altX2 = enemy.mesh.position.x - sidestep.x * enemy.speed * dt;
                        const altZ2 = enemy.mesh.position.z - sidestep.z * enemy.speed * dt;
                        if (!collidesWithPillars(altX2, altZ2, .35) && !collidesWithArenaWalls(altX2, altZ2, .35)) {
                            tx = altX2;
                            tz = altZ2;
                        } else { tx = enemy.mesh.position.x; tz = enemy.mesh.position.z; }
                    }
                }
                enemy.mesh.position.x = tx;
                enemy.mesh.position.z = tz;
            }

            // Face player
            enemy.mesh.lookAt(new THREE.Vector3(playerGroup.position.x, enemy.mesh.position.y, playerGroup.position.z));

            // Attack
            enemy.attackCooldown -= dt;
            if (enemy.attackCooldown <= 0 && dist < 2) {
                if (enemy.type === 'witch' && dist < 15) {
                    // Ranged magic attack
                    const magicPos = enemy.mesh.position.clone();
                    magicPos.y += 1;
                    const magicDir = new THREE.Vector3(dx, .2, dz).normalize();
                    const bolt = new THREE.Mesh(new THREE.SphereGeometry(.15, 6, 6), new THREE.MeshBasicMaterial({ color: 0xcc44ff }));
                    bolt.position.copy(magicPos);
                    arenaGroup.add(bolt);
                    const boltData = { mesh: bolt, dir: magicDir, speed: 8, life: 2.5 };
                    projectilePool.push({ ...boltData, dmg: enemy.damage, hitEnemies: new Set(), isLightning: false });
                    enemy.attackCooldown = enemy.attackRate;
                } else if (dist < 2) {
                    // Melee attack
                    if (!gameState.invincible) {
                        gameState.hp -= enemy.damage;
                        gameState.invincibleTimer = .5;
                        sfxHurt();
                        updateHUD();
                        if (gameState.hp <= 0) { gameState.hp = 0; updateHUD(); showGameOver(); return; }
                    }
                    enemy.attackCooldown = enemy.attackRate;
                }
            }

            // Witch glow
            if (enemy.type === 'witch') {
                enemy.mesh.userData.magicGlow += dt * 3;
                const glowIntensity = 1.5 + Math.sin(enemy.mesh.userData.magicGlow) * .5;
                enemy.mesh.children.forEach(c => {
                    if (c.material && c.material.emissiveIntensity > 1) c.material.emissiveIntensity = glowIntensity;
                });
            }
        }

        // Check wave clear
        if (allEnemiesDead() && enemies.length > 0) {
            const msg = document.getElementById('message-overlay');
            msg.textContent = '⚔ Wave ' + gameState.wave + ' Cleared! ⚔';
            msg.style.display = 'block';
            setTimeout(() => { msg.style.display = 'none'; }, 1800);
            setTimeout(() => {
                clearArena();
                returnToLobby();
                updateShopUI();
                updateHUD();
            }, 2200);
            // Prevent double trigger
            enemies.length = 0;
        }
    }

    // Camera
    if (gameState.inArena || gameState.inLobby) {
        const camDist = gameState.inArena ? 5.5 : 4.5;
        const camHeight = gameState.inArena ? 3.5 : 3;
        let targetX = playerGroup.position.x - Math.sin(playerYaw) * camDist * Math.cos(playerPitch);
        let targetY = playerGroup.position.y + camHeight + Math.sin(playerPitch) * camDist;
        let targetZ = playerGroup.position.z - Math.cos(playerYaw) * camDist * Math.cos(playerPitch);
        // Clamp camera inside room bounds so it can't go through walls
        if (gameState.inLobby) {
            const margin = 0.5;
            targetX = Math.max(-lobbyFloor / 2 + margin, Math.min(lobbyFloor / 2 - margin, targetX));
            targetZ = Math.max(-lobbyDepth / 2 + margin, Math.min(lobbyDepth / 2 - margin, targetZ));
            targetY = Math.max(0.3, Math.min(lobbyHeight - 0.3, targetY));
        } else {
            const margin = 0.5;
            targetX = Math.max(-arenaSize / 2 + margin, Math.min(arenaSize / 2 - margin, targetX));
            targetZ = Math.max(-arenaSize / 2 + margin, Math.min(arenaSize / 2 - margin, targetZ));
            targetY = Math.max(0.3, Math.min(arenaHeight - 0.3, targetY));
        }
        camera.position.lerp(new THREE.Vector3(targetX, targetY, targetZ), 8 * dt);
        camera.lookAt(playerGroup.position.x, playerGroup.position.y + 1.3, playerGroup.position.z);
    }
}

function animate() {
    requestAnimationFrame(animate);
    const dt = Math.min(clock.getDelta(), .2);
    update(dt);
    renderer.render(scene, camera);
}

// ─── INIT ───────────────────────────────────
function init() {
    resetPlayerPosition();
    playerGroup.position.set(0, .1, -2);
    setWeaponMesh(getEquippedWeapon());
    document.getElementById('shop-overlay').style.display = 'block';
    updateShopUI();
    updateHUD();
    document.getElementById('wave-display').textContent = 'Lobby';
    document.getElementById('crosshair').style.display = 'none';
}

window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});

init();
animate();

// Click handler for initial audio context
document.addEventListener('click', () => {
    initAudio();
    if (!muted && !droneNode && gameState.inArena) startDrone();
}, { once: false });

console.log('Hex & Hollow ready. Enter the dungeon.');
