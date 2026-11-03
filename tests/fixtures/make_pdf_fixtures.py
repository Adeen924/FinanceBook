"""
Regenerate the synthetic PDF fixtures in this folder (sanitized: every name,
address and number is made up). Run from the repository root:

    python tests/fixtures/make_pdf_fixtures.py

amazon_like_cid.pdf      Chrome/Skia structure: Identity-H fonts + ToUnicode
digikey_like_winansi.pdf WinAnsi TrueType fragments, rotated label, orphan stream
jlcpcb_like_image.pdf    one JPEG page with no text layer (needs OCR); the image
                         is rendered with Qt, so PyQt6 is needed to regenerate it
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from tests import pdf_fixtures as F  # noqa: E402

INVOICE_LINES = [
    # (x, y, text, bold) on a 1600 x 1300 canvas
    (60, 60, "JiaLiChuang (HongKong) Co., Limited", True),
    (1100, 60, "INVOICE", True),
    (60, 110, "support@jlcpcb.com", False),
    (1000, 140, "Invoice No:", False), (1180, 140, "13310000A2026100200000000", False),
    (1000, 185, "Invoice Date:", False), (1180, 185, "08/10/2026", False),
    (60, 260, "Ship To:", True),
    (60, 300, "Jordan Sample", False),
    (60, 340, "100 Example Street", False),
    (60, 480, "Product", True), (700, 480, "QTY", True), (860, 480, "Unit Price", True),
    (1150, 480, "Ext.Price", True),
    (60, 540, "1", False), (110, 540, "Rigid Populated printed circuit board", False),
    (710, 540, "5", False), (860, 540, "USD $9.03", False), (1150, 540, "USD $45.13", False),
    (760, 700, "Merchandise Total:", False), (1150, 700, "USD $45.13", False),
    (760, 750, "Shipping:", False), (1150, 750, "USD $35.13", False),
    (760, 800, "Merchandise Discount:", False), (1135, 800, "-USD $10.00", False),
    (760, 850, "Subtotal:", False), (1150, 850, "USD $70.26", False),
    (760, 900, "States Sales&Use Tax:", False), (1150, 900, "USD $2.73", False),
    (760, 950, "Import Taxes:", False), (1150, 950, "USD $16.79", False),
    (760, 1000, "Grand Total:", True), (1150, 1000, "USD $89.78", True),
    (60, 1150, "JLCPCB.COM", False),
]


def render_invoice_jpeg() -> bytes:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QBuffer, QIODevice, Qt
    from PyQt6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])  # noqa: F841 (fonts need it)
    # The offscreen platform has no system fonts; load one explicitly.
    for path in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)
    img = QImage(1600, 1300, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setPen(QColor("black"))
    for x, y, text, bold in INVOICE_LINES:
        f = QFont("Arial", 20)
        f.setBold(bold)
        p.setFont(f)
        p.drawText(x, y, text)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPG", 92)
    return bytes(buf.data())


def main():
    out = {
        "amazon_like_cid.pdf": F.skia_cid_pdf(),
        "digikey_like_winansi.pdf": F.winansi_pdf(),
        "jlcpcb_like_image.pdf": F.image_pdf(render_invoice_jpeg(), 1600, 1300),
    }
    for name, data in out.items():
        with open(os.path.join(HERE, name), "wb") as f:
            f.write(data)
        print(f"wrote {name} ({len(data):,} bytes)")


if __name__ == "__main__":
    main()
