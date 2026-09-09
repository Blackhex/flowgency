(function () {
  const root = document.querySelector('[data-workflow-settings-root]');
  const initialNode = document.getElementById('workflow-settings-initial');
  if (!root || !initialNode || !initialNode.textContent) {
    return;
  }

  const initial = JSON.parse(initialNode.textContent);
  const blueprint = root.querySelector('#workflow-blueprint');
  const blueprintLink = root.querySelector('#workflow-blueprint-link');
  const checkButton = root.querySelector('#workflow-check-storage');
  const health = root.querySelector('#workflow-storage-health');
  const form = root.querySelector('#workflow-settings-form');

  function updateBlueprintLink() {
    if (!(blueprint instanceof HTMLSelectElement) || !(blueprintLink instanceof HTMLAnchorElement)) {
      return;
    }
    blueprintLink.href = initial.blueprintHrefBase + blueprint.value;
  }

  function setHealth(label, tone) {
    if (!(health instanceof HTMLElement)) {
      return;
    }
    health.textContent = label;
    health.dataset.state = tone;
  }

  async function checkStorage() {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    setHealth('Checking…', 'pending');
    const response = await fetch(initial.checkUrl, {
      method: 'POST',
      body: new FormData(form),
      headers: { Accept: 'application/json' },
    });
    const payload = await response.json();
    setHealth(payload.label, payload.status);
  }

  if (blueprint instanceof HTMLSelectElement) {
    blueprint.addEventListener('change', updateBlueprintLink);
  }
  if (checkButton instanceof HTMLButtonElement) {
    checkButton.addEventListener('click', checkStorage);
  }
  updateBlueprintLink();
}());