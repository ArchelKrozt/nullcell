"""Path selection — §7, guards and diversity."""

import os
import random
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from nullcell.onion.handshake_nk import x25519_public_bytes
from nullcell.onion.path_select import (
    GUARD_COUNT,
    GUARD_LIFETIME,
    MIN_OBSERVED_AGE,
    GuardSet,
    PathBuilder,
    PathError,
    RelayCandidate,
    RelayDirectory,
    endpoint_prefix,
    select_path,
)

OLD = time.time() - MIN_OBSERVED_AGE - 60


def directory_of(*relays) -> RelayDirectory:
    """Un directorio con esos relevos, saltándose `ingest` (que exige un
    registro firmado): aquí se prueba el mensaje, no la verificación."""
    directory = RelayDirectory()
    for relay in relays:
        directory._relays[relay.pub] = relay  # noqa: SLF001
    return directory


def candidate(host: str, *, first_seen: float = OLD, pow_bits: int = 9) -> RelayCandidate:
    """A usable relay at *host*. ``host`` may be an IP or a name."""
    return RelayCandidate(
        pub=os.urandom(32),
        x25519=x25519_public_bytes(X25519PrivateKey.generate()),
        endpoints=[f"{host}:4433"],
        first_seen=first_seen,
        pow_bits=pow_bits,
    )


def diverse(count: int, **kwargs) -> list[RelayCandidate]:
    """Candidates each in its own /16."""
    return [candidate(f"10.{i}.0.1", **kwargs) for i in range(count)]


# ------------------------------------------------------------------- prefixes
def test_prefix_is_16_for_ipv4_and_48_for_ipv6():
    assert endpoint_prefix("1.2.3.4:443") == endpoint_prefix("1.2.9.9:443")
    assert endpoint_prefix("1.2.3.4:443") != endpoint_prefix("1.3.3.4:443")
    assert endpoint_prefix("[2001:db8::1]:443") == endpoint_prefix("[2001:db8:0:1::9]:443")
    assert endpoint_prefix("[2001:db9::1]:443") != endpoint_prefix("[2001:db8::1]:443")


def test_a_hostname_is_keyed_by_its_parent_domain():
    """§7 will not resolve a relay it is merely weighing up, so a hostname is
    keyed by its domain instead of being excluded. Excluding it meant a relay
    announced by DNS name — the normal way to deploy on a VPS — could not be
    part of any circuit at all."""
    assert endpoint_prefix("relay.example.com:443") == "dns:example.com"
    assert endpoint_prefix("otro.example.com:443") == "dns:example.com"
    assert endpoint_prefix("relay.example.net:443") == "dns:example.net"


def test_a_bare_name_still_has_no_key():
    """It says nothing about who runs it."""
    assert endpoint_prefix("localhost:4433") is None
    assert endpoint_prefix("nonsense") is None


def test_two_relays_under_one_domain_count_as_one_operator():
    """Conservative on purpose: it refuses some paths that would have been
    fine, and §7 says refusing beats fudging."""
    assert endpoint_prefix("a.midominio.com:1") == endpoint_prefix("b.midominio.com:2")


def test_an_observed_address_wins_over_the_announced_one():
    """A relay announcing a hostname might resolve to an address already used
    by another hop, and only an observation can tell us."""
    relay = candidate("relay.example.com")
    assert relay.prefixes == {"dns:example.com"}
    relay.note_observed("203.0.113.9:4433")
    assert relay.prefixes == {"203.0.0.0/16"}


def test_an_observation_unmasks_two_names_as_one_machine(tmp_path):
    """The gap a hostname key cannot close on its own."""
    first, second, third = (
        candidate("a.uno.com"),
        candidate("b.dos.com"),
        candidate("c.tres.com"),
    )
    guards = GuardSet(tmp_path / "guards.json")
    assert len(select_path([first, second, third], guards)) == 3

    # Now we learn that two of them answer on the same machine.
    first.note_observed("203.0.113.9:4433")
    second.note_observed("203.0.113.9:4433")
    with pytest.raises(PathError):
        select_path([first, second, third], GuardSet(tmp_path / "g2.json"))


# ---------------------------------------------------------------------- guards
def test_guards_are_pinned_and_reused(tmp_path):
    pool = diverse(10)
    guards = GuardSet(tmp_path / "guards.json")
    first = set(guards.ensure(pool))
    assert len(first) == GUARD_COUNT
    assert set(guards.ensure(pool)) == first, "guards must not churn"


def test_guards_survive_a_restart(tmp_path):
    pool = diverse(10)
    path = tmp_path / "guards.json"
    chosen = set(GuardSet(path).ensure(pool))
    assert set(GuardSet(path).pubs) == chosen


def test_guards_are_in_distinct_prefixes(tmp_path):
    """Two guards behind one operator is one guard with extra steps."""
    pool = [candidate("10.1.0.1"), candidate("10.1.0.2"), candidate("10.1.0.3")]
    guards = GuardSet(tmp_path / "guards.json")
    assert len(guards.ensure(pool)) == 1


def test_young_relays_are_not_eligible_as_guards(tmp_path):
    fresh = diverse(5, first_seen=time.time())
    guards = GuardSet(tmp_path / "guards.json")
    assert guards.ensure(fresh) == []


def test_expired_guards_are_retired(tmp_path):
    pool = diverse(10)
    guards = GuardSet(tmp_path / "guards.json")
    guards.ensure(pool)
    assert len(guards) == GUARD_COUNT
    later = time.time() + GUARD_LIFETIME + 1
    guards.ensure(pool, now=later)
    # Retired and replaced in one pass, so the set stays full.
    assert len(guards) == GUARD_COUNT


def test_corrupt_guard_file_is_survivable(tmp_path):
    path = tmp_path / "guards.json"
    path.write_text("no soy json")
    assert GuardSet(path).pubs == []


# ------------------------------------------------------------------ selection
def test_selects_three_hops_starting_at_a_guard(tmp_path):
    pool = diverse(12)
    guards = GuardSet(tmp_path / "guards.json")
    hops = select_path(pool, guards, rng=random.Random(1))
    assert len(hops) == 3
    assert guards.is_guard(hops[0].pub)


def test_all_three_hops_are_in_distinct_prefixes(tmp_path):
    pool = diverse(12)
    guards = GuardSet(tmp_path / "guards.json")
    for seed in range(20):
        hops = select_path(pool, guards, rng=random.Random(seed))
        prefixes = [endpoint_prefix(h.endpoints[0]) for h in hops]
        assert len(set(prefixes)) == 3, f"seed {seed} reused a prefix"


def test_no_hop_appears_twice(tmp_path):
    pool = diverse(12)
    guards = GuardSet(tmp_path / "guards.json")
    for seed in range(20):
        hops = select_path(pool, guards, rng=random.Random(seed))
        assert len({h.pub for h in hops}) == 3


def test_a_concentrated_network_is_refused_not_fudged(tmp_path):
    """§7: better no circuit than one the user believes is diverse."""
    pool = [candidate("10.1.0.1"), candidate("10.1.0.2"), candidate("10.1.0.3")]
    guards = GuardSet(tmp_path / "guards.json")
    with pytest.raises(PathError, match="distinct prefixes"):
        select_path(pool, guards)


def test_too_few_relays_is_refused(tmp_path):
    guards = GuardSet(tmp_path / "guards.json")
    with pytest.raises(PathError, match="2 of 2 relays are usable"):
        select_path(diverse(2), guards)


def test_a_brand_new_node_must_wait(tmp_path):
    guards = GuardSet(tmp_path / "guards.json")
    with pytest.raises(PathError, match="too recently observed"):
        select_path(diverse(10, first_seen=time.time()), guards)


def test_relays_below_the_work_floor_are_excluded(tmp_path):
    guards = GuardSet(tmp_path / "guards.json")
    with pytest.raises(PathError):
        select_path(diverse(10, pow_bits=2), guards, min_pow_bits=9)


def test_candidate_from_a_relay_descriptor():
    import asyncio

    from nullcell.core.identity import Identity
    from nullcell.dht import pow as P
    from nullcell.dht import records as R

    original = dict(P.DIFFICULTY)
    P.DIFFICULTY.update(dict.fromkeys(P.DIFFICULTY, 3))
    try:
        identity = Identity.generate()
        body = R.RelayBody(
            endpoints=["10.0.0.1:4433"], x25519=identity.x25519_public, bandwidth=5_000_000
        ).as_dict()
        record = asyncio.run(R.build(identity, R.RecordType.RELAY, body))
        found = RelayCandidate.from_record(record, pow_bits=9)
        assert found is not None
        assert found.pub == identity.public_key
        assert found.bandwidth == 5_000_000
        assert found.prefixes == {"10.0.0.0/16"}

        name = asyncio.run(R.build(identity, R.RecordType.NAME, {"name": "x"}))
        assert RelayCandidate.from_record(name) is None  # wrong record type
    finally:
        P.DIFFICULTY.clear()
        P.DIFFICULTY.update(original)
        P.shutdown_pool()


# --------------------- M10: esperar y actuar son reacciones opuestas (§7)
def test_the_message_says_waiting_is_useless_when_operators_are_missing(tmp_path):
    """El mensaje culpaba siempre al suelo de antigüedad, también cuando lo
    que faltaban eran operadores: le decía a alguien que esperara en el único
    caso en que esperar no va a cambiar nada jamás."""
    paths = PathBuilder(directory_of(candidate("62.171.160.130")), GuardSet(tmp_path / "g.json"))
    message = paths.why_not_ready()
    assert "esperar no basta" in message
    assert "operador" in message
    assert "antigüedad" not in message


def test_the_message_says_to_wait_once_the_operators_are_there(tmp_path):
    relays = [candidate(f"62.{171 + i}.160.130", first_seen=time.time()) for i in range(3)]
    paths = PathBuilder(directory_of(*relays), GuardSet(tmp_path / "g.json"))
    message = paths.why_not_ready()
    assert "sólo hay que esperar" in message
    assert "esperar no basta" not in message


def test_no_relays_at_all_asks_for_a_seed(tmp_path):
    paths = PathBuilder(RelayDirectory(), GuardSet(tmp_path / "g.json"))
    assert "semilla" in paths.why_not_ready()


def test_a_ready_directory_has_nothing_to_say(tmp_path):
    relays = [candidate(f"62.{171 + i}.160.130", first_seen=OLD) for i in range(3)]
    paths = PathBuilder(directory_of(*relays), GuardSet(tmp_path / "g.json"))
    assert paths.why_not_ready() == ""


# ------------------- M10: una red nueva no tiene 3 operadores todavía (§7)
def test_a_shorter_circuit_can_be_built_when_asked_for(tmp_path):
    """La crítica que lo motivó, y es justa: exigir 3 operadores *siempre*
    significa que una red nueva no puede existir nunca, porque nada funciona
    hasta que ya hay gente y no hay gente hasta que algo funciona."""
    only_one = directory_of(candidate("62.171.160.130", first_seen=OLD))

    strict = PathBuilder(only_one, GuardSet(tmp_path / "a.json"))
    assert not strict.ready, "con 3 saltos y un relevo no hay ruta, y eso es correcto"

    relaxed = PathBuilder(only_one, GuardSet(tmp_path / "b.json"), hops=1)
    assert relaxed.ready
    assert len(relaxed.build()) == 1


def test_fewer_hops_is_reported_as_not_anonymous(tmp_path):
    directory = directory_of(candidate("62.171.160.130", first_seen=OLD))
    assert PathBuilder(directory, GuardSet(tmp_path / "a.json")).anonymous is True
    for hops in (1, 2):
        shortened = PathBuilder(directory, GuardSet(tmp_path / f"{hops}.json"), hops=hops)
        assert shortened.anonymous is False


def test_the_warning_names_what_is_actually_lost():
    """«Menos seguro» no dice nada. Lo que hay que decir es quién ve qué."""
    from nullcell.onion.path_select import anonymity_warning

    assert anonymity_warning(3) == ""
    uno, dos = anonymity_warning(1), anonymity_warning(2)
    assert "ve tu dirección IP y a qué portal vas" in uno
    assert "el primero sabe quién eres y el segundo adónde vas" in dos
    for texto in (uno, dos):
        assert "SIN ANONIMATO" in texto
        assert "El contenido sigue cifrado" in texto, "no exagerar tampoco"


def test_the_hop_count_cannot_be_zero(tmp_path):
    """Cero saltos no es un circuito, es una conexión directa con otro nombre."""
    assert PathBuilder(RelayDirectory(), GuardSet(tmp_path / "g.json"), hops=0).hops == 1


# ------------------ M10: el reloj de §7 no vuelve a cero en cada arranque
def test_what_we_observed_survives_a_restart(tmp_path):
    """Sin esto, abrir el programa, esperar, cerrarlo y volver a abrirlo
    empezaba los diez minutos otra vez: quien reinicia a menudo nunca llegaba
    a tener una ruta. §7 quería encarecer inundar la red de relevos nuevos, no
    castigar a quien apaga el ordenador."""
    archivo = tmp_path / "observed.json"
    antes = directory_of(candidate("62.171.160.130", first_seen=time.time() - 540))
    assert antes.remember(archivo) == 1

    despues = RelayDirectory()
    assert despues.recall(archivo) == 1

    # Al volver a ingerirlo, el reloj sigue donde estaba.
    nuevo = candidate("62.171.160.130")
    nuevo.pub = next(iter(antes._relays))  # noqa: SLF001 - el mismo relevo
    if (before := despues._remembered.get(nuevo.pub)) is not None:  # noqa: SLF001
        nuevo.first_seen = before
    assert 530 < nuevo.observed_for() < 560


def test_a_remembered_time_cannot_be_in_the_future(tmp_path):
    """Un `first_seen` posterior a ahora haría que un relevo pareciera más
    antiguo de lo posible, que es exactamente lo que el suelo impide."""
    import json

    archivo = tmp_path / "observed.json"
    direccion = candidate("62.171.160.130")
    from nullcell.core import ids

    archivo.write_text(json.dumps({ids.encode_nid(direccion.pub): time.time() + 86_400}))

    directory = RelayDirectory()
    directory.recall(archivo)
    recordado = directory._remembered[direccion.pub]  # noqa: SLF001
    assert recordado <= time.time() + 1


def test_a_corrupt_file_starts_clean_instead_of_failing(tmp_path):
    archivo = tmp_path / "observed.json"
    archivo.write_text("{esto no es json")
    assert RelayDirectory().recall(archivo) == 0


def test_a_missing_file_is_not_an_error(tmp_path):
    assert RelayDirectory().recall(tmp_path / "no-existe.json") == 0


def test_only_our_own_observation_is_written_not_the_descriptor(tmp_path):
    """Lo que el relevo afirma se vuelve a pedir a la red en cada arranque;
    escribirlo sería restaurar una foto de la red que fue cierta una vez."""
    import json

    archivo = tmp_path / "observed.json"
    directory_of(candidate("62.171.160.130", first_seen=OLD)).remember(archivo)
    guardado = json.loads(archivo.read_text())
    assert all(isinstance(v, (int, float)) for v in guardado.values())
    assert "62.171.160.130" not in archivo.read_text(), "el endpoint no se guarda"


# ------------------ M10: el suelo de antigüedad, ajustable y explicado
def test_the_age_floor_can_be_lowered(tmp_path):
    """En una red de tres máquinas tuyas, diez minutos no protegen de nada:
    el suelo encarece inundar la red de identidades nuevas, y nadie va a
    inundar una red en la que todos los nodos son suyos."""
    recientes = directory_of(
        *[candidate(f"62.{171 + i}.160.130", first_seen=time.time() - 30) for i in range(3)]
    )
    estricto = PathBuilder(recientes, GuardSet(tmp_path / "a.json"))
    assert not estricto.ready

    flexible = PathBuilder(recientes, GuardSet(tmp_path / "b.json"), min_age=0)
    assert flexible.ready
    assert len(flexible.build()) == 3


def test_the_default_floor_is_still_ten_minutes(tmp_path):
    """Bajarlo es una decisión que se toma, no el estado de fábrica."""
    paths = PathBuilder(RelayDirectory(), GuardSet(tmp_path / "g.json"))
    assert paths.min_age == MIN_OBSERVED_AGE == 600.0


def test_the_message_counts_with_the_floor_that_is_in_use(tmp_path):
    """Decir «10 min» cuando el suelo son 2 manda a esperar el óctuple."""
    recientes = directory_of(
        *[candidate(f"62.{171 + i}.160.130", first_seen=time.time() - 30) for i in range(3)]
    )
    paths = PathBuilder(recientes, GuardSet(tmp_path / "g.json"), min_age=120)
    assert "2 min" in paths.why_not_ready()
    assert paths.missing()["ready_in"] is not None


def test_a_negative_floor_is_clamped(tmp_path):
    assert PathBuilder(RelayDirectory(), GuardSet(tmp_path / "g.json"), min_age=-5).min_age == 0.0


# ---------------- M10: saltos automáticos — la privacidad se enciende sola
def auto_builder(tmp_path, *relays):
    return PathBuilder(directory_of(*relays), GuardSet(tmp_path / "g.json"), hops="auto", min_age=0)


def test_hops_follow_the_network_as_it_grows(tmp_path):
    """La respuesta al huevo y la gallina: exigir tres operadores siempre
    significa que una red nueva no puede existir, pero fijar un número bajo
    deja una red madura corriendo sin anonimato porque nadie lo cambió."""
    relays = [candidate(f"62.{171 + i}.160.130", first_seen=time.time() - 60) for i in range(4)]
    esperado = [1, 2, 3, 3]  # nunca más de tres
    for cuantos, saltos in zip(range(1, 5), esperado, strict=True):
        paths = auto_builder(tmp_path / f"n{cuantos}", *relays[:cuantos])
        (tmp_path / f"n{cuantos}").mkdir(exist_ok=True)
        assert paths.hops == saltos, f"con {cuantos} operadores esperaba {saltos}"
        assert paths.ready
        assert len(paths.build()) == saltos


def test_privacy_turns_on_by_itself_at_three(tmp_path):
    relays = [candidate(f"62.{171 + i}.160.130", first_seen=time.time() - 60) for i in range(3)]
    for cuantos in (1, 2):
        carpeta = tmp_path / f"a{cuantos}"
        carpeta.mkdir()
        assert auto_builder(carpeta, *relays[:cuantos]).anonymous is False
    carpeta = tmp_path / "a3"
    carpeta.mkdir()
    assert auto_builder(carpeta, *relays).anonymous is True


def test_automatic_counts_operators_not_machines(tmp_path):
    """Tres relevos del mismo proveedor son un salto, no tres: si no, «auto»
    sería una forma de creerse anónimo detrás de una sola empresa."""
    mismo = [candidate(f"62.171.160.{10 + i}", first_seen=time.time() - 60) for i in range(3)]
    paths = auto_builder(tmp_path, *mismo)
    assert paths.hops == 1
    assert paths.anonymous is False


def test_an_empty_network_is_still_not_ready(tmp_path):
    """«Auto» baja el listón, no lo quita: sin un solo relevo no hay ruta."""
    assert auto_builder(tmp_path).ready is False


def test_a_fixed_count_is_not_affected(tmp_path):
    """Quien pidió tres saltos sigue teniendo tres o ninguno."""
    uno = [candidate("62.171.160.130", first_seen=time.time() - 60)]
    fijo = PathBuilder(directory_of(*uno), GuardSet(tmp_path / "g.json"), min_age=0)
    assert fijo.auto is False
    assert fijo.hops == 3
    assert fijo.ready is False


def test_the_automatic_warning_says_it_will_fix_itself():
    """Y el fijo no lo dice, porque no es verdad: no se arregla solo."""
    from nullcell.onion.path_select import anonymity_warning

    assert "se activará sola" in anonymity_warning(1, auto=True).lower()
    assert "se activará sola" not in anonymity_warning(1).lower()
    assert anonymity_warning(3, auto=True) == ""


# --------- M10: un guard que desaparece no puede bloquear la red entera
def test_a_vanished_guard_does_not_block_every_circuit(tmp_path):
    """§7 fija los guards para **no rotarlos constantemente**, que es lo que
    delata a la larga — no para quedarse atascado cuando desaparecen.

    Visto en la red real: un guard fijado dejó de publicar descriptor y el
    navegador contestaba «no usable pinned guard» a todo, incluso con otros
    relevos vivos. Un guard inalcanzable no protege nada y además impedía
    construir cualquier circuito.
    """
    import json

    from nullcell.core import ids

    # El caso atascado de verdad: los GUARD_COUNT sitios ocupados por celdas
    # que ya no están. Con uno solo libre, `ensure` todavía puede añadir otro
    # y el bloqueo no llega a ocurrir.
    ahora = time.time()
    fantasmas = {ids.encode_nid(bytes([200 + i]) * 32): ahora for i in range(GUARD_COUNT)}
    archivo = tmp_path / "g.json"
    archivo.write_text(json.dumps(fantasmas))

    vivo = candidate("62.171.160.130", first_seen=OLD)
    paths = PathBuilder(directory_of(vivo), GuardSet(archivo), hops="auto", min_age=0)

    assert len(paths.build()) == 1, "un guard ausente bloqueaba todos los circuitos"
    quedan = {ids.encode_nid(p) for p in GuardSet(archivo).pubs}
    assert not (quedan & set(fantasmas)), "los ausentes siguen fijados"


def test_a_live_guard_is_kept_even_if_another_is_down(tmp_path):
    """Sólo se retiran cuando la alternativa es no poder hacer nada: mientras
    quede uno vivo, los demás se conservan aunque estén caídos."""
    import json

    from nullcell.core import ids

    relays = [candidate(f"62.{171 + i}.160.130", first_seen=OLD) for i in range(3)]
    archivo = tmp_path / "g.json"
    ahora = time.time()
    archivo.write_text(
        json.dumps({ids.encode_nid(relays[0].pub): ahora, ids.encode_nid(b"\xee" * 32): ahora})
    )

    paths = PathBuilder(directory_of(*relays), GuardSet(archivo), min_age=0)
    assert len(paths.build()) == 3
    # El caído sigue fijado: no hizo falta retirarlo.
    assert len(GuardSet(archivo).pubs) >= 2


def test_with_nothing_usable_the_message_is_still_the_honest_one(tmp_path):
    """Retirar guards no puede convertirse en inventar una ruta."""
    paths = PathBuilder(RelayDirectory(), GuardSet(tmp_path / "g.json"), min_age=0)
    with pytest.raises(PathError):
        paths.build()
