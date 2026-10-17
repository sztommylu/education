"""Numbered vocabulary input; legacy unnumbered assignments remain readable."""
import re


ORDER_PREFIX = re.compile(r'^\s*(?:[1-9][0-9]*\s*[.．、)）]|[(（]\s*[1-9][0-9]*\s*[)）])\s*')


def strip_order(line):
    return ORDER_PREFIX.sub('', line, count=1).strip()


def hash_vocabulary_parts(line):
    parts = [part.strip() for part in strip_order(line).split('##')]
    return parts[:2] if len(parts) == 3 and not parts[2] and all(parts[:2]) else []


def vocabulary_parts(line):
    parts = hash_vocabulary_parts(line)
    return parts or [part.strip() for part in re.split(r'[:：]', strip_order(line), maxsplit=1)]


def normalized_vocabulary(value):
    return [vocabulary_parts(line) for line in value.splitlines() if line.strip()]
