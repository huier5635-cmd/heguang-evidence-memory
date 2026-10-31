"""Recognize an explicitly truncated observation; the raw audit stays complete."""
RAW_THRESHOLD = 1024 * 1024


def certified_visible_truncation(content, raw):
    if (not isinstance(content, str) or not isinstance(raw, str)
            or not 0 < len(content) <= 65536 or len(raw) <= RAW_THRESHOLD):
        return False
    markers = ['<warning>', '</warning>', '<output_head>', '</output_head>',
               '<elided_chars>', '</elided_chars>', '<output_tail>', '</output_tail>']
    if any(content.count(marker) != 1 for marker in markers):
        return False
    positions = [content.index(marker) for marker in markers]
    if positions != sorted(positions):
        return False
    warning = content.split('<warning>', 1)[1].split('</warning>', 1)[0]
    if 'The output of your last command was too long.' not in warning:
        return False
    head = content.split('<output_head>', 1)[1].split('</output_head>', 1)[0].strip('\r\n')
    tail = content.split('<output_tail>', 1)[1].split('</output_tail>', 1)[0].strip('\r\n')
    elided = content.split('<elided_chars>', 1)[1].split('</elided_chars>', 1)[0].strip()
    # Public tool renderers emit either a bare count or this exact suffix.
    # Accept no free-form prose or fuzzy number extraction.
    suffix = ' characters elided'
    if elided.endswith(suffix):
        elided = elided[:-len(suffix)]
    if not elided.isascii() or not elided.isdecimal() or not 0 < len(elided) <= 12:
        return False
    omitted = int(elided)
    if (not head or not tail or omitted <= 0 or omitted > len(raw)
            or abs(len(raw)-len(head)-len(tail)-omitted) > 16):
        return False
    end = len(raw)
    # Wrapper rendering may trim a few framing line breaks. Never strip/copy
    # the entire large raw string just to certify its suffix.
    for _ in range(8):
        if end and raw[end-1] in '\r\n':
            end -= 1
        else:
            break
    return (len(head)+len(tail) <= len(raw) and raw.startswith(head)
            and raw.endswith(tail, 0, end))
