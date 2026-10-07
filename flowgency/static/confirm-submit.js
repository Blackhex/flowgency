// Delegated confirmation for forms marked data-confirm, so live markup carries no inline handler.
(function () {
  'use strict';

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-confirm')) return;
    if (!window.confirm(form.getAttribute('data-confirm') || '')) event.preventDefault();
  });
})();
