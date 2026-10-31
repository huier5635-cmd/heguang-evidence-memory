"""Add one restricted diagnostic route while preserving all original bytes."""
SITE=b'heguang-8-208-30-48.sslip.io {'
ANCHOR=b'handle_path /coding/* {'
BLOCK=b'''handle_path /coding/dev25/* {
    @dev25_api path /health /add /search
    handle @dev25_api {
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

def add_route(original):
    if (original.count(SITE)!=1 or original.count(ANCHOR)!=1
            or b'/coding/dev25/' in original or b'\r' in original):
        raise ValueError('Existing routing differs from the reviewed configuration')
    changed=original.replace(ANCHOR,BLOCK+ANCHOR,1)
    if changed.replace(BLOCK,b'',1)!=original:
        raise ValueError('Existing routes were changed')
    return changed
