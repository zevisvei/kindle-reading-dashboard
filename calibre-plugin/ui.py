#!/usr/bin/env python
"""The toolbar action: Import reading stats / Open dashboard / Configure."""
import os
import shutil
import subprocess
import sys
import tempfile

from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.threaded_jobs import ThreadedJob

from calibre_plugins.kindle_reading.config import prefs

DBG_LOG = os.path.join(tempfile.gettempdir(), "kindle_reading_debug.log")
# Step tracing is off unless KINDLE_READING_DEBUG is set in the environment.
# Useful for diagnosing a freeze: each GUI-thread step is timestamped to DBG_LOG.
_DEBUG = bool(os.environ.get("KINDLE_READING_DEBUG"))


def _dbg(msg):
    if not _DEBUG:
        return
    try:
        import time
        with open(DBG_LOG, "a", encoding="utf-8") as f:
            f.write("%.3f  %s\n" % (time.time(), msg))
    except Exception:  # noqa
        pass


class KindleReadingAction(InterfaceAction):

    name = "Kindle Reading Dashboard"
    action_spec = ("Kindle Reading", "reader.png",
                   "Import Kindle reading metadata into custom columns", None)
    action_type = "current"

    def genesis(self):
        from qt.core import QMenu
        icon = get_icons("images/reader.png", "Kindle Reading")
        if icon is not None and not icon.isNull():
            self.qaction.setIcon(icon)
        m = self.qaction.menu()
        if m is None:
            m = QMenu(self.gui)
            self.qaction.setMenu(m)
        self.menu = m
        m.clear()
        m.addAction("Import reading stats (all books)",
                    lambda: self.import_stats())
        m.addAction("Refresh selected book(s)", self.refresh_selected)
        m.addAction("Show stats in Book Details (device + library)",
                    self.enable_book_details)
        m.addAction("Open web dashboard", self.open_dashboard)
        m.addSeparator()
        m.addAction("Configure…", self.show_config)
        # NB: triggered emits a `checked` bool — swallow it so it is not passed
        # as restrict_ids to import_stats.
        self.qaction.triggered.connect(lambda checked=False: self.import_stats())
        self._install_device_hook()
        _dbg("genesis done")

    # ---------------------------------------------- auto-import on connect
    def _install_device_hook(self):
        """Wrap the GUI's device_detected method so we can auto-import when a
        device connects (calibre exposes no signal for it)."""
        try:
            orig = self.gui.device_detected

            def wrapped(connected, device_kind, _orig=orig, _self=self):
                _orig(connected, device_kind)
                try:
                    if connected and prefs["auto_on_connect"]:
                        _dbg("device_detected: connected, auto-import scheduled")
                        from qt.core import QTimer
                        # delay so calibre finishes mounting the device
                        QTimer.singleShot(
                            4000, lambda: _self.import_stats(silent=True))
                except Exception as e:  # noqa
                    _dbg("device hook error %r" % e)

            self.gui.device_detected = wrapped
        except Exception as e:  # noqa
            _dbg("install_device_hook failed %r" % e)

    # ---------------------------------------------------------------- config
    def show_config(self):
        _dbg("show_config: enter")
        self.interface_action_base_plugin.do_user_config(self.gui)
        _dbg("show_config: dialog closed")

    # ------------------------------------------------- show in Book Details
    def enable_book_details(self):
        _dbg("enable_book_details: enter")
        """The device-book *grid* can't host custom columns (Calibre limitation),
        but the Book Details panel can — and it shows the matched library record
        for books that are on the device too. Add our columns to that panel so
        the reading stats are visible on the device page."""
        from calibre.gui2 import gprefs
        from calibre_plugins.kindle_reading.config import enabled_columns
        db = self.gui.current_db.new_api
        custom = set(db.field_metadata.custom_field_keys())
        wanted = [c for c in enabled_columns() if c in custom]
        if not wanted:
            return error_dialog(
                self.gui, "No columns",
                "Create and import the columns first (Configure → Create "
                "missing columns, then Import reading stats).", show=True)
        # Book Details reads gprefs['book_display_fields'] as a list of
        # (field, show?) tuples. When it is UNSET, calibre shows every field —
        # including custom columns — by default, so there is nothing to do.
        # Only when the user has customised the list do we ensure our columns
        # are present and turned on (and never drop the existing fields).
        fields = gprefs.get("book_display_fields", None)
        if not fields:
            return info_dialog(
                self.gui, "Already visible",
                "Book Details shows all custom columns by default, so the "
                "reading stats already appear there — for the selected book on "
                "both the library and the device page (device books that are "
                "matched to your library).\n\n"
                "Calibre does not allow custom columns as grid columns in the "
                "device list itself; the details panel is the supported place.",
                show=True)
        fields = [(f, bool(v)) for (f, v) in fields]
        have = {f for f, _ in fields}
        fields = [(f, True if f in wanted else v) for (f, v) in fields]
        for w in wanted:
            if w not in have:
                fields.append((w, True))
        gprefs["book_display_fields"] = fields
        try:
            self.gui.book_details.refresh()
        except Exception:  # noqa
            pass
        info_dialog(
            self.gui, "Book Details updated",
            "Reading columns enabled in the Book Details panel. They show for "
            "the selected book on both the library and the device page (for "
            "device books matched to your library).\n\n"
            "Note: Calibre does not allow custom columns as grid columns in the "
            "device list itself — the details panel is the supported place.",
            show=True)

    # --------------------------------------------------------- detect device
    def _device_prefixes(self):
        """Cheap attribute reads only (no filesystem IO) — the actual isdir
        probing happens in the worker thread so the UI can't freeze."""
        out = []
        try:
            dev = self.gui.device_manager.connected_device
            for attr in ("_main_prefix", "_card_a_prefix", "_card_b_prefix"):
                p = getattr(dev, attr, None) if dev else None
                if p:
                    out.append(p)
        except Exception:  # noqa
            pass
        return out

    # --------------------------------------------------------- refresh subset
    def refresh_selected(self):
        ids = []
        try:
            ids = list(self.gui.library_view.get_selected_ids())
        except Exception:  # noqa
            pass
        if not ids:
            return error_dialog(self.gui, "No selection",
                                "Select one or more books in the library first.",
                                show=True)
        self.import_stats(restrict_ids=set(ids))

    # --------------------------------------------------------- import stats
    def import_stats(self, restrict_ids=None, silent=False):
        _dbg("import_stats: enter (restrict=%s silent=%s)"
             % (None if restrict_ids is None else len(restrict_ids), silent))
        cfg = {k: prefs[k] for k in (
            "source", "ssh_host", "ssh_user", "ssh_password", "ssh_key")}
        cfg["usb_pref"] = prefs["usb_documents"]
        cfg["dev_prefixes"] = self._device_prefixes()
        source = prefs["source"]
        _dbg("import_stats: source=%s dev_prefixes=%r" % (source, cfg["dev_prefixes"]))
        job = ThreadedJob(
            "kindle_reading_import",
            "Importing Kindle reading metadata",
            self._do_import, (cfg, source), {},
            self._import_done)
        # stash per-run options for the callback (callback only gets the job)
        job.kr_restrict = restrict_ids
        job.kr_silent = bool(silent)
        _dbg("import_stats: submitting job")
        self.gui.job_manager.run_threaded_job(job)
        _dbg("import_stats: job submitted (returning to event loop)")
        self.gui.status_bar.show_message("Importing Kindle reading metadata…", 3000)

    def _do_import(self, cfg, source, notifications=None, abort=None, log=None):
        """Runs in a worker thread: sync + decode. Returns the book list."""
        _dbg("_do_import: worker start (source=%s)" % source)
        from calibre_plugins.kindle_reading import engine
        cache = tempfile.mkdtemp(prefix="kindle_reading_")

        def progress(i, n):
            if notifications is not None and n:
                notifications.put((i / float(n), "synced %d/%d" % (i, n)))

        try:
            books, used = engine.collect(cache, cfg, source, progress)
        finally:
            shutil.rmtree(cache, ignore_errors=True)
        _dbg("_do_import: worker done (%d books, via %s)" % (len(books), used))
        return {"books": books, "source": used}

    def _import_done(self, job):
        _dbg("_import_done: callback start (failed=%s)" % job.failed)
        silent = bool(getattr(job, "kr_silent", False))
        restrict = getattr(job, "kr_restrict", None)
        try:
            if job.failed:
                msg = str(getattr(job, "exception", "") or "Import failed.")
                det = getattr(job, "traceback", "") or ""
                _dbg("_import_done: failed: %s" % msg[:80])
                # auto-on-connect for a non-Kindle device shouldn't nag
                if not silent:
                    error_dialog(self.gui, "Kindle reading import",
                                 msg, det_msg=det, show=True)
                else:
                    self.gui.status_bar.show_message("Kindle import: " + msg, 6000)
                return
            result = job.result or {}
            books = [b for b in result.get("books", []) if b.get("has_data")]
            used = result.get("source", "?")
            matched, updated = self._write_columns(books, restrict_ids=restrict)
            _dbg("_import_done: wrote columns matched=%d updated=%d"
                 % (matched, updated))
            # Non-blocking: a modal dialog from the job callback can wedge the
            # event loop. Report via the status bar instead.
            self.gui.status_bar.show_message(
                "Kindle import: %d books, matched %d, updated %d values (source: %s)"
                % (len(books), matched, updated, used), 10000)
            _dbg("_import_done: status message shown (done)")
        except Exception as e:  # noqa
            import traceback
            _dbg("_import_done: EXCEPTION %r\n%s" % (e, traceback.format_exc()))

    # ------------------------------------------------------- write to library
    def _write_columns(self, books, restrict_ids=None):
        from calibre_plugins.kindle_reading.config import enabled_columns
        db = self.gui.current_db.new_api
        custom = set(db.field_metadata.custom_field_keys())
        # {lookup: field_key} for enabled fields whose column actually exists
        colmap = {col: key for col, key in enabled_columns().items()
                  if col in custom}

        if not colmap:
            if not getattr(self, "_silent_write", False):
                error_dialog(self.gui, "No columns",
                             "No enabled custom columns exist yet. Use "
                             "Configure → tick fields → Create missing columns.",
                             show=True)
            return 0, 0

        # --- build match indexes over the Calibre library ---
        by_asin, by_title = {}, {}
        for bid in db.all_book_ids():
            ids = db.field_for("identifiers", bid) or {}
            for k, v in ids.items():
                if k in ("amazon", "mobi-asin") or k.startswith("amazon_"):
                    by_asin[str(v)] = bid
            t = db.field_for("title", bid) or ""
            by_title.setdefault(_norm(t), bid)

        match_title = bool(prefs["match_by_title"])
        # accumulate per-column {book_id: value}
        changes = {lookup: {} for lookup in colmap}
        matched = 0
        for b in books:
            bid = None
            if b.get("asin") and str(b["asin"]) in by_asin:
                bid = by_asin[str(b["asin"])]
            elif match_title and b.get("norm_title") in by_title:
                bid = by_title[b["norm_title"]]
            if bid is None:
                continue
            if restrict_ids is not None and bid not in restrict_ids:
                continue
            matched += 1
            for lookup, field in colmap.items():
                val = b.get(field)
                if val is not None:
                    changes[lookup][bid] = val

        updated = 0
        changed_ids = set()
        for lookup, mp in changes.items():
            if not mp:
                continue
            try:
                db.set_field(lookup, mp)
                updated += len(mp)
                changed_ids.update(mp)
            except Exception as e:  # noqa  (e.g. a datetime value calibre rejects)
                _dbg("_write_columns: set_field %s failed %r" % (lookup, e))
        # Targeted refresh of only the changed rows. A full model().refresh()
        # here rebuilds the entire library view and, with a device connected,
        # re-runs on-device matching — slow enough to look like a freeze.
        if changed_ids:
            _dbg("_write_columns: before refresh_ids (%d)" % len(changed_ids))
            try:
                self.gui.library_view.model().refresh_ids(list(changed_ids))
            except Exception as e:  # noqa
                _dbg("_write_columns: refresh_ids EXC %r" % e)
            _dbg("_write_columns: after refresh_ids")
        return matched, updated

    # --------------------------------------------------------- web dashboard
    def open_dashboard(self):
        _dbg("open_dashboard: enter")
        path = prefs["dashboard_py"]
        if not path or not os.path.exists(path):
            return error_dialog(
                self.gui, "dashboard.py not set",
                "Set the path to reader-dashboard/dashboard.py in Configure.",
                show=True)
        py = _find_python()
        if not py:
            return error_dialog(self.gui, "Python not found",
                                "Could not find a system python to run the "
                                "dashboard. Install Python 3.", show=True)
        try:
            from calibre_plugins.kindle_reading.engine import _no_window_kwargs
            cwd = os.path.dirname(os.path.abspath(path))
            subprocess.Popen([py, path, "serve"], cwd=cwd, **_no_window_kwargs())
            self.gui.status_bar.show_message(
                "Launching dashboard… a browser tab will open.", 5000)
        except Exception as e:  # noqa
            error_dialog(self.gui, "Launch failed", str(e), show=True)


def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _find_python():
    for c in ("python3", "python"):
        if shutil.which(c):
            return shutil.which(c)
    # last resort: calibre's own interpreter (works if paramiko available there)
    return sys.executable if not sys.executable.lower().endswith("calibre.exe") else None
