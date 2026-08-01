#!/bin/sh
# Assemble "CoeOS SE.app" — une app de barre de menus qui embarque le serveur
# gele par PyInstaller. Aucun Python, aucun uv requis sur la machine cible.
#
# Usage: scripts/make_app.sh [release|debug]     (defaut: release)
# Sortie: dist/CoeOS SE.app
#
# Version : lue depuis coeos_se/__init__.py, PAS depuis un fichier VERSION
# separe. La regle de build demande une version marketing versionnee a la main
# et un numero de build auto-incremente ; ici la version vit deja en deux
# endroits (pyproject.toml + __init__.py) et le skill de release previent qu'un
# oubli entre les deux fait mentir /health. Un troisieme endroit serait un
# piege de plus, donc on derive. Le compteur .build-number, lui, s'incremente
# a chaque execution comme exige.
set -eu

CONFIGURATION="${1:-release}"
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' coeos_se/__init__.py)"
[ -n "$VERSION" ] || { echo "x version introuvable dans coeos_se/__init__.py"; exit 1; }

BUILD_FILE="$ROOT/.build-number"
[ -f "$BUILD_FILE" ] || echo 0 > "$BUILD_FILE"
BUILD=$(( $(cat "$BUILD_FILE") + 1 ))
echo "$BUILD" > "$BUILD_FILE"

DIST="$ROOT/dist"
BUILDDIR="$ROOT/build/app"
APP="$DIST/CoeOS SE.app"
ICON_SRC="$ROOT/coeos_se/dashboard/images/coeos.png"

echo "→ CoeOS SE $VERSION (build $BUILD)"
rm -rf "$APP" "$BUILDDIR"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$BUILDDIR"

# ── 1. Geler le serveur ────────────────────────────────────────────────────
# Un venv jetable : le binaire doit embarquer les dependances de la version du
# repo, pas celles qui trainent dans l'environnement courant.
echo "→ gel du serveur (PyInstaller)…"
VENV="$BUILDDIR/venv"
uv venv --python 3.12 "$VENV" >/dev/null
VIRTUAL_ENV="$VENV" uv pip install --quiet "$ROOT" pyinstaller

"$VENV/bin/pyinstaller" --onedir --name coeos-se \
  --collect-all coeos_se --collect-submodules uvicorn \
  --distpath "$BUILDDIR/dist" --workpath "$BUILDDIR/work" \
  --specpath "$BUILDDIR" --noconfirm --log-level WARN \
  "$ROOT/macos/entry.py"

[ -x "$BUILDDIR/dist/coeos-se/coeos-se" ] || { echo "x le gel a echoue"; exit 1; }
cp -R "$BUILDDIR/dist/coeos-se" "$APP/Contents/Resources/server"

# ── 2. Compiler la barre de menus ──────────────────────────────────────────
echo "→ compilation de la barre de menus…"
SWIFT_FLAGS="-O"
[ "$CONFIGURATION" = "debug" ] && SWIFT_FLAGS="-Onone"
# shellcheck disable=SC2086
swiftc $SWIFT_FLAGS -o "$APP/Contents/MacOS/CoeOSSE" \
  "$ROOT/macos/CoeOSMenuBar.swift" -framework AppKit

# ── 3. Icones ──────────────────────────────────────────────────────────────
echo "→ icones…"
sips -z 36 36 "$ICON_SRC" --out "$APP/Contents/Resources/menubar.png" >/dev/null

ICONSET="$BUILDDIR/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size "$ICON_SRC" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) "$ICON_SRC" \
    --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

# ── 4. Info.plist ──────────────────────────────────────────────────────────
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleDisplayName</key>
    <string>CoeOS SE</string>
    <key>CFBundleExecutable</key>
    <string>CoeOSSE</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>eu.odyssai.coeos-se</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>CoeOS SE</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>$VERSION</string>
    <key>CFBundleVersion</key>
    <string>$BUILD</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSHumanReadableCopyright</key>
    <string>OdyssAI — MIT.</string>
    <key>NSLocalNetworkUsageDescription</key>
    <string>CoeOS SE reaches model endpoints, which may run on your local network.</string>
</dict>
</plist>
EOF

# ── 5. Nettoyage avant scellement ──────────────────────────────────────────
# Un __pycache__ ou un attribut etendu cree apres signature invalide le bundle.
find "$APP/Contents/Resources" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$APP/Contents/Resources" -name '*.pyc' -delete 2>/dev/null || true
xattr -cr "$APP" 2>/dev/null || true

# ── 6. Signature ───────────────────────────────────────────────────────────
if [ "$CONFIGURATION" = "release" ]; then
  IDENTITY="${CODESIGN_IDENTITY:-Developer ID Application: Dupont Sophie (U2YXX868N2)}"
  if security find-identity -v -p codesigning 2>/dev/null | grep -q "$IDENTITY"; then
    echo "→ signature avec '$IDENTITY'…"
    ENTITLEMENTS="$ROOT/macos/entitlements.plist"
    # De l'interieur vers l'exterieur : chaque Mach-O embarque d'abord, le
    # bundle ensuite. --options runtime est exige par la notarisation.
    find "$APP/Contents/Resources/server" -type f \( -name '*.dylib' -o -name '*.so' \) \
      -exec codesign --sign "$IDENTITY" --force --timestamp --options runtime {} + 2>/dev/null || true
    codesign --sign "$IDENTITY" --force --timestamp --options runtime \
      --entitlements "$ENTITLEMENTS" "$APP/Contents/Resources/server/coeos-se"
    codesign --sign "$IDENTITY" --force --timestamp --options runtime \
      --entitlements "$ENTITLEMENTS" "$APP"
    codesign --verify --strict --verbose=2 "$APP" 2>&1 | tail -2
  else
    echo "  ! identite '$IDENTITY' absente — bundle non signe, non distribuable."
  fi
fi

echo "OK -> $APP  v$VERSION (build $BUILD)"
