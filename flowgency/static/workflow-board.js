(function () {
  class TicketActionError extends Error {
    constructor(payload) {
      super(payload?.code || 'ticket-action-error');
      this.payload = payload;
    }
  }

  class WorkflowBoardController {
    constructor(initial) {
      this.initial = initial;
      this.board = initial.board;
      this.ticket = initial.board.selected_ticket;
      this.ticketDrafts = new Map();
      this.inputDraft = { values: {}, baseValues: {}, dirty: new Set(), baseVersion: null };
      this.selectedTab = 'overview';
      this.lastActionError = null;
      this.editingOverview = false;
      this.searchTimer = 0;
      this.pollTimer = 0;
      this.etags = { board: null, detail: null };
      this.requestCounters = { page: 0, refresh: 0, action: 0 };
      this.requestControllers = { page: null, refresh: null };
      this.pendingAction = null;
      this.cacheElements();
      this.bindEvents();
      this.inputDraft = this.restoreOrCreateDraft();
      this.restoreDraft();
      this.selectTab(this.selectedTab);
      this.renderAssignment();
      this.applyEditingState(true);
      this.scheduleRefresh();
    }

    cacheElements() {
      this.page = document.querySelector('.workflow-board-page');
      this.isDetailPage = Boolean(this.page && this.page.classList.contains('workflow-ticket-page'));
      this.initialNode = document.getElementById('workflow-initial');
      this.filterForm = document.querySelector('.workflow-board-filters');
      this.searchInput = document.getElementById('workflow-search');
      this.boardAssigneeFilter = document.getElementById('workflow-assignee');
      this.ticketDialog = document.getElementById('workflow-ticket-dialog');
      this.ticketCreateForm = document.getElementById('workflow-create-ticket-form');
      this.ticketCreatePayload = document.getElementById('workflow-create-ticket-payload');
      this.assigneeSelect = document.getElementById('ticket-assignee');
      this.ticketState = document.querySelector('[data-ticket-state]');
      this.runStatus = document.querySelector('[data-ticket-run-status]');
      this.runStatusLabel = document.querySelector('[data-ticket-run-status-label]');
      this.saveInputsButton = document.getElementById('ticket-save-inputs');
      this.runButton = document.getElementById('ticket-run-button');
      this.titleHeading = document.querySelector('.workflow-ticket-title-block h2');
      this.editToggle = document.getElementById('ticket-edit-toggle');
      this.editRegion = document.querySelector('[data-ticket-edit]');
      this.readDescription = document.querySelector('[data-ticket-description-read]');
    }

    bindEvents() {
      document.addEventListener('click', (event) => {
        void this.handleClick(event);
      });
      document.addEventListener('input', (event) => {
        this.handleInput(event);
      });
      document.addEventListener('change', (event) => {
        void this.handleChange(event);
      });
      document.addEventListener('submit', (event) => {
        void this.handleSubmit(event);
      });
      window.addEventListener('popstate', () => {
        void this.loadPage(window.location.href, 'replace', true);
      });
      document.addEventListener('visibilitychange', () => {
        void this.handleVisibilityChange();
      });
    }

    currentTicket() {
      return this.ticket?.ticket || null;
    }

    currentTicketId() {
      return this.currentTicket()?.ref?.ticket_id || null;
    }

    ticketInputs() {
      return Array.from(document.querySelectorAll('[data-ticket-input]'));
    }

    readControlValue(control) {
      const kind = control.getAttribute('data-ticket-kind') || 'text';
      if (kind === 'boolean') {
        if (control.value === '') {
          return null;
        }
        return control.value === 'true';
      }
      if (kind === 'number') {
        if (control.value === '') {
          return null;
        }
        return Number(control.value);
      }
      return control.value;
    }

    writeControlValue(control, value) {
      const kind = control.getAttribute('data-ticket-kind') || 'text';
      if (kind === 'boolean') {
        control.value = value === null || typeof value === 'undefined' ? '' : String(Boolean(value));
        return;
      }
      if (kind === 'number') {
        control.value = value === null || typeof value === 'undefined' ? '' : String(value);
        return;
      }
      control.value = value === null || typeof value === 'undefined' ? '' : String(value);
    }

    cloneDraft(draft) {
      return {
        values: { ...draft.values },
        baseValues: { ...draft.baseValues },
        dirty: new Set(draft.dirty),
        baseVersion: draft.baseVersion,
      };
    }

    restoreOrCreateDraft() {
      const ticketId = this.currentTicketId();
      if (!ticketId) {
        return this.createDraftState();
      }
      const cached = this.ticketDrafts.get(ticketId);
      return cached ? this.cloneDraft(cached) : this.createDraftState();
    }

    saveCurrentDraft() {
      const ticketId = this.currentTicketId();
      if (!ticketId) {
        return;
      }
      this.syncDraftFromInputs();
      this.ticketDrafts.set(ticketId, this.cloneDraft(this.inputDraft));
    }

    issuePayload(code, message, field = 'payload', hint = 'Refresh and retry the ticket action.') {
      return { code, issues: [{ code, field, message, hint }] };
    }

    ensureActionErrorBanner() {
      if (!this.page) {
        return null;
      }
      let banner = document.getElementById('workflow-action-errors');
      if (!banner) {
        banner = document.createElement('div');
        banner.id = 'workflow-action-errors';
        banner.className = 'workflow-issue-banner';
        banner.setAttribute('role', 'alert');
        banner.hidden = true;
        this.page.insertBefore(banner, this.page.firstChild);
      }
      return banner;
    }

    clearActionError() {
      this.lastActionError = null;
      const banner = this.ensureActionErrorBanner();
      if (!banner) {
        return;
      }
      banner.hidden = true;
      banner.innerHTML = '';
    }

    reportActionError(error) {
      this.lastActionError = error instanceof TicketActionError
        ? error.payload
        : this.issuePayload('action-failed', error?.message || 'The workflow action failed.');
      this.renderActionError();
    }

    renderActionError() {
      const banner = this.ensureActionErrorBanner();
      if (!banner) {
        return;
      }
      const issues = Array.isArray(this.lastActionError?.issues) ? this.lastActionError.issues : [];
      banner.innerHTML = '';
      if (issues.length === 0) {
        banner.hidden = true;
        return;
      }
      banner.hidden = false;
      for (const issue of issues) {
        const line = document.createElement('p');
        const strong = document.createElement('strong');
        strong.textContent = String(issue.code || 'action-failed');
        line.appendChild(strong);
        line.appendChild(document.createTextNode(`: ${String(issue.message || 'The workflow action failed.')}`));
        banner.appendChild(line);
      }
    }

    currentBoardUrl(ticketId = this.currentTicketId()) {
      const url = new URL(this.initial.urls.board, window.location.origin);
      const query = this.searchInput ? this.searchInput.value.trim() : (this.board?.query || '');
      const assignee = this.boardAssigneeFilter ? this.boardAssigneeFilter.value : (this.board?.assignee || '');
      if (query) {
        url.searchParams.set('query', query);
      }
      if (assignee) {
        url.searchParams.set('assignee', assignee);
      }
      if (ticketId) {
        url.searchParams.set('ticket', ticketId);
      }
      return url;
    }

    currentSnapshotUrl() {
      if (this.isDetailPage && this.initial.urls.detailSnapshot) {
        return new URL(this.initial.urls.detailSnapshot, window.location.origin);
      }
      const url = new URL(this.initial.urls.snapshot, window.location.origin);
      const query = this.searchInput ? this.searchInput.value.trim() : (this.board?.query || '');
      const assignee = this.boardAssigneeFilter ? this.boardAssigneeFilter.value : (this.board?.assignee || '');
      if (query) {
        url.searchParams.set('query', query);
      }
      if (assignee) {
        url.searchParams.set('assignee', assignee);
      }
      const ticketId = this.currentTicketId();
      if (ticketId) {
        url.searchParams.set('ticket', ticketId);
      }
      return url;
    }

    shouldInterceptLink(event, link) {
      return !(event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target === '_blank');
    }

    captureFocus() {
      const active = document.activeElement;
      if (!active || !(active instanceof HTMLElement) || !active.id) {
        return null;
      }
      return {
        id: active.id,
        ticketId: this.currentTicketId(),
        start: typeof active.selectionStart === 'number' ? active.selectionStart : null,
        end: typeof active.selectionEnd === 'number' ? active.selectionEnd : null,
      };
    }

    restoreFocus(focusState) {
      if (!focusState || focusState.ticketId !== this.currentTicketId()) {
        return;
      }
      const target = document.getElementById(focusState.id);
      if (!(target instanceof HTMLElement)) {
        return;
      }
      target.focus();
      if (typeof focusState.start === 'number' && typeof target.setSelectionRange === 'function') {
        target.setSelectionRange(focusState.start, focusState.end ?? focusState.start);
      }
    }

    beginAbortableRequest(name) {
      this.requestCounters[name] += 1;
      if (this.requestControllers[name]) {
        this.requestControllers[name].abort();
      }
      const controller = new AbortController();
      this.requestControllers[name] = controller;
      return { seq: this.requestCounters[name], controller };
    }

    isLatestRequest(name, seq) {
      return this.requestCounters[name] === seq;
    }

    pageState() {
      this.saveCurrentDraft();
      return { focus: this.captureFocus(), selectedTab: this.selectedTab };
    }

    applyPageHtml(html, nextUrl, pageState, historyMode) {
      const nextDocument = new DOMParser().parseFromString(html, 'text/html');
      const nextPage = nextDocument.querySelector('.workflow-board-page');
      const nextDialog = nextDocument.getElementById('workflow-ticket-dialog');
      const nextInitial = nextDocument.getElementById('workflow-initial');
      if (!nextPage || !nextInitial) {
        throw new TicketActionError(this.issuePayload('navigation-failed', 'The workflow page could not be refreshed.'));
      }
      const currentPage = document.querySelector('.workflow-board-page');
      if (currentPage) {
        currentPage.replaceWith(nextPage);
      }
      const currentDialog = document.getElementById('workflow-ticket-dialog');
      if (currentDialog && nextDialog) {
        currentDialog.replaceWith(nextDialog);
      }
      const currentInitial = document.getElementById('workflow-initial');
      if (currentInitial) {
        currentInitial.replaceWith(nextInitial);
      }
      document.title = nextDocument.title;
      this.cacheElements();
      this.initial = JSON.parse(this.initialNode.textContent || '{}');
      this.board = this.initial.board;
      this.ticket = this.initial.board.selected_ticket;
      this.inputDraft = this.restoreOrCreateDraft();
      this.restoreDraft();
      this.selectTab(pageState.selectedTab || 'overview');
      this.renderAssignment();
      this.renderActionError();
      this.applyEditingState(false);
      this.restoreFocus(pageState.focus);
      if (historyMode === 'push') {
        window.history.pushState({}, '', nextUrl);
      } else if (historyMode === 'replace') {
        window.history.replaceState({}, '', nextUrl);
      }
      this.scheduleRefresh();
    }

    async loadPage(url, historyMode = 'push', preserveErrors = true) {
      const pageState = this.pageState();
      const request = this.beginAbortableRequest('page');
      try {
        const response = await fetch(url, {
          headers: { Accept: 'text/html' },
          signal: request.controller.signal,
        });
        const html = await response.text();
        if (!this.isLatestRequest('page', request.seq)) {
          return;
        }
        this.applyPageHtml(html, response.url, pageState, historyMode);
        this.etags.board = null;
        this.etags.detail = null;
        if (!preserveErrors) {
          this.clearActionError();
        }
      } catch (error) {
        if (error?.name === 'AbortError') {
          return;
        }
        this.reportActionError(error);
      }
    }

    updateVisibleCard() {
      const current = this.currentTicket();
      if (!current) {
        return;
      }
      const card = Array.from(document.querySelectorAll('.workflow-ticket-card')).find((node) => {
        try {
          return new URL(node.href, window.location.origin).searchParams.get('ticket') === current.ref.ticket_id;
        } catch {
          return false;
        }
      });
      if (!card) {
        return;
      }
      const title = card.querySelector('strong');
      if (title) {
        title.textContent = current.title;
      }
      const badge = card.querySelector('.workflow-ticket-status-badge');
      if (badge) {
        badge.classList.toggle('is-working', Boolean(current.active_run_job_id));
        badge.classList.toggle('is-queued', !current.active_run_job_id && Boolean(current.pending_run_job_id));
        badge.textContent = current.active_run_job_id ? 'Working' : current.pending_run_job_id ? 'Queued' : 'Idle';
      }
    }

    async openTicket(ticketId) {
      await this.loadPage(this.currentBoardUrl(ticketId).toString(), 'push', true);
    }

    async closeTicket() {
      await this.loadPage(this.currentBoardUrl(null).toString(), 'push', true);
    }

    async refreshBoard() {
      if (document.hidden) {
        return;
      }
      const request = this.beginAbortableRequest('refresh');
      try {
        const headers = { Accept: 'application/json' };
        const etagKey = this.isDetailPage ? 'detail' : 'board';
        if (this.etags[etagKey]) {
          headers['If-None-Match'] = this.etags[etagKey];
        }
        const response = await fetch(this.currentSnapshotUrl(), {
          headers,
          signal: request.controller.signal,
        });
        if (!this.isLatestRequest('refresh', request.seq)) {
          return;
        }
        if (response.status === 304) {
          this.scheduleRefresh();
          return;
        }
        if (!response.ok) {
          throw new TicketActionError(this.issuePayload('refresh-failed', 'The workflow board could not be refreshed.'));
        }
        this.etags[etagKey] = response.headers.get('etag');
        await this.loadPage(window.location.href, 'replace', true);
      } catch (error) {
        if (error?.name === 'AbortError') {
          return;
        }
        this.reportActionError(error);
        this.scheduleRefresh();
      }
    }

    scheduleRefresh() {
      clearTimeout(this.pollTimer);
      if (document.hidden) {
        return;
      }
      this.pollTimer = window.setTimeout(() => {
        void this.refreshBoard();
      }, 2000);
    }

    async handleVisibilityChange() {
      if (document.hidden) {
        clearTimeout(this.pollTimer);
        if (this.requestControllers.refresh) {
          this.requestControllers.refresh.abort();
        }
        return;
      }
      await this.refreshBoard();
    }

    async handleClick(event) {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) {
        return;
      }
      const tab = target.closest('[data-ticket-tab]');
      if (tab) {
        this.selectTab(tab.getAttribute('data-ticket-tab') || 'overview');
        return;
      }
      if (target.closest('#workflow-new-ticket') && this.ticketDialog) {
        this.ticketDialog.showModal();
        return;
      }
      if (target.closest('#workflow-close-ticket-dialog, #workflow-cancel-ticket-dialog') && this.ticketDialog) {
        this.ticketDialog.close();
        return;
      }
      if (target.closest('#ticket-edit-toggle')) {
        event.preventDefault();
        this.setEditing(!this.editingOverview);
        return;
      }
      if (target.closest('#ticket-edit-cancel')) {
        event.preventDefault();
        this.cancelEdit();
        return;
      }
      if (target.closest('#ticket-edit-save')) {
        event.preventDefault();
        try {
          await this.saveInputs();
          this.editingOverview = false;
          await this.loadPage(window.location.href, 'replace', true);
        } catch (error) {
          this.reportActionError(error);
        }
        return;
      }
      if (target.closest('#ticket-save-inputs')) {
        event.preventDefault();
        try {
          await this.saveInputs();
        } catch (error) {
          this.reportActionError(error);
        }
        return;
      }
      if (target.closest('#ticket-run-button')) {
        event.preventDefault();
        try {
          await this.runAssignedAgent();
        } catch (error) {
          this.reportActionError(error);
        }
        return;
      }
      const cardLink = target.closest('.workflow-ticket-card');
      if (cardLink && this.shouldInterceptLink(event, cardLink)) {
        event.preventDefault();
        const ticketId = new URL(cardLink.href, window.location.origin).searchParams.get('ticket');
        if (ticketId) {
          await this.openTicket(ticketId);
        }
        return;
      }
      const expandLink = target.closest('.workflow-icon-link[aria-label="Expand"]');
      if (expandLink && this.shouldInterceptLink(event, expandLink)) {
        event.preventDefault();
        await this.loadPage(expandLink.href, 'push', true);
        return;
      }
      const closeLink = target.closest('.workflow-icon-link[aria-label="Close"]');
      if (closeLink && this.shouldInterceptLink(event, closeLink)) {
        event.preventDefault();
        await this.closeTicket();
      }
    }

    handleInput(event) {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) {
        return;
      }
      if (target.matches('[data-ticket-input]')) {
        this.updateDraft(target.getAttribute('data-ticket-input'), this.readControlValue(target));
        return;
      }
      if (target === this.searchInput) {
        clearTimeout(this.searchTimer);
        this.searchTimer = window.setTimeout(() => {
          void this.applyBoardFilters().catch((error) => {
            this.reportActionError(error);
          });
        }, 250);
      }
    }

    async handleChange(event) {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) {
        return;
      }
      if (target.matches('[data-ticket-input]')) {
        this.updateDraft(target.getAttribute('data-ticket-input'), this.readControlValue(target));
        return;
      }
      if (target === this.assigneeSelect) {
        try {
          await this.saveAssignee(this.assigneeSelect.value);
        } catch (error) {
          this.reportActionError(error);
        }
        return;
      }
      if (target === this.boardAssigneeFilter) {
        try {
          await this.applyBoardFilters();
        } catch (error) {
          this.reportActionError(error);
        }
      }
    }

    async handleSubmit(event) {
      const form = event.target instanceof HTMLFormElement ? event.target : null;
      if (!form) {
        return;
      }
      if (form === this.filterForm) {
        event.preventDefault();
        try {
          await this.applyBoardFilters();
        } catch (error) {
          this.reportActionError(error);
        }
        return;
      }
      if (form.classList.contains('workflow-pane-back-form')) {
        event.preventDefault();
        const backUrl = new URL(form.action, window.location.origin);
        const formData = new FormData(form);
        for (const [key, value] of formData.entries()) {
          if (value) {
            backUrl.searchParams.set(key, String(value));
          }
        }
        await this.loadPage(backUrl.toString(), 'push', true);
        return;
      }
      if (form === this.ticketCreateForm && this.ticketCreatePayload) {
        event.preventDefault();
        const title = document.getElementById('workflow-new-title');
        const description = document.getElementById('workflow-new-description');
        this.ticketCreatePayload.value = JSON.stringify({
          operation_id: crypto.randomUUID(),
          title: title?.value || '',
          description: description?.value || '',
          field_values: {},
        });
        const response = await fetch(form.action, {
          method: 'POST',
          headers: { Accept: 'text/html' },
          body: new URLSearchParams({ payload: this.ticketCreatePayload.value }),
        });
        this.applyPageHtml(await response.text(), response.url, { focus: null, selectedTab: this.selectedTab }, 'push');
        if (response.status >= 400 && this.ticketDialog) {
          this.ticketDialog.showModal();
        } else if (this.ticketDialog && this.ticketDialog.open) {
          this.ticketDialog.close();
        }
      }
    }

    async applyBoardFilters() {
      await this.loadPage(this.currentBoardUrl().toString(), 'push', false);
    }

    createDraftState() {
      const current = this.ticket;
      const values = {};
      const baseValues = {};
      for (const area of this.ticketInputs()) {
        const key = area.getAttribute('data-ticket-input');
        values[key] = this.readControlValue(area);
        baseValues[key] = this.readControlValue(area);
      }
      return {
        values,
        baseValues,
        dirty: new Set(),
        baseVersion: current?.ticket?.version || null,
      };
    }

    updateDraft(key, value) {
      if (!key) {
        return;
      }
      this.inputDraft.values[key] = value;
      if (this.inputDraft.baseValues[key] !== value) {
        this.inputDraft.dirty.add(key);
      } else {
        this.inputDraft.dirty.delete(key);
      }
      const ticketId = this.currentTicketId();
      if (ticketId) {
        this.ticketDrafts.set(ticketId, this.cloneDraft(this.inputDraft));
      }
    }

    async postTicketAction(url, payload) {
      const response = await fetch(url, {
        method: 'POST',
        headers: { Accept: 'application/json' },
        body: new URLSearchParams({ payload: JSON.stringify(payload) }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new TicketActionError(result);
      }
      return result;
    }

    urlFor(name, ticketId) {
      const template = this.initial.urls[name];
      return template.replace('__ticket__', ticketId);
    }

    async saveAssignee(value) {
      const current = this.currentTicket();
      if (!current || !current.version || !this.assigneeSelect) {
        return;
      }
      const actionSeq = ++this.requestCounters.action;
      const savedDraft = this.cloneDraft(this.inputDraft);
      this.pendingAction = 'assignee';
      this.renderAssignment();
      try {
        const detail = await this.postTicketAction(this.urlFor('assignee', current.ref.ticket_id), {
          version: current.version,
          operation_id: crypto.randomUUID(),
          assignee: value || null,
        });
        if (actionSeq !== this.requestCounters.action || this.currentTicketId() !== current.ref.ticket_id) {
          return;
        }
        this.ticket = detail;
        this.initial.board.selected_ticket = detail;
        this.inputDraft = savedDraft;
        if (this.dirtyFieldsStillMatchServer(detail)) {
          this.inputDraft.baseVersion = detail.ticket.version;
        }
        this.ticketDrafts.set(current.ref.ticket_id, this.cloneDraft(this.inputDraft));
        this.restoreDraft();
        this.clearActionError();
        this.updateVisibleCard();
      } finally {
        this.pendingAction = null;
        this.renderAssignment();
      }
    }

    restoreDraft() {
      for (const area of this.ticketInputs()) {
        const key = area.getAttribute('data-ticket-input');
        if (Object.prototype.hasOwnProperty.call(this.inputDraft.values, key)) {
          this.writeControlValue(area, this.inputDraft.values[key]);
        }
      }
    }

    syncDraftFromInputs() {
      for (const area of this.ticketInputs()) {
        const key = area.getAttribute('data-ticket-input');
        if (!key) {
          continue;
        }
        this.updateDraft(key, this.readControlValue(area));
      }
    }

    dirtyFieldsStillMatchServer(detail) {
      const serverFields = new Map((detail.fields || []).map((field) => [field.id, field.value]));
      for (const key of this.inputDraft.dirty) {
        const currentTicket = detail.ticket || {};
        const serverValue = key === 'title'
          ? currentTicket.title
          : key === 'description'
            ? currentTicket.description
            : serverFields.get(key);
        if (serverValue !== this.inputDraft.baseValues[key]) {
          return false;
        }
      }
      return true;
    }

    async saveInputs() {
      const current = this.currentTicket();
      if (!current || !this.saveInputsButton) {
        return;
      }
      this.syncDraftFromInputs();
      if (this.inputDraft.dirty.size === 0) {
        return;
      }
      const actionSeq = ++this.requestCounters.action;
      const version = this.inputDraft.baseVersion || current.version;
      const fieldValues = {};
      const patch = {};
      for (const key of this.inputDraft.dirty) {
        if (key === 'title' || key === 'description') {
          patch[key] = this.inputDraft.values[key];
        } else {
          fieldValues[key] = this.inputDraft.values[key];
        }
      }
      if (Object.keys(fieldValues).length > 0) {
        patch.field_values = fieldValues;
      }
      this.pendingAction = 'inputs';
      this.renderAssignment();
      try {
        const detail = await this.postTicketAction(this.urlFor('update', current.ref.ticket_id), {
          version,
          operation_id: crypto.randomUUID(),
          patch,
        });
        if (actionSeq !== this.requestCounters.action || this.currentTicketId() !== current.ref.ticket_id) {
          return;
        }
        this.ticket = detail;
        this.initial.board.selected_ticket = detail;
        for (const key of this.inputDraft.dirty) {
          this.inputDraft.baseValues[key] = this.inputDraft.values[key];
        }
        this.inputDraft.dirty.clear();
        this.inputDraft.baseVersion = detail.ticket.version;
        this.ticketDrafts.set(current.ref.ticket_id, this.cloneDraft(this.inputDraft));
        this.clearActionError();
        this.restoreDraft();
        this.updateVisibleCard();
      } catch (error) {
        this.restoreDraft();
        throw error;
      } finally {
        this.pendingAction = null;
        this.renderAssignment();
      }
    }

    async runAssignedAgent() {
      const current = this.currentTicket();
      if (!current || !current.version || !this.runButton || !current.assignee || current.active_run_job_id || current.pending_run_job_id) {
        this.renderAssignment();
        return;
      }
      const actionSeq = ++this.requestCounters.action;
      this.pendingAction = 'run';
      this.renderAssignment();
      try {
        const detail = await this.postTicketAction(this.urlFor('run', current.ref.ticket_id), {
          version: current.version,
          operation_id: crypto.randomUUID(),
        });
        if (actionSeq !== this.requestCounters.action || this.currentTicketId() !== current.ref.ticket_id) {
          return;
        }
        this.ticket = detail;
        this.initial.board.selected_ticket = detail;
        this.clearActionError();
        this.updateVisibleCard();
      } finally {
        this.pendingAction = null;
        this.renderAssignment();
      }
    }

    renderAssignment() {
      const current = this.currentTicket();
      if (!current) {
        return;
      }
      if (this.assigneeSelect) {
        this.assigneeSelect.value = current.assignee || '';
        this.assigneeSelect.disabled = this.pendingAction === 'assignee' || Boolean(current.active_run_job_id) || Boolean(current.pending_run_job_id);
      }
      if (this.ticketState) {
        this.ticketState.textContent = current.state_name;
      }
      if (this.titleHeading) {
        this.titleHeading.textContent = current.title || '';
      }
      const titleInput = document.getElementById('ticket-title');
      if (titleInput && !this.inputDraft.dirty.has('title')) {
        titleInput.value = current.title || '';
      }
      const descriptionInput = document.getElementById('ticket-description');
      if (descriptionInput && !this.inputDraft.dirty.has('description')) {
        descriptionInput.value = current.description || '';
      }
      if (this.saveInputsButton) {
        this.saveInputsButton.disabled = this.pendingAction === 'inputs';
      }
      if (this.runButton) {
        this.runButton.disabled = this.pendingAction === 'run' || !current.assignee || Boolean(current.active_run_job_id) || Boolean(current.pending_run_job_id);
      }
      if (this.runStatus) {
        this.runStatus.classList.remove('is-working', 'is-queued', 'is-idle');
        const label = current.active_run_job_id
          ? `Working ${current.active_run_job_id}`
          : current.pending_run_job_id
            ? `Queued ${current.pending_run_job_id}`
            : 'No active run';
        if (current.active_run_job_id) {
          this.runStatus.classList.add('is-working');
        } else if (current.pending_run_job_id) {
          this.runStatus.classList.add('is-queued');
        } else {
          this.runStatus.classList.add('is-idle');
        }
        if (this.runStatusLabel) {
          this.runStatusLabel.textContent = label;
        }
      }
    }

    setEditing(open, { focus = true } = {}) {
      this.editingOverview = Boolean(open) && Boolean(this.currentTicket());
      if (this.editRegion) {
        this.editRegion.hidden = !this.editingOverview;
      }
      if (this.readDescription) {
        this.readDescription.hidden = this.editingOverview;
      }
      if (this.editToggle) {
        this.editToggle.setAttribute('aria-expanded', this.editingOverview ? 'true' : 'false');
      }
      if (this.editingOverview && focus) {
        const titleInput = document.getElementById('ticket-title');
        if (titleInput instanceof HTMLElement) {
          titleInput.focus();
        }
      }
    }

    applyEditingState(readFromDom = false) {
      if (!this.currentTicket() || !this.editRegion) {
        this.editingOverview = false;
        return;
      }
      if (readFromDom) {
        this.editingOverview = !this.editRegion.hidden;
      }
      this.setEditing(this.editingOverview, { focus: false });
    }

    cancelEdit() {
      for (const key of ['title', 'description']) {
        if (Object.prototype.hasOwnProperty.call(this.inputDraft.baseValues, key)) {
          this.inputDraft.values[key] = this.inputDraft.baseValues[key];
        }
        this.inputDraft.dirty.delete(key);
      }
      const ticketId = this.currentTicketId();
      if (ticketId) {
        this.ticketDrafts.set(ticketId, this.cloneDraft(this.inputDraft));
      }
      this.restoreDraft();
      this.setEditing(false);
    }

    selectTab(name) {
      this.selectedTab = name;
      for (const tab of document.querySelectorAll('[data-ticket-tab]')) {
        const selected = tab.getAttribute('data-ticket-tab') === name;
        tab.classList.toggle('is-active', selected);
        tab.setAttribute('aria-selected', selected ? 'true' : 'false');
      }
      for (const panel of document.querySelectorAll('[data-ticket-panel]')) {
        panel.hidden = panel.getAttribute('data-ticket-panel') !== name;
      }
    }
  }

  const initialNode = document.getElementById('workflow-initial');
  if (!initialNode) {
    return;
  }
  const initial = JSON.parse(initialNode.textContent || '{}');
  window.workflowBoardController = new WorkflowBoardController(initial);
  window.TicketActionError = TicketActionError;
})();