'use strict';
(() => {
  // Token lives in memory, not storage/query strings. Remove it immediately.
  let token = location.hash.slice(1);
  history.replaceState(null, '', location.pathname);
  const byId = (id) => document.getElementById(id);
  let state = null;
  let selected = null;
  let busy = false;
  let polling = false;
  let stopped = false;
  let backendBusy = false;
  let backendPrefilled = false;
  let presenceDeadline = 0;
  let reopening = false;
  const controllers = new Set();
  const notice = (message, error = false) => { byId('notice').textContent = message; byId('notice').classList.toggle('error', error); };
  const clearPassword = () => { byId('password').value = ''; };

  function lockSession(message) {
    token = ''; clearPassword();
    byId('presence-confirm').checked = false;
    byId('enable-confirm').checked = false;
    byId('session-recovery').hidden = false;
    byId('session-message').textContent = message;
    notice(message, true); controls();
  }

  async function api(path, data = {}) {
    if (!token || stopped) throw new Error('Reopen the setup app from this Pi’s local launcher.');
    const controller = new AbortController(); controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(path, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        credentials: 'omit', cache: 'no-store', redirect: 'error', body: JSON.stringify(data), signal: controller.signal });
      if (response.status === 401) { const message = 'Setup session expired. Click Open fresh setup window to continue on this Pi.'; lockSession(message); throw new Error(message); }
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.error === 'string' ? body.error : 'The operation did not complete.');
      return body;
    } finally { clearTimeout(timer); controllers.delete(controller); }
  }

  function controls() {
    const locked = !token || stopped;
    const trial = locked || ['authorizing', 'connecting', 'awaiting_confirmation', 'unknown'].includes(state?.task?.state);
    byId('refresh').disabled = busy || locked;
    byId('session-reopen').disabled = reopening || stopped;
    byId('scan').disabled = busy || trial || !state?.scanAvailable;
    byId('connect').disabled = busy || trial || !state?.changesEnabled || !selected || selected.connected || selected.security === 'unsupported';
    byId('password').disabled = busy || trial || !state?.changesEnabled || !selected || selected.security === 'unsupported';
    byId('keep').disabled = busy || locked || state?.task?.state !== 'awaiting_confirmation';
    byId('rollback').disabled = busy || locked || state?.task?.state !== 'awaiting_confirmation';
    byId('enable').disabled = busy || trial || !state?.scanAvailable || state?.changesEnabled || !byId('enable-confirm').checked;
    byId('enable-panel').hidden = !!state?.changesEnabled;
    byId('confirm').disabled = busy || trial || !state?.changesEnabled;
    byId('priority').disabled = busy || trial || !state?.changesEnabled;
    byId('backend-check').disabled = backendBusy || !state || state.preview || locked;
    byId('presence-start').disabled = busy || backendBusy || trial || !state?.presence?.canStart || !byId('presence-confirm').checked || byId('backend-origin').value.trim() !== state?.backend?.origin;
    byId('presence-stop').disabled = busy || !state?.presence?.running || locked;
    byId('connect').textContent = selected?.connected ? 'Already connected' : !state?.changesEnabled ? 'Enable connections first' : !selected ? 'Select a network first' : 'Connect with rollback protection';
  }

  function renderStatus(next) {
    if (!token || stopped) return;
    state = next;
    byId('identity').textContent = `${next.deviceId} · ${next.preview ? 'WINDOWS PREVIEW — NO PI CONNECTED' : 'This Raspberry Pi only'}`;
    byId('wifi').textContent = next.wifiConnected === true ? 'Connected' : next.wifiConnected === false ? 'Disconnected' : 'Unknown';
    byId('internet').textContent = ({ full: 'Reachable · last report', limited: 'Limited', portal: 'Sign-in required', none: 'Not reachable', unknown: 'Unknown' })[next.internet] || 'Unknown';
    byId('manager').textContent = next.interface ? `${next.manager} · ${next.interface}` : next.manager;
    byId('trial-state').textContent = (next.task?.state || 'unknown').replaceAll('_', ' ');
    byId('trial-message').textContent = next.task?.message || 'No verified report.';
    byId('connection-help').textContent = next.preview ? 'Preview only. Network changes are unavailable.' :
      selected?.security === 'unsupported' ? 'This network uses unsupported security. Choose a WPA2/WPA3 personal network.' :
      selected?.connected ? 'This Pi is already connected to the selected network. Use Check Express API below to check dashboard connectivity.' :
      next.connectionMessage || 'Enable connections, then choose a network.';
    if (!backendPrefilled) { byId('backend-origin').value = next.backend?.origin || ''; backendPrefilled = true; }
    renderBackend(next.backend);
    renderPresence(next.presence);
    if (next.preview) notice('Preview only. No synthetic network list, laptop scan or real device operation is used.');
    else if (['failed', 'unknown', 'authorizing'].includes(next.task?.state)) notice(next.task.message, next.task.state !== 'authorizing');
    else if (!next.changesEnabled) notice('Scan networks, then use Enable connections to approve trials on this Pi.');
    else notice('Local connection trials enabled. Verify the new connection and confirm Keep before rollback expires.');
    controls(); countdown();
  }

  async function refresh() {
    if (polling || stopped || !token) return;
    polling = true;
    try { renderStatus(await api('/api/status')); }
    catch (error) { notice(error.message || 'Status unavailable.', true); }
    finally { polling = false; }
  }

  function choose(item, button) {
    selected = item; clearPassword(); byId('confirm').checked = false;
    byId('ssid').value = item.ssid;
    for (const element of byId('networks').querySelectorAll('button')) element.setAttribute('aria-checked', String(element === button));
    controls();
    byId('connection-help').textContent = item.connected ?
      'This Pi is already connected to this network. Use Check Express API below; reconnecting does not start the backend.' :
      item.security === 'unsupported' ? 'This network uses unsupported security. Choose a WPA2/WPA3 personal network.' :
      state?.changesEnabled ? 'Enter this network’s Wi-Fi password and approve the trial.' : 'Use Enable connections to unlock the password and Connect controls.';
    if (!byId('password').disabled) byId('password').focus();
  }

  async function scan() {
    if (busy) return;
    busy = true; selected = null; clearPassword(); byId('ssid').value = ''; controls();
    byId('scan-status').textContent = 'Scanning on this Pi…'; byId('networks').replaceChildren();
    try {
      const result = await api('/api/scan');
      for (const item of result.networks) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'network';
        button.setAttribute('role', 'radio'); button.setAttribute('aria-checked', 'false');
        const content = document.createElement('span'); const name = document.createElement('span'); name.className = 'network-name'; name.textContent = item.ssid;
        const meta = document.createElement('span'); meta.className = 'network-meta'; meta.textContent = `${item.security === 'unsupported' ? 'Unsupported security — no connection' : item.security === 'wpa3-personal' ? 'WPA3 Personal' : 'WPA2 Personal'}${item.connected ? ' · Currently connected' : ''}`;
        const signal = document.createElement('span'); signal.className = 'signal'; signal.textContent = `${item.signal}%`;
        content.append(name, meta); button.append(content, signal); button.addEventListener('click', () => choose(item, button));
        button.addEventListener('keydown', (event) => {
          const buttons = [...byId('networks').querySelectorAll('button')]; const index = buttons.indexOf(button);
          const delta = ['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : ['ArrowUp', 'ArrowLeft'].includes(event.key) ? -1 : 0;
          if (delta) { event.preventDefault(); const next = buttons[(index + delta + buttons.length) % buttons.length]; next.focus(); next.click(); }
        });
        byId('networks').append(button);
      }
      byId('scan-status').textContent = result.networks.length ? `${result.networks.length} network choices found. Select one to continue.` : 'No supported visible networks found. Move nearer to your hotspot and scan again.';
    } catch (error) { byId('scan-status').textContent = 'Scan did not complete.'; notice(error.message, true); }
    finally { busy = false; controls(); }
  }

  byId('connect-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (byId('connect').disabled || !selected) return;
    if (!byId('confirm').checked) { clearPassword(); notice('Confirm that you are at this Pi and approve the trial.', true); return; }
    const payload = { networkId: selected.id, password: byId('password').value, priority: Number(byId('priority').value), confirmed: true };
    clearPassword(); busy = true; controls();
    try { await api('/api/connect', payload); await refresh(); }
    catch (error) { notice(error.message, true); }
    finally { payload.password = ''; busy = false; controls(); }
  });
  for (const [id, path] of [['keep', '/api/keep'], ['rollback', '/api/rollback']]) byId(id).addEventListener('click', async () => {
    if (byId(id).disabled) return;
    busy = true; controls();
    try { await api(path); await refresh(); } catch (error) { notice(error.message, true); }
    finally { busy = false; controls(); }
  });
  byId('scan').addEventListener('click', scan);
  byId('refresh').addEventListener('click', refresh);
  byId('session-reopen').addEventListener('click', async () => {
    if (byId('session-reopen').disabled) return;
    reopening = true; controls();
    const controller = new AbortController(); controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch('/api/session/reopen', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        credentials: 'omit', cache: 'no-store', redirect: 'error', body: '{}', signal: controller.signal });
      const result = await response.json();
      if (!response.ok || result.accepted !== true) throw new Error(result.error || 'Setup window could not be opened.');
      byId('session-message').textContent = 'A setup window was requested on this Pi. Switch to the newly opened window. If none opens, close and relaunch setup-pi.py from the Pi desktop terminal.';
    } catch (error) { byId('session-message').textContent = error.message || 'Close and relaunch setup-pi.py from the Pi desktop terminal.'; }
    finally { clearTimeout(timer); controllers.delete(controller); reopening = false; controls(); }
  });
  byId('enable-confirm').addEventListener('change', controls);
  byId('presence-confirm').addEventListener('change', controls);
  byId('backend-origin').addEventListener('input', () => { byId('presence-confirm').checked = false; controls(); });
  for (const [id, path] of [['presence-start', '/api/presence/start'], ['presence-stop', '/api/presence/stop']]) byId(id).addEventListener('click', async () => {
    if (byId(id).disabled) return;
    busy = true; controls();
    try {
      await api(path, id === 'presence-start' ? { confirmed: byId('presence-confirm').checked } : {});
      byId('presence-confirm').checked = false;
      await refresh();
    } catch (error) { notice(error.message, true); }
    finally { busy = false; controls(); }
  });
  byId('enable').addEventListener('click', async () => {
    if (byId('enable').disabled) return;
    busy = true; clearPassword(); controls();
    try { await api('/api/authorize', { confirmed: byId('enable-confirm').checked }); await refresh(); }
    catch (error) { notice(error.message, true); }
    finally { busy = false; controls(); }
  });
  function renderBackend(report) {
    if (backendBusy) return;
    byId('backend-state').textContent = ({ ready: 'API ready', unreachable: 'Unreachable', unverified: 'Not verified' })[report?.state] || 'Not checked';
    byId('backend-mode').textContent = ({ 'local-setup': 'Setup only', 'real-lan': 'Real LAN', 'legacy-gps': 'GPS enabled' })[report?.mode] || 'Not checked';
    byId('gps-state').textContent = report?.gpsPersistenceEnabled === true ? 'Enabled' : report?.gpsPersistenceEnabled === false ? 'Disabled' : 'Not checked';
    byId('backend-message').textContent = report?.message || 'Express API has not been checked.';
    byId('backend-time').textContent = report?.checkedAt ? `Last check: ${new Date(report.checkedAt * 1000).toLocaleTimeString()}. Check again after a network change.` : '';
  }
  function renderPresence(report) {
    byId('presence-state').textContent = ({ online: 'Setup online', waiting: 'Waiting for check-in', expired: 'Expired', stopped: 'Stopped', not_configured: 'Not configured' })[report?.state] || 'Not checked';
    byId('presence-message').textContent = report?.message || 'This app has not sent an authenticated setup check-in.';
    byId('presence-time').textContent = report?.lastConfirmedAt ? `Last verified acknowledgement: ${new Date(report.lastConfirmedAt).toLocaleTimeString()}. GPS delivery is checked separately.` : '';
    presenceDeadline = report?.state === 'online' ? performance.now() + Math.max(0, report.expiresInSeconds || 0) * 1000 : 0;
  }
  byId('backend-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (byId('backend-check').disabled) return;
    backendBusy = true; controls();
    byId('backend-state').textContent = 'Checking'; byId('backend-message').textContent = 'Checking Express /readyz from this Pi…';
    try {
      const result = await api('/api/backend-check', { origin: byId('backend-origin').value.trim() });
      if (state) state.backend = result.backend;
      byId('backend-origin').value = result.backend.origin;
      backendBusy = false; renderBackend(result.backend);
    } catch (error) {
      byId('backend-state').textContent = 'Not checked'; byId('backend-message').textContent = error.message;
      byId('backend-time').textContent = '';
    } finally { backendBusy = false; controls(); }
  });
  function countdown() {
    if (state?.presence?.state === 'online' && performance.now() >= presenceDeadline) {
      byId('presence-state').textContent = 'Expired';
      byId('presence-message').textContent = 'The last verified setup acknowledgement expired. Waiting for a new verified check-in.';
    }
    const remaining = Math.max(0, Math.ceil((state?.task?.deadlineEpoch || 0) - Date.now() / 1000));
    byId('countdown').textContent = state?.task?.state === 'awaiting_confirmation' ? `${remaining}s left to confirm. If unsure, choose Roll back.` : '';
    if (!remaining && state?.task?.state === 'awaiting_confirmation') byId('keep').disabled = true;
  }
  const pollTimer = setInterval(() => { if (!document.hidden) refresh(); }, 5000);
  const ageTimer = setInterval(countdown, 1000);
  document.addEventListener('visibilitychange', () => { if (document.hidden) clearPassword(); else refresh(); });
  window.addEventListener('pagehide', () => { stopped = true; token = ''; clearPassword(); controllers.forEach((controller) => controller.abort()); clearInterval(pollTimer); clearInterval(ageTimer); });
  byId('session-recovery').hidden = !!token;
  controls();
  if (token) refresh();
  else lockSession('Reloading cleared this tab’s private setup session. Click Open fresh setup window to continue on this Pi.');
})();
