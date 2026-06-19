#!/usr/bin/env python3
"""
Kindle Reader Dashboard
=======================
Pulls the Kindle library DB (cc.db) + reading-metadata sidecars (.azw3f/.azw3r),
decodes them, and serves a local web UI:
  - a list of every book with basic metadata
  - a detail page per book with ALL metadata (cc.db + KRDS sidecars)

Usage:
    python dashboard.py serve              # SSH sync (default) + build + serve
    python dashboard.py serve --local D:/documents   # read from a USB/local folder
    python dashboard.py serve --no-sync    # reuse the existing cache, just build+serve
    python dashboard.py sync               # only pull files into cache/
    python dashboard.py build              # only (re)build web/library.json

Connection: SSH defaults to the device in ksh.py (192.168.15.244, root/kindle).
"""
import argparse
import datetime
import glob
import json
import logging
import os
import posixpath
import sqlite3
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "cache")
WEB = os.path.join(HERE, "web")
KRDS_DIR = os.path.join(ROOT, "reading-metadata", "scripts")
DB_PATH = os.path.join(CACHE, "cc.db")
FMCACHE_PATH = os.path.join(CACHE, "fmcache.db")
DOCS_CACHE = os.path.join(CACHE, "documents")
REMOTE_DOCS = "/mnt/us/documents"

sys.path.insert(0, KRDS_DIR)
import krds  # noqa: E402  (the patched parser)

_log = logging.getLogger("krds")
_log.setLevel(logging.ERROR)


# --------------------------------------------------------------------------- #
#  SSH sync (single persistent connection, streams files via `cat`)
# --------------------------------------------------------------------------- #
def _ssh_connect():
    import paramiko
    sys.path.insert(0, ROOT)
    import ksh  # HOST / USER / PW live here
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # ksh.connect_kwargs() handles password AND public-key login (key-only
    # devices with password auth disabled). Falls back to legacy kwargs.
    if hasattr(ksh, "connect_kwargs"):
        c.connect(ksh.HOST, **ksh.connect_kwargs())
    else:
        c.connect(ksh.HOST, username=ksh.USER, password=ksh.PW,
                  timeout=15, look_for_keys=False, allow_agent=False)
    return c, ksh.HOST


def _shq(path):
    """Single-quote a path for the remote sh, escaping embedded single quotes."""
    return "'" + path.replace("'", "'\\''") + "'"


def _ssh_read(c, remote, retries=3):
    last = b""
    for _ in range(retries):
        _in, out, err = c.exec_command("cat %s" % _shq(remote))
        data = out.channel.makefile("rb").read()
        out.channel.recv_exit_status()
        if data:
            return data
        last = data
    return last


def sync_ssh():
    os.makedirs(CACHE, exist_ok=True)
    c, host = _ssh_connect()
    print("connected to %s" % host)
    # 1) the library DB
    db = _ssh_read(c, "/var/local/cc.db")
    with open(DB_PATH, "wb") as f:
        f.write(db)
    print("cc.db (%d bytes)" % len(db))
    # 1b) fast-metrics cache holds the modern (KPP) read-state marks + device
    #     reading sessions, which are NOT in cc.db. Best-effort.
    try:
        fm = _ssh_read(c, "/mnt/us/system/fmcache/fmcache.db")
        if fm:
            with open(FMCACHE_PATH, "wb") as f:
                f.write(fm)
            print("fmcache.db (%d bytes)" % len(fm))
    except Exception as e:
        print("fmcache.db skipped (%s)" % e)
    # 2) every sidecar under documents
    _in, out, _e = c.exec_command(
        "find '%s' \\( -name '*.azw3f' -o -name '*.azw3r' \\)" % REMOTE_DOCS)
    files = [l for l in out.read().decode("utf-8", "replace").splitlines() if l.strip()]
    print("found %d sidecars" % len(files))
    for i, remote in enumerate(files, 1):
        rel = posixpath.relpath(remote, REMOTE_DOCS)
        local = os.path.join(DOCS_CACHE, *rel.split("/"))
        os.makedirs(os.path.dirname(local), exist_ok=True)
        data = _ssh_read(c, remote)
        with open(local, "wb") as f:
            f.write(data)
        if i % 10 == 0 or i == len(files):
            print("  %d/%d" % (i, len(files)))
    c.close()
    print("sync complete -> %s" % CACHE)


def sync_local(docs):
    """Mirror cc.db + sidecars from a locally-mounted Kindle (e.g. D:/documents)."""
    import shutil
    os.makedirs(DOCS_CACHE, exist_ok=True)
    # cc.db: try the mount root's /system or var? On USB mass-storage cc.db is NOT
    # exposed, so allow a pre-pulled cache/cc.db to stand. Only sidecars come from USB.
    n = 0
    for pat in ("**/*.azw3f", "**/*.azw3r"):
        for src in glob.glob(os.path.join(docs, pat), recursive=True):
            rel = os.path.relpath(src, docs)
            dst = os.path.join(DOCS_CACHE, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            n += 1
    print("copied %d sidecars from %s" % (n, docs))
    if not os.path.exists(DB_PATH):
        print("WARNING: %s missing. cc.db is not on the USB partition; pull it once "
              "via SSH (python ksh.py get /var/local/cc.db reader-dashboard/cache/cc.db)."
              % DB_PATH)


# --------------------------------------------------------------------------- #
#  Decode + build library.json
# --------------------------------------------------------------------------- #
def decode_sidecar(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
        return krds.KindleReaderDataStore(_log, data).deserialize()
    except Exception as e:
        return {"_error": "%s: %s" % (type(e).__name__, e)}


def _jload(s, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def _author(row):
    cr = _jload(row.get("j_credits"), [])
    if cr and isinstance(cr, list):
        first = cr[0]
        nm = first.get("name") if isinstance(first, dict) else None
        if isinstance(nm, dict):
            return nm.get("display") or nm.get("collation") or ""
        if isinstance(nm, str):
            return nm
    return row.get("p_credits_0_name_collation") or ""


def _stats_from_timer(tm, bi):
    """Derive human numbers from timer.model + book.info.store."""
    out = {}
    if not isinstance(tm, dict):
        return out
    tt = tm.get("totalTime") or 0
    tw = tm.get("totalWords") or 0
    tp = tm.get("totalPercent") or 0.0
    mins = tt / 60000.0
    out["minutes"] = round(mins, 1)
    out["hours"] = round(mins / 60.0, 2)
    out["words_read"] = tw
    out["percent_read"] = round(tp * 100, 1)
    out["wpm_overall"] = round(tw / mins) if mins else 0
    # clean reading speed = words/min across the normal distributions
    ac = tm.get("averageCalculator") or {}
    dists = ac.get("normalDistributions") or []
    s = sum(d.get("sum", 0) for d in dists if isinstance(d, dict))
    n = sum(d.get("count", 0) for d in dists if isinstance(d, dict))
    out["wpm_reading"] = round(s / n) if n else out["wpm_overall"]
    out["speed_samples"] = n
    if isinstance(bi, dict):
        out["book_words"] = bi.get("numberOfWords", 0)
    return out


def load_fmcache():
    """Parse fmcache.db (fast-metrics) for modern read-state marks + device
    reading sessions. Returns {read_state:{asin:..}, sessions:{asin:[..]},
    aggregate:{read,manual_unread,unknown}}. Empty if file absent."""
    out = {"read_state": {}, "sessions": {}, "aggregate": None, "records": {}}
    if not os.path.exists(FMCACHE_PATH):
        return out
    try:
        con = sqlite3.connect(FMCACHE_PATH)
        cur = con.cursor()
    except sqlite3.Error:
        return out
    # read-state marks (latest per asin wins, by created_timestamp)
    latest = {}
    try:
        for rec, ts in cur.execute(
                "SELECT record, created_timestamp FROM records "
                "WHERE schema_name='mar_content_readstate_update_success'"):
            d = json.loads(rec)
            asin = d.get("updated_asin")
            if asin and ts >= latest.get(asin, -1):
                latest[asin] = ts
                out["read_state"][asin] = d.get("read_state")  # READ / UNREAD
    except (sqlite3.Error, ValueError):
        pass
    # all raw records grouped by the book they reference (asin/cde_key/book_asin)
    try:
        for sch, rec, ts in cur.execute(
                "SELECT schema_name, record, created_timestamp FROM records"):
            try:
                d = json.loads(rec)
            except ValueError:
                continue
            asin = (d.get("updated_asin") or d.get("cde_key")
                    or d.get("book_asin") or d.get("asin"))
            if asin:
                out["records"].setdefault(asin, []).append(
                    {"schema": sch, "time": ts, "data": d})
    except sqlite3.Error:
        pass
    # library-wide aggregate (last one)
    try:
        rows = cur.execute("SELECT record FROM records WHERE "
                           "schema_name='mar_book_read_stats'").fetchall()
        if rows:
            out["aggregate"] = json.loads(rows[-1][0])
    except (sqlite3.Error, ValueError):
        pass
    # device reading sessions
    try:
        for (sess,) in cur.execute("SELECT session FROM reading_sessions"):
            p = json.loads(sess).get("payload", {})
            asin = p.get("asin")
            if not asin:
                continue
            st, en = p.get("start_timestamp"), p.get("end_timestamp")
            out["sessions"].setdefault(asin, []).append({
                "start": datetime.datetime.fromtimestamp(st / 1000).isoformat() if st else None,
                "end": datetime.datetime.fromtimestamp(en / 1000).isoformat() if en else None,
                "minutes": round((en - st) / 60000.0, 1) if (st and en) else None,
                "startLoc": p.get("start_reading_location"),
                "endLoc": p.get("end_reading_location"),
                "complete": p.get("is_complete"),
            })
    except (sqlite3.Error, ValueError):
        pass
    con.close()
    return out


def build():
    os.makedirs(WEB, exist_ok=True)
    if not os.path.exists(DB_PATH):
        sys.exit("cc.db not found in cache. Run 'sync' first.")
    fm = load_fmcache()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # series membership: cdeKey -> {seriesId, position label}
    series_of = {}
    try:
        for r in cur.execute("SELECT d_itemCdeKey, d_seriesId, d_itemPositionLabel, "
                             "d_itemPosition FROM Series"):
            series_of[r[0]] = {"seriesId": r[1], "label": r[2], "pos": r[3]}
    except sqlite3.Error:
        pass
    # series container names: seriesId(asin/cdeGroup) -> title
    series_name = {}
    try:
        for r in cur.execute("SELECT p_cdeKey, p_cdeGroup, p_titles_0_nominal FROM "
                             "Entries WHERE p_type='Entry:Item:Series'"):
            for k in (r[0], r[1]):
                if k:
                    series_name[k] = r[2]
    except sqlite3.Error:
        pass

    cols = [d[1] for d in cur.execute("PRAGMA table_info(Entries)")]
    rows = cur.execute(
        "SELECT * FROM Entries WHERE p_type='Entry:Item'").fetchall()

    books = []
    for row in rows:
        r = {k: row[k] for k in cols}
        loc = r.get("p_location") or ""
        # locate sidecars: <loc minus ext>.sdr/  under the cache
        sidecar = {"azw3f": None, "azw3r": None}
        if loc.startswith("/mnt/us/"):
            rel = loc[len("/mnt/us/"):]              # documents/.../book.azw3
            base, _ext = os.path.splitext(rel)
            sdr_rel = base + ".sdr"
            sdr_local = os.path.join(CACHE, *sdr_rel.split("/"))
            if os.path.isdir(sdr_local):
                for f in os.listdir(sdr_local):
                    p = os.path.join(sdr_local, f)
                    if f.endswith(".azw3f"):
                        sidecar["azw3f"] = decode_sidecar(p)
                    elif f.endswith(".azw3r"):
                        sidecar["azw3r"] = decode_sidecar(p)

        azw3f = sidecar["azw3f"] or {}
        azw3r = sidecar["azw3r"] or {}
        tm = azw3f.get("timer.model")
        bi = azw3f.get("book.info.store")
        stats = _stats_from_timer(tm, bi)

        # annotations
        annots = []
        aco = (azw3r.get("annotation.cache.object") or {})
        if isinstance(aco, dict):
            for cls, items in aco.items():
                if isinstance(items, list):
                    for it in items:
                        annots.append({"type": cls.split(".")[-1], **it})

        cde = r.get("p_cdeKey")
        ser = series_of.get(cde)
        # modern read-state (fmcache) overrides legacy cc.db p_readState
        fm_rs = fm["read_state"].get(cde)            # 'READ' / 'UNREAD' / None
        dev_sessions = sorted(fm["sessions"].get(cde, []), key=lambda s: s["start"] or "")
        rstate = r.get("p_readState")
        if fm_rs == "READ" or rstate == 2:
            read_status = "read"
        elif fm_rs == "UNREAD":
            read_status = "unread"
        elif rstate == 1:
            read_status = "reading"
        else:
            read_status = "unread"
        book = {
            "uuid": r.get("p_uuid"),
            "title": r.get("p_titles_0_nominal") or "(ללא כותרת)",
            "author": _author(r),
            "cdeKey": cde,
            "cdeType": r.get("p_cdeType"),
            "publisher": r.get("p_publisher"),
            "language": r.get("p_languages_0"),
            "percentFinished": r.get("p_percentFinished"),
            "readState": r.get("p_readState"),
            "read_status": read_status,          # read / reading / unread (fmcache-aware)
            "fm_read_state": fm_rs,              # READ / UNREAD / None (modern mark)
            "device_sessions": dev_sessions,     # from fmcache reading_sessions
            "fmcache_raw": {                     # raw data from the new DB (fmcache)
                "read_state": fm_rs,
                "reading_sessions": dev_sessions,
                "records": fm["records"].get(cde, []),
            },
            "lastAccess": r.get("p_lastAccess"),
            "location": loc,
            "isArchived": r.get("p_isArchived"),
            "contentSize": r.get("p_contentSize"),
            "series": {
                "label": ser["label"] if ser else None,
                "name": series_name.get(ser["seriesId"]) if ser else None,
                "seriesId": ser["seriesId"] if ser else None,
            } if ser else None,
            "stats": stats,
            "annotations": annots,
            "has_sidecar": bool(tm or azw3r),
            # raw blobs for the detail page
            "cc_raw": {k: v for k, v in r.items() if v is not None},
            "azw3f": azw3f,
            "azw3r": azw3r,
        }
        books.append(book)

    # sort: most-read first
    books.sort(key=lambda b: -(b["stats"].get("minutes") or 0))

    totals = {
        "books": len(books),
        "with_sidecar": sum(1 for b in books if b["has_sidecar"]),
        "hours": round(sum((b["stats"].get("minutes") or 0) for b in books) / 60, 1),
        "words": sum((b["stats"].get("words_read") or 0) for b in books),
        "annotations": sum(len(b["annotations"]) for b in books),
        "read": sum(1 for b in books if b["read_status"] == "read"),
        "reading": sum(1 for b in books if b["read_status"] == "reading"),
        "fm_aggregate": fm["aggregate"],   # account-wide {read, manual_unread, unknown}
    }
    tmin = sum((b["stats"].get("minutes") or 0) for b in books)
    twords = totals["words"]
    totals["avg_wpm"] = round(twords / tmin) if tmin else 0

    out = {"totals": totals, "books": books}
    with open(os.path.join(WEB, "library.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    con.close()
    print("built library.json: %d books, %d with reading data, %.1f h, %d words"
          % (totals["books"], totals["with_sidecar"], totals["hours"], totals["words"]))


# --------------------------------------------------------------------------- #
#  Serve
# --------------------------------------------------------------------------- #
class Handler(SimpleHTTPRequestHandler):
    sync_mode = ("ssh", None)  # (mode, local_path)

    def __init__(self, *a, **k):
        super().__init__(*a, directory=WEB, **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        # never cache HTML/JS/JSON so edits + rebuilds show on a plain refresh
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def do_POST(self):
        if self.path.rstrip("/") == "/api/refresh":
            try:
                mode, local = Handler.sync_mode
                if mode == "ssh":
                    sync_ssh()
                elif mode == "local" and local:
                    sync_local(local)
                build()
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            self.send_error(404)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port=8742):
    os.chdir(WEB)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    print("serving dashboard at %s  (Ctrl-C to stop)" % url)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Kindle Reader Dashboard")
    ap.add_argument("cmd", choices=["serve", "sync", "build"], nargs="?", default="serve")
    ap.add_argument("--local", metavar="DOCS_DIR",
                    help="read sidecars from a local/USB documents folder instead of SSH")
    ap.add_argument("--no-sync", action="store_true", help="skip syncing, reuse cache")
    ap.add_argument("--port", type=int, default=8742)
    args = ap.parse_args()

    mode = ("local", args.local) if args.local else ("ssh", None)
    Handler.sync_mode = mode

    if args.cmd == "sync":
        sync_local(args.local) if args.local else sync_ssh()
        return
    if args.cmd == "build":
        build()
        return
    # serve
    if not args.no_sync:
        try:
            sync_local(args.local) if args.local else sync_ssh()
        except Exception as e:
            print("sync failed (%s) - serving existing cache" % e)
    build()
    serve(args.port)


if __name__ == "__main__":
    main()
