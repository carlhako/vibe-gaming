function mulberry32(a) { return function() { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

function generateMap() {
    mapTiles = [];
    for (let y = 0; y < MAP_ROWS; y++) { mapTiles[y] = []; for (let x = 0; x < MAP_COLS; x++) mapTiles[y][x] = 0; }
    for (let x = 0; x < MAP_COLS; x++) { mapTiles[0][x] = 2; mapTiles[MAP_ROWS - 1][x] = 2; }
    for (let y = 0; y < MAP_ROWS; y++) { mapTiles[y][0] = 2; mapTiles[y][MAP_COLS - 1] = 2; }
    const rng = mulberry32(wave * 137 + 42);
    const numClusters = 10 + Math.floor(rng() * 8);
    for (let c = 0; c < numClusters; c++) {
        const cx = 3 + Math.floor(rng() * (MAP_COLS - 6)), cy = 3 + Math.floor(rng() * (MAP_ROWS - 6)), size = 2 + Math.floor(rng() * 4);
        for (let dy = -size; dy <= size; dy++) for (let dx = -size; dx <= size; dx++) {
            if (Math.abs(dx) + Math.abs(dy) <= size + rng() * 1.5) {
                const tx = cx + dx, ty = cy + dy;
                if (tx > 0 && tx < MAP_COLS - 1 && ty > 0 && ty < MAP_ROWS - 1 && rng() < 0.65) mapTiles[ty][tx] = 2;
            }
        }
    }
    const pcx = Math.floor(MAP_COLS / 2), pcy = Math.floor(MAP_ROWS / 2);
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) { const tx = pcx + dx, ty = pcy + dy; if (tx >= 0 && tx < MAP_COLS && ty >= 0 && ty < MAP_ROWS) mapTiles[ty][tx] = 0; }
    ensureMapConnectivity();
}

function ensureMapConnectivity() {
    const startTX = Math.floor(MAP_COLS / 2), startTY = Math.floor(MAP_ROWS / 2);
    const visited = Array.from({ length: MAP_ROWS }, () => Array(MAP_COLS).fill(false));
    const queue = [{ x: startTX, y: startTY }]; visited[startTY][startTX] = true;
    while (queue.length > 0) {
        const { x, y } = queue.shift();
        for (const [dx, dy] of [[0, 1], [0, -1], [1, 0], [-1, 0]]) {
            const nx = x + dx, ny = y + dy;
            if (nx >= 0 && nx < MAP_COLS && ny >= 0 && ny < MAP_ROWS && !visited[ny][nx] && mapTiles[ny][nx] === 0) { visited[ny][nx] = true; queue.push({ x: nx, y: ny }); }
        }
    }
    const unreachable = [];
    for (let y = 0; y < MAP_ROWS; y++) for (let x = 0; x < MAP_COLS; x++) if (mapTiles[y][x] === 0 && !visited[y][x]) unreachable.push({ x, y });
    if (unreachable.length === 0) return;
    const regionId = Array.from({ length: MAP_ROWS }, () => Array(MAP_COLS).fill(-1));
    let nextRegion = 0;
    for (const tile of unreachable) {
        if (regionId[tile.y][tile.x] !== -1) continue;
        const region = nextRegion++, rQueue = [tile]; regionId[tile.y][tile.x] = region;
        while (rQueue.length > 0) {
            const { x, y } = rQueue.shift();
            for (const [dx, dy] of [[0, 1], [0, -1], [1, 0], [-1, 0]]) {
                const nx = x + dx, ny = y + dy;
                if (nx >= 0 && nx < MAP_COLS && ny >= 0 && ny < MAP_ROWS && mapTiles[ny][nx] === 0 && regionId[ny][nx] === -1) { regionId[ny][nx] = region; rQueue.push({ x: nx, y: ny }); }
            }
        }
    }
    for (let r = 0; r < nextRegion; r++) {
        let bestDist = Infinity, bestWallX = -1, bestWallY = -1;
        for (let y = 1; y < MAP_ROWS - 1; y++) for (let x = 1; x < MAP_COLS - 1; x++) {
            if (mapTiles[y][x] === 0) continue;
            let bordersReachable = false, bordersRegion = false;
            for (const [dx, dy] of [[0, 1], [0, -1], [1, 0], [-1, 0]]) {
                const nx = x + dx, ny = y + dy;
                if (nx >= 0 && nx < MAP_COLS && ny >= 0 && ny < MAP_ROWS) {
                    if (mapTiles[ny][nx] === 0 && visited[ny][nx]) bordersReachable = true;
                    if (regionId[ny][nx] === r) bordersRegion = true;
                }
            }
            if (bordersReachable && bordersRegion) { const dist = Math.abs(x - startTX) + Math.abs(y - startTY); if (dist < bestDist) { bestDist = dist; bestWallX = x; bestWallY = y; } }
        }
        if (bestWallX >= 0) {
            mapTiles[bestWallY][bestWallX] = 0; visited[bestWallY][bestWallX] = true;
            const fq = [{ x: bestWallX, y: bestWallY }];
            while (fq.length > 0) {
                const { x, y } = fq.shift();
                for (const [dx, dy] of [[0, 1], [0, -1], [1, 0], [-1, 0]]) {
                    const nx = x + dx, ny = y + dy;
                    if (nx >= 0 && nx < MAP_COLS && ny >= 0 && ny < MAP_ROWS && !visited[ny][nx] && mapTiles[ny][nx] === 0) { visited[ny][nx] = true; fq.push({ x: nx, y: ny }); }
                }
            }
        }
    }
}

function isWall(wx, wy) { const tx = Math.floor(wx / TILE), ty = Math.floor(wy / TILE); if (tx < 0 || tx >= MAP_COLS || ty < 0 || ty >= MAP_ROWS) return true; return mapTiles[ty][tx] >= 1; }
function isWallCircle(cx, cy, r) { for (let i = 0; i < 8; i++) { const a = (i / 8) * Math.PI * 2; if (isWall(cx + Math.cos(a) * r, cy + Math.sin(a) * r)) return true; } return false; }

function getWallTileAt(x, y) {
    const tx = Math.floor(x / TILE), ty = Math.floor(y / TILE);
    const checks = [{ x: tx, y: ty }, { x: tx + 1, y: ty }, { x: tx - 1, y: ty }, { x: tx, y: ty + 1 }, { x: tx, y: ty - 1 }];
    for (const c of checks) if (c.x >= 0 && c.x < MAP_COLS && c.y >= 0 && c.y < MAP_ROWS && mapTiles[c.y][c.x] >= 1) return { tx: c.x, ty: c.y };
    return null;
}

function damageWallTile(tx, ty, angle) {
    if (tx < 0 || tx >= MAP_COLS || ty < 0 || ty >= MAP_ROWS || mapTiles[ty][tx] === 0) return;
    const cx = tx * TILE + TILE / 2, cy = ty * TILE + TILE / 2;
    if (mapTiles[ty][tx] === 2) { mapTiles[ty][tx] = 1; spawnParticles(cx, cy, 6, '#bbbbcc', 50, 0.3); spawnDamageNumber(cx, cy, 1, '#cccccc'); }
    else if (mapTiles[ty][tx] === 1) {
        mapTiles[ty][tx] = 0; spawnParticles(cx, cy, 16, '#ddcc88', 130, 0.55); spawnParticles(cx, cy, 8, '#998866', 90, 0.4);
        screenShake = Math.max(screenShake, 3); spawnDamageNumber(cx, cy, 0, '#ffe080');
        if (Math.random() < WALL_DESTROY_TOKEN_CHANCE) droppedTokens.push({ x: cx + (Math.random() - 0.5) * 16, y: cy + (Math.random() - 0.5) * 16, life: 15, maxLife: 15, collected: false, flyingToPlayer: false, flyStartX: 0, flyStartY: 0, flyProgress: 0 });
        // Random amulet drop (~1 in 50 when destroying stone blocks)
        if (Math.random() < WALL_DESTROY_AMULET_CHANCE) {
            const amulet = generateRandomAmulet();
            droppedAmulets.push({ x: cx + (Math.random() - 0.5) * 16, y: cy + (Math.random() - 0.5) * 16, life: 20, maxLife: 20, amulet: amulet, glow: 0, collected: false, flyingToPlayer: false, flyStartX: 0, flyStartY: 0, flyProgress: 0 });
            spawnParticles(cx, cy, 14, amulet.color, 100, 0.5);
        }
    }
}

function damageWallsInMeleeArc(px, py, angle, range, halfArc) {
    const wallCheckRange = range + TILE * 0.65;
    const tileMinX = Math.floor((px - wallCheckRange) / TILE), tileMaxX = Math.floor((px + wallCheckRange) / TILE);
    const tileMinY = Math.floor((py - wallCheckRange) / TILE), tileMaxY = Math.floor((py + wallCheckRange) / TILE);
    for (let wy = tileMinY; wy <= tileMaxY; wy++) for (let wx = tileMinX; wx <= tileMaxX; wx++) {
        if (wx < 0 || wx >= MAP_COLS || wy < 0 || wy >= MAP_ROWS || mapTiles[wy][wx] === 0) continue;
        const wcx = wx * TILE + TILE / 2, wcy = wy * TILE + TILE / 2, dx = wcx - px, dy = wcy - py, dist = Math.hypot(dx, dy);
        if (dist < wallCheckRange) { const angleToTile = Math.atan2(dy, dx); let diff = angleToTile - angle; while (diff > Math.PI) diff -= Math.PI * 2; while (diff < -Math.PI) diff += Math.PI * 2; if (Math.abs(diff) <= halfArc) damageWallTile(wx, wy, angle); }
    }
}

function hasLineOfSight(x1, y1, x2, y2) {
    const dist = Math.hypot(x2 - x1, y2 - y1), steps = Math.max(1, Math.ceil(dist / (TILE * 0.35)));
    for (let i = 0; i <= steps; i++) { const t = i / steps; if (isWall(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)) return false; }
    return true;
}

function getSteeringDirection(entX, entY, targetX, targetY, entSize, isStuck) {
    const dx = targetX - entX, dy = targetY - entY, dist = Math.hypot(dx, dy);
    if (dist < 0.5) return { dx: 0, dy: 0 };
    const baseAngle = Math.atan2(dy, dx);
    if (hasLineOfSight(entX, entY, targetX, targetY)) return { dx: Math.cos(baseAngle), dy: Math.sin(baseAngle) };
    const searchSpread = isStuck ? Math.PI * 0.92 : Math.PI * 0.68, numRays = isStuck ? 13 : 9;
    let bestAngle = baseAngle, bestScore = -Infinity;
    for (let i = 0; i < numRays; i++) {
        const offset = (i / (numRays - 1) - 0.5) * searchSpread, angle = baseAngle + offset;
        const rayDist = TILE * 1.3, rx = entX + Math.cos(angle) * rayDist, ry = entY + Math.sin(angle) * rayDist;
        if (isWallCircle(rx, ry, entSize * 0.7)) continue;
        const midDist = TILE * 0.55, midX = entX + Math.cos(angle) * midDist, midY = entY + Math.sin(angle) * midDist;
        if (isWallCircle(midX, midY, entSize * 0.45)) continue;
        const newDx = targetX - rx, newDy = targetY - ry, newDist = Math.hypot(newDx, newDy), anglePenalty = Math.abs(offset) * 35, score = -newDist - anglePenalty;
        if (score > bestScore) { bestScore = score; bestAngle = angle; }
    }
    if (bestScore === -Infinity) return { dx: Math.cos(baseAngle), dy: Math.sin(baseAngle) };
    return { dx: Math.cos(bestAngle), dy: Math.sin(bestAngle) };
}

function getEdgeSpawnPosition() {
    const edge = Math.floor(Math.random() * 4), margin = TILE * 2.5; let x, y;
    switch (edge) { case 0: x = margin + Math.random() * (WORLD_W - margin * 2); y = margin; break; case 1: x = margin + Math.random() * (WORLD_W - margin * 2); y = WORLD_H - margin; break; case 2: x = margin; y = margin + Math.random() * (WORLD_H - margin * 2); break; default: x = WORLD_W - margin; y = margin + Math.random() * (WORLD_H - margin * 2); break; }
    return { x, y };
}

function pointToSegmentDist(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1, lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return Math.hypot(px - x1, py - y1);
    let t = ((px - x1) * dx + (py - y1) * dy) / lenSq; t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

function rectCircleCollision(rx, ry, rw, rh, cx, cy, cr) { const closestX = Math.max(rx, Math.min(cx, rx + rw)), closestY = Math.max(ry, Math.min(cy, ry + rh)); return (cx - closestX) ** 2 + (cy - closestY) ** 2 < cr * cr; }

function resolveWallCollision(entity, radius) {
    const tileMinX = Math.floor((entity.x - radius) / TILE), tileMaxX = Math.floor((entity.x + radius) / TILE), tileMinY = Math.floor((entity.y - radius) / TILE), tileMaxY = Math.floor((entity.y + radius) / TILE);
    for (let ty = tileMinY; ty <= tileMaxY; ty++) for (let tx = tileMinX; tx <= tileMaxX; tx++) {
        if (tx < 0 || tx >= MAP_COLS || ty < 0 || ty >= MAP_ROWS || mapTiles[ty][tx] === 0) continue;
        const wx = tx * TILE, wy = ty * TILE;
        if (rectCircleCollision(wx, wy, TILE, TILE, entity.x, entity.y, radius)) {
            const cx2 = wx + TILE / 2, cy2 = wy + TILE / 2, dx2 = entity.x - cx2, dy2 = entity.y - cy2, dist = Math.hypot(dx2, dy2);
            if (dist < 0.001) continue;
            const overlap = radius + TILE * 0.707 - dist; if (overlap > 0) { entity.x += (dx2 / dist) * overlap; entity.y += (dy2 / dist) * overlap; }
        }
    }
    entity.x = Math.max(radius, Math.min(WORLD_W - radius, entity.x)); entity.y = Math.max(radius, Math.min(WORLD_H - radius, entity.y));
}

function getRandomOpenPosition(minDistFromPlayer) {
    const openTiles = [];
    for (let ty = 0; ty < MAP_ROWS; ty++) {
        for (let tx = 0; tx < MAP_COLS; tx++) {
            if (mapTiles[ty][tx] === 0) {
                const cx = tx * TILE + TILE / 2, cy = ty * TILE + TILE / 2;
                if (!minDistFromPlayer || !player || Math.hypot(cx - player.x, cy - player.y) >= minDistFromPlayer) {
                    openTiles.push({ x: cx, y: cy });
                }
            }
        }
    }
    if (openTiles.length === 0) return { x: WORLD_W / 2, y: WORLD_H / 2 };
    return openTiles[Math.floor(Math.random() * openTiles.length)];
}
