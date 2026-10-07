(function () {
  const REPORT = '[data-activity-report]';
  // Container -> ResizeObserver; reports inserted by a live refresh are bound once, discarded ones released.
  const bound = new WeakMap();

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

  function reportsIn(root) {
    const found = [];
    if (!(root instanceof Element) && root !== document) {
      return found;
    }
    if (root instanceof Element && root.matches(REPORT)) {
      found.push(root);
    }
    root.querySelectorAll(REPORT).forEach(function (report) {
      found.push(report);
    });
    return found;
  }

  function wireReport(container) {
    if (bound.has(container)) {
      syncReport(container);
      return;
    }
    let observer = null;
    if (typeof ResizeObserver === 'function') {
      observer = new ResizeObserver(function () {
        syncReport(container);
      });
      observer.observe(container);
    }
    bound.set(container, observer);
    syncReport(container);
  }

  function unwireReport(container) {
    if (!bound.has(container)) {
      return;
    }
    const observer = bound.get(container);
    if (observer) {
      observer.disconnect();
    }
    bound.delete(container);
  }

  function createIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons();
    }
  }

  // Binds every report under root (default: the document) and renders its icons.
  function initActivityReports(root) {
    reportsIn(root || document).forEach(wireReport);
    createIcons();
  }

  // Releases the ResizeObservers of every report under root; call it for discarded markup.
  function disposeActivityReports(root) {
    reportsIn(root || document).forEach(unwireReport);
  }

  window.initActivityReports = initActivityReports;
  window.disposeActivityReports = disposeActivityReports;

  // One delegated toggle survives a live refresh replacing the button node.
  document.addEventListener('click', function (event) {
    const target = event.target instanceof Element ? event.target.closest('[data-report-toggle]') : null;
    const container = target ? target.closest(REPORT) : null;
    if (!target || !container) {
      return;
    }
    const expanded = target.getAttribute('aria-expanded') === 'true';
    target.setAttribute('aria-expanded', expanded ? 'false' : 'true');
    syncReport(container);
  });

  function containerOf(node) {
    const element = node instanceof Element ? node : node.parentElement;
    return element ? element.closest(REPORT) : null;
  }

  // A live refresh patches reports in place: inserted ones are initialized, discarded ones torn
  // down, and patched ones resynchronised (a morph resets the label and clamp the script owns).
  function watchLiveChanges() {
    if (typeof MutationObserver !== 'function' || !document.body) {
      return;
    }
    const watcher = new MutationObserver(function (records) {
      const touched = new Set();
      let inserted = false;
      records.forEach(function (record) {
        record.removedNodes.forEach(function (node) {
          reportsIn(node).forEach(function (report) {
            if (!report.isConnected) {
              unwireReport(report);
            }
          });
        });
        record.addedNodes.forEach(function (node) {
          if (!(node instanceof Element)) {
            return;
          }
          inserted = true;
          reportsIn(node).forEach(function (report) {
            touched.add(report);
          });
        });
        const container = containerOf(record.target);
        if (container) {
          touched.add(container);
        }
      });
      touched.forEach(function (report) {
        if (report.isConnected) {
          wireReport(report);
        }
      });
      if (inserted) {
        createIcons();
      }
      // The resync above mutates these nodes itself; those records must not retrigger it.
      watcher.takeRecords();
    });
    watcher.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['style', 'hidden', 'aria-expanded'],
    });
  }

  function init() {
    initActivityReports(document);
  }

  watchLiveChanges();
  if (document.fonts && typeof document.fonts.ready === 'object') {
    document.fonts.ready.then(init);
  } else {
    init();
  }
  window.addEventListener('resize', function () {
    reportsIn(document).forEach(syncReport);
  });
})();
