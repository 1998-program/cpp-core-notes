# 02 · Serena — 给 AI Coding Agent 装上 IDE 级代码理解能力

**项目**：[oraios/serena](https://github.com/oraios/serena)
**Stars**：24.7k ⭐ · Python · MIT
**定位**：MCP 工具包，为 AI coding agent 提供符号级代码检索、重构、调试能力，让 agent 操作代码像 IDE 一样精准

---

## 根本问题

主流 AI coding agent（Claude Code、Copilot、Cursor）处理代码时，本质上是在做"文本搜索和替换"——它们不理解代码的符号结构，遇到跨文件重命名、函数引用追踪、接口实现查找等场景，要么靠全文搜索硬搜，要么直接搞错。一个本来5分钟的重构，agent 可能要用10步才能完成，还可能遗漏引用。Serena 给 agent 接入 Language Server Protocol（LSP），让它能像 IDE 一样在**符号层面**理解和操作代码。

---

## 核心工作原理

Serena 作为 MCP Server 运行，底层调用 Language Server（每种语言有独立的 LSP 实现）：

```
Claude Code / Cursor / Copilot CLI
    ↓ MCP 调用
Serena MCP Server
    ↓ LSP 协议
Language Server（Python: pylsp / pyright, Java: eclipse.jdt, etc.）
    ↓ 语义分析
代码符号索引（符号表、引用图、类型信息）
```

核心工具分类：

```python
# 符号级检索（不是字符串搜索，是语义搜索）
serena.find_symbol("UserProfile")      # 找到定义位置 + 所有引用
serena.get_references("fetchUser")     # 列出所有调用点
serena.find_implementations("ICache")  # 找到接口的所有实现类

# 符号级编辑（不是行号替换，是结构化修改）
serena.rename_symbol("old_name", "new_name")   # 原子性跨文件重命名
serena.move_symbol("func", "src/utils.py")     # 移动函数到新文件，自动更新 imports

# 代码理解
serena.get_call_hierarchy("process_request")   # 谁调用了谁，树状展示
serena.get_type_info("request_handler")        # 获取类型签名和文档注释
```

---

## 安装 / 快速上手

```bash
# 方法一：用 uv（推荐）
uvx --from serena serena mcp install --client claude-code

# 方法二：pip
pip install serena
serena mcp install --client claude-code  # 自动写入 Claude Code MCP 配置
```

配置完成后，Claude Code 自动获得 Serena 的工具能力，无需改写工作流。

---

## 实践案例

**场景**：一个 Python 服务有个接口 `ICacheBackend`，有 Redis、Memcached、本地内存三种实现。现在要把接口方法 `get_with_fallback()` 重命名为 `fetch_with_fallback()`，同时要确保所有实现类和所有调用点都同步更新，分布在 12 个文件里。

**没有 Serena 的做法**（Claude Code 原生）：
1. 搜索字符串 `get_with_fallback` 找到所有出现位置
2. 逐文件手动替换（容易漏，容易误替换注释/字符串）
3. 不知道是否遗漏，要人工 review 所有文件

**有 Serena 的做法**：

```
# 在 Claude Code 对话里直接说
把 ICacheBackend 接口的 get_with_fallback 方法重命名为 fetch_with_fallback，
确保所有实现类和调用点都更新

# Claude Code 调用 Serena 工具的内部流程：
1. serena.find_symbol("get_with_fallback")
   → 返回：定义在 cache/interface.py:45，引用 47 处，分布 12 个文件
   
2. serena.rename_symbol("ICacheBackend.get_with_fallback", "fetch_with_fallback")
   → 原子性操作：接口定义 + 3个实现类 + 43个调用点，全部一次性更新
   → 自动跳过注释和字符串中的同名文本（符号级，不是文本级）
   
3. 验证：0 处遗漏，0 处误替换
```

**示例输出**：
```
✅ Renamed ICacheBackend.get_with_fallback → fetch_with_fallback
   - cache/interface.py: 1 definition updated
   - cache/redis_backend.py: 1 implementation updated  
   - cache/memcached_backend.py: 1 implementation updated
   - cache/local_backend.py: 1 implementation updated
   - services/user_service.py: 8 call sites updated
   - services/rec_service.py: 12 call sites updated
   - ... (7 more files)
   Total: 47 changes across 12 files
```

整个过程 1 次对话完成，无需人工逐文件 review。

---

## 关键特性速查

- **符号级操作**：所有工具操作符号而非文本行号，重构不遗漏不误改
- **LSP 驱动**：支持 Python/Java/TypeScript/Go/Rust/C++，每种语言都用对应的成熟 Language Server
- **自评估验证**：Serena 生成了标准化评估 prompt，可在自己的项目上验证实际效果
- **兼容所有 MCP 客户端**：Claude Code、Cursor、Copilot CLI、OpenCode 均可接入
- **无需改写工作流**：安装后 agent 自动调用，对用户完全透明

---

**GitHub**：https://github.com/oraios/serena
