#!/usr/bin/env bash
# =============================================================================
#  Probar el Python que va DENTRO del APK, antes de publicarlo
# =============================================================================
#  Saca del APK los paquetes que instaló Chaquopy (los mismos .pyc que correrán
#  en el teléfono) y, en un python:3.12, cambia sólo lo nativo de arm64
#  (cryptography, PyNaCl, cffi) por las MISMAS versiones para este servidor.
#  Luego:
#    1. comprueba que cada paquete acepte las versiones que lleva al lado
#       (así se habría visto service_identity 26, que exige cryptography>=47);
#    2. arranca el nodo con el mismo puente que usa la app y espera su API.
#
#    bash android/verificar-apk.sh [ruta.apk]
# =============================================================================
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
VERSION=$(grep -oP 'versionName\s*=\s*"\K[^"]+' "$AQUI/app/build.gradle.kts")
APK="$(realpath "${1:-$AQUI/salida/nullcell-$VERSION.apk}")"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/req" "$T/app"
unzip -q "$APK" 'assets/chaquopy/*' -d "$T/x"
for z in "$T"/x/assets/chaquopy/requirements-*.imy; do unzip -q -o "$z" -d "$T/req" 2>/dev/null || true; done
unzip -q "$T/x/assets/chaquopy/app.imy" -d "$T/app"

docker run --rm -e PROFUNDO="${PROFUNDO:-0}" -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$T":/t python:3.12-slim sh -eu -c '
  cd /t/req
  NATIVOS=""
  for d in cryptography-*.dist-info PyNaCl-*.dist-info cffi-*.dist-info; do
    n=${d%.dist-info}; NATIVOS="$NATIVOS ${n%-*}==${n##*-}"
  done
  rm -rf cryptography cryptography-* nacl PyNaCl-* cffi cffi-* _cffi_backend*.so
  pip install -q --user --no-warn-script-location --only-binary :all: $NATIVOS packaging
  python - <<PY
import sys
sys.path[:0] = ["/t/app", "/t/req"]
from importlib.metadata import distributions, version, PackageNotFoundError
from packaging.requirements import Requirement

print("▸ dependencias")
malas = []
for d in distributions(path=["/t/req"]):
    for r in d.requires or []:
        req = Requirement(r)
        if req.marker and not req.marker.evaluate({"extra": ""}):
            continue
        try:
            v = version(req.name)
        except PackageNotFoundError:
            malas.append(f"{d.name} pide {req} y no está"); continue
        if req.specifier and not req.specifier.contains(v, prereleases=True):
            malas.append(f"{d.name} pide {req} y lleva {v}")
for m in malas: print("  ✗", m)
if malas: sys.exit(1)
print("  ✓ todas las versiones encajan")

print("▸ arrancar el nodo con el puente de la app")
import json, tempfile, time, urllib.request
import nullcell_movil as m
info = json.loads(m.arrancar(tempfile.mkdtemp(), hud_port=19180, port=14433))
q = urllib.request.Request(info["hud"] + "/api/state", headers={"X-Nullcell-Token": info["token"]})
estado = None
for _ in range(30):
    try:
        estado = json.load(urllib.request.urlopen(q, timeout=5)); break
    except Exception as e:
        err = e; time.sleep(1)
if estado is None:
    m.detener(); sys.exit(f"  ✗ la API del nodo no contestó: {err}")
print("  ✓ nodo en marcha:", info["nid"][:12] + "…")

import os
if os.environ.get("PROFUNDO") == "1":
    print("▸ abrir nullcell.null por la red real (con la semilla incluida)")
    r = {}
    for _ in range(24):
        r = m.cargar("http://nullcell.null/")
        if r.get("status") == 200: break
        time.sleep(5)
    marca = "✓" if r.get("status") == 200 else "✗"
    print(f"  {marca} estado {r.get("status")} · {len(r.get("body") or "")} bytes")
    if r.get("status") != 200: m.detener(); sys.exit(1)
m.detener()
PY
'
echo "✓ el Python del APK arranca"
