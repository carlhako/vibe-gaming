// ── DOM REFS ──
const screenEl = document.getElementById('screen');
const desktop = document.getElementById('desktop');
const popupContainer = document.getElementById('popup-container');
const bsod = document.getElementById('bsod');
const bsodScore = document.getElementById('bsod-final-score');
const scoreNum = document.getElementById('score-num');
const activeNum = document.getElementById('active-num');
const maxNum = document.getElementById('max-num');
const bossIndicator = document.getElementById('boss-indicator');
const taskbarClock = document.getElementById('taskbar-clock');
const startBtn = document.getElementById('start-btn');
const startMenu = document.getElementById('start-menu');

// ── AD CONTENT POOL ──
const adPool = [
    { emoji: '🎉', title: 'CONGRATULATIONS!', body: 'You are the 1,000,000th visitor! Claim your FREE prize now!', marquee: '★ CLICK HERE ★' },
    { emoji: '⚠️', title: 'WARNING!', body: 'Your computer is INFECTED with 47 viruses! Download FREE scanner now!', marquee: 'SCAN NOW - FREE!' },
    { emoji: '💘', title: 'HOT SINGLES!', body: 'Hot singles in YOUR area want to meet YOU right now!', marquee: 'CLICK TO MEET THEM!' },
    { emoji: '💰', title: 'YOU WON!', body: 'You\'ve won a FREE iPod Nano! Enter your email to claim!', marquee: 'CLAIM YOUR iPOD!' },
    { emoji: '😊', title: 'FREE SMILEYS!', body: 'Download 10,000 FREE smileys and cursors! Smiley Central\u2122', marquee: 'DOWNLOAD NOW!' },
    { emoji: '🐒', title: 'PUNCH THE MONKEY!', body: 'Punch the monkey and WIN $20 cash instantly!', marquee: 'PUNCH TO WIN $20!' },
    { emoji: '⚡', title: 'SPEED BOOST!', body: 'Increase your internet speed 300%! FREE download!', marquee: 'FASTER INTERNET!' },
    { emoji: '🎰', title: 'FREE CRUISE!', body: 'You\'ve been selected for a FREE Bahamas cruise!', marquee: 'CLAIM YOUR TICKET!' },
    { emoji: '🔔', title: 'SYSTEM ALERT!', body: 'Your computer is running slow! Click to FIX now!', marquee: 'FIX NOW!' },
    { emoji: '🦍', title: 'BonziBuddy', body: 'BonziBuddy wants to be your friend! Install FREE toolbar!', marquee: 'INSTALL BONZI!' },
    { emoji: '🏆', title: 'PRIZE WINNER!', body: 'CONGRATULATIONS! You just WON prize #28947!', marquee: 'COLLECT PRIZE!' },
    { emoji: '💾', title: 'FREE SCREENSAVER!', body: 'Your FREE 3D flying toasters screensaver is ready!', marquee: 'DOWNLOAD NOW!' },
    { emoji: '📧', title: 'EMAIL ENHANCER!', body: 'Supercharge your Outlook with FREE smiley toolbar!', marquee: 'ADD TO OUTLOOK!' },
    { emoji: '💻', title: 'FREE LAPTOP!', body: 'You are today\'s lucky visitor! Win a FREE Dell laptop!', marquee: 'CLAIM LAPTOP!' },
    { emoji: '🔍', title: 'SEARCH ASSISTANT', body: 'Install the WebSearch toolbar for better search results!', marquee: 'INSTALL TOOLBAR!' },
    { emoji: '🎵', title: 'FREE MP3s!', body: 'Download FREE MP3 music! 100% legal! No viruses!', marquee: 'DOWNLOAD SONGS!' },
    { emoji: '📺', title: 'FREE CURSORS!', body: 'Animated cursors! Spinning globes! Rainbow trails!', marquee: 'GET CURSORS!' },
    { emoji: '💳', title: 'REFINANCE NOW!', body: 'Lowest mortgage rates in history! Refinance today!', marquee: 'LOW RATES!' }
];

// ── BOSS AD POOL ──
const bossAdPool = [
    { emoji: '😈', title: '😈 DEMON POPUP!', body: 'You cannot close me, mortal! MUAHAHAHA!', marquee: 'TRY TO CLICK THE X!' },
    { emoji: '👹', title: '👹 HELL\'S ADWARE', body: 'I am the pop-up you will NEVER defeat!', marquee: 'JUST TRY IT!' },
    { emoji: '💀', title: '💀 DOOM POPUP', body: 'Your clicks are WEAK! I consume your desktop!', marquee: 'YOU CAN\'T WIN!' },
    { emoji: '🔥', title: '🔥 INFERNAL OFFER', body: 'Spawned from the depths of badware! I dodge all!', marquee: 'CATCH ME IF YOU CAN!' },
    { emoji: '🦹', title: '🦹 SUPER VILLAIN AD', body: 'Ha! Your mouse is no match for my agility!', marquee: 'NICE TRY, HUMAN!' }
];

// ── HELPERS ──
function getDesktopBounds() {
    const th = document.getElementById('taskbar').offsetHeight;
    return { x: 0, y: 0, width: screenEl.clientWidth, height: screenEl.clientHeight - th };
}

function clamp(val, min, max) { return Math.max(min, Math.min(max, val)); }

function getPopupSize(isBoss) {
    const bounds = getDesktopBounds();
    const small = bounds.width < 600;
    if (isBoss) {
        return {
            width: small ? Math.floor(Math.random() * 40 + 180) : Math.floor(Math.random() * 80 + 260),
            height: small ? Math.floor(Math.random() * 40 + 160) : Math.floor(Math.random() * 60 + 190)
        };
    }
    return {
        width: small ? Math.floor(Math.random() * 60 + 150) : Math.floor(Math.random() * 100 + 200),
        height: small ? Math.floor(Math.random() * 50 + 130) : Math.floor(Math.random() * 70 + 160)
    };
}

function getRandomAd() { return adPool[Math.floor(Math.random() * adPool.length)]; }

function getRandomBossAd() { return bossAdPool[Math.floor(Math.random() * bossAdPool.length)]; }

function getSpawnDelay() {
    const tier = Math.floor(totalSpawned / 6);
    const delays = [2000, 1700, 1400, 1100, 850, 650, 500, 400];
    return delays[Math.min(tier, delays.length - 1)];
}

function updateMaxPopups() {
    const bounds = getDesktopBounds();
    const avgArea = 220 * 170;
    const desktopArea = bounds.width * bounds.height;
    MAX_POPUPS = clamp(Math.floor(desktopArea / avgArea * 0.82), 12, 25);
    maxNum.textContent = MAX_POPUPS;
}

function updateScoreDisplay() {
    scoreNum.textContent = totalClosed;
    activeNum.textContent = activePopups.length;
    if (bossActive) {
        bossIndicator.textContent = '😈' + bossDodgeCount;
        bossIndicator.style.color = '#cc0000';
    } else if (nextBossAt - totalClosed <= 5 && nextBossAt - totalClosed > 0) {
        bossIndicator.textContent = 'in ' + (nextBossAt - totalClosed);
        bossIndicator.style.color = '#cc6600';
    } else {
        bossIndicator.textContent = '—';
        bossIndicator.style.color = '#888';
    }
}

function updateClock() {
    const now = new Date();
    const h = now.getHours();
    const m = now.getMinutes().toString().padStart(2, '0');
    const ampm = h >= 12 ? 'PM' : 'AM';
    const h12 = h % 12 || 12;
    taskbarClock.textContent = h12 + ':' + m + ' ' + ampm;
}
