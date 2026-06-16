# reading-metadata

Standalone tools for the Kindle reader data store (KRDS) sidecars. See the
[KRDS format reference](../docs/KRDS-format.md) for the binary structures.

| Script | What it does |
|--------|--------------|
| `scripts/krds.py` | KRDS parser (John Howell, GPL v3) — **patched for FW 5.18.x**. Decodes one `.azw3f`/`.azw3r` to JSON. |
| `scripts/reading_stats.py` | Per-book reading time + pace (WPM) table + CSV, from `timer.model`. |
| `scripts/page_history.py` | Reading sessions / timeline from `page.history.store`. |
| `scripts/export_annotations.py` | Highlights/notes → CSV from `annotation.cache.object`. |
| `scripts/dump_sidecars.py` | Decode every sidecar in a folder tree to JSON. |

```bash
python scripts/krds.py "<book>.sdr/<file>.azw3f"
python scripts/reading_stats.py D:/documents
```

The dashboard ([../reader-dashboard](../reader-dashboard)) imports `krds.py` from here.
