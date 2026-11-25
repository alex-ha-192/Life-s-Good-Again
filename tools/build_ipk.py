#!/usr/bin/env python3
"""Package ../launcher into a webOS .ipk (no LG SDK needed).

    python3 tools/build_ipk.py            -> dist/io.github.lgprivacykit.tiles_1.0.0_all.ipk
    python3 tools/build_ipk.py --icons    -> also (re)draw icon.png / largeIcon.png

Install with webOS Dev Manager (GUI) or `ares-install -d tv <file>.ipk`.
"""
import argparse
import io
import json
import struct
import tarfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "launcher"
DIST = ROOT / "dist"


def png(width, height, pixel):
    rows = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))

    def chunk(tag, data):
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def draw_icon(size):
    """Four flat squares on a dark background."""
    bg = (12, 12, 14, 255)
    cols = [(0, 163, 163, 255), (31, 95, 191, 255), (179, 18, 58, 255), (217, 130, 43, 255)]
    m, g = size * 0.18, size * 0.05
    cell = (size - 2 * m - g) / 2

    def px(x, y):
        for i, (cx, cy) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1))):
            x0 = m + cx * (cell + g)
            y0 = m + cy * (cell + g)
            if x0 <= x < x0 + cell and y0 <= y < y0 + cell:
                return cols[i]
        return bg

    return png(size, size, px)


def tar_gz(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:
        dirs = set()
        for name, data in members:
            parts = Path(name).parts
            for i in range(1, len(parts)):
                d = "./" + "/".join(parts[:i])
                if d not in dirs:
                    dirs.add(d)
                    ti = tarfile.TarInfo(d)
                    ti.type, ti.mode, ti.mtime = tarfile.DIRTYPE, 0o755, int(time.time())
                    tf.addfile(ti)
            ti = tarfile.TarInfo("./" + name)
            ti.size, ti.mode, ti.mtime = len(data), 0o644, int(time.time())
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def ar(entries):
    out = b"!<arch>\n"
    for name, data in entries:
        hdr = f"{name:<16}{int(time.time()):<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
        assert len(hdr) == 60
        out += hdr + data + (b"\n" if len(data) % 2 else b"")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--icons", action="store_true", help="redraw icon.png and largeIcon.png")
    args = ap.parse_args()

    if args.icons or not (APP / "icon.png").exists():
        (APP / "icon.png").write_bytes(draw_icon(80))
        (APP / "largeIcon.png").write_bytes(draw_icon(130))

    info = json.loads((APP / "appinfo.json").read_text())
    app_id, version = info["id"], info["version"]

    files = []
    for p in sorted(APP.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            files.append((f"usr/palm/applications/{app_id}/{p.relative_to(APP).as_posix()}", p.read_bytes()))
    pkginfo = {"app": app_id, "id": app_id, "loc_name": info["title"],
               "package_format_version": 2, "vendor": info.get("vendor", ""), "version": version}
    files.append((f"usr/palm/packages/{app_id}/packageinfo.json", json.dumps(pkginfo, indent=1).encode()))

    size = sum(len(d) for _, d in files)
    control = (f"Package: {app_id}\nVersion: {version}\nSection: misc\nPriority: optional\n"
               f"Architecture: all\nInstalled-Size: {size}\nMaintainer: lg-privacy-kit <noreply@invalid>\n"
               f"Description: {info['title']}\nwebOS-Package-Format-Version: 2\n"
               f"webOS-Packager-Version: 1.0.0\n").encode()

    ipk = ar([("debian-binary", b"2.0\n"),
              ("control.tar.gz", tar_gz([("control", control)])),
              ("data.tar.gz", tar_gz(files))])
    DIST.mkdir(exist_ok=True)
    out = DIST / f"{app_id}_{version}_all.ipk"
    out.write_bytes(ipk)
    print(f"Built {out} ({len(ipk) // 1024} KB, {len(files)} files)")


if __name__ == "__main__":
    main()
