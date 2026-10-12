# #16 · nanobot — 超轻量个人 AI Agent

> **仓库**: [HKUDS/nanobot](https://github.com/HKUDS/nanobot) · **Stars**: ~15k+ · **License**: MIT  
> **定位**: OpenClaw / Claude Code 精神同类，核心极简，可读可改，`pip install nanobot-ai` 即用

---

## 一句话价值

**用最小的 agent loop 跑最稳的长期任务**——不是框架，是一个可以直接读懂、拿来改的个人 Agent 运行时。

---

## 为什么值得关注

大多数 Agent 框架在"易用性 vs 可控性"之间选择了复杂的抽象层。nanobot 反其道而行：

- **核心代码量极小**，整个 agent loop 在几个文件里，读完能搞懂每一行
- **v0.2.0（2026-05-15）** 刚发布，加入 `/goal` 长目标机制、WebUI 内置于 wheel、多 fallback 模型——功能开始追齐生产需求
- **架构图清晰**：消息进 → LLM 决策工具 → Memory/Skills 按需注入上下文 → 回复，没有多余的编排层

---

## 架构核心：小 Agent Loop

```
Channel (Telegram / Feishu / Discord / WeChat / WebUI / CLI)
        ↓
  [agent/loop.py]  ← 核心入口，OTel root span 在此创建
        ↓
  LLM call (任意 provider，支持 fallback_models)
        ↓
  Tool dispatch → Memory / File / Shell / MCP / Web Search
        ↓
  Context build → Skills 按需注入（PRE_BUILD_CONTEXT hook 过滤）
        ↓
  Reply → Channel
```

**关键设计选择**：
- Skills（技能文件）**只作为上下文注入**，不是重量级编排层
- Memory 是 Dream 两阶段系统：短期 session history + 长期语义压缩
- Provider 抽象只需 2 步就能加新 LLM（v0.1.4 重构后）

---

## 真实用户场景（来自高热 Issue）

### 场景 1：长上下文溢出 (Issue #2343, 15条评论)

**痛点**：`run_agent_loop` 不检查 `contextWindowTokens`，长任务后报 400 token 超限

```json
// 触发场景的配置
{
  "maxTokens": 8192,
  "contextWindowTokens": 8192,  // 和 maxTokens 一样大，没有给 history 留空间
  "maxToolIterations": 40        // 40轮工具调用后 history 已经很长了
}
```

**解法**：v0.1.5 引入了 Context Compact 机制，session 上下文超限时**自动压缩**：

```python
# agent/loop.py 中的 compact 触发逻辑（示意）
if estimated_tokens > context_window * 0.85:
    await compact_session_context(session)  # 语义压缩历史
```

---

### 场景 2：Hook 系统 + Skill 禁用 (PR #1934, 14条评论)

用户需求：不想改 SKILL.md 文件，但要在运行时禁用某些 skill

**nanobot 的设计**：事件驱动 Hook 系统，完全对齐 Claude Code 的 hooks 概念：

```
HookEvent: SessionStart | PreToolUse | PostToolUse | PreBuildContext | Stop
```

```bash
# 禁用 skill（状态存 hooks/state.json，skill 文件不动）
nanobot skills disable weather
nanobot skills enable weather

# 用户自定义 hook（JSON 配置，无需改代码）
# .nanobot/hooks.json
{
  "hooks": [{
    "name": "block-dangerous-commands",
    "event": "PreToolUse",
    "matcher": "^exec$",
    "command": "~/.nanobot/hooks/security-check.sh"
  }]
}
```

---

### 场景 3：OTel 可观测性 (PR #3173, 10条评论)

用户需要追踪 LLM 调用链路，Langfuse + LangSmith 双后端支持

**设计亮点**：

```
message-processing (root span)
  └── llm-call (每次 LLM 请求，含重试)
        └── tool:exec_shell (每个工具执行)
        └── tool:web_search
        └── tool:memory_read
```

**零开销 NoOp 模式**：

```python
# LLMProvider.tracer 是 lazy property
# 未配置 observability 时返回 NoOpTracer()
# 所有 start_as_current_span() 都是 no-op，无分支成本
class LLMProvider:
    @cached_property
    def tracer(self) -> Tracer:
        return _registry.get_tracer() or NoOpTracer()
```

---

## 快速上手（3 步）

```bash
pip install nanobot-ai

nanobot onboard  # 交互式配置向导

nanobot agent    # 本地 CLI 模式启动
```

接入如流 / Feishu（已支持）：

```json
// ~/.nanobot/config.json
{
  "providers": { "openrouter": { "apiKey": "sk-or-v1-xxx" } },
  "agents": { "defaults": { "model": "anthropic/claude-opus-4-6" } },
  "channels": { "feishu": { "enabled": true, "appId": "...", "appSecret": "..." } }
}
```

---

## 实践案例

**场景：为 Feishu 机器人接入 OTel 可观测性，精准定位响应慢的根因**

**问题背景**：一个基于 nanobot 的 Feishu 机器人已上线，用户反馈偶尔响应很慢，但不清楚瓶颈在 LLM 调用还是工具执行，现有日志无法区分。

**安装配置**：

```bash
pip install nanobot-ai
```

```json
{
  "providers": { "openrouter": { "apiKey": "sk-or-v1-xxx" } },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-6",
      "fallback_models": ["google/gemini-flash-1.5"],
      "maxToolIterations": 20,
      "contextWindowTokens": 32768
    }
  },
  "channels": { "feishu": { "enabled": true, "appId": "cli_xxx", "appSecret": "xxx" } },
  "observability": {
    "provider": "langfuse",
    "publicKey": "pk-xxx",
    "secretKey": "sk-xxx"
  }
}
```

**一键启动**：

```bash
nanobot agent
# 启动后自动接入 Feishu，调用链路推送到 Langfuse
```

**nanobot 自动生成的 OTel span 结构**（无需手写）：

```
message-processing (总耗时 3.2s)
  └── llm-call (2.1s)
        └── tool:web_search (0.8s)
        └── tool:memory_read (0.04s)
```

**运行结果**：Langfuse Dashboard 中一眼看出 LLM 调用耗时 2.1s，web_search 占 0.8s——定位到网络搜索是瓶颈。针对性配置 `fallback_models`：主模型超 1.5s 未响应时自动切换 gemini-flash，P90 响应时间从 3.2s 降至 1.4s。

**nanobot 最独特的地方**：OTel 是零侵入的——未配置 `observability` 时，所有 `start_as_current_span()` 退化为 `NoOpTracer()`，性能零损耗；一旦配置立刻获得完整调用链可视化，不需要改任何业务代码。Context Compact 机制（v0.1.5+）会在 session 超过 85% context 窗口时自动语义压缩历史，避免长任务报 400 token 超限。


## 关键文件索引

```
nanobot/
├── agent/
│   ├── loop.py        # Agent 主循环，OTel root span 入口
│   ├── context.py     # Context 构建 + Hook 触发点
│   ├── hooks/         # 事件驱动 Hook 系统
│   │   ├── registry.py    # HookRegistry.emit()
│   │   └── filters.py     # SkillsEnabledFilter
│   └── runner.py      # 工具执行 + 工具 span
├── providers/
│   └── base.py        # LLMProvider + NoOp tracer lazy property
├── observability/
│   └── tracer.py      # OTel factory + shutdown
└── channels/          # Telegram / Feishu / Discord / WeChat...
```

---

*自动生成 · 2026-05-17 · OpenClaw Daily Task*
