#!/usr/bin/env python3
"""
dump_sidecars.py - Decode every Kindle sidecar to JSON.

Runs the patched krds.py on all .azw3r / .azw3f (and .yjr/.yjf/.mbp1/.mbs)
under DOCUMENTS_DIR, leaving a <file>.json next to each.

Usage:
    python dump_sidecars.py [DOCUMENTS_DIR]
Default DOCUMENTS_DIR = D:/documents
"""
import glob, os, subprocess, sys

DOCS = sys.argv[1] if len(sys.argv) > 1 else "D:/documents"
HERE = os.path.dirname(os.path.abspath(__file__))
KRDS_PY = os.path.join(HERE, "krds.py")
EXTS = ("*.azw3r", "*.azw3f", "*.yjr", "*.yjf", "*.mbp1", "*.mbs")


def main():
    files = []
    for ext in EXTS:
        files += glob.glob(os.path.join(DOCS, "**", ext), recursive=True)
    print("found %d sidecar files" % len(files))
    ok = fail = 0
    for f in files:
        r = subprocess.run([sys.executable, KRDS_PY, f], capture_output=True, text=True, timeout=60)
        if os.path.exists(f + ".json"):
            ok += 1
        else:
            fail += 1
            print("FAILED:", os.path.basename(f))
            tail = (r.stderr or "").strip().splitlines()[-1:]
            if tail:
                print("   ", tail[0])
    print("decoded %d ok, %d failed" % (ok, fail))


if __name__ == "__main__":
    main()
