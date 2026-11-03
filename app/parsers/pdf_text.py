"""
Stdlib-only PDF reader for receipts: enough of the PDF format to pull
positioned text out of the page content streams and rebuild it as lines, plus
the embedded JPEGs of image-only (scanned / "printed to image") pages.

    doc = read_pdf(data)        # raises PDFEncrypted / PDFError
    doc.text                    # lines rebuilt from glyph positions
    doc.images                  # [(jpeg_bytes, width, height)] drawn on pages
    doc.pages                   # page count

What it understands: indirect objects (incl. object streams, so xref-stream
files work), Flate/LZW/ASCIIHex/ASCII85 filters with PNG predictors, the page
tree with inherited resources, form XObjects, the text operators (Tj TJ ' "
Td TD Tm T* Tc Tw Tz TL Ts Tf), the CTM (cm q Q), simple fonts (WinAnsi /
MacRoman / Standard + /Differences, /Widths) and composite Type0 fonts
(Identity-H/V or an embedded CMap, /W widths) with /ToUnicode CMaps.

Text is rebuilt like `pdftotext -layout`: glyphs are grouped into lines by
baseline, ordered by x, and wide gaps become runs of spaces, so a table row
("Sales Amount      33.68") stays on one line.
"""
import re
import zlib

MAX_PAGES = 30           # receipts are short; this bounds work on huge files
MAX_GLYPHS = 400_000


class PDFError(Exception):
    """The file isn't a PDF we can read."""


class PDFEncrypted(PDFError):
    """The PDF is password-protected / encrypted."""


class Name(str):
    """A PDF /Name (a str, so dict lookups by plain strings work)."""


class Ref(tuple):
    """An indirect reference (num, gen)."""


class Stream:
    __slots__ = ("dict", "raw")

    def __init__(self, d, raw):
        self.dict, self.raw = d, raw


class _Op(str):
    """A bare keyword in a content stream (an operator)."""


# ── lexer / object parser ────────────────────────────────────────────────────

_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"
_NUM = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")
_REGULAR = re.compile(rb"[^ \t\r\n\f\x00()<>\[\]{}/%]+")
_ESC = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12,
        ord("("): 40, ord(")"): 41, ord("\\"): 92}


class _Lexer:
    def __init__(self, data: bytes, pos: int = 0):
        self.data, self.pos = data, pos

    def skip_ws(self):
        d, n = self.data, len(self.data)
        while self.pos < n:
            c = d[self.pos]
            if c in _WS:
                self.pos += 1
            elif c == 0x25:                                   # % comment
                e = d.find(b"\n", self.pos)
                self.pos = n if e < 0 else e + 1
            else:
                break

    def token(self):
        """Next token: a value, or ('<<'|'>>'|'['|']'), or None at EOF."""
        self.skip_ws()
        d = self.data
        if self.pos >= len(d):
            return None
        c = d[self.pos]
        if c == 0x2F:                                         # /Name
            m = _REGULAR.match(d, self.pos + 1)
            raw = m.group(0) if m else b""
            self.pos += 1 + len(raw)
            raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda x: bytes([int(x.group(1), 16)]), raw)
            return Name(raw.decode("latin-1"))
        if c == 0x28:                                         # (string)
            return self._literal()
        if c == 0x3C:
            if d[self.pos + 1:self.pos + 2] == b"<":
                self.pos += 2
                return "<<"
            e = d.find(b">", self.pos)
            if e < 0:
                e = len(d)
            hx = re.sub(rb"[^0-9A-Fa-f]", b"", d[self.pos + 1:e])
            self.pos = e + 1
            if len(hx) % 2:
                hx += b"0"
            return bytes.fromhex(hx.decode("ascii"))
        if c == 0x3E:
            self.pos += 2 if d[self.pos + 1:self.pos + 2] == b">" else 1
            return ">>"
        if c in (0x5B, 0x5D, 0x7B, 0x7D):
            self.pos += 1
            return {0x5B: "[", 0x5D: "]", 0x7B: "{", 0x7D: "}"}[c]
        m = _NUM.match(d, self.pos)
        if m and (m.end() >= len(d) or d[m.end()] in _WS or d[m.end()] in _DELIM):
            self.pos = m.end()
            s = m.group(0)
            return float(s) if b"." in s else int(s)
        m = _REGULAR.match(d, self.pos)
        if not m:
            self.pos += 1
            return _Op("")
        self.pos = m.end()
        w = m.group(0)
        if w == b"true":
            return True
        if w == b"false":
            return False
        if w == b"null":
            return None
        return _Op(w.decode("latin-1"))

    def _literal(self) -> bytes:
        d, i, depth, out = self.data, self.pos + 1, 1, bytearray()
        n = len(d)
        while i < n:
            c = d[i]
            if c == 0x5C and i + 1 < n:
                nx = d[i + 1]
                if nx in _ESC:
                    out.append(_ESC[nx]); i += 2; continue
                if 0x30 <= nx <= 0x37:
                    m = re.match(rb"[0-7]{1,3}", d[i + 1:i + 4])
                    out.append(int(m.group(0), 8) & 0xFF); i += 1 + len(m.group(0)); continue
                if nx in (0x0D, 0x0A):                        # line continuation
                    i += 2
                    if nx == 0x0D and i < n and d[i] == 0x0A:
                        i += 1
                    continue
                out.append(nx); i += 2; continue
            if c == 0x28:
                depth += 1
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            out.append(c)
            i += 1
        self.pos = i
        return bytes(out)

    def value(self, tok=Ellipsis):
        """Parse one complete object (dicts/arrays/refs resolved structurally)."""
        if tok is Ellipsis:
            tok = self.token()
        if tok == "<<":
            out = {}
            while True:
                k = self.token()
                if k is None or k == ">>":
                    return out
                if not isinstance(k, Name):
                    continue
                out[str(k)] = self.value()
        if tok == "[":
            out = []
            while True:
                t = self.token()
                if t is None or t == "]":
                    return out
                out.append(self.value(t))
        if isinstance(tok, int) and not isinstance(tok, bool):
            save = self.pos
            t2 = self.token()
            if isinstance(t2, int) and not isinstance(t2, bool):
                save2 = self.pos
                t3 = self.token()
                if t3 == "R":
                    return Ref((tok, t2))
                self.pos = save2
            self.pos = save
        return tok


# ── filters ──────────────────────────────────────────────────────────────────

def _png_unpredict(data: bytes, columns: int, colors: int = 1, bpc: int = 8) -> bytes:
    bpp = max(1, colors * bpc // 8)
    row_len = (columns * colors * bpc + 7) // 8
    out, prev = bytearray(), bytearray(row_len)
    i = 0
    while i + row_len < len(data) + 1 and i < len(data):
        ft = data[i]
        row = bytearray(data[i + 1:i + 1 + row_len])
        row.extend(b"\0" * (row_len - len(row)))
        for x in range(row_len):
            a = row[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if ft == 1:
                row[x] = (row[x] + a) & 0xFF
            elif ft == 2:
                row[x] = (row[x] + b) & 0xFF
            elif ft == 3:
                row[x] = (row[x] + ((a + b) >> 1)) & 0xFF
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[x] = (row[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        out.extend(row)
        prev = row
        i += 1 + row_len
    return bytes(out)


def _lzw(data: bytes, early: int = 1) -> bytes:
    out = bytearray()
    table = [bytes([i]) for i in range(256)] + [b"", b""]
    bits, buf, nbits, prev = 9, 0, 0, None
    for byte in data:
        buf = (buf << 8) | byte
        nbits += 8
        while nbits >= bits:
            nbits -= bits
            code = (buf >> nbits) & ((1 << bits) - 1)
            if code == 256:
                table = table[:258]
                bits, prev = 9, None
                continue
            if code == 257:
                return bytes(out)
            if prev is None:
                entry = table[code]
            elif code < len(table):
                entry = table[code]
                table.append(prev + entry[:1])
            else:
                entry = prev + prev[:1]
                table.append(entry)
            out.extend(entry)
            prev = entry
            if len(table) + early >= (1 << bits) and bits < 12:
                bits += 1
    return bytes(out)


def _a85(data: bytes) -> bytes:
    import base64
    data = re.sub(rb"\s", b"", data)
    if data.startswith(b"<~"):
        data = data[2:]
    end = data.find(b"~>")
    if end >= 0:
        data = data[:end]
    return base64.a85decode(data)


def _inflate(data: bytes) -> bytes:
    try:
        return zlib.decompress(data)
    except zlib.error:
        # Truncated / trailing-garbage streams are common; take what inflates.
        return zlib.decompressobj().decompress(data)


def decode_stream(doc, s: "Stream") -> tuple[bytes, str]:
    """(decoded bytes, image filter left undecoded or '')."""
    filters = doc.resolve(s.dict.get("Filter"))
    parms = doc.resolve(s.dict.get("DecodeParms") or s.dict.get("DP"))
    if not isinstance(filters, list):
        filters = [filters] if filters else []
    if not isinstance(parms, list):
        parms = [parms] * len(filters)
    data = s.raw
    for f, p in zip(filters, parms + [None] * len(filters)):
        f = str(doc.resolve(f))
        p = doc.resolve(p) or {}
        if f in ("FlateDecode", "Fl"):
            data = _inflate(data)
        elif f in ("LZWDecode", "LZW"):
            data = _lzw(data, int(doc.resolve(p.get("EarlyChange", 1)) if isinstance(p, dict) else 1))
        elif f in ("ASCIIHexDecode", "AHx"):
            hx = re.sub(rb"[^0-9A-Fa-f]", b"", data.split(b">")[0])
            data = bytes.fromhex((hx + b"0" * (len(hx) % 2)).decode())
        elif f in ("ASCII85Decode", "A85"):
            data = _a85(data)
        elif f in ("DCTDecode", "DCT", "JPXDecode", "CCITTFaxDecode", "CCF", "JBIG2Decode"):
            return data, f
        else:
            raise PDFError(f"Unsupported filter {f}")
        if isinstance(p, dict) and int(doc.resolve(p.get("Predictor", 1)) or 1) >= 10:
            data = _png_unpredict(data, int(doc.resolve(p.get("Columns", 1)) or 1),
                                  int(doc.resolve(p.get("Colors", 1)) or 1),
                                  int(doc.resolve(p.get("BitsPerComponent", 8)) or 8))
    return data, ""


# ── document ─────────────────────────────────────────────────────────────────

_OBJ_HDR = re.compile(rb"(?<![0-9])(\d{1,10})\s+(\d{1,5})\s+obj\b")


class _Doc:
    def __init__(self, data: bytes):
        if b"%PDF" not in data[:1024]:
            raise PDFError("Not a PDF file")
        self.data = data
        self.offsets: dict[int, int] = {}
        self.cache: dict[int, object] = {}
        self.trailers: list[dict] = []
        self._scan()

    def _scan(self):
        # Walk the file object by object, jumping over stream bodies so a
        # "1 0 obj" inside compressed data can't be mistaken for an object.
        d, pos = self.data, 0
        while True:
            m = _OBJ_HDR.search(d, pos)
            if not m:
                break
            num = int(m.group(1))
            self.offsets[num] = m.end()                      # later = newer
            pos = m.end()
            s = d.find(b"stream", pos, pos + 4096)
            e = d.find(b"endobj", pos)
            if s >= 0 and (e < 0 or s < e):
                e2 = d.find(b"endstream", s)
                pos = e2 + 9 if e2 >= 0 else s + 6
        for m in re.finditer(rb"trailer\s*<<", d):
            t = _Lexer(d, m.end() - 2).value()
            if isinstance(t, dict):
                self.trailers.append(t)
        objstms = []
        for num in list(self.offsets):
            o = self.get(num)
            if isinstance(o, Stream):
                t = o.dict.get("Type")
                if t == "XRef":
                    self.trailers.append(o.dict)
                elif t == "ObjStm":
                    objstms.append(o)
        for st in objstms:
            self._load_objstm(st)
        if any("Encrypt" in t for t in self.trailers):
            raise PDFEncrypted("The PDF is encrypted")

    def _load_objstm(self, st: Stream):
        try:
            data, _ = decode_stream(self, st)
        except Exception:
            return
        n = int(self.resolve(st.dict.get("N", 0)) or 0)
        first = int(self.resolve(st.dict.get("First", 0)) or 0)
        lx = _Lexer(data)
        pairs = []
        for _ in range(n):
            a, b = lx.token(), lx.token()
            if not isinstance(a, int) or not isinstance(b, int):
                break
            pairs.append((a, b))
        for num, off in pairs:
            if num in self.offsets or num in self.cache:
                continue                                     # a direct object wins
            try:
                self.cache[num] = _Lexer(data, first + off).value()
            except Exception:
                continue

    def get(self, num: int):
        if num in self.cache:
            return self.cache[num]
        off = self.offsets.get(num)
        if off is None:
            return None
        lx = _Lexer(self.data, off)
        val = lx.value()
        if isinstance(val, dict):
            save = lx.pos
            if lx.token() == "stream":
                p = lx.pos
                if self.data[p:p + 2] == b"\r\n":
                    p += 2
                elif self.data[p:p + 1] in (b"\n", b"\r"):
                    p += 1
                length = val.get("Length")
                if isinstance(length, Ref):
                    length = self.get(length[0]) if length[0] != num else None
                raw = None
                if isinstance(length, int) and 0 <= length and \
                        self.data[p + length:p + length + 30].lstrip().startswith(b"endstream"):
                    raw = self.data[p:p + length]
                if raw is None:
                    e = self.data.find(b"endstream", p)
                    raw = self.data[p:e if e >= 0 else len(self.data)]
                    raw = raw[:-2] if raw.endswith(b"\r\n") else raw.rstrip(b"\r\n") if \
                        raw.endswith((b"\n", b"\r")) else raw
                val = Stream(val, raw)
            else:
                lx.pos = save
        self.cache[num] = val
        return val

    def resolve(self, v, depth: int = 0):
        while isinstance(v, Ref) and depth < 32:
            v = self.get(v[0])
            depth += 1
        return v

    def root(self) -> dict:
        for t in reversed(self.trailers):
            r = self.resolve(t.get("Root"))
            if isinstance(r, dict):
                return r
        for num in self.offsets:
            o = self.get(num)
            if isinstance(o, dict) and o.get("Type") == "Catalog":
                return o
        return {}

    def pages(self) -> list[tuple[dict, dict]]:
        """[(page dict, inherited resources)] in reading order."""
        out, seen = [], set()

        def walk(node, res, depth):
            node = self.resolve(node)
            if not isinstance(node, dict) or id(node) in seen or depth > 40:
                return
            seen.add(id(node))
            r = self.resolve(node.get("Resources"))
            res = r if isinstance(r, dict) else res
            kids = self.resolve(node.get("Kids"))
            if isinstance(kids, list) and node.get("Type") != "Page":
                for k in kids:
                    walk(k, res, depth + 1)
            elif node.get("Type") == "Page" or "Contents" in node:
                out.append((node, res or {}))

        walk(self.root().get("Pages"), {}, 0)
        if not out:                       # broken page tree: take every Page object
            for num in sorted(self.offsets):
                o = self.get(num)
                if isinstance(o, dict) and o.get("Type") == "Page":
                    r = self.resolve(o.get("Resources"))
                    out.append((o, r if isinstance(r, dict) else {}))
        return out


# ── fonts ────────────────────────────────────────────────────────────────────

# Helvetica advance widths for ASCII 32..126 (also used for the other
# standard-14 proportional fonts, which only affects gap detection a little).
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
         556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
         1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
         667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
         333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
         556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]

_GLYPHS = {
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#", "dollar": "$",
    "percent": "%", "ampersand": "&", "quotesingle": "'", "quoteright": "’",
    "parenleft": "(", "parenright": ")", "asterisk": "*", "plus": "+", "comma": ",",
    "hyphen": "-", "minus": "-", "period": ".", "slash": "/", "colon": ":", "semicolon": ";",
    "less": "<", "equal": "=", "greater": ">", "question": "?", "at": "@",
    "bracketleft": "[", "backslash": "\\", "bracketright": "]", "asciicircum": "^",
    "underscore": "_", "grave": "`", "quoteleft": "‘", "braceleft": "{", "bar": "|",
    "braceright": "}", "asciitilde": "~", "bullet": "•", "endash": "–", "emdash": "—",
    "quotedblleft": "“", "quotedblright": "”", "ellipsis": "…", "Euro": "€",
    "sterling": "£", "cent": "¢", "yen": "¥", "section": "§", "copyright": "©",
    "registered": "®", "degree": "°", "trademark": "™", "multiply": "×", "fi": "fi",
    "fl": "fl", "ff": "ff", "ffi": "ffi", "ffl": "ffl", "nbspace": " ", "sfthyphen": "-",
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def _glyph_char(name: str) -> str:
    if name in _GLYPHS:
        return _GLYPHS[name]
    if len(name) == 1:
        return name
    m = re.fullmatch(r"uni([0-9A-Fa-f]{4})+", name)
    if m:
        return "".join(chr(int(name[i:i + 4], 16)) for i in range(3, len(name), 4))
    m = re.fullmatch(r"u([0-9A-Fa-f]{4,6})", name)
    if m:
        return chr(int(m.group(1), 16))
    base = name.split(".")[0].split("_")[0]
    return _GLYPHS.get(base, base if len(base) == 1 else "")


def _utf16(b: bytes) -> str:
    if len(b) % 2:
        b += b"\0"
    try:
        return b.decode("utf-16-be")
    except UnicodeDecodeError:
        return ""


def parse_cmap(data: bytes) -> tuple[dict, list]:
    """A /ToUnicode (or encoding) CMap → ({code bytes: text}, codespace lengths)."""
    mapping: dict[bytes, str] = {}
    lens = set()
    lx = _Lexer(data)
    stack: list = []
    mode = ""
    while True:
        t = lx.token()
        if t is None:
            break
        if t == "[":
            stack.append(lx.value("["))
            continue
        if isinstance(t, _Op):
            if t in ("begincodespacerange", "beginbfchar", "beginbfrange",
                     "begincidchar", "begincidrange"):
                mode = t
                stack = []
            elif t == "endcodespacerange":
                for lo in stack[0::2]:
                    if isinstance(lo, bytes):
                        lens.add(len(lo))
                stack = []
            elif t == "endbfchar":
                for src, dst in zip(stack[0::2], stack[1::2]):
                    if isinstance(src, bytes):
                        mapping[src] = _utf16(dst) if isinstance(dst, bytes) else \
                            _glyph_char(str(dst))
                stack = []
            elif t == "endbfrange":
                for i in range(0, len(stack) - 2, 3):
                    lo, hi, dst = stack[i:i + 3]
                    if not isinstance(lo, bytes) or not isinstance(hi, bytes):
                        continue
                    a, b = int.from_bytes(lo, "big"), int.from_bytes(hi, "big")
                    if b - a > 65535 or b < a:
                        continue
                    for k in range(b - a + 1):
                        code = (a + k).to_bytes(len(lo), "big")
                        if isinstance(dst, list):
                            if k < len(dst) and isinstance(dst[k], bytes):
                                mapping[code] = _utf16(dst[k])
                        elif isinstance(dst, bytes) and dst:
                            v = int.from_bytes(dst, "big") + k
                            mapping[code] = _utf16(v.to_bytes(len(dst), "big"))
                stack = []
            elif t.startswith("end"):
                stack = []
                mode = ""
            continue
        if mode:
            stack.append(t)
    return mapping, sorted(lens)


class _Font:
    def __init__(self, doc: _Doc, fd: dict):
        self.composite = fd.get("Subtype") == "Type0"
        self.to_unicode: dict[bytes, str] = {}
        self.code_lens = [2] if self.composite else [1]
        self.widths: dict[int, float] = {}
        self.default_w = 500.0
        self.vertical = False
        self.base = str(doc.resolve(fd.get("BaseFont")) or "")
        self.encoding: dict[int, str] = {}
        tu = doc.resolve(fd.get("ToUnicode"))
        if isinstance(tu, Stream):
            try:
                self.to_unicode, lens = parse_cmap(decode_stream(doc, tu)[0])
                if self.composite and lens:
                    self.code_lens = lens
            except Exception:
                self.to_unicode = {}
        if self.composite:
            self._init_composite(doc, fd)
        else:
            self._init_simple(doc, fd)

    def _init_composite(self, doc, fd):
        enc = doc.resolve(fd.get("Encoding"))
        if isinstance(enc, Stream):
            try:
                _, lens = parse_cmap(decode_stream(doc, enc)[0])
                if lens:
                    self.code_lens = lens
            except Exception:
                pass
        elif isinstance(enc, str) and enc.endswith("-V"):
            self.vertical = True
        desc = doc.resolve(fd.get("DescendantFonts"))
        d = doc.resolve(desc[0]) if isinstance(desc, list) and desc else {}
        if not isinstance(d, dict):
            return
        self.default_w = float(doc.resolve(d.get("DW", 1000)) or 1000)
        w = doc.resolve(d.get("W")) or []
        i = 0
        while i < len(w):
            a = doc.resolve(w[i])
            nxt = doc.resolve(w[i + 1]) if i + 1 < len(w) else None
            if isinstance(nxt, list):
                for k, v in enumerate(nxt):
                    self.widths[int(a) + k] = float(doc.resolve(v) or 0)
                i += 2
            elif i + 2 < len(w):
                lo, hi, v = int(a), int(nxt), float(doc.resolve(w[i + 2]) or 0)
                for c in range(lo, min(hi, lo + 65535) + 1):
                    self.widths[c] = v
                i += 3
            else:
                break

    def _init_simple(self, doc, fd):
        first = int(doc.resolve(fd.get("FirstChar", 0)) or 0)
        ws = doc.resolve(fd.get("Widths"))
        if isinstance(ws, list):
            for k, v in enumerate(ws):
                self.widths[first + k] = float(doc.resolve(v) or 0)
        base = self.base.split("+")[-1]
        if not self.widths:
            if base.startswith("Courier"):
                self.default_w = 600.0
            else:
                for k, v in enumerate(_HELV):
                    self.widths[32 + k] = float(v)
        else:
            fdesc = doc.resolve(fd.get("FontDescriptor"))
            if isinstance(fdesc, dict):
                self.default_w = float(doc.resolve(fdesc.get("MissingWidth", 0)) or 0) or 500.0
        enc = doc.resolve(fd.get("Encoding"))
        base_enc, diffs = "WinAnsiEncoding", []
        if isinstance(enc, str):
            base_enc = enc
        elif isinstance(enc, dict):
            base_enc = str(doc.resolve(enc.get("BaseEncoding")) or "WinAnsiEncoding")
            diffs = doc.resolve(enc.get("Differences")) or []
        codec = "mac_roman" if base_enc == "MacRomanEncoding" else "cp1252"
        for c in range(256):
            try:
                self.encoding[c] = bytes([c]).decode(codec)
            except UnicodeDecodeError:
                self.encoding[c] = bytes([c]).decode("latin-1")
        if base_enc == "StandardEncoding":
            self.encoding[0x27], self.encoding[0x60] = "’", "‘"
        code = 0
        for item in diffs:
            item = doc.resolve(item)
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                code = int(item)
            elif isinstance(item, Name):
                ch = _glyph_char(str(item))
                if ch and 0 <= code < 256:
                    self.encoding[code] = ch
                code += 1
        if base in ("Symbol", "ZapfDingbats"):
            self.encoding = {}

    def codes(self, s: bytes):
        """Split a shown string into (code bytes, cid int)."""
        if not self.composite:
            for b in s:
                yield bytes([b]), b
            return
        lens = self.code_lens
        i = 0
        while i < len(s):
            step = lens[0]
            if len(lens) > 1:
                for ln in lens:
                    if s[i:i + ln] in self.to_unicode:
                        step = ln
                        break
            code = s[i:i + step]
            i += step
            yield code, int.from_bytes(code, "big")

    def text(self, code: bytes, cid: int) -> str:
        if code in self.to_unicode:
            return self.to_unicode[code]
        if self.composite:
            return ""
        return self.encoding.get(cid, "")

    def width(self, cid: int) -> float:
        return self.widths.get(cid, self.default_w) / 1000.0


# ── content-stream interpreter ───────────────────────────────────────────────

def _mul(m, n):
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + b * c2, a * b2 + b * d2, c * a2 + d * c2, c * b2 + d * d2,
            e * a2 + f * c2 + e2, e * b2 + f * d2 + f2)


_IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


class _Glyph:
    __slots__ = ("x", "y", "w", "size", "ch", "rot", "page")

    def __init__(self, x, y, w, size, ch, rot, page):
        self.x, self.y, self.w, self.size, self.ch, self.rot, self.page = \
            x, y, w, size, ch, rot, page


class _Interp:
    def __init__(self, doc: _Doc):
        self.doc = doc
        self.glyphs: list[_Glyph] = []
        self.images: list[tuple[bytes, int, int, float]] = []
        self._fonts: dict[int, _Font] = {}
        self.page = 0

    def font(self, res: dict, name: str):
        fonts = self.doc.resolve(res.get("Font")) or {}
        ref = fonts.get(name) if isinstance(fonts, dict) else None
        fd = self.doc.resolve(ref)
        if not isinstance(fd, dict):
            return None
        key = id(fd)
        if key not in self._fonts:
            try:
                self._fonts[key] = _Font(self.doc, fd)
            except Exception:
                return None
        return self._fonts[key]

    def run(self, content: bytes, res: dict, ctm=_IDENT, depth: int = 0):
        doc = self.doc
        lx = _Lexer(content)
        ops: list = []
        gs = {"ctm": ctm, "font": None, "size": 0.0, "tc": 0.0, "tw": 0.0, "tz": 1.0,
              "tl": 0.0, "rise": 0.0}
        stack = []
        tm = tlm = _IDENT
        while True:
            t = lx.token()
            if t is None:
                break
            if t in ("<<", "["):
                ops.append(lx.value(t))
                continue
            if not isinstance(t, _Op):
                ops.append(t)
                continue
            op = str(t)
            try:
                if op == "q":
                    stack.append(dict(gs))
                elif op == "Q":
                    if stack:
                        gs = stack.pop()
                elif op == "cm" and len(ops) >= 6:
                    gs["ctm"] = _mul(tuple(float(v) for v in ops[-6:]), gs["ctm"])
                elif op == "BT":
                    tm = tlm = _IDENT
                elif op == "Tf" and len(ops) >= 2:
                    gs["font"] = self.font(res, str(ops[-2]))
                    gs["size"] = float(ops[-1])
                elif op == "Tc" and ops:
                    gs["tc"] = float(ops[-1])
                elif op == "Tw" and ops:
                    gs["tw"] = float(ops[-1])
                elif op == "Tz" and ops:
                    gs["tz"] = float(ops[-1]) / 100.0
                elif op == "TL" and ops:
                    gs["tl"] = float(ops[-1])
                elif op == "Ts" and ops:
                    gs["rise"] = float(ops[-1])
                elif op in ("Td", "TD") and len(ops) >= 2:
                    tx, ty = float(ops[-2]), float(ops[-1])
                    if op == "TD":
                        gs["tl"] = -ty
                    tlm = _mul((1, 0, 0, 1, tx, ty), tlm)
                    tm = tlm
                elif op == "Tm" and len(ops) >= 6:
                    tlm = tm = tuple(float(v) for v in ops[-6:])
                elif op == "T*":
                    tlm = _mul((1, 0, 0, 1, 0, -gs["tl"]), tlm)
                    tm = tlm
                elif op in ("Tj", "'", '"', "TJ") and ops:
                    if op in ("'", '"'):
                        if op == '"' and len(ops) >= 3:
                            gs["tw"], gs["tc"] = float(ops[-3]), float(ops[-2])
                        tlm = _mul((1, 0, 0, 1, 0, -gs["tl"]), tlm)
                        tm = tlm
                    arg = ops[-1]
                    items = arg if (op == "TJ" and isinstance(arg, list)) else [arg]
                    for it in items:
                        if isinstance(it, bytes):
                            tm = self._show(it, gs, tm)
                        elif isinstance(it, (int, float)) and not isinstance(it, bool):
                            tx = -float(it) / 1000.0 * gs["size"] * gs["tz"]
                            tm = _mul((1, 0, 0, 1, tx, 0), tm)
                elif op == "Do" and ops and depth < 8:
                    self._do(res, str(ops[-1]), gs["ctm"], depth)
                elif op == "BI":
                    # Inline image: skip its data up to EI.
                    m = re.search(rb"\sID\s", content[lx.pos:])
                    if m:
                        e = re.search(rb"\sEI(?=[\s]|$)", content[lx.pos + m.end():])
                        lx.pos += m.end() + (e.end() if e else 0)
            except (ValueError, TypeError, IndexError, ZeroDivisionError):
                pass
            ops = []
            if len(self.glyphs) > MAX_GLYPHS:
                return

    def _show(self, s: bytes, gs: dict, tm):
        f: _Font = gs["font"]
        size, th = gs["size"], gs["tz"]
        if f is None:
            return tm
        trs = (size * th, 0, 0, size, 0, gs["rise"])
        for code, cid in f.codes(s):
            m = _mul(_mul(trs, tm), gs["ctm"])
            w0 = f.width(cid)
            ch = f.text(code, cid)
            adv = (w0 * size + gs["tc"] + (gs["tw"] if (len(code) == 1 and code == b" ") else 0)) * th
            # Glyph width in device space along the baseline direction.
            sx = (m[0] ** 2 + m[1] ** 2) ** 0.5
            sy = (m[2] ** 2 + m[3] ** 2) ** 0.5
            if ch and sx > 0:
                rot = 0
                if abs(m[1]) > abs(m[0]):
                    rot = 90 if m[1] > 0 else 270
                elif m[0] < 0:
                    rot = 180
                self.glyphs.append(_Glyph(m[4], m[5], w0 * sx, sy, ch, rot, self.page))
            tm = _mul((1, 0, 0, 1, adv, 0), tm)
        return tm

    def _do(self, res, name, ctm, depth):
        xo = self.doc.resolve(res.get("XObject")) or {}
        obj = self.doc.resolve(xo.get(name)) if isinstance(xo, dict) else None
        if not isinstance(obj, Stream):
            return
        st = obj.dict.get("Subtype")
        if st == "Form":
            data, _ = decode_stream(self.doc, obj)
            mtx = self.doc.resolve(obj.dict.get("Matrix"))
            m = tuple(float(v) for v in mtx) if isinstance(mtx, list) and len(mtx) == 6 else _IDENT
            r = self.doc.resolve(obj.dict.get("Resources"))
            self.run(data, r if isinstance(r, dict) else res, _mul(m, ctm), depth + 1)
        elif st == "Image":
            data, flt = decode_stream(self.doc, obj)
            if flt in ("DCTDecode", "DCT"):
                w = int(self.doc.resolve(obj.dict.get("Width", 0)) or 0)
                h = int(self.doc.resolve(obj.dict.get("Height", 0)) or 0)
                area = abs(ctm[0] * ctm[3] - ctm[1] * ctm[2])
                self.images.append((data, w, h, area))


# ── line rebuilding ──────────────────────────────────────────────────────────

def glyphs_to_text(glyphs) -> str:
    """
    Rebuild reading-order text from positioned glyphs (also used for OCR words).
    Each glyph: .x .y (baseline, y up) .w .size .ch .page.
    """
    out_pages = []
    for page in sorted({g.page for g in glyphs}):
        gl = [g for g in glyphs if g.page == page]
        gl.sort(key=lambda g: (-g.y, g.x))
        lines: list[list] = []
        for g in gl:
            if lines:
                ln = lines[-1]
                tol = 0.35 * min(ln[0].size, g.size) if g.size else 1.0
                if abs(ln[0].y - g.y) <= tol:
                    ln.append(g)
                    continue
            lines.append([g])
        text_lines = []
        for ln in lines:
            ln.sort(key=lambda g: g.x)
            parts: list[str] = []
            end = None
            prev = None
            for g in ln:
                if prev is not None and g.ch == prev.ch and abs(g.x - prev.x) < 0.1 * g.size:
                    continue                    # fake-bold double strike
                if end is not None:
                    gap = g.x - end
                    sz = g.size or 1.0
                    if gap > 1.2 * sz:
                        n = max(3, min(60, int(round(gap / (0.5 * sz)))))
                        if parts and parts[-1].endswith(" "):
                            parts[-1] = parts[-1].rstrip(" ")
                        parts.append(" " * n)
                    elif gap > 0.18 * sz and not (parts and parts[-1].endswith(" ")) \
                            and g.ch != " ":
                        parts.append(" ")
                parts.append(g.ch)
                end = max(end or g.x, g.x + g.w) if g.ch != " " or end is None else \
                    max(end, g.x + g.w)
                prev = g
            s = "".join(parts).rstrip()
            s = re.sub(r"(?<=\S) {2}(?=\S)", " ", s)   # two spaces = a word gap
            if s.strip():
                text_lines.append(s)
        out_pages.append("\n".join(text_lines))
    return "\n\n".join(p for p in out_pages if p)


class PdfContent:
    def __init__(self, text: str, images: list, pages: int):
        self.text, self.images, self.pages = text, images, pages

    @property
    def image_only(self) -> bool:
        return not self.text.strip() and bool(self.images)


def read_pdf(data: bytes, max_pages: int = MAX_PAGES) -> PdfContent:
    doc = _Doc(data)
    pages = doc.pages()
    it = _Interp(doc)
    for n, (page, res) in enumerate(pages[:max_pages]):
        it.page = n
        contents = doc.resolve(page.get("Contents"))
        streams = contents if isinstance(contents, list) else [contents]
        buf = []
        for s in streams:
            s = doc.resolve(s)
            if isinstance(s, Stream):
                try:
                    buf.append(decode_stream(doc, s)[0])
                except Exception:
                    continue
        try:
            it.run(b"\n".join(buf), res)
        except RecursionError:
            continue
    glyphs = it.glyphs
    upright = [g for g in glyphs if g.rot == 0]
    if len(upright) < len(glyphs) / 2:
        # Mostly rotated (a landscape page drawn sideways): rotate it back.
        for g in glyphs:
            if g.rot == 90:
                g.x, g.y = g.y, -g.x
            elif g.rot == 270:
                g.x, g.y = -g.y, g.x
            elif g.rot == 180:
                g.x, g.y = -g.x, -g.y
        upright = glyphs
    images = [(d, w, h) for d, w, h, _ in sorted(it.images, key=lambda i: -i[3])]
    return PdfContent(glyphs_to_text(upright), images, len(pages))
