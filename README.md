# Nullcell

Red P2P cifrada y anónima: circuitos onion de 3 saltos sobre QUIC, sin autoridades de directorio.
Direcciones `.null` autocertificadas. Python 3.11+, sin root.

**Estado: M5.** Un navegador abre `https://<nid>.null` con candado, sobre 6 saltos, contra un
portal que **no tiene IP pública, ni UPnP, ni un solo puerto abierto**. Una página con 20 recursos
carga en **129 ms** en laboratorio, y las 20 peticiones viajan por **una sola sesión de
rendezvous**.

> ### ⚠ Qué está verificado y qué sigue abierto
>
> **Verificado en los tests, no inferido:** el salto del medio de un circuito no conoce origen ni
> destino; una trama manipulada derriba el circuito; dos extremos se hablan sin abrir puertos; el
> punto de encuentro reenvía bytes que no puede leer; un nodo del DHT verifica un descriptor
> privado sin saber de quién es; y una consulta de DHT por circuito no lleva nada del que pregunta.
>
> **Sigue abierto, y es el límite de fondo del proyecto (§7):** la selección descentralizada de
> relevos es un problema abierto de investigación. Lo que hay es la mitigación pragmática —guards
> fijados, diversidad de prefijos, antigüedad mínima, PoW— **no una solución demostrada** frente a
> ataques Sybil y de eclipse. Y la correlación de tráfico extremo a extremo no está resuelta, igual
> que en Tor.
>
> **Lo que no está probado aquí:** el lanzamiento real de Firefox. La cadena completa —SOCKS5 con
> nombre, TLS validado contra la CA local, HTTP sobre 6 saltos— está verificada con un cliente que
> hace exactamente lo que hace un navegador, pero **el perfil y la importación de la CA necesitan
> un Firefox de verdad** y no se han ejecutado. `ncell browse` los monta; comprobarlo te toca a ti.
>
> La importación de la CA necesita `certutil` de las herramientas NSS (macOS: `brew install nss`).
> Si falta, `ncell browse` lo dice e imprime el comando, en lugar de fallar en silencio o —peor—
> sugerir el almacén del sistema.

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

ncell host ./mi-sitio --relay <nid>@host:puerto:<x25519-hex> ...      # publicar un portal
ncell browse <nid> --relay ... --service-key <hex>                    # abrir el navegador
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
| L2b rendezvous, puntos de enlace, 6 saltos extremo a extremo (§6) | ✅ |
| Servicios privados: índice cegado y cuerpo cifrado (§6) | ✅ |
| DHT por circuitos, con circuito de directorio reutilizado (decisión 8) | ✅ |
| L3/L4 streams de app multiplexados sobre una sesión (§8) | ✅ |
| `ncell host`: carpeta servida sobre circuitos, cero port forwarding (§8) | ✅ |
| SOCKS5 con resolución remota obligatoria (§8) | ✅ |
| CA local con restricción de nombres, solo en el perfil dedicado (§8) | ✅ |
| `ncell browse`: perfil aislado de Firefox (§8) | ✅ sin probar con Firefox real |
| HTTP/2 hacia el navegador (§16, táctica 7) | ❌ — reclasificada, ver §16 |
| Diversidad por ASN (§7) | ❌ — ver §7, el motivo está documentado |
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
