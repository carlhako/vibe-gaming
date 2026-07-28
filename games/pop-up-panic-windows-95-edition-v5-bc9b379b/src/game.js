// ── GAME STATE ──
var activePopups = [];
var totalClosed = 0;
var totalSpawned = 0;
var gameOver = false;
var spawnTimerId = null;
var popupIdCounter = 0;
var MAX_POPUPS = 20;
var currentSpawnDelay = 1800;
var draggingPopup = null;
var dragOffsetX = 0;
var dragOffsetY = 0;
var startMenuOpen = false;

// ── BOSS STATE ──
var bossActive = false;
var nextBossAt = 15 + Math.floor(Math.random() * 11); // 15-25 closed popups until first boss
var bossDodgeCooldown = false;
var bossDodgeCount = 0;
var bossDetectionRadius = 75; // px from X button center
var bossCanDodge = true; // requires cursor to leave radius then re-enter
var bossPopupData = null;
var bossDodgeTimer = null;

// ── BSOD ──
function triggerBSOD() {
    gameOver = true;
    if (spawnTimerId) { clearTimeout(spawnTimerId);
        spawnTimerId = null; }
    for (var i = 0; i < activePopups.length; i++) {
        if (activePopups[i].element) {
            activePopups[i].element.style.animation = 'none';
            activePopups[i].element.style.transition = 'none';
            activePopups[i].element.classList.remove('dodging');
        }
    }
    if (bossPopupData && bossPopupData.element) {
        detachBossListeners(bossPopupData);
    }
    bsodScore.textContent = 'You closed ' + totalClosed + ' pop-up' + (totalClosed !== 1 ? 's' : '') +
        ' before the system crashed. ' + activePopups.length + ' remained active.';
    bsod.classList.add('show');
    bsod.style.display = 'flex';
    bsod.setAttribute('tabindex', '0');
    bsod.focus();
    updateScoreDisplay();
}

function restartGame() {
    for (var i = 0; i < activePopups.length; i++) {
        if (activePopups[i].element && activePopups[i].element.parentNode) {
            detachBossListeners(activePopups[i]);
            activePopups[i].element.parentNode.removeChild(activePopups[i].element);
        }
    }
    activePopups = [];
    totalClosed = 0;
    totalSpawned = 0;
    gameOver = false;
    popupIdCounter = 0;
    currentSpawnDelay = 1800;
    draggingPopup = null;
    dragOffsetX = 0;
    dragOffsetY = 0;
    resetBossState();
    nextBossAt = 15 + Math.floor(Math.random() * 11);
    bsod.classList.remove('show');
    bsod.style.display = 'none';
    bsod.removeAttribute('tabindex');
    updateMaxPopups();
    updateScoreDisplay();
    updateClock();
    spawnPopup();
    setTimeout(function() { if (!gameOver) spawnPopup(); }, 400);
    setTimeout(function() { if (!gameOver) spawnPopup(); }, 800);
}

// ── BSOD RESTART ──
bsod.addEventListener('click', function(e) { if (gameOver) restartGame(); });
bsod.addEventListener('touchend', function(e) { if (gameOver) { e.preventDefault();
        restartGame(); } });
document.addEventListener('keydown', function(e) {
    if (gameOver) { e.preventDefault();
        restartGame(); return; }
});

// ── RESIZE ──
var resizeDebounce = null;
window.addEventListener('resize', function() {
    if (resizeDebounce) clearTimeout(resizeDebounce);
    resizeDebounce = setTimeout(function() {
        var bounds = getDesktopBounds();
        updateMaxPopups();
        updateScoreDisplay();
        for (var i = 0; i < activePopups.length; i++) {
            var p = activePopups[i];
            if (!p.element) continue;
            p.x = clamp(p.x, -p.width * 0.5, bounds.width - p.width * 0.5);
            p.y = clamp(p.y, -p.height * 0.4, bounds.height - p.height * 0.4);
            p.element.style.left = p.x + 'px';
            p.element.style.top = p.y + 'px';
        }
    }, 200);
});

// ── INIT ──
updateMaxPopups();
updateScoreDisplay();
updateClock();
setInterval(updateClock, 30000);
setTimeout(function() { if (!gameOver) spawnPopup(); }, 300);
setTimeout(function() { if (!gameOver) spawnPopup(); }, 700);
setTimeout(function() { if (!gameOver) spawnPopup(); }, 1200);
