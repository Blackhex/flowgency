import morphdom from 'morphdom';

const VERSION = 1;
const DEFAULT_INTERVAL_MS = 2000;
const DEFAULT_TIMEOUT_MS = 15000;
const STATUS_KINDS = Object.freeze(['healthy', 'stale', 'incompatible', 'unavailable']);

const REGION_SELECTOR = '[data-live-region]';
// Nodes a controller owns: reconciliation never morphs, moves or restyles them.
const OWNED_SELECTOR = [
  'form', 'input', 'select', 'textarea', 'button', 'dialog',
  '[contenteditable]:not([contenteditable="false"])', '.xterm', '[data-live-owned]',
].join(', ');
// Stateful owned content a passive snapshot must never destroy: an item that holds
// one stays until the controller disposes it (then flushDeferred() removes the item).
const PROTECTED_SELECTOR = [
  'form', 'dialog', '[contenteditable]:not([contenteditable="false"])', '.xterm', '[data-live-owned]',
].join(', ');
// Attributes that carry local disclosure state; a server render never overrides them.
const LOCAL_ATTRIBUTES = ['open', 'aria-expanded'];

// Kept in step with the server-side fragment policy in flowgency/web/live.py.
const UNSAFE_TAGS = new Set([
  'script', 'iframe', 'object', 'embed', 'base', 'link', 'style',
  'meta', 'svg', 'math', 'animate', 'set', 'foreignobject',
]);
const UNSAFE_ATTRIBUTES = new Set(['srcdoc', 'autofocus']);
const URL_ATTRIBUTES = new Set([
  'href', 'src', 'action', 'formaction', 'poster', 'data',
  'xlink:href', 'srcset', 'ping', 'background', 'cite', 'manifest',
]);
const UNSAFE_URL_PREFIXES = ['javascript:', 'data:text/html', 'vbscript:'];

const ACCEPTED = Object.freeze({ accepted: true, deferred: false });

// ── Shared helpers ────────────────────────────────────────────────────────

function isHidden() {
  return document.visibilityState === 'hidden';
}

function bindingKey(binding) {
  const source = binding && typeof binding === 'object' ? binding : {};
  const query = {};
  const given = source.query && typeof source.query === 'object' ? source.query : {};
  for (const name of Object.keys(given).sort()) query[name] = String(given[name]);
  return JSON.stringify({
    page: source.page ?? null,
    team: source.team ?? null,
    entity: source.entity ?? null,
    tab: source.tab ?? null,
    query,
  });
}

function isSnapshot(data) {
  return Boolean(data) && typeof data === 'object'
    && data.format === VERSION
    && Boolean(data.binding) && typeof data.binding === 'object'
    && typeof data.structure === 'string'
    && Array.isArray(data.regions)
    && data.regions.every((entry) => entry && typeof entry.key === 'string' && typeof entry.html === 'string');
}

function reportError(error) {
  console.error('FlowgencyLive:', error);
}

// ── Coordinator ───────────────────────────────────────────────────────────

class ReadOnlyMap extends Map {
  set() { throw new TypeError('FlowgencyLive.handles is read-only'); }

  delete() { throw new TypeError('FlowgencyLive.handles is read-only'); }

  clear() { throw new TypeError('FlowgencyLive.handles is read-only'); }
}

const handles = new ReadOnlyMap();
const controls = new Map();

function onVisibilityChange() {
  for (const control of Array.from(controls.values())) {
    if (isHidden()) control.hide();
    else control.show();
  }
}
document.addEventListener('visibilitychange', onVisibilityChange);

// Adapter contract beyond the required binding/url/apply:
//   capture(), isCurrent(captured)  navigation/action generations across body parsing.
//   status(kind), timeout, headers().
//   flushDeferred()  re-apply the retained target; handle.flushDeferred() delegates here.
//     A result with accepted:false means a deferred region was dropped, so the handle
//     clears its ETag and re-reads; the next read is a full 200, never a 304.
//   invalidate()  called by handle.invalidate() before the re-read; a controller that
//     changed its binding passes the new one to LiveRegionView.invalidate(binding) here
//     so a pending target from the previous entity is dropped.
//   dispose().
function register(adapter) {
  if (!adapter || typeof adapter.key !== 'string' || adapter.key === '') {
    throw new TypeError('FlowgencyLive.register requires an adapter with a key');
  }
  for (const name of ['binding', 'url', 'apply']) {
    if (typeof adapter[name] !== 'function') {
      throw new TypeError(`FlowgencyLive adapter "${adapter.key}" requires ${name}()`);
    }
  }
  const { key } = adapter;
  if (handles.has(key)) throw new Error(`FlowgencyLive: "${key}" is already registered`);
  const interval = adapter.interval > 0 ? adapter.interval : DEFAULT_INTERVAL_MS;
  const timeout = adapter.timeout > 0 ? adapter.timeout : DEFAULT_TIMEOUT_MS;

  let timer = null;
  let active = null;
  let readGeneration = 0;
  let actionGeneration = 0;
  let bindingGeneration = 0;
  let openActions = 0;
  let etag = null;
  let status = null;
  let disposed = false;

  const setStatus = (kind) => {
    if (kind === status) return;
    status = kind;
    if (adapter.status) adapter.status(kind);
  };

  const clearTimer = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };

  const abortActive = () => {
    if (active) active.abort();
    active = null;
  };

  const schedule = () => {
    clearTimer();
    if (disposed || openActions > 0 || isHidden()) return;
    timer = setTimeout(() => {
      timer = null;
      read().catch(reportError);
    }, interval);
  };

  // A single passive read; the next one is scheduled only after it settles.
  async function read() {
    if (disposed) return 'disposed';
    if (isHidden()) return 'hidden';
    if (openActions > 0) return 'paused';
    clearTimer();
    abortActive();

    const generation = ++readGeneration;
    const startedBinding = bindingGeneration;
    const startedAction = actionGeneration;
    const controller = new AbortController();
    active = controller;
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    const verdict = () => {
      if (disposed) return 'disposed';
      if (generation !== readGeneration) return 'superseded';
      if (
        startedBinding !== bindingGeneration || startedAction !== actionGeneration
        || openActions > 0 || isHidden()
      ) return 'discarded';
      return null;
    };

    try {
      const captured = adapter.capture ? adapter.capture() : null;
      const expected = bindingKey(adapter.binding());
      const headers = { Accept: 'application/json', ...(adapter.headers ? adapter.headers() : {}) };
      if (etag) headers['If-None-Match'] = etag;
      const response = await fetch(adapter.url(), {
        headers,
        cache: 'no-store',
        credentials: 'same-origin',
        signal: controller.signal,
      });
      let stop = verdict();
      if (stop) return stop;

      if (response.status === 304) {
        setStatus('healthy');
        return 'not-modified';
      }
      if (response.status === 404 || response.status === 410) {
        setStatus('unavailable');
        return 'unavailable';
      }
      if (!response.ok) {
        setStatus('stale');
        return 'failed';
      }

      const text = await response.text();
      stop = verdict();
      if (stop) return stop;
      if (adapter.isCurrent && !adapter.isCurrent(captured)) return 'discarded';

      let data;
      try {
        data = JSON.parse(text);
      } catch {
        setStatus('stale');
        return 'failed';
      }
      if (data && typeof data === 'object' && 'format' in data && data.format !== VERSION) {
        setStatus('incompatible');
        return 'incompatible';
      }
      if (!isSnapshot(data)) {
        setStatus('stale');
        return 'failed';
      }
      if (bindingKey(data.binding) !== expected) {
        setStatus('incompatible');
        return 'incompatible';
      }

      let result;
      try {
        result = adapter.apply(data) || {};
      } catch (error) {
        reportError(error);
        setStatus('stale');
        return 'failed';
      }
      if (result.incompatible) {
        setStatus('incompatible');
        return 'incompatible';
      }
      if (!result.accepted) {
        setStatus('stale');
        return 'rejected';
      }
      etag = response.headers.get('ETag') || null;
      setStatus('healthy');
      return result.deferred ? 'deferred' : 'applied';
    } catch (error) {
      const stop = verdict();
      if (stop) return stop;
      if (!(error && error.name === 'AbortError')) reportError(error);
      setStatus('stale');
      return 'failed';
    } finally {
      clearTimeout(timeoutId);
      if (active === controller) active = null;
      if (generation === readGeneration) schedule();
    }
  }

  const handle = Object.freeze({
    key,
    get status() { return status; },
    get etag() { return etag; },
    refresh: () => read(),
    invalidate() {
      bindingGeneration += 1;
      etag = null;
      abortActive();
      if (adapter.invalidate) {
        try {
          adapter.invalidate();
        } catch (error) {
          reportError(error);
        }
      }
      return read();
    },
    beginAction() {
      if (disposed) return () => {};
      openActions += 1;
      actionGeneration += 1;
      clearTimer();
      abortActive();
      let finished = false;
      return () => {
        if (finished) return;
        finished = true;
        openActions -= 1;
        actionGeneration += 1;
        if (openActions === 0 && !disposed) read().catch(reportError);
      };
    },
    flushDeferred() {
      const result = adapter.flushDeferred ? adapter.flushDeferred() : ACCEPTED;
      if (result && result.accepted === false && !disposed) {
        etag = null;
        read().catch(reportError);
      }
      return result;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      clearTimer();
      abortActive();
      Map.prototype.delete.call(handles, key);
      controls.delete(key);
      if (adapter.dispose) adapter.dispose();
    },
  });

  Map.prototype.set.call(handles, key, handle);
  controls.set(key, {
    hide() {
      clearTimer();
      abortActive();
    },
    show() {
      if (!disposed && openActions === 0) read().catch(reportError);
    },
  });
  schedule();
  return handle;
}

// ── Protected keyed region adapter ────────────────────────────────────────

function isOwned(element) {
  return !element.hasAttribute('data-live-region') && element.matches(OWNED_SELECTOR);
}

function isInsideOwned(node) {
  for (let current = node; current && current.nodeType === Node.ELEMENT_NODE; current = current.parentElement) {
    if (current.hasAttribute('data-live-region')) return false;
    if (current.matches(OWNED_SELECTOR)) return true;
  }
  return false;
}

function isDirtyControl(control) {
  if (control instanceof HTMLSelectElement) {
    const options = Array.from(control.options);
    if (control.multiple) return options.some((option) => option.selected !== option.defaultSelected);
    const defaultIndex = Math.max(0, options.findIndex((option) => option.defaultSelected));
    return control.selectedIndex !== defaultIndex;
  }
  if (control instanceof HTMLTextAreaElement) return control.value !== control.defaultValue;
  if (control instanceof HTMLInputElement) {
    switch (control.type) {
      case 'checkbox':
      case 'radio':
        return control.checked !== control.defaultChecked;
      case 'file':
        return Boolean(control.files && control.files.length > 0);
      case 'button':
      case 'submit':
      case 'reset':
      case 'image':
      case 'hidden':
        return false;
      default:
        return control.value !== control.defaultValue;
    }
  }
  return false;
}

function urlIsUnsafe(name, value) {
  const candidates = name === 'srcset'
    ? value.split(',').map((part) => part.trim().split(/\s+/)[0]).filter(Boolean)
    : [value];
  return candidates.some((candidate) => {
    const normalized = candidate.replace(/[\u0000-\u0020]/g, '').toLowerCase();
    return UNSAFE_URL_PREFIXES.some((prefix) => normalized.startsWith(prefix));
  });
}

function fragmentIsSafe(container) {
  for (const element of container.querySelectorAll('*')) {
    if (UNSAFE_TAGS.has(element.localName.toLowerCase())) return false;
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      if (UNSAFE_ATTRIBUTES.has(name) || name.startsWith('on')) return false;
      if (URL_ATTRIBUTES.has(name) && attribute.value && urlIsUnsafe(name, attribute.value)) return false;
    }
    if (element.localName === 'template' && !fragmentIsSafe(element.content)) return false;
  }
  return true;
}

function significantNodes(parent) {
  return Array.from(parent.childNodes).filter((node) => (
    node.nodeType === Node.ELEMENT_NODE
    || (node.nodeType === Node.TEXT_NODE && /\S/.test(node.nodeValue))
  ));
}

function elementKey(element) {
  return element.getAttribute('data-live-key') || element.id || '';
}

function shapeOf(node) {
  if (node.nodeType === Node.TEXT_NODE) return '#text';
  const key = elementKey(node);
  return key ? `k:${key}|${node.localName}` : `u:${node.localName}`;
}

// Identities pair matching children even when the child list changes shape.
function identities(nodes) {
  const ordinals = new Map();
  const result = new Map();
  for (const node of nodes) {
    if (node.nodeType !== Node.ELEMENT_NODE) continue;
    const shape = shapeOf(node);
    const ordinal = ordinals.get(shape) || 0;
    ordinals.set(shape, ordinal + 1);
    result.set(`${shape}#${ordinal}`, node);
  }
  return result;
}

function localAttributeNames(element) {
  const names = new Set(LOCAL_ATTRIBUTES);
  for (const token of (element.getAttribute('data-live-local') || '').split(/\s+/)) {
    if (token) names.add(token);
  }
  return names;
}

function isScrolledToEnd(element) {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= 2;
}

class LiveRegionView {
  #root;

  #initial;

  #chain = null;

  #pending = null;

  #releaseTimer = null;

  #disposed = false;

  #retained = false;

  #onDrop;

  #onRelease = () => {
    if (!this.#pending || this.#releaseTimer !== null || this.#disposed) return;
    this.#releaseTimer = setTimeout(() => {
      this.#releaseTimer = null;
      const result = this.flushDeferred();
      if (!result.accepted && !this.#disposed && this.#onDrop) {
        try {
          this.#onDrop(result);
        } catch (error) {
          reportError(error);
        }
      }
    }, 0);
  };

  // options.onDrop(result) runs when a release-triggered flush drops a deferred
  // region; wire it to handle.invalidate() so the dropped content converges.
  constructor(root, initial, options = {}) {
    if (!(root instanceof Element)) throw new TypeError('LiveRegionView requires a root element');
    if (!initial || typeof initial.structure !== 'string' || !initial.binding) {
      throw new TypeError('LiveRegionView requires the initial binding and structure');
    }
    this.#root = root;
    this.#initial = initial;
    this.#onDrop = typeof options.onDrop === 'function' ? options.onDrop : null;
    for (const type of ['focusout', 'change', 'input', 'reset', 'submit', 'close', 'toggle']) {
      root.addEventListener(type, this.#onRelease, true);
    }
    document.addEventListener('selectionchange', this.#onRelease);
  }

  apply(snapshot) {
    const rejected = Object.freeze({ accepted: false, deferred: false });
    const incompatible = Object.freeze({ accepted: false, deferred: false, incompatible: true });
    if (this.#disposed || !snapshot || !Array.isArray(snapshot.regions)) return rejected;
    if (snapshot.structure !== this.#initial.structure) return incompatible;

    const elements = this.#regionElements();
    const seen = new Set();
    for (const entry of snapshot.regions) {
      if (!elements.has(entry.key) || seen.has(entry.key)) return incompatible;
      seen.add(entry.key);
    }
    if (seen.size !== elements.size) return incompatible;

    const parsed = new Map();
    for (const entry of snapshot.regions) {
      const template = document.createElement('template');
      template.innerHTML = entry.html;
      if (!fragmentIsSafe(template.content)) return rejected;
      parsed.set(entry.key, template.content);
    }
    for (const [key, fragment] of parsed) {
      if (!this.#compatible(elements.get(key), fragment)) return incompatible;
    }

    const stillDeferred = new Map();
    for (const [key, fragment] of parsed) {
      if (this.#applyRegion(elements.get(key), fragment)) stillDeferred.set(key, fragment);
    }
    this.#pending = stillDeferred.size > 0
      ? { binding: bindingKey(this.#initial.binding), regions: stillDeferred }
      : null;
    return { accepted: true, deferred: stillDeferred.size > 0 };
  }

  setStatus(value) {
    if (!STATUS_KINDS.includes(value)) throw new TypeError(`Unknown live status: ${value}`);
    this.#root.setAttribute('data-live-status', value);
  }

  allowUpdate(current, next) {
    if (isInsideOwned(current)) return false;
    const chain = this.#chain || this.#chainFor(current.closest(REGION_SELECTOR));
    if (chain.has(current)) return false;
    for (const name of localAttributeNames(current)) {
      if (current.hasAttribute(name)) next.setAttribute(name, current.getAttribute(name));
      else next.removeAttribute(name);
    }
    return true;
  }

  allowDiscard(node) {
    if (node.nodeType !== Node.ELEMENT_NODE) return true;
    const chain = this.#chain || this.#chainFor(node.closest(REGION_SELECTOR));
    if (chain.has(node) || node.matches(PROTECTED_SELECTOR) || node.querySelector(PROTECTED_SELECTOR)) {
      this.#retained = true;
      return false;
    }
    return true;
  }

  // A controller that navigated calls this with its new binding (and structure) so a
  // pending target from the previous entity is never applied to the next one.
  invalidate(binding, structure) {
    if (this.#releaseTimer !== null) clearTimeout(this.#releaseTimer);
    this.#releaseTimer = null;
    this.#pending = null;
    this.#initial = {
      ...this.#initial,
      ...(binding ? { binding } : {}),
      ...(typeof structure === 'string' ? { structure } : {}),
    };
  }

  flushDeferred() {
    if (this.#releaseTimer !== null) clearTimeout(this.#releaseTimer);
    this.#releaseTimer = null;
    const pending = this.#pending;
    if (!pending) return { accepted: true, deferred: false };
    if (this.#disposed || pending.binding !== bindingKey(this.#initial.binding)) {
      this.#pending = null;
      return { accepted: false, deferred: false };
    }
    const elements = this.#regionElements();
    const stillDeferred = new Map();
    let dropped = false;
    for (const [key, fragment] of pending.regions) {
      const element = elements.get(key);
      if (!element || !this.#compatible(element, fragment)) {
        dropped = true;
        continue;
      }
      if (this.#applyRegion(element, fragment)) stillDeferred.set(key, fragment);
    }
    this.#pending = stillDeferred.size > 0 ? { binding: pending.binding, regions: stillDeferred } : null;
    return { accepted: !dropped, deferred: stillDeferred.size > 0 };
  }

  dispose() {
    if (this.#disposed) return;
    this.#disposed = true;
    if (this.#releaseTimer !== null) clearTimeout(this.#releaseTimer);
    this.#releaseTimer = null;
    this.#pending = null;
    for (const type of ['focusout', 'change', 'input', 'reset', 'submit', 'close', 'toggle']) {
      this.#root.removeEventListener(type, this.#onRelease, true);
    }
    document.removeEventListener('selectionchange', this.#onRelease);
  }

  #regionElements() {
    const elements = new Map();
    const candidates = [];
    if (this.#root.matches(REGION_SELECTOR)) candidates.push(this.#root);
    candidates.push(...this.#root.querySelectorAll(REGION_SELECTOR));
    for (const element of candidates) {
      const key = element.getAttribute('data-live-region');
      if (!elements.has(key)) elements.set(key, element);
    }
    return elements;
  }

  // Same-key elements must keep their tag and each key may appear only once.
  #compatible(region, fragment) {
    const nextTags = new Map();
    for (const element of fragment.querySelectorAll('[data-live-key]')) {
      const key = element.getAttribute('data-live-key');
      if (nextTags.has(key)) return false;
      nextTags.set(key, element.localName);
    }
    for (const element of region.querySelectorAll('[data-live-key]')) {
      if (isInsideOwned(element)) continue;
      const tag = nextTags.get(element.getAttribute('data-live-key'));
      if (tag !== undefined && tag !== element.localName) return false;
    }
    return true;
  }

  // Elements holding focus, a dirty control, an open dialog or a selection.
  #signals(region) {
    const signals = new Set();
    const active = document.activeElement;
    if (active && active !== document.body && active !== region && region.contains(active)) signals.add(active);
    for (const control of region.querySelectorAll('input, select, textarea')) {
      if (isDirtyControl(control)) signals.add(control);
    }
    for (const element of region.querySelectorAll('dialog[open], [data-live-hold], [data-live-dirty]')) {
      signals.add(element);
    }
    const selection = window.getSelection();
    if (selection && selection.rangeCount > 0 && !selection.isCollapsed) {
      const range = selection.getRangeAt(0);
      if (range.intersectsNode(region)) {
        for (const element of region.querySelectorAll('*')) {
          if (range.intersectsNode(element)) signals.add(element);
        }
      }
    }
    return signals;
  }

  #chainFor(region) {
    const chain = new Set();
    if (!region) return chain;
    for (const signal of this.#signals(region)) {
      for (let node = signal; node && node !== region; node = node.parentElement) chain.add(node);
    }
    return chain;
  }

  #selectionTouches(textNode) {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return false;
    return selection.getRangeAt(0).intersectsNode(textNode);
  }

  #applyRegion(region, fragment) {
    const next = region.cloneNode(false);
    next.append(fragment.cloneNode(true));
    const chain = this.#chainFor(region);
    const following = [region, ...region.querySelectorAll('[data-live-follow]')]
      .filter((element) => element.hasAttribute('data-live-follow'))
      .map((element) => ({ element, follow: isScrolledToEnd(element) }));

    let deferred = false;
    this.#chain = chain;
    this.#retained = false;
    try {
      if (chain.size === 0) this.#morph(region, next, true);
      else deferred = this.#reconcileChildren(region, next);
    } finally {
      this.#chain = null;
    }
    if (this.#retained) deferred = true;
    this.#retained = false;
    for (const { element, follow } of following) {
      if (follow && element.isConnected) element.scrollTop = element.scrollHeight;
    }
    return deferred;
  }

  #morph(current, next, childrenOnly) {
    morphdom(current, next, {
      childrenOnly,
      getNodeKey: (node) => {
        if (node.nodeType !== Node.ELEMENT_NODE || isInsideOwned(node)) return undefined;
        return elementKey(node) || undefined;
      },
      onBeforeElUpdated: (from, to) => this.allowUpdate(from, to),
      onBeforeNodeDiscarded: (node) => this.allowDiscard(node),
    });
  }

  // Containers on a held path never get structural operations, so a held node
  // keeps its parent and position; a changed child list defers to the latest
  // target while matched children are still patched individually.
  #reconcileChildren(current, next) {
    const currentNodes = significantNodes(current);
    const nextNodes = significantNodes(next);
    const currentShape = currentNodes.map(shapeOf);
    const nextShape = nextNodes.map(shapeOf);
    const sameShape = currentShape.length === nextShape.length
      && currentShape.every((shape, index) => shape === nextShape[index]);

    let deferred = false;
    if (sameShape) {
      currentNodes.forEach((node, index) => {
        const counterpart = nextNodes[index];
        if (node.nodeType === Node.TEXT_NODE) {
          if (node.nodeValue === counterpart.nodeValue) return;
          if (this.#selectionTouches(node)) deferred = true;
          else node.nodeValue = counterpart.nodeValue;
        } else if (this.#reconcileElement(node, counterpart)) {
          deferred = true;
        }
      });
      return deferred;
    }

    const counterparts = identities(nextNodes);
    for (const [identity, node] of identities(currentNodes)) {
      const counterpart = counterparts.get(identity);
      if (counterpart) this.#reconcileElement(node, counterpart);
    }
    return true;
  }

  #reconcileElement(current, next) {
    if (isOwned(current)) return false;
    if (!this.#chain.has(current)) {
      this.#morph(current, next, false);
      return false;
    }
    const local = localAttributeNames(current);
    for (const attribute of Array.from(next.attributes)) {
      if (!local.has(attribute.name) && current.getAttribute(attribute.name) !== attribute.value) {
        current.setAttribute(attribute.name, attribute.value);
      }
    }
    for (const attribute of Array.from(current.attributes)) {
      if (!local.has(attribute.name) && !next.hasAttribute(attribute.name)) {
        current.removeAttribute(attribute.name);
      }
    }
    return this.#reconcileChildren(current, next);
  }
}

if (!window.FlowgencyLive) {
  window.FlowgencyLive = Object.freeze({
    version: VERSION,
    register,
    handles,
    LiveRegionView,
  });
}
