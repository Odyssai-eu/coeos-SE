#!/bin/sh
# Notarise + agrafe un DMG (ou un .app) via le service Apple.
#
# La cible doit deja etre signee avec un vrai certificat Developer ID
# Application. Identifiants, au choix :
#   AC_NOTARY_PROFILE   — profil `xcrun notarytool store-credentials`
#   ou AC_APPLE_ID + AC_PASSWORD (mot de passe d'application) + AC_TEAM_ID
#
# Usage: scripts/notarize.sh dist/CoeOS-SE-0.3.2.dmg
set -eu

TARGET="${1:?usage: notarize.sh <chemin-du-dmg-ou-app>}"
[ -e "$TARGET" ] || { echo "x introuvable: $TARGET"; exit 1; }

# notarytool n'accepte que .zip / .pkg / .dmg — un .app nu est refuse. On le
# compresse pour l'envoi, mais on agrafe le ticket sur le .app lui-meme :
# c'est lui que l'utilisateur glisse hors du DMG, et sans ticket agrafe son
# premier lancement hors ligne echoue.
SUBMIT="$TARGET"
CLEANUP=""
case "$TARGET" in
  *.app)
    SUBMIT="${TARGET%.app}.notarize.zip"
    CLEANUP="$SUBMIT"
    rm -f "$SUBMIT"
    ditto -c -k --keepParent "$TARGET" "$SUBMIT"
    ;;
esac

echo "→ envoi de $SUBMIT au service de notarisation…"
if [ -n "${AC_NOTARY_PROFILE:-}" ]; then
  xcrun notarytool submit "$SUBMIT" --keychain-profile "$AC_NOTARY_PROFILE" --wait
elif [ -n "${AC_APPLE_ID:-}" ] && [ -n "${AC_PASSWORD:-}" ] && [ -n "${AC_TEAM_ID:-}" ]; then
  xcrun notarytool submit "$SUBMIT" \
    --apple-id "$AC_APPLE_ID" \
    --password "$AC_PASSWORD" \
    --team-id "$AC_TEAM_ID" \
    --wait
else
  echo "x aucun identifiant (definir AC_NOTARY_PROFILE ou AC_APPLE_ID/AC_PASSWORD/AC_TEAM_ID)"
  exit 1
fi
[ -n "$CLEANUP" ] && rm -f "$CLEANUP"

echo "→ agrafage du ticket…"
xcrun stapler staple "$TARGET"
xcrun stapler validate "$TARGET" && echo "OK -> notarise + agrafe: $TARGET"
