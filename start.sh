#!/usr/bin/env bash
# ==============================================================================
# Sahibinden Arbitraj & Fırsat Radarı Launcher
# Author: SS2Genji
# ==============================================================================

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "============================================================"
echo "  🚀 Sahibinden Arbitraj & Fırsat Radarı"
echo "  Author: SS2Genji | Standard Library + CDP Architecture"
echo "============================================================"

# 1. Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 bulunamadı. Lütfen Python 3.10 veya üzerini yükleyin."
    exit 1
fi

# 2. Check Node.js
if ! command -v node &> /dev/null; then
    echo "⚠️  Node.js bulunamadı. CDP tarayıcısı için Node.js 18+ gereklidir."
fi

# 3. Check / Initialize config.json
if [ ! -f "config.json" ]; then
    echo "📋 config.json bulunamadı, config.example.json dosyasından kopyalanıyor..."
    cp config.example.json config.json
fi

# 4. Check Chrome Remote Debugging Port (9222)
CDP_PORT=9222
if ! curl -s "http://127.0.0.1:${CDP_PORT}/json/version" > /dev/null 2>&1; then
    echo "🔍 9222 portunda çalışan Chrome tespit edilemedi. Tarayıcı aranıyor..."
    CHROME_BIN=""
    for bin in google-chrome google-chrome-stable chromium-browser chromium brave-browser microsoft-edge; do
        if command -v "$bin" &> /dev/null; then
            CHROME_BIN="$bin"
            break
        fi
    done

    if [ -n "$CHROME_BIN" ]; then
        echo "🌐 '$CHROME_BIN' hata ayıklama portu (${CDP_PORT}) ile başlatılıyor..."
        nohup "$CHROME_BIN" --remote-debugging-port="${CDP_PORT}" --user-data-dir="/tmp/radar-chrome" > /dev/null 2>&1 &
        sleep 2
        echo "✅ Chrome CDP portu aktif."
    else
        echo "⚠️  Otomatik Chrome bulunamadı. Tarayıcınızı şu komutla manuel başlatabilirsiniz:"
        echo "   google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/radar-chrome"
    fi
else
    echo "✅ Chrome CDP portu (9222) zaten aktif ve hazır."
fi

# 5. CLI mode or Web Dashboard mode
if [ "$1" == "--cli" ]; then
    echo "🖥️  Terminal tarama modu başlatılıyor..."
    python3 main.py "${@:2}"
else
    echo "🌐 Web Dashboard başlatılıyor: http://localhost:8888"
    python3 web_server.py
fi
