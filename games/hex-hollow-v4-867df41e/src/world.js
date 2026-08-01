// ─── SCENE SETUP ────────────────────────────
const renderer=new THREE.WebGLRenderer({antialias:true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));
renderer.setSize(window.innerWidth,window.innerHeight);
renderer.shadowMap.enabled=true;
renderer.shadowMap.type=THREE.PCFSoftShadowMap;
renderer.toneMapping=THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure=1.6;
document.body.prepend(renderer.domElement);

const scene=new THREE.Scene();
scene.background=new THREE.Color(0x1a1512);
scene.fog=new THREE.FogExp2(0x1a1512,.00035);

const camera=new THREE.PerspectiveCamera(70,window.innerWidth/window.innerHeight,.3,80);
camera.position.set(0,2,3);

const ambient=new THREE.AmbientLight(0x4a3a2a,1.2);
scene.add(ambient);

const dirLight=new THREE.DirectionalLight(0xffeedd,.6);
dirLight.position.set(10,20,5);
dirLight.castShadow=true;
dirLight.shadow.mapSize.set(512,512);
dirLight.shadow.camera.near=.5;
dirLight.shadow.camera.far=60;
dirLight.shadow.camera.left=-20;
dirLight.shadow.camera.right=20;
dirLight.shadow.camera.top=20;
dirLight.shadow.camera.bottom=-20;
scene.add(dirLight);

// ─── MATERIALS & TEXTURES ────────────────────
function makeCanvasTexture(w,h,drawFn){
const c=document.createElement('canvas');c.width=w;c.height=h;
const ctx=c.getContext('2d');drawFn(ctx,w,h);
const tex=new THREE.CanvasTexture(c);tex.colorSpace=THREE.SRGBColorSpace;tex.wrapS=tex.wrapT=THREE.RepeatWrapping;
tex.magFilter=THREE.LinearFilter;tex.minFilter=THREE.LinearMipmapLinearFilter;
tex.generateMipmaps=true;return tex;
}
const stoneTex=makeCanvasTexture(256,256,(ctx,w,h)=>{
ctx.fillStyle='#3a3028';ctx.fillRect(0,0,w,h);
for(let i=0;i<300;i++){ctx.fillStyle=`rgba(${30+Math.random()*40},${20+Math.random()*30},${15+Math.random()*20},.4)`;ctx.fillRect(Math.random()*w,Math.random()*h,8+Math.random()*16,6+Math.random()*12);}
for(let i=0;i<20;i++){ctx.strokeStyle=`rgba(20,15,10,.3)`;ctx.lineWidth=1+Math.random()*2;ctx.beginPath();ctx.moveTo(Math.random()*w,Math.random()*h);ctx.lineTo(Math.random()*w,Math.random()*h);ctx.stroke();}
});
const darkStoneTex=makeCanvasTexture(256,256,(ctx,w,h)=>{
ctx.fillStyle='#1a1512';ctx.fillRect(0,0,w,h);
for(let i=0;i<200;i++){ctx.fillStyle=`rgba(${20+Math.random()*25},${15+Math.random()*20},${10+Math.random()*15},.5)`;ctx.fillRect(Math.random()*w,Math.random()*h,10+Math.random()*20,8+Math.random()*14);}
});
const floorTex=makeCanvasTexture(256,256,(ctx,w,h)=>{
ctx.fillStyle='#2a231c';ctx.fillRect(0,0,w,h);
for(let x=0;x<w;x+=32){for(let y=0;y<h;y+=32){ctx.strokeStyle='#1a1510';ctx.lineWidth=1;ctx.strokeRect(x,y,32,32);}}
for(let i=0;i<400;i++){ctx.fillStyle=`rgba(${15+Math.random()*20},${10+Math.random()*15},${5+Math.random()*10},.3)`;ctx.fillRect(Math.random()*w,Math.random()*h,4+Math.random()*8,3+Math.random()*6);}
});
const stoneMat=new THREE.MeshStandardMaterial({map:stoneTex,roughness:.85,metalness:.05,color:0x998877});
const darkStoneMat=new THREE.MeshStandardMaterial({map:darkStoneTex,roughness:.9,metalness:.05,color:0x665544});
const floorMat=new THREE.MeshStandardMaterial({map:floorTex,roughness:.9,metalness:.02,color:0x887766});
const columnMat=new THREE.MeshStandardMaterial({map:darkStoneTex,roughness:.8,metalness:.1,color:0x776655});
const woodMat=new THREE.MeshStandardMaterial({roughness:.7,metalness:.05,color:0x5a3a20});
const ironMat=new THREE.MeshStandardMaterial({roughness:.4,metalness:.8,color:0x555555});
const goldMat=new THREE.MeshStandardMaterial({roughness:.2,metalness:.9,color:0xf1c40f,emissive:0x332200,emissiveIntensity:.3});

// ─── BUILD LOBBY ────────────────────────────
const lobbyGroup=new THREE.Group();scene.add(lobbyGroup);
const lobbyFloor=5,lobbyDepth=8,lobbyHeight=5;
function addBox(g,w,h,d,mat,x,y,z){const m=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),mat);m.position.set(x,y,z);m.castShadow=true;m.receiveShadow=true;g.add(m);return m;}
// Floor
const lf=new THREE.Mesh(new THREE.PlaneGeometry(lobbyFloor,lobbyDepth),floorMat);lf.rotation.x=-Math.PI/2;lf.position.set(0,0,0);lf.receiveShadow=true;lobbyGroup.add(lf);
// Walls
addBox(lobbyGroup,lobbyFloor+.4,lobbyHeight,.4,stoneMat,0,lobbyHeight/2,-lobbyDepth/2);
addBox(lobbyGroup,lobbyFloor+.4,lobbyHeight,.4,stoneMat,0,lobbyHeight/2,lobbyDepth/2);
addBox(lobbyGroup,.4,lobbyHeight,lobbyDepth+.4,stoneMat,-lobbyFloor/2,lobbyHeight/2,0);
addBox(lobbyGroup,.4,lobbyHeight,lobbyDepth+.4,stoneMat,lobbyFloor/2,lobbyHeight/2,0);
// Ceiling
addBox(lobbyGroup,lobbyFloor+.4,.3,lobbyDepth+.4,darkStoneMat,0,lobbyHeight,0);
// Shop counter
addBox(lobbyGroup,2,.8,.6,woodMat,0,.4,-lobbyDepth/2+1.2);
addBox(lobbyGroup,2.2,.1,.8,woodMat,0,.85,-lobbyDepth/2+1.2);
// Doorway (archway at north end)
const doorwayArch=new THREE.Group();doorwayArch.position.set(0,0,lobbyDepth/2);
const archLeft=addBox(doorwayArch,.3,lobbyHeight,.3,stoneMat,-1,lobbyHeight/2,0);
const archRight=addBox(doorwayArch,.3,lobbyHeight,.3,stoneMat,1,lobbyHeight/2,0);
const archTop=addBox(doorwayArch,2.6,.3,.3,stoneMat,0,lobbyHeight-.15,0);
lobbyGroup.add(doorwayArch);
// Lobby torches
function addTorch(g,x,y,z){
const stick=new THREE.Mesh(new THREE.CylinderGeometry(.06,.06,1,6),woodMat);stick.position.set(x,y+.5,z);stick.castShadow=true;g.add(stick);
const flame=new THREE.Mesh(new THREE.SphereGeometry(.15,8,8),new THREE.MeshStandardMaterial({color:0xff6600,emissive:0xff4400,emissiveIntensity:2,roughness:.3}));flame.position.set(x,y+1,z);g.add(flame);
const light=new THREE.PointLight(0xff8830,15,8);light.position.set(x,y+1,z);light.castShadow=true;light.shadow.mapSize.set(128,128);g.add(light);
return {light,flame};
}
const lobbyTorches=[addTorch(lobbyGroup,-2,2,-lobbyDepth/2+.3),addTorch(lobbyGroup,2,2,-lobbyDepth/2+.3),addTorch(lobbyGroup,-1.5,2,lobbyDepth/2-.3),addTorch(lobbyGroup,1.5,2,lobbyDepth/2-.3)];

// ─── BUILD ARENA ────────────────────────────
const arenaGroup=new THREE.Group();arenaGroup.position.set(0,0,-25);scene.add(arenaGroup);
const arenaSize=22,arenaHeight=9;
// Floor
const af=new THREE.Mesh(new THREE.PlaneGeometry(arenaSize,arenaSize),floorMat);af.rotation.x=-Math.PI/2;af.receiveShadow=true;arenaGroup.add(af);
// Walls
addBox(arenaGroup,arenaSize+.5,arenaHeight,.5,stoneMat,0,arenaHeight/2,-arenaSize/2);
addBox(arenaGroup,arenaSize+.5,arenaHeight,.5,stoneMat,0,arenaHeight/2,arenaSize/2);
addBox(arenaGroup,.5,arenaHeight,arenaSize+.5,stoneMat,-arenaSize/2,arenaHeight/2,0);
addBox(arenaGroup,.5,arenaHeight,arenaSize+.5,stoneMat,arenaSize/2,arenaHeight/2,0);
// Ceiling
addBox(arenaGroup,arenaSize+.5,.4,arenaSize+.5,darkStoneMat,0,arenaHeight,0);
// Columns
const pillarPositions=[];
for(let x=-7;x<=7;x+=7){for(let z=-7;z<=7;z+=7){
const col=new THREE.Mesh(new THREE.CylinderGeometry(.5,.6,arenaHeight,12),columnMat);
col.position.set(x,arenaHeight/2,z);col.castShadow=true;col.receiveShadow=true;
arenaGroup.add(col);pillarPositions.push({x,z,radius:.6});
}}
// Extra columns
for(let x=-3.5;x<=3.5;x+=7){for(let z=-3.5;z<=3.5;z+=7){
if(Math.abs(x)<1&&Math.abs(z)<1)continue;
const col=new THREE.Mesh(new THREE.CylinderGeometry(.4,.5,arenaHeight,12),columnMat);
col.position.set(x,arenaHeight/2,z);col.castShadow=true;col.receiveShadow=true;
arenaGroup.add(col);pillarPositions.push({x,z,radius:.5});
}}
// Arena torches on walls
const arenaTorches=[];
for(let i=0;i<8;i++){
const angle=(i/8)*Math.PI*2;
const wx=Math.cos(angle)*(arenaSize/2-.1);
const wz=Math.sin(angle)*(arenaSize/2-.1);
const stick=new THREE.Mesh(new THREE.CylinderGeometry(.07,.07,1.2,6),woodMat);
stick.position.set(wx,2.5,wz);arenaGroup.add(stick);
const flame=new THREE.Mesh(new THREE.SphereGeometry(.18,8,8),new THREE.MeshStandardMaterial({color:0xff6600,emissive:0xff4400,emissiveIntensity:2.5,roughness:.3}));
flame.position.set(wx,3.2,wz);arenaGroup.add(flame);
const light=new THREE.PointLight(0xff8830,18,10);
light.position.set(wx,3.2,wz);light.castShadow=true;light.shadow.mapSize.set(128,128);
arenaGroup.add(light);arenaTorches.push({light,flame});
}
// Column torches
for(const p of pillarPositions){
if(Math.random()>.5)continue;
const flame=new THREE.Mesh(new THREE.SphereGeometry(.14,8,8),new THREE.MeshStandardMaterial({color:0xff6600,emissive:0xff4400,emissiveIntensity:2,roughness:.3}));
flame.position.set(p.x,arenaHeight-.4,p.z);arenaGroup.add(flame);
const light=new THREE.PointLight(0xff7720,10,7);
light.position.set(p.x,arenaHeight-.4,p.z);light.castShadow=true;light.shadow.mapSize.set(64,64);
arenaGroup.add(light);arenaTorches.push({light,flame});
}

// ─── PLAYER MESH ────────────────────────────
const playerGroup=new THREE.Group();scene.add(playerGroup);
const bodyMesh=new THREE.Mesh(new THREE.CapsuleGeometry(.3,.7,4,8),new THREE.MeshStandardMaterial({color:0x5a4a3a,roughness:.6}));
bodyMesh.position.y=.85;bodyMesh.castShadow=true;playerGroup.add(bodyMesh);
const headMesh=new THREE.Mesh(new THREE.SphereGeometry(.28,8,8),new THREE.MeshStandardMaterial({color:0xd4b896,roughness:.6}));
headMesh.position.y=1.55;headMesh.castShadow=true;playerGroup.add(headMesh);
const helmMesh=new THREE.Mesh(new THREE.SphereGeometry(.32,8,6,.1,Math.PI*2,0,Math.PI*.6),new THREE.MeshStandardMaterial({color:0x444444,roughness:.3,metalness:.7}));
helmMesh.position.y=1.58;playerGroup.add(helmMesh);
const weaponGroup=new THREE.Group();weaponGroup.position.set(.35,1.2,.15);playerGroup.add(weaponGroup);

// ─── WEAPON MESH FUNCTIONS ──────────────────
function makeDaggerMesh(){const g=new THREE.Group();const b=new THREE.Mesh(new THREE.BoxGeometry(.06,.45,.03),ironMat);b.position.y=.2;g.add(b);const h=new THREE.Mesh(new THREE.CylinderGeometry(.04,.04,.15,6),woodMat);h.position.y=.5;g.add(h);return g;}
function makeSwordMesh(){const g=new THREE.Group();const b=new THREE.Mesh(new THREE.BoxGeometry(.08,.7,.04),ironMat);b.position.y=.35;g.add(b);const h=new THREE.Mesh(new THREE.CylinderGeometry(.05,.05,.2,6),woodMat);h.position.y=.75;g.add(h);const guard=new THREE.Mesh(new THREE.BoxGeometry(.2,.06,.08),ironMat);guard.position.y=.62;g.add(guard);return g;}
function makeMaceMesh(){const g=new THREE.Group();const hd=new THREE.Mesh(new THREE.CylinderGeometry(.12,.1,.35,8),ironMat);hd.position.y=.4;g.add(hd);const h=new THREE.Mesh(new THREE.CylinderGeometry(.05,.05,.5,6),woodMat);h.position.y=.75;g.add(h);return g;}
function makeHammerMesh(){const g=new THREE.Group();const hd=new THREE.Mesh(new THREE.BoxGeometry(.2,.35,.18),ironMat);hd.position.y=.4;g.add(hd);const h=new THREE.Mesh(new THREE.CylinderGeometry(.06,.06,.6,6),woodMat);h.position.y=.8;g.add(h);return g;}
function makeScytheMesh(){const g=new THREE.Group();const h=new THREE.Mesh(new THREE.CylinderGeometry(.05,.05,.8,6),woodMat);h.position.y=.4;g.add(h);const blade=new THREE.Mesh(new THREE.BoxGeometry(.06,.5,.25),new THREE.MeshStandardMaterial({color:0x8888cc,roughness:.2,metalness:.9,emissive:0x111133,emissiveIntensity:.5}));blade.position.set(.15,.55,0);blade.rotation.z=.4;g.add(blade);return g;}
function makeBowMesh(){const g=new THREE.Group();const arc=new THREE.Mesh(new THREE.TorusGeometry(.25,.04,6,8,Math.PI),woodMat);arc.position.y=.3;arc.rotation.z=Math.PI/2;g.add(arc);const str=new THREE.Mesh(new THREE.CylinderGeometry(.01,.01,.5,4),new THREE.MeshStandardMaterial({color:0xddddcc}));str.position.y=.3;g.add(str);return g;}
function makeCrossbowMesh(){const g=new THREE.Group();const body=new THREE.Mesh(new THREE.BoxGeometry(.15,.2,.4),woodMat);body.position.y=.3;g.add(body);const bow=new THREE.Mesh(new THREE.BoxGeometry(.04,.08,.6),ironMat);bow.position.set(0,.3,.25);g.add(bow);return g;}
function makeStaffMesh(orbColor=0xff4400){const g=new THREE.Group();const rod=new THREE.Mesh(new THREE.CylinderGeometry(.04,.04,1,8),woodMat);rod.position.y=.5;g.add(rod);const orb=new THREE.Mesh(new THREE.SphereGeometry(.1,8,8),new THREE.MeshStandardMaterial({color:orbColor,emissive:orbColor,emissiveIntensity:1.5,roughness:.2}));orb.position.y=1.05;g.add(orb);return g;}

function setWeaponMesh(wDef){
while(weaponGroup.children.length>0)weaponGroup.remove(weaponGroup.children[0]);
let mesh;
switch(wDef.id){
case 'rusty_dagger':mesh=makeDaggerMesh();break;
case 'arming_sword':mesh=makeSwordMesh();break;
case 'short_bow':mesh=makeBowMesh();break;
case 'flanged_mace':mesh=makeMaceMesh();break;
case 'crossbow':mesh=makeCrossbowMesh();break;
case 'war_hammer':mesh=makeHammerMesh();break;
case 'staff_of_embers':mesh=makeStaffMesh(0xff4400);break;
case 'soul_reaper_scythe':mesh=makeScytheMesh();break;
case 'archmage_staff':mesh=makeStaffMesh(0x8844ff);break;
default:mesh=makeDaggerMesh();
}
weaponGroup.add(mesh);
}

// ─── ENEMY MESH ──────────────────────────────
function createEnemyMesh(type){
const g=new THREE.Group();
if(type==='ghost'){
const body=new THREE.Mesh(new THREE.SphereGeometry(.35,8,8),new THREE.MeshStandardMaterial({color:0xaaccff,roughness:.2,metalness:.1,transparent:true,opacity:.55,emissive:0x334466,emissiveIntensity:.8}));
body.position.y=.5;g.add(body);
const eyeL=new THREE.Mesh(new THREE.SphereGeometry(.08,6,6),new THREE.MeshStandardMaterial({color:0xffffff,emissive:0x88aaff,emissiveIntensity:2}));
eyeL.position.set(-.1,.55,.28);g.add(eyeL);
const eyeR=new THREE.Mesh(new THREE.SphereGeometry(.08,6,6),new THREE.MeshStandardMaterial({color:0xffffff,emissive:0x88aaff,emissiveIntensity:2}));
eyeR.position.set(.1,.55,.28);g.add(eyeR);
g.userData.floatOffset=Math.random()*Math.PI*2;
}else if(type==='zombie'){
const body=new THREE.Mesh(new THREE.CapsuleGeometry(.35,.7,4,8),new THREE.MeshStandardMaterial({color:0x5a7a4a,roughness:.8}));
body.position.y=.85;g.add(body);
const head=new THREE.Mesh(new THREE.SphereGeometry(.3,8,8),new THREE.MeshStandardMaterial({color:0x6a8a5a,roughness:.7}));
head.position.y=1.55;g.add(head);
const eyeL=new THREE.Mesh(new THREE.SphereGeometry(.06,6,6),new THREE.MeshStandardMaterial({color:0xffff00,emissive:0xaaaa00,emissiveIntensity:2}));
eyeL.position.set(-.1,1.6,.24);g.add(eyeL);
const eyeR=new THREE.Mesh(new THREE.SphereGeometry(.06,6,6),new THREE.MeshStandardMaterial({color:0xffff00,emissive:0xaaaa00,emissiveIntensity:2}));
eyeR.position.set(.1,1.6,.24);g.add(eyeR);
}else if(type==='skeleton'){
const body=new THREE.Mesh(new THREE.CapsuleGeometry(.2,.6,4,8),new THREE.MeshStandardMaterial({color:0xddd8c8,roughness:.5}));
body.position.y=.7;g.add(body);
const head=new THREE.Mesh(new THREE.SphereGeometry(.22,8,8),new THREE.MeshStandardMaterial({color:0xeee8d8,roughness:.4}));
head.position.y=1.3;g.add(head);
const eyeL=new THREE.Mesh(new THREE.SphereGeometry(.05,6,6),new THREE.MeshStandardMaterial({color:0xff4444,emissive:0xff0000,emissiveIntensity:2.5}));
eyeL.position.set(-.08,1.32,.18);g.add(eyeL);
const eyeR=new THREE.Mesh(new THREE.SphereGeometry(.05,6,6),new THREE.MeshStandardMaterial({color:0xff4444,emissive:0xff0000,emissiveIntensity:2.5}));
eyeR.position.set(.08,1.32,.18);g.add(eyeR);
}else if(type==='witch'){
const body=new THREE.Mesh(new THREE.ConeGeometry(.4,.9,8),new THREE.MeshStandardMaterial({color:0x3a2040,roughness:.6,emissive:0x1a0a20,emissiveIntensity:.3}));
body.position.y=.5;g.add(body);
const head=new THREE.Mesh(new THREE.SphereGeometry(.25,8,8),new THREE.MeshStandardMaterial({color:0xccbbaa,roughness:.5}));
head.position.y=1.2;g.add(head);
const eyeL=new THREE.Mesh(new THREE.SphereGeometry(.06,6,6),new THREE.MeshStandardMaterial({color:0xff00ff,emissive:0xaa00aa,emissiveIntensity:3}));
eyeL.position.set(-.08,1.22,.2);g.add(eyeL);
const eyeR=new THREE.Mesh(new THREE.SphereGeometry(.06,6,6),new THREE.MeshStandardMaterial({color:0xff00ff,emissive:0xaa00aa,emissiveIntensity:3}));
eyeR.position.set(.08,1.22,.2);g.add(eyeR);
const hat=new THREE.Mesh(new THREE.ConeGeometry(.28,.5,8),new THREE.MeshStandardMaterial({color:0x1a1020,roughness:.5}));
hat.position.y=1.55;g.add(hat);
g.userData.magicGlow=0;
}
g.castShadow=true;return g;
}
