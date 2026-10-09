# CCC press kit

Everything you need to write about CCC in five minutes. All images here use
demo data: fake sessions, fake repos, and a synthetic usage history. Use them
freely, with or without credit.

## The hook

> **Your Claude limit hits. Your 12 sessions keep going.**

CCC (Claude Command Center) is one local dashboard for every coding-agent
session on your machine: Claude Code, Codex, Cursor, Antigravity, Kilo Code,
Kimi Code, OpenCode and Devin, however you launched them. It shows which
session needs you. When your Claude plan runs out, it can move the whole fleet
to a free-model router you already run.

## The number

> **5.82 billion tokens, 429 agent hours, 98% cache hits in 30 days**, on one
> maintainer's machine, from CCC's own share card.

Every CCC user can make the same card from their throughput page. It holds
totals only: no prompts, code, file paths or project names.

## Try it in 3 steps

1. **Install** (macOS or Linux, needs Git and Python 3.9+):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash
   ```
   Or `brew tap amirfish1/ccc && brew install ccc`, or the
   [macOS DMG](https://github.com/amirfish1/claude-command-center/releases/latest).
2. **Open** http://localhost:8090. Every Claude Code and Codex session already
   on the machine shows up on one board. No account, no cloud.
3. **Share your number:** Throughput page, then **Share**. Pick tokens, agent
   hours or $ saved, then download the card.

No install? The [read-only demo](https://ccc.amirfish.ai/demo/) runs in the browser.

## Images

| File | What it shows | Size |
|---|---|---|
| [cards/card-tokens-1200x630.png](cards/card-tokens-1200x630.png) | Share card: tokens processed, 30 days | 1200x630 |
| [cards/card-tokens-1080x1080.png](cards/card-tokens-1080x1080.png) | Same, square | 1080x1080 |
| [cards/card-saved-1200x630.png](cards/card-saved-1200x630.png) | Share card: $ of work done on $0 models | 1200x630 |
| [cards/card-saved-1080x1080.png](cards/card-saved-1080x1080.png) | Same, square | 1080x1080 |
| [cards/card-month-1200x630.png](cards/card-month-1200x630.png) | Monthly card: "My October", a calendar of the month plus four stats | 1200x630 |
| [cards/card-month-1080x1080.png](cards/card-month-1080x1080.png) | Same, square | 1080x1080 |
| [screenshots/board.png](screenshots/board.png) | The fleet: every session on one board, one transcript open | 2880x1800 |
| [screenshots/share.png](screenshots/share.png) | The share screen that makes the card | 2880x1800 |
| [screenshots/mobile.png](screenshots/mobile.png) | A session on a phone, with Call and the composer | 1170x2532 |

The card numbers are synthetic (made by `scripts/press-kit/synthetic-payload.js`),
not anyone's real usage. The quoted number above comes from a real card.

Short clips (GIF, demo data): [attention](../images/feature-wall/attention.gif),
[fleet scan](../images/feature-wall/fleet-scan.gif),
[group chat](../images/feature-wall/group-chat.gif),
[mobile](../images/feature-wall/mobile.gif),
[queue workers](../images/feature-wall/queue-workers.gif).

**Video:** the 20-second limit-hit demo, demo data:
[16:9 MP4](../product-story/assets/video/V-20-limit-hit-16x9.mp4) (1920x1080),
[9:16 MP4](../product-story/assets/video/V-20-limit-hit-9x16.mp4) (1080x1920),
[16:9 GIF](../product-story/assets/video/V-20-limit-hit-16x9.gif) (800px, 0.6 MB).

## Hook lines in other languages

Machine-drafted. Please have a native speaker check them before you publish.

| Language | Hook | Line 2 |
|---|---|---|
| English | Your Claude limit hits. Your 12 sessions keep going. | One local dashboard for every coding agent. |
| 简体中文 (zh) | Claude 额度用完了，你的 12 个会话照样继续跑。 | 一个本地面板，管好所有编程智能体。 |
| 日本語 (ja) | Claude の上限に達しても、12 個のセッションは止まらない。 | すべてのコーディングエージェントを、ひとつのローカルダッシュボードで。 |
| Español (es) | Se acaba tu límite de Claude. Tus 12 sesiones siguen trabajando. | Un panel local para todos tus agentes de código. |
| العربية (ar) | انتهى حدّ Claude لديك؟ جلساتك الاثنتا عشرة تواصل العمل. | لوحة تحكم محلية واحدة لكل وكلاء البرمجة لديك. |

## Facts

- **What:** a local web dashboard (Python, standard library only) for
  coding-agent sessions. Runs on macOS, Linux and Windows.
- **Engines:** Claude Code, Codex, Cursor, Antigravity, Kilo Code, Kimi Code,
  OpenCode, Devin.
- **Price:** free. Source-available under
  [FSL-1.1-MIT](https://github.com/amirfish1/claude-command-center/blob/main/LICENSE),
  free to use and modify, including at work.
- **Privacy:** everything stays on your machine. One anonymous daily ping,
  off with `CCC_TELEMETRY_DISABLED=1`.
- **Not affiliated with Anthropic.**
- **Links:** [GitHub](https://github.com/amirfish1/claude-command-center) ·
  [getccc.dev](https://getccc.dev) · [demo](https://ccc.amirfish.ai/demo/)
- **Maker:** Amir Fish ([@amirfish1](https://github.com/amirfish1) on GitHub).

## Rebuilding these assets

```bash
python3 -m http.server 8877 --directory . &      # from the repo root
node scripts/press-kit/render-cards.js            # cards/ (byte-stable)
node scripts/press-kit/shots.js                   # screenshots/ (demo fixtures, dates shifted to now)
```

Both scripts use puppeteer (see `scripts/story-capture/README.md`) against a plain
static server, never a running CCC, so no real data can reach an image.
