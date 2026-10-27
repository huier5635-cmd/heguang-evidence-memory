"""Bounded, lossless storage for derived path/symbol lists; never changes indexed tokens."""
import json
import zlib

MAX_JSON_BYTES = 1024 * 1024
MAGIC = b"HCF1"


def encode_features(values):
    text = json.dumps(sorted(values))
    raw = text.encode("ascii")
    if len(raw) > MAX_JSON_BYTES:
        raise RuntimeError("Derived feature metadata exceeds storage bound")
    if len(raw) < 96:
        return text
    framed = MAGIC + len(raw).to_bytes(4, "big") + zlib.compress(raw, 3)
    return framed if len(framed) + 8 < len(raw) else text


def decode_features(value):
    try:
        if isinstance(value, bytes):
            if not 8 <= len(value) <= MAX_JSON_BYTES or value[:4] != MAGIC:
                raise ValueError
            expected = int.from_bytes(value[4:8], "big")
            if not 2 <= expected <= MAX_JSON_BYTES:
                raise ValueError
            decoder = zlib.decompressobj()
            raw = decoder.decompress(value[8:], expected + 1)
            if len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError
            text = raw.decode("ascii")
        elif isinstance(value, str) and len(value) <= MAX_JSON_BYTES and value.isascii():
            text = value
        else:
            raise ValueError
        result = json.loads(text)
        if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
            raise ValueError
        return result
    except (ValueError, UnicodeError, zlib.error, OverflowError):
        raise RuntimeError("Corrupt derived feature metadata") from None
