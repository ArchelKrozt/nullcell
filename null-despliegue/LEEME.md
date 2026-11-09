# Carpeta de despliegue

Todo lo que define **tu presencia en la red** vive aquí, para que mudarse a otra máquina —una
Raspberry Pi, un VPS— sea copiar una carpeta y no reconstruir una configuración.

```
null-despliegue/
  nullcell.json     qué portales publicar y con qué nombre
  identity.key      (no está todavía) la identidad de esta celda
```

```bash
ncell desktop --config ./null-despliegue
```

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
