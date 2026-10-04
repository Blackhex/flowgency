(function () {
  function refKey(ref) {
    return JSON.stringify([ref.binding_id, ref.team_id, ref.workflow_id, ref.ticket_id]);
  }

  function editableSignature(detail) {
    return JSON.stringify((detail?.fields || [])
      .filter((field) => !field.is_output && field.type !== 'artifact')
      .map((field) => [field.id, field.type])
      .sort(([left], [right]) => left.localeCompare(right)));
  }

  function isHeld(node) {
    if (node instanceof Element && node.contains(document.activeElement)) {
      return true;
    }
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) {
      return false;
    }
    for (let index = 0; index < selection.rangeCount; index += 1) {
      if (selection.getRangeAt(index).intersectsNode(node)) {
        return true;
      }
    }
    return false;
  }

  function setText(node, value) {
    const text = String(value ?? '');
    if (node && node.textContent !== text) {
      node.textContent = text;
    }
  }

  function isPlainObject(value) {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
  }

  function ticketId(detail) {
    return detail?.ticket?.ref?.ticket_id || null;
  }

  function childSignature(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      return `text:${node.textContent || ''}`;
    }
    if (node instanceof Element) {
      return `${node.tagName}:${node.getAttribute('data-view-key') || ''}`;
    }
    return node.nodeName;
  }

  function parseFragment(html) {
    const template = document.createElement('template');
    template.innerHTML = String(html || '');
    return template.content;
  }

  function attributesMatch(current, desired) {
    if (current.attributes.length !== desired.attributes.length) {
      return false;
    }
    for (const attribute of desired.attributes) {
      if (current.getAttribute(attribute.name) !== attribute.value) {
        return false;
      }
    }
    return true;
  }

  function nodesMatch(current, desired) {
    if (current.nodeType !== desired.nodeType) {
      return false;
    }
    if (current.nodeType === Node.TEXT_NODE) {
      return current.textContent === desired.textContent;
    }
    if (!(current instanceof Element) || !(desired instanceof Element)) {
      return true;
    }
    if (current.tagName !== desired.tagName || !attributesMatch(current, desired)) {
      return false;
    }
    const currentChildren = Array.from(current.childNodes);
    const desiredChildren = Array.from(desired.childNodes);
    if (currentChildren.length !== desiredChildren.length) {
      return false;
    }
    return desiredChildren.every((child, index) => nodesMatch(currentChildren[index], child));
  }

  function patchAttributes(current, desired) {
    const desiredNames = new Set(Array.from(desired.attributes).map((attribute) => attribute.name));
    for (const attribute of Array.from(current.attributes)) {
      if (!desiredNames.has(attribute.name) && attribute.name !== 'open') {
        current.removeAttribute(attribute.name);
      }
    }
    for (const attribute of desired.attributes) {
      if (attribute.name === 'open' && current.hasAttribute('open') !== desired.hasAttribute('open')) {
        continue;
      }
      if (current.getAttribute(attribute.name) !== attribute.value) {
        current.setAttribute(attribute.name, attribute.value);
      }
    }
  }

  function reconcileElement(current, desired) {
    patchAttributes(current, desired);
    reconcileChildren(current, desired.childNodes);
  }

  function reconcileChildren(parent, desiredChildren) {
    const keyed = new Map();
    for (const child of Array.from(parent.children)) {
      const key = child.getAttribute('data-view-key');
      if (key) {
        keyed.set(key, child);
      }
    }
    let before = parent.firstChild;
    for (const desired of Array.from(desiredChildren)) {
      let current = null;
      if (desired instanceof Element) {
        const key = desired.getAttribute('data-view-key');
        current = key ? keyed.get(key) || null : null;
      }
      if (!current && before && childSignature(before) === childSignature(desired)) {
        current = before;
      }
      if (!current || current.nodeType !== desired.nodeType || (current instanceof Element && desired instanceof Element && current.tagName !== desired.tagName)) {
        current = desired.cloneNode(true);
        parent.insertBefore(current, before);
      } else if (current !== before) {
        parent.insertBefore(current, before);
      }
      if (current.nodeType === Node.TEXT_NODE) {
        setText(current, desired.textContent || '');
      } else if (current instanceof Element && desired instanceof Element) {
        reconcileElement(current, desired);
      }
      before = current.nextSibling;
    }
    while (before) {
      const next = before.nextSibling;
      parent.removeChild(before);
      before = next;
    }
  }

  class WorkflowBoardView {
    constructor(page, boardUrl, initialBoard) {
      this.page = page;
      this.boardUrl = boardUrl;
      this.initialBoard = initialBoard;
      this.bindingKey = JSON.stringify([
        initialBoard?.binding?.storage?.binding_id || initialBoard?.binding?.binding_id || initialBoard?.binding_id,
        initialBoard?.binding?.team_id,
        initialBoard?.binding?.workflow_id,
      ]);
      this.initialSelectedKey = initialBoard?.selected_ticket?.ticket ? refKey(initialBoard.selected_ticket.ticket.ref) : null;
      this.editableSignature = editableSignature(initialBoard?.selected_ticket);
      this.deferred = new Map();
      this.columnHost = page.querySelector('.workflow-board-columns');
      this.columnTemplate = page.querySelector('template[data-workflow-column-template]');
      this.cardTemplate = page.querySelector('template[data-workflow-card-template]');
      this.heading = page.querySelector('.workflow-board-title h1');
      this.ticketCount = page.querySelector('[data-board-stat="tickets"] span:last-child');
      this.workingCount = page.querySelector('[data-board-stat="working"] span:last-child');
      this.boardIssues = page.querySelector('[data-board-issues]');
      this.ticketIssues = page.querySelector('[data-ticket-issues]');
      this.titleHeading = page.querySelector('.workflow-ticket-title-block h2');
      this.stateLabel = page.querySelector('[data-ticket-state]');
      this.runStatus = page.querySelector('[data-ticket-run-status]');
      this.runStatusLabel = page.querySelector('[data-ticket-run-status-label]');
      this.description = page.querySelector('[data-ticket-description-read]');
      this.outputs = page.querySelector('[data-ticket-output-values]');
      this.requirements = page.querySelector('[data-ticket-panel="requirements"]');
      this.history = page.querySelector('[data-ticket-panel="history"]');
      this.cards = new Map();
      for (const card of page.querySelectorAll('.workflow-ticket-card[data-view-key]')) {
        this.cards.set(card.getAttribute('data-view-key'), card);
      }
    }

    inspectBoard(board, selectedTicketId) {
      if (!isPlainObject(board)) {
        return { ok: false, reason: 'invalid-board', unavailable: false };
      }
      if (board.presentation?.format !== 1) {
        return { ok: false, reason: 'unsupported-presentation', unavailable: false };
      }
      const bindingKey = JSON.stringify([
        board?.binding?.storage?.binding_id || board?.binding?.binding_id || board?.binding_id,
        board?.binding?.team_id,
        board?.binding?.workflow_id,
      ]);
      if (bindingKey !== this.bindingKey) {
        return { ok: false, reason: 'wrong-binding', unavailable: false };
      }
      if (!Array.isArray(board.columns) || !Number.isFinite(Number(board.ticket_count)) || !Number.isFinite(Number(board.working_count))) {
        return { ok: false, reason: 'invalid-counts', unavailable: false };
      }
      const columnKeys = new Set();
      const cardKeys = new Set();
      for (const column of board.columns) {
        if (!column || columnKeys.has(column.key) || !Array.isArray(column.tickets)) {
          return { ok: false, reason: 'duplicate-column', unavailable: false };
        }
        columnKeys.add(column.key);
        if (Number(column.count) !== column.tickets.length) {
          return { ok: false, reason: 'invalid-column-count', unavailable: false };
        }
        for (const card of column.tickets) {
          const key = refKey(card.ref || {});
          if (cardKeys.has(key)) {
            return { ok: false, reason: 'duplicate-card', unavailable: false };
          }
          cardKeys.add(key);
        }
      }
      const selected = board.selected_ticket;
      if (selectedTicketId && (!selected || selected.ticket === null)) {
        return { ok: true, reason: 'selected-unavailable', unavailable: true };
      }
      if (selected?.ticket) {
        if (selected.ticket.ref.ticket_id !== selectedTicketId || refKey(selected.ticket.ref) !== this.initialSelectedKey && selectedTicketId === ticketId(this.initialBoard?.selected_ticket)) {
          return { ok: false, reason: 'wrong-selected-ticket', unavailable: false };
        }
        if (selected.presentation?.format !== 1) {
          return { ok: false, reason: 'unsupported-detail-presentation', unavailable: false };
        }
        const fieldKeys = new Set();
        for (const field of selected.fields || []) {
          if (fieldKeys.has(field.id)) {
            return { ok: false, reason: 'duplicate-field', unavailable: false };
          }
          fieldKeys.add(field.id);
        }
        if (editableSignature(selected) !== this.editableSignature) {
          return { ok: false, reason: 'editable-signature-changed', unavailable: false };
        }
      }
      return { ok: true, reason: 'ok', unavailable: false };
    }

    deferOrApply(key, node, apply) {
      if (isHeld(node)) {
        this.deferred.set(key, { node, apply });
        return;
      }
      this.deferred.delete(key);
      apply();
    }

    flushDeferred() {
      if (!this.page.isConnected) {
        this.deferred.clear();
        return;
      }
      for (const [key, pending] of this.deferred) {
        if (!isHeld(pending.node)) {
          this.deferred.delete(key);
          pending.apply();
        }
      }
    }

    clearDeferred() {
      this.deferred.clear();
    }

    renderBoard(board, selectedTicketId) {
      this.initialBoard = board;
      setText(this.heading, board.name || '');
      setText(this.ticketCount, `${board.ticket_count} tickets`);
      setText(this.workingCount, `${board.working_count} working`);
      this.renderFragment(this.boardIssues, board.presentation?.issues_html || '', 'board-issues');
      if (this.columnHost && this.columnTemplate && this.cardTemplate) {
        this.renderColumns(board, selectedTicketId);
      }
      if (board.selected_ticket?.ticket) {
        this.renderTicket(board.selected_ticket);
      }
      this.flushDeferred();
    }

    renderColumns(board, selectedTicketId) {
      const desiredKeys = new Set();
      const desiredCardKeys = new Set();
      const columns = board.columns || [];
      for (const column of columns) {
        for (const card of column.tickets) {
          desiredCardKeys.add(`card:${card.ref.binding_id}:${card.ref.team_id}:${card.ref.workflow_id}:${card.ref.ticket_id}`);
        }
      }
      this.columnHost.style.setProperty('--workflow-column-count', String(columns.length || 1));
      let before = Array.from(this.columnHost.children).find((child) => !(child instanceof HTMLTemplateElement)) || null;
      for (const column of columns) {
        const columnKey = `column:${column.key}`;
        desiredKeys.add(columnKey);
        let columnNode = this.columnHost.querySelector(`[data-view-key="${CSS.escape(columnKey)}"]`);
        if (!columnNode) {
          columnNode = this.columnTemplate.content.firstElementChild.cloneNode(true);
          columnNode.setAttribute('data-view-key', columnKey);
          columnNode.setAttribute('data-column-key', column.key);
          this.columnHost.insertBefore(columnNode, before);
        } else if (columnNode !== before) {
          this.deferOrApply(`column:${columnKey}:move`, columnNode, () => this.columnHost.insertBefore(columnNode, before));
        } else {
          this.deferred.delete(`column:${columnKey}:move`);
        }
        this.updateColumn(columnNode, column, board, selectedTicketId, desiredCardKeys);
        before = columnNode.nextElementSibling;
      }
      for (const columnNode of Array.from(this.columnHost.querySelectorAll('.workflow-column[data-view-key]'))) {
        const key = columnNode.getAttribute('data-view-key');
        if (!desiredKeys.has(key)) {
          this.deferOrApply(`column:${key}:move`, columnNode, () => columnNode.remove());
        }
      }
    }

    updateColumn(columnNode, column, board, selectedTicketId, desiredCardKeys) {
      columnNode.setAttribute('data-column-kind', column.kind);
      columnNode.setAttribute('data-column-key', column.key);
      const header = columnNode.querySelector('.workflow-column-header');
      if (header) {
        header.style.setProperty('--workflow-column-color', column.color || '');
      }
      setText(columnNode.querySelector('[data-column-name]'), column.name || '');
      setText(columnNode.querySelector('[data-column-count]'), column.count);
      let empty = columnNode.querySelector('.workflow-empty-column');
      if (!empty) {
        empty = document.createElement('p');
        empty.className = 'workflow-empty-column';
        setText(empty, 'No tickets');
        columnNode.appendChild(empty);
      }
      empty.hidden = column.tickets.length > 0;
      let before = empty;
      for (const card of column.tickets) {
        const key = `card:${card.ref.binding_id}:${card.ref.team_id}:${card.ref.workflow_id}:${card.ref.ticket_id}`;
        let cardNode = this.cards.get(key);
        if (!cardNode || !cardNode.isConnected) {
          cardNode = this.cardTemplate.content.firstElementChild.cloneNode(true);
          cardNode.setAttribute('data-view-key', key);
          this.cards.set(key, cardNode);
          columnNode.insertBefore(cardNode, before);
        } else if (cardNode.parentElement !== columnNode || cardNode.nextElementSibling !== before) {
          this.deferOrApply(`card:${key}:structure`, cardNode, () => columnNode.insertBefore(cardNode, before));
        } else {
          this.deferred.delete(`card:${key}:structure`);
        }
        this.updateCard(cardNode, card, board, selectedTicketId);
        before = cardNode.nextElementSibling;
      }
      for (const cardNode of Array.from(columnNode.querySelectorAll('.workflow-ticket-card[data-view-key]'))) {
        const key = cardNode.getAttribute('data-view-key');
        if (!desiredCardKeys.has(key)) {
          this.deferOrApply(`card:${key}:structure`, cardNode, () => cardNode.remove());
        }
      }
    }

    updateCard(cardNode, card, board, selectedTicketId) {
      cardNode.setAttribute('data-ticket-id', card.ref.ticket_id);
      cardNode.classList.toggle('is-selected', card.ref.ticket_id === selectedTicketId);
      const href = new URL(this.boardUrl, window.location.origin);
      href.searchParams.set('ticket', card.ref.ticket_id);
      if (board.query) {
        href.searchParams.set('query', board.query);
      } else {
        href.searchParams.delete('query');
      }
      if (board.assignee) {
        href.searchParams.set('assignee', board.assignee);
      } else {
        href.searchParams.delete('assignee');
      }
      cardNode.href = href.toString();
      setText(cardNode.querySelector('[data-card-number]'), `FG-${card.number}`);
      setText(cardNode.querySelector('[data-card-title]'), card.title || '');
      this.renderAssignmentMeta(cardNode.querySelector('[data-card-assignment]'), card.assignee);
      const badge = cardNode.querySelector('[data-card-status]');
      if (badge) {
        badge.classList.toggle('is-working', Boolean(card.active_run_job_id));
        badge.classList.toggle('is-queued', !card.active_run_job_id && Boolean(card.pending_run_job_id));
        setText(badge, card.active_run_job_id ? 'Working' : card.pending_run_job_id ? 'Queued' : 'Idle');
      }
    }

    renderAssignmentMeta(node, assignee) {
      if (!node) {
        return;
      }
      const desired = document.createDocumentFragment();
      if (assignee) {
        const avatar = document.createElement('span');
        avatar.className = 'workflow-ticket-avatar';
        setText(avatar, String(assignee).slice(0, 2).toUpperCase());
        desired.appendChild(avatar);
        desired.appendChild(document.createTextNode(` ${assignee}`));
      } else {
        const template = document.createElement('template');
        template.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 8a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Zm-4 5a4 4 0 0 1 8 0" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>';
        desired.appendChild(template.content.firstElementChild);
        desired.appendChild(document.createTextNode(' Unassigned'));
      }
      if (!nodesMatch(node, desired)) {
        reconcileChildren(node, desired.childNodes);
      }
    }

    renderTicket(detail) {
      const current = detail.ticket || {};
      setText(this.stateLabel, current.state_name || '');
      setText(this.titleHeading, current.title || '');
      if (this.runStatus) {
        this.runStatus.classList.remove('is-working', 'is-queued', 'is-idle');
        this.runStatus.classList.add(current.active_run_job_id ? 'is-working' : current.pending_run_job_id ? 'is-queued' : 'is-idle');
      }
      setText(this.runStatusLabel, current.active_run_job_id ? `Working ${current.active_run_job_id}` : current.pending_run_job_id ? `Queued ${current.pending_run_job_id}` : 'No active run');
      this.renderFragment(this.ticketIssues, detail.presentation?.issues_html || '', 'ticket-issues');
      this.renderFragment(this.description, detail.presentation?.description_html || '', 'description');
      this.renderFragment(this.outputs, detail.presentation?.outputs_html || '', 'outputs');
      this.renderFragment(this.requirements, detail.presentation?.requirements_html || '', 'requirements');
      this.renderFragment(this.history, detail.presentation?.history_html || '', 'history');
      const key = `card:${current.ref?.binding_id}:${current.ref?.team_id}:${current.ref?.workflow_id}:${current.ref?.ticket_id}`;
      const card = this.cards.get(key);
      if (card) {
        this.updateCard(card, current, this.initialBoard, current.ref.ticket_id);
      }
    }

    renderFragment(host, html, key) {
      if (!host) {
        return;
      }
      const desired = parseFragment(html);
      this.deferOrApply(`fragment:${key}`, host, () => {
        if (!nodesMatch(host, desired)) {
          reconcileChildren(host, desired.childNodes);
        }
        host.hidden = !host.textContent.trim() && !host.children.length;
      });
    }
  }

  WorkflowBoardView.refKey = refKey;
  window.WorkflowBoardView = WorkflowBoardView;
})();