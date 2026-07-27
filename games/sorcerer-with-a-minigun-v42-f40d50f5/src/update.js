// update.js - Main update loop, camera, game state management

let camera = { x: 0, y: 0 };

function updateCamera(dt) {
    if (!player) return;
    const targetX = player.x - W / 2, targetY = player.y - GAME_VIEW_H / 2, lerpFactor = 1 - Math.exp(-8 * dt);
    camera.x += (targetX - camera.x) * lerpFactor;
    camera.y += (targetY - camera.y) * lerpFactor;
    camera.x = Math.max(0, Math.min(WORLD_W - W, camera.x));
    camera.y = Math.max(0, Math.min(WORLD_H - GAME_VIEW_H, camera.y));
    if (WORLD_W <= W) camera.x = (WORLD_W - W) / 2;
    if (WORLD_H <= GAME_VIEW_H) camera.y = (WORLD_H - GAME_VIEW_H) / 2;
}

function updateMouseWorld() {
    mouseWorldX = mouseX + camera.x;
    mouseWorldY = mouseY + camera.y;
}

// Apply amulet regen power
function updateAmuletRegen(dt) {
    if (!player || !player.alive || !player.amulet) return;
    const regenVal = getAmuletPower(player, 'regen');
    if (regenVal > 0) {
        player.health = Math.min(player.maxHealth, player.health + regenVal * dt);
    }
}

// Update dropped amulets (lifetime, pickup)
function updateDroppedAmulets(dt) {
    for (let i = droppedAmulets.length - 1; i >= 0; i--) {
        const am = droppedAmulets[i];
        am.life -= dt;
        am.glow += dt * 3;
        if (am.life <= 0) {
            droppedAmulets.splice(i, 1);
            continue;
        }
        // Pickup by player proximity
        if (player && player.alive) {
            const dx = player.x - am.x, dy = player.y - am.y, dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < 36) {
                // Show comparison dialog
                amuletCompareState = {
                    current: player.amulet ? { ...player.amulet } : null,
                    currentIsEquipped: !!player.amulet,
                    newAmulet: { ...am },
                    life: am.life,
                    maxLife: am.maxLife
                };
                gameState = 'AMULET_DIALOG';
                droppedAmulets.splice(i, 1);
                continue;
            }
            // Magnet pull
            if (dist < 120) {
                const spd = 150;
                const angle = Math.atan2(player.y - am.y, player.x - am.x);
                am.x += Math.cos(angle) * spd * dt;
                am.y += Math.sin(angle) * spd * dt;
            }
        }
    }
}

function update(dt) {
    if (gameState === 'CHARACTER_SELECT' || gameState === 'SHOP') { updateMouseWorld(); return; }
    if (gameState === 'AMULET_DIALOG') {
        // Keep updating dropped amulets (other ones still decay)
        updateDroppedAmulets(dt);
        updateMouseWorld();
        screenShake = 0; shakeX = 0; shakeY = 0;
        mouseClicked.left = false; mouseClicked.right = false;
        return;
    }
    if (gameState === 'GAME_OVER') {
        gameOverTimer -= dt;
        if (gameOverTimer <= 0 && mouseClicked.left) restartGame();
        updateMouseWorld();
        updateParticles(dt);
        updateDamageNumbers(dt);
        updateDroppedTokens(dt);
        updateDroppedAmulets(dt);
        updateBoneShards(dt);
        updateBeamEffects(dt);
        return;
    }
    if (gameState === 'WAVE_CLEAR') {
        waveClearTimer -= dt;
        updateParticles(dt);
        updateDamageNumbers(dt);
        updateDroppedTokens(dt);
        updateDroppedAmulets(dt);
        updateMinions(dt);
        updateBoneShards(dt);
        updateBeamEffects(dt);
        if (waveClearTimer <= 0) gameState = 'SHOP';
        updateMouseWorld();
        return;
    }
    if (unpauseCountdown > 0) {
        unpauseCountdown -= dt;
        if (unpauseCountdown <= 0) unpauseCountdown = 0;
        else { updateMouseWorld(); screenShake = 0; shakeX = 0; shakeY = 0; mouseClicked.left = false; mouseClicked.right = false; return; }
    }
    if (paused) {
        updateMouseWorld();
        screenShake = 0; shakeX = 0; shakeY = 0;
        mouseClicked.left = false; mouseClicked.right = false;
        return;
    }
    const dtClamped = Math.min(dt, 0.1);
    updateMouseWorld();
    updatePlayer(dtClamped);
    updateAmuletRegen(dtClamped);
    updateDroppedAmulets(dtClamped);
    updateEnemies(dtClamped);
    updateMinions(dtClamped);
    updateProjectiles(dtClamped);
    updateParticles(dtClamped);
    updateDamageNumbers(dtClamped);
    updateDroppedTokens(dtClamped);
    updateBoneShards(dtClamped);
    updateBeamEffects(dtClamped);

    // Purple block invulnerability timer
    if (purpleBlock && purpleBlock.invulnTimer > 0) {
        purpleBlock.invulnTimer -= dtClamped;
    }
    // Purple block hit flash decay
    if (purpleBlock && purpleBlock.hitFlash > 0) {
        purpleBlock.hitFlash -= dtClamped;
    }

    updateCamera(dtClamped);
    if (screenShake > 0) {
        screenShake = Math.max(0, screenShake - dt * 8);
        shakeX = (Math.random() - 0.5) * screenShake * 2;
        shakeY = (Math.random() - 0.5) * screenShake * 2;
    } else { shakeX = 0; shakeY = 0; }
    if (enemies.length > 0 && enemies.every(e => e.health <= 0)) {
        enemies = [];
        startTokenFlyToPlayer();
        gameState = 'WAVE_CLEAR';
        waveClearTimer = 0.8;
    }
    mouseClicked.left = false;
    mouseClicked.right = false;
}
