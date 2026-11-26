// SPDX-License-Identifier: GPL-3.0-or-later
/* Tiles Home v2 - a ring of five pages for LG webOS (Developer Mode app). Original design.
 * Left/right: previous/next page (wraps). Up/down: move through the page's list. OK: open.
 * Yellow: add/remove favourite (the item stays on its own page). Red (on favourites): pick up, move, OK to drop.
 * Back: cancel a move, or return to favourites. No network requests, no analytics, no frameworks.
 * Developer Mode: when Tiles Home opens and the last extend was 7+ days ago, it asks the Developer Mode app
 * to extend (the same launch LG's own CLI uses: com.palmdts.devmode with {extend: true}), then comes back.
 */
(function () {
  "use strict";

  var SELF = "io.github.lgprivacykit.tiles";
  var FAV_KEY = "tileshome.favourites.v2";
  var DEV_KEY = "tileshome.devmode.lastExtend";
  var DEV_EVERY = 7 * 24 * 3600 * 1000; // LG allows ~1000 h (~41 days); extend weekly to stay well clear
  var KEY = { LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40, OK: 13, BACK: 461, ESC: 27, RED: 403, YELLOW: 405, Y: 89, R: 82 };
  var VISIBLE = 7;
  var W = 640;
  // where each neighbour sits relative to the middle: [offset px, scale, opacity]
  var SLOTS = { "-2": [-1090, 0.5, 0.2], "-1": [-680, 0.7, 0.45], "0": [0, 1, 1], "1": [680, 0.7, 0.45], "2": [1090, 0.5, 0.2] };
  var PALETTE = ["#b3123a", "#1f5fbf", "#0e8a87", "#c76f12", "#178a43", "#6b3fb0", "#2c2f8f", "#39414b"];

  var G = {
    tv: '<rect x="8" y="14" width="48" height="32" rx="3"/><path d="M24 54h16M32 46v8"/>',
    hdmi: '<path d="M10 24h44v10l-6 8H16l-6-8z"/><path d="M18 30h28"/>',
    ant: '<path d="M32 26v28M20 54h24M32 26L18 10M32 26l14-16"/><circle cx="32" cy="26" r="3"/>',
    photo: '<rect x="8" y="12" width="48" height="40" rx="3"/><circle cx="22" cy="26" r="5"/><path d="M8 46l16-14 12 10 8-6 12 10"/>',
    music: '<path d="M24 46V16l26-5v30"/><circle cx="18" cy="46" r="6"/><circle cx="44" cy="41" r="6"/>',
    folder: '<path d="M8 18h18l5 6h25v26H8z"/>',
    gear: '<circle cx="32" cy="32" r="8"/><path d="M32 10v8M32 46v8M10 32h8M46 32h8M16.5 16.5l5.7 5.7M41.8 41.8l5.7 5.7M16.5 47.5l5.7-5.7M41.8 22.2l5.7-5.7"/>',
    code: '<path d="M24 20L12 32l12 12M40 20l12 12-12 12"/>',
    home: '<path d="M10 30l22-18 22 18v24H40V40H24v14H10z"/>',
    renew: '<path d="M32 10a22 22 0 1 1-20 13"/><path d="M12 10v13h13"/><path d="M32 22v11l7 5"/>',
    reset: '<path d="M14 32a18 18 0 1 0 6-13.4"/><path d="M14 10v10h10"/>'
  };
  var STAR = '<svg class="fav" viewBox="0 0 64 64"><path d="M32 6l7.6 16.4 18 2-13.4 12.2 3.8 17.7L32 45.2l-16 9.1 3.8-17.7L6.4 24.4l18-2z"/></svg>';

  var INPUT_RE = /^com\.webos\.app\.(hdmi\d*|livetv|externalinput\.[\w.]+|inputcommon|component\d*|av\d*|scart)$/;
  var MEDIA_IDS = { "com.webos.app.mediadiscovery": "folder", "com.webos.app.photovideo": "photo",
                    "com.webos.app.music": "music", "com.webos.app.igallery": "photo" };
  var SYSTEM = { "com.palm.app.settings": 1, "com.palmdts.devmode": 1, "com.webos.app.home": 1 };
  var HIDE = { "com.webos.app.connectionwizard": 1 };
  HIDE[SELF] = 1;

  var CFG = { rooted: false };   // config.json, written by tools/build_ipk.py (--rooted)
  // ---- themes: colours only, set as CSS variables, so changing one never rebuilds the screen
  var THEME_KEY = "tileshome.theme";
  function rgba(hex, a) { var n = parseInt(hex.slice(1), 16); return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")"; }
  var THEMES = [{ id: "midnight", name: "Midnight", sw: "#1f5fbf", bg: "#0a1830", acc: "#8ff0e3", sel: "rgba(255,255,255,.13)", frame: "#fff", t: "#fff" }];
  [["lime", "Lime", "#a4c400"], ["orange", "Orange", "#ff5a00"], ["magenta", "Magenta", "#e3008c"],
   ["blue", "Blue", "#1ba1e2"], ["red", "Red", "#e51400"], ["teal", "Teal", "#00aba9"]].forEach(function (a) {
    THEMES.push({ id: "graphite-" + a[0], name: "Graphite, " + a[1].toLowerCase(), sw: a[2], bg: "#181818", acc: a[2], sel: rgba(a[2], 0.16), frame: a[2], t: "#a8a8a8" });
  });
  var themeIdx = 0;
  function applyTheme(i) {
    themeIdx = i; var th = THEMES[i], s = document.documentElement.style;
    s.setProperty("--bg", th.bg); s.setProperty("--acc", th.acc); s.setProperty("--sel", th.sel);
    s.setProperty("--frame", th.frame); s.setProperty("--t", th.t);
  }
  (function () {
    var id = null; try { id = localStorage.getItem(THEME_KEY); } catch (e) {}
    for (var i = 0; i < THEMES.length; i++) if (THEMES[i].id === id) { applyTheme(i); return; }
    applyTheme(0);
  })();
  var dd = { open: false };   // Settings > Theme expanded in place, its options listed under the row
  var SETTINGS_P = 4;
  function themeRow() { var it = st.pages[SETTINGS_P].items; for (var i = 0; i < it.length; i++) if (it[i].action === "theme") return i; return 0; }
  function optRange() { var it = st.pages[SETTINGS_P].items, lo = -1, hi = -1; for (var i = 0; i < it.length; i++) if (it[i].action === "themeopt") { if (lo < 0) lo = i; hi = i; } return [lo, hi]; }
  function setDD(open) {
    dd.open = open; buildPages();
    st.sel[SETTINGS_P] = open ? optRange()[0] + themeIdx : themeRow();
    schedule();
  }
  function chooseTheme(i) {
    applyTheme(i); try { localStorage.setItem(THEME_KEY, THEMES[i].id); } catch (e) {}
    setDD(false);
  }
  var MOTION_KEY = "tileshome.motion";
  function motionOn() { try { return localStorage.getItem(MOTION_KEY) === "on"; } catch (e) { return false; } }
  var st = { apps: [], pages: [], p: 0, sel: [0, 0, 0, 0, 0], favs: [], moving: false };
  var $ = function (id) { return document.getElementById(id); };

  // ------------------------------------------------------------------ platform
  function luna(uri, params, cb) {
    try {
      var bridge = new PalmServiceBridge();
      bridge.onservicecallback = function (msg) {
        var r; try { r = JSON.parse(msg); } catch (e) { r = { returnValue: false }; }
        if (cb) cb(r);
      };
      bridge.call(uri, JSON.stringify(params || {}));
    } catch (e) {
      if (cb) cb({ returnValue: false, errorText: "not on a TV" });
    }
  }
  function getJSON(path, cb) {
    var x = new XMLHttpRequest();
    x.onreadystatechange = function () {
      if (x.readyState !== 4) return;
      var d = null; try { d = JSON.parse(x.responseText); } catch (e) {}
      cb(d);
    };
    x.open("GET", path, true);
    x.send();
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  function colourFor(id) {
    var h = 0; for (var i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  // ------------------------------------------------------------------ data
  function addApp(seen, a) {
    if (!a || !a.id || HIDE[a.id] || seen[a.id]) return;
    seen[a.id] = a;
    st.apps.push(a);
  }
  function loadApps(cb) {
    getJSON("apps.json", function (baked) {
      var seen = {};
      st.apps = [];
      (Array.isArray(baked) ? baked : []).forEach(function (a) { addApp(seen, a); });
      luna("luna://com.webos.applicationManager/listLaunchPoints", {}, function (r) {
        st.lpOk = !!(r && r.launchPoints);
        ((r && r.launchPoints) || []).forEach(function (lp) {
          var icon = lp.icon || lp.largeIcon;   // the small one: 80px is all we draw, big PNGs cost decode + scaling
          if (seen[lp.id]) { if (!seen[lp.id].icon && icon) seen[lp.id].icon = icon; return; }
          addApp(seen, { id: lp.id, title: lp.title, icon: icon, color: lp.iconColor || lp.bgColor });
        });
        st.apps.sort(function (a, b) { return a.title.toLowerCase() < b.title.toLowerCase() ? -1 : 1; });
        cb();
      });
    });
  }
  function loadFavs(cb) {
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(FAV_KEY)); } catch (e) {}
    if (Array.isArray(saved)) { st.favs = saved; return cb(); }
    getJSON("favourites.json", function (d) { st.favs = Array.isArray(d) ? d : []; cb(); });
  }
  function saveFavs() { try { localStorage.setItem(FAV_KEY, JSON.stringify(st.favs)); } catch (e) {} }

  function item(a, glyph) {
    return { id: a.id, title: a.title, icon: a.icon, color: a.color || colourFor(a.id), glyph: glyph };
  }
  function buildPages() {
    var apps = [], inputs = [], media = [];
    st.apps.forEach(function (a) {
      if (INPUT_RE.test(a.id)) inputs.push(item(a, /livetv/.test(a.id) ? "ant" : "hdmi"));
      else if (MEDIA_IDS[a.id]) media.push(item(a, MEDIA_IDS[a.id]));
      else if (!SYSTEM[a.id]) apps.push(item(a));
    });
    var settings = [
      { id: "com.palm.app.settings", title: "All settings", glyph: "gear", color: "#39414b" },
      { id: "com.palmdts.devmode", title: "Developer Mode", glyph: "code", color: "#39414b" },
      { id: "com.webos.app.home", title: "LG Home", glyph: "home", color: "#39414b" },
      { action: "extend", id: "tiles:extend", title: devTitle(), glyph: "renew", color: "#39414b" },
      { action: "theme", id: "tiles:theme", title: "Theme: " + THEMES[themeIdx].name, glyph: "gear", color: "#39414b" },
      { action: "motion", id: "tiles:motion", title: motionOn() ? "Animations: on" : "Animations: off", glyph: "renew", color: "#39414b" },
      { action: "speed", id: "tiles:speed", title: speedOn ? "Speed readout: on" : "Speed readout: off", glyph: "code", color: "#39414b" },
      { action: "reset", id: "tiles:reset", title: "Reset favourites", glyph: "reset", color: "#39414b" }
    ].filter(function (x) { return hasDevMode() || (x.id !== "com.palmdts.devmode" && x.action !== "extend"); });
    if (dd.open) {
      var ti = 0; settings.forEach(function (x, i) { if (x.action === "theme") ti = i; });
      var opts = THEMES.map(function (t, i) { return { action: "themeopt", idx: i, id: "tiles:theme:" + i, title: t.name, sw: t.sw, cur: i === themeIdx, sub: true }; });
      settings.splice.apply(settings, [ti + 1, 0].concat(opts));
    }
    var all = {};
    [apps, inputs, media, settings].forEach(function (l) { l.forEach(function (x) { if (!all[x.id]) all[x.id] = x; }); });
    // Favourites whose app isn't installed right now are kept in storage but not shown.
    var favs = st.favs.map(function (id) { return all[id]; }).filter(function (x) { return x && !x.action; });
    st.pages = [
      { h: "favourites", items: favs },
      { h: "apps", items: apps },
      { h: "inputs", items: inputs },
      { h: "media", items: media },
      { h: "settings", items: settings }
    ];
    st.pages.forEach(function (pg, i) { st.sel[i] = Math.min(st.sel[i] || 0, Math.max(0, pg.items.length - 1)); });
  }

  // ------------------------------------------------------------------ Developer Mode
  function lastExtend() { var v = 0; try { v = +localStorage.getItem(DEV_KEY) || 0; } catch (e) {} return v; }
  function devTitle() {
    var t = lastExtend();
    if (!t) return "Extend Developer Mode";
    var days = Math.floor((Date.now() - t) / 86400000);
    return "Dev Mode extended " + (days < 1 ? "today" : days === 1 ? "yesterday" : days + " days ago");
  }
  var extending = false, devAbsent = false;
  function extendDevMode(manual) {
    if (extending) return;
    extending = true;
    if (manual) toast("Extending Developer Mode\u2026");
    luna("luna://com.webos.applicationManager/launch", { id: "com.palmdts.devmode", params: { extend: true } }, function (r) {
      if (r && r.returnValue === false) {
        extending = false;
        // not installed (e.g. removed on a rooted TV): stop trying, and say nothing unless asked
        if (/not ?exist|not found|no such/i.test(r.errorText || "")) { devAbsent = true; buildPages(); render(); }
        if (manual) toast("Couldn't reach Developer Mode" + (r.errorText ? ": " + r.errorText : ""));
        return;
      }
      try { localStorage.setItem(DEV_KEY, String(Date.now())); } catch (e) {}
      // the Developer Mode app extends as it opens; come back after a moment
      setTimeout(function () {
        luna("luna://com.webos.applicationManager/launch", { id: SELF }, function () {});
        extending = false;
        buildPages(); render();
      }, 6000);
    });
  }
  // On a rooted TV you may have removed the Developer Mode app; then there's nothing to extend.
  function hasDevMode() {
    if (CFG.rooted || devAbsent) return false;
    return st.apps.some(function (a) { return a.id === "com.palmdts.devmode"; });
  }
  function maybeExtend() {
    if (!hasDevMode()) return;
    if (Date.now() - lastExtend() >= DEV_EVERY) extendDevMode(false);
  }

  // ------------------------------------------------------------------ render
  function iconHTML(it) {
    var inner;
    if (it.icon) inner = '<img src="' + esc(it.icon) + '" alt="">';
    else if (it.glyph && G[it.glyph]) inner = '<svg viewBox="0 0 64 64">' + G[it.glyph] + "</svg>";
    else inner = esc((it.title || "?").charAt(0).toUpperCase());
    return '<span class="ic" style="background:' + esc(it.color || "#39414b") + '">' + inner + "</span>";
  }
  function listHTML(p, active) {
    var items = st.pages[p].items, sel = st.sel[p];
    if (!items.length) return '<div class="empty">' + (p === 0 ? "Press yellow on any app to add it here" : "Nothing here") + "</div>";
    var start = Math.max(0, Math.min(sel - VISIBLE + 2, items.length - VISIBLE));
    var out = "";
    for (var i = start; i < Math.min(items.length, start + VISIBLE); i++) {
      var it = items[i], f = active && i === sel;
      var fav = p !== 0 && !it.action && st.favs.indexOf(it.id) >= 0;
      if (it.sub) {
        out += '<div class="r sub' + (f ? " f" : "") + '" data-i="' + i + '"><span class="sw" style="background:' + it.sw + '"></span><span class="t">' + esc(it.title) + "</span>" + (it.cur ? '<span class="tick">\u2713</span>' : "") + "</div>";
        continue;
      }
      out += '<div class="r' + (f ? " f" : "") + (f && st.moving ? " mv" : "") + '" data-i="' + i + '">' +
        iconHTML(it) + '<span class="t">' + esc(it.title) + "</span>" + (fav ? STAR : "") + "</div>";
    }
    var rest = items.length - start - VISIBLE;
    if (rest > 0) out += '<div class="more">+ ' + rest + " more</div>";
    return '<div class="rows">' + out + "</div>";
  }
  function mod(i) { var n = st.pages.length; return ((i % n) + n) % n; }
  // One element per page, kept between renders, so CSS can animate the ring turning (Metro-style
  // slide on transform/opacity only, which the TV's GPU handles). Only the list inside a page is
  // rebuilt, and only when what it shows changes; moving the selection just moves a class.
  var cols = [], lastD = [], lastSig = [], lastP = -1, firstPaint = true;
  function placeCol(p) {
    var n = st.pages.length, d = mod(p - st.p);
    if (d > n / 2) d -= n;                       // -2..2
    var s = SLOTS[d], el = cols[p];
    // wrapping from one end of the ring to the other: jump, don't sweep across the screen
    var jump = document.body.classList.contains("motion") && lastD[p] !== undefined && Math.abs(d - lastD[p]) > 2;
    if (jump) el.classList.add("jump");
    el.className = "col" + (d < 0 ? " mir" : "") + (d === 0 ? " on" : "") + (jump ? " jump" : "");
    el.setAttribute("data-d", d);
    el.style.transform = "translateX(" + s[0] + "px) scale(" + s[1] + ")";
    el.style.opacity = s[2];
    if (jump) { void el.offsetWidth; el.classList.remove("jump"); }
    lastD[p] = d;
    return d;
  }
  function render() {
    var ring = $("ring");
    if (cols.length !== st.pages.length) {
      ring.innerHTML = ""; cols = []; lastD = []; lastSig = [];
      st.pages.forEach(function (pg, p) {
        var el = document.createElement("section");
        el.innerHTML = '<h2></h2><div class="body"></div>';
        el.style.left = (960 - W / 2) + "px";
        ring.appendChild(el); cols.push(el);
      });
    }
    st.pages.forEach(function (pg, p) {
      var d = placeCol(p), active = d === 0, el = cols[p];
      el.firstChild.textContent = pg.h;
      var items = pg.items, sel = st.sel[p];
      var start = Math.max(0, Math.min(sel - VISIBLE + 2, items.length - VISIBLE));
      var sig = [active, start, st.moving && active, items.map(function (x) { return x.id; }).join(","),
                 st.favs.join(","), items.map(function (x) { return x.title; }).join(",")].join("|");
      var body = el.lastChild;
      if (sig !== lastSig[p]) { body.innerHTML = listHTML(p, active); lastSig[p] = sig; }
      else {
        var rows = body.querySelectorAll(".r");
        for (var i = 0; i < rows.length; i++) {
          var on = active && +rows[i].getAttribute("data-i") === sel;
          rows[i].classList.toggle("f", on);
          rows[i].classList.toggle("mv", on && st.moving);
        }
      }
    });
    firstPaint = false; lastP = st.p;
    hints();
  }
  // What the coloured buttons do right now, shown bottom left.
  var lastHints = "";
  function hints() {
    var it = current(), h = [];
    if (dd.open) { var hh = "<b>\u25B2\u25BC</b> choose" + '<span class="sep"></span>' + "<b>OK</b> use this theme" + '<span class="sep"></span>' + "<b>Back</b> close"; if (hh !== lastHints) { $("hints").innerHTML = hh; lastHints = hh; } return; }
    var Y = '<i class="k y"></i>', R = '<i class="k r"></i>';
    if (st.moving) {
      h.push('<b>\u25B2\u25BC</b> move', "<b>OK</b> drop it here", "<b>Back</b> done");
    } else {
      if (it && !it.action) h.push(Y + (st.favs.indexOf(it.id) >= 0 ? " remove from favourites" : " add to favourites"));
      if (st.p === 0 && st.pages[0].items.length > 1) h.push(R + " move");
      h.push("<b>\u25C0 \u25B6</b> pages");
      if (st.p !== 0) h.push("<b>Back</b> favourites");
    }
    var html = h.join('<span class="sep"></span>');
    if (html !== lastHints) { $("hints").innerHTML = html; lastHints = html; }
  }
  function replay() { firstPaint = true; }
  var toastTimer;
  function toast(msg) {
    var t = $("toast");
    t.textContent = msg;
    t.className = "show";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.className = ""; }, 2000);
  }

  // ------------------------------------------------------------------ actions
  function current() { return st.pages[st.p].items[st.sel[st.p]]; }
  function open(it) {
    if (!it) return;
    if (it.action === "extend") return extendDevMode(true);
    if (it.action === "theme") return setDD(!dd.open);
    if (it.action === "themeopt") return chooseTheme(it.idx);
    if (it.action === "motion") {
      try { localStorage.setItem(MOTION_KEY, motionOn() ? "off" : "on"); } catch (e) {}
      document.body.classList.toggle("motion", motionOn());
      buildPages(); render(); return;
    }
    if (it.action === "speed") {
      speedOn = !speedOn;
      try { localStorage.setItem(SPEED_KEY, speedOn ? "on" : "off"); } catch (e) {}
      lat = []; startSpeed(); buildPages(); render(); return;
    }
    if (it.action === "reset") {
      try { localStorage.removeItem(FAV_KEY); } catch (e) {}
      return loadFavs(function () { buildPages(); render(); toast("Favourites reset"); });
    }
    luna("luna://com.webos.applicationManager/launch", { id: it.id }, function (r) {
      if (r && r.returnValue === false) toast("Couldn't open " + it.title);
    });
  }
  function toggleFav() {
    var it = current();
    if (!it || it.action) return;
    var i = st.favs.indexOf(it.id);
    if (i >= 0) {
      st.favs.splice(i, 1);
      toast(st.p === 0 ? it.title + " removed from favourites (still on its own page)" : it.title + " removed from favourites");
    } else {
      st.favs.push(it.id);
      toast(it.title + " added to favourites");
    }
    saveFavs(); buildPages(); render();
  }
  function moveFav(delta) {
    var items = st.pages[0].items, i = st.sel[0], j = i + delta;
    if (j < 0 || j >= items.length) return;
    var ia = st.favs.indexOf(items[i].id), ib = st.favs.indexOf(items[j].id);
    var tmp = st.favs[ia]; st.favs[ia] = st.favs[ib]; st.favs[ib] = tmp;
    st.sel[0] = j;
    saveFavs(); buildPages(); schedule();
  }

  function key(code) {
    if (dd.open) {
      var rg = optRange(), p0 = st.sel[SETTINGS_P];
      if (code === KEY.UP) st.sel[SETTINGS_P] = Math.max(rg[0], p0 - 1);
      else if (code === KEY.DOWN) st.sel[SETTINGS_P] = Math.min(rg[1], p0 + 1);
      else if (code === KEY.OK) { open(current()); return true; }
      else if (code === KEY.BACK || code === KEY.ESC || code === KEY.LEFT || code === KEY.RIGHT) { setDD(false); return true; }
      schedule();
      return true;
    }
    if (st.moving) {
      if (code === KEY.UP) moveFav(-1);
      else if (code === KEY.DOWN) moveFav(1);
      else if (code === KEY.OK || code === KEY.RED || code === KEY.R || code === KEY.BACK || code === KEY.ESC) {
        st.moving = false; schedule(); toast("Order saved");
      }
      return true;
    }
    var n = st.pages[st.p].items.length;
    switch (code) {
      case KEY.LEFT: st.p = mod(st.p - 1); break;
      case KEY.RIGHT: st.p = mod(st.p + 1); break;
      case KEY.UP: st.sel[st.p] = Math.max(0, st.sel[st.p] - 1); break;
      case KEY.DOWN: st.sel[st.p] = Math.min(Math.max(0, n - 1), st.sel[st.p] + 1); break;
      case KEY.OK: open(current()); return true;
      case KEY.YELLOW: case KEY.Y: toggleFav(); return true;
      case KEY.RED: case KEY.R:
        if (st.p !== 0) { toast("Go to favourites to reorder"); return true; }
        if (!n) return true;
        st.moving = true; toast("Move with up and down, then OK"); break;
      case KEY.BACK: case KEY.ESC:
        if (st.p === 0) return false;
        st.p = 0; break;
      default: return false;
    }
    schedule();
    return true;
  }
  // Held keys repeat faster than a slow TV can paint: apply every press to the state at once,
  // but draw at most once per frame, so presses never queue up behind drawing.
  var pending = false, raf = window.requestAnimationFrame || function (f) { return setTimeout(f, 16); };
  // First press: draw right now, inside the key event, so it makes the very next frame instead of
  // waiting one for requestAnimationFrame. Further presses in the same frame (a held key) are folded
  // into one draw at the next frame.
  var drewT = -1e9;
  function schedule() {
    if (pending) return;
    var now = performance.now();
    if (now - drewT > 16) { drewT = now; render(); speedFrame(); return; }
    pending = true;
    raf(function () { pending = false; drewT = performance.now(); render(); speedFrame(); });
  }
  document.addEventListener("keydown", function (e) {
    if (speedOn) speedKey();
    if (key(e.keyCode)) e.preventDefault();
  });

  // ------------------------------------------------------------------ speed readout (Settings)
  // Shows how long a key press takes to reach the screen, measured on the TV itself.
  var SPEED_KEY = "tileshome.speed";
  var speedOn = false; try { speedOn = localStorage.getItem(SPEED_KEY) === "on"; } catch (e) {}
  var keyT = 0, lat = [], frames = [], lastF = 0;
  function speedKey() { if (!keyT) keyT = performance.now(); }
  function speedFrame() {
    if (!speedOn || !keyT) return;
    raf(function () {   // the frame after the one we drew in is when it's actually on screen
      lat.push(performance.now() - keyT); keyT = 0;
      if (lat.length > 20) lat.shift();
      showSpeed();
    });
  }
  function med(a) { if (!a.length) return 0; var b = a.slice().sort(function (x, y) { return x - y; }); return b[b.length >> 1]; }
  function showSpeed() {
    var el = $("speed"); if (!el) return;
    if (!speedOn) { el.textContent = ""; return; }
    var fps = frames.length > 1 ? 1000 / med(frames) : 0;
    el.textContent = "key to screen: " + Math.round(med(lat)) + " ms (worst " + Math.round(Math.max.apply(null, lat.concat([0]))) +
      ") \u00b7 " + Math.round(fps) + " fps idle \u00b7 " + document.getElementsByTagName("*").length + " nodes";
  }
  function speedLoop(t) {   // idle frame rate, sampled for 3 seconds after each change
    if (lastF) frames.push(t - lastF);
    if (frames.length > 60) frames.shift();
    lastF = t;
    if (speedOn && frames.length < 60) raf(speedLoop); else { lastF = 0; showSpeed(); }
  }
  function startSpeed() { frames = []; lastF = 0; if (speedOn) raf(speedLoop); showSpeed(); }
  // Magic Remote pointer: click a side page to go there, click a row to select it, click again to open.
  $("ring").addEventListener("click", function (e) {
    var col = e.target.closest ? e.target.closest(".col") : null;
    if (!col) return;
    var d = +col.getAttribute("data-d");
    if (d !== 0) { st.p = mod(st.p + d); st.moving = false; return render(); }
    var row = e.target.closest(".r");
    if (!row) return;
    var i = +row.getAttribute("data-i");
    if (i === st.sel[st.p]) return open(current());
    st.sel[st.p] = i; render();
  });
  document.addEventListener("webOSRelaunch", function () { st.p = 0; st.moving = false; dd.open = false; replay(); buildPages(); render(); maybeExtend(); });

  // ------------------------------------------------------------------ clock
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function tick() {
    var d = new Date();
    $("clock").textContent = pad(d.getHours()) + ":" + pad(d.getMinutes()) + " \u00b7 " + DAYS[d.getDay()] + " " + d.getDate() + " " + MONTHS[d.getMonth()];
  }
  setInterval(tick, 10000);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { tick(); replay(); loadApps(function () { buildPages(); render(); maybeExtend(); }); }
  });

  tick();
  document.body.classList.toggle("motion", motionOn());
  getJSON("config.json", function (c) { if (c) CFG = c; start(); });
  function start() { loadFavs(function () { loadApps(function () { buildPages(); render(); startSpeed(); setTimeout(maybeExtend, 1500); }); }); }
})();
