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
| **usarlo sin terminal** (icono, panel, navegador) | `ncell desktop` — ver [Escritorio](#escritorio) |
| **sólo navegar, sin aportar un relevo** | `ncell browse --seed <semilla>` |
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

## Escritorio

La consola no es sitio para una red en la que vives: no es persistente, y cambiar de opinión
significa parar el proceso y arrancarlo con otro flag. `ncell desktop` es el mismo nodo con cara.

```bash
pip install 'nullcell[desktop]'
ncell desktop
```

Aparece un icono **NULL** en la barra de menús. Al pulsarlo se abre un panel con:

- un **interruptor** para compartir ancho de banda, que dice la verdad sobre la asimetría: activarlo
  publica el descriptor, y desactivarlo **deja de anunciar** pero no retracta el descriptor ya
  publicado, que caduca por TTL;
- **publicar una carpeta** como portal `.null`, con un selector nativo y sin abrir puertos;
- el **navegador** de la red, con el buscador y el panel.

Cerrar una ventana no mata el nodo, y las decisiones se guardan: al volver a abrirlo, lo que
pediste sigue pedido.

### Con un solo nodo, que es como empieza todo

§7 no puede construir un circuito con una sola celda —hacen falta 3 relevos de operadores
distintos, observados 10 minutos— y fingir lo contrario sería la única mentira imperdonable aquí.
Así que el panel **funciona por intención**: declaras lo que quieres y el nodo lo cumple en cuanto
puede.

- **Publicar una carpeta funciona desde el primer minuto.** Se sirve en tu máquina, etiquetada
  `sólo local`, con la *misma* aplicación que servirá el portal real —no es una maqueta— y pasa a
  `en línea` sola en cuanto haya relevos.
- **El panel dice qué falta y si toca esperar o actuar**, que son reacciones opuestas: un relevo
  joven se arregla esperando y hay cuenta atrás; una red con un solo operador no se arregla
  esperando nunca, y entonces lo que hace falta es una semilla. Hay una casilla para pegar la de
  otro y un botón para copiar la tuya.
- **El navegador sí se queda apagado** hasta que haya circuitos, porque ahí no hay media tinta.

### Lo que compartir ancho de banda hace, y lo que no

Compartir te convierte en **relevo**: ayudas a otros a construir circuitos. **No te conecta a la
red.** Son dos cosas distintas y conviene no mezclarlas:

| quieres… | necesitas |
|---|---|
| **entrar** a la red | conocer al menos otra celda: una **semilla**. No hay descubrimiento global |
| **navegar** | 3 relevos de operadores distintos, observados 10 min (§7) |
| **ser relevo** | que otros puedan alcanzarte: IP pública con el puerto UDP abierto, o la LAN |

El escritorio detecta solo por dónde es alcanzable —**sin hacer ninguna petición a internet**: una
consulta a un servicio de "cuál es mi IP" le diría a ese servicio que aquí hay un nodo— y el panel
te dice cuál de los tres casos es el tuyo. En una LAN la semilla que te da funciona de verdad para
los equipos de tu casa, que es la forma más fácil de levantar la primera red con dos ordenadores.

**El navegador no necesita autoridad de certificación.** La CA de §8 existe para que un navegador
ajeno acepte `https://…null`; el nuestro registra el esquema `null://` y entrega los bytes del
circuito directamente al renderizador. Cada dirección sigue siendo su propio origen
(`null://<56 chars>`), así que el aislamiento entre portales lo sigue dando el navegador. La CA
sigue existiendo para quien prefiera Firefox vía SOCKS.

> **PySide6 va fijada en 6.11.3 a propósito:** Qt dejó de publicar QtWebEngine para macOS en la
> 6.12, así que una versión más nueva instala un escritorio sin navegador.

## Publicar un portal con nombre, y mudarlo después

Todo lo que define tu presencia en la red cabe en una carpeta, para que mudarse a una Raspberry Pi
sea copiar y no reconstruir:

```
null-despliegue/
  nullcell.toml     lo que TÚ declaras: qué publicar y con qué nombre
  estado.json       lo que el programa cambia en marcha
  identity.key      (opcional) la identidad de esta celda
```

```bash
ncell desktop                          # busca la configuración sola
ncell desktop --config ./otra-carpeta  # o se lo dices tú
```

Sin `--config` se mira en la última carpeta usada, en la actual, en `./null-despliegue` y en
`~/.nullcell/despliegue`. Si no hay ninguna, el escritorio **pregunta una vez** y recuerda la
respuesta.

> **El programa no reescribe tu declaración.** `nullcell.toml` lo escribes tú, con comentarios, y
> sólo se lee; lo que cambia en marcha va a `estado.json`. Un programa que reescribe el fichero que
> una persona edita a mano se lleva por delante sus comentarios, y en silencio. TOML antes que JSON
> por eso mismo: admite comentarios, y una configuración que no puede explicarse a sí misma acaba
> explicada en otro sitio que se desactualiza.

Hay un ejemplo completo en [`null-despliegue/`](null-despliegue/), que publica
[`null-mi-web/`](null-mi-web/) —la documentación del proyecto, servida por la propia red— bajo el
nombre `nullcell.null`.

> **La dirección *es* la clave.** `nullcell.null` no es un dominio que alguien te alquile: es un
> nombre en el DHT atado a una clave Ed25519, y la dirección de 56 caracteres del portal **es esa
> clave** (§3). Si copias la carpeta a otra máquina **sin `identity.key`**, allí arranca una celda
> distinta con otra dirección, y el nombre sigue apuntando a la vieja hasta que caduque. El
> programa avisa.

### Visitar tu propio portal con una sola máquina

Funciona desde el primer momento. Escribe `nullcell.null` en el navegador y se abre, aunque no haya
nadie más en la red: **visitar tu propio sitio no necesita circuito** porque eres los dos extremos,
así que no hay tercero y no hay nada que anonimizar. La barra lo etiqueta **«tu portal · local»**,
para que no se confunda con haberlo alcanzado por la red.

El atajo vale **sólo para tu dirección y tu nombre declarado**, y ese límite es toda su seguridad:
cualquier otro destino pasa por los circuitos, o esto se convertiría en una forma de servir algo
local bajo el nombre de otro.

### Chat tipo IRC, desde el navegador

En el tablero (`ncell desktop` → **Navegador**, o `http://127.0.0.1:9180/?token=…`) hay un panel de
chat: entras a un nombre, ves quién está y lo que se dice **mientras estás**.

El «ping» que busca a otros programas activos no es un ping: es un registro de TTL corto en el DHT
bajo la clave del canal, que dice *«estoy aquí, y se me alcanza por aquí»*. Quien quiera hablar
consulta esa clave. Un multicast sólo cruzaría la red local; un ping directo exigiría saber a
quién, que es el problema que esto resuelve.

A partir de ahí **no hay servidor**: cada miembro abre una sesión de rendezvous con los demás y
reparte cada mensaje a todos. Nadie «tiene» el canal y por nadie pasa todo.

Tres cosas que conviene saber antes de usarlo:

- **No llegan los mensajes de antes de entrar**, como en IRC. Lo contrario exige que alguien
  almacene y reenvíe, y eso es un servidor con otro nombre.
- **Escala a un grupo, no a una multitud.** Reparto completo: N miembros son N−1 envíos por
  mensaje. A partir de unas decenas haría falta otra cosa.
- **Estar en un canal es público.** Cualquiera puede consultar su clave y ver qué direcciones dicen
  estar ahí; es inevitable en un canal abierto. Por eso el chat usa una **identidad propia**
  (`chat.key`), distinta de la de tu portal: ver tu apodo en `#general` no debe revelar que eres
  quien publica `nullcell.null`.

Cada mensaje va **firmado**: la sesión ya cifra, pero en un canal cualquiera puede entregarte un
mensaje y decir que lo escribió otro.

### ¿Puede otro ordenador encontrar tu portal solo?

**El nombre sí; la red no.** Conviene separarlo porque son dos cosas:

- Una vez dentro, `nullcell.null` se resuelve **automáticamente** por el DHT, sin configurar nada.
  Eso ya funciona.
- Pero **entrar** no es automático: una celda recién instalada no conoce a nadie y **no hay
  descubrimiento global**. Necesita una **semilla**, y alguien se la tiene que dar.

Eso no es una pieza que falte, es la decisión central del diseño: un descubrimiento global sería
una lista de arranque que alguien mantiene, es decir, una autoridad — exactamente lo que Nullcell
no quiere tener. Tor lo resuelve con autoridades de directorio incrustadas; aquí
[`bootstrap.json`](bootstrap.json) está **vacío a propósito** mientras no exista una red pública.

#### Invitaciones: una sola cosa que copiar

```bash
ncell invite --announce tu-servidor.com:4433 --portal nullcell
# nullcell://join?s=kopxc7…3fab@tu-servidor.com:4433&p=nullcell
```

Lleva **cómo entrar** y **a qué portal**, en una línea. El panel acepta esto o una semilla suelta
en la misma casilla, porque pedirle a alguien que distinga entre las dos es pedirle que entienda
una diferencia que no le importa.

Tres decisiones, cada una con su motivo:

- **Es legible, no base64.** Quien la recibe ve a qué celdas le dicen que se conecte *antes* de
  pegarla. La primera conexión es directa y revela su IP: una invitación opaca pediría confianza
  ciega justo donde más caro sale.
- **Lleva varias semillas.** Una sola es un punto único de fallo, y también una forma de que una
  celda concreta vea entrar a todo el mundo.
- **No es una credencial.** No firma nada y no avala nada; todo lo aprendido a través de ella se
  verifica por separado (§7). Eso es lo que impide que repartir invitaciones cree autoridades.

El botón del panel genera la tuya con tu semilla **más algunos relevos que ya conoces**, para que
siga funcionando un día que tu máquina esté apagada.

Lo que sí es automático, desde ahora: **entras una vez y ya**. La celda guarda a quién conoció
—incluso si el proceso muere de golpe— y al volver a abrirla rejunta sola, sin semilla.

> **Tu Mac no necesita IP pública para publicar.** Un portal se alcanza por puntos de enlace y un
> punto de encuentro, así que sirve detrás de NAT sin abrir ningún puerto (§0). Lo que sí hace
> falta es que **existan relevos** por los que construir los circuitos.

### Qué ve alguien si apagas la máquina

El nombre no se pierde: el registro `NAME` sigue apuntando a tu clave. Lo que caduca —en una hora—
es el *descriptor*, que dice por dónde alcanzarte. Quien visite verá **«el portal existe, pero está
apagado»**, no un error genérico y no «no existe». Al encender vuelve solo.

Son estados que se parecen desde fuera y significan cosas opuestas —uno no hay que esperarlo, el
otro vuelve por sí mismo— así que el navegador los distingue en lugar de decir «no se pudo cargar
la página».

## Entrar sólo a navegar, sin aportar un relevo

Navegar y prestar ancho de banda son **comandos distintos**, y el primero no necesita nada del
segundo:

```bash
ncell id                                   # tu identidad, una vez
ncell browse --seed <línea-de-semilla>     # a navegar
```

Un cliente **no abre ningún puerto**, no necesita IP pública ni dominio, funciona detrás de NAT y
**no publica nada** en el DHT. Tampoco presenta certificado: la autenticación del enlace es de un
solo lado —el relevo demuestra quién es, el cliente no demuestra nada—, así que ningún relevo del
camino ve tu clave de largo plazo.

Lo que sí necesitas es que **otros** estén prestando relevos: §7 exige 3 saltos en operadores
distintos y 10 minutos de antigüedad observada, así que una instalación nueva espera 10 minutos
antes de poder construir su primer circuito. Si no hay ruta, el error dice cuál de los dos suelos
falló y a cuántos relevos excluyó cada uno.

**No se usa Tor, y no se puede.** Nullcell es su propia red: sus propios circuitos de 3 saltos, su
propio cifrado por capas, su propio espacio de nombres `.null`. Y tunelizarla *a través* de Tor
tampoco es una opción: Nullcell va sobre **QUIC, que es UDP**, y Tor sólo transporta TCP. No es una
pieza que falte, es una incompatibilidad de transporte.

**Lo único que no es anónimo es el arranque**, y no puede serlo: hay que hablar con una semilla
directamente para enterarse de que existe algún relevo —igual que un cliente de Tor descarga su
primer consenso en directo—. A partir de ahí, incluido **qué nombres resuelves**, todo va por tres
saltos (§7, decisión 8).

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
