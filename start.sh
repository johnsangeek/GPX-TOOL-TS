#!/bin/bash
# GPX Overlay — Lance le backend + Expo en un clic
# Usage : double-clic sur start.sh ou ./start.sh

BACKEND_DIR="/Users/johnsanti/Downloads/GPX OVERLAY/BACKEND"
FRONTEND_DIR="/Users/johnsanti/Downloads/GPX OVERLAY/gpx-overlay-app"

osascript <<EOF
tell application "Terminal"
    activate

    -- Fenêtre 1 : Backend Python
    do script "echo '🐍 GPX Overlay — Backend'; cd '$BACKEND_DIR' && .venv/bin/uvicorn main:app --reload --host 0.0.0.0 --port 8000 --timeout-keep-alive 300"

    -- Fenêtre 2 : Expo
    do script "echo '📱 GPX Overlay — Expo'; cd '$FRONTEND_DIR' && npx expo start --ios"
end tell
EOF

echo "✅ Backend + Expo lancés dans Terminal"
