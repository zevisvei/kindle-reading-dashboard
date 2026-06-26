#!/usr/bin/env python
"""
Kindle Reading Dashboard — Calibre plugin.

Pulls the reading metadata a Kindle stores on-device (reading time, pace/WPM,
percent progress, furthest page, words read, read/unread status) and writes it
into Calibre custom columns. Also launches the standalone web dashboard.

Data source is auto-detected: the connected USB Kindle's `documents/` folder
(reading stats from the `.azw3f`/`.azw3r` sidecars), falling back to SSH
(usbnet) which additionally pulls `cc.db` + `fmcache.db` for read-state.
"""
from calibre.customize import InterfaceActionBase

__license__ = "MIT"
__copyright__ = "zevisvei"


class KindleReadingPlugin(InterfaceActionBase):
    name = "Kindle Reading Dashboard"
    description = ("Import Kindle on-device reading metadata (time, WPM, "
                   "progress, furthest page, read-status) into custom columns, "
                   "and launch the web dashboard.")
    supported_platforms = ["windows", "osx", "linux"]
    author = "zevisvei"
    version = (1, 0, 0)
    minimum_calibre_version = (5, 0, 0)

    # the InterfaceAction lives in ui.py
    actual_plugin = "calibre_plugins.kindle_reading.ui:KindleReadingAction"

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.kindle_reading.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
