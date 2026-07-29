const characters = {
    sorcerer: { name: 'Sorcerer', color: '#8b3fb5', robeColor: '#5c1d8a', skinColor: '#e8c9a0', desc: 'Hurls fireballs & summons skeletal minions. A master of dark arts.',
        primary: { name: 'Fireball', damage: 22, cooldown: 0.85, speed: 300, range: 450, color: '#ff6622', size: 6, isRanged: true, arcAngle: 0 },
        secondary: { name: 'Summon Skeleton', cooldown: 5.0, isRanged: false, isSummon: true, minionHealth: 30, minionDamage: 7, minionSpeed: 110, minionSize: 12, minionColor: '#e8dcc8', minionAttackRange: 22, minionAttackCooldown: 0.9, specialName: 'Extra Skeletons', specialDesc: '+1 skeleton per level', aoeName: 'Skeleton Horde', aoeDesc: '+1 skeleton per level' },
        available: true },
    wizard: { name: 'Wizard', color: '#3b7fcf', robeColor: '#1a4a7a', skinColor: '#f0dbb8', desc: 'Quick wand strikes & piercing magic bolts. High attack speed.',
        primary: { name: 'Wand Strike', damage: 13, cooldown: 0.35, range: 38, arcAngle: 0.7, color: '#7cb8f0', isRanged: false },
        secondary: { name: 'Magic Bolt', damage: 22, cooldown: 1.0, speed: 420, range: 550, color: '#44ccff', size: 5, isRanged: true, isSummon: false, specialName: 'Piercing Bolts', specialDesc: 'Bolts pass through enemies', aoeName: 'Chain Lightning', aoeDesc: 'Bolts bounce to nearby' },
        available: false },
    knight: { name: 'Knight', color: '#b0b8c0', robeColor: '#5a5d62', skinColor: '#e0c8a8', desc: 'Sword & zombie swing. Durable melee powerhouse.',
        primary: { name: 'Sword Slash', damage: 24, cooldown: 0.5, range: 48, arcAngle: 0.9, color: '#d0d8e0', isRanged: false },
        secondary: { name: 'Zombie Swing', damage: 50, cooldown: 2.0, range: 58, arcAngle: 1.3, color: '#44ff55', isRanged: false, isSummon: false, specialName: 'Shockwave', specialDesc: 'Swing sends a shockwave', aoeName: 'Necrosis', aoeDesc: 'Bonus dmg vs weakened foes' },
        available: true }
};

const enemyTypes = {
    skeleton: { name: 'Skeleton', baseHP: 35, baseDamage: 8, speed: 70, size: 13, color: '#e8e0d0', xpValue: 1, tokenDrop: 1 },
    ghost: { name: 'Wraith', baseHP: 25, baseDamage: 14, speed: 100, size: 11, color: '#8899cc', xpValue: 2, tokenDrop: 2 },
    demon: { name: 'Demon', baseHP: 70, baseDamage: 18, speed: 55, size: 16, color: '#cc3333', xpValue: 3, tokenDrop: 3 },
    brute: { name: 'Brute', baseHP: 100, baseDamage: 22, speed: 40, size: 19, color: '#664422', xpValue: 4, tokenDrop: 3 },
    witch: { name: 'Witch', baseHP: 180, baseDamage: 8, speed: 55, size: 14, color: '#6b3fa0', xpValue: 5, tokenDrop: 5 },
    devil: { name: 'Devil', baseHP: 16, baseDamage: 10, speed: 150, size: 9, color: '#dd3322', xpValue: 1, tokenDrop: 1 },
    earthshaker: { name: 'Earthshaker', baseHP: 4500, baseDamage: 15, speed: 55, size: 22, color: '#5a4a3a', xpValue: 8, tokenDrop: 8 }
};

function initUpgrades() { playerUpgrades = { prim_dmg: 0, prim_spd: 0, prim_rng: 0, prim_spec: 0, sec_dmg: 0, sec_spd: 0, sec_special: 0, sec_aoe: 0, minigun: 0, railgun: 0, minion_minigun: 0, minion_railgun: 0, skel_spd: 0, vit: 0, annihilator: 0, knight_sword: 0 }; }
function hasMinigun() { return selectedCharacter === 'sorcerer' && (playerUpgrades['minigun'] || 0) >= 1; }
function hasRailgun() { return selectedCharacter === 'sorcerer' && (playerUpgrades['railgun'] || 0) >= 1; }
function hasMinionMinigun() { return selectedCharacter === 'sorcerer' && (playerUpgrades['minion_minigun'] || 0) >= 1; }
function hasMinionRailgun() { return selectedCharacter === 'sorcerer' && (playerUpgrades['minion_railgun'] || 0) >= 1; }
function hasAnnihilator() { return selectedCharacter === 'sorcerer' && (playerUpgrades['annihilator'] || 0) >= 1; }
function hasKnightBlackSword() { return selectedCharacter === 'knight' && (playerUpgrades['knight_sword'] || 0) >= 1; }
function allUpgradesMaxed() {
    for (const key of Object.keys(playerUpgrades)) {
        if (key === 'annihilator' || key === 'knight_sword') continue;
        if ((playerUpgrades[key] || 0) < getUpgradeMaxLevel(key)) return false;
    }
    return true;
}
function getSkeletonSpeedMultiplier() { const lvl = playerUpgrades['skel_spd'] || 0; if (lvl === 0) return 1.0; return [110, 180, 250][lvl - 1] / 110; }
function getVitalityBonus() { const lvl = playerUpgrades['vit'] || 0; if (lvl === 0) return 0; return [0, 10, 20, 40][lvl] * wave; }

function getUpgradeCost(upgradeId) {
    const level = playerUpgrades[upgradeId] || 0;
    const baseCosts = { prim_dmg: [3, 6, 10, 15, 22], prim_spd: [3, 6, 10, 15], prim_rng: [4, 8, 14, 20], prim_spec: [8, 14, 22], sec_dmg: [4, 7, 12, 18, 25], sec_spd: [4, 8, 14, 22], sec_special: [8, 15, 25], sec_aoe: [10, 18, 28], minigun: [50], railgun: [65], minion_minigun: [200], minion_railgun: [500], skel_spd: [5, 10, 18], vit: [15, 25, 40], annihilator: [1000], knight_sword: [50] };
    const costs = baseCosts[upgradeId] || [3, 6, 10, 15]; if (level >= costs.length) return Infinity; return costs[level];
}
function getUpgradeMaxLevel(upgradeId) { const maxes = { prim_dmg: 5, prim_spd: 4, prim_rng: 4, prim_spec: 3, sec_dmg: 5, sec_spd: 4, sec_special: 3, sec_aoe: 3, minigun: 1, railgun: 1, minion_minigun: 1, minion_railgun: 1, skel_spd: 3, vit: 3, annihilator: 1, knight_sword: 1 }; return maxes[upgradeId] || 3; }
function canBuyUpgrade(upgradeId) {
    const level = playerUpgrades[upgradeId] || 0; if (level >= getUpgradeMaxLevel(upgradeId) || tokens < getUpgradeCost(upgradeId)) return false;
    if (upgradeId === 'prim_spec' && (playerUpgrades['prim_dmg'] || 0) < 2) return false;
    if (upgradeId === 'sec_special' && (playerUpgrades['sec_dmg'] || 0) < 2) return false;
    if (upgradeId === 'sec_aoe' && (playerUpgrades['sec_spd'] || 0) < 2) return false;
    if (upgradeId === 'minigun' && (playerUpgrades['prim_spec'] || 0) < 2) return false;
    if (upgradeId === 'railgun' && (playerUpgrades['prim_dmg'] || 0) < 5) return false;
    if (upgradeId === 'minion_minigun' && (playerUpgrades['minigun'] || 0) < 1) return false;
    if (upgradeId === 'minion_railgun' && (playerUpgrades['railgun'] || 0) < 1) return false;
    if (upgradeId === 'annihilator' && !allUpgradesMaxed()) return false;
    if (upgradeId === 'knight_sword' && (playerUpgrades['prim_dmg'] || 0) < 3) return false;
    return true;
}
function getPlayerStat(base, upgradeId, perLevel) { return base * (1 + (playerUpgrades[upgradeId] || 0) * perLevel); }
