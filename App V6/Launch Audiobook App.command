#!/bin/bash
# ── Double-click launcher for the EPUB -> Audiobook app (V6) on macOS ───────
# In Finder, double-click this file to start the app.
#
# FIRST TIME ONLY: macOS needs this marked as executable. Open Terminal and run
# (you can drag the file in to fill the path):
#       chmod +x "Launch Audiobook App.command"
# After that, double-clicking just works.

cd "$(dirname "$0")"

# Find a Python 3 to run the GUI with.
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    osascript -e 'display alert "Python not found" message "Install Python 3 from https://www.python.org/downloads/ then try again."'
    exit 1
fi

exec "$PY" epub_to_audiobook_gui_v6.py
