(() => {
  const root = document.getElementById('routine-editor');
  const initialNode = document.getElementById('routines-initial');
  if (!root || !initialNode || !initialNode.textContent) {
    return;
  }

  const initial = JSON.parse(initialNode.textContent);
  const form = document.getElementById('routines-form');
  const list = root.querySelector('[data-routine-list]');
  const emptyRoutines = root.querySelector('[data-empty-routines]');
  const addRoutineButton = root.querySelector('[data-add-routine]');
  const discardButton = root.querySelector('[data-discard-routines]');
  const saveButton = root.querySelector('[data-save-routines]');
  const payloadInput = form.elements.payload;
  const warningsBox = root.querySelector('[data-page-warnings]');
  const errorsBox = root.querySelector('[data-page-errors]');
  const summaryStatus = root.querySelector('[data-summary-status]');
  const summaryBody = root.querySelector('[data-summary-body]');
  const summaryActions = root.querySelector('[data-summary-actions]');
  const retryPreviewButton = root.querySelector('[data-retry-preview]');
  const reloadButton = root.querySelector('[data-reload-page]');
  const rowTemplate = document.getElementById('routine-row-template');
  const argumentTemplate = document.getElementById('routine-argument-template');
  const summaryRowTemplate = document.getElementById('routine-summary-row-template');

  const PROMPT_SCOPE_LABEL = {
    blueprint: 'Blueprint',
    instance: 'Instance',
  };

  let counter = 0;
  let conflict = Boolean(initial.conflict);
  let dirty = false;
  let submitting = false;
  let controller = null;
  let timer = null;
  let version = Number(initial.draft.draft_version || 0);
  let validVersion = -1;
  let validatedDraft = null;
  let currentIssues = Array.isArray(initial.issues) ? initial.issues : [];
  let currentWarnings = Array.isArray(initial.warnings) ? initial.warnings : [];
  let summaryRows = Array.isArray(initial.summary_rows) ? structuredClone(initial.summary_rows) : [];
  const baselineSummaryRows = Array.isArray(initial.summary_rows) ? structuredClone(initial.summary_rows) : [];

  const savedStatus = new Map((initial.saved_status || []).map((row) => [row.source_index, row]));
  const originalIds = Array.isArray(initial.original_ids) ? initial.original_ids : [];

  function nextKey() {
    counter += 1;
    return `draft-${counter}`;
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons({ attrs: { 'aria-hidden': 'true', focusable: 'false' } });
    }
  }

  function clone(value) {
    return structuredClone(value);
  }

  function promptOptions(row) {
    const groups = {
      blueprint: [],
      instance: [],
    };
    for (const [scope, name] of initial.choices.prompts || []) {
      groups[scope].push({ scope, name, missing: false });
    }
    if (row.promptName && !groups[row.promptScope].some((option) => option.name === row.promptName)) {
      groups[row.promptScope].unshift({ scope: row.promptScope, name: row.promptName, missing: true });
    }
    return groups;
  }

  function channelOptions(row) {
    const options = (initial.choices.channels || []).map(([key, label]) => ({ key, label, missing: false }));
    if (row.memoryScope === 'channel' && row.memoryChannel && !options.some((option) => option.key === row.memoryChannel)) {
      options.unshift({ key: row.memoryChannel, label: row.memoryChannel, missing: true });
    }
    return options;
  }

  function parsePromptValue(value, previousScope) {
    if (!value) {
      return { promptScope: previousScope || 'blueprint', promptName: '' };
    }
    const [promptScope, promptName] = value.split(':', 2);
    return {
      promptScope: promptScope === 'instance' ? 'instance' : 'blueprint',
      promptName: promptName || '',
    };
  }

  function toEditorRow(row) {
    return {
      key: row.key,
      sourceIndex: row.source_index == null ? null : row.source_index,
      id: row.id,
      promptScope: row.prompt_scope,
      promptName: row.prompt_name,
      enabled: row.enabled,
      arguments: [...row.arguments],
      scheduleMode: row.schedule.mode,
      scheduleAmount: row.schedule.amount,
      scheduleUnit: row.schedule.unit,
      scheduleTime: row.schedule.time,
      recoveryMode: row.recovery.mode,
      recoveryAmount: row.recovery.amount,
      recoveryUnit: row.recovery.unit,
      memoryScope: row.memory.scope,
      memoryChannel: row.memory.channel,
      detailsOpen: true,
    };
  }

  function createBlankRow() {
    return {
      key: nextKey(),
      sourceIndex: null,
      id: '',
      promptScope: 'blueprint',
      promptName: '',
      enabled: true,
      arguments: [],
      scheduleMode: 'every',
      scheduleAmount: '',
      scheduleUnit: 'd',
      scheduleTime: '',
      recoveryMode: 'default',
      recoveryAmount: '',
      recoveryUnit: 'h',
      memoryScope: 'inherit',
      memoryChannel: '',
      detailsOpen: true,
    };
  }

  const baseline = {
    routines: (initial.baseline?.draft?.routines || []).map(toEditorRow),
  };
  const draft = {
    routines: (initial.draft?.draft?.routines || []).map(toEditorRow),
  };

  for (const row of [...baseline.routines, ...draft.routines]) {
    const match = String(row.key).match(/(\d+)$/);
    if (match) {
      counter = Math.max(counter, Number(match[1]));
    }
  }

  function serializeEditorState(state) {
    return JSON.stringify({
      routines: state.routines.map((row) => ({
        key: row.key,
        sourceIndex: row.sourceIndex,
        id: row.id,
        promptScope: row.promptScope,
        promptName: row.promptName,
        enabled: row.enabled,
        arguments: [...row.arguments],
        scheduleMode: row.scheduleMode,
        scheduleAmount: row.scheduleAmount,
        scheduleUnit: row.scheduleUnit,
        scheduleTime: row.scheduleTime,
        recoveryMode: row.recoveryMode,
        recoveryAmount: row.recoveryAmount,
        recoveryUnit: row.recoveryUnit,
        memoryScope: row.memoryScope,
        memoryChannel: row.memoryChannel,
      })),
    });
  }

  const baselineEditorSerialized = serializeEditorState(baseline);

  function memoryLabel(row) {
    if (row.memoryScope === 'inherit') {
      return initial.inherited_memory_label || 'Inherit from agent';
    }
    if (row.memoryScope === 'channel') {
      const channels = new Map(initial.choices.channels || []);
      return `Channel: ${channels.get(row.memoryChannel) || row.memoryChannel || 'Channel'}`;
    }
    return `${row.memoryScope.charAt(0).toUpperCase()}${row.memoryScope.slice(1)} memory`;
  }

  function recoveryLabel(row) {
    if (row.recoveryMode === 'default') return 'today';
    if (row.recoveryMode === 'duration') {
      return row.recoveryAmount ? `${row.recoveryAmount}${row.recoveryUnit}` : 'not set';
    }
    return row.recoveryMode;
  }

  function scheduleLabel(row) {
    if (row.scheduleMode === 'at') {
      return row.scheduleTime ? `at ${row.scheduleTime}` : 'not set';
    }
    return row.scheduleAmount ? `every ${row.scheduleAmount}${row.scheduleUnit}` : 'not set';
  }

  function summarizeLocal(state) {
    return state.routines.map((row) => ({
      key: row.key,
      source_index: row.sourceIndex,
      id: row.id,
      enabled: row.enabled,
      prompt_scope: row.promptScope,
      prompt_name: row.promptName,
      schedule: scheduleLabel(row),
      memory: memoryLabel(row),
      arguments: [...row.arguments],
      recovery: recoveryLabel(row),
    }));
  }

  function syncPromptSelect(select, row) {
    const groups = promptOptions(row);
    select.replaceChildren();
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = 'Choose a prompt';
    select.append(blank);
    for (const scope of ['blueprint', 'instance']) {
      const group = document.createElement('optgroup');
      group.label = PROMPT_SCOPE_LABEL[scope];
      for (const option of groups[scope]) {
        const node = document.createElement('option');
        node.value = `${option.scope}:${option.name}`;
        node.textContent = option.missing ? `${option.name} (missing)` : option.name;
        group.append(node);
      }
      select.append(group);
    }
    select.value = row.promptName ? `${row.promptScope}:${row.promptName}` : '';
  }

  function syncChannelSelect(select, row) {
    select.replaceChildren();
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = '';
    select.append(blank);
    for (const option of channelOptions(row)) {
      const node = document.createElement('option');
      node.value = option.key;
      node.textContent = option.missing ? `${option.label} (missing)` : option.label;
      select.append(node);
    }
    select.value = row.memoryChannel;
  }

  function savedSlot(section, value, fallback, withBadge) {
    section.replaceChildren();
    section.textContent = value || fallback;
    if (withBadge) {
      const badge = document.createElement('span');
      badge.className = 'saved-label';
      badge.textContent = 'Saved';
      section.append(document.createTextNode(' '), badge);
    }
  }

  function renderSummary(rows) {
    summaryRows = clone(rows);
    summaryBody.replaceChildren();
    if (rows.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'routine-empty-summary';
      empty.textContent = 'No routines yet.';
      summaryBody.append(empty);
      return;
    }
    for (const row of rows) {
      const fragment = summaryRowTemplate.content.cloneNode(true);
      const section = fragment.querySelector('[data-summary-row]');
      const summaryId = section.querySelector('[data-summary-id]');
      const state = section.querySelector('.routine-state');
      const note = section.querySelector('[data-original-id-note]');
      section.dataset.key = row.key;
      section.dataset.summaryKey = row.key;
      section.dataset.sourceIndex = row.source_index == null ? '' : String(row.source_index);
      summaryId.textContent = row.id || '(blank id)';
      state.textContent = row.enabled ? 'Enabled' : 'Disabled';
      state.dataset.enabled = row.enabled ? 'true' : 'false';
      section.querySelector('[data-summary-prompt]').textContent = row.prompt_name
        ? `${row.prompt_name} / ${PROMPT_SCOPE_LABEL[row.prompt_scope]}`
        : 'Not selected';
      section.querySelector('[data-summary-schedule]').textContent = row.schedule;
      section.querySelector('[data-summary-memory]').textContent = row.memory;
      section.querySelector('[data-summary-arguments]').textContent = row.arguments.length ? row.arguments.join(', ') : 'None';
      section.querySelector('[data-summary-recovery]').textContent = row.recovery;

      if (row.source_index == null) {
        savedSlot(section.querySelector('[data-saved-last]'), '', 'Not saved', false);
        savedSlot(section.querySelector('[data-saved-next]'), '', 'Not saved', false);
      } else {
        const saved = conflict ? null : savedStatus.get(row.source_index);
        savedSlot(section.querySelector('[data-saved-last]'), saved?.last_fired || '', 'Saved value unavailable', Boolean(saved));
        savedSlot(section.querySelector('[data-saved-next]'), saved?.next_due || '', 'Saved value unavailable', Boolean(saved));
      }

      const originalId = row.source_index == null ? '' : (originalIds[row.source_index] || '');
      if (originalId && originalId !== row.id) {
        note.hidden = false;
        note.textContent = '';
        note.append('Saved status belongs to ');
        const mono = document.createElement('span');
        mono.className = 'routine-mono';
        mono.textContent = originalId;
        note.append(mono, '.');
      } else {
        note.hidden = true;
      }
      summaryBody.append(fragment);
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

  function clearFieldErrors() {
    for (const row of list.querySelectorAll('[data-routine-row]')) {
      row.querySelector('[data-row-error]').hidden = true;
      row.querySelector('[data-row-error]').textContent = '';
      for (const error of row.querySelectorAll('[data-field-error]')) {
        error.hidden = true;
        error.textContent = '';
      }
      for (const input of row.querySelectorAll('input, select')) {
        input.removeAttribute('aria-invalid');
        input.removeAttribute('aria-describedby');
      }
    }
  }

  function fieldGroup(row, key) {
    if (key === 'id') return [row.querySelector('[data-field="id"]')];
    if (key === 'prompt') return [row.querySelector('[data-field="prompt"]')];
    if (key === 'schedule') return [...row.querySelectorAll('[data-field="schedule.mode"], [data-field="schedule.amount"], [data-field="schedule.unit"], [data-field="schedule.time"]')];
    if (key === 'memory') return [...row.querySelectorAll('[data-field="memory.scope"], [data-field="memory.channel"]')];
    if (key === 'recovery') return [...row.querySelectorAll('[data-field="recovery.mode"], [data-field="recovery.amount"], [data-field="recovery.unit"]')];
    if (key === 'arguments') return [...row.querySelectorAll('[data-argument-value]')];
    return [];
  }

  function issueKey(field) {
    if (!field) return null;
    if (field.endsWith('.id')) return 'id';
    if (field.includes('.prompt')) return 'prompt';
    if (field.includes('.schedule')) return 'schedule';
    if (field.includes('.memory')) return 'memory';
    if (field.includes('.recovery')) return 'recovery';
    if (field.includes('.arguments')) return 'arguments';
    return null;
  }

  function showWarnings(warnings) {
    currentWarnings = Array.isArray(warnings) ? warnings : [];
    renderMessages(warningsBox, currentWarnings.map((issue) => issue.message || 'Warning'));
  }

  function showIssues(issues) {
    currentIssues = Array.isArray(issues) ? issues : [];
    clearFieldErrors();
    const rows = [...list.querySelectorAll('[data-routine-row]')];
    const general = [];
    for (const issue of currentIssues) {
      const field = issue.field || '';
      if (issue.code === 'config-conflict') {
        conflict = true;
      }
      const match = field.match(/^routines\.(\d+)\.(.+)$/);
      if (!match) {
        general.push(issue.message || 'Invalid value.');
        continue;
      }
      const row = rows[Number(match[1])];
      if (!row) {
        general.push(issue.message || 'Invalid value.');
        continue;
      }
      const mapped = issueKey(field);
      if (!mapped) {
        const rowError = row.querySelector('[data-row-error]');
        rowError.hidden = false;
        rowError.textContent = issue.message || 'Invalid value.';
        continue;
      }
      const target = row.querySelector(`[data-field-error="${mapped}"]`);
      if (!target) {
        general.push(issue.message || 'Invalid value.');
        continue;
      }
      target.hidden = false;
      target.textContent = issue.message || 'Invalid value.';
      const group = fieldGroup(row, mapped);
      for (const input of group) {
        if (!input) continue;
        input.setAttribute('aria-invalid', 'true');
        input.setAttribute('aria-describedby', target.id || '');
      }
      if (mapped === 'arguments' || mapped === 'recovery') {
        const details = row.querySelector('[data-optional-details]');
        details.open = true;
      }
    }
    renderMessages(errorsBox, general);
  }

  function setSummaryState(message, tone, options = {}) {
    summaryStatus.textContent = message;
    summaryStatus.dataset.tone = tone;
    summaryStatus.hidden = !message;
    summaryBody.setAttribute('aria-busy', options.busy ? 'true' : 'false');
    summaryActions.hidden = !options.retry && !options.reload;
    retryPreviewButton.hidden = !options.retry;
    reloadButton.hidden = !options.reload;
  }

  function showPending() {
    setSummaryState('Updating preview', 'pending', { busy: true, retry: false, reload: false });
  }

  function showFailure(result) {
    const code = result.code || 'preview-unavailable';
    if (code === 'config-conflict') {
      conflict = true;
    }
    showIssues(result.issues || []);
    const message = code === 'config-conflict'
      ? 'Reload before saving routines again.'
      : code === 'validation-failed'
        ? 'Correct the highlighted fields.'
        : 'Preview unavailable';
    setSummaryState(message, 'error', { busy: false, retry: code === 'preview-unavailable', reload: conflict });
    updateActions();
  }

  function currentRow(key) {
    return draft.routines.find((row) => row.key === key) || null;
  }

  function focusRoutineAction(key, direction) {
    const row = list.querySelector(`[data-routine-row][data-key="${CSS.escape(key)}"]`);
    if (!row) return;
    const preferred = direction === 'up' ? row.querySelector('[data-move-up]') : row.querySelector('[data-move-down]');
    const alternate = direction === 'up' ? row.querySelector('[data-move-down]') : row.querySelector('[data-move-up]');
    if (preferred && !preferred.disabled) {
      preferred.focus();
      return;
    }
    if (alternate && !alternate.disabled) {
      alternate.focus();
      return;
    }
    row.querySelector('[data-remove-routine]')?.focus();
  }

  function captureFocus() {
    const active = document.activeElement;
    if (!(active instanceof HTMLElement)) return null;
    const row = active.closest('[data-routine-row]');
    if (!row) return null;
    const focus = {
      key: row.dataset.key,
      selector: '',
      index: null,
      start: null,
      end: null,
    };
    if (active.matches('[data-field]')) {
      focus.selector = `[data-field="${active.dataset.field}"]`;
    } else if (active.matches('[data-argument-value]')) {
      focus.selector = '[data-argument-value]';
      focus.index = Number(active.closest('[data-argument-row]')?.dataset.argumentIndex || 0);
    } else if (active.matches('[data-argument-action]')) {
      focus.selector = `[data-argument-action="${active.dataset.argumentAction}"]`;
      focus.index = Number(active.closest('[data-argument-row]')?.dataset.argumentIndex || 0);
    } else if (active.matches('[data-action]')) {
      focus.selector = `[data-action="${active.dataset.action}"]`;
    } else if (active.matches('[data-add-argument]')) {
      focus.selector = '[data-add-argument]';
    }
    if (active instanceof HTMLInputElement && typeof active.selectionStart === 'number') {
      focus.start = active.selectionStart;
      focus.end = active.selectionEnd;
    }
    return focus;
  }

  function restoreFocus(focus) {
    if (!focus || !focus.key) return;
    const row = list.querySelector(`[data-routine-row][data-key="${CSS.escape(focus.key)}"]`);
    if (!row) return;
    let target = null;
    if (focus.index != null) {
      const argRow = row.querySelector(`[data-argument-row][data-argument-index="${focus.index}"]`);
      target = argRow?.querySelector(focus.selector);
    } else if (focus.selector) {
      target = row.querySelector(focus.selector);
    }
    if (!(target instanceof HTMLElement)) return;
    target.focus();
    if (target instanceof HTMLInputElement && focus.start != null) {
      target.setSelectionRange(focus.start, focus.end ?? focus.start);
    }
  }

  function bindRowIds(article, row, index) {
    const token = `${String(row.key).replace(/[^a-zA-Z0-9_-]+/g, '-') || 'row'}-${index}`;
    const idInput = article.querySelector('[data-field="id"]');
    const promptSelect = article.querySelector('[data-field="prompt"]');
    const enabled = article.querySelector('[data-field="enabled"]');
    const scheduleModes = [...article.querySelectorAll('[data-field="schedule.mode"]')];
    const renameWarning = article.querySelector('[data-rename-warning]');
    const idError = article.querySelector('[data-field-error="id"]');
    const promptError = article.querySelector('[data-field-error="prompt"]');
    const scheduleError = article.querySelector('[data-field-error="schedule"]');
    const memoryError = article.querySelector('[data-field-error="memory"]');
    const argumentsError = article.querySelector('[data-field-error="arguments"]');
    const recoveryError = article.querySelector('[data-field-error="recovery"]');
    const rowError = article.querySelector('[data-row-error]');

    idInput.id = `routine-${token}-id`;
    promptSelect.id = `routine-${token}-prompt`;
    enabled.id = `routine-${token}-enabled`;
    for (const input of scheduleModes) {
      input.name = `routine-${token}-schedule-mode`;
    }
    article.querySelector('.routine-enabled').htmlFor = enabled.id;
    article.querySelectorAll('.routine-field > .routine-label')[0].htmlFor = idInput.id;
    article.querySelectorAll('.routine-field > .routine-label')[1].htmlFor = promptSelect.id;
    renameWarning.id = `routine-${token}-rename-warning`;
    idError.id = `routine-${token}-id-error`;
    promptError.id = `routine-${token}-prompt-error`;
    scheduleError.id = `routine-${token}-schedule-error`;
    memoryError.id = `routine-${token}-memory-error`;
    argumentsError.id = `routine-${token}-arguments-error`;
    recoveryError.id = `routine-${token}-recovery-error`;
    rowError.id = `routine-${token}-row-error`;
  }

  function renderArguments(article, row) {
    const argumentList = article.querySelector('[data-argument-list]');
    const empty = article.querySelector('[data-empty-arguments]');
    argumentList.replaceChildren();
    row.arguments.forEach((value, index) => {
      const fragment = argumentTemplate.content.cloneNode(true);
      const argRow = fragment.querySelector('[data-argument-row]');
      const input = argRow.querySelector('[data-argument-value]');
      argRow.dataset.argumentIndex = String(index);
      input.value = value;
      input.setAttribute('aria-label', `Argument ${index + 1}`);
      argRow.querySelector('[data-move-argument-up]').disabled = index === 0;
      argRow.querySelector('[data-move-argument-down]').disabled = index === row.arguments.length - 1;
      argumentList.append(fragment);
    });
    empty.hidden = row.arguments.length !== 0;
  }

  function updateRowDecorations(article, row, index, total) {
    article.dataset.key = row.key;
    article.dataset.sourceIndex = row.sourceIndex == null ? '' : String(row.sourceIndex);
    article.querySelector('[data-routine-ordinal]').textContent = String(index + 1).padStart(2, '0');
    article.querySelector('[data-field="enabled"]').checked = row.enabled;
    article.querySelector('[data-field="id"]').value = row.id;
    syncPromptSelect(article.querySelector('[data-field="prompt"]'), row);
    article.querySelector('[data-field="schedule.amount"]').value = row.scheduleAmount;
    article.querySelector('[data-field="schedule.unit"]').value = row.scheduleUnit;
    article.querySelector('[data-field="schedule.time"]').value = row.scheduleTime;
    for (const input of article.querySelectorAll('[data-field="schedule.mode"]')) {
      input.checked = input.value === row.scheduleMode;
    }
    article.querySelector('[data-schedule-every-fields]').hidden = row.scheduleMode !== 'every';
    article.querySelector('[data-schedule-at-fields]').hidden = row.scheduleMode !== 'at';
    article.querySelector('[data-field="memory.scope"]').value = row.memoryScope;
    syncChannelSelect(article.querySelector('[data-field="memory.channel"]'), row);
    article.querySelector('[data-memory-channel-wrap]').hidden = row.memoryScope !== 'channel';
    article.querySelector('[data-field="recovery.mode"]').value = row.recoveryMode;
    article.querySelector('[data-field="recovery.amount"]').value = row.recoveryAmount;
    article.querySelector('[data-field="recovery.unit"]').value = row.recoveryUnit;
    article.querySelector('[data-recovery-duration-wrap]').hidden = row.recoveryMode !== 'duration';
    article.querySelector('[data-optional-details]').open = row.detailsOpen;
    renderArguments(article, row);

    const originalId = row.sourceIndex == null ? '' : (originalIds[row.sourceIndex] || '');
    const renameWarning = article.querySelector('[data-rename-warning]');
    if (originalId && row.id !== originalId) {
      renameWarning.hidden = false;
      renameWarning.textContent = '';
      renameWarning.append('Changing this ID creates a different routine identity. Existing schedule markers and routine memory remain under ');
      const mono = document.createElement('span');
      mono.className = 'routine-mono';
      mono.textContent = originalId;
      renameWarning.append(mono, '; they are not migrated.');
    } else {
      renameWarning.hidden = true;
      renameWarning.textContent = '';
    }

    article.querySelector('[data-move-up]').disabled = index === 0;
    article.querySelector('[data-move-down]').disabled = index === total - 1;
  }

  function renderRows(state, focus = captureFocus()) {
    list.replaceChildren();
    state.routines.forEach((row, index) => {
      const fragment = rowTemplate.content.cloneNode(true);
      const article = fragment.querySelector('[data-routine-row]');
      bindRowIds(article, row, index);
      updateRowDecorations(article, row, index, state.routines.length);
      list.append(fragment);
    });
    emptyRoutines.hidden = state.routines.length !== 0;
    refreshIcons();
    restoreFocus(focus);
    showWarnings(currentWarnings);
    showIssues(currentIssues);
  }

  function syncRow(article, row) {
    row.enabled = article.querySelector('[data-field="enabled"]').checked;
    row.id = article.querySelector('[data-field="id"]').value;
    const prompt = parsePromptValue(article.querySelector('[data-field="prompt"]').value, row.promptScope);
    row.promptScope = prompt.promptScope;
    row.promptName = prompt.promptName;
    row.scheduleMode = article.querySelector('[data-field="schedule.mode"]:checked')?.value || 'every';
    row.scheduleAmount = article.querySelector('[data-field="schedule.amount"]').value;
    row.scheduleUnit = article.querySelector('[data-field="schedule.unit"]').value || 'd';
    row.scheduleTime = article.querySelector('[data-field="schedule.time"]').value;
    row.memoryScope = article.querySelector('[data-field="memory.scope"]').value || 'inherit';
    row.memoryChannel = article.querySelector('[data-field="memory.channel"]').value;
    row.recoveryMode = article.querySelector('[data-field="recovery.mode"]').value || 'default';
    row.recoveryAmount = article.querySelector('[data-field="recovery.amount"]').value;
    row.recoveryUnit = article.querySelector('[data-field="recovery.unit"]').value || 'h';
    row.arguments = [...article.querySelectorAll('[data-argument-row]')].map((argRow) => argRow.querySelector('[data-argument-value]').value);
    row.detailsOpen = article.querySelector('[data-optional-details]').open;
  }

  function syncDraftFromDom() {
    const next = [];
    for (const article of list.querySelectorAll('[data-routine-row]')) {
      const row = clone(currentRow(article.dataset.key));
      syncRow(article, row);
      next.push(row);
    }
    draft.routines = next;
  }

  function collectDraft() {
    syncDraftFromDom();
    return {
      routines: draft.routines.map((row) => ({
        key: row.key,
        source_index: row.sourceIndex,
        id: row.id,
        prompt_scope: row.promptScope,
        prompt_name: row.promptName,
        enabled: row.enabled,
        arguments: [...row.arguments],
        schedule: row.scheduleMode === 'every'
          ? { mode: 'every', amount: row.scheduleAmount, unit: row.scheduleUnit, time: '' }
          : { mode: 'at', amount: '', unit: 'd', time: row.scheduleTime },
        recovery: row.recoveryMode === 'duration'
          ? { mode: 'duration', amount: row.recoveryAmount, unit: row.recoveryUnit }
          : { mode: row.recoveryMode, amount: '', unit: 'h' },
        memory: row.memoryScope === 'channel'
          ? { scope: 'channel', channel: row.memoryChannel }
          : { scope: row.memoryScope, channel: '' },
      })),
    };
  }

  function draftPayloadFromState(state) {
    return {
      routines: state.routines.map((row) => ({
        key: row.key,
        source_index: row.sourceIndex,
        id: row.id,
        prompt_scope: row.promptScope,
        prompt_name: row.promptName,
        enabled: row.enabled,
        arguments: [...row.arguments],
        schedule: row.scheduleMode === 'every'
          ? { mode: 'every', amount: row.scheduleAmount, unit: row.scheduleUnit, time: '' }
          : { mode: 'at', amount: '', unit: 'd', time: row.scheduleTime },
        recovery: row.recoveryMode === 'duration'
          ? { mode: 'duration', amount: row.recoveryAmount, unit: row.recoveryUnit }
          : { mode: row.recoveryMode, amount: '', unit: 'h' },
        memory: row.memoryScope === 'channel'
          ? { scope: 'channel', channel: row.memoryChannel }
          : { scope: row.memoryScope, channel: '' },
      })),
    };
  }

  function updateActions() {
    syncDraftFromDom();
    dirty = serializeEditorState(draft) !== baselineEditorSerialized;
    const currentDraft = JSON.stringify(collectDraft());
    const hasBlankArgument = draft.routines.some((row) => row.arguments.some((value) => value === ''));
    saveButton.disabled = !dirty || conflict || submitting || hasBlankArgument || validVersion !== version || currentDraft !== validatedDraft;
    root.dataset.dirty = dirty ? 'true' : 'false';
  }

  function localIssuesForDraft(payload) {
    const issues = [];
    payload.routines.forEach((row, index) => {
      const prefix = `routines.${index}`;
      if (row.id.trim() === '') {
        issues.push({ field: `${prefix}.id`, message: 'Routine id is required.' });
      }
      if (row.prompt_name.trim() === '') {
        issues.push({ field: `${prefix}.prompt`, message: 'Prompt name is required.' });
      }
      if (row.schedule.mode === 'every' && row.schedule.amount === '') {
        issues.push({ field: `${prefix}.schedule`, message: 'Schedule interval amount is required.' });
      }
      if (row.schedule.mode === 'at' && row.schedule.time === '') {
        issues.push({ field: `${prefix}.schedule`, message: 'Schedule time is required.' });
      }
      if (row.memory.scope === 'channel' && row.memory.channel.trim() === '') {
        issues.push({ field: `${prefix}.memory`, message: 'Channel memory requires a channel.' });
      }
      if (row.recovery.mode === 'duration' && row.recovery.amount === '') {
        issues.push({ field: `${prefix}.recovery`, message: 'Recovery duration amount is required.' });
      }
      if (row.arguments.some((value) => value === '')) {
        issues.push({ field: `${prefix}.arguments`, message: 'Routine arguments must be non-empty strings.' });
      }
    });
    return issues;
  }

  function requestPreview(requestVersion, requestDraft) {
    const localIssues = localIssuesForDraft(requestDraft);
    if (localIssues.length > 0) {
      controller = null;
      showIssues(localIssues);
      setSummaryState('Correct the highlighted fields.', 'warning', { busy: false, retry: false, reload: conflict });
      updateActions();
      return;
    }
    controller = new AbortController();
    fetch(initial.preview_url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify({
        revision: initial.draft.revision,
        draft_version: requestVersion,
        draft: requestDraft,
      }),
    }).then(async (response) => {
      const result = await response.json();
      if (requestVersion !== version || result.draft_version !== requestVersion) {
        return;
      }
      controller = null;
      if (!response.ok) {
        showFailure(result);
        return;
      }
      conflict = false;
      currentIssues = [];
      renderSummary(result.rows || []);
      showWarnings(result.warnings || []);
      showIssues([]);
      setSummaryState('', 'pending', { busy: false, retry: false, reload: false });
      validVersion = requestVersion;
      validatedDraft = JSON.stringify(requestDraft);
      updateActions();
    }).catch((error) => {
      if (error.name === 'AbortError' || requestVersion !== version) {
        return;
      }
      controller = null;
      showFailure({ code: 'preview-unavailable', issues: [] });
    });
  }

  function markChanged() {
    version += 1;
    validVersion = -1;
    validatedDraft = null;
    controller?.abort();
    clearTimeout(timer);
    showPending();
    updateActions();
    const requestVersion = version;
    timer = window.setTimeout(() => requestPreview(requestVersion, collectDraft()), 250);
  }

  function discardDraft() {
    if (conflict) {
      if (window.confirm('Discard the stale routines draft and reload the current configuration?')) {
        window.location.reload();
      }
      return;
    }
    controller?.abort();
    clearTimeout(timer);
    submitting = false;
    currentIssues = [];
    currentWarnings = initial.warnings || [];
    draft.routines = baseline.routines.map((row) => clone(row));
    version += 1;
    validVersion = version;
    validatedDraft = JSON.stringify(draftPayloadFromState(draft));
    renderRows(draft, null);
    renderSummary(baselineSummaryRows.length ? baselineSummaryRows : summarizeLocal(baseline));
    showWarnings(currentWarnings);
    showIssues([]);
    setSummaryState('', 'pending', { busy: false, retry: false, reload: false });
    updateActions();
    list.querySelector('[data-routine-row] [data-field="id"]')?.focus();
  }

  function addRoutine() {
    syncDraftFromDom();
    draft.routines.push(createBlankRow());
    currentIssues = [];
    renderRows(draft, null);
    renderSummary(summarizeLocal(draft));
    list.querySelector('[data-routine-row]:last-child [data-field="id"]')?.focus();
    markChanged();
  }

  function moveRoutine(key, direction) {
    syncDraftFromDom();
    const index = draft.routines.findIndex((row) => row.key === key);
    const destination = index + direction;
    if (index < 0 || destination < 0 || destination >= draft.routines.length) return;
    const [row] = draft.routines.splice(index, 1);
    draft.routines.splice(destination, 0, row);
    renderRows(draft, null);
    renderSummary(summarizeLocal(draft));
    markChanged();
    focusRoutineAction(key, direction < 0 ? 'up' : 'down');
  }

  function removeRoutine(key) {
    syncDraftFromDom();
    const index = draft.routines.findIndex((row) => row.key === key);
    if (index < 0) return;
    const focusKey = draft.routines[index + 1]?.key || draft.routines[index - 1]?.key || null;
    draft.routines.splice(index, 1);
    renderRows(draft, null);
    renderSummary(summarizeLocal(draft));
    markChanged();
    if (focusKey) {
      list.querySelector(`[data-routine-row][data-key="${CSS.escape(focusKey)}"] [data-remove-routine]`)?.focus();
    } else {
      addRoutineButton.focus();
    }
  }

  function changeArgument(key, index, change) {
    syncDraftFromDom();
    const row = currentRow(key);
    if (!row) return;
    if (change === 'add') {
      row.arguments.push('');
      row.detailsOpen = true;
      renderRows(draft, null);
      renderSummary(summarizeLocal(draft));
      markChanged();
      list.querySelector(`[data-routine-row][data-key="${CSS.escape(key)}"] [data-argument-row]:last-child [data-argument-value]`)?.focus();
      return;
    }
    if (change === 'remove') {
      row.arguments.splice(index, 1);
    }
    if (change === 'up' && index > 0) {
      [row.arguments[index - 1], row.arguments[index]] = [row.arguments[index], row.arguments[index - 1]];
    }
    if (change === 'down' && index < row.arguments.length - 1) {
      [row.arguments[index + 1], row.arguments[index]] = [row.arguments[index], row.arguments[index + 1]];
    }
    renderRows(draft, null);
    renderSummary(summarizeLocal(draft));
    markChanged();
  }

  renderRows(draft, null);
  renderSummary(summaryRows.length ? summaryRows : summarizeLocal(draft));
  showWarnings(currentWarnings);
  showIssues(currentIssues);

  if (!conflict && serializeEditorState(draft) === baselineEditorSerialized && currentIssues.length === 0) {
    validVersion = version;
    validatedDraft = JSON.stringify(draftPayloadFromState(draft));
    setSummaryState('', 'pending', { busy: false, retry: false, reload: false });
  } else {
    setSummaryState(conflict ? 'Reload before saving routines again.' : 'Saved summary shown', conflict ? 'error' : 'warning', { busy: false, retry: false, reload: conflict });
  }
  updateActions();

  addRoutineButton.addEventListener('click', () => {
    addRoutine();
  });

  list.addEventListener('click', (event) => {
    const row = event.target.closest('[data-routine-row]');
    if (!row) return;
    const key = row.dataset.key;
    if (event.target.closest('[data-move-up]')) {
      moveRoutine(key, -1);
      return;
    }
    if (event.target.closest('[data-move-down]')) {
      moveRoutine(key, 1);
      return;
    }
    if (event.target.closest('[data-remove-routine]')) {
      removeRoutine(key);
      return;
    }
    if (event.target.closest('[data-add-argument]')) {
      changeArgument(key, 0, 'add');
      return;
    }
    const argumentRow = event.target.closest('[data-argument-row]');
    if (argumentRow && event.target.closest('[data-remove-argument]')) {
      changeArgument(key, Number(argumentRow.dataset.argumentIndex), 'remove');
      return;
    }
    if (argumentRow && event.target.closest('[data-move-argument-up]')) {
      changeArgument(key, Number(argumentRow.dataset.argumentIndex), 'up');
      return;
    }
    if (argumentRow && event.target.closest('[data-move-argument-down]')) {
      changeArgument(key, Number(argumentRow.dataset.argumentIndex), 'down');
    }
  });

  list.addEventListener('toggle', (event) => {
    if (!event.target.matches('[data-optional-details]')) return;
    syncDraftFromDom();
    const row = currentRow(event.target.closest('[data-routine-row]').dataset.key);
    if (row) {
      row.detailsOpen = event.target.open;
      updateActions();
    }
  }, true);

  list.addEventListener('input', (event) => {
    const row = event.target.closest('[data-routine-row]');
    if (!row) return;
    syncDraftFromDom();
    const state = currentRow(row.dataset.key);
    if (!state) return;
    updateRowDecorations(row, state, [...list.querySelectorAll('[data-routine-row]')].indexOf(row), draft.routines.length);
    renderSummary(summarizeLocal(draft));
    markChanged();
  });

  list.addEventListener('change', (event) => {
    const row = event.target.closest('[data-routine-row]');
    if (!row) return;
    syncDraftFromDom();
    const state = currentRow(row.dataset.key);
    if (!state) return;
    updateRowDecorations(row, state, [...list.querySelectorAll('[data-routine-row]')].indexOf(row), draft.routines.length);
    renderSummary(summarizeLocal(draft));
    markChanged();
  });

  form.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target.matches('input[type="text"], input[type="time"]')) {
      event.preventDefault();
    }
  });

  discardButton.addEventListener('click', () => {
    discardDraft();
  });

  retryPreviewButton.addEventListener('click', () => {
    validVersion = -1;
    validatedDraft = null;
    showPending();
    updateActions();
    requestPreview(version, collectDraft());
  });

  reloadButton.addEventListener('click', () => {
    window.location.reload();
  });

  form.addEventListener('submit', (event) => {
    const current = collectDraft();
    const serialized = JSON.stringify(current);
    if (conflict || submitting || validVersion !== version || serialized !== validatedDraft) {
      event.preventDefault();
      if (!conflict && !submitting) {
        markChanged();
      }
      return;
    }
    payloadInput.value = JSON.stringify({
      revision: initial.draft.revision,
      draft_version: version,
      draft: current,
    });
    submitting = true;
    updateActions();
  });

  window.addEventListener('beforeunload', (event) => {
    if (!dirty || submitting) return;
    event.preventDefault();
    event.returnValue = '';
  });
})();