# CCC

[English](README.md) · **日本語**

[![CI](https://github.com/amirfish1/claude-command-center/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/amirfish1/claude-command-center/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/amirfish1/claude-command-center?color=blue)](https://github.com/amirfish1/claude-command-center/releases)
[![License: FSL-1.1-MIT](https://img.shields.io/badge/license-FSL--1.1--MIT-green)](LICENSE)
![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)
![Local only](https://img.shields.io/badge/runs-100%25%20local-purple)

> この文書は英語版 README の翻訳です。内容に差異がある場合は [英語版](README.md) が正となります。翻訳の改善 PR も歓迎します。

**コーディングエージェントは、もうターミナルに収まりきらない。**

CCC はすべてのセッションをひとつのローカルボードにまとめ、今どれがあなたを待っているかを教えてくれます。

_Claude が最初のタスクを進めている間に、次のタスクを始めましょう。_

**Claude Code**、**Codex**、**Cursor**、**Antigravity**、**Kilo Code**、**Kimi Code**、
**OpenCode**、**Devin** のセッションを、どこから起動したものでもひとつのローカルダッシュボードで扱えます。
8 つすべてを CCC から起動・監視でき、そのうち 7 つには続けてメッセージを送れます（Kilo Code は起動のみで、続きの送信には対応していません）。
CCC はローカルで動作し、ソースは公開されています。仕事での利用も含め、無料で使用・改変できます。

![CCC v5.35：セッション一覧、進行中のエージェントとの会話、入力欄](docs/images/ccc-v5-35-hero.png)

curl でインストール：

```bash
curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | CCC_FROM=readme bash
```

Homebrew でインストール：

```bash
brew tap amirfish1/ccc
brew install ccc
ccc
```

または [macOS 版 DMG](https://github.com/amirfish1/claude-command-center/releases/latest) をダウンロードし、
`CCC.app` を「アプリケーション」フォルダへドラッグしてください。

まずは [読み取り専用デモ](https://ccc.amirfish.ai/demo/) で試せます。架空のサンプルデータを入れた本物のダッシュボードで、インストールは不要です。
[予備のデモ URL](https://amirfish1.github.io/claude-command-center/demo/)。

## 5 分で無料スタート

コーディングエージェントは初めてですか？初回起動ウィザードがマシンの状態を確認し、
Node、Claude Code CLI、ローカルの無料モデルルーターのインストールを手伝ったうえで、
最初の実タスクを **$0 のモデル** で実行します。各インストール手順はあなたの承認を得てから進みます。
最後に正直な計算結果を表示します：*「今回の実行コストは $0。API 料金なら $X かかっていました。」*

[セットアップの流れ](docs/onboarding.md) · [無料プロバイダーとプライバシー](docs/free-models.md)（英語）。
無料プロバイダーにはそれぞれ利用制限があります。API キー不要のプロバイダーはプロンプトを学習に利用するため、
CCC は有効化の前に同意を確認します。非公開のコードは送らないでください。

## クイックスタート

ダッシュボードの実行には Git と Python 3.9+ が必要です。エージェントを起動するには、
インストール済みのエージェント CLI を使うか、初回起動ウィザードにセットアップを任せてください。
`gh` は GitHub 連携機能用で、なくても動作します。

Python ツール派ですか？ [uvx または pipx で wheel をビルドして実行](docs/install.md#python-runners-preview) できます。
これはソースからビルドするプレビュー版で、PyPI には公開していません。

### WatchTower も同梱

CCC のキュー機能は WatchTower で動いています。[セットアップと要件](docs/install.md#watchtower-queue-engine) を参照してください。

### Windows で実行

```powershell
irm https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.ps1 | iex
```

クローンしたリポジトリからは `.\run.ps1` を実行します。ネイティブ Windows ではフォアグラウンドで動作し、
WSL2 では Linux と同じ systemd サービスとして動かせます。[Windows ガイド](docs/install.md#running-on-windows)。

### ソースから実行

```bash
git clone https://github.com/amirfish1/claude-command-center
cd claude-command-center
./run.sh
```

[http://localhost:8090](http://localhost:8090) を開き、作業を始める前にリポジトリを選んでください。
フォアグラウンドで起動した場合は、ターミナルを開いたままにしておきます。

### Linux で実行

`./run.sh` でフォアグラウンド実行、`./run.sh --install-service` で systemd ユーザーサービスとして登録できます。
[Linux と WSL2 のガイド](docs/install.md#running-on-linux-and-wsl2) ·
[macOS のサービス](docs/install.md#from-source-on-macos) · [Docker](docs/docker.md)。

### エージェントの設定について

CCC はエージェントの設定にフック（hooks）を登録したりスキル（skills）をインストールしたりする前に、必ず確認を求めます。
スキップすることも、変更内容を確認することも、あとから設定画面で削除することもできます。
[何が変わるのか、元に戻す方法](docs/agent-config-consent.md)。

## 対応エンジン

8 つのエンジンすべてで、CCC からセッションを開始できます。続きの送信やモデル切り替えへの対応はエンジンごとに異なり、
Kilo Code はセッションの再開に対応しておらず、Cursor IDE はメタデータのみの同期です。
**GitHub Copilot CLI**、**VS Code Copilot Chat**、**Grok CLI**、および Devin のクラウドセッションは読み取り専用です。
[エンジン対応表の全体](docs/engine-support.md)。

## さらに詳しく

以下のドキュメントは現在英語版のみです。

- [機能ガイド](docs/features.md)：承認、検索、worktree、設定、コストを考慮した続行、音声、キュー、ボード表示。
- [スマートフォンからのアクセス](docs/phone-access.md)：信頼できるネットワーク上でスマホからセッションを確認・返信。Tailscale で設定できます。
- [CLI コマンド](docs/cli.md) · [オーケストレーション API](docs/orchestration.md) · [設定](docs/configuration.md) · [アーキテクチャ](docs/architecture.md)。
- [変更履歴](CHANGELOG.md) · [リリース](https://github.com/amirfish1/claude-command-center/releases)。

## コントリビュート

[CONTRIBUTING.md](CONTRIBUTING.md) を参照してください。フィードバックやバグ報告、この日本語版の改善も歓迎します。

## ライセンス

[Functional Source License 1.1, MIT Future License (FSL-1.1-MIT)](LICENSE) © 2026 Amir Fish。
無料で使用・改変でき、自社サーバーでチーム向けに運用するなど、仕事での利用も可能です。
ただし、CCC を販売したり、競合製品やホスティングサービスとして提供したりすることはできません。
各リリースは公開から 2 年後に MIT ライセンスへ移行します。2026-07-28 より前にリリースされたバージョンは引き続き
[MIT ライセンス](LICENSE-MIT) が適用され、一部のサードパーティによる貢献も MIT のままです（[NOTICE](NOTICE) を参照）。
ライセンス条項は英語の原文が優先されます。
