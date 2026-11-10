"""Invitaciones: una sola cosa que copiar y pegar — §1, §7.

Entrar a Nullcell exige saber de alguien, y hasta ahora eso significaba pasar
una línea de semilla cruda y, aparte, decir qué portal visitar. Dos cosas que
copiar, y una de ellas con una arroba y dos puntos en medio que invitan a
cortarla mal.

Una invitación es **una sola cadena** que lleva todo:

    nullcell://join?s=<dirección>@<host>:<puerto>&s=<otra>&p=nullcell

Tres decisiones, cada una con su motivo:

* **Es un URI legible, no base64.** Quien la recibe puede ver a qué celdas le
  están diciendo que se conecte antes de pegarla. Una invitación opaca pide
  confianza ciega para la única operación del sistema en la que la primera
  conexión es directa y revela tu IP.
* **Admite varias semillas.** Una sola es un punto único de fallo el día que
  esa celda esté apagada, y es también una forma de que una celda concreta vea
  entrar a todo el mundo.
* **Puede llevar un portal.** Casi siempre invitas a *algo*, no a la red en
  abstracto, y el panel puede abrirlo en cuanto haya ruta.

Lo que una invitación **no** es: una credencial. No firma nada y no avala nada;
todo registro aprendido a través de ella se verifica por separado (§7). Eso es
lo que impide que repartir invitaciones cree autoridades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final
from urllib.parse import parse_qsl, quote, urlsplit

from nullcell.core import ids

__all__ = ["Invitation", "InviteError", "build", "parse"]

SCHEME: Final[str] = "nullcell"
ACTION: Final[str] = "join"
#: Más que esto no es una invitación, es una lista de arranque.
MAX_SEEDS: Final[int] = 8


class InviteError(ValueError):
    """La invitación no se puede usar. El mensaje se le enseña a la persona."""


@dataclass(slots=True)
class Invitation:
    seeds: list[str] = field(default_factory=list)
    portal: str | None = None

    def __str__(self) -> str:
        return build(self.seeds, portal=self.portal)


def _check_seed(line: str) -> str:
    """Una línea `<dirección>@host:puerto`, validada de verdad."""
    nid, _, endpoint = line.strip().partition("@")
    if not endpoint:
        raise InviteError(f"«{line[:40]}» no tiene la forma dirección@host:puerto")
    try:
        ids.decode_nid(ids.parse_hostname(nid))
    except Exception as exc:  # noqa: BLE001
        raise InviteError(f"la dirección de «{line[:24]}…» no es válida: {exc}") from exc
    if ":" not in endpoint:
        raise InviteError(f"«{endpoint}» no dice el puerto")
    return f"{ids.parse_hostname(nid)}@{endpoint}"


def build(seeds: list[str], *, portal: str | None = None) -> str:
    """Una invitación a partir de líneas de semilla."""
    clean = [_check_seed(s) for s in seeds if s and s.strip()][:MAX_SEEDS]
    if not clean:
        raise InviteError("una invitación sin semillas no lleva a ninguna parte")
    parts = [f"s={quote(s, safe='@:.')}" for s in clean]
    if portal:
        parts.append(f"p={quote(ids.parse_hostname(portal), safe='')}")
    return f"{SCHEME}://{ACTION}?" + "&".join(parts)


def parse(text: str) -> Invitation:
    """Lee una invitación, o una línea de semilla suelta.

    Acepta las dos formas a propósito: pedirle a alguien que distinga entre
    «esto es una invitación» y «esto es una semilla» es pedirle que entienda
    una diferencia que no le importa.
    """
    text = (text or "").strip()
    if not text:
        raise InviteError("no hay nada que pegar")

    if not text.lower().startswith(f"{SCHEME}://"):
        return Invitation(seeds=[_check_seed(text)])

    split = urlsplit(text)
    if split.netloc.lower() not in ("", ACTION):
        raise InviteError(f"no sé qué es «{split.netloc}»; esperaba {SCHEME}://{ACTION}?…")

    seeds: list[str] = []
    portal: str | None = None
    problems: list[str] = []
    for key, value in parse_qsl(split.query, keep_blank_values=False):
        if key == "s":
            try:
                seeds.append(_check_seed(value))
            except InviteError as exc:
                # Una semilla rota no invalida las demás: la invitación lleva
                # varias precisamente para sobrevivir a que alguna falle.
                problems.append(str(exc))
        elif key == "p":
            portal = ids.parse_hostname(value) or None

    if not seeds:
        detail = f" ({problems[0]})" if problems else ""
        raise InviteError(f"esa invitación no trae ninguna semilla usable{detail}")
    return Invitation(seeds=seeds[:MAX_SEEDS], portal=portal)
