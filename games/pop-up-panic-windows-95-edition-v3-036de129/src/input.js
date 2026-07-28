// ── DRAGGING ──
function startDrag(popupId, clientX, clientY, element, originalEvent) {
    if (gameOver) return;
    bringToFront(popupId);
    var popup = null;
    for (var i = 0; i < activePopups.length; i++) {
        if (activePopups[i].id === popupId) { popup = activePopups[i]; break; }
    }
    if (!popup) return;
    draggingPopup = popup;
    dragOffsetX = clientX - popup.x;
    dragOffsetY = clientY - popup.y;
    element.classList.add('dragging');
    // Remove dodge transition during drag for boss
    if (popup.isBoss) { element.classList.remove('dodging'); }
    if (originalEvent) originalEvent.preventDefault();
}

function onMouseMove(e) {
    if (!draggingPopup) return;
    var bounds = getDesktopBounds();
    draggingPopup.x = clamp(e.clientX - dragOffsetX, -draggingPopup.width * 0.5, bounds.width - draggingPopup.width * 0.5);
    draggingPopup.y = clamp(e.clientY - dragOffsetY, -draggingPopup.height * 0.4, bounds.height - draggingPopup.height * 0.4);
    draggingPopup.element.style.left = draggingPopup.x + 'px';
    draggingPopup.element.style.top = draggingPopup.y + 'px';
}

function onTouchMove(e) {
    if (!draggingPopup) return;
    var t = e.touches[0];
    var bounds = getDesktopBounds();
    draggingPopup.x = clamp(t.clientX - dragOffsetX, -draggingPopup.width * 0.5, bounds.width - draggingPopup.width * 0.5);
    draggingPopup.y = clamp(t.clientY - dragOffsetY, -draggingPopup.height * 0.4, bounds.height - draggingPopup.height * 0.4);
    draggingPopup.element.style.left = draggingPopup.x + 'px';
    draggingPopup.element.style.top = draggingPopup.y + 'px';
}

function endDrag() {
    if (!draggingPopup) return;
    draggingPopup.element.classList.remove('dragging');
    draggingPopup = null;
}

document.addEventListener('mousemove', onMouseMove);
document.addEventListener('mouseup', endDrag);
document.addEventListener('touchmove', onTouchMove, { passive: false });
document.addEventListener('touchend', endDrag);
document.addEventListener('touchcancel', endDrag);

// ── START MENU ──
function toggleStartMenu() {
    startMenuOpen = !startMenuOpen;
    if (startMenuOpen) {
        startMenu.classList.add('show');
        startBtn.classList.add('active');
    } else {
        startMenu.classList.remove('show');
        startBtn.classList.remove('active');
    }
}
function closeStartMenu() {
    startMenuOpen = false;
    startMenu.classList.remove('show');
    startBtn.classList.remove('active');
}

startBtn.addEventListener('click', function(e) { e.stopPropagation(); toggleStartMenu(); });
startMenu.addEventListener('click', function(e) {
    var item = e.target.closest('.start-menu-item');
    if (!item) return;
    var act = item.getAttribute('data-do');
    if (act === 'restart') { closeStartMenu(); restartGame(); }
    else if (act === 'shutdown') { closeStartMenu(); if (!gameOver) triggerBSOD(); }
    else {
        item.style.background = '#000080';
        item.style.color = '#fff';
        setTimeout(function() { item.style.background = ''; item.style.color = ''; }, 200);
        closeStartMenu();
    }
});
document.addEventListener('click', function(e) {
    if (startMenuOpen && !startMenu.contains(e.target) && e.target !== startBtn) { closeStartMenu(); }
});

// ── DESKTOP ICON CLICKS ──
desktop.addEventListener('click', function(e) {
    var icon = e.target.closest('.desktop-icon');
    if (!icon) return;
    icon.style.background = 'rgba(0,0,128,0.4)';
    icon.style.border = '1px dotted #fff';
    setTimeout(function() { icon.style.background = ''; icon.style.border = '1px solid transparent'; }, 200);
});

// ── TOUCH SCROLL PREVENTION ──
document.addEventListener('touchmove', function(e) { if (draggingPopup) e.preventDefault(); }, { passive: false });
popupContainer.addEventListener('touchend', function(e) {
    if (e.target.getAttribute('data-role') === 'close') e.preventDefault();
});
