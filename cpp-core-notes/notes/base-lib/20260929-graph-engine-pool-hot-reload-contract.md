# 2026-09-29 基础库代码理解：GraphEngine 图池与双缓冲热加载合同

> 日期：2026-09-29（周二）
> 主题选择：未发现当日 daily-plan；近期计划文件也未在本地快照中找到。本次按邻近未覆盖主题回退到 `feed-general/framework` 的 **GraphEngine 配置装载、GraphPool 池化与 GraphManager 双缓冲热加载**。KU 正文未读取，业务背景与线上收益需人工补充；本文只依据当前代码快照。
> 配对关系：业务库笔记分析 generator-service 的请求到响应 GraphProcessor 链；本文只分析框架层的图生命周期、池化访问与重载隔离。
> 范围：`baidu/feed-general/framework/include/graph_engine.h`、`baidu/feed-general/framework/src/graph_engine.cpp`、`baidu/feed-general/framework/include/graph_manager.h`、`baidu/feed-general/framework/src/graph_manager.cpp`、`baidu/feed-general/framework/include/graph_function.h`。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.05em;">GraphEngine 生命周期与热加载边界</div><div style="display:grid;grid-template-columns:1.05fr 24px 1.25fr 24px 1.25fr 24px 1.15fr;gap:8px;align-items:center;margin-top:12px;"><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #3d5a80;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">配置入口</div><div style="margin-top:7px;font-size:12px;color:#52606d;">GraphManager::wireup 创建前台 GraphEngine；GraphEngine::wireup 解析 parser 与 graph 配置目录。</div></div><div style="text-align:center;color:#64748b;font-size:20px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #2d6a4f;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">构图与池化</div><div style="margin-top:7px;font-size:12px;color:#52606d;">GraphUnit 先完成 parser、dependency、ExpressionProcessor 注入，再按 pool_size 建立 GraphPool。</div></div><div style="text-align:center;color:#64748b;font-size:20px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #9a5b28;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">请求访问</div><div style="margin-top:7px;font-size:12px;color:#52606d;">GraphEngine::get/try_get 返回池化图实例；业务 processor 在 GraphPath 上消费依赖与 emit。</div></div><div style="text-align:center;color:#64748b;font-size:20px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #b8432f;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">后台重载</div><div style="margin-top:7px;font-size:12px;color:#52606d;">GraphManager 维护两个 shared_ptr 槽位，后台图构建成功后原子切换索引，旧图等待引用归零。</div></div></div><div style="margin-top:10px;font-size:12px;color:#52606d;">核心合同：配置解析和池初始化发生在请求前；请求只拿池化实例；重载不得破坏仍在执行请求持有的旧 GraphEngine。</div></div>

## 1. 核心结论

1. **GraphUnit 是配置到可运行 GraphPool 的装配边界。** `wireup` 先解析模板、parser 与 graph 配置，再进入 `init_pool`；`init_pool` 调用 `ExpressionProcessor::apply`、绑定 page heap、执行 `_builder.finish()`，最后通过 `_pool->initialize` 创建实际 Graph 对象（`baidu/feed-general/framework/src/graph_engine.cpp:51-101`、`:198-210`、`:298-320`）。
2. **GraphEngine 的访问是池化访问，不是每请求重建。** `get` 按名字查找图，找不到时回退到首个图，再调用对应 GraphUnit 的 `get()`（`graph_engine.cpp:322-341`）；`try_get` 则在产品/渠道或图名不存在时返回空对象（`graph_engine.cpp:343-374`）。调用方必须区分 `get` 的容错回退与 `try_get` 的显式失败。
3. **GraphManager 用双槽位隔离重载和在线请求。** `wireup` 把初始图放入 `_graph_engines[0]`，启动后台 `run` 线程；后台线程选择非当前槽位构建或复用 GraphEngine，发现配置变更后执行 `reload_graph`，最后切换 `_graph_idx`。当后台图仍被请求持有时，`use_count() > 1` 会跳过本轮重载（`baidu/feed-general/framework/src/graph_manager.cpp:26-68`）。
4. **GraphFunction 的运行合同是 setup -> operator/process -> reset。** 框架在 `setup` 阶段注入配置，在 `operator()` 中执行前后 hook 与 `processor()`，在 `reset()` 中清理本轮状态（`baidu/feed-general/framework/include/graph_function.h:14-26`）。因此池化 Graph 的复用安全性依赖每个 function 正确 reset，而不是依赖重新分配对象。

## 2. 配置装载与 GraphPool 初始化

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GraphUnit 从配置到池化图实例
participant "GraphManager" as GM
participant "GraphEngine" as GE
participant "GraphUnit" as GU
participant "TemplateParser / UnitParser" as PARSER
participant "GraphBuilder" as BUILDER
participant "GraphPool" as POOL
GM -> GE : initialize()
GM -> GE : wireup(ApplicationContext)
GE -> GU : parse graph config
GU -> PARSER : parse template / unit parsers
PARSER --> GU : vertex + dependency definitions
GU -> BUILDER : apply ExpressionProcessor
GU -> BUILDER : page_heap + finish()
GU -> POOL : initialize(build, pool_size, cached_size)
POOL --> GE : pooled Graph ready
GE --> GM : publish engine slot
@enduml
```

`GraphUnit::init_pool` 中默认 `pool_size = 10`，并读取 `cached_size`、`reserve_global`、`life_cycle` 等配置项；实际初始化闭包调用 `_builder.build().release()` 生成 Graph。这里的风险不是“是否能构图”，而是 pool 参数、GraphBuilder 完成时机和 pooled object 生命周期是否与业务请求持有时间一致（`graph_engine.cpp:198-220`、`:298-312`）。

```infographic
infographic sequence-ascending-steps
data
  title GraphEngine 初始化顺序
  desc 配置解析、依赖绑定和池化构建必须在请求访问前完成
  items
    - label 1. parser
      desc 读取 graph parser 与模板配置
      icon mdi/file-tree
    - label 2. dependency
      desc 构造 vertex、global_depend 和 named dependency
      icon mdi/source-branch
    - label 3. builder
      desc ExpressionProcessor 注入后完成 GraphBuilder
      icon mdi/cog-sync
    - label 4. pool
      desc 用闭包构建 Graph 并初始化 GraphPool
      icon mdi/layers-triple
    - label 5. serve
      desc get/try_get 返回可复用图实例
      icon mdi/flash
 theme
  palette #3d5a80 #2d6a4f #b56b32
```

## 3. 双缓冲热加载与引用安全

`GraphManager::get_graph` 只读取当前 `_graph_idx` 指向的 shared_ptr；后台线程使用另一槽位，避免直接修改在线图。后台重载前检查 `bg_graph_ref.use_count() > 1`，说明旧后台图仍可能被请求或其他组件持有，此时跳过重载，等待下一轮（`graph_manager.cpp:26-30`、`:52-68`）。`reload_graph` 的顺序是 `initialize()` 后 `wireup(ApplicationContext::instance())`；wireup 失败则不应发布新槽位（`graph_manager.cpp:128-141`）。

这形成三个可操作的排查判断：

- **请求拿到空图**：先查 `try_get` 的产品/渠道映射，再查 GraphManager 当前槽位是否完成 wireup；不要先归因到业务 processor。
- **配置已改但流量未生效**：检查后台线程的 reload sleep、配置 mtime 检测和 `use_count` 阻塞；旧请求长时间持有 GraphPool 对象会延迟切换。
- **重载后状态串请求**：检查 GraphFunction 的 `reset()` 是否清空本轮状态，尤其是池化对象中的 GraphData、依赖指针和缓存字段。

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fffaf5;border:1px solid #ead7c0;border-left:5px solid #b56b32;border-radius:6px;padding:14px;margin:16px 0;color:#3d2b1f;"><div style="font-size:12px;font-weight:800;color:#8a4b20;text-transform:uppercase;letter-spacing:.05em;">Pitfalls / 关键边界</div><div style="margin-top:9px;font-size:14px;line-height:1.65;"><strong>回退语义：</strong> `get(name)` 找不到名字会回退到首个图，可能掩盖配置命名错误；需要严格校验时使用 `try_get`。<br><strong>引用阻塞：</strong> 双槽位不是强制中断，后台图的 `use_count` 大于 1 会延后重载。<br><strong>池化复用：</strong> GraphFunction 的 reset 是复用安全的前提；新增状态字段必须同步加入 reset。<br><strong>证据边界：</strong> 本文没有读取 KU 正文，线上配置发布策略和业务收益需人工补充。</div></div>

```infographic
infographic list-column-done-list
data
  title GraphEngine 调试 checklist
  items
    - label 确认 graph parser 与 graph 配置文件被加载
      desc 检查 wireup 返回值和 parser not found 日志
      done true
    - label 确认 GraphPool 参数与初始化闭包
      desc 关注 pool_size、cached_size、reserve_global
      done true
    - label 区分 get 回退与 try_get 空返回
      desc 用图名、product_id、channel_id 复现查找路径
      done true
    - label 检查后台重载是否被引用计数阻塞
      desc 观察 graph being used shared num 日志
      done false
    - label 复核 GraphFunction reset
      desc 防止池化对象把请求状态带入下一轮
      done false
 theme
  palette #2d6a4f #3d5a80 #b8432f
```

## 4. 证据来源

- `baidu/feed-general/framework/include/graph_engine.h:41-107`：GraphUnit、GraphEngine 接口与池化访问合同。
- `baidu/feed-general/framework/src/graph_engine.cpp:51-101`：配置解析与 GraphUnit wireup。
- `baidu/feed-general/framework/src/graph_engine.cpp:198-320`：GraphPool 初始化、GraphBuilder 构建和监控暴露。
- `baidu/feed-general/framework/src/graph_engine.cpp:322-410`：GraphEngine 初始化、get/try_get 和目录 wireup。
- `baidu/feed-general/framework/include/graph_manager.h:30-44`：双 GraphEngine 槽位与管理接口。
- `baidu/feed-general/framework/src/graph_manager.cpp:26-68`、`:128-141`：当前图读取、后台 reload 和引用安全。
- `baidu/feed-general/framework/include/graph_function.h:14-26`：setup、processor、reset 生命周期合同。
