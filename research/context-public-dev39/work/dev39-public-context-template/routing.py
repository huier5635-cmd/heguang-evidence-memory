"""Preview a context-profile route; preserve every unrelated configuration byte."""
import hashlib

MAIN=b'''handle_path /coding/* {
    @coding_api path /health /add /search
    handle @coding_api {
        request_body {
            max_size 134217728
        }
        reverse_proxy 127.0.0.1:8753 {
            transport http {
                response_header_timeout 30m
            }
        }
    }
    handle {
        respond "Not found" 404
    }
}
'''
CONTEXT=MAIN.replace(b'/coding/*',b'/coding/context/*').replace(b'@coding_api',b'@context_api').replace(b'127.0.0.1:8753',b'127.0.0.1:8755')
UPDATED_MAIN=MAIN.replace(b'127.0.0.1:8753',b'127.0.0.1:8755')

def adapt(current,expected_sha256):
    if hashlib.sha256(current).hexdigest()!=expected_sha256:
        raise ValueError('Current Caddy configuration differs')
    if (current.count(MAIN)!=1 or current.count(b'handle_path /coding/dev6/* {')!=1
            or current.count(b'handle_path /coding/dev25/* {')!=1
            or b'/coding/context/' in current or b'127.0.0.1:8755' in current or b'\r' in current):
        raise ValueError('Unexpected current version routing')
    updated=current.replace(MAIN,CONTEXT+b'\n'+UPDATED_MAIN,1)
    if updated.replace(CONTEXT+b'\n'+UPDATED_MAIN,MAIN,1)!=current:
        raise ValueError('Unrelated routing changed')
    return updated
