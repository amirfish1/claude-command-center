// Site analytics for ccc.amirfish.ai (PostHog, cookieless). See docs/telemetry.md
// "Website analytics": no cookies, no localStorage, no session replay, no
// autocapture, Do Not Track honored. Only pageviews (with their utm_* tags)
// and explicit download / install-option clicks are recorded.
!function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},o="capture register register_once unregister opt_out_capturing has_opted_out_capturing opt_in_capturing reset".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);

posthog.init("phc_v38DtRAtrAK7ygMfwJpqeLFjhJpEUVBcYBnFgmYVUSHe", {
  api_host: "https://us.i.posthog.com",
  persistence: "memory",
  person_profiles: "identified_only",
  autocapture: false,
  capture_pageview: false,
  capture_pageleave: false,
  disable_session_recording: true,
  disable_surveys: true,
  capture_dead_clicks: false,
  capture_performance: false,
  capture_heatmaps: false,
  capture_exceptions: false,
  respect_dnt: true,
  loaded: function (ph) {
    ph.register({ site: "ccc" });
    ph.capture("$pageview");
  },
});

window.cccTrackCta = function (kind, placement) {
  try {
    posthog.capture("cta_click", {
      cta_kind: kind,
      cta_placement: placement || "unspecified",
      cta_path: "/" + kind,
      source_path: location.pathname,
    });
  } catch (_) {}
};
