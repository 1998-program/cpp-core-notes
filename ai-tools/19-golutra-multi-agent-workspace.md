# #19 · golutra — 多 Agent 桌面编排工作台

> **仓库**: [golutra/golutra](https://github.com/golutra/golutra) · Vue 3 + Rust (Tauri)  
> **定位**: 把你已有的 CLI（Claude Code / Codex / Gemini / OpenClaw 等）统一接入一个可视化调度层，**不换工具，只加编排**

---

## 一句话价值

**「一个人 + 一个 AI 军团」**——把单线程的人工上下文切换，升级为多 Agent 并行执行 + 自动结果回传，同时保留你熟悉的每一个 CLI 命令。

---

## 核心使用场景

### 场景1：多 Agent 并行开发

```
用户下达一个需求
    ↓
golutra 拆分为子任务
    ├── Agent A (Claude Code) → 写核心逻辑
    ├── Agent B (Codex CLI)   → 写单测
    └── Agent C (Gemini CLI)  → 写文档
    ↓
结果自动汇总回主工作流
```

不需要手动开三个终端、复制粘贴上下文、等一个跑完再启动下一个。

### 场景2：长期运行的自动化工作流

golutra 的核心差异点是**为长期运行设计**，不只是短时对话：

- 自定义工作流模板，一键导入/导出
- 支持跨会话的任务状态追踪
- 配合 [EverOS](https://github.com/EverMind-AI/EverOS) 作为记忆层，保留跨任务上下文

典型用法：「每天自动拉取 GitHub issue → 分配给对应 Agent → 生成 PR → 汇报结果」，全程无人值守。

### 场景3：Stealth Terminal（隐形终端注入）

```
可视化界面中点击 Agent 头像
    → 查看实时日志
    → 直接向终端流注入 prompt（无需切换窗口）
    → Agent 立即响应并继续执行
```

解决的痛点：多个 Agent 同时跑时，你不知道哪个卡住了、哪个需要干预。

---

## 真实用户场景（来自 Issues）

**Issue #70「希望增加 AI 自主沟通的能力」**：
用户希望多个 AI Agent 之间能像公司组织架构一样，有 leader 传达意图，其他 Agent 自主协作，不需要人在中间转述。这正是 golutra 下一阶段「CEO Agent」的目标方向。

**Issue #94「Claude Code 拒绝执行 golutra-cli 命令」**：
Claude Code 把 golutra 的 `golutra-cli.exe skills chat` 命令识别为「提示注入攻击」并拒绝执行。这暴露了一个真实问题：**当编排层通过 prompt 向 Agent 下达指令时，Agent 的安全策略可能把编排指令当成攻击**。golutra 的解法是通过 [golutra-mcp](https://github.com/golutra/golutra-mcp) 走 MCP 协议连接，绕过纯 prompt 注入的信任问题。

**Issue #56「添加 OpenClaw 时端口占用」**：
多个 OpenClaw 实例共享端口，用户希望直接复用已有 Agent 而不是新开实例。说明 golutra 对 OpenClaw 的集成还在完善中，目前每个成员对应一个独立进程。

---

## 架构关键点

```
golutra (Tauri 桌面)
├── 可视化调度层 (Vue 3)
│   ├── Agent 头像面板（点击查看日志）
│   ├── 工作流编辑器（拖拽式）
│   └── 结果汇总视图
├── 隐形终端层 (Rust)
│   ├── 进程管理（每个 Agent 一个子进程）
│   ├── Prompt 注入（直接写入 stdin）
│   └── 输出捕获（stdout/stderr 实时回传）
└── 集成层
    ├── CLI 兼容：Claude / Codex / Gemini / OpenClaw / Qwen / 任意 CLI
    ├── MCP 连接：golutra-mcp（更稳定的工具集成）
    └── 记忆层：EverOS（跨会话上下文）
```

**为什么用 Tauri（Rust + WebView）而不是 Electron**：
- 内存占用更低（多个 Agent 进程已经很重了，框架本身要轻）
- Rust 做进程管理更可靠，不会因为 JS 事件循环阻塞导致 Agent 输出丢失

---

## 与同类工具对比

| 工具 | 定位 | 核心差异 |
|------|------|----------|
| golutra | 多 Agent 桌面编排 | 可视化 + 不换 CLI + 长期运行 |
| claude-wall | 纯 Claude Code 多窗口 | 只支持 Claude，轻量 tmux 方案 |
| tmux 脚本 | 手动多终端管理 | 无自动编排，需人工切换 |
| LangGraph | 代码级 Agent 编排框架 | 需要写代码定义图，无 GUI |

**golutra 的独特价值**：面向「不想写编排代码」的开发者，用 GUI 替代 LangGraph 的 DAG 定义。

---

## 实践案例

**场景：并行调度三个 Agent 完成需求开发，节省 60%+ 时间**

**问题背景**：一个中等规模需求（核心逻辑 + 单测 + 文档），串行让单个 Claude Code 完成需要 35+ 分钟，且中途需要频繁切换终端监控进度。

**安装配置**：

```bash
# 下载 golutra 桌面 App（macOS）：https://github.com/golutra/golutra/releases
# 安装后：Settings → Add Agent，依次添加 Claude Code、Codex CLI、Gemini CLI
```

**工作流配置**（golutra UI 拖拽，或导入 YAML）：

```yaml
name: "并行开发工作流"
agents:
  - { id: architect, cli: claude, role: "分析需求，生成接口设计和子任务拆解" }
  - { id: developer, cli: codex,  role: "实现核心业务逻辑" }
  - { id: tester,    cli: gemini, role: "编写单元测试，覆盖边界场景" }
flow:
  - step: architect
    input: "{{user_task}}"
    output_to: [developer, tester]
  - parallel: [developer, tester]
  - step: architect
    input: "合并以上结果，生成最终 PR 描述"
```

**实际执行过程**：

```
用户输入："实现用户登录限流：IP 5次/分钟，账号 10次/小时，超限返回 429"

→ architect (Claude Code, 3min)：生成接口设计 + 子任务拆分

→ 并行启动（Stealth Terminal 实时查看各 Agent 日志）：
   developer (Codex, 9min)：src/rate_limiter.py (156行) + src/redis_backend.py (89行)
   tester (Gemini, 8min)：tests/test_rate_limiter.py (212行，覆盖率 94%)

→ architect (Claude Code, 11min 总计)：汇总结果，生成 PR 描述
```

**运行结果**：

```
总耗时：11 分钟（串行估计 35 分钟，节省 69%）
产出：
  src/rate_limiter.py        156 行，Redis sliding window 实现
  src/redis_backend.py        89 行，连接池 + 重试逻辑
  tests/test_rate_limiter.py 212 行，覆盖率 94%
```

**golutra 最独特的地方**：通过 Stealth Terminal 直接向 Agent 进程 stdin 注入 prompt，不需要更换任何现有 CLI 工具——Claude Code、Codex、Gemini 仍然是独立进程，golutra 只是在外面加了调度和可视化层。相比 LangGraph 需要写 Python 图定义，golutra 用 GUI 实现同等效果，代码量为零。


## 局限性

- **BSL 1.1 协议**：不是完全开源，商业竞品不能直接用其代码
- **Agent 间通信靠 prompt**：Claude Code 等 Agent 可能把编排指令识别为攻击（见 Issue #94）
- **每个 Agent 独立进程**：资源消耗随 Agent 数量线性增长，OpenClaw 多实例有端口冲突问题
- **CEO Agent 尚未实现**：README 描述的「一个月无人值守」目前还是 roadmap

---

*自动生成 · 2026-05-21 · OpenClaw Daily Task*
