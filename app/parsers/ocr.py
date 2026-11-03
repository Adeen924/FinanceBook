"""
Offline OCR through the OCR engine built into Windows 10/11
(Windows.Media.Ocr), driven from PowerShell — nothing to install.

    available()                 → True on Windows when PowerShell exists
    ocr_image(path) → str       raises OCRError (no OCR language, bad image…)

Words come back with their bounding boxes and are rebuilt into lines with the
same layout logic as PDF text, so receipt parsing sees one format. Slow-ish
(about a second per page): callers run it off the UI thread.
"""
import base64
import os
import subprocess
import sys

from parsers.pdf_text import glyphs_to_text

TIMEOUT = 90


class OCRError(Exception):
    """OCR couldn't run or read the image (message is user-presentable)."""


_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
  function Await($op, [Type]$t) {
    $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
  [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
  [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
  [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime] | Out-Null
  $eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if ($eng -eq $null) { [Console]::Out.Write("ERR`tnolang"); exit 3 }
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:FB_OCR_IMAGE)) ([Windows.Storage.StorageFile])
  $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $tr = New-Object Windows.Graphics.Imaging.BitmapTransform
  $max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
  $big = [Math]::Max($dec.OrientedPixelWidth, $dec.OrientedPixelHeight)
  if ($big -gt $max) {
    $k = $max / $big
    $tr.ScaledWidth = [uint32][Math]::Floor($dec.PixelWidth * $k)
    $tr.ScaledHeight = [uint32][Math]::Floor($dec.PixelHeight * $k)
  }
  $bmp = Await ($dec.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
    [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied, $tr,
    [Windows.Graphics.Imaging.ExifOrientationMode]::RespectExifOrientation,
    [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $res = Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
  $sb = New-Object System.Text.StringBuilder
  foreach ($l in $res.Lines) { foreach ($w in $l.Words) { $r = $w.BoundingRect
    [void]$sb.Append(("{0}`t{1}`t{2}`t{3}`t{4}`n" -f [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height, $w.Text)) } }
  [Console]::Out.Write("OK`n" + $sb.ToString())
} catch {
  [Console]::Out.Write("ERR`t" + $_.Exception.Message); exit 2
}
"""


def _powershell() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    exe = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return exe if os.path.isfile(exe) else ""


def available() -> bool:
    return sys.platform == "win32" and bool(_powershell())


class _Word:
    __slots__ = ("x", "y", "w", "size", "ch", "rot", "page")

    def __init__(self, x, y, w, h, text):
        # Centre line (not the bottom) groups words with and without descenders.
        self.x, self.y, self.w, self.size = x, -(y + h / 2.0), w, max(h, 1)
        self.ch, self.rot, self.page = text, 0, 0


def words_to_text(tsv: str) -> str:
    words = []
    for ln in tsv.splitlines():
        parts = ln.split("\t", 4)
        if len(parts) != 5:
            continue
        try:
            x, y, w, h = (int(float(p)) for p in parts[:4])
        except ValueError:
            continue
        if parts[4].strip():
            words.append(_Word(x, y, w, h, parts[4].strip()))
    return glyphs_to_text(words)


def ocr_image(path: str, timeout: float = TIMEOUT) -> str:
    """Text of an image file (JPEG/PNG/GIF/BMP/TIFF), laid out in lines."""
    if not available():
        raise OCRError("Offline OCR needs Windows 10 or 11.")
    enc = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
    env = dict(os.environ, FB_OCR_IMAGE=os.path.abspath(path))
    try:
        proc = subprocess.run(
            [_powershell(), "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
            capture_output=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise OCRError("Reading the image took too long.")
    except OSError as e:
        raise OCRError(f"Couldn't start Windows OCR ({e.strerror or type(e).__name__}).")
    out = proc.stdout.decode("utf-8", "replace").lstrip("\ufeff")
    if out.startswith("ERR\tnolang"):
        raise OCRError("Windows has no OCR language installed (Settings → Time & language → "
                       "Language → add English with its optional features).")
    if not out.startswith("OK"):
        msg = out[4:].strip() if out.startswith("ERR") else proc.stderr.decode("utf-8", "replace")
        raise OCRError("Windows OCR couldn't read this image." + (f" ({msg[:160]})" if msg else ""))
    return words_to_text(out[3:])
