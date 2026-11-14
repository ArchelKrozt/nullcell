"""aioquic._buffer en Python puro, para Android.

`aioquic` sólo trae una pieza nativa: este lector/escritor de bytes
(`_buffer.c`). No hay rueda de aioquic para Android, y compilarla exige el NDK;
esta versión replica el C **byte a byte** —mismos límites, mismos errores, y
el mismo recorte silencioso de enteros que hacen los formatos `B/H/I/K` de
`PyArg_ParseTuple`— para que el resto de aioquic no note la diferencia.

Se valida corriendo la suite entera de Nullcell con este módulo en lugar del
nativo (ver android/LEEME.md).
"""

from __future__ import annotations

__all__ = ["Buffer", "BufferReadError", "BufferWriteError"]


class BufferReadError(ValueError):
    pass


class BufferWriteError(ValueError):
    pass


class Buffer:
    __slots__ = ("_data", "_pos", "_end")

    def __init__(self, capacity: int | None = 0, data: bytes | None = None) -> None:
        if data is not None:
            self._data = bytearray(data)
        else:
            capacity = 0 if capacity is None else capacity
            if capacity < 0:
                raise ValueError("capacity cannot be negative")
            self._data = bytearray(capacity)
        self._end = len(self._data)
        self._pos = 0

    # ------------------------------------------------------------ propiedades
    @property
    def capacity(self) -> int:
        return self._end

    @property
    def data(self) -> bytes:
        return bytes(self._data[: self._pos])

    def data_slice(self, start: int, end: int) -> bytes:
        if start < 0 or start > self._end or end < 0 or end > self._end or end < start:
            raise BufferReadError("Read out of bounds")
        return bytes(self._data[start:end])

    def eof(self) -> bool:
        return self._pos == self._end

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > self._end:
            raise BufferReadError("Seek out of bounds")
        self._pos = pos

    def tell(self) -> int:
        return self._pos

    # ------------------------------------------------------------------ leer
    def _take(self, length: int) -> bytearray:
        if length < 0 or self._pos + length > self._end:
            raise BufferReadError("Read out of bounds")
        start = self._pos
        self._pos += length
        return self._data[start : self._pos]

    def pull_bytes(self, length: int) -> bytes:
        return bytes(self._take(length))

    def pull_uint8(self) -> int:
        return self._take(1)[0]

    def pull_uint16(self) -> int:
        return int.from_bytes(self._take(2), "big")

    def pull_uint32(self) -> int:
        return int.from_bytes(self._take(4), "big")

    def pull_uint64(self) -> int:
        return int.from_bytes(self._take(8), "big")

    def pull_uint_var(self) -> int:
        if self._pos + 1 > self._end:
            raise BufferReadError("Read out of bounds")
        kind = self._data[self._pos] >> 6
        length = 1 << kind  # 1, 2, 4 u 8 bytes
        raw = self._take(length)
        return int.from_bytes(raw, "big") & ((1 << (8 * length - 2)) - 1)

    # --------------------------------------------------------------- escribir
    def _put(self, raw: bytes | bytearray) -> None:
        length = len(raw)
        if self._pos + length > self._end:
            raise BufferWriteError("Write out of bounds")
        self._data[self._pos : self._pos + length] = raw
        self._pos += length

    def push_bytes(self, value: bytes) -> None:
        self._put(value)

    # Los formatos B/H/I/K del C no comprueban desbordamiento: recortan.
    def push_uint8(self, value: int) -> None:
        self._put((value & 0xFF).to_bytes(1, "big"))

    def push_uint16(self, value: int) -> None:
        self._put((value & 0xFFFF).to_bytes(2, "big"))

    def push_uint32(self, v: int) -> None:
        self._put((v & 0xFFFFFFFF).to_bytes(4, "big"))

    def push_uint64(self, v: int) -> None:
        self._put((v & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big"))

    def push_uint_var(self, value: int) -> None:
        value &= 0xFFFFFFFFFFFFFFFF
        if value <= 0x3F:
            self._put(value.to_bytes(1, "big"))
        elif value <= 0x3FFF:
            self._put((value | 0x4000).to_bytes(2, "big"))
        elif value <= 0x3FFFFFFF:
            self._put((value | 0x80000000).to_bytes(4, "big"))
        elif value <= 0x3FFFFFFFFFFFFFFF:
            self._put((value | 0xC000000000000000).to_bytes(8, "big"))
        else:
            raise ValueError("Integer is too big for a variable-length integer")
