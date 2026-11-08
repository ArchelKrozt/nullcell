# Nullcell

Red P2P cifrada y anónima: circuitos onion de 3 saltos sobre QUIC, sin autoridades de directorio.
Direcciones `.null` autocertificadas. Python 3.11+, sin root.

**Estado: M1.** Identidad, marcos y enlace QUIC verificado funcionan. Todavía **no hay circuitos
onion** (M3) ni rendezvous (M4): hoy esto no es una red anónima, es su capa de enlace.

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

> **No pongas `~/.nullcell/` en iCloud, Dropbox ni OneDrive.** Sincronizar `identity.key` saca tu
> clave privada de la máquina y sincronizar las bases SQLite las corrompe. `ncell` avisa si lo
> detecta.

## Uso (M1)

```bash
ncell id                                   # tu identidad y tu dirección
ncell addr <nid>                           # valida una dirección, sin tocar la red
ncell listen --port 4433                   # celda alcanzable
ncell ping <nid> --addr 127.0.0.1:4433     # enlace verificado + RTT
```

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
| L1b Kademlia, descriptores, bootstrap (§7) | ❌ M2 |
| L2a circuitos onion de 3 saltos (§5) | ❌ M3 |
| L2b rendezvous, intro points (§6) | ❌ M4 |
| SOCKS5, CA local, `ncell browse`, `ncell host` (§8) | ❌ M5 |
| Buscador y HUD (§8, §9) | ❌ M7 |

**El cliente es anónimo ya en la capa de enlace** (§4): el relevo prueba quién es con su
certificado, el cliente no presenta ninguno. Esa asimetría es deliberada.

## Desarrollo

```bash
~/.venvs/nullcell/bin/python -m pytest -q          # 49 pruebas
~/.venvs/nullcell/bin/python bench/link_throughput.py   # puerta de rendimiento de §16
~/.venvs/nullcell/bin/ruff check .
```

## Licencia

AGPL-3.0-or-later — obliga a publicar las modificaciones de los nodos que operen en red.
