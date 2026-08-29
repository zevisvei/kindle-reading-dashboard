#!/usr/bin/env python3
"""
reading_stats.py - Kindle native reading statistics from the metrics sidecars.

Decodes every <book>.sdr metrics sidecar — *.azw3f (AZW3), *.yjf (KFX), *.mbs
(MOBI) — via the (patched) krds.py in this folder, pulls timer.model +
book.info.store, prints a per-book table + totals, and writes
reading_stats.csv next to this script.

Usage:
    python reading_stats.py [DOCUMENTS_DIR]
Default DOCUMENTS_DIR = D:/documents  (Kindle mounted as a drive)
"""
import csv, glob, json, os, subprocess, sys

DOCS = sys.argv[1] if len(sys.argv) > 1 else "D:/documents"
HERE = os.path.dirname(os.path.abspath(__file__))
KRDS_PY = os.path.join(HERE, "krds.py")

# Metrics sidecars, by container format: AZW3, KFX, legacy MOBI.
METRICS_EXTS = (".azw3f", ".yjf", ".mbs")


def _strip_ext(name, exts):
    """Drop the sidecar extension (the basename also carries a device hash)."""
    for ext in exts:
        if ext in name:
            return name.rsplit(ext, 1)[0]
    return name


def decode_all(docs):
    out = []
    for ext in METRICS_EXTS:
        for f in glob.glob(os.path.join(docs, "**", "*" + ext), recursive=True):
            j = f + ".json"
            if not (os.path.exists(j) and os.path.getmtime(j) >= os.path.getmtime(f)):
                subprocess.run([sys.executable, KRDS_PY, f], capture_output=True, timeout=60)
            if os.path.exists(j):
                out.append(j)
    return out


def clean_title(path):
    b = os.path.basename(path).split("7d1790cc")[0]
    return _strip_ext(b, METRICS_EXTS).strip(" -_")


def main():
    if not os.path.isfile(KRDS_PY):
        sys.exit("krds.py not found next to this script")
    rows = []
    for j in decode_all(DOCS):
        try:
            d = json.load(open(j, encoding="utf-8"))
        except Exception:
            continue
        tm = d.get("timer.model")
        if not tm:
            continue
        bi = d.get("book.info.store") or {}
        tt, tw, tp = tm.get("totalTime", 0), tm.get("totalWords", 0), tm.get("totalPercent", 0.0)
        mins = tt / 60000.0
        rows.append({
            "title": clean_title(j), "minutes": round(mins, 1), "words_read": tw,
            "wpm": round(tw / mins) if mins else 0, "percent": round(tp * 100, 1),
            "book_words": bi.get("numberOfWords", 0),
        })
    rows.sort(key=lambda r: -r["minutes"])

    print("%-46s %8s %9s %5s %6s" % ("book", "time", "words", "wpm", "%"))
    print("-" * 82)
    tmin = twords = 0
    for r in rows:
        h, m = int(r["minutes"] // 60), int(r["minutes"] % 60)
        print("%-46s %4dh%02dm %9d %5d %5.0f%%" % (r["title"][:46], h, m, r["words_read"], r["wpm"], r["percent"]))
        tmin += r["minutes"]; twords += r["words_read"]
    print("-" * 82)
    print("TOTAL: %.1f hours | %d words read | avg %.0f wpm | %d books" % (
        tmin / 60, twords, (twords / tmin if tmin else 0), len(rows)))

    out = os.path.join(HERE, "reading_stats.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["title", "minutes", "words_read", "wpm", "percent", "book_words"])
        w.writeheader(); w.writerows(rows)
    print("\nwrote %s" % out)


if __name__ == "__main__":
    main()
