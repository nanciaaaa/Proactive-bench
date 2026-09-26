# AA-ACA：文本 Passive 到真实 GUI 采集的后端扩充蓝图

> 日期：2026-09-24　状态：设计与实施蓝图，未实现、未完成设备验收。  
> 修订 2026-09-25：新增 §5.1 实施优先级，明确 §7 是**发布门槛而非生产步骤**；解除离线 Passive 流下的背压/录制队列要求及环境参数的预先锁定要求；B3a 降为 P2。旧 P/A Track 标签已由下述现行 GT 契约取代。  
> 对齐说明：下文以 `../相关文件/ProactiveBench/ProactiveBench/` 当前开发版的 `benchmark.json`、`code/runtime.py`、`code/README.md`、`code/GT_FORMAT.md` 和根级 `prompts/agent_contract.json` 为**运行与任务设置的准绳**；旧正式方案仅作采集背景，不覆盖当前协议。现有 17 条是开发 Episode，尚非 Gold。本文未声称已完成 GUI 设备采集或 image 模式模型运行。  
> 依据：[Benchmark v1 正式方案](./AA-ACA_Benchmark_v1_原子场景簇与冻结世界数据构建正式方案_20260922.md)、[App 后端接口规范](./mobileworld_app_backend_spec/README.md)、[KU extension 契约](../contract-upgrade-20260926/CONTRACT_UPGRADE_BLUEPRINT.md)。  
> 本文聚焦**可复用的 Passive GUI 数据生产流程**，适用于已登记场景及后续新增场景、KU 原生 App 和 extension App。当前协议为 `streaming_round_auto_advance_v2`：先填满首个 Passive 滑窗，逐轮可 `search` / `wait`（均推进一条 Passive），EOF 后可继续 `search` 或 `final`；查询上限 6 次，按查询发生时刻读取声明式 World 历史，最终输出单条动作 Need 或拒绝结果及证据。本文的 GUI 采集不是 GUI 主动搜索或 GUI 执行评测；未来如需动态外部世界，另行版本化。

### 与当前 ProactiveBench 的对齐判定

| 原蓝图/旧正式方案口径 | 当前任务设置 | 本文处理 |
|---|---|---|
| EOF 后才在静态 W* 上 Search、最终 Need Set | 流中即可查证；`search`/`wait` 自动前进，EOF 仍可查；按 `current_time` 回放 World 历史；最终单条 `need/evidence` | 将冻结视为采集时的隔离/最终状态核验，不限制模型何时查询；不用 Need Set |
| `type/sequence/text_projection/screenshot_ref` 作为运行时公开包 | 流中使用 `tag/text_event`；summary 对所有 tag 仅公开 `packet_id/text_event`，image 对 GUI 公开 ID 加图像，非 GUI 仍为文本 | 老字段仅可留在作者侧采集收据；输出必须编译为现行 `stream.jsonl`，不扩模型公开字段 |
| GUI Track 全部观察为图、缺图即可删除非 GUI 行 | image 模式仅要求 **GUI tag** 全部有图；非 GUI 仍为文本；`run.py` 当前只用 summary | 称为“GUI 图像 + 非 GUI 文本”输入，不伪称全图；纯图仅作为未来新协议，不删语义必要的事件 |
| P/A 标签按 Track 重算、同流两 World 必须相反结论 | GT 只有 `need/evidence_sets`，正例/contradicted/NoNeed；17 条开发数据，配对合法性由场景 compatibility 决定 | 图片可见性变更须重审 GT 证据组与动作语义；不添加 P/A 字段或强制所有配对结论相反 |
| 单帧机器审过即可宣称 GUI Gold | 当前 `validate` 只验格式/绑定，语义裁决另行导入；现有发布器仍需人工签署 | 采集 QA 收据不等于任务 Gold，发布需同时完成 GUI、场景语义和评测链验收 |

## 1. 结论与边界

**可以规定文本 Passive 行格式，由 Runner 分发到 App 适配器、准备界面并截图；但文本只能作为有来源的场景规格与可见内容预期，不能单凭一句自然语言可靠生成真实 GUI。** 每种可采集观察还需绑定：权威业务状态、可显示它的 App/系统表面、定位到具体页面的导航方法、可见性断言、采集时刻和恢复方法。Runner 编排这些步骤，Controller/适配器只实现设备和 App 能力，不能由 Controller 生成 GT 或把文本直接画成假截图。

建议先完成一个真实闭环：**固定一条已登记 Episode，至少两条可采集的 GUI tag，其中一条在 App 详情页；按该簇合法的 compatibility 选共享 Passive 的 World 对照，核验公共可见流与各自历史/readback。** 对照可以覆盖不同结果，但不强迫每组相反；通过后再扩场景簇和 App。不要先建通用“任意文字生成任意页面”引擎。

当前应称为 **ProactiveBench image 模式的 GUI 图像 + 非 GUI 文本 Passive**：GUI tag 的模型可见内容来自 Android 真正渲染的截图，不能同时投递作者 `text_event` 捷径；其他 tag 仍投递 `text_event`。`text_event` 保留在源流供 summary 模式使用，但 GUI image 模式不公开它。不能为了“纯 GUI”删掉任务所需的通知或状态事件，也不能将后台事件伪造成可见 GUI；全图协议须另行设计、变更数据和评分后才能声称支持。

一条 Passive 对应**一个观察点**；当前运行时每个 GUI packet 最多绑定一张 `gui/<packet_id>.png`，不是多帧协议。列表/详情或滚动后的不同观察须编成不同 packet，保持现有 ID、顺序及证据引用；单张首屏不能声称观察全文。采集是离线生产过程，模型评测只读取绑定哈希的图片，不随模型运行重新截图。

**单帧 GUI 采集 QA 不以人工目测代替机器收据；这不排除 ProactiveBench 的独立动作语义裁决和场景业务审核。** 每个待发布的像素断言都须有可执行检查和证据；无法确认时拒收该图。语义裁决尚未完成时指标为待评，不能把机器截图验收等同于整条 Episode 的 Gold。

## 2. 本地工程现状与真实缺口

以下为 2026-09-24 对本地源码的核查；已运行 extension 的 41 项相关后端测试，未重新启动 KU 设备验收。

| 位置 | 已有能力 | 对本目标仍缺什么 |
|---|---|---|
| `KnowU-Bench/src/knowu_bench/core/server.py`、`runtime/controller.py` | `/task/init`、`/screenshot`、`/step`；ADB `screencap` | `/task/init` 面向 KU 预定义任务，不接受任意 Passive 行与页面定位契约；截图只证明当前屏幕有图，不证明对应 packet/实体/时间正确 |
| `simulator-extension/scripts/reset-instance.sh` | KU `init_state` 恢复、六 APK 重装、Controller 重建、ADB reverse 与 APK 哈希核对 | 尚无面向采集任务的按 Episode 串行调度、状态收据与复位后污染检查 |
| `simulator-extension/local-controller/server.py`、`store.py` | 六款扩展 App 的 `seed/read_state/query/teardown/freeze/advance_time`、passive packet 表、`/local/v1/device/screenshot`、App 启停 | 未实现“指定 packet → 指定页面 → 等待可见 → 采集 → 关联 packet”的通用闭环。现有截图接口额外生成 `text_projection="当前屏幕截图"` 的新 packet，不能直接当原 packet 的可靠图像版本；截图名按毫秒生成，需防冲突和采集关联 |
| `simulator-extension/scripts/p4-closed-loop.py` 等验收脚本 | 已有六 App seed→真实 GUI→readback→截图→teardown 的设备证据 | 以固定 App、路径、端口和 `sleep(6)` 的专项脚本为主，尚不是数据工厂；人工/脚本截图没有流 ID、World/Track/版本一体的产物契约 |
| `相关文件/simple_evidence_access_tree_clustered_v2/semantic_runner.py` | 物化语义 Episode、Frontier、Ledger 与 Need 验证 | 不负责 Android 初始化/画面采集；它的 JSON world 不能自动当作真实 App 数据库状态 |
| `simulator-extension/_archive/contract-upgrade-20260926/CONTRACT_UPGRADE_BLUEPRINT.md` §0 | 明确六款扩展 App 可服务后续场景；首批样例簇涉及的 Calendar/Mail/Clock/Contacts/Files 属另一接入路径 | **App 能力按场景逐项验收。** 六款 extension 的类型化 seed 验收不能替代其他 App 的 GUI 验收；新增场景也可直接复用六款已验证的能力 |

本次源码调整后，extension 的 `seed`/`read_state`/`query`、突变日志与 Passive 拉取更接近既有 Adapter 契约：`record_version` 对外为 `app.vN` 字符串，`drain_events` 返回 App 来源与 `sequence`，查询参数分页/投影校验和管理写入的 Runner key 校验已有回归覆盖。**这些是可复用基础，不需要为 GUI 采集再造一套同功能接口。** 仍需由 Capture Runner 负责跨 App 顺序、图像绑定与端到端复位；当前 `notification` packet 依然不等于 Android 已显示系统通知。

还有一个实现细节影响时间语义：现有 extension `advance_time()` 留有接口，但不自动生成预置外生业务事件；`freeze` 约束的是 Controller 状态，不能自动保证 Android 原生 App、系统通知、其他后台服务不变化。GUI 采集必须单独验证这些表面的时间与写入行为。

当前 `gui_capture.py` 产出 `pending_review`，`gui_publish.py` 仍要求逐帧 `review_status=approved`、`reviewer/reviewed_at` 等人工审核字段；这与拟议的机器截图 QA 不一致。后续发布流程须验证机器截图收据；这不替代 ProactiveBench 动作语义裁决。在此之前不能把现有流程称为通过 GUI 验收，更不能称任务 Gold。本蓝图修订不表示工程已完成改造。

### 2.1 KU 原生 App 与 extension App 的并列接入

两类 App 运行在同一 KU Android 设备上，**共用 Episode 编排、ADB 截图、Packet 关联、QA 和发布格式**；差别是业务状态由谁持有、用什么适配器注入/回读。一个 Passive 流可以跨两类 App，Runner 按每行 `app_id/surface` 派发，但同一 Episode 的全部 App 必须在同一次基线恢复后初始化。

| 环节 | KU 原生/预装 App | 六款 extension App | Runner 的统一检查 |
|---|---|---|---|
| 恢复 | KU `init_state` 快照；另清理 App/系统残留 | 同一 KU 快照，加 extension SQLite 重建、APK/reverse 校验 | 设备与相关后台都回到同一基线 |
| 初始化 | 复用 `KnowU-Bench/runtime/setup` 的 Calendar、Clock、Contacts、Files、Messages 等 helper 或经验证的 App 适配器；逐项补参数化、幂等及回读 | 使用 local Controller 的 `seed/restore_baseline` 和类型化记录 | 对照 World 资产核验实际注入值与实体身份 |
| 页面呈现 | KU `AndroidController` 的 ADB 操作或 App 专用确定性导航；需要新增 `view_id` 就绪断言 | Controller `process`/现有 WebView 导航，补指定实体的 `view_id` 就绪断言 | 确认前台 App、目标实体及像素可见字段 |
| 截图 | KU `/screenshot` 或直接 ADB `screencap` | extension `/device/screenshot` 或同一 ADB 截图 | 只保留一个原始 PNG 采集路径并绑定原 packet；避免两套接口各自产生不一致的事件 |
| 独立回读 | App 自身数据库/ContentProvider/已验证 helper；按 App 补覆盖范围与版本 | local Controller `read_state/query/mutations` | GUI 与权威状态一致，导航未污染业务事实 |
| 冻结与复位 | 需逐 App 验证系统时间、通知、后台同步及快照恢复 | Controller `freeze/teardown` 加 KU 快照；核查其管不到的系统表面 | 历史与决策点状态可核验，采集不引入漂移；下一 Episode 无残留 |

KU `/task/init` 会按预定义 Task 加载快照并执行任务初始化，**不能对每条 Passive 行调用**，否则会把前面观察的设备状态清掉。采集 Runner 应在 Episode 开始时恢复一次基线，再调用所需的 KU setup helper/适配器与 extension seed；现有 KU Task 仅在初始化与该 Episode 起始 World 完全一致时直接复用。任一 App 缺少可验证注入或画面能力，该 GUI packet 就不能进入 image 输入。

### 2.2 App 内对话框现状（不等于顶部消息横幅）

extension 六款 App **已有 App 内弹窗能力**：`POST /local/v1/apps/{app}/seed` 注入 `entity_type="popup"`，会设置 `popup_queue` 并生成 `app_event` packet；App 的 `load()` 在启动时读取队列，页面以 `.confirm.popup` 显示标题和正文。另有 `POST /runtime/v1/apps/{app}/actions`、`type="trigger_popup"`，可传 `scenario_id/kind/title/message`，写入队列；`dismiss_popup` 关闭。这里的两条路径都操作同一 extension 状态，故可作为 **App 内弹窗** 的数据来源。

但它们还不等于“Runner 随时调用，当前屏幕立即弹出”的稳定采集接口：WebView 当前只在 `load()` 或由页面自身 `act()` 调用动作后更新本地 `popup`，Runner 单独 POST `trigger_popup` 不会主动推送到已打开的页面；可通过重启/重载 App 后读取队列显示，若要求不重启的逐行即时弹出，则需补 App 状态订阅/显式刷新及就绪确认。当前 `trigger_popup` 的 mutation actor 记为 `agent_gui`，Runner 用它触发被动弹窗会误记为 Agent 行为；采集应先用预置 `seed`，需要流中触发时补 `runner_event` 身份与 packet 关联。它显示的是 App 内对话框，并非 Android 系统通知或原生权限弹窗。

KU 原生侧没有与六款 extension 通用的“任意 App 弹窗激活”入口。已见 KU Controller 有 `simulate_sms` 和 `/sms`，部分 Task 可引发真实短信通知；其他弹窗取决于具体 App 和触发条件，须逐 App 验证，不能用 extension 的对话框冒充其来源。

### 2.3 顶部消息横幅（Android heads-up notification）

用户提供的参考图是**其他界面上方由 Android System UI 展示的应用通知横幅**，并非 `.confirm.popup`。对这一类 Passive，当前工程的结论更严格：**六款 extension App 尚无已实现的“按来源 App 发布系统通知并立即采集横幅”接口**。源码中 `passive_packets.type="notification"` 只是 Controller 内的语义记录；`trigger_popup`/`seed popup` 只显示 App 内对话框。六款 APK 未见 `POST_NOTIFICATIONS` 声明、通知渠道或调用 Android `NotificationManager` 的实现。KU 的 `/sms` 可注入 SMS，但横幅由系统短信应用和设备设置决定，不能保证它代表任意指定 App，也不能保证必然以横幅方式出现。

对此应在每个需要横幅的来源 App 中实现最小的**原生系统通知发布能力**，由 Runner 私有接口请求 `source_app + notification_id + channel + title/body + logical_time + episode_id/packet_id`；App 在真实 Android 设备上发布通知，Runner 核对前台处于指定背景页/桌面、通知来源包名、权限/渠道状态、横幅实际出现，再在短暂显示窗口内截图。Android 13+ 的通知权限、Android 8+ 的渠道重要性及设备免打扰/通知开关要在采集基线固定并验证；高重要性是出现横幅的必要配置之一，**最终是否以横幅呈现仍以真实屏幕验收为准**。不要由 Runner 绘制仿通知悬浮层，也不要把 `app_event` 文本 packet 改名成已采集系统通知。

横幅容易几秒后消失，因此需要“触发 → 检测 System UI 横幅/指定文案 → 立即原始截图 → 保存通知记录与 PNG 哈希 → ACK packet”的单行握手；未捕获横幅就重置该 Episode 重试，不能退而截图通知栏后仍标为顶部横幅。通知栏截图可作为另一个明确的观察类型。多个通知连续到达时须规定排序与互相遮挡，采后取消/清理，避免污染下一 packet。KU 原生 App 也按相同验收标准逐 App 核实；能否调用其真实通知入口取决于对应 App，不能用 extension 包名代替。

## 3. 最小可落地的数据契约

### 3.1 作者侧 Packet 与采集指令分开

以 ProactiveBench `passive_inputs/<passive_id>/stream.jsonl` 为运行输入：源包包含 `packet_id, sequence, tag, source, observed_at, delivered_at, text_event`；可选图片字段为成对的 `screenshot_ref/screenshot_sha256`，仅供宿主按当前 Passive 的 `gui/<packet_id>.png` 取图。模型摘要模式只见 `packet_id/text_event`；图像模式 GUI 只见 `packet_id` 与原图，非 GUI 仍见 `packet_id/text_event`。在**作者私有**采集计划中另加 `capture`，不把导航、实体绑定、World ID 或 QA 预期传给模型。

```json
{
  "packet_id": "p008",
  "sequence": 8,
  "tag": "gui",
  "source": "calendar",
  "observed_at": "2026-09-22T20:00:00+08:00",
  "delivered_at": "2026-09-22T20:00:00+08:00",
  "text_event": "日历详情显示：项目预约，9月23日14:00至14:30。",
  "capture": {
    "surface": "app_screen",
    "app_id": "calendar",
    "view_id": "event_detail",
    "subject_ref": "booking_01",
    "visible_assertions": ["title", "start_at", "end_at"]
  }
}
```

此例是**作者侧输入**，不是可直接送入 `Session` 的一行：编译时剥离 `capture`，只将校验后的公开源字段与图片路径/哈希写回源流。`view_id` 是适配器可达页面名，`subject_ref` 是私有逻辑引用；`visible_assertions` 要求像素可读，不能仅凭 readback/UI 层级。截图必须绑定当前 Passive 下固定的 `gui/<packet_id>.png`，路径与 SHA-256 成对出现；已有图不静默覆盖。Schema/能力表仅约束作者采集，不另立模型协议。

采集计划可私有标记 `render_policy`，但不能改变现行运行时 tag 投递规则：

| 策略 | 何时使用 | GUI Track 处理 |
|---|---|---|
| `system_surface` | 希望把通知转为 GUI tag 时，须有真实系统表面 | 只有修订流并重验语义/证据后才截图；原有 notification tag 在 image 模式仍是文字 |
| `app_screen` | App 列表、详情、表单等 | 通过 App 导航到指定视图并截图 |
| `text_only` | 非 GUI tag 或无可验证画面 | 保留原 tag 的 `text_event`，不可伪造图片 |
| `not_observable` | 只存在于隐藏 World 的后台事实 | 不新增 Passive；不得删除现有任务流中的语义必要观察 |

拒绝 `tag=gui` 却无可采集表面、重复 `packet_id`、时间/顺序冲突。GUI `text_event` 作为 summary 摘要，不能声称截图未显示的决定性事实；图像不得泄漏 World 分支。当前每包仅一张图；多帧需先拆成不同 packet 并审查证据引用，不增设 `frames[]` 运行字段。

### 3.2 Episode 级私有计划

Runner 以当前场景 `cluster.json`、`episodes/<episode_id>/manifest.json`、引用的 shared 基线、World patch/history、Passive stream、compatibility 和初始化投影为事实来源；`benchmark.json` 只登记场景路径。计划记录源资产哈希、设备/App 版本、环境参数、各 GUI packet 的适配器/视图/时间/断言/超时；按 `history.initial_overrides/events` 在观察时刻还原状态，不用最终 World 倒拍。采集计划由资产编译，不另存一套 World；新增场景只加数据目录与路径登记，不在公共模型运行代码加场景 adapter。

同一 Episode 可比较 summary 与 image 输入，但不能默认两种模态证据等价。图片若缺少决定性事实，应修正摘要或重新审核该 Episode 的 `need/evidence_sets` 与语义判断，必要时拆版本并重新计算 bundle/hash；不能无声沿用现有 GT。当前 GT 没有 P/A 标签，也不支持每 Track 一个并行答案字段。图片文件名不可泄漏 World 或答案，模型只收到受控图像字节。

### 3.3 采集结果最小记录

每张图至少保存：`episode_id`、`passive_id`、`packet_id`、采集运行 ID、源资产哈希、观察与采集时间、设备/App 状态、导航/readback 收据、`gui/<packet_id>.png` 和 SHA-256、尺寸、断言/失败原因、机器验收规则版本及证据区域。保留原始 PNG；派生裁剪/压缩图不可替换运行时按原图哈希取回的资产。UI dump 只用于定位/QA，不作为像素可见事实。

## 4. 运行流程：Runner、Controller、App 的职责

```text
作者资产 + 合法 Episode
  → 编译/校验 capture_plan
  → 独占 KU 设备；恢复 init_state 和扩展权威服务
  → 注入共享初态；按 World history 初始化观察起点；独立 readback
  → 按 packet 的投递时间/顺序重放 history 事件
  → 呈现指定 App/系统表面；等到可见性断言成立
  → 原始截图、设备状态与哈希；绑定原 packet
  → 到 EOF/决策点，核验最终 World 状态与历史回放；隔离采集后台写入
  → 复位并验证无残留；机器验收 GUI Track
```

职责划分：

| Module | 对外 Interface | 实现责任 |
|---|---|---|
| 数据编译/校验 | `compile_capture_plan(episode) -> plan / reject_reasons` | 复用场景 manifest、stream、history、compatibility；验证时间、App 能力、公开/私有边界；不碰设备，不改公共运行时 |
| Capture Runner | `capture_episode(plan, device) -> receipt` | 单设备独占、阶段状态机、超时/重试、逐 packet 分发、产物提交、失败隔离和 teardown；唯一持有 GT/World 绑定的层 |
| App/系统呈现适配器 | `prepare_view(view_id, subject_ref, at) -> view_receipt` | 通过已声明途径打开实际 UI，提供当前 activity/实体/可见字段断言；不能返回 GT |
| KU/extension Controller | 已有 seed/readback/进程/时钟/截图等受控接口，必要时补一个小的“就绪/呈现”操作 | 设备与权威状态读写；截图原始像素；不要新增第二套模拟器或通用自然语言解释器 |
| QA/发布器 | `validate_capture(receipt) -> pass / reject` | 按版本化机器规则验原图与事实；发布时另验场景 GT/语义裁决及 `Session` image 输入能力，不把采集 QA 当作完整任务评分 |

**顺序是关键。** 一个 Episode 恢复一次基线，按 `sequence` 播放 Passive；不能每行重置手机后截互不相干的状态再宣称是同一条连续流。若页面切换本身是被动 `user_action`，动作与造成的状态变化要可追溯；若导航只是采集者为取景而做，不得混入 Agent 的观察流或业务 mutation。最好先用直接打开已知页面的受控入口；没有入口时用小段确定性的 GUI 导航，并记录且验证它不会修改业务状态。

本版采集是离线逐包批处理：按源流顺序校验与采图，逐包落收据；失败暂停而不跳行。采集 EOF 后核验决策点状态。**模型运行不是等采集完才首次查询**：`Session` 首轮填满最多 10 条的窗口，之后每轮 `search` 或 `wait` 都滑动 1 条；流中查询按当轮时间读 World history，下一轮返回回执；EOF 可继续查或 final。采集 wall-clock 不等于模型逻辑时间。无需背压/实时推送队列；若将来接直播输入，另立协议。

**真实时间与逻辑时间分离。** `observed_at` 是事实观察时刻、`delivered_at` 决定运行时窗口时刻，`captured_at` 是生产时钟。Android 时区/时钟/App 缓存/通知排序须与计划一致。动态样本须由当前 `active_world/history/*.json` 的 `initial_overrides/events` 重放，不能拿最终 World 倒拍旧画面；流中查询必须与同一时刻的历史投影相符。

**EOF 不是 Search 的起点。** `Session` 到 EOF 后把 `current_time` 推到 `decision_point`，仍可用剩余预算查当前 World 并 final；之前的查询按历史时点结算。设备侧“freeze”仅用于阻止采集期间后台漂移，须覆盖实际参与 App；不能把 extension Controller 的 freeze 当成运行协议、也不能据此删除流中查询历史。导航和截图不得改业务事实。

**故障处理。** seed、readback、页面定位、截图、哈希、可见性或恢复任一步失败，整条 GUI Track 标记 `capture_failed`，不发布半成品；保留故障日志与原图用于定位。仅在同一基线、同一资产哈希、同一 plan 下对暂态设备错误重试；重试需从干净基线开始。对“页面内容不符”不能靠多截几次挑一张通过，应先修状态、导航或契约。单设备一次只采一条 Episode；扩量再用多实例并行。

## 5. 后端尚需完成的工作（按依赖顺序）

| 序号 | 工作 | 最小修改位置/方式 | 完成证据 |
|---|---|---|---|
| B0 | **已登记场景 × App 可呈现性审计**：覆盖当前五簇中拟转图的 GUI tag；列出来源、权威数据、视图、字段、注入/恢复/读回现状 | 作者侧能力表；不增场景专属运行时代码 | 每个拟采 packet 有 `supported / missing / text_only` 结论；只复用已验证能力 |
| B1 | 私有 `capture_plan` 与现有流格式校验 | 作者侧离线命令；复用 ProactiveBench `validate.py` 的公开包约束，不修改场景无关 runtime | 缺视图、不可见字段、时间/历史冲突、泄漏等可拒绝；产物包含源哈希 |
| B2 | 参与场景的 App 真实初始化与独立读回 | KU App 优先复用 `runtime/setup` 和已验证 helper；extension App 复用 local Controller；不足时按 App 补适配器 | seed 后 GUI、业务存储 readback 与语义 World 三者同值；反事实只改声明字段；可恢复到基线 |
| B3 | 表面呈现能力 | App 适配器声明 `view_id → navigation + ready assertion`；通知类接 Android 系统表面 | 能稳定打开指定实体而非仅打开 App 首页；不会误进同名实体；超时可诊断；通知确来自声明来源 |
| B3a | **系统顶部横幅通知**（仅当选中的 Passive 含此类型；本版 **P2 不做**，见 §5.1） | 对实际来源 App 加原生通知发布入口；Runner 增加触发后即刻检测/截图/取消的握手，不复用 `trigger_popup` | 在桌面或其他 App 前台截到真实 System UI 横幅，来源包名/标题/正文与 packet 一致；消失或被遮挡时拒收 |
| B4 | Capture Runner | 新增一个离线采集命令，调用现有 reset/seed/process/screenshot/readback 接口 | 单 Episode 顺序采集，图按 `passive_id/packet_id` 绑定；中断可清理 |
| B5 | 截图接口收据修整 | 复用 ADB 原始截图，绕开自动生成新公开 packet 的接口副作用 | PNG 与 `screenshot_sha256` 成对绑定在源流固定路径；一个 GUI packet 一图，无覆盖/撞名 |
| B6 | 可见性和污染检查 | UIAutomator/已有 WebView 定位 + 原始 PNG 的 OCR/图像区域检查；`read_state`/mutation journal/teardown；按 App/视图声明机器断言与阈值 | 每个关键值有像素区域、识别结果及置信度/阈值收据；无遮挡/截断/加载中/错误页；取景动作不修改业务状态；重置后无跨 Episode 残留；不确定即拒收 |
| B7 | GUI 采集验收、发布与评测输入 | 绑定 PNG 哈希的机器收据；用 `Session(..., gui_mode="image").model_messages(model_capabilities=...)` 投影并验证模型图片能力；现有 `run.py` 仍为 summary，需另行接通 image 运行入口 | GUI 只给 ID+图，非 GUI 给 ID+文本；`current_time`/公开 frontier 仍由 Session 提供；GT 和私有路径不外泄；语义裁决独立进行 |

B2/B3 是工期主风险，**每新增一个场景簇都执行 B0，再只补缺失的 App 能力**。现有六款 App 的类型化注入和 GUI 验收覆盖其各自业务；当前五簇涉及的 Calendar/Mail/Clock/Files/Contacts 等页面和数据不能由它们直接代劳。Calendar 时间/提醒、Mail 收件、Files 内容、Contacts 同名实体等须逐 App 读回。只能经 JSON 模拟查询、不能在设备画面呈现的 GUI tag 暂留 summary 开发数据，不能开启整流 image 模式。

### 5.1 实施优先级（2026-09-25 新增）

本蓝图的目标是**先产出可用的 GUI 数据**，再决定是否对外发布为评测集；§5 与 §7 此前未分档，容易读成“必须先通过全部验收才能产出第一条数据”。因此按优先级分档，**生产流程只需 P0 全通**：

- **P0（产出数据必需）**：B0、B1、B2、B3、B4、B5。
- **P1（对外发布评测集必需）**：B6、B7，以及 §6.9 的“证据区域 + 置信度 + 规则版本”完整机器验收体系、§3.2 provenance 的逐项重验、G3/G4/G5（见 §7）。
- **P2（本版不做）**：B3a 系统顶部横幅。原 `notification` tag 保留文本，不宣称其为图像；若某 GUI tag 必须依赖该横幅，本轮拒收 image 输入，不能靠 `render_policy` 略过或偷换现有 Passive。将来改 tag/流时重验 GT 和评分绑定。

P0 阶段最低核验为“原图可找到锚点 + 独立 readback 一致”，仅用于开发数据，不算已验收 Gold；P1 升级为 §6.9 的完整机器收据。**图片决定性信息不足时，立即标明当前 GT/证据组不适用于 image 输入，不等 P1 才处理，也不添加 P/A 字段。**

## 6. 关键一致性检查

1. **事实一致**：每个可见事实可追溯到同一 Episode 的 `GlobalBase + ClusterBase + WorldVariant` 或声明的外生事件；GUI 与 `read_state/query` 指向同一实体版本。不能以截图 OCR 作为唯一“权威回读”。
2. **来源一致**：截图里的 App/通知发送者与 `source` 一致；由 Runner 操作形成的画面不能冒称自然到达的通知。系统通知权限、频道、折叠和锁屏显示策略必须固定并留证。
3. **时间一致**：`sequence`、`observed_at`、`delivered_at`、设备时间、截图时刻各有明确语义；旧观察不引用未来 World 状态。若 Android 通知显示“刚刚/昨天”，也须符合逻辑时间。
4. **反事实同流**：按场景 compatibility 校验共享 Passive 的 World 组合；模型每轮可查，须保证查询前公开窗口/初始 Frontier/预算不泄漏分支，并验证按时点回放的 Active 结果。像素可以有系统噪声，决定性公开事实不能由图像文件名泄漏。
5. **模态真值**：`text_event` 决定结论的字段须逐项确认图片是否可读；不可见时修正摘要或重新审核 `need/evidence_sets` 与动作语义，并重新绑定版本/hash；不得无声沿用 summary 的 GT。
6. **采集完整性**：PNG 可解码、尺寸正确、非黑屏/加载屏/权限对话框、指定 App 在前台、目标实体唯一，页面稳定后才截。分辨率、DPI、字体、语言、主题、动画/网络条件与 APK/系统/数据版本**逐帧记录**（§3.3 字段）；**不预先锁定**这批环境参数，改为在机器验收中加一条“同一 Episode 内各帧的分辨率/DPI/字体/主题/语言必须一致，不一致即拒收”。仅当需要声称字节级可复现（G5）时，才升级为“预先锁定 + 采集前后各验一次”。
7. **权限与隔离**：Runner 私有管理密钥、seed、GT、真实实体绑定不传给 Agent；只读的截图下载也不能向模型暴露宿主机路径或其他 Episode 的图片。
8. **复位完整**：KU snapshot 与 extension SQLite/后台服务一起恢复。现有 `teardown` 只覆盖已声明范围；涉及原生系统通知、Calendar/Contacts/Files 或多 App 服务时，须另核对残留及不可逆副作用。
9. **机器验收闭环**：每个 `visible_assertion` 都要在原始 PNG 的明确区域核验公开字段及其可读性，结合 UI 层级定位、OCR/图像检测与独立 readback；UI 层级命中或数据库有值不能单独证明像素可见。系统横幅还须机器检测真实 System UI 区域、文案、来源和遮挡状态。机器检查覆盖模态真值、反事实泄漏、原图与发布图哈希；保存规则/模型版本、阈值、实测值和失败原因。任一必要证据缺失、置信度不足或检查器不支持该页面时，状态只能为 `reject/unsupported`，不得进入发布集。

## 7. 验收顺序与发布门槛

| 阶段 | 最小交付 | 通过条件 |
|---|---|---|
| G0 契约 | 一个已登记场景簇的合法 Episode、现行流格式、能力表与私有 capture plan | 编译器能拒绝不可呈现、历史时间冲突和私有泄漏计划 |
| G1 单帧 | 1 条真实 App GUI packet | seed→独立 readback→指定详情页→原 PNG→机器可见性验收→复位全通过 |
| G2 连续流 | 同一 Episode 至少 2 个观察点 | 不重置设备连续采集；packet 顺序、时间、来源、画面逐项一致；无采集动作污染 |
| G3 反事实 | 对已登记 compatibility 的共享 Passive/多 World 配对抽样 | 查询前公开 GUI 不泄漏分支；各历史时点及决策点 readback 与声明式 World 一致；不强制 GT 结论相反 |
| G4 子集 | 规模由实际可采 GUI tag 决定，不沿用旧正式方案的 8–16 条目标 | 每张图可追溯、每条 image 输入有机器收据；整流所有 GUI tag 均有图，失败/拒收数公开；相关版本按组划分 |
| G5 冻结 | 版本化图片、manifest、QA、哈希、采集收据与评测投影 | 重放给模型的图和通过机器验收的图字节一致；不因环境更新静默替换；后续修改发布新版本 |

**§7 是发布门槛，不是生产步骤。** 可先生产 G0/G1 开发数据，G2 做连续流样板；G3/G4/G5 是对外发布时的门槛。除此之外必须有 ProactiveBench 的格式/绑定校验、场景业务审核、证据组评分及动作语义裁决；待评正例指标保留 null，不能因截图通过就称 Gold。

G1/G2 设备 smoke 不能替代 G4 对每张图的机器可见性验收；机器截图 QA 与业务/动作语义审核是不同环节。发布器按原图哈希和收据逐包校验；任何 `pending/reject/unsupported` 或缺失收据的 GUI 包都不得用于 image 输入。当前 `run.py` 仅运行 summary；未接通具备图片能力的运行入口及完成语义裁决前，只能发布明确标注的开发数据，不称 GUI Gold 或完整模型评测成绩。

## 8. 暂不扩大的范围

首版不需要通用自然语言到 UI 的生成器、为六款扩展 App 重造 KU 任务系统、实时模型驱动截图、视频全帧录制、GUI 主动搜索导航、GUI 执行评分或多设备调度平台。当前目标用**作者侧结构化 Packet + 一个离线 Capture Runner + 各 App 少量视图适配**即可覆盖；当具体场景证明这些能力不足，再扩对应 seam。
