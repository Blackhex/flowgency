(() => {
  const root = document.getElementById('permission-editor');
  const initialNode = document.getElementById('permissions-initial');
  if (!root || !initialNode || !initialNode.textContent) {
    return;
  }

  const initial = JSON.parse(initialNode.textContent);
  const form = document.getElementById('permissions-form');
  const modeSelect = document.getElementById('mode');
  const list = root.querySelector('[data-rule-list]');
  const emptyState = root.querySelector('[data-empty-rules]');
  const pathTemplate = document.getElementById('permission-row-template-path');
  const noPathTemplate = document.getElementById('permission-row-template-no-path');
  const addRuleButton = root.querySelector('[data-add-rule-button]');
  const addRuleMenu = root.querySelector('[data-add-rule-menu]');
  const summaryBody = document.getElementById('permission-summary');
  const summaryStatus = root.querySelector('[data-summary-status]');
  const summaryActions = root.querySelector('[data-summary-actions]');
  const retryPreviewButton = root.querySelector('[data-retry-preview]');
  const reloadButton = root.querySelector('[data-reload-page]');
  const pageErrors = root.querySelector('[data-page-errors]');
  const modeError = root.querySelector('[data-mode-error]');
  const saveButton = root.querySelector('[data-save-button]');
  const discardButton = root.querySelector('[data-discard-button]');
  const workspaceWrite = root.querySelector('[data-workspace-write]');
  const payloadInput = form.elements.payload;
  const savedSummaryHtml = summaryBody.innerHTML;
  const savedWorkspaceWrite = workspaceWrite.textContent;

  let rowCounter = 0;
  let conflict = Boolean(initial.conflict);
  let submitting = false;
  let dirty = false;
  let controller = null;
  let previewTimer = null;
  let draftVersion = Number(initial.draft.draft_version || 0);
  let validVersion = -1;
  let validatedDraft = null;
  let menuOpen = false;

  function nextRowId() {
    rowCounter += 1;
    return `permission-row-${rowCounter}`;
  }

  function unique(values) {
    const seen = new Set();
    const result = [];
    for (const value of values) {
      if (!value || seen.has(value)) {
        continue;
      }
      seen.add(value);
      result.push(value);
    }
    return result;
  }

  function availableNames(target) {
    return initial.catalog.tools
      .filter((tool) => !tool.targets.length || tool.targets.includes(target))
      .map((tool) => tool.name);
  }

  function normalizeDraft(draft) {
    return {
      mode: draft.mode,
      rules: draft.rules.map((rule) => ({
        source_index: rule.source_index === undefined ? null : rule.source_index,
        target: rule.target,
        path: rule.target === 'path' ? (rule.path ?? '') : null,
        selected: [...rule.selected],
      })),
    };
  }

  function wireToDisplay(draft) {
    return {
      mode: draft.mode,
      rules: draft.rules.map((rule) => ({
        clientId: nextRowId(),
        sourceIndex: rule.source_index,
        target: rule.target,
        path: rule.path ?? '',
        selected: [...rule.selected],
        choices: unique(availableNames(rule.target).concat(rule.selected)),
      })),
    };
  }

  const baselineDraft = structuredClone(initial.baseline.draft);
  const baselineSerialized = JSON.stringify(normalizeDraft(baselineDraft));
  let displayDraft = wireToDisplay(initial.draft.draft);

  function createToolChoice(row, name, checked) {
    const toolList = row.querySelector('[data-tool-list]');
    const label = document.createElement('label');
    const input = document.createElement('input');
    const text = document.createElement('span');
    const rowId = row.dataset.rowId;
    input.type = 'checkbox';
    input.dataset.toolName = name;
    input.id = `${rowId}-tool-${toolList.children.length}`;
    input.checked = checked;
    text.textContent = name;
    label.htmlFor = input.id;
    label.append(input, text);
    toolList.append(label);
  }

  function setRuleStatus(row) {
    const selectedCount = row.querySelectorAll('[data-tool-name]:checked').length;
    const status = row.querySelector('[data-rule-status]');
    if (!status) {
      return;
    }
    status.textContent = selectedCount === 0
      ? 'No tools selected'
      : `${selectedCount} tool${selectedCount === 1 ? '' : 's'} selected`;
  }

  function ruleTitle(index, rule) {
    if (rule.target === 'no_path') {
      return 'No-path tools';
    }
    return index === 0 ? 'Workspace access' : 'Path rule';
  }

  function createRow(rule, index) {
    const template = rule.target === 'path' ? pathTemplate : noPathTemplate;
    const fragment = template.content.cloneNode(true);
    const row = fragment.querySelector('[data-rule-row]');
    row.dataset.rowId = rule.clientId;
    row.setAttribute('data-row-id', rule.clientId);
    row.dataset.target = rule.target;
    row.dataset.sourceIndex = rule.sourceIndex == null ? '' : String(rule.sourceIndex);
    row.querySelector('[data-rule-title]').textContent = ruleTitle(index, rule);

    const removeButton = row.querySelector('[data-remove-rule]');
    removeButton.setAttribute('aria-label', `Remove ${ruleTitle(index, rule).toLowerCase()}`);
    removeButton.title = 'Remove rule';

    const pathInput = row.querySelector('[data-rule-path]');
    if (pathInput) {
      pathInput.id = `${rule.clientId}-path`;
      pathInput.value = rule.path;
      row.querySelector('[data-path-label]').htmlFor = pathInput.id;
      row.querySelector('[data-path-error]').id = `${rule.clientId}-path-error`;
    }

    const toolLabel = row.querySelector('[data-tools-label]');
    const toolList = row.querySelector('[data-tool-list]');
    if (toolLabel) {
      toolLabel.id = `${rule.clientId}-tools-label`;
      toolList.setAttribute('aria-labelledby', toolLabel.id);
    } else {
      toolList.setAttribute('aria-label', 'No-path tools');
    }

    const customInput = row.querySelector('[data-custom-tool]');
    const addCustomButton = row.querySelector('[data-add-custom-tool]');
    customInput.id = `${rule.clientId}-custom-tool`;
    row.querySelector('[data-selected-error]').id = `${rule.clientId}-selected-error`;
    row.querySelector('[data-row-error]').id = `${rule.clientId}-row-error`;
    addCustomButton.setAttribute('aria-label', 'Add custom tool');
    addCustomButton.title = 'Add custom tool';
    addCustomButton.setAttribute('aria-controls', customInput.id);

    for (const name of rule.choices) {
      createToolChoice(row, name, rule.selected.includes(name));
    }

    setRuleStatus(row);
    return row;
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons({ attrs: { 'aria-hidden': 'true', focusable: 'false' } });
    }
  }

  function updateEmptyState() {
    emptyState.hidden = list.querySelector('[data-rule-row]') !== null;
  }

  function renderDraft(draft) {
    modeSelect.value = draft.mode;
    list.replaceChildren();
    for (const [index, rule] of draft.rules.entries()) {
      list.append(createRow(rule, index));
    }
    updateEmptyState();
    refreshIcons();
  }

  function snapshotDisplayDraft() {
    return {
      mode: modeSelect.value,
      rules: [...list.querySelectorAll('[data-rule-row]')].map((row) => ({
        clientId: row.dataset.rowId,
        sourceIndex: row.dataset.sourceIndex === '' ? null : Number(row.dataset.sourceIndex),
        target: row.dataset.target,
        path: row.dataset.target === 'path' ? row.querySelector('[data-rule-path]').value : '',
        selected: [...row.querySelectorAll('[data-tool-name]:checked')].map((input) => input.dataset.toolName),
        choices: [...row.querySelectorAll('[data-tool-name]')].map((input) => input.dataset.toolName),
      })),
    };
  }

  function collectDraft() {
    return {
      mode: modeSelect.value,
      rules: [...root.querySelectorAll('[data-rule-row]')].map((row) => ({
        source_index: row.dataset.sourceIndex === '' ? null : Number(row.dataset.sourceIndex),
        target: row.dataset.target,
        path: row.dataset.target === 'path' ? row.querySelector('[data-rule-path]').value : null,
        selected: [...row.querySelectorAll('[data-tool-name]:checked')].map((input) => input.dataset.toolName),
      })),
    };
  }

  function setSummaryState(message, tone, options = {}) {
    summaryBody.setAttribute('aria-busy', options.busy ? 'true' : 'false');
    summaryStatus.textContent = message;
    summaryStatus.dataset.tone = tone;
    summaryStatus.hidden = !message;
    summaryBody.hidden = Boolean(options.hideSummary);
    summaryActions.hidden = !options.retry && !options.reload;
    retryPreviewButton.hidden = !options.retry;
    reloadButton.hidden = !options.reload;
  }

  function setSummaryPending() {
    setSummaryState('Updating preview', 'pending', { busy: true, hideSummary: true, retry: false, reload: false });
  }

  function setSummaryUnavailable() {
    setSummaryState('Preview unavailable', 'error', { busy: false, hideSummary: true, retry: true, reload: conflict });
  }

  function setSummaryStale() {
    setSummaryState('Saved summary shown', 'warning', { busy: false, hideSummary: false, retry: false, reload: conflict });
  }

  function setSummaryCurrent() {
    setSummaryState('', 'pending', { busy: false, hideSummary: false, retry: false, reload: false });
  }

  function clearIssueState() {
    pageErrors.hidden = true;
    pageErrors.replaceChildren();
    modeError.hidden = true;
    modeError.textContent = '';
    modeSelect.removeAttribute('aria-invalid');
    modeSelect.removeAttribute('aria-describedby');
    for (const row of list.querySelectorAll('[data-rule-row]')) {
      for (const field of row.querySelectorAll('[data-path-error], [data-selected-error], [data-row-error]')) {
        field.hidden = true;
        field.textContent = '';
      }
      const pathInput = row.querySelector('[data-rule-path]');
      const customInput = row.querySelector('[data-custom-tool]');
      const toolInputs = row.querySelectorAll('[data-tool-name]');
      if (pathInput) {
        pathInput.removeAttribute('aria-invalid');
        pathInput.removeAttribute('aria-describedby');
      }
      if (customInput) {
        customInput.removeAttribute('aria-invalid');
        customInput.removeAttribute('aria-describedby');
      }
      for (const input of toolInputs) {
        input.removeAttribute('aria-invalid');
        input.removeAttribute('aria-describedby');
      }
    }
  }

  function renderMessages(container, messages) {
    container.replaceChildren();
    for (const message of messages) {
      const line = document.createElement('p');
      line.textContent = message;
      container.append(line);
    }
    container.hidden = messages.length === 0;
  }

  function applyIssues(issues) {
    clearIssueState();
    const rows = [...list.querySelectorAll('[data-rule-row]')];
    const generalMessages = [];
    for (const issue of issues) {
      const field = issue.field || '';
      const message = issue.message || 'Invalid value.';
      const match = field.match(/^rules\.(\d+)\.(.+)$/);
      if (field === 'mode') {
        modeError.textContent = message;
        modeError.hidden = false;
        modeSelect.setAttribute('aria-invalid', 'true');
        modeSelect.setAttribute('aria-describedby', modeError.id);
        continue;
      }
      if (match) {
        const row = rows[Number(match[1])];
        if (!row) {
          generalMessages.push(message);
          continue;
        }
        const suffix = match[2];
        if (suffix === 'path') {
          const pathError = row.querySelector('[data-path-error]');
          const pathInput = row.querySelector('[data-rule-path]');
          if (pathError && pathInput) {
            pathError.textContent = message;
            pathError.hidden = false;
            pathInput.setAttribute('aria-invalid', 'true');
            pathInput.setAttribute('aria-describedby', pathError.id);
            continue;
          }
        }
        if (suffix === 'selected') {
          const selectedError = row.querySelector('[data-selected-error]');
          if (selectedError) {
            selectedError.textContent = message;
            selectedError.hidden = false;
            const describedBy = selectedError.id;
            for (const input of row.querySelectorAll('[data-tool-name], [data-custom-tool]')) {
              input.setAttribute('aria-invalid', 'true');
              input.setAttribute('aria-describedby', describedBy);
            }
            continue;
          }
        }
        const rowError = row.querySelector('[data-row-error]');
        if (rowError) {
          rowError.textContent = message;
          rowError.hidden = false;
          continue;
        }
      }
      generalMessages.push(message);
      if (issue.code === 'catalog-conflict' || issue.code === 'config-conflict') {
        conflict = true;
      }
    }
    renderMessages(pageErrors, generalMessages);
  }

  function setDirtyState() {
    dirty = JSON.stringify(collectDraft()) !== baselineSerialized;
    saveButton.disabled = !dirty || conflict || submitting || validVersion !== draftVersion;
    root.dataset.dirty = dirty ? 'true' : 'false';
  }

  function closeMenu(restoreFocus) {
    menuOpen = false;
    addRuleMenu.hidden = true;
    addRuleButton.setAttribute('aria-expanded', 'false');
    if (restoreFocus) {
      addRuleButton.focus();
    }
  }

  function openMenu() {
    menuOpen = true;
    addRuleMenu.hidden = false;
    addRuleButton.setAttribute('aria-expanded', 'true');
    const firstItem = addRuleMenu.querySelector('[role="menuitem"]');
    if (firstItem) {
      firstItem.focus();
    }
  }

  function schedulePreview() {
    draftVersion += 1;
    validVersion = -1;
    validatedDraft = null;
    controller?.abort();
    clearTimeout(previewTimer);
    setSummaryPending();
    setDirtyState();
    const version = draftVersion;
    previewTimer = window.setTimeout(() => requestPreview(version), 250);
  }

  async function requestPreview(version) {
    const draft = collectDraft();
    const serializedDraft = JSON.stringify(draft);
    const activeController = new AbortController();
    controller = activeController;
    try {
      const response = await fetch(initial.preview_url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: activeController.signal,
        body: JSON.stringify({
          revision: initial.draft.revision,
          catalog_id: initial.draft.catalog_id,
          draft_version: version,
          draft,
        }),
      });
      const payload = await response.json();
      if (version !== draftVersion || payload.draft_version !== version) {
        return;
      }
      if (!response.ok) {
        applyIssues(payload.issues || []);
        if (payload.code === 'catalog-conflict' || payload.code === 'config-conflict') {
          conflict = true;
        }
        setSummaryUnavailable();
        setDirtyState();
        return;
      }
      summaryBody.innerHTML = payload.summary_html;
      workspaceWrite.textContent = payload.workspace_write ? 'Workspace-root write: granted' : 'Workspace-root write: not granted';
      validVersion = version;
      validatedDraft = serializedDraft;
      conflict = false;
      applyIssues([]);
      setSummaryCurrent();
      setDirtyState();
    } catch (error) {
      if (error.name === 'AbortError' || version !== draftVersion) {
        return;
      }
      setSummaryUnavailable();
      setDirtyState();
    }
  }

  function retryPreview() {
    controller?.abort();
    clearTimeout(previewTimer);
    validVersion = -1;
    validatedDraft = null;
    setSummaryPending();
    setDirtyState();
    requestPreview(draftVersion);
  }

  function restoreBaseline() {
    controller?.abort();
    clearTimeout(previewTimer);
    conflict = false;
    submitting = false;
    draftVersion = Number(initial.baseline.draft_version || 0);
    validVersion = draftVersion;
    displayDraft = wireToDisplay(baselineDraft);
    renderDraft(displayDraft);
    applyIssues([]);
    summaryBody.innerHTML = savedSummaryHtml;
    workspaceWrite.textContent = savedWorkspaceWrite;
    validatedDraft = JSON.stringify(collectDraft());
    setSummaryCurrent();
    setDirtyState();
  }

  function addRule(target) {
    const nextDraft = snapshotDisplayDraft();
    const clientId = nextRowId();
    nextDraft.rules.push({
      clientId,
      sourceIndex: null,
      target,
      path: '',
      selected: [],
      choices: availableNames(target),
    });
    displayDraft = nextDraft;
    renderDraft(displayDraft);
    applyIssues([]);
    const selector = target === 'path' ? '[data-rule-path]' : '[data-custom-tool]';
    list.querySelector(`[data-row-id="${clientId}"] ${selector}`)?.focus();
    schedulePreview();
  }

  function addCustomTool(row) {
    const input = row.querySelector('[data-custom-tool]');
    const name = input.value.trim();
    if (!name) {
      input.focus();
      return;
    }
    const nextDraft = snapshotDisplayDraft();
    const nextRule = nextDraft.rules.find((rule) => rule.clientId === row.dataset.rowId);
    if (!nextRule) {
      return;
    }
    nextRule.choices = unique(nextRule.choices.concat(name));
    if (!nextRule.selected.includes(name)) {
      nextRule.selected.push(name);
    }
    displayDraft = nextDraft;
    renderDraft(displayDraft);
    applyIssues([]);
    const target = list.querySelector(`[data-row-id="${row.dataset.rowId}"] [data-tool-name="${CSS.escape(name)}"]`);
    if (target) {
      target.focus();
    }
    schedulePreview();
  }

  renderDraft(displayDraft);
  refreshIcons();

  const currentDraftSerialized = JSON.stringify(collectDraft());
  if (!conflict && currentDraftSerialized === baselineSerialized && initial.issues.length === 0 && summaryBody.textContent.trim() !== 'Preview unavailable') {
    validVersion = draftVersion;
    validatedDraft = currentDraftSerialized;
    setSummaryCurrent();
  } else if (summaryBody.innerHTML.trim()) {
    setSummaryStale();
  } else {
    setSummaryUnavailable();
  }
  applyIssues(initial.issues || []);
  setDirtyState();

  addRuleButton.addEventListener('click', () => {
    if (menuOpen) {
      closeMenu(false);
      return;
    }
    openMenu();
  });

  addRuleButton.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openMenu();
    }
  });

  addRuleMenu.addEventListener('keydown', (event) => {
    const items = [...addRuleMenu.querySelectorAll('[role="menuitem"]')];
    const index = items.indexOf(document.activeElement);
    if (event.key === 'Escape') {
      event.preventDefault();
      closeMenu(true);
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      const nextIndex = index === -1 ? 0 : (index + direction + items.length) % items.length;
      items[nextIndex]?.focus();
      return;
    }
    if (event.key === 'Home') {
      event.preventDefault();
      items[0]?.focus();
      return;
    }
    if (event.key === 'End') {
      event.preventDefault();
      items[items.length - 1]?.focus();
      return;
    }
    if (event.key === 'Tab') {
      closeMenu(false);
    }
  });

  addRuleMenu.addEventListener('click', (event) => {
    const button = event.target.closest('[data-add-rule-type]');
    if (!button) {
      return;
    }
    const target = button.dataset.addRuleType;
    closeMenu(false);
    addRule(target);
  });

  document.addEventListener('mousedown', (event) => {
    if (menuOpen && !addRuleMenu.contains(event.target) && !addRuleButton.contains(event.target)) {
      closeMenu(false);
    }
  });

  list.addEventListener('click', (event) => {
    const removeButton = event.target.closest('[data-remove-rule]');
    if (removeButton) {
      const row = removeButton.closest('[data-rule-row]');
      const nextDraft = snapshotDisplayDraft();
      const index = nextDraft.rules.findIndex((rule) => rule.clientId === row.dataset.rowId);
      if (index === -1) {
        return;
      }
      const focusId = nextDraft.rules[index + 1]?.clientId || nextDraft.rules[index - 1]?.clientId || null;
      nextDraft.rules.splice(index, 1);
      displayDraft = nextDraft;
      renderDraft(displayDraft);
      applyIssues([]);
      const nextFocus = focusId
        ? list.querySelector(`[data-row-id="${focusId}"] [data-remove-rule]`)
        : addRuleButton;
      nextFocus?.focus();
      schedulePreview();
      return;
    }

    const addCustomButton = event.target.closest('[data-add-custom-tool]');
    if (addCustomButton) {
      addCustomTool(addCustomButton.closest('[data-rule-row]'));
    }
  });

  list.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || !event.target.matches('[data-custom-tool]')) {
      return;
    }
    event.preventDefault();
    addCustomTool(event.target.closest('[data-rule-row]'));
  });

  list.addEventListener('input', (event) => {
    if (event.target.matches('[data-rule-path]')) {
      setRuleStatus(event.target.closest('[data-rule-row]'));
      schedulePreview();
    }
  });

  list.addEventListener('change', (event) => {
    if (event.target.matches('[data-tool-name]')) {
      setRuleStatus(event.target.closest('[data-rule-row]'));
      schedulePreview();
    }
  });

  modeSelect.addEventListener('change', () => {
    schedulePreview();
  });

  discardButton.addEventListener('click', () => {
    if (conflict) {
      window.location.reload();
      return;
    }
    restoreBaseline();
  });

  retryPreviewButton.addEventListener('click', () => {
    retryPreview();
  });

  reloadButton.addEventListener('click', () => {
    window.location.reload();
  });

  form.addEventListener('submit', (event) => {
    if (validVersion !== draftVersion || conflict || submitting) {
      event.preventDefault();
      return;
    }
    const collected = collectDraft();
    const serialized = JSON.stringify(collected);
    if (serialized !== validatedDraft) {
      event.preventDefault();
      schedulePreview();
      return;
    }
    payloadInput.value = JSON.stringify({
      revision: initial.draft.revision,
      catalog_id: initial.draft.catalog_id,
      draft_version: draftVersion,
      draft: collected,
    });
    submitting = true;
    setDirtyState();
  });

  window.addEventListener('beforeunload', (event) => {
    if (!dirty || submitting) {
      return;
    }
    event.preventDefault();
    event.returnValue = '';
  });
})();