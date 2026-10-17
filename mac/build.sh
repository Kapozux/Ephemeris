#!/bin/sh
# 编 Ephemeris 的 Mac App（mac/Ephemeris.swift），装到 ~/Applications/Ephemeris.app。要 Xcode 命令行工具（swiftc）。
# 后台服务（5055）照旧用 launchctl 那个；App 只是一个窗口。重编一次覆盖旧的，开着的话先退出。
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT="${1:-$HOME/Applications}/Ephemeris.app"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

swiftc -O "$HERE/mac/Ephemeris.swift" -o "$TMP/Ephemeris"

mkdir -p "$TMP/Ephemeris.iconset"
"$TMP/Ephemeris" --render-icon "$TMP/icon.png"
for s in 16 32 128 256 512; do
  sips -z $s $s "$TMP/icon.png" --out "$TMP/Ephemeris.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$TMP/icon.png" --out "$TMP/Ephemeris.iconset/icon_${s}x${s}@2x.png" >/dev/null
done

rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
mv "$TMP/Ephemeris" "$OUT/Contents/MacOS/Ephemeris"
iconutil -c icns "$TMP/Ephemeris.iconset" -o "$OUT/Contents/Resources/Ephemeris.icns"
sed "s|__KEY_FILE__|$HERE/.app_key|" "$HERE/mac/Info.plist" > "$OUT/Contents/Info.plist"
codesign --force --sign - "$OUT" >/dev/null 2>&1
echo "装好了：$OUT"
