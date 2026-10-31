"""Canonical JSON hashing/audit compression without a message-sized UTF-8 copy."""
import hashlib
import json
import zlib


def canonical_bytes(value):
    active = set()

    def string(text):
        yield b'"'
        for offset in range(0, len(text), 8192):
            yield json.dumps(text[offset:offset+8192], ensure_ascii=False)[1:-1].encode('utf-8')
        yield b'"'

    def visit(item):
        if isinstance(item, str):
            yield from string(item)
        elif isinstance(item, (dict, list, tuple)):
            identity = id(item)
            if identity in active:
                raise ValueError('Circular reference detected')
            active.add(identity)
            try:
                if isinstance(item, dict):
                    yield b'{'
                    for index, (key, child) in enumerate(sorted(item.items())):
                        if index:
                            yield b','
                        if not isinstance(key, str):
                            if key is None or isinstance(key, (bool, int, float)):
                                key = json.dumps(key)
                            else:
                                raise TypeError('Unsupported JSON key')
                        yield from string(key)
                        yield b':'
                        yield from visit(child)
                    yield b'}'
                else:
                    yield b'['
                    for index, child in enumerate(item):
                        if index:
                            yield b','
                        yield from visit(child)
                    yield b']'
            finally:
                active.remove(identity)
        else:
            yield json.dumps(item, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    yield from visit(value)


def canonical_digest(value):
    checksum = hashlib.sha256()
    for part in canonical_bytes(value):
        checksum.update(part)
    return checksum.hexdigest()


def compressed_canonical_json(value):
    checksum, compressor, output = hashlib.sha256(), zlib.compressobj(3), []
    for part in canonical_bytes(value):
        checksum.update(part)
        compressed = compressor.compress(part)
        if compressed:
            output.append(compressed)
    output.append(compressor.flush())
    return b''.join(output), checksum.hexdigest()
