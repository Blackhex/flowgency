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
  const healthDetails = root.querySelector('#workflow-storage-health-details');
  const healthDetail = root.querySelector('[data-workflow-settings-health-detail]');
  const healthIssues = root.querySelector('[data-workflow-settings-health-issues]');
  const form = root.querySelector('#workflow-settings-form');
  const nameInput = root.querySelector('#workflow-name');
  const integration = root.querySelector('#workflow-integration');
  const storageRoot = root.querySelector('#workflow-storage-root');
  const saveButton = root.querySelector('[data-workflow-settings-save]');
  const revertButton = root.querySelector('[data-workflow-settings-revert]');
  const statusNode = root.querySelector('[data-workflow-settings-status]');

  let baseline = normalizeForm(initial.savedForm);
  let healthRequestVersion = 0;
  let activeRequest = null;
  let currentHealthTarget = normalizedHealthTarget(readForm());

  function normalizeForm(value) {
    return {
      name: typeof value?.name === 'string' ? value.name : '',
      blueprint: typeof value?.blueprint === 'string' ? value.blueprint : '',
      integration: typeof value?.integration === 'string' ? value.integration : 'local',
      storageRoot: typeof value?.storageRoot === 'string' ? value.storageRoot : '',
      expectedRevision: typeof value?.expectedRevision === 'string' ? value.expectedRevision : '',
    };
  }

  function readForm() {
    return normalizeForm({
      name: nameInput instanceof HTMLInputElement ? nameInput.value : '',
      blueprint: blueprint instanceof HTMLSelectElement ? blueprint.value : '',
      integration: integration instanceof HTMLSelectElement ? integration.value : '',
      storageRoot: storageRoot instanceof HTMLInputElement ? storageRoot.value : '',
      expectedRevision: form instanceof HTMLFormElement
        ? String(new FormData(form).get('expected_revision') ?? '')
        : '',
    });
  }

  function normalizedHealthTarget(state) {
    return JSON.stringify({
      blueprint: state.blueprint.trim(),
      integration: state.integration.trim(),
      storageRoot: state.storageRoot.trim(),
    });
  }

  function sameForm(left, right) {
    return left.name === right.name
      && left.blueprint === right.blueprint
      && left.integration === right.integration
      && left.storageRoot === right.storageRoot;
  }

  function updateBlueprintLink() {
    if (!(blueprint instanceof HTMLSelectElement) || !(blueprintLink instanceof HTMLAnchorElement)) {
      return;
    }
    blueprintLink.href = initial.blueprintHrefBase + blueprint.value;
  }

  function setStatus(label, dirty) {
    if (!(statusNode instanceof HTMLElement)) {
      return;
    }
    statusNode.textContent = label;
    statusNode.dataset.state = dirty ? 'dirty' : 'saved';
  }

  function updateDirtyState() {
    const dirty = !sameForm(readForm(), baseline);
    setStatus(dirty ? 'Unsaved changes' : 'Saved', dirty);
    if (saveButton instanceof HTMLButtonElement) {
      saveButton.disabled = !dirty;
    }
    if (revertButton instanceof HTMLButtonElement) {
      revertButton.disabled = !dirty;
    }
  }

  function clearChildren(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function normalizedIssues(payload) {
    if (!Array.isArray(payload?.issues)) {
      return [];
    }
    return payload.issues
      .filter((issue) => issue && typeof issue === 'object')
      .map((issue) => ({
        message: typeof issue.message === 'string' ? issue.message : '',
        hint: typeof issue.hint === 'string' ? issue.hint : '',
      }))
      .filter((issue) => issue.message || issue.hint);
  }

  function setHealth(payload) {
    if (!(health instanceof HTMLElement)) {
      return;
    }
    health.textContent = typeof payload?.label === 'string' ? payload.label : 'Not checked';
    health.dataset.state = typeof payload?.status === 'string' ? payload.status : 'idle';

    if (!(healthDetails instanceof HTMLElement) || !(healthDetail instanceof HTMLElement) || !(healthIssues instanceof HTMLElement)) {
      return;
    }

    const detail = typeof payload?.detail === 'string' ? payload.detail : '';
    const issues = normalizedIssues(payload);
    healthDetails.hidden = !detail && issues.length === 0;
    healthDetail.hidden = !detail;
    healthDetail.textContent = detail;
    clearChildren(healthIssues);
    for (const issue of issues) {
      const item = document.createElement('li');
      item.textContent = issue.hint ? `${issue.message} ${issue.hint}` : issue.message;
      healthIssues.appendChild(item);
    }
    healthIssues.hidden = issues.length === 0;
  }

  function resetHealth() {
    if (activeRequest?.controller) {
      activeRequest.controller.abort();
    }
    activeRequest = null;
    currentHealthTarget = normalizedHealthTarget(readForm());
    if (checkButton instanceof HTMLButtonElement) {
      checkButton.disabled = false;
    }
    setHealth({ status: 'idle', label: 'Not checked', detail: '', issues: [] });
  }

  function targetChanged() {
    const nextTarget = normalizedHealthTarget(readForm());
    if (nextTarget === currentHealthTarget) {
      return;
    }
    resetHealth();
  }

  async function checkStorage() {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const requestTarget = normalizedHealthTarget(readForm());
    const requestVersion = ++healthRequestVersion;
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    activeRequest = { version: requestVersion, target: requestTarget, controller };
    currentHealthTarget = requestTarget;
    if (checkButton instanceof HTMLButtonElement) {
      checkButton.disabled = true;
    }
    setHealth({ status: 'pending', label: 'Checking…', detail: '', issues: [] });
    try {
      const response = await fetch(initial.checkUrl, {
        method: 'POST',
        body: new FormData(form),
        headers: { Accept: 'application/json' },
        signal: controller?.signal,
      });
      const payload = await response.json();
      const latestTarget = normalizedHealthTarget(readForm());
      if (!activeRequest || activeRequest.version !== requestVersion || activeRequest.target !== requestTarget || latestTarget !== requestTarget) {
        return;
      }
      setHealth(payload);
    } catch (error) {
      if (controller?.signal.aborted) {
        return;
      }
      const latestTarget = normalizedHealthTarget(readForm());
      if (!activeRequest || activeRequest.version !== requestVersion || activeRequest.target !== requestTarget || latestTarget !== requestTarget) {
        return;
      }
      setHealth({
        status: 'unavailable',
        label: 'Check failed',
        detail: 'Could not check storage right now.',
        issues: [],
      });
    } finally {
      if (activeRequest && activeRequest.version === requestVersion) {
        activeRequest = null;
        if (checkButton instanceof HTMLButtonElement) {
          checkButton.disabled = false;
        }
      }
    }
  }

  function revertForm() {
    if (nameInput instanceof HTMLInputElement) {
      nameInput.value = baseline.name;
    }
    if (blueprint instanceof HTMLSelectElement) {
      blueprint.value = baseline.blueprint;
    }
    if (integration instanceof HTMLSelectElement) {
      integration.value = baseline.integration;
    }
    if (storageRoot instanceof HTMLInputElement) {
      storageRoot.value = baseline.storageRoot;
    }
    updateBlueprintLink();
    resetHealth();
    updateDirtyState();
  }

  if (blueprint instanceof HTMLSelectElement) {
    blueprint.addEventListener('change', () => {
      updateBlueprintLink();
      targetChanged();
      updateDirtyState();
    });
  }
  if (integration instanceof HTMLSelectElement) {
    integration.addEventListener('change', () => {
      targetChanged();
      updateDirtyState();
    });
  }
  if (storageRoot instanceof HTMLInputElement) {
    storageRoot.addEventListener('input', () => {
      targetChanged();
      updateDirtyState();
    });
  }
  if (nameInput instanceof HTMLInputElement) {
    nameInput.addEventListener('input', updateDirtyState);
  }
  if (checkButton instanceof HTMLButtonElement) {
    checkButton.addEventListener('click', checkStorage);
  }
  if (revertButton instanceof HTMLButtonElement) {
    revertButton.addEventListener('click', (event) => {
      event.preventDefault();
      revertForm();
    });
  }
  if (window.lucide && typeof window.lucide.createIcons === 'function') {
    window.lucide.createIcons({ attrs: { 'stroke-width': 1.8 } });
  }
  updateBlueprintLink();
  setHealth(initial.health);
  currentHealthTarget = normalizedHealthTarget(readForm());
  updateDirtyState();
}());