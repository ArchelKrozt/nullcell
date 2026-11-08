"""Frames — §5."""

import pytest

from nullcell.core import frames as f


def test_sizes_match_the_architecture():
    assert f.FRAME_SIZE == 512
    assert f.HEADER_SIZE == 5
    assert f.PAYLOAD_SIZE == 507
    assert f.RELAY_HEADER_SIZE == 11
    assert f.RELAY_DATA_SIZE == 496


def test_every_frame_is_exactly_512_bytes():
    for payload in (b"", b"x", b"y" * 100, b"z" * f.PAYLOAD_SIZE):
        assert len(f.pack(1, f.Cmd.PADDING, payload)) == f.FRAME_SIZE


def test_header_roundtrip():
    frame = f.pack(0xDEADBEEF, f.Cmd.RELAY, b"hola")
    assert f.parse_header(frame) == (0xDEADBEEF, int(f.Cmd.RELAY))


def test_oversized_payload_rejected():
    with pytest.raises(f.FrameError):
        f.pack(1, f.Cmd.RELAY, b"x" * (f.PAYLOAD_SIZE + 1))


def test_truncated_frame_rejected():
    with pytest.raises(f.FrameError):
        f.parse_header(b"abc")


def test_extend_is_a_relay_command_not_a_link_command():
    """§5: EXTEND must be end-to-end, or hop 1 chooses your path for you."""
    assert not hasattr(f.Cmd, "EXTEND")
    assert f.RelayCmd.EXTEND


def test_circ_id_top_bit_follows_the_initiator_convention():
    for _ in range(200):
        assert not f.new_circ_id(link_initiator=True) & f.CIRC_ID_RESPONDER_BIT
        assert f.new_circ_id(link_initiator=False) & f.CIRC_ID_RESPONDER_BIT


def test_circ_id_is_never_zero():
    assert all(f.new_circ_id(link_initiator=True) != 0 for _ in range(200))


def test_relay_subheader_roundtrip():
    payload = f.pack_relay_payload(f.RelayCmd.DATA, b"cuerpo", stream_id=0xBEEF, digest=0x11223344)
    assert len(payload) == f.PAYLOAD_SIZE
    h = f.parse_relay_payload(payload)
    assert h.rcmd == f.RelayCmd.DATA
    assert h.stream_id == 0xBEEF
    assert h.digest == 0x11223344
    assert h.length == 6
    assert h.payload == b"cuerpo"


def test_recognized_zero_means_for_me():
    mine = f.parse_relay_payload(f.pack_relay_payload(f.RelayCmd.DATA, b"x"))
    theirs = f.parse_relay_payload(f.pack_relay_payload(f.RelayCmd.DATA, b"x", recognized=1))
    assert mine.is_for_me
    assert not theirs.is_for_me


def test_relay_data_cannot_exceed_496():
    f.pack_relay_payload(f.RelayCmd.DATA, b"x" * f.RELAY_DATA_SIZE)
    with pytest.raises(f.FrameError):
        f.pack_relay_payload(f.RelayCmd.DATA, b"x" * (f.RELAY_DATA_SIZE + 1))


def test_lying_length_field_rejected():
    payload = bytearray(f.pack_relay_payload(f.RelayCmd.DATA, b"x"))
    payload[9:11] = (f.RELAY_DATA_SIZE + 1).to_bytes(2, "big")
    with pytest.raises(f.FrameError):
        f.parse_relay_payload(bytes(payload))


def test_padding_never_leaks_a_previous_frame():
    """A reused FrameBuffer slot must not expose the old occupant's bytes."""
    buf = f.FrameBuffer(1)
    buf.add(1, f.Cmd.PADDING, b"SECRETO" * 20)
    buf.reset()
    buf.add(2, f.Cmd.PADDING, b"ok")
    frame = bytes(buf.view())
    assert b"SECRETO" not in frame
    assert frame[f.HEADER_SIZE : f.HEADER_SIZE + 2] == b"ok"
    assert frame[f.HEADER_SIZE + 2 :] == bytes(f.PAYLOAD_SIZE - 2)


def test_frame_buffer_batches_into_one_contiguous_write():
    buf = f.FrameBuffer(8)
    for i in range(8):
        buf.add(i, f.Cmd.PADDING, bytes([i]))
    assert len(buf) == 8
    assert buf.full
    view = buf.view()
    assert len(view) == 8 * f.FRAME_SIZE
    for i, frame in enumerate(f.iter_frames(view)):
        assert f.parse_header(frame) == (i, int(f.Cmd.PADDING))


def test_frame_buffer_refuses_to_overflow():
    buf = f.FrameBuffer(1)
    buf.add(1, f.Cmd.PADDING)
    with pytest.raises(f.FrameError):
        buf.add(2, f.Cmd.PADDING)


def test_iter_frames_ignores_a_partial_tail():
    blob = f.pack(1, f.Cmd.PADDING) + f.pack(2, f.Cmd.PADDING) + b"parcial"
    assert len(list(f.iter_frames(blob))) == 2
