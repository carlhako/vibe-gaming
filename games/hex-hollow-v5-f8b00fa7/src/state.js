// ─── WEAPON DEFINITIONS ────────────────────
const weaponDefs=[
{id:'rusty_dagger',name:'Rusty Dagger',type:'melee',damage:10,range:2.2,speed:1.8,price:0,desc:'Fast stabs'},
{id:'arming_sword',name:'Arming Sword',type:'melee',damage:22,range:2.6,speed:1.4,price:75,desc:'Balanced blade'},
{id:'short_bow',name:'Short Bow',type:'ranged',damage:16,range:16,speed:1.3,price:150,desc:'Fires arrows'},
{id:'flanged_mace',name:'Flanged Mace',type:'melee',damage:32,range:2.4,speed:1.0,price:225,desc:'Crushing blows'},
{id:'crossbow',name:'Crossbow',type:'ranged',damage:38,range:20,speed:.8,price:400,desc:'Powerful bolts'},
{id:'war_hammer',name:'War Hammer',type:'melee',damage:48,range:2.8,speed:.65,price:600,desc:'Devastating smash'},
{id:'staff_of_embers',name:'Staff of Embers',type:'ranged',damage:55,range:22,speed:1.1,price:900,desc:'Fires fireballs'},
{id:'soul_reaper_scythe',name:'Soul Reaper Scythe',type:'melee',damage:65,range:3.5,speed:1.6,price:1500,desc:'Wide fast swings'},
{id:'archmage_staff',name:'Archmage Staff',type:'ranged',damage:85,range:25,speed:.7,price:2500,desc:'Chain lightning'},
];

// ─── GAME STATE ─────────────────────────────
const gameState={
coins:100,
maxHp:100,hp:100,
ownedWeapons:['rusty_dagger'],
equippedWeaponId:'rusty_dagger',
weaponUpgrades:{},
wave:0,
inArena:false,
inLobby:true,
countdownActive:false,
countdownValue:0,
gameOver:false,
attackCooldown:0,
dodgeCooldown:0,
invincible:false,
invincibleTimer:0,
dodgeDir:new THREE.Vector3(),
isDodging:false,
dodgeTimer:0,
totalWavesCleared:0,
};
function getEquippedWeapon(){
return weaponDefs.find(w=>w.id===gameState.equippedWeaponId)||weaponDefs[0];
}
function getUpgradeLevel(wid){return gameState.weaponUpgrades[wid]||0;}
function getUpgradeCost(wid){const base=weaponDefs.find(w=>w.id===wid)?.price||50;return Math.floor(base*.3)*(getUpgradeLevel(wid)+1);}
function getWeaponDamage(wDef){return wDef.damage+getUpgradeLevel(wDef.id)*5;}
function getWeaponSpeed(wDef){return wDef.speed*(1+getUpgradeLevel(wDef.id)*.1);}

// ─── PLAYER ─────────────────────────────────
const playerVelocity=new THREE.Vector3();
const playerDirection=new THREE.Vector3();
let playerYaw=Math.PI,playerPitch=-.3;
const playerSpeed=9;
let keys={w:false,a:false,s:false,d:false,space:false};

function resetPlayerPosition(){
playerGroup.position.set(0,.1,-2);
playerYaw=Math.PI;playerPitch=-.3;
playerVelocity.set(0,0,0);
gameState.dodgeCooldown=0;
gameState.attackCooldown=0;
gameState.isDodging=false;
gameState.dodgeTimer=0;
gameState.invincible=false;
gameState.invincibleTimer=0;
}

// ─── SHOP LOGIC ─────────────────────────────
function updateShopUI(){
const container=document.getElementById('shop-items');
container.innerHTML='';
for(const wDef of weaponDefs){
const owned=gameState.ownedWeapons.includes(wDef.id);
const equipped=gameState.equippedWeaponId===wDef.id;
const upgLevel=getUpgradeLevel(wDef.id);
const maxUpg=upgLevel>=5;
const div=document.createElement('div');div.className='shop-item';
const info=document.createElement('div');info.className='shop-info';
info.innerHTML=`<div class="shop-name">${wDef.name}${upgLevel>0?' +'+upgLevel:''}</div><div class="shop-stats">DMG:${getWeaponDamage(wDef)} | SPD:${getWeaponSpeed(wDef).toFixed(2)} | ${wDef.desc}</div>`;
div.appendChild(info);
const actions=document.createElement('div');actions.className='shop-actions';
if(equipped){
const btn=document.createElement('button');btn.className='btn-equipped';btn.textContent='Equipped';btn.disabled=true;actions.appendChild(btn);
}else if(owned){
const eqBtn=document.createElement('button');eqBtn.className='btn-equip';eqBtn.textContent='Equip';
eqBtn.addEventListener('click',()=>{gameState.equippedWeaponId=wDef.id;setWeaponMesh(wDef);updateShopUI();updateHUD();sfxCoin();});
actions.appendChild(eqBtn);
if(!maxUpg){
const upgBtn=document.createElement('button');upgBtn.className='btn-upgrade';
const cost=getUpgradeCost(wDef.id);
upgBtn.textContent='Upgrade ('+cost+'💰)';
if(gameState.coins>=cost){
upgBtn.addEventListener('click',()=>{
gameState.coins-=cost;
gameState.weaponUpgrades[wDef.id]=upgLevel+1;
updateShopUI();updateHUD();sfxCoin();
});
}else{upgBtn.disabled=true;upgBtn.style.opacity='.5';}
actions.appendChild(upgBtn);
}
}else{
const buyBtn=document.createElement('button');buyBtn.className='btn-buy';buyBtn.textContent='Buy ('+wDef.price+'💰)';
if(gameState.coins>=wDef.price&&wDef.price>0){
buyBtn.addEventListener('click',()=>{
gameState.coins-=wDef.price;
gameState.ownedWeapons.push(wDef.id);
gameState.equippedWeaponId=wDef.id;
setWeaponMesh(wDef);
updateShopUI();updateHUD();sfxCoin();
});
}else if(wDef.price===0){
buyBtn.className='btn-equipped';buyBtn.textContent='Starter';buyBtn.disabled=true;
}else{buyBtn.disabled=true;buyBtn.style.opacity='.5';}
actions.appendChild(buyBtn);
}
div.appendChild(actions);container.appendChild(div);
}
document.getElementById('shop-coins').textContent='Your Coins: '+gameState.coins;
const potionBtn=document.getElementById('shop-potion-btn');
potionBtn.textContent='🧪 Buy Health Potion (25💰) - Restores 40 HP';
if(gameState.coins>=25&&gameState.hp<gameState.maxHp){
potionBtn.disabled=false;potionBtn.style.opacity='1';
}else{potionBtn.disabled=true;potionBtn.style.opacity='.5';}
}

function updateHUD(){
document.getElementById('hp-bar-inner').style.width=(gameState.hp/gameState.maxHp*100)+'%';
document.getElementById('hp-text').textContent=Math.ceil(gameState.hp)+' / '+gameState.maxHp;
document.getElementById('coins-display').textContent='🪙 '+gameState.coins;
document.getElementById('weapon-display').textContent='⚔ '+getEquippedWeapon().name+(getUpgradeLevel(gameState.equippedWeaponId)>0?' +'+getUpgradeLevel(gameState.equippedWeaponId):'');
}

// ─── GAME OVER ──────────────────────────────
function showGameOver(){
gameState.gameOver=true;
gameState.inArena=false;
document.exitPointerLock();
document.getElementById('crosshair').style.display='none';
document.getElementById('game-over-overlay').style.display='flex';
document.getElementById('go-waves').textContent='Waves Cleared: '+gameState.totalWavesCleared;
document.getElementById('go-coins').textContent='Coins Lost: all but 100';
stopDrone();
clearArena();
}
