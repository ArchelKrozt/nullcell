# Nullcell

[![CI](https://github.com/ArchelKrozt/nullcell/actions/workflows/ci.yml/badge.svg)](https://github.com/ArchelKrozt/nullcell/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/licencia-AGPL--3.0-blue)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)

Red P2P cifrada y anónima: circuitos onion de 3 saltos sobre QUIC, sin autoridades de directorio.
Direcciones `.null` autocertificadas. Python 3.11+, sin root.

**Estado: M7.** `https://panaderia.null` funciona conociendo solo el nombre, y el HUD local es
también el buscador: portales con sus sellos criptográficos, topología del DHT, y búsqueda cuyo
orden **se explica** en lugar de darte un número opaco. El portal no tiene IP pública, ni UPnP, ni
un solo puerto abierto.

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

## Por dónde empieza todo

| Quieres… | Fichero / comando |
|---|---|
| **entender el proyecto** | [`NULLCELL-ARQUITECTURA.md`](NULLCELL-ARQUITECTURA.md) — el documento del que todo lo demás sale. Las referencias `§N` del código apuntan ahí |
| **instalarlo** | [`deploy/install.sh`](deploy/install.sh) |
| **levantar un relevo** (lo que hace que exista una red) | `ncell relay --announce TU_IP:4433` |
| **el punto de entrada del programa** | [`nullcell/cli/main.py`](nullcell/cli/main.py) → `app`, declarado en `pyproject.toml` como el binario `ncell` |
| **el relevo en sí** | [`nullcell/cli/relay.py`](nullcell/cli/relay.py) |
| **cómo se ensambla una celda** | [`nullcell/cli/runtime.py`](nullcell/cli/runtime.py) |
| **operar un relevo en serio** | [`docs/RELAY_OP.md`](docs/RELAY_OP.md) |
| **montarlo en un VPS y entrar desde el teléfono** | [`docs/VPS.md`](docs/VPS.md) |
| **el formato de cable** | [`docs/PROTOCOL.md`](docs/PROTOCOL.md) |

## Instalación

### Rápida

```bash
git clone https://github.com/ArchelKrozt/nullcell
cd nullcell
./deploy/install.sh
```

### A mano

```bash
python3 -m venv ~/.venvs/nullcell          # fuera de carpetas sincronizadas
~/.venvs/nullcell/bin/pip install ".[dev]"
~/.venvs/nullcell/bin/ncell --version
```

> Con `pip install -e` (editable), si mueves o renombras la carpeta hay que **reinstalar**: la
> instalación editable graba la ruta absoluta en el entorno virtual.

## Levantar la primera red

Una red de Nullcell **son relevos**. Sin ellos no hay nada, y por eso el primer paso de cualquier
despliegue es levantar uno.

```bash
# 1. el primer relevo de la red: no tiene semillas, y eso es correcto
ncell relay --announce TU_IP_PUBLICA:4433

# 2. te imprime la línea que das a los demás
ncell relay --announce TU_IP_PUBLICA:4433 --print-seed
# jkssaepy2lvvfvds3paf3l42qgkxubxmsgw3tubcbmar56p7anzbcsyb@TU_IP:4433

# 3. el segundo operador arranca así
ncell relay --announce SU_IP:4433 --seed LA_LINEA_DE_ARRIBA
```

Una semilla **no es un secreto ni una credencial**: §7 dice que es una *presentación*, nada más.
No firma nada, no avala nada, y todo registro aprendido a través de ella se verifica por separado.
Publícala donde quieras.

Necesitas **un puerto UDP abierto** (QUIC va sobre UDP) y una IP alcanzable. No necesitas root, ni
dominio, ni certificado. Todo lo demás está en [`docs/RELAY_OP.md`](docs/RELAY_OP.md), incluida la
parte legal.

### Como servicio

```bash
./deploy/install.sh --relay TU_IP:4433     # systemd, usuario sin privilegios
journalctl -u nullcell-relay -f
```

`systemctl stop` envía `SIGTERM`, que **vacía** los circuitos vivos antes de salir en lugar de
tirarlos de golpe (§10).

### Con Docker

```bash
cd deploy
ANNOUNCE=tu.ip:4433 docker compose up -d
docker compose logs | grep "seed line"
```

> **Sin construir:** el `Dockerfile` y los `compose` están escritos y validados sintácticamente,
> pero en esta máquina no hay Docker, así que **nadie ha ejecutado `docker build`**. Si lo haces y
> falla, es un fallo real y conviene saberlo.

### Laboratorio

```bash
docker compose -f deploy/docker-compose.lab.yml up --build   # 5 relevos que se encuentran solos
```

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

ncell relay --announce TU_IP:4433                                     # prestar ancho de banda
ncell host ./mi-sitio --name panaderia --title "La Panadería"         # publicar un portal
ncell host app.py --wsgi app:application                              # o una app WSGI
ncell browse panaderia.null                                          # abrir el navegador
ncell resolve panaderia.null                                         # ver sus puntos de enlace
```

`ncell host` y `ncell browse` eligen sus rutas con la selección de §7. Un nodo recién instalado
**tiene que esperar 10 minutos** antes de poder construir un circuito: es el suelo de antigüedad
observada, y el mensaje de error lo dice en lugar de fallar en seco.

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
| Descriptores de portal, publicación y renovación automática (§8) | ✅ |
| Nombres legibles resueltos en el DHT, alias locales, nombres reservados (§8, §9) | ✅ |
| Selección de ruta de §7 en uso real: un solo sitio elige saltos | ✅ |
| HTTP/2 hacia el navegador (§16, táctica 7) | ❌ — reclasificada, ver §16 |
| Buscador: índice FTS5, lookups DHT concurrentes, ranking explicable (§8) | ✅ |
| Gossip de observaciones firmadas con Bloom filters (§8, §9) | ✅ |
| Crawler educado sobre circuitos, `robots.txt` obligatorio (§8) | ✅ |
| HUD completo: portales con sellos, topología, buscador (§9) | ✅ |
| Relleno `PADDING`: las dos máquinas de §5 | ✅ |
| `ncell relay`: apagado elegante, límites, línea de semilla (§1, §10) | ✅ |
| Despliegue: Docker, systemd, instalador, `docs/RELAY_OP.md` (§12) | ✅ sin construir la imagen |
| Proxy HTTP en `:9151`, sólo `CONNECT` (§8) | ✅ |
| TUI (§13, M8) | ❌ |
| Listas de bloqueo firmadas y suscribibles (§10) | ❌ |
| Modo TUN y navegador embebido (§13, M8, opcionales) | ❌ |
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
