# Nullcell

[![CI](https://github.com/ArchelKrozt/nullcell/actions/workflows/ci.yml/badge.svg)](https://github.com/ArchelKrozt/nullcell/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Android](https://img.shields.io/github/v/release/ArchelKrozt/nullcell?filter=android-v*&include_prereleases&label=android)](https://github.com/ArchelKrozt/nullcell/releases)

**[English](#english) · [Español](#español)**

---

<a id="english"></a>

## English

An encrypted, anonymous peer-to-peer network: 3-hop onion circuits over QUIC, with no directory
authorities. Self-certifying `.null` addresses. Python 3.11+, no root required.

`https://nullcell.null` works knowing only its name, a portal needs no public IP and no open port,
and the network starts working **with a single seed** — anonymity switches itself on as soon as
there are three independent operators.

The full architecture is in [`NULLCELL-ARQUITECTURA.md`](NULLCELL-ARQUITECTURA.md) (Spanish).
`§N` references in this README and in the code point to that document.

> ### ⚠ What is verified and what is still open
>
> **Verified by tests, not inferred:** the middle hop of a circuit knows neither origin nor
> destination; a tampered frame tears the circuit down; two endpoints talk without opening ports;
> the rendezvous point forwards bytes it cannot read; a DHT node verifies a private descriptor
> without knowing whose it is; a DHT lookup over a circuit carries nothing about who asked.
>
> **Still open, and the fundamental limit of the project (§7):** decentralized relay selection is
> an open research problem. What exists is the pragmatic mitigation — pinned guards, prefix
> diversity, minimum observed age, proof of work — **not a proven solution** against Sybil and
> eclipse attacks. End-to-end traffic correlation is not solved either, same as in Tor.

### Honest limits

- **More cells don't mean more speed.** They give availability, path diversity and anonymity.
  Latency grows with every hop: client↔service is **6 hops** with full anonymity, ~500–800 ms RTT.
- **Throughput:** the measured ceiling is **~21 MB/s per link** (one core, loopback), set by
  `aioquic`'s pure-Python QUIC processing, not by the protocol (§16).
- **The network needs volunteer public relays.** You can browse and publish without opening
  ports, but someone has to lend reachable bandwidth.
- **Decentralized relay selection is an open problem** (see above). Tor uses a consensus signed by
  9 authorities; Nullcell deliberately has none.
- **A global adversary watching the whole network is outside the threat model.**
- **Moving to post-quantum crypto will require rotating addresses**, because the address *is* the
  public key (§3) — same as Tor v3.
- **Legal note:** an anonymous network also attracts illegal content, and running a public relay
  may bring complaints to the operator. Relaying is **explicit opt-in**, and the project ships no
  detection-evasion tooling.

### Where to start

| You want to… | File / command |
|---|---|
| **browse from your phone** | the [Android app](#android-app) |
| **understand the project** | [`NULLCELL-ARQUITECTURA.md`](NULLCELL-ARQUITECTURA.md) |
| **install it** | [`deploy/install.sh`](deploy/install.sh) |
| **use it without a terminal** (tray icon, panel, browser) | `ncell desktop` — see [Desktop](#desktop) |
| **just browse, without relaying** | `ncell browse` |
| **run a relay** (what makes a network exist) | `ncell relay --announce YOUR_IP:4433` |
| **run a relay seriously** | [`docs/RELAY_OP.md`](docs/RELAY_OP.md) |
| **deploy on a VPS** | [`docs/VPS.md`](docs/VPS.md) |
| **the wire format** | [`docs/PROTOCOL.md`](docs/PROTOCOL.md) |
| **the program's entry point** | [`nullcell/cli/main.py`](nullcell/cli/main.py) → `ncell` |
| **how a cell is assembled** | [`nullcell/cli/runtime.py`](nullcell/cli/runtime.py) |

### Installation

```bash
git clone https://github.com/ArchelKrozt/nullcell
cd nullcell
./deploy/install.sh
```

Or by hand:

```bash
python3 -m venv ~/.venvs/nullcell          # outside synced folders
~/.venvs/nullcell/bin/pip install ".[dev]"
~/.venvs/nullcell/bin/ncell --version
```

> **Don't put `~/.nullcell/` in iCloud, Dropbox or OneDrive.** Syncing `identity.key` takes your
> private key off the machine, and syncing the SQLite databases corrupts them. `ncell` warns if it
> detects it.

### A small network: one seed is enough

The project's seed (this repository's server, which hosts `nullcell.null`) ships inside the
package in [`nullcell/bootstrap.json`](nullcell/bootstrap.json), so a fresh install can enter the
network with no configuration. Everything defaults to `--hops auto`:

| distinct operators | hops | anonymity | 10-min wait |
|---|---|---|---|
| 1 (only the seed) | 1 — the circuit goes direct | no | no |
| 2 | 2 | no | no |
| 3 or more, observed 10 min | 3 | **yes, automatically** | yes |

- It is recomputed on every circuit, so a cell running for hours **switches to 3 hops as soon as
  the third operator appears**, without restarting.
- It counts **operators, not machines**: three relays at the same provider are one hop.
- If the server hosting a portal has **no other relay** that can carry circuits, the portal is
  **its own introduction point**. The connection is still end-to-end encrypted (QUIC between cells,
  Noise NK between browser and portal, TLS against the local CA); what's missing is anonymity.
  When foreign relays appear, the portal moves to the normal path by itself.
- Every interface says so with a lock: **🔒 anonymous** or **⚠ not anonymous**.
- `--hops 3` still exists for anyone who never wants to connect without anonymity.

**The price, stated plainly:** with one hop, that relay sees both your IP and which portal you
visit. With two, the first knows who you are and the second where you go. Content stays encrypted
end to end; what you lose is **who talks to whom** — exactly what Nullcell exists to protect.

**The 10-minute floor (`--min-age`).** A relay isn't used until *your* cell has observed it for 10
minutes: nobody can fake how long you've been watching them, so it makes flooding the network with
new identities expensive (§7, §10). In `auto` it only applies once there is anonymity to protect.

**A cell doesn't count itself.** With N machines, whoever *publishes* has only N−1 relays to route
through — using its own node as introduction point would announce its own address. That's why the
self-introduction above is the explicit, labelled exception for a network of one.

### Seeds that circulate

A seed is a line like `wuxq7z…ih7ib@62.171.160.130:4433`. It is **not a secret or a credential**:
an introduction and nothing more (§7). Everything learned through it is verified separately.

- Cells **verify** the seeds you add (they must answer with their identity) and **exchange** them
  with relays, receiving the ones they're missing. Each new user enters through more doors, and
  the network stops depending on one.
- Limits against eclipse attacks: few seeds per offer, few per network prefix, and seeds that keep
  failing are dropped.
- **Invitations** carry how to enter and which portal, in one line:

```bash
ncell invite --announce your-server.com:4433 --portal nullcell
# nullcell://join?s=kopxc7…3fab@your-server.com:4433&p=nullcell
```

They are readable on purpose (you see which cells you'll connect to *before* pasting), carry
several seeds (no single point of failure), and sign nothing.

After the first entry the cell remembers who it met and rejoins by itself, without a seed.

### Running a relay

```bash
ncell relay --announce YOUR_PUBLIC_IP:4433                 # first relay of a network
ncell relay --announce YOUR_PUBLIC_IP:4433 --print-seed    # the line you give others
ncell relay --announce THEIR_IP:4433 --seed THAT_LINE      # second operator
```

You need **one open UDP port** (QUIC is UDP) and a reachable IP. No root, domain or certificate.

```bash
./deploy/install.sh --relay YOUR_IP:4433     # systemd, unprivileged user
journalctl -u nullcell-relay -f
```

`systemctl stop` sends `SIGTERM`, which **drains** live circuits instead of dropping them (§10).

**Docker:** `cd deploy && ANNOUNCE=your.ip:4433 docker compose up -d`. The project's own server
runs in a hardened container (read-only root, no capabilities, own network, only 4433/udp
published). A 5-relay lab: `docker compose -f deploy/docker-compose.lab.yml up --build`.

### Android app

A `.null` browser for Android **with its own node**: the phone builds its own circuits, like the
desktop. Nobody on the path — the project server included — sees which portals you open.

**Download:** [latest release](https://github.com/ArchelKrozt/nullcell/releases) →
`nullcell-<version>.apk` (Android 8+, 64-bit ARM). Open it on the phone and allow "install unknown
apps".

| Tab | |
|---|---|
| **Home** | Live status, anonymity ring (operators / 3), lock, cells, relays, seeds, recent activity |
| **Search** | The network's search engine (by name, word or tag), queried over circuits |
| **Browse** | `.null` browser with address bar, lock, back/forward, reload, menu |
| **Seeds** | Add (paste a line or a `nullcell://` invitation), remove, exchange now, share |
| **Settings** | Your address, auto-anonymity status, updates, what the browser protects, disconnect |

- **One seed is enough**: the project's is bundled. It browses with 1 hop until the network has 3
  operators, then goes anonymous by itself.
- **Nothing goes to the normal internet** from pages: the WebView only loads `.null` addresses; any
  image, font or script from the internet is blocked, since it would leave straight from the phone
  and reveal your IP. No cache, no third-party cookies.
- **The phone doesn't announce itself** or relay traffic: it only browses and shares seeds.
- **It updates itself.** On launch (at most every 6 h) it checks this repository's
  `android-v*` releases; if there's a newer one it asks, downloads it, **verifies the SHA-256
  published by GitHub and that the signature matches the installed app**, and opens Android's
  installer. This is the only thing the app sends to the normal internet (GitHub sees your IP), so
  it can be turned off in Settings.

The engine is the same Python code, embedded with [Chaquopy](https://chaquo.com/chaquopy/); the UI
is Kotlin + Jetpack Compose. Building, signing and publishing: [`android/LEEME.md`](android/LEEME.md).

### Desktop

```bash
pip install 'nullcell[desktop]'
ncell desktop
```

A **NULL** icon appears in the menu bar. Its panel has a switch to share bandwidth, **publish a
folder** as a `.null` portal (no ports opened), and the network's **browser** with search and the
dashboard. Closing a window doesn't kill the node, and your choices are remembered.

No terminal needed: double-click [`Nullcell.command`](Nullcell.command) on macOS, or run
`python3 nullcell-escritorio.py`; `ncell launcher` creates a `Nullcell.app`.

The browser needs no certificate authority: it registers the `null://` scheme and hands circuit
bytes straight to the renderer. Each address is its own origin, so portals stay isolated. The local
CA (§8) exists for those who prefer Firefox over SOCKS (`ncell browse`).

> **PySide6 is pinned to 6.11.3 on purpose:** Qt stopped publishing QtWebEngine for macOS in 6.12.
> `ncell desktop` doesn't run on a Raspberry Pi (no Qt wheels for ARM) — use `ncell node` there.

### Publishing a named portal

Everything that defines your presence fits in one folder, so moving to another machine is a copy:

```
my-deployment/
  nullcell.toml     what YOU declare: what to publish, under which name, title, tags
  estado.json       what the program changes while running
  identity.key      (optional) this cell's identity
```

```bash
ncell desktop --config ./my-deployment                              # with a UI
ncell node --config ./my-deployment --announce your-ip:4433 --relay # headless (server, Pi)
```

`nullcell.toml` is only read, never rewritten, so your comments survive. Format and moving between
machines: [`docs/DESPLIEGUE.md`](docs/DESPLIEGUE.md).

> **The address *is* the key.** `nullcell.null` is a DHT name bound to an Ed25519 key, and the
> 56-character address **is that key** (§3). Copy the folder **without `identity.key`** and the new
> machine is a different cell with a different address.

- The portal's **name, title, description and tags are published as search keywords**, renewed
  automatically, so the network's search engine finds it by any of them (accents and case don't
  matter).
- If you switch the machine off, the name survives; the descriptor expires within an hour, and
  visitors see **"the portal exists but is offline"**, not a generic error.
- Visiting your own portal works from the first minute, labelled **"your portal · local"**.

### Just browsing

```bash
ncell browse                         # the project seed is bundled
ncell browse --seed <seed-line>      # add others so you don't depend on one
```

A client **opens no port**, needs no public IP, works behind NAT and **publishes nothing**. It
presents no certificate either: link authentication is one-sided, so no relay sees your long-term
key.

**The only non-anonymous step is bootstrap**: you must talk to a seed directly to learn that relays
exist, just as a Tor client downloads its first consensus directly. From then on, including **which
names you resolve**, everything goes over circuits (§7, decision 8).

Nullcell doesn't use Tor and can't be tunnelled through it: it runs on **QUIC (UDP)** and Tor only
carries TCP.

### IRC-style chat

In the dashboard there is a chat: join a name, see who's there and what's said **while you're
there**. Presence is a short-TTL DHT record under the channel key; after that there is **no
server** — every member opens a rendezvous session with the others. Messages from before you joined
don't arrive, it scales to a group (not a crowd), being in a channel is public, and chat uses its
own identity (`chat.key`) separate from your portal's. Every message is signed.

### Updates (desktop / server)

```bash
ncell update            # checks for a new version, and warns before contacting anything
ncell update --no-check # only explains how to update, no network
```

The desktop and relay **never check by themselves**: a periodic request would tell a third-party
server that a Nullcell node lives at that IP, every day. Typing the command *is* the consent. (The
Android app does check, because installing APKs by hand is what people forget — and it can be
switched off.)

### Usage

```bash
ncell id                                   # your identity and address
ncell addr <nid>                           # validate an address, offline
ncell ping <nid> --addr 127.0.0.1:4433     # verified link + RTT

ncell node --announce IP:4433 --hud        # full cell with local dashboard
ncell relay --announce YOUR_IP:4433        # lend bandwidth
ncell host ./my-site --name bakery --title "The Bakery"   # publish a portal
ncell host app.py --wsgi app:application                   # or a WSGI app
ncell publish bakery --announce IP:4433                    # register a name
ncell resolve bakery.null                                  # look it up in the DHT
ncell browse bakery.null                                   # open the browser
ncell invite --announce IP:4433 --portal bakery            # one-line invitation
```

`--seed` is optional everywhere. `--hops` is `auto` by default on `node`, `browse` and
`desktop`; `ncell host` always uses 3 hops (for a small network, publish with `ncell node --config`).

### Status

| Layer | Status |
|---|---|
| L0 Ed25519 + X25519 identity, 56-char addresses (§3) | ✅ |
| L1a QUIC link, NID-bound certificate, fingerprint check, keepalive (§4) | ✅ |
| 512 B cells, `RELAY` subheader, version negotiation (§5) | ✅ |
| L1b Kademlia, signed descriptors with PoW, bootstrap and PEX (§7) | ✅ |
| Readable first-come names `bakery.null` in the DHT (§8) | ✅ |
| L2a 3-hop onion circuits, Noise NK, anti-tagging digest (§5) | ✅ |
| Path selection: pinned guards, prefix diversity, minimum age (§7) | ✅ |
| Automatic hops (`auto`) and single-seed networks | ✅ |
| Seed verification and exchange between cells | ✅ |
| L2b rendezvous, introduction points, 6 hops end to end (§6) | ✅ |
| Private services: blinded index, encrypted body (§6) | ✅ |
| DHT over circuits (decision 8) | ✅ |
| L3/L4 multiplexed app streams over a session (§8) | ✅ |
| `ncell host`, SOCKS5 with mandatory remote resolution, local CA (§8) | ✅ |
| `ncell browse`: isolated Firefox profile (§8) | ✅ not tested with a real Firefox |
| Search: FTS5 index, keyword shards, name lookup, explainable ranking (§8) | ✅ |
| Signed observation gossip with Bloom filters, polite crawler (§8, §9) | ✅ |
| Full HUD and desktop app with embedded browser (§9) | ✅ |
| `PADDING` cover traffic (§5) | ✅ |
| `ncell relay`: graceful shutdown, limits, seed line (§1, §10) | ✅ |
| Deployment: Docker, systemd, installer (§12) | ✅ |
| Android app with its own node and self-update | ✅ not yet tested on many phones |
| HTTP/2 to the browser (§16, tactic 7) | ❌ reclassified, see §16 |
| TUI, TUN mode (§13, M8) | ❌ |
| Signed, subscribable block lists (§10) | ❌ |
| ASN diversity (§7) | ❌ reason documented in §7 |

### Development

```bash
~/.venvs/nullcell/bin/python -m pytest -q                # full suite (~730 tests)
~/.venvs/nullcell/bin/python -m pytest -q -m "not slow"  # without the 30-cell mesh
~/.venvs/nullcell/bin/python bench/link_throughput.py    # §16 gates
~/.venvs/nullcell/bin/ruff check . && ruff format --check .
```

Contributing: [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) · Security reports:
[`docs/SECURITY.md`](docs/SECURITY.md).

### License

AGPL-3.0-or-later — modifications to nodes that run on a network must be published.

---

<a id="español"></a>

## Español

Red P2P cifrada y anónima: circuitos onion de 3 saltos sobre QUIC, sin autoridades de directorio.
Direcciones `.null` autocertificadas. Python 3.11+, sin root.

`https://nullcell.null` funciona conociendo sólo el nombre, un portal no necesita IP pública ni
puertos abiertos, y la red funciona **con una sola semilla**: el anonimato se enciende solo en
cuanto hay tres operadores distintos.

La arquitectura completa está en [`NULLCELL-ARQUITECTURA.md`](NULLCELL-ARQUITECTURA.md). Las
referencias `§N` de este README y del código apuntan a ese documento.

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

### Límites honestos

- **Más celdas no dan más velocidad.** Dan disponibilidad, diversidad de rutas y anonimato. La
  latencia sube con cada salto: con anonimato completo, cliente↔servicio son **6 saltos**,
  ~500-800 ms de ida y vuelta.
- **Rendimiento:** el techo medido es **~21 MB/s por enlace** (1 núcleo, loopback), y lo fija el
  QUIC en Python puro de `aioquic`, no el protocolo (§16).
- **La red necesita relevos públicos voluntarios.** Puedes navegar y publicar sin abrir puertos,
  pero alguien tiene que prestar ancho de banda alcanzable.
- **La selección descentralizada de relevos es un problema abierto** (ver arriba). Tor usa un
  consenso firmado por 9 autoridades; Nullcell no quiere ninguna.
- **Un adversario global que observe toda la red está fuera del modelo de amenaza.**
- **Migrar a cripto post-cuántica exigirá rotar direcciones**, porque la dirección *es* la clave
  pública (§3). Igual que en Tor v3.
- **Nota legal, sin rodeos:** una red anónima atrae también contenido ilegal, y operar un relevo
  público puede traer reclamos al operador. El relevo es **opt-in explícito** y el proyecto no
  incluye herramientas de evasión de detección.

### Por dónde empezar

| Quieres… | Fichero / comando |
|---|---|
| **navegar desde el teléfono** | la [app de Android](#app-de-android) |
| **entender el proyecto** | [`NULLCELL-ARQUITECTURA.md`](NULLCELL-ARQUITECTURA.md) |
| **instalarlo** | [`deploy/install.sh`](deploy/install.sh) |
| **usarlo sin terminal** (icono, panel, navegador) | `ncell desktop` — ver [Escritorio](#escritorio) |
| **sólo navegar, sin aportar un relevo** | `ncell browse` |
| **levantar un relevo** (lo que hace que exista una red) | `ncell relay --announce TU_IP:4433` |
| **operar un relevo en serio** | [`docs/RELAY_OP.md`](docs/RELAY_OP.md) |
| **montarlo en un VPS** | [`docs/VPS.md`](docs/VPS.md) |
| **el formato de cable** | [`docs/PROTOCOL.md`](docs/PROTOCOL.md) |
| **el punto de entrada del programa** | [`nullcell/cli/main.py`](nullcell/cli/main.py) → `ncell` |
| **cómo se ensambla una celda** | [`nullcell/cli/runtime.py`](nullcell/cli/runtime.py) |

### Instalación

```bash
git clone https://github.com/ArchelKrozt/nullcell
cd nullcell
./deploy/install.sh
```

O a mano:

```bash
python3 -m venv ~/.venvs/nullcell          # fuera de carpetas sincronizadas
~/.venvs/nullcell/bin/pip install ".[dev]"
~/.venvs/nullcell/bin/ncell --version
```

> **No pongas `~/.nullcell/` en iCloud, Dropbox ni OneDrive.** Sincronizar `identity.key` saca tu
> clave privada de la máquina y sincronizar las bases SQLite las corrompe. `ncell` avisa si lo
> detecta.

### Red pequeña: con una semilla basta

La semilla del proyecto (el servidor de este repositorio, que aloja `nullcell.null`) viene dentro
del paquete, en [`nullcell/bootstrap.json`](nullcell/bootstrap.json): una instalación nueva entra
a la red sin configurar nada. Todo arranca por defecto con `--hops auto`:

| operadores distintos | saltos | anonimato | espera de 10 min |
|---|---|---|---|
| 1 (sólo la semilla) | 1 — el circuito va directo | no | no |
| 2 | 2 | no | no |
| 3 o más, ya observados 10 min | 3 | **sí, solo** | sí |

- Se recalcula en cada circuito: una celda encendida desde hace horas **pasa a 3 saltos en cuanto
  aparece el tercer operador**, sin reiniciar.
- Cuenta **operadores, no máquinas**: tres relevos del mismo proveedor son un salto.
- Si el servidor que aloja un portal **no tiene ningún otro relevo** que lleve circuitos, el portal
  es **su propio punto de enlace**. La conexión sigue cifrada de extremo a extremo (QUIC entre
  celdas, Noise NK entre navegador y portal, TLS contra la CA local); lo que no hay es anonimato.
  En cuanto aparecen relevos ajenos, el portal pasa solo a la ruta normal.
- Todas las interfaces lo dicen con un candado: **🔒 anónimo** o **⚠ sin anonimato**.
- `--hops 3` sigue existiendo para quien no quiera conectar nunca sin anonimato.

**El precio, escrito:** con un salto, ese relevo ve tu IP y a qué portal vas, las dos cosas a la
vez. Con dos, el primero sabe quién eres y el segundo adónde vas. El contenido sigue cifrado
extremo a extremo; lo que se pierde es **quién habla con quién**, que es justo lo que Nullcell
existe para proteger.

**El suelo de 10 minutos (`--min-age`).** Un relevo no se usa hasta que *tu* celda lo ha observado
10 minutos: nadie puede falsificar el tiempo que tú llevas viéndolo, así que encarece inundar la
red de identidades nuevas (§7, §10). En `auto` sólo se aplica cuando hay anonimato que proteger.

**Una celda no se cuenta a sí misma.** Con N máquinas, quien *publica* sólo tiene N−1 relevos por
los que pasar: usar su propio nodo como punto de enlace anunciaría su dirección. Por eso el punto de
enlace propio de arriba es la excepción explícita, y etiquetada, para una red de uno.

### Semillas que circulan

Una semilla es una línea como `wuxq7z…ih7ib@62.171.160.130:4433`. **No es un secreto ni una
credencial**: es una presentación y nada más (§7). Todo lo aprendido por ella se verifica aparte.

- Las celdas **comprueban** las semillas que añades (tienen que contestar con su identidad) y las
  **intercambian** con los relevos, recibiendo las que les faltan. Cada usuario nuevo entra por
  más puertas y la red deja de depender de una.
- Límites contra ataques de eclipse: pocas semillas por oferta, pocas por prefijo de red, y las que
  fallan una y otra vez se descartan.
- Las **invitaciones** llevan cómo entrar y a qué portal, en una línea:

```bash
ncell invite --announce tu-servidor.com:4433 --portal nullcell
# nullcell://join?s=kopxc7…3fab@tu-servidor.com:4433&p=nullcell
```

Son legibles a propósito (ves a qué celdas te conectarás *antes* de pegarla), llevan varias
semillas (sin punto único de fallo) y no firman nada.

Después de la primera entrada, la celda recuerda a quién conoció y vuelve a entrar sola, sin
semilla.

### Levantar un relevo

```bash
ncell relay --announce TU_IP_PUBLICA:4433                 # primer relevo de una red
ncell relay --announce TU_IP_PUBLICA:4433 --print-seed    # la línea que das a los demás
ncell relay --announce SU_IP:4433 --seed ESA_LINEA        # el segundo operador
```

Necesitas **un puerto UDP abierto** (QUIC va sobre UDP) y una IP alcanzable. Sin root, dominio ni
certificado.

```bash
./deploy/install.sh --relay TU_IP:4433     # systemd, usuario sin privilegios
journalctl -u nullcell-relay -f
```

`systemctl stop` envía `SIGTERM`, que **vacía** los circuitos vivos en lugar de tirarlos (§10).

**Docker:** `cd deploy && ANNOUNCE=tu.ip:4433 docker compose up -d`. El servidor del proyecto corre
en un contenedor endurecido (raíz de sólo lectura, sin capacidades, red propia, sólo 4433/udp
publicado). Laboratorio de 5 relevos: `docker compose -f deploy/docker-compose.lab.yml up --build`.

### App de Android

Navegador `.null` para Android **con nodo propio**: el teléfono arma sus propios circuitos, como el
escritorio. Nadie en el camino —tampoco el servidor del proyecto— ve qué portales abres.

**Descarga:** [última versión](https://github.com/ArchelKrozt/nullcell/releases) →
`nullcell-<versión>.apk` (Android 8 o superior, ARM de 64 bits). Ábrelo en el teléfono y permite
"instalar apps desconocidas".

| Pestaña | |
|---|---|
| **Inicio** | Estado en vivo, anillo de anonimato (operadores / 3), candado, celdas, relevos, semillas, actividad |
| **Buscar** | El buscador de la red (por nombre, palabra o etiqueta), consultado por circuitos |
| **Navegar** | Navegador `.null` con barra de dirección, candado, atrás/adelante, recargar y menú |
| **Semillas** | Añadir (pegar una línea o una invitación `nullcell://`), quitar, intercambiar ahora, compartir |
| **Ajustes** | Tu dirección, estado del anonimato automático, actualizaciones, qué protege, desconectar |

- **Una semilla basta**: la del proyecto viene incluida. Navega con 1 salto hasta que la red tenga
  3 operadores, y entonces pasa sola a anónima.
- **Las páginas no salen a internet normal**: el WebView sólo carga direcciones `.null`; una imagen,
  fuente o script de internet se bloquea, porque saldría directo del teléfono y delataría tu IP.
  Sin caché, sin cookies de terceros.
- **El teléfono no se anuncia** ni presta ancho de banda: sólo navega y comparte semillas.
- **Se actualiza sola.** Al abrirse (como mucho cada 6 h) mira las releases `android-v*` de este
  repositorio; si hay una más nueva, pregunta, la descarga, **comprueba la huella SHA-256 que
  publica GitHub y que la firma sea la misma que la de la app instalada**, y abre el instalador de
  Android. Es lo único que la app envía a internet normal (GitHub ve tu IP), así que se puede
  apagar en Ajustes.

El motor es el mismo código Python, embebido con [Chaquopy](https://chaquo.com/chaquopy/); la
interfaz es Kotlin + Jetpack Compose. Compilar, firmar y publicar:
[`android/LEEME.md`](android/LEEME.md).

### Escritorio

```bash
pip install 'nullcell[desktop]'
ncell desktop
```

Aparece un icono **NULL** en la barra de menús. Su panel tiene un interruptor para compartir ancho
de banda, **publicar una carpeta** como portal `.null` (sin abrir puertos) y el **navegador** de la
red con el buscador y el tablero. Cerrar una ventana no mata el nodo, y las decisiones se guardan.

Sin terminal: doble clic en [`Nullcell.command`](Nullcell.command) en macOS, o
`python3 nullcell-escritorio.py`; `ncell launcher` crea un `Nullcell.app`.

El navegador no necesita autoridad de certificación: registra el esquema `null://` y entrega los
bytes del circuito directamente al renderizador. Cada dirección es su propio origen, así que los
portales siguen aislados. La CA local (§8) existe para quien prefiera Firefox vía SOCKS
(`ncell browse`).

> **PySide6 va fijada en 6.11.3 a propósito:** Qt dejó de publicar QtWebEngine para macOS en la
> 6.12. `ncell desktop` no funciona en una Raspberry Pi (Qt no publica ruedas para ARM): ahí se usa
> `ncell node`.

### Publicar un portal con nombre

Todo lo que define tu presencia cabe en una carpeta, para que mudarse sea copiar:

```
mi-despliegue/
  nullcell.toml     lo que TÚ declaras: qué publicar, con qué nombre, título y etiquetas
  estado.json       lo que el programa cambia en marcha
  identity.key      (opcional) la identidad de esta celda
```

```bash
ncell desktop --config ./mi-despliegue                              # con interfaz
ncell node --config ./mi-despliegue --announce tu-ip:4433 --relay   # sin interfaz (servidor, Pi)
```

`nullcell.toml` sólo se lee, nunca se reescribe, así que tus comentarios sobreviven. Formato y
mudanzas: [`docs/DESPLIEGUE.md`](docs/DESPLIEGUE.md).

> **La dirección *es* la clave.** `nullcell.null` es un nombre en el DHT atado a una clave Ed25519,
> y la dirección de 56 caracteres **es esa clave** (§3). Si copias la carpeta **sin
> `identity.key`**, la otra máquina es otra celda con otra dirección.

- El **nombre, título, descripción y etiquetas** del portal se publican como palabras clave del
  buscador, y se renuevan solos: el buscador de la red lo encuentra por cualquiera de ellos (sin
  importar tildes ni mayúsculas).
- Si apagas la máquina, el nombre se conserva; el descriptor caduca en una hora y quien visite ve
  **«el portal existe, pero está apagado»**, no un error genérico.
- Visitar tu propio portal funciona desde el primer minuto, etiquetado **«tu portal · local»**.

### Sólo navegar

```bash
ncell browse                         # la semilla del proyecto viene incluida
ncell browse --seed <semilla>        # añade otras para no depender de una
```

Un cliente **no abre ningún puerto**, no necesita IP pública, funciona detrás de NAT y **no publica
nada**. Tampoco presenta certificado: la autenticación del enlace es de un solo lado, así que
ningún relevo ve tu clave de largo plazo.

**Lo único que no es anónimo es el arranque**: hay que hablar con una semilla directamente para
saber que existen relevos, igual que un cliente de Tor descarga su primer consenso en directo. A
partir de ahí, incluido **qué nombres resuelves**, todo va por circuitos (§7, decisión 8).

Nullcell no usa Tor ni puede ir a través de él: va sobre **QUIC (UDP)** y Tor sólo transporta TCP.

### Chat tipo IRC

En el tablero hay un chat: entras a un nombre, ves quién está y lo que se dice **mientras estás**.
La presencia es un registro de TTL corto en el DHT bajo la clave del canal; a partir de ahí **no
hay servidor**: cada miembro abre una sesión de rendezvous con los demás. No llegan los mensajes de
antes de entrar, escala a un grupo (no a una multitud), estar en un canal es público, y el chat usa
una identidad propia (`chat.key`), distinta de la de tu portal. Cada mensaje va firmado.

### Actualizaciones (escritorio / servidor)

```bash
ncell update            # pregunta si hay versión nueva, y avisa antes de contactar
ncell update --no-check # sólo dice cómo actualizar, sin tocar la red
```

El escritorio y el relevo **nunca comprueban solos**: una petición periódica le diría a un servidor
ajeno que hay un nodo Nullcell en esa IP, todos los días. Escribir el comando *es* el
consentimiento. (La app de Android sí comprueba, porque instalar APKs a mano es justo lo que se
olvida, y se puede apagar.)

### Uso

```bash
ncell id                                   # tu identidad y tu dirección
ncell addr <nid>                           # valida una dirección, sin tocar la red
ncell ping <nid> --addr 127.0.0.1:4433     # enlace verificado + RTT

ncell node --announce IP:4433 --hud        # celda completa con panel local
ncell relay --announce TU_IP:4433          # prestar ancho de banda
ncell host ./mi-sitio --name panaderia --title "La Panadería"   # publicar un portal
ncell host app.py --wsgi app:application                         # o una app WSGI
ncell publish panaderia --announce IP:4433                       # registrar un nombre
ncell resolve panaderia.null                                     # buscarlo en el DHT
ncell browse panaderia.null                                      # abrir el navegador
ncell invite --announce IP:4433 --portal panaderia               # invitación de una línea
```

`--seed` es opcional en todos. `--hops` es `auto` por defecto en `node`, `browse` y `desktop`;
`ncell host` usa siempre 3 saltos (en una red pequeña, publica con `ncell node --config`).

### Estado

| Capa | Estado |
|---|---|
| L0 identidad Ed25519 + X25519, direcciones de 56 caracteres (§3) | ✅ |
| L1a enlace QUIC, certificado ligado al NID, huella, keepalive (§4) | ✅ |
| Marcos de 512 B, subcabecera `RELAY`, negociación de versión (§5) | ✅ |
| L1b Kademlia, descriptores firmados con PoW, bootstrap y PEX (§7) | ✅ |
| Nombres legibles `panaderia.null` first-come en el DHT (§8) | ✅ |
| L2a circuitos onion de 3 saltos, Noise NK, digest anti-etiquetado (§5) | ✅ |
| Selección de ruta: guards fijados, diversidad de prefijos, antigüedad mínima (§7) | ✅ |
| Saltos automáticos (`auto`) y redes de una sola semilla | ✅ |
| Verificación e intercambio de semillas entre celdas | ✅ |
| L2b rendezvous, puntos de enlace, 6 saltos extremo a extremo (§6) | ✅ |
| Servicios privados: índice cegado y cuerpo cifrado (§6) | ✅ |
| DHT por circuitos (decisión 8) | ✅ |
| L3/L4 streams de app multiplexados sobre una sesión (§8) | ✅ |
| `ncell host`, SOCKS5 con resolución remota obligatoria, CA local (§8) | ✅ |
| `ncell browse`: perfil aislado de Firefox (§8) | ✅ sin probar con Firefox real |
| Buscador: índice FTS5, fragmentos de palabras clave, búsqueda por nombre, ranking explicable (§8) | ✅ |
| Gossip de observaciones firmadas con Bloom filters, crawler educado (§8, §9) | ✅ |
| HUD completo y app de escritorio con navegador embebido (§9) | ✅ |
| Relleno `PADDING` (§5) | ✅ |
| `ncell relay`: apagado elegante, límites, línea de semilla (§1, §10) | ✅ |
| Despliegue: Docker, systemd, instalador (§12) | ✅ |
| App de Android con nodo propio y autoactualización | ✅ aún sin probar en muchos teléfonos |
| HTTP/2 hacia el navegador (§16, táctica 7) | ❌ reclasificada, ver §16 |
| TUI, modo TUN (§13, M8) | ❌ |
| Listas de bloqueo firmadas y suscribibles (§10) | ❌ |
| Diversidad por ASN (§7) | ❌ el motivo está documentado en §7 |

### Desarrollo

```bash
~/.venvs/nullcell/bin/python -m pytest -q                # toda la suite (~730 pruebas)
~/.venvs/nullcell/bin/python -m pytest -q -m "not slow"  # sin la malla de 30 celdas
~/.venvs/nullcell/bin/python bench/link_throughput.py    # puertas de §16
~/.venvs/nullcell/bin/ruff check . && ruff format --check .
```

Contribuir: [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) · Reportes de seguridad:
[`docs/SECURITY.md`](docs/SECURITY.md).

### Licencia

AGPL-3.0-or-later: obliga a publicar las modificaciones de los nodos que operen en red.
