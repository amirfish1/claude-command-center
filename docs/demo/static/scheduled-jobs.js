// Copyright (c) 2026 Amir Fish. All rights reserved.
// SPDX-License-Identifier: LicenseRef-CCC-Software-License
/**
 * Scheduled Jobs Panel for System Status Modal.
 * Aggregates and renders launchd (laptop) and systemd (hermes) jobs.
 */
(function() {
  'use strict';

  let _jobsData = null;
  let _filterHost = 'all';
  const _expandedLogs = new Map(); // jobId -> string logContent

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function relativeTime(isoStr) {
    if (!isoStr) return 'never';
    try {
      const ms = Date.parse(isoStr);
      if (isNaN(ms)) return isoStr;
      const diffSec = Math.round((Date.now() - ms) / 1000);
      if (diffSec < 0) {
        // Future
        const futureSec = Math.abs(diffSec);
        if (futureSec < 60) return `in ${futureSec}s`;
        if (futureSec < 3600) return `in ${Math.round(futureSec / 60)}m`;
        if (futureSec < 86400) return `in ${Math.round(futureSec / 3600)}h`;
        return `in ${Math.round(futureSec / 86400)}d`;
      }
      if (diffSec < 60) return `${diffSec}s ago`;
      if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
      if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h ago`;
      return `${Math.round(diffSec / 86400)}d ago`;
    } catch (_) {
      return isoStr;
    }
  }

  async function poll(force) {
    const $list = document.getElementById('sysJobsList');
    if (!$list) return;

    try {
      const url = `/api/system/scheduled-jobs${force ? '?force=1' : ''}`;
      const res = await (window.backgroundApiFetch || fetch)(url, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data && data.ok) {
        _jobsData = data;
        render();
      }
    } catch (e) {
      if (!$list.querySelector('.sys-job-card')) {
        $list.innerHTML = `<div style="color:var(--text-danger,#f85149); font-size:12px; padding:12px;">Failed to load scheduled jobs: ${escapeHtml(e.message)}</div>`;
      }
    }
  }

  function render() {
    const $list = document.getElementById('sysJobsList');
    if (!$list || !_jobsData) return;

    const jobs = _jobsData.jobs || [];
    const hosts = _jobsData.hosts || {};

    const countAll = jobs.length;
    const countLaptop = jobs.filter(j => j.host === 'laptop').length;
    const countHermes = jobs.filter(j => j.host === 'hermes').length;

    const $cAll = document.getElementById('sysJobsCountAll');
    const $cLaptop = document.getElementById('sysJobsCountLaptop');
    const $cHermes = document.getElementById('sysJobsCountHermes');
    if ($cAll) $cAll.textContent = String(countAll);
    if ($cLaptop) $cLaptop.textContent = String(countLaptop);
    if ($cHermes) $cHermes.textContent = String(countHermes);

    const $hostStatus = document.getElementById('sysJobsHostStatus');
    if ($hostStatus) {
      const hStatus = hosts.hermes?.status || 'unknown';
      const hColor = hStatus === 'online' ? 'var(--text-success, #3fb950)' : 'var(--text-danger, #f85149)';
      $hostStatus.innerHTML = `Hermes host: <span style="font-weight:600; color:${hColor}">${escapeHtml(hStatus)}</span>`;
    }

    const filtered = jobs.filter(j => _filterHost === 'all' || j.host === _filterHost);

    if (!filtered.length) {
      $list.innerHTML = '<div style="opacity:.6; font-size:12px; padding:16px; text-align:center;">No scheduled jobs found for this filter.</div>';
      return;
    }

    $list.innerHTML = filtered.map(job => {
      const isRunning = job.status === 'running';
      const isFailed = job.status === 'failed';
      const isSuccess = job.status === 'success';

      let statusColor = 'var(--text-muted)';
      let statusBg = 'rgba(255,255,255,0.06)';
      let statusText = 'Idle';
      if (isRunning) {
        statusColor = '#58a6ff';
        statusBg = 'rgba(56, 139, 253, 0.15)';
        statusText = 'Running';
      } else if (isFailed) {
        statusColor = '#f85149';
        statusBg = 'rgba(248, 81, 73, 0.15)';
        statusText = job.exit_code !== null && job.exit_code !== undefined ? `Failed (${job.exit_code})` : 'Failed';
      } else if (isSuccess) {
        statusColor = '#3fb950';
        statusBg = 'rgba(63, 185, 80, 0.15)';
        statusText = 'OK';
      }

      const hostClass = job.host === 'hermes' ? 'sys-host-hermes' : 'sys-host-laptop';
      const isLogOpen = _expandedLogs.has(job.id);
      const logContent = _expandedLogs.get(job.id) || '';

      const lastRunStr = job.last_run_at ? relativeTime(job.last_run_at) : 'none';
      const nextRunStr = job.next_run_at ? ` · Next ${relativeTime(job.next_run_at)}` : '';

      return `
        <div class="sys-job-card" data-id="${escapeHtml(job.id)}" style="background:var(--surface-1, rgba(255,255,255,0.03)); border:1px solid var(--border); border-radius:8px; padding:10px 14px;">
          <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:10px;">
            <div style="flex:1; min-width:0;">
              <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px; flex-wrap:wrap;">
                <span class="sys-job-host-badge ${hostClass}" style="font-size:10px; font-weight:700; text-transform:uppercase; padding:2px 6px; border-radius:4px; letter-spacing:0.04em;">${escapeHtml(job.host)}</span>
                <span style="font-weight:600; font-size:13px; color:var(--text);">${escapeHtml(job.name)}</span>
                <span style="font-size:11px; color:var(--text-muted);">${escapeHtml(job.manager)}</span>
              </div>
              <div style="font-size:11.5px; color:var(--text-muted); display:flex; gap:12px; flex-wrap:wrap; align-items:center;">
                <span>📅 ${escapeHtml(job.schedule || 'On demand')}</span>
                <span>⏱ Last: <strong>${escapeHtml(lastRunStr)}</strong>${escapeHtml(nextRunStr)}</span>
              </div>
            </div>
            <div style="display:flex; align-items:center; gap:8px; flex-shrink:0;">
              <span class="sys-job-status-pill" style="font-size:11px; font-weight:600; color:${statusColor}; background:${statusBg}; padding:3px 8px; border-radius:12px; border:1px solid ${statusColor}33;">
                ${escapeHtml(statusText)}
              </span>
              <button type="button" class="upd-btn sys-job-log-toggle" data-id="${escapeHtml(job.id)}" style="font-size:11px; padding:3px 8px;">
                ${isLogOpen ? 'Hide Log' : 'View Log'}
              </button>
            </div>
          </div>
          ${isLogOpen ? `
            <div class="sys-job-log-drawer" style="margin-top:10px; border-top:1px solid var(--border); padding-top:8px;">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                <span style="font-size:10.5px; color:var(--text-muted); font-family:var(--font-mono, monospace);">
                  ${escapeHtml(job.stdout_path || job.log_cmd || 'Log Output')}
                </span>
                <button type="button" class="upd-btn sys-job-log-refresh" data-id="${escapeHtml(job.id)}" style="font-size:10px; padding:2px 6px;">↻ Refresh</button>
              </div>
              <pre style="background:rgba(0,0,0,0.3); border:1px solid var(--border); border-radius:6px; padding:8px 10px; font-size:11px; line-height:1.4; color:var(--text); max-height:220px; overflow-y:auto; white-space:pre-wrap; word-break:break-all; margin:0;">${escapeHtml(logContent || 'Loading log…')}</pre>
            </div>
          ` : ''}
        </div>
      `;
    }).join('');

    // Attach log toggle handlers
    $list.querySelectorAll('.sys-job-log-toggle').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        const id = e.currentTarget.getAttribute('data-id');
        if (!id) return;
        if (_expandedLogs.has(id)) {
          _expandedLogs.delete(id);
          render();
        } else {
          _expandedLogs.set(id, 'Loading log entries…');
          render();
          await fetchLog(id);
        }
      });
    });

    $list.querySelectorAll('.sys-job-log-refresh').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        const id = e.currentTarget.getAttribute('data-id');
        if (!id) return;
        _expandedLogs.set(id, 'Refreshing log entries…');
        render();
        await fetchLog(id);
      });
    });
  }

  async function fetchLog(jobId) {
    try {
      const res = await (window.backgroundApiFetch || fetch)(`/api/system/scheduled-jobs/log?id=${encodeURIComponent(jobId)}&lines=60`);
      const data = await res.json();
      if (data && data.ok) {
        _expandedLogs.set(jobId, data.log || '(Log file or journal was empty)');
      } else {
        _expandedLogs.set(jobId, (data && data.error) ? `Error: ${data.error}` : 'Failed to fetch log.');
      }
    } catch (err) {
      _expandedLogs.set(jobId, `Error fetching log: ${err.message}`);
    }
    render();
  }

  function init() {
    const $filterAll = document.getElementById('sysJobsFilterAll');
    const $filterLaptop = document.getElementById('sysJobsFilterLaptop');
    const $filterHermes = document.getElementById('sysJobsFilterHermes');
    const $refreshBtn = document.getElementById('sysJobsRefreshBtn');

    function setFilter(host) {
      _filterHost = host;
      [$filterAll, $filterLaptop, $filterHermes].forEach(btn => {
        if (!btn) return;
        if (btn.getAttribute('data-host') === host) btn.classList.add('active');
        else btn.classList.remove('active');
      });
      render();
    }

    if ($filterAll) $filterAll.addEventListener('click', () => setFilter('all'));
    if ($filterLaptop) $filterLaptop.addEventListener('click', () => setFilter('laptop'));
    if ($filterHermes) $filterHermes.addEventListener('click', () => setFilter('hermes'));
    if ($refreshBtn) $refreshBtn.addEventListener('click', () => poll(true));
  }

  window.ScheduledJobsPanel = {
    init,
    poll,
    render,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
