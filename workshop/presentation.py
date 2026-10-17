"""Read-only display transformations; submitted source material stays unchanged."""
from .vocabulary import vocabulary_parts


def vocabulary_sections(snapshot):
    if not isinstance(snapshot, dict):
        return []
    assignment = snapshot.get('assignment')
    if not isinstance(assignment, dict) or assignment.get('category') != 'vocabulary':
        return []
    content = assignment.get('content', {})
    if not isinstance(content, dict):
        content = {}
    sections = []
    for key, label in [('words', '单词'), ('phrases', '词组'), ('sentences', '句子')]:
        value = content.get(key, '')
        lines = value.splitlines() if isinstance(value, str) else []
        items = []
        for line in lines:
            parts = vocabulary_parts(line)
            if len(parts) == 2 and parts[1].strip():
                items.append(parts[1].strip())
        sections.append({'label':label, 'items':items})
    return sections
