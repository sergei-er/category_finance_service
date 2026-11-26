(() => {
  const root = document.querySelector('.workbench');
  if (!root) return;
  const key = 'assessment-ui:' + location.pathname;
  const blocks = [...root.querySelectorAll('details[id]')];
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
    if (saved) {
      for (const block of blocks) {
        if (Object.hasOwn(saved.open, block.id)) block.open = saved.open[block.id];
      }
      if (location.hash) {
        const target = document.getElementById(location.hash.slice(1));
        if (target) {
          for (let parent = target; parent; parent = parent.parentElement) {
            if (parent.tagName === 'DETAILS') parent.open = true;
          }
          requestAnimationFrame(() => target.scrollIntoView({block: 'center'}));
        }
      }
    }
  } catch (_) { /* Без хранилища стандартные HTML-формы продолжают работать. */ }
  const save = () => {
    try { sessionStorage.setItem(key, JSON.stringify({open: Object.fromEntries(blocks.map(b => [b.id, b.open]))})); }
    catch (_) { /* Хранилище может быть отключено браузером. */ }
  };
  blocks.forEach(block => block.addEventListener('toggle', save));
  document.querySelectorAll('[data-expand]').forEach(button => button.addEventListener('click', () => {
    root.querySelectorAll('.measure-block,.measure-subgroup').forEach(block => { block.open = button.dataset.expand === 'all'; });
    save();
  }));
  document.querySelectorAll('form').forEach(form => form.addEventListener('submit', save));
})();
