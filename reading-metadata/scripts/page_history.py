#!/usr/bin/env python3
"""
page_history.py - Build a reading timeline / sessions from page.history.store.

Each metrics sidecar (.azw3f / .yjf / .mbs) holds page.history.store = a list of
{position, time}. This groups consecutive page-turn timestamps into reading
sessions (gap > GAP_MIN minutes starts a new session) and reports per-book and
per-day reading activity.

Usage:
    python page_history.py [DOCUMENTS_DIR] [GAP_MIN]
Defaults: DOCUMENTS_DIR=D:/documents  GAP_MIN=15
"""
import csv, glob, json, os, sys
from datetime import datetime

DOCS = sys.argv[1] if len(sys.argv) > 1 else "D:/documents"
GAP_MIN = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
HERE = os.path.dirname(os.path.abspath(__file__))

# Metrics sidecars, by container format: AZW3, KFX, legacy MOBI.
METRICS_EXTS = (".azw3f", ".yjf", ".mbs")


def _strip_ext(name, exts):
    """Drop the sidecar extension (the basename also carries a device hash)."""
    for ext in exts:
        if ext in name:
            return name.rsplit(ext, 1)[0]
    return name


def clean_title(path):
    b = os.path.basename(path).split("7d1790cc")[0]
    return _strip_ext(b, METRICS_EXTS).strip(" -_")


def parse(t):
    try:
        return datetime.fromisoformat(t)
    except Exception:
        return None


def sessions(times, gap_min):
    times = sorted(t for t in times if t)
    if not times:
        return []
    out, start, last = [], times[0], times[0]
    for t in times[1:]:
        if (t - last).total_seconds() > gap_min * 60:
            out.append((start, last))
            start = t
        last = t
    out.append((start, last))
    return out


def main():
    by_day = {}
    rows = []
    files = []
    for ext in METRICS_EXTS:
        files += glob.glob(os.path.join(DOCS, "**", "*" + ext + ".json"), recursive=True)
    for j in files:
        try:
            d = json.load(open(j, encoding="utf-8"))
        except Exception:
            continue
        ph = d.get("page.history.store")
        if not ph:
            continue
        times = [parse(r["time"]) for r in ph if isinstance(r, dict) and r.get("time")]
        sess = sessions(times, GAP_MIN)
        title = clean_title(j.rsplit(".json", 1)[0])
        for s, e in sess:
            mins = (e - s).total_seconds() / 60.0
            rows.append({"title": title, "start": s.isoformat(timespec="minutes"),
                         "end": e.isoformat(timespec="minutes"), "minutes": round(mins, 1)})
            by_day[s.date()] = by_day.get(s.date(), 0) + mins

    rows.sort(key=lambda r: r["start"])
    print("=== reading sessions (gap > %g min) ===" % GAP_MIN)
    print("%-40s %-17s %-17s %6s" % ("book", "start", "end", "min"))
    for r in rows:
        print("%-40s %-17s %-17s %6.1f" % (r["title"][:40], r["start"], r["end"], r["minutes"]))

    print("\n=== reading per day ===")
    for day in sorted(by_day):
        print("%s  %6.1f min  (%.1f h)" % (day, by_day[day], by_day[day] / 60))

    out = os.path.join(HERE, "reading_sessions.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["title", "start", "end", "minutes"])
        w.writeheader(); w.writerows(rows)
    print("\nwrote %s  (%d sessions)" % (out, len(rows)))


if __name__ == "__main__":
    main()
