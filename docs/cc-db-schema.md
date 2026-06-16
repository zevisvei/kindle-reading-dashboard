# cc.db — Content Catalog database schema

The Kindle library database. On-device path: `/var/local/cc.db` (SQLite). It is the live catalog held open by the main app (`KPPMainApp`); other copies like `/mnt/us/cc.db` are stale backups.

Source: the `Entries` schema with Amazon's original column comments (FW 5.18.1, J9G29R).

---

## Naming convention

| Prefix | Meaning |
|--------|---------|
| `p_` | scalar property (number/text/bool) |
| `j_` | JSON-encoded property (array/object) |
| `d_` | detail column (in the `Series` table) |
| `p_titles_0_nominal` | flattened `titles[0].nominal` (index 0 = first) |
| `COLLATE icu` | ICU collation — a non-standard sqlite extension |

---

## Table `Entries` — key fields

### Identity / CDE
| Field | Meaning |
|-------|---------|
| `p_uuid` | unique row id — **primary key** |
| `p_type` | `Entry:Item`=book, `Entry:Item:Series`=series container, `Collection`=collection |
| `p_cdeKey` | content key/ASIN (ASIN for store items, UUID for sideloaded) |
| `p_cdeType` | `EBOK`=book, `PDOC`=personal doc, `series` |

### Files / location
| Field | Meaning |
|-------|---------|
| `p_location` | content file URI, e.g. `/mnt/us/documents/.../book.azw3` |
| `p_cover`, `p_thumbnail` | image paths |
| `p_contentSize`, `p_diskUsage` | sizes |

### Title / author
| Field | Meaning |
|-------|---------|
| `p_titles_0_nominal` | **display title** |
| `j_credits` | JSON array of credits (author display name in `[0].name.display`) |
| `p_credits_0_name_collation` | first author, for sorting (COLLATE icu) |

### Reading progress
| Field | Meaning |
|-------|---------|
| `p_lastAccessedPosition` | last read position (relative URI, e.g. `#1234`) |
| `p_percentFinished` | **percent complete (0–100)** |
| `p_readState` | **legacy** read state: `null/0`=unread, `1`=reading, `2`=read. **NOT** updated by the modern "Mark as Read" — see [read-state-storage.md](read-state-storage.md). |
| `p_lastAccess` | last access, **epoch SECONDS** (not ms). |

### Series
Built in two parts: rows in the `Series` table link each book (`d_itemCdeKey`) to a series id (`d_seriesId`) with a position (`d_itemPosition`, `d_itemPositionLabel`); and a container row in `Entries` with `p_type='Entry:Item:Series'` holds the series name.

### Collections / visibility / DRM
`j_collections`, `p_collectionCount`, `p_isArchived`, `p_isVisibleInHome`, `p_isDRMProtected`, `p_originType` (0=normal, 21=Kindle Unlimited), `p_publisher`, `p_languages_0`.

---

## Useful queries

```sql
-- reading progress per book
SELECT substr(p_titles_0_nominal,1,30) AS title,
       round(p_percentFinished) AS pct, p_readState AS state
FROM Entries WHERE p_type='Entry:Item' AND p_percentFinished IS NOT NULL
ORDER BY p_percentFinished DESC;

-- books grouped by legacy read state
SELECT p_readState, COUNT(*) FROM Entries
WHERE p_type='Entry:Item' GROUP BY p_readState;

-- books in a series, in order
SELECT d_itemPositionLabel, d_itemCdeKey FROM Series
WHERE d_seriesId='urn:collection:1:asin-...' ORDER BY d_itemPosition;
```

## ICU collation gotcha

`p_titles_0_collation` and `p_credits_0_name_collation` are declared `COLLATE icu`. Plain sqlite3 (without the ICU extension) **fails on INSERT** against this schema. Tools that write to `cc.db` strip `COLLATE icu` (via `PRAGMA writable_schema=ON`), insert, then restore it. This toolkit is **read-only** so it is unaffected.

---

*Source: cc.db from the device. FW 5.18.1.*
