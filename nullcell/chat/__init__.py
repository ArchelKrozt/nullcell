"""Chat tipo IRC sobre la red — M10.

Canales, presencia y mensajes firmados, montados sobre lo que ya existe: el
DHT para encontrarse y las sesiones de rendezvous para hablar. No se inventa
criptografía nueva.

Lo que **sí** hay que decir en voz alta, porque es nuevo y es metadato:
pertenecer a un canal es visible para cualquiera que consulte el DHT por ese
canal. El contenido va cifrado extremo a extremo entre los dos interlocutores,
pero *quién está en #general* no es secreto, y no puede serlo en un canal al
que cualquiera se puede unir. Por eso el chat usa una **identidad propia**,
distinta de la de tu portal: ver tu apodo en un canal no debe revelar que eres
quien publica `nullcell.null`.
"""

from __future__ import annotations

__all__ = ["CHAT_IDENTITY_FILE"]

#: La identidad del chat vive aparte de `identity.key` a propósito.
CHAT_IDENTITY_FILE = "chat.key"
