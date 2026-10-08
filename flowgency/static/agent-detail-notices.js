// Shows a notice when a saved revision differs from the one this page's form loaded.
// Read-only: it never changes a form field, never reloads and never touches a draft.
(function () {
  'use strict';

  var host = document.querySelector('[data-live-notices]');
  if (!host) return;

  var MARKER = '[data-live-revision]';
  var loaded = {};

  function loadedRevision(marker) {
    var name = marker.getAttribute('data-live-baseline-input');
    if (name) {
      var inputs = document.querySelectorAll('input[name="' + name + '"]');
      for (var i = 0; i < inputs.length; i++) {
        if (!inputs[i].closest('[data-live-region]')) return inputs[i].value;
      }
    }
    return loaded[marker.getAttribute('data-live-scope')];
  }

  function changedScopes() {
    var changed = {};
    var markers = document.querySelectorAll(MARKER);
    for (var i = 0; i < markers.length; i++) {
      var scope = markers[i].getAttribute('data-live-scope');
      var current = markers[i].getAttribute('data-live-revision');
      if (!(scope in loaded)) loaded[scope] = current;
      if (current !== loadedRevision(markers[i])) {
        changed[scope] = markers[i].getAttribute('data-live-changed');
      }
    }
    return changed;
  }

  function render() {
    var changed = changedScopes();
    var shown = host.querySelectorAll('[data-live-notice-scope]');
    for (var i = 0; i < shown.length; i++) {
      var scope = shown[i].getAttribute('data-live-notice-scope');
      if (!(scope in changed)) shown[i].remove();
      else if (shown[i].textContent !== changed[scope]) shown[i].textContent = changed[scope];
    }
    Object.keys(changed).forEach(function (scope) {
      if (host.querySelector('[data-live-notice-scope="' + scope + '"]')) return;
      var notice = document.createElement('p');
      notice.setAttribute('data-live-notice-scope', scope);
      notice.className = 'mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900';
      notice.textContent = changed[scope];
      host.appendChild(notice);
    });
  }

  // Record the loaded revisions before the first refresh can replace a marker.
  changedScopes();
  // A controller that advanced its own baselines after saving announces it, so no stale notice lingers.
  document.addEventListener('flowgency:live-baseline', render);
  new MutationObserver(function (records) {
    for (var i = 0; i < records.length; i++) {
      if (!host.contains(records[i].target)) return render();
    }
  }).observe(document.querySelector('main') || document.body, {
    attributes: true,
    attributeFilter: ['data-live-revision'],
    childList: true,
    subtree: true,
  });
})();
