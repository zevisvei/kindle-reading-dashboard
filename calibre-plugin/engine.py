#!/usr/bin/env python
"""
Data layer: pull Kindle on-device reading metadata (USB or SSH), decode the
KRDS `.azw3f`/`.azw3r` sidecars (+ cc.db / fmcache.db over SSH), and return a
normalised per-book list ready to map onto Calibre books.

Self-contained: only the Python standard library + the bundled `krds` parser.
SSH uses paramiko when importable, otherwise shells out to the system `ssh`.
"""
import datetime
import glob
import json
import logging
import os
import posixpath
import shutil
import sqlite3
import subprocess
import sys

from calibre_plugins.kindle_reading import krds

# krds logs "Unknown data structure ..." at ERROR for every field it does not
# model — harmless, but a flood across a library. Mute it (CRITICAL + a
# NullHandler, no propagation to the root/calibre handlers).
_log = logging.getLogger("kindle_reading.krds")
_log.setLevel(logging.CRITICAL)
_log.addHandler(logging.NullHandler())
_log.propagate = False

REMOTE_DOCS = "/mnt/us/documents"
REMOTE_CCDB = "/var/local/cc.db"
REMOTE_FMCACHE = "/mnt/us/system/fmcache/fmcache.db"


def _no_window_kwargs():
    """On Windows, keep subprocesses from popping a console window (and from
    stealing focus / freezing the UI)."""
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kw["startupinfo"] = si
        except Exception:  # noqa
            pass
    return kw


# --------------------------------------------------------------------------- #
#  KRDS decode + numeric stats  (ported from reader-dashboard/dashboard.py)
# --------------------------------------------------------------------------- #
def decode_sidecar(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
        # krds prints "Unknown data structure ..." to stdout for fields it does
        # not model — across a whole library that is a flood of lines into the
        # GUI log. Silence stdout for the duration of the parse.
        import contextlib
        import io as _io
        with contextlib.redirect_stdout(_io.StringIO()):
            return krds.KindleReaderDataStore(_log, data).deserialize()
    except Exception as e:  # noqa
        return {"_error": "%s: %s" % (type(e).__name__, e)}


def _jload(s, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:  # noqa
        return default


def _author(row):
    cr = _jload(row.get("j_credits"), [])
    if cr and isinstance(cr, list):
        nm = cr[0].get("name") if isinstance(cr[0], dict) else None
        if isinstance(nm, dict):
            return nm.get("display") or nm.get("collation") or ""
        if isinstance(nm, str):
            return nm
    return row.get("p_credits_0_name_collation") or ""


def _stats_from_timer(tm, bi):
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
    ac = tm.get("averageCalculator") or {}
    dists = ac.get("normalDistributions") or []
    s = sum(d.get("sum", 0) for d in dists if isinstance(d, dict))
    n = sum(d.get("count", 0) for d in dists if isinstance(d, dict))
    out["wpm_reading"] = round(s / n) if n else out["wpm_overall"]
    if isinstance(bi, dict):
        out["book_words"] = bi.get("numberOfWords", 0)
    return out


# ---- page mapping (apnx oPNToPosition: index = printed page) -------------- #
def _apnx(azw3r):
    return (azw3r or {}).get("apnx.key") or None


def _total_pages(azw3r):
    a = _apnx(azw3r)
    if a and isinstance(a.get("oPNToPosition"), list):
        return len(a["oPNToPosition"]) - 1
    return None


def _page_of(azw3r, position):
    a = _apnx(azw3r)
    if not a or not isinstance(a.get("oPNToPosition"), list) or position is None:
        return None
    arr = a["oPNToPosition"]
    lo, hi, ans = 0, len(arr) - 1, 0
    while lo <= hi:                       # last index whose position <= target
        mid = (lo + hi) // 2
        if arr[mid] <= position:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def _furthest_position(azw3f):
    """fpr = furthest position read (not the last)."""
    for k in ("fpr", "lpr"):
        v = azw3f.get(k)
        if isinstance(v, dict) and v.get("position") is not None:
            try:
                return int(v["position"])
            except (TypeError, ValueError):
                pass
    return None


def _asin_from_info(azw3f):
    bi = azw3f.get("book.info.store")
    if isinstance(bi, dict):
        for k in ("asin", "ASIN", "cdeKey", "cde_key"):
            if bi.get(k):
                return str(bi[k])
    return None


# --------------------------------------------------------------------------- #
#  fmcache.db (modern read-state)  — SSH only (not on USB partition)
# --------------------------------------------------------------------------- #
def load_fmcache(path):
    out = {"read_state": {}}
    if not path or not os.path.exists(path):
        return out
    try:
        con = sqlite3.connect(path)
        cur = con.cursor()
    except sqlite3.Error:
        return out
    latest = {}
    try:
        for rec, ts in cur.execute(
                "SELECT record, created_timestamp FROM records "
                "WHERE schema_name='mar_content_readstate_update_success'"):
            d = json.loads(rec)
            asin = d.get("updated_asin")
            if asin and ts >= latest.get(asin, -1):
                latest[asin] = ts
                out["read_state"][asin] = d.get("read_state")   # READ / UNREAD
    except (sqlite3.Error, ValueError):
        pass
    con.close()
    return out


# --------------------------------------------------------------------------- #
#  SSH transport: paramiko if available, else the system `ssh` binary
# --------------------------------------------------------------------------- #
class SSH(object):
    def __init__(self, host, user, password, key):
        self.host, self.user, self.password, self.key = host, user, password, key
        self._c = None
        self._mode = None
        try:
            import paramiko  # noqa
            self._mode = "paramiko"
        except Exception:  # noqa
            self._mode = "binary"

    # -- paramiko --
    def _connect(self):
        if self._c is not None:
            return
        import paramiko
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # IMPORTANT: only touch the ssh-agent / ~/.ssh keys when a key file is
        # configured. On Windows, agent/Pageant enumeration can block for a long
        # time and freeze Calibre's UI ("black screen"), so password-only logins
        # must disable both. Mirrors the dashboard's legacy connect path.
        kw = {"username": self.user, "timeout": 15, "banner_timeout": 15,
              "auth_timeout": 15}
        if self.key:
            kw["key_filename"] = os.path.expanduser(self.key)
            kw["look_for_keys"] = True
            kw["allow_agent"] = True
        else:
            kw["look_for_keys"] = False
            kw["allow_agent"] = False
        if self.password:
            kw["password"] = self.password
        c.connect(self.host, **kw)
        self._c = c

    def _ssh_args(self):
        a = ["ssh", "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=" + os.devnull,
             "-o", "ConnectTimeout=15", "-o", "BatchMode=yes"]
        if self.key:
            a += ["-i", os.path.expanduser(self.key)]
        a += ["%s@%s" % (self.user, self.host)]
        return a

    def run_bytes(self, remote_cmd):
        """Run a remote command, return stdout bytes."""
        if self._mode == "paramiko":
            self._connect()
            _in, out, _err = self._c.exec_command(remote_cmd)
            data = out.channel.makefile("rb").read()
            out.channel.recv_exit_status()
            return data
        # binary ssh — never let it pop a console window or block forever
        try:
            p = subprocess.run(self._ssh_args() + [remote_cmd],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=60, **_no_window_kwargs())
            return p.stdout
        except subprocess.TimeoutExpired:
            return b""

    def read_file(self, remote):
        q = "'" + remote.replace("'", "'\\''") + "'"
        return self.run_bytes("cat %s" % q)

    def find_sidecars(self):
        cmd = ("find '%s' \\( -name '*.azw3f' -o -name '*.azw3r' \\)"
               % REMOTE_DOCS)
        data = self.run_bytes(cmd)
        return [l for l in data.decode("utf-8", "replace").splitlines()
                if l.strip()]

    def close(self):
        if self._c is not None:
            try:
                self._c.close()
            except Exception:  # noqa
                pass


# --------------------------------------------------------------------------- #
#  Sync into a local cache dir (mirrors reader-dashboard cache layout)
# --------------------------------------------------------------------------- #
def ssh_reachable(host, timeout=2.5):
    """Fast TCP probe so we never fire a storm of ssh calls at an unreachable
    usbnet IP (the cause of the console-window flood + UI freeze)."""
    import socket
    try:
        s = socket.create_connection((host, 22), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def sync_ssh(cache, cfg, progress=lambda *a: None):
    if not ssh_reachable(cfg["ssh_host"]):
        raise RuntimeError(
            "SSH host %s:22 is not reachable. If the Kindle is plugged in as a "
            "USB drive, use the USB source instead (usbnet/SSH needs the device "
            "configured for network, not mass-storage)." % cfg["ssh_host"])
    docs = os.path.join(cache, "documents")
    os.makedirs(docs, exist_ok=True)
    ssh = SSH(cfg["ssh_host"], cfg["ssh_user"], cfg["ssh_password"], cfg["ssh_key"])
    try:
        db = ssh.read_file(REMOTE_CCDB)
        if db:
            with open(os.path.join(cache, "cc.db"), "wb") as f:
                f.write(db)
        try:
            fm = ssh.read_file(REMOTE_FMCACHE)
            if fm:
                with open(os.path.join(cache, "fmcache.db"), "wb") as f:
                    f.write(fm)
        except Exception:  # noqa
            pass
        files = ssh.find_sidecars()
        for i, remote in enumerate(files, 1):
            rel = posixpath.relpath(remote, REMOTE_DOCS)
            local = os.path.join(docs, *rel.split("/"))
            os.makedirs(os.path.dirname(local), exist_ok=True)
            data = ssh.read_file(remote)
            with open(local, "wb") as f:
                f.write(data)
            progress(i, len(files))
    finally:
        ssh.close()


def sync_usb(documents, cache, progress=lambda *a: None):
    docs = os.path.join(cache, "documents")
    os.makedirs(docs, exist_ok=True)
    srcs = []
    for pat in ("**/*.azw3f", "**/*.azw3r"):
        srcs += glob.glob(os.path.join(documents, pat), recursive=True)
    for i, src in enumerate(srcs, 1):
        rel = os.path.relpath(src, documents)
        dst = os.path.join(docs, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        progress(i, len(srcs))
    # Some Kindles expose cc.db (and fmcache) on the USB partition. If present,
    # grab them so we can build the richer cc.db view (titles + read-status)
    # instead of the sidecar-only view.
    root = os.path.dirname(os.path.normpath(documents))
    for src, dst in ((os.path.join(root, "cc.db"),
                      os.path.join(cache, "cc.db")),
                     (os.path.join(root, "system", "fmcache", "fmcache.db"),
                      os.path.join(cache, "fmcache.db"))):
        try:
            if os.path.isfile(src):
                shutil.copy2(src, dst)
        except Exception:  # noqa
            pass
    return len(srcs)


def _scan_for_kindle_documents():
    """Best-effort: find a connected Kindle's documents folder by scanning
    ready drives. Windows uses GetDriveType so empty/optical drives are skipped
    (avoids the 'no disk' freeze). A drive qualifies if it has a 'documents'
    folder; one that also has cc.db / a system folder is preferred."""
    candidates = []
    if os.name == "nt":
        try:
            import ctypes
            import string
            k = ctypes.windll.kernel32
            mask = k.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if not (mask >> i) & 1:
                    continue
                root = letter + ":\\"
                # 2=removable, 3=fixed; skip cdrom/network/no-media
                if k.GetDriveTypeW(root) not in (2, 3):
                    continue
                candidates.append(root)
        except Exception:  # noqa
            candidates = []
    else:
        for base in ("/media", "/run/media", "/mnt", "/Volumes"):
            if os.path.isdir(base):
                for d in os.listdir(base):
                    candidates.append(os.path.join(base, d))
                    sub = os.path.join(base, d)
                    if os.path.isdir(sub):
                        for d2 in os.listdir(sub):
                            candidates.append(os.path.join(sub, d2))
    best = ""
    for root in candidates:
        try:
            docs = os.path.join(root, "documents")
            if not os.path.isdir(docs):
                continue
            # strong signal: a Kindle has cc.db or a system folder too
            if (os.path.isfile(os.path.join(root, "cc.db"))
                    or os.path.isdir(os.path.join(root, "system"))):
                return docs
            best = best or docs
        except Exception:  # noqa
            continue
    return best


def resolve_usb_documents(cfg):
    """Worker-thread USB locate: explicit pref → Calibre device prefixes →
    drive scan. Runs off the GUI thread so slow IO never freezes the UI."""
    pref = cfg.get("usb_pref") or cfg.get("usb_documents") or ""
    try:
        if pref and os.path.isdir(pref):
            return pref
    except Exception:  # noqa
        pass
    for p in (cfg.get("dev_prefixes") or []):
        try:
            d = os.path.join(p, "documents")
            if os.path.isdir(d):
                return d
        except Exception:  # noqa
            continue
    return _scan_for_kindle_documents()


# --------------------------------------------------------------------------- #
#  Build a normalised per-book list from a synced cache
# --------------------------------------------------------------------------- #
def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _annotations_count(azw3r):
    aco = (azw3r or {}).get("annotation.cache.object")
    if not isinstance(aco, dict):
        return None
    n = 0
    for items in aco.values():
        if isinstance(items, list):
            n += len(items)
    return n or None


def _to_datetime(t):
    """KRDS/epoch timestamp -> aware datetime (best-effort). Accepts s or ms."""
    if not t:
        return None
    try:
        t = float(t)
        if t > 1e12:        # milliseconds
            t /= 1000.0
        if t <= 0:
            return None
        return datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
    except Exception:  # noqa
        return None


def _last_read(azw3f, cc_last_access=None):
    for k in ("lpr", "fpr"):
        v = azw3f.get(k)
        if isinstance(v, dict) and v.get("time"):
            dt = _to_datetime(v["time"])
            if dt:
                return dt
    return _to_datetime(cc_last_access)   # cc.db p_lastAccess (epoch seconds)


def _book_from_sidecars(azw3f, azw3r, title, author, asin, read_status,
                        cc_last_access=None):
    stats = _stats_from_timer(azw3f.get("timer.model"), azw3f.get("book.info.store"))
    pos = _furthest_position(azw3f)
    pg = _page_of(azw3r, pos)
    tot = _total_pages(azw3r)
    page_str = ("%d/%d" % (pg, tot)) if (pg is not None and tot) else (
        str(pg) if pg is not None else None)
    pct = stats.get("percent_read")
    return {
        "title": title,
        "author": author,
        "asin": asin,
        "norm_title": _norm(title),
        "hours": stats.get("hours"),
        "minutes": int(round(stats["minutes"])) if stats.get("minutes") else None,
        "wpm": stats.get("wpm_reading"),
        "wpm_overall": stats.get("wpm_overall"),
        "progress": int(round(pct)) if pct else None,
        "page": page_str,
        "words": stats.get("words_read"),
        "book_words": stats.get("book_words") or None,
        "annotations": _annotations_count(azw3r),
        "status": read_status,
        "last_read": _last_read(azw3f, cc_last_access),
        "has_data": bool(azw3f.get("timer.model") or azw3r),
    }


def build_from_ccdb(cache):
    """SSH path: cc.db present → reliable title/author/asin + read-status."""
    db_path = os.path.join(cache, "cc.db")
    fm = load_fmcache(os.path.join(cache, "fmcache.db"))
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cols = [d[1] for d in cur.execute("PRAGMA table_info(Entries)")]
    rows = cur.execute("SELECT * FROM Entries WHERE p_type='Entry:Item'").fetchall()
    books = []
    for row in rows:
        r = {k: row[k] for k in cols}
        loc = r.get("p_location") or ""
        azw3f, azw3r = {}, {}
        if loc.startswith("/mnt/us/"):
            base, _ = os.path.splitext(loc[len("/mnt/us/"):])
            sdr = os.path.join(cache, *(base + ".sdr").split("/"))
            if os.path.isdir(sdr):
                for f in os.listdir(sdr):
                    p = os.path.join(sdr, f)
                    if f.endswith(".azw3f"):
                        azw3f = decode_sidecar(p)
                    elif f.endswith(".azw3r"):
                        azw3r = decode_sidecar(p)
        cde = r.get("p_cdeKey")
        fm_rs = fm["read_state"].get(cde)
        rstate = r.get("p_readState")
        if fm_rs == "READ" or rstate == 2:
            status = "read"
        elif fm_rs == "UNREAD":
            status = "unread"
        elif rstate == 1:
            status = "reading"
        else:
            status = "unread"
        books.append(_book_from_sidecars(
            azw3f, azw3r,
            r.get("p_titles_0_nominal") or "", _author(r),
            cde, status, cc_last_access=r.get("p_lastAccess")))
    con.close()
    return books


def build_from_usb(cache):
    """USB path: no cc.db → walk sidecars, derive title from folder, asin from
    book.info.store when present. No read-status (lives in fmcache, SSH-only)."""
    docs = os.path.join(cache, "documents")
    books = []
    for dirpath, _dirs, files in os.walk(docs):
        f3 = [f for f in files if f.endswith(".azw3f")]
        if not f3:
            continue
        azw3f = decode_sidecar(os.path.join(dirpath, f3[0]))
        r_files = [f for f in files if f.endswith(".azw3r")]
        azw3r = decode_sidecar(os.path.join(dirpath, r_files[0])) if r_files else {}
        # folder name: "<title> - <author>.sdr"
        folder = os.path.basename(dirpath)
        if folder.endswith(".sdr"):
            folder = folder[:-4]
        title, author = folder, ""
        if " - " in folder:
            title, author = folder.rsplit(" - ", 1)
        books.append(_book_from_sidecars(
            azw3f, azw3r, title, author, _asin_from_info(azw3f), None))
    return books


def _build(cache, used):
    """Prefer the richer cc.db view (titles + read-status) when cc.db was
    synced; otherwise fall back to the sidecar-only view."""
    if os.path.exists(os.path.join(cache, "cc.db")):
        return build_from_ccdb(cache), used
    return build_from_usb(cache), used


def collect(cache, cfg, source, progress=lambda *a: None):
    """Sync + build. Returns (books, source_used). Never silently falls back to
    SSH: SSH only runs when explicitly selected, so a disconnected device gives
    a clear error instead of a hung/failed SSH attempt."""
    if source == "ssh":
        sync_ssh(cache, cfg, progress)
        return _build(cache, "ssh")

    # usb / auto: locate the Kindle's documents folder (off the GUI thread)
    docs = resolve_usb_documents(cfg)
    if not docs:
        raise RuntimeError(
            "No Kindle found. Connect the Kindle as a USB drive (or set its "
            "documents folder in Configure). To pull data over the network "
            "instead, set Source = ssh in Configure.")
    sync_usb(docs, cache, progress)
    return _build(cache, "usb")
