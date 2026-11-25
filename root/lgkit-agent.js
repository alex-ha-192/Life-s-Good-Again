#!/usr/bin/env node
/* LG TV Privacy Kit - on-TV agent. ROOTED TVs ONLY (Homebrew Channel with root).
 *
 * Started at boot by lgkit-boot.sh (through Homebrew Channel's init.d). Runs on the TV itself,
 * so no computer has to stay on. It does three things:
 *
 *   1. Home: whenever LG's Home comes up (Home button, power-on, wake from standby, or LG
 *      popping it up by itself), it opens Tiles Home on top. It doesn't patch LG's launcher.
 *      Exception: if you pick "LG Home" from inside Tiles Home, LG's Home is left alone.
 *   2. Power-on: opens Tiles Home at boot and when the TV wakes from standby (BOOT_TO_TILES=1).
 *   3. Guard: every GUARD_HOURS (default 6), and a minute after each power-on, it re-reads the
 *      kit's privacy settings and turns back off anything a firmware update or LG turned on.
 *      If it changes anything, it shows a short message on the TV.
 *
 * Everything it does is in /var/lib/webosbrew/lgkit/agent.log and status.json.
 * Uses only luna-send and the remote's input events. ES5 so it runs on old webOS node too.
 *
 *   node lgkit-agent.js            run (normally started by lgkit-boot.sh)
 *   node lgkit-agent.js --guard    run the settings guard once, print what it did, exit
 */
"use strict";
var fs = require("fs");
var cp = require("child_process");
var path = require("path");

var DIR = process.env.LGKIT_DIR || path.dirname(__filename);
var LUNA = process.env.LUNA_SEND || "luna-send";
var APP = process.env.TILES_APP || "io.github.lgprivacykit.tiles";
var HOME_APPS = (process.env.HOME_APPS || "com.webos.app.home,com.webos.app.homeconnect,com.webos.app.launcher")
  .split(",").filter(Boolean);
var HOME_CODE = parseInt(process.env.HOME_CODE || "0", 10); // 0 = not known yet; find-home-key.js finds it
var BOOT_TO_TILES = process.env.BOOT_TO_TILES !== "0";
var REDIRECT_HOME = process.env.REDIRECT_HOME !== "0";
var GUARD_HOURS = parseFloat(process.env.GUARD_HOURS || "6");
var INPUT_DIR = process.env.INPUT_DIR || "/dev/input";
var LOG = path.join(DIR, "agent.log");
var STATUS = path.join(DIR, "status.json");

var status = { started: new Date().toISOString(), redirects: 0, lastRedirect: null, guardRuns: 0,
               lastGuard: null, lastGuardChanged: [], homeKeySeen: false, pid: process.pid };

function log(m) {
  try {
    var st = fs.statSync(LOG);
    if (st.size > 256 * 1024) fs.renameSync(LOG, LOG + ".1");
  } catch (e) {}
  try { fs.appendFileSync(LOG, new Date().toISOString() + " " + m + "\n"); } catch (e) {}
}
function saveStatus() {
  try { fs.writeFileSync(STATUS, JSON.stringify(status, null, 1)); } catch (e) {}
}
// Some webOS builds' luna-send only prints its reply to a terminal (seen on webOS 23). Then every
// call goes through script(1), which gives it one. Decided once at start by probe().
var VIA_SCRIPT = false;
function q(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'"; }
function lunaArgs(args) {
  if (!VIA_SCRIPT) return { cmd: LUNA, args: args };
  return { cmd: "script", args: ["-q", "-c", [LUNA].concat(args).map(q).join(" "), "/dev/null"] };
}
function parseReply(out) {
  var s = String(out || "").replace(/\r/g, ""), i = s.indexOf("{");
  if (i < 0) return null;
  try { return JSON.parse(s.slice(i, s.lastIndexOf("}") + 1)); } catch (e) { return null; }
}
function luna(uri, params, cb) {
  var c = lunaArgs(["-n", "1", "-w", "5000", uri, JSON.stringify(params || {})]);
  cp.execFile(c.cmd, c.args, { timeout: 10000 }, function (err, out) {
    var r = parseReply(out);
    if (cb) cb(r || { returnValue: false, errorText: err ? err.message : "no reply" });
  });
}
function probe(cb) {
  cp.execFile(LUNA, ["-n", "1", "-w", "5000", "luna://com.webos.applicationManager/running", "{}"], { timeout: 8000 },
    function (err, out) {
      if (parseReply(out)) { status.luna = "direct"; return cb(); }
      cp.execFile("script", ["-q", "-c", LUNA + " -n 1 -w 5000 luna://com.webos.applicationManager/running '{}'", "/dev/null"],
        { timeout: 10000 }, function (e2, out2) {
          if (parseReply(out2)) { VIA_SCRIPT = true; status.luna = "via script (luna-send only prints to a terminal here)"; }
          else status.luna = "no replies (redirects rely on the Home key code; guard can't read settings)";
          log("luna-send: " + status.luna);
          cb();
        });
    });
}
function subscribe(uri, params) {
  var c = lunaArgs(["-i", uri, JSON.stringify(params)]);
  return cp.spawn(c.cmd, c.args);
}
function toast(msg) {
  luna("luna://com.webos.notification/createToast", { sourceId: APP, message: msg }, function () {});
}

// ------------------------------------------------------------------ launching Tiles Home
var lastLaunch = 0;
function launchTiles(reason, force) {
  // debounce repeats, except when LG Home has actually come up: then Tiles must go back on top
  if (!force && Date.now() - lastLaunch < 1500) return;
  lastLaunch = Date.now();
  luna("luna://com.webos.applicationManager/launch", { id: APP }, function (r) {
    if (r.returnValue === false) log("launch failed (" + reason + "): " + (r.errorText || "?"));
    else log("opened Tiles Home (" + reason + ")");
  });
  status.redirects++; status.lastRedirect = new Date().toISOString() + " " + reason; saveStatus();
}

// ------------------------------------------------------------------ foreground app watch
var fg = "", prevFg = "", lastHomeKey = 0;
function onForeground(appId) {
  if (!appId || appId === fg) return;
  prevFg = fg; fg = appId;
  if (!REDIRECT_HOME || HOME_APPS.indexOf(appId) < 0) return;
  // You chose "LG Home" inside Tiles Home: the Home key wasn't pressed, the last app was Tiles.
  if (prevFg === APP && status.homeKeySeen && Date.now() - lastHomeKey > 2500) {
    log("LG Home opened from Tiles Home, leaving it");
    return;
  }
  launchTiles("LG Home came up: " + appId, true);
  // close LG's Home behind Tiles so it isn't left running (and can't reappear on Back)
  setTimeout(function () { luna("luna://com.webos.applicationManager/closeByAppId", { id: appId }, function () {}); }, 1500);
}
function watchForeground() {
  var p = subscribe("luna://com.webos.applicationManager/getForegroundAppInfo", { subscribe: true });
  var buf = "";
  p.stdout.on("data", function (d) {
    buf += String(d).replace(/\r/g, "");
    var lines = buf.split("\n");
    buf = lines.pop();
    lines.forEach(function (l) {
      var i = l.indexOf("{");
      if (i < 0) return;
      try { var r = JSON.parse(l.slice(i)); if (r.appId !== undefined) onForeground(r.appId); } catch (e) {}
    });
  });
  p.on("error", function (e) { log("foreground watch error: " + e.message); });
  p.on("exit", function () { setTimeout(watchForeground, 5000); });
}

// ------------------------------------------------------------------ remote Home key
// struct input_event: time (2 longs) + type(u16) + code(u16) + value(s32); 16 bytes on 32-bit, 24 on 64-bit.
function watchKeys() {
  if (!HOME_CODE) { log("HOME_CODE not set: relying on the foreground watch alone"); return; }
  var sz = (process.arch === "x64" || process.arch === "arm64") ? 24 : 16;
  var ents = [];
  try { ents = fs.readdirSync(INPUT_DIR).filter(function (f) { return /^event\d+$/.test(f); }); } catch (e) {}
  ents.forEach(function (f) {
    var p = path.join(INPUT_DIR, f), fd;
    try { fd = fs.openSync(p, "r"); } catch (e) { return; }
    var buf = Buffer.alloc(sz * 64);
    (function read() {
      fs.read(fd, buf, 0, buf.length, null, function (err, n) {
        if (err || !n) { try { fs.closeSync(fd); } catch (e) {} return; }
        for (var o = 0; o + sz <= n; o += sz) {
          var type = buf.readUInt16LE(o + sz - 8), code = buf.readUInt16LE(o + sz - 6), value = buf.readInt32LE(o + sz - 4);
          if (type === 1 && value === 1 && code === HOME_CODE) {
            lastHomeKey = Date.now();
            if (!status.homeKeySeen) { status.homeKeySeen = true; saveStatus(); }
            if (REDIRECT_HOME) launchTiles("Home button");   // at once: beats most of LG Home's animation
          }
        }
        setImmediate(read);
      });
    })();
  });
  log("watching " + ents.length + " input devices for key " + HOME_CODE);
}

// ------------------------------------------------------------------ power-on / wake
function onPowerOn(reason) {
  if (BOOT_TO_TILES) setTimeout(function () { launchTiles(reason); }, reason === "boot" ? 3000 : 600);
  setTimeout(function () { guard(); }, 60000);
}
function watchPower() {
  var p = subscribe("luna://com.webos.service.tvpower/power/getPowerState", { subscribe: true });
  var buf = "", last = "";
  p.stdout.on("data", function (d) {
    buf += String(d).replace(/\r/g, "");
    var lines = buf.split("\n");
    buf = lines.pop();
    lines.forEach(function (l) {
      var i = l.indexOf("{");
      if (i < 0) return;
      try {
        var s = JSON.parse(l.slice(i)).state;
        if (s && s !== last) { if (last && s === "Active") onPowerOn("wake from standby"); last = s; }
      } catch (e) {}
    });
  });
  p.on("error", function () {});
  p.on("exit", function () { setTimeout(watchPower, 15000); });
  // fallback: a big jump in the clock means the TV was suspended
  var t = Date.now();
  setInterval(function () { var n = Date.now(); if (n - t > 15000) onPowerOn("resume (clock jump)"); t = n; }, 2000);
}

// ------------------------------------------------------------------ settings guard
function loadSettings() {
  try { return JSON.parse(fs.readFileSync(path.join(DIR, "settings.json"), "utf8")).settings || []; }
  catch (e) { log("no settings.json: " + e.message); return []; }
}
function guard(done) {
  var list = loadSettings(), changed = [], i = 0;
  (function next() {
    if (i >= list.length) {
      status.guardRuns++; status.lastGuard = new Date().toISOString(); status.lastGuardChanged = changed; saveStatus();
      log("guard: " + (changed.length ? "turned back off " + changed.join(", ") : "all " + list.length + " settings as set"));
      if (changed.length) toast("Privacy Kit turned back off: " + changed.join(", "));
      if (done) done(changed);
      return;
    }
    var s = list[i++];
    luna("luna://com.webos.settingsservice/getSystemSettings", { category: s.category, keys: [s.key] }, function (r) {
      var cur = r && r.settings ? r.settings[s.key] : undefined;
      if (cur === undefined || cur === s.value) return next(); // not on this firmware, or already right
      var set = {}; set[s.key] = s.value;
      luna("luna://com.webos.settingsservice/setSystemSettings", { category: s.category, settings: set }, function (w) {
        if (w.returnValue !== false) changed.push(s.label || s.key);
        else log("guard: couldn't set " + s.key + ": " + (w.errorText || "?"));
        next();
      });
    });
  })();
}

// ------------------------------------------------------------------ main
if (process.argv[2] === "--guard") {
  probe(function () {
    guard(function (changed) { console.log(changed.length ? "Changed: " + changed.join(", ") : "Nothing to change."); process.exit(0); });
  });
} else probe(function () {
  log("agent start: app=" + APP + " home=" + HOME_APPS.join("|") + " key=" + (HOME_CODE || "none") +
      " boot=" + BOOT_TO_TILES + " guard=" + GUARD_HOURS + "h");
  saveStatus();
  watchForeground();
  watchKeys();
  watchPower();
  if (process.env.LGKIT_FIRST_BOOT !== "0") onPowerOn("boot");
  if (GUARD_HOURS > 0) setInterval(function () { guard(); }, GUARD_HOURS * 3600 * 1000);
  process.on("SIGTERM", function () { log("agent stop"); process.exit(0); });
});
