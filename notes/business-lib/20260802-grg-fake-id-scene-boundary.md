# 2026-08-02 周日代码理解：GRG fake_id 与个性化关闭边界
<!-- topic-nav:start -->
> 主题：`business/personalization-id` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](2026-07-04-close-individual-fakeid-cross-service-boundary.md)
<!-- topic-nav:end -->


> 日期：2026-08-02  
> 主题来源：2026-08-02 daily-plan 缺失，按历史未覆盖主题 fallback 到 GRG `fake_id` / 个性化关闭边界  
> 服务：`feeda-mv-grg`  
> 范围：分析 `fake_id`、`is_close_individual`、scene 映射与图执行边界之间的联动；本文只聚焦请求分流和输出约束，不展开全部推荐策略。  
> 内网文档：当前环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:18px;margin:16px 0;color:#1f2937"><style scoped>.arch-wrap{display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px}.arch-col{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px}.arch-col h3{margin:0 0 8px 0;font-size:14px;color:#102a43}.arch-box{border:1px solid #bcccdc;border-radius:8px;padding:10px;margin:8px 0;background:#fdfefe}.arch-box strong{display:block;margin-bottom:4px;color:#102a43}.arch-mid{display:grid;grid-template-rows:auto auto;gap:12px}.arch-flow{border:1px dashed #bcccdc;border-radius:10px;padding:12px;background:#fbfdff}</style><div class="arch-wrap"><div class="arch-col"><h3>请求入口</h3><div class="arch-box"><strong>GRG request</strong>携带 UA、scene、fake_id、个性化开关</div><div class="arch-box"><strong>scene mapper</strong>把请求字段映射到 `short_micro_video`、`news_updates_dibar`、`default` 等图</div><div class="arch-box"><strong>policy gate</strong>决定是否走个性化，还是走降级链路</div></div><div class="arch-mid"><div class="arch-flow"><div class="arch-box"><strong>边界判断</strong><br/>`fake_id` 更像请求隔离标记，`is_close_individual` 更像业务策略开关。两者一起决定输入是否进入个性化图。</div><div class="arch-box"><strong>执行分支</strong><br/>匹配个性化图时，后续会进入多队列合并与策略函数；关闭时，则直接走收敛后的公共出口。</div></div><div class="arch-flow"><div class="arch-box"><strong>输出约束</strong><br/>最终返回需要保留请求可解释性，避免把“个性化关闭”误写成“算法失败”。</div></div></div><div class="arch-col"><h3>响应出口</h3><div class="arch-box"><strong>GRGResponse</strong>最终响应容器</div><div class="arch-box"><strong>business scene</strong>由 scene + 开关组合决定</div><div class="arch-box"><strong>fallback path</strong>个性化关闭时保底返回公共内容</div></div></div></div>

## 1. 核心时序

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "Client" as C
participant "GRG Service" as S
participant "Scene Mapper" as M
participant "Policy Gate" as G
participant "Diversity / Recall Graph" as D
participant "Response" as R
C -> S : request(fake_id, scene, is_close_individual)
S -> M : resolve scene
M -> G : evaluate policy
alt personalized enabled
  G -> D : enter personalized graph
  D -> R : merge / rank / emit
else personalized closed
  G -> R : bypass to fallback response
end
R -> C : return result
@enduml
```

## 2. 场景信息图

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title fake_id 与个性化关闭的职责分离
  items
    - label fake_id
      desc 请求隔离与标识，帮助服务区分不同实验或用户组。
      children
        - label 作用
          desc 只负责标识，不直接决定策略。
    - label is_close_individual
      desc 个性化关闭开关，直接控制是否进入个性化图。
      children
        - label 作用
          desc 决定业务是否走降级路径。
```

## 3. 关键理解

这条边界最容易写错的地方，是把 `fake_id` 当成策略开关。实际上它更多是标识和隔离信息，真正控制链路分支的是 `is_close_individual` 和 scene 映射后的图选择。

另一个要点是：个性化关闭并不等于服务失败。它应该被记录为一次有意的业务降级，响应里需要保留“为什么没有进入个性化图”的解释能力，否则排查时会把正常降级误判为策略异常。

## 4. Pitfalls

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff7ed;border:1px solid #fdba74;border-radius:12px;padding:14px;margin:16px 0;color:#7c2d12"><div style="font-size:15px;font-weight:800;margin-bottom:6px">常见坑</div><ul style="margin:0;padding-left:18px"><li>把 `fake_id` 误当成个性化开关，导致场景判断和实验标记混在一起。</li><li>只看返回结果，不看 scene 分支，排查时会漏掉“降级路径其实是正确路径”。</li><li>修改了图映射，却没同步关闭态的 fallback，容易出现个性化开关生效但响应出口没变的错觉。</li></ul></div>

## 5. 调试 checklist

```infographic
infographic list-column-done-list
data
  title 调试 checklist
  items
    - label 确认 request 中的 fake_id 是否只是标识
      done true
    - label 确认 is_close_individual 是否命中了关闭分支
      done true
    - label 确认 scene mapper 选中的图是否正确
      done true
    - label 确认降级路径是否保留解释信息
      done true
    - label 确认输出没有把降级写成错误
      done true
```

## 6. 证据来源

- `notes/business-lib/20260718-grg-fake-id-scene-boundary.md`
- `notes/business-lib/20260724-grc-video-launch-response-contract.md`
- `notes/business/20260529-grg-diversity-soft-rules-in-feeda-mv-grg.md`
- `src/service/grg_service.cpp`
- `src/request/grg_request.cpp`
- `conf/grg_scene_mapping.conf`

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260802-grg-fake-id-scene-boundary.md)。
