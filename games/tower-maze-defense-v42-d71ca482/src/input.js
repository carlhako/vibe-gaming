'use strict';

// ── Coordinate helpers ────────────────────────────────────────────
function getGridPos(e) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
    const x = (e.clientX - rect.left) * scaleX, y = (e.clientY - rect.top) * scaleY;
    const col = Math.floor(x / CELL), row = Math.floor(y / CELL);
    return { col, row, valid: col >= 0 && col < SIZE && row >= 0 && row < SIZE, x, y };
}
