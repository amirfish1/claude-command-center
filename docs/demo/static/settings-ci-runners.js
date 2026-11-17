/* Start a setup agent with saved session defaults; keep diagnostics read-only. */
(() => {
  'use strict';
  const start = document.getElementById('ciRunnerStart');
  const launchStatus = document.getElementById('ciRunnerLaunchStatus');
  const check = document.getElementById('ciRunnerCheck');
  const status = document.getElementById('ciRunnerSetupStatus');
  if (!start || !check || !status) return;
  let startedSession = '';
  start.addEventListener('click', async () => {
    if (startedSession) {
      window._cccCloseSettingsModal?.();
      window.cccOpenSession?.(startedSession);
      return;
    }
    if (typeof window.cccSpawnPromptSession !== 'function') {
      launchStatus.textContent = 'Session setup is unavailable. Reload CCC and try again.';
      return;
    }
    start.disabled = true;
    start.textContent = 'Starting…';
    launchStatus.textContent = 'Starting your default new-session agent…';
    const prompt = [
      'Set up a GitHub Actions self-hosted repository runner on this CCC server machine so CCC can show local CI progress.',
      'Use the selected project working directory to identify the GitHub repository. Ask if it is ambiguous.',
      'This button authorizes setup for that repository. Inspect existing runners first and reuse a suitable runner instead of creating duplicates.',
      'Run locally on this CCC server machine as the same OS user as CCC. Do not move setup to SSH hosts, remote agent services or another machine.',
      'Check the operating system and architecture, GitHub CLI installation, account access, repository admin permissions, and the current official GitHub runner instructions.',
      'Install missing prerequisites and a runner in a dedicated user-owned directory outside the project checkout; obtain a short-lived registration token through authorized GitHub CLI/API access and register it. Never print tokens, store them in source, or put them in chat or logs.',
      'If interactive GitHub sign-in or authorization is needed, ask the user to complete that step securely. Do not navigate a personal logged-in browser without explicit permission.',
      'Start the runner, verify GitHub reports it online, and ensure CCC can read its .runner configuration and _diag metadata. Prefer a user service for persistent operation; follow host rules and explain how to stop/remove it.',
      'Self-hosted jobs execute repository code with this machine’s access. Prefer private trusted repositories. Before enabling public or untrusted pull-request jobs, explain the consequence and ask for explicit approval.',
      'Inspect workflow runner labels and dependencies. Prepare the smallest compatible routing change if needed, preserve existing CI and security gates, and follow repository approval rules before changing routing or triggering deployment, spending money or sending messages.',
      'Verify the setup with GET /api/ci/setup and, when an authorized CI job runs, GET /api/ci/local and the CCC header progress. Distinguish runner readiness from a job that has actually passed. If a real run requires approval or a future push, report that exact remaining check.',
      'Finish with the runner name, target repository, verified status, and exact stop/removal instructions. Persist until setup is working or a specific required user action blocks it.'
    ].join('\n\n');
    try {
      const result = await window.cccSpawnPromptSession({
        prompt, name: 'Set up GitHub Actions runner',
        sessionDefaults: true, localOnly: true
      });
      if (!result?.ok) throw new Error(result?.error || 'Could not start setup.');
      startedSession = result.session_id || '';
      launchStatus.textContent = 'Setup session started. Continue with the agent in your session list.';
      start.textContent = startedSession ? 'Open setup session' : 'Setup session started';
      window._cccCloseSettingsModal?.();
      if (startedSession) window.cccOpenSession?.(startedSession);
    } catch (error) {
      launchStatus.textContent = 'Could not start setup: ' + error.message;
      start.textContent = 'Set up a runner';
    } finally {
      start.disabled = !startedSession && start.textContent === 'Setup session started';
    }
  });
  check.addEventListener('click', async () => {
    check.disabled = true;
    check.textContent = 'Checking…';
    status.textContent = 'Checking GitHub access and local runners…';
    try {
      const response = await fetch('/api/ci/setup', {cache: 'no-store', cccPriority: 'user', signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error('Setup check failed. Try again.');
      const data = await response.json();
      const list = document.createElement('ul');
      function item(text) { const li = document.createElement('li'); li.textContent = text; list.append(li); }
      if (!data.supported) item('Automatic detection currently supports macOS and Linux.');
      item(!data.github_cli ? 'GitHub CLI is not installed.'
        : data.github_authenticated ? 'GitHub CLI can reach GitHub with the current account.'
        : 'GitHub access is not connected or could not be verified.');
      if (!data.runner_check_available) item('Local runner status is unavailable.');
      else if (!data.runners.length) item('No local runner is running.');
      else for (const runner of data.runners) item(runner.name + ' · ' + (runner.state === 'running_job' ? 'Running a job' : 'Runner process started · no active job detected'));
      status.replaceChildren(list);
    } catch (error) {
      status.textContent = error.name === 'TimeoutError' ? 'Setup check timed out. Try again.' : 'Setup check unavailable. Try again.';
    } finally {
      check.disabled = false;
      check.textContent = 'Refresh status';
    }
  });
})();
