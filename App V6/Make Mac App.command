#!/bin/bash
# ── One-time builder: creates "EPUB to Audiobook.app" for macOS ─────────────
# Run this ONCE on the Mac. Afterwards, double-click the generated
# "EPUB to Audiobook.app" to launch the app with NO Terminal window.
#
# HOW TO RUN: open Terminal, type  bash  followed by a space, then drag this
# file into the Terminal window and press Enter. (No chmod needed this way.)

set -e
cd "$(dirname "$0")"

APP="EPUB to Audiobook.app"
echo "Building $APP ..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

# Info.plist — tells macOS this folder is a double-clickable app.
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>EPUB to Audiobook</string>
    <key>CFBundleDisplayName</key><string>EPUB to Audiobook</string>
    <key>CFBundleIdentifier</key><string>com.local.epub2audiobook</string>
    <key>CFBundleVersion</key><string>6.0</string>
    <key>CFBundleShortVersionString</key><string>6.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>launcher</string>
</dict>
</plist>
PLIST

# The launcher: finds the app folder (3 levels up from this script inside the
# bundle), then runs the GUI with python3. No Terminal window appears because
# macOS launches a .app bundle directly, not through Terminal.
cat > "$APP/Contents/MacOS/launcher" <<'LAUNCH'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$DIR"
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    osascript -e 'display alert "Python not found" message "Install Python 3 from https://www.python.org/downloads/ then try again."'
    exit 1
fi
exec "$PY" epub_to_audiobook_gui_v6.py
LAUNCH

chmod +x "$APP/Contents/MacOS/launcher"

echo "Done. Double-click \"$APP\" to launch."
echo "Tip: drag it to the Dock to keep it handy."
