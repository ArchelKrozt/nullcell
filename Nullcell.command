#!/bin/sh
# Doble clic para abrir Nullcell. macOS ejecuta los .command en la Terminal.
#
# `cd` al directorio del propio fichero porque el doble clic arranca en la
# carpeta personal, y entonces el script de al lado no existiría.
cd "$(dirname "$0")" || exit 1
exec python3 nullcell-escritorio.py "$@"
