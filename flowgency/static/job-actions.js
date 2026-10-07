// Copies the resume command. The button may be replaced by a live refresh, so the handler is delegated.
(function () {
  'use strict';

  document.addEventListener('click', function (event) {
    var button = event.target instanceof Element ? event.target.closest('#copy-resume') : null;
    if (!button) return;
    var field = document.getElementById('resume-command');
    var feedback = document.getElementById('copy-resume-feedback');
    if (!field || !feedback) return;
    var write = navigator.clipboard
      ? navigator.clipboard.writeText(field.value)
      : Promise.reject(new Error('clipboard unavailable'));
    write.then(function () {
      feedback.textContent = 'Copied.';
    }, function () {
      field.focus();
      field.select();
      feedback.textContent = 'Copy failed. Select the command and copy it manually.';
    });
  });
})();
