# Nullcell

Red P2P cifrada y anónima: circuitos onion de 3 saltos sobre QUIC, sin autoridades de directorio.
Direcciones `.null` autocertificadas. Python 3.11+, sin root.

**Estado: M3.** Identidad, marcos, enlace QUIC verificado, DHT Kademlia y **circuitos onion de
3 saltos** funcionan, con selección de ruta, control de flujo y un HUD de depuración.

> ### ⚠ Aún no la uses para nada que requiera anonimato
>
> Los circuitos existen y sus propiedades están verificadas en los tests: el salto del medio no
> conoce origen ni destino, y una trama manipulada derribael circuito. **Pero faltan dos cosas:**
>
> 1. **El DHT todavía no va por circuitos.** §7 decidió que sí, y `dht/transport.py` es la costura
>    preparada para ello, pero hoy `DirectTransport` consulta por enlace directo y revela tu IP y
>    qué buscas a nodos cualesquiera.
> 2. **No hay rendezvous (M4),** así que no hay servicios ocultos ni anonimato del lado que
>    publica.
>
> Y el límite de fondo de §7 sigue en pie: la selección descentralizada de relevos es un problema
> abierto de investigación. Lo que hay es la mitigación pragmática, no una solución demostrada.

La arquitectura completa está en [`NULLCELL-ARQUITECTURA.md`](NULLCELL-ARQUITECTURA.md). Las
referencias `§N` de este README y del código apuntan a ese documento.

---

## Límites honestos

Antes que nada, porque definen el proyecto:

- **Más celdas no dan más velocidad.** Dan disponibilidad, diversidad de rutas y anonimato. La
  latencia sube con cada salto: cliente↔servicio son **6 saltos**, ~500-800 ms de ida y vuelta.
- **Rendimiento:** el techo medido hoy es **~21 MB/s por enlace** (1 núcleo, loopback), y lo fija
  el procesado de QUIC en Python puro de `aioquic`, no el diseño del protocolo (§16, hallazgos de
  M1). Nullcell no va a dar el ancho de banda de tu línea.
- **La red necesita relevos públicos voluntarios.** Puedes navegar y publicar sin abrir puertos,
  pero alguien tiene que prestar ancho de banda alcanzable. Sin relevos no hay red.
- **La selección descentralizada de relevos es un problema abierto de investigación.** Tor usa un
  consenso firmado por 9 autoridades; nosotros no queremos autoridades. La mitigación de §7 (PoW,
  diversidad de prefijos, verificación cruzada, listas opt-in) es pragmática, **no una solución
  demostrada** frente a ataques Sybil y de eclipse.
- **Correlación de tráfico extremo a extremo: no resuelta.** Tampoco en Tor. Mitigada con guards
  fijos, marcos de tamaño uniforme y relleno.
- **Un adversario global que observe toda la red está fuera del modelo de amenaza.**
- **Migrar a cripto post-cuántica exigirá rotar direcciones**, porque la dirección lleva la clave
  pública (§3). Igual que en Tor v3.
- **Nota legal, sin rodeos:** una red anónima atrae también contenido ilegal, y operar un relevo
  público puede traer reclamos al operador. El relevo es **opt-in explícito** y el proyecto no
  incluye herramientas de evasión de detección.

## Instalación

```bash
python3 -m venv ~/.venvs/nullcell          # fuera de carpetas sincronizadas
~/.venvs/nullcell/bin/pip install -e ".[dev]"
```

> Si mueves o renombras la carpeta del proyecto, **reinstala**: una instalación editable graba la
> ruta absoluta en el entorno virtual y `ncell` deja de funcionar hasta que corras
> `pip install -e .` desde la ruta nueva.

> **No pongas `~/.nullcell/` en iCloud, Dropbox ni OneDrive.** Sincronizar `identity.key` saca tu
> clave privada de la máquina y sincronizar las bases SQLite las corrompe. `ncell` avisa si lo
> detecta.

## Uso

```bash
ncell id                                   # tu identidad y tu dirección
ncell addr <nid>                           # valida una dirección, sin tocar la red
ncell listen --port 4433                   # solo enlace, sin DHT
ncell ping <nid> --addr 127.0.0.1:4433     # enlace verificado + RTT

ncell node --port 4433 --announce IP:4433 --seed <nid>@host:puerto   # celda completa
ncell publish panaderia --announce IP:4433 --seed <nid>@host:puerto  # registra nombre
ncell resolve panaderia.null --seed <nid>@host:puerto                # busca en el DHT
ncell node --hud                                                     # con panel local
```

`--seed` es necesario mientras `bootstrap.json` esté vacío: no hay red pública todavía.

Dos celdas en la misma máquina:

```bash
NULLCELL_HOME=/tmp/a ncell id              # copia el NID
NULLCELL_HOME=/tmp/a ncell listen --host 127.0.0.1 --port 4455 &
NULLCELL_HOME=/tmp/b ncell ping <NID-de-a> --addr 127.0.0.1:4455
```

`--addr` es temporal: hasta el DHT de M2 no hay forma de resolver un NID a un endpoint.

## Qué funciona y qué no

| Capa | Estado |
|---|---|
| L0 identidad Ed25519 + X25519, direcciones de 56 chars (§3) | ✅ |
| L1a enlace QUIC, certificado ligado al NID, verificación de huella (§4) | ✅ |
| Marcos de 512 B, subcabecera `RELAY`, negociación de versión (§5) | ✅ |
| L1b Kademlia, descriptores firmados con PoW, bootstrap y PEX (§7) | ✅ |
| Nombres legibles `panaderia.null` first-come en el DHT (§8) | ✅ |
| Baliza multicast en LAN, opt-in (§7) | ✅ |
| L2a circuitos onion de 3 saltos, Noise NK, digest anti-etiquetado (§5) | ✅ |
| Selección de ruta: guards fijados, diversidad de prefijos, antigüedad mínima (§7) | ✅ |
| SENDME por circuito y por stream, vida del circuito (§5) | ✅ |
| HUD mínimo de depuración, `ncell node --hud` (§9) | ✅ |
| Diversidad por ASN (§7) | ❌ — ver §7, el motivo está documentado |
| DHT por circuitos (decisión 8) | ❌ M4 |
| L2b rendezvous, intro points (§6) | ❌ M4 |
| SOCKS5, CA local, `ncell browse`, `ncell host` (§8) | ❌ M5 |
| Buscador y HUD (§8, §9) | ❌ M7 |

**El cliente es anónimo ya en la capa de enlace** (§4): el relevo prueba quién es con su
certificado, el cliente no presenta ninguno. Esa asimetría es deliberada. Pero el anonimato
*frente a la red* necesita los circuitos de M3 — ver el aviso de arriba.

## Desarrollo

```bash
~/.venvs/nullcell/bin/python -m pytest -q                # toda la suite
~/.venvs/nullcell/bin/python -m pytest -q -m "not slow"  # sin la malla de 30 celdas
~/.venvs/nullcell/bin/python bench/link_throughput.py    # puertas de §16
~/.venvs/nullcell/bin/ruff check . && ruff format --check .
```

La prueba de éxito de M2 (§13) es `test_thirty_cells_find_each_other_and_route`, marcada `slow`:
30 celdas en un proceso, con QUIC real, que arrancan desde una semilla y resuelven un descriptor
que la celda buscadora no tiene en local.

## Licencia

AGPL-3.0-or-later — obliga a publicar las modificaciones de los nodos que operen en red.
