#!/usr/bin/env python
"""Persistent settings + the configuration widget."""
from calibre.utils.config import JSONConfig

try:
    from qt.core import (QWidget, QVBoxLayout, QFormLayout, QLineEdit, QComboBox,
                         QLabel, QPushButton, QGroupBox, QGridLayout, QCheckBox,
                         QScrollArea)
except ImportError:  # very old calibre
    from PyQt5.Qt import (QWidget, QVBoxLayout, QFormLayout, QLineEdit, QComboBox,
                          QLabel, QPushButton, QGroupBox, QGridLayout, QCheckBox,
                          QScrollArea)

from calibre_plugins.kindle_reading.fields import (
    FIELDS, FIELD_LABEL, FIELD_DT, DEFAULT_COL, DEFAULT_ON)

# stored under the calibre config dir as plugins/kindle_reading.json
prefs = JSONConfig("plugins/kindle_reading")

prefs.defaults["source"] = "auto"          # auto | usb | ssh
prefs.defaults["usb_documents"] = ""        # blank => auto from connected device
prefs.defaults["ssh_host"] = "192.168.15.244"
prefs.defaults["ssh_user"] = "root"
prefs.defaults["ssh_password"] = "kindle"
prefs.defaults["ssh_key"] = ""              # private-key path (optional)
prefs.defaults["dashboard_py"] = ""         # path to reader-dashboard/dashboard.py
prefs.defaults["match_by_title"] = True
prefs.defaults["auto_on_connect"] = False   # import automatically when device connects
# per-field enable + column lookup name, keyed by field key
prefs.defaults["field_on"] = dict(DEFAULT_ON)
prefs.defaults["field_col"] = dict(DEFAULT_COL)


def field_on(key):
    return bool(prefs["field_on"].get(key, DEFAULT_ON[key]))


def field_col(key):
    return prefs["field_col"].get(key, DEFAULT_COL[key])


def enabled_columns():
    """{lookup_name: field_key} for every enabled field with a '#' lookup."""
    out = {}
    for key, *_ in FIELDS:
        col = field_col(key)
        if field_on(key) and col.startswith("#"):
            out[col] = key
    return out


class ConfigWidget(QWidget):

    def __init__(self):
        QWidget.__init__(self)
        v = QVBoxLayout(self)

        # ---- source ----
        src_box = QGroupBox("Data source")
        sf = QFormLayout(src_box)
        self.source = QComboBox()
        self.source.addItems(["auto", "usb", "ssh"])
        self.source.setCurrentText(prefs["source"])
        sf.addRow("Source", self.source)
        self.usb_documents = QLineEdit(prefs["usb_documents"])
        self.usb_documents.setPlaceholderText("blank = auto-detect connected Kindle")
        sf.addRow("USB documents folder", self.usb_documents)
        v.addWidget(src_box)

        # ---- ssh ----
        ssh_box = QGroupBox("SSH (usbnet) — only used when Source = ssh")
        ef = QFormLayout(ssh_box)
        self.ssh_host = QLineEdit(prefs["ssh_host"])
        self.ssh_user = QLineEdit(prefs["ssh_user"])
        self.ssh_password = QLineEdit(prefs["ssh_password"])
        self.ssh_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssh_key = QLineEdit(prefs["ssh_key"])
        self.ssh_key.setPlaceholderText("optional private-key path")
        ef.addRow("Host", self.ssh_host)
        ef.addRow("User", self.ssh_user)
        ef.addRow("Password", self.ssh_password)
        ef.addRow("Key file", self.ssh_key)
        v.addWidget(ssh_box)

        # ---- fields (checklist) ----
        col_box = QGroupBox("Fields to import (tick = add as a custom column)")
        outer = QVBoxLayout(col_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.addWidget(QLabel("<b>Field</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Type</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Column lookup name</b>"), 0, 2)
        self.field_checks = {}
        self.field_edits = {}
        for i, (key, label, dt, _dc, _on) in enumerate(FIELDS, start=1):
            cb = QCheckBox(label)
            cb.setChecked(field_on(key))
            e = QLineEdit(field_col(key))
            self.field_checks[key] = cb
            self.field_edits[key] = e
            grid.addWidget(cb, i, 0)
            grid.addWidget(QLabel(dt), i, 1)
            grid.addWidget(e, i, 2)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self.match_by_title = QCheckBox("Match books by title when ASIN is unavailable")
        self.match_by_title.setChecked(bool(prefs["match_by_title"]))
        outer.addWidget(self.match_by_title)
        self.auto_on_connect = QCheckBox("Import automatically when the Kindle connects")
        self.auto_on_connect.setChecked(bool(prefs["auto_on_connect"]))
        outer.addWidget(self.auto_on_connect)
        create = QPushButton("Create missing columns…")
        create.clicked.connect(self._create_columns)
        outer.addWidget(create)
        v.addWidget(col_box)

        # ---- dashboard ----
        d_box = QGroupBox("Web dashboard")
        df = QFormLayout(d_box)
        self.dashboard_py = QLineEdit(prefs["dashboard_py"])
        self.dashboard_py.setPlaceholderText("path to reader-dashboard/dashboard.py")
        df.addRow("dashboard.py", self.dashboard_py)
        v.addWidget(d_box)

        v.addWidget(QLabel("Changes to columns require a Calibre restart."))

    def _create_columns(self):
        from calibre.gui2.ui import get_gui
        from calibre.gui2 import error_dialog, info_dialog
        gui = get_gui()
        if gui is None:
            return
        db = gui.library_view.model().db
        existing = db.field_metadata.custom_field_keys()
        created = []
        for key, label, dt, _dc, _on in FIELDS:
            if not self.field_checks[key].isChecked():
                continue
            lookup = self.field_edits[key].text().strip()
            if not lookup.startswith("#") or lookup in existing:
                continue
            try:
                db.create_custom_column(lookup[1:], "Kindle " + label, dt, False)
                created.append(lookup)
            except Exception as e:  # noqa
                error_dialog(self, "Column error",
                             "Could not create %s: %s" % (lookup, e), show=True)
                return
        if created:
            info_dialog(self, "Columns created",
                        "Created: %s\n\nRestart Calibre for them to appear."
                        % ", ".join(created), show=True)
        else:
            info_dialog(self, "Nothing to do",
                        "All ticked columns already exist (or none ticked).",
                        show=True)

    def save_settings(self):
        prefs["source"] = self.source.currentText()
        prefs["usb_documents"] = self.usb_documents.text().strip()
        prefs["ssh_host"] = self.ssh_host.text().strip()
        prefs["ssh_user"] = self.ssh_user.text().strip()
        prefs["ssh_password"] = self.ssh_password.text()
        prefs["ssh_key"] = self.ssh_key.text().strip()
        prefs["dashboard_py"] = self.dashboard_py.text().strip()
        prefs["match_by_title"] = bool(self.match_by_title.isChecked())
        prefs["auto_on_connect"] = bool(self.auto_on_connect.isChecked())
        on, cols = {}, {}
        for key, *_ in FIELDS:
            on[key] = bool(self.field_checks[key].isChecked())
            val = self.field_edits[key].text().strip()
            cols[key] = val or DEFAULT_COL[key]
        prefs["field_on"] = on
        prefs["field_col"] = cols
