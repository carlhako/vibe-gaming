// ── BOSS DODGE LOGIC ──
function getCloseButtonCenter(popupData) {
    const el = popupData.element;
    if (!el) return null;
    const titlebar = el.querySelector('.popup-titlebar');
    const closeBtn = el.querySelector('.close-btn');
    if (!titlebar || !closeBtn) return null;
    const btnRect = closeBtn.getBoundingClientRect();
    return {
        x: btnRect.left + btnRect.width / 2,
        y: btnRect.top + btnRect.height / 2
    };
}

function distanceToCloseButton(clientX, clientY, popupData) {
    const center = getCloseButtonCenter(popupData);
    if (!center) return Infinity;
    const dx = clientX - center.x;
    const dy = clientY - center.y;
    return Math.sqrt(dx * dx + dy * dy);
}

function triggerBossDodge() {
    if (!bossActive || !bossPopupData || !bossPopupData.element || gameOver) return;
    if (bossDodgeCooldown) return;
    if (draggingPopup === bossPopupData) return;

    const bounds = getDesktopBounds();
    const el = bossPopupData.element;
    const currentLeft = parseFloat(el.style.left) || 0;
    const currentTop = parseFloat(el.style.top) || 0;
    const pw = bossPopupData.width;
    const ph = bossPopupData.height;

    // Pick a new position at least 120px away from current
    let newX, newY;
    let attempts = 0;
    do {
        newX = Math.random() * Math.max(0, bounds.width - pw * 0.6);
        newY = Math.random() * Math.max(0, bounds.height - ph * 0.6);
        attempts++;
    } while (
        attempts < 20 &&
        Math.abs(newX - currentLeft) < 100 &&
        Math.abs(newY - currentTop) < 80
    );

    bossPopupData.x = clamp(newX, -pw * 0.5, bounds.width - pw * 0.5);
    bossPopupData.y = clamp(newY, -ph * 0.4, bounds.height - ph * 0.4);

    // Apply dodge animation class
    el.classList.add('dodging');
    el.style.left = bossPopupData.x + 'px';
    el.style.top = bossPopupData.y + 'px';

    // Remove transition class after animation completes
    if (bossDodgeTimer) clearTimeout(bossDodgeTimer);
    bossDodgeTimer = setTimeout(function() {
        if (el) el.classList.remove('dodging');
        bossDodgeTimer = null;
    }, 200);

    // Update dodge state
    bossDodgeCount++;
    bossCanDodge = false;
    bossDodgeCooldown = true;
    bossDetectionRadius = Math.max(28, bossDetectionRadius - 5);

    // Reset cooldown after delay
    setTimeout(function() {
        bossDodgeCooldown = false;
    }, 320);

    updateScoreDisplay();
}

function handleBossMouseMove(e) {
    if (!bossActive || !bossPopupData || gameOver) return;
    if (draggingPopup === bossPopupData) return;
    const dist = distanceToCloseButton(e.clientX, e.clientY, bossPopupData);

    if (dist > bossDetectionRadius + 15) {
        // Cursor left the danger zone — reset can-dodge flag
        bossCanDodge = true;
    }

    if (dist <= bossDetectionRadius && bossCanDodge && !bossDodgeCooldown) {
        triggerBossDodge();
    }
}

function handleBossTouchMove(e) {
    if (!bossActive || !bossPopupData || gameOver) return;
    if (draggingPopup === bossPopupData) return;
    if (!e.touches || e.touches.length === 0) return;
    const t = e.touches[0];
    const dist = distanceToCloseButton(t.clientX, t.clientY, bossPopupData);

    if (dist > bossDetectionRadius + 20) {
        bossCanDodge = true;
    }

    if (dist <= bossDetectionRadius && bossCanDodge && !bossDodgeCooldown) {
        triggerBossDodge();
    }
}

function attachBossListeners(popupData) {
    const el = popupData.element;
    if (!el) return;
    el.addEventListener('mousemove', handleBossMouseMove);
    el.addEventListener('touchmove', handleBossTouchMove, { passive: false });
}

function detachBossListeners(popupData) {
    const el = popupData.element;
    if (!el) return;
    el.removeEventListener('mousemove', handleBossMouseMove);
    el.removeEventListener('touchmove', handleBossTouchMove);
}

function resetBossState() {
    if (bossPopupData && bossPopupData.element) {
        detachBossListeners(bossPopupData);
        bossPopupData.element.classList.remove('dodging');
    }
    bossActive = false;
    bossPopupData = null;
    bossDodgeCooldown = false;
    bossDodgeCount = 0;
    bossDetectionRadius = 75;
    bossCanDodge = true;
    if (bossDodgeTimer) { clearTimeout(bossDodgeTimer);
        bossDodgeTimer = null; }
}
