# Where the Kindle stores "Mark as Read"

A non-obvious finding, verified empirically on FW 5.18.1 (new `KPPMainApp`).

## TL;DR

The modern **Read/Unread** status is **not** stored in `cc.db.p_readState`. Manually marking a book Read leaves `p_readState=1` (reading) even after a sync and a full reboot. The mark lives in:

1. **`/mnt/us/system/fmcache/fmcache.db`** (fast-metrics queue) — locally, and
2. the **Amazon cloud** (whispersync dataset `BookReadStates/CurrentStates`) — authoritatively.

## Evidence

Marking "The Innovators" as Read produced this record in `fmcache.db` → table `records`:

```json
{
  "schema_name": "mar_content_readstate_update_success",
  "previous_read_state": "UNKNOWN",
  "read_state": "READ",
  "read_state_origin": "MANUAL",
  "read_state_internal_source": "DETAIL_VIEW",
  "updated_asin": "<cdeKey>"
}
```

Note `previous_read_state: "UNKNOWN"` — the modern system has its **own** namespace and never read the legacy `p_readState=1`. Meanwhile `cc.db.p_readState` stayed `1` on disk (verified with on-device `sqlite3`, not a stale copy), even after `Sync My Kindle` and a full power-cycle.

Books that *do* show `p_readState=2` got that value from an **earlier cloud round-trip** when the device had Wi-Fi. With usbnet-only (no internet, `wlan0` down), a fresh manual mark never propagates to `cc.db`.

## What's in fmcache.db

A rolling **fast-metrics** (telemetry) buffer pending upload. Useful tables:

| Table | Contents |
|-------|----------|
| `records` | event log. `schema_name='mar_content_readstate_update_success'` = read-state marks; many other reading events (`ereader_open_book`, `ereader_close_book`, `eink_end_actions_class_instance`, latency ops, …) keyed by `cde_key`/`book_asin`/`asin`. |
| `reading_sessions` | real per-book sessions: `{asin, start_timestamp, end_timestamp, start/end_reading_location, is_complete}`. |
| `mar_book_read_stats` | account-wide aggregate: `{read, manual_unread, unknown}`. |

Because it is a rolling telemetry buffer, it holds only **recent** sessions/events, not full history, and gets cleared after upload.

## The cloud side

`/var/local/wsync.db` registers a whispersync dataset `BookReadStates/CurrentStates` in `DataSetProperties`, but locally it has **0 records** — the read states live server-side and are fetched/synced when online.

## How this toolkit handles it

`dashboard.py` reads `fmcache.db` and uses `mar_content_readstate_update_success` (latest per asin) as a **read-state override** on top of `cc.db.p_readState`, so a freshly-marked book correctly shows "Read (manual)". It also surfaces the `reading_sessions` and the raw event records per book.

## Related device facts

- `cc.db` is held open by `KPPMainApp`. There is **no** separate `com.lab126.ccat` upstart job on this firmware (`stop com.lab126.ccat` → "Unknown job"); the catalog is part of the main app.
- Stores write lazily; a clean shutdown/reboot flushes `cc.db`, but the read-state still won't appear there without a cloud round-trip.
