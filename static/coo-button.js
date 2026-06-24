/* COO board launcher.
 *
 * Injects a "COO" button into the dashboard topbar, immediately left of the
 * "Today" toggle, that pops open the COO board in its own window. Loaded by
 * the gated loader in index.html when the server `coo` feature flag is true
 * (i.e. the board file is present on disk).
 */
(function () {
  "use strict";

  function injectCooButton() {
    if (document.getElementById("cooPopButton")) return true;
    var today = document.getElementById("todayToggleBtn");
    if (!today || !today.parentNode) return false;  // topbar not painted yet → retry

    var btn = document.createElement("button");
    btn.type = "button";
    btn.id = "cooPopButton";
    btn.className = today.className;  // mirror the Today button's styling
    btn.title = "Open the COO board — live status of the sessions you're tracking.";
    btn.setAttribute("aria-label", "Open COO board");
    btn.innerHTML =
      '<span aria-hidden="true">\u{1F9D1}‍\u{1F4BC}</span>' +
      '<span class="sh-action-label">COO</span>';

    btn.addEventListener("click", function () {
      try {
        localStorage.setItem("ccc-coo-mode", "1");
        window.dispatchEvent(new Event("ccc-coo-mode-changed"));
      } catch (_) {}
      // Named target → repeated clicks focus the same window instead of stacking.
      var w = window.open(
        "/coo",
        "ccc-coo-board",
        "width=1280,height=920,menubar=no,toolbar=no,location=no,status=no"
      );
      if (w) w.focus();
    });

    today.parentNode.insertBefore(btn, today);  // left of Today
    return true;
  }

  // The topbar is static HTML, but this script may run before <body> parses on
  // a cold load. Poll briefly until the Today button exists, then stop.
  (function waitForTopbar(tries) {
    if (injectCooButton() || tries <= 0) return;
    setTimeout(function () { waitForTopbar(tries - 1); }, 150);
  })(40);
})();
