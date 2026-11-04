(function () {
  'use strict';
  const button = document.getElementById('browseApps');
  const panel = document.getElementById('marketplace');
  async function post(action, body) {
    const response = await fetch('/api/app-marketplace/' + action, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || 'App operation failed');
    return result;
  }
  button.onclick = async function () {
    panel.hidden = !panel.hidden;
    if (panel.hidden) return;
    panel.textContent = 'Loading app index…';
    try {
      const response = await fetch('/api/app-marketplace/index');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      panel.textContent = '';
      const note = document.createElement('p');
      note.textContent = 'Apps run in a sandbox. Review the actual package permissions before installing. Index listings are not security endorsements.';
      panel.appendChild(note);
      data.apps.forEach(function (app) {
        const card = document.createElement('section');
        card.className = 'marketplace-card';
        const title = document.createElement('h3');
        title.textContent = app.name;
        const description = document.createElement('p');
        description.textContent = app.description;
        const permissions = document.createElement('p');
        permissions.textContent = 'Permissions: ' + (app.permissions.join(', ') || 'None');
        const status = document.createElement('p');
        status.setAttribute('role', 'status');
        const install = document.createElement('button');
        install.className = 'btn primary';
        install.textContent = 'Install';
        install.onclick = async function () {
          install.disabled = true;
          status.textContent = 'Preparing package for review…';
          let preview;
          try {
            preview = await post('prepare', {id: app.id});
            const requested = preview.permissions.map(p => p + ': ' + preview.permission_descriptions[p]).join('\n') || 'None';
            if (!window.confirm('Install ' + preview.label + ' (' + preview.id + ')?\n\nRequested permissions:\n' + requested + '\n\nOnly approve data you want this app to read.')) {
              await post('cancel', {token: preview.token});
              status.textContent = 'Installation cancelled.';
              return;
            }
            await post('install', {token: preview.token, consent: true});
            status.textContent = 'Installed. ';
            const open = document.createElement('a');
            open.href = '/view/' + encodeURIComponent(preview.id);
            open.textContent = 'Open app';
            status.appendChild(open);
            install.textContent = 'Installed';
            window.dispatchEvent(new Event('ccc:marketplace-installed'));
            if (window.cccRefreshRail) window.cccRefreshRail();
          } catch (error) {
            status.textContent = error.message;
          } finally {
            install.disabled = install.textContent === 'Installed';
          }
        };
        card.append(title, description, permissions, install, status);
        panel.appendChild(card);
      });
    } catch (error) {
      panel.textContent = error.message || 'Cannot load app index';
    }
  };
})();
