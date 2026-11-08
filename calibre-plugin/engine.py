#!/usr/bin/env python
"""
Data layer: pull Kindle on-device reading metadata (USB or SSH), decode the
KRDS sidecars — AZW3 `.azw3f`/`.azw3r`, KFX `.yjf`/`.yjr`, MOBI `.mbs`/`.mbp1`
(+ cc.db / fmcache.db over SSH) — and return a normalised per-book list ready
to map onto Calibre books.

Self-contained: only the Python standard library + the bundled `krds` parser.
SSH uses paramiko when importable (Configure can install it into calibre's
config folder, see sshdeps.py), otherwise shells out to the system `ssh`.
"""
import datetime
import glob
import json
import logging
import os
import posixpath
import re
import shutil
import sqlite3
import subprocess
import sys

from calibre_plugins.kindle_reading import krds, sshdeps

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

# KRDS sidecar extensions, by container format: AZW3, KFX, legacy MOBI.
# METRICS_* hold timer.model / book.info.store, READER_* hold annotations,
# font.prefs and apnx.key.
METRICS_EXTS = (".azw3f", ".yjf", ".mbs")
READER_EXTS = (".azw3r", ".yjr", ".mbp1")
SIDECAR_EXTS = METRICS_EXTS + READER_EXTS


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
    out["book_words"] = _book_words(bi)
    return out


def _book_words(bi):
    """Estimated words in the whole book. book.info.store only holds the words
    the Kindle has counted so far (numberOfWords) and the fraction of the book
    they cover (percentOfBook), so scale up; 0 when nothing is counted yet."""
    if not isinstance(bi, dict):
        return 0
    n = bi.get("numberOfWords") or 0
    p = bi.get("percentOfBook") or 0.0
    return int(round(n / p)) if n and p > 0 else 0


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
    """{read_state: {asin: READ/UNREAD}, sessions: {asin: [start ms, ...]}}"""
    out = {"read_state": {}, "sessions": {}}
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
    # device reading sessions (the same table the dashboard shows)
    try:
        for (sess,) in cur.execute("SELECT session FROM reading_sessions"):
            p = json.loads(sess).get("payload", {})
            if p.get("asin") and p.get("start_timestamp"):
                out["sessions"].setdefault(p["asin"], []).append(p["start_timestamp"])
    except (sqlite3.Error, ValueError, AttributeError):
        pass
    con.close()
    return out


def load_series(cur):
    """{cdeKey: (series name, index)} from cc.db's Series table. The index is
    the position label when it is a number ("2"), else position + 1."""
    names = {}
    try:
        for key, group, title in cur.execute(
                "SELECT p_cdeKey, p_cdeGroup, p_titles_0_nominal FROM Entries "
                "WHERE p_type='Entry:Item:Series'"):
            for k in (key, group):
                if k:
                    names[k] = title
    except sqlite3.Error:
        return {}
    out = {}
    try:
        for item, sid, pos, label in cur.execute(
                "SELECT d_itemCdeKey, d_seriesId, d_itemPosition, "
                "d_itemPositionLabel FROM Series"):
            name = names.get(sid)
            if not (item and name):
                continue
            try:
                idx = float(label)
            except (TypeError, ValueError):
                idx = float(pos) + 1 if pos is not None else 1.0
            out[item] = (name, idx)
    except sqlite3.Error:
        pass
    return out


# --------------------------------------------------------------------------- #
#  SSH transport: paramiko if available, else the system `ssh` binary
# --------------------------------------------------------------------------- #
class SSH(object):
    def __init__(self, host, user, password, key):
        self.host, self.user, self.password, self.key = host, user, password, key
        self._c = None
        self._mode = None
        sshdeps.activate()
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
        find_expr = " -o ".join("-name '*%s'" % ext for ext in SIDECAR_EXTS)
        cmd = "find '%s' \\( %s \\)" % (REMOTE_DOCS, find_expr)
        data = self.run_bytes(cmd)
        return [l for l in data.decode("utf-8", "replace").splitlines()
                if l.strip()]

    def find_book_files(self):
        """Every file under documents/ outside the .sdr folders."""
        data = self.run_bytes("find '%s' -type f ! -path '*.sdr/*'" % REMOTE_DOCS)
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


def usbnet_hint(host):
    """Windows often gives the Kindle's USB network (RNDIS) adapter an automatic
    169.254.x address instead of one in the Kindle's subnet, so the Kindle is
    unreachable. Detect that and return how to fix it ('' when not the case)."""
    import socket
    if os.name != "nt" or host.count(".") != 3:
        return ""
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        return ""
    prefix = host.rsplit(".", 1)[0] + "."
    if any(a.startswith(prefix) for a in addrs) or             not any(a.startswith("169.254.") for a in addrs):
        return ""
    idx = ""
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetAdapter | ? { $_.InterfaceDescription -like '*RNDIS*' "
             "-and $_.Status -eq 'Up' } | select -First 1).ifIndex"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
            **_no_window_kwargs())
        idx = p.stdout.decode("ascii", "ignore").strip()
    except Exception:  # noqa
        pass
    pc_ip = prefix + ("1" if host.endswith(".201") else "201")
    cmd = "New-NetIPAddress -InterfaceIndex %s -IPAddress %s -PrefixLength 24" % (
        idx if idx.isdigit() else "<index>", pc_ip)
    msg = ("\n\nThe Kindle's USB network adapter has no address in %s0/24 "
           "(Windows gave it an automatic 169.254.x address). Fix it once, in "
           "PowerShell run as Administrator:\n\n    %s" % (prefix, cmd))
    if not idx.isdigit():
        msg += ("\n\n(<index> = the ifIndex of the \"Kindle USB RNDIS\" adapter "
                "in the output of Get-NetAdapter.)")
    return msg


def sync_ssh(cache, cfg, progress=lambda *a: None):
    if not ssh_reachable(cfg["ssh_host"]):
        raise RuntimeError(
            "SSH host %s:22 is not reachable. If the Kindle is plugged in as a "
            "USB drive, use the USB source instead (usbnet/SSH needs the device "
            "configured for network, not mass-storage)." % cfg["ssh_host"]
            + usbnet_hint(cfg["ssh_host"]))
    docs = os.path.join(cache, "documents")
    os.makedirs(docs, exist_ok=True)
    ssh = SSH(cfg["ssh_host"], cfg["ssh_user"], cfg["ssh_password"], cfg["ssh_key"])
    if ssh._mode == "binary" and not cfg["ssh_key"]:
        # the system ssh runs with BatchMode (no prompts), so it cannot log in
        # with a password
        raise RuntimeError(
            "SSH with a password needs SSH support. Open Configure and click "
            "\"Install SSH support…\" (one-time download, no Python install "
            "needed), or set a key file.")
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
        books = [posixpath.relpath(p, REMOTE_DOCS) for p in ssh.find_book_files()]
        save_book_list(cache, books)
        files = [REMOTE_DOCS + "/" + r for r in drop_orphans(
            [posixpath.relpath(p, REMOTE_DOCS) for p in files], books,
            keep_all=cfg.get("include_deleted"))]
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
    if not db and not files:
        raise RuntimeError(
            "Reached %s over SSH but read nothing (no cc.db, no reading files). "
            "Check the SSH user and password in Configure." % cfg["ssh_host"])


def save_book_list(cache, book_files):
    """The book files on the device (relative to documents/), for the
    dashboard: it marks books whose .sdr folder is left over as deleted.
    Same file name and format as dashboard.py's save_book_list."""
    if not book_files:
        return
    with open(os.path.join(cache, "book_files.txt"), "w", encoding="utf-8") as f:
        f.write(chr(10).join(b.replace(os.sep, "/") for b in book_files))


def drop_orphans(sidecars, book_files, keep_all=False):
    """Keep only sidecars whose book is still on the device. The Kindle keeps
    a book's "<book minus extension>.sdr/" folder after the book is deleted,
    so without this the dashboard lists books that are gone. Paths are
    relative to documents/ with '/' separators. With no book list (listing
    failed) or keep_all (Configure: include deleted books) nothing is dropped."""
    if keep_all or not book_files:
        return list(sidecars)
    bases = set()
    for f in book_files:
        f = f.replace("\\", "/")
        bases.add(f)                          # "KOReader.sh" -> "KOReader.sh.sdr"
        bases.add(posixpath.splitext(f)[0])   # "x.azw3" -> "x.sdr"
    out = []
    for s in sidecars:
        s2 = s.replace("\\", "/")
        sdr = s2.split(".sdr/", 1)[0] if ".sdr/" in s2 else None
        if sdr is None or sdr in bases:
            out.append(s)
    return out


def sync_usb(documents, cache, progress=lambda *a: None, keep_all=False):
    docs = os.path.join(cache, "documents")
    os.makedirs(docs, exist_ok=True)
    srcs = []
    for pat in ["**/*" + ext for ext in SIDECAR_EXTS]:
        srcs += glob.glob(os.path.join(documents, pat), recursive=True)
    books = []
    for dirpath, dirs, names in os.walk(documents):
        dirs[:] = [d for d in dirs if not d.endswith(".sdr")]
        books += [os.path.relpath(os.path.join(dirpath, n), documents) for n in names]
    save_book_list(cache, books)
    srcs = [os.path.join(documents, r) for r in drop_orphans(
        [os.path.relpath(s, documents) for s in srcs], books, keep_all)]
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


def is_mtp_kindle(dev):
    """True for a Kindle that Calibre talks to over MTP (Scribe 3, Colorsoft,
    2024+ Paperwhite, ...). Those get no drive letter / mount point, so the
    USB path cannot see them. Needs Calibre 7.25+ for the by-name MTP calls
    (Calibre's own file cache deliberately skips the .sdr folders on Kindles)."""
    try:
        return (dev is not None and bool(getattr(dev, "is_kindle", False))
                and hasattr(dev, "list_folder_by_name")
                and hasattr(dev, "get_file_by_name"))
    except Exception:  # noqa
        return False


def _mtp_list(dev, storage, names):
    """(name, is_folder) for each entry of storage/names on an MTP device.
    Uses the driver's raw listing when available: Calibre's list_folder_by_name
    reads x['modified'] unconditionally, and some Kindles (e.g. Scribe 3)
    report files without a modification date -> KeyError: 'modified'."""
    raw = getattr(dev, "list_mtp_folder_by_name", None)
    if raw is not None:
        return [(x["name"], bool(x.get("is_folder")))
                for x in raw(storage, *names)]
    return [(e.name, e.is_folder)
            for e in dev.list_folder_by_name(storage, *names)]


def sync_mtp(dev, cache, keep_all=False):
    """Copy every KRDS sidecar under documents/ from an MTP Kindle into
    <cache>/documents, mirroring the device layout (same shape sync_usb
    produces). MUST run on Calibre's device thread (a device-manager job):
    the MTP driver refuses calls from any other thread."""
    docs = os.path.join(cache, "documents")
    os.makedirs(docs, exist_ok=True)
    fsc = dev.filesystem_cache
    storage = fsc.storage(getattr(dev, "_main_id", None)) or fsc.entries[0]

    found, books = [], []

    def walk(names):
        for name, is_folder in _mtp_list(dev, storage, names):
            if is_folder:
                walk(names + (name,))
            elif name.endswith(SIDECAR_EXTS):
                found.append(names + (name,))
            elif not any(n.endswith(".sdr") for n in names):
                books.append("/".join(names[1:] + (name,)))

    walk(("documents",))
    save_book_list(cache, books)
    keep = set(drop_orphans(["/".join(n[1:]) for n in found], books, keep_all))
    found = [n for n in found if "/".join(n[1:]) in keep]
    for names in found:
        local = os.path.join(docs, *names[1:])
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "wb") as f:
            dev.get_file_by_name(f, storage, *names)
    return len(found)


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


def _progress(stats, pg, tot, cc_percent):
    """How far into the book (0-100). timer.model's totalPercent is the share
    read in total, so re-reading pushes it past 100%. Order: cc.db
    p_percentFinished (the device's own value), else furthest page / total
    pages, else totalPercent capped at 100."""
    if cc_percent is not None:
        try:
            return min(max(float(cc_percent), 0.0), 100.0)
        except (TypeError, ValueError):
            pass
    if pg is not None and tot:
        return min(100.0, 100.0 * pg / tot)
    p = stats.get("percent_read")
    return min(100.0, p) if p else None


def device_key(path):
    """Key that links a book file and its .sdr sidecar folder on the device:
    the path from the device root without extension / .sdr, lower case
    ("documents/foo/title - author"). Matches calibre's Book.lpath."""
    p = path.replace("\\", "/").strip("/")
    if p.lower().endswith(".sdr"):
        p = p[:-4]
    else:
        p = p.rsplit(".", 1)[0] if "." in p.rsplit("/", 1)[-1] else p
    return p.lower()


def _first_read(sessions, azw3f):
    """Earliest reading session on the device (fmcache), else the earliest
    entry of the book's page history."""
    times = [t for t in (sessions or []) if t]
    ph = azw3f.get("page.history.store")
    if isinstance(ph, list):
        for e in ph:
            if isinstance(e, dict) and e.get("time"):
                times.append(e["time"])
    dts = [d for d in (_to_datetime(t) for t in times) if d]
    return min(dts) if dts else None


def annotations_raw(azw3r):
    """annotation.cache.object flattened to [{type, startPosition, ...}]
    (the same shape as azw3text/extract_highlights.annotations_from_azw3r)."""
    out = []
    aco = (azw3r or {}).get("annotation.cache.object")
    if isinstance(aco, dict):
        for cls, items in aco.items():
            if isinstance(items, list):
                out += [dict(it, type=cls.split(".")[-1]) for it in items
                        if isinstance(it, dict)]
    return out


def _book_from_sidecars(azw3f, azw3r, title, author, asin, read_status,
                        cc_last_access=None, cc_percent=None, sdr_key=None,
                        series=None, sessions=None, sdr_dir=None):
    stats = _stats_from_timer(azw3f.get("timer.model"), azw3f.get("book.info.store"))
    pos = _furthest_position(azw3f)
    pg = _page_of(azw3r, pos)
    tot = _total_pages(azw3r)
    page_str = ("%d/%d" % (pg, tot)) if (pg is not None and tot) else (
        str(pg) if pg is not None else None)
    pct = _progress(stats, pg, tot, cc_percent)
    return {
        "title": title,
        "author": author,
        "asin": asin,
        "norm_title": _norm(title),
        "device_key": sdr_key,           # links to calibre's device matching (lpath)
        "hours": stats.get("hours"),
        "minutes": int(round(stats["minutes"])) if stats.get("minutes") else None,
        "wpm": stats.get("wpm_reading"),
        "wpm_overall": stats.get("wpm_overall"),
        "progress": int(round(pct)) if pct is not None else None,
        "page": page_str,
        "words": stats.get("words_read"),
        "book_words": stats.get("book_words") or None,
        "annotations": _annotations_count(azw3r),
        # no mark on the device (no cc.db / fmcache): opened = reading
        "status": read_status or ("reading" if pct else "unread"),
        "last_read": _last_read(azw3f, cc_last_access),
        "first_read": _first_read(sessions, azw3f),
        "sessions": len(sessions) if sessions else None,
        "series": series,                # (name, index) from cc.db, or None
        "annots_raw": annotations_raw(azw3r),
        "sdr_dir": sdr_dir,              # cache folder, for highlight text
        "has_data": bool(azw3f.get("timer.model") or azw3r),
    }


def build_from_ccdb(cache):
    """cc.db present → reliable title/author/asin + read-status. Books whose
    sidecars are on the device but not in this cc.db (e.g. an older copy set
    in Configure) are added from the sidecars alone."""
    db_path = os.path.join(cache, "cc.db")
    fm = load_fmcache(os.path.join(cache, "fmcache.db"))
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cols = [d[1] for d in cur.execute("PRAGMA table_info(Entries)")]
    rows = cur.execute("SELECT * FROM Entries WHERE p_type='Entry:Item'").fetchall()
    series = load_series(cur)
    books, used_sdr = [], set()
    # every location cc.db knows, of any type (dictionaries, My Clippings):
    # their .sdr folders must not come back as sidecar-only books
    for (loc,) in cur.execute("SELECT p_location FROM Entries "
                              "WHERE p_location LIKE '/mnt/us/%'"):
        base = os.path.splitext(loc[len("/mnt/us/"):])[0]
        used_sdr.add(os.path.normcase(os.path.normpath(
            os.path.join(cache, *(base + ".sdr").split("/")))))
    for row in rows:
        r = {k: row[k] for k in cols}
        loc = r.get("p_location") or ""
        azw3f, azw3r, sdr = {}, {}, None
        key = device_key(loc[len("/mnt/us/"):]) if loc.startswith("/mnt/us/") else None
        if loc.startswith("/mnt/us/"):
            base, _ = os.path.splitext(loc[len("/mnt/us/"):])
            sdr = os.path.join(cache, *(base + ".sdr").split("/"))
            used_sdr.add(os.path.normcase(os.path.normpath(sdr)))
            if os.path.isdir(sdr):
                for f in os.listdir(sdr):
                    p = os.path.join(sdr, f)
                    if f.endswith(METRICS_EXTS):
                        azw3f = decode_sidecar(p)
                    elif f.endswith(READER_EXTS):
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
            cde, status, cc_last_access=r.get("p_lastAccess"),
            cc_percent=r.get("p_percentFinished"), sdr_key=key,
            series=series.get(cde), sessions=fm["sessions"].get(cde), sdr_dir=sdr))
    con.close()
    return books + build_from_usb(cache, skip=used_sdr)


def build_from_usb(cache, skip=()):
    """No cc.db → walk sidecars, derive title from folder, asin from
    book.info.store when present. Read status only when an fmcache.db is
    available (synced, or a copy set in Configure)."""
    docs = os.path.join(cache, "documents")
    fm = load_fmcache(os.path.join(cache, "fmcache.db"))
    fm_rs = fm["read_state"]
    books = []
    for dirpath, _dirs, files in os.walk(docs):
        f3 = [f for f in files if f.endswith(METRICS_EXTS)]
        if not f3 or os.path.normcase(os.path.normpath(dirpath)) in skip:
            continue
        azw3f = decode_sidecar(os.path.join(dirpath, f3[0]))
        r_files = [f for f in files if f.endswith(READER_EXTS)]
        azw3r = decode_sidecar(os.path.join(dirpath, r_files[0])) if r_files else {}
        # folder name: "<title> - <author>.sdr"
        folder = os.path.basename(dirpath)
        if folder.endswith(".sdr"):
            folder = folder[:-4]
        asin = _asin_from_info(azw3f)
        # Store / Send-to-Kindle books on newer firmware live in
        # documents/Downloads/Items01/"<title>_<ASIN or 32-char doc id>.sdr"
        m = _ITEMS_SUFFIX.match(folder)
        if m and os.path.basename(os.path.dirname(dirpath)).startswith("Items"):
            folder = m.group(1)
            if not asin and len(m.group(2)) == 10:
                asin = m.group(2)
        title, author = folder, ""
        if " - " in folder:
            title, author = folder.rsplit(" - ", 1)
        status = {"READ": "read", "UNREAD": "unread"}.get(fm_rs.get(asin))
        books.append(_book_from_sidecars(
            azw3f, azw3r, title, author, asin, status,
            sdr_key=device_key(os.path.relpath(dirpath, cache)),
            sessions=fm["sessions"].get(asin), sdr_dir=dirpath))
    return books


_ITEMS_SUFFIX = re.compile(r"^(.+)_([A-Z0-9]{10}|[A-Z2-7]{32})$")


def _build(cache, used):
    """Prefer the richer cc.db view (titles + read-status) when cc.db was
    synced; otherwise fall back to the sidecar-only view."""
    if os.path.exists(os.path.join(cache, "cc.db")):
        return build_from_ccdb(cache), used
    return build_from_usb(cache), used


NO_KINDLE_MSG = (
    "No Kindle found. Connect the Kindle as a USB drive (or set its documents "
    "folder in Configure). Newer Kindles (Scribe 3, Colorsoft, 2024+ "
    "Paperwhite) connect over MTP instead: those are read through Calibre's own "
    "device connection, which needs Calibre 7.25 or later. A jailbroken Kindle "
    "on USB networking is used automatically when its SSH host (Configure) "
    "answers.")


def sync(cache, cfg, source, progress=lambda *a: None):
    """Pull the device's data into `cache` (documents/ + cc.db/fmcache.db when
    the source has them). Returns the source used. `auto` prefers USB / MTP
    and uses SSH only when no USB Kindle is found and the SSH port answers a
    quick TCP probe, so a disconnected device gives a clear error instead of a
    hung SSH attempt."""
    if source == "ssh":
        sync_ssh(cache, cfg, progress)
        return "ssh"
    if cfg.get("mtp_synced"):
        # sidecars were already pulled into `cache` by sync_mtp (device thread)
        return "mtp"
    # usb / auto: locate the Kindle's documents folder (off the GUI thread)
    docs = resolve_usb_documents(cfg)
    if not docs and source == "auto" and cfg.get("ssh_host") \
            and ssh_reachable(cfg["ssh_host"]):
        sync_ssh(cache, cfg, progress)
        return "ssh"
    if not docs:
        raise RuntimeError(NO_KINDLE_MSG)
    sync_usb(docs, cache, progress, keep_all=cfg.get("include_deleted"))
    return "usb"


def apply_db_files(cache, cfg):
    """Copies of cc.db / fmcache.db set in Configure (e.g. pulled earlier over
    SSH or copied from a backup) take the place of the synced ones. Read only:
    the user's files are copied, never modified."""
    for key, name in (("ccdb_path", "cc.db"), ("fmcache_path", "fmcache.db")):
        src = cfg.get(key) or ""
        if src:
            if not os.path.isfile(src):
                raise RuntimeError("%s set in Configure does not exist: %s" % (name, src))
            shutil.copyfile(src, os.path.join(cache, name))


def collect(cache, cfg, source, progress=lambda *a: None):
    """Sync + build. Returns (books, source_used)."""
    used = sync(cache, cfg, source, progress)
    apply_db_files(cache, cfg)
    return _build(cache, used)
