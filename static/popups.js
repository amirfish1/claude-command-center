/* Pop-up approvals: every promo pop-up stays off until it is approved here.
 *
 * A pop-up (toast, modal, floating card, notification ask) shows only when
 * its id is in APPROVED. Approve one by adding its id here AND to APPROVED in
 * ccc_server/popups.py (tests/test_popups.py keeps the lists equal).
 *
 * Things the user opens on purpose (Settings, /?onboarding=1, the "Send test
 * notification" button) are not pop-ups and are never gated.
 *
 * Local preview of a held pop-up (this browser only):
 *   localStorage.setItem('ccc-popups-preview', 'star-ask,notify-task')  // or '*'
 */
(function () {
  'use strict';

  var ALL = [
    'moment-zero',
    'savings-milestone',
    'notify-permission',
    'notify-task',
    'notify-digest',
    'notify-milestone',
    'notify-other',
    'star-ask',
    'router-detected',
    'limit-failover',
  ];

  // Approved pop-ups. Empty: nothing shows until Amir approves it.
  var APPROVED = [];

  var NOTIFY_KIND_IDS = {
    task: 'notify-task',
    needs_input: 'notify-task',
    digest: 'notify-digest',
    milestone: 'notify-milestone',
  };

  function preview() {
    try {
      return String(localStorage.getItem('ccc-popups-preview') || '')
        .split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    } catch (_) { return []; }
  }

  function allowed(id) {
    if (APPROVED.indexOf(id) !== -1) return true;
    var p = preview();
    return p.indexOf('*') !== -1 || p.indexOf(id) !== -1;
  }

  window.cccPopups = {
    all: ALL.slice(),
    approved: APPROVED.slice(),
    allowed: allowed,
    notifyAllowed: function (kind) {
      return allowed(NOTIFY_KIND_IDS[String(kind || '')] || 'notify-other');
    },
  };
})();
