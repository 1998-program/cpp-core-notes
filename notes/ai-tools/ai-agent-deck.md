# 🎛️ Agent Deck — AI Coding Agents 统一终端管理器

> 项目地址：[asheshgoplani/agent-deck](https://github.com/asheshgoplani/agent-deck) · 开源协议：MIT · 2026-05 登上 GitHub Trending

---

## 项目概述

在使用多个 AI Coding Agent 时（Claude Code、Gemini CLI、OpenCode、Codex……），你可能面临：

- **终端 Tab 爆炸**：10 个项目 × N 个 Agent，窗口难以追踪
- **上下文丢失**：切换项目时找不到对应会话
- **配置重复**：每个项目单独配 MCP、Skills，改一次要改很多地方

**Agent Deck** 解决的就是这个问题——**One TUI for all your AI coding agents**。

---

## 核心功能

### 1. 统一会话视图

```
┌─────────────────────────────────────────────────────┐
│  Agent Deck                          [?] help        │
├──────────────┬──────────────────────────────────────┤
│ Sessions     │  Session: my-project/claude-main      │
│ ▶ my-project │  Status:  ● Running                   │
│   claude-main│  Agent:   Claude Code                 │
│   gemini-bg  │  Group:   my-project                  │
│ ▶ api-service│  MCP:     web-search, browser         │
│   codex-main │  Skills:  refactor, test-gen          │
│ ▶ frontend   │                                        │
│   claude-ui  │  Last activity: 2s ago                │
└──────────────┴──────────────────────────────────────┘
```

- **状态一览**：Running / Waiting / Idle，一眼看清每个 Agent 的状态
- **单键切换**：方向键或 `/` 搜索，毫秒级跳转任意会话
- **分组管理**：按项目分 Group，支持折叠/展开

### 2. Session Fork（会话分叉）

```
Press f → Quick fork (继承全部对话历史)
Press F → Fork with name/group 自定义

原始会话
  ├── fork-v1: 尝试方案A
  ├── fork-v2: 尝试方案B
  └── fork-v3: 探索重构方向
```

**最核心的功能**：无需重新描述问题背景，fork 后继承完整对话历史，像 Git branch 一样探索不同解法。

### 3. MCP 热切换

```toml
# ~/.agent-deck/config.toml
[mcp.web-search]
command = "npx @modelcontextprotocol/server-brave-search"
env = { BRAVE_API_KEY = "..." }

[mcp.browser]
command = "npx @playwright/mcp"
```

- `Press m` 打开 MCP Manager
- `Space` 切换 LOCAL（仅当前会话）/ GLOBAL（所有会话）
- Agent Deck 自动处理 Agent 重启，无需手动改配置文件

### 4. Skills 管理池

```
~/.agent-deck/skills/pool/     ← 统一存放所有 skill
  refactor/SKILL.md
  test-gen/SKILL.md
  code-review/SKILL.md

项目级 .agent-deck/skills.toml ← 声明该项目使用哪些 skill
→ 自动 materialize 到 .claude/skills/
```

**核心思路**：Skill 存一份，按需 attach/detach，而不是每个项目复制一份。

---

## 安装与快速上手

```bash
# 安装
npm install -g agent-deck

# 启动
agent-deck

# 或直接打开某个目录的 Agent
agent-deck start --agent claude --dir ~/my-project
```

**加载 Claude Skill（可选）**：
```bash
mkdir -p ~/.claude/skills/agent-deck/references
curl -sL https://raw.githubusercontent.com/asheshgoplani/agent-deck/main/skills/agent-deck/SKILL.md \
  > ~/.claude/skills/agent-deck/SKILL.md
```

---

## 键盘快捷键速查

| 按键 | 功能 |
|------|------|
| `↑↓` / `jk` | 切换会话 |
| `Enter` | 进入会话 |
| `f` | 快速 Fork |
| `F` | Fork（自定义名称/分组） |
| `m` | MCP Manager |
| `s` | Skills Manager |
| `n` | 新建会话 |
| `/` | 搜索会话 |
| `g` | 切换 Group |
| `q` | 退出 |

---

## 推荐在线架构中的应用

对于**推荐服务工程师**，Agent Deck 的实际价值：

1. **多模块并行开发**：同时用 Claude Code 改 C++ 推荐引擎、用 Gemini CLI 写 Python 分析脚本、用 Codex 生成测试，Agent Deck 统一管理，不再切换 Tab

2. **实验性重构**：对某个 brpc 接口有两种重构方案？Fork 两个 Claude 会话分别探索，不用担心丢失上下文

3. **MCP 按场景切换**：本地开发时开 browser MCP 用于调试；线上 review 时换成 web-search MCP 查文档——一键切换，无需改配置

4. **团队 Skill 标准化**：团队共用 `~/.agent-deck/skills/pool/`（通过 dotfiles 共享），保证 code-review、test-gen 等 Skill 在所有项目中行为一致

---

## 技术亮点

| 特性 | 实现 |
|------|------|
| TUI 框架 | Ink（React for terminals）|
| 会话持久化 | 进程 detach + UNIX socket 通信 |
| MCP 热插拔 | 拦截 Agent 的 MCP 配置文件写入，restart 时注入新配置 |
| Fork 实现 | 序列化对话历史 JSON → 新进程加载 `--resume` |
| Git Worktree 集成 | 每个 Fork 可绑定独立的 git worktree，真正隔离代码变更 |

---

*生成时间：2026-05-16 · 系列：AI 工具深度研究 · 项目：asheshgoplani/agent-deck*
