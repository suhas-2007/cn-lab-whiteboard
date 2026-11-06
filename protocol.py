"""Length-prefixed UTF-8 JSON messages over TCP."""
import json
import struct

MAX_FRAME = 8 * 1024 * 1024


def exact(sock, count):
    data = bytearray()
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise EOFError("Connection closed")
        data.extend(part)
    return bytes(data)


def pack(message):
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ValueError("Frame too large")
    return struct.pack("!I", len(data)) + data


def receive(sock):
    size = struct.unpack("!I", exact(sock, 4))[0]
    if not 1 <= size <= MAX_FRAME:
        raise ValueError("Invalid frame length")
    data = exact(sock, size)
    message = json.loads(data)
    if not isinstance(message, dict):
        raise ValueError("Expected an object")
    return message
