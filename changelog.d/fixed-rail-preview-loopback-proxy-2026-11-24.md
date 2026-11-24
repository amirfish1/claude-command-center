- Fixed self-served previews falling back to the remote browser (or a blank
  frame) when the dashboard is reached through the tailnet HTTPS proxy:
  loopback URLs on the bound port now rewrite to a same-origin relative URL
  so the plain iframe path survives mixed-content rules.
