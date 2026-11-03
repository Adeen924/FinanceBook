"""
Tiny synthetic PDFs that mimic the structure of real receipt producers, built
with the stdlib only. All names, addresses and numbers are made up.

    skia_cid_pdf()      Chrome "Save as PDF" (Skia): Type0/Identity-H fonts with a
                        ToUnicode CMap, a y-flipped CTM and one Td per glyph.
    winansi_pdf()       DigiKey-style invoice: WinAnsi TrueType with /Widths,
                        words split into fragments, a rotated label, an orphan
                        content stream that is not on any page, two pages.
    objstm_pdf()        PDF 1.5 with the page tree and font in an object stream
                        and an xref stream.
    image_pdf(jpeg)     jsPDF-style page that is one DCT (JPEG) image, no text.
    encrypted_pdf()     a trailer with /Encrypt.
"""
import zlib

# Glyph ids for the fake CID font: one per character we draw.
_CHARS = (" $#&'(),-.0123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ"
          "abcdefghijklmnopqrstuvwxyz’•ﬁ©")
GID = {ch: i + 3 for i, ch in enumerate(_CHARS)}


def build(objects: dict, root: int, info: int | None = None, xref_stream: bool = False,
          extra_trailer: bytes = b"") -> bytes:
    """objects: {num: bytes body (without 'obj'/'endobj')} → a PDF file."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objects[num] + b"\nendobj\n"
    size = max(objects) + 1
    if xref_stream:
        xnum = size
        size += 1
        offsets[xnum] = len(out)
        rows = bytearray()
        for n in range(size):
            if n in offsets:
                rows += b"\x01" + offsets[n].to_bytes(4, "big") + b"\x00"
            else:
                rows += b"\x00\x00\x00\x00\x00\x00"
        data = zlib.compress(bytes(rows))
        out += (f"{xnum} 0 obj\n<< /Type /XRef /Size {size} /W [1 4 1] /Root {root} 0 R "
                f"/Filter /FlateDecode /Length {len(data)} >>\nstream\n").encode()
        out += data + b"\nendstream\nendobj\n"
        out += f"startxref\n{offsets[xnum]}\n%%EOF\n".encode()
        return bytes(out)
    xref = len(out)
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for n in range(1, size):
        out += (f"{offsets[n]:010d} 00000 n \n" if n in offsets else "0000000000 65535 f \n").encode()
    trailer = f"<< /Size {size} /Root {root} 0 R".encode()
    if info:
        trailer += f" /Info {info} 0 R".encode()
    out += b"trailer\n" + trailer + extra_trailer + b" >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"
    return bytes(out)


def stream(data: bytes, extra: str = "", compress: bool = True) -> bytes:
    if compress:
        data = zlib.compress(data)
        extra += " /Filter /FlateDecode"
    return f"<< /Length {len(data)}{extra} >>\nstream\n".encode() + data + b"\nendstream"


def _to_unicode() -> bytes:
    pairs = [(GID[ch], ch) for ch in _CHARS]
    # Digits as a bfrange (consecutive ids), everything else as bfchar.
    digits = [p for p in pairs if p[1].isdigit()]
    rest = [p for p in pairs if not p[1].isdigit()]
    lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
             "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange",
             "1 beginbfrange",
             f"<{digits[0][0]:04X}> <{digits[-1][0]:04X}> <{ord('0'):04X}>", "endbfrange",
             f"{len(rest)} beginbfchar"]
    for gid, ch in rest:
        lines.append(f"<{gid:04X}> <{''.join(f'{b:02X}' for b in ch.encode('utf-16-be'))}>")
    lines += ["endbfchar", "endcmap", "end", "end"]
    return "\n".join(lines).encode()


def _skia_text(x: float, y: float, text: str, size: float = 14, font: str = "/F5") -> bytes:
    """One glyph per Td like Skia; y is measured from the top (the CTM flips it)."""
    adv = size * 0.55
    ops = [f"BT\n{font} {size:g} Tf\n1 0 0 -1 {x:g} {y:g} Tm"]
    first = True
    for ch in text:
        if not first:
            ops.append(f"{adv:g} 0 Td")
        ops.append(f"<{GID[ch]:04X}> Tj")
        first = False
    ops.append("ET")
    return "\n".join(ops).encode()


AMAZON_LIKE_LINES = [
    # (x, y-from-top, text) in the 1/0.24-scaled space Skia uses
    (40, 61, "Order Details"),
    (40, 120, "Order placed November 2, 2026"),
    (330, 120, "Order # 112-0000000-0000001"),
    (59, 200, "Jordan Sample"),
    (900, 200, "Item(s) Subtotal:"),
    (1250, 200, "$12.50"),
    (900, 230, "Shipping & Handling:"),
    (1250, 230, "$0.00"),
    (900, 260, "Total before tax:"),
    (1250, 260, "$12.50"),
    (900, 290, "Estimated tax to be"),
    (1250, 290, "$0.97"),
    (900, 310, "collected:"),
    (900, 340, "Grand Total:"),
    (1250, 340, "$13.47"),
    (59, 420, "Arriving today"),
    (121, 470, "Acme Widget Holder, 2 Pack ’Blue’ ﬁts all"),
    (121, 500, "Desk Organizer"),
    (147, 540, "Sold by: Example Seller"),
    (147, 570, "$12.50"),
    (59, 640, "Back to top"),
    (300, 700, "© 1996-2026, Amazon.com, Inc. or its affiliates"),
]


def skia_cid_pdf(lines=AMAZON_LIKE_LINES) -> bytes:
    body = b".24 0 0 -.24 0 792 cm\nq\n" + b"\n".join(_skia_text(*ln) for ln in lines) + b"\nQ"
    widths = " ".join("550" for _ in _CHARS)
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
           b"/Resources << /Font << /F5 5 0 R >> >> >>",
        4: stream(body),
        5: b"<< /Type /Font /Subtype /Type0 /BaseFont /AAAAAA+FakeSans /Encoding /Identity-H "
           b"/DescendantFonts [6 0 R] /ToUnicode 7 0 R >>",
        6: f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /AAAAAA+FakeSans /DW 1000 "
           f"/W [3 [{widths}]] /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) "
           f"/Supplement 0 >> >>".encode(),
        7: stream(_to_unicode()),
    }
    return build(objs, 1)


def _frag(text: str, widths: dict, size: float) -> float:
    return sum(widths.get(ord(c), 500) for c in text) / 1000.0 * size


def winansi_pdf() -> bytes:
    """DigiKey-like: fragments with Td, a rotated 'Bill To', orphan stream, 2 pages."""
    widths = {c: 600 for c in range(32, 256)}          # monospaced: easy, exact gaps

    def row(x, y, parts, size=8.0):
        out = [f"BT\n{x:.3f} {y:.1f} Td\n/F1 {size:.3f} Tf"]
        first = True
        for p in parts:
            if not first:
                out.append(f"{adv:.3f} 0.0 Td")
            esc = p.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            out.append(f"({esc}) Tj")
            adv = _frag(p, widths, size)
            first = False
        out.append("ET")
        return "\n".join(out)

    page1 = "\n".join([
        row(600, 573.1, ["Invoi", "ce ", "# ", "90000001"], 10),
        "BT\n31.2 517.2 Td\n0 1 -1 0 31.2 517.2 Tm\n/F1 8 Tf\n(Bill To:) Tj\nET",
        row(40, 400, ["Order Date:"]), row(200, 400, ["Invoice Date:"]),
        row(40, 390, ["01-Feb-2026"]), row(200, 390, ["03-Feb-2026"]),
        row(30, 300, ["1"]), row(60, 300, ["2"]), row(90, 300, ["0"]), row(120, 300, ["2"]),
        row(150, 300, ["PART: ", "123-FAKE01-ND"]), row(330, 300, ["DESC: ", "TEST WIDGET 5V"]),
        row(560, 300, ["4.25000"]), row(640, 300, ["8.50", " T"]),
        row(560, 250, ["Sales ", "Amount"]), row(680, 250, ["8.50"]),
        row(520, 235, ["Shipping ", "charges ", "applied"]), row(680, 235, ["6.99"]),
        row(520, 220, ["** Charges ", "subtotal **"]), row(680, 220, ["15.49"]),
        row(560, 205, ["Sales ", "Tax"]), row(680, 205, ["1.20"]),
        row(520, 190, ["Total ", "charged ", "to ", "credit ", "card"]), row(680, 190, ["16.69"]),
        row(40, 100, ["www.", "digikey", ".com"]),
    ]).encode()
    page2 = row(40, 500, ["Page ", "2 of 2"]).encode()
    orphan = row(40, 500, ["ORPHAN ", "TEXT ", "MUST ", "NOT ", "APPEAR"]).encode()
    w = " ".join(str(widths[c]) for c in range(32, 256))
    objs = {
        1: b"<< /Type /Pages /Kids [ 37 0 R 42 0 R ] /Count 2 >>",
        3: b"<< /Type /Catalog /Pages 1 0 R >>",
        5: f"<< /Type /Font /Subtype /TrueType /BaseFont /AAAAAA+LiberationMono "
           f"/FirstChar 32 /LastChar 255 /Widths 7 0 R /Encoding /WinAnsiEncoding >>".encode(),
        7: f"[{w}]".encode(),
        20: stream(orphan),
        36: stream(page1),
        37: b"<< /Type /Page /Parent 1 0 R /MediaBox [0 0 792 612] /Contents 36 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
        40: stream(page2),
        42: b"<< /Type /Page /Parent 1 0 R /MediaBox [0 0 792 612] /Contents [40 0 R] "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
    }
    return build(objs, 3)


def objstm_pdf() -> bytes:
    """Catalog, pages and font inside a compressed object stream + an xref stream."""
    content = stream(b"BT /F1 12 Tf 72 720 Td (Corner Hardware) Tj 0 -20 Td "
                     b"(Hammer    19.99) Tj 0 -20 Td (Total    19.99) Tj ET")
    inner = {1: b"<< /Type /Catalog /Pages 2 0 R >>",
             2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
             3: b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
             5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    header, body = [], b""
    for num, obj in inner.items():
        header.append(f"{num} {len(body)}")
        body += obj + b"\n"
    head = " ".join(header).encode() + b"\n"
    objstm = zlib.compress(head + body)
    objs = {4: content,
            6: f"<< /Type /ObjStm /N {len(inner)} /First {len(head)} /Filter /FlateDecode "
               f"/Length {len(objstm)} >>\nstream\n".encode() + objstm + b"\nendstream"}
    return build(objs, 1, xref_stream=True)


def image_pdf(jpeg: bytes, width: int = 280, height: int = 236) -> bytes:
    """One page drawn entirely by a JPEG image (like jsPDF output)."""
    content = f"q {595.28:.2f} 0 0 {841.89:.2f} 0 0 cm /I0 Do Q".encode()
    objs = {
        1: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        2: b"<< /ProcSet [/PDF /Text /ImageC] /Font << /F1 5 0 R >> /XObject << /I0 6 0 R >> >>",
        3: b"<< /Type /Page /Parent 1 0 R /Resources 2 0 R /MediaBox [0 0 595.28 841.89] "
           b"/Contents 4 0 R >>",
        4: stream(content, compress=False),
        5: b"<< /Type /Font /BaseFont /Helvetica /Subtype /Type1 /Encoding /WinAnsiEncoding >>",
        6: f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
           f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length {len(jpeg)} "
           f"/Filter /DCTDecode >>\nstream\n".encode() + jpeg + b"\nendstream",
        7: b"<< /Type /Catalog /Pages 1 0 R >>",
    }
    return build(objs, 7)


def encrypted_pdf() -> bytes:
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
            4: stream(b"\x8a\x13\x99garbled"),
            5: b"<< /Filter /Standard /V 2 /R 3 /O <00> /U <00> /P -4 >>"}
    return build(objs, 1, extra_trailer=b" /Encrypt 5 0 R")


# A minimal valid baseline JPEG (8x8 grey), for image-only detection tests.
TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c"
    "1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b"
    "080008000801011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5"
    "100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c115"
    "52d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a6364"
    "65666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7"
    "b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda000801"
    "0100003f00fbd3ffd9")
