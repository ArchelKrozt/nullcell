#!/usr/bin/env bash
# Instala Nullcell y, opcionalmente, un relevo como servicio.
#
#   ./install.sh                 # sólo el programa, en /opt/nullcell
#   ./install.sh --relay IP:4433 # además el servicio de systemd
#
# No descarga nada de internet aparte de las dependencias de Python desde PyPI,
# y no necesita root salvo para instalar el servicio.
set -euo pipefail

PREFIX="${PREFIX:-/opt/nullcell}"
ANNOUNCE=""
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --relay) ANNOUNCE="${2:?--relay necesita IP:puerto}"; shift 2 ;;
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done

say() { printf '  %s\n' "$*"; }

echo "Nullcell — instalación"
say "repositorio: $REPO_ROOT"
say "destino:     $PREFIX"

# --- Python ------------------------------------------------------------------
PYTHON="$(command -v python3.12 || command -v python3.11 || command -v python3 || true)"
[[ -n "$PYTHON" ]] || { echo "✗ no se encontró python3" >&2; exit 1; }
VERSION="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
say "python:      $PYTHON ($VERSION)"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
  || { echo "✗ hace falta Python 3.11 o superior" >&2; exit 1; }

# --- entorno -----------------------------------------------------------------
SUDO=""
if [[ ! -w "$(dirname "$PREFIX")" ]]; then
  SUDO="sudo"
  say "se usará sudo para escribir en $(dirname "$PREFIX")"
fi

$SUDO "$PYTHON" -m venv "$PREFIX"
$SUDO "$PREFIX/bin/pip" install --quiet --upgrade pip
$SUDO "$PREFIX/bin/pip" install --quiet "$REPO_ROOT"
say "instalado:   $($PREFIX/bin/ncell --version 2>/dev/null || echo ncell)"

# --- comprobación ------------------------------------------------------------
if ! "$PREFIX/bin/ncell" id >/dev/null 2>&1; then
  echo "✗ la instalación no funciona: 'ncell id' falló" >&2
  exit 1
fi
say "verificado:  ncell id responde"

# --- servicio ----------------------------------------------------------------
if [[ -n "$ANNOUNCE" ]]; then
  command -v systemctl >/dev/null || { echo "✗ systemd no disponible" >&2; exit 1; }
  echo
  echo "Relevo como servicio"
  id -u nullcell >/dev/null 2>&1 || $SUDO useradd --system --home-dir /var/lib/nullcell \
    --create-home --shell /usr/sbin/nologin nullcell
  say "usuario:     nullcell (sin shell, sin privilegios)"

  UNIT=/etc/systemd/system/nullcell-relay.service
  $SUDO sed "s|CAMBIAME:4433|$ANNOUNCE|; s|/opt/nullcell|$PREFIX|" \
    "$REPO_ROOT/deploy/nullcell-relay.service" | $SUDO tee "$UNIT" >/dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now nullcell-relay
  say "servicio:    nullcell-relay activo, anunciando $ANNOUNCE"
  echo
  echo "La línea de semilla que das a otros operadores:"
  sleep 2
  $SUDO journalctl -u nullcell-relay --no-pager 2>/dev/null | grep -o 'seed line for others: .*' | tail -1 \
    || say "(todavía no en el log; prueba: journalctl -u nullcell-relay | grep 'seed line')"
  echo
  say "abre el puerto UDP $(echo "$ANNOUNCE" | awk -F: '{print $NF}') en tu cortafuegos"
else
  echo
  echo "Para operar un relevo —que es lo que hace que exista una red (§1):"
  say "$PREFIX/bin/ncell relay --announce TU_IP:4433"
  say "o vuelve a ejecutar esto con: ./install.sh --relay TU_IP:4433"
fi
