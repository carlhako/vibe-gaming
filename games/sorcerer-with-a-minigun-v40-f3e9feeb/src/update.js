
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

function update(dt) {
    if (gameState === 'CHARACTER_SELECT' || gameState === 'SHOP') { updateMouseWorld(); return; }
    if (gameState === 'GAME_OVER') {
        gameOverTimer -= dt;
        if (gameOverTimer <= 0 && mouseClicked.left) restartGame();
        updateMouseWorld();
        updateParticles(dt);
        updateDamageNumbers(dt);
        updateDroppedTokens(dt);
        updateBoneShards(dt);
        updateBeamEffects(dt);
        return;
    }
    if (gameState === 'WAVE_CLEAR') {
        waveClearTimer -= dt;
        updateParticles(dt);
        updateDamageNumbers(dt);
        updateDroppedTokens(dt);
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
    updateEnemies(dtClamped);
    updateMinions(dtClamped);
    updateProjectiles(dtClamped);
    updateParticles(dtClamped);
    updateDamageNumbers(dtClamped);
    updateDroppedTokens(dtClamped);
    updateBoneShards(dtClamped);
    updateBeamEffects(dtClamped);
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
