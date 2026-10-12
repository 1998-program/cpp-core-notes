# wshobson/agents — 多 Harness 统一的 AI Agent Plugin Marketplace

> 一套 Markdown 源码，五个 AI Coding Harness 的原生制品：Claude Code / Codex CLI / Cursor / OpenCode / Gemini CLI / GitHub Copilot。88 插件、194 Agent、158 Skill、106 Command，从单体源自动生成各平台最佳实践格式。

| 维度 | 数据 |
|------|------|
| **Stars** | 37,334+ |
| **语言** | Python (Tooling) + Markdown (Content) |
| **协议** | MIT |
| **组件规模** | 88 Plugins / 194 Agents / 158 Skills / 106 Commands |
| **支持 Harness** | Claude Code / Codex CLI / Cursor / OpenCode / Gemini CLI / Copilot |
| **最后更新** | 2026-06-29 |

---

## 一句话定位

**写一次 Markdown，生成五种 Harness 的原生插件格式** —— 不是最低公分母翻译，而是利用各平台特性（Codex 的 TOML、Gemini 的 subagent spec、Cursor 的 rules）的「真正原生」制品。

---

## 核心使用场景

### 场景一：跨团队工具标准化

**问题**：团队里有人用 Claude Code，有人用 Cursor，有人用 Codex CLI —— 同一个开发规范（如 Python 代码风格、安全审查流程）需要在每个工具里单独配置，维护成本高。

**解决方案**：

```bash
# 团队只需维护 plugins/python-development/ 一份源码
# 运行 make generate-all 自动生成各平台制品
make generate-all  # 生成 .codex/, .cursor/, .opencode/, .gemini/ 等

# Claude Code 用户
/plugin marketplace add wshobson/agents
/plugin install python-development

# Cursor 用户
# 直接读取 .cursor/rules/ 和 .claude/skills/

# Gemini CLI 用户
gemini extensions install .
```

**效果**：Python 编码规范、测试策略、依赖管理规则在五个 Harness 里保持一致，无需重复维护。

---

### 场景二：企业级质量门禁自动化

**问题**：AI 编码助手生成的代码质量参差不齐，企业需要统一的评审、安全扫描、测试覆盖标准。

**解决方案**：

```bash
# 使用内置的 plugin-eval 框架
uv run plugin-eval score plugins/security-compliance --depth quick    # <2s 静态检查
uv run plugin-eval score plugins/security-compliance --depth llm      # ~30s 语义评估
uv run plugin-eval certify plugins/security-compliance                # 完整认证

# 集成到 CI 流程
make validate STRICT=1   # 结构验证
make garden              # 漂移检测（死链接、过期文件、超限 skill）
make test                # 386 个测试用例
make smoke-test          # 真实 CLI 子进程测试
```

**效果**：每个插件通过三层质量门禁（静态 / LLM Judge / Monte Carlo），企业可自定义标准。

---

### 场景三：渐进式上下文注入

**问题**：传统 AI 编码助手的 AGENTS.md 或 CLAUDE.md 文件动辄上千行，token 浪费严重，且容易过时。

**解决方案**：

```
# AGENTS.md 仅 150 行 —— 作为目录索引
plugins/
└── agent-orchestration/
    ├── agents/
    │   └── orchestrator.md       # Agent 配置
    ├── skills/
    │   └── 1/
    │       ├── SKILL.md          # 核心 Skill（<8KB）
    │       └── references/
    │           └── details.md    # 详情（按需加载）
    └── commands/
        └── 1.md                  # Slash Command
```

**原理**：
- **Tier 0-4 模型分层**：Opus 负责架构/安全，Sonnet 负责文档/测试，Haiku 负责快速操作
- **Progressive Disclosure**：Agent 激活时才加载 Skill，Skill 详情按需读取 `references/details.md`
- **Body Cap**：Codex 的 8KB 限制自动截断到 `references/` 溢出

**效果**：典型任务 token 消耗降低 60%+，上下文窗口留给真正的业务逻辑。

---

### 场景四：多 Agent 协作编排

**问题**：复杂任务需要多个专业 Agent 协作（如全栈开发需要前端/后端/安全/测试 Agent），手动协调成本高。

**解决方案**：

```bash
# 安装 orchestration 相关插件
/plugin install agent-orchestration
/plugin install full-stack-orchestration
/plugin install security-compliance

# 调用内置的 16 个 Orchestrator
# 它们会自动调度相关 Agent 协作
```

**架构**：

```
Orchestrator Agent
├── 前端 Agent (frontend-mobile-development)
├── 后端 Agent (backend-development)
├── 安全 Agent (security-compliance)
└── 测试 Agent (tdd-workflows)
```

**效果**：一个 `/build-full-stack` 命令触发多 Agent 并行工作，各司其职。

---

## 架构设计亮点

### 1. Single Source of Truth

```
plugins/                    # 唯一源码目录
├── python-development/
│   ├── .claude-plugin/plugin.json
│   ├── agents/*.md
│   ├── commands/*.md
│   └── skills/*/SKILL.md
├── backend-development/
│   └── ...

# 生成的制品（gitignored 或 committed）
.codex/          # Codex TOML 格式
.cursor/         # Cursor rules
.opencode/       # OpenCode 权限块
gemini/          # Gemini subagents
.copilot/        # Copilot skills
```

**不变式**：永不手动编辑生成文件，所有修改在 `plugins/` 完成。

---

### 2. Adapter 框架

| Adapter | 输出 | 关键转换 |
|---------|------|----------|
| `codex.py` | `.codex/skills/`, `.codex/agents/` | Markdown → TOML，8KB 截断，sandbox_mode 推断 |
| `cursor.py` | `.cursor/rules/*.mdc` | 手工精选规则 + 读取 `.claude/` |
| `opencode.py` | `.opencode/agents/` | `permission:` 块从 `tools:` 生成，锁定的 Agent 自动 deny-all |
| `gemini.py` | `skills/`, `agents/` | April 2026 subagent spec，`@{path}` 注入 |
| `copilot.py` | `.copilot/` | Markdown profiles + SKILL.md + commands-as-skills |

---

### 3. Progressive Disclosure 模式

```python
# AGENTS.md (~150 行)
# ↓ 按需加载
# Skill SKILL.md (<8KB)
# ↓ 按需加载
# references/details.md (无限制)
```

**示例**：

```markdown
<!-- skills/1/SKILL.md -->
# Python Async Patterns

## Quick Reference
- Use `asyncio.gather()` for concurrent tasks
- Prefer `asyncio.create_task()` over `ensure_future()`

## When to Use
Use when implementing async I/O in Python services.

## References
See [details.md](references/details.md) for:
- Event loop internals
- Cancellation patterns
- Error propagation strategies
```

---

### 4. Model Tier 策略

| Tier | 模型 | 典型用途 |
|------|------|----------|
| 0 | Fable 5 | 超长自主任务（迁移、重构） |
| 1 | Opus | 架构设计、安全审查、生产代码 |
| 2 | inherit | 用户选择（后端/前端/ML 等专业领域） |
| 3 | Sonnet | 文档、测试、调试、API 引用 |
| 4 | Haiku | 快速操作、SEO、部署、内容生成 |

**动态映射**：Adapter 在生成时将 `opus` / `sonnet` 别名转换为各 Harness 的原生模型 ID。

---

## 快速上手

### Claude Code（原生支持）

```bash
# 添加市场
/plugin marketplace add wshobson/agents

# 安装插件
/plugin install python-development
/plugin install security-compliance
/plugin install agent-orchestration

# 查看已安装
/plugin list
```

### Gemini CLI

```bash
# 克隆并生成
gh repo clone wshobson/agents ~/agents && cd ~/agents
make generate HARNESS=gemini

# 安装
gemini extensions install .
```

### OpenCode

```bash
gh repo clone wshobson/agents ~/agents && cd ~/agents
make install-opencode  # 生成 + 符号链接
```

### Codex CLI / Cursor

```bash
# Codex
npx codex-marketplace add wshobson/agents

# Cursor
# 添加仓库后，/plugin install <name> 会读取 .cursor-plugin/ 和源码
```

---

## 插件分类速览

| 分类 | 插件示例 | 数量 |
|------|----------|------|
| **开发语言** | python-development, javascript-typescript, jvm-languages | 8 |
| **后端/前端** | backend-development, frontend-mobile-development | 5 |
| **安全合规** | security-compliance, backend-api-security, protect-mcp | 6 |
| **数据工程** | data-engineering, machine-learning-ops, llm-application-dev | 5 |
| **基础设施** | cloud-infrastructure, kubernetes-operations, cicd-automation | 6 |
| **质量保障** | tdd-workflows, unit-testing, comprehensive-review | 8 |
| **编排协作** | agent-orchestration, agent-teams, full-stack-orchestration | 16 |
| **文档内容** | documentation-generation, seo-content-creation | 7 |
| **业务分析** | startup-business-analyst, quantitative-trading | 4 |

---

## 与同类项目对比

| 项目 | 跨平台支持 | 质量门禁 | Progressive Disclosure | Adapter 框架 |
|------|-----------|----------|------------------------|--------------|
| **wshobson/agents** | 5 Harness | ✅ 三层评估 | ✅ SKILL.md + references/ | ✅ 自动生成 |
| awesome-claude-code | 单平台 | ❌ | ❌ | ❌ |
| agent-skills | 单平台 | ❌ | 部分 | ❌ |
| cursor-rules | 单平台 | ❌ | ❌ | ❌ |

**核心差异**：不是「资源列表」，而是「可安装的跨平台插件系统」。

---

## 实战建议

### 1. 企业团队部署

```bash
# Fork 仓库，添加企业专属插件
# plugins/company-security/
# plugins/company-api-standards/

# 配置 CI 自动生成和验证
# .github/workflows/validate.yml 已包含：
# - make validate
# - make garden
# - make test
# - smoke-test (OpenCode + Gemini CLI)
```

### 2. 自定义 Agent 分层

```markdown
<!-- agents/my-backend-agent.md -->
---
name: backend-expert
description: Use PROACTIVELY when building production backend services
model: opus  # Tier 1
tools:
  - read
  - write
  - exec
---

Expert in scalable backend architecture, API design, and performance optimization.
```

### 3. 集成到现有项目

```bash
# 方式一：作为子模块
git submodule add https://github.com/wshobson/agents.git .agents

# 方式二：仅安装需要的插件
/plugin install python-development
# 只加载该插件的 agents/commands/skills，不加载整个市场
```

---

## 关键文件路径

```
wshobson/agents/
├── AGENTS.md                    # 统一上下文文件（150 行索引）
├── ARCHITECTURE.md              # 架构概览
├── CLAUDE.md                    # → AGENTS.md 符号链接
├── plugins/                     # 源码目录（唯一编辑点）
│   ├── python-development/
│   ├── security-compliance/
│   └── ... (88 个插件)
├── tools/
│   ├── adapters/                # 各 Harness 的适配器
│   ├── generate.py              # make generate 调用
│   └── validate_generated.py    # 结构验证
└── docs/
    ├── plugins.md               # 完整插件目录
    ├── agents.md                # 194 个 Agent 参考
    ├── authoring.md             # 编写规范
    └── harnesses.md             # 跨平台能力矩阵
```

---

## 总结

**wshobson/agents** 解决了 AI 编码工具碎片化的核心痛点：**一套内容，多平台原生**。其 Adapter 框架、Progressive Disclosure 模式、三层质量门禁，使其成为企业级 AI Agent 工具链的理想基础设施。

**推荐场景**：
- 团队使用多种 AI 编码工具，需要统一规范
- 企业需要质量门禁和认证机制
- 大型项目需要按需加载上下文，节省 token
- 需要多 Agent 协作的复杂任务

**项目地址**：https://github.com/wshobson/agents
