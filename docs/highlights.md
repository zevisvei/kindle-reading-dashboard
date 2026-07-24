# Highlights: mapping KRDS positions to book text

How the dashboard turns a Kindle highlight (which is stored as bare *positions*)
back into the *text* you highlighted. This documents the format and the empirical
check behind [`../azw3text/`](../azw3text/).

## Where highlights live

A highlight/note/bookmark is stored in the reader-data-store sidecar `*.azw3r`,
inside the book's `.sdr` folder, under `annotation.cache.object`. Decoded with
[`krds.py`](../reading-metadata/scripts/krds.py) one highlight looks like:

```json
{
  "startPosition": "234061",
  "endPosition":   "234158",
  "creationTime":  "2026-05-07T13:59:53.233000",
  "lastModificationTime": "2026-05-07T13:59:53.233000",
  "template": "0￼0"
}
```

Just offsets — **no text**. Notes add a `note` field (your typed note) but still no
highlighted text. The text has to come from the book itself.

## What the positions point at

For a **KF8 / AZW3** book, `startPosition` and `endPosition` are **byte offsets into
the assembled main markup flow** — i.e. into the blob KindleUnpack writes as
`assembled_text.dat` when run with `-d` (`b"".join(k8proc.parts)`, the skeleton +
fragments reassembled into the original flow-0 markup).

This is *not* the same frame as the raw `.rawml` (which also contains the non-text
flows — CSS, SVG), and it is *not* a scaled "location" number. It is a plain byte
offset into the assembled text.

## The empirical check

*The Innovators* (Hebrew translation), one real highlight at `234061–234158`:

| buffer | `assembled_text[234061:234158]` (tags stripped) |
|--------|--------------------------------------------------|
| `assembled_text.dat` | `…אמר פעם, "אבל אינטואיציה היא רק תולדה של` — coherent (an Einstein quote) ✓ |
| `.rawml` (wrong frame) | `…נטואיטיבי למדי", איינ…` — shifted, garbled ✗ |

A second highlight at `1692720–1692748` slices out `הקמתה של החברה המלכותית`
("the founding of the Royal Society"), and its attached note sits at `1692734–1692748`
inside that same span. Positions land exactly where the reader put them.

`apnx.key.oPNToPosition` in the same sidecar is in the **same** offset space (values
`0, 2300, 4600, …`), which is how the dashboard also resolves a highlight to a printed
page number.

### Reproduce it

```bash
python azw3text/assemble_azw3_text.py "BOOK.azw3"                 # -> BOOK.dat
python - <<'PY'
buf = open("BOOK.dat","rb").read()
print(buf[234061:234158].decode("utf-8","replace"))
PY
# or just:
python azw3text/extract_highlights.py "BOOK.azw3" --stdout
```

## Word-boundary snapping

The reader stores the raw offset of the selection edge, which can fall mid-word. The
extractor widens `[start,end)` outward to the nearest ASCII whitespace so highlights
read as whole words (ASCII whitespace never occurs inside a UTF-8 multibyte sequence,
so widening never splits a character). The exact byte slice is preserved as
`text_exact` in the JSON output.

## Scope

- **KF8 / AZW3 only.** Mobipocket-7 (`.mobi`) uses a different position basis (offset
  into the mobi7 record text); KFX is a different container entirely. Both are rejected.
- **DRM.** Encrypted books can't be read.

*Verified on FW 5.18.1, Kindle Basic 10th gen. Uses KindleUnpack (GPL v3) + krds.py (GPL v3).*
