# Operar un relevo

Un relevo es lo que hace que exista una red. §1 de la arquitectura lo dice sin
rodeos: *"La red necesita relays públicos voluntarios. Un usuario doméstico
puede navegar y publicar sin abrir nada, pero alguien tiene que prestar ancho
de banda alcanzable. Sin relays no hay red."*

Esto es la guía para hacerlo. Está en español porque va dirigida a operadores;
el código y el resto de `docs/` están en inglés (§15).

---

## Antes de empezar: lee esto

Operar un relevo público **puede traerte reclamos**. Una red anónima atrae
también contenido ilegal, y aunque un relevo **no puede leer lo que reenvía**
—el cifrado en capas de §5 lo impide por construcción— quien recibe una queja
es el titular de la IP.

Qué puedes decir con verdad si te preguntan:

- **No sabes qué pasó por tu relevo.** No es una postura: es matemáticamente
  cierto. Un relevo tiene una sola capa de cifrado, la suya, y el contenido va
  bajo otras dos.
- **No sabes de dónde venía ni a dónde iba.** Un relevo intermedio conoce dos
  vecinos y ninguno de los dos es el origen o el destino (§5).
- **No guardas registros de quién se conectó.** Por defecto el log de un relevo
  **no anota direcciones**; sólo aparecen con `--log-level debug`. Es
  deliberado: la diferencia entre un operador que no tiene nada que entregar y
  uno que sí.

Lo que **no** debes hacer: el proyecto no incluye ni incluirá herramientas de
evasión de detección, y el relevo es siempre **opt-in explícito** (§10).

---

## Lo que necesitas

- **Python 3.11 o superior.**
- **Un puerto UDP abierto** hacia internet. Por defecto 4433. QUIC va sobre UDP;
  si tu proveedor sólo deja pasar TCP, no sirve.
- **Una IP o nombre alcanzable desde fuera.** Lo que anuncias es lo que otros
  intentarán alcanzar.
- Poco más. Un relevo **no necesita root** (§0, decisión 3) y gasta más red que
  CPU.

No necesitas: dominio, certificado, ni abrir TCP 80/443.

---

## Instalación

### Con el instalador

```bash
git clone https://github.com/ArchelKrozt/nullcell
cd nullcell
./deploy/install.sh --relay TU_IP_PUBLICA:4433
```

Eso crea un usuario `nullcell` sin shell, instala en `/opt/nullcell`, levanta el
servicio de systemd y te imprime tu línea de semilla.

### A mano

```bash
python3 -m venv ~/.venvs/nullcell
~/.venvs/nullcell/bin/pip install .
~/.venvs/nullcell/bin/ncell relay --announce TU_IP:4433
```

### Con Docker

```bash
cd deploy
ANNOUNCE=tu.ip.publica:4433 docker compose up -d
docker compose logs | grep "seed line"
```

> **Sin probar:** la imagen de Docker no se ha construido todavía. El
> `Dockerfile` y los `compose` están escritos y validados sintácticamente, pero
> nadie ha ejecutado `docker build`. Si lo haces y falla, es un fallo real.

---

## El primer relevo de una red

Si eres el primero, **no tienes semillas**, y eso es correcto. El relevo lo dirá:

```
WARNING nullcell.relay  no seeds: this relay is alone. That is correct for the
                        first relay on a network — hand out the seed line above
```

Tu línea de semilla es lo que das a los demás:

```bash
ncell relay --announce TU_IP:4433 --print-seed
# jkssaepy2lvvfvds3paf3l42qgkxubxmsgw3tubcbmar56p7anzbcsyb@TU_IP:4433
```

**No es un secreto ni una credencial.** §7 es explícito: una semilla es una
*presentación*, nada más. No firma nada, no avala nada, y todo registro
aprendido a través de ella se verifica por separado. Puedes publicarla donde
quieras.

El segundo operador arranca así:

```bash
ncell relay --announce SU_IP:4433 --seed TU_LINEA_DE_SEMILLA
```

Cuando haya varias, se ponen en `bootstrap.json` y dejan de hacer falta a mano.

---

## Límites

```bash
ncell relay --announce TU_IP:4433 \
  --max-mbps 50 \        # declara tu ancho de banda (0 = sin declarar)
  --max-circuits 512 \   # tope de circuitos simultáneos
  --drain-timeout 30     # cuánto espera al apagarse
```

Un relevo **sólo puede negarse por volumen**: no puede leer lo que reenvía, así
que no hay nada que filtrar por contenido (§10). Si te preocupa la factura de
tráfico, `--max-mbps` es lo que tienes.

---

## Apagarlo bien

`systemctl stop` y `docker stop` envían `SIGTERM`, y eso **vacía** los circuitos
vivos antes de salir en lugar de tirarlos de golpe:

```
INFO nullcell.relay  SIGTERM: draining, up to 30 s
INFO nullcell.relay  drained cleanly
```

Tirar cientos de circuitos a la vez es a la vez una mala experiencia para quien
los usaba y **una señal observable en la red** (§10). Si tienes prisa, un
segundo `SIGTERM` sale de inmediato.

Con systemd, deja `TimeoutStopSec` por encima de tu `--drain-timeout`. Con
Docker, `stop_grace_period`.

---

## Vigilarlo

```bash
journalctl -u nullcell-relay -f
```

Una línea de estado cada minuto:

```
up 18.4 h · 37 circuits · 2841193 frames forwarded · 412 created ·
391 extended · 3 refused · 64 cells known
```

| Campo | Qué mirar |
|---|---|
| `circuits` | circuitos en tránsito ahora |
| `frames forwarded` | marcos de 512 B reenviados |
| `refused` | si sube, estás tocando `--max-circuits` o te están inundando de `CREATE` |
| `cells known` | si es 0 o 1, no te has unido a nada: revisa semillas y cortafuegos |

---

## Qué guarda en disco

Todo en `$NULLCELL_HOME`, por defecto `~/.nullcell`:

| Fichero | Qué es |
|---|---|
| `identity.key` | **tu clave privada**, modo 600. Si la pierdes, tu relevo es otro nodo |
| `peers.db` | celdas conocidas, para rearrancar sin partir de cero |
| `guards.json` | tus guards fijados (§7) |
| `dht.db`, `index.db` | cachés; se pueden borrar |

**No pongas esto en una carpeta sincronizada** (iCloud, Dropbox, OneDrive).
Sincronizar `identity.key` saca tu clave privada de la máquina y sincronizar las
bases SQLite las corrompe. El programa avisa si lo detecta.

Copia de seguridad: `identity.key` y nada más.

---

## Problemas

**`cells known` se queda en 0.** El puerto UDP no está abierto, o lo que pasas
en `--announce` no es alcanzable desde fuera. Compruébalo desde otra máquina:
`ncell ping TU_NID --addr TU_IP:4433`.

**`refused` sube sin parar.** O has puesto `--max-circuits` muy bajo, o alguien
te inunda de `CREATE`. El rate limit por enlace ya actúa; si persiste, bájalo.

**No arranca: `ModuleNotFoundError`.** Instalación incompleta. `pip install .`
otra vez en el venv.

**Avisa de carpeta sincronizada.** Mueve `$NULLCELL_HOME` fuera de la nube.

---

## Lo que este relevo no hace

Dicho para que no te lleves sorpresas:

- **No te hace anónimo a ti.** Un relevo es alcanzable por definición. Si
  quieres navegar con anonimato, eso es `ncell browse`, que es otra cosa.
- **No protege contra un adversario que ve toda la red.** Está fuera del modelo
  de amenaza (§10), y lo estará.
- **No resuelve la correlación de tráfico extremo a extremo.** Tampoco Tor.
- **La selección descentralizada de relevos es un problema abierto de
  investigación** (§7). Lo que hay son mitigaciones, no una solución demostrada
  frente a ataques Sybil y de eclipse.
