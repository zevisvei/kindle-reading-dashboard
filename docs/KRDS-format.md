# KRDS — Kindle Reader Data Store format

Reference for the binary "reader data store" sidecar files a Kindle writes next to each book:
`.azw3r`, `.azw3f`, `.mbp1`, `.mbs`, `.yjf`, `.yjr`.

Based on [krds.py](../reading-metadata/scripts/krds.py) (John Howell, GPL v3) and the actual format observed on **FW 5.18.1**. Every structure the parser knows is documented here, including ones that may not appear in your data.

---

## Container format

A file is: signature (`00 00 00 00 00 1A B1 26`) + value `1` + a count + objects.
Every value is tagged with a type byte. An object is `OBJECT_BEGIN` + name (UTF) + values + `OBJECT_END`.

### Datatype tags

| Code | Type | Encoding |
|----:|------|----------|
| 0 | BOOLEAN | byte: 0=false, 1=true |
| 1 | INT | 4-byte signed, big-endian |
| 2 | LONG | 8-byte signed |
| 3 | UTF | bool + 2-byte length + UTF-8 (empty string if bool=true) |
| 4 | DOUBLE | 8-byte float |
| 5 | SHORT | 2-byte signed |
| 6 | FLOAT | 4-byte float |
| 7 | BYTE | signed byte |
| 9 | CHAR | single char |
| -2 | OBJECT_BEGIN | named object |
| -1 | OBJECT_END | end of object |

---

## Reading time & pace (in `.azw3f`) — the most useful data

### `timer.model` — the book's reading-time model
| Field | Type | Meaning |
|-------|------|---------|
| `version` | long | structure version |
| `totalTime` | long | **cumulative reading time, milliseconds** |
| `totalWords` | long | **words read** (in measured sessions) |
| `totalPercent` | double | fraction of book read (0–1) by words |
| `averageCalculator` | object | speed model → `timer.average.calculator` |

Pace: `WPM = totalWords / (totalTime / 60000)`.

### `timer.average.calculator` — reading-speed statistics
| Field | Type | Meaning |
|-------|------|---------|
| `samples1` / `samples2` | double[] | speed samples |
| `normalDistributions` | object[] | cumulative normal distributions |
| `outliers` | double[] | discarded samples (fast flipping / pauses) |

### `timer.average.calculator.distribution.normal`
| Field | Type | Meaning |
|-------|------|---------|
| `count` | long | sample count |
| `sum` | double | sum (mean = sum/count) |
| `sumOfSquares` | double | for variance: `sumOfSquares/count − mean²` |

This is how the Kindle estimates reading speed and "X minutes left in chapter".

### `book.info.store`
| Field | Type | Meaning |
|-------|------|---------|
| `numberOfWords` | long | total words in the book |
| `percentOfBook` | double | fraction of book with known word counts |

### `page.history.store` — page-turn history
Array of `page.history.record`: `{position (string), time (ISO datetime)}`. The basis for a reading timeline. Sparse on FW 5.18.x.

### `timer.data.store` / `.v2` — timer state (usually null)
`on` (bool), `readingTimerModel` (object), `version` (long), `lastOption` (int, v2 only).

---

## Reading position (in `.azw3f` and `.azw3r`)

- **`lpr`** — Last Page Read: `{position, time}`. Old style is just a position string; version ≤2 adds time.
- **`fpr` / `updated_lpr`** — extended: `{position, time, timeZoneOffset, country, device}`.
- **`erl`** — End Reading Location (single position).
- **`sync_lpr`** — bool, whether to sync LPR to the cloud.

---

## Annotations (in `.azw3r`)

### `annotation.cache.object`
Cache of all annotations, grouped by type. Empty (`[]`) if none.

| Code | Class |
|----:|-------|
| 0 | annotation.personal.bookmark |
| 1 | annotation.personal.highlight |
| 2 | annotation.personal.note |
| 3 | annotation.personal.clip_article |
| 10 | annotation.personal.handwritten_note |
| 11 | annotation.personal.sticky_note |
| 13 | annotation.personal.underline |

### `annotation.personal.*` — one annotation
`startPosition`, `endPosition`, `creationTime`, `lastModificationTime`, `template`, and for notes: `note` text (or `*_nbk_ref` for handwritten/sticky).

---

## Display preferences (in `.azw3r`)

### `font.prefs`
`typeface`, `lineSp`, `size`, `align`, `insetTop/Left/Bottom/Right`, `bold`, `userSideloadableFont`, `customFontIndex`, `mobi7SystemFont`, `mobi7RestoreFont`, `readingPresetSelected`. On FW 5.18.x extra trailing fields appear; the patched parser stores them under `_unparsed_trailing`.

### `reader.state.preferences`
`fontPreferences` (→ font.prefs), `leftMargin`, `rightMargin`, `topMargin`, `bottomMargin`.

### `language.store`
`language` (string), `unknown1` (int).

---

## Page numbering

### `apnx.key` — printed-page mapping
| Field | Meaning |
|-------|---------|
| `asin` | print-edition ISBN (page-number source) |
| `cdeType` | CDE type (e.g. EBOK) |
| `sidecarAvailable` | whether an `.apnx` file exists |
| `oPNToPosition` | **int[] — index = printed page number, value = starting position** |
| `first` | first numbered page |
| `pageMap` | page-map string, e.g. `(1,a,1)` |

**Position → page:** find the largest index `i` with `oPNToPosition[i] ≤ position`. Total pages = `len(oPNToPosition) − 1`. (Uniform increments mean an artificial/even page split; non-uniform means a real print edition.)

---

## The FW 5.18.x patch

The original `krds.py` failed on the newer format (`font.prefs` excess values, `annotation.cache.object` pops, the unknown `whisperstore.migration.status`). The fix in this repo's `krds.py`:
- `decode_object` wraps per-structure decoding in try/except → on failure returns the raw value list (`raw` fallback). Safe because the object is read in full (delimited by `OBJECT_END`) before decoding, so the byte stream stays aligned.
- Unknown trailing fields are drained and preserved under `_unparsed_trailing`.

---

*Based on krds.py + a real device. FW 5.18.1, Kindle Basic 10th gen (J9G29R).*
