'use strict';
(() => {
  const form = document.getElementById('assignment-form');
  if (!form) return;
  const course = form.querySelector('#id_subject');
  const category = form.querySelector('#id_english_category');
  const kind = form.querySelector('#id_kind');
  function toggle(id, visible) {
    const group = document.getElementById(id);
    group.hidden = !visible;
    if (group.tagName === 'FIELDSET') group.disabled = !visible;
  }
  function update() {
    const selected = course.value ? course.selectedOptions[0].textContent.trim() : '';
    const english = selected === '英语';
    const vocabulary = english && category.value === 'vocabulary';
    const translation = english && category.value === 'translation';
    toggle('english-options', english);
    toggle('vocabulary-fields', vocabulary);
    toggle('translation-fields', translation);
    toggle('assignment-settings', vocabulary || translation);
    toggle('course-placeholder', !english);
    document.getElementById('course-placeholder').textContent = selected
      ? `${selected}的任务布置表单稍后开放，请先选择英语。`
      : '先选择课程，再选择要布置的学习内容。';
    document.getElementById('assign-submit').disabled = !(vocabulary || translation);
    const due = document.getElementById('assignment-due');
    due.hidden = kind.value !== 'long';
    due.querySelector('input').disabled = kind.value !== 'long';
    document.getElementById('task-kind-help').textContent = kind.value === 'long'
      ? '长期任务：适合跨多天完成的阅读、项目等，可选截止日期。'
      : '日常任务：记录当天布置的作业；日常任务不会自动每天重复。';
  }
  course.addEventListener('change', update);
  category.addEventListener('change', update);
  kind.addEventListener('change', update);
  update();
})();
