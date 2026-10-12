# feeda-mv-gr 业务链路：GRC/GRG 召回、融合与响应生成

> 日期：2026-07-03｜类型：业务库笔记｜主题来源：本机未发现可用 `daily-plan` 文件；按周五 fallback 选择 feeda-mv-gr 业务链路，从本地代码检索补齐 GRC/GRG 召回、Dibar 分支、ExecMerge 与响应生成。已按任务要求设置 `BAIDU_CC_USERNAME=hujiyang`；未提供 KU URL/doc-id，业务口径需人工补充。

## 1. 架构全景：业务链路是“召回路由 + 多流融合 + 响应拼装”

<style>.bizflow{font-family:Inter,Arial,sans-serif;background:#f8fafc;border:1px solid #d7e3ef;border-radius:18px;padding:18px;margin:14px 0;color:#172033}.bizflow-title{font-size:25px;font-weight:850;letter-spacing:-.02em}.bizflow-sub{font-size:13px;color:#526274;margin:8px 0 16px}.bizrow{display:grid;grid-template-columns:1fr 34px 1fr 34px 1fr;gap:8px;align-items:stretch}.bizbox{background:#fff;border:1px solid #d7e3ef;border-radius:14px;padding:12px}.bizbox h3{margin:0 0 8px;color:#1f4f7a;font-size:15px}.bizbox p{font-size:12.5px;line-height:1.55;color:#526274;margin:0}.bizarr{display:flex;align-items:center;justify-content:center;font-size:22px;color:#64748b;font-weight:900}.bizsignals{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px}.bizsig{border-radius:12px;padding:10px;background:#eef6ff;border:1px solid #c7def5}.bizsig b{display:block;font-size:13px}.bizsig span{font-size:12px;color:#526274;line-height:1.45}.bizsig.hot{background:#fff7ed;border-color:#fed7aa}.bizsig.ok{background:#ecfdf5;border-color:#bbf7d0}</style><div class="bizflow"><div class="bizflow-title">feeda-mv-gr 业务链路全景</div><div class="bizflow-sub">默认图把用户/历史/请求特征准备好后，核心由 `MicroVideoGrcRecallProcessor` 决定访问 GRC、GRG、lite-grc 或 lite-grg；召回结果进入旧 merge_graph 或 ExecEngine 融合，最后由 GenResponse 拼装下发。</div><div class="bizrow"><div class="bizbox"><h3>请求与上下文</h3><p>`ChangedCommonInfo`、`DeviceInfo`、`RefreshInfo`、`SidInfo`、历史与 UMS 特征由前置图节点准备，作为业务 Processor 的强依赖。</p></div><div class="bizarr">→</div><div class="bizbox"><h3>GRC/GRG 召回</h3><p>`MicroVideoGrcRecallProcessor` 按 lite、MGrc/MGrg、开关与跳过标记选择下游，并把 `GRCRequest` 写入业务字段。</p></div><div class="bizarr">→</div><div class="bizbox"><h3>融合与响应</h3><p>`merge_graph.conf` 定义推荐/直播/运营干预三类资源；`exec_merge.cpp` 可按 `merge_engine.conf` 把多输入流交给 ExecEngine。</p></div></div><div class="bizsignals"><div class="bizsig ok"><b>GRC Request</b><span>承载 product、ua、flow_loc、sid、history、query、兴趣与隐私字段。</span></div><div class="bizsig hot"><b>flow_loc rewrite</b><span>ua 87 + flow_loc 69 + `dibar_to_ertiao` 可被改写为 60。</span></div><div class="bizsig hot"><b>ExecMerge</b><span>`exec_merge_grc` 命中后透传 `call_pcs`，Graph 内也有 `ExecMergeProcessor`。</span></div><div class="bizsig ok"><b>GenResponse</b><span>从 `EndMergeResult` 构造 JSON/feedlist/GeneralResponse，补充标签、合集、端重排等 ext。</span></div></div></div>

## 2. 业务调用链：从默认图到响应

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor "feed request" as req
participant "global.conf default graph" as graph
participant "Request/History/UserFeature" as prep
participant "MicroVideoGrcRecallProcessor" as recall
participant "GRC/GRG/lite plugin" as down
participant "merge_graph / ExecMerge" as merge
participant "GenResponseProcessor" as resp
participant "Response / GeneralResponse" as out
req -> graph: enter default graph
 graph -> prep: parse_request + recall_user + history + cache_request
prep --> recall: CommonInfo / SidInfo / RequestFeature / History / UMS
recall -> recall: choose lite-grg/lite-grc/grg/grc
recall -> recall: genRequest(copy flow_loc, sid, query, history, call_pcs)
recall -> down: plugin->call(GRCRequest, timeout_cntl)
down --> recall: GRCResponse
recall -> recall: parseResponse into MicroVideoRecallInfo / Zhibo / MergeInfo
recall --> merge: MergeResultDsInfo / ZhiboDsData / IntervenDsResult / MergeDsData
merge -> merge: rank/select/pk/update rules
merge --> resp: EndMergeResult
resp -> resp: get_feedlist + req_ext + user_ext + cate/interest/card data
resp --> out: Response or GeneralResponse
@enduml
```

## 3. 配置与 Stream 信息图

```infographic
infographic list-grid-badge-card
data
  title 业务链路关键配置
  desc 本地代码可验证的默认图、召回、融合与 Dibar 分支
  items
    - label global.conf
      desc include micro_video.conf、merge_graph.conf、exec_merge.conf、jisu_duanju_dibar_recall.conf 等业务子图
      icon mdi/file-tree-outline
    - label micro_video.conf
      desc MicroVideoGrcRecallProcessor 发出召回、直播、融合、兴趣、GrcAttachment、RealRecNum 等数据
      icon mdi/radar
    - label merge_graph.conf
      desc gr_merging 使用 mv_feed_rec、mv_zhibo_rec、mv_operate_interven 三类资源与 slot_rule
      icon mdi/source-merge
    - label merge_engine.conf
      desc ExecEngine select_stream 接入 MergeResultDsInfo、IntervenDsResult、ZhiboDsData、MergeDsData
      icon mdi/pipe
    - label jisu_duanju_dibar_recall.conf
      desc 短剧底 bar: recall -> FillMeta -> filter，产出 JisuDuanjuDibarRecallFinalResult
      icon mdi/movie-open-outline
```

## 4. 业务实现要点

### 4.1 MicroVideoGrcRecallProcessor 是下游路由中心

- `conf/plugins/graph/micro_video.conf:1-36`：该节点 emit `MicroVideoRecallInfo`、`MicroVideoZhiboInfo`、`MicroVideoMergeInfo`、`ChangedCommonInfoGrc`、`RealRecNum`、`GrcAttachment`、`KeziIdRecallInfo` 等业务数据。
- `conf/plugins/graph/micro_video.conf:37-172`：依赖覆盖召回开关、场景信息、历史、SidInfo、请求特征、搜索向量、FeedUfs、UMS 等，说明它不是单纯 RPC 包装，而是 GRC/GRG 请求聚合点。
- `src/processor/micro_grc_recall.cpp:250-268`：lite-grg 由 `Util::is_lite_grg(ua, os_branch, sid_info)` 判定；lite-grc 当前硬编码 ua 189 且 os_branch 为 `a2` 或 `i3`。
- `src/processor/micro_grc_recall.cpp:299-333`：下游选择顺序是 lite-grg → lite-grc → MGrg → MGrc/switch_down_stream，并设置 `is_grg_request`。

### 4.2 召回请求有多个“业务改写点”

- `src/processor/micro_grc_recall.cpp:353-358`：只有在 plugin 存在、不是 search 请求好看、没有 `NoNeedRecallGrc`、没有 `IsSkipRequestGRG` 时才真正调用下游。
- `src/processor/micro_grc_recall.cpp:524-543`：`flow_loc` 会被业务场景改写；ua 123 非 108 改 50，ua 87 + flow_loc 69 + `dibar_to_ertiao` 改 60，通用小视频场景改 103，通用搜索场景改 55。
- `src/processor/micro_grc_recall.cpp:621-633`：sid 字符串由 `sid_set_str` 与 smfw 拼接后传给 GRC。
- `src/processor/micro_grc_recall.cpp:662-664`：`exec_merge_grc` 命中时设置 `common_info.call_pcs`，这是 GRC 侧融合框架/运营强插统一的开关。
- `src/processor/micro_grc_recall.cpp:725-755`：Dibar 查询命中 `dibar_fill_query_vec` 时会校验 64 维 query ernie vec，并透传给 GRC。

### 4.3 融合有两套路径：merge_graph 与 ExecMerge

- `conf/plugins/graph/merge_graph.conf:1-18`：`gr_merging` 定义 schema、item_id、merge_score、resource_type、CommonInfo 与 SidInfo。
- `conf/plugins/graph/merge_graph.conf:19-33`：rank 规则为 `QBasedMultiResRank`，资源包括 `mv_feed_rec`、`mv_zhibo_rec`、`mv_operate_interven`。
- `conf/plugins/graph/merge_graph.conf:35-68`：slot_rule 包括运营干预与直播插入；直播规则依赖 `RealRecNum`、`IsQuanmingSlide`、`IsSparkApp`、`IsBottomBar`、Dibar 直播开关等。
- `conf/plugins/graph/exec_merge.conf:1-10`：Graph 中只有 `ExecMergeProcessor` 一个 merge vertex，输入流依赖从 `merge_engine.conf` 自动加载。
- `src/processor/exec_merge.cpp:56-98`：读取 `multi_select_stream.select_stream.pip`，把 pip name 映射成 GraphDependency，并预留 `input_name_vec/input_dep_vec`。
- `src/processor/exec_merge.cpp:100-117`：`engine.wire_up(plugins/merge_engine.conf)` 后绑定 ExecEngine 的 graph dependency。
- `src/processor/exec_merge.cpp:133-187`：运行期把每个输入 `std::vector<DynamicStruct*>` 包成 `InputStreamContainer`，以 `rec_num` 作为 loop num，运行 engine 并输出 `pk_output`。

### 4.4 Dibar/短剧分支是独立召回后再回到响应选择

- `conf/plugins/graph/jisu_duanju_dibar_recall.conf:1-17`：`JisuDuanjuDibarRecallProcessor` 依赖 CommonInfo、DeviceInfo、SidInfo、SceneInfo、RefreshInfo，产出短剧底 bar recall result。
- `conf/plugins/graph/jisu_duanju_dibar_recall.conf:18-34`：`FillMetaProcessor` 使用 `handle_name: jisu_duanju_dibar_recall` 填充 meta。
- `conf/plugins/graph/jisu_duanju_dibar_recall.conf:36-56`：`JisuDuanjuDibarFilterProcessor` 依赖 meta 结果、历史过滤、SidInfo，产出 `JisuDuanjuDibarRecallFinalResult`。
- 这条链路说明 Dibar 不是只靠 `MicroVideoGrcRecallProcessor` 内部字段透传，也可以在默认图里通过独立 vertex 形成召回 + meta + filter 的闭包。

### 4.5 响应生成负责把融合结果转成可下发结构

- `src/processor/gen_response.cpp:42-102`：`GenResponseContext` 依赖 `EndMergeResult`、用户策略、场景、DibarProfile、UMS、Follow、PCS sample、FeedAiTopic 等大量业务数据。
- `src/processor/gen_response.cpp:109-163`：setup 阶段绑定 `EndMergeResult` 到 `merge_result_info`，并 emit `Response`、`SampleResponse`、`GeneralResponse`。
- `src/processor/gen_response.cpp:206-220`：process 阶段取 `merge_result_info`，清理 nshead/protobuf 响应与 sample log。
- `src/processor/gen_response.cpp:286-355`：遍历融合结果构造合集/短剧 next 信息，并按场景调用 `get_feedlist` 或 after-reading response 逻辑。
- `src/processor/gen_response.cpp:367-383`：补充 cate interest、req_ext、interest_card_ext、user_ext，并设置 `errno`、`num`、`need_disaster_recovery`。

## 5. Pitfalls 卡片

<style>.bizpit{font-family:Inter,Arial,sans-serif;background:#f4f3f0;border:1px solid #ddd4c8;border-radius:18px;padding:18px;margin:18px 0;color:#1f2933}.bizpit-meta{font-size:12px;font-weight:800;letter-spacing:.08em;color:#2d6a4f;text-transform:uppercase}.bizpit-title{font-size:28px;font-weight:850;letter-spacing:-.03em;margin:6px 0 12px}.bizpit-grid{display:grid;grid-template-columns:2fr 1fr;gap:14px}.bizpit-main{font-size:14px;line-height:1.7;color:#3f3f46}.bizpit-panel{border-top:4px solid #2d6a4f;background:#fffefa;border-radius:10px;padding:12px;font-size:13px;line-height:1.6}.bizpit-panel b{display:block;margin-bottom:5px;color:#2f2f2f}.bizpit-end{font-size:12px;color:#2d6a4f;font-weight:800;margin-top:12px}</style><div class="bizpit"><div class="bizpit-meta">Business Pitfalls</div><div class="bizpit-title">先确认“有没有调用下游”，再分析召回空</div><div class="bizpit-grid"><div class="bizpit-main">`MicroVideoGrcRecallProcessor` 会因为 search 请求好看、`NoNeedRecallGrc`、`IsSkipRequestGRG`、plugin 为空等原因跳过 RPC。召回为空时，如果直接看 GRCResponse 内容，容易忽略请求根本没有发出去。</div><div class="bizpit-panel"><b>关键判断</b>看 `is_recall_mgrg`、`no_need_recall_grc_p`、`is_skip_request_grg_p`、`is_search_req_hk` 与 SIA 的 RPC 名称，先分清“未请求”和“请求后空返回”。</div></div><div class="bizpit-end">∎ 召回问题按 route → request → rpc → parse → merge → response 顺序排。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title feeda-mv-gr 业务问题排查 checklist
  desc 用本地代码可验证的字段和配置定位召回、融合、响应问题
  items
    - label 路由是否正确
      desc 检查 lite-grg/lite-grc/MGrg/MGrc 分支与 FLAGS_switch_down_stream
      done true
    - label 是否跳过 RPC
      desc 检查 is_search_req_hk、NoNeedRecallGrc、IsSkipRequestGRG、grc_plugin 是否为空
      done true
    - label flow_loc 是否被改写
      desc Dibar、通用小视频、通用搜索、ua 123 都可能改写 GRC common_info.flow_loc
      done true
    - label 融合输入是否齐全
      desc merge_graph 看 mv_feed_rec/mv_zhibo_rec/mv_operate_interven；ExecMerge 看 merge_engine.conf pip
      done true
    - label 响应是否被 EndMergeResult 切走
      desc GenResponse 只消费 EndMergeResult；Dibar/特殊话题/搜索等需检查 expression.conf 选择
      done true
```

## 7. 证据来源

- `conf/plugins/graph/global.conf:31-64`：默认图 include 业务子配置列表。
- `conf/plugins/graph/micro_video.conf:1-172`：MicroVideoGrcRecallProcessor emit/depend 列表。
- `src/processor/micro_grc_recall.cpp:120-247`：Processor setup 绑定依赖与输出。
- `src/processor/micro_grc_recall.cpp:250-333`：lite 与 GRC/GRG 下游路由选择。
- `src/processor/micro_grc_recall.cpp:353-385`：RPC 调用条件、容灾标记与 parseResponse。
- `src/processor/micro_grc_recall.cpp:524-543`：flow_loc 业务改写。
- `src/processor/micro_grc_recall.cpp:662-664`：`exec_merge_grc` 控制 `call_pcs`。
- `src/processor/micro_grc_recall.cpp:725-755`：Dibar query ernie vec 透传。
- `conf/plugins/graph/merge_graph.conf:1-86`：旧融合框架资源、rank、slot_rule。
- `conf/plugins/graph/exec_merge.conf:1-10` 与 `conf/plugins/merge_engine.conf:1-159`：ExecMerge 图节点与多流配置。
- `src/processor/exec_merge.cpp:44-198`：ExecMergeProcessor setup/process 主逻辑。
- `conf/plugins/graph/jisu_duanju_dibar_recall.conf:1-56`：短剧底 bar 召回、FillMeta、Filter 链路。
- `src/processor/gen_response.cpp:42-220`：响应生成依赖、输出与 process 起点。
- `src/processor/gen_response.cpp:286-383`：feed_list、ext、errno、num、disaster 字段构造。
