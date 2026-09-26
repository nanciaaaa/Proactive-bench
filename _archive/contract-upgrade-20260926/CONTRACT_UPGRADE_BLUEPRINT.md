# 六款仿真 App 契约收敛方案蓝本

> 版本：v0.1-draft　日期：2026-09-22
> 依据：`../../../相关文件/AA-ACA_Benchmark_v1_原子场景簇与冻结世界数据构建正式方案_20260922.md`（下称 AA-ACA v1）与 `../../../相关文件/mobileworld_app_backend_spec/`（下称 backend spec）。
> 定位：把本工程六款 App 与 Local Controller 的注入/查询/事件/验证接口收敛到 backend spec 的 Adapter 契约与 AA-ACA v1 的 Passive/冻结约定，供未来场景簇（扩展场景，非 v1 四簇核心 App）按固定契约注入初始化数据、驱动 Passive 流、取证与验证，支撑后续 scaling。
> 状态：设计蓝本，未开始实现。

## 0. 范围与决策记录

### 0.1 范围

- **In**：六款 App（嘟嘟/连汽/星穹/爸爸去哪/网抑云/超维）+ Local Controller 的契约升级；类型化 seed/query、Passive packet 流、mutation journal、episode_scope 隔离、冻结只读、user_memory（默认关闭）；UI 拟真对照与数据全覆盖初始化约束（§5–§6）。
- **Out**：v1 四簇核心 App（Calendar/Mail/Clock/Contacts/Files，属另一工程）；Need 规则、Evidence Tree、Frontier、Search 预算、GT 与评分（Runner 层，App 侧不实现）；KU 环境、启动链、复位编排与数据集版本门控不动。
- **不破坏**：现有全部路由、六款 App UI/包名、25 项测试、APK 构建链、`init_state` 复位语义。

### 0.2 决策记录（已与用户确认）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 六款 App 领域不做 v1 四簇映射，本蓝本只收敛契约 | 六款 App 为后续扩展场景服务；v1 四簇需要的是另一组核心 App |
| D2 | `user_memory` 接口**保留但默认关闭** | 对应 v1 `user_ref=null`；保留接口避免未来 User 路线重做，默认 `feature_off` |
| D3 | 逻辑引用统一采用 `semantic_ref` | backend spec 已落地该命名；AA-ACA 的 `entity_ref` 视为同义，本文不再引入第二个名字 |
| D4 | 现有通用 4 字段注入接口保留，新增类型化 seed/query 并行 | 兼容现有 UI/测试；类型化接口服务 Runner |
| D5 | Patch 支持 `add`/`update`/`delete` + `allowed_changed_fields` 白名单，冻结其行为 | 对齐 AA-ACA §5.2：加已存在、改/删不存在均报错；实际变化集与声明集一致 |
| D6 | 其余全部按 backend spec 8 方法 + envelope + 错误码落地 | 见 §2–§4、§7 |
| D7 | UI 贴近参考 APK：结构一致、视觉近似、功能近似、无第三方商标 | 见 §5；GUI 画面可信是 GUI Track 数据的前提 |
| D8 | App 界面可见的每个数据点都必须经固定接口注入并可读回，无硬编码业务数据 | 见 §6；未来任何场景 = 新 fixture，不改 App 代码 |

## 1. 现状盘点

### 1.1 现有接口（保留不动）

```text
POST /local/v1/instance/init|reset|export        GET /local/v1/instance/status
GET  /runtime/v1/apps/{app_id}/bootstrap          GET /runtime/v1/apps/{app_id}/data/{resource}
POST /runtime/v1/apps/{app_id}/actions            GET /runtime/v1/apps/{app_id}/state
POST /runtime/v1/apps/{app_id}/telemetry          GET /local/v1/apps/{app_id}/events
POST /local/v1/apps/{app_id}/data/{resource}      GET /local/v1/device/status
POST /local/v1/device/screenshot                  GET /local/v1/sqlite/summary
POST /local/v1/clock                               GET /local/v1/faults
POST /local/v1/instance/lifecycle                  GET /health
```

### 1.2 可直接复用的资产

- SQLite 事务、action 幂等、`expected_revision` 乐观锁、domain event 与 telemetry 分离、故障注入、模拟时钟、`CONTAMINATED` 数据集门控（`local-controller/store.py`）。
- reset 编排（KU `init_state` + 六包重装 + Controller 重建 + reverse）与 51 次设备 soak 已验证。
- 泄漏扫描（`scripts/verify.sh` 隔离边界检查）、25 项测试、KU 对齐构建链。
- 注入→GUI→reset 端到端已用星轨"上海南站"验证。

### 1.3 与目标契约的差距（要改的点）

1. 注入是通用 4 字段 `[title, subtitle, meta, price, available, rank]`，无类型化实体、`record_version`、`authority`、`semantic_ref`。
2. 查询只有分页/搜索，无 `operation/selector/projection/as_of` 与 `coverage{scope, complete}` 语义。
3. 无 Passive packet 流（只有 bootstrap 弹窗脚本）；事件表无 cursor 拉取语义。
4. action 日志无 `actor`（runner_seed/agent_gui 等）、before/after 版本、`trace_ref`。
5. 无 episode_scope 命名空间与 teardown；无冻结只读模式。
6. 无 `capabilities`/`restore_baseline`/`read_state`/`user_memory` 接口。

## 2. 目标契约总览

### 2.1 Adapter 方法 → 路由映射

| backend spec 方法 | 新路由（增量，现有路由不动） | 调用方 | 信任边界 |
|---|---|---|---|
| `capabilities()` | `GET /local/v1/apps/{app_id}/capabilities` | Runner | 管理凭据 |
| `restore_baseline(baseline_ref, episode_scope)` | `POST /local/v1/apps/{app_id}/restore_baseline` | Runner | 管理凭据 |
| `seed(fixture, episode_scope, idempotency_key)` | `POST /local/v1/apps/{app_id}/seed` | Runner | 管理凭据 |
| `read_state(scope)` | `POST /local/v1/apps/{app_id}/read_state` | Runner | 管理凭据 |
| `query(operation, selector, projection, as_of)` | `POST /local/v1/apps/{app_id}/query` | Runner 受控只读；Agent 结构化赛道仅暴露此路由 | 受控投影 |
| `drain_events(cursor)` | `GET /local/v1/apps/{app_id}/passive/events?cursor=` | Runner | 受控只读 |
| `read_mutations(since_cursor)` | `GET /local/v1/apps/{app_id}/mutations?since_cursor=` | Runner | 管理凭据 |
| `teardown(episode_scope)` | `POST /local/v1/apps/{app_id}/teardown` | Runner | 管理凭据 |
| （扩展）`advance_time(logical_time)` | `POST /local/v1/instance/advance_time` | Runner | 管理凭据；只推进 fixture 预置外生事件 |
| （冻结）`freeze` / `unfreeze` | `POST /local/v1/instance/freeze` / `unfreeze` | Runner | 管理凭据 |
| （D2）`user_memory` | `POST /local/v1/apps/user_memory/query`（同 envelope） | Runner | 受控投影；feature flag 默认关，返回 `feature_off` |

**信任边界**：管理凭据用 `X-Runner-Key`（实例启动时生成，写入 `instance.json`，不回传 Agent）。Agent 工具集白名单只允许 `query` 与 `drain_events`，且只经 Runner 转发——App 侧不区分角色，由 Runner 卡边界。Loopback 绑定维持现状。

### 2.2 统一 envelope（对齐 backend spec §4）

```json
{
  "status": "ok | not_found | permission_denied | stale | timeout | backend_error | feature_off",
  "source": "starrail",
  "operation": "get_order_details",
  "semantic_ref": "order.trip_20260922_01",
  "record_version": "starrail.v7",
  "observed_at": "2026-09-22T20:05:02+08:00",
  "coverage": {"scope": "single_record | query_scope", "complete": true, "as_of": "2026-09-22T20:05:02+08:00"},
  "data": {}
}
```

- `evidence_id`/`action_id` 由 Runner 分配，App 侧不出。
- `complete=false`、空结果、`not_found`、权限拒绝、超时、失败彼此不同，禁止用 `[]` 混写。
- 失败返回 `error.code/message/retryable`，不伪装为"完整空结果"。

## 3. 数据层改造

### 3.1 类型化实体注册表

新增 `contracts/entity-catalog.json`：每款 App 声明 entity 类型、字段 schema、`query_operations`（granularity、selector_fields、result_fields、coverage 语义）。形如 backend spec 的 `app_manifest.template.json`，六款 App 各一份。示例（星穹）：

```json
{
  "app_id": "starrail",
  "entity_types": [
    {
      "name": "station",
      "fields": ["semantic_ref", "title", "lines", "city"],
      "query_operations": [
        {"name": "search_stations", "granularity": "metadata",
         "selector_fields": ["keyword"], "result_fields": ["semantic_ref", "title", "city"],
         "coverage": "complete_for_query_scope"}
      ]
    },
    {"name": "train", "fields": ["semantic_ref", "train_no", "depart_station", "arrive_station", "depart_at", "arrive_at", "seats"], "query_operations": ["..."]}
  ]
}
```

### 3.2 新表（`local-controller/store.py` 增量）

```sql
CREATE TABLE IF NOT EXISTS typed_entities(
  app_id TEXT, entity_type TEXT, semantic_ref TEXT, episode_scope TEXT,
  record_version TEXT, updated_at TEXT, authority TEXT, data_json TEXT,
  PRIMARY KEY(app_id, entity_type, semantic_ref));
CREATE TABLE IF NOT EXISTS seed_receipts(
  idempotency_key TEXT PRIMARY KEY, episode_scope TEXT, applied_at TEXT, readback_hash TEXT);
CREATE TABLE IF NOT EXISTS mutations(
  mutation_id TEXT PRIMARY KEY, episode_scope TEXT, actor TEXT, app_id TEXT,
  semantic_ref TEXT, operation TEXT, at TEXT,
  before_version TEXT, after_version TEXT, changed_fields TEXT, status TEXT, trace_ref TEXT);
CREATE TABLE IF NOT EXISTS passive_packets(
  sequence INTEGER PRIMARY KEY, packet_id TEXT UNIQUE, app_id TEXT, type TEXT, source TEXT,
  observed_at TEXT, delivered_at TEXT, text_projection TEXT, screenshot_ref TEXT, payload_json TEXT);
```

- `typed_entities` 与现有 `injected_records`/`inventory`/`app_state` 共存：bootstrap/actions/GUI 继续走现有表；seed 写 typed 表，query 从 typed 表投影。GUI 渲染与结构化查询**同源**（同一 SQLite 权威状态）。
- 真实 DB/API ID 只存 Runner 私有 binding；`semantic_ref` 是跨 App 稳定逻辑引用，同名不自动等于同对象（重复姓名允许并存）。
- 时间一律 ISO 8601 带 UTC offset + IANA 时区字段。

### 3.3 seed fixture 与 world delta（对齐 AA-ACA §5.2）

fixture 形如 backend spec 的 `world_fixture.template.json`：

```json
{
  "episode_scope": "scene-xxx-001",
  "common_records": [ {"app_id": "starrail", "entity_type": "station", "semantic_ref": "station.shanghai_hongqiao", "record_version": "starrail.v1", "updated_at": "...", "authority": "railway_official", "data": {...}} ],
  "world_deltas": [
    {"op": "update", "semantic_ref": "station.shanghai_hongqiao",
     "set": {"data.title": "上海南站", "data.lines": "1/3/15"},
     "allowed_changed_fields": ["station.shanghai_hongqiao.title", "station.shanghai_hongqiao.lines"]}
  ]
}
```

- Patch 语义冻结：`add` 已存在 → 报错；`update`/`delete` 不存在 → 报错；实际变化集必须等于 `allowed_changed_fields` 声明集（G1 结构关）。
- `seed` 幂等：同 `idempotency_key` 重复调用返回原收据且不产生重复对象；不同内容复用同 key → `IDEMPOTENCY_KEY_REUSED`（复用现有 action 幂等语义）。
- 收据：`episode_scope`、`idempotency_key`、`applied_at`、每个 `semantic_ref` 的真实 ID 映射（留在 Runner 私有层，App 收据只存 hash）、`readback_hash`。

## 4. 逐方法改造要点

### 4.1 capabilities

返回 App/package、版本、entity 类型、只读操作、可监测 mutation 类型、权限与结果 schema 版本。**公开给 Agent 的描述由 Runner 生成，不能含 world facts**（现有 `/__debug` 不暴露内部字段的原则延续到 envelope）。

### 4.2 restore_baseline / seed / read_state

- `restore_baseline(baseline_ref, episode_scope)`：恢复该 App 基线 + 相关服务状态；重复调用幂等；返回设备/服务版本、baseline hash、清理范围收据。
- `seed`：见 §3.3；只改声明字段，其他字段与背景保持相同；**不能把 `world_id` 写进任何 Agent 可见字段**。
- `read_state(scope)`：从权威存储**重新读取**归一化记录 + 完整性标志，覆盖背景对象以查副作用；不允许把 Runner JSON 回显当独立读回。

### 4.3 query（核心）

- 输入：`operation`（必须在 capabilities 声明）、`selector`、`projection`（必须是声明 result_fields 子集）、`as_of`（可选）。
- 输出：§2.2 envelope；列表类必须给 `coverage{scope, complete}`——只查一页不能证明不存在；完整列表为空可在声明范围内证明不存在。
- 错误语义：未查到关键词/截断/超时/失败 ≠ 空结果；`not_found`/`permission_denied`/`stale`/`timeout`/`backend_error` 各走各的 `error` 路径。
- App 侧不返回 `world_id`/GT/backend_id/未请求正文。

### 4.4 drain_events（Passive 流）

- 现有 bootstrap 十类确定性弹窗继续走老路（App 渲染用）；**新增** packet 流供 Runner 拉取，packet 契约对齐 AA-ACA §6.2：

```json
{
  "packet_id": "p008", "sequence": 8, "type": "notification | gui | user_action | app_event | os_event",
  "source": "starrail", "observed_at": "2026-09-22T20:00:00+08:00",
  "delivered_at": "2026-09-22T20:00:00+08:00",
  "text_projection": "……", "screenshot_ref": null
}
```

- `sequence` 决定稳定投递顺序；`screenshot_ref` 由 Runner 解析为媒体输入，**不把文件名/宿主机路径/World 分支原样给模型**。
- 文本投影只描述可见内容，作者解释、隐藏状态、建议动作不得进 `text_projection`。
- `drain_events(cursor)` 返回 packet 列表 + 下一 cursor；隐藏正文不得偷放进通知 packet。
- 弹窗脚本 → packet 的映射：权限/促销/会员/网络失败 → `notification` 或 `app_event`；用户点击/下单 → `user_action`；截图可见内容 → `gui`（GUI Track 用）。

### 4.5 read_mutations（mutation journal）

- 现有 `actions` 表事务 + domain event 保留；新增 journal 记录（backend spec §3 形态）：

```json
{
  "mutation_id": "m0007", "episode_scope": "scene-xxx-001", "actor": "agent_gui",
  "app_id": "starrail", "semantic_ref": "order.trip_20260922_01",
  "operation": "refund_ticket", "at": "2026-09-22T20:06:10+08:00",
  "before_version": "starrail.v7", "after_version": "starrail.v8",
  "changed_fields": ["status", "refund_amount"], "status": "committed", "trace_ref": "gui-actions/step-0009"
}
```

- `actor` 至少区分 `runner_seed` / `external_world` / `agent_gui` / `runner_teardown`；空列表也声明覆盖完整。
- 真实旧值/新值由私有 state snapshot 或 journal 记录，**不能仅信 Agent 自报操作成功**；最终状态相同也可能曾发生多余操作，不能只靠终态判无副作用。

### 4.6 teardown / advance_time / freeze

- `teardown(episode_scope)`：只删/恢复该 episode 足迹，独立回读与基线差异；恢复失败时该 worker 不进入下一场景（复用现有 `FAILED` 实例语义）。
- `advance_time(logical_time)`：只执行 fixture 预置外生事件，返回应用后版本与被动事件 cursor；当前六 App 无定时业务，先留接口。
- `freeze`：对应 AA-ACA §5.3 冻结语义——拒绝一切业务写入（返回 `SESSION_FROZEN`），不触发后台同步、不因真实耗时使预约过期；允许变化的只有 Ledger/预算/模型状态（Runner 侧）。现有 reset 窗口的 `SESSION_NOT_READY` 写拒绝逻辑可直接复用改造成 freeze。

### 4.7 user_memory（D2：保留、默认关闭）

- 作为 Controller 内 Runner service（非 APK），operations：`search_summary`、`get_episode`（backend spec §5）。
- 走同一 envelope，`app_id="user_memory"`；feature flag `user_memory_enabled=false` 时任何调用返回 `feature_off`，对应 v1 `user_ref=null`。
- 未来 User 路线打开 flag 时，仅需填充 seed 内容与检索实现，接口不变。

## 5. UI 拟真与 GUI 可信设定（D7）

### 5.1 参考 App 映射

| 扩展 App | 参考 APK | 拟真目标 |
|---|---|---|
| 嘟嘟 | `app/滴滴.apk` | 聚合出行：地图叫车区、车型报价、行程 |
| 连汽约车 | `app/曹操出行.apk` | 品质预约：预约计价、优惠、ETA |
| 星穹铁路 | `app/铁路12306.apk` | 官方票务：车站/车次/席别/订单 |
| 爸爸去哪 | `app/携程.apk` | 旅行比价：机酒景点、房型、出行人 |
| 网抑云 | `app/网易云.apk` | 音乐社区：发现/播放/红心/歌单 |
| 超维 | `app/知乎.apk` | 知识社区：文章/目录/标签/收藏 |

### 5.2 拟真原则

- 目标：GUI 截图“可信”，让 GUI Track 画面像真实用户手机；不以像素级复刻为目标。
- 结构一致 > 视觉近似 > 像素对照（分层见 §5.3）。
- 原创素材：图标/启动图/splash 必须原创，无第三方商标、无复刻品牌字（现有六套 SVG 与 launcher 资产保持）。
- 功能近似：参考 App 的核心业务流在扩展 App 中必须端到端 GUI 可操作，不能只有壳。
- 禁止：界面出现 seed/revision/模拟时间/数据来源/“虚拟/仿真”字样（现有静态断言保持并扩展）。
- 拟真与契约冲突时契约优先（同源、可注入、无泄漏先于视觉近似）。

### 5.3 拟真分层（每层有验收）

| 层 | 内容 | 验收 |
|---|---|---|
| T1 信息架构 | 首页直接进业务内容（无统一介绍顶栏，已完成）；底部导航/页面骨架/入口宫格与参考 App 同构 | UIAutomator dump 结构比对 + 首页截图基线 |
| T2 视觉近似 | 主题色、密度、列表项/详情页/表单/弹窗/底部窗口组件形态贴近参考 | 逐页截图对照 + 48dp 触控/安全区/返回键断言（已有） |
| T3 流程等价 | 各领域核心流程 GUI 可操作且产生真实状态变化：叫车→报价→支付→取消；车次→乘客表单→下单→退改；搜索→播放→红心→歌单；文章→收藏→笔记等 | 实机走流程 + mutation journal 佐证 |
| T4 逐页对照 | 参考 APK 通过隐私/权限首启后可进入的页面逐一对照 | 参考包与扩展包截图并排记录（首启流程被阻挡的部分见 §12） |

每款 App 新增一份 `apps/{app_id}/ui-fidelity-checklist.md`：记录 T1–T4 逐项状态、对照截图路径与差异说明；新增页面/组件必须同步更新该清单。

### 5.4 GUI 与权威状态同源

- 界面渲染的每个数据点必须来自 Controller 权威状态（seed 注入 → 同一 SQLite → GUI 渲染 → query 读回），不允许 App 内硬编码业务数据充当“看起来真实”的内容——同时是 §6 的数据全覆盖要求。
- GUI Track 的 `gui` 类型 packet 必须携带真实截图，截图内容 = 该时刻 seed 出的可见状态；`text_projection` 只描述截图实际可见信息（对齐 AA-ACA §6.3）。
- 离线降级数据 = 与在线同一数据集的 APK 内置只读副本（已实现），不引入第二套不可注入数据源。

## 6. 数据全覆盖初始化设定（D8）

### 6.1 核心原则

**App 界面可见的每一个数据点，都必须能通过固定接口注入并能读回；不存在硬编码业务数据。** 未来任何场景 = 新 fixture 文件，不改 App 代码。这是 scaling 的前提：场景簇只关心“注入什么”，不关心 App 怎么实现。

### 6.2 数据四分类与初始化要求

| 分类 | 定义 | 初始化要求 |
|---|---|---|
| 静态 UI 文案 | 按钮、标签、空态文案、导航名等界面骨架 | 不注入；在 entity-catalog 的 `static_chrome` 白名单声明，测试防漂移 |
| 展示数据 | 列表/详情/标签/价格/库存/图片等业务内容 | 必须经 `seed` 注入（typed entity），`query` 可投影读回 |
| 场景状态 | 弹窗脚本/促销/权限/故障/时钟/会员等驱动性状态 | 必须可注入可覆盖：bootstrap 弹窗脚本纳入 seed 可写层（现有十类确定性场景为默认 fixture），故障/时钟走既有接口 |
| 用户状态 | 订单/收藏/歌单/历史/库存变化等交互产物 | 必须可注入（预置状态）且 `read_state`/`read_mutations` 可读回 |

### 6.3 完整性约束

1. **字段并集**：entity-catalog 的字段 = GUI 可见字段并集（含 subtitle/meta/标签/时间线等次级字段），不允许“UI 显示了但 catalog 没有”的字段。
2. **溯源表**：每款 App 一份 `apps/{app_id}/ui-data-provenance.json`：`{ui_location → semantic_ref/字段 → 注入接口 → 读回接口}`。新增 UI 数据点必须同时更新 catalog 与溯源表。
3. **覆盖断言**：新增测试遍历每款 App 渲染的数据点，验证全部可溯源或命中 `static_chrome` 白名单；未登记即失败（进 §10 测试计划）。
4. **注入一致性**：四类数据走同一 seed 接口（`POST /local/v1/apps/{app_id}/seed`），不接受多套并行注入协议；`world_deltas` 只改声明字段。
5. **复位完整性**：reset/teardown 后注入数据必须全部消失（“上海南站→上海虹桥”已验证，推广到四类数据，每类一个实机用例）。
6. **降级数据同源**：Controller 不可达时的 APK 内置降级数据 = 同一数据集的只读副本（已实现），不得引入第二套不可注入数据源。

## 7. 泄漏与可见性约束（对齐 AA-ACA §8.2 + backend spec §4）

白名单外禁止进入 Agent 视图：

- `world_id`/`episode_scope`（管理面用）以外的私有标记、`idempotency_key`、真实 DB ID、`seed`/`revision` 字样、数据集版本、模拟时间、研究用途字样（GUI 已清理，envelope 再验一遍）。
- 宿主机绝对路径、verifier 私有路径、`snapshot_hash`、Oracle 字段。
- 文件/附件返回稳定 URI 或内容句柄 + MIME/大小/SHA-256/版本。
- 静态回归扩展：`scripts/verify.sh` 隔离扫描新增 envelope 字段白名单断言；六款 App 模板禁止出现 `<header>` 等既有断言保持。

## 8. 兼容与迁移

- **增量路由**：§2.1 全部为新增，现有路由、App UI、bootstrap/actions 语义零改动；升级后 25 项现有测试必须原样通过。
- 数据集版本门控、`CONTAMINATED`、reset 编排不动；seed 的 episode 数据不进入只读数据集（seed 走实例状态层）。
- 契约版本：bootstrap 的 `contract_version` 机制沿用，新增 `adapter_contract_version` 字段供 Runner 兼容判断。

## 9. 阶段划分与验收

| 阶段 | 交付 | 验收（对应 backend spec §7 + AA-ACA 检查单） |
|---|---|---|
| P0 | §2.2 envelope + §3.1 entity-catalog.json 冻结；`X-Runner-Key` 路由分组；六份 `ui-fidelity-checklist.md` 与 `ui-data-provenance.json` 骨架 | 六款 App catalog 字段/操作齐全且 = GUI 可见字段并集；错误码集合冻结；`static_chrome` 白名单确定 |
| P1 | typed_entities 表 + seed/query/read_state/capabilities 落地；seed 覆盖四类数据（展示/场景/用户/降级） | 同一 fixture 两次 seed 无重复对象；readback 与 Android UI 一致；projection/coverage/错误区分符合契约；无硬编码业务数据残留 |
| P2 | mutations 表 + read_mutations；passive_packets + drain_events；弹窗→packet 映射（含 `gui` 类型真实截图引用） | 一个正确操作、一个错对象操作、一次重复、一次无操作可被终态+journal 区分；packet sequence/cursor 稳定 |
| P3 | episode_scope 隔离 + teardown；freeze/unfreeze；user_memory 默认关闭接口；advance_time 留接口 | teardown 后本地/背景状态恢复且四类注入数据全部消失；冻结期写入被拒且无后台变化；user_memory 返回 `feature_off` |
| P4 | 全量回归 + KU AVD 实机；参考 APK 逐页对照基线（T1–T3，T4 按可用程度） | 25 项旧测试 + 新增契约/溯源测试全过；六 Vite/Uni-app 构建 + APK 校验过；六款 App 各完成一次 注入→GUI 可见→readback→journal→teardown 实机闭环；50 次设备 soak 重跑 |

## 10. 测试计划（新增）

1. seed 幂等：双 seed 不重复；同 key 异内容 → `IDEMPOTENCY_KEY_REUSED`。
2. readback vs query 同源：seed 后 `read_state` 归一化记录与 `query` 投影字段一致。
3. coverage 语义：列表 `complete=false` 不证明不存在；空列表 + `complete=true` 证明不存在；`not_found`/`permission_denied`/`timeout` 互不混写。
4. journal actor：`runner_seed` 注入与 `agent_gui` 操作在 `read_mutations` 中可区分；before/after 版本正确。
5. drain_events：cursor 续拉不丢不重；packet 字段齐全；`text_projection` 无私有标记。
6. freeze：冻结期 `actions`/`seed` 被拒（`SESSION_FROZEN`），读仍可用。
7. teardown 隔离：两个 episode_scope 交叠数据，teardown 一个不影响另一个；恢复失败实例进 `FAILED`。
8. user_memory：默认 `feature_off`；打开 flag 后 envelope 正常。
9. 泄漏扫描：新 envelope 字段白名单；六 App release 产物无 `/__debug`（既有断言保持）。
10. 回归：现有 25 项 + 六套构建 + `verify-apks.sh` 全过；星轨“上海南站”注入实验在 typed seed 路径复验（六款各一例）。
11. 数据溯源完整性：遍历每款 App 渲染的数据点，验证全部可溯源到 typed entity 或命中 `static_chrome` 白名单；新增 UI 数据点未登记 → 测试失败。
12. UI 拟真回归：参考 APK 结构对照基线（IA/导航/流程骨架）；六款首页/详情/表单/确认弹窗截图抽检无内部标记、无商标复刻、与 seed 状态一致。

## 11. 与未来场景簇的对接约定

- 本蓝本完成后，App 侧就绪的只有：类型化 seed、同源 query、passive packet、journal、teardown、冻结。**Need 规则、Evidence Tree、Frontier、Search 预算、GT、评分全部留在 Runner**（AA-ACA §7/§9）。
- 未来在六款 App 上建场景簇时，Runner 只需引用 `entity-catalog.json` 生成 Evidence Tree 端点与投影，用 `seed` 注入 `common_records + world_deltas`，用 `drain_events` 组织 Passive 流，用 `query` 提供取证，用 `read_mutations` 验证 GUI 执行——App 侧不再改动，即达到"按固定契约注入初始化数据、支撑 scaling"的目标。
- 资产目录引用 AA-ACA §11.1 的 `benchmark_v1/` 结构；App 侧不实现该结构，只在其 `capabilities` 中声明 `adapter_contract_version` 供 Runner 校验。

## 12. 未决项（实现前确认）

1. `X-Runner-Key` 是否需要在多 Runner 场景下改为每实例多 key（当前假设单 Runner，loopback 内可后置）。
2. 六款 App 中哪些场景需要 `add`/`delete` patch（当前假设 `update` 为主，退票/取消走状态字段而非删实体）。
3. `user_memory` 的 `get_episode` 数据源暂为空（接口保留，内容等 User 路线）。
4. 参考 APK 部分页面因隐私协议/通知权限首启流程被阻挡，T4 逐页对照基线需在可进入业务页后补齐（read.md 已有此下一步计划，此处列为持续任务）。

## 13. 2026-09-23 截图反馈后的契约增量

- 六款增加 `account_field(key,value)` 的 seed、`list_account_fields`、read_state、teardown 与 GUI 保存动作，用于各自账户选项的可填写状态。
- 四款订单应用的 `order` 增加语义标题、说明、原价、抵扣、优惠名称、支付明细，增加 `get_order_details`；优惠规则影响未支付订单实付额，支付明细记录该金额。
- 音乐 `play_history` 增加 `playing`，GUI 支持单曲红心及暂停/继续。默认 `popup_queue` 为空，弹窗仅由显式 seed 或触发动作产生。
- 新字段已加入六份 catalog 与溯源表；实机证据和最终验收状态以 read.md 最新阶段记录为准。

## 14. 2026-09-23 二轮真实交互升级（本轮执行依据）

### 14.1 用户可见流程

1. **独立支付与订单详情**：交通和旅行订单从列表进入独立详情页，显示行程/商品、联系人或出行人、原价、优惠、应付、支付方式及历史明细；详情确认后进入支付页。支付页选择微信或支付宝、选择适用优惠券，输入六位假密码才能提交；假密码只作本地交互验证，不接入真实支付、不写入明文状态或日志。支付方式、券后金额和时间写入订单支付记录。旅游预订可选常用出行人或新填姓名与手机号；打车填写/确认起终点；铁路填乘车人并确认车次席次。
2. **按顺序选择**：嘟嘟/连汽先起点和终点，再进入对应服务独立页选择车型，确认后生成订单。星穹先起终站和日期，再看车次，选车次后看席次，最后确认乘车人与订单。搜索/查询结果是独立页面，不与首页全部资源并列展示。打车成功后首页醒目显示当前车牌号、车型、司机与预计到达分钟。
   - **2026-09-23 用户补充**：连汽的即时、预约、接送机、商务各自提供多个车型及价格，不以单个“即时用车”条目代替车型选择；四类入口分别过滤自己的车型。
   - **2026-09-23 用户补充**：支付页的优惠券是可选列表，点开后可逐张选择、换券或选择“不使用优惠券”；订单详情和应付金额立即反映实际选择。不可把优惠处理成简单的使用/不使用二选一。
3. **音乐内容层级**：单曲详情提供播放、红心和“收藏到歌单”，并在现有歌单中选择；创建歌单只保留在歌单列表。歌单卡片进入歌单详情，展示其中多首可单独播放的歌曲，另提供“播放全部”。“播客”入口及内容文案按实际资源修正，不把音乐歌单误称为播客。
4. **功能独立页与有效入口**：首页保留需要实现的服务图标；点打车、出租车、车票、酒店等进入各自有标题、返回、筛选/输入和下一步按钮的页面，服务资源须按类型过滤，不能只弹通用列表或把所有资源同时列在首页。没有可执行流程的图标应收起，不做空壳入口。
5. **超维阅读**：文章、话题卡片点入独立详情页，显示标题、正文/简介和相关内容；收藏是详情中的独立动作，返回后保持当前位置。

### 14.2 数据与契约

- 扩充订单快照：起终点/车次席次/服务类型、车牌司机 ETA、出行人姓名手机号、支付方式和已用券；字段可 seed、query/read_state、teardown。既有订单与 `get_order_details` 保持兼容，新增字段可缺省。
- 订单状态转换由 Controller 校验：`CONFIRMED` 才可支付，优惠券金额按当前订单重算，换券或取消用券恢复同一订单原价后重新计算；`checkout_pay` 接收微信/支付宝与六位假密码的有效性证明，不留存密码；付款后付款记录不可重复追加。手机号只保存在对应订单/常用出行人状态，界面使用语义标签。
- 歌单与阅读详情直接使用现有类型化歌曲/文章/话题资源及用户歌单状态；新增动作应进入 mutation journal，界面数据纳入六份溯源表。

### 14.3 阶段与验收

| 阶段 | 实施 | 验收 |
|---|---|---|
| A | 核对 `相关文件` 中五段参考视频、当前页面与现有资源字段；记录差异 | read.md 有视频观察、现状和剩余工作 |
| B | 独立服务页、顺序选择和阅读/歌单详情 | 六款相关入口真实点击进入对应详情；起终点→车型、站点→车次→席次顺序可回退 |
| C | 订单详情、联系人/出行人、券与假密码支付、打车进度 | 支付前后金额及支付方式一致；错误密码/缺出行人不得付款；首页车牌 ETA 可见 |
| D | 全量回归、六包重建、KU Docker AVD 装机点击和截图、read.md 最终状态 | 自动化测试及六 APK 校验通过；逐应用 GUI 流程和新增字段 seed→GUI→query/read_state→teardown 留证；测试数据清除后 READY |

### 14.4 本轮验收结果（2026-09-23）

- A–D 已执行。六款 APK 已装入 `ku_simulator_extension_acceptance_0`；`scripts/verify.sh` 89 项测试与六包校验通过。
- 六款真实点击主流程：`runtime/instances/acceptance/second-round-gui-report.json`；连汽四类三车型及换券/不用券：`coupon-choice-gui-report.json`；常用出行人姓名手机号：`common-traveler-gui-report.json`。
- 本版可见字段注入→WebView→query/read_state→teardown：`current-provenance-report.json` 与六份 `provenance/{app}-current.json`，六款均通过，412 CSS px 无横向溢出。
- 最终 reset 后六包仍已安装，六款 revision=0 且订单/播放历史为空。参考 APK 无法进入的内页继续按 T4 边界记录，不把它当作逐像素验收证据。

## 15. 2026-09-23 三轮页面与状态修正

1. 超维话题进入独立话题页，列出属于该话题的多篇可点开的条目；条目再进入正文。话题与文章的从属关系由可注入、可查询的数据字段提供。
2. 首页保留未实现功能的图标作为静态贴图，实际支持的入口仍进入对应业务流程。嘟嘟恢复以常用地址和地点数据为主的推荐目的地；打车不提供收藏。取消行程后，订单列表与详情明确显示“已取消”。
3. 个人主页卡片与每个统计数字分别进入对应账户、订单、优惠券、收藏或歌单页面，不共用订单页；优惠券与收藏有各自独立的视图。
4. 铁路重新查询车次时清除上次选择；只在选定车次并进入席次页后显示“已选车次”。
5. 音乐明确区分歌单与单曲：歌单卡片总是进入含多首单曲的歌单页，支持选单曲或播放全部；仅单曲能红心和收藏到用户歌单，歌单不能作为歌曲收藏到歌单。
6. 每完成数据、页面、构建、KU 设备点击与复位阶段均更新 `read.md`。验收以同一最终六包的真实 WebView 点击、权威状态、query/read_state、截图和无横向溢出为准。

### 15.1 本轮最终验收（2026-09-23）

- 数据、页面、契约和离线资源已完成：话题到文章、歌单到单曲均由显式 ID 关联；未实现功能图标保持静态；订单取消、账户独立视图及车次状态均按上述要求工作。
- 最终六包已装入 KU Docker AVD。`scripts/verify.sh` 通过 90 项测试、共享 Vue 模板编译、六款 Vite/Uni-app 构建与 APK 校验；`verify-third-round-gui.py` 十项设备点击和 `verify-second-round-gui.py` 六款主流程全通过。
- `verify-current-provenance.py` 对最终六包逐款验证 GUI、query/read_state 和 412 CSS px 无横向溢出。最后复位后实例为 `READY`，六款 revision=0、订单和播放历史为空。证据路径及每阶段记录见 `read.md`。

## 16. 2026-09-23 四轮订单与业务详情修正

1. 嘟嘟选完车型的主按钮用“确认并呼叫”；首页增加可同时查看打车与出租车车型的“查询车辆”入口，原有两个专属入口继续可用。连汽首页不再预先展示服务车型，只在查询结果选择车型。
2. 嘟嘟、连汽与旅行支付页均能打开可用优惠券窗口，逐张选择或不使用；账户优惠券页可正常加载。优惠券按订单原价重算并在支付前展示。修复缺少优惠券资源导致的加载报错。
3. 铁路及旅行的出行人姓名和手机号放在独立订单确认页填写，可选常用出行人；确认并保存后才开放支付。支付页只显示支付方式、优惠券（适用应用）与假密码。
4. 取消、退票和改签先选择具体订单。改签订单再依次选择新车次和席次，Controller 根据新旧席次原始票价计算差额并写入订单；退票只处理选中的订单。
5. 车票查询进入所选车次的详情，明确展示车次、出发与到达站、发到时间和票价，之后再选择席次。订票及订单详情沿用相同数据。
6. 旅行景点结果进入景点介绍页，展示活动/特色后再确认预订。超维删除重复的关注话题导航，只保留账户中的关注话题入口。
7. 每个阶段继续更新 `read.md`；最终重新生成六包，在 KU Docker AVD 实际点击支付券、出行人、改签退票、车次景点详情和各首页入口，并复位验收。

## 17. 2026-09-23 五轮截图反馈

1. 优惠券选择按钮至少 48px 高、112px 宽；长券列表可滚动。
2. 网抑云静态服务图标与交互服务图标均保持独立圆形，不将红底施加到图标和文字的外层容器。
3. 超维移除记录页“切换收藏”占位入口，保留文章详情的收藏操作；连汽移除“开始导航”占位入口，改为选择订单取消预约。
4. 连汽允许取消未支付或已支付预约；已支付取消记负向模拟退款流水且不可重复取消。嘟嘟不显示取消入口，Controller 也拒绝该操作。重新生成契约与六个 APK 后在 KU 实机点击并复位。
5. 连汽支付页明确展示预约车型、预约时间、起终点及付款后取消规则；预约时间进入订单快照，不能只复用即时打车金额页。
