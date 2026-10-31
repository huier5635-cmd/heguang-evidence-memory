"""Add a fixed query-candidate prefix, preserving every existing route byte."""
import hashlib
MAIN=b'handle_path /coding/* {\n    @coding_api path /health /add /search\n    handle @coding_api {\n        request_body {\n            max_size 134217728\n        }\n        reverse_proxy 127.0.0.1:8755 {\n            transport http {\n                response_header_timeout 30m\n            }\n        }\n    }\n    handle {\n        respond "Not found" 404\n    }\n}\n'
QUERY=MAIN.replace(b'/coding/*',b'/coding/query/*').replace(b'@coding_api',b'@query_api').replace(b'127.0.0.1:8755',b'127.0.0.1:8757')
def adapt(current,expected_sha256):
    if hashlib.sha256(current).hexdigest()!=expected_sha256:
        raise ValueError('Current Caddy configuration differs')
    if (current.count(MAIN)!=1 or current.count(b'handle_path /coding/context/* {')!=1
            or current.count(b'handle_path /coding/dev6/* {')!=1 or current.count(b'handle_path /coding/dev25/* {')!=1
            or current.count(b'127.0.0.1:8755')!=2 or b'/coding/query/' in current
            or b'127.0.0.1:8757' in current or b'\r' in current):
        raise ValueError('Unexpected current version routing')
    updated=current.replace(MAIN,QUERY+b'\n'+MAIN,1)
    if updated.replace(QUERY+b'\n'+MAIN,MAIN,1)!=current:
        raise ValueError('Unrelated routing changed')
    return updated
