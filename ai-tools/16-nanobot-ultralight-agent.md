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

**对你的实际提升**：brpc 的 service + 推荐 DAG 长任务场景中，Agent 调用链路长、工具多，同样需要主动管理 context budget，不能依赖框架自动截断。

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

**对你的实际提升**：ng-framework DAG 计算图中，不同 pipeline 需要不同的工具集合。这种"无侵入 hook + 运行时 skill 开关"的设计值得借鉴——特别是在 FuncExecutor 注册与 Agent 工具暴露的绑定上。

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

**对你的实际提升**：推荐服务 brpc + jemalloc 的 P99 追踪痛苦在于跨服务调用链。OTel 的 baggage propagation（`session_id` 自动传到所有子 span）的思路可以迁移到 brpc 的 attachment 机制，实现 trace_id 零侵入传递。

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

## 与推荐在线架构的结合点

| nanobot 特性 | 推荐架构对应场景 |
|-------------|----------------|
| `/goal` 长目标机制 | 离线评估任务、批量 pipeline 的进度跟踪 |
| Dream 两阶段 Memory | 用户长期兴趣建模 + 短期会话上下文 |
| OTel NoOp Tracer | brpc 调用链无侵入 trace，attachment 传 baggage |
| Hook PRE_BUILD_CONTEXT | ng-framework DAG 节点级别的 context 裁剪 |
| fallback_models 配置 | 主模型降级到备用模型，类似 brpc 的 backup request |

---

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
