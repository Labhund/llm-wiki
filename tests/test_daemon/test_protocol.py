import json
import struct
from llm_wiki.daemon.protocol import encode_message, decode_message


def test_encode_decode_roundtrip():
    msg = {"type": "search", "query": "sRNA", "limit": 10}
    encoded = encode_message(msg)
    decoded = decode_message(encoded)
    assert decoded == msg


def test_encode_empty_dict():
    encoded = encode_message({})
    decoded = decode_message(encoded)
    assert decoded == {}


def test_encode_nested():
    msg = {"type": "status", "data": {"pages": 42, "clusters": ["bio", "ml"]}}
    encoded = encode_message(msg)
    decoded = decode_message(encoded)
    assert decoded == msg


def test_message_framing():
    """Verify length prefix is correct."""
    msg = {"hello": "world"}
    encoded = encode_message(msg)
    length = struct.unpack("!I", encoded[:4])[0]
    assert length == len(encoded) - 4
    assert encoded[4:] == json.dumps(msg).encode("utf-8")
