(function () {
  'use strict';
  if (window.cccDomesticProviders) return;

  function node(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function styles() {
    if (document.getElementById('ccc-domestic-css')) return;
    const link = node('link');
    link.id = 'ccc-domestic-css';
    link.rel = 'stylesheet';
    link.href = '/static/domestic-providers.css';
    document.head.appendChild(link);
  }

  async function api(url, body) {
    const response = await fetch(url, body === undefined ? { cache: 'no-store' } : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || (response.status === 404
      ? 'Paid-key presets are not available in this server yet.' : 'That did not work. Try again.'));
    return data;
  }

  function link(text, url) {
    const el = node('a', 'dp-link', text);
    el.href = url;
    el.target = '_blank';
    el.rel = 'noopener noreferrer';
    return el;
  }

  function groupPresets(presets) {
    const groups = new Map();
    presets.forEach((preset) => {
      if (!groups.has(preset.family)) groups.set(preset.family, []);
      groups.get(preset.family).push(preset);
    });
    return [...groups.values()];
  }

  function card(presets, controller) {
    const el = node('article', 'dp-card');
    el.dataset.family = presets[0].family;
    let selected = presets[0];
    let busy = false;
    let removePending = false;

    function render() {
      el.replaceChildren();
      el.dataset.preset = selected.id;
      const heading = node('div', 'dp-heading');
      heading.append(node('h4', '', selected.name), node('span', 'dp-paid', 'Paid'));
      el.append(heading);
      const regionLabel = node('label', 'dp-label', 'Account region');
      const region = node('select', 'dp-select');
      region.setAttribute('aria-label', selected.name + ' account region');
      presets.forEach((p) => {
        const opt = node('option', '', p.region);
        opt.value = p.id;
        opt.selected = p.id === selected.id;
        region.append(opt);
      });
      region.disabled = busy || presets.length === 1;
      region.addEventListener('change', () => {
        selected = presets.find((p) => p.id === region.value);
        removePending = false;
        render();
      });
      if (presets.length === 1) {
        // One shared endpoint: a disabled dropdown reads as broken, so say it plainly.
        regionLabel.append(node('div', 'dp-region-fixed', selected.region));
      } else {
        regionLabel.append(region);
      }
      el.append(regionLabel, node('p', 'dp-note', selected.region_note), node('p', 'dp-price', selected.price_note));
      const modelLabel = node('label', 'dp-label', 'Available models');
      const models = node('select', 'dp-select');
      models.setAttribute('aria-label', selected.name + ' model');
      selected.models.forEach((model) => {
        const opt = node('option', '', model);
        opt.value = model;
        models.append(opt);
      });
      modelLabel.append(models);
      el.append(modelLabel);
      const details = node('details', 'dp-details');
      details.append(node('summary', '', 'Endpoint and official guide'), node('code', 'dp-endpoint', selected.base_url),
        link('Read the official guide', selected.docs_url));
      el.append(details);
      const signup = link('Get an API key', selected.signup_url);
      signup.dataset.dpSignup = '';
      el.append(signup);
      const saved = node('p', 'dp-saved', selected.configured ? 'Key saved for this region. Not tested yet.' : 'No key saved for this region.');
      saved.dataset.dpSaved = '';
      el.append(saved);
      const label = node('label', 'dp-label', selected.configured ? 'Replace API key' : 'API key');
      const input = node('input', 'dp-input');
      input.type = 'password';
      input.placeholder = selected.key_hint;
      input.autocomplete = 'off';
      input.spellcheck = false;
      input.maxLength = 4096;
      input.setAttribute('aria-label', selected.name + ' ' + selected.region + ' API key');
      label.append(input);
      el.append(label);
      const actions = node('div', 'dp-actions');
      const save = node('button', 'dp-button', 'Save key');
      save.type = 'button';
      save.dataset.dpSave = '';
      const message = node('p', 'dp-message');
      message.setAttribute('role', 'status');
      message.setAttribute('aria-live', 'polite');
      const instructions = node('p', 'dp-next');
      function showNext() {
        instructions.textContent = selected.configured
          ? 'To run: start a new session, choose Claude, then ' + models.value + ' for ' + selected.region + ' (paid). Turn off free $0.'
          : 'Saving a key does not start a session or charge you.';
      }
      showNext();
      models.addEventListener('change', showNext);
      const submit = async () => {
        if (busy) return;
        const key = input.value.trim();
        if (!new RegExp(selected.key_regex).test(key)) {
          message.textContent = 'Paste the full API key, without quotes or spaces.';
          message.classList.add('is-error');
          input.focus();
          return;
        }
        busy = true;
        input.disabled = region.disabled = save.disabled = true;
        save.textContent = 'Saving...';
        message.textContent = '';
        message.classList.remove('is-error');
        try {
          const result = await api('/api/domestic-providers/keys', { preset: selected.id, key });
          if (!result.ok) throw new Error(result.error || 'The key could not be saved. Try again.');
          input.value = '';
          selected.configured = true;
          saved.textContent = 'Key saved for this region. Not tested yet.';
          message.textContent = result.message || 'Key saved. Your first run will test it.';
          showNext();
          document.dispatchEvent(new CustomEvent('ccc-domestic-keys-changed'));
          if (window.cccFx && typeof window.cccFx.play === 'function') window.cccFx.play('success');
          renderRemove();
        } catch (error) {
          message.textContent = error.message || 'Could not reach CCC. Try again.';
          message.classList.add('is-error');
        } finally {
          busy = false;
          input.disabled = save.disabled = false;
          region.disabled = presets.length === 1;
          save.textContent = 'Save key';
        }
      };
      save.addEventListener('click', submit);
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); submit(); }
      });
      actions.append(save);
      const removal = node('span', 'dp-removal');
      function renderRemove() {
        removal.replaceChildren();
        if (!selected.configured) return;
        const remove = node('button', 'dp-button dp-secondary', removePending ? 'Remove saved key' : 'Remove key');
        remove.type = 'button';
        remove.dataset.dpRemove = '';
        remove.addEventListener('click', async () => {
          if (busy) return;
          if (!removePending) {
            removePending = true;
            message.textContent = 'Remove this region\'s key? Existing sessions keep their current key. New runs will need a key.';
            renderRemove();
            return;
          }
          busy = true;
          region.disabled = input.disabled = save.disabled = remove.disabled = true;
          try {
            await api('/api/domestic-providers/keys/remove', { preset: selected.id });
            selected.configured = false;
            input.value = '';
            saved.textContent = 'No key saved for this region.';
            showNext();
            message.textContent = 'Key removed.';
            removePending = false;
            renderRemove();
            document.dispatchEvent(new CustomEvent('ccc-domestic-keys-changed'));
          } catch (error) {
            message.textContent = error.message || 'Could not remove the key. Try again.';
            message.classList.add('is-error');
          } finally {
            busy = false;
            input.disabled = save.disabled = false;
            region.disabled = presets.length === 1;
            remove.disabled = false;
          }
        });
        removal.append(remove);
        if (removePending) {
          const cancel = node('button', 'dp-button dp-secondary', 'Keep key');
          cancel.type = 'button';
          cancel.onclick = () => { removePending = false; message.textContent = ''; renderRemove(); };
          removal.append(cancel);
        }
      }
      renderRemove();
      actions.append(removal);
      el.append(actions, message, instructions);
    }
    render();
    controller.cards.push({ update(rows) {
      if (busy) return;
      presets.forEach((p) => {
        const fresh = rows.find((r) => r.id === p.id);
        if (fresh) p.configured = fresh.configured;
      });
      const saved = el.querySelector('[data-dp-saved]');
      if (saved) saved.textContent = selected.configured
        ? 'Key saved for this region. Not tested yet.' : 'No key saved for this region.';
    } });
    return el;
  }

  function mount(host) {
    if (host._cccDomesticProviders) return host._cccDomesticProviders;
    styles();
    const root = node('section', 'dp-root');
    const title = node('h3', 'dp-title', 'Use your own paid key');
    const intro = node('p', 'dp-intro', 'GLM, Kimi, DeepSeek, Qwen and MiniMax. Pick the region where you created your API key. These models are not free.');
    const privacy = node('p', 'dp-note', 'Your key stays in CCC\'s local key store. Runs go straight to your chosen provider, not the free router. Prompts and code are sent to that provider.');
    const grid = node('div', 'dp-grid');
    root.append(title, intro, privacy, grid);
    host.append(root);
    const controller = { cards: [], root, async refresh() {
      try {
        const data = await api('/api/domestic-providers');
        if (!Array.isArray(data.presets)) throw new Error('The provider list could not be loaded.');
        if (controller.cards.length) {
          controller.cards.forEach((entry) => entry.update(data.presets));
          return;
        }
        grid.replaceChildren();
        groupPresets(data.presets).forEach((group) => grid.append(card(group, controller)));
      } catch (error) {
        if (controller.cards.length) return;
        grid.replaceChildren(node('p', 'dp-message is-error', error.message || 'Could not reach CCC. Try again.'));
        const retry = node('button', 'dp-button', 'Try again');
        retry.type = 'button';
        retry.onclick = () => controller.refresh();
        grid.append(retry);
      }
    } };
    host._cccDomesticProviders = controller;
    grid.textContent = 'Loading paid-key presets...';
    controller.refresh();
    return controller;
  }

  window.cccDomesticProviders = { mount, groupPresets };
})();
