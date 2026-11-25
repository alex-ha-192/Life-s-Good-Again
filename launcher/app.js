/* Tiles Home v2 - a ring of five pages for LG webOS (Developer Mode app). Original design.
 * Left/right: previous/next page (wraps). Up/down: move through the page's list. OK: open.
 * Yellow: add/remove favourite (the item stays on its own page). Red (on favourites): pick up, move, OK to drop.
 * Back: cancel a move, or return to favourites. No network requests, no analytics, no frameworks.
 */
(function () {
  "use strict";

  var SELF = "io.github.lgprivacykit.tiles";
  var FAV_KEY = "tileshome.favourites.v2";
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
    reset: '<path d="M14 32a18 18 0 1 0 6-13.4"/><path d="M14 10v10h10"/>'
  };
  var STAR = '<svg class="fav" viewBox="0 0 64 64"><path d="M32 6l7.6 16.4 18 2-13.4 12.2 3.8 17.7L32 45.2l-16 9.1 3.8-17.7L6.4 24.4l18-2z"/></svg>';

  var INPUT_RE = /^com\.webos\.app\.(hdmi\d*|livetv|externalinput\.[\w.]+|inputcommon|component\d*|av\d*|scart)$/;
  var MEDIA_IDS = { "com.webos.app.mediadiscovery": "folder", "com.webos.app.photovideo": "photo",
                    "com.webos.app.music": "music", "com.webos.app.igallery": "photo" };
  var SYSTEM = { "com.palm.app.settings": 1, "com.palmdts.devmode": 1, "com.webos.app.home": 1 };
  var HIDE = { "com.webos.app.connectionwizard": 1 };
  HIDE[SELF] = 1;

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
        ((r && r.launchPoints) || []).forEach(function (lp) {
          var icon = lp.largeIcon || lp.icon;
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
      { action: "reset", id: "tiles:reset", title: "Reset favourites", glyph: "reset", color: "#39414b" }
    ];
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
      out += '<div class="r' + (f ? " f" : "") + (f && st.moving ? " mv" : "") + '" data-i="' + i + '">' +
        iconHTML(it) + '<span class="t">' + esc(it.title) + "</span>" + (fav ? STAR : "") + "</div>";
    }
    var rest = items.length - start - VISIBLE;
    if (rest > 0) out += '<div class="more">+ ' + rest + " more</div>";
    return '<div class="rows">' + out + "</div>";
  }
  function mod(i) { var n = st.pages.length; return ((i % n) + n) % n; }
  function render() {
    var html = "";
    [-2, 2, -1, 1, 0].forEach(function (d) {
      var p = mod(st.p + d), s = SLOTS[d];
      html += '<section class="col' + (d < 0 ? " mir" : "") + '" data-d="' + d + '" style="left:' + (960 - W / 2 + s[0]) +
        "px;transform:scale(" + s[1] + ");opacity:" + s[2] + '"><h2>' + esc(st.pages[p].h) + "</h2>" + listHTML(p, d === 0) + "</section>";
    });
    $("ring").innerHTML = html;
  }
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
    var row = document.querySelector(".col[data-d='0'] .r.f");
    if (row) { row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash"); }
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
    saveFavs(); buildPages(); render();
  }

  function key(code) {
    if (st.moving) {
      if (code === KEY.UP) moveFav(-1);
      else if (code === KEY.DOWN) moveFav(1);
      else if (code === KEY.OK || code === KEY.RED || code === KEY.R || code === KEY.BACK || code === KEY.ESC) {
        st.moving = false; render(); toast("Order saved");
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
    render();
    return true;
  }
  document.addEventListener("keydown", function (e) { if (key(e.keyCode)) e.preventDefault(); });
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
  document.addEventListener("webOSRelaunch", function () { st.p = 0; st.moving = false; render(); });

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
    if (!document.hidden) { tick(); loadApps(function () { buildPages(); render(); }); }
  });

  tick();
  loadFavs(function () { loadApps(function () { buildPages(); render(); }); });
})();
