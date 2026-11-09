"""Where this cell is reachable from — §7, §10.

The bug these pin: the desktop announced `127.0.0.1` when nobody passed
`--announce`, and the panel happily offered the "share bandwidth" switch. Using
it would have published a relay descriptor pointing at the operator's own
loopback — an unreachable entry in *everyone else's* directory — and the
"copy my seed" button handed out a line nobody could ever use.
"""

from __future__ import annotations

import pytest

from nullcell.core import reach


@pytest.mark.parametrize("endpoint", ["127.0.0.1:4433", "::1", "[::1]:4433", "127.0.0.1"])
def test_loopback_reaches_nobody(endpoint):
    assert reach.classify(endpoint) == "loopback"


@pytest.mark.parametrize("endpoint", ["192.168.1.20:4433", "10.8.0.1:4433", "100.64.0.1:4433"])
def test_a_private_address_reaches_the_lan(endpoint):
    """Useful — a real network with the machines in one building — but it is
    not the internet, and saying so is the difference between a working seed
    and a confusing one."""
    assert reach.classify(endpoint) == "lan"


@pytest.mark.parametrize(
    "endpoint",
    ["8.8.8.8:4433", "185.199.108.153:4433", "[2606:4700::1111]:4433", "relay.midominio.com:4433"],
)
def test_a_routable_address_or_a_name_reaches_the_internet(endpoint):
    assert reach.classify(endpoint) == "internet"


def test_a_bare_ipv6_address_is_not_mangled_by_the_port_split():
    """Splitting on the last colon turns `2606:4700::1111` into `2606:4700:`,
    and the address stops being an address."""
    assert reach.classify("2606:4700::1111") == "internet"


def test_every_class_has_something_to_say():
    for name in ("loopback", "lan", "internet", "unknown"):
        assert len(reach.describe(name)) > 30


def test_the_lan_address_is_found_without_any_network_request():
    """Asking a "what is my IP" service would tell that service there is a node
    here, which is exactly what §9 refuses to do for a web font. Connecting a
    UDP socket sends nothing; it only asks the kernel which source address it
    would pick."""
    found = reach.lan_address()
    assert found is None or reach.classify(found) != "loopback"


def test_the_announced_endpoint_is_preferred_over_detection():
    endpoint, how = reach.best_endpoint(4433, announce="mivps.com:4433")
    assert endpoint == "mivps.com:4433"
    assert how == "internet"


def test_without_an_announce_it_picks_the_best_it_can_prove():
    endpoint, how = reach.best_endpoint(4433)
    assert endpoint.endswith(":4433")
    # Loopback is an honest fallback, never an invented address.
    assert how in {"lan", "loopback"}
