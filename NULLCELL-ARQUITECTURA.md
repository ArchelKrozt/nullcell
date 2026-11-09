# Nullcell — Red P2P cifrada y anónima ("segundo internet")

> Plan de arquitectura **v1.2** · 2026-11-08 · Python 3.11+
> v1.1 añadió: formato de cable completo y versionado (§5), dos clases de servicio (§6), DHT
> por circuitos (§7), personalización del HUD y arranque en frío (§9), y rendimiento con
> objetivos medibles (§16).
> v1.2 cierra el formato de dirección: **la dirección lleva la clave pública** (§3), lo que
> habilita el cegado de los servicios privados de §6.

## 0. Decisiones tomadas

| # | Decisión | Consecuencia |
|---|---|---|
| 1 | **Anónima desde v1**: circuitos onion de 3 saltos | El enrutamiento es el corazón del proyecto, no un añadido. Latencia 300–800 ms. Nadie ve tu IP salvo tu primer salto. |
| 2 | **Transporte QUIC** (`aioquic`) entre nodos vecinos | Multiplexado, congestión y retransmisión gratis. Solo escribimos identidad + onion encima. |
| 3 | **Proxy SOCKS5 ahora, TUN opcional después** | Llegamos rápido a "Firefox abre un `.null`"; la arquitectura queda preparada para una tarjeta de red virtual más adelante. |
| 4 | **Navegador existente con perfil dedicado** (`ncell browse`) + CA local | Cero código de navegador, aislamiento total, candado y APIs modernas en los portales (§8). |
| 5 | **Nombre: Nullcell** · binario `ncell` · direcciones `.null` | "Celda" = nodo; la unidad de 512 B pasa a llamarse **marco**. Verificado libre en PyPI y npm (§15). |
| 6 | **Código y protocolo en inglés, README bilingüe, HUD en español por defecto** | El proyecto necesita voluntarios para los relevos; el código no puede ser una barrera. |
| 7 | **Subcabecera `RELAY` con `stream_id` y digest acumulado** (§5) | Sin `stream_id` una página necesitaría 20 circuitos; sin digest, un guard malicioso puede confirmar el circuito con un ataque de etiquetado. |
| 8 | **Todo el DHT de un cliente va por circuitos** (§7) | Lo contrario anula la decisión 1 en el primer lookup. Cuesta 1-3 s por búsqueda, y por eso hacen falta caché, paralelismo y resultados progresivos. |
| 9 | **`VERSIONS` en el primer marco de cada enlace** (§5) | Tres bytes hoy; un cisma de red el día de la primera incompatibilidad si no está. Es también lo que permite revisar el tamaño de marco con datos de M1 (§16). |
| 10 | **La dirección lleva la clave pública**, no su hash: 56 chars (§3) | Habilita el cegado de servicios privados (§6), da checksum contra erratas, elimina la colisión de hash truncado y permite verificar al relevo **sin bajar su descriptor**. Es el mismo camino que recorrió Tor de v2 a v3. |

**Regalo inesperado de la decisión 1:** con rendezvous (§6), un sitio alojado en Nullcell **no
necesita IP pública, ni UPnP, ni abrir puertos**. Todas las conexiones son salientes. El NAT, que
era el enemigo número uno del diseño anterior, deja de ser un problema para quien publica. El
precio lo pagan los **relays**, que sí necesitan ser alcanzables.

## 1. ¿Se puede hacer? Sí.

| Red | Qué resuelve | Qué le copiamos |
|---|---|---|
| Tor | circuitos onion, onion services, ntor | el modelo completo de anonimato y rendezvous |
| I2P | red overlay con servicios internos, túneles | descubrimiento sin autoridades centrales |
| IPFS / libp2p | Kademlia, multiplexado, relays | DHT y gestión de peers |
| ZeroNet / Freenet | webs servidas por los usuarios | hosting distribuido con contenido firmado |

Nullcell = **Tor descentralizado (sin autoridades de directorio) + DHT + buscador integrado**, en
una sola app que no requiere root.

### Límites honestos

- **Más nodos ≠ más velocidad.** Dan disponibilidad, diversidad de rutas y anonimato. La latencia
  sube con cada salto: cliente↔servicio son **6 saltos** (3 + 3 vía rendezvous).
- **La red necesita relays públicos voluntarios.** Un usuario doméstico puede navegar y publicar
  sin abrir nada, pero alguien tiene que prestar ancho de banda alcanzable. Sin relays no hay red.
- **El punto más difícil y el más honesto del plan:** Tor elige relays con un *consenso firmado*
  por 9 autoridades de directorio. Nosotros no queremos autoridades → la selección de relays
  descentralizada es un problema **abierto de investigación** (ataques Sybil y de eclipse).
  Nuestra mitigación en §7 es pragmática, no una solución demostrada. Hay que decirlo en el README.
- **Bootstrap.** Para el primer arranque hace falta conocer algún nodo: lista embebida + mDNS LAN
  + peers guardados. Único punto semi-centralizado, igual que en Tor o Bitcoin.
- **Nombre:** resuelto en §15 — el proyecto es **Nullcell**, binario `ncell`, direcciones `.null`
  (con alias `.null.alt` según RFC 9476).

## 2. Arquitectura por capas

```
L5  APPS + HUD       sitios .null, chat, archivos, dashboard local    (nullcell/hud/)
L4  BUSCADOR         índice distribuido + FTS local + ranking         (nullcell/search/)
L3  SERVICIOS        descriptores, nombres, proxy local SOCKS5        (nullcell/services/, gateway/)
L2b RENDEZVOUS       intro points, cookies, encuentro anónimo         (nullcell/onion/rendezvous.py)
L2a CIRCUITOS ONION  3 saltos, cifrado en capas, marcos fijos         (nullcell/onion/)
L1b OVERLAY          Kademlia DHT, descriptores de relay, guards      (nullcell/dht/, discovery/)
L1a ENLACE           QUIC entre vecinos, identidad verificada         (nullcell/net/)
L0  IDENTIDAD        Ed25519 + X25519 -> NID                          (nullcell/core/)
```

## 3. L0 — Identidad y direcciones

- Primer arranque: **Ed25519** (firmar) + **X25519** (ECDH). En `~/.nullcell/identity.key`, modo
  600, cifrable con passphrase.
- Un cliente que solo navega **no presenta identidad a nadie**. Su clave sirve para firmar lo que
  publica, no para conectarse.

### Formato de dirección — la dirección *es* la clave

```
NID = base32_sin_padding( pub_ed25519 ‖ checksum ‖ version )     = 56 caracteres
      ╰─ 32 B ─╯          ╰─ 2 B ─╯    ╰ 1 B ╯
```

| Campo | Bytes | Contenido |
|---|---|---|
| `pub_ed25519` | 32 | La clave pública, **literal, no su hash** |
| `checksum` | 2 | `sha256("nullcell-addr" ‖ pub ‖ version)[:2]` — detecta erratas al teclear antes de tocar la red |
| `version` | 1 | bits 0-6: versión del formato (hoy `1`). **bit 7: clase** — 0 = portal público, 1 = servicio privado (§6) |

35 bytes → base32 sin relleno → **56 caracteres exactos**, sin padding que recortar:

```
k7f2qz3mn4xr8bvc5tyu2wld9hsa6gjp0eqf7znx1mkv4rtb3ysq.null
```

**Por qué la clave y no su hash.** Es la decisión 10 de §0, y da cinco cosas de una vez:

1. **Habilita el cegado** de los servicios privados de §6 — que necesita la clave pública, y de un
   hash truncado no se recupera. Sin esto, §6 no se puede implementar.
2. **Autocertificación directa.** Verificar un relevo es comparar su clave con la dirección que
   buscabas. Con un hash había que *bajar su descriptor* para obtener la clave antes de poder
   verificar nada — un viaje de red extra en el peor momento, el del primer contacto.
3. **Checksum.** Una dirección mal teclada se detecta en local, sin lookup y sin fuga al DNS.
4. **Sin colisión de hash truncado.** 52 chars de base32 sobre `sha256` recortado daban 260 bits;
   suficiente, pero era un margen que había que razonar. Con la clave literal el problema no existe.
5. **La clase va en la dirección.** El cliente sabe si debe buscar un descriptor público o uno
   cegado **antes** de preguntar a nadie.

Coste: **4 caracteres más** que el formato anterior. Tor hizo exactamente este cambio entre las
direcciones v2 (16 chars, hash) y v3 (56 chars, clave), por estas mismas razones.

**Dirección vs. clave del DHT — no son lo mismo.** La dirección es la clave pública; la clave de
enrutamiento en Kademlia (§7) sigue siendo `sha256(pub)`. Separarlas importa: el espacio del DHT
necesita distribución uniforme para que los buckets se repartan bien, y mezclar "dónde se guarda"
con "cómo te llamas" complica el cegado de §6, donde el índice se calcula sobre la clave *cegada*
y no sobre la real.

**Límite honesto — post-cuántico.** Publicar la dirección publica la clave pública Ed25519. Un
adversario con un ordenador cuántico suficientemente grande podría, en el futuro, derivar la
privada. Con direcciones-hash la clave quedaba oculta hasta publicar el descriptor — lo que un
portal público hace de todas formas, así que la diferencia real afecta solo a servicios privados
que nunca publican. La consecuencia práctica es la misma que en Tor v3: **migrar a cripto
post-cuántica exigirá rotar direcciones**, y por eso el byte de `version` existe desde el primer
día. Hay que decirlo en el README junto a los demás límites de §1.

## 4. L1a — Enlace: QUIC entre vecinos

- `aioquic` sobre UDP. Cada nodo alcanzable escucha un puerto QUIC.
- Certificado **autofirmado** cuyo CN/SAN contiene el NID; el cliente ignora la PKI mundial por
  completo y **compara la clave Ed25519 del certificado con la que va dentro de la dirección que
  buscaba** (§3). Como la dirección *contiene* la clave, es una igualdad de 32 bytes en local: no
  hace falta bajar el descriptor del relevo para poder verificarlo.
- **Autenticación de un solo lado**: el relay prueba quién es, el cliente no dice nada. Es lo que
  mantiene anónimo al cliente ya en la capa de enlace (modelo Tor).
- QUIC nos da: multiplexado de streams, control de congestión, retransmisión, migración de ruta,
  y pasa por muchos cortafuegos que bloquean todo menos UDP/443.
- Un enlace transporta **marcos de 512 bytes fijos** de muchos circuitos distintos, multiplexados.
  El tamaño fijo es una medida anti-análisis de tráfico, no un capricho.

## 5. L2a — Circuitos onion

**Construcción telescópica** (igual que Tor, con Noise en lugar de ntor):

1. Cliente abre enlace QUIC con el **guard** (salto 1) y envía `CREATE`: handshake
   **Noise_NK_25519_ChaChaPoly_SHA256** — `N` = iniciador sin clave estática (anónimo),
   `K` = clave del relay ya conocida por su descriptor. Resultado: clave de sesión con el salto 1.
2. Cliente envía `EXTEND(salto2)` cifrado para el salto 1; este abre enlace con el salto 2 y
   reenvía el `CREATE`. El cliente acaba con una clave que **solo** comparte con el salto 2.
3. Igual para el salto 3. Ahora hay 3 claves independientes.
4. Cada marco de datos se cifra en 3 capas (ChaCha20, una por salto). El salto 1 sabe quién eres
   pero no a dónde vas; el salto 3 sabe a dónde va pero no quién eres; el salto 2 no sabe nada.

### Formato de marco

```
[circ_id u32][cmd u8][payload 507 B]   = 512 B exactos
```

Comandos de nivel de enlace (`cmd`): `VERSIONS`, `CREATE/CREATED`, `RELAY`, `DESTROY`, `PADDING`.

> **Cambio respecto al primer borrador:** `EXTEND/EXTENDED` **no** son comandos de enlace, son
> comandos de relevo (`rcmd`, abajo). Tienen que viajar cifrados extremo a extremo hasta el salto
> que va a extender el circuito; si fueran comandos de enlace, el primer salto podría leer y
> modificar a qué nodo quieres extender, y elegir la ruta por ti.

**Asignación de `circ_id`:** el ID es **local al enlace**, no global a la red. Los dos extremos de
un enlace pueden abrir circuitos, así que el **bit más significativo** lo fija quién inició el
enlace QUIC: el iniciador usa IDs con el bit alto a 0, el receptor a 1. Sin esta regla los IDs
colisionan en cuanto un relevo abre un circuito propio por un enlace que ya existía.

### Subcabecera `RELAY` — multiplexado e integridad

El payload de un marco `RELAY` **no es opaco**: lleva 11 bytes de subcabecera, cifrados dentro de
las capas de onion, y 496 bytes de datos útiles.

```
[rcmd u8][recognized u16][stream_id u16][digest u32][length u16][data 496 B]
```

| Campo | Para qué |
|---|---|
| `rcmd` | `DATA`, `BEGIN`, `CONNECTED`, `END`, `SENDME`, `EXTEND`, `EXTENDED`, `TRUNCATED`, `RESOLVE` |
| `recognized` | 0 cuando el marco es **para mí**. Un relevo intermedio quita su capa, lee este campo y, si no es 0, reenvía sin interpretar nada más. Es la prueba barata de "esto no va dirigido a mí" |
| `stream_id` | **Multiplexa streams de aplicación dentro de un mismo circuito.** Sin él, una página con 20 peticiones necesitaría 20 circuitos de 6 saltos: inviable. Es también lo que hace posible HTTP/2 hacia el navegador (§16) |
| `digest` | Primeros 4 bytes de un **digest acumulado** del flujo con ese salto concreto |
| `length` | Bytes útiles en `data`; el resto es relleno hasta completar el marco |

**Por qué el digest no es opcional.** Sin él existe el **ataque de etiquetado**: un guard malicioso
voltea bits de un marco que pasa; el destino recibe basura y la corrupción viaja hasta el otro
extremo del circuito. Si el adversario controla también el último salto, ese patrón de corrupción
le **confirma** que su guard y su salida están en el mismo circuito — anonimato roto sin haber
roto ninguna primitiva criptográfica. Con un digest acumulado por capa, el primer marco manipulado
no verifica, el circuito se derriba y el adversario no aprende nada.

**Presupuesto de bytes:** 512 − 4 (`circ_id`) − 1 (`cmd`) − 11 (subcabecera) = **496 B de datos de
aplicación por marco**, un 96,9 % de eficiencia de carga útil.

### Negociación de versión

Primer marco de todo enlace nuevo: `VERSIONS`, con la lista de versiones de protocolo que el nodo
habla. Se elige la mayor en común o se cierra el enlace. El descriptor de relevo (§7) publica
también su rango de versiones, para no construir circuitos que van a fallar a mitad.

Son tres bytes hoy y evitan un cisma de red el día de la primera incompatibilidad. Toda red P2P
que no lo puso al principio lo ha pagado. Es además el requisito para poder revisar el tamaño de
marco con datos reales (§16).

### Vida del circuito

| Regla | Valor | Por qué |
|---|---|---|
| Streams nuevos sobre un circuito ya usado | máx. **10 min** desde su primer uso | Un circuito de horas enlaza entre sí todo lo que hiciste durante horas |
| Circuito sin streams activos | se cierra a los **10 min** | No regalar recursos a los relevos voluntarios |
| ~~Rekey de las claves de capa~~ → **retiro y reconstrucción** | cada **1 GB o 1 h**, lo que llegue antes | Forward secrecy. **Cambiado en M3:** un rekey en sitio necesita un mensaje de protocolo que §5 no define, y un circuito nuevo da la misma propiedad sin esa maquinaria. Es también lo que hace Tor. Queda dicho en lugar de divergir en silencio |
| Caída de un salto intermedio | `DESTROY` hacia los dos lados, los streams reciben `END`, el cliente reconstruye y **reintenta solo los streams idempotentes** | Un POST a medias no se puede repetir en silencio |

### Control de flujo y relleno

**SENDME por circuito y por stream**, además del control de congestión de QUIC: ventana de
circuito 1000 marcos (incremento 100), ventana de stream 500 (incremento 50). Un circuito lento no
debe bloquear a los demás que comparten el mismo enlace.

**Relleno:** marcos `PADDING` con dos máquinas sencillas — (a) *netflow padding* en el enlace, para
que un enlace inactivo no desaparezca de los registros de flujo del ISP y reaparezca al usarlo, y
(b) relleno entre cliente y guard durante la construcción del circuito, que es la fase con firma
temporal más reconocible. No es un esquema demostrado contra un adversario que ve toda la red
(§10, declarado fuera del modelo); es encarecer el análisis pasivo.

## 6. L2b — Rendezvous: publicar sin abrir puertos

Para que **ambos extremos** queden anónimos (y sin NAT):

1. El servicio elige 3 **introduction points** y mantiene un circuito abierto a cada uno.
2. Publica en el DHT, bajo `sha256(nid)`, un **descriptor firmado**: intro points, claves,
   TTL. Firmado con su Ed25519 → nadie puede falsificarlo ni suplantarlo.
3. El cliente baja el descriptor, elige un **rendezvous point**, le abre un circuito y deja una
   *cookie* aleatoria.
4. Por otro circuito, el cliente pide a un intro point que avise al servicio: "ve al rendezvous X
   con la cookie Y".
5. El servicio abre su propio circuito de 3 saltos hasta el rendezvous. Los dos circuitos se
   empalman: **6 saltos**, cifrado extremo a extremo, ninguno de los dos conoce la IP del otro, y
   el rendezvous reenvía bytes que no puede leer.

### Dos clases de servicio: portal público y servicio privado

El paso 2 tal como está descrito guarda el descriptor **en claro bajo `sha256(nid)`**, y eso tiene
un coste que conviene ver: cualquier nodo del DHT que almacene ese registro aprende el NID del
servicio y sus tres puntos de enlace. Recorriendo el espacio de claves se puede **levantar un censo
de todos los servicios de la red** y cosechar sus intro points para atacarlos.

Tor v3 lo resolvió indexando el descriptor bajo una **clave cegada** derivada de la pública y
rotada por periodo de tiempo, con el **cuerpo cifrado** con una clave derivada de la dirección: el
nodo que almacena no sabe de quién es ni puede leer lo que guarda.

Aquí hay una tensión real con la decisión de tener buscador integrado (§8): **lo que está cegado no
se puede indexar.** La salida que conserva las dos cosas es dejar que lo elija quien publica.

| | **Portal público** | **Servicio privado** |
|---|---|---|
| Clave de índice en el DHT | `sha256(nid)`, estable | `sha256(blinded_pub ‖ periodo)`, rotada |
| Cuerpo del descriptor | en claro | cifrado con clave derivada del NID |
| Aparece en el Oráculo | sí | no |
| Enumerable por un nodo del DHT | sí — es el precio de ser encontrable | no |
| Caso de uso | la panadería: quiere que la encuentren | chat entre dos personas, archivo compartido, panel remoto |

`ncell host` toma `--public` (por defecto, con confirmación la primera vez) o `--private`. **El
cliente sabe cuál buscar sin preguntar a nadie: la clase va en el bit 7 del byte de `version` de la
propia dirección** (§3).

**Periodo de tiempo:** 24 h, con solapamiento de 2 h en los bordes, para que el cambio de periodo
no deje un servicio ilocalizable mientras los relojes se ponen de acuerdo (§16, relojes).

### Derivaciones del servicio privado

Con la dirección llevando la clave pública (§3), las tres derivaciones quedan definidas:

| Qué | Cómo | Quién puede calcularlo |
|---|---|---|
| Clave cegada del periodo | `blind(pub, sha256("nullcell-blind" ‖ pub ‖ periodo))` | Cualquiera que **conozca la dirección** |
| Clave de índice en el DHT | `sha256("nullcell-index" ‖ blinded_pub ‖ periodo)` | Ídem — y el nodo que almacena solo ve esto |
| Clave de cifrado del cuerpo | `HKDF(sha256("nullcell-cred" ‖ pub), periodo)` | Ídem |

El nodo del DHT que almacena el registro ve **solo** la clave de índice: no puede derivar la
dirección real, no puede leer el cuerpo y no puede enumerar nada. Quien tiene la dirección calcula
las tres cosas en local, sin red.

El servicio firma el descriptor con la **clave cegada** del periodo, no con la real, para que el
nodo que almacena pueda verificar que el registro es legítimo sin aprender de quién es.

> **Construido en M4** (`dht/private.py`, `onion/blinding.py`). El registro lleva la clave
> **cegada** en su campo `pub`, así que una verificación Ed25519 normal funciona y la clave real no
> aparece en ninguna parte. Comprobado en los tests: el nodo del DHT verifica el descriptor sin
> saber de quién es, quien tiene la dirección lo abre, otra dirección no, y la clave real no está
> en los bytes del registro.
>
> **Y el PoW de un descriptor privado cuesta lo mismo que uno público** (9 bits). La privacidad no
> es un descuento: si lo fuera, el camino barato para inundar el DHT sería publicar en privado.

## 7. L1b — Descubrimiento y selección de relays (la parte delicada)

**DHT Kademlia** (k=20, XOR sobre `sha256(pub)` — la **clave de enrutamiento**, que no es la
dirección: ver §3) guarda dos tipos de registro firmado:
- **Descriptor de relay**: `{id, endpoints, claves, ancho_banda, uptime, políticas, ts, ttl, pow, sig}`
- **Descriptor de servicio**: intro points (§6) + metadatos para el buscador (§8).

**Entrada a la red:** `bootstrap.json` embebido → `~/.nullcell/peers.db` (sesión anterior) →
**descubrimiento en LAN** → **PEX** con los peers conocidos.

> **Nota de implementación (M2):** lo que llamábamos "mDNS" se implementó como una **baliza UDP
> multicast propia** con anuncio firmado, en un grupo y puerto de Nullcell, no como mDNS/DNS-SD
> estándar. Se dice claro porque la diferencia importa: no aparece en `dns-sd` ni en navegadores
> Bonjour, y a cambio no añade la dependencia de zeroconf. DNS-SD de verdad se puede montar encima
> más adelante sin tocar nada más. Va **desactivada por defecto**: anunciar en la red local que
> esta máquina corre un nodo Nullcell es útil en un laboratorio y una revelación innecesaria en
> cualquier otro sitio.

**Selección de los 3 saltos, sin autoridad central:**
- **Guards pinned:** el salto 1 se fija durante semanas (2–3 guards). Rotar el primer salto
  constantemente es lo que te delata a la larga.
- **Diversidad obligatoria:** los 3 saltos en prefijos distintos — **/16 en IPv4, /48 en IPv6** —
  y, si se puede resolver, ASN distintos. (La regla original decía solo "/16", que es pensamiento
  IPv4: en IPv6 un solo operador reparte /48 a mansalva y tres saltos "distintos" podrían estar en
  la misma mano.)

  > **Hueco conocido, de M3: la diversidad por ASN no está implementada**, y conviene saber por
  > qué. Resolver el ASN de una IP exige o un conjunto de datos externo —una dependencia y un
  > problema de obsolescencia— o una consulta de red, que revela qué relevos estás sopesando. La
  > diversidad por prefijo es un **proxy más débil**, y está etiquetada como tal en el código: dos
  > /16 distintos pueden estar perfectamente en el mismo operador. Un hostname como endpoint no
  > tiene prefijo comparable y queda fuera de la selección, por la misma razón: resolverlo
  > delataría.
  >
  > Y la política completa, también de M3: **guards fijados 30 días** (§7 decía "semanas"),
  > guards en prefijos distintos entre sí, y **antigüedad observada mínima de 10 minutos** antes de
  > enrutar por un relevo. La consecuencia honesta de lo último es que **un nodo recién instalado
  > no puede construir un circuito durante 10 minutos**, y el mensaje de error lo dice así.
- **PoW** (argon2id) en el descriptor + antigüedad mínima observada → encarece inundar la red de
  relays falsos.
- **Verificación cruzada:** se consulta el mismo descriptor a varios nodos de buckets distintos
  para detectar eclipse attacks.
- **Listas de relays firmadas y *opt-in*:** curadores de la comunidad publican listas; el usuario
  elige a quién seguir, o a nadie. Es el compromiso práctico mientras la red es pequeña, y queda
  documentado como tal.

### Decisión: todo el tráfico del DHT va por circuitos

No estaba dicho en ninguna parte y es la decisión con más consecuencias de esta sección.

Si un cliente consulta el DHT **directamente**, revela su IP y *qué está buscando* a nodos
cualesquiera de la red. Se cae la decisión nº 1 del proyecto en el primer lookup: no sirve de nada
un circuito de 3 saltos para visitar un portal si antes has preguntado en claro por su descriptor.
Tor nunca deja que un cliente hable con un HSDir directamente, y es por esto exactamente.

**Todo lookup y toda publicación de un cliente van por circuito.** El precio es latencia, y hay que
presupuestarla desde el principio en lugar de descubrirla en M5:

| Operación | Coste |
|---|---|
| Un salto de Kademlia | 1 ida y vuelta por circuito ≈ **300-800 ms** |
| Un lookup completo (O(log n), ~3-5 saltos, α=3 en paralelo) | **1-3 s** |
| Búsqueda de 5 palabras clave | 5 lookups **concurrentes**, no en serie ≈ **1-3 s** en total |
| Resolver un nombre y abrir el portal | lookup + rendezvous ≈ **3-5 s** en frío |

> **Medido en M2.** Sin circuitos, sobre loopback y con 30 celdas, una búsqueda completa
> (3-5 saltos de Kademlia con α=3) tarda **7 ms de mediana**. Es decir: el presupuesto de 1-3 s de
> arriba es **casi todo coste de circuito**, no del DHT. La lógica de Kademlia no es el problema;
> los 300-800 ms por salto sí, y por eso las tres mitigaciones de abajo no son opcionales.

Tres mitigaciones, las tres necesarias:

- **Circuito de directorio dedicado y caliente.** Uno o dos circuitos reservados para DHT, que se
  construyen al arrancar y se reutilizan. Así el coste de construcción no se paga en cada lookup.
- **Caché agresiva.** Tabla de rutas y descriptores en `~/.nullcell/dht.db`, cada uno con su TTL.
  Un nodo que lleva días encendido no debería necesitar casi ningún lookup en frío.
- **Resultados progresivos.** Los lookups se lanzan en paralelo y se empujan al HUD por WebSocket
  **a medida que llegan**, con los pendientes a la vista. "3 de 5 palabras resueltas" es mejor que
  una ruedita de 8 segundos, y encaja con la honestidad de interfaz de §9.

**Excepción, y solo una:** el mantenimiento de la tabla de rutas (pings de refresco a vecinos, que
no revelan interés por ningún contenido concreto) puede ir directo **si el nodo es un relevo
público** — ya es alcanzable y no tiene IP que esconder. Un cliente puro lo manda todo por
circuito, sin excepciones.

> **Construido en M4.** `dht/transport.py` tiene las dos implementaciones y Kademlia no sabe cuál
> usa. `CircuitTransport` alcanza al nodo consultado como **salida** de un circuito de 3 saltos, y
> mantiene **un solo circuito de directorio reutilizado**: medido, 1 construcción para 5
> peticiones. Cada transporte declara `anonymous`, y el HUD lo muestra, porque la diferencia entre
> los dos *es* la decisión 8 y no debe quedar implícita.

## 8. L3/L4 — Servicios, nombres, acceso y buscador

### Descriptor de servicio
```json
{
  "id": "k7f2qz...xq", "kind": "web", "name": "panaderia",
  "title": "La Panadería", "desc": "Pan, pedidos y recetas",
  "tags": ["comida","tienda"], "lang": "es",
  "intro_points": ["m4x...","p9z...","q2w..."],
  "ts": 1762560000, "ttl": 3600, "pow": "00000a3f...", "sig": "ed25519(...)"
}
```

### Nombres
- Siempre funciona `<nid>.null` (autocertificado, no se puede robar).
- `panaderia.null`: registro *first-come* en el DHT bajo `sha256("name:panaderia")`, con PoW y
  renovación obligatoria; si caduca, se libera. Alias personales en `~/.nullcell/hosts`.

### Acceso del usuario: ¿cómo se navega?

**Decisión: navegador existente, con perfil dedicado lanzado por Nullcell.** Ni el navegador normal
del usuario (mezcla su identidad real), ni un navegador escrito por nosotros (más trabajo que la
red entera y toda su superficie de ataque a nuestro cargo).

El programa levanta en `127.0.0.1`:
- **SOCKS5** `:9150` — resolución de nombres remota obligatoria.
- **HTTP proxy** `:9151` para clientes sin SOCKS.
- **UI local** `:9180` — buscador, circuitos, tus sitios. Protegida con **token de sesión +
  validación de cabecera `Host`**: cualquier web del internet normal puede lanzar peticiones a
  localhost, y sin eso la UI sería atacable desde fuera (DNS rebinding / CSRF).

`ncell browse` lanza Firefox con `-profile ~/.nullcell/browser-profile` y estos ajustes:

| Ajuste | Por qué |
|---|---|
| `network.proxy.socks = 127.0.0.1:9150` | todo el tráfico va por circuitos |
| **`network.proxy.socks_remote_dns = true`** | **crítico**: sin esto el navegador resuelve `.null` por el DNS del ISP → fuga de qué visitas y además no funciona |
| `media.peerconnection.enabled = false` | WebRTC revela IPs locales y reales |
| geolocalización, telemetría, autocompletar: off | reducir fingerprint y rastros |
| perfil separado | sin cookies, historial ni sesiones del internet normal |

### CA local y "secure context" (detalle que importa más de lo que parece)

Los navegadores tratan `.onion` como origen confiable por una excepción explícita del estándar
(RFC 7686 / W3C secure contexts). **`.null` no tendría ese privilegio**: todo saldría marcado
como "no seguro" y el navegador **bloquearía service workers, WebCrypto, cámara y notificaciones**.
Las webs de Nullcell nacerían lisiadas.

Solución: Nullcell genera una **CA local propia**, la instala **únicamente en el perfil dedicado**
(nunca en el almacén de confianza del sistema — eso sí sería un riesgo real para la máquina del
usuario) y emite por sitio un certificado ligado a su clave Ed25519, validado contra el NID.
Resultado: `https://panaderia.null` con candado y todas las APIs modernas disponibles.

No es TLS por confidencialidad — el circuito ya cifra extremo a extremo — es TLS **para que el
navegador conceda los permisos de origen seguro**.

### Por qué NO se incrustan sitios ajenos en la UI local

Un iframe con un sitio de Nullcell dentro de `127.0.0.1:9180` haría correr su JavaScript en el
origen de nuestra propia UI: podría llamar a la API local, publicar en tu nombre, leer tu estado o
apagar tu nodo. En cambio, servir cada sitio en su propia URL `https://<id>.null` hace que la
**política de mismo origen del navegador aísle cada sitio gratis**, sin que escribamos nada. Es el
argumento técnico más fuerte a favor del modelo proxy.

### Navegador embebido (opcional, v2)

Un "Nullcell Browser" con Chromium embebido (PyQt6-WebEngine o pywebview) daría la experiencia de
una sola app. Coste: +150–300 MB de empaquetado, actualizaciones de seguridad del motor a nuestro
cargo y una superficie de ataque enorme. Va en M8 como modo opcional, nunca como requisito.

```
$ ncell browse
  ✓ 3 circuitos listos · guard: m4x…kz
  ✓ perfil aislado + CA local
  → abriendo navegador en https://search.null
```

### Hostear
```bash
ncell host ./mi-sitio --name panaderia    # estático
ncell host --wsgi app:application         # Flask/FastAPI
```
El servidor HTTP interno escucha **sobre circuitos**, nunca en un puerto público. Cero port
forwarding.

### Buscador — tres mecanismos
1. **Índice DHT por palabra clave:** al publicar, `sha256("kw:" + palabra)` → `{id, title, score}`
   por cada palabra de título/desc/tags. Buscar = N lookups. Funciona desde el día 1, sin crawler.
2. **FTS local:** SQLite **FTS5**. Un crawler educado (`robots.txt`, rate limit) recorre los
   servicios conocidos vía circuitos y guarda el texto → búsqueda instantánea y offline.
3. **Gossip de índices:** intercambio de Bloom filters + deltas firmados, para que un nodo nuevo
   herede el conocimiento de la red sin recrawlear todo.

**Ranking:** BM25 × uptime observado × nº de nodos que confirman el descriptor × endosos firmados
de nodos en los que *tú* confías (web of trust personal, sin autoridad global).

**Prioridad entre los tres mecanismos — no están en pie de igualdad.** El **gossip (3) es el
primario**: Bloom filters y deltas firmados son lo que permite a un nodo nuevo heredar el
conocimiento de la red en minutos. Rastrear portales por circuitos de 6 saltos (2) es lentísimo y
no escala, así que el crawler queda como complemento para profundidad de texto, con presupuesto
explícito: máximo 1 portal concurrente, 1 petición cada 2 s, `robots.txt` obligatorio y solo con el
nodo inactivo. El índice DHT (1) es el que funciona desde el día 1 y el que sostiene las búsquedas
de un nodo recién instalado.

**Punto caliente del índice por palabra clave.** `sha256("kw:pan")` concentra **todo** el tráfico y
el almacenamiento de esa palabra en los mismos 20 nodos, sin límite: es el problema de *hot spot*
clásico de Kademlia y aquí además es un vector de spam directo. Dos medidas, las dos necesarias:

- **Tope de entradas por clave** (p. ej. 256) con desalojo por `dificultad_pow × antigüedad ×
  confirmaciones`. Quien quiera ocupar una palabra popular tiene que pagar PoW de forma sostenida.
- **Sharding de claves populares** en `kw:pan:0..15`, para repartir la carga entre 16 grupos de
  nodos distintos en lugar de castigar a 20 voluntarios por tener una palabra de moda.

## 9. Dashboard local — NULLCELL HUD

La pantalla principal del programa: `https://hud.null` (servida en `127.0.0.1:9180`, dentro del
perfil dedicado). Es a la vez el panel de control del nodo, el buscador y la **portada de la red**.
Estética cyberpunk, pero cada dato que muestra sale de una fuente real del protocolo.

```
┌─ NULLCELL ─────────────────────────────── k7f2qz…xq ── ▲2.1MB/s ▼0.8MB/s ─┐
│ ◈ buscar en la red…                                      [ 3 circuitos ]  │
├───────────────────────────┬───────────────────────────────────────────────┤
│ CIRCUITOS                 │ PORTALES ACTIVOS            47 vistos · 31 ok │
│ ●───●───●  C1    182ms    │ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│ m4x p9z q2w               │ │ ⬡ sello │ │ ⬡       │ │ ⬡       │           │
│ ●───●───●  C2    241ms    │ │panadería│ │ ágora   │ │ archivo │           │
│ ●───●───●  C3      — ⟳    │ │ web · es│ │ chat    │ │ files   │           │
├───────────────────────────┤ │~4m · 7✓ │ │~1m · 12✓│ │~22m · 3✓│           │
│ MIS SERVICIOS             │ └─────────┘ └─────────┘ └─────────┘           │
│ panaderia.null ● 3 int    ├───────────────────────────────────────────────┤
│ 142 req/h · 1.2 MB        │ TOPOLOGÍA (grafo XOR)    ░░▒▒▓█ 1.2k celdas   │
├───────────────────────────┴───────────────────────────────────────────────┤
│ > circuito C4 construido vía m4x…kz · 3 saltos · 198ms                    │
│ > descriptor publicado · ttl 3600 · pow 0.4s                              │
└───────────────────────────────────────────────────────────────────────────┘
```

### Paneles y de dónde sale cada dato

| Panel | Dato | Fuente real |
|---|---|---|
| Cabecera | NID, sello, ancho de banda, rol | `core/identity.py`, contadores del enlace |
| Buscador | consulta | `search/query.py`: FTS5 local + lookups DHT por keyword |
| Circuitos | 3 saltos, RTT por salto, edad, estado | `onion/circuit.py` (RTT ya medido con marcos PING) |
| Portales activos | nombre, kind, tags, frescura, confirmaciones, confianza | descriptores firmados del DHT + gossip de observaciones |
| Mis servicios | intro points vivos, peticiones, tráfico | `onion/intro_point.py`, `host/` |
| Relay (si activo) | marcos reenviados, circuitos en tránsito, cuota usada | `onion/relay_logic.py` |
| Topología | nodos y relays conocidos, buckets | `dht/routing_table.py` |
| Consola | eventos en vivo | bus de eventos interno vía **WebSocket** (sin polling) |

### Decisión importante: ¿cómo sabemos que un portal está "operativo"?

La tentación es que cada nodo sondee cada servicio. **Sería un ataque de denegación de servicio
construido dentro del propio diseño**: N nodos × M servicios × un circuito de 6 saltos cada uno.
Además, sondear revela a los intro points qué te interesa.

Modelo elegido, de barato a caro:

1. **Frescura del descriptor (pasivo, por defecto).** Si el descriptor se renovó en el DHT hace
   menos de su TTL, el servicio está vivo: publicar exige tener intro points funcionando. Coste de
   red: cero extra.
2. **Gossip de observaciones firmadas.** Un nodo que visitó un portal publica `{id, visto_en, ok}`
   firmado. El HUD muestra "visto hace 4 m · confirmado por 7 nodos". Es reputación distribuida,
   no una medición propia.
3. **Sondeo activo solo bajo demanda.** Si el usuario pulsa "comprobar", *ese* nodo abre un
   circuito. Nunca automático, nunca en bucle, con rate limit.

El HUD **no inventa métricas**: muestra "visto hace 4 m por 7 nodos", no un "99,8 % uptime" que no
podemos verificar. Honestidad en la interfaz.

### Los sellos: identidad visual sin tocar la red

Un favicon o miniatura traída del sitio remoto sería contenido ajeno cargado en el origen de
nuestra UI: fuga de que lo estás mirando + superficie de ataque. En su lugar, cada portal recibe un
**sello criptográfico generado localmente**: geometría determinista derivada de `sha256(nid)`
(tipo identicon, hexágonos y trazos). Cero red, cero riesgo, imposible de falsificar — dos claves
distintas no comparten sello — y encaja con la estética.

Miniaturas reales: opcionales, solo en M8, traídas por circuito, **re-codificadas** en nuestro lado
para eliminar metadatos y cargas maliciosas, servidas desde nuestro origen con CSP estricta.

### Reglas duras del HUD

- **Cero peticiones externas.** Nada de CDN, nada de Google Fonts, nada de telemetría: fuentes,
  CSS y JS empaquetados en el repo. Se impone con `Content-Security-Policy: default-src 'self'`.
  Un solo `<link>` a un CDN delataría al internet normal que hay un nodo Nullcell aquí.
- **Nunca incrustar sitios ajenos** (iframe) en el origen de la UI. Abrir en pestaña propia.
- **Token de sesión + validación de `Host`** en toda la API local.
- **Nombres reservados:** `hud.null`, `search.null`, `me.null` se resuelven localmente y
  quedan **prohibidos en el registro DHT**, o alguien podría secuestrar tu panel de control.
- **Arranque en frío:** un nodo nuevo no conoce ningún portal. El HUD no debe aparecer vacío →
  `bootstrap.json` incluye una semilla de portales conocidos, y el gossip rellena el resto en
  minutos. Hay que diseñar el estado "conectando" para que no parezca roto.

### Dirección visual (cyberpunk sin volverse ilegible)

| Elemento | Decisión |
|---|---|
| Base | negro azulado `#07090d`, no negro puro; superficies `#0d1117` |
| Acentos | **máximo dos**: cian `#22d3ee` (datos/activo) y magenta `#f0f` (alertas). La saturación total en todo es el error típico del estilo |
| Texto | casi blanco `#e6edf3` para cuerpo; el neón solo en bordes, cifras y acentos — contraste mínimo 4.5:1 |
| Tipografía | monoespaciada empaquetada (JetBrains Mono o IBM Plex Mono) para datos; sans condensada para titulares |
| Motivos | rejilla de fondo, corchetes tipo HUD, barras de datos, sparklines, brillo suave en bordes, scanlines **muy** sutiles |
| Glitch | solo como evento puntual (circuito caído, portal nuevo). Permanente = ruido que estorba |
| Animación | CSS, nunca JS por frame; con 100 tarjetas el panel no debe quemar CPU |
| Accesibilidad | respetar `prefers-reduced-motion`: sin scanlines ni animación. Solo tema oscuro, declarado como tal |

Stack: `aiohttp` + WebSocket para empuje en vivo, frontend sin build (HTML/CSS/JS plano o Preact
empaquetado localmente), sellos en SVG generados en `gateway/sigil.py`.

### Personalización y temas

La dirección visual de arriba es la **piel por defecto**, no la única posible. Pero tres decisiones
ya tomadas acotan de forma dura qué puede ser un tema aquí:

1. **`default-src 'self'`** (reglas duras): un tema **no puede** traer fuentes, CSS ni imágenes de
   fuera. Un solo `<link>` a un CDN delataría al internet normal que hay un nodo Nullcell en esta IP.
2. **Frontend sin build**: nada de pipeline de compilación de temas. CSS plano, legible, versionado.
3. **`prefers-reduced-motion`**: lo respeta *cualquier* tema, no solo el de serie.

Con eso, el mecanismo es el mínimo que funciona:

- **Tokens CSS en `:root`** — `--bg`, `--surface`, `--accent-data`, `--accent-alert`, `--text`,
  `--text-dim`, `--grid`, `--glow`, `--font-mono`, `--font-display`. La piel cyberpunk es el valor
  **por defecto de esos tokens**, no colores literales repartidos por la hoja de estilo.
- **`~/.nullcell/theme.css`** opcional. Si existe, el HUD lo sirve **desde su propio origen** (no
  como fichero externo, que violaría la CSP) después de la hoja base. Solo puede redefinir tokens;
  no se inyecta JS ni se cargan recursos nuevos.
- **Temas incluidos:** `nullcell` (por defecto), `mono` (sin neón, para capturas y documentación) y
  `contrast` (alto contraste, cumple AA en todos los pares). Siguen siendo **solo oscuros**: el
  tema claro no está en v1 y se declara como decisión, no como olvido.
- **Fuentes:** únicamente las empaquetadas en `hud/fonts/`. Un tema elige entre ellas, no añade.
- **Lo que no es configurable:** los **sellos** —su geometría sale de `sha256(nid)`, es identidad
  criptográfica, y que fueran editables significaría poder suplantar visualmente a otro portal— y
  el **layout de paneles**, que queda fijo en v1.

**Validación en CI:** un test comprueba que la hoja base no contiene colores literales fuera de
`:root` y que los tres temas incluidos pasan 4.5:1 en texto de cuerpo. Un tema del usuario que no
cumpla no se rechaza —es suyo— pero el HUD lo marca.

### El estado "conectando" (arranque en frío)

Las reglas duras dejaban esto como pendiente: *"un nodo nuevo no conoce ningún portal; hay que
diseñar el estado conectando para que no parezca roto."* Concretado, porque es la primera pantalla
que ve cualquiera y decide si el proyecto parece vivo o parece averiado:

| Fase | Qué se ve | Duración típica |
|---|---|---|
| 0. Identidad | NID y sello aparecen **al instante** — son locales, no esperan red | < 50 ms |
| 1. Bootstrap | "buscando celdas… 0/8 contactadas", con la lista rellenándose una a una | 1-5 s |
| 2. Primer enlace | contador de celdas conocidas subiendo, topología empezando a dibujarse | 2-10 s |
| 3. Circuitos | las tres barras `●───●───●` se construyen **visiblemente, salto a salto** | 3-15 s |
| 4. Portales | la semilla de `bootstrap.json` primero, el gossip rellenando después | 10-60 s |

Reglas del estado de arranque:

- **Ningún panel aparece vacío sin explicación.** Cada uno dice qué está esperando y de qué fase
  depende.
- Las fases completadas **se marcan y no se re-animan**. Un indicador que vuelve a girar parece un
  fallo.
- Si una fase falla (sin red, sin peers alcanzables, puerto UDP bloqueado) el panel lo dice **con
  el motivo** y un botón de reintento. Nunca se queda girando indefinidamente.
- El buscador queda **deshabilitado con el motivo a la vista** hasta que haya al menos un circuito,
  en lugar de aceptar una consulta que todavía no puede responder.

### Cuándo se construye

El HUD **no es solo decoración, es la herramienta de depuración del proyecto**: un visor de
circuitos en vivo vale oro cuando se está construyendo la capa onion. Por eso se parte en dos:

- **M3**: HUD mínimo y feo — circuitos, saltos, RTT, consola de eventos. Para depurar.
  **Construido.** `ncell node --hud`, en `gateway/localui.py` y `hud/`. Las cuatro reglas duras
  están verificadas por tests que nombran el ataque que cada una detiene: sin token 401, token
  falso 401, `Host` ajeno 403 (DNS rebinding), CSP sin nada externo. Cero peticiones externas y
  cero colores literales fuera de `:root` son también tests, no buenas intenciones.
- **M7**: el HUD completo con buscador, portales, sellos, topología y la piel cyberpunk.

## 10. Anti-abuso, seguridad y modelo de amenaza

- **Sybil/spam:** PoW argon2id para publicar nombres, descriptores y keywords; rate limit por
  circuito; cuotas de almacenamiento DHT.

> **⚠ Parámetros del PoW, fijados con mediciones de M2.** El plan decía "PoW argon2id" sin
> parámetros, y al implementarlo apareció un factor que no estaba considerado: **verificar es
> superficie de DoS**. Cada nodo del DHT tiene que verificar cada registro que le ofrecen, así que
> un PoW caro de verificar es un arma apuntando a los nodos honestos.
>
> argon2id es **simétrico**: un intento de resolución y una verificación cuestan lo mismo. La
> asimetría útil viene de los **bits de dificultad** (resolver = 2^bits intentos, verificar = 1),
> no de la dureza en memoria. Medido en 1 núcleo:
>
> | memoria | ms/hash | bits para ~0,4 s | verificaciones/s | asimetría |
> |---|---|---|---|---|
> | 8 KiB | 0,118 | 12 | 8.465 | 4.096× |
> | 64 KiB | 0,296 | 10 | 3.373 | 1.024× |
> | **256 KiB** | **0,911** | **9** | **1.097** | **512×** |
> | 1 MiB | 3,385 | 7 | 295 | 128× |
> | 4 MiB | 13,403 | 5 | 75 | 32× |
>
> **Elegido: 256 KiB, `opslimit=1`.** Es donde la dureza en memoria sigue siendo real (32× el
> mínimo de PyNaCl, el paralelismo en GPU no es gratis) y un nodo aún verifica más de mil registros
> por segundo. Más a la derecha, la verificación se desploma y la dureza extra cuesta más de lo que
> compra.
>
> Dificultad por tipo de registro, porque el recurso escaso no es el mismo:
>
> | Registro | Bits | Resolver (esperado) | Por qué |
> |---|---|---|---|
> | Descriptor de relevo | 9 | ~0,5 s | lo renueva cada relevo cada hora |
> | Descriptor de servicio | 9 | ~0,5 s | se renueva dentro de su TTL |
> | Entrada de keyword | 11 | ~1,9 s | **es el vector de spam directo** (§8); un sitio con 10 palabras paga ~19 s |
> | Registro de nombre | 14 | ~15 s | first-come sobre un espacio global y escaso |
>
> **Y el desalojo puntúa por el trabajo realmente hecho, no por la dificultad que el tipo exige.**
> Son cosas distintas: el valor exigido vive en una tabla global que una actualización de protocolo
> podría cambiar, y eso reescribiría retroactivamente la posición de todo lo ya almacenado. Los
> bits reales son una propiedad del registro, se miden con un solo hash y solo suben haciendo más
> trabajo.
- **Moderación sin censura central:** listas de bloqueo firmadas y suscribibles. Cada usuario
  elige curadores; ninguna lista es obligatoria ni global.
- **Política de relay:** opt-in, con límites de ancho de banda y de conexiones que fija el
  operador. Un relay no puede leer lo que reenvía, así que solo puede negarse por volumen.
- **Apagado elegante del relevo:** `ncell relay drain` deja de aceptar circuitos nuevos y termina
  los vivos antes de salir. Sin esto, un operador que reinicia tira cientos de circuitos de golpe,
  y ese patrón es a la vez una mala experiencia y una señal observable en la red.
- **Nota legal, sin rodeos:** una red anónima atrae también contenido ilegal, y operar un relay
  público puede traer reclamos al operador. El proyecto debe (a) documentarlo en el README,
  (b) no incluir herramientas de evasión de detección, (c) dejar el relay en opt-in explícito.
- **Amenazas y estado:**

| Amenaza | Estado |
|---|---|
| MITM | Resuelto: direcciones autocertificadas |
| Espionaje del ISP | Resuelto: solo ve QUIC hacia tu guard |
| Relay malicioso lee contenido | Resuelto: cifrado en capas + E2E en rendezvous |
| Correlación de tráfico extremo a extremo | **No resuelto** (tampoco en Tor). Mitigado con guards, marcos fijos y relleno |
| Sybil / eclipse en el DHT | **Parcialmente**: PoW + diversidad + verificación cruzada |
| Un adversario global que ve toda la red | Fuera del modelo de amenaza. Hay que declararlo |
| **Adversario cuántico** (derivar la privada de una dirección publicada) | Fuera del modelo, igual que en Tor v3. La dirección lleva la clave pública (§3); migrar exigirá rotar direcciones, y el byte de `version` existe para eso |
| Ataque a la UI local desde una web normal | Resuelto: token de sesión + validación de `Host` |
| Secuestro del panel de control vía nombre DHT | Resuelto: `hud/search/me.null` reservados y prohibidos en el DHT |
| Sondeo masivo de portales = DDoS de la propia red | Resuelto por diseño: frescura de descriptor + gossip, sondeo solo bajo demanda (§9) |
| Fugas por el navegador (DNS, WebRTC, JS) | Resuelto: perfil dedicado con `socks_remote_dns` y WebRTC off (§8) |
| **Ataque de etiquetado** (un guard voltea bits para confirmar el circuito) | Resuelto: digest acumulado por capa en la subcabecera `RELAY` (§5) |
| **Fuga del DHT** (a quién consultas y desde qué IP) | Resuelto: todo el DHT de un cliente va por circuitos (§7) |
| **Censo de servicios desde el DHT** | Resuelto para servicios privados con índice cegado y cuerpo cifrado (§6). Asumido y declarado para los portales públicos: ser encontrable es ser enumerable |
| **Inundación de `CREATE`** para agotar la CPU de un relevo | Mitigado: rate limit por enlace + PoW exigible en `CREATE` cuando el relevo está bajo carga |
| **Spam del índice por palabra clave** | Mitigado: tope de entradas por clave con desalojo por PoW y sharding de claves populares (§8) |
| Enumeración de relevos | Fuera del modelo: los descriptores de relevo son públicos por necesidad, igual que el consenso de Tor. Hay que declararlo, no esconderlo |

## 11. Stack Python

| Necesidad | Elección | Por qué |
|---|---|---|
| Concurrencia | `asyncio` + `uvloop` | miles de circuitos, stdlib |
| Transporte | **`aioquic`** | QUIC: multiplexado y congestión resueltos |
| Cripto | `PyNaCl` (ya instalado) + `cryptography` | Ed25519, X25519, ChaCha20-Poly1305 auditados |
| Handshake | `noiseprotocol` (patrón NK) | cripto probada; no inventamos nada |
| PoW | `argon2-cffi` | anti-Sybil con coste en memoria |
| Serialización | `cbor2` | binario, compacto, sin ejecución de código (≠ pickle) |
| HTTP / proxy / UI | `aiohttp` | servidor, cliente y websockets |
| Índice | `sqlite3` + FTS5 | full-text real, cero dependencias |
| CLI / TUI | `typer` + `textual` | `ncell status` con circuitos en vivo |
| Pruebas | `pytest-asyncio` + Docker Compose (30 nodos) | probar la malla de verdad, no mocks |

Licencia sugerida: **AGPL-3.0** (obliga a publicar modificaciones de nodos en red).

**Nota importante sobre este stack:** `aioquic` tiene la máquina de estados de QUIC en **Python
puro**, y el coste por marco en Python es la restricción que manda sobre el rendimiento de todo
el proyecto. No es motivo para cambiar de lenguaje —el control de la red no es el cuello de
botella— pero sí para diseñar la ruta caliente con cuidado desde M1. Ver **§16**.

## 12. Estructura del repo

```
nullcell/
  core/      identity.py  crypto.py  frames.py  ids.py  config.py
  net/       quic_link.py  link_manager.py  cert.py  listener.py
  onion/     circuit.py  builder.py  handshake_nk.py  relay_logic.py
             rendezvous.py  intro_point.py  guards.py  path_select.py
  dht/       kademlia.py  routing_table.py  records.py  store.py  pow.py
  discovery/ bootstrap.py  mdns.py  pex.py
  services/  descriptor.py  publisher.py  resolver.py  names.py
  gateway/   socks5.py  http_proxy.py  localui.py  localca.py  browser_profile.py
             hud_api.py  sigil.py  events.py
  hud/       index.html  app.js  style.css  fonts/   # cero dependencias externas
  host/      site_static.py  site_wsgi.py  robots.py
  search/    indexer.py  crawler.py  query.py  gossip.py  rank.py
  cli/       main.py  tui.py
tests/       unit/  integration/  net_lab/   # docker compose, 30 nodos
docs/        PROTOCOL.md  THREAT_MODEL.md  QUICKSTART.md  RELAY_OP.md
             PERFORMANCE.md  SECURITY.md  CONTRIBUTING.md
bench/       link_throughput.py  circuit_build.py  relay_forward.py  rendezvous.py
bootstrap.json  pyproject.toml  README.md  LICENSE
```

`SECURITY.md` (cómo reportar una vulnerabilidad y en cuánto se responde) y `CONTRIBUTING.md` no son
opcionales en un proyecto cuya viabilidad depende de que gente desconocida levante relevos (§1).
`bench/` existe desde M1 porque los objetivos de §16 son puertas de hito, y una puerta sin
instrumento de medida no es una puerta.

### Estado en disco — `~/.nullcell/`

Estaba repartido por cinco secciones distintas. Reunido:

| Ruta | Qué guarda | Notas |
|---|---|---|
| `identity.key` | Ed25519 + X25519 | modo **600**, cifrable con passphrase (§3) |
| `peers.db` | peers conocidos de sesiones anteriores | entrada a la red sin depender de `bootstrap.json` (§7) |
| `dht.db` | caché de tabla de rutas y descriptores, con TTL | lo que evita pagar lookups en frío (§7) |
| `index.db` | SQLite + FTS5 del buscador | modo **WAL**, escrituras en hilo dedicado (§16) |
| `hosts` | alias de nombres personales | formato texto, editable a mano (§8) |
| `theme.css` | tema del HUD, opcional | solo redefine tokens `:root` (§9) |
| `ca/` | CA local y certificados por sitio | **nunca** se instala en el almacén del sistema (§8) |
| `browser-profile/` | perfil dedicado de Firefox | lo gestiona `ncell browse` (§8) |
| `services/<nombre>/` | claves y estado de cada portal que alojas | incluye si es público o privado (§6) |
| `config.toml` | configuración del nodo y política de relevo | |

**Aviso de ubicación:** el directorio **no debe vivir en una carpeta sincronizada en la nube**
(iCloud Drive, Dropbox). Sincronizar `identity.key` saca tu clave privada de la máquina, y
sincronizar las bases SQLite las corrompe. Si el nodo detecta que `~/.nullcell/` está dentro de una
ruta sincronizada conocida, avisa al arrancar.

## 13. Hitos

| Hito | Entrega | Prueba de éxito |
|---|---|---|
| **M0** | esqueleto, `ncell id`, config, CI | identidad persistente reproducible |
| **M1** | enlace QUIC + verificación de fingerprint + marcos 512B | 2 nodos intercambian marcos, cliente anónimo |
| **M2** | Kademlia + descriptores firmados + PoW + bootstrap/mDNS/PEX | 30 nodos Docker se encuentran solos |
| **M3** | **circuitos onion de 3 saltos** (Noise NK, EXTEND, capas, SENDME) + **HUD mínimo de depuración** | el salto 2 no puede saber origen ni destino (test); los circuitos se ven en vivo |
| **M4** | **rendezvous + intro points + descriptores de servicio** | dos nodos tras NAT se hablan sin abrir puertos |
| **M5** | streams de app, `ncell host`, SOCKS5, CA local, `ncell browse` | **Firefox abre `https://k7f2…xq.null`** con candado ← momento "wow" |
| **M6** | nombres legibles + guards + diversidad de rutas | `https://panaderia.null` funciona y la ruta es sana |
| **M7** | buscador + **NULLCELL HUD completo** (portales, sellos, topología, piel cyberpunk) | buscar "pan" encuentra el sitio de otro nodo; el HUD lista portales vivos |
| **M8** | TUI, instaladores, docs de relay, relleno, **modo TUN y navegador embebido opcionales** | alguien ajeno instala, publica un sitio y lo encuentran |

M3 y M4 son el núcleo duro: ahí se decide si el proyecto es real. Todo lo demás es trabajo
conocido. M5 es el momento en que **se siente** como otro internet.

**Puertas de rendimiento.** La columna "prueba de éxito" de arriba es funcional: dice si *funciona*,
no si es *usable*. Cada hito tiene además un objetivo medible en **§16**, y los de M1, M3 y M5 son
bloqueantes: si no se pasan, no se avanza de hito, se perfila. La razón es concreta — en Python el
coste por marco es la restricción que manda, y descubrirlo en M5 con la capa onion ya escrita
significa reescribirla.

## 14. Siguiente paso propuesto

Construir **M0 + M1** en una sesión: identidad, marcos, enlace QUIC con verificación de
fingerprint y un `ncell ping <nid>` funcionando entre dos procesos locales. Es la base
sobre la que se apoya todo lo demás y se puede probar de inmediato.

## 15. Nombre y nomenclatura

El nombre anterior lo ocupa `matrix.org`. **Elegido: Nullcell.** Candidatos verificados el
2026-11-08 en PyPI, npm y GitHub:

| Candidato | PyPI | npm | Veredicto |
|---|---|---|---|
| **nullcell** | libre | libre | ✅ **ELEGIDO** |
| nullgrid | libre | libre | ✅ alternativa limpia |
| zerocell / voidcell | libre | — | ✅ variantes |
| tesela | libre | ocupado | ✅ metáfora perfecta, estética distinta |
| umbral, tessera, cinder, vesper, nocturne | **ocupados** | — | ❌ |

**Criterio que importa más que la disponibilidad: no colisionar con el vocabulario de nuestro
propio campo.** Tres trampas evitadas:
- **Erebus** → "ataque Erebus" es un ataque real de partición de la red P2P de Bitcoin.
- **Eclipse** → "eclipse attack" ya aparece en nuestro propio modelo de amenaza (§10).
- **Nebula** y **Reticulum** → son redes overlay existentes; colisión frontal.

### Objeciones honestas a "nullcell"

1. **Choque de vocabulario interno:** en el protocolo, una "celda nula" sería naturalmente una
   celda de relleno (`PADDING`). **Solución:** la unidad de 512 B pasa a llamarse **marco**
   (*frame*, vocabulario estándar de redes) y "celda" queda libre para designar al **nodo**. Con
   eso el nombre deja de chocar y empieza a sumar: *una celda sola no es nada; muchas celdas son
   la red* — que es exactamente la tesis del proyecto.
2. **Connotación:** "celda" evoca prisión, y "célula" en español evoca célula terrorista. Para una
   red anónima que ya arrastra un problema de percepción pública, es un regalo para un titular de
   prensa. No es descalificante, pero hay que saberlo de antemano.
3. **"null" es el prefijo más gastado del branding hacker** (nullcon, null community, nullbyte,
   /dev/null): legalmente libre, pero suena genérico dentro del nicho.

### Nomenclatura completa

| Concepto | Término | Nota |
|---|---|---|
| Proyecto / red | **Nullcell** | |
| Binario / CLI | `ncell` | **no** `nc`: es netcat |
| Paquete | `nullcell` (PyPI) | libre |
| Sufijo de direcciones | **`.null`** + alias `.null.alt` | ver abajo |
| Nodo | **celda** (*cell*) | tu nodo = tu celda |
| Unidad de protocolo (512 B) | **marco** (*frame*) | renombrado para no chocar con "celda" |
| Roles | cliente · **relevo** · **guardián** · **punto de enlace** · **punto de encuentro** | relay · guard · intro point · rendezvous |
| Identidad / dirección | **NID** = `base32(pub ‖ checksum ‖ version)` — **56 chars** | La dirección *contiene* la clave (§3) |
| Clave de enrutamiento DHT | `sha256(pub)` | Distinta de la dirección, a propósito (§3) |
| Sitios alojados | **portales** | |
| Panel de control | **HUD** → `hud.null` | |
| Buscador | **Oráculo** → `search.null` | |
| Avatar criptográfico | **sello** (*sigil*) | §9 |
| Configuración | `~/.nullcell/` | |
| Red de pruebas | `nullcell-lab` | 30 celdas en Docker |

### Sufijo: `.null`, y además `.null.alt`

**RFC 9476** (sept. 2023) reserva `.alt` para espacios de nombres que **no** son DNS y recomienda
a los proyectos existentes moverse ahí. Lo correcto según el estándar sería `panaderia.null.alt`;
lo bonito es `panaderia.null`.

Decisión: **el resolvedor acepta ambos**. Cuesta tres líneas, `.null` queda como forma canónica y
`.null.alt` como forma correcta ante el IETF. Tor usó `.onion` y obtuvo su reserva formal
(RFC 7686) *después*; esa puerta queda abierta.

Por qué `.null` y no `.cell`: un gTLD comercial `.cell` es plausible (operadoras de telefonía),
`.null` es mucho menos probable. Menos riesgo de colisión futura.

**Nota de fuga:** si un nombre `.null` escapa del proxy, va al DNS global y responde NXDOMAIN —
fallo seguro, pero el resolvedor del ISP ve la cadena. De ahí que `socks_remote_dns` (§8) sea
obligatorio y no opcional.

### Idioma del proyecto

**Decidido: código, comentarios y `docs/PROTOCOL.md` en inglés** (para que cualquiera pueda
contribuir), **README bilingüe** y **HUD con i18n, español por defecto**.

### Sobre "nodo" y "celda"

Son sinónimos en la documentación: **"celda" es el término de la interfaz y del material público**
("tu celda", "1.2k celdas activas"), y "nodo" se usa libremente en prosa técnica por claridad. La
unidad de protocolo es siempre **marco**, nunca celda, para que no haya ambigüedad.

### Carpeta del repositorio — resuelto

El repositorio vive **en minúsculas**, igual que el paquete de PyPI y el futuro repositorio de
GitHub, dentro de la carpeta de trabajo que ya existía:

```
RodentTech/
└── RED-NULLCELL-P2P/              ← abrir el editor AQUÍ
    └── nullcell/                  ← raíz del repositorio git
        ├── nullcell/              paquete Python
        ├── tests/  bench/  docs/  .github/
        ├── NULLCELL-ARQUITECTURA.md
        └── pyproject.toml
```

Se eligió anidar en lugar de renombrar la carpeta exterior porque **el historial de sesiones del
editor se indexa por la ruta absoluta del proyecto**: renombrar la carpeta exterior lo habría
dejado huérfano. El nivel `RED-NULLCELL-P2P/` sobra conceptualmente y se conserva solo por eso.
Para GitHub es invisible: el repositorio empieza en `nullcell/`.

> **⚠ Trampa real, nos pasó al montar esto.** Mover una carpeta llamada `nullcell` dentro de otra
> que pasaba a llamarse `nullcell`, **en iCloud Drive**, hizo que la sincronización resolviera la
> colisión renombrando el paquete a `nullcell 2/`. Resultado inmediato: `ncell` dejó de importar y
> `pytest` no pudo ni recoger las pruebas.
>
> Se recuperó sin pérdidas **porque el commit era anterior al movimiento**: un `git status` mostró
> los 13 ficheros como borrados más un `nullcell 2/` sin rastrear, y renombrar de vuelta devolvió
> el árbol byte a byte. Dos lecciones que valen para cualquier movimiento de este repositorio:
> **commitear antes de mover**, y **`git status` justo después** — es el detector de duplicados de
> iCloud. El riesgo era de la transición, no permanente: la estructura anidada ya asentada se
> sincroniza sin problema.

**Aviso, por lo de §12:** la carpeta está dentro de iCloud Drive. Para el documento es cómodo; para
el repositorio de código es tolerable pero molesto (iCloud sincroniza `.git/` y `__pycache__`), y
para `~/.nullcell/` **no** — ver el aviso de ubicación en §12. El estado del nodo vive fuera, en
`~/.nullcell/`, y el entorno virtual también, en `~/.venvs/nullcell/`.

**Trampa al mover o renombrar la carpeta:** una instalación editable (`pip install -e .`) graba la
ruta absoluta del proyecto dentro del entorno virtual, así que mover o renombrar la carpeta **rompe
el comando `ncell`** hasta reinstalar:

```bash
cd <nueva-ruta> && ~/.venvs/nullcell/bin/pip install -e .
```

Está anotado aquí porque el síntoma —`ncell` que deja de existir, o importa el paquete viejo— no
apunta a su causa por ningún lado.

Estado del directorio `RodentTech/` que hay que limpiar a mano:

| Entrada | Qué es | Qué hacer |
|---|---|---|
| `NULLCELL-ARQUITECTURA.md` | este documento, versión buena | conservar |
| `RED-MATRIX-P2P` | **archivo vacío de 0 bytes**, no una carpeta; lo dejó el editor al abrir una ruta que ya no existía | borrar |
| `nullcell/` | carpeta con una copia anterior de este documento | borrar o reutilizar como carpeta del proyecto |

Nombre sugerido para la carpeta definitiva: **`nullcell`** en minúsculas, igual que el paquete de
PyPI y el futuro repositorio de GitHub.


## 16. Rendimiento: restricciones, objetivos y tácticas

Las secciones anteriores definen **qué** hace la red. Esta define **a qué velocidad puede hacerlo** y
qué hay que vigilar para que siga siendo usable. Ninguna de las tácticas de aquí cambia una sola
propiedad de seguridad o de anonimato de §1-§10: si alguna lo hiciera, está marcada.

> ### ⚠ Corregido con datos de M1 — 2026-11-08
>
> Esta sección se escribió a priori y **una de sus premisas resultó falsa al medirla**. El texto
> original decía que la restricción que manda es el coste por marco en Python. No lo es:
>
> | Medición (loopback, 1 núcleo, Python 3.11.3) | Resultado |
> |---|---|
> | Nuestra capa de marcos, empaquetando | **2.280.000 marcos/s** (1,17 GB/s) |
> | Techo de `aioquic` con bytes crudos | **21,3 MB/s** (plano para cualquier escritura ≥ 4 KB) |
> | Enlace completo con nuestros marcos | 19,6 MB/s · 38.218 marcos/s — **91,8 % del techo** |
>
> La capa de marcos va **~50× por encima** de lo que el transporte puede mover. El techo lo fija
> el procesado de paquetes QUIC de `aioquic` en Python puro, y es un techo **en bytes/s, no en
> marcos/s**. Las consecuencias están al final de la sección, en *Hallazgos de M1*.

### La restricción que manda sobre todas las demás

> Lo que sigue es el razonamiento original, conservado porque explica el diseño de la ruta
> caliente —que sigue siendo correcto— aunque su conclusión sobre dónde está el cuello de botella
> quedó refutada por la medición de arriba.

**El coste por marco en Python.** La cuenta, para un circuito a 10 MB/s:

```
10 MB/s ÷ 512 B        =  20.500 marcos/s
× 3 capas de ChaCha20  =  61.500 llamadas de cifrado por segundo
```

Lo que cuesta **no es el cifrado** —OpenSSL mueve gigabytes por segundo— es cruzar la frontera
Python→C 61.500 veces por segundo, asignando objetos en cada cruce. Y encima `aioquic` procesa los
paquetes QUIC en Python puro, que suma sobre el mismo presupuesto.

**Consecuencia honesta, y va al README:** Nullcell no va a dar el ancho de banda de tu línea. El
orden de magnitud realista por circuito en Python puro es de **unidades de MB/s**, no de decenas.
La latencia de 6 saltos (§1) es irreducible por diseño; el throughput sí es un problema de
ingeniería, y esto es lo que se puede hacer al respecto.

### Objetivos medibles por hito

El documento tenía prueba de éxito funcional en cada hito y **ninguna de rendimiento**. Estas son
puertas: si no se pasan, no se avanza de hito, se perfila. Se miden con `bench/` (§12).

Los objetivos de M1 están **reexpresados tras medir** (ver el aviso de arriba): una cifra absoluta
de marcos/s medía a `aioquic`, no a Nullcell, así que la puerta es ahora la **eficiencia sobre el
techo del transporte**, que sí mide nuestro código.

| Hito | Métrica | Objetivo mínimo | Cómo se mide |
|---|---|---|---|
| **M1** | marcos/s empaquetados por la capa de marcos, 1 núcleo | **≥ 500.000** · *medido: 2.280.000* | `bench/link_throughput.py` |
| **M1** | eficiencia del enlace sobre el techo crudo de QUIC | **≥ 85 %** · *medido: 91,8 %* | ídem, contra `bench_raw_ceiling` |
| **M1** | asignaciones por marco en la ruta caliente | **< 1 byte** · *medido: 0,04* | `tracemalloc` en estado estable |
| **M2** | búsqueda remota en malla de 30 celdas, loopback sin circuitos | **≤ 100 ms** · *medido: 7 ms de mediana* | `tests/integration/test_dht.py` |
| **M3** | construcción de circuito de 3 saltos, red local | **≤ 150 ms** · *medido: 3,9 ms* | `bench/circuit_build.py` |
| **M3** | eficiencia de la capa onion: 3 saltos vs (1 salto ÷ 3) | **≥ 80 %** · *medido: 99 %* | ídem |
| **M3** | marcos/s reenviados por un relevo, 1 núcleo | **≥ 60.000** | `bench/relay_forward.py` |
| **M4** | establecimiento de rendezvous (6 saltos) | **≤ 2 s** en laboratorio | `bench/rendezvous.py` |
| **M5** | carga completa de una página con 20 recursos | **≤ 3 s** en laboratorio | Firefox con el perfil + `performance.timing` |
| **M7** | HUD con 200 tarjetas de portal | **≤ 5 % CPU** en reposo, 60 fps al desplazar | perfilador del navegador |
| **M7** | eventos/s empujados al HUD con un relevo saturado | **≤ 20/s** tras agregación | contador en `gateway/events.py` |

Las puertas de **M1, M3 y M5** son bloqueantes. Las demás son objetivos.

### Tácticas — capa de marcos y enlace

**1. Coalescencia de escrituras.** El tamaño fijo de 512 B es una propiedad del **marco**, no de la
escritura al socket. Escribir 40 marcos en un solo `stream.write()` colapsa el coste de syscall y
de asyncio en ~40× y **no cambia nada** para un observador: sigue viendo un flujo de unidades
uniformes e indistinguibles entre sí. Es la optimización con mejor relación beneficio/riesgo de
toda la lista, y no toca ninguna propiedad de §10.

**2. Cero asignaciones en la ruta caliente del relevo.** Quitar una capa de cifrado es un XOR sobre
un buffer. Se implementa con `memoryview` sobre `bytearray` preasignados de un pool, nunca con
concatenación de `bytes` ni con *slices* que copian. Un relevo que asigna dos objetos por marco
pasa la mitad de su tiempo en el recolector de basura en lugar de reenviando.

**3. Contexto de cifrado reutilizado por lote.** Procesar los marcos de un mismo circuito en lotes
permite reutilizar el objeto cipher por salto y por lote, en lugar de construir uno por marco.

**4. Multi-core en los relevos — la de mayor impacto para la red entera.** Un relevo en una máquina
de 8 núcleos usa **uno**, por el GIL. Como los relevos son el recurso escaso de todo el proyecto
(§1: *"sin relays no hay red"*), que un relevo aproveche sus 8 núcleos **multiplica por 8 la
capacidad que aporta cada voluntario**. La vía es `SO_REUSEPORT` con N procesos escuchando el mismo
puerto UDP.

> **Advertencia honesta:** con QUIC esto no es gratis. El kernel reparte los paquetes por hash del
> 4-tuple, y si un cliente migra de ruta sus paquetes pueden aterrizar en el proceso equivocado,
> que no tiene ese estado de conexión. Dos salidas: **(a)** desactivar la migración de ruta en v1 y
> aceptar la limitación, o **(b)** dirigir paquetes por connection ID con eBPF, que es lo que hace
> Cloudflare — trabajo de M8 y solo en Linux. Va al documento como limitación conocida, no como
> sorpresa de implementación.

**5. Módulo acelerado opcional, con respaldo en Python puro.** La capa de cripto de marcos en Rust
vía PyO3 daría un orden de magnitud. **Esto roza la filosofía del proyecto**: §15 decidió el código
en inglés justamente para que cualquiera pueda contribuir, y una extensión nativa es una barrera.
La regla que lo hace aceptable, y que no se negocia:

> **La implementación de referencia en Python puro siempre funciona y es la que se lee para
> entender el protocolo.** El módulo nativo es un acelerador **opcional** que un operador de relevo
> instala si quiere. Nadie necesita Rust para contribuir, para auditar ni para usar la red.

**6. Decisión a cerrar midiendo: ¿512 B es el tamaño correcto?** Tor usa 514 porque está escrito en
C y el coste por celda es despreciable. Aquí el coste por marco **es** la restricción, y un marco de
1024 B —o de ~1200 B, que encaja en un datagrama QUIC sin fragmentar— reduce la CPU por byte a la
mitad o más. El precio es desperdicio de relleno en mensajes pequeños: una pulsación de teclado en
un chat costaría 1200 B en lugar de 512.

**No se cambia a ciegas.** La uniformidad en toda la red es el punto del tamaño fijo, y cambiarlo
después es incompatible hacia atrás. Se decide **con el benchmark de M1 en la mano**, y por eso
`VERSIONS` (§5) tiene que existir desde el primer día.

> **✓ DECIDIDO con la medición de M1: se queda en 512 B.** El razonamiento de arriba daba por
> supuesto que el techo era en **marcos/s**; es en **bytes/s** (21,3 MB/s, plano). Con un marco de
> 1024 B el rendimiento en MB/s sería el mismo y los marcos/s **bajarían a la mitad**, pagando
> además más relleno en mensajes pequeños. Un marco mayor no compra nada. Queda cerrado.

### Tácticas — latencia percibida

Los 6 saltos son irreducibles (~500-800 ms de ida y vuelta). El **tiempo de carga de una página**
no lo es.

**7. HTTP/2 hacia el navegador.** No estaba dicho en ninguna parte qué versión de HTTP habla el
proxy local con el navegador, y es determinante: con HTTP/1.1 el navegador abre 6 conexiones y
encola el resto; con **HTTP/2** las 20 peticiones de una página se multiplexan sobre **un** circuito
usando los `stream_id` de §5. La CA local de §8 ya es el requisito que lo hace posible —ningún
navegador habla h2 sin TLS—, así que el coste adicional es casi nulo. Es probablemente la mayor
mejora de experiencia percibida de toda esta sección.

**8. Pool de circuitos calientes, con política escrita.** El HUD muestra "3 circuitos listos", pero
la política no estaba en ninguna parte:

| Regla | Valor |
|---|---|
| Circuitos limpios en reserva | **3**, reconstruidos en cuanto uno se usa |
| Pre-extensión | los dos primeros saltos se construyen por adelantado; el tercero al conocer el destino |
| **Aislamiento** | **un circuito por portal destino** |
| Reintento | 2 intentos con guards distintos antes de informar de fallo al navegador |

El **aislamiento por destino** es una propiedad de anonimato, no una optimización: si compartes
circuito entre portales, el último salto puede enlazar tus visitas entre sí. Sube el número de
construcciones, y por eso el pool tiene que ser agresivo — es la compensación directa de una
propiedad de anonimato pagada con CPU y ancho de banda.

**9. Caché en el gateway.** Las respuestas cacheables de los portales se guardan en el gateway
local respetando sus cabeceras. Un recurso que no se pide son 6 saltos que no se pagan.

### Tácticas — buscador y DHT

**10. Lookups paralelos, cacheados y progresivos** — especificado en §7, con su presupuesto de
latencia.

**11. Gossip primario, crawler secundario** — especificado en §8.

**12. Tope y sharding del índice por palabra clave** — especificado en §8.

### Tácticas — nada que bloquee el event loop

Dos cosas del diseño actual van a congelar el bucle de eventos entero, y congelarlo significa
circuitos que no responden, enlaces con timeout y HUD colgado:

| Qué | Coste | Dónde va |
|---|---|---|
| **PoW argon2id** | el propio HUD muestra `pow 0.4s` | Fuera del bucle, sin excepción: 400 ms congelados tiran circuitos vivos. **Medido en M2: basta un `ThreadPoolExecutor`** — libsodium libera el GIL (3,69× en 4 hilos, casi lineal) y el bucle se retrasa como máximo ~5 ms. Nada de `ProcessPoolExecutor`: sin pickling, sin riesgos de spawn/fork y sin exigir que el módulo principal sea reimportable. Además, repartir la búsqueda entre hilos divide el tiempo por N: un nombre de 14 bits baja de ~15 s a ~4 s en cuatro núcleos |
| **SQLite / FTS5** | decenas de ms en un índice grande | Hilo escritor dedicado + modo **WAL** + lecturas en executor |
| Ficheros de `~/.nullcell/` | variable; discos de red y carpetas sincronizadas pueden ser lentísimos | executor de hilos |

**Regla general del proyecto:** nada que pueda tardar más de **5 ms** va en una corrutina sin
executor. Un test de CI con un vigilante del bucle de eventos falla la build si alguna iteración
supera **50 ms**.

### Tácticas — HUD

§9 fija el objetivo correcto ("con 100 tarjetas el panel no debe quemar CPU") sin los mecanismos:

| Problema | Mecanismo |
|---|---|
| 200+ tarjetas de portal | `content-visibility: auto` + lista virtualizada por ventana visible |
| 200 sellos SVG | Generar **una vez**, cachear como `<symbol>` y referenciar con `<use>`. Regenerar geometría en cada render es gratis con 10 y caro con 500 |
| Consola de eventos | **Buffer circular con tope de 500 líneas.** Sin tope, el DOM crece sin límite en una sesión larga y el navegador acaba muriendo |
| Manguera del WebSocket | Un relevo activo genera miles de eventos/s. Hace falta **agregación en el servidor**: contadores fusionados, empuje por lotes a 10 Hz, y niveles (`debug`/`info`/`warn`) filtrados **en el servidor**, no en el cliente |
| Animación | Solo CSS, y solo `transform`/`opacity` — las propiedades que no provocan reflow. Nunca JS por frame (ya en §9) |

### Relojes

Los TTL de descriptores, los periodos de cegado (§6) y la frescura de portales (§9) dependen de la
hora, y no se puede confiar en el reloj del sistema ni depender de NTP, que es a la vez una huella y
un vector. Lo que se hace:

- Se **acepta un desajuste de ±2 h** en la validación de descriptores.
- Los periodos de tiempo del cegado **se solapan** en los bordes (§6).
- El nodo **estima su propio desvío** a partir de las marcas de tiempo de los descriptores firmados
  de muchos relevos distintos, usando la **mediana** —no la media— para que unos pocos nodos
  mintiendo no lo puedan mover.
- Si el desvío estimado supera **1 h**, el HUD lo avisa: a partir de ahí los fallos de validación
  parecen fallos de red y son un infierno de depurar.

### Hallazgos de M3 — la capa onion no es el coste

| Lo que decía el plan | Lo que mide el banco |
|---|---|
| Construcción de circuito de 3 saltos ≤ 150 ms | **3,9 ms de mediana.** Dos handshakes Noise NK relevados más uno directo; sobra margen de 38× |
| Throughput por circuito ≥ 5 MB/s | **3,1 MB/s**, y el objetivo estaba mal planteado — ver abajo |

**El objetivo absoluto de throughput medía el banco, no el diseño. Otra vez.** En el laboratorio,
un marco de 3 saltos cruza **tres enlaces QUIC dentro del mismo proceso y el mismo bucle de
eventos**, así que el techo es el de un enlace partido por tres. Reformulado como lo que de verdad
se quería saber —*¿cuánto cuesta la capa onion?*— midiendo 1 salto contra 3:

| | marcos/s | MB/s |
|---|---|---|
| 1 salto | 18.611 | 9,5 |
| 3 saltos | 6.121 | 3,1 |
| ideal de 3 saltos en 1 núcleo (1 salto ÷ 3) | — | 3,2 |
| **eficiencia de la capa onion** | | **99 %** |

**Las tres capas de ChaCha20 y los digests acumulados son esencialmente gratis.** Encaja con el
hallazgo de M1: el techo lo pone `aioquic`, y todo lo que escribimos encima queda muy por debajo de
él. En la red real cada relevo es una máquina distinta, así que el throughput de un circuito lo
limitará el enlace más lento (~21 MB/s), no la suma de tres en un núcleo.

**La lección, que ya es la segunda vez:** un objetivo absoluto de rendimiento sobre una topología
simulada en un proceso mide la simulación. Las puertas de rendimiento de este proyecto se expresan
**relativas a un techo medido**, no en cifras absolutas.

Por el camino, escribir §5 destapó dos asignaciones por marco en mi propia ruta caliente: el digest
se calculaba sobre una **copia de 507 bytes** con el campo a cero, y el tag se escribía con otra
copia. Las dos eliminadas —el digest se alimenta en tres trozos de un `memoryview`, el tag se
escribe en sitio— que es literalmente la regla de la táctica 2 aplicada a quien la escribió.

### Hallazgos de M1 — lo que cambió al medir

| Lo que decía el plan | Lo que mide el banco | Qué hacer |
|---|---|---|
| El coste por marco en Python es la restricción que manda | Falso: la capa de marcos va a 2,28 M marcos/s, ~50× el transporte | Dejar de tratar la capa de marcos como el cuello de botella. Sigue valiendo escribirla sin asignaciones, pero ya no es ahí donde se gana |
| Táctica 1, coalescencia: "~40× menos coste de syscall" | **Confirmada y es enorme**: 6,7 MB/s con un marco por escritura → 21,8 MB/s con lotes de 64. **3,2×** | No es una optimización opcional: sin ella la red va a un tercio de velocidad. Debe estar en la ruta por defecto |
| Táctica 6, revisar el tamaño de marco | **Refutada**: el techo es en bytes/s, no en marcos/s | 512 B se queda. Decisión cerrada |
| Táctica 5, módulo acelerado en Rust para la cripto de marcos | **Prematuro**: nuestra capa no es el límite | Si algún día se acelera algo nativo, el candidato es el procesado de paquetes QUIC, no nuestros marcos. Baja de prioridad |
| Objetivo M1 de 40.000 marcos/s | Era el **97 % del techo absoluto** del transporte: medía a `aioquic` | Reexpresado como eficiencia sobre el techo (≥ 85 %) |

**La consecuencia grande, y va al README:** el límite de rendimiento de Nullcell es hoy
`aioquic`, no el diseño del protocolo ni la capa de marcos. ~21 MB/s por enlace en loopback y
1 núcleo. Eso **confirma la conclusión honesta** de esta sección —unidades de MB/s, no decenas—
pero por una razón distinta a la que se había escrito. Si algún día hace falta más, el trabajo
está en el transporte QUIC (multi-core del §16.4, o un QUIC nativo), no en nuestro código.

### Lo que queda deliberadamente sin optimizar

Para que no se confunda con un olvido:

- **La latencia de 6 saltos.** Es el diseño, no un defecto. Reducirla significa renunciar al
  anonimato de uno de los dos extremos.
- **El coste del PoW.** Es el punto: encarecer el Sybil. Se mueve fuera del bucle, no se abarata.
- **El tamaño fijo del marco.** Su valor exacto se decide midiendo (táctica 6), pero que sea **fijo
  y uniforme en toda la red** no se toca: es una medida anti-análisis de tráfico.
- **Las peticiones externas del HUD.** Cero sigue siendo cero. Ninguna mejora de carga justifica un
  CDN (§9, reglas duras).
