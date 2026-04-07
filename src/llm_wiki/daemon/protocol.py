"""Length-prefixed JSON protocol for daemon IPC.

Wire format: [4 bytes: big-endian uint32 payload length][N bytes: JSON payload]
"""
from __future__ import annotations

import asyncio
import json
import socket
import struct

HEADER_SIZE = 4


def encode_message(msg: dict) -> bytes:
    """Encode a dict as a length-prefixed JSON message."""
    payload = json.dumps(msg).encode("utf-8")
    return struct.pack("!I", len(payload)) + payload


def decode_message(data: bytes) -> dict:
    """Decode a length-prefixed JSON message."""
    length = struct.unpack("!I", data[:HEADER_SIZE])[0]
    payload = data[HEADER_SIZE : HEADER_SIZE + length]
    return json.loads(payload)


async def read_message(reader: asyncio.StreamReader) -> dict:
    """Read one message from an async stream."""
    header = await reader.readexactly(HEADER_SIZE)
    length = struct.unpack("!I", header)[0]
    payload = await reader.readexactly(length)
    return json.loads(payload)


async def write_message(writer: asyncio.StreamWriter, msg: dict) -> None:
    """Write one message to an async stream."""
    writer.write(encode_message(msg))
    await writer.drain()


def read_message_sync(sock: socket.socket) -> dict:
    """Read one message from a blocking socket."""
    header = _recv_exact(sock, HEADER_SIZE)
    length = struct.unpack("!I", header)[0]
    payload = _recv_exact(sock, length)
    return json.loads(payload)


def write_message_sync(sock: socket.socket, msg: dict) -> None:
    """Write one message to a blocking socket."""
    sock.sendall(encode_message(msg))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from a blocking socket."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed while reading")
        buf.extend(chunk)
    return bytes(buf)
