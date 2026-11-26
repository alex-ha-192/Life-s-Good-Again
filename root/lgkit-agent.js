#!/usr/bin/env node
// SPDX-License-Identifier: GPL-3.0-or-later
/* LG TV Privacy Kit - on-TV agent. ROOTED TVs ONLY (Homebrew Channel with root).
 *
 * Started at boot by lgkit-boot.sh (through Homebrew Channel's init.d). Runs on the TV itself,
 * so no computer has to stay on. It does one thing: every GUARD_HOURS (default 6), and a minute
 * after each power-on or wake, it re-reads the kit's privacy settings and turns back off anything a
 * firmware update or LG turned on. It does this silently; changes are
 * written to agent.log and status.json.
 *
 * It doesn't touch the Home button: Tiles Home declares itself a Home app, and Home Chooser
 * (org.webosbrew.homechooser, from Homebrew Channel) makes the Home button open it.
 *
 * Everything it does is in /var/lib/webosbrew/lgkit/agent.log and status.json.
 * Uses only luna-send. ES5 so it runs on old webOS node too.
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
var GUARD_HOURS = parseFloat(process.env.GUARD_HOURS || "6");
var LOG = path.join(DIR, "agent.log");
var STATUS = path.join(DIR, "status.json");

var status = { started: new Date().toISOString(), guardRuns: 0, lastGuard: null, lastGuardChanged: [], pid: process.pid };

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

// ------------------------------------------------------------------ power-on / wake
function onPowerOn(reason) {
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
      // silent by design: what changed goes to agent.log and status.json, never on screen
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
  log("agent start: guard every " + GUARD_HOURS + "h and after power-on");
  saveStatus();
  watchPower();
  if (process.env.LGKIT_FIRST_BOOT !== "0") onPowerOn("boot");
  if (GUARD_HOURS > 0) setInterval(function () { guard(); }, GUARD_HOURS * 3600 * 1000);
  process.on("SIGTERM", function () { log("agent stop"); process.exit(0); });
});
