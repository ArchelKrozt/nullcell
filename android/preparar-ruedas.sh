#!/usr/bin/env bash
# =============================================================================
#  Ruedas de Python para la app de Android  →  android/ruedas/
# =============================================================================
#  Chaquopy instala las dependencias de Nullcell en el APK. Las criptográficas
#  (cryptography, PyNaCl, cffi) las trae Chaquopy compiladas para Android; el
#  resto se prepara aquí como ruedas **puras** (py3-none-any):
#
#    nullcell     el propio paquete, desde este repositorio
#    aioquic      sin su única pieza nativa: `_buffer` va en Python puro
#                 (python-puro/_buffer.py, idéntico al C; ver LEEME.md)
#    aiohttp, multidict, yarl, propcache, frozenlist, cbor2
#                 sus extensiones en C son opcionales: se construyen sin ellas
#
#  Uso:  bash android/preparar-ruedas.sh        (necesita docker)
# =============================================================================
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$AQUI/.." && pwd)"
SALIDA="$AQUI/ruedas"
rm -rf "$SALIDA" && mkdir -p "$SALIDA"

docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$RAIZ":/repo:ro -v "$SALIDA":/salida \
  -e AIOHTTP_NO_EXTENSIONS=1 -e MULTIDICT_NO_EXTENSIONS=1 -e YARL_NO_EXTENSIONS=1 \
  -e PROPCACHE_NO_EXTENSIONS=1 -e FROZENLIST_NO_EXTENSIONS=1 -e CBOR2_BUILD_C_EXTENSION=0 \
  python:3.12-slim sh -eu -c '
    pip install -q --user --no-warn-script-location wheel build
    export PATH=/tmp/.local/bin:$PATH
    T=$(mktemp -d)

    echo "▸ nullcell"
    cp -r /repo "$T/repo" && rm -rf "$T/repo/android" "$T/repo/build"
    pip wheel -q --no-deps "$T/repo" -w "$T/nc"
    # El móvil no tiene línea de comandos: fuera `typer`, que sólo usa
    # nullcell/cli/main.py y que en sus versiones nuevas arrastra pydantic-core
    # (Rust, sin rueda para Android). El nodo carga sin él (comprobado).
    (cd "$T/nc" && python -m wheel unpack -d u nullcell-*.whl >/dev/null \
      && sed -i "/^Requires-Dist: typer/d" u/nullcell-*/nullcell-*.dist-info/METADATA \
      && python -m wheel pack -d /salida u/nullcell-* >/dev/null)

    echo "▸ aioquic sin piezas nativas"
    V=1.8.0
    pip download -q --no-deps --only-binary :all: --platform manylinux2014_x86_64 \
        --python-version 3.12 "aioquic==$V" -d "$T/aq"
    cd "$T/aq" && python -m wheel unpack -d u aioquic-*.whl
    D=$(ls -d u/aioquic-*)
    rm -f "$D"/aioquic/_buffer*.so "$D"/aioquic/_buffer.c
    cp /repo/android/python-puro/_buffer.py "$D/aioquic/_buffer.py"
    W=$(ls "$D"/aioquic-*.dist-info/WHEEL)
    sed -i -e "s/^Root-Is-Purelib: .*/Root-Is-Purelib: true/" -e "/^Tag: /d" -e "/^[[:space:]]*$/d" "$W"
    printf "Tag: py3-none-any\n" >> "$W"
    # pylsqpack sólo lo usa aioquic.h3 (HTTP/3), que Nullcell no usa, y es nativo.
    sed -i "/^Requires-Dist: pylsqpack/d" "$D"/aioquic-*.dist-info/METADATA
    # Chaquopy compila cryptography hasta la 42.0.8 para Android; aioquic 1.8
    # pide >=43 pero la suite entera de Nullcell pasa con 42.0.8 (comprobado).
    sed -i "s/^Requires-Dist: cryptography.*/Requires-Dist: cryptography>=42/" "$D"/aioquic-*.dist-info/METADATA
    python -m wheel pack -d /salida "$D"

    echo "▸ dependencias sin extensiones en C"
    pip wheel -q --no-deps --no-binary :all: -w "$T/puras" \
        aiohttp multidict yarl propcache frozenlist "cbor2<6"
    for f in "$T"/puras/*.whl; do
      case "$f" in
        *py3-none-any.whl) cp "$f" /salida/ ;;
        *) python -m wheel tags --python-tag py3 --abi-tag none --platform-tag any \
             --remove "$f" >/dev/null && cp "$T"/puras/*py3-none-any.whl /salida/ 2>/dev/null || true ;;
      esac
    done
    ls -1 /salida
  '
echo "✓ ruedas en $SALIDA"
