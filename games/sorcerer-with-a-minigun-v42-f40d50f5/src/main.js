let lastTime = performance.now();
function gameLoop(timestamp) {
    let dt = (timestamp - lastTime) / 1000;
    if (dt > 0.2) dt = 0.2;
    if (dt <= 0) dt = 0.016;
    lastTime = timestamp;
    update(dt);
    render();
    requestAnimationFrame(gameLoop);
}

initUpgrades();
generateMap();
camera = { x: WORLD_W / 2 - W / 2, y: WORLD_H / 2 - GAME_VIEW_H / 2 };
updateMouseWorld();
updateCamera(0.016);
requestAnimationFrame(gameLoop);
console.log('Darkhold Arena v37 ready. Choose your champion! Press P to pause.');
