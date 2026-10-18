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
  const controllers = new Set();
  const notice = (message, error = false) => { byId('notice').textContent = message; byId('notice').classList.toggle('error', error); };
  const clearPassword = () => { byId('password').value = ''; };

  async function api(path, data = {}) {
    if (!token || stopped) throw new Error('Reopen the setup app from this Pi’s local launcher.');
    const controller = new AbortController(); controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(path, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        credentials: 'omit', cache: 'no-store', redirect: 'error', body: JSON.stringify(data), signal: controller.signal });
      if (response.status === 401) { token = ''; clearPassword(); throw new Error('Session expired. Reopen the local setup app.'); }
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.error === 'string' ? body.error : 'The operation did not complete.');
      return body;
    } finally { clearTimeout(timer); controllers.delete(controller); }
  }

  function controls() {
    const trial = ['connecting', 'awaiting_confirmation', 'unknown'].includes(state?.task?.state);
    byId('scan').disabled = busy || trial || !state?.scanAvailable;
    byId('connect').disabled = busy || trial || !state?.changesEnabled || !selected || selected.security === 'unsupported';
    byId('password').disabled = busy || trial || !state?.changesEnabled || !selected || selected.security === 'unsupported';
    byId('keep').disabled = busy || state?.task?.state !== 'awaiting_confirmation';
    byId('rollback').disabled = busy || state?.task?.state !== 'awaiting_confirmation';
  }

  function renderStatus(next) {
    state = next;
    byId('identity').textContent = `${next.deviceId} · ${next.preview ? 'WINDOWS PREVIEW — NO PI CONNECTED' : 'This Raspberry Pi only'}`;
    byId('wifi').textContent = next.wifiConnected === true ? 'Connected' : next.wifiConnected === false ? 'Disconnected' : 'Unknown';
    byId('internet').textContent = ({ full: 'Reachable · last report', limited: 'Limited', portal: 'Sign-in required', none: 'Not reachable', unknown: 'Unknown' })[next.internet] || 'Unknown';
    byId('manager').textContent = next.interface ? `${next.manager} · ${next.interface}` : next.manager;
    byId('trial-state').textContent = (next.task?.state || 'unknown').replaceAll('_', ' ');
    byId('trial-message').textContent = next.task?.message || 'No verified report.';
    if (next.preview) notice('Preview only. No synthetic network list, laptop scan or real device operation is used.');
    else if (!next.changesEnabled) notice('Read-only session. Scanning is available; connection changes require explicit local operator approval.');
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
  function countdown() {
    const remaining = Math.max(0, Math.ceil((state?.task?.deadlineEpoch || 0) - Date.now() / 1000));
    byId('countdown').textContent = state?.task?.state === 'awaiting_confirmation' ? `${remaining}s left to confirm. If unsure, choose Roll back.` : '';
    if (!remaining && state?.task?.state === 'awaiting_confirmation') byId('keep').disabled = true;
  }
  const pollTimer = setInterval(() => { if (!document.hidden) refresh(); }, 5000);
  const ageTimer = setInterval(countdown, 1000);
  document.addEventListener('visibilitychange', () => { if (document.hidden) clearPassword(); else refresh(); });
  window.addEventListener('pagehide', () => { stopped = true; token = ''; clearPassword(); controllers.forEach((controller) => controller.abort()); clearInterval(pollTimer); clearInterval(ageTimer); });
  controls(); refresh();
})();
