#!/usr/bin/env python3
"""
export_annotations.py - Export highlights / notes / bookmarks from .azw3r.

Reads annotation.cache.object from every decoded .azw3r.json and writes them
to annotations.csv (type, book, start, end, created, note text).

Run dump_sidecars.py first (or this will decode on the fly).

Usage:
    python export_annotations.py [DOCUMENTS_DIR]
Default DOCUMENTS_DIR = D:/documents
"""
import csv, glob, json, os, subprocess, sys

DOCS = sys.argv[1] if len(sys.argv) > 1 else "D:/documents"
HERE = os.path.dirname(os.path.abspath(__file__))
KRDS_PY = os.path.join(HERE, "krds.py")


def clean_title(path):
    b = os.path.basename(path).split("7d1790cc")[0]
    return b.rsplit(".azw3r", 1)[0].strip(" -_")


def main():
    rows = []
    for f in glob.glob(os.path.join(DOCS, "**", "*.azw3r"), recursive=True):
        j = f + ".json"
        if not os.path.exists(j):
            subprocess.run([sys.executable, KRDS_PY, f], capture_output=True, timeout=60)
        if not os.path.exists(j):
            continue
        try:
            d = json.load(open(j, encoding="utf-8"))
        except Exception:
            continue
        cache = d.get("annotation.cache.object")
        if not cache or not isinstance(cache, dict):
            continue
        title = clean_title(f)
        for atype, items in cache.items():
            for a in (items or []):
                if not isinstance(a, dict):
                    continue
                rows.append({
                    "type": atype.replace("annotation.personal.", ""),
                    "book": title,
                    "start": a.get("startPosition", ""),
                    "end": a.get("endPosition", ""),
                    "created": a.get("creationTime", ""),
                    "note": a.get("note", ""),
                })

    if not rows:
        print("No annotations found (your books have empty annotation caches).")
        return
    out = os.path.join(HERE, "annotations.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["type", "book", "start", "end", "created", "note"])
        w.writeheader(); w.writerows(rows)
    print("wrote %s  (%d annotations)" % (out, len(rows)))


if __name__ == "__main__":
    main()
