# ExecEngine MultiStreamEngine：从配置展开到请求级运行时
<!-- topic-nav:start -->
> 主题：`base/multistream-binding` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260529-exec-engine-multistreamengine-graphdependency-deep-dive.md)
<!-- topic-nav:end -->


> 日期：2026-06-04  
> 主题来源：未发现本机 daily-plan 文件；按双 Markdown 工作流延续 2026-06-03 主题，今天补齐 `MultiStreamEngineInfoLoader` 如何把配置静态展开为运行时 stream/pip/executor/operator 图，以及请求中如何复用该图。  
> 范围：`add_engine_info()`、`create_engine_info()`、`load_pip()`、`load_executor()`、`load_op()`、`wire_up()`、`run_prepare()`、`run()`。  
> 内网文档：当前环境未发现可用 `ku` 命令；已按要求先设置 `BAIDU_CC_USERNAME=hujiyang` 检查，本文以代码库检索结果替代，线上配置/运维口径需人工补充。

## 1. 架构全景图

<div class="arch-wrapper" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8dee9;border-radius:16px;padding:18px;margin:16px 0;color:#1f2937;">
<div class="arch-title" style="font-size:22px;font-weight:800;margin-bottom:6px;">MultiStreamEngine：配置静态图 + 请求动态容器</div>
<div class="arch-subtitle" style="font-size:13px;color:#64748b;margin-bottom:16px;">配置加载阶段构建可复用的执行图；请求阶段只创建 stream 容器、绑定依赖并按槽位循环。</div>
<div class="arch-layer" style="background:#ffffff;border-left:5px solid #3d5a80;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">配置加载层 · MultiStreamEngineInfoLoader</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;">
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>global</b><br><span style="font-size:12px;color:#64748b;">conf_instance / conf_file</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>schema</b><br><span style="font-size:12px;color:#64748b;">dependency / mutable context</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>stream</b><br><span style="font-size:12px;color:#64748b;">name / trival / pip[]</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>executor</b><br><span style="font-size:12px;color:#64748b;">exec_type / stages</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>operator</b><br><span style="font-size:12px;color:#64748b;">factory + dataframe</span></div>
</div>
</div>
<div class="arch-layer" style="background:#ffffff;border-left:5px solid #2d6a4f;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">请求准备层 · wire_up / run_prepare</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>wire_up(file)</b><br><span style="font-size:12px;color:#64748b;">选择静态或动态 engine_info</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>op wire_up</b><br><span style="font-size:12px;color:#64748b;">初始化依赖数据与 codebook</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>create(map)</b><br><span style="font-size:12px;color:#64748b;">按 pip name 绑定输入</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>prepare</b><br><span style="font-size:12px;color:#64748b;">context / executor / operator request init</span></div>
</div>
</div>
<div class="arch-layer" style="background:#ffffff;border-left:5px solid #b45309;border-radius:12px;padding:14px;">
<div style="font-weight:700;margin-bottom:10px;">执行与观测层 · run</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>loop</b><br><span style="font-size:12px;color:#64748b;">loop_num < result_num</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>stream chain</b><br><span style="font-size:12px;color:#64748b;">generator_input → executor.run</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>rollback</b><br><span style="font-size:12px;color:#64748b;">check_output false 则重试本槽</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>dapper</b><br><span style="font-size:12px;color:#64748b;">采样上报算子耗时</span></div>
</div>
</div>
</div>

## 2. 核心结论

- `add_engine_info()` 是配置入口：读取 `[multi_stream_engine]`，解析 `conf_file/conf_instance/dependency_schema/mutable_context_schema`，再调用 `create_engine_info()` 展开 stream 图；证据见 `exec-engine/src/multi_stream_engine.cc:60-113`。
- `create_engine_info()` 会逐个 stream 读取 pip，再逐 executor 读取 operator，并把每个 operator dataframe 的依赖条件汇总到 engine 级 `dependencies_map`；证据见 `exec-engine/src/multi_stream_engine.cc:115-205`。
- `load_executor()` 只在首次创建时新建 `ExecStrategy` 与 strategy generator，并用 `engine+stream+pip+executor` 作为 key 缓存；后续动态创建复用同一 strategy，避免带状态策略重复初始化；证据见 `exec-engine/src/multi_stream_engine.cc:256-378`。
- `load_op()` 通过 factory 创建 `BatchOperator` 与 conf，加载 `conf/option/depend/mutable_data`，再写入 `host_meta`、schema、operator index；证据见 `exec-engine/src/multi_stream_engine.cc:381-450`。
- 请求期 `MultiStreamEngineInfo::create()` 根据 pip name 从 `item_container_map` 找输入，并把每个 stream 的输出再注册回 map；这使后续 pip 可以消费前序 stream 的结果；证据见 `exec-engine/src/multi_stream_engine.h:48-110`。

## 3. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "Config file" as conf
participant "MultiStreamEngineInfoLoader" as loader
participant "Factory" as factory
participant "MultiStreamEngineInfo" as info
participant "MultiStreamEngine" as engine
database "item_container_map" as map
participant "ExecutorStream" as stream
participant "BatchOperator" as op
conf -> loader : add_engine_info(file)
loader -> loader : parse global + schema
loop each stream
  loader -> loader : load_pip()
  loop each executor name
    loader -> loader : load_executor()
    loader -> factory : create ExecStrategy / generator
    loop each stage operator
      loader -> factory : create BatchOperator + Conf
      loader -> op : set dataframe schema / dependencies
    end
  end
end
loader -> info : store stream_infos + dependencies_map
engine -> info : wire_up(file)
engine -> op : wire_up(context) + init_dependency_data
engine -> info : run_prepare(map) / create(map)
info -> map : resolve pip input and register stream output
engine -> stream : prepare(context)
loop per result slot
  engine -> stream : run(input, output)
  stream -> op : real_process / rule selection
  engine -> op : update_context(result)
end
@enduml
```

## 4. 配置结构信息图

```infographic hierarchy-structure
data
  title MultiStreamEngine 元信息树
  desc 读配置时形成稳定执行图；请求期只补输入输出容器和上下文。
  items
    - label multi_stream_engine
      desc conf_instance / conf_file / schema
      children
        - label stream[]
          desc name、trival、pip[]
          children
            - label pip[]
              desc name 指向输入容器键；executor 可逗号分隔
              children
                - label executor
                  desc exec_type、is_syncer、stages、generator
                  children
                    - label BatchOperator
                      desc conf、option、depend、mutable_data、index
```

## 5. 调试 checklist

```infographic list-column-done-list
data
  title MultiStreamEngine 配置加载排障
  desc 初始化失败通常不是代码分支，而是配置字段、factory 注册或依赖名不匹配。
  items
    - label 检查 global 字段
      desc conf_file、conf_instance、dependency_schema、mutable_context_schema 缺失会直接 FATAL
      done true
    - label 检查 stream / pip name
      desc pip name 必须能在 item_container_map 中找到；stream 输出名会注册为后续输入键
      done true
    - label 检查 executor stages
      desc stages 里的每个 stage 都要在 executor 配置下存在数组
      done true
    - label 检查 operator factory
      desc name 必须已注册 BatchOperator 和 BatchOperatorConf
      done true
    - label 检查 depend 条件
      desc 同一依赖多条件会 OR 合并；空条件优先表示无条件依赖
      done true
    - label 检查采样开关
      desc Dapper 成本依赖 collect_multi_stream_op_cost_ratio 命中
      done true
```

## 6. Pitfalls 卡片

<div class="card-frame" style="max-width:980px;margin:18px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div class="card" style="background:#fbf9f1;border:1px solid #e7dfd1;border-radius:18px;padding:22px;color:#2f2a24;">
<div class="card-meta" style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#7c6853;">Pitfalls · Config to Runtime</div>
<div class="card-title" style="font-size:30px;font-weight:850;letter-spacing:-.02em;margin:8px 0 14px;">stream 名是输出键，pip 名是输入键。</div>
<div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;">
<div style="border-top:4px solid #7c6853;padding-top:12px;line-height:1.65;font-size:14px;">排查“某个 stream 没有输入”时，先按配置串起 `stream.name → item_container_map key` 与 `pip.name → input key`。如果前序 stream 输出名没有被后序 pip 引用，operator 本身完全正常也不会产出预期结果。</div>
<div style="background:#f1eadf;border-radius:14px;padding:14px;font-size:13px;line-height:1.6;">带状态 `ExecStrategy` 只应初始化一次；重复 key 会 FATAL，找不到 key 也会 FATAL。</div>
</div>
<div style="margin-top:14px;border-left:4px solid #7c6853;padding-left:12px;font-size:14px;line-height:1.6;">依赖条件汇总时，空 condition 表示无条件依赖，会覆盖多条件 OR 的排查方向。</div>
</div>
</div>

## 7. 证据来源

- `exec-engine/src/multi_stream_engine.cc:60-113`：配置入口与 global/schema 解析。
- `exec-engine/src/multi_stream_engine.cc:115-205`：stream/pip 展开与依赖条件合并。
- `exec-engine/src/multi_stream_engine.cc:208-254`：pip 到 executor 的展开。
- `exec-engine/src/multi_stream_engine.cc:256-378`：executor、ExecStrategy、generator 与 strategy cache。
- `exec-engine/src/multi_stream_engine.cc:381-450`：operator factory、conf/option/depend/mutable_data 加载。
- `exec-engine/src/multi_stream_engine.h:48-110`：请求级 stream 输入输出容器构造。
- `exec-engine/src/multi_stream_engine.h:215-283`：`wire_up()` 选择 engine info、operator wire_up 与 codebook。
- `exec-engine/src/multi_stream_engine.h:286-315`：`run_prepare()`。
- `exec-engine/src/multi_stream_engine.h:319-478`：按槽位执行、rollback、Dapper 成本上报。
