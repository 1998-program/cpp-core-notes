# 15 · Agent Deck — AI Agent 的终端会话管理中心

**项目**：[asheshgoplani/agent-deck](https://github.com/asheshgoplani/agent-deck)
**Stars**：2.5k ⭐ · Go · MIT
**定位**：终端 TUI 应用，统一管理 Claude Code、Gemini CLI、OpenCode、Codex 等多个 AI coding agent 的会话，支持 Telegram/Discord 远程监控

---

## 根本问题

当你同时使用多个 AI coding agent 时（比如 Claude Code 处理 A 项目，Gemini CLI 处理 B 项目，再加一个正在跑的 long-running research agent），它们分散在多个终端窗口里，无法统一监控状态、切换上下文，更不知道哪个已经完成在等待输入。Agent Deck 提供一个统一的 TUI "指挥台"，所有 agent 的运行状态一屏可见，支持 MCP 连接池管理、Telegram 远程通知等。

---

## 核心工作原理

```
┌─────────────────── Agent Deck TUI ──────────────────┐
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │ Claude Code  │  │ Gemini CLI   │  │  Codex CLI │ │
│  │ Project: A   │  │ Project: B   │  │ Research   │ │
│  │ Status: 🔄   │  │ Status: ✅   │  │ Status: ⏳ │ │
│  │ Last: 2m ago │  │ Last: done   │  │ Running... │ │
│  └──────────────┘  └──────────────┘  └────────────┘ │
│                                                       │
│  [1] Focus Claude  [2] Focus Gemini  [3] Fork Session│
│  [T] Telegram      [M] MCP Pool     [Q] Quit         │
└──────────────────────────────────────────────────────┘
```

### MCP 连接池（核心特性）

```yaml
# ~/.agent-deck/config.yaml
mcp_servers:
  github:
    command: "npx @modelcontextprotocol/server-github"
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
    
  filesystem:
    command: "npx @modelcontextprotocol/server-filesystem"
    args: ["/home/user/projects"]
    
# MCP 池化：多个 agent 共享同一个 MCP Server 连接
# 避免为每个 agent 启动独立的 MCP Server 进程
pool:
  github:
    min_connections: 2
    max_connections: 8
  filesystem:
    min_connections: 1
    max_connections: 4
```

### Session Fork（会话复制）

```bash
# 把当前会话 fork 给一个新 agent，让两个 agent 并行探索不同方案
# 在 TUI 里按 [F] Fork Session：
# Agent 1 继续当前方向（重构方案 A）
# Agent 2 在 fork 点的快照上开始（探索重构方案 B）
# 最后比较两个方案的结果
```

---

## 安装 / 快速上手

```bash
# 方法一：brew（macOS）
brew install agent-deck

# 方法二：下载预编译二进制
curl -sL https://github.com/asheshgoplani/agent-deck/releases/latest/download/agent-deck-linux-amd64.tar.gz | tar xz
sudo mv agent-deck /usr/local/bin/

# 方法三：从源码编译（需要 Go 1.24+）
git clone https://github.com/asheshgoplani/agent-deck
cd agent-deck && go build -o agent-deck .

# 启动
agent-deck
```

---

## 实践案例

**场景**：你在并行处理三个任务：①重构 `feature.cpp` 的性能热路径，②调研最新的 LLM 推理框架对比，③给项目写 README。三个任务分别用 Claude Code、Gemini CLI（因为需要 Google Search）和另一个 Claude Code 实例。以前这三个终端窗口乱开，Agent Deck 让它们统一管理。

**配置**：

```yaml
# ~/.agent-deck/config.yaml
agents:
  claude-perf:
    type: claude-code
    working_dir: ~/workspace/rec-service
    startup_message: "专注优化 feature.cpp 热路径"
    
  gemini-research:
    type: gemini-cli
    working_dir: ~/workspace
    startup_message: "调研 LLM 推理框架对比"
    
  claude-docs:
    type: claude-code
    working_dir: ~/workspace/rec-service
    startup_message: "专注更新项目 README"

mcp_servers:
  github:
    command: "npx @modelcontextprotocol/server-github"
    env: {GITHUB_TOKEN: "${GITHUB_TOKEN}"}
    
notifications:
  telegram:
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"
    on_events: ["agent_complete", "agent_waiting_input", "agent_error"]
```

**使用流程**：

```bash
agent-deck  # 启动 TUI

# TUI 界面：
┌──────────────────────────── Agent Deck ──────────────────────────────┐
│  claude-perf [feature.cpp]      gemini-research [research]           │
│  ████████░░ 80% 正在优化...     ██████████ 100% 完成！              │
│  "正在分析 line 89 的瓶颈"       "已生成对比报告，等待确认"          │
│                                                                       │
│  claude-docs [README]           MCP Pool: github(3/8) fs(1/4)       │
│  ████░░░░░░ 40% 进行中...       Notifications: Telegram ✅           │
│  "正在更新架构章节"                                                   │
│                                                                       │
│  [Tab] 切换  [Enter] 聚焦  [F] Fork  [T] Telegram通知  [Q] 退出      │
└──────────────────────────────────────────────────────────────────────┘

# gemini-research 完成了，手机 Telegram 收到通知：
# "✅ gemini-research 完成：LLM 推理框架对比报告已生成
#  文件位置：~/workspace/llm-comparison.md"

# 你在手机上回复："发布到 GitHub"
# Agent Deck 把命令转发给 gemini-research agent → 自动创建 PR
```

**MCP 连接池节省的开销**：

```
没有 Agent Deck：
3 个 agent × 2 个 MCP Server = 6 个 MCP 进程（每个约 50MB）
总内存：~300MB

有 Agent Deck MCP Pool：
1 个 GitHub MCP Server（共享）+ 1 个 Filesystem MCP Server（共享）
总内存：~100MB，节省 66%
```

---

## 关键特性速查

- **多 Agent 统一视图**：所有运行中的 agent 状态一屏展示，不再迷失在多个终端窗口
- **MCP 连接池**：多 agent 共享 MCP Server 连接，节省内存和启动时间
- **Session Fork**：复制会话给新 agent 并行探索，比较不同方案
- **Telegram 远程通知**：agent 完成或等待输入时推送手机通知，支持远程回复
- **兼容所有主流 agent**：Claude Code、Gemini CLI、OpenCode、Codex CLI 均支持

---

**GitHub**：https://github.com/asheshgoplani/agent-deck
