"""SENDME flow control — §5."""

import asyncio

import pytest

from nullcell.onion.flow import (
    CIRCUIT_INCREMENT,
    CIRCUIT_WINDOW,
    STREAM_INCREMENT,
    STREAM_WINDOW,
    DeliveryCounter,
    FlowControl,
    FlowError,
    SendWindow,
)


def test_windows_match_the_architecture():
    assert (CIRCUIT_WINDOW, CIRCUIT_INCREMENT) == (1000, 100)
    assert (STREAM_WINDOW, STREAM_INCREMENT) == (500, 50)


def test_window_spends_down_to_zero_then_blocks():
    window = SendWindow(3, 1)
    assert all(window.try_consume() for _ in range(3))
    assert window.available == 0
    assert window.blocked
    assert not window.try_consume()


def test_credit_reopens_the_window():
    window = SendWindow(3, 2)
    for _ in range(3):
        window.try_consume()
    window.credit()
    assert window.available == 2
    assert not window.blocked
    assert window.try_consume()


def test_credit_beyond_the_initial_window_is_refused():
    """A peer that over-credits is either broken or trying to make us flood."""
    window = SendWindow(10, 5)
    window.try_consume()
    with pytest.raises(FlowError, match="above the initial"):
        window.credit(9)
    assert window.available == 9  # unchanged


async def test_consume_waits_for_credit():
    window = SendWindow(1, 1)
    await window.consume()
    assert window.blocked

    waiter = asyncio.create_task(window.consume())
    await asyncio.sleep(0.01)
    assert not waiter.done(), "should still be waiting for a SENDME"

    window.credit()
    await asyncio.wait_for(waiter, 1.0)
    assert window.available == 0


def test_delivery_counter_fires_every_increment():
    counter = DeliveryCounter(3)
    assert [counter.record() for _ in range(7)] == [False, False, True, False, False, True, False]
    assert counter.pending == 1


async def test_flow_control_meters_circuit_and_stream():
    flow = FlowControl()
    await flow.acquire(stream_id=7)
    assert flow.circuit_send.available == CIRCUIT_WINDOW - 1
    assert flow.stream_send(7).available == STREAM_WINDOW - 1
    assert flow.streams == 1


async def test_stream_zero_is_not_metered():
    """Control traffic rides circuit credit only; there is no stream 0."""
    flow = FlowControl()
    await flow.acquire(stream_id=0)
    assert flow.streams == 0


def test_on_delivered_reports_both_levels():
    flow = FlowControl()
    for _ in range(STREAM_INCREMENT - 1):
        assert flow.on_delivered(3) == (False, False)
    assert flow.on_delivered(3) == (False, True)  # stream window reached first


def test_forgetting_a_stream_releases_its_windows():
    flow = FlowControl()
    flow.stream_send(5)
    assert flow.streams == 1
    flow.forget(5)
    assert flow.streams == 0
