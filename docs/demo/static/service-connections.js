/* One directory; native editors retain their secure stores and event handlers. */
(() => {
  'use strict';
  let api, root, providerEditor, credentialEditor;
  const get = id => document.getElementById(id);
  function editor(kind, {profile, provider} = {}) {
    if (!root) return;
    api.navigate();
    const target = kind === 'provider' ? providerEditor : credentialEditor;
    target.open = true;
    if (kind === 'provider') {
      const input = get('byokProfileInput');
      input.readOnly = !!profile;
      input.value = profile || '';
      get('byokProviderSelect').disabled = !!provider;
      if (provider) get('byokProviderSelect').value = provider;
      get('byokSaveStatus').textContent = profile
        ? 'Replace the credential for this exact profile and provider. Existing model and launch references stay the same.'
        : 'A profile groups provider credentials for supported launches and services. Saving does not select a default or verify access.';
      get('byokKeyInput').value = '';
    }
    target.scrollIntoView({block:'nearest'});
    (kind === 'provider' ? get(profile ? 'byokKeyInput' : 'byokProfileInput') : get('vaultNameInput')).focus();
  }
  function mount(options) {
    if (root) return;
    const section = get('settingsSection-vault'), legacy = get('settingsSection-api-keys');
    if (!section || !legacy || !get('byokProfilesList') || !get('vaultEntriesList')) return;
    api = options;
    root = document.createElement('div'); root.id = 'serviceConnections';
    root.innerHTML = `<div class="settings-row" data-settings-search-group data-keywords="connections accounts credentials provider profile secrets vault byok add">
      <div class="settings-row-main">
      <div class="service-connection-add"><button type="button" class="settings-action-btn" id="serviceConnectionAdd">+ Add</button><button type="button" class="settings-action-btn" id="serviceConnectionRefresh" hidden>Retry</button><span id="serviceConnectionStatus" role="status" hidden></span></div></div></div>
      <div class="service-connection-directory" aria-label="Saved secrets"></div>`;
    const heading=section.querySelector('.settings-section-eyebrow'); if(heading)heading.textContent='Vault';
    section.querySelector(':scope > .settings-section-note')?.remove();
    if(heading)heading.after(root);else section.prepend(root);
    const directory=root.querySelector('.service-connection-directory');
    for (const [id,title] of [['vaultEntriesList',''],['byokProfilesList','Provider keys']]) {
      const row=get(id).closest('.settings-row');const label=row.querySelector('.settings-row-label');label.textContent=title;label.hidden=!title;directory.append(row);
    }
    function makeEditor(id,title,rows) {
      const details=document.createElement('details'); details.id=id;details.className='service-connection-editor';
      const summary=document.createElement('summary');summary.textContent=title;details.append(summary);
      rows.forEach(row=>{if(row)details.append(row);});section.append(details);return details;
    }
    providerEditor=makeEditor('serviceProviderEditor','Add provider key',[
      get('byokBackendDesc').closest('.settings-row'),get('byokSaveKeyBtn').closest('.settings-row')]);
    credentialEditor=makeEditor('serviceCredentialEditor','Add',[
      get('vaultBackendDesc').closest('.settings-row'),get('vaultFormRow')]);
    directory.before(credentialEditor);
    providerEditor.querySelector('.settings-row-label').textContent='Provider account storage';
    credentialEditor.querySelector('.settings-row-label').textContent='Named credential storage';
    const byokHint=get('byokSaveKeyBtn').closest('.settings-row').querySelector('.settings-row-desc');
    byokHint.textContent='Enter a profile name, provider and new key. Values are write-only. Existing profiles and model references are preserved.';
    get('byokProfileInput').setAttribute('aria-label','Provider profile name');get('byokProviderSelect').setAttribute('aria-label','Provider');get('byokKeyInput').setAttribute('aria-label','New provider credential');get('byokSaveStatus').setAttribute('role','status');
    const cancel=document.createElement('button');cancel.type='button';cancel.className='settings-action-btn';cancel.id='serviceProviderCancel';cancel.textContent='Done';
    get('byokSaveKeyBtn').after(cancel);cancel.onclick=()=>{get('byokKeyInput').value='';providerEditor.open=false;get('serviceConnectionAdd').focus();};
    const advanced=makeEditor('serviceConnectionCompatibility','Advanced',[
      get('vaultByokList').closest('.settings-row'),...section.querySelectorAll('[data-keywords*="vault cli"]')]);
    const expanded=document.createElement('section');expanded.id=advanced.id;expanded.className=advanced.className;
    const title=document.createElement('h3');title.textContent='Advanced';expanded.append(title,...[...advanced.children].filter(node=>node.tagName!=='SUMMARY'));advanced.replaceWith(expanded);
    const copyRow=get('vaultByokList').closest('.settings-row');copyRow.querySelector('.settings-row-label').textContent='Existing provider credentials';
    copyRow.querySelector('.settings-row-desc').textContent='Advanced: explicitly copy a provider credential into a named entry for a tool that needs one. This creates an independent copy; it does not link or verify either entry.';
    const usage=get('byokUsageDesc').closest('.settings-row');usage.querySelector('.settings-row-label').textContent='Provider usage (last 30 days)';expanded.append(usage);
    const smartRepo=get('smartRepoTokenBtn')?.closest('.settings-row');
    const smartRepoNotes=smartRepo?.querySelectorAll('.settings-row-desc');
    if(smartRepoNotes?.[1])smartRepoNotes[1].textContent='Jev receives your scrubbed prompt, repo descriptions and recent session titles. Your token stays in local secure storage.';
    legacy.classList.add('service-connections-retired');legacy.setAttribute('aria-hidden','true');
    get('serviceConnectionAdd').onclick=()=>api.newCredential();
    get('serviceConnectionRefresh').onclick=()=>api.refresh();
    section.addEventListener('click',event=>{
      const replace=event.target.closest('[data-service-profile]');
      if(replace)editor('provider',{profile:replace.dataset.serviceProfile,provider:replace.dataset.serviceProvider});
    });
    for(const details of [providerEditor,credentialEditor])details.addEventListener('toggle',()=>{
      if(!details.open)details.querySelectorAll('input[type="password"]').forEach(input=>{input.value='';});
    });
  }
  window.cccServiceConnections = {mount, editor, status(text) {
    const status=get('serviceConnectionStatus');if(status){status.textContent=text;status.hidden=!text;}
  }, closeCredential() {
    if(credentialEditor)credentialEditor.open=false;
  }, unavailable(flag) {
    const retry=get('serviceConnectionRefresh');if(retry)retry.hidden=!flag;
  }, leave() {
    if(credentialEditor)credentialEditor.open=false;
    for(const details of [providerEditor,credentialEditor])details?.querySelectorAll('input[type="password"]').forEach(input=>{input.value='';});
  }, redirect(id) {
    if(!root || id!=='api-keys')return false;
    editor('provider');return true;
  }};
})();
