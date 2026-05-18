# #17 · OpenHuman — 个人 AI 超级智能体

> **仓库**: [tinyhumansai/openhuman](https://github.com/tinyhumansai/openhuman) · **Stars**: 3.4k+ · **License**: GNU  
> **定位**: 登顶 GitHub Trending 的开源个人 Agent，核心理念「Context in minutes, not weeks」——让 AI 在几分钟内吃透你的全部工作上下文，变成真正懂你的数字分身  
> **技术栈**: Rust 核心 + TypeScript 前端 + Tauri 桌面壳

---

## 一句话价值

**把你所有的服务数据（邮件/日历/代码/文档/聊天）喂给 Agent，每 20 分钟自动同步，Memory Tree 本地压缩存储，让 Agent 一开口就已经了解你的全部上下文。**

---

## 核心架构

```
118+ OAuth 集成（Gmail/Notion/GitHub/Slack/Calendar/Jira...）
        ↓  每 20 分钟 auto-fetch
 Auto-Fetch 引擎（Rust）
        ↓  清洗 + 压缩成 ≤3k token Markdown chunks
 Memory Tree（本地 SQLite + Obsidian .md vault）
        ↓  层级摘要树，Jaccard 去重合并
 Agent Orchestrator
        ↓  TokenJuice 压缩层（HTML→MD，短 URL，去非 ASCII）
 LLM 路由（reasoning / fast / vision 三档按任务分流）
        ↓
 Desktop UI + 桌面吉祥物 + 语音（STT/ElevenLabs TTS）
```

**关键设计选择**：
- Memory Tree 存 SQLite，不上云，隐私留本地
- 反射（Reflection）只对 orchestrator 触发，sub-agent transcript 不写入长期记忆（PR #1519）
- TokenJuice 压缩层在每个工具调用结果进 LLM 之前介入，最高降低 80% token 用量

---

## 最核心使用场景集合

### 场景 1：零上下文冷启动 → 秒变熟手

**痛点**：每次打开新 Agent，都要重新解释「我在做什么项目、团队背景、技术栈」，新 Agent 就像刚入职的实习生。

**OpenHuman 的解法**：
```
1. 连接 GitHub → 自动拉取你的仓库、commit、PR 历史
2. 连接 Notion/Google Docs → 自动拉取设计文档、会议记录
3. 连接 Slack → 自动拉取团队讨论上下文
4. 20 分钟后 Memory Tree 建立完毕
5. 直接问：「我们的认证模块最近有什么风险？」
   → Agent 已知道你的代码、最近的 PR review 评论、Slack 里的讨论
```

**真实用户反馈（来自 Trending 讨论）**：
> 以前我要花 2 周时间 prompt 调教才能让 Agent 真正有用，OpenHuman 1 小时内就能回答关于我项目的具体问题了

---

### 场景 2：多服务数据联动分析

118+ 集成全部作为「typed tool」暴露给 Agent，可以跨服务联动：

```
「把本周 Jira 里 P0 bug 的修复进度和对应的 GitHub PR 状态汇总一下，
  看看有没有 PR 已 merge 但 Jira 还没关的」

→ Agent 同时调用 Jira tool + GitHub tool + Calendar tool
→ 生成跨平台状态报告
```

不需要手动打开三个窗口，不需要写脚本，直接自然语言。

---

### 场景 3：Discord/Slack 对话记忆化

最新 PR #1993 实现了 Discord 对话的自动 Memory 化：

```
接入 Discord 后
  ↓  Tauri scanner 监听 Gateway 事件（READY/MESSAGE_CREATE/GUILD_CREATE...）
  ↓  按 channel 归一化成 transcript（含作者、时间戳、permalink）
  ↓  upsert 进本地 Memory Tree
```

效果：你在 Discord 技术频道讨论过的架构决策，Agent 下次会话直接知道，不需要你再复述。

---

### 场景 4：低成本 Reflection + 学习回路

PR #1519 的 SummarizerProvider 设计：

```toml
# config.toml
[learning.summarizer]
enabled = true
# 反射任务路由到 cheap 模型（hint:fast），不烧 orchestrator 级别推理
```

```
每次对话结束 → Reflection Hook 触发（仅 orchestrator）
  ↓  tool-call digest 压缩（按 tool 归并，保留 2 个样本，截断 160 chars）
  ↓  便宜模型生成反思摘要
  ↓  Jaccard 相似度（0.55 阈值）去重合并近似条目
  ↓  写入 Memory Tree
```

Agent 越用越懂你，且成本不会随使用次数线性增长。

---

### 场景 5：多 Agent 工具共享同一 Memory 后端

OpenHuman 支持对接 `agentmemory` 后端：

```toml
# config.toml
[memory]
backend = "agentmemory"
```

设置后，Claude Code、Cursor、Codex、OpenHuman 共用同一个持久化 Memory 存储——你在 Claude Code 里学到的项目上下文，OpenHuman 开口就知道。

---

## TokenJuice：token 压缩层

```
每个工具调用返回值 → TokenJuice
  ├── HTML → Markdown（去掉 tag，保留内容）
  ├── 长 URL → 短链
  ├── 非 ASCII 字符清理
  └── 输出截断至 context-window cap
```

效果：同等信息量，token 消耗降低最高 **80%**，直接影响每次对话的速度和成本。

---

## 安装（3 分钟上手）

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/tinyhumansai/openhuman/main/scripts/install.sh | bash

# Windows
irm https://raw.githubusercontent.com/tinyhumansai/openhuman/main/scripts/install.ps1 | iex
```

也可直接下载桌面 App：[tinyhumans.ai/openhuman](https://tinyhumans.ai/openhuman)

---

## 与同类工具对比

| | Claude Code | Hermes Agent | OpenHuman |
|--|-------------|--------------|----------|
| 记忆范围 | 当前对话 | 自学习 | Memory Tree + 118+ 服务数据 |
| 上下文建立 | 手动 | 渐进式 | 20 分钟全量同步 |
| 数据存储 | 无持久化 | 本地 | 本地 SQLite + Obsidian vault |
| Token 优化 | 无 | 无 | TokenJuice 最高降 80% |
| 开源 | ❌ | ✅ MIT | ✅ GNU |

---

## 关键文件索引

```
openhuman/
├── src/openhuman/
│   ├── learning/
│   │   ├── summarizer/      # SummarizerProvider，Reflection 路由到廉价模型
│   │   ├── transcript_ingest/merge.rs  # Jaccard 去重合并
│   │   └── reflection.rs    # 仅 orchestrator 触发 Reflection
│   ├── inference/provider/
│   │   └── factory.rs       # create_chat_provider_from_string，session gate
│   └── memory/              # Memory Tree + SQLite 存储
├── app/src-tauri/
│   └── discord_scanner.rs   # Discord Gateway 事件 → Memory ingest
└── config.toml              # learning.summarizer / memory.backend 配置入口
```

---

*自动生成 · 2026-05-18 · OpenClaw Daily Task*
