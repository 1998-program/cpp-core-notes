# 08 · How Claude Code Works — Claude Code 源码深度解析

**项目**：[Windy3f3f3f3f/how-claude-code-works](https://github.com/Windy3f3f3f3f/how-claude-code-works)
**Stars**：2.5k ⭐ · 文档项目 · MIT
**定位**：对 Claude Code 50 万行 TypeScript 源码的系统性解读，15 篇专题文档，覆盖 Agent 循环、上下文工程、工具系统、安全防护等核心设计

---

## 根本问题

Claude Code 是目前使用最广泛的 AI coding agent，但它的源码有 50 万行 TypeScript，从哪里开始读？这个项目的作者和 Claude Code 一起读源码，把关键设计决策提炼成 15 篇专题文档，让你不需要自己啃 50 万行代码，就能理解 Claude Code 为什么这样设计——这些设计思路可以直接用于构建自己的 AI Agent。

---

## 核心工作原理

项目本身是文档，但它揭示的 Claude Code 内部机制值得深入理解：

### Agent 主循环（ReAct 模式）

```typescript
// 简化的 Claude Code 主循环
async function query(userMessage: string) {
  while (true) {
    const response = await callClaudeAPI(context);
    
    if (response.type === 'text') {
      streamToTerminal(response.text);  // 流式输出，不等全部生成
    }
    
    if (response.type === 'tool_use') {
      // 工具预执行：模型还在输出时就开始执行工具
      const toolResult = await executeToolConcurrently(response.tool);
      context.addToolResult(toolResult);
      continue;  // 把工具结果注入上下文，继续循环
    }
    
    if (response.stop_reason === 'end_turn') break;
  }
}
```

### 为什么 Claude Code 感觉很快？三个关键设计

```
1. 全链路流式输出
   API → 解析 → 终端渲染，每个 token 立刻显示
   用户感知延迟 ≈ 首 token 延迟（约 0.5s），而不是总生成时间

2. 工具预执行（Tool Prefetching）
   模型说"我要读 src/server.cpp"时，文件已经在读了
   利用模型生成的 5-30 秒窗口，把 ~1 秒的工具延迟藏起来

3. 9 阶段并行启动
   不相关的初始化任务并行执行
   关键路径压到约 235ms
```

### 上下文压缩（Context Compaction）

```typescript
// 当上下文接近 token 上限时触发
async function compactContext(messages: Message[]) {
  // 1. 保留最近 N 轮对话（verbatim）
  const recentMessages = messages.slice(-KEEP_RECENT);
  
  // 2. 对历史对话生成摘要
  const summary = await summarize(messages.slice(0, -KEEP_RECENT));
  
  // 3. 重建上下文：摘要 + 最近对话
  return [
    {role: 'system', content: summary},
    ...recentMessages
  ];
  // 上下文从 100k tokens 压缩到 20k tokens，继续工作
}
```

### 权限系统（Bash AST 分析）

```typescript
// Claude Code 不是简单地问"你确定要执行这个命令吗？"
// 而是解析 Bash AST，识别危险操作

function analyzeCommand(cmd: string): RiskLevel {
  const ast = parseBashAST(cmd);
  
  // 检测危险模式
  if (ast.contains('rm -rf /')) return 'BLOCK';
  if (ast.contains('> /etc/passwd')) return 'BLOCK';
  if (ast.contains('curl | bash')) return 'CONFIRM';  // 需要用户确认
  
  // 检测重定向目标
  const redirectTargets = ast.getRedirectTargets();
  if (redirectTargets.some(isSystemFile)) return 'CONFIRM';
  
  return 'ALLOW';
}
```

---

## 安装 / 快速上手

这是文档项目，直接阅读：

```bash
# 在线阅读（推荐）
# https://windy3f3f3f3f.github.io/how-claude-code-works/

# 本地克隆
git clone https://github.com/Windy3f3f3f3f/how-claude-code-works
# 直接用浏览器打开 docs/ 目录下的 HTML 文件

# 配套实践项目（从零实现 Claude Code）
git clone https://github.com/Windy3f3f3f3f/claude-code-from-scratch
# ~4000 行 TypeScript 或 Python，11 章分步教程
```

---

## 实践案例

**场景**：你在用 Claude Code 开发，发现它有时候会"忘记"之前的对话内容，想理解这是为什么，以及如何通过 `CLAUDE.md` 配置来减少这种情况。

**阅读路径**：

1. 读第 5 章「上下文工程」→ 理解 Claude Code 如何管理 token 预算
2. 关键发现：Claude Code 有 4 种上下文来源，优先级从高到低：
   ```
   系统提示词（CLAUDE.md）> 工具结果 > 对话历史 > 文件内容
   ```
3. 理解压缩触发时机：当上下文超过 95% 时触发，历史对话被摘要替换
4. 实践：在 `CLAUDE.md` 里写入关键约束，确保压缩后仍然保留

```markdown
# CLAUDE.md（放在项目根目录）
## 关键约束（压缩后仍然保留）
- 所有 API 调用必须有错误处理
- 使用 absl:: 容器，不用 std:: 容器（热路径）
- 日志用 LOG(INFO)/LOG(WARNING)，不用 printf
- 构建命令：bcmake（不是 cmake）

## 项目结构
- src/: 核心服务代码
- proto/: Protobuf 定义
- test/: 单元测试
```

这样即使上下文被压缩，Claude Code 也不会"忘记"这些关键约束。

---

## 关键特性速查

- **15 篇专题文档**：Agent 循环、工具系统、上下文工程、安全防护、启动优化等全覆盖
- **源码级分析**：所有结论来自实际源码分析，不是猜测
- **配套实践项目**：`claude-code-from-scratch`，4000 行代码从零实现，TypeScript 和 Python 两个版本
- **在线文档站**：https://windy3f3f3f3f.github.io/how-claude-code-works/ 支持搜索
- **中英双语**：主文档中文，有英文版 README

---

**GitHub**：https://github.com/Windy3f3f3f3f/how-claude-code-works
