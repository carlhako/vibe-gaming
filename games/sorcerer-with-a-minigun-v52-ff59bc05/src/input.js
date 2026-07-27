const keys = {};
let mouseX = W / 2, mouseY = H / 2, mouseWorldX = 0, mouseWorldY = 0;
let mouseDown = { left: false, right: false };
let mouseClicked = { left: false, right: false };

canvas.addEventListener('mousemove', (e) => { ensureFocus(e); const rect = canvas.getBoundingClientRect(), scaleX = W / rect.width, scaleY = H / rect.height; mouseX = (e.clientX - rect.left) * scaleX; mouseY = (e.clientY - rect.top) * scaleY; });
canvas.addEventListener('mousedown', (e) => { e.preventDefault(); ensureFocus(e); if (e.button === 0) { mouseDown.left = true; mouseClicked.left = true; } if (e.button === 2) { mouseDown.right = true; mouseClicked.right = true; } });
canvas.addEventListener('mouseup', (e) => { if (e.button === 0) mouseDown.left = false; if (e.button === 2) mouseDown.right = false; });
canvas.addEventListener('contextmenu', (e) => e.preventDefault());
window.addEventListener('keydown', (e) => { keys[e.key.toLowerCase()] = true; if ((e.key === ' ' || e.key === 'Enter') && gameState === 'WAVE_CLEAR') { gameState = 'SHOP'; waveClearTimer = 0; } if (e.key === ' ' || e.key === 'ArrowUp' || e.key === 'ArrowDown' || e.key === 'ArrowLeft' || e.key === 'ArrowRight') e.preventDefault(); if ((e.key === 'p' || e.key === 'P') && gameState === 'PLAYING' && !amuletPickupState) { e.preventDefault(); if (unpauseCountdown > 0) { unpauseCountdown = 0; paused = true; } else if (paused) { unpauseCountdown = 3.0; paused = false; } else paused = true; } });
window.addEventListener('keyup', (e) => { keys[e.key.toLowerCase()] = false; });
window.addEventListener('blur', () => { for (const k in keys) keys[k] = false; mouseDown.left = false; mouseDown.right = false; });
document.addEventListener('visibilitychange', () => { if (document.hidden) { for (const k in keys) keys[k] = false; mouseDown.left = false; mouseDown.right = false; } });
canvas.addEventListener('touchmove', (e) => { e.preventDefault(); ensureFocus(e); const rect = canvas.getBoundingClientRect(), scaleX = W / rect.width, scaleY = H / rect.height; mouseX = (e.touches[0].clientX - rect.left) * scaleX; mouseY = (e.touches[0].clientY - rect.top) * scaleY; });
canvas.addEventListener('touchstart', (e) => { e.preventDefault(); ensureFocus(e); const rect = canvas.getBoundingClientRect(), scaleX = W / rect.width, scaleY = H / rect.height; mouseX = (e.touches[0].clientX - rect.left) * scaleX; mouseY = (e.touches[0].clientY - rect.top) * scaleY; mouseDown.left = true; mouseClicked.left = true; });
canvas.addEventListener('touchend', (e) => { e.preventDefault(); mouseDown.left = false; });

canvas.addEventListener('click', (e) => { ensureFocus(e); const rect = canvas.getBoundingClientRect(), scaleX = W / rect.width, scaleY = H / rect.height, cx = (e.clientX - rect.left) * scaleX, cy = (e.clientY - rect.top) * scaleY;

  // Amulet pickup dialog
  if (gameState === 'PLAYING' && amuletPickupState && window._amuletDialogBtns) {
    for (const btn of window._amuletDialogBtns) {
      if (cx >= btn.x && cx <= btn.x + btn.w && cy >= btn.y && cy <= btn.y + btn.h) {
        if (btn.action === 'equip') {
          equipAmulet(amuletPickupState.newAmulet);
        } else if (btn.action === 'discard') {
          discardAmuletPickup();
        }
        return;
      }
    }
    return;
  }

  // Test button: launch sandbox mode (shop with 10k tokens + wave selector)
  if (gameState === 'CHARACTER_SELECT' && window._testButton) {
    if (cx >= window._testButton.x && cx <= window._testButton.x + window._testButton.w && cy >= window._testButton.y && cy <= window._testButton.y + window._testButton.h) {
      selectedCharacter = 'sorcerer';
      startSandboxGame();
      return;
    }
  }

  if (gameState === 'CHARACTER_SELECT' && window._selectButtons) { for (const btn of window._selectButtons) { if (cx >= btn.x && cx <= btn.x + btn.w && cy >= btn.y && cy <= btn.y + btn.h) { if (btn.key === 'start') startGame(); else if (characters[btn.key] && characters[btn.key].available !== false) selectedCharacter = btn.key; return; } } }
  if (gameState === 'SHOP' && window._shopButtons) { for (const btn of window._shopButtons) { if (cx >= btn.x && cx <= btn.x + btn.w && cy >= btn.y && cy <= btn.y + btn.h) { if (btn.id === 'wave_down') { if (sandboxMode && wave > 1) { wave--; generateMap(); player.x = Math.floor(MAP_COLS / 2) * TILE + TILE / 2; player.y = Math.floor(MAP_ROWS / 2) * TILE + TILE / 2; player.maxHealth = BASE_MAX_HP + getVitalityBonus(); player.health = player.maxHealth; } return; } if (btn.id === 'wave_up') { if (sandboxMode && wave < 50) { wave++; generateMap(); player.x = Math.floor(MAP_COLS / 2) * TILE + TILE / 2; player.y = Math.floor(MAP_ROWS / 2) * TILE + TILE / 2; player.maxHealth = BASE_MAX_HP + getVitalityBonus(); player.health = player.maxHealth; } return; } if (btn.id === 'next_wave') { if (sandboxMode) { sandboxMode = false; } else { wave++; } generateMap(); player.x = Math.floor(MAP_COLS / 2) * TILE + TILE / 2; player.y = Math.floor(MAP_ROWS / 2) * TILE + TILE / 2; player.maxHealth = BASE_MAX_HP + getVitalityBonus(); player.health = player.maxHealth; player.alive = true; player.invulnTimer = 1.0; player.primaryCooldown = 0; player.secondaryCooldown = 0; player.dashCooldown = 0; player.railgunCharge = 0; player.railgunCharging = false; player.isDashing = false; player.dashTimer = 0; player.knockbackX = 0; player.knockbackY = 0; minions = []; boneShards = []; beamEffects = []; paused = false; unpauseCountdown = 0; spawnWave(); gameState = 'PLAYING'; camera.x = player.x - W / 2; camera.y = player.y - GAME_VIEW_H / 2; canvas.focus({ preventScroll: true }); } else if (canBuyUpgrade(btn.id)) { const cost = getUpgradeCost(btn.id); tokens -= cost; playerUpgrades[btn.id] = (playerUpgrades[btn.id] || 0) + 1; if (btn.id === 'minigun') { if (!hasRailgun() || player.activeWeapon !== 'railgun') player.activeWeapon = 'minigun'; player.primaryCooldown = 0; } if (btn.id === 'railgun') { player.activeWeapon = 'railgun'; player.primaryCooldown = 0; player.railgunCharge = 0; player.railgunCharging = false; } if (btn.id === 'vit') { const oldMax = player.maxHealth; player.maxHealth = BASE_MAX_HP + getVitalityBonus(); player.health = Math.min(player.maxHealth, player.health + (player.maxHealth - oldMax) + 5); } else { if (player) player.health = Math.min(player.maxHealth, player.health + 10); } } return; } } }
  if (gameState === 'PLAYING' && player && player.alive && selectedCharacter === 'sorcerer' && window._bottomWeaponBtns) { for (const btn of window._bottomWeaponBtns) { if (cx >= btn.x && cx <= btn.x + btn.w && cy >= btn.y && cy <= btn.y + btn.h) { performWeaponSwap(btn.type); return; } } }
  if (gameState === 'GAME_OVER' && gameOverTimer <= 0) restartGame();
});
