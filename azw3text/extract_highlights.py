#!/usr/bin/env python3
"""
extract_highlights.py  --  pull your highlights & notes out of an AZW3 book.

The Kindle keeps highlights/notes in the reader-data-store sidecar (``.azw3r``,
inside the book's ``.sdr`` folder), but stores only *positions* -- byte offsets,
no text. The text lives in the book. This tool joins the two:

    .azw3r  (positions, via krds.py)   +   book text (via assemble_azw3_text)
        -> the actual highlighted sentences, with notes, pages and dates.

Verified against a real device: KF8 highlight start/end positions are byte
offsets into KindleUnpack's ``assembled_text.dat`` (the assembled main markup).
See the repo docs for the empirical check.

Usage
-----
    python extract_highlights.py BOOK.azw3
    python extract_highlights.py BOOK.azw3 --format json -o out.json
    python extract_highlights.py BOOK.azw3 --dat BOOK.dat      # reuse a cached .dat
    python extract_highlights.py BOOK.azw3 --azw3r path/to/file.azw3r

If a highlight lands mid-word (the reader stores raw offsets), the text is
expanded out to the nearest whitespace so it reads as whole words; the exact
byte-offset slice is kept too (``text_exact`` in JSON).

GPL v3 (uses KindleUnpack + krds.py, both GPL v3).
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from assemble_azw3_text import assemble_azw3_text, AssembleError  # noqa: E402

# Annotation classes that describe a span of text worth showing.
_SPAN_TYPES = {"highlight", "note", "underline"}
_MAX_SNAP = 40           # bytes we will walk outward to reach a word boundary
_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"\s+")
_log = logging.getLogger("krds")
_log.setLevel(logging.ERROR)


# --------------------------------------------------------------------------- #
#  Locating krds.py (the sidecar parser)
# --------------------------------------------------------------------------- #
def _find_krds():
    for cand in (os.environ.get("KRDS"),
                 os.path.join(_HERE, "..", "reading-metadata", "scripts"),
                 os.path.join(_HERE, "reading-metadata", "scripts"),
                 _HERE):
        if cand and os.path.isfile(os.path.join(cand, "krds.py")):
            return os.path.abspath(cand)
    return None


def _load_krds():
    d = _find_krds()
    if not d:
        raise AssembleError(
            "krds.py not found. It ships in this repo at "
            "reading-metadata/scripts/krds.py; set $KRDS to its folder if elsewhere.")
    if d not in sys.path:
        sys.path.insert(0, d)
    import krds  # noqa: E402
    return krds


# --------------------------------------------------------------------------- #
#  Position -> text
# --------------------------------------------------------------------------- #
def _clean_span(seg):
    """Bytes of assembled markup -> readable text (tags + tag fragments removed)."""
    text = seg.decode("utf-8", "replace")
    text = _TAG.sub(" ", text)                       # whole tags
    # a highlight edge can cut a tag in half; drop the dangling halves
    gt, lt = text.find(">"), text.find("<")
    if gt != -1 and (lt == -1 or gt < lt):           # leading "...>"
        text = text[gt + 1:]
    lt = text.rfind("<")
    if lt != -1 and ">" not in text[lt:]:            # trailing "<..."
        text = text[:lt]
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&#39;", "'").replace("&quot;", '"'))
    return _WS.sub(" ", text).strip()


def _snap(buf, start, end):
    """Widen [start,end) outward to the nearest ASCII whitespace (whole words).

    ASCII whitespace bytes never occur inside a UTF-8 multibyte sequence, so the
    widened slice is always valid UTF-8 and never splits a character.
    """
    ls = start
    steps = 0
    while ls > 0 and not buf[ls - 1:ls].isspace() and steps < _MAX_SNAP:
        ls -= 1
        steps += 1
    if steps >= _MAX_SNAP:                            # no boundary near -> don't guess
        ls = start
    le = end
    steps = 0
    n = len(buf)
    while le < n and not buf[le:le + 1].isspace() and steps < _MAX_SNAP:
        le += 1
        steps += 1
    if steps >= _MAX_SNAP:
        le = end
    return ls, le


def highlight_text(buf, start, end, snap=True):
    """Return (readable, exact) text for the byte range [start, end) of *buf*."""
    n = len(buf)
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return "", ""
    if start > end:
        start, end = end, start
    start = max(0, min(start, n))
    end = max(0, min(end, n))
    exact = _clean_span(buf[start:end])
    if not snap:
        return exact, exact
    ls, le = _snap(buf, start, end)
    return _clean_span(buf[ls:le]), exact


# --------------------------------------------------------------------------- #
#  Annotations from a decoded .azw3r
# --------------------------------------------------------------------------- #
def annotations_from_azw3r(azw3r_obj):
    """Flatten annotation.cache.object into a list of {type,startPosition,...}."""
    out = []
    aco = (azw3r_obj or {}).get("annotation.cache.object")
    if isinstance(aco, dict):
        for cls, items in aco.items():
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        out.append({"type": cls.split(".")[-1], **it})
    return out


def has_text_annotations(azw3r_obj):
    """True if there's at least one span annotation (highlight/note/underline).

    Bookmarks carry a position but no text worth extracting, so a book with only
    bookmarks does not need its text assembled.
    """
    return any(a["type"] in _SPAN_TYPES for a in annotations_from_azw3r(azw3r_obj))


def extract_highlights(assembled, azw3r_obj, snap=True):
    """Join decoded annotations with the assembled text.

    Returns a list of dicts sorted by start position, each with the original
    KRDS fields plus ``text`` (readable) and ``text_exact``.
    """
    result = []
    for a in annotations_from_azw3r(azw3r_obj):
        rec = dict(a)
        if a["type"] in _SPAN_TYPES:
            readable, exact = highlight_text(
                assembled, a.get("startPosition"), a.get("endPosition"), snap=snap)
            rec["text"] = readable
            rec["text_exact"] = exact
        result.append(rec)

    def _key(r):
        try:
            return int(r.get("startPosition"))
        except (TypeError, ValueError):
            return 0
    result.sort(key=_key)
    return result


# --------------------------------------------------------------------------- #
#  Sidecar / book plumbing for the CLI
# --------------------------------------------------------------------------- #
def find_sdr(book, sdr=None):
    if sdr:
        return sdr
    base, _ = os.path.splitext(os.path.abspath(book))
    cand = base + ".sdr"
    return cand if os.path.isdir(cand) else None


def find_azw3r(book, sdr=None, azw3r=None):
    if azw3r:
        return azw3r
    d = find_sdr(book, sdr)
    if not d:
        return None
    hits = glob.glob(os.path.join(d, "*.azw3r"))
    return hits[0] if hits else None


def decode_azw3r(path, krds=None):
    krds = krds or _load_krds()
    with open(path, "rb") as f:
        data = f.read()
    return krds.KindleReaderDataStore(_log, data).deserialize()


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
def _fmt_date(iso):
    return (iso or "").split("T")[0]


def render_markdown(highlights, title=None):
    title = title or "Highlights"
    hs = [h for h in highlights if h["type"] in _SPAN_TYPES and h.get("text")]
    bms = [h for h in highlights if h["type"] == "bookmark"]
    n_notes = sum(1 for h in hs if h.get("note"))
    lines = ["# %s" % title,
             "",
             "*%d highlights%s%s*" % (
                 len(hs),
                 (", %d notes" % n_notes) if n_notes else "",
                 (", %d bookmarks" % len(bms)) if bms else ""),
             ""]
    for h in hs:
        text = h.get("text", "").strip()
        if not text:
            continue
        for ln in text.split("\n"):
            lines.append("> %s" % ln)
        if h.get("note"):
            lines.append(">")
            lines.append("> \U0001F4DD %s" % h["note"])
        meta = "pos %s–%s" % (h.get("startPosition"), h.get("endPosition"))
        d = _fmt_date(h.get("creationTime"))
        if d:
            meta += " · %s" % d
        lines.append("")
        lines.append("<sub>%s</sub>" % meta)
        lines.append("")
    if bms:
        lines.append("## Bookmarks")
        for b in bms:
            lines.append("- pos %s · %s" % (b.get("startPosition"), _fmt_date(b.get("creationTime"))))
        lines.append("")
    return "\n".join(lines)


def render_text(highlights, title=None):
    out = []
    if title:
        out.append(title)
        out.append("=" * len(title))
        out.append("")
    for h in highlights:
        if h["type"] in _SPAN_TYPES and h.get("text"):
            out.append(h["text"])
            if h.get("note"):
                out.append("    [note] %s" % h["note"])
            out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Extract highlights & notes from an AZW3 book.")
    ap.add_argument("book", help="the .azw3 file")
    ap.add_argument("-o", "--out", help="output file (default: <book>.highlights.<ext>)")
    ap.add_argument("--format", choices=["md", "json", "txt"], default="md")
    ap.add_argument("--dat", help="reuse a cached assembled-text .dat instead of re-assembling")
    ap.add_argument("--sdr", help="the book's .sdr sidecar folder (auto-detected by default)")
    ap.add_argument("--azw3r", help="the .azw3r sidecar file (auto-detected by default)")
    ap.add_argument("--no-snap", action="store_true",
                    help="do not widen highlights to whole words")
    ap.add_argument("--kindleunpack", metavar="PATH", help="path to KindleUnpack (repo or lib/)")
    ap.add_argument("--stdout", action="store_true", help="write to stdout")
    args = ap.parse_args(argv)

    azw3r_path = find_azw3r(args.book, args.sdr, args.azw3r)
    if not azw3r_path:
        sys.exit("no .azw3r sidecar found (looked for <book>.sdr/*.azw3r); "
                 "pass --azw3r or --sdr")
    try:
        azw3r = decode_azw3r(azw3r_path)
    except Exception as e:
        sys.exit("failed to decode %s: %s" % (azw3r_path, e))

    annots = annotations_from_azw3r(azw3r)
    if not annots:
        print("no annotations in %s" % os.path.basename(azw3r_path), file=sys.stderr)
        # still emit an (empty) file so callers can rely on it existing
        highlights = []
        assembled = b""
    else:
        try:
            if args.dat:
                with open(args.dat, "rb") as f:
                    assembled = f.read()
            else:
                assembled = assemble_azw3_text(args.book, kindleunpack=args.kindleunpack)
        except AssembleError as e:
            sys.exit("could not read book text: %s" % e)
        highlights = extract_highlights(assembled, azw3r, snap=not args.no_snap)

    title = os.path.splitext(os.path.basename(args.book))[0]
    if args.format == "json":
        body = json.dumps({"title": title, "source": os.path.basename(args.book),
                           "highlights": highlights}, ensure_ascii=False, indent=2)
        ext = "json"
    elif args.format == "txt":
        body = render_text(highlights, title)
        ext = "txt"
    else:
        body = render_markdown(highlights, title)
        ext = "md"

    if args.stdout:
        sys.stdout.buffer.write(body.encode("utf-8"))
        return 0
    out = args.out or (os.path.splitext(args.book)[0] + ".highlights." + ext)
    with open(out, "w", encoding="utf-8") as f:
        f.write(body if body.endswith("\n") else body + "\n")
    n = sum(1 for h in highlights if h["type"] in _SPAN_TYPES and h.get("text"))
    print("wrote %s (%d highlights)" % (out, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
