"""Por qué no se pudo abrir un portal — §9.

"No se pudo cargar la página" is the answer a browser gives when it does not
know, and here we almost always do. The states look alike from outside and mean
completely different things, and the difference decides what the person should
do next:

* **el nombre no existe** — nadie lo ha registrado. Nada que esperar.
* **el portal está apagado** — el nombre está registrado y apunta a una clave,
  pero el *descriptor* que dice por dónde alcanzarlo caducó porque nadie lo
  renueva. Vuelve solo cuando la máquina se encienda.
* **no hay red suficiente** — el problema es nuestro, no suyo: esta celda
  todavía no puede construir un circuito (§7).
* **no responde** — está publicado y aún así no contesta.

That the second one is even distinguishable is a property of the design, not a
guess: a descriptor expires in an hour and the owner republishes it while alive,
so its absence *is* the signal. Saying "no existe" there would be wrong, and it
is exactly what a generic error does.
"""

from __future__ import annotations

__all__ = ["explain"]


def explain(exc: BaseException, host: str = "") -> tuple[str, str]:
    """A title and an explanation an ordinary person can act on."""
    name = f"{host[:12]}…" if len(host) > 16 else (host or "ese portal")
    message = str(exc)
    low = message.lower()

    if "not registered in the directory" in low or "no está registrado" in low:
        return (
            "Ese nombre no existe en la red",
            f"Nadie ha registrado «{name}». Los nombres .null son del primero que llega, "
            "así que podría estar libre: si es tuyo, publícalo desde el panel.",
        )

    if "no usable service descriptor" in low or "may be offline" in low:
        return (
            "El portal existe, pero está apagado",
            f"«{name}» está registrado y apunta a una clave, pero no hay forma de alcanzarlo "
            "ahora mismo: su descriptor caduca en una hora y lo renueva la propia máquina "
            "mientras está encendida.\n\nNo hace falta hacer nada: vuelve solo en cuanto su "
            "dueño la encienda.",
        )

    if "no private descriptor" in low:
        return (
            "Es un servicio privado",
            f"«{name}» existe, pero sólo se anuncia a quien tiene su clave de acceso (§6).",
        )

    if "relays are usable" in low or "relevos" in low or "bootstrap" in low:
        return (
            "Todavía no hay red suficiente",
            "El problema es de este lado: esta celda aún no puede construir un circuito. "
            f"{message}",
        )

    if "did not answer" in low or "timeout" in low or "timed out" in low:
        return (
            "El portal no responde",
            f"«{name}» está publicado, pero no ha contestado a tiempo. Puede estar saturado, "
            "o acabar de apagarse sin que su descriptor haya caducado aún.",
        )

    if "no circuit" in low or "circuit" in low:
        return (
            "No se pudo construir el camino",
            f"No hay forma de llegar a «{name}» con los relevos que conoce esta celda.\n\n"
            f"{message}",
        )

    return ("No se pudo abrir", message)
