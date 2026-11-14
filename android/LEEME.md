# Nullcell para Android

Navegador de la red Nullcell con **nodo propio**: el teléfono arma sus propios
circuitos, igual que el escritorio. Nadie en el camino —tampoco el servidor del
proyecto— ve qué portales abres. El motor es el mismo código Python de
Nullcell, embebido con [Chaquopy](https://chaquo.com/chaquopy/); la interfaz es
Kotlin + Jetpack Compose.

## Qué hace

| Pestaña | |
|---|---|
| **Inicio** | Estado en vivo, anillo de progreso hacia el anonimato (operadores / 3), candado 🔒 / ⚠, celdas, relevos, semillas, accesos rápidos y actividad del nodo |
| **Buscar** | El buscador de la red (por nombre, palabra o etiqueta). Las consultas van por circuitos |
| **Navegar** | Navegador de portales `.null` con barra de dirección, candado, atrás/adelante, recargar y menú |
| **Semillas** | Gestor: añadir (hoja inferior, pegar línea o invitación `nullcell://`), quitar (con confirmación), intercambiar ahora, compartir la invitación |
| **Ajustes** | Tu dirección, estado del anonimato automático, qué protege el navegador, desconectar |

- **Una semilla basta.** La del servidor del proyecto viene incluida. Con un
  solo operador en la red se navega con 1 salto (cifrado, sin anonimato); con 3
  operadores la app pasa sola a 3 saltos. El candado lo dice siempre.
- **Semillas que circulan.** Las que añades se comprueban (que contesten con su
  identidad) y se comparten con los relevos; a cambio recibes las que te faltan.
  Así cada usuario nuevo entra por más puertas y la red deja de depender de una.
- **Nada sale a internet normal.** El WebView sólo carga direcciones `.null`:
  una imagen, fuente o script de internet se bloquea, porque saldría directo del
  teléfono y delataría tu IP. Sin caché, sin cookies de terceros.
- **El teléfono no se anuncia** ni presta ancho de banda: sólo navega.
- Las invitaciones `nullcell://join?s=…` abren la app con la semilla lista.

## Construir

```bash
bash android/construir.sh          # → android/salida/nullcell-<versión>.apk
RUEDAS=1 bash android/construir.sh # rehace también las ruedas de Python
```

Necesita docker y una cadena Android ya preparada (JDK 17, SDK 35, Gradle 8.10);
por defecto usa la del cliente de Mundo Nexo (`CADENA=/ruta/.cadena` para otra).
Compila dentro de un contenedor `python:3.12`, que es lo que Chaquopy necesita.

## Cómo entra Python en el APK

| Paquete | De dónde |
|---|---|
| `nullcell` | rueda de este repo, **sin `typer`** (el móvil no tiene línea de comandos; el nodo carga sin él) |
| `aioquic` 1.8 | rueda reempaquetada **sin piezas nativas**: su única extensión, `_buffer`, va en Python puro (`python-puro/_buffer.py`) |
| `aiohttp`, `multidict`, `yarl`, `propcache`, `frozenlist`, `cbor2<6` | ruedas puras, construidas sin sus extensiones C opcionales |
| `cryptography` 42.0.8, `PyNaCl` 1.5.0 | compiladas por Chaquopy para Android |

### Lo que está comprobado

- `python-puro/_buffer.py` da **exactamente** lo mismo que el `_buffer.c` nativo
  en 40 000 casos y 400 000 operaciones aleatorias (lecturas, escrituras,
  enteros de longitud variable, límites y errores).
- La suite entera de Nullcell (728 pruebas) pasa con **estas mismas ruedas puras**
  y sin ningún módulo nativo de esos paquetes.
- También pasa con `cryptography` 42.0.8, la que Chaquopy compila para Android
  (aioquic 1.8 declara `>=43`; la rueda reempaquetada lo relaja a `>=42`).
- `app/src/main/python/nullcell_movil.py` —el puente que usa la app— arranca un
  nodo contra la red real, encuentra `nullcell.null` en el buscador y lo carga.

### Lo que no está comprobado aquí

En el servidor de desarrollo no hay emulador arm64: **el APK no se ha ejecutado
en un teléfono desde aquí.** Si algo falla al abrirlo, `adb logcat -s Nullcell
python.stdout python.stderr` dice qué.

## Firma

El APK va firmado con la **clave de depuración** de Android. Sirve para
instalarlo a mano; para publicarlo hace falta una clave propia (y guardarla bien:
sin ella no se pueden publicar actualizaciones).
