# 12 · Awesome Claude Code Subagents — 100+ 专用 Subagent 集合

**项目**：[VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents)
**Stars**：20.7k ⭐ · Shell · MIT
**定位**：100+ 开箱即用的专用 Claude Code subagent 配置集合，覆盖开发全流程，让 Claude Code 的 subagent 功能从"理论可用"变成"拿来就用"

---

## 根本问题

Claude Code 支持 subagent（子代理并行执行任务），但大多数用户不知道怎么写好一个 subagent 的 system prompt，导致要么结果质量差，要么任务边界不清晰。这个项目预制了 100+ 个针对特定任务优化过的 subagent 定义，涵盖代码审查、安全审计、文档生成、测试编写、数据库设计等场景，直接复用无需自己调 prompt。

---

## 核心工作原理

每个 subagent 是一个 markdown 文件，定义了：

```markdown
# security-auditor
---
name: security-auditor
description: 专门负责代码安全审计的 subagent
---

## Role
你是一个安全审计专家，专注于识别代码中的安全漏洞。

## Focus Areas
- SQL/NoSQL 注入
- XSS/CSRF 漏洞
- 不安全的反序列化
- 硬编码密钥/密码
- 越权访问问题
- 依赖漏洞

## Output Format
对每个发现的问题输出：
- 严重等级（Critical/High/Medium/Low）
- 漏洞位置（文件:行号）
- 漏洞描述
- 修复建议（含代码示例）
```

Claude Code 调用 subagent 时，把这个 system prompt 注入给子 agent，子 agent 专注执行自己的职责范围。

### 调用方式

```bash
# 在 Claude Code 里使用预制 subagent
/agent use security-auditor

# 或在对话中引用
> 用 security-auditor subagent 审计 src/ 目录
```

---

## 安装 / 快速上手

```bash
# 方法一：直接通过 Claude Code plugin marketplace
/plugin marketplace add VoltAgent/awesome-claude-code-subagents
/plugin install awesome-claude-code-subagents@all

# 方法二：手动克隆
git clone https://github.com/VoltAgent/awesome-claude-code-subagents
cp -r awesome-claude-code-subagents/agents ~/.claude/agents/

# 方法三：按需安装单个 agent
mkdir -p ~/.claude/agents
curl -sL https://raw.githubusercontent.com/VoltAgent/awesome-claude-code-subagents/main/agents/code-reviewer.md \
  > ~/.claude/agents/code-reviewer.md
```

---

## 实践案例

**场景**：周五下午，PR #52 需要在下班前 review，但改动涉及三个方面：性能优化（热路径改动）、安全（新增了一个外部 API 调用）、文档（新增了一个公开接口）。以前需要三个人分别 review 或者一个人花 2 小时，现在用三个 subagent 并行处理。

**流程**：

```bash
# 在 Claude Code 里
> 用三个 subagent 并行 review PR #52：
  1. performance-reviewer：重点检查热路径的内存分配和算法复杂度
  2. security-auditor：检查新增外部 API 调用的安全性（认证、输入校验、错误处理）
  3. doc-reviewer：检查新增公开接口的文档是否完整

# performance-reviewer subagent 的输出示例：
## Performance Review Results

### Critical Issues (Block)
**src/feature.cpp:156**
```cpp
// 每次请求创建 map，热路径不可接受
std::unordered_map<int64_t, float> scores;
for (const auto& item : candidates) {
    scores[item.id] = calculate_score(item);
}
```
建议：使用 thread-local 的 absl::flat_hash_map 或预分配池

### Warnings (Should Fix)
**src/feature.cpp:203**: std::vector::push_back 无 reserve，可能多次扩容
建议：改为 absl::InlinedVector<float, 16>

# security-auditor subagent 的输出示例：
## Security Audit Results

### High Severity
**src/external_api.cpp:89**
外部 API 调用缺少超时设置（可能被拖死）：
```cpp
// 问题代码
auto response = http_client.Get(url);  // 无超时

// 修复
auto response = http_client.Get(url, {.timeout = std::chrono::seconds(5)});
```

### Medium Severity  
输入未校验长度，可能导致超大请求打穿内存限制
```

**三个 subagent 并行执行，约 3 分钟完成**，汇总后发布到 PR 评论区。整个过程无需人工参与 review，开发者只需最终确认并批准。

**常用 subagent 类别**（项目包含 100+ 个）：

| 类别 | 代表 subagent |
|------|-------------|
| 代码质量 | code-reviewer, refactoring-advisor |
| 安全 | security-auditor, dependency-scanner |
| 测试 | test-writer, coverage-analyzer |
| 文档 | doc-generator, api-doc-reviewer |
| 数据库 | schema-designer, query-optimizer |
| 运维 | dockerfile-reviewer, k8s-config-auditor |
| 前端 | accessibility-checker, performance-auditor |

---

## 关键特性速查

- **100+ 预制 subagent**：覆盖开发全流程，专业级 system prompt，开箱即用
- **并行执行**：多个 subagent 同时工作，复杂 review 从小时级压缩到分钟级
- **职责清晰**：每个 subagent 专注一个领域，结果质量高于通用 prompt
- **易于自定义**：markdown 格式，复制修改即可创建新 subagent
- **社区驱动**：100+ contributor，持续新增 subagent

---

**GitHub**：https://github.com/VoltAgent/awesome-claude-code-subagents
