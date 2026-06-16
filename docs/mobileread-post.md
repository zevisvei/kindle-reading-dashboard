# MobileRead announcement post (backup)

Where: **Kindle Developer's Corner** — https://www.mobileread.com/forums/forumdisplay.php?f=150

## Title

```
[Tool] kindle-reading-dashboard — view your on-device reading stats, pace & metadata (KRDS + cc.db)
```

## Body (BBCode)

```bbcode
I put together a small offline toolkit that reads and visualizes the reading metadata a Kindle already stores on-device — reading time, pace (WPM), per-book progress, highlights, printed-page mapping, and read/unread status — in a local web dashboard. No cloud account, no Amazon API, no KOReader.

[B]GitHub:[/B] https://github.com/zevisvei/kindle-reading-dashboard
[B]License:[/B] GPL v3

[B]What it shows[/B]
[LIST]
[*]Library list: title, author, series, % complete, reading time, pace, current page, read/reading/unread — sortable & searchable.
[*]Per-book detail: timer.model reading-time data, reading-speed distribution, reading timeline, device sessions, highlights/notes, font prefs, all cc.db fields, plus the raw decoded JSON of every source.
[*]Position → printed page translation via the apnx page map.
[/LIST]

[B]Where the data comes from[/B]
[LIST]
[*][B]cc.db[/B] (/var/local) — library: title/author/series/%/legacy read-state.
[*][B]*.azw3f / *.azw3r[/B] KRDS sidecars — reading time/pace (timer.model), word counts, page history, last/first position, annotations, font prefs.
[*][B]fmcache.db[/B] (/mnt/us/system/fmcache) — this was the interesting find: the modern "Mark as Read" status is NOT in cc.db.p_readState. The new app keeps read-state in fmcache (schema mar_content_readstate_update_success) and in the cloud (whispersync BookReadStates), so a manual mark can sit there without ever touching p_readState. The dashboard reads fmcache and uses it as an override, and also surfaces the per-book reading_sessions there.
[/LIST]

[B]Requirements[/B]
[LIST]
[*]Python 3 + paramiko.
[*]Full dashboard needs a jailbroken device with USB networking (SSH). Basic sidecar decoding (time/pace/highlights/page map) works on a stock device mounted as a USB drive, via the CLI scripts or --local.
[/LIST]
Tested on a Kindle Basic 10th gen (J9G29R), FW 5.18.1.

[B]Credits[/B]
The KRDS parser (krds.py) is John Howell's work (GPL v3) from the KRDS thread ( https://www.mobileread.com/forums/showthread.php?t=322172 ); I patched it for FW 5.18.x (a raw fallback so unknown/new structures don't break decoding). Windows RNDIS usbnet driver by Marco77.

Not affiliated with Amazon. Reads metadata only — no DRM circumvention, read-only against the device. Feedback welcome.
```

## Moderator PM (if the thread is still not visible after ~48h)

To: a Kindle Developer's Corner moderator (or post in the Feedback forum).

```
Subject: New thread awaiting approval — Kindle Developer's Corner

Hi, I posted a new thread in Kindle Developer's Corner a couple of days ago
("[Tool] kindle-reading-dashboard …") and it doesn't appear to be visible yet —
I think it's held in the new-member moderation queue. Could you please review/approve
it when you have a moment? It's an open-source (GPL v3) reading-metadata tool, not spam.
Thanks!
```
