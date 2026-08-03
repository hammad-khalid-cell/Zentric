"""The offline guard itself.

`conftest.no_network` is what converts "the suite claims to be offline" into "the suite
is offline". It permits loopback (FastAPI's TestClient needs a local socketpair for its
event loop) while blocking everything remote — so it needs its own tests, or a future
tweak could quietly turn it into a no-op and the flaky-network class of bug would
return unnoticed.
"""
import socket

import pytest


def test_remote_dns_is_blocked():
    """The exact call that used to fail intermittently against Supabase/Chroma."""
    with pytest.raises(RuntimeError, match="offline by design"):
        socket.getaddrinfo("aws-1-ap-northeast-2.pooler.supabase.com", 5432)


def test_remote_connect_is_blocked():
    with pytest.raises(RuntimeError, match="offline by design"):
        socket.create_connection(("api.groq.com", 443))


def test_the_error_names_the_host_it_blocked():
    """So the failure points at the unmocked boundary instead of being a mystery."""
    with pytest.raises(RuntimeError, match="api.smith.langchain.com"):
        socket.getaddrinfo("api.smith.langchain.com", 443)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_is_permitted(host):
    """Not a network call in any meaningful sense, and TestClient depends on it."""
    socket.getaddrinfo(host, 0)   # must not raise


def test_socket_connect_to_a_remote_address_is_blocked():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="offline by design"):
            sock.connect(("1.1.1.1", 443))
    finally:
        sock.close()


@pytest.mark.allow_network
def test_the_opt_out_marker_restores_real_networking():
    """A test whose point IS the network can opt out. Asserted without actually going
    anywhere: the guard is simply not installed, so the real function is in place."""
    assert socket.getaddrinfo.__module__ != "tests.conftest"
