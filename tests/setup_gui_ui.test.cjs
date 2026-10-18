'use strict';
// Actual frontend with a tiny DOM boundary; no browser, network or OS operations.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'candidate/setup_gui/web/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'candidate/setup_gui/web/index.html'), 'utf8');
class Element {
  constructor() { this.value = ''; this.checked = false; this.disabled = false; this.children = []; this.events = {}; this.attrs = {}; this.classList = { toggle() {} }; }
  addEventListener(name, handler) { this.events[name] = handler; }
  append(...items) { this.children.push(...items); }
  replaceChildren() { this.children = []; }
  setAttribute(name, value) { this.attrs[name] = value; }
  querySelectorAll() { return this.children; }
  focus() {}
}
async function fixture(overrides = {}) {
  let monotonic = 0;
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map((match) => [match[1], new Element()]));
  elements.priority.value = '50';
  let status = { deviceId: 'GPS-001', preview: false, changesEnabled: false, scanAvailable: true,
    wifiConnected: true, internet: 'full', manager: 'NetworkManager', interface: 'wlan0',
    connectionMessage: 'Use Enable connections.', task: { state: 'idle', message: 'No changes.' },
    presence: { state: 'not_configured', message: 'This app has not sent an authenticated setup check-in.' },
    backend: { origin: 'http://192.168.1.5:5000', state: 'not_checked', message: 'Not checked.' }, ...overrides };
  const requests = [];
  const intervals = [];
  const document = { hidden: false, getElementById(id) { assert.ok(elements[id], `missing HTML element ${id}`); return elements[id]; },
    createElement() { return new Element(); }, addEventListener() {} };
  const sandbox = { document, location: { hash: '#synthetic-session', pathname: '/' }, history: { replaceState() {} },
    window: { addEventListener() {} }, AbortController, Date, Set, performance: { now() { return monotonic; } },
    setTimeout() { return 1; }, clearTimeout() {}, clearInterval() {}, setInterval(fn) { intervals.push(fn); return intervals.length; },
    async fetch(url, options) {
      const body = JSON.parse(options.body); requests.push({ url, body });
      let result = {};
      if (url === '/api/status') result = status;
      if (url === '/api/authorize') { status = { ...status, changesEnabled: true }; result = { accepted: true }; }
      if (url === '/api/scan') result = { networks: [
        { id: 'chosen', ssid: 'Depot', security: 'wpa2-personal', signal: 80, connected: false },
        { id: 'active', ssid: 'Current', security: 'wpa2-personal', signal: 70, connected: true }] };
      if (url === '/api/connect') { status = { ...status, task: { state: 'awaiting_confirmation', deadlineEpoch: Date.now() / 1000 + 30 } }; result = { accepted: true }; }
      if (url === '/api/backend-check') result = { backend: { origin: body.origin, state: 'unreachable', message: 'Laptop backend unreachable.' } };
      if (url === '/api/presence/start') { status = { ...status, presence: { state: 'online', configured: true, canStart: false, running: true, active: true, expiresInSeconds: 44, lastConfirmedAt: '2026-10-18T04:00:00.123Z', message: 'Verified check-in.' } }; result = { presence: status.presence }; }
      if (url === '/api/presence/stop') { status = { ...status, presence: { state: 'stopped', configured: true, canStart: true, running: false, active: false } }; result = { presence: status.presence }; }
      return { status: 200, ok: true, async json() { return result; } };
    } };
  vm.runInNewContext(source, sandbox, { filename: 'app.js' });
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  await settle();
  return { elements, requests, intervals, settle, advance(ms) { monotonic += ms; }, update(next) { status = { ...status, ...next }; },
    async fire(id, event = 'click') { assert.ok(elements[id].events[event], `missing handler ${id}`); await elements[id].events[event]({ preventDefault() {} }); await settle(); } };
}

test('read-only session enables Connect after explicit approval without relaunch', async () => {
  const f = await fixture(); const e = f.elements;
  assert.equal(e.connect.disabled, true);
  assert.equal(e.enable.disabled, true);
  e['enable-confirm'].checked = true; await f.fire('enable-confirm', 'change');
  assert.equal(e.enable.disabled, false);
  await f.fire('enable');
  assert.deepEqual(f.requests.find((r) => r.url === '/api/authorize').body, { confirmed: true });
  assert.equal(e['enable-panel'].hidden, true);
  await f.fire('scan'); await e.networks.children[0].events.click();
  assert.equal(e.ssid.value, 'Depot'); assert.equal(e.password.disabled, false); assert.equal(e.connect.disabled, false);
  e.password.value = 'synthetic-wifi-only'; e.confirm.checked = true;
  await f.fire('connect-form', 'submit');
  assert.equal(f.requests.find((r) => r.url === '/api/connect').body.password, 'synthetic-wifi-only');
  assert.equal(e.password.value, ''); assert.equal(e.keep.disabled, false);
});

test('already connected selection points to API check rather than another profile', async () => {
  const f = await fixture({ changesEnabled: true });
  await f.fire('scan'); await f.elements.networks.children[1].events.click();
  assert.equal(f.elements.connect.disabled, true); assert.equal(f.elements.connect.textContent, 'Already connected');
  assert.match(f.elements['connection-help'].textContent, /Check Express API/);
  assert.equal(f.requests.filter((r) => r.url === '/api/backend-check').length, 0);
  await f.fire('backend-form', 'submit');
  assert.equal(f.elements['backend-state'].textContent, 'Unreachable');
  assert.match(f.elements['backend-message'].textContent, /unreachable/);
});

test('native authorization failures stay actionable across status refreshes', async () => {
  const message = 'No desktop authentication agent. Open the setup from the Pi desktop terminal.';
  const f = await fixture({ task: { state: 'failed', message }, connectionMessage: message });
  assert.equal(f.elements.notice.textContent, message);
  await f.fire('refresh');
  assert.equal(f.elements.notice.textContent, message);
  assert.equal(f.elements.connect.disabled, true);
});

test('preview never enables connection or backend operations', async () => {
  const f = await fixture({ preview: true, scanAvailable: false });
  f.elements['enable-confirm'].checked = true; await f.fire('enable-confirm', 'change');
  assert.equal(f.elements.enable.disabled, true); assert.equal(f.elements['backend-check'].disabled, true);
  assert.equal(f.requests.length, 1);
});

test('setup-only API readiness is separate from GPS writes and dashboard check-in', async () => {
  const f = await fixture({ backend: { state: 'ready', mode: 'local-setup', gpsPersistenceEnabled: false,
    setupPresenceEnabled: true, message: 'Express setup API is ready.' } });
  assert.equal(f.elements['backend-state'].textContent, 'API ready');
  assert.equal(f.elements['backend-mode'].textContent, 'Setup only');
  assert.equal(f.elements['gps-state'].textContent, 'Disabled');
  assert.equal(f.elements['presence-state'].textContent, 'Not configured');
  assert.match(f.elements['presence-message'].textContent, /has not sent/);
  assert.deepEqual(f.requests.map((request) => request.url), ['/api/status']);
});

test('GPS readiness never becomes confirmed dashboard presence and resets on a failed check', async () => {
  const f = await fixture({ backend: { state: 'ready', mode: 'real-lan', gpsPersistenceEnabled: true } });
  assert.equal(f.elements['gps-state'].textContent, 'Enabled');
  assert.equal(f.elements['presence-state'].textContent, 'Not configured');
  await f.fire('backend-form', 'submit');
  assert.equal(f.elements['backend-state'].textContent, 'Unreachable');
  assert.equal(f.elements['backend-mode'].textContent, 'Not checked');
  assert.equal(f.elements['gps-state'].textContent, 'Not checked');
  assert.equal(f.elements['presence-state'].textContent, 'Not configured');
});

test('authenticated check-ins require explicit approval and stay separate from GPS writes', async () => {
  const f = await fixture({ backend: { origin: 'http://192.168.1.5:5000', state: 'ready', mode: 'local-setup', gpsPersistenceEnabled: false },
    presence: { state: 'stopped', configured: true, canStart: true, running: false } });
  assert.equal(f.elements['presence-start'].disabled, true);
  f.elements['backend-origin'].value = 'http://192.168.1.6:5000';
  await f.fire('backend-origin', 'input');
  f.elements['presence-confirm'].checked = true; await f.fire('presence-confirm', 'change');
  assert.equal(f.elements['presence-start'].disabled, true);
  f.elements['backend-origin'].value = 'http://192.168.1.5:5000';
  assert.equal(f.requests.length, 1);
  f.elements['presence-confirm'].checked = true; await f.fire('presence-confirm', 'change');
  assert.equal(f.elements['presence-start'].disabled, false);
  await f.fire('presence-start');
  assert.deepEqual(f.requests.find((request) => request.url === '/api/presence/start').body, { confirmed: true });
  assert.equal(f.elements['presence-state'].textContent, 'Setup online');
  assert.equal(f.elements['gps-state'].textContent, 'Disabled');
  assert.equal(f.elements['presence-stop'].disabled, false);
  await f.fire('presence-stop');
  assert.equal(f.elements['presence-state'].textContent, 'Stopped');
  assert.equal(f.elements['presence-start'].disabled, true);
});

test('setup online expires locally without waiting for a status poll', async () => {
  const f = await fixture({ presence: { state: 'online', running: true, expiresInSeconds: 1 } });
  assert.equal(f.elements['presence-state'].textContent, 'Setup online');
  f.advance(1001); f.intervals[1]();
  assert.equal(f.elements['presence-state'].textContent, 'Expired');
});
