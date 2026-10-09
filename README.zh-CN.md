# CCC

[English](README.md) · **简体中文**

[![CI](https://github.com/amirfish1/claude-command-center/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/amirfish1/claude-command-center/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/amirfish1/claude-command-center?color=blue)](https://github.com/amirfish1/claude-command-center/releases)
[![License: FSL-1.1-MIT](https://img.shields.io/badge/license-FSL--1.1--MIT-green)](LICENSE)
![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)
![Local only](https://img.shields.io/badge/runs-100%25%20local-purple)

> 本文由英文版 README 翻译而来，内容以 [英文版](README.md) 为准。欢迎提 PR 改进译文。

**终端已经装不下你的编程智能体了。**

CCC 把所有会话放进同一块本地看板，并告诉你此刻哪个会话在等你。

_让 Claude 忙着第一个任务，你可以先开下一个。_

一个本地面板，统一管理 **Claude Code**、**Codex**、**Cursor**、**Antigravity**、
**Kilo Code**、**Kimi Code**、**OpenCode** 和 **Devin** 的会话，无论它们是从哪里启动的。
八种引擎都能从 CCC 启动和监控，其中七种支持继续发送消息（Kilo Code 只能发起，不能续聊）。
CCC 完全在本地运行，源码公开，个人和公司都可以免费使用和修改。

![CCC v5.35：会话列表、正在进行的智能体对话和输入框](docs/images/ccc-v5-35-hero.png)

用 curl 安装：

```bash
curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | CCC_FROM=readme bash
```

用 Homebrew 安装：

```bash
brew tap amirfish1/ccc
brew install ccc
ccc
```

也可以下载 [macOS DMG](https://github.com/amirfish1/claude-command-center/releases/latest)，
把 `CCC.app` 拖进“应用程序”文件夹。

想先看看？打开 [只读演示](https://ccc.amirfish.ai/demo/)：完整的面板，填充的是虚构数据，无需安装。
[备用演示地址](https://amirfish1.github.io/claude-command-center/demo/)。

## 5 分钟免费上手

第一次用编程智能体？首次运行向导会检查你的电脑，帮你装好 Node、Claude Code CLI
和一个本地免费模型路由，然后用 **$0 的模型** 跑完你的第一个真实任务。每一步安装都需要你确认。
最后它会给你算一笔账：*“这次运行花了 $0；按 API 价格算，本来要花 $X。”*

[安装向导说明](docs/onboarding.md) · [免费模型与隐私](docs/free-models.md)（英文）。
免费服务商各有用量限制。其中免密钥的服务商会把提示词用于训练，CCC 启用它之前会先征得你的同意。
请不要把私有代码发给它。

## 国产模型：GLM、Kimi、DeepSeek、通义千问、MiniMax

CCC 内置了五家国产模型的预设：**智谱 GLM**、**Kimi（月之暗面）**、**DeepSeek**、
**通义千问 Qwen（阿里云百炼）** 和 **MiniMax**。除 DeepSeek 只有一个通用端点外，
每家都分国际站和国内站两个端点，选你注册 API 密钥的那个区域即可。

在 **设置 > 免费模型（Settings > Free models）** 或密钥向导里选择服务商、填入密钥，
就能直接用这些模型启动 Claude 会话。会话恢复时也会继续使用同一家服务商，不会悄悄切回 Anthropic 账号。

- 这些是 **付费** 模型，按各家官方价格计费，不走 CCC 的免费路由。
- 密钥只保存在 CCC 的本地密钥库里；提示词和代码会直接发给你选的服务商。
- 区域要对应：国内站的密钥填国内端点，国际站的密钥填国际端点。
  Kimi Code 订阅密钥、阿里云 Coding Plan 密钥不能用在这里。

更多细节见 [自带密钥（BYOK）与 Vault](docs/byok-and-vault.md)（英文）。

## 快速开始

运行面板需要 Git 和 Python 3.9+。要启动智能体，可以用你已经装好的智能体 CLI，
也可以让首次运行向导帮你装一个。`gh` 是可选的，用于 GitHub 相关功能。

偏好 Python 工具？可以 [用 uvx 或 pipx 构建并运行 wheel](docs/install.md#python-runners-preview)。
这是从源码构建的预览版，并未发布到 PyPI。

### 自带 WatchTower

CCC 的任务队列由 WatchTower 驱动。见 [安装与要求](docs/install.md#watchtower-queue-engine)。

### 在 Windows 上运行

```powershell
irm https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.ps1 | iex
```

从源码目录运行则执行 `.\run.ps1`。原生 Windows 在前台运行；WSL2 下可以用 Linux 的
systemd 服务方式。[Windows 指南](docs/install.md#running-on-windows)。

### 从源码运行

```bash
git clone https://github.com/amirfish1/claude-command-center
cd claude-command-center
./run.sh
```

打开 [http://localhost:8090](http://localhost:8090)，先选一个代码仓库再开始工作。
前台启动时请保持终端开着。

### 在 Linux 上运行

用 `./run.sh` 前台运行，或用 `./run.sh --install-service` 安装为 systemd 用户服务。
[Linux 与 WSL2 指南](docs/install.md#running-on-linux-and-wsl2) ·
[macOS 服务](docs/install.md#from-source-on-macos) · [Docker](docs/docker.md)。

### 你的智能体配置

CCC 在智能体配置里注册钩子（hooks）或安装技能（skills）之前都会先问你。
你可以跳过、先查看改动，或者之后在设置里移除。
[会改什么，以及如何撤销](docs/agent-config-consent.md)。

## 引擎支持

八种引擎都能从 CCC 发起会话。续聊和模型切换的支持程度各不相同：Kilo Code 不支持恢复会话，
Cursor IDE 只同步元数据。**GitHub Copilot CLI**、**VS Code Copilot Chat**、**Grok CLI**
以及 Devin 云端会话为只读。[完整的引擎支持表](docs/engine-support.md)。

## 了解更多

以下文档目前只有英文版：

- [功能指南](docs/features.md)：审批、搜索、worktree、设置、按成本续跑、语音、队列和看板视图。
- [手机访问](docs/phone-access.md)：在可信网络里用手机查看会话并回复，可通过 Tailscale 设置。
- [CLI 命令](docs/cli.md) · [编排 API](docs/orchestration.md) · [配置项](docs/configuration.md) · [架构](docs/architecture.md)。
- [更新日志](CHANGELOG.md) · [版本发布](https://github.com/amirfish1/claude-command-center/releases)。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。欢迎反馈问题和提交 PR，包括改进这份中文文档。

## 许可证

[Functional Source License 1.1, MIT Future License (FSL-1.1-MIT)](LICENSE) © 2026 Amir Fish。
可以免费使用、修改，也可以在公司内部运行，包括部署在你自己的服务器上供团队使用。
但不得出售 CCC，也不得把它做成竞品或托管服务对外提供。每个版本发布满两年后转为 MIT 许可。
2026-07-28 之前发布的版本仍适用 [MIT 许可证](LICENSE-MIT)；部分第三方贡献保持 MIT（见 [NOTICE](NOTICE)）。
许可条款以英文原文为准。
