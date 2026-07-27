// render.js - all rendering: world, tokens, projectiles, enemies, player, UI, HUD, amulets

function toScreenX(wx) { return wx - camera.x; }
function toScreenY(wy) { return wy - camera.y; }

function render() {
    ctx.clearRect(0, 0, W, H);
    if (gameState === 'CHARACTER_SELECT') { renderCharacterSelect(); return; }
    ctx.save();
    if (gameState === 'PLAYING') { ctx.beginPath(); ctx.rect(0, 0, W, GAME_VIEW_H); ctx.clip(); }
    const sx = shakeX, sy = shakeY; ctx.translate(sx, sy);
    const startTX = Math.floor(camera.x / TILE), startTY = Math.floor(camera.y / TILE), endTX = Math.ceil((camera.x + W) / TILE), endTY = Math.ceil((camera.y + GAME_VIEW_H) / TILE);
    for (let ty = startTY; ty <= endTY; ty++) for (let tx = startTX; tx <= endTX; tx++) { if (tx < 0 || tx >= MAP_COLS || ty < 0 || ty >= MAP_ROWS) { ctx.fillStyle = '#0a0a10'; ctx.fillRect(tx * TILE - camera.x, ty * TILE - camera.y, TILE, TILE); continue; } const shade = ((tx + ty) % 2 === 0) ? '#1a1a24' : '#161620'; ctx.fillStyle = shade; const sx2 = tx * TILE - camera.x, sy2 = ty * TILE - camera.y; ctx.fillRect(sx2, sy2, TILE, TILE); ctx.strokeStyle = '#1f1f2c'; ctx.lineWidth = 0.5; ctx.strokeRect(sx2, sy2, TILE, TILE); if (mapTiles[ty][tx] >= 1) drawWall(sx2, sy2, mapTiles[ty][tx]); }
    for (const beam of beamEffects) { const alpha = Math.min(1, beam.life / beam.maxLife), bsx1 = toScreenX(beam.x1), bsy1 = toScreenY(beam.y1), bsx2 = toScreenX(beam.x2), bsy2 = toScreenY(beam.y2); if (beam.color.startsWith('#')) { const r = parseInt(beam.color.slice(1, 3), 16), g = parseInt(beam.color.slice(3, 5), 16), b = parseInt(beam.color.slice(5, 7), 16); ctx.strokeStyle = 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')'; } else ctx.strokeStyle = beam.color.replace(')', ', ' + alpha + ')').replace('rgb', 'rgba'); ctx.lineWidth = beam.thickness * alpha; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(bsx1, bsy1); ctx.lineTo(bsx2, bsy2); ctx.stroke(); }
    for (const token of droppedTokens) { if (token.collected) continue; const tsx = toScreenX(token.x), tsy = toScreenY(token.y), pulse = 1 + Math.sin(Date.now() / 300) * 0.2; ctx.fillStyle = '#ffcc00'; ctx.beginPath(); ctx.arc(tsx, tsy, 5 * pulse, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = '#fff8c0'; ctx.beginPath(); ctx.arc(tsx, tsy, 2.5 * pulse, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = 'rgba(255,200,40,0.25)'; ctx.beginPath(); ctx.arc(tsx, tsy, 9 * pulse, 0, Math.PI * 2); ctx.fill(); }
    for (const amulet of droppedAmulets) { if (amulet.collected) continue; const asx = toScreenX(amulet.x), asy = toScreenY(amulet.y), pulse = 1 + Math.sin(amulet.life * 4) * 0.15; const amType = AMULET_TYPES.find(t => t.id === amulet.type); const aColor = amType ? amType.glowColor : '#cc88ff'; ctx.fillStyle = 'rgba(0,0,0,0.4)'; ctx.beginPath(); ctx.ellipse(asx, asy + 6, 8, 3, 0, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = aColor; ctx.globalAlpha = 0.3; ctx.beginPath(); ctx.arc(asx, asy, 12 * pulse, 0, Math.PI * 2); ctx.fill(); ctx.globalAlpha = 1; ctx.fillStyle = '#1a1028'; ctx.strokeStyle = aColor; ctx.lineWidth = 2; ctx.fillRect(asx - 8, asy - 8, 16, 16); ctx.strokeRect(asx - 8, asy - 8, 16, 16); ctx.fillStyle = aColor; ctx.font = '13px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText(amType ? amType.icon : '\u2726', asx, asy + 4); }
    // Purple crystal block
    if (purpleBlock && purpleBlock.health > 0) drawPurpleBlock();
    for (const proj of projectiles) { if (!proj.alive) continue; for (let i = 0; i < proj.trail.length; i++) { const t = proj.trail[i], alpha = (i / proj.trail.length) * 0.5, tsx2 = toScreenX(t.x), tsy2 = toScreenY(t.y); if (proj.color.startsWith('#')) { const r = parseInt(proj.color.slice(1, 3), 16), g = parseInt(proj.color.slice(3, 5), 16), bval = parseInt(proj.color.slice(5, 7), 16); ctx.fillStyle = 'rgba(' + r + ',' + g + ',' + bval + ',' + alpha + ')'; } else ctx.fillStyle = proj.color.replace(')', ', ' + alpha + ')').replace('rgb', 'rgba'); ctx.beginPath(); ctx.arc(tsx2, tsy2, proj.size * 0.7, 0, Math.PI * 2); ctx.fill(); } const psx = toScreenX(proj.x), psy = toScreenY(proj.y); ctx.fillStyle = proj.color; ctx.beginPath(); ctx.arc(psx, psy, proj.size, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = '#ffffff'; ctx.beginPath(); ctx.arc(psx, psy, proj.size * 0.45, 0, Math.PI * 2); ctx.fill(); }
    for (const minion of minions) { if (minion.health <= 0) continue; const msx = toScreenX(minion.x), msy = toScreenY(minion.y); if (msx < -30 || msx > W + 30 || msy < -30 || msy > H + 30) continue; drawMinion(minion, msx, msy); }
    for (const enemy of enemies) { if (enemy.health <= 0) continue; const esx = toScreenX(enemy.x), esy = toScreenY(enemy.y); if (esx < -30 || esx > W + 30 || esy < -30 || esy > H + 30) continue; drawEnemy(enemy, esx, esy); }
    if (player && player.alive) { const psx = toScreenX(player.x), psy = toScreenY(player.y); drawPlayer(psx, psy); }
    for (const shard of boneShards) { const ssx = toScreenX(shard.x), ssy = toScreenY(shard.y), alpha = Math.min(1, shard.life / shard.maxLife); ctx.fillStyle = 'rgba(220,210,180,' + alpha + ')'; ctx.save(); ctx.translate(ssx, ssy); ctx.rotate(Math.atan2(shard.vy, shard.vx)); ctx.fillRect(-4, -1.5, 8, 3); ctx.fillStyle = 'rgba(255,245,225,' + (alpha * 0.7) + ')'; ctx.fillRect(-2, -1, 4, 2); ctx.restore(); }
    for (const dn of damageNumbers) { const dnx = toScreenX(dn.x), dny = toScreenY(dn.y), alpha = Math.min(1, dn.life / dn.maxLife); ctx.fillStyle = dn.color; ctx.globalAlpha = alpha; ctx.font = 'bold 13px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText(dn.text, dnx, dny); ctx.globalAlpha = 1; }
    for (const p of particles) { const psx2 = toScreenX(p.x), psy2 = toScreenY(p.y), alpha = Math.min(1, p.life / p.maxLife); ctx.fillStyle = p.color; ctx.globalAlpha = alpha; ctx.fillRect(psx2 - p.size / 2, psy2 - p.size / 2, p.size, p.size); }
    // Shockwaves
    for (const sw of shockwaves) {
        if (!sw.alive) continue;
        const swsx = toScreenX(sw.x), swsy = toScreenY(sw.y);
        const alpha = 1 - (sw.radius / sw.maxRadius);
        // Outer ring
        ctx.strokeStyle = 'rgba(200,140,60,' + (alpha * 0.8) + ')';
        ctx.lineWidth = 4 * alpha;
        ctx.beginPath(); ctx.arc(swsx, swsy, sw.radius, 0, Math.PI * 2); ctx.stroke();
        // Inner glow ring
        ctx.strokeStyle = 'rgba(255,200,100,' + (alpha * 0.5) + ')';
        ctx.lineWidth = 2 * alpha;
        ctx.beginPath(); ctx.arc(swsx, swsy, sw.radius - 6, 0, Math.PI * 2); ctx.stroke();
        // Ground debris particles along the ring
        const ringStep = Math.max(4, Math.floor(sw.radius / 10));
        for (let i = 0; i < ringStep; i++) {
            const a = (i / ringStep) * Math.PI * 2;
            const rx = sw.x + Math.cos(a) * sw.radius;
            const ry = sw.y + Math.sin(a) * sw.radius;
            if (!isWallCircle(rx, ry, 3)) {
                const rxs = toScreenX(rx), rys = toScreenY(ry);
                ctx.fillStyle = 'rgba(180,140,100,' + (alpha * 0.6) + ')';
                ctx.fillRect(rxs - 1.5, rys - 1.5, 3, 3);
            }
        }
    }
    ctx.globalAlpha = 1; ctx.restore();
    if (gameState === 'PLAYING') renderBottomBar();
    if (amuletPickupState) { renderAmuletDialog(); }
    if (gameState === 'PLAYING' && (paused || unpauseCountdown > 0) && !amuletPickupState) renderPauseOverlay();
    if (gameState === 'WAVE_CLEAR') renderWaveClear();
    if (gameState === 'SHOP') renderShop();
    if (gameState === 'GAME_OVER') renderGameOver();
}

function renderBottomBar() {
    if (!player) return; const barY = GAME_VIEW_H, barH = HUD_BAR_H;
    ctx.fillStyle = '#0a0a14'; ctx.fillRect(0, barY, W, barH); ctx.strokeStyle = '#3a2a5a'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(0, barY); ctx.lineTo(W, barY); ctx.stroke(); ctx.strokeStyle = 'rgba(100,40,160,0.25)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, barY + 2); ctx.lineTo(W, barY + 2); ctx.stroke();
    const row1Y = barY + 8, hpBarX = 15, hpBarW = 300, hpBarH = 16;
    ctx.fillStyle = '#1a0a0a'; ctx.fillRect(hpBarX - 1, row1Y - 1, hpBarW + 2, hpBarH + 2); ctx.fillStyle = '#3a1010'; ctx.fillRect(hpBarX, row1Y, hpBarW, hpBarH);
    const hpRatio = player.health / player.maxHealth, hpColor = hpRatio > 0.5 ? '#cc3333' : hpRatio > 0.25 ? '#dd6622' : '#ee1111'; ctx.fillStyle = hpColor; ctx.fillRect(hpBarX, row1Y, hpBarW * hpRatio, hpBarH);
    ctx.fillStyle = '#ffffff'; ctx.font = 'bold 11px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('HP: ' + Math.ceil(player.health) + '/' + player.maxHealth, hpBarX + hpBarW / 2, row1Y + 12);
    const statX = hpBarX + hpBarW + 18; ctx.fillStyle = '#c9a23b'; ctx.font = 'bold 12px "Courier New", monospace'; ctx.textAlign = 'left'; ctx.fillText('WAVE ' + wave, statX, row1Y + 8); ctx.fillText('\u2726 ' + tokens, statX, row1Y + 22);
    if (selectedCharacter === 'sorcerer') { const aliveMinions = minions.filter(m => m.health > 0).length, minigunCount = minions.filter(m => m.health > 0 && m.minionType === 'minigun').length, railgunCount = minions.filter(m => m.health > 0 && m.minionType === 'railgun').length; let minionText = '\u2620 ' + aliveMinions; if (hasMinionRailgun() && railgunCount > 0) { minionText += ' (' + railgunCount + '\u26a1)'; if (hasMinionMinigun() && minigunCount > 0) minionText += ' (' + minigunCount + '\ud83d\udd2b)'; } else if (hasMinionMinigun() && minigunCount > 0) minionText += ' (' + minigunCount + '\ud83d\udd2b)'; ctx.fillStyle = hasMinionRailgun() ? '#6699ff' : (hasMinionMinigun() ? '#ff8844' : '#8899bb'); ctx.fillText(minionText, statX + 90, row1Y + 22); }
    ctx.fillStyle = '#999'; ctx.fillText('Enemies: ' + enemies.filter(e => e.health > 0).length, statX, row1Y + 36);
    // Amulet icon box
    const amIconS = 28, amIconX = W - 250, amIconY = barY + barH - amIconS - 6;
    ctx.fillStyle = '#0d0d1a'; ctx.fillRect(amIconX - 1, amIconY - 1, amIconS + 2, amIconS + 2);
    if (currentAmulet) {
        const aType = AMULET_TYPES.find(t => t.id === currentAmulet.type);
        ctx.strokeStyle = aType ? aType.glowColor : '#aa55cc'; ctx.lineWidth = 2;
        ctx.strokeRect(amIconX - 1, amIconY - 1, amIconS + 2, amIconS + 2);
        ctx.fillStyle = '#151025';
        ctx.fillRect(amIconX, amIconY, amIconS, amIconS);
        ctx.fillStyle = aType ? aType.glowColor : '#cc88ff';
        ctx.font = 'bold 16px "Courier New", monospace';
        ctx.textAlign = 'center';
        ctx.fillText(aType ? aType.icon : '\u2726', amIconX + amIconS / 2, amIconY + amIconS / 2 + 5);
        ctx.fillStyle = aType ? aType.glowColor : '#cc88ff';
        ctx.font = '8px "Courier New", monospace';
        ctx.fillText('AMULET', amIconX + amIconS / 2, amIconY - 4);
    } else {
        ctx.strokeStyle = '#332244'; ctx.lineWidth = 1;
        ctx.strokeRect(amIconX - 1, amIconY - 1, amIconS + 2, amIconS + 2);
        ctx.fillStyle = '#0a0a14';
        ctx.fillRect(amIconX, amIconY, amIconS, amIconS);
        ctx.fillStyle = '#333';
        ctx.font = '14px "Courier New", monospace';
        ctx.textAlign = 'center';
        ctx.fillText('\u25c7', amIconX + amIconS / 2, amIconY + amIconS / 2 + 5);
        ctx.fillStyle = '#444';
        ctx.font = '8px "Courier New", monospace';
        ctx.fillText('AMULET', amIconX + amIconS / 2, amIconY - 4);
    }
    const cdBarX = W - 310, cdBarW = 95, cdBarH = 10;
    let primCdMax; if (hasRailgun() && player.activeWeapon === 'railgun') primCdMax = RAILGUN_CHARGE_TIME; else if (hasMinigun() && player.activeWeapon === 'minigun') primCdMax = 0.1; else primCdMax = getPrimaryCooldown();
    if (hasAnnihilator() && player.activeWeapon === 'annihilator') { const heatRatio = player.annihilatorHeat; ctx.fillStyle = '#100a0a'; ctx.fillRect(cdBarX, row1Y, cdBarW, cdBarH); ctx.fillStyle = player.annihilatorOverheated ? '#ff2222' : (heatRatio > 0.6 ? '#ff6622' : (heatRatio > 0.3 ? '#ff8833' : '#442222')); ctx.fillRect(cdBarX, row1Y, cdBarW * heatRatio, cdBarH); ctx.fillStyle = player.annihilatorOverheated ? '#ff4444' : '#ff8844'; ctx.font = '8px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText(player.annihilatorOverheated ? 'OVERHEATED!' : 'HEAT ' + Math.round(heatRatio * 100) + '%', cdBarX + cdBarW / 2, row1Y + 8); }
    else if (hasRailgun() && player.activeWeapon === 'railgun' && player.railgunCharging) { const chargeRatio = player.railgunCharge; ctx.fillStyle = '#0a1020'; ctx.fillRect(cdBarX, row1Y, cdBarW, cdBarH); ctx.fillStyle = '#2266aa'; ctx.fillRect(cdBarX, row1Y, cdBarW * chargeRatio, cdBarH); ctx.fillStyle = '#88ddff'; ctx.font = '8px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('CHARGING ' + Math.round(chargeRatio * 100) + '%', cdBarX + cdBarW / 2, row1Y + 8); }
    else { const primCdRatio = player.primaryCooldown > 0 ? 1 - (player.primaryCooldown / primCdMax) : 1; ctx.fillStyle = '#1a1a1a'; ctx.fillRect(cdBarX, row1Y, cdBarW, cdBarH); ctx.fillStyle = player.primaryCooldown > 0 ? '#553322' : '#88aa44'; ctx.fillRect(cdBarX, row1Y, cdBarW * Math.max(0, primCdRatio), cdBarH); ctx.fillStyle = '#ccc'; ctx.font = '8px "Courier New", monospace'; ctx.textAlign = 'center'; let primLabel = 'PRIMARY'; if (hasRailgun() && player.activeWeapon === 'railgun') primLabel = 'RAILGUN'; else if (hasMinigun() && player.activeWeapon === 'minigun') primLabel = 'MINIGUN'; ctx.fillText(primLabel, cdBarX + cdBarW / 2, row1Y + 8); }
    const cdBarX2 = cdBarX + cdBarW + 8; ctx.fillStyle = '#1a1a1a'; ctx.fillRect(cdBarX2, row1Y, cdBarW, cdBarH); const secCdRatio = player.secondaryCooldown > 0 ? 1 - (player.secondaryCooldown / getSecondaryCooldown()) : 1; ctx.fillStyle = player.secondaryCooldown > 0 ? '#553322' : '#aa6644'; ctx.fillRect(cdBarX2, row1Y, cdBarW * Math.max(0, secCdRatio), cdBarH); ctx.fillStyle = '#ccc'; ctx.font = '8px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('SECONDARY', cdBarX2 + cdBarW / 2, row1Y + 8);
    const cdBarX3 = cdBarX2 + cdBarW + 8; ctx.fillStyle = '#1a1a1a'; ctx.fillRect(cdBarX3, row1Y, cdBarW, cdBarH); const dashCdRatio = player.dashCooldown > 0 ? 1 - (player.dashCooldown / DASH_COOLDOWN) : 1; ctx.fillStyle = player.dashCooldown > 0 ? '#334466' : '#5588cc'; ctx.fillRect(cdBarX3, row1Y, cdBarW * Math.max(0, dashCdRatio), cdBarH); ctx.fillStyle = '#ccc'; ctx.font = '8px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('DASH [SPACE]', cdBarX3 + cdBarW / 2, row1Y + 8);
    const row2Y = barY + 38;
    if (selectedCharacter === 'sorcerer' && (hasMinigun() || hasRailgun() || hasAnnihilator())) { const btnW = 130, btnH = 22, gap = 12, btnStartX = hpBarX; let btnOffsetX = btnStartX; if (hasMinigun()) { const isActive = player.activeWeapon === 'minigun'; ctx.fillStyle = isActive ? '#2a1808' : '#140c04'; ctx.fillRect(btnOffsetX, row2Y, btnW, btnH); ctx.strokeStyle = isActive ? '#ff8833' : '#553311'; ctx.lineWidth = isActive ? 3 : 1.5; ctx.strokeRect(btnOffsetX, row2Y, btnW, btnH); if (isActive) { ctx.fillStyle = 'rgba(255,136,51,0.12)'; ctx.fillRect(btnOffsetX - 1, row2Y - 1, btnW + 2, btnH + 2); } ctx.fillStyle = isActive ? '#ffcc88' : '#885533'; ctx.font = 'bold 11px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('[1]', btnOffsetX + 18, row2Y + 15); ctx.fillStyle = isActive ? '#ffaa55' : '#664422'; ctx.fillText('\ud83d\udd2b MINIGUN', btnOffsetX + btnW / 2 + 8, row2Y + 15); if (!window._bottomWeaponBtns) window._bottomWeaponBtns = []; window._bottomWeaponBtns.push({ x: btnOffsetX, y: row2Y, w: btnW, h: btnH, type: 'minigun' }); btnOffsetX += btnW + gap; } if (hasRailgun()) { const isActive = player.activeWeapon === 'railgun'; ctx.fillStyle = isActive ? '#0a1a28' : '#060c14'; ctx.fillRect(btnOffsetX, row2Y, btnW, btnH); ctx.strokeStyle = isActive ? '#44aaff' : '#223344'; ctx.lineWidth = isActive ? 3 : 1.5; ctx.strokeRect(btnOffsetX, row2Y, btnW, btnH); if (isActive) { ctx.fillStyle = 'rgba(68,170,255,0.12)'; ctx.fillRect(btnOffsetX - 1, row2Y - 1, btnW + 2, btnH + 2); } ctx.fillStyle = isActive ? '#aaddff' : '#334466'; ctx.font = 'bold 11px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('[2]', btnOffsetX + 18, row2Y + 15); ctx.fillStyle = isActive ? '#88ccff' : '#334455'; ctx.fillText('\u26a1 RAILGUN', btnOffsetX + btnW / 2 + 8, row2Y + 15); if (!window._bottomWeaponBtns) window._bottomWeaponBtns = []; window._bottomWeaponBtns.push({ x: btnOffsetX, y: row2Y, w: btnW, h: btnH, type: 'railgun' }); btnOffsetX += btnW + gap; } if (hasAnnihilator()) { const isActive = player.activeWeapon === 'annihilator'; ctx.fillStyle = isActive ? '#2a0810' : '#14040a'; ctx.fillRect(btnOffsetX, row2Y, btnW, btnH); ctx.strokeStyle = isActive ? '#ff4444' : '#552222'; ctx.lineWidth = isActive ? 3 : 1.5; ctx.strokeRect(btnOffsetX, row2Y, btnW, btnH); if (isActive) { ctx.fillStyle = 'rgba(255,68,68,0.12)'; ctx.fillRect(btnOffsetX - 1, row2Y - 1, btnW + 2, btnH + 2); } ctx.fillStyle = isActive ? '#ffcccc' : '#885555'; ctx.font = 'bold 11px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('[3]', btnOffsetX + 18, row2Y + 15); ctx.fillStyle = isActive ? '#ff6666' : '#553333'; ctx.fillText('\ud83d\udca5 ANNIHILATOR', btnOffsetX + btnW / 2 + 8, row2Y + 15); if (!window._bottomWeaponBtns) window._bottomWeaponBtns = []; window._bottomWeaponBtns.push({ x: btnOffsetX, y: row2Y, w: btnW, h: btnH, type: 'annihilator' }); } }
    if (!paused && unpauseCountdown <= 0) { ctx.fillStyle = '#444'; ctx.font = '9px "Courier New", monospace'; ctx.textAlign = 'right'; ctx.fillText('P: Pause', W - 15, row2Y + 15); }
    ctx.textAlign = 'start';
}

function formatAmuletPower(power) {
    switch (power.type) {
        case 'regen': return 'Regenerate ' + power.value + ' HP/sec';
        case 'damageMult': return '+' + Math.round((power.value - 1) * 100) + '% damage dealt';
        case 'extraSkeletons': return '+' + power.value + ' extra skeletons';
        case 'minionDamageMult': return '+' + Math.round((power.value - 1) * 100) + '% minion damage';
        case 'speedMult': return '+' + Math.round((power.value - 1) * 100) + '% movement speed';
        case 'damageResist': return Math.round(power.value * 100) + '% damage resistance';
        case 'cdMult': return '-' + Math.round((1 - power.value) * 100) + '% cooldowns';
        case 'magnetMult': return Math.round(power.value) + 'x token pickup range';
        case 'plunderChance': return '100% token drop from walls';
        case 'coolantMult': return 'Annihilator fires 2x longer';
        default: return power.type + ': ' + power.value;
    }
}

function renderAmuletDialog() {
    if (!amuletPickupState) return;
    // Reset button tracking each frame
    window._amuletDialogBtns = [];
    
    // Dim background
    ctx.fillStyle = 'rgba(0,0,0,0.7)';
    ctx.fillRect(0, 0, W, H);
    
    const newA = amuletPickupState.newAmulet;
    const existingA = amuletPickupState.existingAmulet;
    const newType = AMULET_TYPES.find(t => t.id === newA.type);
    const existingType = existingA ? AMULET_TYPES.find(t => t.id === existingA.type) : null;
    
    // Title
    ctx.fillStyle = '#c9a23b';
    ctx.font = 'bold 24px "Courier New", monospace';
    ctx.textAlign = 'center';
    ctx.fillText('\u2726 Amulet Found! \u2726', W / 2, 55);
    
    const cardW = 280, cardH = 260, cardGap = 30;
    const leftCardX = W / 2 - cardW - cardGap / 2;
    const rightCardX = W / 2 + cardGap / 2;
    const cardY = 85;
    
    // Helper to draw one amulet card
    function drawAmuletCard(cx, cy, amuletObj, amuletType, label, isNew) {
        // Card background
        ctx.fillStyle = isNew ? '#1a1a2e' : '#1a1a1a';
        ctx.strokeStyle = isNew && amuletType ? amuletType.glowColor : '#444';
        ctx.lineWidth = isNew ? 3 : 2;
        ctx.fillRect(cx, cy, cardW, cardH);
        ctx.strokeRect(cx, cy, cardW, cardH);
        
        // Label
        ctx.fillStyle = isNew ? '#ffcc44' : '#888';
        ctx.font = 'bold 13px "Courier New", monospace';
        ctx.textAlign = 'center';
        ctx.fillText(label, cx + cardW / 2, cy + 22);
        
        // Icon
        const iconCY = cy + 60;
        const glowPulse = isNew ? (1 + Math.sin(Date.now() / 400) * 0.15) : 1;
        if (amuletType) {
            ctx.fillStyle = amuletType.glowColor;
            ctx.globalAlpha = 0.2 * glowPulse;
            ctx.beginPath(); ctx.arc(cx + cardW / 2, iconCY, 30, 0, Math.PI * 2); ctx.fill();
            ctx.globalAlpha = 1;
            ctx.fillStyle = '#151025';
            ctx.strokeStyle = amuletType.glowColor;
            ctx.lineWidth = 2;
            ctx.fillRect(cx + cardW / 2 - 22, iconCY - 22, 44, 44);
            ctx.strokeRect(cx + cardW / 2 - 22, iconCY - 22, 44, 44);
            ctx.fillStyle = amuletType.glowColor;
            ctx.font = '28px "Courier New", monospace';
            ctx.fillText(amuletType.icon, cx + cardW / 2, iconCY + 9);
        } else {
            ctx.fillStyle = '#333';
            ctx.globalAlpha = 0.2;
            ctx.beginPath(); ctx.arc(cx + cardW / 2, iconCY, 30, 0, Math.PI * 2); ctx.fill();
            ctx.globalAlpha = 1;
            ctx.fillStyle = '#111';
            ctx.strokeStyle = '#333';
            ctx.lineWidth = 1;
            ctx.fillRect(cx + cardW / 2 - 22, iconCY - 22, 44, 44);
            ctx.strokeRect(cx + cardW / 2 - 22, iconCY - 22, 44, 44);
            ctx.fillStyle = '#555';
            ctx.font = '24px "Courier New", monospace';
            ctx.fillText('\u25c7', cx + cardW / 2, iconCY + 8);
        }
        
        // Name
        const nameY = cy + 110;
        ctx.fillStyle = amuletType ? '#fff' : '#555';
        ctx.font = 'bold 14px "Courier New", monospace';
        ctx.textAlign = 'center';
        ctx.fillText(amuletType ? amuletType.name : 'None', cx + cardW / 2, nameY);
        
        // Powers list
        let descY = nameY + 22;
        if (amuletType) {
            ctx.fillStyle = '#aaa';
            ctx.font = '11px "Courier New", monospace';
            for (const power of amuletType.powers) {
                ctx.fillText(formatAmuletPower(power), cx + cardW / 2, descY);
                descY += 16;
            }
        } else {
            ctx.fillStyle = '#555';
            ctx.font = '11px "Courier New", monospace';
            ctx.fillText('No amulet equipped', cx + cardW / 2, descY);
        }
        
        // Button
        const btnW = 100, btnH = 30;
        const btnX = cx + cardW / 2 - btnW / 2;
        const btnY = cy + cardH - 45;
        if (isNew) {
            ctx.fillStyle = '#1a3a1a';
            ctx.strokeStyle = '#44cc44';
            ctx.lineWidth = 2;
            ctx.fillRect(btnX, btnY, btnW, btnH);
            ctx.strokeRect(btnX, btnY, btnW, btnH);
            ctx.fillStyle = '#88ff88';
            ctx.font = 'bold 13px "Courier New", monospace';
            ctx.fillText('EQUIP', cx + cardW / 2, btnY + 21);
            if (!window._amuletDialogBtns) window._amuletDialogBtns = [];
            window._amuletDialogBtns.push({ x: btnX, y: btnY, w: btnW, h: btnH, action: 'equip' });
        } else {
            ctx.fillStyle = '#2a1a1a';
            ctx.strokeStyle = '#884444';
            ctx.lineWidth = 2;
            ctx.fillRect(btnX, btnY, btnW, btnH);
            ctx.strokeRect(btnX, btnY, btnW, btnH);
            ctx.fillStyle = '#ff8888';
            ctx.font = 'bold 13px "Courier New", monospace';
            ctx.fillText('DISCARD', cx + cardW / 2, btnY + 21);
            if (!window._amuletDialogBtns) window._amuletDialogBtns = [];
            window._amuletDialogBtns.push({ x: btnX, y: btnY, w: btnW, h: btnH, action: 'discard' });
        }
    }
    
    drawAmuletCard(leftCardX, cardY, existingA, existingType, 'CURRENT AMULET', false);
    drawAmuletCard(rightCardX, cardY, newA, newType, 'NEW AMULET', true);
    
    ctx.fillStyle = '#aaa';
    ctx.font = '12px "Courier New", monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Choose to equip the new amulet or discard it', W / 2, cardY + cardH + 35);
    
    ctx.textAlign = 'start';
}

function renderPauseOverlay() { ctx.fillStyle = 'rgba(0,0,0,0.7)'; ctx.fillRect(0, 0, W, H); if (unpauseCountdown > 0) { const seconds = Math.ceil(unpauseCountdown), countSize = 80 + (unpauseCountdown % 1) * 40, alpha = Math.min(1, (unpauseCountdown % 1) * 3); ctx.fillStyle = '#ffffff'; ctx.font = 'bold ' + countSize + 'px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText(seconds.toString(), W / 2, H / 2 - 10); ctx.fillStyle = 'rgba(200,200,200,' + alpha + ')'; ctx.font = '18px "Courier New", monospace'; ctx.fillText('Get ready...', W / 2, H / 2 + 55); ctx.fillStyle = '#777'; ctx.font = '12px "Courier New", monospace'; ctx.fillText('Press P to cancel', W / 2, H / 2 + 80); } else { ctx.fillStyle = '#ffffff'; ctx.font = 'bold 48px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('PAUSED', W / 2, H / 2 - 10); ctx.fillStyle = '#aaa'; ctx.font = '18px "Courier New", monospace'; ctx.fillText('Press P to resume (3s countdown)', W / 2, H / 2 + 30); } ctx.textAlign = 'start'; }

function drawPurpleBlock() {
    const bsx = toScreenX(purpleBlock.x), bsy = toScreenY(purpleBlock.y);
    const s = purpleBlock.size;
    if (bsx < -s * 2 || bsx > W + s * 2 || bsy < -s * 2 || bsy > GAME_VIEW_H + s * 2) return;
    // Update glow phase continuously
    purpleBlock.glowPhase += 0.03;
    const glowPulse = 1 + Math.sin(purpleBlock.glowPhase) * 0.18;
    const hitFlashAlpha = purpleBlock.hitFlash > 0 ? purpleBlock.hitFlash / 0.12 : 0;
    
    // Outer glow aura — always visible, pulses
    const outerGlowR = s * 1.6 * glowPulse;
    const outerGrad = ctx.createRadialGradient(bsx, bsy, s * 0.35, bsx, bsy, outerGlowR);
    outerGrad.addColorStop(0, 'rgba(180,100,255,0.55)');
    outerGrad.addColorStop(0.5, 'rgba(140,60,220,0.25)');
    outerGrad.addColorStop(1, 'rgba(100,20,180,0)');
    ctx.fillStyle = outerGrad;
    ctx.beginPath(); ctx.arc(bsx, bsy, outerGlowR, 0, Math.PI * 2); ctx.fill();
    
    // Mid glow ring
    const midGlowR = s * 1.15 * glowPulse;
    const midGrad = ctx.createRadialGradient(bsx, bsy, s * 0.25, bsx, bsy, midGlowR);
    midGrad.addColorStop(0, 'rgba(200,130,255,0.6)');
    midGrad.addColorStop(0.7, 'rgba(150,80,240,0.2)');
    midGrad.addColorStop(1, 'rgba(120,40,200,0)');
    ctx.fillStyle = midGrad;
    ctx.beginPath(); ctx.arc(bsx, bsy, midGlowR, 0, Math.PI * 2); ctx.fill();
    
    // Shadow under crystal
    ctx.fillStyle = 'rgba(0,0,0,0.4)';
    ctx.beginPath(); ctx.ellipse(bsx, bsy + s * 0.55, s * 0.85, s * 0.25, 0, 0, Math.PI * 2); ctx.fill();
    
    // Main crystal body — faceted square with rotation illusion
    ctx.save();
    ctx.translate(bsx, bsy);
    const crystalRot = purpleBlock.glowPhase * 0.4;
    ctx.rotate(crystalRot);
    
    // Outer crystal shell
    const innerSize = s * 0.85;
    ctx.fillStyle = hitFlashAlpha > 0.5 ? '#ffffff' : '#3a2060';
    ctx.strokeStyle = hitFlashAlpha > 0 ? 'rgba(255,255,255,' + hitFlashAlpha + ')' : '#9966dd';
    ctx.lineWidth = 2.5 * glowPulse;
    ctx.beginPath();
    ctx.moveTo(0, -innerSize);
    ctx.lineTo(innerSize * 0.7, -innerSize * 0.3);
    ctx.lineTo(innerSize, innerSize * 0.3);
    ctx.lineTo(innerSize * 0.3, innerSize);
    ctx.lineTo(-innerSize * 0.3, innerSize);
    ctx.lineTo(-innerSize, innerSize * 0.3);
    ctx.lineTo(-innerSize * 0.7, -innerSize * 0.3);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    
    // Inner faceted faces — create a gem-like look
    ctx.fillStyle = hitFlashAlpha > 0.5 ? '#ffffff' : '#5a30a0';
    ctx.beginPath();
    ctx.moveTo(0, -innerSize * 0.85);
    ctx.lineTo(innerSize * 0.55, -innerSize * 0.1);
    ctx.lineTo(0, innerSize * 0.15);
    ctx.lineTo(-innerSize * 0.55, -innerSize * 0.1);
    ctx.closePath();
    ctx.fill();
    
    ctx.fillStyle = hitFlashAlpha > 0.5 ? '#ffffff' : '#7b4fc0';
    ctx.beginPath();
    ctx.moveTo(0, innerSize * 0.15);
    ctx.lineTo(innerSize * 0.55, -innerSize * 0.1);
    ctx.lineTo(innerSize * 0.15, innerSize * 0.6);
    ctx.lineTo(-innerSize * 0.15, innerSize * 0.6);
    ctx.closePath();
    ctx.fill();
    
    ctx.fillStyle = hitFlashAlpha > 0.5 ? '#ffffff' : '#4a2590';
    ctx.beginPath();
    ctx.moveTo(0, innerSize * 0.15);
    ctx.lineTo(-innerSize * 0.55, -innerSize * 0.1);
    ctx.lineTo(-innerSize * 0.15, innerSize * 0.6);
    ctx.closePath();
    ctx.fill();
    
    // Bright core
    const coreGrad = ctx.createRadialGradient(0, 0, 0, 0, 0, s * 0.4 * glowPulse);
    coreGrad.addColorStop(0, 'rgba(255,255,255,0.7)');
    coreGrad.addColorStop(0.4, 'rgba(220,180,255,0.5)');
    coreGrad.addColorStop(1, 'rgba(180,100,255,0)');
    ctx.fillStyle = coreGrad;
    ctx.beginPath(); ctx.arc(0, 0, s * 0.4 * glowPulse, 0, Math.PI * 2); ctx.fill();
    
    // Tiny sparkles
    for (let i = 0; i < 4; i++) {
        const sparkAngle = purpleBlock.glowPhase * 1.7 + i * Math.PI / 2;
        const sparkDist = s * 0.6;
        const sparkX = Math.cos(sparkAngle) * sparkDist;
        const sparkY = Math.sin(sparkAngle) * sparkDist;
        const sparkAlpha = 0.4 + Math.sin(purpleBlock.glowPhase * 3 + i) * 0.4;
        ctx.fillStyle = 'rgba(255,255,255,' + sparkAlpha + ')';
        ctx.beginPath(); ctx.arc(sparkX, sparkY, 2, 0, Math.PI * 2); ctx.fill();
    }
    
    ctx.restore();
    
    // Health bar
    const barW = s * 2.2, barH = 4, barY = bsy - s * 1.5;
    ctx.fillStyle = '#150520';
    ctx.fillRect(bsx - barW / 2 - 1, barY - 1, barW + 2, barH + 2);
    const hpRatio = purpleBlock.health / purpleBlock.maxHealth;
    const barGrad = ctx.createLinearGradient(bsx - barW / 2, barY, bsx + barW / 2, barY);
    barGrad.addColorStop(0, '#7722cc');
    barGrad.addColorStop(0.5, '#bb66ff');
    barGrad.addColorStop(1, '#7722cc');
    ctx.fillStyle = barGrad;
    ctx.fillRect(bsx - barW / 2, barY, barW * hpRatio, barH);
    
    // Label
    ctx.fillStyle = '#cc99ff';
    ctx.font = 'bold 9px "Courier New", monospace';
    ctx.textAlign = 'center';
    ctx.fillText('\u2726 CRYSTAL \u2726', bsx, barY - 5);
    ctx.textAlign = 'start';
}

function drawWall(sx, sy, tileState) {
    if (tileState === 2) { ctx.fillStyle = '#2a2a36'; ctx.fillRect(sx, sy, TILE, TILE); ctx.fillStyle = '#333340'; ctx.fillRect(sx + 2, sy + 2, TILE - 4, TILE - 4); ctx.strokeStyle = '#22222e'; ctx.lineWidth = 1; ctx.strokeRect(sx + 2, sy + 2, TILE - 4, TILE - 4); ctx.beginPath(); ctx.moveTo(sx, sy + TILE / 2); ctx.lineTo(sx + TILE, sy + TILE / 2); ctx.stroke(); ctx.beginPath(); ctx.moveTo(sx + TILE / 2, sy); ctx.lineTo(sx + TILE / 2, sy + TILE / 2); ctx.stroke(); ctx.fillStyle = '#3d3d4d'; ctx.fillRect(sx + 3, sy + 3, TILE - 6, 3); ctx.fillRect(sx + 3, sy + 3, 3, TILE - 6); }
    else { ctx.fillStyle = '#1a1a24'; ctx.fillRect(sx, sy, TILE, TILE); ctx.fillStyle = '#282833'; ctx.fillRect(sx + 2, sy + 2, TILE - 4, TILE - 4); ctx.strokeStyle = '#1a1a24'; ctx.lineWidth = 1; ctx.strokeRect(sx + 2, sy + 2, TILE - 4, TILE - 4); ctx.strokeStyle = '#3a3a44'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(sx + 6, sy + 6); ctx.lineTo(sx + TILE * 0.45, sy + TILE * 0.5); ctx.lineTo(sx + TILE - 10, sy + TILE - 6); ctx.stroke(); ctx.beginPath(); ctx.moveTo(sx + TILE - 7, sy + 8); ctx.lineTo(sx + TILE * 0.5, sy + TILE * 0.35); ctx.lineTo(sx + 10, sy + TILE - 9); ctx.stroke(); ctx.beginPath(); ctx.moveTo(sx + TILE * 0.3, sy + 4); ctx.lineTo(sx + TILE * 0.35, sy + TILE * 0.4); ctx.stroke(); ctx.fillStyle = '#333340'; ctx.fillRect(sx + 9, sy + TILE - 8, 4, 4); ctx.fillRect(sx + TILE - 13, sy + 12, 3, 3); ctx.fillRect(sx + TILE - 16, sy + TILE - 10, 2, 3); ctx.fillStyle = '#2a2a33'; ctx.fillRect(sx + 12, sy + 8, 3, 2); }
}

function drawPlayer(px, py) {
    const ch = characters[selectedCharacter], size = PLAYER_SIZE, swapGlow = player.swapFlash > 0, isDashing = player.isDashing && player.dashTimer > 0;
    if (isDashing) { const dashAlpha = 0.18; for (let a = 1; a <= 3; a++) { const backX = px - player.dashDx * a * 18, backY = py - player.dashDy * a * 18; ctx.fillStyle = 'rgba(180,200,255,' + (dashAlpha * (1 - a / 4)) + ')'; ctx.beginPath(); ctx.arc(backX, backY, size * 0.7, 0, Math.PI * 2); ctx.fill(); } }
    ctx.fillStyle = swapGlow ? 'rgba(255,255,255,0.25)' : 'rgba(180,160,220,0.15)'; ctx.beginPath(); ctx.arc(px, py, size + 8, 0, Math.PI * 2); ctx.fill();
    const bodyColor = player.attackFlash > 0 ? '#ffffff' : player.secondaryFlash > 0 ? '#ffe8c0' : ch.robeColor, invulnBlink = player.invulnTimer > 0 && Math.floor(player.invulnTimer * 20) % 2 === 0;
    ctx.fillStyle = invulnBlink ? '#ffffff' : bodyColor; ctx.fillRect(px - size * 0.5, py - size * 0.3, size, size * 0.85);
    ctx.fillStyle = invulnBlink ? '#ffffff' : ch.skinColor; ctx.beginPath(); ctx.arc(px, py - size * 0.5, size * 0.38, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = invulnBlink ? '#ffffff' : ch.color;
    if (selectedCharacter === 'knight') { ctx.fillRect(px - size * 0.35, py - size * 0.95, size * 0.7, size * 0.4); ctx.fillRect(px - size * 0.2, py - size * 1.15, size * 0.4, size * 0.25); }
    else { ctx.beginPath(); ctx.moveTo(px, py - size * 1.15); ctx.lineTo(px - size * 0.4, py - size * 0.5); ctx.lineTo(px + size * 0.4, py - size * 0.5); ctx.closePath(); ctx.fill(); }
    const showRailgun = selectedCharacter === 'sorcerer' && hasRailgun() && player.activeWeapon === 'railgun', showMinigun = selectedCharacter === 'sorcerer' && hasMinigun() && player.activeWeapon === 'minigun', showAnnihilator = selectedCharacter === 'sorcerer' && hasAnnihilator() && player.activeWeapon === 'annihilator';
    if (showRailgun) {
        ctx.save(); ctx.translate(px, py); ctx.rotate(player.angle);
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#3a3a4a'; ctx.fillRect(-6, -5, 38, 10); ctx.fillStyle = invulnBlink ? '#ffffff' : '#4a4a5a'; ctx.fillRect(-4, -4, 35, 8);
        for (let c = 0; c < 4; c++) { const coilX = 4 + c * 8, coilGlow = 0.5 + Math.sin(Date.now() / 150 + c) * 0.3, coilAlpha = 0.4 + coilGlow * 0.5; ctx.fillStyle = invulnBlink ? 'rgba(255,255,255,' + coilAlpha + ')' : 'rgba(68,170,255,' + coilAlpha + ')'; ctx.fillRect(coilX, -6, 4, 12); ctx.fillStyle = invulnBlink ? 'rgba(255,255,255,' + (coilAlpha * 0.7) + ')' : 'rgba(136,221,255,' + (coilAlpha * 0.7) + ')'; ctx.fillRect(coilX + 1, -5, 2, 10); }
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#2a2a38'; ctx.fillRect(36, -7, 10, 14); ctx.fillStyle = invulnBlink ? '#ffffff' : '#222233'; ctx.fillRect(44, -6, 6, 12);
        const chargeGlow = player.railgunCharging ? player.railgunCharge : 0, tipGlowAlpha = 0.35 + chargeGlow * 0.65, tipGlowR = 5 + chargeGlow * 18;
        ctx.fillStyle = invulnBlink ? 'rgba(255,255,255,' + tipGlowAlpha + ')' : 'rgba(100,200,255,' + tipGlowAlpha + ')'; ctx.beginPath(); ctx.arc(50, 0, tipGlowR, 0, Math.PI * 2); ctx.fill();
        if (chargeGlow > 0.3) { ctx.fillStyle = invulnBlink ? 'rgba(255,255,255,' + (chargeGlow * 0.8) + ')' : 'rgba(200,240,255,' + (chargeGlow * 0.8) + ')'; ctx.beginPath(); ctx.arc(50, 0, tipGlowR * 0.55, 0, Math.PI * 2); ctx.fill(); }
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#5a4a3a'; ctx.fillRect(-2, 6, 8, 14); ctx.fillStyle = invulnBlink ? '#ffffff' : '#4a3a2a'; ctx.fillRect(-1, 7, 6, 12);
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#333344'; ctx.fillRect(14, -12, 14, 6); ctx.fillStyle = invulnBlink ? 'rgba(255,255,255,0.5)' : 'rgba(100,180,255,' + (0.3 + chargeGlow * 0.5) + ')'; ctx.fillRect(16, -11, 10, 4);
        ctx.restore();
        if (player.railgunCharging) { const barW = 34, barH = 5, barX = px - barW / 2, barY = py - size - 20; ctx.fillStyle = '#0a0a20'; ctx.fillRect(barX - 1, barY - 1, barW + 2, barH + 2); const chargeRatio = player.railgunCharge; let barColor; if (chargeRatio >= 0.99) barColor = '#ffffff'; else if (chargeRatio > 0.5) barColor = '#88ddff'; else barColor = '#3388cc'; ctx.fillStyle = barColor; ctx.fillRect(barX, barY, barW * chargeRatio, barH); ctx.fillStyle = 'rgba(100,180,255,' + (0.2 + chargeRatio * 0.5) + ')'; ctx.fillRect(barX - 2, barY - 2, (barW + 4) * chargeRatio, barH + 4); if (chargeRatio > 0.15) { ctx.fillStyle = '#ffffff'; ctx.font = 'bold 8px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText(chargeRatio >= 0.99 ? 'MAX' : Math.round(chargeRatio * 100) + '%', px, barY - 7); ctx.textAlign = 'start'; } }
    } else if (showMinigun) {
        const spin = player.minigunSpin || 0; ctx.save(); ctx.translate(px, py); ctx.rotate(player.angle);
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#4a3020'; ctx.fillRect(-14, -4, 10, 8); ctx.fillStyle = invulnBlink ? '#ffffff' : '#cc8833'; ctx.fillRect(12, 5, 6, 4); ctx.fillRect(10, 7, 4, 3);
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#3a3a3a'; ctx.fillRect(2, -9, 30, 18); ctx.fillStyle = invulnBlink ? '#ffffff' : '#4a4a4a'; ctx.fillRect(4, -7, 26, 14); ctx.fillStyle = invulnBlink ? '#ffffff' : '#2a2a2a'; ctx.fillRect(28, -11, 9, 22); ctx.fillStyle = invulnBlink ? '#ffffff' : '#555'; ctx.fillRect(36, -10, 4, 20);
        const barrelCount = 6, barrelR = 7; for (let i = 0; i < barrelCount; i++) { const angle = spin + (i / barrelCount) * Math.PI * 2, bx = 40 + Math.cos(angle) * barrelR, by = Math.sin(angle) * barrelR; ctx.fillStyle = invulnBlink ? '#ffffff' : '#383838'; ctx.fillRect(bx, by - 2, 10, 4); ctx.fillStyle = invulnBlink ? '#ffffff' : '#2a2a2a'; ctx.fillRect(bx + 1, by - 1, 8, 2); }
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#444'; ctx.beginPath(); ctx.arc(45, 0, barrelR + 2.5, 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = invulnBlink ? '#ffffff' : '#555'; ctx.lineWidth = 1; ctx.stroke();
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#5a3a2a'; ctx.fillRect(0, 8, 7, 13); ctx.fillStyle = invulnBlink ? '#ffffff' : '#4a2a1a'; ctx.fillRect(1, 9, 5, 11);
        ctx.strokeStyle = invulnBlink ? '#ffffff' : '#333'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(2, 7); ctx.lineTo(6, 12); ctx.lineTo(11, 9); ctx.stroke();
        if (player.minigunMuzzleFlash > 0) { const flashAlpha = player.minigunMuzzleFlash / 0.06; ctx.fillStyle = 'rgba(255,200,40,' + (flashAlpha * 0.9) + ')'; ctx.beginPath(); ctx.arc(48, 0, 9 + Math.random() * 3, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = 'rgba(255,255,200,' + (flashAlpha * 0.7) + ')'; ctx.beginPath(); ctx.arc(48, 0, 5, 0, Math.PI * 2); ctx.fill(); }
        ctx.restore();
        if (player.minigunMuzzleFlash > 0 && Math.random() < 0.5) { const ejectAngle = player.angle - Math.PI / 2 + (Math.random() - 0.5) * 0.4, ejectX = px + Math.cos(player.angle) * 5 + Math.cos(ejectAngle) * 10, ejectY = py + Math.sin(player.angle) * 5 + Math.sin(ejectAngle) * 10; particles.push({ x: ejectX, y: ejectY, vx: Math.cos(ejectAngle) * (60 + Math.random() * 80), vy: Math.sin(ejectAngle) * (40 + Math.random() * 60), life: 0.4, maxLife: 0.4, color: '#cc9933', size: 2 + Math.random() * 2 }); }
    } else if (showAnnihilator) {
        const spin = player.minigunSpin || 0; ctx.save(); ctx.translate(px, py); ctx.rotate(player.angle);
        const heatRatio = player.annihilatorHeat || 0;
        const overheat = player.annihilatorOverheated;
        // Body/stock
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#2a1018'; ctx.fillRect(-8, -5, 12, 10);
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#1a0808'; ctx.fillRect(2, -10, 32, 20);
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#331010'; ctx.fillRect(6, -8, 26, 16);
        // Heat glow on body
        const bodyGlowAlpha = 0.2 + heatRatio * 0.7;
        ctx.fillStyle = 'rgba(255,60,20,' + bodyGlowAlpha + ')';
        ctx.fillRect(10, -7, 18, 14);
        // Barrel housing - menacing dark metal
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#1c0c0c'; ctx.fillRect(30, -12, 10, 24);
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#2a1515'; ctx.fillRect(38, -11, 5, 22);
        // Spinning barrels - larger than minigun
        const barrelCount = 6, barrelR = 8;
        for (let i = 0; i < barrelCount; i++) {
            const angle = spin + (i / barrelCount) * Math.PI * 2;
            const bx = 42 + Math.cos(angle) * barrelR;
            const by = Math.sin(angle) * barrelR;
            ctx.fillStyle = invulnBlink ? '#ffffff' : '#251010';
            ctx.fillRect(bx, by - 2.5, 12, 5);
            ctx.fillStyle = invulnBlink ? '#ffffff' : '#1a0808';
            ctx.fillRect(bx + 1, by - 1.5, 10, 3);
            // Heat glow on barrel tips
            const barrelHeatAlpha = 0.3 + heatRatio * 0.65;
            ctx.fillStyle = 'rgba(255,100,30,' + barrelHeatAlpha + ')';
            ctx.fillRect(bx + 9, by - 2, 4, 4);
        }
        // Front plate
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#3a1515';
        ctx.beginPath(); ctx.arc(48, 0, barrelR + 3.5, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = invulnBlink ? '#ffffff' : '#551a1a'; ctx.lineWidth = 1.5; ctx.stroke();
        // Heat ring around front
        const frontGlowAlpha = 0.35 + heatRatio * 0.6;
        ctx.fillStyle = 'rgba(255,80,30,' + frontGlowAlpha + ')';
        ctx.beginPath(); ctx.arc(48, 0, barrelR + 6, 0, Math.PI * 2); ctx.fill();
        // Center glow
        const centerGlowAlpha = 0.5 + heatRatio * 0.5;
        ctx.fillStyle = 'rgba(255,255,200,' + centerGlowAlpha + ')';
        ctx.beginPath(); ctx.arc(48, 0, 3 + heatRatio * 5, 0, Math.PI * 2); ctx.fill();
        // Overheat white-hot core
        if (overheat) {
            ctx.fillStyle = 'rgba(255,255,255,' + (0.7 + Math.sin(Date.now() / 80) * 0.3) + ')';
            ctx.beginPath(); ctx.arc(48, 0, 6 + Math.random() * 4, 0, Math.PI * 2); ctx.fill();
        }
        // Muzzle flash
        if (player.minigunMuzzleFlash > 0 || player.attackFlash > 0) {
            const flashAlpha = (player.minigunMuzzleFlash || player.attackFlash) / 0.1;
            ctx.fillStyle = 'rgba(255,180,40,' + (flashAlpha * 0.9) + ')';
            ctx.beginPath(); ctx.arc(52, 0, 12 + Math.random() * 4, 0, Math.PI * 2); ctx.fill();
            ctx.fillStyle = 'rgba(255,255,200,' + (flashAlpha * 0.7) + ')';
            ctx.beginPath(); ctx.arc(52, 0, 7, 0, Math.PI * 2); ctx.fill();
            ctx.fillStyle = 'rgba(255,255,255,' + (flashAlpha * 0.5) + ')';
            ctx.beginPath(); ctx.arc(52, 0, 3, 0, Math.PI * 2); ctx.fill();
        }
        // Grip/stock
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#2a1010'; ctx.fillRect(-3, 8, 8, 15);
        ctx.fillStyle = invulnBlink ? '#ffffff' : '#1a0808'; ctx.fillRect(-2, 9, 6, 13);
        ctx.restore();
        // Heat bar above player
        if (heatRatio > 0.01) {
            const barW = 34, barH = 5, barX = px - barW / 2, barY = py - size - 22;
            ctx.fillStyle = '#100808'; ctx.fillRect(barX - 1, barY - 1, barW + 2, barH + 2);
            let barColor;
            if (overheat) barColor = '#ffffff';
            else if (heatRatio > 0.7) barColor = '#ff4422';
            else if (heatRatio > 0.35) barColor = '#ff8822';
            else barColor = '#ffaa44';
            ctx.fillStyle = barColor;
            ctx.fillRect(barX, barY, barW * heatRatio, barH);
            if (heatRatio > 0.25) {
                ctx.fillStyle = 'rgba(255,100,30,' + (0.2 + heatRatio * 0.4) + ')';
                ctx.fillRect(barX - 2, barY - 2, (barW + 4) * heatRatio, barH + 4);
            }
            ctx.fillStyle = overheat ? '#ff4444' : '#ffaa66';
            ctx.font = 'bold 8px "Courier New", monospace'; ctx.textAlign = 'center';
            ctx.fillText(overheat ? 'OVERHEATED!' : 'HEAT ' + Math.round(heatRatio * 100) + '%', px, barY - 7);
            ctx.textAlign = 'start';
        }
        // Overheat smoke particles are handled in updatePlayer
    } else if (selectedCharacter === 'sorcerer') { const tipX = px + Math.cos(player.angle) * size * 1.2, tipY = py + Math.sin(player.angle) * size * 1.2; ctx.strokeStyle = invulnBlink ? '#ffffff' : '#b57edc'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(tipX, tipY); ctx.stroke(); const glowPulse = 1 + Math.sin(Date.now() / 200) * 0.25; ctx.fillStyle = 'rgba(255,120,40,0.5)'; ctx.beginPath(); ctx.arc(tipX, tipY, 4.5 * glowPulse, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = '#ff8844'; ctx.beginPath(); ctx.arc(tipX, tipY, 2.5, 0, Math.PI * 2); ctx.fill(); }
    else if (player.attackSwingTimer > 0) { const swingProgress = 1 - (player.attackSwingTimer / SWING_DURATION), halfArc = player.attackSwingArc / 2, swingStartAngle = player.angle - halfArc, swingAngle = swingStartAngle + swingProgress * player.attackSwingArc, arcAlpha = Math.max(0, (1 - swingProgress) * 0.55); ctx.strokeStyle = 'rgba(255,245,200,' + arcAlpha + ')'; ctx.lineWidth = 5; ctx.beginPath(); ctx.arc(px, py, size * 1.35, swingStartAngle, swingAngle); ctx.stroke(); const glowAlpha = Math.max(0, (1 - swingProgress) * 0.25); ctx.strokeStyle = 'rgba(255,255,220,' + glowAlpha + ')'; ctx.lineWidth = 9; ctx.beginPath(); ctx.arc(px, py, size * 1.35, swingStartAngle, swingAngle); ctx.stroke(); ctx.strokeStyle = invulnBlink ? '#ffffff' : '#f0e8d0'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(px, py); const weaponLen = size * 1.5; ctx.lineTo(px + Math.cos(swingAngle) * weaponLen, py + Math.sin(swingAngle) * weaponLen); ctx.stroke(); const tipX = px + Math.cos(swingAngle) * weaponLen, tipY = py + Math.sin(swingAngle) * weaponLen; ctx.fillStyle = invulnBlink ? '#ffffff' : '#fff8e0'; ctx.beginPath(); ctx.arc(tipX, tipY, 3.5, 0, Math.PI * 2); ctx.fill(); for (let i = 0; i < 3; i++) { const lx = tipX - Math.cos(swingAngle) * (4 + i * 5) + Math.cos(swingAngle + Math.PI / 2) * (i - 1) * 3, ly = tipY - Math.sin(swingAngle) * (4 + i * 5) + Math.sin(swingAngle + Math.PI / 2) * (i - 1) * 3, la = (1 - swingProgress) * 0.7 * (1 - i / 3); ctx.strokeStyle = 'rgba(255,255,240,' + la + ')'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(lx, ly); ctx.lineTo(lx - Math.cos(swingAngle) * 10, ly - Math.sin(swingAngle) * 10); ctx.stroke(); } }
    else { ctx.strokeStyle = invulnBlink ? '#ffffff' : '#d0c8b8'; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px + Math.cos(player.angle) * size * 1.3, py + Math.sin(player.angle) * size * 1.3); ctx.stroke(); const tipX = px + Math.cos(player.angle) * size * 1.3, tipY = py + Math.sin(player.angle) * size * 1.3; ctx.fillStyle = invulnBlink ? '#ffffff' : '#f0e8d0'; ctx.beginPath(); ctx.arc(tipX, tipY, 3, 0, Math.PI * 2); ctx.fill(); }
    const eyeX = Math.cos(player.angle) * 3, eyeY = Math.sin(player.angle) * 3; ctx.fillStyle = '#111'; ctx.beginPath(); ctx.arc(px + eyeX, py - size * 0.5, 2, 0, Math.PI * 2); ctx.fill();
    // Stun indicator
    if (player.stunTimer > 0) {
        const stunAlpha = 0.5 + Math.sin(Date.now() / 120) * 0.35;
        ctx.fillStyle = 'rgba(255,255,100,' + stunAlpha + ')';
        ctx.font = 'bold 10px "Courier New", monospace'; ctx.textAlign = 'center';
        ctx.fillText('\u26a1 STUNNED', px, py - size - 22);
        ctx.textAlign = 'start';
        // Yellow sparkles around player
        for (let i = 0; i < 3; i++) {
            const sparkAngle = Date.now() / 200 + i * 2.1;
            const sparkDist = size + 10 + Math.sin(Date.now() / 150 + i) * 5;
            ctx.fillStyle = 'rgba(255,220,40,' + (stunAlpha * 0.6) + ')';
            ctx.beginPath(); ctx.arc(px + Math.cos(sparkAngle) * sparkDist, py + Math.sin(sparkAngle) * sparkDist, 2.5, 0, Math.PI * 2); ctx.fill();
        }
    }
    if (player.health < player.maxHealth) { const barW = 28, barH = 4, barY = py - size - 10; ctx.fillStyle = '#300000'; ctx.fillRect(px - barW / 2, barY, barW, barH); ctx.fillStyle = '#cc2222'; ctx.fillRect(px - barW / 2, barY, barW * (player.health / player.maxHealth), barH); }
}

function drawMinion(minion, mx, my) {
    const s = minion.size, flashColor = minion.hitFlash > 0 ? '#ffffff' : minion.color, isMoving = minion.isMoving, walkPhase = minion.walkTimer || 0, isArcher = minion.minionType === 'archer', isMinigun2 = minion.minionType === 'minigun', isRailgun2 = minion.minionType === 'railgun', bobY = isMoving ? Math.sin(walkPhase * 2) * 1.5 : 0, bodyY = my - s * 0.08 + bobY;
    ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.beginPath(); ctx.ellipse(mx, my + s * 0.65, s * 0.7, s * 0.22, 0, 0, Math.PI * 2); ctx.fill();
    const hipY = bodyY + s * 0.3, legLen = s * 0.55, kneeY = hipY + legLen * 0.5, footY = hipY + legLen, lPhase = walkPhase, lKneeX = mx - s * 0.15 + Math.sin(lPhase) * s * 0.35, lFootX = lKneeX + Math.sin(lPhase) * s * 0.25;
    ctx.strokeStyle = flashColor; ctx.lineWidth = 2.8; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(mx - s * 0.2, hipY); ctx.lineTo(lKneeX, kneeY); ctx.lineTo(lFootX, footY); ctx.stroke(); ctx.fillStyle = flashColor; ctx.beginPath(); ctx.arc(lFootX, footY, 2.2, 0, Math.PI * 2); ctx.fill();
    const rPhase = walkPhase + Math.PI, rKneeX = mx + s * 0.15 + Math.sin(rPhase) * s * 0.35, rFootX = rKneeX + Math.sin(rPhase) * s * 0.25;
    ctx.strokeStyle = flashColor; ctx.beginPath(); ctx.moveTo(mx + s * 0.2, hipY); ctx.lineTo(rKneeX, kneeY); ctx.lineTo(rFootX, footY); ctx.stroke(); ctx.fillStyle = flashColor; ctx.beginPath(); ctx.arc(rFootX, footY, 2.2, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#d8ccb4'; ctx.beginPath(); ctx.ellipse(mx, hipY, s * 0.28, s * 0.1, 0, 0, Math.PI * 2); ctx.fill();
    const spineTop = bodyY - s * 0.35; ctx.strokeStyle = '#ddd0c0'; ctx.lineWidth = 2.2; ctx.beginPath(); ctx.moveTo(mx, hipY - s * 0.05); ctx.lineTo(mx, spineTop); ctx.stroke();
    for (let v = 0; v < 4; v++) { ctx.fillStyle = '#ddd0c0'; ctx.beginPath(); ctx.arc(mx, hipY - s * 0.08 - v * s * 0.1, 1.3, 0, Math.PI * 2); ctx.fill(); }
    const ribTop = spineTop + s * 0.08, ribBottom = hipY - s * 0.2; ctx.strokeStyle = '#d4c8b0'; ctx.lineWidth = 2;
    for (let r = 0; r < 4; r++) { const ry = ribTop + r * (ribBottom - ribTop) / 3, ribWidth = s * 0.3 + r * s * 0.03; ctx.beginPath(); ctx.moveTo(mx - ribWidth, ry); ctx.quadraticCurveTo(mx, ry - s * 0.06, mx + ribWidth, ry); ctx.stroke(); }
    const shoulderY = spineTop + s * 0.02, upperArmLen = s * 0.35, forearmLen = s * 0.3, laPhase = walkPhase + Math.PI, lShoulderX = mx - s * 0.35, lElbowX = lShoulderX + Math.sin(laPhase) * s * 0.22, lElbowY = shoulderY + upperArmLen * 0.65, lHandX = lElbowX + Math.sin(laPhase) * s * 0.18, lHandY = lElbowY + forearmLen * 0.75;
    ctx.strokeStyle = flashColor; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(lShoulderX, shoulderY); ctx.lineTo(lElbowX, lElbowY); ctx.lineTo(lHandX, lHandY); ctx.stroke(); ctx.fillStyle = flashColor; ctx.beginPath(); ctx.arc(lHandX, lHandY, 1.8, 0, Math.PI * 2); ctx.fill();
    const raPhase = walkPhase, rShoulderX = mx + s * 0.35, rElbowX = rShoulderX + Math.sin(raPhase) * s * 0.22, rElbowY = shoulderY + upperArmLen * 0.65, rHandX = rElbowX + Math.sin(raPhase) * s * 0.18, rHandY = rElbowY + forearmLen * 0.75;
    ctx.strokeStyle = flashColor; ctx.beginPath(); ctx.moveTo(rShoulderX, shoulderY); ctx.lineTo(rElbowX, rElbowY); ctx.lineTo(rHandX, rHandY); ctx.stroke(); ctx.fillStyle = flashColor; ctx.beginPath(); ctx.arc(rHandX, rHandY, 1.8, 0, Math.PI * 2); ctx.fill();
    const headY = spineTop - s * 0.22, skullR = s * 0.32;
    ctx.fillStyle = '#f5efe0'; ctx.beginPath(); ctx.arc(mx, headY, skullR, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = '#ede4d4'; ctx.beginPath(); ctx.moveTo(mx - skullR * 0.65, headY + skullR * 0.25); ctx.lineTo(mx + skullR * 0.65, headY + skullR * 0.25); ctx.lineTo(mx + skullR * 0.45, headY + skullR * 0.75); ctx.lineTo(mx - skullR * 0.45, headY + skullR * 0.75); ctx.closePath(); ctx.fill();
    ctx.fillStyle = '#f0e8d8'; ctx.beginPath(); ctx.arc(mx - skullR * 0.5, headY + skullR * 0.15, skullR * 0.35, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(mx + skullR * 0.5, headY + skullR * 0.15, skullR * 0.35, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#f5efe0'; ctx.beginPath(); ctx.arc(mx, headY, skullR, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#0d0d0d'; ctx.beginPath(); ctx.ellipse(mx - skullR * 0.32, headY - skullR * 0.08, skullR * 0.25, skullR * 0.28, 0.15, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.ellipse(mx + skullR * 0.32, headY - skullR * 0.08, skullR * 0.25, skullR * 0.28, -0.15, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = 'rgba(200,40,40,0.55)'; ctx.beginPath(); ctx.arc(mx - skullR * 0.32, headY - skullR * 0.08, skullR * 0.1, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(mx + skullR * 0.32, headY - skullR * 0.08, skullR * 0.1, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#0d0d0d'; ctx.beginPath(); ctx.moveTo(mx, headY + skullR * 0.02); ctx.lineTo(mx - skullR * 0.16, headY + skullR * 0.28); ctx.lineTo(mx + skullR * 0.16, headY + skullR * 0.28); ctx.closePath(); ctx.fill();
    ctx.fillStyle = '#e8dcc8'; for (let t = -2; t <= 2; t++) ctx.fillRect(mx + t * 3.2 - 1.5, headY + skullR * 0.38, 3, 3.5);
    if (isRailgun2) {
        const rgAngle = minion.angle || 0, rgOriginX = mx + Math.cos(rgAngle) * s * 0.35, rgOriginY = my + Math.sin(rgAngle) * s * 0.2;
        ctx.save(); ctx.translate(rgOriginX, rgOriginY); ctx.rotate(rgAngle);
        ctx.fillStyle = '#2a2a3a'; ctx.fillRect(-3, -3, 20, 6); ctx.fillStyle = '#3a3a4a'; ctx.fillRect(-2, -2, 18, 4);
        for (let c = 0; c < 3; c++) { const coilX = 2 + c * 6, coilGlow = 0.5 + Math.sin(Date.now() / 200 + c) * 0.3, coilAlpha = 0.4 + coilGlow * (minion.railgunCharging ? 0.8 : 0.3); ctx.fillStyle = 'rgba(68,140,220,' + coilAlpha + ')'; ctx.fillRect(coilX, -4, 3, 8); ctx.fillStyle = 'rgba(120,180,240,' + (coilAlpha * 0.6) + ')'; ctx.fillRect(coilX + 0.5, -3, 2, 6); }
        ctx.fillStyle = '#1a1a28'; ctx.fillRect(18, -3, 5, 6); ctx.fillStyle = '#111122'; ctx.fillRect(22, -2.5, 4, 5);
        const chargeGlow = minion.railgunCharging ? minion.railgunCharge : 0, tipGlowAlpha = 0.3 + chargeGlow * 0.65, tipGlowR = 3 + chargeGlow * 9;
        ctx.fillStyle = 'rgba(100,160,255,' + tipGlowAlpha + ')'; ctx.beginPath(); ctx.arc(26, 0, tipGlowR, 0, Math.PI * 2); ctx.fill();
        if (chargeGlow > 0.3) { ctx.fillStyle = 'rgba(160,210,255,' + (chargeGlow * 0.6) + ')'; ctx.beginPath(); ctx.arc(26, 0, tipGlowR * 0.5, 0, Math.PI * 2); ctx.fill(); }
        ctx.fillStyle = '#4a3a2a'; ctx.fillRect(-1, 4, 5, 8); ctx.fillStyle = '#3a2a1a'; ctx.fillRect(0, 5, 3, 6); ctx.fillStyle = '#2a2a38'; ctx.fillRect(8, -8, 8, 3); ctx.fillStyle = 'rgba(80,150,240,' + (0.2 + chargeGlow * 0.4) + ')'; ctx.fillRect(9, -7, 6, 1.5);
        ctx.restore();
        ctx.strokeStyle = '#556688'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(mx + Math.cos(rgAngle) * s * 0.2, my + Math.sin(rgAngle) * s * 0.1); ctx.lineTo(rgOriginX, rgOriginY); ctx.stroke();
        if (minion.railgunCharging) { const barW = s * 1.6, barH = 3, barX = mx - barW / 2, barY = my - s * 1.15; ctx.fillStyle = '#0a0a20'; ctx.fillRect(barX - 1, barY - 1, barW + 2, barH + 2); const chargeRatio = minion.railgunCharge; let barColor; if (chargeRatio >= 0.99) barColor = '#ffffff'; else if (chargeRatio > 0.5) barColor = '#88ddff'; else barColor = '#4488cc'; ctx.fillStyle = barColor; ctx.fillRect(barX, barY, barW * chargeRatio, barH); ctx.fillStyle = 'rgba(80,150,240,' + (0.15 + chargeRatio * 0.4) + ')'; ctx.fillRect(barX - 1.5, barY - 1.5, (barW + 3) * chargeRatio, barH + 3); }
    } else if (isMinigun2) {
        const mgAngle = minion.angle || 0, mgOriginX = mx + Math.cos(mgAngle) * s * 0.35, mgOriginY = my + Math.sin(mgAngle) * s * 0.2;
        ctx.save(); ctx.translate(mgOriginX, mgOriginY); ctx.rotate(mgAngle);
        ctx.fillStyle = '#4a4a4a'; ctx.fillRect(-4, -5, 16, 10); ctx.fillStyle = '#3a3a3a'; ctx.fillRect(-3, -4, 14, 8); ctx.fillStyle = '#2a2a2a'; ctx.fillRect(10, -6, 6, 12);
        const mgBarrelCount = 5, mgBarrelR = 3.8, mgSpin = minion.minigunSpin || 0;
        for (let i = 0; i < mgBarrelCount; i++) { const angle = mgSpin + (i / mgBarrelCount) * Math.PI * 2, bx = 14 + Math.cos(angle) * mgBarrelR, by = Math.sin(angle) * mgBarrelR; ctx.fillStyle = '#383838'; ctx.fillRect(bx, by - 1.5, 7, 3); ctx.fillStyle = '#2a2a2a'; ctx.fillRect(bx + 1, by - 1, 5, 2); }
        ctx.fillStyle = '#444'; ctx.beginPath(); ctx.arc(18, 0, mgBarrelR + 2, 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = '#555'; ctx.lineWidth = 0.8; ctx.stroke();
        ctx.fillStyle = '#5a3a2a'; ctx.fillRect(-2, 5, 5, 7);
        if (minion.minigunMuzzleFlash > 0) { const flashAlpha = minion.minigunMuzzleFlash / 0.06; ctx.fillStyle = 'rgba(255,200,40,' + (flashAlpha * 0.85) + ')'; ctx.beginPath(); ctx.arc(20, 0, 6 + Math.random() * 2, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = 'rgba(255,255,200,' + (flashAlpha * 0.6) + ')'; ctx.beginPath(); ctx.arc(20, 0, 3.5, 0, Math.PI * 2); ctx.fill(); }
        ctx.restore();
        ctx.strokeStyle = '#cc8833'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(mx + Math.cos(mgAngle) * s * 0.35 + Math.cos(mgAngle + Math.PI / 2) * 5, my + Math.sin(mgAngle) * s * 0.2 + Math.sin(mgAngle + Math.PI / 2) * 5); ctx.lineTo(mx + Math.cos(mgAngle) * s * 0.1 - Math.cos(mgAngle + Math.PI / 2) * 3, my + Math.sin(mgAngle) * s * 0.1 - Math.sin(mgAngle + Math.PI / 2) * 3); ctx.stroke();
    } else {
        const weaponAngle = minion.angle || 0, weaponStartX = mx + Math.cos(weaponAngle) * s * 0.25, weaponStartY = bodyY + Math.sin(weaponAngle) * s * 0.25, weaponEndX = mx + Math.cos(weaponAngle) * s * 1.15, weaponEndY = bodyY + Math.sin(weaponAngle) * s * 1.15;
        ctx.strokeStyle = '#c8c0b0'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(weaponStartX, weaponStartY); ctx.lineTo(weaponEndX, weaponEndY); ctx.stroke(); ctx.fillStyle = '#ddd8d0'; ctx.beginPath(); ctx.arc(weaponEndX, weaponEndY, 2.5, 0, Math.PI * 2); ctx.fill();
        const crossX = weaponStartX + Math.cos(weaponAngle) * s * 0.08, crossY = weaponStartY + Math.sin(weaponAngle) * s * 0.08; ctx.strokeStyle = '#b0a890'; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(crossX + Math.cos(weaponAngle + Math.PI / 2) * s * 0.15, crossY + Math.sin(weaponAngle + Math.PI / 2) * s * 0.15); ctx.lineTo(crossX - Math.cos(weaponAngle + Math.PI / 2) * s * 0.15, crossY - Math.sin(weaponAngle + Math.PI / 2) * s * 0.15); ctx.stroke();
        if (isArcher) { const bowAngle = minion.angle || 0, bowCenterX = mx + Math.cos(bowAngle) * s * 0.4, bowCenterY = my + Math.sin(bowAngle) * s * 0.3, bowRadius = s * 0.75; ctx.strokeStyle = '#b89570'; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(bowCenterX, bowCenterY, bowRadius, bowAngle - Math.PI * 0.55, bowAngle + Math.PI * 0.55); ctx.stroke(); ctx.strokeStyle = '#d4c8a0'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(bowCenterX + Math.cos(bowAngle - Math.PI * 0.55) * bowRadius, bowCenterY + Math.sin(bowAngle - Math.PI * 0.55) * bowRadius); ctx.lineTo(bowCenterX + Math.cos(bowAngle + Math.PI * 0.55) * bowRadius, bowCenterY + Math.sin(bowAngle + Math.PI * 0.55) * bowRadius); ctx.stroke(); const nockX = bowCenterX + Math.cos(bowAngle) * bowRadius * 0.85, nockY = bowCenterY + Math.sin(bowAngle) * bowRadius * 0.85; ctx.fillStyle = '#ffe8a0'; ctx.beginPath(); ctx.arc(nockX, nockY, 2.5, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = 'rgba(255,220,140,0.3)'; ctx.beginPath(); ctx.arc(nockX, nockY, 5, 0, Math.PI * 2); ctx.fill(); }
    }
    ctx.fillStyle = 'rgba(80,140,220,0.18)'; ctx.beginPath(); ctx.arc(mx, my, s * 0.85, 0, Math.PI * 2); ctx.fill();
    if (minion.health < minion.maxHealth) { const barW = s * 1.9, barH = 2.5, barY = my - s * 0.95; ctx.fillStyle = '#200000'; ctx.fillRect(mx - barW / 2, barY, barW, barH); ctx.fillStyle = isRailgun2 ? '#6699ff' : (isMinigun2 ? '#ff8844' : '#7799cc'); ctx.fillRect(mx - barW / 2, barY, barW * (minion.health / minion.maxHealth), barH); }
    const timeRatio = minion.lifetime / minion.maxLifetime, pieX = mx, pieY = my - s * 1.05, pieR = 5.5; ctx.strokeStyle = '#222'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(pieX, pieY, pieR, 0, Math.PI * 2); ctx.stroke();
    if (timeRatio > 0.001) { let pieColor; if (timeRatio > 0.5) pieColor = '#44cc44'; else if (timeRatio > 0.25) pieColor = '#cccc44'; else pieColor = '#cc4444'; ctx.fillStyle = pieColor; ctx.beginPath(); ctx.moveTo(pieX, pieY); ctx.arc(pieX, pieY, pieR - 1.2, -Math.PI / 2, -Math.PI / 2 + timeRatio * Math.PI * 2); ctx.closePath(); ctx.fill(); ctx.fillStyle = 'rgba(0,0,0,0.45)'; ctx.beginPath(); ctx.arc(pieX, pieY, pieR - 2.8, 0, Math.PI * 2); ctx.fill(); }
}

function drawEnemy(enemy, ex, ey) {
    const s = enemy.size, flashColor = enemy.hitFlash > 0 ? '#ffffff' : enemy.color, stunInd = enemy.stunTimer > 0;
    ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.beginPath(); ctx.ellipse(ex, ey + s * 0.5, s * 0.8, s * 0.3, 0, 0, Math.PI * 2); ctx.fill();
    if (enemy.type === 'witch') {
        const robeColor = enemy.hitFlash > 0 ? '#ffffff' : '#3d1f5c', hatColor = enemy.hitFlash > 0 ? '#ffffff' : '#2a1040', skinColor = enemy.hitFlash > 0 ? '#ffffff' : '#c8e0b0', glowPulse = 1 + Math.sin(Date.now() / 500) * 0.2;
        ctx.fillStyle = 'rgba(120,40,200,' + (0.15 * glowPulse) + ')'; ctx.beginPath(); ctx.arc(ex, ey, s * 1.15, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = robeColor; ctx.beginPath(); ctx.moveTo(ex - s * 0.5, ey + s * 0.2); ctx.lineTo(ex + s * 0.5, ey + s * 0.2); ctx.lineTo(ex + s * 0.35, ey + s * 0.8); ctx.lineTo(ex - s * 0.35, ey + s * 0.8); ctx.closePath(); ctx.fill();
        ctx.strokeStyle = robeColor; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(ex - s * 0.4, ey - s * 0.1); ctx.lineTo(ex - s * 0.7, ey + s * 0.25); ctx.stroke(); ctx.beginPath(); ctx.moveTo(ex + s * 0.4, ey - s * 0.1); ctx.lineTo(ex + s * 0.7, ey + s * 0.25); ctx.stroke();
        ctx.fillStyle = skinColor; ctx.beginPath(); ctx.arc(ex, ey - s * 0.35, s * 0.3, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = hatColor; ctx.beginPath(); ctx.moveTo(ex, ey - s * 1.25); ctx.lineTo(ex - s * 0.4, ey - s * 0.45); ctx.lineTo(ex + s * 0.4, ey - s * 0.45); ctx.closePath(); ctx.fill(); ctx.fillStyle = hatColor; ctx.fillRect(ex - s * 0.55, ey - s * 0.5, s * 1.1, s * 0.12); ctx.fillStyle = '#9944cc'; ctx.fillRect(ex - s * 0.42, ey - s * 0.52, s * 0.84, s * 0.07);
        const eyeGlow = 0.7 + Math.sin(Date.now() / 300) * 0.3; ctx.fillStyle = 'rgba(255,200,50,' + eyeGlow + ')'; ctx.beginPath(); ctx.arc(ex - s * 0.1, ey - s * 0.38, s * 0.08, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.1, ey - s * 0.38, s * 0.08, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#000'; ctx.beginPath(); ctx.arc(ex - s * 0.1, ey - s * 0.38, s * 0.04, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.1, ey - s * 0.38, s * 0.04, 0, Math.PI * 2); ctx.fill();
        const staffAngle = -0.7; ctx.strokeStyle = '#886644'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(ex + s * 0.5, ey + s * 0.4); ctx.lineTo(ex + s * 0.5 + Math.cos(staffAngle) * s * 1.1, ey + s * 0.4 + Math.sin(staffAngle) * s * 1.1); ctx.stroke();
        const orbX = ex + s * 0.5 + Math.cos(staffAngle) * s * 1.1, orbY = ey + s * 0.4 + Math.sin(staffAngle) * s * 1.1; ctx.fillStyle = 'rgba(200,80,255,' + (0.5 + Math.sin(Date.now() / 250) * 0.3) + ')'; ctx.beginPath(); ctx.arc(orbX, orbY, s * 0.2, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = 'rgba(255,200,255,0.5)'; ctx.beginPath(); ctx.arc(orbX, orbY, s * 0.08, 0, Math.PI * 2); ctx.fill();
    } else if (enemy.type === 'devil') {
        const devilRed = enemy.hitFlash > 0 ? '#ffffff' : '#cc2211', devilDark = enemy.hitFlash > 0 ? '#ffffff' : '#881100';
        ctx.fillStyle = devilRed; ctx.beginPath(); ctx.ellipse(ex, ey + s * 0.1, s * 0.5, s * 0.55, 0, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = devilRed; ctx.beginPath(); ctx.arc(ex, ey - s * 0.35, s * 0.38, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = devilDark; ctx.beginPath(); ctx.moveTo(ex - s * 0.15, ey - s * 0.6); ctx.lineTo(ex - s * 0.35, ey - s * 1.0); ctx.lineTo(ex - s * 0.05, ey - s * 0.5); ctx.closePath(); ctx.fill(); ctx.beginPath(); ctx.moveTo(ex + s * 0.15, ey - s * 0.6); ctx.lineTo(ex + s * 0.35, ey - s * 1.0); ctx.lineTo(ex + s * 0.05, ey - s * 0.5); ctx.closePath(); ctx.fill();
        ctx.fillStyle = '#ffff00'; ctx.beginPath(); ctx.arc(ex - s * 0.12, ey - s * 0.38, s * 0.1, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.12, ey - s * 0.38, s * 0.1, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#000'; ctx.beginPath(); ctx.arc(ex - s * 0.12, ey - s * 0.38, s * 0.05, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.12, ey - s * 0.38, s * 0.05, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = devilDark; ctx.beginPath(); ctx.moveTo(ex - s * 0.3, ey - s * 0.1); ctx.quadraticCurveTo(ex - s * 0.9, ey - s * 0.5, ex - s * 0.5, ey - s * 0.05); ctx.quadraticCurveTo(ex - s * 0.75, ey - s * 0.3, ex - s * 0.3, ey - s * 0.1); ctx.fill(); ctx.beginPath(); ctx.moveTo(ex + s * 0.3, ey - s * 0.1); ctx.quadraticCurveTo(ex + s * 0.9, ey - s * 0.5, ex + s * 0.5, ey - s * 0.05); ctx.quadraticCurveTo(ex + s * 0.75, ey - s * 0.3, ex + s * 0.3, ey - s * 0.1); ctx.fill();
        ctx.strokeStyle = devilRed; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(ex, ey + s * 0.45); ctx.quadraticCurveTo(ex + s * 0.4, ey + s * 0.7, ex + s * 0.5, ey + s * 0.4); ctx.stroke(); ctx.fillStyle = devilRed; ctx.beginPath(); ctx.moveTo(ex + s * 0.5, ey + s * 0.4); ctx.lineTo(ex + s * 0.65, ey + s * 0.25); ctx.lineTo(ex + s * 0.5, ey + s * 0.55); ctx.closePath(); ctx.fill();
        ctx.fillStyle = devilDark; ctx.beginPath(); ctx.arc(ex - s * 0.2, ey + s * 0.5, s * 0.15, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.2, ey + s * 0.5, s * 0.15, 0, Math.PI * 2); ctx.fill();
    } else if (enemy.type === 'skeleton') {
        const boneColor = flashColor, skullColor = '#f5efe0', eyeGlow = 0.6 + Math.sin(Date.now() / 400) * 0.3, idleSway = Math.sin((enemy.idleTimer || 0) * 0.8) * 0.03;
        const hipY = ey - s * 0.18, lFootX = ex - s * 0.28, lFootY = ey + s * 0.5, rFootX = ex + s * 0.28, rFootY = ey + s * 0.5, lKneeX = ex - s * 0.16 + idleSway * s, lKneeY = ey + s * 0.12, rKneeX = ex + s * 0.16 - idleSway * s, rKneeY = ey + s * 0.12;
        ctx.strokeStyle = boneColor; ctx.lineWidth = 3; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(ex - s * 0.17, hipY); ctx.lineTo(lKneeX, lKneeY); ctx.lineTo(lFootX, lFootY); ctx.stroke(); ctx.fillStyle = boneColor; ctx.beginPath(); ctx.arc(lFootX, lFootY, 2.2, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = boneColor; ctx.beginPath(); ctx.moveTo(ex + s * 0.17, hipY); ctx.lineTo(rKneeX, rKneeY); ctx.lineTo(rFootX, rFootY); ctx.stroke(); ctx.fillStyle = boneColor; ctx.beginPath(); ctx.arc(rFootX, rFootY, 2.2, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#d8ccb4'; ctx.beginPath(); ctx.ellipse(ex, hipY, s * 0.27, s * 0.09, 0, 0, Math.PI * 2); ctx.fill();
        const spineTop = ey - s * 0.63; ctx.strokeStyle = '#ddd0c0'; ctx.lineWidth = 2.2; ctx.beginPath(); ctx.moveTo(ex, hipY - s * 0.05); ctx.lineTo(ex, spineTop); ctx.stroke();
        for (let v = 0; v < 4; v++) { ctx.fillStyle = '#ddd0c0'; ctx.beginPath(); ctx.arc(ex, hipY - s * 0.07 - v * s * 0.11, 1.3, 0, Math.PI * 2); ctx.fill(); }
        const ribTop = spineTop + s * 0.1, ribBottom = hipY - s * 0.22; ctx.strokeStyle = '#d4c8b0'; ctx.lineWidth = 2;
        for (let r = 0; r < 4; r++) { const ry = ribTop + r * (ribBottom - ribTop) / 3, ribWidth = s * 0.3 + r * s * 0.03; ctx.beginPath(); ctx.moveTo(ex - ribWidth, ry); ctx.quadraticCurveTo(ex, ry - s * 0.06, ex + ribWidth, ry); ctx.stroke(); }
        const shoulderY = spineTop + s * 0.04, lShoulderX = ex - s * 0.38, lElbowX = ex - s * 0.48 + idleSway * s * 0.6, lElbowY = shoulderY + s * 0.32, lHandX = ex - s * 0.42 + idleSway * s * 0.8, lHandY = lElbowY + s * 0.28;
        ctx.strokeStyle = boneColor; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(lShoulderX, shoulderY); ctx.lineTo(lElbowX, lElbowY); ctx.lineTo(lHandX, lHandY); ctx.stroke(); ctx.fillStyle = boneColor; ctx.beginPath(); ctx.arc(lHandX, lHandY, 2, 0, Math.PI * 2); ctx.fill();
        const rShoulderX = ex + s * 0.38, rElbowX = ex + s * 0.48 - idleSway * s * 0.6, rElbowY = shoulderY + s * 0.32, rHandX = ex + s * 0.42 - idleSway * s * 0.8, rHandY = rElbowY + s * 0.28;
        ctx.strokeStyle = boneColor; ctx.beginPath(); ctx.moveTo(rShoulderX, shoulderY); ctx.lineTo(rElbowX, rElbowY); ctx.lineTo(rHandX, rHandY); ctx.stroke(); ctx.fillStyle = boneColor; ctx.beginPath(); ctx.arc(rHandX, rHandY, 2, 0, Math.PI * 2); ctx.fill();
        const headY = spineTop - s * 0.23, skullR = s * 0.33;
        ctx.fillStyle = '#ede4d4'; ctx.beginPath(); ctx.moveTo(ex - skullR * 0.62, headY + skullR * 0.22); ctx.lineTo(ex + skullR * 0.62, headY + skullR * 0.22); ctx.lineTo(ex + skullR * 0.42, headY + skullR * 0.72); ctx.lineTo(ex - skullR * 0.42, headY + skullR * 0.72); ctx.closePath(); ctx.fill();
        ctx.fillStyle = '#f0e8d8'; ctx.beginPath(); ctx.arc(ex - skullR * 0.48, headY + skullR * 0.13, skullR * 0.33, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + skullR * 0.48, headY + skullR * 0.13, skullR * 0.33, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = skullColor; ctx.beginPath(); ctx.arc(ex, headY, skullR, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#0d0d0d'; ctx.beginPath(); ctx.ellipse(ex - skullR * 0.3, headY - skullR * 0.07, skullR * 0.24, skullR * 0.27, 0.15, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.ellipse(ex + skullR * 0.3, headY - skullR * 0.07, skullR * 0.24, skullR * 0.27, -0.15, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = 'rgba(220,30,30,' + eyeGlow + ')'; ctx.beginPath(); ctx.arc(ex - skullR * 0.3, headY - skullR * 0.07, skullR * 0.1, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + skullR * 0.3, headY - skullR * 0.07, skullR * 0.1, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#0d0d0d'; ctx.beginPath(); ctx.moveTo(ex, headY + skullR * 0.02); ctx.lineTo(ex - skullR * 0.15, headY + skullR * 0.26); ctx.lineTo(ex + skullR * 0.15, headY + skullR * 0.26); ctx.closePath(); ctx.fill();
        ctx.fillStyle = '#e8dcc8'; for (let t = -2; t <= 2; t++) ctx.fillRect(ex + t * 3.2 - 1.5, headY + skullR * 0.36, 3, 3.5);
    } else if (enemy.type === 'ghost') { ctx.fillStyle = flashColor; ctx.globalAlpha = 0.75; ctx.beginPath(); ctx.arc(ex, ey, s * 0.7, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex, ey - s * 0.4, s * 0.5, 0, Math.PI * 2); ctx.fill(); ctx.globalAlpha = 1; ctx.fillStyle = '#ddeeff'; ctx.beginPath(); ctx.arc(ex - 2, ey - s * 0.3, 2.5, 0, Math.PI * 2); ctx.arc(ex + 2, ey - s * 0.3, 2.5, 0, Math.PI * 2); ctx.fill(); }
    else if (enemy.type === 'demon') {
        const demonBody = flashColor, demonDark = enemy.hitFlash > 0 ? '#ffffff' : '#551010', eyeGlow = 0.7 + Math.sin(Date.now() / 300) * 0.3;
        ctx.fillStyle = demonBody; ctx.beginPath(); ctx.moveTo(ex - s * 0.42, ey - s * 0.25); ctx.quadraticCurveTo(ex - s * 0.4, ey - s * 0.55, ex - s * 0.05, ey - s * 0.55); ctx.lineTo(ex + s * 0.05, ey - s * 0.55); ctx.quadraticCurveTo(ex + s * 0.4, ey - s * 0.55, ex + s * 0.42, ey - s * 0.25); ctx.lineTo(ex + s * 0.5, ey + s * 0.5); ctx.lineTo(ex - s * 0.5, ey + s * 0.5); ctx.closePath(); ctx.fill();
        if (!enemy.hitFlash) { ctx.fillStyle = 'rgba(255,50,20,0.25)'; ctx.beginPath(); ctx.ellipse(ex - s * 0.1, ey - s * 0.05, s * 0.13, s * 0.18, 0, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.ellipse(ex + s * 0.1, ey - s * 0.05, s * 0.13, s * 0.18, 0, 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = demonDark; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(ex, ey - s * 0.25); ctx.lineTo(ex, ey + s * 0.2); ctx.stroke(); ctx.beginPath(); ctx.moveTo(ex - s * 0.12, ey - s * 0.15); ctx.quadraticCurveTo(ex, ey + s * 0.02, ex + s * 0.12, ey - s * 0.15); ctx.stroke(); }
        ctx.fillStyle = demonBody; ctx.beginPath(); ctx.moveTo(ex - s * 0.18, ey + s * 0.32); ctx.lineTo(ex - s * 0.32, ey + s * 0.68); ctx.lineTo(ex - s * 0.12, ey + s * 0.68); ctx.lineTo(ex - s * 0.04, ey + s * 0.32); ctx.closePath(); ctx.fill(); ctx.fillStyle = demonDark; ctx.beginPath(); ctx.arc(ex - s * 0.22, ey + s * 0.68, s * 0.11, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = demonBody; ctx.beginPath(); ctx.moveTo(ex + s * 0.18, ey + s * 0.32); ctx.lineTo(ex + s * 0.32, ey + s * 0.68); ctx.lineTo(ex + s * 0.12, ey + s * 0.68); ctx.lineTo(ex + s * 0.04, ey + s * 0.32); ctx.closePath(); ctx.fill(); ctx.fillStyle = demonDark; ctx.beginPath(); ctx.arc(ex + s * 0.22, ey + s * 0.68, s * 0.11, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = demonBody; ctx.beginPath(); ctx.moveTo(ex - s * 0.38, ey - s * 0.18); ctx.lineTo(ex - s * 0.55, ey + s * 0.08); ctx.lineTo(ex - s * 0.45, ey + s * 0.22); ctx.lineTo(ex - s * 0.28, ey + s * 0.05); ctx.closePath(); ctx.fill(); ctx.fillStyle = demonDark; for (let c = 0; c < 3; c++) { ctx.beginPath(); const clawX = ex - s * 0.5 + c * s * 0.07, clawY = ey + s * 0.18; ctx.moveTo(clawX, clawY); ctx.lineTo(clawX - s * 0.04, clawY + s * 0.13); ctx.lineTo(clawX + s * 0.04, clawY + s * 0.13); ctx.closePath(); ctx.fill(); }
        ctx.fillStyle = demonBody; ctx.beginPath(); ctx.moveTo(ex + s * 0.38, ey - s * 0.18); ctx.lineTo(ex + s * 0.55, ey + s * 0.08); ctx.lineTo(ex + s * 0.45, ey + s * 0.22); ctx.lineTo(ex + s * 0.28, ey + s * 0.05); ctx.closePath(); ctx.fill(); ctx.fillStyle = demonDark; for (let c = 0; c < 3; c++) { ctx.beginPath(); const clawX = ex + s * 0.43 + c * s * 0.07, clawY = ey + s * 0.18; ctx.moveTo(clawX, clawY); ctx.lineTo(clawX - s * 0.04, clawY + s * 0.13); ctx.lineTo(clawX + s * 0.04, clawY + s * 0.13); ctx.closePath(); ctx.fill(); }
        const headY = ey - s * 0.52; ctx.fillStyle = demonBody; ctx.beginPath(); ctx.moveTo(ex - s * 0.26, headY + s * 0.05); ctx.lineTo(ex - s * 0.22, headY - s * 0.32); ctx.lineTo(ex + s * 0.22, headY - s * 0.32); ctx.lineTo(ex + s * 0.26, headY + s * 0.05); ctx.lineTo(ex + s * 0.18, headY + s * 0.18); ctx.lineTo(ex - s * 0.18, headY + s * 0.18); ctx.closePath(); ctx.fill();
        ctx.fillStyle = demonDark; ctx.beginPath(); ctx.moveTo(ex - s * 0.17, headY - s * 0.26); ctx.quadraticCurveTo(ex - s * 0.36, headY - s * 0.62, ex - s * 0.12, headY - s * 0.7); ctx.quadraticCurveTo(ex - s * 0.04, headY - s * 0.45, ex - s * 0.07, headY - s * 0.22); ctx.closePath(); ctx.fill(); ctx.beginPath(); ctx.moveTo(ex + s * 0.17, headY - s * 0.26); ctx.quadraticCurveTo(ex + s * 0.36, headY - s * 0.62, ex + s * 0.12, headY - s * 0.7); ctx.quadraticCurveTo(ex + s * 0.04, headY - s * 0.45, ex + s * 0.07, headY - s * 0.22); ctx.closePath(); ctx.fill();
        ctx.fillStyle = 'rgba(255,200,20,' + eyeGlow + ')'; ctx.beginPath(); ctx.arc(ex - s * 0.09, headY - s * 0.03, s * 0.07, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.09, headY - s * 0.03, s * 0.07, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = '#000'; ctx.beginPath(); ctx.arc(ex - s * 0.09, headY - s * 0.03, s * 0.035, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.09, headY - s * 0.03, s * 0.035, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#1a0000'; ctx.beginPath(); ctx.arc(ex, headY + s * 0.08, s * 0.1, 0, Math.PI); ctx.fill(); ctx.fillStyle = '#f5efe0'; ctx.beginPath(); ctx.moveTo(ex - s * 0.07, headY + s * 0.08); ctx.lineTo(ex - s * 0.04, headY + s * 0.18); ctx.lineTo(ex - s * 0.01, headY + s * 0.08); ctx.closePath(); ctx.fill(); ctx.beginPath(); ctx.moveTo(ex + s * 0.07, headY + s * 0.08); ctx.lineTo(ex + s * 0.04, headY + s * 0.18); ctx.lineTo(ex + s * 0.01, headY + s * 0.08); ctx.closePath(); ctx.fill();
    } else if (enemy.type === 'brute') {
        const bruteBody = flashColor, bruteDark = enemy.hitFlash > 0 ? '#ffffff' : '#2a1a0a', bruteLight = enemy.hitFlash > 0 ? '#ffffff' : '#554433', eyeGlow = 0.65 + Math.sin(Date.now() / 500) * 0.25;
        ctx.fillStyle = bruteBody; ctx.beginPath(); ctx.moveTo(ex - s * 0.58, ey - s * 0.2); ctx.quadraticCurveTo(ex - s * 0.52, ey - s * 0.52, ex - s * 0.08, ey - s * 0.56); ctx.lineTo(ex + s * 0.08, ey - s * 0.56); ctx.quadraticCurveTo(ex + s * 0.52, ey - s * 0.52, ex + s * 0.58, ey - s * 0.2); ctx.lineTo(ex + s * 0.48, ey + s * 0.48); ctx.lineTo(ex - s * 0.48, ey + s * 0.48); ctx.closePath(); ctx.fill();
        if (!enemy.hitFlash) { ctx.strokeStyle = bruteDark; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(ex, ey - s * 0.28); ctx.lineTo(ex, ey + s * 0.18); ctx.stroke(); ctx.beginPath(); ctx.moveTo(ex - s * 0.14, ey - s * 0.18); ctx.quadraticCurveTo(ex, ey - s * 0.02, ex + s * 0.14, ey - s * 0.18); ctx.stroke(); ctx.beginPath(); ctx.moveTo(ex - s * 0.14, ey + s * 0.05); ctx.quadraticCurveTo(ex, ey + s * 0.18, ex + s * 0.14, ey + s * 0.05); ctx.stroke(); }
        ctx.fillStyle = bruteBody; ctx.beginPath(); ctx.moveTo(ex - s * 0.28, ey + s * 0.32); ctx.lineTo(ex - s * 0.38, ey + s * 0.66); ctx.lineTo(ex - s * 0.13, ey + s * 0.66); ctx.lineTo(ex - s * 0.08, ey + s * 0.32); ctx.closePath(); ctx.fill(); ctx.fillStyle = bruteDark; ctx.beginPath(); ctx.ellipse(ex - s * 0.25, ey + s * 0.66, s * 0.14, s * 0.07, 0, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = bruteBody; ctx.beginPath(); ctx.moveTo(ex + s * 0.28, ey + s * 0.32); ctx.lineTo(ex + s * 0.38, ey + s * 0.66); ctx.lineTo(ex + s * 0.13, ey + s * 0.66); ctx.lineTo(ex + s * 0.08, ey + s * 0.32); ctx.closePath(); ctx.fill(); ctx.fillStyle = bruteDark; ctx.beginPath(); ctx.ellipse(ex + s * 0.25, ey + s * 0.66, s * 0.14, s * 0.07, 0, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = bruteBody; ctx.beginPath(); ctx.moveTo(ex - s * 0.48, ey - s * 0.32); ctx.lineTo(ex - s * 0.62, ey + s * 0.06); ctx.lineTo(ex - s * 0.48, ey + s * 0.24); ctx.lineTo(ex - s * 0.33, ey - s * 0.05); ctx.closePath(); ctx.fill(); ctx.fillStyle = bruteDark; ctx.beginPath(); ctx.arc(ex - s * 0.53, ey + s * 0.2, s * 0.12, 0, Math.PI * 2); ctx.fill(); if (!enemy.hitFlash) { ctx.fillStyle = bruteLight; ctx.beginPath(); ctx.arc(ex - s * 0.56, ey + s * 0.17, s * 0.035, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex - s * 0.5, ey + s * 0.17, s * 0.035, 0, Math.PI * 2); ctx.fill(); }
        ctx.fillStyle = bruteBody; ctx.beginPath(); ctx.moveTo(ex + s * 0.48, ey - s * 0.32); ctx.lineTo(ex + s * 0.62, ey + s * 0.06); ctx.lineTo(ex + s * 0.48, ey + s * 0.24); ctx.lineTo(ex + s * 0.33, ey - s * 0.05); ctx.closePath(); ctx.fill(); ctx.fillStyle = bruteDark; ctx.beginPath(); ctx.arc(ex + s * 0.53, ey + s * 0.2, s * 0.12, 0, Math.PI * 2); ctx.fill(); if (!enemy.hitFlash) { ctx.fillStyle = bruteLight; ctx.beginPath(); ctx.arc(ex + s * 0.56, ey + s * 0.17, s * 0.035, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.5, ey + s * 0.17, s * 0.035, 0, Math.PI * 2); ctx.fill(); }
        const headY = ey - s * 0.6; ctx.fillStyle = bruteBody; ctx.beginPath(); ctx.ellipse(ex, headY + s * 0.04, s * 0.23, s * 0.2, 0, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = bruteDark; ctx.beginPath(); ctx.ellipse(ex, headY - s * 0.04, s * 0.26, s * 0.09, 0, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = 'rgba(255,120,20,' + eyeGlow + ')'; ctx.beginPath(); ctx.arc(ex - s * 0.07, headY + s * 0.01, s * 0.055, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.07, headY + s * 0.01, s * 0.055, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#110000'; ctx.beginPath(); ctx.arc(ex, headY + s * 0.12, s * 0.1, 0, Math.PI); ctx.fill(); ctx.fillStyle = '#ddd8c8'; for (let tp = 0; tp < 4; tp++) { const tx = ex + (tp - 1.5) * s * 0.04; ctx.fillRect(tx - s * 0.015, headY + s * 0.12, s * 0.03, s * (0.04 + (tp % 2) * 0.03)); }
        if (!enemy.hitFlash) { ctx.fillStyle = bruteDark; for (let sp = 0; sp < 3; sp++) { const spikeX = ex - s * 0.35 + sp * s * 0.35; ctx.beginPath(); ctx.moveTo(spikeX, ey - s * 0.46); ctx.lineTo(spikeX - s * 0.035, ey - s * 0.56); ctx.lineTo(spikeX + s * 0.035, ey - s * 0.51); ctx.closePath(); ctx.fill(); } }
    } else if (enemy.type === 'earthshaker') {
        const esBody = enemy.hitFlash > 0 ? '#ffffff' : '#5a4a3a', esDark = enemy.hitFlash > 0 ? '#ffffff' : '#2a1a0a', esCrack = enemy.hitFlash > 0 ? '#ffffff' : '#aa55ff', esGlow = 0.5 + Math.sin(Date.now() / 400) * 0.25;
        // Purple crack glow aura
        ctx.fillStyle = 'rgba(170,85,255,' + (0.2 * esGlow) + ')'; ctx.beginPath(); ctx.arc(ex, ey, s * 1.2, 0, Math.PI * 2); ctx.fill();
        // Shadow
        ctx.fillStyle = 'rgba(0,0,0,0.5)'; ctx.beginPath(); ctx.ellipse(ex, ey + s * 0.7, s * 0.85, s * 0.25, 0, 0, Math.PI * 2); ctx.fill();
        // Legs - thick stone pillars
        ctx.fillStyle = esDark; ctx.fillRect(ex - s * 0.45, ey - s * 0.05, s * 0.28, s * 0.75); ctx.fillRect(ex + s * 0.17, ey - s * 0.05, s * 0.28, s * 0.75);
        ctx.strokeStyle = esCrack; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(ex - s * 0.3, ey + s * 0.1); ctx.lineTo(ex - s * 0.38, ey + s * 0.55); ctx.stroke();
        // Arms - massive stone fists
        ctx.fillStyle = esBody; ctx.fillRect(ex - s * 0.75, ey - s * 0.4, s * 0.35, s * 0.55); ctx.fillRect(ex + s * 0.4, ey - s * 0.4, s * 0.35, s * 0.55);
        ctx.fillStyle = esDark; ctx.beginPath(); ctx.arc(ex - s * 0.58, ey + s * 0.2, s * 0.18, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.58, ey + s * 0.2, s * 0.18, 0, Math.PI * 2); ctx.fill();
        // Body - bulky stone torso
        ctx.fillStyle = esBody; ctx.beginPath(); ctx.moveTo(ex - s * 0.6, ey - s * 0.55); ctx.lineTo(ex + s * 0.6, ey - s * 0.55); ctx.lineTo(ex + s * 0.5, ey + s * 0.05); ctx.lineTo(ex - s * 0.5, ey + s * 0.05); ctx.closePath(); ctx.fill();
        // Chest cracks
        ctx.strokeStyle = esCrack; ctx.lineWidth = 1.5; ctx.globalAlpha = esGlow;
        ctx.beginPath(); ctx.moveTo(ex - s * 0.3, ey - s * 0.35); ctx.lineTo(ex - s * 0.1, ey - s * 0.05); ctx.lineTo(ex + s * 0.05, ey - s * 0.15); ctx.lineTo(ex + s * 0.2, ey + s * 0.05); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(ex + s * 0.3, ey - s * 0.4); ctx.lineTo(ex + s * 0.15, ey - s * 0.1); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(ex - s * 0.4, ey - s * 0.1); ctx.lineTo(ex - s * 0.25, ey - s * 0.3); ctx.stroke();
        ctx.globalAlpha = 1;
        // Head - angular stone
        ctx.fillStyle = esBody; ctx.beginPath(); ctx.moveTo(ex - s * 0.25, ey - s * 0.55); ctx.lineTo(ex - s * 0.35, ey - s * 0.85); ctx.lineTo(ex - s * 0.05, ey - s * 1.1); ctx.lineTo(ex + s * 0.25, ey - s * 0.85); ctx.lineTo(ex + s * 0.15, ey - s * 0.55); ctx.closePath(); ctx.fill();
        // Head cracks
        ctx.strokeStyle = esCrack; ctx.lineWidth = 1.2; ctx.globalAlpha = esGlow;
        ctx.beginPath(); ctx.moveTo(ex - s * 0.1, ey - s * 0.7); ctx.lineTo(ex + s * 0.05, ey - s * 0.6); ctx.lineTo(ex + s * 0.1, ey - s * 0.8); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(ex + s * 0.05, ey - s * 0.85); ctx.lineTo(ex - s * 0.1, ey - s * 0.95); ctx.stroke();
        ctx.globalAlpha = 1;
        // Glowing purple eyes
        ctx.fillStyle = 'rgba(170,85,255,' + esGlow + ')'; ctx.beginPath(); ctx.arc(ex - s * 0.1, ey - s * 0.78, s * 0.06, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.1, ey - s * 0.78, s * 0.06, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = 'rgba(255,255,255,' + (esGlow * 0.8) + ')'; ctx.beginPath(); ctx.arc(ex - s * 0.1, ey - s * 0.78, s * 0.025, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.arc(ex + s * 0.1, ey - s * 0.78, s * 0.025, 0, Math.PI * 2); ctx.fill();
        // Ground slam charging warning effect
        if (enemy.gsCharging) {
            const chargeProgress = enemy.gsChargeTimer / EARTHSHAKER_GROUND_SLAM_CHARGE;
            const warnAlpha = 0.35 + chargeProgress * 0.55;
            const warnPulse = 1 + Math.sin(Date.now() / 80) * 0.15;
            ctx.fillStyle = 'rgba(255,180,40,' + (warnAlpha * 0.5) + ')';
            ctx.beginPath(); ctx.arc(ex, ey, s * 2.2 * warnPulse, 0, Math.PI * 2); ctx.fill();
            ctx.strokeStyle = 'rgba(255,150,30,' + warnAlpha + ')'; ctx.lineWidth = 3;
            ctx.beginPath(); ctx.arc(ex, ey, s * 1.8 * warnPulse, 0, Math.PI * 2); ctx.stroke();
            // Warning text
            ctx.fillStyle = 'rgba(255,200,60,' + (warnAlpha * 0.9) + ')';
            ctx.font = 'bold 11px "Courier New", monospace'; ctx.textAlign = 'center';
            ctx.fillText('GROUND SLAM!', ex, ey - s * 2.5);
            ctx.textAlign = 'start';
        }
        // Rage visual indicator
        if (enemy.rageTriggered) {
            const ragePulse = 1 + Math.sin(Date.now() / 200) * 0.2;
            ctx.fillStyle = 'rgba(255,50,20,' + (0.15 * ragePulse) + ')';
            ctx.beginPath(); ctx.arc(ex, ey, s * 1.5 * ragePulse, 0, Math.PI * 2); ctx.fill();
            ctx.fillStyle = 'rgba(255,100,40,' + (0.08 * ragePulse) + ')';
            ctx.beginPath(); ctx.arc(ex, ey, s * 1.8 * ragePulse, 0, Math.PI * 2); ctx.fill();
        }
    }
    if (stunInd) { ctx.fillStyle = '#ffff88'; ctx.font = '10px monospace'; ctx.textAlign = 'center'; ctx.fillText('\u2726', ex, ey - s - 6); }
    if (!enemy.aggroed && enemy.type !== 'witch' && enemy.type !== 'earthshaker') { ctx.fillStyle = 'rgba(150,150,200,0.6)'; ctx.font = '8px monospace'; ctx.textAlign = 'center'; ctx.fillText('\u2026', ex, ey - s - 7); }
    if (!enemy.aggroed && enemy.type === 'witch') { ctx.fillStyle = 'rgba(200,150,255,0.7)'; ctx.font = '9px monospace'; ctx.textAlign = 'center'; ctx.fillText('\u263d', ex, ey - s - 8); }
    if (!enemy.aggroed && enemy.type === 'earthshaker') { ctx.fillStyle = 'rgba(200,150,100,0.7)'; ctx.font = '9px monospace'; ctx.textAlign = 'center'; ctx.fillText('\u2b24', ex, ey - s - 8); }
    const barW = s * 2, barH = 3, barY = ey - s - 4; ctx.fillStyle = '#200000'; ctx.fillRect(ex - barW / 2, barY, barW, barH); const barColor = enemy.type === 'witch' ? '#bb44dd' : enemy.type === 'devil' ? '#ee4422' : enemy.type === 'earthshaker' ? '#cc8844' : '#dd3333'; ctx.fillStyle = barColor; ctx.fillRect(ex - barW / 2, barY, barW * (enemy.health / enemy.maxHealth), barH);
    if (enemy.type === 'witch' && enemy.aggroed) { const devilsAlive = enemy.devilsAlive || 0; ctx.fillStyle = '#ff6644'; ctx.font = '8px monospace'; ctx.textAlign = 'center'; ctx.fillText('\ud83d\udc7f' + devilsAlive + '/' + enemy.maxDevils, ex, ey - s - 14); }
    if (enemy.type === 'earthshaker' && enemy.aggroed) { ctx.fillStyle = '#cc8844'; ctx.font = '8px monospace'; ctx.textAlign = 'center'; ctx.fillText((enemy.rageTriggered ? 'RAGE ' : '') + '\u2b24', ex, ey - s - 14); }
}

function renderWaveClear() { ctx.fillStyle = 'rgba(0,0,0,0.6)'; ctx.fillRect(0, 0, W, H); ctx.fillStyle = '#c9a23b'; ctx.font = 'bold 28px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('WAVE ' + wave + ' CLEAR!', W / 2, H / 2 - 20); ctx.fillStyle = '#ddd'; ctx.font = '16px "Courier New", monospace'; ctx.fillText('Tokens are flying to you!', W / 2, H / 2 + 15); ctx.fillText('Press SPACE or ENTER to enter the shop', W / 2, H / 2 + 45); }

function renderShop() {
    ctx.fillStyle = 'rgba(8,8,16,0.92)'; ctx.fillRect(0, 0, W, H); const ch = characters[selectedCharacter];
    ctx.fillStyle = '#c9a23b'; ctx.font = 'bold 24px "Courier New", monospace'; ctx.textAlign = 'center';
    if (sandboxMode) ctx.fillText('\u2692 SANDBOX SHOP \u2014 Start at Wave ' + wave, W / 2, 35);
    else ctx.fillText('\u2692 SHOP \u2014 After Wave ' + wave, W / 2, 35);
    const vitBonus = getVitalityBonus(), vitLabel = vitBonus > 0 ? ' (+' + vitBonus + ')' : ''; ctx.fillStyle = '#ddd'; ctx.font = '14px "Courier New", monospace'; ctx.fillText('Tokens: \u2726 ' + tokens + '    |    Character: ' + ch.name + '    |    HP: ' + player.maxHealth + vitLabel, W / 2, 58);
    const cardW = 200, cardH = 90, gapX = 20, gapY = 15, startX = 30, startY = 80;
    let primSpecLabel, primSpecDesc; if (selectedCharacter === 'sorcerer') { primSpecLabel = 'Homing Flames'; primSpecDesc = 'Fireballs curve to foes'; } else if (selectedCharacter === 'wizard') { primSpecLabel = 'Mana Leech'; primSpecDesc = 'Heal on primary hit'; } else { primSpecLabel = 'Bleeding Edge'; primSpecDesc = 'Bleed DoT on hit'; }
    const upgrades = [
        { id: 'prim_dmg', label: ch.primary.name + ' DMG', desc: '+25% damage/level', icon: '\u2694' }, { id: 'prim_spd', label: ch.primary.name + ' SPD', desc: '-18% cooldown/level', icon: '\u23f1' }, { id: 'prim_rng', label: ch.primary.name + ' RNG', desc: '+22% reach/level', icon: '\u2194' }, { id: 'prim_spec', label: primSpecLabel, desc: primSpecDesc, icon: '\u2726', prereq: 'prim_dmg' },
        { id: 'sec_dmg', label: ch.secondary.isSummon ? 'Skeleton Power' : ch.secondary.name + ' DMG', desc: ch.secondary.isSummon ? '+HP & damage/level' : '+28% damage/level', icon: '\ud83d\udca5' }, { id: 'sec_spd', label: ch.secondary.isSummon ? 'Rapid Summon' : ch.secondary.name + ' SPD', desc: '-18% cooldown/level', icon: '\u23f1' }, { id: 'sec_special', label: ch.secondary.specialName, desc: ch.secondary.specialDesc, icon: '\u2605', prereq: 'sec_dmg' }, { id: 'sec_aoe', label: ch.secondary.aoeName, desc: ch.secondary.aoeDesc, icon: '\u2b21', prereq: 'sec_spd' },
        { id: 'vit', label: 'Vitality', desc: '', icon: '\u2764', isVit: true }
    ];
    if (selectedCharacter === 'sorcerer') { upgrades.push({ id: 'skel_spd', label: 'Skeleton Speed', desc: 'Move: 110\u2192180\u2192250 / level', icon: '\ud83d\udca8' }, { id: 'minigun', label: 'Fireball Minigun', desc: 'Rapid-fire homing shots', icon: '\ud83d\udd2b', prereq: 'prim_spec', prereqLevel: 2, isMajor: true }, { id: 'railgun', label: 'Arcane Railgun', desc: 'Charge beam, pierces all', icon: '\u26a1', prereq: 'prim_dmg', prereqLevel: 5, isMajor: true }); if (hasMinigun()) upgrades.push({ id: 'minion_minigun', label: 'Skeleton Miniguns', desc: 'Summons get miniguns!', icon: '\ud83d\udc80', isMajor: true }); if (hasRailgun()) upgrades.push({ id: 'minion_railgun', label: 'Skeleton Railguns', desc: 'Summons charge & fire railguns!', icon: '\ud83d\udc80\u26a1', isMajor: true, isUltra: true }); if (selectedCharacter === 'sorcerer' && allUpgradesMaxed()) upgrades.push({ id: 'annihilator', label: 'THE ANNIHILATOR!', desc: 'Full-auto railgun! Overheats!', icon: '\ud83d\udca5', isMajor: true, isUltra: true }); }
    const shopButtons = [];
    for (let i = 0; i < upgrades.length; i++) { const col = i % 4, row = Math.floor(i / 4), cx2 = startX + col * (cardW + gapX), cy2 = startY + row * (cardH + gapY), upg = upgrades[i], level = playerUpgrades[upg.id] || 0, maxLevel = getUpgradeMaxLevel(upg.id), cost = getUpgradeCost(upg.id), canBuy = canBuyUpgrade(upg.id), isMaxed = level >= maxLevel, prereqLevel = upg.prereqLevel || 2, prereqMet = !upg.prereq || (playerUpgrades[upg.prereq] || 0) >= prereqLevel, isMajor = upg.isMajor || false, isUltra = upg.isUltra || false, isVit = upg.isVit || false; if (isUltra) { const pulse = 1 + Math.sin(Date.now() / 350 + i) * 0.15; ctx.fillStyle = isMaxed ? '#1a1218' : canBuy ? 'rgba(20,10,40,' + (0.55 * pulse) + ')' : '#12101a'; ctx.strokeStyle = isMaxed ? '#332244' : canBuy ? 'rgba(100,60,220,' + (0.9 * pulse) + ')' : '#221133'; ctx.lineWidth = 3; ctx.fillRect(cx2 - 2, cy2 - 2, cardW + 4, cardH + 4); } else if (isMajor) { const pulse = 1 + Math.sin(Date.now() / 500 + i) * 0.12; ctx.fillStyle = isMaxed ? '#1a1212' : canBuy ? 'rgba(40,10,10,' + (0.5 * pulse) + ')' : '#151212'; ctx.strokeStyle = isMaxed ? '#552222' : canBuy ? 'rgba(220,50,50,' + (0.8 * pulse) + ')' : '#442222'; ctx.lineWidth = 3; ctx.fillRect(cx2 - 1, cy2 - 1, cardW + 2, cardH + 2); } ctx.fillStyle = isMaxed ? '#1a1a1a' : canBuy ? '#1a1a2e' : '#151520'; if (isVit) { ctx.strokeStyle = isMaxed ? '#442222' : canBuy ? '#cc6655' : '#332222'; ctx.lineWidth = isMaxed ? 2 : (canBuy ? 3 : 2); } else if (isMajor || isUltra) { ctx.strokeStyle = isUltra ? '#5533aa' : '#c9a23b'; ctx.lineWidth = isUltra ? 2 : 2; } else { ctx.strokeStyle = isMaxed ? '#444' : canBuy ? '#c9a23b' : '#333'; ctx.lineWidth = 2; } ctx.fillRect(cx2, cy2, cardW, cardH); ctx.strokeRect(cx2, cy2, cardW, cardH); ctx.fillStyle = '#fff'; ctx.font = '20px monospace'; ctx.textAlign = 'left'; ctx.fillText(upg.icon, cx2 + 8, cy2 + 28); ctx.fillStyle = isMaxed ? '#666' : (isVit ? '#ffaa99' : (isUltra ? '#ccbbff' : (isMajor ? '#ffaaaa' : '#eee'))); ctx.font = 'bold 12px "Courier New", monospace'; ctx.fillText(upg.label, cx2 + 36, cy2 + 22); ctx.fillStyle = '#aaa'; ctx.font = '10px "Courier New", monospace'; ctx.fillText('Lv ' + level + '/' + maxLevel, cx2 + 36, cy2 + 38); let descText = upg.desc; if (isVit) { const vitBonuses = [0, 10, 20, 40], nextLevel = Math.min(level + 1, maxLevel), nextBonus = vitBonuses[nextLevel] * wave, curBonus = vitBonuses[level] * wave; if (isMaxed) descText = '+' + curBonus + ' HP (\u00d7Wave ' + wave + ')'; else descText = '+' + nextBonus + ' HP at Wave ' + wave; } ctx.fillStyle = '#999'; ctx.font = '9px "Courier New", monospace'; ctx.fillText(descText, cx2 + 8, cy2 + 56); const btnX = cx2 + cardW - 80, btnY = cy2 + cardH - 28, btnW = 72, btnH = 22; if (isMaxed) { ctx.fillStyle = '#222'; ctx.fillRect(btnX, btnY, btnW, btnH); ctx.strokeStyle = isVit ? '#442222' : (isUltra ? '#332244' : (isMajor ? '#442222' : '#333')); ctx.lineWidth = 1; ctx.strokeRect(btnX, btnY, btnW, btnH); ctx.fillStyle = '#777'; ctx.font = '10px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('MAXED', btnX + btnW / 2, btnY + 15); } else if (!prereqMet) { ctx.fillStyle = '#1a1a2a'; ctx.fillRect(btnX, btnY, btnW, btnH); ctx.strokeStyle = '#334'; ctx.lineWidth = 1; ctx.strokeRect(btnX, btnY, btnW, btnH); ctx.fillStyle = '#667'; ctx.font = '9px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('REQ Lv' + prereqLevel, btnX + btnW / 2, btnY + 10); ctx.fillText(upg.prereq, btnX + btnW / 2, btnY + 20); } else if (!canBuy) { ctx.fillStyle = '#222'; ctx.fillRect(btnX, btnY, btnW, btnH); ctx.strokeStyle = '#444'; ctx.lineWidth = 1; ctx.strokeRect(btnX, btnY, btnW, btnH); ctx.fillStyle = '#888'; ctx.font = '10px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('\u2726' + cost, btnX + btnW / 2, btnY + 15); } else { ctx.fillStyle = '#1a3a1a'; ctx.fillRect(btnX, btnY, btnW, btnH); ctx.strokeStyle = '#44cc44'; ctx.lineWidth = 2; ctx.strokeRect(btnX, btnY, btnW, btnH); ctx.fillStyle = '#44ff55'; ctx.font = 'bold 10px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('\u2726' + cost + ' BUY', btnX + btnW / 2, btnY + 15); shopButtons.push({ x: btnX, y: btnY, w: btnW, h: btnH, id: upg.id }); } ctx.textAlign = 'start';
    // Info button for Annihilator lore
    if (upg.id === 'annihilator') {
        const infoSize = 18, infoX = cx2 + cardW - infoSize - 4, infoY = cy2 + 4;
        ctx.fillStyle = '#1a1a2e'; ctx.fillRect(infoX, infoY, infoSize, infoSize);
        ctx.strokeStyle = '#6644aa'; ctx.lineWidth = 1.5; ctx.strokeRect(infoX, infoY, infoSize, infoSize);
        ctx.fillStyle = '#bb99ee'; ctx.font = 'bold 13px "Courier New", monospace'; ctx.textAlign = 'center';
        ctx.fillText('i', infoX + infoSize / 2, infoY + infoSize / 2 + 5);
        ctx.textAlign = 'start';
        window._annihilatorInfoBtn = { x: infoX, y: infoY, w: infoSize, h: infoSize };
    }
    }
    // Wave selector for sandbox mode
    if (sandboxMode) {
        const wsY = H - 110, wsH = 34;
        const decW = 38, lblW = 90, incW = 38, wsTotalW = decW + lblW + incW;
        const wsX = W / 2 - wsTotalW / 2;
        // Decrease button
        const decX = wsX, decY = wsY;
        ctx.fillStyle = wave <= 1 ? '#1a1a1a' : '#1a1a2e'; ctx.strokeStyle = wave <= 1 ? '#333' : '#4466aa'; ctx.lineWidth = 2;
        ctx.fillRect(decX, decY, decW, wsH); ctx.strokeRect(decX, decY, decW, wsH);
        ctx.fillStyle = wave <= 1 ? '#555' : '#88aacc'; ctx.font = 'bold 20px "Courier New", monospace'; ctx.textAlign = 'center';
        ctx.fillText('\u25c0', decX + decW / 2, decY + wsH / 2 + 7);
        if (wave > 1) shopButtons.push({ x: decX, y: decY, w: decW, h: wsH, id: 'wave_down' });
        // Wave label
        const lblX = wsX + decW;
        ctx.fillStyle = '#12121e'; ctx.strokeStyle = '#c9a23b'; ctx.lineWidth = 2;
        ctx.fillRect(lblX, decY, lblW, wsH); ctx.strokeRect(lblX, decY, lblW, wsH);
        ctx.fillStyle = '#ffcc44'; ctx.font = 'bold 15px "Courier New", monospace';
        ctx.fillText('WAVE ' + wave, lblX + lblW / 2, decY + wsH / 2 + 5);
        // Increase button
        const incX = wsX + decW + lblW;
        ctx.fillStyle = '#1a1a2e'; ctx.strokeStyle = '#4466aa'; ctx.lineWidth = 2;
        ctx.fillRect(incX, decY, incW, wsH); ctx.strokeRect(incX, decY, incW, wsH);
        ctx.fillStyle = '#88aacc'; ctx.font = 'bold 20px "Courier New", monospace';
        ctx.fillText('\u25b6', incX + incW / 2, decY + wsH / 2 + 7);
        shopButtons.push({ x: incX, y: decY, w: incW, h: wsH, id: 'wave_up' });
    }
    const nwX = W / 2 - 90, nwY = H - 55, nwW = 180, nwH = 36; ctx.fillStyle = '#2a3a2a'; ctx.strokeStyle = '#5a8a3a'; ctx.lineWidth = 2; ctx.fillRect(nwX, nwY, nwW, nwH); ctx.strokeRect(nwX, nwY, nwW, nwH); ctx.fillStyle = '#ccffaa'; ctx.font = 'bold 16px "Courier New", monospace'; ctx.textAlign = 'center';
    if (sandboxMode) ctx.fillText('\u25b6 START WAVE \u25b6', W / 2, nwY + 25);
    else ctx.fillText('\u25b6 NEXT WAVE \u25b6', W / 2, nwY + 25);
    ctx.textAlign = 'start'; shopButtons.push({ x: nwX, y: nwY, w: nwW, h: nwH, id: 'next_wave' }); window._shopButtons = shopButtons;
    // Annihilator lore dialog
    if (showAnnihilatorLore) renderAnnihilatorLore();
}

function renderAnnihilatorLore() {
    // Dim overlay
    ctx.fillStyle = 'rgba(0,0,0,0.82)';
    ctx.fillRect(0, 0, W, H);
    
    // Dialog box
    const dlgW = 620, dlgH = 460, dlgX = (W - dlgW) / 2, dlgY = (H - dlgH) / 2;
    ctx.fillStyle = '#0d0d1a';
    ctx.strokeStyle = '#ff4444';
    ctx.lineWidth = 2;
    ctx.fillRect(dlgX, dlgY, dlgW, dlgH);
    ctx.strokeRect(dlgX, dlgY, dlgW, dlgH);
    
    // Inner glow border
    ctx.strokeStyle = 'rgba(255,68,68,0.25)';
    ctx.lineWidth = 4;
    ctx.strokeRect(dlgX + 4, dlgY + 4, dlgW - 8, dlgH - 8);
    
    // Title
    ctx.fillStyle = '#ff4444';
    ctx.font = 'bold 22px "Courier New", monospace';
    ctx.textAlign = 'center';
    ctx.fillText('\u2620  THE ANNIHILATOR  \u2620', dlgX + dlgW / 2, dlgY + 35);
    
    // Decorative line
    ctx.strokeStyle = '#ff4444';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(dlgX + 30, dlgY + 48);
    ctx.lineTo(dlgX + dlgW - 30, dlgY + 48);
    ctx.stroke();
    
    // Story text - wrapped
    const storyLines = [
        'Long before the Darkhold Arena was built, the Sorcerer Kings',
        'fought a war against something they could not kill.',
        '',
        'The Void.',
        '',
        'No army could stop it. No spell could contain it. Entire',
        'kingdoms vanished into black storms that consumed stone,',
        'steel, flesh, and soul alike.',
        '',
        'In desperation, the Archmages gathered beneath the ruins',
        'of Blackspire and forged a weapon that violated every law',
        'of magic.',
        '',
        'They fused a Railgun powered by starfire with a demonic',
        'war engine powered by stolen souls.',
        '',
        'The result was The Annihilator.',
        '',
        'The weapon did not fire bullets.',
        'It did not fire spells.',
        'It fired concentrated tears in reality itself.',
        '',
        'Each shot ripped open a microscopic rift into the Void,',
        'unleashing enough force to erase anything in its path.',
        'Mountains were split in half. Castles disappeared in',
        'moments. Entire battlefields were reduced to smoking glass.',
        '',
        'The weapon was so powerful that even its creators could',
        'not wield it safely.',
        '',
        'As The Annihilator fired, the weapon consumed more energy',
        'than its frame could contain. Its barrels glowed white-hot,',
        'its runes cracked, and reality itself began to distort',
        'around the wielder. If pushed too far, the weapon',
        'threatened to destroy its owner along with everything',
        'nearby.',
        '',
        'The Archmages sealed it away beneath the Darkhold and',
        'erased its existence from every known record.',
        '',
        'For centuries it remained hidden.',
        'Forgotten.',
        'Waiting.',
        '',
        'Now, after mastering every weapon, every spell, every',
        'upgrade, and every forbidden art known to the Sorcerers,',
        'you have become powerful enough to uncover its resting',
        'place.',
        '',
        'The warnings carved into its prison read:',
        '',
        '\u201cThis is not a weapon.',
        'It is the last mistake of the First Age.\u201d',
        '',
        'And yet...',
        'You picked it up anyway.'
    ];
    
    const lineHeight = 13;
    const textAreaY = dlgY + 62;
    const textAreaH = dlgH - 62 - 55; // from below title to above close button
    const totalTextH = storyLines.length * lineHeight;
    const maxScroll = Math.max(0, totalTextH - textAreaH);
    
    // Clamp scroll
    annihilatorLoreScroll = Math.max(0, Math.min(annihilatorLoreScroll, maxScroll));
    
    // Clip text area
    ctx.save();
    ctx.beginPath();
    ctx.rect(dlgX + 10, textAreaY, dlgW - 38, textAreaH);
    ctx.clip();
    
    ctx.fillStyle = '#ccbbbb';
    ctx.font = '11px "Courier New", monospace';
    ctx.textAlign = 'center';
    
    const scrollOffset = annihilatorLoreScroll;
    for (let i = 0; i < storyLines.length; i++) {
        const lineY = textAreaY + i * lineHeight - scrollOffset;
        if (lineY < textAreaY - lineHeight || lineY > textAreaY + textAreaH + lineHeight) continue;
        if (storyLines[i] === '') continue;
        ctx.fillText(storyLines[i], dlgX + dlgW / 2 - 12, lineY);
    }
    
    ctx.restore();
    
    // Scroll bar (only if content overflows)
    if (maxScroll > 0) {
        const sbX = dlgX + dlgW - 24;
        const sbY = textAreaY;
        const sbW = 10;
        const sbH = textAreaH;
        
        // Track background
        ctx.fillStyle = '#1a1a28';
        ctx.fillRect(sbX, sbY, sbW, sbH);
        ctx.strokeStyle = '#333';
        ctx.lineWidth = 1;
        ctx.strokeRect(sbX, sbY, sbW, sbH);
        
        // Thumb
        const thumbH = Math.max(28, (textAreaH / totalTextH) * sbH);
        const thumbY = sbY + (annihilatorLoreScroll / maxScroll) * (sbH - thumbH);
        ctx.fillStyle = '#553333';
        ctx.strokeStyle = '#994444';
        ctx.lineWidth = 1;
        ctx.fillRect(sbX + 1, thumbY, sbW - 2, thumbH);
        ctx.strokeRect(sbX + 1, thumbY, sbW - 2, thumbH);
        // Thumb grip lines
        ctx.strokeStyle = '#883333';
        ctx.lineWidth = 1;
        const gripMidY = thumbY + thumbH / 2;
        for (let g = -1; g <= 1; g++) {
            ctx.beginPath();
            ctx.moveTo(sbX + 3, gripMidY + g * 5);
            ctx.lineTo(sbX + sbW - 3, gripMidY + g * 5);
            ctx.stroke();
        }
        
        // Store scrollbar rect for click/drag handling
        window._annihilatorLoreScrollbar = { x: sbX, y: sbY, w: sbW, h: sbH, thumbY: thumbY, thumbH: thumbH, maxScroll: maxScroll };
    } else {
        window._annihilatorLoreScrollbar = null;
    }
    
    // Close button
    const closeW = 120, closeH = 32;
    const closeX = dlgX + dlgW / 2 - closeW / 2;
    const closeY = dlgY + dlgH - closeH - 16;
    ctx.fillStyle = '#2a1a1a';
    ctx.strokeStyle = '#ff4444';
    ctx.lineWidth = 2;
    ctx.fillRect(closeX, closeY, closeW, closeH);
    ctx.strokeRect(closeX, closeY, closeW, closeH);
    ctx.fillStyle = '#ff8888';
    ctx.font = 'bold 13px "Courier New", monospace';
    ctx.textAlign = 'center';
    ctx.fillText('CLOSE', closeX + closeW / 2, closeY + 22);
    ctx.textAlign = 'start';
    
    window._annihilatorLoreCloseBtn = { x: closeX, y: closeY, w: closeW, h: closeH };
}

function renderCharacterSelect() {
    ctx.fillStyle = '#0a0a14'; ctx.fillRect(0, 0, W, H); ctx.fillStyle = '#c9a23b'; ctx.font = 'bold 32px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('DARKHOLD ARENA', W / 2, 55); ctx.fillStyle = '#888'; ctx.font = '14px "Courier New", monospace'; ctx.fillText('Choose Your Champion', W / 2, 80);
    const charKeys = ['sorcerer', 'wizard', 'knight'], cardW = 240, cardH = 320, totalW = cardW * 3 + 60, startX2 = (W - totalW) / 2, cardY = 110, selectButtons = [];
    for (let i = 0; i < charKeys.length; i++) { const key = charKeys[i], ch = characters[key], cx3 = startX2 + i * (cardW + 30), isAvailable = ch.available !== false; ctx.fillStyle = isAvailable ? '#12121e' : '#0e0e14'; ctx.strokeStyle = isAvailable ? (selectedCharacter === key ? '#c9a23b' : '#333') : '#222'; ctx.lineWidth = selectedCharacter === key && isAvailable ? 3 : 1; ctx.fillRect(cx3, cardY, cardW, cardH); ctx.strokeRect(cx3, cardY, cardW, cardH); if (!isAvailable) { ctx.fillStyle = 'rgba(0,0,0,0.45)'; ctx.fillRect(cx3, cardY, cardW, cardH); } const previewX = cx3 + cardW / 2, previewY = cardY + 70; ctx.fillStyle = isAvailable ? ch.robeColor : '#3a3a40'; ctx.fillRect(previewX - 18, previewY - 10, 36, 30); ctx.fillStyle = isAvailable ? ch.skinColor : '#888880'; ctx.beginPath(); ctx.arc(previewX, previewY - 16, 13, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = isAvailable ? ch.color : '#555555'; if (key === 'knight') { ctx.fillRect(previewX - 12, previewY - 32, 24, 13); } else { ctx.beginPath(); ctx.moveTo(previewX, previewY - 38); ctx.lineTo(previewX - 14, previewY - 16); ctx.lineTo(previewX + 14, previewY - 16); ctx.closePath(); ctx.fill(); } if (key === 'sorcerer') { ctx.fillStyle = '#ff8844'; ctx.beginPath(); ctx.arc(previewX + 20, previewY - 5, 5, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = 'rgba(255,120,40,0.35)'; ctx.beginPath(); ctx.arc(previewX + 20, previewY - 5, 8, 0, Math.PI * 2); ctx.fill(); } else { ctx.strokeStyle = isAvailable ? '#d0c8b8' : '#555'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(previewX, previewY); ctx.lineTo(previewX + 20, previewY - 5); ctx.stroke(); } ctx.fillStyle = isAvailable ? '#fff' : '#666'; ctx.font = 'bold 18px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText(ch.name, cx3 + cardW / 2, cardY + 120); ctx.fillStyle = isAvailable ? '#aaa' : '#555'; ctx.font = '11px "Courier New", monospace'; const words = ch.desc.split(' '); let line = '', lineY = cardY + 145; for (const word of words) { if ((line + ' ' + word).length > 30) { ctx.fillText(line.trim(), cx3 + cardW / 2, lineY); lineY += 16; line = word; } else line += ' ' + word; } ctx.fillText(line.trim(), cx3 + cardW / 2, lineY); const statY = lineY + 25; ctx.fillStyle = isAvailable ? '#ccc' : '#555'; ctx.font = 'bold 11px "Courier New", monospace'; ctx.fillText('PRIMARY', cx3 + cardW / 2, statY); ctx.fillStyle = isAvailable ? '#bbb' : '#555'; ctx.font = '10px "Courier New", monospace'; ctx.fillText(ch.primary.name + ': ' + ch.primary.damage + ' dmg', cx3 + cardW / 2, statY + 16); ctx.fillText(ch.primary.isRanged ? 'Ranged projectile' : 'Melee swing', cx3 + cardW / 2, statY + 30); ctx.fillStyle = isAvailable ? '#ccc' : '#555'; ctx.font = 'bold 11px "Courier New", monospace'; ctx.fillText('SECONDARY', cx3 + cardW / 2, statY + 52); ctx.fillStyle = isAvailable ? '#bbb' : '#555'; ctx.font = '10px "Courier New", monospace'; if (ch.secondary.isSummon) { ctx.fillText(ch.secondary.name + ': summons skeletons', cx3 + cardW / 2, statY + 68); ctx.fillText('15s lifespan | Cooldown: ' + ch.secondary.cooldown + 's', cx3 + cardW / 2, statY + 82); } else if (ch.secondary.isRanged) { ctx.fillText(ch.secondary.name + ': ' + ch.secondary.damage + ' dmg', cx3 + cardW / 2, statY + 68); ctx.fillText('Ranged | Cooldown: ' + ch.secondary.cooldown + 's', cx3 + cardW / 2, statY + 82); } else { ctx.fillText(ch.secondary.name + ': ' + ch.secondary.damage + ' dmg', cx3 + cardW / 2, statY + 68); ctx.fillText('Melee AoE | Cooldown: ' + ch.secondary.cooldown + 's', cx3 + cardW / 2, statY + 82); } if (!isAvailable) { ctx.fillStyle = 'rgba(0,0,0,0.55)'; ctx.fillRect(cx3 + 10, cardY + 115, cardW - 20, 90); ctx.fillStyle = '#cc4444'; ctx.font = 'bold 14px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('NOT CURRENTLY', cx3 + cardW / 2, cardY + 148); ctx.fillText('AVAILABLE', cx3 + cardW / 2, cardY + 166); } const btnX2 = cx3 + cardW / 2 - 60, btnY2 = cardY + cardH - 45; if (isAvailable) { ctx.fillStyle = key === selectedCharacter ? '#4a6a2a' : '#2a3a1a'; ctx.strokeStyle = key === selectedCharacter ? '#aadd66' : '#555'; ctx.lineWidth = 2; ctx.fillRect(btnX2, btnY2, 120, 32); ctx.strokeRect(btnX2, btnY2, 120, 32); ctx.fillStyle = '#fff'; ctx.font = 'bold 13px "Courier New", monospace'; ctx.fillText(key === selectedCharacter ? '\u2713 SELECTED' : 'SELECT', cx3 + cardW / 2, btnY2 + 22); } else { ctx.fillStyle = '#1a1a1a'; ctx.strokeStyle = '#333'; ctx.lineWidth = 1; ctx.fillRect(btnX2, btnY2, 120, 32); ctx.strokeRect(btnX2, btnY2, 120, 32); ctx.fillStyle = '#555'; ctx.font = 'bold 13px "Courier New", monospace'; ctx.fillText('UNAVAILABLE', cx3 + cardW / 2, btnY2 + 22); } if (isAvailable) selectButtons.push({ x: btnX2, y: btnY2, w: 120, h: 32, key }); }
    const startBtnX2 = W / 2 - 100, startBtnY2 = cardY + cardH + 25; ctx.fillStyle = '#c9a23b'; ctx.fillRect(startBtnX2, startBtnY2, 200, 44); ctx.fillStyle = '#000'; ctx.font = 'bold 18px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('\u2694  ENTER THE ARENA  \u2694', W / 2, startBtnY2 + 30); ctx.textAlign = 'start'; selectButtons.push({ x: startBtnX2, y: startBtnY2, w: 200, h: 44, key: 'start' }); window._selectButtons = selectButtons; ctx.fillStyle = '#666'; ctx.font = '11px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('WASD/Arrows: Move | Mouse: Aim | LMB: Primary | RMB: Secondary | SPACE/Q: Dash', W / 2, startBtnY2 + 65); ctx.fillText('1/2: Swap weapons (Sorcerer) | P: Pause | Walls are destructible', W / 2, startBtnY2 + 82); ctx.textAlign = 'start';

    // Test button - bottom right corner, grants 10k tokens for testing
    const testBtnSize = 24, testBtnX = W - testBtnSize - 12, testBtnY = H - testBtnSize - 12;
    ctx.fillStyle = '#1a1a2e'; ctx.fillRect(testBtnX, testBtnY, testBtnSize, testBtnSize);
    ctx.strokeStyle = '#446688'; ctx.lineWidth = 1.5; ctx.strokeRect(testBtnX, testBtnY, testBtnSize, testBtnSize);
    ctx.fillStyle = '#88aacc'; ctx.font = '10px "Courier New", monospace'; ctx.textAlign = 'center';
    ctx.fillText('T', testBtnX + testBtnSize / 2, testBtnY + testBtnSize / 2 + 4);
    ctx.textAlign = 'start';
    window._testButton = { x: testBtnX, y: testBtnY, w: testBtnSize, h: testBtnSize };
}

function renderGameOver() { ctx.fillStyle = 'rgba(10,0,0,0.8)'; ctx.fillRect(0, 0, W, H); ctx.fillStyle = '#cc2222'; ctx.font = 'bold 36px "Courier New", monospace'; ctx.textAlign = 'center'; ctx.fillText('YOU HAVE FALLEN', W / 2, H / 2 - 30); ctx.fillStyle = '#ddd'; ctx.font = '18px "Courier New", monospace'; ctx.fillText('Reached Wave ' + wave, W / 2, H / 2 + 15); ctx.fillText('Tokens collected: \u2726 ' + tokens, W / 2, H / 2 + 40); ctx.fillStyle = '#c9a23b'; ctx.font = '14px "Courier New", monospace'; ctx.fillText('Click anywhere to return to character select', W / 2, H / 2 + 75); ctx.textAlign = 'start'; }
