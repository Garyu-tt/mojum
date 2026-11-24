"""Check JSON container depth independently of the decoder."""

MAX_JSON_DEPTH = 256


def exceeds_json_depth(text: str, limit: int = MAX_JSON_DEPTH) -> bool:
    """Count containers outside quoted strings before decoding bounded text."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > limit:
                return True
        elif char in "]}" and depth:
            depth -= 1
    return False
