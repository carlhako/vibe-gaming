'use strict';

function generateBasePathCells(rand) {
    const size = SIZE;
    const visited = new Set();
    const amplitude = Math.floor(size * (0.42 + rand() * 0.15));
    const centerY = size / 2;
    const freq1 = 0.6 + rand() * 0.9;
    const freq2 = 2.0 + rand() * 1.5;
    const phase1 = rand() * Math.PI * 2;
    const phase2 = rand() * Math.PI * 2;
    const rawTargets = new Array(size);
    for (let x = 0; x < size; x++) {
        const t = x / (size - 1);
        const wave1 = Math.sin(t * Math.PI * 2 * freq1 + phase1) * amplitude;
        const wave2 = Math.sin(t * Math.PI * 2 * freq2 + phase2) * (amplitude * 0.5);
        rawTargets[x] = centerY + wave1 + wave2 + (rand() - 0.5) * 2.2;
    }
    const smoothed = new Array(size);
    for (let x = 0; x < size; x++) {
        let sum = rawTargets[x], count = 1;
        if (x > 0) { sum += rawTargets[x - 1]; count++; }
        if (x < size - 1) { sum += rawTargets[x + 1]; count++; }
        smoothed[x] = sum / count;
    }
    for (let x = 0; x < size; x++) smoothed[x] = Math.max(1.5, Math.min(size - 2.5, smoothed[x]));
    let startY = Math.round(smoothed[0]);
    startY = Math.max(1, Math.min(size - 2, startY));
    const path = [];
    path.push([0, startY]);
    visited.add('0,' + startY);
    let prevY = startY;
    for (let x = 1; x < size; x++) {
        const targetY = Math.round(smoothed[x]);
        const clampedTarget = Math.max(1, Math.min(size - 2, targetY));
        let curY = prevY;
        while (curY !== clampedTarget) {
            curY += curY < clampedTarget ? 1 : -1;
            const key = (x - 1) + ',' + curY;
            if (!visited.has(key)) { path.push([x - 1, curY]); visited.add(key); }
            else break;
        }
        const rightKey = x + ',' + curY;
        if (!visited.has(rightKey)) { path.push([x, curY]); visited.add(rightKey); }
        else {
            let found = false;
            for (let offset = 1; offset <= 5; offset++) {
                for (const sign of [1, -1]) {
                    const tryY = curY + offset * sign;
                    if (tryY >= 1 && tryY < size - 1) {
                        const tryKey = x + ',' + tryY;
                        if (!visited.has(tryKey)) { path.push([x, tryY]); visited.add(tryKey); found = true; break; }
                    }
                }
                if (found) break;
            }
            if (!found) {
                const fy = Math.min(Math.max(1, curY), size - 2);
                const fk = x + ',' + fy;
                if (!visited.has(fk)) { path.push([x, fy]); visited.add(fk); }
            }
        }
        prevY = path[path.length - 1][1];
    }
    const end = path[path.length - 1];
    if (end[0] < size - 1) {
        let cx = end[0], cy = end[1];
        while (cx < size - 1) { cx++; const k = cx + ',' + cy; if (!visited.has(k)) { path.push([cx, cy]); visited.add(k); } }
    }
    return path;
}

function tryAddLoop(pathCells, allPathCells, rand) {
    for (let attempt = 0; attempt < 40; attempt++) {
        const idx = 2 + Math.floor(rand() * (pathCells.length - 9));
        const fromCell = pathCells[idx];
        const toCell = pathCells[idx + 1];
        const dx = toCell[0] - fromCell[0];
        const dy = toCell[1] - fromCell[1];
        if (Math.abs(dx) + Math.abs(dy) !== 1) continue;
        const sdx = Math.sign(dx);
        const sdy = Math.sign(dy);

        let perpDirs;
        if (sdx !== 0) {
            perpDirs = [[0, 1], [0, -1]];
        } else {
            perpDirs = [[1, 0], [-1, 0]];
        }
        if (rand() < 0.5) perpDirs.reverse();

        for (const [px, py] of perpDirs) {
            const loopWidth = 6 + Math.floor(rand() * 7);
            const loopHeight = 6 + Math.floor(rand() * 7);
            const loopCells = [];
            const tempSet = new Set(allPathCells);
            let valid = true;
            let cx = fromCell[0], cy = fromCell[1];

            for (let i = 0; i < loopHeight; i++) {
                cx += px; cy += py;
                if (cx < 0 || cx >= SIZE || cy < 0 || cy >= SIZE) { valid = false; break; }
                const key = cx + ',' + cy;
                if (tempSet.has(key)) { valid = false; break; }
                loopCells.push([cx, cy]); tempSet.add(key);
            }
            if (!valid) continue;

            for (let i = 0; i < loopWidth; i++) {
                cx += sdx; cy += sdy;
                if (cx < 0 || cx >= SIZE || cy < 0 || cy >= SIZE) { valid = false; break; }
                const key = cx + ',' + cy;
                if (tempSet.has(key)) { valid = false; break; }
                loopCells.push([cx, cy]); tempSet.add(key);
            }
            if (!valid) continue;

            for (let i = 0; i < loopHeight; i++) {
                cx -= px; cy -= py;
                if (cx < 0 || cx >= SIZE || cy < 0 || cy >= SIZE) { valid = false; break; }
                const key = cx + ',' + cy;
                if (tempSet.has(key)) { valid = false; break; }
                loopCells.push([cx, cy]); tempSet.add(key);
            }
            if (!valid) continue;

            for (let i = 0; i < loopWidth - 1; i++) {
                cx -= sdx; cy -= sdy;
                if (cx < 0 || cx >= SIZE || cy < 0 || cy >= SIZE) { valid = false; break; }
                const key = cx + ',' + cy;
                if (tempSet.has(key) && !(cx === toCell[0] && cy === toCell[1])) { valid = false; break; }
                loopCells.push([cx, cy]); tempSet.add(key);
            }
            if (!valid) continue;
            if (cx !== toCell[0] || cy !== toCell[1]) continue;

            loopCells.pop();
            pathCells.splice(idx + 1, 0, ...loopCells);
            for (const [lx, ly] of loopCells) {
                allPathCells.add(lx + ',' + ly);
            }
            return true;
        }
    }
    return false;
}

function tryAddSplit(pathCells, allPathCells, rand) {
    for (let attempt = 0; attempt < 45; attempt++) {
        const startIdx = 3 + Math.floor(rand() * (pathCells.length - 20));
        const minGap = 12;
        const maxGap = Math.min(28, pathCells.length - startIdx - 5);
        if (maxGap < minGap) continue;
        const endIdx = startIdx + minGap + Math.floor(rand() * (maxGap - minGap));

        const startCell = pathCells[startIdx];
        const endCell = pathCells[endIdx];
        const mainDx = endCell[0] - startCell[0];
        const mainDy = endCell[1] - startCell[1];

        let px = 0, py = 0;
        if (Math.abs(mainDx) >= Math.abs(mainDy)) {
            py = rand() < 0.5 ? 1 : -1;
        } else {
            px = rand() < 0.5 ? 1 : -1;
        }
        const perpDirs = (px === 0) ? [[0, py], [0, -py]] : [[px, 0], [-px, 0]];

        for (const [ppx, ppy] of perpDirs) {
            const branchCells = [];
            const tempSet = new Set(allPathCells);
            let valid = true;
            let cx = startCell[0], cy = startCell[1];
            const perpDist = 4 + Math.floor(rand() * 6);

            for (let i = 0; i < perpDist; i++) {
                cx += ppx; cy += ppy;
                if (cx < 0 || cx >= SIZE || cy < 0 || cy >= SIZE) { valid = false; break; }
                const key = cx + ',' + cy;
                if (tempSet.has(key)) { valid = false; break; }
                branchCells.push([cx, cy]); tempSet.add(key);
            }
            if (!valid) continue;

            const targetCX = endCell[0] + ppx * perpDist;
            const targetCY = endCell[1] + ppy * perpDist;
            const xSteps = targetCX - cx;
            const ySteps = targetCY - cy;
            const xs = Math.sign(xSteps);
            const ys = Math.sign(ySteps);

            const wiggleAmplitude = (ppx !== 0) ? 1 : 0;

            for (let i = 0; i < Math.abs(xSteps); i++) {
                cx += xs;
                if (wiggleAmplitude > 0 && i > 1 && i < Math.abs(xSteps) - 1 && rand() < 0.15) {
                    const nudge = (rand() < 0.5 ? -1 : 1);
                    const nudgeY = cy + nudge;
                    if (nudgeY >= 0 && nudgeY < SIZE) {
                        const nudgeKey = cx + ',' + nudgeY;
                        if (!tempSet.has(nudgeKey)) {
                            branchCells.push([cx, nudgeY]); tempSet.add(nudgeKey);
                            cy = nudgeY;
                            const backY = cy - nudge;
                            if (i + 1 < Math.abs(xSteps)) {
                                cx += xs; i++;
                                const backKey = cx + ',' + backY;
                                if (backY >= 0 && backY < SIZE && !tempSet.has(backKey)) {
                                    branchCells.push([cx, backY]); tempSet.add(backKey);
                                    cy = backY;
                                } else {
                                    cx -= xs; i--;
                                    cy -= nudge;
                                    branchCells.pop(); tempSet.delete(nudgeKey);
                                    const key = cx + ',' + cy;
                                    if (tempSet.has(key)) { valid = false; break; }
                                    branchCells.push([cx, cy]); tempSet.add(key);
                                }
                            }
                        }
                    }
                }
                if (cx < 0 || cx >= SIZE || cy < 0 || cy >= SIZE) { valid = false; break; }
                const key = cx + ',' + cy;
                if (tempSet.has(key)) { valid = false; break; }
                branchCells.push([cx, cy]); tempSet.add(key);
            }
            if (!valid) continue;

            const wiggleAmplitudeY = (ppy !== 0) ? 1 : 0;
            for (let i = 0; i < Math.abs(ySteps); i++) {
                cy += ys;
                if (wiggleAmplitudeY > 0 && i > 1 && i < Math.abs(ySteps) - 1 && rand() < 0.15) {
                    const nudge = (rand() < 0.5 ? -1 : 1);
                    const nudgeX = cx + nudge;
                    if (nudgeX >= 0 && nudgeX < SIZE) {
                        const nudgeKey = nudgeX + ',' + cy;
                        if (!tempSet.has(nudgeKey)) {
                            branchCells.push([nudgeX, cy]); tempSet.add(nudgeKey);
                            cx = nudgeX;
                            const backX = cx - nudge;
                            if (i + 1 < Math.abs(ySteps)) {
                                cy += ys; i++;
                                const backKey = backX + ',' + cy;
                                if (backX >= 0 && backX < SIZE && !tempSet.has(backKey)) {
                                    branchCells.push([backX, cy]); tempSet.add(backKey);
                                    cx = backX;
                                } else {
                                    cy -= ys; i--;
                                    cx -= nudge;
                                    branchCells.pop(); tempSet.delete(nudgeKey);
                                    const key = cx + ',' + cy;
                                    if (tempSet.has(key)) { valid = false; break; }
                                    branchCells.push([cx, cy]); tempSet.add(key);
                                }
                            }
                        }
                    }
                }
                if (cx < 0 || cx >= SIZE || cy < 0 || cy >= SIZE) { valid = false; break; }
                const key = cx + ',' + cy;
                if (tempSet.has(key)) { valid = false; break; }
                branchCells.push([cx, cy]); tempSet.add(key);
            }
            if (!valid) continue;

            for (let i = 0; i < perpDist; i++) {
                cx -= ppx; cy -= ppy;
                if (cx < 0 || cx >= SIZE || cy < 0 || cy >= SIZE) { valid = false; break; }
                const key = cx + ',' + cy;
                if (tempSet.has(key) && i < perpDist - 1) { valid = false; break; }
                if (i < perpDist - 1) {
                    branchCells.push([cx, cy]); tempSet.add(key);
                }
            }
            if (!valid) continue;
            if (cx !== endCell[0] || cy !== endCell[1]) continue;

            for (const [bx, by] of branchCells) {
                allPathCells.add(bx + ',' + by);
            }

            return {
                atMainIndex: startIdx,
                branchPath: branchCells,
                mergeMainIndex: endIdx
            };
        }
    }
    return null;
}

function generatePathWithFeatures(prng) {
    const rand = prng || Math.random;
    const pathCells = generateBasePathCells(rand);
    const allPathCells = new Set(pathCells.map(([x, y]) => x + ',' + y));

    const maxLoops = rand() < 0.25 ? 3 : (rand() < 0.55 ? 2 : (rand() < 0.80 ? 1 : 0));
    let loopsAdded = 0;
    for (let attempt = 0; attempt < 20 && loopsAdded < maxLoops; attempt++) {
        if (tryAddLoop(pathCells, allPathCells, rand)) loopsAdded++;
    }

    const maxSplits = 1 + Math.floor(rand() * 4);
    const splits = [];
    for (let attempt = 0; attempt < 25 && splits.length < maxSplits; attempt++) {
        const split = tryAddSplit(pathCells, allPathCells, rand);
        if (split) splits.push(split);
    }

    const splitMap = {};
    for (const split of splits) {
        splitMap[split.atMainIndex] = split;
    }

    const buildable = Array.from({ length: SIZE }, () => Array(SIZE).fill(true));
    for (const [x, y] of pathCells) {
        if (y >= 0 && y < SIZE && x >= 0 && x < SIZE) buildable[y][x] = false;
    }
    for (const split of splits) {
        for (const [x, y] of split.branchPath) {
            if (y >= 0 && y < SIZE && x >= 0 && x < SIZE) buildable[y][x] = false;
        }
    }

    const pathStart = pathCells[0];
    const pathEnd = pathCells[pathCells.length - 1];
    const buildableCount = buildable.flat().filter(Boolean).length;

    return {
        path: pathCells,
        start: pathStart,
        end: pathEnd,
        buildable,
        buildableCount,
        splits,
        splitMap,
        allPathCellSet: allPathCells,
        loopCount: loopsAdded,
        splitCount: splits.length
    };
}

function generatePath(prng) {
    const result = generatePathWithFeatures(prng);
    return {
        path: result.path,
        buildable: result.buildable,
        start: result.start,
        end: result.end,
        buildableCount: result.buildableCount,
        splits: result.splits,
        splitMap: result.splitMap,
        allPathCellSet: result.allPathCellSet,
        loopCount: result.loopCount,
        splitCount: result.splitCount
    };
}

function validatePathData(data) {
    if (!data || !data.path || data.path.length === 0) return { valid: false, reason: 'Generated path is empty.' };
    const p = data.path;
    if (p[0][0] !== 0) return { valid: false, reason: 'Path does not start at left edge.' };
    if (p[p.length - 1][0] !== SIZE - 1) return { valid: false, reason: 'Path does not reach right edge.' };
    for (let i = 1; i < p.length; i++) {
        const dx = Math.abs(p[i][0] - p[i - 1][0]), dy = Math.abs(p[i][1] - p[i - 1][1]);
        if (!((dx === 1 && dy === 0) || (dx === 0 && dy === 1))) return { valid: false, reason: 'Path not contiguous at step ' + i + '.' };
    }
    for (const cell of p) if (cell[0] < 0 || cell[0] >= SIZE || cell[1] < 0 || cell[1] >= SIZE) return { valid: false, reason: 'Path cell out of bounds.' };
    if (data.buildableCount < MIN_BUILDABLE_CELLS) return { valid: false, reason: 'Not enough buildable cells (' + data.buildableCount + '). Need ' + MIN_BUILDABLE_CELLS + '.' };
    if (data.splits) {
        for (const split of data.splits) {
            if (split.atMainIndex < 0 || split.atMainIndex >= p.length) return { valid: false, reason: 'Split start index out of bounds.' };
            if (split.mergeMainIndex < 0 || split.mergeMainIndex >= p.length) return { valid: false, reason: 'Split merge index out of bounds.' };
            if (split.mergeMainIndex <= split.atMainIndex + 5) return { valid: false, reason: 'Split too short (need at least 6 main-path steps between start and merge).' };
            for (const [bx, by] of split.branchPath) {
                if (bx < 0 || bx >= SIZE || by < 0 || by >= SIZE) return { valid: false, reason: 'Branch cell out of bounds.' };
            }
        }
    }
    return { valid: true, reason: '' };
}
