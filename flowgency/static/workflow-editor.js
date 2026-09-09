(() => {
  const root = document.querySelector('[data-workflow-editor-root]');
  const initialNode = document.getElementById('workflow-editor-data');
  if (!root || !initialNode || !initialNode.textContent) return;

  const clone = (value) => structuredClone(value);
  const initialState = JSON.parse(initialNode.textContent);
  const fieldKinds = {
    text: 'Text',
    number: 'Number',
    boolean: 'Boolean',
    artifact: 'Artifact',
  };
  const operators = {
    equals: 'Equals',
    not_equals: 'Not equal',
    is_present: 'Is present',
  };
  const uiStateKey = 'flowgency.workflow-editor.ui';
  const pendingDraftKey = 'flowgency.workflow-editor.pending';

  let baseline = clone(initialState.baseline);
  let draft = clone(initialState.draft);
  let draftVersion = Number(initialState.draft_version || 0);
  let issues = Array.isArray(initialState.issues) ? clone(initialState.issues) : [];
  let warning = typeof initialState.warning === 'string' ? initialState.warning : '';
  let activeTab = 'overview';
  let activeTransitionRef = null;
  let previewRequestId = 0;
  let latestPreviewRequestId = 0;
  let previewTimer = 0;
  let menuState = null;

  const form = root.querySelector('[data-editor-form]');
  const payloadInput = root.querySelector('[data-editor-payload]');
  const titleNode = root.querySelector('[data-editor-title]');
  const pageHeading = document.querySelector('main h1');
  const saveButton = root.querySelector('[data-editor-save]');
  const revertButton = root.querySelector('[data-editor-revert]');
  const statusNode = root.querySelector('[data-editor-status]');
  const warningNode = root.querySelector('[data-editor-warning]');
  const issuesNode = root.querySelector('[data-editor-issues]');
  const transitionNav = root.querySelector('[data-editor-transition-nav]');
  const mobileTransition = root.querySelector('[data-mobile-transition]');
  const previewUrl = form?.getAttribute('data-preview-url') || '';
  const saveUrl = form?.getAttribute('data-save-url') || '';
  const tabButtons = Array.from(root.querySelectorAll('[data-editor-tab]'));
  const tabPanels = new Map(
    Array.from(root.querySelectorAll('[data-editor-panel]')).map((panel) => [panel.dataset.editorPanel, panel]),
  );

  function makeKey(prefix) {
    return `${prefix}-${Math.random().toString(16).slice(2, 10)}${Date.now().toString(16)}`;
  }

  function normalizeDraft(value) {
    if (Array.isArray(value)) return value.map(normalizeDraft);
    if (value && typeof value === 'object') {
      return Object.fromEntries(
        Object.entries(value)
          .filter(([key]) => key !== 'key')
          .map(([key, nested]) => [key, normalizeDraft(nested)]),
      );
    }
    return value;
  }

  function sameDraft(left, right) {
    return JSON.stringify(normalizeDraft(left)) === JSON.stringify(normalizeDraft(right));
  }

  function isDirty() {
    return !sameDraft(draft, baseline);
  }

  function stateRef(row) {
    return row.existing_state_id || row.key;
  }

  function fieldRef(row) {
    return row.existing_field_id || row.key;
  }

  function transitionRef(row) {
    return row.existing_transition_id || row.key;
  }

  function criterionRef(row) {
    return row.existing_criterion_id || row.key;
  }

  function useRef(row) {
    return row.existing_field_id || row.draft_field_key;
  }

  function fieldByRef(ref) {
    return draft.fields.find((row) => fieldRef(row) === ref) || null;
  }

  function stateByRef(ref) {
    return draft.states.find((row) => stateRef(row) === ref) || null;
  }

  function stateLabel(ref) {
    return stateByRef(ref)?.name || 'Unknown state';
  }

  function fieldOptions() {
    return draft.fields.map((row) => ({ value: fieldRef(row), label: row.label || 'Untitled field', type: row.type }));
  }

  function stateOptions() {
    return draft.states.map((row) => ({ value: stateRef(row), label: row.name || 'Untitled state' }));
  }

  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function readPendingDraft(pathname = window.location.pathname) {
    try {
      const raw = sessionStorage.getItem(pendingDraftKey);
      if (!raw) return null;
      const pending = JSON.parse(raw);
      if (!pending || typeof pending !== 'object' || pending.path !== pathname || !pending.draft) return null;
      sessionStorage.removeItem(pendingDraftKey);
      return pending.draft;
    } catch {
      return null;
    }
  }

  function persistPendingDraft(pathname, nextDraft) {
    try {
      sessionStorage.setItem(
        pendingDraftKey,
        JSON.stringify({
          path: pathname,
          draft: nextDraft,
        }),
      );
    } catch {
      // Ignore session storage failures.
    }
  }

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'htmlFor') node.htmlFor = value;
      else if (key === 'checked') node.checked = Boolean(value);
      else if (key === 'disabled') node.disabled = Boolean(value);
      else if (key === 'value') node.value = value;
      else if (key === 'type') node.type = value;
      else node.setAttribute(key, value);
    }
    const list = Array.isArray(children) ? children : [children];
    for (const child of list) {
      if (child === undefined || child === null || child === false) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function icon(name) {
    return el('i', { 'data-lucide': name });
  }

  function setStatus(text, dirtyFlag) {
    if (!statusNode) return;
    statusNode.textContent = text;
    statusNode.dataset.dirty = dirtyFlag ? 'true' : 'false';
    if (saveButton) saveButton.disabled = !dirtyFlag;
    if (revertButton) revertButton.disabled = !dirtyFlag;
  }

  function refreshTitles() {
    const title = String(draft.name || 'New workflow').trim() || 'New workflow';
    if (titleNode) titleNode.textContent = title;
    if (pageHeading) pageHeading.textContent = title;
  }

  function showWarning(text) {
    if (!warningNode) return;
    warningNode.hidden = !text;
    warningNode.textContent = text || '';
  }

  function showIssues(rows) {
    if (!issuesNode) return;
    clearNode(issuesNode);
    if (!rows || rows.length === 0) {
      issuesNode.hidden = true;
      return;
    }
    issuesNode.hidden = false;
    issuesNode.append(el('strong', { text: 'Cannot save workflow' }));
    const list = el('ul');
    for (const issue of rows) {
      const detail = [issue.message || 'Invalid value.', issue.field ? `Field: ${issue.field}.` : '', issue.hint || ''].filter(Boolean).join(' ');
      list.append(el('li', { text: detail }));
    }
    issuesNode.append(list);
  }

  function loadUiState() {
    try {
      const raw = sessionStorage.getItem(uiStateKey);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved && typeof saved === 'object') {
        if (typeof saved.activeTab === 'string') activeTab = saved.activeTab;
        if (typeof saved.activeTransitionRef === 'string') activeTransitionRef = saved.activeTransitionRef;
      }
    } catch {
      // Ignore session storage failures.
    }
  }

  function persistUiState() {
    try {
      sessionStorage.setItem(uiStateKey, JSON.stringify({ activeTab, activeTransitionRef }));
    } catch {
      // Ignore session storage failures.
    }
  }

  function ensureActiveTransition() {
    const refs = draft.transitions.map((row) => transitionRef(row));
    if (!refs.length) {
      activeTransitionRef = null;
      return;
    }
    if (activeTransitionRef && refs.includes(activeTransitionRef)) return;
    activeTransitionRef = refs[0];
  }

  function currentTransitionIndex() {
    ensureActiveTransition();
    if (!activeTransitionRef) return -1;
    return draft.transitions.findIndex((row) => transitionRef(row) === activeTransitionRef);
  }

  function currentTransition() {
    const index = currentTransitionIndex();
    return index >= 0 ? draft.transitions[index] : null;
  }

  function localStateUsage(ref) {
    return draft.transitions.some((row) => row.from_state_id === ref || row.to_state_id === ref);
  }

  function setUseReference(row, ref) {
    const field = fieldByRef(ref);
    if (!field) return;
    if (field.existing_field_id) {
      row.existing_field_id = field.existing_field_id;
      row.draft_field_key = null;
    } else {
      row.existing_field_id = null;
      row.draft_field_key = field.key;
    }
  }

  function setRuleField(rule, ref) {
    setUseReference(rule, ref);
    const field = fieldByRef(ref);
    if (!field) return;
    if (rule.operator === 'is_present') {
      rule.value = null;
      return;
    }
    if (field.type === 'boolean') {
      rule.value = Boolean(rule.value);
      return;
    }
    if (field.type === 'number') {
      rule.value = typeof rule.value === 'number' ? rule.value : 0;
      return;
    }
    if (rule.value === null || rule.value === undefined) rule.value = '';
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = window.setTimeout(() => {
      void runPreview();
    }, 120);
  }

  function refreshAfterChange() {
    issues = [];
    warning = '';
    persistUiState();
    render();
    schedulePreview();
  }

  function buildPayload() {
    draftVersion += 1;
    const payload = JSON.stringify({
      expected_revision: initialState.expected_revision,
      expected_digest: initialState.expected_digest,
      draft_version: draftVersion,
      draft,
    });
    if (payloadInput) payloadInput.value = payload;
    return payload;
  }

  async function submit(url) {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
      },
      body: new URLSearchParams({ payload: buildPayload() }),
      redirect: 'follow',
    });
    if (response.redirected) return { response, redirected: true };
    const data = await response.json();
    return { response, redirected: false, data };
  }

  async function readRedirectEditorState(response) {
    try {
      const html = await response.text();
      if (!html) return null;
      const documentNode = new DOMParser().parseFromString(html, 'text/html');
      const payloadNode = documentNode.getElementById('workflow-editor-data');
      if (!payloadNode || !payloadNode.textContent) return null;
      return JSON.parse(payloadNode.textContent);
    } catch {
      return null;
    }
  }

  function applyRedirectState(redirectedState, nextDraft = null) {
    baseline = clone(redirectedState.baseline);
    draft = clone(nextDraft || redirectedState.draft);
    initialState.expected_revision = redirectedState.expected_revision;
    initialState.expected_digest = redirectedState.expected_digest;
    draftVersion = Number(redirectedState.draft_version || draftVersion);
    if (typeof redirectedState.workflow_count === 'number') initialState.workflow_count = redirectedState.workflow_count;
    initialNode.textContent = JSON.stringify({
      ...redirectedState,
      draft: clone(draft),
    });
  }

  function rebaseSavedReferences(savedRows, redirectedRows, refForRow) {
    const refs = new Map();
    const limit = Math.min(savedRows.length, redirectedRows.length);
    for (let index = 0; index < limit; index += 1) {
      refs.set(refForRow(savedRows[index]), refForRow(redirectedRows[index]));
    }
    return refs;
  }

  function rebaseFieldUse(row, refs) {
    const ref = useRef(row);
    const nextRef = refs.get(ref);
    if (!nextRef) return row;
    return {
      ...row,
      existing_field_id: nextRef,
      draft_field_key: null,
    };
  }

  function rebaseTransitionDraft(savedDraft, currentDraft, redirectedDraft) {
    const stateRefs = rebaseSavedReferences(savedDraft.states, redirectedDraft.states, stateRef);
    const fieldRefs = rebaseSavedReferences(savedDraft.fields, redirectedDraft.fields, fieldRef);
    const transitionRefs = rebaseSavedReferences(savedDraft.transitions, redirectedDraft.transitions, transitionRef);
    const criterionRefs = new Map();
    const transitionLimit = Math.min(savedDraft.transitions.length, redirectedDraft.transitions.length);
    for (let index = 0; index < transitionLimit; index += 1) {
      const savedTransition = savedDraft.transitions[index];
      const redirectedTransition = redirectedDraft.transitions[index];
      const limit = Math.min(savedTransition.criteria.length, redirectedTransition.criteria.length);
      for (let criterionIndex = 0; criterionIndex < limit; criterionIndex += 1) {
        criterionRefs.set(
          criterionRef(savedTransition.criteria[criterionIndex]),
          criterionRef(redirectedTransition.criteria[criterionIndex]),
        );
      }
    }

    return {
      ...currentDraft,
      states: currentDraft.states.map((row) => ({
        ...row,
        existing_state_id: stateRefs.get(stateRef(row)) || row.existing_state_id,
      })),
      fields: currentDraft.fields.map((row) => ({
        ...row,
        existing_field_id: fieldRefs.get(fieldRef(row)) || row.existing_field_id,
      })),
      transitions: currentDraft.transitions.map((row) => ({
        ...row,
        existing_transition_id: transitionRefs.get(transitionRef(row)) || row.existing_transition_id,
        from_state_id: stateRefs.get(row.from_state_id) || row.from_state_id,
        to_state_id: stateRefs.get(row.to_state_id) || row.to_state_id,
        inputs: row.inputs.map((item) => rebaseFieldUse(item, fieldRefs)),
        outputs: row.outputs.map((item) => rebaseFieldUse(item, fieldRefs)),
        preconditions: row.preconditions.map((item) => rebaseFieldUse(item, fieldRefs)),
        criteria: row.criteria.map((item) => ({
          ...item,
          existing_criterion_id: criterionRefs.get(criterionRef(item)) || item.existing_criterion_id,
        })),
      })),
    };
  }

  async function runPreview() {
    if (!previewUrl) return;
    const requestId = ++previewRequestId;
    const draftSnapshot = clone(draft);
    const result = await submit(previewUrl);
    if (result.redirected) return;
    if (requestId < latestPreviewRequestId) return;
    if (!sameDraft(draft, draftSnapshot)) return;
    latestPreviewRequestId = requestId;
    issues = Array.isArray(result.data.issues) ? result.data.issues : [];
    warning = result.response.ok ? '' : 'Correct the highlighted issues before saving.';
    if (typeof result.data.workflow_count === 'number') initialState.workflow_count = result.data.workflow_count;
    showIssues(issues);
    showWarning(warning);
    setStatus(isDirty() ? 'Unsaved changes' : 'Saved', isDirty());
  }

  async function saveDraft() {
    if (!saveUrl) return;
    clearTimeout(previewTimer);
    setStatus('Saving…', true);
    const savedTransitionIndex = currentTransitionIndex();
    const savedDraft = clone(draft);
    persistUiState();
    const result = await submit(saveUrl);
    if (result.redirected) {
      const redirectedState = await readRedirectEditorState(result.response);
      const redirectedTransitions = Array.isArray(redirectedState?.draft?.transitions)
        ? redirectedState.draft.transitions
        : [];
      let nextDraft = null;
      if (redirectedState && !sameDraft(draft, savedDraft)) {
        nextDraft = rebaseTransitionDraft(savedDraft, draft, redirectedState.draft);
      }
      if (savedTransitionIndex >= 0 && redirectedTransitions[savedTransitionIndex]) {
        activeTransitionRef = transitionRef(redirectedTransitions[savedTransitionIndex]);
      } else if (!redirectedTransitions.length) {
        activeTransitionRef = null;
      }
      const redirectUrl = new URL(result.response.url);
      if (redirectedState && redirectUrl.pathname === window.location.pathname && redirectUrl.search === window.location.search) {
        applyRedirectState(redirectedState, nextDraft);
        issues = [];
        warning = '';
        showIssues(issues);
        showWarning(warning);
        setStatus(isDirty() ? 'Unsaved changes' : 'Saved', isDirty());
        persistUiState();
        render();
        return;
      }
      if (nextDraft) persistPendingDraft(redirectUrl.pathname, nextDraft);
      baseline = clone(nextDraft || draft);
      issues = [];
      warning = '';
      showIssues(issues);
      showWarning(warning);
      setStatus('Saved', false);
      persistUiState();
      window.location.assign(result.response.url);
      return;
    }
    issues = Array.isArray(result.data.issues) ? result.data.issues : [];
    warning = result.response.status === 409 ? 'Reload before saving again.' : 'Correct the highlighted issues before saving.';
    showIssues(issues);
    showWarning(warning);
    setStatus('Unsaved changes', true);
  }

  function revertDraft() {
    draft = clone(baseline);
    issues = [];
    warning = '';
    menuState = null;
    ensureActiveTransition();
    persistUiState();
    render();
  }

  function addState() {
    const row = {
      key: makeKey('ds'),
      existing_state_id: null,
      name: 'New state',
      color: '#9ca3af',
      initial: draft.states.length === 0,
    };
    if (!draft.states.some((item) => item.initial)) row.initial = true;
    draft.states.push(row);
    refreshAfterChange();
  }

  function moveState(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= draft.states.length) return;
    const [row] = draft.states.splice(index, 1);
    draft.states.splice(target, 0, row);
    refreshAfterChange();
  }

  function deleteState(index) {
    const row = draft.states[index];
    if (!row) return;
    if (localStateUsage(stateRef(row))) return;
    draft.states.splice(index, 1);
    if (!draft.states.some((item) => item.initial) && draft.states[0]) draft.states[0].initial = true;
    refreshAfterChange();
  }

  function ensureField(label = 'New field', type = 'text') {
    const row = {
      key: makeKey('df'),
      existing_field_id: null,
      label,
      type,
    };
    draft.fields.push(row);
    return row;
  }

  function addTransition() {
    const states = stateOptions();
    const first = states[0]?.value || '';
    const second = states[1]?.value || first;
    const row = {
      key: makeKey('dt'),
      existing_transition_id: null,
      name: 'New transition',
      from_state_id: first,
      to_state_id: second,
      inputs: [],
      outputs: [],
      preconditions: [],
      criteria: [],
    };
    draft.transitions.push(row);
    activeTab = 'transitions';
    activeTransitionRef = transitionRef(row);
    refreshAfterChange();
  }

  function deleteTransition(index) {
    if (index < 0 || index >= draft.transitions.length) return;
    draft.transitions.splice(index, 1);
    ensureActiveTransition();
    refreshAfterChange();
  }

  function addUse(kind, options = {}) {
    const transition = currentTransition();
    if (!transition) return;
    const field = options.ref ? fieldByRef(options.ref) : ensureField(options.label || 'New field', options.type || 'text');
    if (!field) return;
    const row = { existing_field_id: null, draft_field_key: null, required: true };
    setUseReference(row, fieldRef(field));
    transition[kind].push(row);
    menuState = null;
    refreshAfterChange();
  }

  function removeUse(kind, index) {
    const transition = currentTransition();
    if (!transition) return;
    transition[kind].splice(index, 1);
    refreshAfterChange();
  }

  function addPrecondition() {
    const transition = currentTransition();
    if (!transition) return;
    const firstField = draft.fields[0] || ensureField();
    const row = {
      existing_field_id: null,
      draft_field_key: null,
      operator: 'equals',
      value: '',
    };
    setRuleField(row, fieldRef(firstField));
    transition.preconditions.push(row);
    refreshAfterChange();
  }

  function removePrecondition(index) {
    const transition = currentTransition();
    if (!transition) return;
    transition.preconditions.splice(index, 1);
    refreshAfterChange();
  }

  function addCriterion() {
    const transition = currentTransition();
    if (!transition) return;
    transition.criteria.push({ key: makeKey('dc'), existing_criterion_id: null, description: '' });
    refreshAfterChange();
  }

  function removeCriterion(index) {
    const transition = currentTransition();
    if (!transition) return;
    transition.criteria.splice(index, 1);
    refreshAfterChange();
  }

  function bindTextInput(node, getter, setter) {
    node.value = getter() ?? '';
    node.addEventListener('input', () => {
      setter(node.value);
      refreshAfterChange();
    });
  }

  function bindCheckbox(node, getter, setter) {
    node.checked = Boolean(getter());
    node.addEventListener('change', () => {
      setter(node.checked);
      refreshAfterChange();
    });
  }

  function bindSelect(node, getter, setter) {
    node.value = getter() ?? '';
    node.addEventListener('change', () => {
      setter(node.value);
      refreshAfterChange();
    });
  }

  function appendOptions(select, rows, selectedValue) {
    rows.forEach((row) => {
      const option = el('option', { value: row.value, text: row.label });
      if (row.value === selectedValue) option.selected = true;
      select.append(option);
    });
  }

  function renderMenu(kind, container) {
    const transition = currentTransition();
    if (!transition) return;
    const currentRef = transitionRef(transition);
    const isOpen = menuState && menuState.kind === kind && menuState.transitionRef === currentRef;
    const wrap = el('div', { class: 'wf-menu-wrap' });
    const button = el('button', { type: 'button', class: 'wf-icon-btn', 'aria-label': `Add ${kind === 'inputs' ? 'input' : 'output'}`, title: `Add ${kind === 'inputs' ? 'input' : 'output'}` }, icon('plus'));
    button.addEventListener('click', () => {
      menuState = isOpen ? null : { transitionRef: currentRef, kind, label: '', type: 'text' };
      render();
    });
    wrap.append(button);
    if (isOpen && menuState) {
      const menu = el('div', { class: 'wf-menu' });
      menu.append(el('div', { class: 'wf-menu-title', text: 'Create new field' }));
      const menuForm = el('div', { class: 'wf-menu-form' });
      const labelInput = el('input', { class: 'wf-input', type: 'text', value: menuState.label, 'aria-label': 'New field label' });
      labelInput.addEventListener('input', () => {
        menuState.label = labelInput.value;
      });
      const typeSelect = el('select', { class: 'wf-select', 'aria-label': 'New field type' });
      appendOptions(typeSelect, Object.entries(fieldKinds).map(([value, label]) => ({ value, label })), menuState.type);
      typeSelect.addEventListener('change', () => {
        menuState.type = typeSelect.value;
      });
      const createButton = el('button', { type: 'button', class: 'wf-btn' }, 'Create and add');
      createButton.addEventListener('click', () => {
        addUse(kind, { label: menuState.label.trim() || 'New field', type: menuState.type || 'text' });
      });
      menuForm.append(labelInput, typeSelect, el('div', { class: 'wf-menu-actions' }, createButton));
      menu.append(menuForm);

      menu.append(el('div', { class: 'wf-menu-title', text: 'Reuse existing field' }));
      const usedRefs = new Set(transition[kind].map((row) => useRef(row)));
      const reusable = fieldOptions().filter((row) => !usedRefs.has(row.value));
      if (!reusable.length) {
        menu.append(el('div', { class: 'wf-menu-item', text: 'No reusable fields in this list.' }));
      } else {
        reusable.forEach((row) => {
          const item = el('button', { type: 'button', class: 'wf-menu-item' }, `${row.label} (${fieldKinds[row.type]})`);
          item.addEventListener('click', () => addUse(kind, { ref: row.value }));
          menu.append(item);
        });
      }
      wrap.append(menu);
    }
    container.append(wrap);
  }

  function renderOverview(panel) {
    clearNode(panel);
    const details = el('section', { class: 'wf-section' });
    const nameLabel = el('label', { class: 'wf-field' });
    nameLabel.append(el('span', { class: 'wf-label', text: 'Name' }));
    const nameInput = el('input', { class: 'wf-input', type: 'text', 'data-editor-bind': 'name', 'aria-label': 'Blueprint name' });
    bindTextInput(nameInput, () => draft.name, (value) => {
      draft.name = value;
      refreshTitles();
    });
    nameLabel.append(nameInput);

    const descriptionLabel = el('label', { class: 'wf-field' });
    descriptionLabel.append(el('span', { class: 'wf-label', text: 'Description' }));
    const descriptionInput = el('textarea', { class: 'wf-textarea', rows: '3', 'data-editor-bind': 'description', 'aria-label': 'Blueprint description' });
    bindTextInput(descriptionInput, () => draft.description, (value) => {
      draft.description = value;
    });
    descriptionLabel.append(descriptionInput);
    details.append(nameLabel, descriptionLabel);
    panel.append(details);

    const usage = el('section', { class: 'wf-section' });
    usage.append(el('div', { class: 'wf-section-head' }, [el('h2', { text: 'Used by' }), el('span', { class: 'wf-label', text: `${initialState.workflow_count || 0} workflow${initialState.workflow_count === 1 ? '' : 's'}` })]));
    usage.append(el('p', { class: 'wf-empty', text: initialState.workflow_count ? 'This blueprint is currently bound into one or more workflow instances.' : 'This blueprint is not currently bound to any workflow instances.' }));
    panel.append(usage);
  }

  function renderStates(panel) {
    clearNode(panel);
    const section = el('section', { class: 'wf-section' });
    const head = el('div', { class: 'wf-section-head' }, el('h2', { text: 'States' }));
    const addButton = el('button', { type: 'button', class: 'wf-btn' }, [icon('plus'), 'Add state']);
    addButton.addEventListener('click', addState);
    head.append(addButton);
    section.append(head);

    draft.states.forEach((row, index) => {
      const ref = stateRef(row);
      const stateRow = el('div', { class: 'wf-state-row' });

      const colorInput = el('input', { class: 'wf-color', type: 'color', value: row.color, 'aria-label': `${row.name || 'State'} color` });
      colorInput.addEventListener('input', () => {
        row.color = colorInput.value;
        refreshAfterChange();
      });
      stateRow.append(colorInput);

      const stateName = el('label', { class: 'wf-field' });
      stateName.append(el('span', { class: 'wf-label', text: 'Name' }));
      const nameInput = el('input', { class: 'wf-input', type: 'text', value: row.name, 'aria-label': `State name ${index + 1}` });
      bindTextInput(nameInput, () => row.name, (value) => {
        row.name = value;
      });
      stateName.append(nameInput);
      stateRow.append(stateName);

      const initialLabel = el('label', { class: 'wf-checkbox-label wf-state-initial' });
      const radio = el('input', { class: 'wf-radio', type: 'radio', name: 'initial-state', checked: row.initial, 'aria-label': `Make ${row.name || 'state'} initial` });
      radio.addEventListener('change', () => {
        draft.states.forEach((item) => {
          item.initial = item === row;
        });
        refreshAfterChange();
      });
      initialLabel.append(radio, 'Initial');
      stateRow.append(initialLabel);

      const actions = el('div', { class: 'wf-state-actions' });
      const upButton = el('button', { type: 'button', class: 'wf-icon-btn', disabled: index === 0, 'aria-label': `Move ${row.name || 'state'} up`, title: 'Move up' }, icon('arrow-up'));
      upButton.addEventListener('click', () => moveState(index, -1));
      const downButton = el('button', { type: 'button', class: 'wf-icon-btn', disabled: index === draft.states.length - 1, 'aria-label': `Move ${row.name || 'state'} down`, title: 'Move down' }, icon('arrow-down'));
      downButton.addEventListener('click', () => moveState(index, 1));
      const deleteButton = el('button', { type: 'button', class: 'wf-icon-btn', disabled: localStateUsage(ref), 'aria-label': `Delete ${row.name || 'state'}`, title: localStateUsage(ref) ? 'State is referenced by a transition' : 'Delete state' }, icon('trash-2'));
      deleteButton.addEventListener('click', () => deleteState(index));
      actions.append(upButton, downButton, deleteButton);
      stateRow.append(actions);
      section.append(stateRow);
    });
    panel.append(section);
  }

  function renderTransitionNav() {
    if (!transitionNav) return;
    clearNode(transitionNav);
    if (activeTab !== 'transitions') {
      transitionNav.hidden = true;
      return;
    }
    transitionNav.hidden = false;
    const head = el('div', { class: 'wf-transition-nav-head' }, el('h3', { text: 'Transitions' }));
    const addButton = el('button', { type: 'button', class: 'wf-icon-btn', 'aria-label': 'Add transition', title: 'Add transition' }, icon('plus'));
    addButton.addEventListener('click', addTransition);
    head.append(addButton);
    transitionNav.append(head);

    draft.transitions.forEach((row) => {
      const ref = transitionRef(row);
      const button = el('button', { type: 'button', class: 'workflow-editor-transition-item', 'aria-pressed': String(ref === activeTransitionRef) });
      button.append(el('strong', { text: row.name || 'Untitled transition' }));
      button.append(el('div', { class: 'wf-transition-item-states' }, [stateLabel(row.from_state_id), icon('arrow-right'), stateLabel(row.to_state_id)]));
      button.addEventListener('click', () => {
        activeTransitionRef = ref;
        persistUiState();
        render();
      });
      transitionNav.append(button);
    });
  }

  function renderMobileTransitionPicker() {
    if (!mobileTransition) return;
    clearNode(mobileTransition);
    mobileTransition.hidden = activeTab !== 'transitions';
    if (activeTab !== 'transitions') return;
    const wrap = el('div', { class: 'wf-field' });
    wrap.append(el('span', { class: 'wf-label', text: 'Transition' }));
    const row = el('div', { class: 'wf-section-head' });
    const select = el('select', { class: 'wf-select', 'aria-label': 'Transition picker' });
    draft.transitions.forEach((item) => {
      const option = el('option', { value: transitionRef(item), text: item.name || 'Untitled transition' });
      if (transitionRef(item) === activeTransitionRef) option.selected = true;
      select.append(option);
    });
    select.addEventListener('change', () => {
      activeTransitionRef = select.value;
      persistUiState();
      render();
    });
    const addButton = el('button', { type: 'button', class: 'wf-icon-btn', 'aria-label': 'Add transition', title: 'Add transition' }, icon('plus'));
    addButton.addEventListener('click', addTransition);
    row.append(select, addButton);
    wrap.append(row);
    mobileTransition.append(wrap);
  }

  function renderTypedValue(rule, container, index) {
    const field = fieldByRef(useRef(rule));
    const kind = field?.type || 'text';
    if (rule.operator === 'is_present') {
      container.append(el('div', { class: 'wf-rule-value-cell wf-label', text: 'No value' }));
      return;
    }
    if (kind === 'boolean') {
      const label = el('label', { class: 'wf-checkbox-label wf-rule-value-cell' });
      const input = el('input', { class: 'wf-checkbox', type: 'checkbox', checked: Boolean(rule.value), 'aria-label': `Precondition value ${index + 1}` });
      input.addEventListener('change', () => {
        rule.value = input.checked;
        refreshAfterChange();
      });
      label.append(input, 'Value is true');
      container.append(label);
      return;
    }
    if (kind === 'number') {
      const input = el('input', { class: 'wf-input', type: 'number', value: rule.value ?? 0, 'aria-label': `Precondition value ${index + 1}` });
      input.addEventListener('input', () => {
        rule.value = input.value === '' ? null : Number(input.value);
        refreshAfterChange();
      });
      container.append(input);
      return;
    }
    const input = el('input', { class: 'wf-input', type: 'text', value: rule.value ?? '', 'aria-label': `Precondition value ${index + 1}` });
    bindTextInput(input, () => rule.value ?? '', (value) => {
      rule.value = value;
    });
    container.append(input);
  }

  function renderContracts(kind, transition, container) {
    const section = el('section');
    const head = el('div', { class: 'wf-section-head' }, el('h3', { text: kind === 'inputs' ? 'Inputs' : 'Outputs' }));
    renderMenu(kind, head);
    section.append(head);
    section.append(el('div', { class: 'wf-contract-head' }, [el('span', { text: 'Label' }), el('span', { text: 'Type' }), el('span', { text: 'Required' }), el('span', { text: '' })]));
    if (!transition[kind].length) {
      section.append(el('p', { class: 'wf-empty', text: 'None' }));
      container.append(section);
      return;
    }
    transition[kind].forEach((row, index) => {
      const ref = useRef(row);
      const field = fieldByRef(ref);
      const contractRow = el('div', { class: 'wf-contract-row' });
      const labelInput = el('input', { class: 'wf-input', type: 'text', value: field?.label || '', 'aria-label': `${kind === 'inputs' ? 'Input' : 'Output'} label ${index + 1}` });
      bindTextInput(labelInput, () => fieldByRef(ref)?.label || '', (value) => {
        const target = fieldByRef(ref);
        if (target) target.label = value;
      });
      const typeSelect = el('select', { class: 'wf-select', 'aria-label': `${kind === 'inputs' ? 'Input' : 'Output'} type ${index + 1}` });
      appendOptions(typeSelect, Object.entries(fieldKinds).map(([value, label]) => ({ value, label })), field?.type || 'text');
      bindSelect(typeSelect, () => fieldByRef(ref)?.type || 'text', (value) => {
        const target = fieldByRef(ref);
        if (target) target.type = value;
      });
      const requiredWrap = el('label', { class: 'wf-required' });
      const requiredInput = el('input', { class: 'wf-checkbox', type: 'checkbox', checked: row.required, 'aria-label': `${kind === 'inputs' ? 'Input' : 'Output'} required ${index + 1}` });
      bindCheckbox(requiredInput, () => row.required, (value) => {
        row.required = value;
      });
      requiredWrap.append(requiredInput);
      const removeButton = el('button', { type: 'button', class: 'wf-icon-btn', 'aria-label': `Remove ${kind === 'inputs' ? 'input' : 'output'} field ${index + 1}`, title: 'Remove field' }, icon('x'));
      removeButton.addEventListener('click', () => removeUse(kind, index));
      contractRow.append(labelInput, typeSelect, requiredWrap, removeButton);
      section.append(contractRow);
    });
    container.append(section);
  }

  function renderTransitions(panel) {
    clearNode(panel);
    renderMobileTransitionPicker();
    if (!draft.transitions.length) {
      const empty = el('section', { class: 'wf-section' });
      const head = el('div', { class: 'wf-section-head' }, el('h2', { text: 'Transitions' }));
      const addButton = el('button', { type: 'button', class: 'wf-btn' }, [icon('plus'), 'Add transition']);
      addButton.addEventListener('click', addTransition);
      head.append(addButton);
      empty.append(head, el('p', { class: 'wf-empty', text: 'No transitions yet.' }));
      panel.append(empty);
      return;
    }

    const transition = currentTransition();
    if (!transition) return;
    const index = currentTransitionIndex();

    const basics = el('section', { class: 'wf-section' });
    const head = el('div', { class: 'wf-section-head' }, el('h2', { text: 'Transition' }));
    const deleteButton = el('button', { type: 'button', class: 'wf-btn', 'aria-label': 'Delete transition' }, [icon('trash-2'), 'Delete']);
    deleteButton.addEventListener('click', () => deleteTransition(index));
    head.append(deleteButton);
    basics.append(head);

    const nameLabel = el('label', { class: 'wf-field' });
    nameLabel.append(el('span', { class: 'wf-label', text: 'Name' }));
    const nameInput = el('input', { class: 'wf-input', type: 'text', value: transition.name, 'aria-label': 'Transition name' });
    bindTextInput(nameInput, () => transition.name, (value) => {
      transition.name = value;
    });
    nameLabel.append(nameInput);
    basics.append(nameLabel);

    const stateGrid = el('div', { class: 'wf-two' });
    const fromLabel = el('label', { class: 'wf-field' });
    fromLabel.append(el('span', { class: 'wf-label', text: 'From' }));
    const fromSelect = el('select', { class: 'wf-select', 'aria-label': 'From state' });
    appendOptions(fromSelect, stateOptions(), transition.from_state_id);
    bindSelect(fromSelect, () => transition.from_state_id, (value) => {
      transition.from_state_id = value;
    });
    fromLabel.append(fromSelect);

    const toLabel = el('label', { class: 'wf-field' });
    toLabel.append(el('span', { class: 'wf-label', text: 'To' }));
    const toSelect = el('select', { class: 'wf-select', 'aria-label': 'To state' });
    appendOptions(toSelect, stateOptions(), transition.to_state_id);
    bindSelect(toSelect, () => transition.to_state_id, (value) => {
      transition.to_state_id = value;
    });
    toLabel.append(toSelect);
    stateGrid.append(fromLabel, toLabel);
    basics.append(stateGrid);
    panel.append(basics);

    const preconditions = el('section', { class: 'wf-section' });
    const preHead = el('div', { class: 'wf-section-head' }, el('h3', { text: 'Preconditions' }));
    const addRule = el('button', { type: 'button', class: 'wf-icon-btn', 'aria-label': 'Add precondition', title: 'Add precondition' }, icon('plus'));
    addRule.addEventListener('click', addPrecondition);
    preHead.append(addRule);
    preconditions.append(preHead);
    if (!transition.preconditions.length) preconditions.append(el('p', { class: 'wf-empty', text: 'None' }));
    transition.preconditions.forEach((row, position) => {
      const line = el('div', { class: 'wf-rule' });
      const fieldLabelNode = el('label', { class: 'wf-field' });
      fieldLabelNode.append(el('span', { class: 'wf-label', text: 'Field' }));
      const fieldSelect = el('select', { class: 'wf-select', 'aria-label': `Precondition input ${position + 1}` });
      appendOptions(fieldSelect, fieldOptions(), useRef(row));
      bindSelect(fieldSelect, () => useRef(row), (value) => {
        setRuleField(row, value);
      });
      fieldLabelNode.append(fieldSelect);
      line.append(fieldLabelNode);

      const operatorLabel = el('label', { class: 'wf-field' });
      operatorLabel.append(el('span', { class: 'wf-label', text: 'Operator' }));
      const operatorSelect = el('select', { class: 'wf-select', 'aria-label': `Precondition operator ${position + 1}` });
      appendOptions(operatorSelect, Object.entries(operators).map(([value, label]) => ({ value, label })), row.operator);
      bindSelect(operatorSelect, () => row.operator, (value) => {
        row.operator = value;
        if (value === 'is_present') row.value = null;
        else setRuleField(row, useRef(row));
      });
      operatorLabel.append(operatorSelect);
      line.append(operatorLabel);

      const valueLabel = el('label', { class: 'wf-field wf-rule-value' });
      valueLabel.append(el('span', { class: 'wf-label', text: 'Value' }));
      renderTypedValue(row, valueLabel, position);
      line.append(valueLabel);

      const removeButton = el('button', { type: 'button', class: 'wf-icon-btn wf-rule-remove', 'aria-label': `Remove precondition ${position + 1}`, title: 'Remove precondition' }, icon('x'));
      removeButton.addEventListener('click', () => removePrecondition(position));
      line.append(removeButton);
      preconditions.append(line);
    });
    panel.append(preconditions);

    const contracts = el('section', { class: 'wf-section wf-contracts' });
    renderContracts('inputs', transition, contracts);
    renderContracts('outputs', transition, contracts);
    panel.append(contracts);

    const criteria = el('section', { class: 'wf-section' });
    const criteriaHead = el('div', { class: 'wf-section-head' }, el('h3', { text: 'Agent criteria' }));
    const addCriterionButton = el('button', { type: 'button', class: 'wf-icon-btn', 'aria-label': 'Add agent criterion', title: 'Add agent criterion' }, icon('plus'));
    addCriterionButton.addEventListener('click', addCriterion);
    criteriaHead.append(addCriterionButton);
    criteria.append(criteriaHead);
    if (!transition.criteria.length) criteria.append(el('p', { class: 'wf-empty', text: 'None' }));
    transition.criteria.forEach((row, position) => {
      const line = el('div', { class: 'wf-criterion' });
      const textarea = el('textarea', { class: 'wf-textarea', rows: '3', value: row.description, 'aria-label': `Agent criterion ${position + 1}` });
      bindTextInput(textarea, () => row.description, (value) => {
        row.description = value;
      });
      const removeButton = el('button', { type: 'button', class: 'wf-icon-btn', 'aria-label': `Remove agent criterion ${position + 1}`, title: 'Remove criterion' }, icon('x'));
      removeButton.addEventListener('click', () => removeCriterion(position));
      line.append(textarea, removeButton);
      criteria.append(line);
    });
    panel.append(criteria);
  }

  function renderTabs() {
    tabButtons.forEach((button) => {
      button.setAttribute('aria-selected', String(button.dataset.editorTab === activeTab));
    });
    tabPanels.forEach((panel, name) => {
      panel.hidden = name !== activeTab;
    });
  }

  function renderPanels() {
    renderOverview(tabPanels.get('overview'));
    renderStates(tabPanels.get('states'));
    renderTransitions(tabPanels.get('transitions'));
  }

  function render() {
    ensureActiveTransition();
    refreshTitles();
    renderTabs();
    renderTransitionNav();
    renderPanels();
    showIssues(issues);
    showWarning(warning);
    setStatus(isDirty() ? 'Unsaved changes' : 'Saved', isDirty());
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons({ attrs: { 'stroke-width': 1.8 } });
    }
  }

  const pendingDraft = readPendingDraft();
  if (pendingDraft) initialState.draft = pendingDraft;
  loadUiState();
  ensureActiveTransition();
  tabButtons.forEach((button) => {
    button.addEventListener('click', () => {
      activeTab = button.dataset.editorTab || 'overview';
      persistUiState();
      render();
    });
  });
  if (saveButton) {
    saveButton.addEventListener('click', (event) => {
      event.preventDefault();
      void saveDraft();
    });
  }
  if (revertButton) {
    revertButton.addEventListener('click', (event) => {
      event.preventDefault();
      revertDraft();
    });
  }
  if (form) {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      void saveDraft();
    });
  }

  render();
})();