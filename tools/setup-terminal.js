import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

const STATE_LABELS = {
  starting: 'Connecting',
  running: 'Connected',
  exited: 'The setup session exited.',
  stopped: 'The setup session was stopped.',
  unavailable: 'Setup session is no longer available.',
};

const MAX_RETRIES = 6;
const MAX_RETRY_DELAY_MS = 8000;
const POLICY_CLOSE_CODE = 1008;

(() => {
  const container = document.querySelector('#setup-terminal');
  const connectionStatus = document.querySelector('#terminal-connection');
  const relaunchForm = document.querySelector('#terminal-relaunch');

  const terminal = new Terminal({
    fontFamily: 'JetBrains Mono',
    fontSize: 13,
    convertEol: true,
    // Without an explicit linkHandler, xterm's default OSC 8 activation
    // calls window.confirm then window.open for whatever URI the remote
    // process printed; an inert handler keeps links visible/hoverable
    // without letting escape sequences trigger navigation.
    linkHandler: { activate: () => {} },
  });
  const fit = new FitAddon();
  terminal.loadAddon(fit);
  terminal.open(container);
  terminal.options.disableStdin = true;

  const fitAndResize = () => {
    try {
      fit.fit();
    } catch (error) {
      // The container can be mid-teardown (e.g. navigation away); a failed
      // fit here is not actionable and never worth surfacing to the user.
    }
  };
  document.fonts.ready.then(fitAndResize);
  new ResizeObserver(fitAndResize).observe(container);
  window.addEventListener('resize', fitAndResize);

  let socket = null;
  let retryCount = 0;
  let retryTimer = null;
  let stopRetrying = false;

  const setStatus = (text) => {
    if (connectionStatus) connectionStatus.textContent = text;
  };

  const sendSize = () => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'resize', rows: terminal.rows, cols: terminal.cols }));
    }
  };

  const scheduleReconnect = () => {
    if (stopRetrying) return;
    if (retryCount >= MAX_RETRIES) {
      setStatus('Setup session disconnected. Reload the page to try again.');
      return;
    }
    const delay = Math.min(1000 * 2 ** retryCount, MAX_RETRY_DELAY_MS);
    retryCount += 1;
    setStatus('Reconnecting');
    window.clearTimeout(retryTimer);
    retryTimer = window.setTimeout(connect, delay);
  };

  function connect() {
    setStatus('Connecting');
    terminal.options.disableStdin = true;
    // A reconnect always starts from a clean screen: the server replays
    // (possibly truncated) history from scratch, so a stale prior render
    // must never linger underneath it.
    terminal.reset();

    const url = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/setup/session/ws`;
    socket = new WebSocket(url);
    socket.binaryType = 'arraybuffer';

    socket.addEventListener('open', () => {
      retryCount = 0;
    });

    socket.addEventListener('message', ({ data }) => {
      if (typeof data !== 'string') {
        terminal.write(new Uint8Array(data));
        return;
      }
      let state;
      try {
        state = JSON.parse(data);
      } catch (error) {
        return;
      }
      if (state.truncated) {
        setStatus('Earlier terminal output is unavailable.');
      } else {
        setStatus(STATE_LABELS[state.state] || state.state);
      }
      if (state.state === 'running') {
        terminal.options.disableStdin = false;
        // The PTY starts at a fixed default size; send the browser's fitted
        // size as soon as attach is confirmed rather than waiting for a
        // later resize event that may never fire (e.g. the fit already
        // matches the PTY default, so xterm reports no change).
        sendSize();
        if (relaunchForm) relaunchForm.classList.add('hidden');
      } else if (state.state === 'exited' || state.state === 'stopped') {
        // Terminal session states: the user (or the process) ended the
        // session on purpose, so an automatic reconnect would only loop.
        // Leave the final output on screen and offer a way to start a new
        // session rather than stranding the user on a dead terminal.
        terminal.options.disableStdin = true;
        stopRetrying = true;
        if (relaunchForm) relaunchForm.classList.remove('hidden');
      } else if (state.state === 'unavailable') {
        terminal.options.disableStdin = true;
        stopRetrying = true;
      }
    });

    socket.addEventListener('close', (event) => {
      terminal.options.disableStdin = true;
      if (event.code === POLICY_CLOSE_CODE) {
        stopRetrying = true;
        setStatus('Setup access was denied. Reload the page to try again.');
        return;
      }
      scheduleReconnect();
    });
  }

  terminal.onData((data) => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'input', data }));
    }
  });
  terminal.onResize(sendSize);
  // xterm can report non-UTF8 input (e.g. raw mouse reports) here; the
  // control channel only carries JSON text, which has no safe encoding for
  // that, so mouse input is intentionally unsupported rather than mangled.
  terminal.onBinary(() => {});

  fitAndResize();
  connect();
})();
