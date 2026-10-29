// Static demo: mutating calls are no-ops, so mirror the real enqueue by prepending the new ticket
// to the next /api/queue/list read (same shape as the fixture rows).
var added = null;
(function () {
  var oj = Response.prototype.json;
  Response.prototype.json = function () {
    var url = this.url || '';
    return oj.call(this).then(function (d) {
      if (added && /\/api\/queue\/list/.test(url) && d && Array.isArray(d.items) && d.items.indexOf(added) < 0) {
        d.items = [added].concat(d.items); d.count = d.items.length;
      }
      return d;
    });
  };
})();
document.addEventListener('DOMContentLoaded', function () {
  var f = window.fetch;
  window.fetch = function (input, init) {
    var u = typeof input === 'string' ? input : (input && input.url) || '';
    if (/\/api\/ux-fixes\/enqueue/.test(u) && init && init.body) {
      try {
        var b = JSON.parse(init.body);
        added = { ref: (b.project || 'BUGS') + '-113', number: 113, project: b.project || 'BUGS', status: 'open',
          note: b.note, text: b.note, type: 'feature', priority: 'p0', source: 'ccc',
          created_at: '2026-10-09T08:00:00Z', updated_at: '2026-10-09T08:00:00Z', claimable: true, watchtower_runnable: true };
      } catch (e) {}
    }
    return f(input, init);
  };
});
// Software-rendered 4K cannot afford the modal's backdrop blur (video froze in 8 s bursts); keep the dim, drop the blur.
document.addEventListener('DOMContentLoaded', function () {
  var s = document.createElement('style');
  s.textContent = '*{backdrop-filter:none!important;-webkit-backdrop-filter:none!important}';
  document.head.appendChild(s);
});
