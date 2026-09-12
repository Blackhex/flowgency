(function () {
  function setCollapsed(textEl, collapsed) {
    if (collapsed) {
      textEl.style.display = '-webkit-box';
      textEl.style.webkitBoxOrient = 'vertical';
      textEl.style.webkitLineClamp = '2';
      textEl.style.overflow = 'hidden';
    } else {
      textEl.style.display = '';
      textEl.style.webkitBoxOrient = '';
      textEl.style.webkitLineClamp = '';
      textEl.style.overflow = '';
    }
  }

  function fullHeight(textEl) {
    const previous = {
      display: textEl.style.display,
      orient: textEl.style.webkitBoxOrient,
      clamp: textEl.style.webkitLineClamp,
      overflow: textEl.style.overflow,
    };
    setCollapsed(textEl, false);
    const height = textEl.scrollHeight;
    textEl.style.display = previous.display;
    textEl.style.webkitBoxOrient = previous.orient;
    textEl.style.webkitLineClamp = previous.clamp;
    textEl.style.overflow = previous.overflow;
    return height;
  }

  function collapsedHeight(textEl) {
    const previous = {
      display: textEl.style.display,
      orient: textEl.style.webkitBoxOrient,
      clamp: textEl.style.webkitLineClamp,
      overflow: textEl.style.overflow,
    };
    setCollapsed(textEl, true);
    const height = textEl.clientHeight;
    textEl.style.display = previous.display;
    textEl.style.webkitBoxOrient = previous.orient;
    textEl.style.webkitLineClamp = previous.clamp;
    textEl.style.overflow = previous.overflow;
    return height;
  }

  function syncReport(container) {
    const textEl = container.querySelector('[data-report-text]');
    const button = container.querySelector('[data-report-toggle]');
    if (!textEl || !button) {
      return;
    }
    const expanded = button.getAttribute('aria-expanded') === 'true';
    const hasOverflow = fullHeight(textEl) > collapsedHeight(textEl) + 1;
    if (!hasOverflow) {
      setCollapsed(textEl, false);
      button.hidden = true;
      button.setAttribute('aria-expanded', 'false');
      button.textContent = 'Show more';
      return;
    }
    button.hidden = false;
    setCollapsed(textEl, !expanded);
    button.textContent = expanded ? 'Show less' : 'Show more';
  }

  function wireReport(container) {
    const button = container.querySelector('[data-report-toggle]');
    if (!button || button.dataset.activityBound === 'true') {
      return;
    }
    button.dataset.activityBound = 'true';
    button.addEventListener('click', function () {
      const expanded = button.getAttribute('aria-expanded') === 'true';
      button.setAttribute('aria-expanded', expanded ? 'false' : 'true');
      syncReport(container);
    });
    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(function () {
        syncReport(container);
      });
      observer.observe(container);
    }
    syncReport(container);
  }

  function init() {
    document.querySelectorAll('[data-activity-report]').forEach(wireReport);
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons();
    }
  }

  if (document.fonts && typeof document.fonts.ready === 'object') {
    document.fonts.ready.then(init);
  } else {
    init();
  }
  window.addEventListener('resize', function () {
    document.querySelectorAll('[data-activity-report]').forEach(syncReport);
  });
})();