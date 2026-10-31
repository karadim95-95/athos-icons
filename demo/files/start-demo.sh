#!/bin/sh
# Starts the Athos Icons demo. Keep this window open while you use the shop.
cd "$(dirname "$0")" || exit 1
exec ./python/bin/python3 app/demo/launch.py
