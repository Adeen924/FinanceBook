#!/bin/bash
# update_mac.sh — rebuild FinanceBook.app from the latest code.
# Needed only when the app says "This update needs a rebuild" (a release that
# changed Python libraries); normal updates install themselves from inside the app.
set -euo pipefail
cd "$(dirname "$0")"
git checkout main
git pull --tags origin main
./build_mac.sh
