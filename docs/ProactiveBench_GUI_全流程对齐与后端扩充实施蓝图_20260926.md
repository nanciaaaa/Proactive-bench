# ProactiveBench GUI 全流程对齐与后端扩充实施蓝图

> 状态：代码核查后的实施方案，**不是已完成集成或 Gold 声明**。依据当前 `../../相关文件/ProactiveBench/ProactiveBench/` 与本目录源码；本文是唯一现行实施蓝图。对齐前的[旧扩充蓝图](../_archive/pb-gui-prealignment-20260926/AA-ACA_Benchmark_v1_文本Passive到真实GUI采集_后端扩充蓝图_20260924.md)与[旧工程日志](../_archive/pb-gui-prealignment-20260926/readme_gui工程实现.md)已隔离归档，仅供追溯，不作任务契约或完成依据。后续 PB 版本变化先重做第 0 轮契约冻结。

## 1. 不可变边界与目标

1. **不能修改 KU 原本的代码、镜像、APK、快照 `init_state` 或任务实现。** 所有新注入、导航、截图、读回、复位与 QA 仅放在 `simulator-extension/` 的脚本、Controller、扩展 App 或可新增的侧车；复用 KU 已有公开接口、ADB 与 snapshot。若现有 KU App 无法在此边界内呈现 PB 字段，该 packet 记 `unsupported`，不伪造截图、不改 KU 过关。
2. PB 的 `benchmark.json`、根级 prompts、`code/runtime.py`、`code/world_model.py`、`code/validate.py`、`code/evaluate.py`、各场景数据是**运行/评分真值**。不另建一个会自行决定 Need 的 GUI benchmark；现有 extension 独立 pilot 只算硬件/表面能力证据，不直接充当 PB Episode 或 Gold。
3. 目标是把**同一 PB Episode** 的共享基线、场景补丁、World/history、Passive、权限/frontier、GT 与真实 Android GUI 画面贯通。Active Search 仍由 PB 按授权来源/当轮 `current_time` 读取 JSON World；GUI 采集侧必须核验被展示的事实与这一投影一致。**不要求 Agent 在手机 GUI 中搜索或执行动作**，也不把系统截图接口当 Active 查询。
4. 本文先确定兼容接口和实施顺序，不在基础后端尚未同源时批量采图。每轮修改、审查、验证与未完成项写入本目录 `PB-GUI工程日志.md`；不得把设计完成写成设备验收通过。

## 1.1 V1 范围冻结：先做一条可复用闭环

**V1 不是全量 PB GUI 数据集，也不是 Gold 发布工程。** V1 只交付一条可复用的端到端闭环：

```text
冻结 PB 抽象适配契约
  → PB Passive 适配/编译
  → PB 数据 → adapter_plan
  → adapter_plan → 真实 App 初始化接口
  → seed / history replay / 独立 readback
  → 指定 App 页面与实体导航
  → ADB 原始截图
  → GUI 像素 QA、哈希、复位收据
  → 回写 PB GUI 图片引用
  → PB image replay 验证
```

V1 最小交付：冻结 `source`、`entity_ref`、`fields`、`observed_at`、`delivered_at`、`adapter`、`seed_projection`、`history_replay`、`readback`、`surface/view_id`、`visible_assertions` 和 `unsupported/reject` 原因；写 Passive 转换层；写真实 App 适配器连接；完成 GUI 获取、机器 QA 和 PB image replay。

V1 不要求覆盖全部 17 Episode/38 GUI packet、所有来源、image live、正式 `evaluate.py`、动作语义裁决、Gold、正式发布或修改 PB 核心查询/评分协议。没有足够 GUI 语义的 Passive 保持 `text_only` 或 `unsupported`，不得补造截图。

## 1.2 V2 范围：用冻结格式扩展数据

V1 闭环通过后，才用冻结格式制作或转换新数据：新 Passive → PB 格式校验 → GUI 可呈现性审计 → 新来源/页面/history 适配 → seed/readback/截图/QA → 按需扩展数据集。全量 coverage、image live、评分、Gold 和正式发布属于 V2/发布目标，不是 V1 失败条件。

## 1.3 对旧蓝图的修正

旧蓝图把“当前要完成的适配器、后端连接和 GUI 采集闭环”与“最终全量 PB 数据集、image live、评分、Gold 和正式发布”写在同一条实施链上，造成两个边界混淆：一是把 17 Episode/38 GUI packet 的数据规模误当成第一版前置条件，二是把尚未存在的 Passive/GUI 事实当成可以通过后续采图补齐的内容。修正后，V1 只验证一条可复用闭环；V2 才扩展 Passive、来源、页面、时间变化和覆盖范围。适配器不得创造 Passive 没有提供的 GUI 语义，缺失信息只能保持 `text_only` 或 `unsupported`。

## 2. PB 当前任务/数据契约（对齐目标）

- 当前登记五簇、17 条开发 Episode，**0 Gold**；场景只在根级 `benchmark.json` 登记路径。每条 `episodes/<id>/manifest.json` 绑定一个 `passive_id`、一个 `world_variant_id`、`history_ref`、初始化目标、访问树/绑定及私有 GT；合法配对以该簇 compatibility 为准，不按旧正式方案自动做笛卡尔积。
- 源流为 `passive_inputs/<passive_id>/stream.jsonl`；顺序包有 `packet_id, sequence, tag, source, observed_at, delivered_at, text_event, author_provenance`。`tag=gui` 可另有成对的 `screenshot_ref/screenshot_sha256`；图片只能位于该 Passive 的 `gui/<packet_id>.png`。非 GUI tag 不附图；不得混入旧 `type/text_projection`。历史合成、真实观察和捕获衍生须如实标 provenance。
- 公共输入 `minimal_passive_v2`：summary 对所有 tag 只给 `packet_id/text_event`；image 对 GUI 给 ID+图，对其他 tag 仍给 ID+文本；原始 `tag/source/observed_at/delivered_at` 等作者字段**不逐包投递给模型**。窗口最多 10 条，首轮填满，之后每轮 `search` 或 `wait` 前进一步；流中即可查，查询按当轮时间回放 history，回执下一轮出现；EOF 后仍可查或 final。查询总预算 6，无最低查询次数，不要求正例必须 Active。
- 最终模型只有一条 `need/evidence`（动作句或 `NoNeed` / `contradicted` / `insufficient_evidence`），中间 NeedThread 为 `thread_id/need/support/contradict`；GT 仅 `need/evidence_sets`。`insufficient_evidence` 不是 GT 类别；没有 P/A、Need Set、`expected_need_set` 或公开标准答案。正例动作语义裁决与有效证据组是不同验收环节；未裁决指标保持 null。
- `code/run.py` 当前 live 入口**固定 summary**，image 仅有 `Session(..., gui_mode="image")` 与 `code/runtime.py replay --gui-mode image --image-capacity N` 的离线通道；原始图片回放不能被误报为 image 模型实跑。PB 的初始化 JSON 标注 `semantic_json_target_not_applied_to_android`；它不是已完成设备写入/读回证明。

## 3. 已有工程与实际冲突

| 环节 | 当前可复用的代码/能力 | 与 PB 的冲突或未完成处 | 对齐处理 |
|---|---|---|---|
| 作者资产 | `scripts/gui_contract.py`、`gui_formal.py`、`contracts/gui-capture.schema.json`、`datasets/gui-*-pilot/` 可编译旧私有计划；部分已有正式 pilot | 旧 `global-base/cluster-base/world-variant/passive/oracle` 是独立资产；硬编码若干 `goal.type`，用 `expected_need_set/passive_class/public_rule` 校验或公开规则，且只收 `type=gui/text_projection`；不能读 PB manifest/history/GT | **新增或替换作者侧 PB 入口**：从 PB 已登记 Episode/引用资产编译私有采集计划，PB GT 只供私有验收；不复用旧 oracle 生成任务答案，不向模型投 `public_rule`。旧 pilot 入口留作回归，不批量转换成 PB |
| 反事实 | `gui_contract.assert_public_equivalent`、`gui_pair_qa.py` 对比旧计划与像素 | 假设所有包均 GUI/带 frame_id、所有同组都要同图；像素规则固定 1080×2400；旧 `gui_g4_audit.py` 的 14 候选不是 PB 17 Episode | 按 PB compatibility/同流关系与运行前公开窗口校验；仅可比较合法共享 Passive 的 GUI 包，非 GUI 文字也须等价；像素差异可注明系统噪声，不硬编码所有设备/簇同尺寸 |
| 物理权威状态 | `local-controller/store.py` 类型化 seed/read_state/query、mutation journal、extension WebView 同源；`scripts/gui_{calendar,mail,clock,contacts,files,messages}.py` 有局部 KU 原生 App 注入/读回 | extension 自有 SQLite/记录模型 ≠ PB `shared + cluster patch + world history`；KU App 脚本仅支持严格 pilot 字段/格式；PB 的 Gallery/Settings 等源缺明确 GUI 适配；`seed` 的 world_deltas 立即执行，`advance_time` 不自动回放 PB history | 做 PB 记录→各 App 的**受限映射表与回读投影**；每个 `source/field` 核对语义、权限、实体、范围/版本。观察起点用 history.initial_overrides，还原后按 effective_at 重放 events；不可映射的包 fail closed。Active 保持 PB JSON 原树与查询规则 |
| 被动事件 | `store.py` 的 passive_packets/drain_events 及 extension popup | Controller 自动发 UUID、局部自增 sequence、`type/text_projection`，drain 时补 `delivered_at`；`/device/screenshot` 又新发“当前屏幕截图”包。这些都不是 PB 原 packet/时间/谱系 | 原 PB stream 由作者资产拥有；设备/Controller 仅提供产生界面的事实与收据。离线采集以 PB ID/顺序/时间绑定图片，不直接将 Controller drain 输出当 PB 流；截图用无副作用的 ADB 原图通道或侧车接口 |
| 设备采集 | `scripts/gui_capture.py`、`gui_machine_qa.py`、`gui_publish.py`、`gui_pair_qa.py` 已能针对若干视图 seed→导航→原图/OCR→读回→复位→哈希发布 | capture 依赖旧 fixture/私有 `frames` 与只含 GUI 的包；QA 仅支持列出的视图/字段，某些状态如 Clock enabled 无校准规则；旧 release 是 `manifest.json` 中 `type/frames/sha256`，PB 不直接读取 | 保留设备/像素工具，新增 PB packet→单张 `gui/<packet_id>.png` 的绑定与审计；按视图增规则。转换前 fail closed；经 `code/validate.py`、`Session` image 投影实际检验后发布，不把旧 release 目录冒充 PB Passive |
| 通知 | `scripts/gui_notification.py` 与本地 Controller/extension 接口已有特定 Dudu 原生 heads-up 试验 | 旧蓝图“本版完全不做横幅”已过时，但这不证明 PB 的 Calendar/Mail/Messages 通知真实来源或截图可靠 | 只在**选定 PB GUI 包**、其真实来源和机器 QA 均被证明时使用；现有非 GUI `notification` 仍投文字，不改 tag 以凑全图；未支持的系统表面拒收 |
| 运行/评分/发布 | PB `validate.py` 验结构/图像哈希，`Session.model_messages` 可离线投图，`evaluate.py` 验动作/证据 | extension `gui_g4_audit` 只审自有 release；PB live `run.py` 仍 summary；仅 OCR 通过不能说明 PB GT、来源版本、NeedThread、语义或 Gold 已通过 | 先产绑定 PB 流的 image 资产，再接 PB image live 入口/模型能力与窗口容量检查；最后沿用 PB 评分、独立语义裁决与 bundle 快照；不同 mode 不混算 |

**真实能力范围**：meeting 的 p002/p003 各 7 条 GUI 包已有历史图片，但 PB `validation/status.json` 明确指出它们早于扩充后的基线、仍待重采/复核；p001 无 GUI。其余四簇的每条 Passive 各有 2 条 GUI 包且目前均无图。按 B0 能力优先选**可完整采集**的一条流做闭环：contact（Messages/Contacts）、PDF（Mail/Files）、photo（Messages/Gallery）、offline（Mail/Settings）；不要因旧 pilot 涉及某 App 就宣称整个簇已覆盖。所有当前流有 22–28 条事件，不能只采 GUI 就删除其余观察。

| PB 场景 | GUI 观察与真实缺口 | 第一条整流验收要点 |
|---|---|---|
| `meeting-preparation-001` | Calendar/Mail 历史图与扩充基线不兼容；Clock 覆盖状态主要由 Active 决定 | p002/p003 须重采并核匹配的 `passive_id`，不能把 p001 强行标 image；提醒/邮件来源及初态须独立读回 |
| `contact-email-sync-001` | Contacts 列表 + Messages 正文；现有 Contact/SMS pilot 不等于 PB 字段/时间 | 列表不应显示未展开的工作/私人邮箱；消息来源、邮箱差异由 PB 目标详情查询判定，不向图片提前泄漏 |
| `pdf-submission-size-001` | Files 列表 + Mail 附件限制；现有 Files pilot 只验文件名/短文本 | 文件名能看见不等于字节大小已观察；以文件实际字节哈希/大小核对 PB Active，邮件 5,000,000 字节条件须完整可读 |
| `photo-resolution-preparation-001` | Gallery 列表 + Messages 正文；Gallery 缺物理适配 | 列表不提前呈现隐藏的原始宽高；MediaStore/文件读回的分辨率与 PB Active 一致，不能把缩放图冒充原图 |
| `offline-demo-mode-001` | Settings 分类列表 + Mail 正文；Settings 缺物理适配 | 分类列表不提前露出飞行模式当前值；真实系统开关状态与 PB Active 一致，采集不改变亮度/字体/时区等其他设置 |

## 4. 统一数据流与后端责任

```text
PB benchmark.json → cluster.json → episode manifest
                  → shared baseline + cluster patch + world variant + history
                  → 原 passive stream + compatibility + private_gt（仅作者/评分）
                  → 作者侧 PB capture_plan（字段映射、时间、实体、视图、机器断言）
                  → KU init_state 复位一次 + extension 权威服务复位
                  → observation_start 初态注入/独立 readback；按 history 事件推进
                  → 对每条 tag=gui：指定实体/真实 App 表面 → 原图 + UI/状态收据
                  → PB stream 保留原非 GUI 包；GUI 包加固定图路径/哈希与捕获 provenance
                  → PB validate + image replay + 机器像素/状态/泄漏 QA + 反事实校验
                  → PB image live 运行（接通后）→ PB evaluate/独立动作语义裁决
```

**时间**：`observed_at` 是观察所描述的事实时刻，`delivered_at` 是 PB 当前窗口与查询时刻，`captured_at` 是采集 wall-clock；物理设备时钟只是呈现条件。必须检查每个可查询时点 PB `world_at(history, current_time)` 与设备读回的一致性；若设备只用于 Passive 呈现，Active 仍使用同一已绑定的 PB 世界，不能把设备额外生成的随机 ID 或状态当成新的 Active 世界。EOF 不是第一次查询；`freeze` 只是设备侧隔离，不能篡改 PB 历史。

**图片/可见性**：一包 GUI 对应一个原始 PNG，`screenshot_ref` 只能是 `passive_inputs/<passive_id>/gui/<packet_id>.png`，与 `screenshot_sha256` 成对。多页/滚动后才见的决定性信息必须通过合法新 packet 与 GT 证据引用的版本修订表达，不能在现有包里偷偷增加 `frames[]`。GUI image 模式不发送 `text_event`；非 GUI 包仍以文本出现，故不能称“纯图”。图上决定性字段缺失时标 `unsupported`，重审 GT/证据组/动作语义并新建版本；不以文字摘要填补图像证据。

**同一 Passive 的多 World**：PB manifest 可以让多个 Episode 引用同一个 `passive_ref`，因此不能每个 World 各写一套同名图片再覆盖共享路径。先在合法配对中分别采集/验收，核对决定性的公开像素和非 GUI 文本同流，再择一套**无分支泄漏的共同图片资产**绑定该 Passive；若确实无法共享，须新增受版本约束的 Passive 变体、更新合法矩阵和相关 GT/运行哈希，不能原位改旧流或静默拆分。

**设备/权限**：仅采集 Runner 持有 seed、绑定、GT、密钥；模型只经 PB `public_state`/受控图像 loader 获取公共输入。一个 Episode 一次基线复位并顺序采集，导航只取景、不伪造成 Agent 行为且不得改变业务状态。读回、mutation journal、PNG 像素检查三者不能互相替代；未实现的物理字段不以 PB JSON 推断为设备已生效。

**复用旧 GUI 实现的界限**：extension 的 popup 是 WebView 内部对话框，需 `load()`/受控刷新后才呈现；`trigger_popup` 的 `agent_gui` mutation 不得当成自然 Passive。Dudu 原生 heads-up 已有专项脚本，但非任意来源/任意 PB packet 的保证，App 内 popup、Android 顶部横幅、通知栏是三个不同表面。旧 `gui_machine_qa.py` 能通过的视图/字段才标已支持；证据收据至少绑定 PB Episode/Passive/packet、源资产与设备版本、原图哈希、App/activity、业务 readback、区域/OCR 或像素值、置信度/规则版本、污染与复位状态。无法验证像素或来源时明确 `reject/unsupported`；人工可以修规格/程序并重采，不能代替单帧机器证据。独立动作语义裁决可以另行人工或独立评审，不与像素验收混为一谈。

## 5. 严格实施顺序：先契约，再后端，再 GUI，最后模型与发布

每轮**只推进通过上一轮闸门的内容**；历史 pilot 保留，不原位覆写 PB 旧流、旧图、旧 GT 或旧运行。任何 PB 源数据/prompt/GT/图片改变产生新 bundle 与 run，不给旧成绩“补图重评分”。以下交付文件均在 extension 侧，除明确的 PB 数据副本/入口适配外不得改 KU 原码。

| 轮次 | 先做什么／落点 | 通过条件；未通过不得开始什么 |
|---|---|---|
| **0 V1 契约冻结** | 选择一个可完整支持的 PB Episode/小场景簇作为 V1（优先使用已验证的 `ep_0004`/`ep_0005`）；冻结 `source/entity_ref/fields/observed_at/delivered_at`、`adapter`、`seed_projection`、`history_replay`、`readback`、`surface/view_id`、`visible_assertions` 与 `unsupported/reject` 原因；建立无设备契约检查 | 选定样本的 PB Episode/Passive/World 绑定、10 窗口/6 预算、模式投影与 GT schema 可复查；不得把 17 Episode/38 packet 全量覆盖设为 V1 前置条件，也不得先动设备 |
| **1 V1 Passive 适配/编译** | 在 `scripts/` 复用 `pb_gui_bridge.py`/`pb_backend_plan.py` 的 PB 入口，把上游原始 Passive 或当前 PB stream 编译为合法 PB Passive 与 `adapter_plan`；保留 packet、顺序、时间和来源，不凭空补 GUI 事实；无足够 GUI 信息时输出 `text_only`/`unsupported` | 无设备单测拒绝路径、ID、字段、时间、非法配对、GT 泄漏和旧 `type/text_projection`；适配输出可被 PB 校验器接受；此轮不改 PB 核心 runtime、Controller schema 或 KU 原码 |
| **2 V1 后端适配与连接** | 对选定 V1 来源完成 `adapter_plan → 真实 App 初始化接口`，并接通 seed、history replay、独立 readback、reset residue；只补 extension 侧适配器/侧车，未适配来源明确拒绝，不把 PB JSON 推断成设备已生效 | 同一 `record_ref/source/fields` 的设备初态、每个 history 变化点与决策点均能独立读回；权限、范围、版本与 PB 投影一致；任一不一致则停在本轮，不进入 GUI 采集 |
| **3 V1 GUI 采集闭环** | 将 `gui_capture.py` 的 reset、指定页面/实体导航、ADB 原始 PNG 与设备收据接到 V1 私有计划；索引 `(cluster_id, passive_id, packet_id, asset_hash)`，只采选定 Passive 的可支持 GUI 包，不制造新公开事实 | 页面目标唯一，图像确属声明来源与时点，导航无 mutation，图与 readback 一致，复位无残留；单图失败不得进入 image replay；V1 先证明一条闭环可复用，不追求全量覆盖 |
| **4 V1 QA 与 PB image replay** | 用 `gui_machine_qa.py`、哈希/复位收据和 `pb_gui_image_host.py` 验证原始 PNG、像素可见性、绑定关系与 image replay；通过后才把图片引用回写到对应 PB Passive 的私有 bundle | PB `runtime.py replay --gui-mode image`、坏哈希/漏图/私有泄漏负例通过；`text_only`/`unsupported` 不进入 image；本轮不要求 image live、正式 `evaluate.py`、Gold 或正式发布 |
| **5 V2 数据与适配扩展** | 仅在 V1 闭环通过后，使用冻结格式制作/转换新 Passive，发现新来源、页面或时间变化，再逐项增加后端适配、GUI QA 规则和覆盖范围；扩展仍须通过同一适配器与收据链 | 每个新增来源先有可读字段、seed/readback 与页面证据，再进入采集；不得用缺少 GUI 信息的 Passive 强行生成截图；V1 已有结果不可被新数据静默覆盖 |
| **6 V2 评分与正式发布** | 在 V2 数据与覆盖范围稳定后，才接 PB `evaluate.py`、image live、动作语义裁决、Gold 生成和正式发布；报告全量 coverage、失败数与待评项 | 仅当机器图像 QA、业务审查、动作语义裁决、PB 评测与发布器全部通过，才讨论全量 17 Episode/38 packet 指标与 Gold；局部 V1 结果不冒充全量结果 |

**依赖锁**：1 依赖 0；2 依赖 1 的确切 PB 字段与 history；3 依赖 2 的真实 readback；4 依赖 3 的原图与 PB 包绑定；5 依赖 4 的冻结格式与可复用收据链；6 依赖 5 的稳定数据与覆盖审计。不得以“先拍全图再补物理状态/GT”的逆序推进。V1 契约或后端字段/时序变动，必须回到 0→1→2→3→4，重新采集与生成新 bundle；像素规则变动回到 4 重审受影响帧；V2 数据扩展不得倒灌修改 PB 核心查询、评分协议或 KU 原码。

## 6. 每轮修改与多轮审查纪律

每轮结束把以下内容追加到 `simulator-extension/docs/PB-GUI工程日志.md`（**含失败轮次**）：轮次、日期、PB 版本/资产哈希、修改文件、选定场景/Episode、验证命令与结果、机器收据位置、已知不支持/风险、下一轮入口条件。该日志文件不存 GT、密钥或本机私有截图路径。旧运行/发布不可静默覆盖。

每轮做两次审查并留下结论：**契约审查**核对 PB 源 schema、时间、同流、权限/泄漏、输出/评分不变量和 KU 未改动；**设备/回归审查**核对最小单测→场景回放→机器像素/复位→相关 PB 全簇验证（未接设备的轮次只做前两级，并明示未运行）。发现不一致就停在本轮修复后复审，不提前推进。至少以“一个 Episode 单图 → 同 Episode 全部 GUI 图 → 合法反事实对照 → 一个簇 → 五簇”的顺序做独立复查；每次先核读取/资产哈希，再跑模型，最后评分。

本轮仅完成代码/契约核查及蓝图整合；**没有修改 KU、Controller、PB 运行代码，也没有新增 GUI Gold 或声称设备验证通过**。后续按轮实现，README 每轮更新，失败与未支持项如实保留。
