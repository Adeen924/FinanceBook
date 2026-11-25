#!/bin/bash
# build_mac.sh — build FinanceBook.app for your own Mac and install it in /Applications.
#
#   ./build_mac.sh                 build the version of the latest release tag
#   FB_VERSION=1.19.0 ./build_mac.sh   build with a chosen version number (testing)
#   FB_YES=1 ./build_mac.sh        replace an existing /Applications copy without asking
#   FB_NO_INSTALL=1 ./build_mac.sh build only (dist/mac/FinanceBook.app)
#
# Needs Apple's free Command Line Tools (xcode-select --install) and Python 3.12
# (Homebrew python@3.12 or python.org). No Apple Developer account: the app is
# ad-hoc signed, and because it is built on this Mac it is never quarantined,
# so Gatekeeper opens it without an "unidentified developer" prompt.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ── Python environment ───────────────────────────────────────────────────────
VENV="$ROOT/.venv-mac"
if [ ! -x "$VENV/bin/python" ]; then
    PY=""
    for c in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 \
             /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 python3.12; do
        if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
    done
    if [ -z "$PY" ]; then
        echo "Python 3.12 not found. Install it (brew install python@3.12, or python.org) and run again." >&2
        exit 1
    fi
    say "Creating .venv-mac with $PY"
    "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
say "Installing dependencies"
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt pyinstaller

# ── Version (latest release tag, never edited in the repo) ───────────────────
if [ -z "${FB_VERSION:-}" ]; then
    TAG="$(git describe --tags --abbrev=0 --match 'v[0-9]*' --exclude '*-*' 2>/dev/null || true)"
    FB_VERSION="${TAG#v}"
    FB_VERSION="${FB_VERSION:-$(tr -d '[:space:]' < app/VERSION)}"
fi
export FB_VERSION
say "Building FinanceBook $FB_VERSION"

# ── Icon (.icns from app/assets/icon.ico) ────────────────────────────────────
WORK="$ROOT/build/mac"
mkdir -p "$WORK"
ICONSET="$WORK/FinanceBook.iconset"
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
sips -s format png app/assets/icon.ico --out "$WORK/icon.png" >/dev/null
for s in 16 32 128 256 512; do
    sips -z $s $s "$WORK/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2))
    sips -z $d $d "$WORK/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$WORK/FinanceBook.icns"
export FB_ICNS="$WORK/FinanceBook.icns"

# ── Freeze the launcher ──────────────────────────────────────────────────────
DIST="$ROOT/dist/mac"
rm -rf "$DIST"
pyinstaller FinanceBook-macos.spec --noconfirm --distpath "$DIST" --workpath "$WORK/pyinstaller" \
    --log-level WARN
APP="$DIST/FinanceBook.app"
RES="$APP/Contents/Resources"

# ── Bundle app/ (only files git knows about: no local databases or settings) ─
say "Copying app/ into the bundle"
rm -rf "$RES/app"
git ls-files -z --cached --others --exclude-standard app \
    | rsync -a --from0 --files-from=- "$ROOT/" "$RES/"
printf '%s' "$FB_VERSION" > "$RES/app/VERSION"
cp requirements.txt "$RES/requirements.txt"      # lets the updater spot runtime changes
python -m compileall -q "$RES/app"                # precompiled: nothing is written in the .app later

# ── Ad-hoc sign (no Apple account; Apple Silicon needs a signature to run) ───
say "Signing (ad-hoc)"
xattr -cr "$APP"
codesign --force --deep -s - "$APP"
codesign --verify --deep --strict "$APP"

say "Self-test of the build"
"$APP/Contents/MacOS/FinanceBook" --self-test | tail -1

if [ -n "${FB_NO_INSTALL:-}" ]; then
    echo "Built: $APP"
    exit 0
fi

# ── Install to /Applications (old copy kept until the new one works) ─────────
DEST="/Applications/FinanceBook.app"
OLD="/Applications/FinanceBook-old.app"
if [ -d "$DEST" ]; then
    if [ -z "${FB_YES:-}" ]; then
        read -r -p "Replace the FinanceBook.app in /Applications? It is kept as FinanceBook-old.app until the new one opens. [y/N] " ans
        case "$ans" in [yY]*) ;; *) echo "Not installed. Built app: $APP"; exit 0 ;; esac
    fi
    osascript -e 'tell application "FinanceBook" to quit' >/dev/null 2>&1 || true
    rm -rf "$OLD"
    mv "$DEST" "$OLD"
fi
ditto "$APP" "$DEST"

say "Checking the installed app"
if "$DEST/Contents/MacOS/FinanceBook" --self-test | tail -1 | grep -q "SELF-TEST OK"; then
    rm -rf "$OLD"
    echo "Installed FinanceBook $FB_VERSION in /Applications."
    open "$DEST"
else
    echo "The installed app failed its self-test." >&2
    if [ -d "$OLD" ]; then
        rm -rf "$DEST"; mv "$OLD" "$DEST"
        echo "Put the previous FinanceBook.app back." >&2
    fi
    exit 1
fi
