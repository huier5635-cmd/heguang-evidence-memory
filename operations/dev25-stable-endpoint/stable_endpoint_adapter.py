"""Keep reviewed URLs stable during pre-Full diagnosis, preserve old backend."""
import hashlib

ORIGINAL_CODING=b'''handle_path /coding/* {
    @coding_api path /health /add /search
    handle @coding_api {
        request_body {
            max_size 67108864
        }
        reverse_proxy 127.0.0.1:8752 {
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

def adapt(current,expected_sha256):
    if hashlib.sha256(current).hexdigest()!=expected_sha256:
        raise ValueError('Current Caddy configuration differs')
    if (current.count(ORIGINAL_CODING)!=1 or b'/coding/dev6/' in current
            or current.count(b'handle_path /coding/dev25/* {')!=1):
        raise ValueError('Unexpected current diagnostic routing')
    legacy=ORIGINAL_CODING.replace(b'/coding/*',b'/coding/dev6/*').replace(b'@coding_api',b'@dev6_legacy_api')
    main=ORIGINAL_CODING.replace(b'max_size 67108864',b'max_size 134217728').replace(b'127.0.0.1:8752',b'127.0.0.1:8753')
    new=current.replace(ORIGINAL_CODING,legacy+b'\n'+main,1)
    if new.replace(legacy+b'\n'+main,ORIGINAL_CODING,1)!=current:
        raise ValueError('Unrelated routing changed')
    return new
