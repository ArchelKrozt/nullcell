#!/usr/bin/env bash
# =============================================================================
#  Construir el APK de Nullcell para Android  →  android/salida/
# =============================================================================
#  Compila dentro de un contenedor `python:3.12` (Chaquopy necesita un Python
#  3.12 con pip para instalar los paquetes en el APK) y reutiliza una cadena
#  Android ya preparada —JDK 17, Android SDK 35, Gradle 8.10—, por defecto la
#  del cliente de Mundo Nexo, montada en sólo lectura.
#
#    bash android/construir.sh
#    CADENA=/ruta/.cadena bash android/construir.sh
#    RUEDAS=1 bash android/construir.sh      # rehace también las ruedas
# =============================================================================
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
CADENA="${CADENA:-/srv/storage/nucleo/webapps/rpg/cliente_apk/.cadena}"
[ -x "$CADENA/jdk/bin/java" ] || { echo "✗ falta la cadena Android en $CADENA" >&2; exit 1; }

if [ ! -f "$AQUI/ruedas/nullcell-0.1.0-py3-none-any.whl" ] || [ "${RUEDAS:-}" = "1" ]; then
  bash "$AQUI/preparar-ruedas.sh"
  # Misma versión de nullcell (0.1.0) con código nuevo: Chaquopy reutilizaría
  # la instalación anterior. Se borra para que instale las ruedas recién hechas.
  rm -rf "$AQUI/app/build/python" "$AQUI/app/build/intermediates/python"* "$AQUI/app/build/generated/python"
fi

mkdir -p "$AQUI/.cadena/gradle-home"
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$AQUI":/proyecto \
  -v "$CADENA":/cadena:ro \
  -e JAVA_HOME=/cadena/jdk \
  -e ANDROID_HOME=/cadena/android-sdk -e ANDROID_SDK_ROOT=/cadena/android-sdk \
  -e GRADLE_USER_HOME=/proyecto/.cadena/gradle-home \
  -e NULLCELL_BUILD_PYTHON=/usr/local/bin/python3 \
  -w /proyecto python:3.12-slim sh -eu -c '
    echo "sdk.dir=/cadena/android-sdk" > local.properties
    /cadena/gradle/bin/gradle --no-daemon --console=plain :app:assembleRelease
  '

VERSION=$(grep -oP 'versionName\s*=\s*"\K[^"]+' "$AQUI/app/build.gradle.kts")
mkdir -p "$AQUI/salida"
cp "$AQUI/app/build/outputs/apk/release/app-release.apk" "$AQUI/salida/nullcell-$VERSION.apk"
ls -la "$AQUI/salida/"
echo "✓ android/salida/nullcell-$VERSION.apk"
