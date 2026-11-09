# Carpeta de despliegue

Todo lo que define **tu presencia en la red** vive aquí, para que mudarse a otra máquina —una
Raspberry Pi, un VPS— sea copiar una carpeta y no reconstruir una configuración.

```
null-despliegue/
  nullcell.toml     lo que TÚ declaras: qué publicar y con qué nombre
  estado.json       lo que el programa cambia en marcha
  identity.key      (no está todavía) la identidad de esta celda
```

```bash
ncell desktop                          # lo busca solo
ncell desktop --config ./otra-carpeta  # o se lo dices tú
```

## El programa no reescribe tu declaración

`nullcell.toml` es tuyo: lo escribes a mano, con comentarios, y el programa **sólo lo lee**. Lo que
cambia en marcha —si estás compartiendo ancho de banda— va a `estado.json`, que es del programa.

Esa separación no es orden por gusto. Antes el programa guardaba encima de tu fichero y, al
hacerlo, convertía un `../null-mi-web` escrito a mano en una ruta absoluta y borraba el título y
las etiquetas: exactamente lo que estas instrucciones te dicen que no hagas, hecho por el
programa y en silencio. Guardar con más cuidado era el parche; dejar de guardar ahí es el arreglo.

Por eso también TOML antes que JSON: **admite comentarios**, y una configuración que no puede
explicarse a sí misma acaba explicada en otro sitio que se desactualiza. Si prefieres JSON,
`nullcell.json` también se lee.

## Dónde se busca sola

Sin `--config`, se mira en este orden y se usa la primera que tenga declaración:

1. la última que usaste (se recuerda en `~/.nullcell/despliegue.json`)
2. la carpeta actual
3. `./null-despliegue` y `./despliegue`
4. `~/.nullcell/despliegue`

Si no hay ninguna, el escritorio **pregunta una vez**: buscarla, crear una nueva, o seguir sin
publicar nada —que es perfectamente válido: no hace falta despliegue para navegar ni para ser
relevo. Y si tu declaración está rota, lo dice en lugar de seguir a la siguiente candidata:
saltársela escondería una errata en el fichero que estás editando ahora mismo.

## La dirección *es* la clave

`nullcell.null` no es un dominio que alguien te alquile: es un nombre en el DHT atado a una clave
Ed25519, y la dirección de 56 caracteres del portal **es esa clave** (§3).

Consecuencia que conviene entender antes de mudarse: si copias esta carpeta a una Raspberry Pi
**sin `identity.key`**, allí arrancará una celda distinta, con otra dirección, y el nombre
`nullcell.null` seguirá apuntando a la vieja hasta que su registro caduque.

Para llevarte la identidad contigo:

```bash
cp ~/.nullcell/identity.key null-despliegue/identity.key
chmod 600 null-despliegue/identity.key
```

> **Eso es una clave privada en una carpeta que vas a copiar.** Trátala como tal: sin repositorios
> públicos, sin copias de seguridad en la nube, y con permisos `0600`. El programa avisa si
> encuentra otra cosa.

## Rutas relativas, a propósito

`"folder": "../null-mi-web"` es relativo **a esta carpeta**. Una ruta absoluta como
`/Users/alguien/sitio` deja de existir en cuanto la carpeta llega a otra máquina, y el fallo
aparece como un portal vacío en lugar de como un error claro. El programa avisa si ve una ruta
absoluta.

Si quieres que la mudanza sea una sola carpeta, mete el sitio dentro:

```
null-despliegue/
  nullcell.json     con  "folder": "sitios/mi-web"
  identity.key
  sitios/mi-web/
```

## Qué ve alguien si apagas la máquina

El nombre **no se pierde**: el registro `NAME` sigue apuntando a tu clave. Lo que caduca —en una
hora— es el *descriptor*, que dice por dónde alcanzarte, porque nadie lo renueva. Quien visite
verá **«el portal existe pero está apagado»**, no un error genérico. Al encender vuelve solo.
