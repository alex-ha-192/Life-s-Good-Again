#!/usr/bin/env node
/* Find your remote's Home key code - ROOTED TVs ONLY. Run over SSH as root:
 *     node find-home-key.js
 * Then press the Home button on your remote a few times. It prints the code.
 * Then re-run install.sh with --home-code N (or set HOME_CODE in /var/lib/webosbrew/lgkit/lgkit.env and reboot).
 * Ctrl-C to stop.
 */
"use strict";
var fs = require("fs");
var sz = (process.arch === "x64" || process.arch === "arm64") ? 24 : 16;
var dir = "/dev/input";
console.log("Press buttons on the remote. type=1 lines are key presses; note the code for Home.\n");
fs.readdirSync(dir).filter(function (f) { return /^event\d+$/.test(f); }).forEach(function (f) {
  var fd;
  try { fd = fs.openSync(dir + "/" + f, "r"); } catch (e) { return; }
  var buf = Buffer.alloc(sz * 64);
  (function read() {
    fs.read(fd, buf, 0, buf.length, null, function (err, n) {
      if (err) return;
      for (var o = 0; o + sz <= n; o += sz) {
        var type = buf.readUInt16LE(o + sz - 8);
        var code = buf.readUInt16LE(o + sz - 6);
        var value = buf.readInt32LE(o + sz - 4);
        if (type === 1 && value === 1) console.log(dir + "/" + f + "  key code = " + code);
      }
      setImmediate(read);
    });
  })();
});
