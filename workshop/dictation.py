"""Chinese prompts, bounded pen strokes and server-side OCR for vocabulary tasks."""
import io
import json
import math
import re
from django.core.exceptions import ValidationError
from .vocabulary import vocabulary_parts

MAX_QUESTIONS = 100


def questions(task):
    assignment = task.template_snapshot.get('assignment', {})
    if task.subject.name != '英语':
        return []
    if assignment.get('category') == 'translation':
        from .translation import translation_pairs
        return [{**pair, 'label':'课本翻译'}
                for pair in translation_pairs(assignment.get('content', {}).get('textbook_text', ''))]
    if assignment.get('category') != 'vocabulary':
        return []
    rows = []
    for key, label in [('words', '单词'), ('phrases', '词组'), ('sentences', '句子')]:
        for line in assignment.get('content', {}).get(key, '').splitlines():
            parts = vocabulary_parts(line)
            if len(parts) == 2 and all(part.strip() for part in parts):
                rows.append({'prompt': parts[1].strip(), 'expected': parts[0].strip(), 'label': label})
    return rows


def parse_strokes(payload, count):
    if count > MAX_QUESTIONS:
        raise ValidationError('一次最多默写 100 项，请家长拆分任务。')
    if not isinstance(payload, str) or len(payload) > 2_000_000:
        raise ValidationError('笔迹数据过大，请减少笔画后重试。')
    try:
        rows = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise ValidationError('笔迹数据无效，请刷新后重新书写。') from exc
    if not isinstance(rows, list) or len(rows) != count:
        raise ValidationError('笔迹与题目数量不一致，请刷新页面。')
    point_count = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'strokes'} or not isinstance(row['strokes'], list) or len(row['strokes']) > 200:
            raise ValidationError('笔迹格式无效。')
        for stroke in row['strokes']:
            if not isinstance(stroke, dict) or set(stroke) != {'width', 'points'}:
                raise ValidationError('笔画格式无效。')
            width, points = stroke['width'], stroke['points']
            if type(width) not in (int, float) or not math.isfinite(width) or not 2 <= width <= 16:
                raise ValidationError('笔画宽度无效。')
            if not isinstance(points, list) or not 1 <= len(points) <= 5000:
                raise ValidationError('笔画点数无效。')
            point_count += len(points)
            if point_count > 50_000:
                raise ValidationError('笔迹过于复杂，请减少笔画后重试。')
            for point in points:
                if not isinstance(point, list) or len(point) != 2 or any(type(p) not in (int, float) or not math.isfinite(p) for p in point) or not 0 <= point[0] <= 1600 or not 0 <= point[1] <= 400:
                    raise ValidationError('笔迹坐标无效。')
    if not any(row['strokes'] for row in rows):
        raise ValidationError('请先手写英文答案，再保存提交。')
    return rows


def recognize_strokes(strokes):
    from PIL import Image, ImageDraw
    from .ocr import recognize
    image = Image.new('RGB', (1600, 400), 'white')
    draw = ImageDraw.Draw(image)
    for stroke in strokes:
        points = [tuple(point) for point in stroke['points']]
        width = round(stroke['width'])
        if len(points) > 1:
            draw.line(points, fill='#17221c', width=width, joint='curve')
        radius = width / 2
        for x, y in points:
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill='#17221c')
    data = io.BytesIO()
    image.save(data, format='PNG')
    return recognize(data.getvalue())


def recognize_answers(items, rows):
    answers = []
    for index, (item, row) in enumerate(zip(items, rows), 1):
        if row['strokes']:
            try:
                result = recognize_strokes(row['strokes'])
            except ValueError as exc:
                raise ValidationError(f'第 {index} 题无法识别，请重新书写。') from exc
            except Exception as exc:
                raise ValidationError('本地 OCR 暂不可用，请确认已安装 requirements-ocr.txt 并准备好模型；笔迹尚未提交。') from exc
        else:
            result = {'text': '', 'confidence': 0}
        answers.append({**item, 'recognized': result['text'], 'confidence': result['confidence']})
    return {'version': 1, 'rows': rows, 'answers': answers}


def draft_state(task):
    count = len(questions(task))
    if task.handwriting_draft and len(task.handwriting_draft.get('rows', [])) == count:
        return task.handwriting_draft
    rows = task.handwriting.get('rows', [])
    rows = rows if len(rows) == count else [{'strokes': []} for _ in range(count)]
    return {'rows': rows, 'saved_indices': [i for i, row in enumerate(rows) if row['strokes']],
            'revision': task.handwriting_draft.get('revision', 0) if task.handwriting_draft else 0}


def completion_status(task, job=None, review=None):
    """Parent progress comes from saved student strokes, including unsubmitted work."""
    items = questions(task)
    state = draft_state(task)
    saved = set(state['saved_indices'])
    submitted_rows = task.handwriting.get('rows', [])
    submitted_answers = task.handwriting.get('answers', [])
    submitted = bool(task.current_id and task.handwriting and state['rows'] == submitted_rows)
    count = sum(index in saved for index in range(len(items)))
    if not count:
        status = '尚未开始'
    elif submitted:
        status = ('已批改 · ' + ('通过' if review.grade in ('A', 'B', 'C') else '未通过')) if review else '已提交，等待批改'
        if job and job.status == 'parent' and not review:
            status = '已提交，待家长审核'
    elif count < len(items):
        status = '正在完成，尚未提交批改'
    else:
        status = '手写已完成，待提交批改'
    if task.phase == 'awarded':
        status = '完成'
    elif task.phase == 'revision':
        status = '待修改，等待学生重新提交'
    elif submitted:
        status = '等待审核' + (' · AI ' + ('通过' if review.grade in ('A', 'B', 'C') else '未通过') if review else '')
    sections = []
    labels = list(dict.fromkeys(item['label'] for item in items))
    for label in labels:
        rows = []
        for index, item in enumerate(items):
            if item['label'] != label:
                continue
            is_saved = index in saved
            matches = is_saved and index < len(submitted_rows) and state['rows'][index] == submitted_rows[index] and index < len(submitted_answers)
            answer = submitted_answers[index] if matches else {}
            rows.append({'prompt':item['prompt'], 'number':index + 1, 'saved':is_saved,
                         'strokes':state['rows'][index]['strokes'] if is_saved else [],
                         'script_id':f'completion-strokes-{index}',
                         'recognized':answer.get('recognized'),
                         'confidence_percent':round(answer['confidence'] * 100, 1) if matches else None})
        sections.append({'label':label, 'items':rows, 'total':len(rows),
                         'saved':sum(row['saved'] for row in rows)})
    return {'status':status, 'saved':count, 'total':len(items), 'submitted':submitted,
            'sections':sections}
