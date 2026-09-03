/*!
 * rt.js — the platform-injected real-time client (VG_RT) for multiplayer games.
 *
 * This file is served from /vendor/rt/ with `Access-Control-Allow-Origin: *`
 * (a sandboxed game sends `Origin: null`) and injected into every multiplayer
 * game's served HTML by the generation pipeline — the model never writes
 * WebSocket code, exactly as it never writes the three.js import map. See
 * multiplayer.py and openspec/changes/add-multiplayer-games/.
 *
 * It owns: the hub URL (derived from `location` + the injected game_id — the
 * game never hard-codes an origin), the `join` handshake, reconnect with
 * exponential backoff, the `ping`->`pong` echo, JSON framing, and a
 * client-side max payload size that matches the hub's frame cap.
 *
 * Game API (stable):
 *   VG_RT.send(d)                 -> relay an opaque JSON value to the room
 *   VG_RT.on(evt, cb)            -> 'welcome' | 'roster' | 'peers' | 'msg' | 'status'
 *   VG_RT.off(evt, cb)
 *   VG_RT.me                     -> this client's hub-assigned member id (null until 'welcome')
 *   VG_RT.roster                 -> [{id, nick, ping_ms}, ...] (all members, includes me)
 *   VG_RT.peers                  -> roster minus me
 *   VG_RT.max                    -> room capacity (from 'welcome')
 *   VG_RT.setNick(str)          -> set/allow-change the display nickname
 *   VG_RT.connected             -> bool
 *
 * Peer payloads ('msg' events) are UNTRUSTED DATA. Never pass them to eval or
 * build markup from them — use textContent, not innerHTML.
 */
(function () {
  "use strict";

  if (window.VG_RT && window.VG_RT.__installed) return;

  // Must agree with rt_hub.py's `max_frame_bytes` default. The whole JSON
  // frame is bounded by the hub; this budget is for the `d` payload alone,
  // leaving generous headroom for the {"t":"msg","d":...} wrapper.
  var MAX_FRAME_BYTES = 16384;
  var MAX_PAYLOAD_BYTES = MAX_FRAME_BYTES - 256;

  var script =
    document.currentScript ||
    (function () {
      var s = document.getElementsByTagName("script");
      for (var i = s.length - 1; i >= 0; i--) {
        if (s[i].src && s[i].src.indexOf("/vendor/rt/rt.js") !== -1) return s[i];
      }
      return null;
    })();

  var gameId = (script && script.getAttribute("data-game-id")) || "";
  var maxFrameAttr = script && parseInt(script.getAttribute("data-max-frame"), 10);
  if (maxFrameAttr && maxFrameAttr > 512) {
    MAX_FRAME_BYTES = maxFrameAttr;
    MAX_PAYLOAD_BYTES = MAX_FRAME_BYTES - 256;
  }
  // Optional explicit hub origin (e.g. "ws://localhost:8620"). In production
  // the reverse proxy makes /rt/ same-origin and this is unset; it exists for
  // local dev where the hub runs on its own port with no proxy in front.
  var hubBase = script && script.getAttribute("data-hub");

  function hubUrl() {
    if (hubBase) return hubBase.replace(/\/+$/, "") + "/rt/" + gameId;
    var scheme = location.protocol === "https:" ? "wss:" : "ws:";
    return scheme + "//" + location.host + "/rt/" + gameId;
  }

  var listeners = { welcome: [], roster: [], peers: [], msg: [], status: [] };
  function emit(evt, arg) {
    var cbs = listeners[evt];
    if (!cbs) return;
    for (var i = 0; i < cbs.length; i++) {
      try {
        cbs[i](arg);
      } catch (e) {
        /* a game handler throwing must not break the socket */
      }
    }
  }

  var ws = null;
  var backoff = 500; // ms, doubles to a ceiling
  var reconnectTimer = null;
  var closedByUs = false;

  var api = {
    __installed: true,
    me: null,
    max: 0,
    roster: [],
    peers: [],
    nick: "",
    connected: false,

    on: function (evt, cb) {
      if (listeners[evt] && typeof cb === "function") listeners[evt].push(cb);
      return api;
    },
    off: function (evt, cb) {
      var cbs = listeners[evt];
      if (!cbs) return api;
      var i = cbs.indexOf(cb);
      if (i !== -1) cbs.splice(i, 1);
      return api;
    },
    setNick: function (nick) {
      api.nick = nick == null ? "" : String(nick);
      if (api.connected) sendRaw({ t: "join", nick: api.nick });
      return api;
    },
    send: function (d) {
      // `api.connected` / `ws` both lag the socket's real state — they only
      // update from the async `close` event — so a socket already in CLOSING
      // or CLOSED still passes those checks. `ws.send()` on such a socket does
      // not throw; Blink logs it at error level, which fails the generation
      // smoke test. Gate on the live readyState instead.
      if (!api.connected || !ws || ws.readyState !== WebSocket.OPEN) return false;
      var payload;
      try {
        payload = JSON.stringify({ t: "msg", d: d });
      } catch (e) {
        return false;
      }
      if (byteLength(payload) > MAX_FRAME_BYTES) return false;
      // The `d`-only check gives the game a stable number to design against.
      if (byteLength(JSON.stringify(d)) > MAX_PAYLOAD_BYTES) return false;
      try {
        ws.send(payload);
        return true;
      } catch (e) {
        return false;
      }
    },
  };

  function byteLength(str) {
    // TextEncoder is available in every sandboxed game context.
    return new TextEncoder().encode(str).length;
  }

  function sendRaw(obj) {
    // Same reasoning as api.send(): suppress join/pong control frames once the
    // socket has left OPEN so no write hits a CLOSING/CLOSED socket.
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify(obj));
    } catch (e) {
      /* socket went away; reconnect logic handles it */
    }
  }

  function recomputePeers() {
    api.peers = api.roster.filter(function (m) {
      return m.id !== api.me;
    });
  }

  function onMessage(ev) {
    var frame;
    try {
      frame = JSON.parse(ev.data);
    } catch (e) {
      return;
    }
    if (!frame || typeof frame !== "object") return;

    switch (frame.t) {
      case "welcome":
        api.me = frame.id || null;
        api.max = frame.max || 0;
        recomputePeers();
        emit("welcome", { id: api.me, max: api.max });
        break;
      case "roster":
        api.roster = Array.isArray(frame.members) ? frame.members : [];
        recomputePeers();
        emit("roster", api.roster);
        emit("peers", api.peers);
        break;
      case "ping":
        sendRaw({ t: "pong", seq: frame.seq });
        break;
      case "msg":
        emit("msg", { from: frame.from, d: frame.d });
        break;
    }
  }

  function scheduleReconnect() {
    if (closedByUs || reconnectTimer) return;
    reconnectTimer = setTimeout(function () {
      reconnectTimer = null;
      connect();
    }, backoff);
    backoff = Math.min(backoff * 2, 15000);
  }

  function connect() {
    if (!gameId) return; // nothing to connect to; game runs solo
    try {
      ws = new WebSocket(hubUrl());
    } catch (e) {
      scheduleReconnect();
      return;
    }
    ws.onopen = function () {
      api.connected = true;
      backoff = 500;
      sendRaw({ t: "join", nick: api.nick });
      emit("status", { connected: true });
    };
    ws.onmessage = onMessage;
    ws.onerror = function () {
      // Swallowed on purpose: a transient socket error must not surface as a
      // console error (the generation smoke test fails on console.error).
    };
    ws.onclose = function () {
      api.connected = false;
      ws = null;
      emit("status", { connected: false });
      scheduleReconnect();
    };
  }

  window.VG_RT = api;
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();
