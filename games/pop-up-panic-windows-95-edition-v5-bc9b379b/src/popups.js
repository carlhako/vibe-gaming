// ── POPUP LIFECYCLE ──

function createPopupElement(ad, isBoss) {
    const size = getPopupSize(!!isBoss);
    const bounds = getDesktopBounds();
    const pid = ++popupIdCounter;

    const maxX = Math.max(0, bounds.width - size.width * 0.6);
    const maxY = Math.max(0, bounds.height - size.height * 0.6);
    const x = Math.random() * maxX;
    const y = Math.random() * maxY;

    const wrapper = document.createElement('div');
    wrapper.className = 'popup-window' + (isBoss ? ' boss' : '');
    wrapper.id = 'popup-' + pid;
    wrapper.style.width = size.width + 'px';
    wrapper.style.left = x + 'px';
    wrapper.style.top = y + 'px';
    wrapper.style.zIndex = 100 + pid;
    wrapper.setAttribute('data-popup-id', pid);

    wrapper.innerHTML =
        '<div class="popup-titlebar" data-role="drag">' +
        '<span class="title-icon">' + ad.emoji + '</span>' +
        '<span class="title-text">' + ad.title + '</span>' +
        '<button class="close-btn" data-role="close" title="Close">\u2715</button>' +
        '</div>' +
        '<div class="popup-content">' +
        '<div class="ad-emoji">' + ad.emoji + '</div>' +
        '<div class="ad-title">' + ad.title + '</div>' +
        '<div class="ad-body">' + ad.body + '</div>' +
        '<div class="marquee">' + ad.marquee + '</div>' +
        '<span class="fake-btn">' + (isBoss ? 'NO' : 'OK') + '</span>' +
        '</div>';

    const closeBtn = wrapper.querySelector('.close-btn');
    closeBtn.addEventListener('click', function(e) { e.stopPropagation(); e.preventDefault();
        closePopup(pid); });
    closeBtn.addEventListener('touchend', function(e) { e.stopPropagation(); e.preventDefault();
        closePopup(pid); });

    const titlebar = wrapper.querySelector('.popup-titlebar');
    titlebar.addEventListener('mousedown', function(e) {
        if (e.target.getAttribute('data-role') === 'close') return;
        startDrag(pid, e.clientX, e.clientY, wrapper, e);
    });
    titlebar.addEventListener('touchstart', function(e) {
        if (e.target.getAttribute('data-role') === 'close') return;
        var t = e.touches[0];
        startDrag(pid, t.clientX, t.clientY, wrapper, e);
    }, { passive: false });

    wrapper.addEventListener('mousedown', function(e) {
        if (e.target.getAttribute('data-role') === 'close') return;
        bringToFront(pid);
    });

    popupContainer.appendChild(wrapper);

    var popupData = { id: pid, element: wrapper, x: x, y: y, width: size.width, height: size.height, isBoss: !!isBoss };
    activePopups.push(popupData);

    if (isBoss) {
        bossPopupData = popupData;
        bossActive = true;
        bossDodgeCount = 0;
        bossDetectionRadius = 75;
        bossCanDodge = true;
        bossDodgeCooldown = false;
        attachBossListeners(popupData);
    }

    return popupData;
}

function closePopup(popupId) {
    if (gameOver) return;
    var idx = -1;
    for (var i = 0; i < activePopups.length; i++) { if (activePopups[i].id === popupId) { idx = i; break; } }
    if (idx === -1) return;
    var popup = activePopups[idx];
    var wasBoss = popup.isBoss;

    if (wasBoss) {
        detachBossListeners(popup);
        if (popup.element) popup.element.classList.remove('dodging');
    }

    if (popup.element && popup.element.parentNode) {
        popup.element.style.transition = 'transform 0.12s ease-in, opacity 0.12s ease-in';
        popup.element.style.transform = 'scale(0.5)';
        popup.element.style.opacity = '0';
        (function(el) {
            setTimeout(function() { if (el && el.parentNode) el.parentNode.removeChild(el); }, 120);
        })(popup.element);
    }
    activePopups.splice(idx, 1);
    totalClosed++;
    updateScoreDisplay();

    if (wasBoss) {
        bossActive = false;
        bossPopupData = null;
        if (bossDodgeTimer) { clearTimeout(bossDodgeTimer);
            bossDodgeTimer = null; }
        bossDodgeCooldown = false;
        bossDodgeCount = 0;
        bossDetectionRadius = 75;
        bossCanDodge = true;
        nextBossAt = totalClosed + 15 + Math.floor(Math.random() * 11);
        updateScoreDisplay();
        // Spawn two quick popups as a "reward" for beating the boss
        setTimeout(function() { if (!gameOver && activePopups.length < MAX_POPUPS) spawnPopup(); }, 200);
        setTimeout(function() { if (!gameOver && activePopups.length < MAX_POPUPS) spawnPopup(); }, 450);
    }

    if (activePopups.length < MAX_POPUPS && gameOver === false && !spawnTimerId) {
        scheduleNextSpawn();
    }
}

function bringToFront(popupId) {
    var popup = null;
    for (var i = 0; i < activePopups.length; i++) { if (activePopups[i].id === popupId) { popup = activePopups[i]; break; } }
    if (!popup || !popup.element) return;
    var maxZ = 0;
    for (var j = 0; j < activePopups.length; j++) { var z = parseInt(activePopups[j].element.style.zIndex) || 0; if (z > maxZ) maxZ = z; }
    popup.element.style.zIndex = maxZ + 1;
}

function spawnPopup() {
    if (gameOver) return;

    // Check if it's boss time — guaranteed at threshold, or 10% random chance
    if (!bossActive && (totalClosed >= nextBossAt || Math.random() < 0.10)) {
        createPopupElement(getRandomBossAd(), true);
        totalSpawned++;
        currentSpawnDelay = getSpawnDelay();
        updateScoreDisplay();
        if (activePopups.length >= MAX_POPUPS) { triggerBSOD(); return; }
        scheduleNextSpawn();
        return;
    }

    createPopupElement(getRandomAd(), false);
    totalSpawned++;
    currentSpawnDelay = getSpawnDelay();
    updateScoreDisplay();
    if (activePopups.length >= MAX_POPUPS) { triggerBSOD(); return; }
    scheduleNextSpawn();
}

function scheduleNextSpawn() {
    if (spawnTimerId) { clearTimeout(spawnTimerId);
        spawnTimerId = null; }
    if (gameOver) return;
    if (activePopups.length >= MAX_POPUPS) return;
    spawnTimerId = setTimeout(function() { spawnTimerId = null;
        spawnPopup(); }, currentSpawnDelay);
}
