#!/usr/bin/env python3
"""
assemble_azw3_text.py  --  extract the full assembled text of an AZW3/KF8 book.

KindleUnpack, when run with ``-d``, writes a debug file ``assembled_text.dat``:
the complete text of the book as a single blob (the reconstructed main KF8
markup flow). That one artefact is buried inside a large program that also
builds a whole EPUB, copies images, fonts, etc.

This is a small, single-purpose front end: it drives *only* the part of
KindleUnpack that produces that blob and writes it next to the book, named
after the book (``<book>.dat``). Nothing else is unpacked, nothing is written
to a working directory.

Why the raw ``.dat`` (markup) and not clean ``.txt``?  The Kindle stores
reading positions -- last-page-read, and highlight/note start/end -- as byte
offsets into exactly this assembled markup. Keeping the markup lets
``extract_highlights.py`` slice the highlighted text straight out by offset.
Use ``--plain`` if you also want a tag-stripped ``.txt`` for human reading.

Usage
-----
    python assemble_azw3_text.py BOOK.azw3                 # -> BOOK.dat
    python assemble_azw3_text.py BOOK.azw3 -o OUT.dat
    python assemble_azw3_text.py BOOK.azw3 --plain         # also BOOK.txt
    python assemble_azw3_text.py BOOK.azw3 --stdout > x
    python assemble_azw3_text.py *.azw3                    # batch

Finding KindleUnpack
--------------------
This needs KindleUnpack's ``lib/`` on disk (it is not re-implemented here).
It is located, in order, from:
  1. --kindleunpack PATH        (path to the KindleUnpack repo, or its lib/)
  2. $KINDLEUNPACK               (same)
  3. common spots next to this script / the book / the cwd
  4. python assemble_azw3_text.py --get-kindleunpack   (git-clones a copy)

KindleUnpack: https://github.com/kevinhendricks/KindleUnpack  (GPL v3)
This wrapper is offered under the same GPL v3 terms.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

K8_BOUNDARY = b"BOUNDARY"

# Where --get-kindleunpack drops a copy, and one of the auto-detect spots.
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".kindleunpack")
_KU_URL = "https://github.com/kevinhendricks/KindleUnpack"


class AssembleError(Exception):
    """Anything that stops us producing assembled text (DRM, mobi7-only, ...)."""


# --------------------------------------------------------------------------- #
#  Locating KindleUnpack
# --------------------------------------------------------------------------- #
def _is_ku_root(path):
    """A directory that contains the KindleUnpack ``lib`` package."""
    return bool(path) and os.path.isfile(os.path.join(path, "lib", "mobi_k8proc.py"))


def _normalise_ku(path):
    """Accept either the repo root or its ``lib/`` dir; return the root."""
    if not path:
        return None
    path = os.path.abspath(os.path.expanduser(path))
    if _is_ku_root(path):
        return path
    # user pointed straight at .../KindleUnpack/lib
    parent = os.path.dirname(path)
    if os.path.basename(path) == "lib" and _is_ku_root(parent):
        return parent
    return None


def find_kindleunpack(hint=None, book=None):
    """Return the KindleUnpack repo root, or None if it cannot be found."""
    candidates = [hint, os.environ.get("KINDLEUNPACK")]
    here = os.path.dirname(os.path.abspath(__file__))
    search_bases = [here, os.path.dirname(here), _VENDOR_DIR, os.getcwd()]
    if book:
        search_bases.append(os.path.dirname(os.path.abspath(book)))
    for base in search_bases:
        candidates.append(base)
        candidates.append(os.path.join(base, "KindleUnpack"))
    for c in candidates:
        root = _normalise_ku(c)
        if root:
            return root
    return None


def get_kindleunpack(dest=None):
    """git-clone a copy of KindleUnpack into ``dest`` (default: ./.kindleunpack)."""
    import subprocess
    dest = dest or os.path.join(_VENDOR_DIR, "KindleUnpack")
    if _is_ku_root(dest):
        print("KindleUnpack already present at %s" % dest)
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print("cloning KindleUnpack -> %s" % dest)
    subprocess.check_call(["git", "clone", "--depth", "1", _KU_URL, dest])
    if not _is_ku_root(dest):
        raise AssembleError("clone did not produce a usable KindleUnpack at %s" % dest)
    return dest


_KU_LOADED = None


def _load_kindleunpack(root):
    """Import KindleUnpack's lib package from ``root`` and return the modules we use."""
    global _KU_LOADED
    if _KU_LOADED and _KU_LOADED[0] == root:
        return _KU_LOADED[1]
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from lib.mobi_sectioner import Sectionizer     # noqa: E402
        from lib.mobi_header import MobiHeader          # noqa: E402
        from lib.mobi_k8proc import K8Processor         # noqa: E402
    except Exception as e:  # pragma: no cover - import wiring
        raise AssembleError("could not import KindleUnpack from %s (%s)" % (root, e))
    mods = (Sectionizer, MobiHeader, K8Processor)
    _KU_LOADED = (root, mods)
    return mods


# --------------------------------------------------------------------------- #
#  The actual assembly (mirrors kindleunpack.py's KF8 path, nothing more)
# --------------------------------------------------------------------------- #
def assemble_azw3_text(infile, kindleunpack=None):
    """Return the assembled KF8 text of *infile* as ``bytes``.

    Raises AssembleError for DRM-protected books, mobi7-only files, or when
    KindleUnpack cannot be located.
    """
    root = find_kindleunpack(hint=kindleunpack, book=infile)
    if not root:
        raise AssembleError(
            "KindleUnpack not found. Pass --kindleunpack PATH, set $KINDLEUNPACK, "
            "or run: python %s --get-kindleunpack" % os.path.basename(__file__))
    Sectionizer, MobiHeader, K8Processor = _load_kindleunpack(root)

    sect = Sectionizer(infile)
    if sect.ident not in (b"BOOKMOBI", b"TEXtREAd"):
        raise AssembleError("not a MOBI/AZW file (ident=%r)" % sect.ident)

    mh = MobiHeader(sect, 0)
    if mh.isK8():
        k8 = mh                                   # KF8-only azw3
    else:
        k8 = None                                 # maybe a combo M7/KF8
        for i in range(len(sect.sectionoffsets) - 1):
            beg, end = sect.sectionoffsets[i], sect.sectionoffsets[i + 1]
            if (end - beg) == 8 and sect.loadSection(i) == K8_BOUNDARY:
                k8 = MobiHeader(sect, i + 1)
                break
        if k8 is None:
            raise AssembleError(
                "no KF8 part in this file (older Mobipocket 7 format); "
                "assembled-text offsets would not match the reader's positions")

    if k8.isEncrypted():
        raise AssembleError("book is DRM-encrypted; cannot read its text")

    rawML = k8.getRawML()
    proc = K8Processor(k8, sect, None, False)      # files=None: only used by debug dump
    proc.buildParts(rawML)
    return b"".join(proc.parts)


# --------------------------------------------------------------------------- #
#  Markup -> plain text
# --------------------------------------------------------------------------- #
_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


def strip_markup(data):
    """Turn the assembled markup (bytes or str) into readable plain text.

    Block-level tags become newlines so paragraphs survive; everything else is
    dropped. Not meant to be perfect -- just legible.
    """
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else data
    text = re.sub(r"(?i)<\s*(/?p|/?div|br|/h[1-6]|h[1-6])\b[^>]*>", "\n", text)
    text = _TAG.sub("", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
                .replace("&quot;", '"'))
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANKS.sub("\n\n", text)
    return text.strip() + "\n"


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def _default_out(infile, plain):
    base, _ = os.path.splitext(infile)
    return base + (".txt" if plain else ".dat")


def _process_one(infile, out=None, plain=False, to_stdout=False, kindleunpack=None):
    assembled = assemble_azw3_text(infile, kindleunpack=kindleunpack)
    # default: raw assembled markup (.dat) -- what highlight extraction needs.
    # --plain: tag-stripped, human-readable text (.txt) instead.
    if to_stdout:
        sys.stdout.buffer.write(strip_markup(assembled).encode("utf-8") if plain else assembled)
        return None
    out_path = out or _default_out(infile, plain)
    if plain:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(strip_markup(assembled))
    else:
        with open(out_path, "wb") as f:
            f.write(assembled)
    return [out_path]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Extract the full assembled text of an AZW3/KF8 book "
                    "(the KindleUnpack 'assembled_text.dat', standalone).")
    ap.add_argument("books", nargs="*", help="one or more .azw3 / .azw / .mobi files")
    ap.add_argument("-o", "--out", help="output path (single input only)")
    ap.add_argument("--plain", action="store_true",
                    help="also write a tag-stripped .txt (human-readable)")
    ap.add_argument("--stdout", action="store_true",
                    help="write to stdout instead of a file")
    ap.add_argument("--kindleunpack", metavar="PATH",
                    help="path to the KindleUnpack repo (or its lib/ dir)")
    ap.add_argument("--get-kindleunpack", action="store_true",
                    help="git-clone a private copy of KindleUnpack and exit")
    args = ap.parse_args(argv)

    if args.get_kindleunpack:
        try:
            get_kindleunpack()
        except Exception as e:
            sys.exit("failed to fetch KindleUnpack: %s" % e)
        return 0

    if not args.books:
        ap.error("no book given (or use --get-kindleunpack)")
    if args.out and len(args.books) > 1:
        ap.error("-o/--out works with a single input only")

    rc = 0
    for infile in args.books:
        if not os.path.isfile(infile):
            print("skip (not a file): %s" % infile, file=sys.stderr)
            rc = 1
            continue
        try:
            written = _process_one(infile, out=args.out, plain=args.plain,
                                    to_stdout=args.stdout, kindleunpack=args.kindleunpack)
        except AssembleError as e:
            print("%s: %s" % (os.path.basename(infile), e), file=sys.stderr)
            rc = 1
            continue
        if written:
            for w in written:
                print("wrote %s (%d bytes)" % (w, os.path.getsize(w)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
