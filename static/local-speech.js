/* Optional offline voice. Checking status never installs anything. */
(function () {
  'use strict';
  const button = document.getElementById('localSpeechInstall');
  const status = document.getElementById('localSpeechStatus');
  let timer = null;
  let installing = false;
  let requested = false;
  let ready = false;
  let offered = false;
  let pending = null;
  let generation = 0;
  function render(data) {
    installing = data.state === 'installing';
    const wasReady = ready;
    ready = data.state === 'ready';
    if (button) {
      button.disabled = installing || requested || ready;
      button.textContent = installing ? 'Installing offline voice...' : ready ? 'Offline voice ready'
        : data.reason ? 'Repair offline voice (~330 MB)' : 'Install better offline voice (~330 MB)';
    }
    if (status) status.textContent = installing ? (data.progress || 'Installing offline voice...')
      : data.reason || (ready ? 'Ready. Speak can use the offline voice.'
        : 'Optional download (~330 MB). Your Mac voice keeps working during installation.');
    clearTimeout(timer);
    if (installing) timer = setTimeout(check, 1500);
    if (ready && !wasReady) window.dispatchEvent(new Event('ccc-local-speech-ready'));
    return data;
  }
  function check() {
    if (pending) return pending;
    const checkedGeneration = generation;
    pending = fetch('/api/local-speech/status').then(r => {
      if (!r.ok) throw new Error('Voice status is unavailable on this connection.');
      return r.json();
    }).then(data => checkedGeneration === generation ? render(data) : null).catch(() => {
      if (checkedGeneration !== generation) return null;
      if (status) status.textContent = 'Could not check offline voice status. Your Mac voice still works.';
      if (installing) timer = setTimeout(check, 3000);
      return null;
    }).finally(() => {
      pending = null;
      if (installing) { clearTimeout(timer); timer = setTimeout(check, 1500); }
    });
    return pending;
  }
  async function install() {
    if (installing || requested) return;
    requested = true;
    generation += 1;
    if (button) button.disabled = true;
    if (status) status.textContent = 'Starting offline voice installation...';
    try {
      const response = await fetch('/api/local-speech/install', { method: 'POST' });
      if (!response.ok) throw new Error('Install requires a local connection to CCC.');
      requested = false;
      render(await response.json());
    } catch (error) {
      requested = false;
      render({ state: 'failed', reason: error.message });
    }
  }
  async function offer() {
    if (offered || !window.cccPopups || !window.cccPopups.allowed('local-voice-install')) return;
    const data = await check();
    if (!data || offered || data.state !== 'not_installed') return;
    offered = true;
    try {
      if (localStorage.getItem('ccc-local-voice-offered')) return;
      localStorage.setItem('ccc-local-voice-offered', '1');
    } catch (_) {}
    const toast = document.createElement('div');
    toast.id = 'localSpeechOffer';
    toast.setAttribute('role', 'status');
    toast.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:9999;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:12px;display:flex;gap:12px;align-items:center;max-width:90vw;';
    const action = document.createElement('button');
    action.textContent = 'Install better offline voice (~330 MB)';
    action.type = 'button';
    action.onclick = () => { toast.remove(); install(); };
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.textContent = 'Not now';
    dismiss.onclick = () => toast.remove();
    toast.append(action, dismiss);
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 20000);
  }
  if (button) button.addEventListener('click', install);
  window.cccLocalSpeech = { offer: offer };
  check();
})();
