#!/usr/bin/env python
"""Single source of truth for the reading-data fields the plugin can import.

Each field maps a key in the per-book record (produced by engine) to a Calibre
custom column. config.py renders one checkbox + column-name row per field;
ui.py creates/writes only the enabled ones; engine.py fills every key.
"""

# key, label, calibre datatype, default lookup name, on-by-default
FIELDS = [
    ("hours",       "Reading hours",        "float",    "#kreadtime",   True),
    ("minutes",     "Reading minutes",      "int",      "#kreadmin",    False),
    ("wpm",         "Reading pace (WPM)",   "int",      "#kwpm",        True),
    ("wpm_overall", "WPM incl. breaks",     "int",      "#kwpmall",     False),
    ("progress",    "Progress %",           "int",      "#kprogress",   True),
    ("page",        "Furthest page",        "text",     "#kpage",       True),
    ("words",       "Words read",           "int",      "#kwords",      True),
    ("book_words",  "Words in book",        "int",      "#kbookwords",  False),
    ("annotations", "Highlights / notes",   "int",      "#kannots",     False),
    ("status",      "Read status",          "text",     "#kreadstatus", True),
    ("last_read",   "Last read",            "datetime", "#klastread",   False),
]

# convenience views
FIELD_KEYS = [f[0] for f in FIELDS]
FIELD_LABEL = {f[0]: f[1] for f in FIELDS}
FIELD_DT = {f[0]: f[2] for f in FIELDS}
DEFAULT_COL = {f[0]: f[3] for f in FIELDS}
DEFAULT_ON = {f[0]: f[4] for f in FIELDS}
