# Nullcell en un VPS, y la red desde el teléfono

Esta guía monta dos cosas distintas en el mismo servidor y conviene no
confundirlas, porque una es un regalo a la red y la otra es un riesgo que
asumes tú:

1. **Un relevo** (`ncell relay`). Público, sin secretos, reenvía tráfico que no
   puede leer. Es lo que hace que la red exista. Si sólo vas a hacer una cosa,
   haz esta.
2. **Una pasarela** (`ncell browse --bind`) para que *tus* dispositivos
   naveguen por la red. Es la parte delicada: la pasarela es **el único
   componente de Nullcell que maneja texto claro**.

---

## Lo primero: por qué aquí no hay una URL normal

Lo natural sería `https://mivps.com/` y que esa página navegue la red por ti.
No está construido, y no por falta de tiempo:

* El servidor vería **el texto claro de todo** lo que miras, junto con **tu IP
  de verdad**. Nullcell existe para que ningún punto tenga esas dos cosas a la
  vez. Un gateway web las junta en el sitio con nombre y factura a tu nombre.
* Los sitios `.null` dejarían de estar aislados entre sí. Hoy cada uno vive en
  su propio origen (`https://<56 chars>.null`), así que el navegador impide que
  uno lea las cookies o el DOM de otro. Si todos pasan a ser rutas bajo
  `mivps.com`, el navegador los considera **el mismo sitio** y esa separación
  desaparece: un sitio hostil lee lo que haces en los demás.
* Y es un patrón con historial: es `tor2web`, y la recomendación de Tor sobre
  usarlo para navegar es no hacerlo.

Lo que esta guía monta no es eso. El tráfico **sigue cifrado hasta tu
dispositivo**; el VPS transporta, no traduce.

---

## Parte 1 — El relevo

```bash
curl -fsSL https://raw.githubusercontent.com/ArchelKrozt/nullcell/main/deploy/install.sh | sh
```

o a mano, que es lo mismo y se lee mejor (`docs/RELAY_OP.md` lo detalla):

```bash
ncell relay --announce tu-servidor.com:4433 --port 4433
```

`--announce` acepta **nombre DNS**, no sólo IP. Eso importa más de lo que
parece: hasta M8 un relevo anunciado por nombre quedaba fuera de toda ruta —
`§7` no resuelve nombres de relevos que está sopesando, porque la consulta DNS
revelaría la lista de candidatos. Ahora un nombre se indexa por su **dominio
padre** y, cuando una conexión real ocurre, su dirección observada sustituye al
dominio. Consecuencia práctica: **dos subdominios del mismo dominio cuentan
como un solo operador**, así que no montes tres relevos en
`a.midominio.com`, `b.midominio.com`, `c.midominio.com` esperando tres saltos.

Abre el puerto UDP (no TCP: es QUIC):

```bash
sudo ufw allow 4433/udp
```

Y guarda tu línea de semilla, que es lo que le das a otra gente para que entre:

```bash
ncell relay --announce tu-servidor.com:4433 --print-seed
```

### El primer nodo de una red nueva

Una red recién nacida tiene un problema de arranque que conviene saber antes de
pelearse con él: **`§7` exige 3 saltos en operadores distintos y 10 minutos de
antigüedad observada**. Con un solo relevo no hay circuito posible, y el error
te dirá exactamente qué suelo falló y a cuántos relevos excluyó cada uno.

* Para **probar en serio** necesitas 3 relevos en 3 proveedores distintos.
* Para **probar en una máquina**, `deploy/docker-compose.lab.yml` levanta una
  red de laboratorio completa. No es anónima y no pretende serlo.

---

## Parte 2 — El teléfono

El montaje, en una línea: **WireGuard hasta el VPS, y el proxy de Nullcell
escuchando sólo en la dirección del túnel.**

```
teléfono ──WireGuard (cifrado)──► VPS ──circuito de 3 saltos──► sitio .null
```

### 2.1 WireGuard

```bash
curl -O https://raw.githubusercontent.com/angristan/wireguard-install/master/wireguard-install.sh
sudo bash wireguard-install.sh
```

Te deja un QR para la app de WireGuard del teléfono. El servidor queda
normalmente en `10.8.0.1` y el teléfono en `10.8.0.2`. Comprueba con
`ip addr show wg0` cuál es la tuya.

### 2.2 La pasarela, escuchando en el túnel

```bash
ncell browse --bind 10.8.0.1 --no-launch
```

`--bind` **rechaza** una dirección pública y rechaza `0.0.0.0`. No avisa:
rechaza, con código de salida 2. El motivo es que quien alcance esa dirección
no "usa" la red — lee el texto claro de todo lo que pase por ahí y sale a la
red en nombre de tu nodo. Un proxy abierto en internet tarda minutos en ser
encontrado. Direcciones que sí acepta: loopback y cualquier cosa no enrutable
globalmente, que es donde viven WireGuard (`10.x`), una LAN (`192.168.x`) y
Tailscale (`100.64.x`).

### 2.3 El teléfono

En los ajustes de la Wi-Fi/VPN, proxy **manual**:

* **Servidor:** `10.8.0.1`
* **Puerto:** `9151` (el proxy HTTP; iOS y Android no saben de SOCKS en los
  ajustes del sistema)

Firefox para Android sí habla SOCKS5, y es mejor: usa `10.8.0.1:9150` y marca
*proxy DNS when using SOCKS*. Resolver los nombres en el proxy, y no en el
teléfono, es lo que evita que tu operador móvil vea qué buscas.

### 2.4 La CA

Para que `https://….null` no dé error de certificado, el teléfono tiene que
confiar en la CA local del nodo:

```bash
# en el VPS
cat ~/.nullcell/ca/ca.pem
```

Transfiérela al teléfono e instálala. En iOS son dos pasos: *Ajustes → General
→ VPN y gestión de dispositivos* para instalar el perfil, y después *General →
Información → Ajustes de certificados de confianza* para **activarla**; si te
salta el primero, el certificado está instalado y no sirve para nada.

---

## Los riesgos, escritos donde se ven

Esto no es una sección de relleno. Son las tres cosas que esta guía empeora
respecto a correr Nullcell en tu propio portátil:

**1. El VPS es un salto de confianza, y ve texto claro.** La pasarela termina
el TLS local para hablar con los sitios `.null`. Mientras el proxy corre ahí,
el VPS puede ver qué sitios abres. Tu proveedor de VPS sabe quién eres (le
pagas) y puede acceder a la máquina. Frente a la red Nullcell sigues siendo
anónimo — ningún relevo sabe de dónde sale el tráfico. Frente a **tu
proveedor de VPS**, no. Si eso no te sirve, corre `ncell browse` en el
portátil y deja en el VPS sólo el relevo.

**2. La CA en el teléfono es system-wide.** En tu ordenador, `§8` instala la CA
*sólo* en el perfil de Firefox aislado que crea `ncell browse`, nunca en el
almacén del sistema. En un teléfono eso no se puede: la instalas para todo el
dispositivo. La CA lleva **NameConstraints** limitándola a `null` y `null.alt`,
así que un certificado suyo para `tu-banco.com` debería ser rechazado —
**"debería"**: que iOS y Android respeten NameConstraints no lo he verificado,
y la documentación de ninguno de los dos lo promete. Trátalo como una
mitigación probable, no como una garantía. Mientras esa CA esté instalada, su
clave privada vive en el VPS.

**3. El teléfono no es el perfil aislado.** El navegador normal de tu teléfono
tiene tus sesiones, tus cookies y tu historial, y va a usar el mismo motor para
la red Nullcell. `§8` separa esas dos cosas con un perfil dedicado precisamente
para que un sitio `.null` no pueda mirar nada de lo otro. Usa al menos un
navegador distinto del que usas a diario, y mejor en modo privado.

**Lo que esta guía no hace, y es deliberado:** no expone nada de Nullcell a
internet salvo el relevo, que no puede leer lo que reenvía. El tráfico de tu
teléfono va cifrado por WireGuard hasta el túnel, y de ahí entra al circuito.
No hay ningún punto en el que el contenido viaje en claro por internet.

---

## Comprobar que funciona

```bash
# ¿cuántos relevos usables ve este nodo? El HUD y la línea de estado de
# `node` lo dicen; Ctrl-C para salir.
ncell node

# ¿resuelve un nombre?
ncell resolve <nombre>

# ¿responde el relevo desde fuera? Desde otra máquina:
ncell ping <nid>@tu-servidor.com:4433
```

`browse` imprime en cada ciclo `guards fijados: N · relevos usables: M`. Con
`M < 3` no hay ruta posible, y no es un fallo del montaje.

Si `browse` dice que no hay ruta, lee el mensaje completo: nombra cuál de los
dos suelos falló (antigüedad observada de 10 min, o el mínimo de PoW) y cuántos
relevos excluyó cada uno. Casi siempre es el primero, y se arregla esperando.

## El HUD, de paso

El panel de `§9` escucha en `127.0.0.1:9180` y **comprueba la cabecera Host**,
así que no se puede exponer con `--bind` ni por accidente: es una defensa
contra DNS rebinding y funciona. Para verlo desde tu portátil, túnel SSH:

```bash
ssh -L 9180:127.0.0.1:9180 usuario@tu-servidor.com
```

y abre la URL con token que imprime `ncell node` en el servidor.
