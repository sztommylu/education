(() => {
  'use strict';
  function paint(canvas, strokes) {
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = '#17221c'; ctx.fillStyle = '#17221c'; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    for (const stroke of strokes) {
      if (!stroke.points.length) continue;
      ctx.lineWidth = stroke.width; ctx.beginPath(); ctx.moveTo(...stroke.points[0]);
      for (const point of stroke.points.slice(1)) ctx.lineTo(...point);
      ctx.stroke();
      for (const point of [stroke.points[0], stroke.points[stroke.points.length - 1]]) {
        ctx.beginPath(); ctx.arc(...point, stroke.width / 2, 0, Math.PI * 2); ctx.fill();
      }
    }
  }
  for (const container of document.querySelectorAll('.handwriting-saved')) {
    const rows = JSON.parse(document.getElementById(container.dataset.source).textContent);
    for (const [index, row] of rows.entries()) {
      const block = document.createElement('div'); block.className = 'handwriting-question';
      const title = document.createElement('strong'); title.textContent = `${index + 1}. ${row.prompt}`;
      const canvas = document.createElement('canvas'); canvas.width = 1600; canvas.height = 400;
      canvas.className = 'handwriting-pad'; canvas.setAttribute('aria-label', `${row.prompt}：已保存的原始笔迹`);
      const result = document.createElement('p'); result.className = 'muted';
      result.textContent = `识别原文：${row.recognized || '（未作答）'} · OCR 置信度：${(row.confidence * 100).toFixed(1)}%`;
      block.append(title, canvas, result); container.append(block); paint(canvas, row.strokes);
    }
  }
  for (const canvas of document.querySelectorAll('[data-handwriting-strokes]')) {
    paint(canvas, JSON.parse(document.getElementById(canvas.dataset.handwritingStrokes).textContent));
  }
  const form = document.querySelector('#handwriting-form');
  if (form) {
    const outsideActions = document.querySelector('[data-outside-task-card]');
    const taskCard = outsideActions?.closest('.panel');
    if (outsideActions && taskCard) taskCard.after(outsideActions);
    const state = JSON.parse(document.querySelector('#handwriting-state').textContent);
    const items = JSON.parse(document.querySelector('#handwriting-items').textContent);
    const rows = state.rows, saved = new Set(state.saved_indices), dirty = new Set();
    const runner = document.querySelector('#handwriting-runner'), canvas = document.querySelector('#handwriting-canvas');
    const status = document.querySelector('#handwriting-status');
    const title = document.querySelector('#handwriting-prompt'), position = document.querySelector('#handwriting-position');
    const previous = document.querySelector('#handwriting-previous'), next = document.querySelector('#handwriting-next');
    const save = document.querySelector('#handwriting-save'), submit = document.querySelector('#handwriting-submit');
    let current = -1, group = [], busy = false, submitting = false, pointer = null, active = null;
    const categoryButtons = [...document.querySelectorAll('[data-start-category]')];
    const indices = label => items.flatMap((item, index) => item.label === label ? [index] : []);
    const complete = index => saved.has(index) && !dirty.has(index);
    function update() {
      const total = items.filter((_, index) => complete(index)).length;
      for (const button of categoryButtons) {
        const list = indices(button.dataset.startCategory), done = list.filter(complete).length;
        button.disabled = busy || !list.length;
        button.textContent = done === list.length && list.length ? '查看 / 重写' : done ? '继续' : '开始';
        const label = document.querySelector(`[data-category-progress="${button.dataset.startCategory}"]`);
        if (label) label.textContent = `已保存 ${done} / ${list.length} 题`;
      }
      const offset = group.indexOf(current);
      previous.disabled = busy || offset <= 0;
      next.disabled = busy || current < 0 || !complete(current);
      next.textContent = offset === group.length - 1 ? '完成本组' : '下一题';
      save.disabled = busy || current < 0 || !rows[current].strokes.length || (complete(current) && !dirty.has(current));
      submit.disabled = busy || total !== items.length;
      form.querySelectorAll('[data-undo], [data-clear]').forEach(button => button.disabled = busy || current < 0);
      form.querySelector('[name=revision]').value = state.revision;
    }
    function show(index) {
      current = index; runner.hidden = false; active = null; pointer = null;
      title.textContent = items[index].prompt;
      position.textContent = `${items[index].label} · 第 ${group.indexOf(index) + 1} / ${group.length} 题`;
      canvas.setAttribute('aria-label', `${items[index].prompt}：请手写英文`);
      paint(canvas, rows[index].strokes); update();
      status.textContent = complete(index) ? '本题已保存，可以查看或重写。' : '写好后点击“保存本题”。';
      title.focus({preventScroll:true});
    }
    function mayLeave() {
      if (current >= 0 && dirty.has(current)) {status.textContent = '请先保存当前题，再切换题目。'; return false;}
      return !busy;
    }
    for (const button of categoryButtons) button.addEventListener('click', () => {
      if (!mayLeave()) {document.querySelector('#handwriting-practice').scrollIntoView({block:'center'}); return;}
      group = indices(button.dataset.startCategory);
      if (!group.length) return;
      show(group.find(index => !complete(index)) ?? group[0]);
      document.querySelector('#handwriting-practice').scrollIntoView({block:'start'});
    });
    previous.onclick = () => {if (mayLeave()) show(group[group.indexOf(current) - 1]);};
    next.onclick = () => {
      if (busy || !complete(current)) return;
      const offset = group.indexOf(current);
      if (offset < group.length - 1) show(group[offset + 1]);
      else {
        runner.hidden = true; current = -1; update();
        status.textContent = saved.size === items.length ? '' : '本组已完成，请开始或继续其他区域。';
        if (!submit.disabled) submit.focus();
        else categoryButtons.find(button => indices(button.dataset.startCategory).some(index => !complete(index)))?.focus();
      }
    };
    function changed() {dirty.add(current); update(); status.textContent = '笔迹已修改，请保存本题。';}
    const coordinate = event => {
      const rect = canvas.getBoundingClientRect();
      return [Math.round(Math.max(0, Math.min(1600, (event.clientX - rect.left) * 1600 / rect.width))),
              Math.round(Math.max(0, Math.min(400, (event.clientY - rect.top) * 400 / rect.height)))];
    };
    canvas.addEventListener('pointerdown', event => {
      if (busy || current < 0 || pointer !== null || !event.isPrimary) return;
      event.preventDefault(); pointer = event.pointerId; canvas.setPointerCapture(pointer);
      active = {width:5, points:[coordinate(event)]}; rows[current].strokes.push(active);
      changed(); paint(canvas, rows[current].strokes);
    });
    canvas.addEventListener('pointermove', event => {
      if (!active || event.pointerId !== pointer) return;
      const point = coordinate(event), last = active.points[active.points.length - 1];
      if (point[0] !== last[0] || point[1] !== last[1]) {active.points.push(point); paint(canvas, rows[current].strokes);}
    });
    const finish = event => {if (event.pointerId === pointer) {active = null; pointer = null;}};
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(name, finish);
    form.querySelector('[data-undo]').onclick = () => {if (!busy && current >= 0 && rows[current].strokes.length) {rows[current].strokes.pop(); changed(); paint(canvas, rows[current].strokes);}};
    form.querySelector('[data-clear]').onclick = () => {if (!busy && current >= 0 && rows[current].strokes.length) {rows[current].strokes = []; changed(); paint(canvas, []);}};
    save.onclick = async () => {
      if (busy || current < 0 || !rows[current].strokes.length) return;
      busy = true; update(); status.textContent = '正在保存本题…';
      const body = new FormData();
      body.set('csrfmiddlewaretoken', form.querySelector('[name=csrfmiddlewaretoken]').value);
      body.set('operation', 'save_handwriting_answer'); body.set('index', current);
      body.set('revision', state.revision); body.set('handwriting', JSON.stringify([rows[current]]));
      try {
        const response = await fetch(form.action, {method:'POST', body, credentials:'same-origin'});
        if (response.redirected) throw Error('登录状态已失效，请重新登录后保存。');
        const result = await response.json();
        if (!response.ok) throw Error(result.error || '保存失败，请重试。');
        state.revision = result.revision; saved.clear(); result.saved_indices.forEach(index => saved.add(index)); dirty.delete(current);
        status.textContent = '本题已保存到服务器，点击下一题继续。';
      } catch (error) {status.textContent = error instanceof SyntaxError ? '服务暂不可用，笔迹已保留，请重试保存。' : error.message;}
      finally {busy = false; update();}
    };
    form.addEventListener('submit', event => {
      if (busy || items.some((_, index) => !complete(index))) {event.preventDefault(); status.textContent = '请先完成并保存所有题目。'; return;}
      submitting = true; busy = true; update(); status.textContent = '正在识别全部笔迹并提交 AI 批改，请稍候…';
    });
    window.addEventListener('beforeunload', event => {if (!submitting && dirty.size) {event.preventDefault(); event.returnValue = '';}});
    update();
  }
  if (document.querySelector('[data-handwriting-pending]')) setTimeout(() => location.reload(), 5000);
})();
