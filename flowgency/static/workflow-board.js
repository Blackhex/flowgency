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
      this.inputDraft = this.createDraftState();
      this.assigneeSelect = document.getElementById('ticket-assignee');
      this.ticketState = document.querySelector('[data-ticket-state]');
      this.saveInputsButton = document.getElementById('ticket-save-inputs');
      this.newTicketButton = document.getElementById('workflow-new-ticket');
      this.ticketDialog = document.getElementById('workflow-ticket-dialog');
      this.ticketDialogClose = document.getElementById('workflow-close-ticket-dialog');
      this.ticketDialogCancel = document.getElementById('workflow-cancel-ticket-dialog');
      this.ticketCreateForm = document.getElementById('workflow-create-ticket-form');
      this.ticketCreatePayload = document.getElementById('workflow-create-ticket-payload');
      if (this.assigneeSelect) {
        this.assigneeSelect.addEventListener('change', () => {
          void this.saveAssignee(this.assigneeSelect.value).catch(() => {});
        });
      }
      if (this.saveInputsButton) {
        this.saveInputsButton.addEventListener('click', () => {
          void this.saveInputs().catch(() => {});
        });
      }
      for (const area of document.querySelectorAll('[data-ticket-input]')) {
        area.addEventListener('input', () => {
          this.updateDraft(area.getAttribute('data-ticket-input'), area.value);
        });
      }
      for (const tab of document.querySelectorAll('[data-ticket-tab]')) {
        tab.addEventListener('click', () => {
          this.selectTab(tab.getAttribute('data-ticket-tab') || 'overview');
        });
      }
      if (this.newTicketButton && this.ticketDialog) {
        this.newTicketButton.addEventListener('click', () => {
          this.ticketDialog.showModal();
        });
      }
      if (this.ticketDialogClose && this.ticketDialog) {
        this.ticketDialogClose.addEventListener('click', () => {
          this.ticketDialog.close();
        });
      }
      if (this.ticketDialogCancel && this.ticketDialog) {
        this.ticketDialogCancel.addEventListener('click', () => {
          this.ticketDialog.close();
        });
      }
      if (this.ticketCreateForm && this.ticketCreatePayload) {
        this.ticketCreateForm.addEventListener('submit', (event) => {
          const title = document.getElementById('workflow-new-title');
          const description = document.getElementById('workflow-new-description');
          this.ticketCreatePayload.value = JSON.stringify({
            operation_id: crypto.randomUUID(),
            title: title?.value || '',
            description: description?.value || '',
            field_values: {},
          });
        });
      }
      this.selectTab('overview');
    }

    currentTicket() {
      return this.ticket?.ticket || null;
    }

    createDraftState() {
      const current = this.ticket;
      const values = {};
      const baseValues = {};
      for (const area of document.querySelectorAll('[data-ticket-input]')) {
        const key = area.getAttribute('data-ticket-input');
        values[key] = area.value;
        baseValues[key] = area.value;
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
      const savedDraft = {
        values: { ...this.inputDraft.values },
        baseValues: { ...this.inputDraft.baseValues },
        dirty: new Set(this.inputDraft.dirty),
        baseVersion: this.inputDraft.baseVersion,
      };
      this.assigneeSelect.disabled = true;
      try {
        const detail = await this.postTicketAction(this.urlFor('assignee', current.ref.ticket_id), {
          version: current.version,
          operation_id: crypto.randomUUID(),
          assignee: value || null,
        });
        this.ticket = detail;
        this.inputDraft = savedDraft;
        if (this.dirtyFieldsStillMatchServer(detail)) {
          this.inputDraft.baseVersion = detail.ticket.version;
        }
        this.restoreDraft();
        this.renderAssignment();
      } catch (error) {
        this.renderAssignment();
        this.lastActionError = error;
      } finally {
        this.assigneeSelect.disabled = false;
      }
    }

    restoreDraft() {
      for (const area of document.querySelectorAll('[data-ticket-input]')) {
        const key = area.getAttribute('data-ticket-input');
        if (Object.prototype.hasOwnProperty.call(this.inputDraft.values, key)) {
          area.value = this.inputDraft.values[key];
        }
      }
    }

    syncDraftFromInputs() {
      for (const area of document.querySelectorAll('[data-ticket-input]')) {
        const key = area.getAttribute('data-ticket-input');
        if (!key) {
          continue;
        }
        this.updateDraft(key, area.value);
      }
    }

    dirtyFieldsStillMatchServer(detail) {
      const serverFields = new Map((detail.fields || []).map((field) => [field.id, field.value]));
      for (const key of this.inputDraft.dirty) {
        if (serverFields.get(key) !== this.inputDraft.baseValues[key]) {
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
      const version = this.inputDraft.baseVersion || current.version;
      const fieldValues = {};
      for (const key of this.inputDraft.dirty) {
        fieldValues[key] = this.inputDraft.values[key];
      }
      this.saveInputsButton.disabled = true;
      try {
        const detail = await this.postTicketAction(this.urlFor('update', current.ref.ticket_id), {
          version,
          operation_id: crypto.randomUUID(),
          patch: {
            field_values: fieldValues,
          },
        });
        this.ticket = detail;
        for (const key of this.inputDraft.dirty) {
          this.inputDraft.baseValues[key] = this.inputDraft.values[key];
        }
        this.inputDraft.dirty.clear();
        this.inputDraft.baseVersion = detail.ticket.version;
        this.renderAssignment();
        this.restoreDraft();
      } catch (error) {
        this.restoreDraft();
        this.lastActionError = error;
      } finally {
        this.saveInputsButton.disabled = false;
      }
    }

    renderAssignment() {
      const current = this.currentTicket();
      if (!current || !this.assigneeSelect) {
        return;
      }
      this.assigneeSelect.value = current.assignee || '';
      if (this.ticketState) {
        this.ticketState.textContent = current.state_name;
      }
    }

    selectTab(name) {
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