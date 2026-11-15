"""Aislamiento de la suite: ninguna prueba habla con la red real.

`Runtime.assemble` añade siempre la semilla del proyecto (el servidor real,
`nullcell/bootstrap.json`). Las pruebas que hacían `bootstrap(seed_nodes())` o
`verify_seeds()` se presentaban así al servidor de verdad, que aprendía nodos
de prueba con direcciones 127.0.0.1. Aquí se apaga para toda la suite; la
prueba que comprueba que la semilla viene en el paquete lo pide con la marca
`semilla_real`.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _sin_semilla_real(request, monkeypatch):
    if request.node.get_closest_marker("semilla_real"):
        return
    from nullcell.discovery import bootstrap

    monkeypatch.setattr(bootstrap, "load_bootstrap", lambda *a, **k: [])
