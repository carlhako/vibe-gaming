// ─── AUDIO SYSTEM ────────────────────────────
let audioCtx=null,muted=false,masterGain=null;
function initAudio(){if(!audioCtx){audioCtx=new(window.AudioContext||window.webkitAudioContext)();masterGain=audioCtx.createGain();masterGain.gain.value=.35;masterGain.connect(audioCtx.destination);}}
function playTone(freq,dur,type='sine',vol=.3,slide=0){
if(muted||!audioCtx)return;const t=audioCtx.currentTime;const o=audioCtx.createOscillator();const g=audioCtx.createGain();
o.type=type;o.frequency.setValueAtTime(freq,t);if(slide)o.frequency.linearRampToValueAtTime(freq+slide,t+dur);
g.gain.setValueAtTime(vol,t);g.gain.exponentialRampToValueAtTime(.001,t+dur);
o.connect(g);g.connect(masterGain);o.start(t);o.stop(t+dur);
}
function playNoise(dur,vol=.2,lowpass=800){
if(muted||!audioCtx)return;const t=audioCtx.currentTime;const buf=audioCtx.createBuffer(1,audioCtx.sampleRate*dur,audioCtx.sampleRate);
const d=buf.getChannelData(0);for(let i=0;i<d.length;i++)d[i]=(Math.random()*2-1);
const s=audioCtx.createBufferSource();s.buffer=buf;const f=audioCtx.createBiquadFilter();f.type='lowpass';f.frequency.value=lowpass;
const g=audioCtx.createGain();g.gain.setValueAtTime(vol,t);g.gain.exponentialRampToValueAtTime(.001,t+dur);
s.connect(f);f.connect(g);g.connect(masterGain);s.start(t);s.stop(t+dur);
}
function sfxClang(){playNoise(.12,.4,1200);playTone(300,.08,'square',.25);playTone(150,.1,'sawtooth',.2,-100);}
function sfxCoin(){playTone(1200,.06,'sine',.2);setTimeout(()=>playTone(1600,.06,'sine',.25),40);}
function sfxBeep(){playTone(440,.15,'square',.3);}
function sfxHurt(){playNoise(.15,.5,300);playTone(80,.2,'sawtooth',.3,-40);}
function sfxFireball(){playNoise(.25,.3,600);playTone(200,.2,'sawtooth',.2,300);}
function sfxLightning(){playNoise(.3,.5,2000);playTone(60,.15,'square',.4);playTone(80,.1,'square',.3);}
function sfxDodge(){playNoise(.1,.25,400);}
function sfxCountdown(){playTone(600,.2,'square',.4);}
function sfxGo(){playTone(800,.3,'square',.5);playTone(1200,.2,'sine',.4,200);}
let droneNode=null;
function startDrone(){
if(muted||!audioCtx)return;stopDrone();
const o=audioCtx.createOscillator();o.type='sawtooth';o.frequency.value=40;
const o2=audioCtx.createOscillator();o2.type='sine';o2.frequency.value=55;
const g=audioCtx.createGain();g.gain.value=.06;
const f=audioCtx.createBiquadFilter();f.type='lowpass';f.frequency.value=200;
o.connect(g);o2.connect(g);g.connect(f);f.connect(masterGain);
o.start();o2.start();droneNode={o,o2,g,f};
}
function stopDrone(){if(droneNode){droneNode.o.stop();droneNode.o2.stop();droneNode=null;}}
function setMute(m){
muted=m;
if(masterGain)masterGain.gain.value=m?0:.35;
if(m){stopDrone();}else if(droneNode===null && audioCtx){startDrone();}
}
document.getElementById('mute-btn').addEventListener('click',()=>{
initAudio();muted=!muted;setMute(muted);
document.getElementById('mute-btn').textContent=muted?'🔇':'🔊';
});
