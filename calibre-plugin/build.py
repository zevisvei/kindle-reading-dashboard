#!/usr/bin/env python3
"""Zip this folder into an installable Calibre plugin.

    python build.py            # -> kindle_reading_dashboard.zip
    calibre-customize -a kindle_reading_dashboard.zip   # install

The zip must contain __init__.py and plugin-import-name-kindle_reading.txt at
its root; everything imports under the calibre_plugins.kindle_reading package.
"""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "kindle_reading_dashboard.zip")
INCLUDE = (".py", ".txt", ".png")
SKIP_FILES = {"build.py"}


def main():
    if os.path.exists(OUT):
        os.remove(OUT)
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(HERE):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f in SKIP_FILES or not f.endswith(INCLUDE):
                    continue
                full = os.path.join(root, f)
                if os.path.abspath(full) == OUT:
                    continue
                arc = os.path.relpath(full, HERE).replace(os.sep, "/")
                z.write(full, arc)
                n += 1
    print("wrote %s (%d files)" % (OUT, n))


if __name__ == "__main__":
    main()
