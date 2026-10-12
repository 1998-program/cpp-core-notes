# 01 · Context7 — 给 LLM 实时喂最新文档

**项目**：[upstash/context7](https://github.com/upstash/context7)
**Stars**：56k ⭐ · TypeScript · MIT
**定位**：MCP Server，把 100+ 开源库的最新版文档实时注入 LLM 上下文，消灭"幻觉 API"问题

---

## 根本问题

LLM 的训练数据有截止日期。你问 Claude "React 18 的 `useTransition` 怎么用"，它给你的可能是 React 17 时代的写法；你让它写 Next.js 15 的 middleware，它可能生成一个已被废弃的 API。Context7 通过 MCP 协议，在每次对话时实时从官方文档源拉取最新版本的代码示例，直接注入提示词，让 LLM 永远在用"今天的文档"而不是"一年前的记忆"。

---

## 核心工作原理

Context7 本质上是一个**文档代理层**，工作流如下：

```
用户提示词 (含 "use context7")
    ↓
Context7 MCP Server 解析库名和版本
    ↓
向 context7.com 文档索引发起 API 请求
    ↓
返回该库该版本的精确代码片段 + 文档段落
    ↓
注入到 LLM 上下文窗口
    ↓
LLM 基于最新文档生成代码
```

它维护了一个持续同步的**文档索引库**，覆盖 100+ 主流开源库（React、Next.js、Supabase、LangChain、FastAPI 等），每次文档更新都会自动同步。

提供两种工具调用接口：
- `resolve-library-id`：根据库名模糊匹配，返回 Context7 库 ID
- `get-library-docs`：根据库 ID + 查询词返回相关文档片段（可指定 token 预算）

```typescript
// MCP 工具调用示例（Claude Code 内部发出）
{
  "tool": "get-library-docs",
  "input": {
    "context7CompatibleLibraryID": "/vercel/next.js",
    "topic": "middleware authentication",
    "tokens": 5000
  }
}
// 返回：Next.js 15 middleware 的最新示例代码，含类型定义
```

---

## 安装 / 快速上手

```bash
# 一键安装（推荐）
npx ctx7 setup
# 选择 MCP 模式，自动配置到 Claude Code / Cursor / OpenCode

# 手动配置（Claude Code）
claude mcp add context7 -- npx -y @upstash/context7-mcp
```

使用方式极简——在任意提示词末尾加 `use context7` 即可：

```
帮我写一个 Next.js 15 的 JWT 鉴权 middleware。use context7
```

---

## 实践案例

**场景**：从 React 18 升级到 React 19，旧代码里大量用了 `useEffect` 做数据请求，想改成新的 `use()` + Suspense 模式，但 Claude 老是给出 React 17 风格的写法。

**配置**：
```bash
npx ctx7 setup --claude  # 配置到 Claude Code
```

**使用流程**：

```
# 在 Claude Code 里输入
把以下组件的数据请求从 useEffect 模式迁移到 React 19 的 use() + Suspense 模式。
use library /facebook/react

// 旧代码
function UserProfile({ userId }) {
  const [user, setUser] = useState(null);
  useEffect(() => {
    fetchUser(userId).then(setUser);
  }, [userId]);
  if (!user) return <div>Loading...</div>;
  return <div>{user.name}</div>;
}
```

**Context7 注入的文档片段**（真实从 React 19 官方文档拉取）：

```typescript
// React 19 use() API - Context7 提供的最新示例
import { use, Suspense } from 'react';

function UserProfile({ userPromise }) {
  // use() 可以在条件语句和循环中使用（不同于 Hooks）
  const user = use(userPromise);
  return <div>{user.name}</div>;
}

function App({ userId }) {
  const userPromise = fetchUser(userId);
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <UserProfile userPromise={userPromise} />
    </Suspense>
  );
}
```

**输出结果**：Claude 基于 Context7 注入的文档，生成了完全符合 React 19 规范的代码，没有出现 `useState` + `useEffect` 的旧模式。整个迁移过程 Claude 还自动检测了代码中所有用到旧模式的组件并批量改写。

没有 Context7 时，Claude 生成的代码仍然是 `useEffect` 模式，因为它的训练数据截止在 React 19 发布之前。

---

## 关键特性速查

- **版本精确**：可在提示词里指定版本（如 `Next.js 14`），Context7 会匹配对应版本文档
- **零配置使用**：只需在提示词里加 `use context7`，无需其他改动
- **Token 预算控制**：`get-library-docs` 的 `tokens` 参数控制注入量，避免撑爆上下文
- **Library ID 语法**：用 `/org/repo` 格式精确指定库（如 `/supabase/supabase`），跳过模糊匹配
- **支持 100+ 库**：Next.js、React、FastAPI、LangChain、Supabase、Drizzle、Prisma 等主流库均已收录

---

**GitHub**：https://github.com/upstash/context7
