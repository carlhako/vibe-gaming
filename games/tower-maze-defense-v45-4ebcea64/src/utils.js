'use strict';

function mulberry32(a) {
    return function() {
        a |= 0; a = a + 0x6D2B79F5 | 0;
        var t = Math.imul(a ^ a >>> 15, 1 | a);
        t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
        return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
}

function generateRandomSeed() { return Math.floor(Math.random() * 4294967296); }
function seedToHex(seed) { return (seed >>> 0).toString(16).padStart(8, '0'); }
function hexToSeed(hex) { const p = parseInt(hex, 16); return isNaN(p) ? null : p >>> 0; }
function isValidHexFormat(hex) { return /^[0-9a-fA-F]{1,8}$/.test(hex) && hex.trim().length > 0; }
