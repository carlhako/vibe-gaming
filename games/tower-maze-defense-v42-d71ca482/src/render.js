'use strict';

// --- DRAWING FUNCTIONS ---
function drawCrossbow(ctx, r, level) {
    const s = r * 0.85;
    ctx.fillStyle = '#3d2b1f'; ctx.fillRect(-s * 0.7, s * 0.35, s * 1.4, s * 0.35);
    ctx.strokeStyle = '#5a3d2b'; ctx.lineWidth = 1; ctx.strokeRect(-s * 0.7, s * 0.35, s * 1.4, s * 0.35);
    ctx.strokeStyle = level >= 1 ? '#c9a96e' : '#8b6914';
    ctx.lineWidth = 2 + level * 0.5;
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.75, -Math.PI * 0.45, Math.PI * 0.45);
    ctx.stroke();
    ctx.strokeStyle = level >= 2 ? '#e0c080' : '#a07820';
    ctx.lineWidth = 1.2 + level * 0.3;
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.6, -Math.PI * 0.38, Math.PI * 0.38);
    ctx.stroke();
    ctx.strokeStyle = '#ccc'; ctx.lineWidth = 0.8;
    ctx.beginPath(); ctx.moveTo(s * 0.53, -s * 0.53); ctx.lineTo(s * 0.53, s * 0.53); ctx.stroke();
    ctx.fillStyle = '#ddd'; ctx.fillRect(s * 0.1, -1.2, s * 1.0, 2.4);
    ctx.fillStyle = '#e74c3c'; ctx.fillRect(s * 0.95, -2, s * 0.2, 4);
    if (level >= 1) { ctx.fillStyle = '#f1c40f'; ctx.fillRect(-s * 0.72, s * 0.3, s * 1.44, 1.5); }
    if (level >= 2) {
        ctx.fillStyle = '#f39c12';
        ctx.beginPath(); ctx.arc(0, 0, s * 0.15, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = '#f1c40f'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(0, 0, s * 0.2, 0, Math.PI * 2); ctx.stroke();
    }
}

function drawCannon(ctx, r, level) {
    const s = r * 0.85;
    ctx.fillStyle = '#3a2a1a';
    ctx.fillRect(-s * 0.55, s * 0.25, s * 0.28, s * 0.35);
    ctx.fillRect(s * 0.27, s * 0.25, s * 0.28, s * 0.35);
    ctx.strokeStyle = '#5a3d2b'; ctx.lineWidth = 1;
    ctx.strokeRect(-s * 0.55, s * 0.25, s * 0.28, s * 0.35);
    ctx.strokeRect(s * 0.27, s * 0.25, s * 0.28, s * 0.35);
    ctx.fillStyle = '#4a3020';
    ctx.beginPath(); ctx.arc(-s * 0.41, s * 0.5, s * 0.2, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(s * 0.41, s * 0.5, s * 0.2, 0, Math.PI * 2); ctx.fill();
    const bw = 3.5 + level * 1.2;
    const bl = s * 1.1 + level * 0.15;
    ctx.fillStyle = level >= 2 ? '#5a4a3a' : '#4a3828';
    ctx.fillRect(-s * 0.1, -bw, bl, bw * 2);
    ctx.strokeStyle = '#6a5040'; ctx.lineWidth = 1.2;
    ctx.strokeRect(-s * 0.1, -bw, bl, bw * 2);
    ctx.fillStyle = level >= 1 ? '#c9a96e' : '#7a6040';
    ctx.fillRect(s * 0.25, -bw - 1, 2.5, bw * 2 + 2);
    ctx.fillRect(s * 0.65, -bw - 1, 2.5, bw * 2 + 2);
    ctx.fillStyle = '#333';
    ctx.beginPath(); ctx.arc(s * 1.0, 0, bw * 0.7, 0, Math.PI * 2); ctx.fill();
    if (level >= 2) { ctx.fillStyle = '#f1c40f'; ctx.fillRect(-s * 0.05, -1, s * 0.3, 2); }
}

function drawIceCrystal(ctx, r, level) {
    const s = r * 0.9;
    ctx.fillStyle = level >= 2 ? 'rgba(133,193,233,0.9)' : 'rgba(160,210,240,0.85)';
    ctx.beginPath();
    ctx.moveTo(0, -s * 1.1);
    ctx.lineTo(s * 0.55, -s * 0.3);
    ctx.lineTo(s * 0.55, s * 0.5);
    ctx.lineTo(0, s * 0.9);
    ctx.lineTo(-s * 0.55, s * 0.5);
    ctx.lineTo(-s * 0.55, -s * 0.3);
    ctx.closePath(); ctx.fill();
    ctx.strokeStyle = 'rgba(200,230,255,0.9)'; ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.strokeStyle = 'rgba(255,255,255,0.5)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, -s * 0.5); ctx.lineTo(s * 0.3, s * 0.1); ctx.lineTo(-s * 0.3, s * 0.1); ctx.closePath(); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, s * 0.1); ctx.lineTo(s * 0.3, s * 0.55); ctx.lineTo(-s * 0.3, s * 0.55); ctx.closePath(); ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.beginPath(); ctx.arc(0, -s * 0.25, 1.5, 0, Math.PI * 2); ctx.fill();
    if (level >= 1) {
        ctx.fillStyle = 'rgba(180,220,250,0.7)';
        ctx.beginPath(); ctx.moveTo(s * 0.55, -s * 0.3); ctx.lineTo(s * 0.85, s * 0.0); ctx.lineTo(s * 0.55, s * 0.5); ctx.closePath(); ctx.fill();
        ctx.beginPath(); ctx.moveTo(-s * 0.55, -s * 0.3); ctx.lineTo(-s * 0.85, s * 0.0); ctx.lineTo(-s * 0.55, s * 0.5); ctx.closePath(); ctx.fill();
    }
    if (level >= 2) {
        ctx.fillStyle = 'rgba(220,240,255,0.6)';
        ctx.beginPath(); ctx.moveTo(0, -s * 1.1); ctx.lineTo(s * 0.2, -s * 0.7); ctx.lineTo(-s * 0.2, -s * 0.7); ctx.closePath(); ctx.fill();
    }
}

function drawLaserTurret(ctx, r, level) {
    const s = r * 0.85;
    ctx.fillStyle = '#2a3040'; ctx.fillRect(-s * 0.6, s * 0.2, s * 1.2, s * 0.4);
    ctx.strokeStyle = '#4a5060'; ctx.lineWidth = 1; ctx.strokeRect(-s * 0.6, s * 0.2, s * 1.2, s * 0.4);
    ctx.fillStyle = level >= 2 ? '#3a3040' : '#2a2a3a';
    ctx.beginPath(); ctx.arc(0, -s * 0.05, s * 0.35, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = '#5a5a7a'; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.arc(0, -s * 0.05, s * 0.35, 0, Math.PI * 2); ctx.stroke();
    const glowColor = level >= 1 ? '#ffd700' : '#f1c40f';
    ctx.fillStyle = glowColor;
    ctx.beginPath(); ctx.arc(0, -s * 0.05, s * 0.15, 0, Math.PI * 2); ctx.fill();
    ctx.shadowColor = glowColor; ctx.shadowBlur = 5 + level * 3;
    ctx.beginPath(); ctx.arc(0, -s * 0.05, s * 0.1, 0, Math.PI * 2); ctx.fill();
    ctx.shadowBlur = 0;
    const bl = s * 0.75 + level * 0.2;
    ctx.fillStyle = '#3a3a4a';
    ctx.fillRect(s * 0.15, -s * 0.12 - 1, bl, s * 0.24 + 2);
    ctx.strokeStyle = '#5a5a6a'; ctx.lineWidth = 1;
    ctx.strokeRect(s * 0.15, -s * 0.12 - 1, bl, s * 0.24 + 2);
    ctx.fillStyle = glowColor;
    ctx.fillRect(s * 0.15 + bl - 2, -s * 0.1, 4, s * 0.2);
    if (level >= 1) {
        ctx.strokeStyle = '#f1c40f'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(0, -s * 0.05, s * 0.4, 0, Math.PI * 2); ctx.stroke();
    }
    if (level >= 2) {
        ctx.fillStyle = '#ffd700';
        ctx.beginPath(); ctx.arc(s * 0.25, -s * 0.05, 2, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.arc(s * 0.45, -s * 0.05, 2, 0, Math.PI * 2); ctx.fill();
    }
}

function drawTeslaCoil(ctx, r, level) {
    const s = r * 0.85;
    ctx.fillStyle = '#2a2a3a'; ctx.fillRect(-s * 0.55, s * 0.25, s * 1.1, s * 0.3);
    ctx.strokeStyle = '#4a4a5a'; ctx.lineWidth = 1; ctx.strokeRect(-s * 0.55, s * 0.25, s * 1.1, s * 0.3);
    ctx.fillStyle = '#888'; ctx.fillRect(-1.5, -s * 0.75, 3, s * 1.0);
    const coilColor = level >= 1 ? '#c9a040' : '#a08030';
    ctx.strokeStyle = coilColor; ctx.lineWidth = 1.2;
    const coilStart = -s * 0.65, coilEnd = s * 0.2, coilH = coilEnd - coilStart;
    const turns = 5 + level * 2;
    for (let i = 0; i < turns; i++) {
        const ty = coilStart + (coilH * i) / turns;
        const ty2 = coilStart + (coilH * (i + 1)) / turns;
        ctx.beginPath();
        ctx.moveTo(-s * 0.38, ty);
        ctx.lineTo(s * 0.38, ty + (ty2 - ty) * 0.3);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(s * 0.38, ty + (ty2 - ty) * 0.3);
        ctx.lineTo(-s * 0.38, ty2);
        ctx.stroke();
    }
    const sphereR = s * 0.22 + level * 0.04;
    const sphereY = -s * 0.78;
    const grad = ctx.createRadialGradient(0, sphereY - sphereR * 0.3, sphereR * 0.1, 0, sphereY, sphereR);
    grad.addColorStop(0, '#fff');
    grad.addColorStop(0.3, '#d4a0e8');
    grad.addColorStop(1, '#6c3483');
    ctx.fillStyle = grad;
    ctx.beginPath(); ctx.arc(0, sphereY, sphereR, 0, Math.PI * 2); ctx.fill();
    ctx.shadowColor = '#c39bdb'; ctx.shadowBlur = 6 + level * 2;
    ctx.fillStyle = 'rgba(200,150,220,0.3)';
    ctx.beginPath(); ctx.arc(0, sphereY, sphereR * 1.15, 0, Math.PI * 2); ctx.fill();
    ctx.shadowBlur = 0;
    if (level >= 1) {
        ctx.fillStyle = '#777'; ctx.fillRect(-s * 0.32, -s * 0.55, 1.5, s * 0.55);
        ctx.fillRect(s * 0.28, -s * 0.55, 1.5, s * 0.55);
        ctx.fillStyle = '#c39bdb'; ctx.beginPath(); ctx.arc(-s * 0.31, -s * 0.58, 2.5, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.arc(s * 0.29, -s * 0.58, 2.5, 0, Math.PI * 2); ctx.fill();
    }
    if (level >= 2) {
        ctx.fillStyle = '#f1c40f';
        ctx.beginPath(); ctx.arc(0, -s * 0.4, 1.5, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.arc(0, -s * 0.15, 1.5, 0, Math.PI * 2); ctx.fill();
    }
}

function drawMintTower(ctx, r, level) {
    const s = r * 0.85;
    if (level < 2) {
        const coinColors = ['#f1c40f', '#e6b800', '#f9d423', '#e6b800', '#f1c40f'];
        for (let i = 0; i < 3 + level; i++) {
            const cy = s * 0.45 - i * s * 0.28;
            ctx.fillStyle = coinColors[i % coinColors.length];
            ctx.beginPath();
            ctx.ellipse(0, cy, s * 0.55, s * 0.12, 0, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#b8860b'; ctx.lineWidth = 0.8;
            ctx.beginPath();
            ctx.ellipse(0, cy, s * 0.55, s * 0.12, 0, 0, Math.PI * 2);
            ctx.stroke();
            ctx.fillStyle = '#8b6914'; ctx.font = (7 + level * 1.5) + 'px sans-serif';
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText('$', 0, cy);
        }
        ctx.fillStyle = '#3d2b1f'; ctx.fillRect(-s * 0.55, s * 0.5, s * 1.1, s * 0.2);
        ctx.strokeStyle = '#5a3d2b'; ctx.lineWidth = 1; ctx.strokeRect(-s * 0.55, s * 0.5, s * 1.1, s * 0.2);
    } else {
        ctx.fillStyle = '#8b6914';
        ctx.fillRect(-s * 0.65, s * 0.05, s * 1.3, s * 0.55);
        ctx.strokeStyle = '#5a3d2b'; ctx.lineWidth = 1.5;
        ctx.strokeRect(-s * 0.65, s * 0.05, s * 1.3, s * 0.55);
        ctx.fillStyle = '#a07820';
        ctx.beginPath();
        ctx.arc(0, s * 0.05, s * 0.7, Math.PI, 0);
        ctx.fill();
        ctx.strokeStyle = '#5a3d2b'; ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(0, s * 0.05, s * 0.7, Math.PI, 0);
        ctx.stroke();
        ctx.fillStyle = '#f1c40f'; ctx.fillRect(-s * 0.62, s * 0.02, s * 1.24, 2.5);
        ctx.fillStyle = '#f1c40f'; ctx.fillRect(-s * 0.62, s * 0.35, s * 1.24, 2);
        ctx.fillStyle = '#f1c40f';
        ctx.beginPath(); ctx.arc(0, s * 0.2, s * 0.15, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#333';
        ctx.fillRect(-1, s * 0.2, 2, s * 0.12);
        ctx.fillStyle = '#fff';
        ctx.beginPath(); ctx.arc(s * 0.45, -s * 0.2, 2, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.arc(-s * 0.35, -s * 0.15, 1.5, 0, Math.PI * 2); ctx.fill();
    }
}

function drawRangeBuffTower(ctx, r, level) {
    const s = r * 0.85;
    ctx.fillStyle = '#3a3a4a'; ctx.fillRect(-s * 0.5, s * 0.3, s * 1.0, s * 0.25);
    ctx.strokeStyle = '#5a5a6a'; ctx.lineWidth = 1; ctx.strokeRect(-s * 0.5, s * 0.3, s * 1.0, s * 0.25);
    ctx.fillStyle = '#555'; ctx.fillRect(-2, -s * 0.05, 4, s * 0.45);
    ctx.strokeStyle = '#777'; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.arc(0, -s * 0.15, s * 0.6, Math.PI * 0.55, Math.PI * 0.45, true);
    ctx.stroke();
    ctx.fillStyle = level >= 1 ? 'rgba(93,173,226,0.35)' : 'rgba(93,173,226,0.2)';
    ctx.beginPath(); ctx.arc(0, -s * 0.15, s * 0.55, Math.PI * 0.55, Math.PI * 0.45, true);
    ctx.fill();
    ctx.strokeStyle = level >= 2 ? '#5dade2' : '#3a7aaa';
    ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.arc(0, -s * 0.15, s * 0.6, Math.PI * 0.6, Math.PI * 0.4, true);
    ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.beginPath(); ctx.arc(0, -s * 0.55, 2.5, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = level >= 2 ? '#87ceeb' : '#5dade2';
    ctx.lineWidth = 1.2;
    for (let i = 0; i < 2 + level; i++) {
        const arcR = s * (0.7 + i * 0.2);
        ctx.beginPath();
        ctx.arc(0, -s * 0.15, arcR, Math.PI * 0.65, Math.PI * 0.35, true);
        ctx.stroke();
    }
    if (level >= 1) {
        ctx.fillStyle = '#5dade2';
        ctx.beginPath(); ctx.arc(-s * 0.3, s * 0.35, 2, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.arc(s * 0.3, s * 0.35, 2, 0, Math.PI * 2); ctx.fill();
    }
    if (level >= 2) {
        ctx.fillStyle = '#f1c40f';
        ctx.beginPath(); ctx.arc(0, -s * 0.55, 4, 0, Math.PI * 2); ctx.fill();
    }
}

function drawSpeedBuffTower(ctx, r, level) {
    const s = r * 0.85;
    ctx.fillStyle = '#2a3a2a'; ctx.fillRect(-s * 0.5, s * 0.3, s * 1.0, s * 0.25);
    ctx.strokeStyle = '#3a5a3a'; ctx.lineWidth = 1; ctx.strokeRect(-s * 0.5, s * 0.3, s * 1.0, s * 0.25);
    ctx.fillStyle = '#555'; ctx.fillRect(-2, -s * 0.1, 4, s * 0.5);
    const arrowColor = level >= 1 ? '#58d68d' : '#2ecc71';
    ctx.fillStyle = arrowColor;
    ctx.beginPath(); ctx.moveTo(0, -s * 0.8); ctx.lineTo(s * 0.35, -s * 0.25); ctx.lineTo(-s * 0.35, -s * 0.25); ctx.closePath(); ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(0, -s * 0.8); ctx.lineTo(s * 0.35, -s * 0.25); ctx.lineTo(-s * 0.35, -s * 0.25); ctx.closePath(); ctx.stroke();
    ctx.strokeStyle = 'rgba(255,255,255,0.4)'; ctx.lineWidth = 1.5;
    for (let i = 0; i < 2 + level; i++) {
        const ly = -s * 0.2 + i * s * 0.15;
        ctx.beginPath(); ctx.moveTo(-s * 0.45, ly); ctx.lineTo(s * 0.45, ly); ctx.stroke();
    }
    if (level >= 1) {
        ctx.fillStyle = '#58d68d';
        ctx.beginPath(); ctx.moveTo(0, -s * 0.65); ctx.lineTo(s * 0.25, -s * 0.2); ctx.lineTo(-s * 0.25, -s * 0.2); ctx.closePath(); ctx.fill();
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(0, -s * 0.65); ctx.lineTo(s * 0.25, -s * 0.2); ctx.lineTo(-s * 0.25, -s * 0.2); ctx.closePath(); ctx.stroke();
    }
    if (level >= 2) {
        ctx.fillStyle = '#f1c40f';
        ctx.beginPath(); ctx.moveTo(0, -s * 0.5); ctx.lineTo(s * 0.2, -s * 0.15); ctx.lineTo(-s * 0.2, -s * 0.15); ctx.closePath(); ctx.fill();
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.moveTo(0, -s * 0.5); ctx.lineTo(s * 0.2, -s * 0.15); ctx.lineTo(-s * 0.2, -s * 0.15); ctx.closePath(); ctx.stroke();
    }
}

function drawAttackBuffTower(ctx, r, level) {
    const s = r * 0.85;
    ctx.fillStyle = '#3a1a1a'; ctx.fillRect(-s * 0.5, s * 0.3, s * 1.0, s * 0.25);
    ctx.strokeStyle = '#5a2a2a'; ctx.lineWidth = 1; ctx.strokeRect(-s * 0.5, s * 0.3, s * 1.0, s * 0.25);
    const orbR = s * 0.35;
    const orbY = -s * 0.05;
    const grad = ctx.createRadialGradient(0, orbY - orbR * 0.2, orbR * 0.1, 0, orbY, orbR);
    grad.addColorStop(0, '#fff');
    grad.addColorStop(0.3, level >= 2 ? '#ff6b6b' : '#e74c3c');
    grad.addColorStop(1, level >= 1 ? '#922b21' : '#c0392b');
    ctx.fillStyle = grad;
    ctx.beginPath(); ctx.arc(0, orbY, orbR, 0, Math.PI * 2); ctx.fill();
    ctx.shadowColor = level >= 2 ? '#ff6b6b' : '#e74c3c';
    ctx.shadowBlur = 8 + level * 3;
    ctx.fillStyle = 'rgba(231,76,60,0.2)';
    ctx.beginPath(); ctx.arc(0, orbY, orbR * 1.15, 0, Math.PI * 2); ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = '#f1948a'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(0, orbY, orbR, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = '#ffcccc';
    ctx.beginPath(); ctx.arc(-orbR * 0.3, orbY - orbR * 0.3, orbR * 0.2, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,0.6)'; ctx.lineWidth = 1;
    if (level >= 1) {
        ctx.beginPath(); ctx.arc(0, orbY, orbR + 4, 0, Math.PI * 2); ctx.stroke();
        ctx.beginPath(); ctx.arc(0, orbY, orbR + 7, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(255,255,255,0.25)'; ctx.lineWidth = 0.8; ctx.stroke();
    }
    if (level >= 2) {
        ctx.fillStyle = '#f1c40f';
        ctx.beginPath(); ctx.arc(0, orbY - orbR - 3, 3, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.arc(orbR * 0.6, orbY + orbR * 0.3, 2.5, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.arc(-orbR * 0.6, orbY + orbR * 0.3, 2.5, 0, Math.PI * 2); ctx.fill();
    }
    ctx.fillStyle = '#3a1a1a';
    ctx.fillRect(-2, s * 0.15, 4, s * 0.25);
}

function drawSteamRollerDepot(ctx, r, level) {
    const s = r * 0.85;
    ctx.fillStyle = '#4a3030';
    ctx.fillRect(-s * 0.65, -s * 0.25, s * 1.3, s * 0.75);
    ctx.strokeStyle = '#6a4040';
    ctx.lineWidth = 1.2;
    ctx.strokeRect(-s * 0.65, -s * 0.25, s * 1.3, s * 0.75);
    ctx.fillStyle = '#3a2020';
    ctx.beginPath();
    ctx.moveTo(-s * 0.75, -s * 0.25);
    ctx.lineTo(0, -s * 0.7);
    ctx.lineTo(s * 0.75, -s * 0.25);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = '#5a3030';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = '#2a1818';
    ctx.fillRect(-s * 0.3, s * 0.05, s * 0.6, s * 0.42);
    ctx.strokeStyle = '#4a2828';
    ctx.lineWidth = 1;
    ctx.strokeRect(-s * 0.3, s * 0.05, s * 0.6, s * 0.42);
    ctx.strokeStyle = '#5a3838';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, s * 0.05);
    ctx.lineTo(0, s * 0.47);
    ctx.stroke();
    ctx.fillStyle = level >= 2 ? '#ff3b3b' : (level >= 1 ? '#e74c3c' : '#c0392b');
    ctx.beginPath();
    ctx.arc(0, -s * 0.35, s * 0.18, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#fff';
    ctx.font = 'bold ' + (7 + level) + 'px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('🚂', 0, -s * 0.35);
    ctx.fillStyle = '#333';
    ctx.fillRect(s * 0.35, -s * 0.7, s * 0.15, s * 0.35);
    ctx.strokeStyle = '#555';
    ctx.lineWidth = 0.8;
    ctx.strokeRect(s * 0.35, -s * 0.7, s * 0.15, s * 0.35);
    if (Math.sin(game ? game.animTime * 2 : 0) > -0.3) {
        const smokeAlpha = 0.3 + Math.sin((game ? game.animTime : 0) * 3) * 0.15;
        ctx.fillStyle = 'rgba(180,180,180,' + smokeAlpha + ')';
        ctx.beginPath();
        ctx.arc(s * 0.42, -s * 0.78, s * 0.1, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(s * 0.48, -s * 0.92, s * 0.07, 0, Math.PI * 2);
        ctx.fill();
    }
    if (level >= 1) {
        ctx.fillStyle = '#f1c40f';
        ctx.beginPath();
        ctx.arc(-s * 0.5, s * 0.15, 2, 0, Math.PI * 2);
        ctx.fill();
    }
    if (level >= 2) {
        ctx.fillStyle = '#f1c40f';
        ctx.beginPath();
        ctx.arc(s * 0.5, s * 0.15, 2, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = '#f1c40f';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(0, -s * 0.35, s * 0.22, 0, Math.PI * 2);
        ctx.stroke();
    }
}

function drawSteamRollerEntity(ctx, roller) {
    const s = roller.size;
    ctx.save();
    ctx.translate(roller.x, roller.y);

    ctx.fillStyle = 'rgba(0,0,0,0.35)';
    ctx.beginPath();
    ctx.ellipse(0, s * 0.85, s * 1.0, s * 0.25, 0, 0, Math.PI * 2);
    ctx.fill();

    const bodyGrad = ctx.createLinearGradient(0, -s * 0.6, 0, s * 0.5);
    bodyGrad.addColorStop(0, '#e07070');
    bodyGrad.addColorStop(0.5, roller.color);
    bodyGrad.addColorStop(1, '#6b2020');
    ctx.fillStyle = bodyGrad;
    ctx.fillRect(-s * 0.75, -s * 0.55, s * 1.5, s * 0.9);
    ctx.strokeStyle = '#3a1010';
    ctx.lineWidth = 1.2;
    ctx.strokeRect(-s * 0.75, -s * 0.55, s * 1.5, s * 0.9);

    ctx.fillStyle = '#8b3a3a';
    ctx.fillRect(-s * 0.2, -s * 0.95, s * 0.65, s * 0.45);
    ctx.strokeStyle = '#5a2020';
    ctx.lineWidth = 1;
    ctx.strokeRect(-s * 0.2, -s * 0.95, s * 0.65, s * 0.45);
    ctx.fillStyle = '#ffd700';
    ctx.fillRect(-s * 0.05, -s * 0.85, s * 0.35, s * 0.25);
    ctx.strokeStyle = '#3a1010';
    ctx.lineWidth = 0.8;
    ctx.strokeRect(-s * 0.05, -s * 0.85, s * 0.35, s * 0.25);

    ctx.fillStyle = '#444';
    ctx.fillRect(s * 0.3, -s * 1.05, s * 0.2, s * 0.55);
    ctx.strokeStyle = '#555';
    ctx.lineWidth = 0.7;
    ctx.strokeRect(s * 0.3, -s * 1.05, s * 0.2, s * 0.55);
    const smokeAlpha = 0.35 + Math.sin(roller.wheelAngle * 1.5) * 0.15;
    ctx.fillStyle = 'rgba(200,200,200,' + smokeAlpha + ')';
    ctx.beginPath();
    ctx.arc(s * 0.4, -s * 1.15, s * 0.16, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(s * 0.5, -s * 1.3, s * 0.11, 0, Math.PI * 2);
    ctx.fill();

    ctx.save();
    ctx.translate(-s * 0.8, s * 0.3);
    ctx.rotate(roller.wheelAngle);
    ctx.fillStyle = '#555';
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.35, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#333';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.35, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeStyle = '#777';
    ctx.lineWidth = 1;
    for (let i = 0; i < 4; i++) {
        const a = i * Math.PI / 2;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(Math.cos(a) * s * 0.32, Math.sin(a) * s * 0.32);
        ctx.stroke();
    }
    ctx.fillStyle = '#888';
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.1, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    ctx.save();
    ctx.translate(s * 0.5, s * 0.2);
    ctx.rotate(roller.wheelAngle * 0.8);
    ctx.fillStyle = '#555';
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.25, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#333';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.25, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeStyle = '#777';
    ctx.lineWidth = 0.8;
    for (let i = 0; i < 3; i++) {
        const a = i * Math.PI * 2 / 3;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(Math.cos(a) * s * 0.22, Math.sin(a) * s * 0.22);
        ctx.stroke();
    }
    ctx.fillStyle = '#888';
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.08, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    const barW = s * 2.2;
    const barH = 3;
    const barY = -s * 1.25;
    ctx.fillStyle = 'rgba(0,0,0,0.6)';
    ctx.fillRect(-barW / 2, barY, barW, barH);
    const hpRatio = roller.hp / roller.maxHp;
    ctx.fillStyle = hpRatio > 0.5 ? '#2ecc71' : hpRatio > 0.25 ? '#f1c40f' : '#e74c3c';
    ctx.fillRect(-barW / 2, barY, barW * hpRatio, barH);

    ctx.restore();
}

function drawTowerModel(tower, cx, cy, radius) {
    const r = radius;
    ctx.save();
    ctx.translate(cx, cy);
    const needsRotation = tower.type !== 'ice' && tower.type !== 'mint' && !isBuffTower(tower.type) && tower.type !== 'steamRoller';
    if (needsRotation) {
        ctx.rotate(tower.angle);
    }
    switch (tower.type) {
        case 'arrow': drawCrossbow(ctx, r, tower.level); break;
        case 'cannon': drawCannon(ctx, r, tower.level); break;
        case 'ice': drawIceCrystal(ctx, r, tower.level); break;
        case 'sniper': drawLaserTurret(ctx, r, tower.level); break;
        case 'tesla': drawTeslaCoil(ctx, r, tower.level); break;
        case 'rangeBuff': drawRangeBuffTower(ctx, r, tower.level); break;
        case 'speedBuff': drawSpeedBuffTower(ctx, r, tower.level); break;
        case 'attackBuff': drawAttackBuffTower(ctx, r, tower.level); break;
        case 'mint': drawMintTower(ctx, r, tower.level); break;
        case 'steamRoller': drawSteamRollerDepot(ctx, r, tower.level); break;
    }
    ctx.restore();
}

function drawPathLine(pathArr, fillColor, borderColor, fillWidth, borderWidth) {
    if (!pathArr || pathArr.length < 2) return;
    ctx.save();
    ctx.strokeStyle = borderColor;
    ctx.lineWidth = borderWidth;
    ctx.lineCap = 'butt';
    ctx.lineJoin = 'miter';
    ctx.beginPath();
    ctx.moveTo(cellCx(pathArr[0][0]), cellCy(pathArr[0][1]));
    for (let i = 1; i < pathArr.length; i++) {
        ctx.lineTo(cellCx(pathArr[i][0]), cellCy(pathArr[i][1]));
    }
    ctx.stroke();
    ctx.strokeStyle = fillColor;
    ctx.lineWidth = fillWidth;
    ctx.beginPath();
    ctx.moveTo(cellCx(pathArr[0][0]), cellCy(pathArr[0][1]));
    for (let i = 1; i < pathArr.length; i++) {
        ctx.lineTo(cellCx(pathArr[i][0]), cellCy(pathArr[i][1]));
    }
    ctx.stroke();
    ctx.restore();
}

function drawDirectionArrows(pathArr, color, interval, arrowSize) {
    if (!pathArr || pathArr.length < interval + 2) return;
    ctx.fillStyle = color;
    for (let i = interval; i < pathArr.length - 2; i += interval) {
        const curr = pathArr[i];
        const next = pathArr[i + 1];
        const ax = cellCx(curr[0]);
        const ay = cellCy(curr[1]);
        const dx = cellCx(next[0]) - ax;
        const dy = cellCy(next[1]) - ay;
        const angle = Math.atan2(dy, dx);
        ctx.save();
        ctx.translate(ax, ay);
        ctx.rotate(angle);
        ctx.beginPath();
        ctx.moveTo(arrowSize, 0);
        ctx.lineTo(-arrowSize * 0.7, -arrowSize * 0.6);
        ctx.lineTo(-arrowSize * 0.7, arrowSize * 0.6);
        ctx.closePath();
        ctx.fill();
        ctx.restore();
    }
}

function drawArrowhead(ctx, x, y, ux, uy, size, color) {
    ctx.fillStyle = color;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(Math.atan2(uy, ux));
    ctx.beginPath();
    ctx.moveTo(size, 0);
    ctx.lineTo(-size * 0.6, -size * 0.55);
    ctx.lineTo(-size * 0.6, size * 0.55);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
}

function drawSplitIndicators() {
    if (!game || !game.splitMap) return;
    const arrowSize = 8;
    const stemLength = CELL * 0.7;

    for (const key of Object.keys(game.splitMap)) {
        const split = game.splitMap[key];
        const si = parseInt(key);

        if (si < game.path.length && si + 1 < game.path.length && split.branchPath && split.branchPath.length > 0) {
            const fromCell = game.path[si];
            const toCell = game.path[si + 1];
            const fx = cellCx(fromCell[0]);
            const fy = cellCy(fromCell[1]);
            const tx = cellCx(toCell[0]);
            const ty = cellCy(toCell[1]);

            const mdx = tx - fx;
            const mdy = ty - fy;
            const mDist = Math.hypot(mdx, mdy);
            if (mDist < 0.1) continue;
            const mux = mdx / mDist;
            const muy = mdy / mDist;

            const bCell = split.branchPath[0];
            const bx = cellCx(bCell[0]);
            const by = cellCy(bCell[1]);
            const bdx = bx - fx;
            const bdy = by - fy;
            const bDist = Math.hypot(bdx, bdy);
            if (bDist < 0.1) continue;
            const bux = bdx / bDist;
            const buy = bdy / bDist;

            ctx.save();

            const mainEndX = fx + mux * stemLength;
            const mainEndY = fy + muy * stemLength;
            ctx.strokeStyle = 'rgba(93, 173, 226, 0.85)';
            ctx.lineWidth = 4;
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(fx, fy);
            ctx.lineTo(mainEndX, mainEndY);
            ctx.stroke();

            const branchEndX = fx + bux * stemLength;
            const branchEndY = fy + buy * stemLength;
            ctx.strokeStyle = 'rgba(243, 156, 18, 0.85)';
            ctx.lineWidth = 4;
            ctx.beginPath();
            ctx.moveTo(fx, fy);
            ctx.lineTo(branchEndX, branchEndY);
            ctx.stroke();

            drawArrowhead(ctx, mainEndX, mainEndY, mux, muy, arrowSize, 'rgba(93, 173, 226, 0.95)');
            drawArrowhead(ctx, branchEndX, branchEndY, bux, buy, arrowSize, 'rgba(243, 156, 18, 0.95)');

            ctx.fillStyle = '#ffffff';
            ctx.shadowColor = 'rgba(255,255,255,0.8)';
            ctx.shadowBlur = 6;
            ctx.beginPath();
            ctx.arc(fx, fy, 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.shadowBlur = 0;

            ctx.strokeStyle = 'rgba(255,255,255,0.6)';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(fx, fy, 7, 0, Math.PI * 2);
            ctx.stroke();

            ctx.restore();
        }

        if (split.mergeMainIndex < game.path.length && split.branchPath && split.branchPath.length > 0) {
            const mc = game.path[split.mergeMainIndex];
            const mx = cellCx(mc[0]);
            const my = cellCy(mc[1]);

            const lastBranch = split.branchPath[split.branchPath.length - 1];
            const lbx = cellCx(lastBranch[0]);
            const lby = cellCy(lastBranch[1]);

            ctx.save();

            ctx.strokeStyle = 'rgba(46, 204, 113, 0.55)';
            ctx.lineWidth = 3;
            ctx.setLineDash([6, 5]);
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(lbx, lby);
            ctx.lineTo(mx, my);
            ctx.stroke();
            ctx.setLineDash([]);

            ctx.fillStyle = '#2ecc71';
            ctx.shadowColor = 'rgba(46, 204, 113, 0.6)';
            ctx.shadowBlur = 6;
            ctx.beginPath();
            ctx.arc(mx, my, 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.shadowBlur = 0;

            ctx.strokeStyle = 'rgba(46, 204, 113, 0.5)';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(mx, my, 7, 0, Math.PI * 2);
            ctx.stroke();

            ctx.restore();
        }
    }
}

function render() {
    if (!game) return;
    ctx.clearRect(0, 0, CANVAS, CANVAS);

    const buildable = game.buildable;

    for (let r = 0; r < SIZE; r++) {
        for (let c = 0; c < SIZE; c++) {
            const x = c * CELL, y = r * CELL;
            if (buildable && buildable[r] && buildable[r][c]) {
                ctx.fillStyle = '#1a2f3f';
                ctx.fillRect(x, y, CELL, CELL);
                ctx.strokeStyle = '#1e3348';
                ctx.lineWidth = 0.5;
                ctx.strokeRect(x, y, CELL, CELL);
            } else {
                ctx.fillStyle = '#182430';
                ctx.fillRect(x, y, CELL, CELL);
                ctx.strokeStyle = '#1e2d3d';
                ctx.lineWidth = 0.5;
                ctx.strokeRect(x, y, CELL, CELL);
            }
        }
    }

    const MAIN_BORDER_W = CELL * 0.94;
    const MAIN_FILL_W   = CELL * 0.74;
    const BRANCH_BORDER_W = CELL * 0.88;
    const BRANCH_FILL_W   = CELL * 0.64;

    if (game.path && game.path.length >= 2) {
        drawPathLine(game.path, '#3a4d60', '#0e1722', MAIN_FILL_W, MAIN_BORDER_W);
        drawDirectionArrows(game.path, 'rgba(200,215,230,0.50)', 5, 6);
    }

    if (game.splits) {
        for (const split of game.splits) {
            if (split.branchPath && split.branchPath.length >= 2) {
                drawPathLine(split.branchPath, '#3d4550', '#10181f', BRANCH_FILL_W, BRANCH_BORDER_W);
                drawDirectionArrows(split.branchPath, 'rgba(210,200,180,0.42)', 4, 5.5);
            }
        }
    }

    drawSplitIndicators();

    if (game.pathStart) {
        ctx.fillStyle = 'rgba(46, 204, 113, 0.30)';
        ctx.fillRect(game.pathStart[0] * CELL, game.pathStart[1] * CELL, CELL, CELL);
        ctx.fillStyle = '#2ecc71'; ctx.font = 'bold 20px sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('▶', game.pathStart[0] * CELL + CELL / 2, game.pathStart[1] * CELL + CELL / 2);
    }
    if (game.pathEnd) {
        ctx.fillStyle = 'rgba(231, 76, 60, 0.25)';
        ctx.fillRect(game.pathEnd[0] * CELL, game.pathEnd[1] * CELL, CELL, CELL);
        ctx.fillStyle = '#e74c3c'; ctx.font = 'bold 20px sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('■', game.pathEnd[0] * CELL + CELL / 2, game.pathEnd[1] * CELL + CELL / 2);
    }

    for (const se of game.stunEffects) {
        const alpha = se.life / se.maxLife;
        ctx.strokeStyle = 'rgba(200, 140, 220, ' + alpha + ')'; ctx.lineWidth = 3;
        ctx.shadowColor = 'rgba(180, 100, 220, ' + alpha + ')'; ctx.shadowBlur = 12;
        ctx.beginPath(); ctx.moveTo(se.x1, se.y1);
        const midX = (se.x1 + se.x2) / 2 + (Math.random() - 0.5) * 30;
        const midY = (se.y1 + se.y2) / 2 + (Math.random() - 0.5) * 30;
        ctx.lineTo(midX, midY); ctx.lineTo(se.x2, se.y2); ctx.stroke(); ctx.shadowBlur = 0;
        ctx.strokeStyle = 'rgba(255, 220, 255, ' + (alpha * 1.2) + ')'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(se.x1, se.y1); ctx.lineTo(midX, midY); ctx.lineTo(se.x2, se.y2); ctx.stroke();
    }

    for (const pulse of game.pulseEffects) {
        const alpha = pulse.life / pulse.maxLife;
        const progress = 1 - alpha;
        const currentRadius = pulse.maxRadius * progress;

        for (let t = 1; t <= 3; t++) {
            const tailProgress = Math.max(0, progress - t * 0.1);
            if (tailProgress <= 0) continue;
            const tailRadius = pulse.maxRadius * tailProgress;
            const tailAlpha = alpha * (1 - t * 0.3) * 0.55;
            ctx.beginPath();
            ctx.arc(pulse.x, pulse.y, tailRadius, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(133, 193, 233, ' + tailAlpha + ')';
            ctx.lineWidth = 1.5 + t * 0.5; ctx.stroke();
        }

        ctx.beginPath();
        ctx.arc(pulse.x, pulse.y, currentRadius, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(133, 193, 233, ' + (alpha * 0.8) + ')';
        ctx.lineWidth = 3.5 * alpha;
        ctx.shadowColor = 'rgba(133, 193, 233, ' + (alpha * 0.6) + ')'; ctx.shadowBlur = 12;
        ctx.stroke(); ctx.shadowBlur = 0;

        if (alpha > 0.15) {
            ctx.beginPath();
            ctx.arc(pulse.x, pulse.y, currentRadius * 0.82, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(200, 230, 255, ' + (alpha * 0.55) + ')';
            ctx.lineWidth = 2 * alpha; ctx.stroke();
        }
    }

    for (const roller of game.steamRollers) {
        if (!roller.alive) continue;
        drawSteamRollerEntity(ctx, roller);
    }

    for (const tower of game.towers) {
        const cx = tower.col * CELL + CELL / 2;
        const cy = tower.row * CELL + CELL / 2;
        const radius = CELL * 0.38;

        const platSize = CELL * 0.72;
        const platX = cx - platSize / 2;
        const platY = cy - platSize / 2;
        ctx.fillStyle = 'rgba(180, 188, 196, 0.28)';
        ctx.fillRect(platX, platY, platSize, platSize);
        ctx.strokeStyle = 'rgba(200, 208, 216, 0.35)';
        ctx.lineWidth = 1;
        ctx.strokeRect(platX, platY, platSize, platSize);

        if (tower === game.selectedTower) {
            const displayRange = (isAttackTower(tower.type) ? tower.effRange : tower.range) * CELL || (tower.type === 'steamRoller' ? 0 : CELL * 2.5);
            if (displayRange > 0) {
                ctx.beginPath();
                ctx.arc(cx, cy, displayRange, 0, Math.PI * 2);
                if (isBuffTower(tower.type)) {
                    ctx.fillStyle = 'rgba(200, 150, 220, 0.06)';
                    ctx.strokeStyle = 'rgba(200, 150, 220, 0.2)';
                } else {
                    ctx.fillStyle = 'rgba(255,255,255,0.06)';
                    ctx.strokeStyle = 'rgba(255,255,255,0.12)';
                }
                ctx.fill();
                ctx.lineWidth = 1; ctx.stroke();
            }
        }

        if (tower.stunTimer > 0 && isAttackTower(tower.type)) {
            ctx.beginPath(); ctx.arc(cx, cy, radius + 4, 0, Math.PI * 2);
            const stunAlpha = 0.25 + Math.sin(game.animTime * 8) * 0.15;
            ctx.fillStyle = 'rgba(155, 89, 182, ' + stunAlpha + ')'; ctx.fill();
            ctx.strokeStyle = 'rgba(200, 150, 220, ' + (stunAlpha + 0.3) + ')'; ctx.lineWidth = 2; ctx.stroke();
            ctx.fillStyle = '#d4a0e8'; ctx.font = 'bold 13px sans-serif';
            ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
            ctx.fillText('⚡', cx, cy - radius - 5 + Math.sin(game.animTime * 10) * 1.5);
        }

        const stunDim = (tower.stunTimer > 0 && isAttackTower(tower.type)) ? 0.5 : 1;
        ctx.globalAlpha = stunDim;

        drawTowerModel(tower, cx, cy, radius);

        ctx.globalAlpha = 1;

        if (tower.level > 0) {
            ctx.fillStyle = '#f1c40f'; ctx.font = '9px sans-serif';
            ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
            ctx.fillText('★'.repeat(tower.level + 1), cx, cy - radius - 4);
        }

        if (isBuffTower(tower.type) && tower !== game.selectedTower) {
            const buffPct = Math.round(tower.buffValue * 100);
            ctx.fillStyle = 'rgba(255,255,255,0.6)'; ctx.font = '8px sans-serif';
            ctx.textAlign = 'center'; ctx.textBaseline = 'top';
            ctx.fillText('+' + buffPct + '%', cx, cy + radius + 2);
        }
        if (tower.type === 'steamRoller' && tower !== game.selectedTower) {
            const activeCount = game.steamRollers.filter(r => r.towerId === tower.id && r.alive).length;
            if (activeCount > 0) {
                ctx.fillStyle = '#2ecc71';
                ctx.font = '8px sans-serif';
                ctx.textAlign = 'center'; ctx.textBaseline = 'top';
                ctx.fillText(activeCount + ' active', cx, cy + radius + 2);
            } else {
                const cdRemaining = Math.max(0, tower.rollerSpawnTimer);
                ctx.fillStyle = '#f39c12';
                ctx.font = '8px sans-serif';
                ctx.textAlign = 'center'; ctx.textBaseline = 'top';
                ctx.fillText(cdRemaining.toFixed(0) + 's', cx, cy + radius + 2);
            }
        }
    }

    for (const enemy of game.enemies) {
        if (!enemy.alive) continue;
        const r = enemy.size;
        if (enemy.type === 'boss') {
            const auraR = r + 5 + Math.sin(game.animTime * 3) * 2;
            ctx.beginPath(); ctx.arc(enemy.x, enemy.y, auraR, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(155, 89, 182, 0.35)'; ctx.lineWidth = 2; ctx.stroke();
        }
        ctx.beginPath(); ctx.arc(enemy.x, enemy.y, r, 0, Math.PI * 2);
        ctx.fillStyle = enemy.color; ctx.shadowColor = enemy.color;
        ctx.shadowBlur = enemy.type === 'boss' ? 12 : 6; ctx.fill(); ctx.shadowBlur = 0;
        if (enemy.type === 'boss') {
            ctx.fillStyle = '#f1c40f'; ctx.font = 'bold 14px sans-serif';
            ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
            ctx.fillText('👑', enemy.x, enemy.y - r - 3);
        }

        if (enemy === game.selectedEnemy) {
            ctx.beginPath();
            ctx.arc(enemy.x, enemy.y, enemy.size + 7, 0, Math.PI * 2);
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 2.5;
            ctx.setLineDash([4, 3]);
            ctx.stroke();
            ctx.setLineDash([]);
            const pulseR = enemy.size + 10 + Math.sin(game.animTime * 5) * 2;
            ctx.beginPath();
            ctx.arc(enemy.x, enemy.y, pulseR, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(255,255,255,0.35)';
            ctx.lineWidth = 1.5;
            ctx.stroke();
        }

        const barW = r * 2.4, barH = 3;
        const barX = enemy.x - barW / 2, barY = enemy.y - r - (enemy.type === 'boss' ? 10 : 5) - (enemy === game.selectedEnemy ? 6 : 0);
        ctx.fillStyle = 'rgba(0,0,0,0.6)'; ctx.fillRect(barX, barY, barW, barH);
        const hpRatio = enemy.hp / enemy.maxHp;
        ctx.fillStyle = hpRatio > 0.5 ? '#2ecc71' : hpRatio > 0.25 ? '#f1c40f' : '#e74c3c';
        ctx.fillRect(barX, barY, barW * hpRatio, barH);
        if (enemy.slowFactor < 0.9) {
            ctx.fillStyle = 'rgba(133,193,233,0.4)';
            ctx.beginPath(); ctx.arc(enemy.x, enemy.y, r + 3, 0, Math.PI * 2); ctx.fill();
        }
        if (enemy.onBranch) {
            ctx.fillStyle = '#f39c12'; ctx.font = '8px sans-serif';
            ctx.textAlign = 'center'; ctx.textBaseline = 'top';
            ctx.fillText('↗', enemy.x, enemy.y + r + 2);
        }
    }

    for (const p of game.projectiles) {
        if (p.isChain) continue;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = p.color || '#f1c40f'; ctx.shadowColor = p.color || '#f1c40f';
        ctx.shadowBlur = 8; ctx.fill(); ctx.shadowBlur = 0;
    }

    for (const beam of game.beamEffects) {
        const alpha = beam.life / beam.maxLife;
        if (alpha <= 0) continue;
        ctx.strokeStyle = 'rgba(241, 196, 15, ' + (alpha * 0.9) + ')'; ctx.lineWidth = 5 * alpha;
        ctx.shadowColor = 'rgba(241, 196, 15, ' + (alpha * 0.8) + ')'; ctx.shadowBlur = 18;
        ctx.beginPath(); ctx.moveTo(beam.x1, beam.y1); ctx.lineTo(beam.x2, beam.y2); ctx.stroke(); ctx.shadowBlur = 0;
        ctx.strokeStyle = 'rgba(255, 255, 200, ' + (alpha * 0.95) + ')'; ctx.lineWidth = 2.2 * alpha;
        ctx.beginPath(); ctx.moveTo(beam.x1, beam.y1); ctx.lineTo(beam.x2, beam.y2); ctx.stroke();
    }

    for (const arc of game.lightningArcs) {
        const alpha = arc.life / arc.maxLife;
        if (alpha <= 0) continue;
        const dx = arc.x2 - arc.x1, dy = arc.y2 - arc.y1;

        ctx.strokeStyle = 'rgba(180, 120, 220, ' + (alpha * 0.35) + ')'; ctx.lineWidth = 8 * alpha;
        ctx.shadowColor = 'rgba(180, 120, 220, ' + (alpha * 0.55) + ')'; ctx.shadowBlur = 16;
        ctx.beginPath(); ctx.moveTo(arc.x1, arc.y1);
        const segs = 10;
        for (let s = 1; s < segs; s++) {
            const t = s / segs;
            const jx = arc.x1 + dx * t + (Math.random() - 0.5) * 12 * alpha;
            const jy = arc.y1 + dy * t + (Math.random() - 0.5) * 12 * alpha;
            ctx.lineTo(jx, jy);
        }
        ctx.lineTo(arc.x2, arc.y2); ctx.stroke(); ctx.shadowBlur = 0;

        const numLines = 3 + Math.floor(alpha * 2);
        for (let line = 0; line < numLines; line++) {
            const offsetBase = (line - (numLines - 1) / 2) * 4;
            ctx.strokeStyle = 'rgba(220, 180, 255, ' + (alpha * (0.75 + line * 0.08)) + ')';
            ctx.lineWidth = (2.0 - line * 0.35) * alpha;
            ctx.beginPath(); ctx.moveTo(arc.x1, arc.y1);
            for (let s = 1; s < segs; s++) {
                const t = s / segs;
                const jx = arc.x1 + dx * t + (Math.random() - 0.5) * 10 * alpha + offsetBase * (1 - Math.abs(t - 0.5) * 2);
                const jy = arc.y1 + dy * t + (Math.random() - 0.5) * 10 * alpha + offsetBase * (1 - Math.abs(t - 0.5) * 2) * 0.5;
                ctx.lineTo(jx, jy);
            }
            ctx.lineTo(arc.x2, arc.y2); ctx.stroke();
        }

        ctx.strokeStyle = 'rgba(255, 240, 255, ' + (alpha * 0.7) + ')'; ctx.lineWidth = 0.8 * alpha;
        ctx.beginPath(); ctx.moveTo(arc.x1, arc.y1);
        for (let s = 1; s < segs; s++) {
            const t = s / segs;
            const jx = arc.x1 + dx * t + (Math.random() - 0.5) * 3 * alpha;
            const jy = arc.y1 + dy * t + (Math.random() - 0.5) * 3 * alpha;
            ctx.lineTo(jx, jy);
        }
        ctx.lineTo(arc.x2, arc.y2); ctx.stroke();

        for (let s = 0; s < 8; s++) {
            const t = Math.random();
            const sx = arc.x1 + dx * t + (Math.random() - 0.5) * 14;
            const sy = arc.y1 + dy * t + (Math.random() - 0.5) * 14;
            ctx.fillStyle = 'rgba(255, 255, 255, ' + (alpha * Math.random() * 0.9) + ')';
            ctx.beginPath(); ctx.arc(sx, sy, 0.8 + Math.random() * 1.8, 0, Math.PI * 2); ctx.fill();
        }
        for (let s = 0; s < 3; s++) {
            const sx = arc.x2 + (Math.random() - 0.5) * 10;
            const sy = arc.y2 + (Math.random() - 0.5) * 10;
            ctx.fillStyle = 'rgba(255, 220, 255, ' + (alpha * 0.8) + ')';
            ctx.beginPath(); ctx.arc(sx, sy, 1.5 + Math.random() * 2, 0, Math.PI * 2); ctx.fill();
        }
    }

    for (const p of game.particles) {
        const alpha = Math.min(1, p.life * 2);
        ctx.globalAlpha = alpha; ctx.beginPath();
        ctx.arc(p.x, p.y, Math.max(0.5, p.size), 0, Math.PI * 2);
        ctx.fillStyle = p.color; ctx.fill();
    }
    ctx.globalAlpha = 1;

    for (const n of game.moneyNotes) {
        const alpha = Math.min(1, n.life / n.maxLife * 1.3);
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.translate(n.x, n.y);
        ctx.rotate(n.rotation);
        const s = n.size;
        ctx.fillStyle = '#5cb85c';
        ctx.fillRect(-s, -s * 0.58, s * 2, s * 1.16);
        ctx.strokeStyle = '#2d6a2d';
        ctx.lineWidth = 1.2;
        ctx.strokeRect(-s, -s * 0.58, s * 2, s * 1.16);
        ctx.strokeStyle = 'rgba(255,255,255,0.3)';
        ctx.lineWidth = 0.6;
        ctx.strokeRect(-s * 0.85, -s * 0.46, s * 1.7, s * 0.92);
        ctx.fillStyle = '#f1c40f';
        ctx.font = 'bold ' + (s * 1.2) + 'px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('$', 0, 0);
        ctx.restore();
    }

    if (game.moneyNoteTotal) {
        const mt = game.moneyNoteTotal;
        const alpha = Math.min(1, mt.life / mt.maxLife * 1.2);
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.fillStyle = '#f1c40f';
        ctx.font = 'bold 24px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.shadowColor = 'rgba(241, 196, 15, 0.7)';
        ctx.shadowBlur = 16;
        ctx.fillText(mt.text, mt.x, mt.y);
        ctx.shadowBlur = 0;
        ctx.strokeStyle = 'rgba(0,0,0,0.5)';
        ctx.lineWidth = 3;
        ctx.strokeText(mt.text, mt.x, mt.y);
        ctx.fillText(mt.text, mt.x, mt.y);
        ctx.restore();
    }
}
