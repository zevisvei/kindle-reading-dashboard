# Kindle Reading Dashboard — Calibre plugin

Imports the reading metadata your Kindle stores **on-device** into Calibre
custom columns, and launches the standalone web dashboard.

Per book it can fill: **reading hours, pace (WPM), progress %, furthest page,
words read, read/reading/unread status.** All decoded from the device's own
`.azw3f`/`.azw3r` sidecars (+ `cc.db` / `fmcache.db` over SSH) — no cloud, no
Amazon API.

## Build & install

```bash
cd calibre-plugin
python build.py                                   # -> kindle_reading_dashboard.zip
calibre-customize -a kindle_reading_dashboard.zip # or: Calibre → Preferences → Plugins → Load from file
```

Restart Calibre. A **Kindle Reading** button appears on the toolbar.

## Fields you can import

Tick any of these in **Configure** (each maps to a custom column you name):
reading hours, reading minutes, pace (WPM, net), WPM incl. breaks, progress %,
furthest page, words read, words in book, highlights/notes count, read status,
last-read date. Six are on by default.

## First run

1. **Configure…** (button menu):
   - **Fields to import** — tick the fields you want; each row has the column lookup name. Click **Create missing columns…** to create the ticked ones, then restart Calibre so they show.
   - **Data source**: `auto` (USB, with cc.db when present), `usb`, or `ssh`. Auto never silently SSHs — a disconnected device gives a clear message.
   - **USB documents folder**: leave blank to auto-detect the connected Kindle; or set e.g. `E:/documents`.
   - **SSH**: host / user / password / optional key. Used only when Source = ssh.
   - **Import automatically when the Kindle connects** — runs the import a few seconds after the device mounts.
   - **dashboard.py**: path to `reader-dashboard/dashboard.py` (for *Open web dashboard*).
2. **Import reading stats (all books)** — syncs, decodes, matches to your library, writes the enabled columns.
3. **Refresh selected book(s)** — same, but only updates the rows you have selected in the library.

## How matching works

Books are matched to your Calibre library by **Amazon ASIN** identifier
(`amazon` / `mobi-asin` / `amazon_*`) first, then by **normalised title**
(toggleable). USB-only mode has no `cc.db`, so titles come from the `.sdr`
folder name and read-status is unavailable — connect over SSH for the full set.

## Device page

The reading columns also appear on the **device page** (Kindle connected) in the
**Book Details panel** for any device book that is matched to your library —
use menu → *Show stats in Book Details*. Calibre does **not** allow custom
columns as *grid* columns in the device book list itself; the details panel is
the supported place. (Custom columns show in Book Details by default, so this is
usually a no-op unless you previously hid fields.)

## Troubleshooting — black screen / popping command windows

Fixed in 1.0. Cause: when the Kindle is plugged in as a **USB drive**, usbnet
SSH (`192.168.15.244`) is unreachable, and the SSH fallback over the system
`ssh` binary opened one console window per file and blocked the UI. Now:

- USB is auto-detected and preferred, so SSH is not used at all over USB.
- A 2.5 s TCP probe (`ssh_reachable`) aborts SSH fast instead of flooding.
- All subprocesses run with `CREATE_NO_WINDOW` on Windows — no popups.

## Source / SSH notes

- **USB mass-storage** exposes the sidecars (reading time, pace, progress,
  furthest page, annotations) but **not** `cc.db`/`fmcache.db`.
- **SSH (usbnet)** pulls everything, including modern "Mark as Read" state.
- SSH uses **paramiko** if Calibre has it, otherwise the system **`ssh`**
  binary (key-based / agent auth recommended — non-interactive password auth
  via plain `ssh` is unreliable; prefer a key, or use paramiko).

Decoding reuses the bundled `krds.py` parser, identical to the toolkit's.
