# azw3text — book text & highlight extraction

Two small, single-purpose scripts that pull the *text* out of a sideloaded
AZW3/KF8 book, and use it to turn the Kindle's stored highlight/note **positions**
into the actual **sentences** you highlighted.

| script | in | out |
|--------|-----|-----|
| `assemble_azw3_text.py` | `book.azw3` | `book.dat` — the whole book text as one blob |
| `extract_highlights.py` | `book.azw3` + its `.sdr` sidecar | `book.highlights.md` — your highlights, with notes/pages/dates |

Both are standalone CLIs; the dashboard also imports them (see below).

---

## Why this exists

KindleUnpack, run with `-d`, drops a debug file `assembled_text.dat`: the entire
book as a single blob (the reconstructed KF8 main markup flow). That one artefact
is exactly what you need, but it's buried inside a program that also builds a full
EPUB, extracts every image and font, etc.

`assemble_azw3_text.py` drives **only** the part of KindleUnpack that produces that
blob and writes it next to the book. Nothing else is unpacked.

The pay-off is `extract_highlights.py`. The Kindle stores your highlights and notes
in the reader-data-store sidecar (`.azw3r`, inside the book's `.sdr` folder), but it
only records **positions** — start/end byte offsets — never the text. The text is in
the book. Join the two and you get your highlights back as readable text, fully
offline, with no Amazon account and no "export" limit.

**Verified on a real device (FW 5.18.1):** for KF8/AZW3, a highlight's
`startPosition`/`endPosition` are byte offsets straight into `assembled_text.dat`.
Example — offsets `234061–234158` in *The Innovators* slice out an Einstein quote;
offsets `1692720–1692748` slice out *"the founding of the Royal Society"*. See
[`../docs/highlights.md`](../docs/highlights.md) for the empirical check.

---

## Install

These scripts need **KindleUnpack** on disk (it is *not* re-implemented here — it's
used as-is, both projects are GPL v3). Get it once:

```bash
# option A: let the script fetch a private copy (into azw3text/.kindleunpack/)
python assemble_azw3_text.py --get-kindleunpack

# option B: clone it yourself and point at it
git clone https://github.com/kevinhendricks/KindleUnpack
export KINDLEUNPACK=/path/to/KindleUnpack        # or pass --kindleunpack PATH
```

`krds.py` (the sidecar parser) already ships in this repo at
`reading-metadata/scripts/krds.py`; the scripts find it automatically.

No pip packages required — pure standard library.

---

## Usage

### Get the book text

```bash
python assemble_azw3_text.py BOOK.azw3            # -> BOOK.dat (raw markup)
python assemble_azw3_text.py BOOK.azw3 --plain    # -> BOOK.txt (readable text)
python assemble_azw3_text.py *.azw3               # batch
python assemble_azw3_text.py BOOK.azw3 --stdout   # to stdout
```

`.dat` keeps the markup because highlight offsets index into it. Use `--plain` when
you just want something to read.

### Get your highlights

```bash
python extract_highlights.py BOOK.azw3                    # -> BOOK.highlights.md
python extract_highlights.py BOOK.azw3 --format json      # -> BOOK.highlights.json
python extract_highlights.py BOOK.azw3 --dat BOOK.dat     # reuse a cached .dat
```

The `.sdr` sidecar folder is found automatically next to the book (override with
`--sdr DIR` or `--azw3r FILE`). Highlights that land mid-word are widened out to
whole words for readability; the exact byte slice is kept too (`text_exact` in JSON).

Sample Markdown output:

```markdown
# The Innovators

*3 highlights, 1 notes*

> "…but intuition," Einstein once said, "is nothing but the outcome of…"

<sub>pos 234061–234158 · 2026-05-07</sub>

> the founding of the Royal Society
>
> 📝 my note here

<sub>pos 1692720–1692748 · 2026-06-16</sub>
```

---

## In the dashboard

`reader-dashboard/dashboard.py` uses these automatically to show highlighted text on
each book's detail page:

1. **You supply the text.** Run `assemble_azw3_text.py` on a book and drop the result
   as `assembled_text.dat` inside that book's cached `.sdr` folder. On the next
   `build`, highlights show their words.
2. **The dashboard makes it.** Set `KRD_ASSEMBLE_AZW3=1` before a sync. For every book
   that *has* annotations and an *unencrypted* `.azw3` reachable (over SSH or in the
   `--local` folder), it pulls the book, assembles the text, and caches it — books
   without highlights are never fetched, so it stays cheap.

```bash
KRD_ASSEMBLE_AZW3=1 python reader-dashboard/dashboard.py serve
KRD_ASSEMBLE_AZW3=1 python reader-dashboard/dashboard.py serve --local D:/documents
```

DRM-protected books are skipped with a message (their text can't be read); this only
works on your own sideloaded, DRM-free books.

---

## Limitations

- **KF8/AZW3 only.** Older Mobipocket-7 (`.mobi`) and Amazon KFX are different position
  models and are rejected with a message.
- **DRM.** Encrypted books can't be read; nothing here removes DRM.
- Offsets that fall inside markup are cleaned up (tags stripped, word boundaries
  snapped), so extracted text is legible but not always byte-perfect to the pixel the
  reader drew.

## License

GPL v3, matching KindleUnpack and `krds.py`, on which this depends.
KindleUnpack © Kevin Hendricks et al. · `krds.py` © John Howell.
