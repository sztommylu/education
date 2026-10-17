from decimal import Decimal
from django import template
from workshop.presentation import vocabulary_sections as build_vocabulary_sections
register=template.Library()
@register.filter
def money(value): return f'{Decimal(value or 0)/100:.2f}'
@register.filter
def sign(value): return f'{value:+d}'
@register.filter
def phase_class(value): return {'draft':'neutral','waiting':'amber','revision':'rose','passed':'amber','awarded':'green','rejected':'neutral'}.get(value,'neutral')

@register.filter
def task_status(task, viewer):
    if task.phase in ('revision', 'rejected'):
        return '待修改' if viewer.is_parent else '被驳回'
    if task.phase == 'passed':
        return '等待审核'
    return task.get_phase_display()

@register.filter
def review_result(grade):
    return '通过' if grade in ('A', 'B', 'C') else '未通过'

@register.filter
def vocabulary_sections(snapshot):
    return build_vocabulary_sections(snapshot)

@register.filter
def handwriting_preview(value):
    if not isinstance(value, dict):
        return []
    return [{'prompt': answer['prompt'], 'recognized': answer['recognized'],
             'confidence': answer['confidence'], 'strokes': row['strokes']}
            for answer, row in zip(value.get('answers', []), value.get('rows', []))]
