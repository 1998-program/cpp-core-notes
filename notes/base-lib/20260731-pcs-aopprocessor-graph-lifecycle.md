# PCS AOPProcessor 图算子生命周期：把业务 run() 收敛到 GraphEngine 回调

> 日期：2026-07-31  
> 范围：feeda-pcs 公共图算子基类、baipai PCS 图配置、processor 参数装配  
> 说明：未找到今日 daily-plan，按近期去重策略选择较少覆盖的 feeda-pcs AOPProcessor 链路；内网 KU 上下文需人工补充，本文以代码库证据为准。

<style>.pcs-arch{font-family:Arial,"Microsoft YaHei",sans-serif;border:1px solid #d7dee8;background:#f8fafc;padding:18px;border-radius:8px;color:#1f2937}.pcs-arch-title{font-size:20px;font-weight:800;margin-bottom:12px}.pcs-arch-grid{display:grid;grid-template-columns:1.05fr 1.2fr 1.05fr;gap:12px}.pcs-layer{background:#fff;border:1px solid #e5e7eb;border-radius:6px;padding:12px}.pcs-layer h3{font-size:14px;margin:0 0 10px 0;color:#334155}.pcs-box{border:1px solid #cbd5e1;background:#f8fafc;border-radius:5px;padding:9px;margin:7px 0;font-size:13px;line-height:1.45}.pcs-box strong{display:block;color:#0f766e;margin-bottom:3px}.pcs-arrow{text-align:center;font-weight:700;color:#64748b;padding-top:48px}.pcs-note{margin-top:12px;border-left:4px solid #0f766e;background:#eef6f4;padding:10px 12px;font-size:13px}</style><div class="pcs-arch"><div class="pcs-arch-title">架构全景：配置图顶点到 AOPProcessor 生命周期</div><div class="pcs-arch-grid"><div class="pcs-layer"><h3>配置层</h3><div class="pcs-box"><strong>multi_graph.conf</strong>选择 baipai / dc 图实例</div><div class="pcs-box"><strong>parameter_manager.conf</strong>把 flow 绑定到 processor_conf_file</div><div class="pcs-box"><strong>graph.conf</strong>声明 vertex、depend、emit_mid_data</div></div><div class="pcs-layer"><h3>GraphEngine 适配层</h3><div class="pcs-box"><strong>AOPProcessor</strong>继承 GraphProcessor，承接 setup/process 回调</div><div class="pcs-box"><strong>Context</strong>贯穿 init、run、字段读写和中间数据访问</div><div class="pcs-box"><strong>GraphVertex</strong>提供顶点依赖、输出和调度上下文</div></div><div class="pcs-layer"><h3>业务算子层</h3><div class="pcs-box"><strong>init(vertex, context)</strong>读取配置并建立字段映射</div><div class="pcs-box"><strong>run(context)</strong>执行召回、预测、过滤、融合等业务逻辑</div><div class="pcs-box"><strong>get_field_name()</strong>定义算子结果落点</div></div></div><div class="pcs-note">核心边界：GraphEngine 只关心 GraphProcessor::setup/process，业务侧只实现 init/run/get_field_name；AOPProcessor 是两者之间的窄适配层。</div></div>

## 核心结论

AOPProcessor 的价值不在于封装某个具体策略，而是把所有 PCS 业务算子的生命周期压成同一份契约：`setup(GraphVertex)` 和 `process(GraphVertex)` 属于图引擎回调，`init(GraphVertex&, Context&)`、`run(Context&)`、`get_field_name()` 属于业务实现。这样 `graph.conf` 只需要描述顶点和依赖，不需要知道每个 processor 的内部状态如何初始化。

从配置看，PCS 运行时至少分三层装配：`multi_graph.conf` 选择产品/场景图，`parameter_manager.conf` 决定请求参数使用哪份 processor 配置，`pcs_protocol/graph.conf` 再把 processor 组织为顶点依赖链。AOPProcessor 位于这三层配置和 C++ 业务实现之间，负责把图调度事件翻译成可复用的业务上下文。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor "PCS Request" as req
participant "multi_graph.conf" as mg
participant "parameter_manager.conf" as pm
participant "graph.conf" as graph
participant "GraphEngine" as ge
participant "AOPProcessor" as aop
participant "Concrete Processor" as cp
req -> mg : select graph / product flow
mg -> pm : bind processor_conf_file
pm -> graph : load vertex DAG
graph -> ge : register processors and dependencies
ge -> aop : setup(GraphVertex)
aop -> cp : init(vertex, context)
ge -> aop : process(GraphVertex)
aop -> cp : run(context)
cp --> aop : write field / mid data
aop --> ge : return status
@enduml
```

## 配置结构信息图

```infographic
infographic hierarchy-structure
data
  title PCS 图配置三层装配
  desc 从图实例到 processor 顶点的配置分工
  items
    - label multi_graph.conf
      desc 选择 baipai/dc 等图实例和 flow 入口
      icon mdi/source-branch
      children
        - label parameter_manager.conf
          desc 按参数组绑定 processor_conf_file
          icon mdi/tune
          children
            - label baipai_processor.conf
              desc 请求字段、client、processor 映射
              icon mdi/file-cog
            - label graph.conf
              desc vertex/depend/emit_mid_data DAG
              icon mdi/graph-outline
theme
  palette #0f766e #475569 #d97706
```

## 代码阅读要点

`include/common_base/graph/aop_processor.h:24-43` 定义了 AOPProcessor 的关键边界：公开的虚函数是 `init`、`run`、`get_field_name`，私有侧保留 `setup(GraphVertex&)`、`process(GraphVertex&)` 以及携带 `Context` 的通用处理接口。这说明具体算子不应直接改写 GraphEngine 的入口协议，而应把业务状态放入 Context 或 processor 自身初始化逻辑。

`conf/baipai/flows/parameter_manager.conf:1-80` 多次把参数组绑定到 `baipai_processor.conf`，并在后段切到 `dc_processor.conf`。这类配置是常见的“同一套图框架，多份 processor 参数”的边界：调度拓扑稳定，参数和 client 绑定按业务线变化。

`conf/baipai/flows/pcs_protocol/graph.conf:162-255` 可以看到多个 `[.@vertex]` 块，例如 `BaipaiGrcRecallDataProcessor`、`BaipaiExplorationInterestVideoProcessor`，并通过 `[..@depend]` 和 `[..emit_mid_data]` 串接中间数据。这里的 processor 名称就是 AOPProcessor 子类进入 GraphEngine DAG 的显式锚点。

## Pitfalls

<style>.pit-card{font-family:Arial,"Microsoft YaHei",sans-serif;border:1px solid #d9e2ec;background:#fff;padding:16px;border-radius:8px;color:#243447}.pit-head{font-size:18px;font-weight:800;margin-bottom:10px;color:#0f766e}.pit-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.pit-item{border-top:3px solid #0f766e;background:#f8fafc;padding:10px;border-radius:5px;font-size:13px;line-height:1.55}.pit-item b{display:block;margin-bottom:4px;color:#1f2937}</style><div class="pit-card"><div class="pit-head">调试时最容易踩错的边界</div><div class="pit-grid"><div class="pit-item"><b>不要从具体 processor 反推全图</b>先看 graph.conf 的 vertex/depend，否则容易把单个召回算子的局部依赖误判为完整链路。</div><div class="pit-item"><b>不要跳过 parameter_manager</b>同名 processor 在不同 flow 下可能读不同 processor_conf_file，线上表现会随参数组变化。</div><div class="pit-item"><b>不要在业务 run() 假设依赖已存在</b>依赖是否产出由 graph.conf 的 depend/emit_mid_data 决定，缺失时应检查图配置而不是只查 C++。</div><div class="pit-item"><b>不要把 AOPProcessor 当业务策略</b>它是生命周期适配层；策略语义在子类和配置绑定里。</div></div></div>

## 调试 Checklist

```infographic
infographic list-column-done-list
data
  title AOPProcessor 链路排查顺序
  items
    - label 确认 graph 实例
      desc 从 multi_graph.conf 找到产品和 flow 入口
      done true
    - label 确认 processor 参数文件
      desc 在 parameter_manager.conf 中定位 processor_conf_file
      done true
    - label 确认顶点依赖
      desc 在 graph.conf 检查 vertex/depend/emit_mid_data
      done true
    - label 确认子类实现
      desc 阅读 processor 的 init/run/get_field_name
      done true
    - label 确认中间数据缺口
      desc 缺字段优先查依赖配置，其次查 run 写入逻辑
      done true
theme
  palette #0f766e #64748b #f59e0b
```

## 证据来源

- `README.md:1-19`：feeda-mv-pcs / feeda-dc-pcs 模块和图引擎可视化入口。
- `include/common_base/graph/aop_processor.h:24-43`：AOPProcessor 生命周期接口和 GraphProcessor 回调边界。
- `conf/baipai/flows/multi_graph.conf:1-80`：图实例入口配置。
- `conf/baipai/flows/parameter_manager.conf:1-80`：参数组到 processor 配置文件的绑定。
- `conf/baipai/flows/pcs_protocol/baipai_processor.conf:1-120`：baipai processor 参数结构。
- `conf/baipai/flows/pcs_protocol/graph.conf:162-255`：processor 顶点、依赖和 mid-data 产出关系。
