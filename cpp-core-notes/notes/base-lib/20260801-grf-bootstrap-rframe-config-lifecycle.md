# GRF 前端框架启动链路：从 brpc 服务到 RFrame 节点注册

> 日期：2026-08-01  
> 范围：GRF gr-front 入口、RFrame IOC 注册、动态 so 装载、配置与 channel 初始化  
> 说明：未找到今日 daily-plan，按近期去重策略选择尚少覆盖的 GRF 前端框架主题；内网 KU 上下文需人工补充，本文以代码库证据为准。

<style>.grf-arch{font-family:Arial,"Microsoft YaHei",sans-serif;border:1px solid #d7dee8;background:#f8fafc;padding:18px;border-radius:8px;color:#1f2937}.grf-title{font-size:20px;font-weight:800;margin-bottom:12px}.grf-grid{display:grid;grid-template-columns:1fr 1.25fr 1fr;gap:12px}.grf-layer{border:1px solid #d9e2ef;background:#fff;border-radius:8px;padding:12px}.grf-layer h3{margin:0 0 10px 0;font-size:14px;color:#334155}.grf-box{border:1px solid #cbd5e1;border-left:4px solid #3d5a80;border-radius:6px;padding:9px;margin:8px 0;background:#fdfefe;font-size:13px;line-height:1.45}.grf-box strong{display:block;color:#1e3a5f;margin-bottom:3px}.grf-flow{margin-top:12px;display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.grf-step{background:#eaf2f8;border:1px solid #c7d7e8;border-radius:6px;padding:8px;text-align:center;font-size:12px;font-weight:700;color:#24415f}</style><div class="grf-arch"><div class="grf-title">GRF gr-front 启动全景</div><div class="grf-grid"><div class="grf-layer"><h3>进程入口</h3><div class="grf-box"><strong>main.cc</strong>解析 gflags、加载日志、构造 brpc ServerOptions</div><div class="grf-box"><strong>服务面</strong>NsheadJsonService 承接主请求，DiscardService 处理丢弃回调</div></div><div class="grf-layer"><h3>框架装配</h3><div class="grf-box"><strong>RFrame IOC</strong>注册 CPUNode、GRCPUNode、IONode、NsheadIO、RedisIO、HttpIO 等节点类型</div><div class="grf-box"><strong>动态插件</strong>init_so 按配置装载 pre_so_path / after_so_path，扩展业务节点能力</div><div class="grf-box"><strong>配置上下文</strong>GlobalInitializer 与 ConfManager 提供全局配置、树配置和运行参数</div></div><div class="grf-layer"><h3>运行依赖</h3><div class="grf-box"><strong>Channels</strong>初始化 RPC/HTTP 连接池，供 IONode 与 DAO 使用</div><div class="grf-box"><strong>业务树</strong>global.conf include 多个 tree 与 channel 配置，驱动请求走向</div></div></div><div class="grf-flow"><div class="grf-step">配置读取</div><div class="grf-step">IOC 注册</div><div class="grf-step">SO 装载</div><div class="grf-step">AddService</div><div class="grf-step">RunUntilAsked</div></div></div>

## 核心结论

GRF 的 gr-front 不是一个只注册 RPC handler 的薄入口，而是把三类工作压在启动期完成：一是把 RFrame 的节点类型注册到 IOC，二是按配置装载动态 so，三是把 brpc 服务、channel 和业务树配置绑定成运行时图。后续请求进入 `NsheadJsonService` 后，才能通过 `CommandDispatcher` 与配置树进入具体节点链路。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Operator as op
participant "main.cc" as main
participant "GlobalInitializer" as global
participant "RFrame IOC" as ioc
participant "SharedManager" as so
participant "brpc::Server" as server
participant "NsheadJsonService" as nshead
op -> main: start gr-front
main -> global: init conf / log
main -> ioc: register CPUNode / IONode / IO classes
main -> so: load pre_so_path / after_so_path
main -> server: AddService(nshead, discard)
main -> server: Start(port)
server -> nshead: dispatch user request
@enduml
```

## 配置到运行时的装配关系

```infographic
infographic hierarchy-structure
data
  title GRF 启动配置结构
  desc global.conf 聚合业务树与 channel，main.cc 再把配置转换为运行时对象
  items
    - label global.conf
      desc include sample_server_tree / interleaving_tree / list_tree / redis tree / channels
      icon mdi/file-tree
      children
        - label sample_server_tree.conf
          desc 声明 SampleServerIONode 的树节点
        - label interleaving_tree.conf
          desc 声明 InterleavingIONode 的树节点
        - label backend.conf
          desc condition 决定 product、command、channel_id 对应后端节点
    - label main.cc
      desc init_rframe + init_so + AddService
      icon mdi/application-cog
      children
        - label IOC register
          desc CPUNode、GRCPUNode、IONode、NsheadIO、RedisIO、HttpIO
        - label SharedManager load
          desc 按配置加载前置与后置 so
    - label Channels
      desc 为 IO 节点提供 RPC/HTTP 通道
      icon mdi/lan-connect
theme
  palette #3d5a80 #2d6a4f #b86b2d
```

## 代码阅读要点

1. `main.cc:108-116` 注册 RFrame 基础节点和 IO 类型，说明节点实例化不是靠硬编码 new，而是通过 IOC 名称解析。
2. `main.cc:44-92` 的 `init_so` 根据配置加载前置/后置 so，是业务能力热插拔的关键边界。
3. `main.cc:155-192` 创建 `NsheadJsonService`、注册服务并进入 `RunUntilAskedToQuit`，这是入口链路闭环。
4. `conf/global.conf:1-5` 作为聚合配置，只保留 include；实际树和后端选择分散在 `sample_server_tree.conf`、`interleaving_tree.conf`、`backend.conf`。
5. `conf/backend.conf:1-19` 用 `product`、`command`、`channel_id` 条件选择后端 IONode，业务分流在配置层就已经发生。

## Pitfalls 与调试卡片

<style>.grf-card{font-family:Arial,"Microsoft YaHei",sans-serif;background:#fbfbf8;border:1px solid #ddd8ce;border-radius:8px;padding:16px;color:#2f3437}.grf-card h3{margin:0 0 8px 0;font-size:18px}.grf-card-grid{display:grid;grid-template-columns:1.1fr .9fr;gap:12px}.grf-card-panel{border-top:4px solid #3d5a80;background:#fff;padding:12px;border-radius:6px;font-size:13px;line-height:1.6}.grf-card-panel strong{display:block;margin-bottom:4px;color:#1e3a5f}</style><div class="grf-card"><h3>调试判断：入口正常不代表业务树正常</h3><div class="grf-card-grid"><div class="grf-card-panel"><strong>易错点</strong>brpc 服务启动成功只能证明 AddService 和端口监听正常；若 IOC 注册、so 装载、global.conf include 或 backend condition 有误，请求仍可能在调度后失败。</div><div class="grf-card-panel"><strong>优先排查</strong>先看 main.cc 启动日志，再看配置 include 是否完整，最后确认目标 command/channel_id 是否命中 backend.conf 条件。</div></div></div>

```infographic
infographic list-column-done-list
data
  title GRF 启动链路调试 checklist
  items
    - label 确认 global.conf include 完整
      desc global.conf 应包含业务树、redis tree、channels 等配置入口
      done true
    - label 核对 init_rframe 注册项
      desc 新节点类型必须被 IOC 注册或由动态 so 提供
      done true
    - label 检查 init_so 装载路径
      desc pre_so_path / after_so_path 缺失会导致业务节点不可用
      done true
    - label 校验 backend condition
      desc product、command、channel_id 不匹配会绕过预期 IONode
      done true
    - label 再看请求 handler
      desc handler 只是入口，真正执行依赖配置树和节点链
      done true
theme
  palette #3d5a80 #2d6a4f #d97706
```

## 证据来源

- `main.cc:44-92`：按配置装载动态 so。
- `main.cc:108-116`：注册 RFrame 节点与 IO 类型。
- `main.cc:155-192`：创建并注册 brpc 服务，启动 server。
- `conf/global.conf:1-5`：聚合业务树和 channel 配置。
- `conf/backend.conf:1-19`：按 product / command / channel_id 选择后端节点。
- `src/grf/channels/channels.cc:26-40`：Channels 初始化入口。

## 待人工补充

- 今日 daily-plan 文件缺失，主题选择来自本地代码去重。
- KU/内网设计背景未检索到可用输入，需人工补充上线背景、调用方和变更历史。
