#!/bin/sh
# Construit le .app et l'emballe dans un DMG glisser-vers-Applications.
#
# Sortie: dist/CoeOS-SE-<version>.dmg
#
# Gatekeeper : un DMG qui passe sur une AUTRE machine exige une vraie signature
# Developer ID ET la notarisation Apple. Sans notarisation l'utilisateur doit
# passer par clic-droit → Ouvrir, ce qui annule l'interet d'un DMG grand
# public. Ce script signe puis, si les identifiants sont presents, notarise.
set -eu

CONFIGURATION="${1:-release}"
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

if [ "${COEOS_PACKAGE_SKIP_BUILD:-0}" != "1" ]; then
  "$ROOT/scripts/make_app.sh" "$CONFIGURATION"
fi

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' coeos_se/__init__.py)"
BUILD="$(cat "$ROOT/.build-number" 2>/dev/null || echo 0)"
DIST="$ROOT/dist"
APP="$DIST/CoeOS SE.app"
STAGING="$DIST/dmg-staging"
DMG="$DIST/CoeOS-SE-$VERSION.dmg"
VOLNAME="CoeOS SE $VERSION"

[ -d "$APP" ] || { echo "x app absente: $APP — lancer scripts/make_app.sh"; exit 1; }

HAS_CREDS=0
if [ -n "${AC_NOTARY_PROFILE:-}" ] || { [ -n "${AC_APPLE_ID:-}" ] && [ -n "${AC_PASSWORD:-}" ] && [ -n "${AC_TEAM_ID:-}" ]; }; then
  HAS_CREDS=1
fi

# Notariser l'APP avant de l'emballer. Agrafer le seul DMG ne suffit pas : une
# fois l'app glissee dans /Applications, elle sort du DMG et perd le ticket ;
# son premier lancement doit alors interroger Apple en ligne, et echoue si la
# machine ne l'est pas.
if [ "$HAS_CREDS" = "1" ]; then
  echo "→ notarisation de l'app avant emballage…"
  "$ROOT/scripts/notarize.sh" "$APP"
fi

echo "→ preparation du DMG…"
rm -rf "$STAGING" "$DMG"
mkdir -p "$STAGING"
cp -R "$APP" "$STAGING/CoeOS SE.app"
ln -s /Applications "$STAGING/Applications"

hdiutil create \
  -volname "$VOLNAME" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  "$DMG"

IDENTITY="${CODESIGN_IDENTITY:-Developer ID Application: Dupont Sophie (U2YXX868N2)}"
if security find-identity -v -p codesigning 2>/dev/null | grep -q "$IDENTITY"; then
  codesign --sign "$IDENTITY" --force --timestamp "$DMG"
  echo "  OK DMG signe avec '$IDENTITY'"
else
  echo "  ! DMG non signe (identite '$IDENTITY' absente)."
fi

if [ "$HAS_CREDS" = "1" ]; then
  echo "→ notarisation du DMG…"
  "$ROOT/scripts/notarize.sh" "$DMG"
else
  echo "  i Notarisation ignoree (ni AC_NOTARY_PROFILE ni AC_APPLE_ID/AC_PASSWORD/AC_TEAM_ID)."
fi

rm -rf "$STAGING"
echo "OK -> $DMG  v$VERSION (build $BUILD)"
