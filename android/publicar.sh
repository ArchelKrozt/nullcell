#!/usr/bin/env bash
# =============================================================================
#  Publicar el APK como release de GitHub  →  las apps instaladas lo detectan
# =============================================================================
#    GH_TOKEN=… bash android/publicar.sh "Notas de la versión"
#  Crea la etiqueta android-v<versión> sobre el commit actual (ya subido) y
#  adjunta android/salida/nullcell-<versión>.apk. La app busca exactamente
#  releases `android-v*` con un asset `.apk`.
# =============================================================================
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
REPO="${REPO:-ArchelKrozt/nullcell}"
: "${GH_TOKEN:?falta GH_TOKEN}"
VERSION=$(grep -oP 'versionName\s*=\s*"\K[^"]+' "$AQUI/app/build.gradle.kts")
APK="$AQUI/salida/nullcell-$VERSION.apk"
[ -f "$APK" ] || { echo "✗ no existe $APK: ejecuta construir.sh" >&2; exit 1; }
SHA=$(git -C "$AQUI" rev-parse HEAD)
git -C "$AQUI" branch -r --contains "$SHA" | grep -q . || { echo "✗ el commit $SHA no está subido" >&2; exit 1; }
NOTAS="${1:-Nullcell para Android $VERSION}"
H=(-H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json")
CUERPO=$(python3 -c 'import json,sys; print(json.dumps({"tag_name":"android-v"+sys.argv[1],"target_commitish":sys.argv[2],"name":"Nullcell para Android "+sys.argv[1],"body":sys.argv[3],"prerelease":True}))' "$VERSION" "$SHA" "$NOTAS")
ID=$(curl -sf "${H[@]}" -X POST "https://api.github.com/repos/$REPO/releases" -d "$CUERPO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -sf "${H[@]}" -H "Content-Type: application/vnd.android.package-archive" \
  --data-binary @"$APK" "https://uploads.github.com/repos/$REPO/releases/$ID/assets?name=$(basename "$APK")" >/dev/null
echo "✓ https://github.com/$REPO/releases/tag/android-v$VERSION"
