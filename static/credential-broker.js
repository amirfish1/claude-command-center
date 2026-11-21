/* Credential prompts deliberately bypass chat, event feeds and browser storage. */
(() => {
  'use strict';
  const cards = new Map();
  const host = document.createElement('section');
  host.id = 'credentialBroker';
  host.setAttribute('aria-label', 'Credential requests');
  host.hidden = true;
  const heading = document.createElement('h2');
  heading.id = 'credentialBrokerHeading';
  heading.setAttribute('aria-live', 'polite');
  host.append(heading);
  document.body.append(host);
  let busy = false;
  let timer;

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function clearValue(card) {
    const input = card.querySelector('.credential-value');
    input.value = '';
    input.type = 'password';
    card.querySelector('.credential-visibility input').checked = false;
  }

  function clearCard(id) {
    const card = cards.get(id);
    if (!card) return;
    clearValue(card);
    card.remove();
    cards.delete(id);
  }

  function updateCount() {
    host.hidden = cards.size === 0;
    heading.textContent = `${cards.size} credential request${cards.size === 1 ? '' : 's'}`;
  }

  function makeCard(request) {
    const form = element('form', '', 'credential-card');
    form.dataset.requestId = request.id;
    form.dataset.expiresAt = String(request.expires_at * 1000);
    form.autocomplete = 'off';
    form.append(element('h3', request.name));
    form.append(element('p', request.reason, 'credential-reason'));
    form.append(element('p', request.session_id ? `Session (reported): ${request.session_id}` : 'Local program · no session provided', 'credential-session'));
    const expiry = new Date(request.expires_at * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    form.append(element('p', `Expires at ${expiry} · ${request.store_only ? 'Stored in the Vault' : 'Delivered once'}, outside chat.`, 'credential-note'));
    const approval = element('div');
    const label = element('label', request.kind === 'otp' ? 'One-time code' : 'Credential');
    const input = element('input', '', 'credential-value');
    input.type = 'password';
    input.id = `credential-${request.id}`;
    input.autocomplete = 'new-password';
    input.spellcheck = false;
    input.required = true;
    input.maxLength = 16384;
    input.setAttribute('autocapitalize', 'none');
    label.htmlFor = input.id;
    approval.append(label, input);
    const visibilityLabel = element('label', '', 'credential-visibility');
    const visibility = element('input');
    visibility.type = 'checkbox';
    visibility.setAttribute('aria-controls', input.id);
    visibility.addEventListener('change', () => {
      input.type = visibility.checked ? 'text' : 'password';
    });
    visibilityLabel.append(visibility, document.createTextNode('View hidden'));
    approval.append(visibilityLabel);
    const remember = element('input');
    remember.type = 'checkbox';
    if (request.store_only) {
      approval.append(element('p', 'Saved to the CCC Vault. The requesting program is told only the entry name.', 'credential-note'));
    } else {
      const rememberLabel = element('label', '', 'credential-remember');
      remember.disabled = request.kind === 'otp';
      rememberLabel.append(remember, document.createTextNode('Remember for this service on this machine'));
      approval.append(rememberLabel);
      approval.append(element('p', request.kind === 'otp'
        ? 'One-time codes are never saved.'
        : 'Optional: save in the CCC Vault. Future requests for this service and kind reuse it.', 'credential-note'));
    }
    const error = element('p', '', 'credential-error');
    error.setAttribute('role', 'status');
    const actions = element('div', '', 'credential-actions');
    const deny = element('button', 'Deny');
    deny.type = 'button';
    const submit = element('button', 'Submit credential');
    submit.type = 'submit';
    actions.append(deny, submit);
    approval.append(actions);

    const denial = element('div');
    denial.hidden = true;
    const noteLabel = element('label', 'Reason for denial (optional)');
    const note = element('textarea', '', 'credential-denial-note');
    note.rows = 2;
    note.maxLength = 500;
    note.id = `credential-denial-${request.id}`;
    noteLabel.htmlFor = note.id;
    const denialActions = element('div', '', 'credential-actions');
    const back = element('button', 'Back');
    back.type = 'button';
    const confirmDeny = element('button', 'Deny request');
    confirmDeny.type = 'button';
    denialActions.append(back, confirmDeny);
    denial.append(noteLabel, note, denialActions);
    form.append(approval, denial, error);

    async function answer(denied) {
      if (form.dataset.sending === '1') return;
      if (!denied && !form.reportValidity()) return;
      form.dataset.sending = '1';
      submit.disabled = deny.disabled = confirmDeny.disabled = back.disabled = true;
      error.textContent = '';
      // Only this request body contains the value. Clear the input immediately,
      // including on transport failure; never retain a retry copy in the DOM.
      let body = JSON.stringify(denied ? {id: request.id, deny: true, note: note.value.trim()} : {
        id: request.id, value: input.value, remember: remember.checked,
      });
      clearValue(form);
      try {
        const response = await fetch('/api/secrets/resolve', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          cache: 'no-store', credentials: 'same-origin', body,
        });
        body = '';
        if (response.ok || response.status === 409 || response.status === 410) {
          clearCard(request.id);
          updateCount();
          poll();
        } else {
          error.textContent = denied ? 'Could not deny the request. Try again.' :
            'Could not submit. Check the connection and storage permissions, then enter it again.';
        }
      } catch (_) {
        error.textContent = denied ? 'Connection interrupted. Try denying the request again.' :
          'Connection interrupted. Enter the credential again to retry.';
      } finally {
        body = '';
        form.dataset.sending = '';
        submit.disabled = deny.disabled = confirmDeny.disabled = back.disabled = false;
      }
    }
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (denial.hidden) answer(false);
    });
    deny.addEventListener('click', () => {
      clearValue(form);
      error.textContent = '';
      approval.hidden = true;
      denial.hidden = false;
      note.focus();
    });
    back.addEventListener('click', () => {
      note.value = '';
      error.textContent = '';
      denial.hidden = true;
      approval.hidden = false;
      deny.focus();
    });
    confirmDeny.addEventListener('click', () => answer(true));
    return form;
  }

  async function poll() {
    clearTimeout(timer);
    for (const [id, card] of cards) {
      if (Number(card.dataset.expiresAt) <= Date.now()) clearCard(id);
    }
    updateCount();
    if (busy || document.hidden) return;
    busy = true;
    try {
      const response = await fetch('/api/secrets/pending', {
        // Short-lived prompts must surface even while startup parks background reads.
        cache: 'no-store', credentials: 'same-origin', cccPriority: 'user',
      });
      if (response.ok) {
        const data = await response.json();
        const ids = new Set(data.requests.map((r) => r.id));
        for (const id of cards.keys()) if (!ids.has(id)) clearCard(id);
        for (const request of data.requests) {
          if (!cards.has(request.id)) {
            const card = makeCard(request);
            cards.set(request.id, card);
            host.append(card);
          }
        }
        updateCount();
      }
    } catch (_) { /* next visible poll retries; never log request objects */ }
    finally {
      busy = false;
      if (!document.hidden) timer = setTimeout(poll, 4000);
    }
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      clearTimeout(timer);
      for (const card of cards.values()) clearValue(card);
    } else poll();
  });
  window.addEventListener('pagehide', () => {
    clearTimeout(timer);
    for (const id of cards.keys()) clearCard(id);
  });
  window.addEventListener('pageshow', poll);
  poll();
})();
