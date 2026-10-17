"""Extract textbook translation pairs while keeping historical tasks readable."""
from .vocabulary import ORDER_PREFIX, hash_vocabulary_parts, strip_order


def _legacy_source_questions(text):
    rows, current, preamble = [], None, []
    for line in text.splitlines():
        if ORDER_PREFIX.match(line):
            if current is not None:
                rows.append('\n'.join(current).strip())
            current = preamble + [strip_order(line)]
            preamble = []
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)
    if current is not None:
        rows.append('\n'.join(current).strip())
    return rows or ([text.strip()] if text.strip() else [])


def translation_pairs(text):
    lines = [line for line in text.splitlines() if line.strip()]
    pairs = [hash_vocabulary_parts(line) for line in lines]
    if lines and all(pairs):
        return [{'expected':english, 'prompt':chinese} for english, chinese in pairs]
    return [{'expected':'', 'prompt':source} for source in _legacy_source_questions(text)]


def source_questions(text):
    return [pair['prompt'] for pair in translation_pairs(text)]
