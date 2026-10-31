#!/bin/bash
# Starts the Athos Icons demo. Keep this window open while you use the shop.
cd "$(dirname "$0")" || exit 1
# Files downloaded from the internet are blocked by macOS until approved; this script was
# approved when you opened it, so let the Python next to it run too.
xattr -dr com.apple.quarantine . 2>/dev/null
./python/bin/python3 app/demo/launch.py
