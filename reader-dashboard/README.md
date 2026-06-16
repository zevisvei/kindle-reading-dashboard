# reader-dashboard

The local web dashboard. Single entrypoint: `dashboard.py` (sync → build → serve).
See the [root README](../README.md) for full docs.

```bash
python dashboard.py serve              # SSH sync + build + open browser
python dashboard.py serve --no-sync    # reuse local cache
python dashboard.py serve --local D:/documents   # USB mass-storage instead of SSH
python dashboard.py sync | build       # individual steps
```

- `web/index.html` — sortable/searchable library table
- `web/book.html` — full per-book metadata + raw JSON of every source
- `web/common.js` — shared helpers (date/number formatting, page mapping, read-status)
- Generated `web/library.json` and `cache/` hold your personal data and are git-ignored.

Reads `cc.db` (library), the KRDS `.azw3f`/`.azw3r` sidecars (reading data), and
`fmcache.db` (modern read-state + device sessions — see
[../docs/read-state-storage.md](../docs/read-state-storage.md)).
