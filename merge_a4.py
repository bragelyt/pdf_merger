#!/usr/bin/env python3
"""
Merge PDFs into one print-ready A4 PDF without re-rendering anything.

- Pages are copied as-is: images, fonts and vector graphics are NOT
  decoded, rasterized or re-compressed (unlike "Print to PDF").
- Pages that aren't A4 are scaled to fit A4 (aspect ratio kept, centered)
  by adding a single transformation matrix in front of the page content.
- Landscape pages become landscape A4, portrait pages portrait A4.
- Anything outside the visible area (crop box) stays hidden.
- Links, form fields, comments and bookmarks move along with the content.

Usage:
    Put the PDFs in the "input" folder next to this script. They are merged
    in alphabetical order (prefix them 01_, 02_, ... to control the order).
    The result is written to the "output" folder, named after the current
    time, e.g. output/2026-11-18_19-05-42.pdf.

    python merge_a4.py
    python merge_a4.py --no-upscale   # don't enlarge small pages
"""
import sys
from datetime import datetime
from pathlib import Path
from typing import cast

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    NullObject,
    PdfObject,
    RectangleObject,
)

A4_W, A4_H = 595.276, 841.890  # points (210 x 297 mm)
TOLERANCE = 2.0                 # points; treat as A4 if within this

HERE = Path(__file__).resolve().parent
INPUT_DIR = HERE / "input"
OUTPUT_DIR = HERE / "output"


def fit_to_a4(page: PageObject, allow_upscale: bool = True) -> tuple[str, Transformation | None]:
    """Turn the page into an A4 page. Returns a note and the transformation
    applied to the page content (None if the page was left untouched)."""
    box = cast(RectangleObject, page.cropbox)  # the visible area (pypdf types it as Any)
    media = cast(RectangleObject, page.mediabox)
    x0, y0 = float(box.left), float(box.bottom)
    x1, y1 = float(box.right), float(box.top)
    rotation = page.rotation % 360

    # Size as displayed, i.e. after the page's /Rotate is applied
    w, h = x1 - x0, y1 - y0
    if rotation in (90, 270):
        w, h = h, w
    tw, th = (A4_H, A4_W) if w > h else (A4_W, A4_H)

    is_a4 = abs(w - tw) <= TOLERANCE and abs(h - th) <= TOLERANCE
    if is_a4 and rotation == 0 and (x0, y0) == (0, 0) and list(media) == list(box):
        return "already A4", None

    s = min(tw / w, th / h)
    if not allow_upscale:
        s = min(s, 1.0)

    # Move the crop box centre to the origin, bake in the /Rotate (clockwise
    # in PDF), scale, then move the centre to the middle of the A4 sheet.
    t = (
        Transformation()
        .translate(-(x0 + x1) / 2, -(y0 + y1) / 2)
        .rotate(-rotation)
        .scale(s, s)
        .translate(tw / 2, th / 2)
    )
    # Snap the float noise from cos/sin(90°) to exact values
    a, b, c, d, e, f = (round(v, 9) for v in t.ctm)
    t = Transformation((a, b, c, d, e, f))

    # Clip to the old crop box (in the old coordinates, after the matrix), so
    # content that was cropped away doesn't show up in the new margins.
    contents = page.get_contents()
    if contents is not None:
        clip: list[tuple[list[FloatObject], bytes]] = [
            ([FloatObject(x0), FloatObject(y0), FloatObject(x1 - x0), FloatObject(y1 - y0)], b"re"),
            ([], b"W"),
            ([], b"n"),
        ]
        contents.operations = clip + contents.operations
        page.replace_contents(contents)

    page.rotation = 0
    page.add_transformation(t)
    transform_annotations(page, t)

    new_box = RectangleObject([0, 0, tw, th])
    page.mediabox = new_box
    page.cropbox = new_box
    page.trimbox = new_box
    page.bleedbox = new_box
    page.artbox = new_box

    note = f"{w/72*25.4:.0f}x{h/72*25.4:.0f} mm -> A4, scale {s:.3f}"
    if rotation:
        note += f", rotation {rotation}° baked in"
    return note, t


# --- Small typed helpers: pypdf types the contents of PDF dicts/arrays as Any


def resolve(v: object) -> object:
    """Follow an indirect reference (e.g. "12 0 R") to the actual object."""
    return cast(object, v.get_object()) if isinstance(v, PdfObject) else v


def get(d: DictionaryObject, key: str) -> object:
    return cast(object, d.get(key))


def as_dict(v: object) -> DictionaryObject | None:
    v = resolve(v)
    return v if isinstance(v, DictionaryObject) else None


def as_list(v: object) -> list[object]:
    v = resolve(v)
    return list(cast(list[object], v)) if isinstance(v, ArrayObject) else []


def as_float(v: object) -> float | None:
    v = resolve(v)
    return float(v) if isinstance(v, (int, float)) else None


def floats(v: object) -> list[float]:
    return [f for f in map(as_float, as_list(v)) if f is not None]


def map_points(t: Transformation, values: list[float]) -> list[FloatObject]:
    """Transform a flat [x1, y1, x2, y2, ...] list."""
    out: list[FloatObject] = []
    for i in range(0, len(values) - 1, 2):
        x, y = t.apply_on((values[i], values[i + 1]))
        out += [FloatObject(x), FloatObject(y)]
    return out


def transform_annotations(page: PageObject, t: Transformation) -> None:
    """Move links, form fields, comments etc. along with the page content."""
    for ref in as_list(get(page, "/Annots")):
        annot = as_dict(ref)
        if annot is None:
            continue
        rect = floats(get(annot, "/Rect"))
        if len(rect) == 4:
            xa, ya, xb, yb = map_points(t, rect)
            annot[NameObject("/Rect")] = RectangleObject(
                [min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb)]
            )
        # Point lists used by link highlights, ink, lines, polygons, callouts
        for key in ("/QuadPoints", "/Vertices", "/L", "/CL"):
            if key in annot:
                annot[NameObject(key)] = ArrayObject(map_points(t, floats(get(annot, key))))
        if "/InkList" in annot:
            annot[NameObject("/InkList")] = ArrayObject(
                ArrayObject(map_points(t, floats(stroke)))
                for stroke in as_list(get(annot, "/InkList"))
            )


def to_pdf(v: float | None) -> PdfObject:
    return NullObject() if v is None else FloatObject(v)


def transform_destination(dest: ArrayObject, t: Transformation) -> None:
    """Fix the coordinates in a destination like [page /XYZ left top zoom]."""
    items = as_list(dest)
    kind = str(items[1]) if len(items) > 1 else ""
    args = [as_float(v) for v in items[2:]]
    # With a 90°/270° rotation baked in, x and y swap roles
    swapped = abs(t.ctm[0]) < 1e-9

    def pt(x: float | None, y: float | None) -> tuple[float | None, float | None]:
        nx, ny = t.apply_on((x or 0.0, y or 0.0))
        if swapped:
            return (nx if y is not None else None), (ny if x is not None else None)
        return (nx if x is not None else None), (ny if y is not None else None)

    new_args: list[float | None]
    if kind == "/XYZ" and len(args) >= 2:
        left, top = pt(args[0], args[1])
        new_args = [left, top, *args[2:]]
    elif kind in ("/FitH", "/FitBH") and args:
        left, top = pt(None, args[0])
        kind, new_args = (kind.replace("H", "V"), [left]) if swapped else (kind, [top])
    elif kind in ("/FitV", "/FitBV") and args:
        left, top = pt(args[0], None)
        kind, new_args = (kind.replace("V", "H"), [top]) if swapped else (kind, [left])
    elif kind == "/FitR" and len(args) == 4 and None not in args:
        xa, ya, xb, yb = map_points(t, [v or 0.0 for v in args])
        new_args = [min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb)]
    else:
        return  # /Fit, /FitB: no coordinates
    dest[1:] = [NameObject(kind), *(to_pdf(v) for v in new_args)]


def fix_destinations(writer: PdfWriter, transforms: dict[int, Transformation]) -> None:
    """Make bookmarks, named destinations and internal links point at the
    right spot on pages that were scaled/moved."""
    seen: set[int] = set()

    def fix(value: object) -> None:
        d = resolve(value)
        if isinstance(d, DictionaryObject):  # {/D [...]} from a name tree
            fix(get(d, "/D"))
            return
        if not isinstance(d, ArrayObject) or not d or id(d) in seen:
            return  # names (str) point into the name tree, fixed there
        seen.add(id(d))
        page_ref = as_list(d)[0]
        if isinstance(page_ref, IndirectObject) and page_ref.idnum in transforms:
            transform_destination(d, transforms[page_ref.idnum])

    def fix_action(value: object) -> None:
        action = as_dict(value)
        if action is not None and get(action, "/S") == "/GoTo":
            fix(get(action, "/D"))

    # Bookmarks (outline tree)
    def walk_outline(item: object) -> None:
        node = as_dict(item)
        while node is not None:
            fix(get(node, "/Dest"))
            fix_action(get(node, "/A"))
            walk_outline(get(node, "/First"))
            node = as_dict(get(node, "/Next"))

    # Named destinations in the /Names /Dests tree
    def walk_names(item: object) -> None:
        node = as_dict(item)
        if node is None:
            return
        for v in as_list(get(node, "/Names"))[1::2]:
            fix(v)
        for kid in as_list(get(node, "/Kids")):
            walk_names(kid)

    root = writer.root_object
    outlines = as_dict(get(root, "/Outlines"))
    if outlines is not None:
        walk_outline(get(outlines, "/First"))

    old_dests = as_dict(get(root, "/Dests"))  # older style named destinations
    if old_dests is not None:
        for v in cast(list[object], list(old_dests.values())):
            fix(v)

    names_root = as_dict(get(root, "/Names"))
    if names_root is not None:
        walk_names(get(names_root, "/Dests"))

    # Internal links on every page
    for page in writer.pages:
        for ref in as_list(get(page, "/Annots")):
            annot = as_dict(ref)
            if annot is not None:
                fix(get(annot, "/Dest"))
                fix_action(get(annot, "/A"))


def main(argv: list[str]) -> None:
    allow_upscale = "--no-upscale" not in argv
    if [a for a in argv if a != "--no-upscale"]:
        print(__doc__)
        sys.exit(1)

    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    inputs = sorted(
        (p for p in INPUT_DIR.iterdir() if p.suffix.lower() == ".pdf"),
        key=lambda p: p.name.lower(),
    )
    if not inputs:
        print(f"No PDFs found in {INPUT_DIR}")
        sys.exit(1)
    stamp = f"{datetime.now():%Y-%m-%d_%H-%M-%S}"
    out_path = OUTPUT_DIR / f"{stamp}.pdf"
    n = 2
    while out_path.exists():  # two runs within the same second
        out_path = OUTPUT_DIR / f"{stamp}_{n}.pdf"
        n += 1

    writer = PdfWriter()
    transforms: dict[int, Transformation] = {}
    for path in inputs:
        start = len(writer.pages)
        # append() keeps bookmarks, links and form fields, wired to the new pages
        writer.append(PdfReader(path))
        for i, page in enumerate(writer.pages[start:], 1):
            note, t = fit_to_a4(page, allow_upscale)
            if t is not None and page.indirect_reference is not None:
                transforms[page.indirect_reference.idnum] = t
            print(f"{path.name} p{i}: {note}")

    fix_destinations(writer, transforms)

    # Lossless clean-up only: merge identical objects (e.g. the same logo/font
    # used in several input files) and Flate-compress content streams.
    writer.compress_identical_objects()
    for page in writer.pages:
        page.compress_content_streams()

    with open(out_path, "wb") as f:
        _ = writer.write(f)
    print(f"\nWrote {out_path.relative_to(HERE)} ({len(writer.pages)} pages)")


if __name__ == "__main__":
    main(sys.argv[1:])
