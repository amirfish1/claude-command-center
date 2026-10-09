# ccc-dashboard plugin

Claude Code skills for working with a running
[Claude Command Center](https://github.com/amirfish1/claude-command-center) (CCC):
spawn, message and check on sibling sessions, verify fixes in a fresh session,
and hand work off. The skills call CCC's local HTTP API, so CCC must be
running on the same machine.

Install:

```
/plugin marketplace add amirfish1/claude-command-center
/plugin install ccc-dashboard@claude-command-center
```

The skills are generated from `skills/*.md` in the main repo by
`scripts/build-plugin.py`; edit them there, not here.
Licence: FSL-1.1-MIT (see `LICENSE` in the main repo).
