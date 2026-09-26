# GUI 工程实现进展

依据：`simulator-extension/AA-ACA_Benchmark_v1_文本Passive到真实GUI采集_后端扩充蓝图_20260924.md`。本文件逐步记录已做工作及验证状态；未通过设备验收的功能不记为完成。2026-09-25 蓝图修订后，发布门槛以**机器审核**为准；下文早期“待人工审核/人工签署”均为历史实现记录，不再是有效验收方向。

## 2026-09-24 · B0 场景与 App 可呈现性审计

- 新增 `contracts/gui-capabilities.json`。首批四个样例簇所需的核心 App 均标 `missing`：KU 部分 setup helper 存在，但缺少可独立验收的注入、实体读回、详情定位及复位闭环。Mail 原生通知和 Android 顶部横幅未验收。新增场景簇须重新做逐 App 能力审计。
- 六款 extension App 仅将已有的 App 内 `popup` 标为可采候选；不冒称四簇 Gold、系统通知或详情页能力。
- 当前 `adb devices` 为空，因此真实 GUI 的 G1–G5 设备验收尚未开始。

## 2026-09-24 · B1 Packet 与 capture_plan 编译

- 新增 `scripts/gui_contract.py`，从作者 JSON 引用的 seed fixture 编译私有计划，记录两份源资产 SHA-256；编译阶段不接触设备。
- 拒绝重复 ID、错序、无时区/逆序时间、未声明 App/视图/实体/字段、伪造截图引用和 packet 中的私有标签；反事实计划可比较查询前公开观察。
- `python3 -m unittest discover -s tests -p test_gui_contract.py -v`：2 项通过。当前仅允许已登记的 extension App 内 popup 视图，其他表面明确拒绝。

## 2026-09-24 · 蓝图修订适配

- 已核对新版蓝图：流程须可复用于后续场景簇，首批四簇是样例而非固定全集。能力表保留按 App/视图登记；对新 App 先审计、验收再开放编译。现阶段的 popup 适配仍只作为独立试点，不宣称满足四簇或详情页门槛。

## 2026-09-24 · B4/B5/B6 离线采集与机器检查

- 新增 `scripts/gui_capture.py`：单设备文件锁，Episode 开始恢复一次基线，按 packet 顺序 seed/readback/启动 App/等待 UIAutomator 可见/ADB 原始截图；每帧立即持久化收据，EOF 冻结与回读，最后复位并核查残留。失败整条标为 `capture_failed`。
- 使用唯一随机 PNG 名和 `packet_id/frame_id` 收据关联，避免现有截图接口自动生成含糊的额外 Passive packet。原 PNG 做结构、CRC、压缩数据与 SHA-256 检查；记录设备版本、尺寸、系统时间、导航和权威实体版本。
- 机器就绪检查不能代替像素可读性人工复核；`adb devices` 当前为空，G1/G2 实机结果未获得。B2/B3 对首批核心 App 与 App 详情页仍未完成。

## 2026-09-24 · B7 审核后发布

- 新增 `scripts/gui_publish.py`：仅接受机检和复位通过、每帧有人名/时间批准且原图哈希一致的收据；公开 Manifest 只含 packet 顺序、来源、观察时间与图片引用/哈希，不输出 `text_projection`、实体绑定或 World 信息。
- `python3 -m unittest discover -s tests -p 'test_gui_*.py' -v`：4 项通过，覆盖非法计划、公开同流、PNG 损坏和发布白名单。

## 2026-09-24 · B3 详情视图试点与两 World 样例

- 为 extension 超威文章 `article_detail` 增加确定性路径：从首页唯一标题点击，等待详情标题/副标题/正文及“内容详情”标记，再从同一 `articles` 权威记录读回；不触发业务 action。
- `datasets/gui-capture-pilot/` 加入两个反事实作者输入：同一公开两 packet（嘟嘟 App 内弹窗、超威文章详情），隐藏 `favorite` 状态不同。它们是后续机制的采集试点，**不是**首批四簇 Gold，也未提供正式 Need/GT 标注。
- 两计划经 `gui_contract.py` 公开同流检查通过；详情定位仍待 Android 实机验证，不能据此宣称 G1–G3 通过。

## 2026-09-24 · B2 extension 权威状态离线回读

- 在临时 local Controller 实例中分别 seed 试点 fixture：嘟嘟回读 `popup/notice-01`，超威回读 `articles/article-pilot` 与隐藏 `favorite/favorite-hidden`，接口均返回 `ok`。
- 此检查只证明 extension Controller 的权威层注入/回读；GUI 与实体一致、反事实画面等价及复位仍须设备采集验证。

## 2026-09-24 · G0 计划不可呈现情况加固

- 编译器拒绝同 App 多个 popup（当前 seed 队列只显示最后一个）、没有逐帧导航的多帧声明、逆序投递时间和 EOF 早于投递；不再把不可忠实呈现的计划误认作可采集。
- 补回归覆盖这些拒收路径；多页/长文采集须先有可验收的逐帧导航适配器，不能用重复首屏截图代替。

## 2026-09-24 · B6/B7 人工审核与发布边界

- 发布前每张原图须由审核人在私有 `receipt.json` 填写 `review_status=approved`、`reviewer`、带时区 `reviewed_at`，以及与计划断言字段完全一致的 `visible_fields: {"字段名": true}`；机器不代替人判断像素是否可读。
- 发布器拒绝重复、越界或符号链接图片路径；有公开投递时间时保留 `delivered_at`，继续剔除私有文案与实体引用。相关失败用例已加入 GUI 回归。

## 2026-09-24 · B2/B4 全 World 回读

- Runner 在全部 App seed 后先按 `episode_scope + entity_type + semantic_ref + data` 核对每条记录（包括画面不显示的 World 分支），EOF 冻结后再比较回读哈希；不再仅凭截图目标实体判断 World 已正确初始化。
- 增加隐藏记录缺失的拒收用例；这仍不能代替另一 World 的设备实采与正式 GT/Need 标注。

## 2026-09-24 · G1 设备试采（未通过）

- 找到运行中的 `gui-pilot` Android 容器并接通宿主 ADB；首试因 Controller 未运行拒收。临时恢复 Controller 后完成基线恢复、两 App seed 与全 World 独立回读，取景 ADB 阶段失败，收据标 `capture_failed` 且复位成功，**没有可发布图片**。
- 为 Runner 的外部命令失败补命令名、退出码和输出诊断；待定位取景后重试，不把 seed 回读当成 G1 实机验收。
- 诊断发现 Android 14 上旧 `dumpsys window` 焦点字段缺失，但 `topResumedActivity` 确认嘟嘟确在前台；取景核对改读 Activity resumed 状态。

## 2026-09-24 · G1/G2/G3 试点实采与时间缺陷

- 修正前台核对后，A/B 两 World 均在同一设备上逐个完成两 packet 连续实采、EOF 冻结回读和复位；A 多一个隐藏 `favorite` 记录，B 无此记录。四张原 PNG 均为 1080×2400，详情页和 App 内弹窗像素可见；目前均为 `pending_review`，不是正式 Gold。
- 实采暴露基线设备停在 2026-04-30 GMT，而 packet 为 2026-09-24 +08:00；旧图片**不得发布**。模拟器已验证可设置系统时钟及 `Asia/Shanghai`，现将 IANA 时区纳入作者计划，并在每条 packet 前校时/验证偏差；须重采确认。
- 首次重采虽使 `date -Is` 正确，状态栏仍显示 UTC 2:00；确认直接 `setprop` 不通知 System UI。改为 Android AlarmManager 的 `set-timezone`/`set-time` 系统服务，手工截图状态栏已显示 10:00；上一轮仍不得发布。

## 2026-09-24 · G1/G2 校时后复采

- A/B 各自重新采得两张原图，系统状态栏分别显示 10:00、10:02，帧内 Android 时间与计划相差 4–9 秒；每条流都同设备连续采集、EOF 回读并复位成功。四张图仍 `pending_review`，须独立人工逐字段/跨 World 复核才能发布。
- 审查发现手工重启 Controller 会覆盖镜像基线版本为空；启动脚本现从实际 KU 容器读取 image digest，Runner 缺版本收据时直接拒收。已采的旧收据版本为空，不能用作 G5 冻结版本。

## 2026-09-24 · G1/G2 版本化实采与发布复验

- A/B 重新各采两帧，基线镜像 digest 一致、Android 时钟分别约为 10:00:05/10:02:09、EOF 回读与复位通过；原图保留在 `runtime/gui-capture-pilot-{a,b}-versioned-20260924/`，仍未审批或发布。
- 发布器重新校验基线 digest、逐帧 Android 时区/时间偏差，并要求人工明确标记 `visible_time_matches: true`，使之前时钟错误/版本缺失的 `pending_review` 收据无法误发布。

## 2026-09-24 · 综合审查第一轮：图片完整性

- 原 PNG 校验从“IDAT 能解压”扩展为逐行像素长度/过滤字节、尾部数据及大小上界校验；拒绝 CRC 正确却缺像素的伪完整 PNG。仅接受当前 Android `screencap` 使用的 8-bit RGB/RGBA 非隔行格式。

## 2026-09-24 · 综合审查第二轮：计划谱系

- 新收据保存私有计划 SHA-256，发布时按原计划字节复验；采后变更 packet、截图绑定或隐藏状态计划会拒绝发布。既有收据未含此字段，须重采，不补签旧收据。

## 2026-09-24 · 综合审查第三轮：契约一致性

- 编译器与 JSON Schema 统一：`frame_id`/`frames` 必须二选一，拒绝未知作者/采集字段和逐帧额外字段；错误 App ID 直接拒收，不触发内部 KeyError。
- 全量复核发现原 `gui-capture.schema.json` 的 `frames` 属性少一个结束括号，导致 Schema 不能解析；已修复并用独立回归锁定。

## 2026-09-24 · 历史未完成门槛快照（不得据此覆盖后续状态）

- G0/G3：试点 fixture 不是正式 `GlobalBase + ClusterBase + WorldVariant + compatibility + GT/Need` Episode；A/B 虽有不同隐藏回读，还没有合法矩阵、相反正确结论和 GUI Track 独立真值。
- B2/B3：首批 Calendar/Mail/Clock/Contacts/Files 的注入、独立回读、详情导航与复位未逐 App 实采验收；Android heads-up 通知也尚无真实来源 App 发布/捕获握手，不能冒称系统通知或首批四簇纯 GUI。
- B6/G4：最新四张试点图未经人工逐字段、时间、来源和跨 World 泄漏复核；也没有 8–16 条候选子集、拒收统计和按组划分。
- G5：最新原图是在计划哈希门槛加入之前采得，尚无可发布收据；重采、人工审批与版本冻结后方可生成正式公开 Manifest，不能补签旧图。
- 2026-09-24 本地验证：`python3 -m unittest discover -s tests -q` 共 116 项通过；Schema JSON 解析、脚本编译与启动脚本语法检查通过。设备试点机检通过不等于上述发布门槛通过。本节是当日快照，后续完成项以日期靠后的记录为准。

## 2026-09-24 · B0/B1 KU Calendar 受限适配计划

- 设备实验确认 Fossify `events.db` 可用固定 `import_id` 注入并独立回读；搜索唯一 ASCII 标题后点击会打开真实 **Edit Event** 页面，标题、时间、地点、说明、30 分钟提醒在同一屏可见；没有点保存，随后恢复快照。
- 能力表仅开放 `calendar/event_editor` 试点，不冒称只读详情或所有 Calendar 场景；编译器支持单独 `ku_records`，拒绝其他未验收 KU App、跨日/非 30 分钟提醒/不可确定搜索标题。设备 Runner 接入与端到端复测仍待完成。

## 2026-09-24 · B2/B3 KU Calendar 试点 Runner

- 新增 `scripts/gui_calendar.py`：固定 `import_id` 对应私有 `semantic_ref`，Fossify SQLite 注入与逐字段独立回读；Runner 在同一 Episode 基线下接入 Calendar 搜索→编辑页、原 PNG、导航前后读回、EOF 回读及快照后残留检查。
- 新增 `datasets/gui-calendar-pilot/` 单帧作者资产及不支持标题的拒收测试。此时只是代码路径，本条**不宣称设备端到端成功**，也不是正式场景簇 Gold。

## 2026-09-24 · B2/B3 KU Calendar 单帧实采

- `gui-pilot` 设备运行 `datasets/gui-calendar-pilot/author.json`：固定导入 ID 注入 Fossify `events.db`，独立读回事件 ID/标题/起止/地点/说明/提醒/来源，唯一搜索结果打开真实 **Edit Event** 页面；原 PNG 的六个断言字段与 10:00 状态栏可见，导航后和 EOF 权威行未变化，快照恢复后导入 ID 不残留。
- 收据在 `runtime/gui-calendar-pilot-first-20260924/`，状态 `pending_review`、`reset_verified=true`；仅证明受限英文标题、同日 30 分钟提醒页面路径。人工审批、正式场景簇 Mail/Calendar 联动和跨 World GUI Gold 均未完成。

## 2026-09-24 · B0/B6 Mail 试验揭示系统横幅污染

- KU `com.gmailclone` 的 `state.json` 可注入并显示真实邮件正文，但 APK 未声明通知权限；它不能发布声明来源为 Mail 的 Android heads-up。该页面试验截图被基线 Calendar 的旧提醒横幅覆盖，**已拒绝作为任何 Mail GUI 证据**。
- App 屏采集现于每张原图前后检查窗口树是否由 System UI/其他 App 覆盖，出现则整条失败；瞬时遮挡仍需人工逐像素检查。Mail 详情注入/回读 Runner 及真实通知发布均待实现。

## 2026-09-24 · B1/B2/B3 KU Mail 详情试点实现

- 新增 `scripts/gui_mail.py` 与 `datasets/gui-mail-pilot/`：在基线 `state.json` 中追加唯一且预置为已读的消息，启动 `com.gmailclone`、打开唯一主题，截图前后核对整份权威 JSON 哈希，EOF 再读回，复位后检查消息消失。
- `mail_detail` 仅允许 `app_screen`，编译器拒绝将它标成系统通知；当前为代码和测试完成，设备试采尚未通过，真实 Mail 横幅仍缺原生 App 能力。

## 2026-09-24 · KU Mail 首次 Runner 实采拒收

- `runtime/gui-mail-pilot-first-20260924/` 的 seed、权威 JSON 读回、真实 Mail 详情导航、EOF 和快照复位均通过机检，但逐像素检查原图发现旧 Fossify Calendar 的 **Gym Session** 顶部横幅遮住邮件主题与状态栏；本帧明确拒收，不得发布或计作 G1。UIAutomator 前后窗口树检查未捕获这一瞬时遮挡，`pending_review` 仅是机检状态而非人工通过。
- 下一步需在 App 页面采集期间抑制非目标的系统通知，并把系统表面显隐纳入拒收检查；独立的真实通知 Track 不能复用这项抑制策略。

## 2026-09-24 · B6 App 屏通知污染防护与 KU Mail 重采

- `scripts/gui_capture.py` 在当前仅支持的 `app_screen` 采集前启用并核验 Android DND 无打扰模式，原图前后还检查 `NotificationShade` 窗口显隐；两条定向回归覆盖抑制失败和系统遮挡。该策略不适用于未来真实通知截图，接入 `system_surface` 时必须另走允许通知的采集路径。
- `runtime/gui-mail-pilot-dnd-20260924/` 重采通过 seed→独立 JSON 读回→真实 Mail 详情页→原始 PNG→EOF→复位机检。人工查看原图可清楚读到主题 `GuiPilotMail`、发件人 `Organizer`、日期 `Sep 24`、正文和 10:00 状态栏，未见遮挡；仍为试点 `pending_review`，不冒称通知或正式 G1/Gold。

## 2026-09-24 · G2 Calendar/Mail 双观察点试采

- 新增 `datasets/gui-calendar-mail-pilot/` 两条有序 Packet：10:00 Calendar 真正 Edit Event 页面、10:05 Mail 真正详情页。`runtime/gui-calendar-mail-pilot-first-20260924/` 单次 Runner 中连续出两张原 PNG，中间未复位；两个独立权威源的 seed、逐帧前后读回、EOF 对照和最终快照残留检查均通过，状态 `pending_review`、`reset_verified=true`。
- 人工查看两张原图：Calendar 标题/10:30–11:00/地点/说明/30 分钟提醒，Mail 主题/发件人/日期/正文均可读，状态栏为各自 10:00/10:05，无顶部遮挡。它证明受限试点的 G2 形状，不代表正式样例簇、反事实 W*、通知或独立 GUI Track 审核完成。全量本地测试 121 项通过（现存少量资源关闭警告）。

## 2026-09-24 · B0 KU Clock 真实入口审计

- 设备确认 Google DeskClock 的 `alarms.db/alarm_templates` 存储含 `external_uuid`、时间、启用位、重复日和标签；真实 Alarm 卡片可显示唯一标签、时间和 `onoff` 开关。直接插入非 UUID 字符串会导致 DeskClock 后台线程崩溃；改成合法 UUID 后，详情卡片正常渲染，已恢复设备快照。本结论仅作为适配设计依据，尚未登记为可采能力。

## 2026-09-24 · B1/B2/B3 KU Clock 受限适配代码

- 新增 `scripts/gui_clock.py`、`datasets/gui-clock-pilot/` 并接入编译器、能力表、Runner：以 Episode/ref 派生合法稳定 UUID，限定唯一 ASCII 标签和一次性闹钟，核对权威库的时间/启用状态，并在同一闹钟卡片核对标签、可见时间及开关状态；EOF 与快照后残留也核查。
- 15 项聚焦测试通过。此时仅为代码与小范围人工设备探测，尚未通过 Runner 的实际截图/复位验收，不能称 Clock 已正式可采或满足目标日期覆盖。

## 2026-09-24 · KU Clock 首次 Runner 图片拒收

- `runtime/gui-clock-pilot-first-20260924/` 通过权威行读回、唯一卡片 UI 状态与复位机检，但原图含 DND 的“Device is set to total silence”系统提示及 DeskClock 的 bedtime 引导气泡，遮挡页面底部；该帧拒收，不得升格为 G1。说明 DND 防横幅也会制造短时 UI，须等待系统提示稳定消失并加强窗口/应用浮层检查。

## 2026-09-25 · KU Clock 浮层拒收修正与暂停点

- `runtime/gui-clock-pilot-clean-20260924/` 在关闭 DND 后检测到真实 System UI 横幅，按规则 `capture_failed`；`runtime/gui-clock-pilot-unobscured-20260924/` 虽通过机检，原图仍有 DeskClock bedtime 引导气泡，**拒收**。窗口核查确认该气泡是独立可见的 `PopupWindow`，不属于目标闹钟卡片。
- Runner 现对 Clock 明确关闭 DND、在固定的 1080×2400 设备基线上点击非业务工具栏空白处消除引导气泡，并等待系统通知窗口消退；Clock 截图前后额外拒绝可见 `PopupWindow`。该动作前后仍核对权威闹钟行，未将导航作为业务操作。它是当前设备基线专用路径，换分辨率或 APK 必须重新验证。
- `runtime/gui-clock-pilot-tip-guard-20260925/`：单帧 Runner 收据 `pending_review`、`reset_verified=true`，权威 UUID/10:45/关闭状态从注入到 EOF 一致；人工查看原 PNG 的 `GuiPilotAlarm`、10:45 AM、关闭的开关与 10:00 状态栏清楚可见，无系统横幅、DND 提示或引导气泡。仍未经过正式逐帧审核与发布签署，不计作正式 G1/Gold。全量本地测试 123 项通过（仅见此前已有的资源关闭警告）。

### 当前进度

- 已有可复用的私有 Packet 编译、能力白名单、单设备顺序 Runner、原始 PNG/哈希/收据、独立权威读回、复位残留检查和待审核发布路径；六款 extension App 只验收其已声明的 popup/局部详情能力。
- KU Calendar 编辑页、KU Mail 详情页、KU Clock 一次性闹钟卡片、Contacts 同名身份详情、Files Download 列表与短纯文本正文均有受限设备试点；Calendar→Mail 双 Packet 在同一 Episode 中连续实采并验证 EOF、复位。Files A/B 还有同流不同字节与正式资产形状的试点。失败图保留为诊断证据，不得误作通过图。所有当前通过的试点均为 `pending_review`，不是正式四簇 GUI Gold。

### 待做事项（恢复工作时按依赖顺序）

1. B0/B2/B3：逐场景核对权威 World、视图和字段；Contacts 同名电话/邮箱详情与 Files Download 列表/短纯文本正文已做受限试点，仍需联系人更新来源与其他字段、Files 多版本、Clock 日期/重复与启用语义、Calendar 只读详情及 Mail 真正收件能力的独立设备闭环。每接入新能力先审计再开放。
2. B3a：若选中 Passive 含顶部通知，在**实际来源 App** 增加最小原生通知发布入口，验证权限/频道/包名/文案与触发→即时原图→取消握手；当前 Gmailclone App 屏不能冒称 Mail heads-up，App 屏 DND 防污染策略也不能用于通知 Track。
3. B1/B4/B6：Files 单簇已经从 `manifest + GlobalBase + ClusterBase + WorldVariant + compatibility` 派生合法计划与相反 Need Set，并有内部离线 Search/Need 试点；仍需把正式发布包与隔离模型查询服务接通、扩展至首批四簇、按时重放事件、查询前跨 World 人工可见性审核，以及消除 Clock 导航对固定坐标的依赖或将设备配置明确锁定。
4. G0–G5/B7：按正式样例簇和后续场景逐门验收；构建有拒收计数的候选子集，逐 Track 做人工像素/时间/来源/遮挡审核，按组划分并冻结版本化原图、manifest、QA、哈希、收据和只含公开图片的评测输入；禁止用单帧/双帧试点替代正式门槛。
5. 全部需求完成后依蓝图逐项进行**多轮综合审查**（契约与数据谱系、设备 GUI/污染/复位、反事实与发布投影），修正遗漏并重新跑定向、全量和真实设备验收。当前尚未到最终审查阶段。

## 2026-09-25 · 接续工作：B1/B4 数据路由与反事实同流

- 核对现有代码和试点后发现：编译器允许将 KU 原生 App 记录放进 `common_records`，但 Runner 只会从 `ku_records` 注入这些 App；这种计划会在采集时才暴露未注入状态。现已在编译期拒绝错误存储路由和缺字段记录。
- 反事实同流核对现在比较每个被截图实体的完整种子数据，同时保留原有公开字段比较，防止未列入 `visible_assertions` 的页面内容随 World 分支变化而泄漏。
- GUI 定向回归 16 项通过、相关脚本编译通过。当前 `adb devices` 无设备；本步是离线契约修正，不宣称新增真实 GUI 验收。

## 2026-09-25 · B0/B2/B3 KU Contacts 同名身份试点

- 在恢复的 Android 14 设备上审计 Google Contacts：以 ContactsProvider 的 `raw_contact_id/contact_id` 直达详情页可见姓名和电话；两条同名联系人只按姓名导航会歧义。新增 `scripts/gui_contacts.py`，通过禁用自动聚合注入两条同名、不同电话记录，按唯一电话定位 raw ID，核对完整 name/phone 数据行，再以 `contact_id` 打开真实详情页。只开放受限 `contacts/contact_detail` 能力。
- `datasets/gui-contacts-pilot/` 中的 `person-a/person-b` 同名。单帧捕获 `person-b`：两条权威记录 ID 22/23 分离；原 PNG 明确显示 `GuiPilotTwin` 和 `555-0112`（对应 `person-b`），10:00 状态栏可见；EOF 回读哈希相同，快照复位后两号码均不残留。收据在 `runtime/gui-contacts-pilot-second-20260925/`，`pending_review`、`reset_verified=true`。
- GUI 定向回归 17 项通过。该图尚未签署人工审核；只证明受限同名身份与电话详情，不覆盖邮箱等字段、完整 Contact Update 场景或正式 Gold。首次采集因临时 Controller 进程在命令结束后退出而拒收，保留失败收据；随后在同一执行会话维持 Controller 完成实采。

## 2026-09-25 · B0/B2/B3 KU Files 路径与内容哈希试点

- 审计 Android DocumentsUI：可直达 `Download` 目录，再搜索唯一文件名；页面像素显示目录、文件名和修改时间。新增 `scripts/gui_files.py` 与 `datasets/gui-files-pilot/`，仅开放单层 Download 文件视图；实际设备文件的 SHA-256 和修改时间由 ADB 独立回读，内容哈希只作权威证据，不冒充像素可见字段。
- 首次原图的状态栏为 10:00，但文件列表显示注入时的 8:50，已拒绝该图。修正为作者资产显式 `modified_at`，注入后设置设备文件时间并在 seed、逐帧、EOF 回读校验。`runtime/gui-files-pilot-time-corrected-20260925/` 重采的原图显示 `Download / GuiPilotProof.txt / 10 AM`，状态栏 10:00；设备字节哈希 `4f87dcbba1b4f9e4b9215f95b513f91af23eb9b56055a511104ae9777112269d`，EOF 与复位通过，收据 `pending_review`。
- GUI 定向回归 18 项通过。此路径只证明单层 Download 列表和设备内容哈希；文件正文、Docreader 打开、旧版/损坏/异名同内容的完整场景矩阵仍未实现，也未完成正式人工签署。

## 2026-09-25 · G3 试点：Files 隐藏内容反事实

- `gui-files-pilot` 新增 B World：同一 `Download/GuiPilotProof.txt`、同长度 17 字节、同修改时间，内容由 `GuiPilotContentV1` 改为 `GuiPilotContentV2`。编译器的公开同流核对按已验收的列表视图比较路径、修改时间、尺寸和断言；允许未打开的字节不同，拒绝尺寸或修改时间泄漏。
- A/B 分别从同一设备基线完整实采：`runtime/gui-files-world-{a,b}-20260925/` 均 `pending_review`、EOF 哈希回读及复位通过。两张 1080×2400 原始 PNG 的 SHA-256 **完全相同**（`64b1ed75605c11acee9794af51a2286ed32fcb455cd9dbefd68dd4b8b1cbb28d`）；设备文件 SHA-256 分别为 `4f87dcbba1b4f9e4b9215f95b513f91af23eb9b56055a511104ae9777112269d` 与 `2f4584b6414720c0921b53250c84f4283cade6f31e0110ef830e898ed2bb23f8`。
- 这证明受限列表视图的查询前像素相同、隐藏字节不同；尚未由正式 `GlobalBase + ClusterBase + WorldVariant + compatibility` 编译，也没有相反 Need/GT 和独立 GUI Track 标注，不能计作正式 G3 Gold。

## 2026-09-25 · B1 导航身份唯一性补查

- 编译期现在按适配器真正用于定位的键拒绝重复 KU 记录：Calendar 标题、Mail 主题、Clock 标签、Contacts 电话、Files 路径。Contacts 同名仍可合法存在；相同号码不能在运行时才失败。GUI 定向回归 18 项通过。

## 2026-09-25 · G0/G3 正式资产形状试点编译

- 新增 `scripts/gui_formal.py` 和 `datasets/gui-files-formal-pilot/`：由 `manifest` 引用 GlobalBase、ClusterBase、WorldVariant、Passive、compatibility、oracle；按实体稳定 ID 应用声明过的 `update` patch，拒绝不在合法矩阵中的组合及未声明的字段变化，再交给现有 Packet 编译器。私有计划保留七份源资产哈希、相反 Need Set 和统一公开任务规则；发布投影只允许公开规则与图片 Packet，不输出 oracle/World。
- A（已批准内容）和 B（陈旧内容）的 GT 分别为空与 `restore_file_content`；两者被标为 `active_required`，查询前列表不能显示字节。正式形状计划重新在同一设备基线上各采一次，`runtime/gui-files-formal-{a,b}-20260925/` 均完成 seed、按帧、EOF 和复位机检，收据 `pending_review`。两张图中路径、文件名、17 B、10 AM 和 10:00 状态栏一致；原 PNG 哈希不同，仍需图像差异/系统噪声复核。
- GUI 定向回归 19 项通过。此处只是**单簇受限正式形状试点**：尚未接入可执行的 ≤6 Search/Need 验证器，也不覆盖四个样例簇全矩阵或人工签署，因此不能称为正式 G3/G4/G5 Gold。

## 2026-09-25 · G0/G3 GT 派生与源资产快照加固

- `ClusterBase.goal` 现在持有批准内容；编译器从解析后的 World 实际字节派生应有 Need Set，并拒绝与 oracle 不符的标注。公开任务规则也由同一 goal/路径生成，避免人工重复填写造成冲突。
- 每次正式形状采集将七份源 JSON 原字节存入私有 `private-assets/`，采集前核对哈希；发布前复验快照哈希。此前两份没有源快照的试点收据不得用于新版发布门槛。
- 以当前资产重新实采 `runtime/gui-files-formal-{a,b}-snapshotted-20260925/`：两 World 均 `pending_review`、七份源快照齐全、EOF 与复位机检通过；文件字节哈希和相反 Need Set 一致。GUI 定向回归 19 项通过。原图仍需逐字段人工审核及公开投影/主动查询端到端验证。

## 2026-09-25 · 第一轮中途审查：资产引用边界

- 作者 `fixture_ref` 与正式 manifest 的六项文件引用现在必须留在各自资产目录内；`..` 或符号链接逃逸在编译阶段拒绝，避免私有采集计划读取目录外文件。
- 全量后端测试 127 项通过；相关 GUI 脚本编译、正式 A/B 计划编译通过。测试仍有原有资源关闭警告，未影响结果。当前仍未到蓝图要求的最终多轮综合审查阶段。

## 2026-09-25 · B7 私有源快照与发布门槛回归

- 发布器补回归：缺失或被改写的正式源资产快照一律拒绝；源快照完整时才进入原有逐帧人工审核与图片白名单流程。
- 对实际 `gui-files-formal-a-snapshotted` 收据尝试发布，按预期因 `human visibility review is incomplete` 拒绝，未生成 release；不能把我对图片的检查冒充具名人工签署。

## 2026-09-25 · 第二轮中途审查：变体净变化与失败收据

- World patch 的声明变化集现在按**最终解析后的 World 对比 ClusterBase**计算；先改再改回的字段不会被误算为真实反事实变化。回归覆盖回滚式 patch。
- 正式源资产在采集前变动时会留下 `capture_failed` 收据和明确原因，而非只留下半成品目录。用故意错误的 manifest 哈希验证得到 `capture_failed / reset_verified=false / formal source changed before capture`，未触碰设备。

## 2026-09-25 · G3 图片级同流复核

- 对最新两份 `snapshotted` 正式形状试点原 PNG 做逐行 PNG 反滤波后像素比较：差异仅在画面第 49–81 行的状态栏系统图标，共 33 行；第 82–2400 行逐像素一致。A/B 的目录、文件名、17 B、10 AM 和整块 App 页面一致，文件字节哈希与 Need Set 相反。此机检仍不能代替人工逐字段复核。

## 2026-09-25 · 蓝图门槛现况

| 门槛 | 当前状态 | 下一项可验证交付 |
|---|---|---|
| B0/B2/B3 | Calendar/Mail/Clock/Contacts/Files 和 extension 局部视图有受限实机试点；Contacts 同名电话/邮箱可读；正式四簇的完整字段与页面矩阵未闭合 | Calendar 只读详情、Mail 真实收件、Clock 日期/重复、Contacts 更新来源/其他字段、Files 多版本/正文逐项验收 |
| B3a | Mail APK 无源码且原生 heads-up 未实现；App 内 popup 不能替代 | 取得真实来源 App 源码后增加通知渠道/权限/发布握手并实采 |
| B1/B4/B5 | 私有 Packet、单设备顺序 Runner、原 PNG/哈希/收据和 Files 单簇正式资产形状已实采 | 将首批四簇真实 World 与 Passive 资产接入，按事件顺序重放 |
| B6 | 权威回读、EOF、复位、遮挡/时间机检与 A/B 像素对照已做；全部图片未具名人工签署 | 审核人逐帧签署可读字段、时间、来源和遮挡 |
| B7/G5 | 发布器会拒绝未审核图片；还没有正式 release | 人审后产出公开白名单和版本化 QA/收据包并验证回放字节一致 |
| G0–G3 | 文件可用性单簇 A/B 已有合法矩阵、相反 Need、真实截图与不同 W* 回读；内部离线 Search ≤6/Need 试点已执行，独立模型服务与人工审核未闭合 | 将查询宿主与正式审核发布包隔离接通，再扩正式样例簇 |
| G4 | 尚无 8–16 条跨机制候选及按组划分/拒收统计 | 在各参与 App 能力验收后建立候选集与独立 GUI Track QA |

现阶段**不得宣称全部蓝图需求完成**，因此最终多轮综合审查尚未开始；以上两轮是中途缺陷复查。

## 2026-09-25 · 当前验证快照

- 全量后端测试 `127` 项通过；GUI 脚本编译、能力表/Schema JSON 解析及启动/复位脚本语法检查通过。测试仅见已有资源关闭警告。
- 发布白名单回归覆盖 `public_rule` 可见而 `text_projection`、实体绑定和 oracle 私有答案不可见；正式源快照缺失/变更会拒绝发布。真实正式形状收据仍因人工审核未完成而被拒绝。

## 2026-09-25 · B0 Calendar/Clock 剩余能力设备审计

- Fossify Calendar 的 `ACTION_VIEW content://com.android.calendar/events/<id>` 实测只打开月视图，没有直达只读事项详情；当前稳定路径仍是搜索唯一标题后进入编辑页，不能将其改名为只读详情能力。
- DeskClock 数据库含显式 `alarm_instances(year,month,day,...)`，当前 `alarm_templates` 一次性试点只有时分与启用位，没有目标日期读回；需先验证实例生成/重排和真实卡片日期语义，不能从模板小时推断目标日期已覆盖。

- 设备审计结束后重新加载 `init_state` 并停止 `gui-pilot` 容器；后续实采需重新启动模拟器与 Controller。所有已采 PNG/收据保存在各自 `runtime/gui-*` 目录。

## 2026-09-25 · B7/G5 待审核发布包与重放核验

- `scripts/gui_publish.py` 在审核通过后会把模型可见的 `manifest.json/images/manifest.sha256` 写入公开目录，同时把原始收据、私有计划、源资产快照及 `qa.json` 写入并列的 `.private` 目录；公开目录不含 World/GT/作者计划。复制后逐图核对 SHA-256，最后才写完成标志 `manifest.sha256`。
- 新增 `--verify RELEASE_DIR` 重放校验：复验公开 Manifest、每张 PNG、私有收据/计划、源快照及帧绑定。定向回归用审核通过的模拟收据发布后通过，再篡改图片时按预期拒绝。
- 重放校验也拒绝公开目录出现计划外文件或符号链接；回归验证额外 `secret.txt` 会使 release 无效，避免模型从图片目录旁读到意外私有内容。
- 真实采集的两份 Files A/B 仍是 `pending_review`，没有写入人工签名，也未产出正式 release；此步完成的是发布与重放机制，不等于 G5 冻结验收。

## 2026-09-25 · B2/B3 Contacts 同名邮箱详情试点

- Contacts 适配器现在可在同一 raw contact 中注入/独立回读邮箱；计划若含邮箱，必须把 `email` 列为像素可见断言，不能只审姓名和电话。邮箱限于可安全注入的 ASCII 地址格式。
- `datasets/gui-contacts-email-pilot/` 的两人同名，电话与邮箱各不相同。`runtime/gui-contacts-email-pilot-20260925/` 实机收据 `pending_review`、`reset_verified=true`；目标 `person-b` 的原 PNG 同屏可读 `GuiPilotTwin`、`555-0112`、`target@example.test` 和 10:00，注入/EOF 权威 ID 保持 22/23 分离，复位无残留。
- GUI 定向回归 20 项通过。此为受限字段试点，仍未覆盖完整 contact_update 场景的更新来源、修改动作与 Need 验证，未具名人工审批。

## 2026-09-25 · B2/B3 Files 正文实机试点

- `docreader_content` 现从 Files 的 Download 目录按唯一文件名打开真实文件，经 Android 解析器进入 OpenDocument Reader；设备文件的 SHA-256 和修改时间在导航前、EOF 均独立回读。阅读器加载时会短暂出现 `page_view`，稳定后该节点消失，因此就绪条件改为阅读器界面树中唯一的正文文本节点，避免把加载页当成可见正文。
- `datasets/gui-docreader-pilot/` 的 `GuiPilotContentV1` 在 `runtime/gui-docreader-pilot-v2-20260925/` 完成原始 1080×2400 PNG 实采。收据为 `pending_review`，`reset_verified=true`；画面可读正文与 10:00，文件 SHA-256 为 `4f87dcbba1b4f9e4b9215f95b513f91af23eb9b56055a511104ae9777112269d`。首次实采因等待短暂加载节点失败，保留 `runtime/gui-docreader-pilot-20260925/` 失败收据；修正后重采通过。
- GUI 定向回归 21 项通过。此步只验收短单行纯文本阅读；正文图片仍待具名人工审核，文件历史版本、来源和完整可用性场景未闭合，不能计作完整 Files 簇 Gold。
- 全量后端回归 129 项通过；仍有原有数据库/文件关闭 `ResourceWarning`，未出现测试失败。
- 采后 `init_state` 已恢复并验证目标文件消失，`gui-pilot` 容器随后停止；下一次实采须重新启动设备。

## 2026-09-25 · G3 Files 冻结世界 Search/Need 执行试点

- 新增 `scripts/gui_files_search.py`：仅从正式形状采集计划、七份源快照与 EOF 文件哈希一致的私有收据建立只读会话。公开查询先 `files.list_download` 获得不含正文的文件列表和本次会话 `f001` 引用，再 `files.read_text` 取得冻结正文；非法、未解锁和参数错误查询均计入最多六次预算，证据 ID 只从本次 Ledger 产生。作者侧 `finish/grade` 将公开路径目标映射到私有 oracle Need Set，校验引用与正文取证，不向查询结果返回 GT。
- 用实际 `runtime/gui-files-formal-{a,b}-snapshotted-20260925/` 收据执行同样两步查询：列表投影一致，第二步分别得到 `GuiPilotContentV1` 与 `GuiPilotContentV2`。合成回归覆盖相反 Need、先读未解锁、六次预算与 EOF 哈希篡改拒绝，共 2 项通过。
- 这是**内部离线试点**；两组截图仍待具名人工审核，尚未向模型提供独立隔离的查询服务，未接入其余三簇或正式 GUI Track 评分，因此不计作 G3/G4/G5 全部通过。
- 补充 JSONL 本地查询宿主：标准输出只给允许动作、查询响应和提交 ACK，私有 Ledger/Need 评分另写指定结果文件；实际 B World 两次查询加最终提交得到 `need_set_correct/evidence_refs_valid/decision_grounded=true`。列表完整性仅声称 `episode_declared_Download_files` 范围，不把模拟器基线中的全部 Download 文件误报为已完整列出。全量回归 131 项通过（既有资源关闭警告仍在）。
- 查询宿主的非法 JSON/非对象请求也按一次无效 Search 消耗预算，避免免费反复探测；JSONL 定向回归验证第三次有效取证的 `e003` 和评分只落在私有文件。该定向回归现为 3 项通过。
- A/B 的首个列表查询响应（除本次会话证据 ID）在回归中逐字段相等；只有第二步正文查询分支。此检查对应查询前同流，不能替代图片人工审核。

## 2026-09-25 · B0 四簇逐表面缺口审计

- 新增 `contracts/gui-scenario-audit.json`，按正式方案的 `reminder_coverage`、`appointment_sync`、`file_availability`、`contact_update` 列出 11 个候选来源/目标表面。每项明确正式状态 `missing`、可复用的受限 `pilot_view`（若有）和还缺的权威/日期/版本/身份/通知/人工审核证据；Messages 没有适配器，未误列为可采。
- JSON 解析与四个簇 ID、9 个试点视图对能力表的引用核对通过。此审计用于选择正式 Passive 后逐项关闭缺口；现有试点不能自动把这些表面升级为 `supported`，也不代表所有候选表面必须同时纳入同一 Episode。
- 本轮全量后端回归 132 项通过；仍只有既有资源关闭 `ResourceWarning`。最终多轮综合审查应在全部蓝图门槛、人工审核与正式发布证据齐全后开始。

## 2026-09-25 · G0 appointment_sync 正式形状 A/B 编译

- 新增 `datasets/gui-appointment-formal-pilot/` 的七层资产：相同的 Mail 更新正文和公开 Passive，Calendar A 已同步、B 保留旧时间；合法矩阵只允许这两个组合。`gui_formal.py` 现在从 `sync_event_time` goal、真实 Mail 来源字段与 Calendar World 派生相反 `update_calendar_event` Need，并拒绝发件人/目标时间与来源正文冲突。Calendar 作为被动阶段不显示的查询目标可进入正式计划，普通作者计划仍拒绝未声明来源 App。
- A/B 计划编译和公开同流比较通过，Calendar 仅允许 `start_at/end_at` 两字段变化；定向契约回归 15 项通过。此时只有资产/编译证据，尚未在设备实采，不能升格为场景 Gold。

## 2026-09-25 · 蓝图人工审核门槛修订与 B6 OCR 初步闭环

- 已核对移入本工程的新版蓝图：GUI Track **仅机器审核**；缺规则、阈值、像素区域、可追溯证据的帧只能拒收。旧发布器要求 `reviewer/reviewed_at`，必须拆除；旧 `pending_review` 收据不能靠人工签名补过。
- Capture Runner 新增逐帧私有 UI XML 快照及 SHA-256 绑定；新增 `scripts/gui_ocr.cjs`、固定 SHA-256 的英语 OCR 模型、`scripts/gui_machine_qa.py`。首个支持的 `mail_detail` 规则从 XML 找每个字段的唯一屏幕区域，在**原始 PNG** 对主题、发件人、日期、正文及状态栏时间逐区 OCR，要求文本匹配且置信度 ≥80，写出规则/模型版本、区域、识别值、阈值和失败原因。其他视图暂为 `unsupported`，不能发布。
- `runtime/gui-appointment-formal-machine-b-20260925/` 的 Mail 原图机器收据 `pass`：五个区域置信度分别 89/96/96/90/95；首次时间区域过宽误含图标而拒收，固定为只覆盖时钟的区域后重跑通过。该收据尚未接入发布器，也未完成 A/B 同流和正式 QA 全门槛。
- 初版 A/B Mail 图发现空 `senderLogo` 触发每次启动随机头像色，同一个 A 重采也换色；旧图只保留诊断。正式试点资产改用内嵌固定头像像素，随后从相同基线重采 `runtime/gui-appointment-formal-machine-{a,b}-20260925/`。两条收据均 `pending_review`、EOF Calendar 权威时间分别 11:30 与 10:30、复位通过；两张 1080×2400 原 PNG 的 **App 区域逐像素相同**，差异仅在状态栏第 49–83 行系统图标（RGB 差异框 `x=219..966, y=49..83`）。两条 Mail OCR 机器收据均 `pass`。这是查询前同流的强证据，但尚未满足全簇能力门槛。

## 2026-09-25 · B7 机器 QA 发布器首个实机包

- `gui_publish.py` 删除人工 `reviewer/reviewed_at/visible_fields` 放行条件，只接受与当前原图重新运行 OCR 结果完全一致、状态 `pass` 的 `qa-machine.json`；私有发布包保留机器规则/模型哈希、逐帧区域和测值，公开目录继续只含 Manifest 与原 PNG。`verify_release` 复验机器 QA 哈希、帧绑定、图片字节和白名单。
- 实际 `runtime/gui-appointment-formal-machine-{a,b}-20260925/` 各自生成 `runtime/gui-appointment-release-{a,b}-v1/` 和并列 `.private/`，`--verify` 均通过。两个公开规则、来源与时间一致，随机图片名不含 World 标签；原 PNG 哈希不同，对照只允许 App 决定性区域等价。此为**单视图机器验收发布试点**，不是四簇完整 Gold。
- 发布回归已改成真实设备 Mail 原图与 UI 快照：即使给帧手填 `reviewer=human`，缺机器 QA 仍拒绝；伪造 OCR 文本、错误 Android 时钟、篡改发布 PNG、公开目录夹带私有文件、改写私有机器收据均被拒。全量后端 133 项通过（既有资源关闭警告仍在）。`tesseract.js@7.0.0` 已锁定到工程依赖，模型 SHA-256 固定为 `5dc5d8d640a212c9d6184921ba103b186f50e0fed9ee716c53e6b312b400d747`。

## 2026-09-25 · G3 反事实图片机器同流收据

- 新增 `scripts/gui_pair_qa.py`：先用编译计划验证查询前公开内容同流，再逐行反滤波原始 PNG 并比较 RGB 像素；固定 1080×2400 设备把第 132–2336 行作为 App 决定性区域，系统状态栏差异单独记录。文件哈希、packet/frame/source/时间和两边机器 QA 状态均先核对，App 区域任一行变化就 `reject`。
- `runtime/gui-appointment-pair-qa-20260925.json` 机检 `pass`：A/B 两张图仅第 49–83 行有 35 行系统图标差异，App 区域 0 行差异。该收据和每帧 OCR/EOF 相反 Calendar 回读一起构成此受限试点的 G3 证据，尚未覆盖完整 appointment_sync 场景矩阵。

## 2026-09-25 · B6/B7 Files 列表机器验收与发布

- 机器 QA 规则扩至 `download_file` 与 `docreader_content`，当前发布规则升为 `gui-pixel-ocr-v2`，旧 Mail v1 包仍可按原版本复验。Files 列表文件名在原始字号 OCR 首次只有置信度 32 且把点号读成空格，机器按规则**拒收**；对 UI 唯一节点对应的原 PNG 区域做 3 倍无损最近邻放大后，重新 OCR 得 `GuiPilotProof.txt`、置信度 89，目录/状态栏分别 96/96。放大只供机器识别，公开原 PNG 不变。
- 重新实采 `runtime/gui-files-formal-machine-{a,b}-20260925/`，两边文件哈希及相反 Need Set/EOF/复位仍成立；逐帧 OCR 和 `runtime/gui-files-pair-qa-20260925.json` 均为 `pass`。两份只含公开图片与规则的 `runtime/gui-files-release-{a,b}-v1/` 已由机器发布器生成，`--verify` 均通过。此证据只覆盖受限单文件列表 A/B，不能代替完整文件可用性簇。
- `runtime/gui-docreader-machine-20260925/` 也重采了真实文件正文并保存 UI 区域快照；正文 `GuiPilotContentV1` 与 10:00 的原图 OCR 置信度分别 88/95，机器收据 `pass`，文件哈希/EOF/复位通过。`runtime/gui-docreader-release-v1/` 已发布且重放校验通过。该试点不包含多版本或损坏文件场景。

## 2026-09-25 · 修订后机器审核语义与对照收据加固

- 依据修订蓝图 §1、B6/B7、§7，后续采集成功状态改称 `capture_complete`，表示 seed、回读、截图、EOF、复位完成；只有独立 `qa-machine.json` 为 `pass` 才可发布。既有 `pending_review` 只作为旧收据的兼容状态，不能由人工字段放行。
- 四簇能力审计与 Calendar 能力说明中的“人工审核”后续要求已改成机器像素验收。文档前段保留原始历史记录，当前有效门槛以本节和修订蓝图为准。
- A/B 像素同流检查现在对两边原图重新运行 OCR 机器验收并核对收据，拒绝仅改写 `qa-machine.json` 伪造通过。发布器保留旧试点包复验兼容；此项不提升四簇完整性状态。
- 全量后端回归 134 项通过；旧 appointment A/B 发布包重放校验通过。仍见既有资源关闭警告。

## 2026-09-25 · B6/B7 Contacts 同名身份机器验收

- `contact_detail` 加入 `gui-pixel-ocr-v3`：从原始 UI 快照定位唯一大标题、电话和邮箱节点；电话先用数字与权威值绑定，再按屏幕实际格式（`555-0112`）在原 PNG 区域 OCR。状态栏时钟仍独立验收，阈值均为 80；旧 v1/v2 发布包保持可复验。
- 新采 `runtime/gui-contacts-machine-20260925/` 返回 `capture_complete` 且复位通过；机器 OCR 对姓名、电话、邮箱、10:00 时钟分别为 86/95/88/96，均 `pass`。`runtime/gui-contacts-release-v1/` 已发布并重放校验。此证据仅为同名联系人的单条详情，不含 Contact Update 来源和修改闭环。
- 回归增加电话号码格式与权威数字绑定的检查；全量后端 135 项通过（既有资源关闭警告）。修正旧 v1 OCR 收据缺省 `scale=1` 的对照兼容后，appointment A/B 同流机器复验重新通过；Files A/B 也通过重验。

## 2026-09-25 · G2 Calendar→Mail 连续流机器验收

- `event_editor` 加入 `gui-pixel-ocr-v4`，要求标题、开始/结束时刻、地点、说明、提醒以及状态栏时间逐区 OCR；之前的规则版本仍可复验。Fossify 页面是编辑页，`source` 和事件日期尚未列为像素断言，因此不能据此声称来源或目标日期已经验收。
- 新采 `runtime/gui-calendar-mail-machine-20260925/` 在**同一次基线恢复**后按 10:00 Calendar、10:05 Mail 连续完成两帧，EOF 冻结/独立回读与最终复位均通过，收据状态 `capture_complete`。Calendar 七项 OCR 置信度 89–96，Mail 五项 89–96，两帧机器状态均 `pass`。
- `runtime/gui-calendar-mail-release-v1/` 已发布两张原 PNG，公开白名单与私有机器 QA 绑定，`--verify` 通过。它证明 G2 的受限双 App 连续流闭环；邮件是真实 App 详情而非原生到达通知，仍缺正式簇的事件重放和全字段可见性。

## 2026-09-25 · 当前门槛快照（取代前文历史待办表）

| 门槛 | 当前可核验证据 | 未闭合部分 |
|---|---|---|
| B0/B1 | 四簇 11 个候选表面审计；Files 与 Appointment 正式形状 A/B 编译和合法矩阵 | Reminder、Contact 的正式 World/Passive/Need 资产及所有选定表面的 `supported` 结论 |
| B2/B3 | Calendar、Mail、Contacts、Files/Reader 受限真实 seed/readback/GUI/复位；Clock 卡片旧试点 | Calendar 只读详情/来源、Clock 日期实例、Contacts 更新来源、Files 完整版本/损坏矩阵、真实到达通知 |
| B4/B5/B6 | 单 Episode 顺序采集、原 PNG/哈希/UI 快照；Mail、Files、Reader、Contacts、Calendar 编辑页及中文 extension 弹窗/内容页 OCR 机器收据 | Clock 与任何选中通知表面的像素规则；日期、遮挡与来源的完整断言范围 |
| B7/G2/G3/G5 | 机器发布/重放；Calendar→Mail 两帧连续流；Files 与 Appointment 受限 A/B 同流 | 正式四簇全部参与 App 验收、查询服务隔离、每条 Track 独立 GT/QA、完整冻结发布 |
| G4 | 当前仅单视图/受限试点包 | 8–16 条跨机制候选、拒收计数、按组划分与逐条机器 QA |

上述发布包标记为试点，**没有任何一个完整场景簇被宣称为纯 GUI Gold**。按修订蓝图，剩余工作须继续使用机器验收，不得回退到人工签署。全部需求完成后才开始用户要求的多轮综合审查。

- 本轮全量后端回归 135 项通过；发布包 Calendar→Mail、Contacts、Files B 重放校验通过，定向 GUI 管线 9 项通过。实采后复位收据已验证，`gui-pilot` 容器已停止。

## 2026-09-25 · G3 Appointment 冻结 Calendar Search/Need 试点

- 新增 `scripts/gui_appointment_search.py`，仅接受**已机器发布且重放校验通过**的 Appointment release，再从私有包核对 EOF 冻结、复位、Calendar 实际数据库回读与对应 World。公开动作限 `calendar.find_event`、`calendar.read_event`，六次预算；回读只给事件公开引用、标题和当前起止时间，私有 oracle/评分仅写指定结果文件。
- 对现有 A/B 机器发布包各执行同样两步 Search：A 回读 11:30–12:00，B 回读 10:30–11:00；提交 Need 分别为空和 `update_calendar_event`，两边私有评分的 Need、证据引用和取证充分性均为 `true`。单元回归覆盖先读未解锁、正确取证与篡改 EOF 回读拒绝。
- 这是受限 `appointment_sync` 试点的冻结查询闭环；Mail 仍是预置已读详情，真实收件/发件来源认证、通知和完整同步历史未完成，不能升格为完整簇 Gold。
- 本轮全量后端回归 136 项通过。

## 2026-09-25 · Files 查询入口改为机器发布包

- `gui_files_search.py` 的公开查询入口现在必须给 `--release-dir`，先执行发布包重放校验，再从并列私有包读取冻结 World/权威回读；不再接受未通过像素机器验收的原始采集目录作为查询输入。
- 对已有 Files A/B 机器发布包复跑两步查询：相同列表先行，再读冻结正文；A/B 私有 Need、证据引用、取证充分性评分均通过。回归改用模拟发布校验来测试查询协议，实际命令则使用真实发布包验证。
- 全量后端回归 136 项通过；仅有既有资源关闭警告。

## 2026-09-25 · B6 中文 extension 页面机器 OCR

- 固定 `chi_sim` OCR 模型 SHA-256 `a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730`，机器收据升为 `gui-pixel-ocr-v5` 并逐帧标注语言/模型哈希。中文单行区域采用 Tesseract 单行模式；先前默认模式把真实弹窗正文拆成竖列且低置信度，明确保留这个失败原因，修正后才验收。
- 新采 `runtime/gui-extension-machine-a-20260925/`：同一 Episode 中先截嘟嘟 App 内弹窗、再截超威内容详情；原 PNG 的标题/正文、详情标题/副标题/正文和两次状态时钟均由中文 OCR 按区域验收，机器状态 `pass`。`runtime/gui-extension-release-a-v1/` 已发布并重放校验。
- 这是**App 内弹窗**和内容页，不是 Android 顶部系统通知；不能用该收据关闭 B3a。正在以同规则重采 B World 以核对隐藏事实不会影响公开流。
- B World `runtime/gui-extension-machine-b-20260925/` 同样 `capture_complete`、EOF 与复位通过，两帧中文 OCR 均 `pass`；`runtime/gui-extension-release-b-v1/` 已机器发布。A/B 图片对照 `runtime/gui-extension-pair-qa-20260925.json` 为 `pass`：两帧 App 区域均 0 行差异，只有状态栏 21/15 行系统图标差异；隐藏的 favorite 分支没有泄露进公开像素。两边仍是受限 extension 试点，不是正式四簇资产。
- 中文单行识别加入真实截图回归，防止恢复默认版式后误把正文拆列；全量后端 137 项通过。旧 Calendar→Mail 与新 extension A/B 发布包均重放校验通过；实采复位后 `gui-pilot` 容器已停止。

## 2026-09-25 · B6/B7 Clock 关闭闹钟卡片机器验收

- 新采 `runtime/gui-clock-machine-20260925/`，设备权威 UUID/时间/关闭位、GUI 目标卡片和复位通过。初次英文 OCR 把整块大号 `10:45 AM` 读成 `10:45.`，置信度 0，机器明确 `reject`；改用 UI 时间节点导出的**数字与 AM 两个原图区域**分别识别，得到 95/96，避免把 UI 文本本身当作可见性证据。
- `enabled=false` 同时要求目标卡片 `onoff` 节点为 unchecked，并在**原始 PNG** 开关区域实测左侧亮度高于右侧至少 50；本次实测差为 156.33。标签/时钟 OCR 为 91/96。机器规则 `gui-pixel-ocr-v6` 状态 `pass`，`runtime/gui-clock-release-v1/` 发布与重放通过。
- 仅验收关闭的一次性卡片视觉状态；`enabled=true` 因没有校准像素规则仍为 `unsupported`。卡片未显示目标日期或真实 alarm instance，因此 Reminder Coverage 正式簇的日期/重复范围仍缺。

## 2026-09-25 · 综合审查第四轮：彻底移除人工待审放行

- 发现 `gui_machine_qa.py`、`gui_publish.py`、`gui_pair_qa.py` 和 `gui_files_search.py` 仍把历史 `pending_review` 当作可接受状态；这会让不具备机器完成收据的旧采集进入发布/查询，违反蓝图“机器审核唯一放行”。
- 已统一要求收据状态严格为 `capture_complete`；`reviewer`、`reviewed_at`、`review_status` 不再提供放行能力。旧 `pending_review` 发布包继续保留作历史诊断，但不可重新发布、反事实验收或查询。
- 新增回归：即使 `qa-machine.json` 已通过，`pending_review` 也必须拒收；全量后端回归 138 项通过。
- 复核发布重放入口后又补上私有 `receipt.json` 的状态/复位检查；即使篡改 `qa.json` 使哈希匹配，`verify_release` 仍拒收 `pending_review`。定向发布管线回归 11 项通过。

## 2026-09-25 · 综合审查第五轮：采集器去除人工审核字段

- `gui_capture.py` 原本仍在每帧写 `review_status=pending`；这虽不参与放行，却继续污染收据契约并留下误用入口。现已删除，采集产物只保留机器可验证的事实、哈希、UI 快照和状态字段。
- 测试夹具同步移除 `reviewer/reviewed_at/review_status`，并保留 `pending_review` 的拒收回归；全量后端回归 138 项通过。

## 2026-09-25 · 综合审查第六轮：工程总验收

- `./scripts/verify.sh` 通过：138 项 Python 回归、共享 Vue 模板、六款 Vite 构建、六款 Uni-app 构建、envelope 字段白名单、隔离扫描及六枚 KU 对齐 APK 校验全部通过。
- 本轮仍不把旧 `pending_review` 历史包升格为发布资产；其状态已被新的发布/重放门禁拒收。设备端缺失的 heads-up、Clock 日期实例和正式四簇覆盖仍按门槛快照保留为未完成。

## 2026-09-25 · 综合审查第七轮：旧发布包重放边界

- 直接对现有历史包执行 `verify_release`：`gui-files-release-a-v1`、`gui-appointment-release-a-v1`、`gui-docreader-release-v1` 因私有收据为 `pending_review` 明确拒收；`gui-extension-release-a-v1` 与 `gui-clock-release-v1` 的 `capture_complete` 包重放通过。
- 这证明门禁不是只限制新发布命令，而是对已存在发布包的重放入口同样生效；未重写历史包，避免以当前规则补签旧图。

## 2026-09-25 · Appointment A 重采尝试：环境拒收

- 按新 `capture_complete` 门禁对 `manifest-a.json` 重采两次；两次均在 `reset-instance.sh` 阶段因 `gui-pilot` Controller 的 `127.0.0.1:36243` 端口失活而得到 `capture_failed`，`reset_verified=false`，没有生成可发布图片。
- 尝试用同一实例参数恢复 Controller 后进程立即退出，故不把这次环境故障计为 GUI 功能失败，也不把旧 `pending_review` 图片补签为完成；后续需要先恢复稳定设备 Controller，再继续 Appointment/Files 重采。

## 2026-09-25 · Appointment A/B 重新机器发布与冻结查询闭环

- 先恢复临时 Controller 会话，再对正式 `gui-appointment-formal-pilot` 的 A/B World 重采：`runtime/gui-appointment-machine-{a,b}-20260925-v4/` 均为 `capture_complete`，seed、Mail/Calendar 独立回读、EOF 冻结和复位均通过。
- 两帧原图均通过 `gui-pixel-ocr-v6`；分别生成并重放校验 `runtime/gui-appointment-release-a-v2/`、`runtime/gui-appointment-release-b-v2/`。`runtime/gui-appointment-pair-qa-v2.json` 为 `pass`，查询前公开画面同流且 A/B App 决定性区域无泄漏。
- `gui_appointment_search.py` 对 A/B 各执行相同两步 `calendar.find_event` → `calendar.read_event`，查询预算各使用 2/6；私有评分均为 `need_set_correct=true`、`evidence_refs_valid=true`、`decision_grounded=true`。这是 Appointment 受限正式试点的机器闭环，不升格为完整四簇 Gold。

## 2026-09-25 · Files A/B 重新机器发布与冻结查询闭环

- 使用持久 Controller 会话重采 `runtime/gui-files-machine-{a,b}-20260925-v2/`；两边均为 `capture_complete`，Files seed、真实文件哈希回读、Download 列表、原 PNG、EOF 冻结和复位通过。
- 两边均通过原图 `gui-pixel-ocr-v6`，发布并重放校验 `runtime/gui-files-release-a-v2/`、`runtime/gui-files-release-b-v2/`；`runtime/gui-files-pair-qa-v2.json` 为 `pass`，查询前列表公开字段完全同流，正文差异只在 EOF 后读取。
- A/B 冻结查询均使用相同两步 `files.list_download` → `files.read_text`，预算各使用 2/6；私有评分均为 `need_set_correct=true`、`evidence_refs_valid=true`、`decision_grounded=true`。这是单文件可用性受限试点，不替代完整版本/损坏矩阵。

## 2026-09-25 · 新发布包最终重放核验

- 直接重放核验四个新包：Appointment A/B 与 Files A/B 的 manifest、私有 QA、机器规则/模型、帧绑定和 PNG 哈希全部通过；四条采集收据均为 `capture_complete` 且 `reset_verified=true`。
- 设备 Controller 已停止；没有留下运行中的采集服务或未复位的本轮实例。

## 2026-09-25 · Reader 重采复核：设备表面不稳定

- 尝试按同一机器门禁重采 `docreader_content`，收据为 `capture_failed`，原因是 DocumentsUI 搜索结果中的文件标题未达到唯一可导航条件；随后设备 ADB 连接也从 `127.0.0.1:5556` 消失。
- 不把该次失败当作 Reader 发布证据；已有 Reader 机器图仍需在稳定设备表面下重新采集并通过 `gui-pixel-ocr-v6` 后才能发布。

## 2026-09-25 · Reader 导航修复与机器发布

- Reader 重采失败根因是 DocumentsUI 文件名文本节点存在重复/不稳定的无障碍节点；先按可见屏幕矩形去重，再将文件打开动作改为 `android:id/title` 资源定位，仍保留不同屏幕矩形即拒收。
- 新增回归覆盖同一可见矩形重复节点与隐藏节点；定向管线回归通过。修复后 `runtime/gui-docreader-machine-20260925-v4/` 完成 seed、真实 Reader 页面、原 PNG、EOF、复位和 `capture_complete`。
- 原图 `gui-pixel-ocr-v6` 机器 QA、发布和重放均通过，正式包为 `runtime/gui-docreader-release-v2/`；旧 Reader `pending_review` 包未被改写或补签。

## 2026-09-25 · Reader 导航修复回归收口

- `tap_unique_text` 现在只接受可见节点，并按屏幕矩形去重；DocumentsUI 文件打开改用 `android:id/title` 资源定位，避免同一文件行的重复无障碍节点被误判为多个目标。不同矩形的同名目标仍拒收。
- 修复后的真实 Reader v4 采集、`gui-pixel-ocr-v6` QA、发布和重放均通过；新增定向回归通过。全量 Python 回归现为 139 项通过。

## 2026-09-25 · B3a 系统通知最小实现落地

- 新增 Dudu 原生 `NotificationReceiver`：声明 `POST_NOTIFICATIONS`，创建 Android 高重要性 Notification Channel，并通过真实 `NotificationManager` 发布标题/正文；这条路径不复用 App 内 `.confirm.popup`。
- 新增受 `X-Runner-Key` 保护的 `POST /local/v1/apps/dudu/notification`：校验通知 ID/频道/标题/正文长度，授权通知权限，调用显式 Android Broadcast，并写入 `notification` Passive packet。当前只开放 Dudu，避免未经设备验收冒称六 App 都支持。
- 契约回归后端共 141 项通过；`system_surface.heads_up_notification` 仍保持 `missing`，因为本轮 Gradle 8.2.1 首次下载超时，APK 重编和真实 System UI 横幅 OCR 尚未完成，不能把原生发布能力当作横幅验收证据。

## 2026-09-25 · B3a 横幅采集器与真实证据边界

- 新增 `scripts/gui_notification.py`：只向真实原生 Receiver 发送通知，回到桌面后轮询 System UI 的标题/正文及 Heads-Up 窗口，三者同时满足才保存原始 PNG；超时只写 `reject`，不会退化为通知栏或 App 内弹窗截图。
- 该采集器当前只允许已声明的 Dudu Receiver；能力表仍把 heads-up 标为 `missing`，直到 APK 重编、权限/频道、System UI 横幅和来源包名在设备上全部通过机器验收。

## 2026-09-25 · 通知接口与采集器回归

- 新增通知契约回归覆盖 Receiver、Dudu-only capability scope 和 Controller 路由存在性；后端全量回归现为 142 项通过。
- Gradle 分发包已完成缓存并使用 KU Java 17 构建验证；在真实横幅验收通过前，通知实现状态继续保持“已实现但未设备验收”。

## 2026-09-25 · B3a APK 编译与横幅设备验收结论

- 使用 KU 基线容器内 Java 17 完成 Dudu 原生 APK 编译，确认新 APK 的 `NotificationReceiver` 与 `POST_NOTIFICATIONS` 已打包；本机 Java 11 不能直接运行 Android Gradle Plugin，未修改工程 Java 配置。
- 新 APK 已安装到 `127.0.0.1:5556`；Controller 路由成功授予通知权限并广播真实 Receiver。设备 `dumpsys notification` 可见 Dudu 通知、`importance=4`、高重要性 channel 和 `mIsInterruptive=true`。
- `scripts/gui_notification.py` 仍拒绝发布：System UI 未同时提供可观测标题、正文和 Heads-Up 窗口；没有保存截图、没有生成通过收据，也没有更新 `system_surface.heads_up_notification`。本次失败收据为 `runtime/gui-notification-machine-20260925/receipt.json`，不得当作横幅证据。
- 为真实中断路径补齐 channel 声音、通知默认属性、高优先级和公开可见性后，重新编译并重试仍拒绝；因此能力表继续保持 `missing`，不以代码存在或 Notification Manager 记录替代真实 GUI 验收。

## 2026-09-25 · 多轮综合审查收口

- 静态搜索确认生产发布与查询代码不再以 `pending_review`、`reviewer`、`reviewed_at` 或 `review_status` 放行；仅测试保留 `pending_review` 拒收回归夹具。
- 对现有发布目录逐一执行 `verify_release`：Appointment/Files v2、Reader v2 及既有机器完整包通过；历史 v1 `pending_review` 包按设计拒收，未被补签或改写。
- `python3 -m unittest discover -s tests -p 'test_*.py' -q` 与 `./scripts/verify.sh` 均通过（142 项）；`scripts/verify-apks.sh` 对六个 APK 的哈希、签名、包名及 KU 工具链均通过。
- 本轮 Controller 已停止、实例 PID 残留已清理；通知失败收据仍为 `reject`，能力表和 README 没有把未验收的 Heads-Up 横幅标为完成。

## 2026-09-25 · B3a 干净基线对照与收据加强

- 在恢复 `init_state`、确认新 Dudu APK 和清空旧通知后重新触发通知；Dudu 通知确实进入高重要性 channel，但仍无 `headsUpIsVisible=true` 的 System UI 状态，采集器拒绝且不保存 PNG。
- 同一干净 AVD 上用系统 `cmd notification post` 做对照也没有 Heads-Up，说明当前失败来自 AVD/System UI 呈现条件，不能通过放宽 Dudu 检查器冒充成功。
- `gui_notification.py` 现额外验证来源包名、channel、活动通知 key，并在取消后确认活动通知消失；同时兼容 System UI 服务中的 `headsUpIsVisible=true` 检查。诊断收据为 `runtime/gui-notification-machine-20260925-diag/receipt.json`。
- 新增回归锁定原生通知 `contentIntent` 与上述 Dudu-only 机器契约；`heads_up_notification` 继续保持 `missing`。

## 2026-09-25 · 通知收据改进后的回归

- Dudu Receiver 新增标准 `contentIntent`，重新编译、安装和干净基线实测仍未观察到 Heads-Up；采集器确认通知发布后存在、来源/频道正确，取消后活动通知消失。
- 全量 Python 回归仍为 142 项通过；`./scripts/verify.sh` 重新通过，六个 APK 的哈希、签名、包名和 KU 工具链校验通过。

## 2026-09-25 · 历史横幅证据复核与 Dudu 形态修正

- 用户提供的历史截图与工程内 `runtime/gui-mail-pilot-first-20260924/images/48302f9b9c9375178a03f80055c1d66f.png` 完全属于真实 Android System UI 横幅：来源为 `org.fossify.calendar`，标题 `Gym Session`，正文时间和 `Snooze` 动作均在横幅中可见。对应收据明确记录当时横幅遮挡 Mail 页面，因此该帧被拒收，但它证明系统横幅确实存在。
- 修正结论：当前失败不能再表述为“AVD/System UI 不支持横幅”；旧证据证明表面存在。当前未通过的是 Dudu 来源通知的可重复触发/即时呈现握手，且历史截图没有 Dudu packet 绑定、System UI dump 和来源机器收据，不能直接升格为 B3a 完成证据。
- Dudu 通知改用标准白色 `ic_stat_notification`，并保留事件类别、时间戳、content intent、来源/活动/清理校验；干净基线重试仍拒绝，能力表继续保持 `missing`。

## 2026-09-25 · Calendar 到时提醒复现尝试

- 复用真实 Calendar 适配器，在干净 `init_state` 注入一个已经到提醒时刻的事件（09:30–10:00、10:00 采集），随后完成真实详情页、权威读回、截图和复位；该运行 `runtime/gui-calendar-banner-due-20260925/` 未产生系统提醒横幅。
- 这说明历史 `Gym Session` 横幅来自此前设备中已被 Android Calendar 调度器实际触发的通知，而不是仅由 Calendar 数据库插入即可稳定重现；不能用本次无横幅运行替代旧图，也不能把 Dudu 实验失败归因为 System UI 不存在。

## 2026-09-25 · Calendar 原生通知入口审计

- 设备包审计确认 `org.fossify.calendar` 声明了 `POST_NOTIFICATIONS`、`USE_EXACT_ALARM` 和内部 `RescheduleEventsReceiver`，但没有公开可安全调用的“发布指定通知” Receiver；仅写入 `events.db` 后打开详情页不能证明通知调度器已排程或已发送。
- 因此历史 Calendar 横幅只能作为真实 System UI 表面证据；要形成可发布 B3a 收据，还需要通过 Calendar 自身的合法调度链产生通知，并绑定 packet、来源包名、标题/正文、System UI 状态和取消后的清理结果。当前能力表继续保留 `missing`。

## 2026-09-25 · 默认关键应用 Messages 原生短信试点

- 按当前优先级先保证默认关键应用可用，不再让横幅复现阻塞 Messages 主线；新增 `scripts/gui_messages.py`，通过 Android 模拟器真实 `adb emu sms send` 注入来件短信，不调用 Shell 通知、不绘制 App 内伪横幅。
- 采集器已接入 `messages` 的 seed、Android SMS Provider 权威回读、Google Messages 原生会话列表页面、真实 PNG/UI dump、EOF 不变性和 reset 后残留检查；能力表新增 `conversation_list(sender, body)` pilot。
- 该 pilot 证明的是“真实来件短信 → Messages 页面”的可用闭环；来源联系人归属、联系人更新语义和 Heads-Up 横幅仍未验收，故 `system_surface.heads_up_notification` 继续为 `missing`，Messages 的通知字段标为 `native_sms_unverified`。
- 新增契约回归锁定真实短信传输入口和采集器接线；当前尚未在本轮设备上重采，因为 ADB 模拟器目前无在线设备，不能把静态接线冒充实机收据。
- 本步完成后的全量 Python 回归为 143 项通过，`./scripts/verify.sh` 和六个 APK 校验也通过；真实 Messages 收据待下一次 AVD 在线时再采集。

## 2026-09-25 · Messages 试点 Episode 资产补齐

- 新增 `datasets/gui-messages-pilot/author.json` 与 `fixture.json`，把真实短信输入、来源号码、正文、会话列表可见字段和 `init_state` 基线固定为可重放的结构化 Episode。
- 编译器回归现在实际编译该 Episode，拒绝范围仍包含未声明视图、错误实体类型、时间冲突和不可见字段；本资产尚未标记为机器发布包。首次设备运行确认带 `+86` 的号码不会落入 Provider，已改用模拟器稳定接受的纯数字号码，待重采完成真实 PNG/Provider 收据。
- 设备逐步复现确认正文真实可见；Google Messages 会把 `13800138000` 格式化成 `(380) 013-8000`，适配器已增加严格的后十位数字等价断言，未放宽为任意文本命中。

## 2026-09-25 · Messages 真实设备采集与发布

- 在 `gui-pilot` AVD 上完成真实 `adb emu sms send` → Android SMS Provider 回读 → Google Messages 会话列表 → 原始 PNG/UI dump → EOF 冻结 → reset 清理闭环；收据为 `runtime/gui-messages-machine-20260925/receipt.json`，状态 `capture_complete` 且 `reset_verified=true`。
- 独立 `gui-pixel-ocr-v6` 增加 `conversation_list` 机器规则：号码按实际 UI 格式化区域验收、正文按 `conversation_snippet` 区域验收，并校验来源包为 `com.google.android.apps.messaging`；`qa-machine.json` 为 `pass`。
- 已生成并重放校验 `runtime/gui-messages-release-v1/`，公开 Manifest、PNG 哈希、私有计划/收据/QA 绑定全部通过。该交付证明 Messages 会话列表能力，不把 Heads-Up 横幅或联系人更新语义宣称为已完成。

## 2026-09-25 · G4 GUI 候选子集与拒收统计

- 新增 `contracts/gui-g4-candidates.json`，固定 11 条跨机制 GUI Episode：Appointment A/B、Files A/B、extension A/B、Calendar→Mail、Clock、Contacts、Reader、Messages；世界成对样例保持在同一 split，避免 A/B 跨组泄漏。
- 新增 `scripts/gui_g4_audit.py`，逐条调用发布重放校验，拒绝缺失私有 QA/PNG 哈希/机器验收的候选，并扫描采集收据输出 `rejection_summary`；报告为 `runtime/gui-g4-audit-20260925.json`。
- 这是 G4 候选子集门槛的机器审计，不把 11 条候选误称为四个完整场景簇 Gold；完整场景矩阵、通知横幅和正式 Need/GT 评测隔离仍未闭合。
- 本轮全量 Python 回归为 146 项通过；`./scripts/verify.sh`、六个 APK 校验和 G4 审计均通过，拒收统计为 `capture_failed=10`、`reject=6`。

## 2026-09-25 · contact_update 真实关联 Episode 接线

- 新增 `datasets/gui-contact-update-pilot/`：同一 Episode 先采 Contacts 的姓名/电话/邮箱，再采 Messages 来件；短信以 `sender_phone` 作为 Provider 注入与权威回读键，以 `sender` 作为 Messages 页面联系人标签。
- Messages 契约兼容匿名号码和联系人标签两种合法形态；机器 QA 仍要求唯一 `conversation_name` 区域和正文 `conversation_snippet` 区域，不接受只命中 Provider 或任意文本。
- 该 Episode 已通过计划编译回归，但尚未生成新的真实 PNG/发布包；下一次 AVD 在线时执行设备采集，若 Messages 不读取 Contacts 标签则按真实结果保持受限 pilot，不伪造来源身份。
- 首次设备尝试确认 Contacts 写入成功，但 SMS Provider 回读存在注入后短暂竞态；`seed_message` 现等待最多 10 秒的真实 Provider 行再进入 GUI，超时仍拒收，不把命令返回 `OK` 当成短信已落库。

## 2026-09-25 · contact_update 两帧真实发布

- 在干净 AVD 上完成 Contacts → Messages 连续 Episode：Contacts 的 `GuiPilotTwin / 13800138000 / target@example.test` 与 Messages 的 `GuiPilotTwin / Your contact update is ready.` 均通过权威回读、真实 PNG、EOF、复位和来源前台校验。
- Contacts 电话 OCR 采用原图 3×区域并按数字等价处理系统格式化空格；Messages 号码/联系人标签仍要求唯一 `conversation_name` 区域，正文要求唯一 `conversation_snippet` 区域。两帧 `gui-pixel-ocr-v6` 均 `pass`。
- 已生成并重放校验 `runtime/gui-contact-update-release-v1/`；G4 候选子集扩为 12 条，contact_update 不再只有匿名短信 pilot，但来源更新历史、Mail 替代来源和系统横幅仍未完成。
- 本轮全量 Python 回归为 147 项通过；`./scripts/verify.sh`、六个 APK 校验、contact_update 发布重放和 G4 12 条候选审计均通过。

## 2026-09-25 · contact_update 正式 World/Need 对照资产

- 新增 `datasets/gui-contact-update-formal-pilot/` 的 GlobalBase/ClusterBase、合法 A/B World、公开 Passive、Compatibility 和 Oracle；A 为联系人号码已同步，B 保持旧号码，公开 Messages 联系人标签与正文相同，差异只在 Provider 权威号码。
- `scripts/gui_formal.py` 新增 `sync_contact_identity` 目标，编译时校验 Contacts 与 Messages 的同一来源标签/正文，并生成 A 为空、B 为 `update_contact_phone` 的 Need Set；两份计划只完成正式契约编译，尚未把 A/B 设备帧发布为 G3 Gold。

## 2026-09-25 · contact_update 正式契约修正与回归

- 修正正式 A/B World：两边都使用同一来件号码 `13800138000`，仅 Contacts Provider 记录为已同步/旧号码；这样 Messages 的公开联系人标签与正文、来件号码不会泄漏 World，B 仍正确产生 `update_contact_phone` Need。
- 新增正式 contact_update 合同回归：验证 A/B 公开等价、相反 Need Set，以及来源标签篡改拒绝；正式 `manifest-a/b.json` 编译断言通过。
- 定向合同回归 16 项、全量 Python 回归 148 项均通过；本步未把正式 A/B 设备帧冒充为已发布 Gold，系统 Heads-Up 横幅仍保持 `missing`，不阻塞默认 Messages 会话列表能力。

## 2026-09-25 · Controller 持久化与重复故障修正

- 根因确认：`start-instance.sh` 的后台 Controller 在当前执行会话结束时会被宿主清理，且原脚本只等待 stdout、不验证 HTTP 存活；不是业务请求异常。新增外挂 `scripts/controller-supervisor.sh`，异常退出自动重启，固定端口避免重启后客户端仍指向旧端口。
- `start-instance.sh` 现在为动态端口先分配固定端口、启动后轮询 `/health`，启动失败立即报错；`stop-instance.sh` 同时支持安全回收 supervisor，并保留实例 ID 校验，避免误杀其他实例。
- 增加 supervisor 重启自测和静态回归；不修改 KU 模拟器原始文件。真实采集仍可用前台 Controller 作为宿主清理时的兜底方式。

## 2026-09-25 · contact_update 正式 A/B 真实闭环

- 首次 B 采集真实暴露：联系人旧号码会让 Messages 显示号码而非联系人名，证明不能仅靠契约假设公开等价；未把该失败收据发布。
- 正式 World 改为外挂隐藏的同名号码锚点：A 由目标联系人命中 `13800138000`，B 由私有锚点命中同一来件号码；目标联系人仍保持同步/旧号码差异，B 的 `update_contact_phone` Need 不变。该记录只用于 Android ContactsProvider 的真实解析，不进入公开 Passive。
- 重新采集 `runtime/gui-contact-update-formal-machine-{a,b}-20260925-v3/`：两边均 `capture_complete`、权威 Contacts/SMS 回读、EOF 冻结、复位和 `gui-pixel-ocr-v6` 机器 QA 通过。关键 Messages 联系人名和正文一致。
- 对 Messages 动态轮换的搜索栏提示词（`Search places`/`Search conversations`/`Search images & videos`）增加资源 ID 白名单，只忽略该 App chrome 区域；正文、联系人名、前台包和其他 App 区域仍严格逐像素检查。`runtime/gui-contact-update-formal-pair-qa-20260925-v3.json` 为 `pass`。
- 已生成并重放 `runtime/gui-contact-update-formal-release-{a,b}-20260925-v1/`；两包各 1 packet/1 frame，旧 `capture_failed` 和旧 pending 目录未被改写。系统 Heads-Up 仍保持 `missing`，不阻塞默认 Messages 会话列表交付。

## 2026-09-25 · 蓝图修订口径复核与 Controller 重启密钥修正

- 按当前蓝图 §5.1 重核：P0 为 B0–B5，P1 为 B6/B7、完整机器证据与 G3/G4/G5，B3a 系统顶部横幅明确为 P2；本工程不把横幅缺失当作 Messages/App 屏阻塞，也不把任何 `pending/reject` 收据当发布证据。
- P/A 继续作为作者侧/私有 Track 信息保留：正式 contact_update Oracle 为 `active_required`，不会写入公开 manifest 或暴露给 Agent；公开包只包含机器通过的 GUI 图片、来源、时间和 frame 哈希。
- 发现并修正 Controller supervisor 的重启边界：固定端口仍不足以保持正在运行的 Runner 授权，`local-controller/server.py` 现在从已有实例状态复用合法 `runner_key`；新增 Controller 重启密钥回归，旧客户端在自动重启后仍可继续请求。
- 当前修订只改 `simulator-extension` 外挂代码、资产和收据，不修改 KU 模拟器原始文件；修正后的全量 Python 回归为 150 项通过，`./scripts/verify.sh`、六个 APK 校验、正式包重放、A/B 对照和 G4 审计均通过。采集完成后已停止 Controller 与 AVD，无残留运行服务。
- 综合审查修正 G4 清单遗漏：`contracts/gui-g4-candidates.json` 现在登记正式 contact_update A/B 两个发布包，固定候选数为 14，报告 `runtime/gui-g4-audit-20260925-final.json` 与合同一致；拒收统计仍公开保留，不因补登记而掩盖失败收据。

## 2026-09-25 · contact_update EOF 查询隔离闭环

- 新增 `scripts/gui_contact_search.py`：只接受已机器发布、冻结且复位通过的正式 contact_update 包；公开查询仅提供 Contacts/Messages 的固定动作和证据，不返回 Oracle、World/semantic ref 或私有路径。
- A/B 使用同一 `contacts.find_contact → contacts.read_contact → messages.find_conversation → messages.read_conversation` 路线，预算使用 4/6；真实发布包实际运行结果均为 `need_set_correct=true`、`evidence_refs_valid=true`、`decision_grounded=true`，A 为空、B 为 `update_contact_phone`。
- 新增 `tests/test_gui_contact_search.py` 覆盖反事实同路由、私有 Need、预算耗尽和冻结回读门禁；该能力仍限定为 contact_update 正式试点，不把整个四簇查询矩阵宣称完成。

## 2026-09-25 · Contacts EOF 回读证据补强

- `scripts/gui_contacts.py` 的私有 `read_contact` 收据现在同时保存已独立校验的完整 `data`（电话、邮箱等），不再只保存 Provider 行 ID/显示名；后续采集的 EOF/复位证据可直接核对 World 字段，仍不进入公开 GUI manifest。
- 现有历史发布包保持不可变；未用新代码补签旧收据。Contacts 适配器定向回归与 contact_update 查询回归通过。

## 2026-09-25 · 蓝图修订后全量回归复核

- 再次核对最新蓝图：P0 为 B0–B5，先保证可生产 GUI 数据；P1 才是 B6/B7、完整机器验收和 G3/G4/G5 对外发布门槛；B3a 系统顶部横幅按 P2 保持不做，不以通知栏或 App 内弹窗冒充。
- `python3 -m unittest discover -s tests -v`：152 项通过；`./scripts/verify.sh` 通过，六个 APK 构建与校验通过。当前仍无运行中的 Controller、AVD 或残留服务。

## 2026-09-25 · Controller 跨会话自愈与 Clock 重采

- 复现并定位 Controller 退出根因：启动命令结束后宿主会话可能回收后台 supervisor，后续独立 `reset-instance.sh` 访问旧端口失败；这不是业务路由或 runner key 失效。
- 新增 `scripts/ensure-controller.sh`，由 `reset-instance.sh` 统一执行健康检查、同端口恢复、原实例参数/runner key 复用和恢复锁；重复 reset 不会并发拉起两个 Controller。采集器继续只调用 reset，不维护第二套恢复逻辑。
- 在 Controller 失活后独立执行 `reset-instance.sh gui-pilot` 已自动恢复并成功 reset；随后 Clock `runtime/gui-clock-machine-20260925-v4/` 完成真实 seed、DeskClock 页面、原 PNG、EOF、复位，`gui-pixel-ocr-v6` 通过，并发布/重放 `runtime/gui-clock-release-v3/`。
- 本轮 `python3 -m unittest discover -s tests -v` 为 152 项通过；`./scripts/verify.sh`、六个 APK 构建与校验、Clock 发布重放均通过。P2 系统横幅及 Clock 日期/重复规则仍不因一次性卡片成功而冒称完成。

## 2026-09-25 · 采集运行约束

- 只要 AVD/Controller 的参数、镜像和 APK 版本未变化，连续场景之间复用同一运行实例，不中途停止；全部采集、机器验收和综合审查结束后再统一停止并清理。

## 2026-09-26 · §3.3/§6.6 逐帧环境记录与机器一致性验收

- `scripts/gui_capture.py` 新增 `DEVICE_ENV_FIELDS` 与 `read_device_env()`：每帧在截图相邻位置独立读取 `resolution`（`wm size`）、`density`（`wm density`）、`font_scale`、`locale`、`theme`（`cmd uimode night`，不可用时回退 `secure ui_night_mode`）和 `timezone`，写入该帧 `device_env`；Episode 级 `device` 快照改用同一函数，不再逐项散读。`locale` 空值回退 `ro.product.locale`/`system_locales`，仍无值则拒收；仅 `font_scale=null` 记为 `default`（表示未设置覆盖），不以空串冒充实测值。
- `scripts/gui_machine_qa.py` 机器规则升为 `gui-pixel-ocr-v7`：新增 `audit_device_env()`，拒绝缺帧环境、字段缺失/空值、同一 Episode 内任一字段漂移、Episode 快照与帧不一致（`timezone` 例外：`set_device_time` 会按 packet 改写 `persist.sys.timezone`，只做逐帧一致性），并把 `wm size` 解析出的分辨率与磁盘 PNG 实际解码尺寸逐帧绑定。任一项不过 `audit_capture()` 直接 `reject`，不进入 OCR。
- `scripts/gui_publish.py`：`gui-pixel-ocr-v7` 的发布与重放额外要求 `qa-machine.json` 中 `device_env.status=pass`；v5/v6 等旧版本仍按各自规则版本重放，未改写任何旧收据、旧发布包或旧 `pending_review` 结果。
- 新增回归 `tests/test_gui_pipeline.py::test_device_env_missing_or_drifted_frames_are_rejected`：覆盖字段缺失（`locale`）、帧间漂移（`density`）、分辨率与 PNG 尺寸不一致，以及 `audit_capture`/`publish` 的拒收路径；原发布门禁用例补齐逐帧环境夹具。
- 验证：`python3 -m unittest discover -s tests` 共 153 项通过（原 152 项 + 新增 1 项）。直接重放现有发布包，v3–v6 机器完成包全部通过，`pending_review` 的旧 v1 包仍按原语义拒收。
- 版本边界：v7 之前采集目录没有逐帧 `device_env`，用当前 `audit_capture`/`gui_pair_qa` 重新审计会按 v7 拒收；这只表示旧采集目录不再满足新规则，不影响已发布包按原规则版本的 `verify_release` 重放，也没有静默补签。
- **本轮未启动 AVD/Controller、未采集新实机帧**；`read_device_env` 只做了本地 mock 冒烟，逐帧环境字段的真实取值、`theme` 回退路径与 `wm size` 匹配仍需在稳定设备表面上重采后，才能声称实机通过。分辨率与 PNG 按原方向严格比较（不自动换序），当前采集面均为竖屏；将来若支持旋转采集需新规则版本。
- 独立复核：本地再次运行 153 项测试通过，Clock v3 与 contact_update B 旧发布包重放通过；测试有既存资源关闭警告。按要求派发的 `packy-gpt-sol` 只读复审被服务端 403 拒绝；用户随后改为主模型本地审核。本地审核发现无法读取 Android locale 时原代码将其伪记为 `default`，现改为采集失败并增加回归。此改动仍需实机采集验收，不冒称贵模型已审核。

## 2026-09-26 · G4 候选规模改为建议值（audit 取消硬上下限）

- 对照蓝图 §7：8–16 条候选是**建议规模，不是门槛**；`scripts/gui_g4_audit.py` 原先内嵌 `8 <= len(candidates) <= 16`，把建议规模误当硬门槛，本步移除。
- 审计仍强制结构不变量：候选非空、`candidate_count` 与候选列表精确一致、`candidate_id` 唯一、`splits` 完整覆盖全部候选且不得重复；每条候选的已发布包仍必须通过 `gui_publish.verify_release` 重放校验。
- 已追踪调用者：`tests/test_g4_candidates.py` 与 CLI `--spec/--runtime-root/--output` 是仅有的调用面，接口与报告结构未变；未修改 KU 模拟器原文件，也未扩大 G4 Gold 宣称（仍只称候选子集机器审计）。

## 2026-09-26 · G4 规模边界定向回归

- `tests/test_g4_candidates.py` 删除 `8<=count<=16` 契约断言；真实合同审计改为只要求非空、`candidate_count` 精确匹配、`candidate_id` 唯一、`splits` 完整且无重复，并继续要求每条候选 `verify_release` 通过。
- 新增 1 条与 17 条候选的定向用例（`unittest.mock` 替换 `verify_release`）：报告 `status=pass` 且 `candidate_count` 与输入一致，证明数量本身不被拒收。
- 新增仍须拒收的回归：空候选、`candidate_count` 不匹配、重复 `candidate_id`、`splits` 重复成员、`splits` 漏项；数量放开不削弱结构门禁。

## 2026-09-26 · G4 规模修正验证

- 定向回归 `python3 -m unittest tests.test_g4_candidates -v`：9 项通过（含 1 条/17 条候选按数量本身不拒收，以及五类结构拒收）。
- GUI 相关无设备模块 `tests.test_g4_candidates`、`test_gui_contract`、`test_gui_pipeline`、`test_static`、`test_notification_contract`、`test_ui_provenance`、`test_product_gaps`、`test_gui_appointment_search`、`test_gui_contact_search`、`test_gui_files_search` 合计 74 项通过。
- CLI 以真实合同重跑 `scripts/gui_g4_audit.py`（输出仅写 `/tmp`，未改写 `runtime/` 历史报告）：`status=pass`、`candidate_count=14`、拒收统计 `capture_failed=16`、`reject=6`，与既有合同一致。
- 全量 `discover` 在本沙箱中仍有 25 项 Controller/回环端口依赖用例报错（`PermissionError: Operation not permitted`），均与本改动无关；已发布包重放与实际 AVD 采集未在本轮执行。

## 2026-09-26 · 主模型本地复审

- 逐帧环境补丁的 `locale` 无法取值回退已改为拒收；新增测试后定向 15 项、全量 154 项通过。旧 Clock v3 与 contact_update B 发布包重放通过。设备未连接，v7 实采仍待验证。
- G4 审计变更已检查真实合同、1/17 候选模拟验证与所有调用面：移除的只有数量硬门槛，非空、唯一、split 无重复/漏项及逐包机器重放仍在。主环境全量 161 项通过（有既存资源关闭警告）；Broker 子沙箱的回环端口权限错误并非主环境回归失败。未修改 KU 原始文件。

## 2026-09-26 · v7 逐帧环境实机复采结果

- 启动 `gui-pilot` AVD 后实测 `read_device_env()` 返回 `Physical size: 1080x2400`、`Physical density: 420`、`font_scale=default`、`locale=en-US`、`Night mode: no`；说明新增读取路径在真实 Android 上可取值。
- Clock v7 采集完成 seed、独立读回和 EOF freeze，帧收据已写入 `runtime/gui-clock-env-v7-20260926/receipt.json`，但随后 `reset-instance.sh gui-pilot` 超过 300 秒，`reset_verified=false`，因此没有进入机器 QA/发布，不能称实机闭环通过。
- 已停止 `gui-pilot` 容器并保留失败收据；下轮先定位 reset 超时（旧 Controller pid/恢复路径），再重采，避免把未复位设备用于连续 Episode。

## 2026-09-26 · reset 单一总 deadline 与阶段收据

- 追踪调用者：`scripts/gui_capture.py` 两处（采集前与 finally 清理）用 300 秒外层超时调用 `scripts/reset-instance.sh`；脚本内部六个 force-stop、snapshot load、boot 等待（180 秒上限）、六个安装与六个校验各自最多 120 秒，累加可静默超过 300 秒，外层 `subprocess` 只杀直接子进程，失败还会被 finally 的第二次 reset 覆盖成 `reset: ...`。上轮 `runtime/gui-clock-env-v7-20260926/receipt.json` 即此路径，`reset_verified=false`。
- `reset-instance.sh` 改为单一墙钟 deadline：`RESET_TOTAL_BUDGET_SECONDS`（默认 240 秒，严格小于调用者 300 秒），每个步骤取 `min(ADB_RESET_TIMEOUT_SECONDS, 剩余时间)`，boot loop 也受同一 deadline 与 180 秒上限双重约束；超时用 `timeout --kill-after` 杀整个进程组，避免遗留孤儿进程。
- 阶段与耗时写入 `runtime/instances/<id>/reset.log`（每步 start/ok/failed + duration + budget_remaining，失败有 `reset status=failed phase=<阶段>` 汇总行）；失败时 stderr 明确 `reset failed at phase=<阶段>`，实例仍按原策略标 `FAILED`/保持 `CONTAMINATED`。
- `gui_capture.py` 新增 `reset_instance()`：显式传递 240 秒内部预算，失败或超时的 `failure_reason` 带上 `reset.log` 末尾阶段行；外层 300 秒超时改为杀整个进程组并明确报“exceeded capture timeout”，不再静默。finally 二次 reset 失败时保留原始 `capture_failure_reason`，不再互相覆盖。
- 回归：新增 `tests/test_reset_budget.py`（6 项）用假 adb/docker/curl 在临时根目录实跑脚本，覆盖失败阶段命名、单步被 deadline 截断（120 秒 ADB 超时下 3 秒预算约 3 秒返回）、boot 等待受全局 deadline、预算 < 外层的静态断言，以及 `reset_instance()` 预算传递与超时杀进程组；相邻 `test_static`、`test_gui_pipeline`、`test_notification_contract`、`test_gui_files_search`、`test_gui_contact_search` 合计 33 项通过。本轮未改 KU 模拟器源码、未把 300 秒调大、未启动 AVD。

## 2026-09-26 · reset 本地代码审核修正

- 审核发现 `pm path` 原先通过 `| ... || true` 吞掉 ADB 超时，可能继续执行安装并误报完成；已拆分命令取值，保留 `run_step` 的退出状态，校验路径和哈希也相同处理。失败状态回写使用 `curl --max-time 5`，避免错误处理本身无界等待。
- 新增 `pm path` 挂起不得落入安装的离线回归，`tests.test_reset_budget` 7 项通过；阶段日志仍明确失败步骤。真实设备 reset 时长和 v7 采集/发布仍待重新验证，不复用上轮拒收收据。
- 首次实机重启仅 5 秒即在 `dudu-pm-path` 以 code=1 拒收，阶段日志证明新路径有效，同时揭示基线中 APK 未安装时 Android `pm path` 正常返回 1。已把该步骤的 code=1 记录为 `missing` 并继续安装；其他错误码及超时仍失败，最终安装与 APK 哈希校验不放宽。新增缺失包回归，未把该次失败算作成功采集。
- 二次实机 `start-simulator.sh` 已 reset 成功，但随后的 Clock v7 采集在 `controller-ensure` 120 秒超时，`runtime/gui-clock-env-v7-20260926-v2/receipt.json` 保留 `capture_failed/reset_verified=false`。根因是 `ensure-controller.sh` 的 fd 9 恢复锁被后台 `controller-supervisor.sh` 继承，下一次 ensure 等待该锁。现于后台启动处显式关闭 fd 9，保留父进程串行互斥；新增回归，未篡改两次拒收收据。

## 2026-09-26 · reset 修复与 Clock v7 实机闭环

- 修复后设备首次 reset 正常通过；独立二次 reset 在约 13 秒内通过，证明 Controller 恢复锁不再被 supervisor 继承。`runtime/instances/gui-pilot/reset.log` 保留各阶段与总耗时（Clock 采后复位 8 秒）。
- `runtime/gui-clock-env-v7-20260926-v3/` 完成真实 Clock seed、单帧原 PNG/逐帧环境、权威读回、EOF freeze 与复位，`capture_complete`、`reset_verified=true`；`gui-pixel-ocr-v7` 原图机器 QA 为 `pass`。
- 发布并重放 `runtime/gui-clock-release-v7-20260926-v1/`，1 packet/1 frame，manifest SHA-256 为 `a38655a81fbd2ac477fc603bdb6fd098e277410edfda0638665d71d52b4e7539`。历史 v7-v1/v2 失败采集收据保持不变，不升格为发布包；此结果只证明受限 Clock 单帧，不代表 reminder_coverage 完整场景簇。
- 本轮主模型本地审核并修复缺包 `pm path` 返回 1、超时传播和恢复锁继承问题；全量 Python 回归 170 项通过（仍有既存资源关闭警告）。未修改 KU 模拟器原文件。

## 2026-09-26 · GUI Track A/B 私有分类编码收束

- 修正 `tests/test_gui_track_pair.py::test_opposite_need_sets_are_required` 的 mock 路径：原 `scripts.gui_pair_qa.verify_release` 会按命名空间包再导入一份 `scripts.gui_pair_qa`，被 patch 的不是被测模块引用的函数，导致真实 `verify_release` 执行并在缺 `manifest.sha256` 的临时目录上报 `FileNotFoundError`。现改为 patch `gui_pair_qa.verify_release`（测试以顶层模块名导入），Need 条件断言恢复生效。
- 收紧 `scripts/gui_pair_qa.py::audit_release_pair` 的相反 Need 判定：两侧私有计划必须各自带 `formal.oracle`，`variant_id` 必须是非空字符串且两侧不同，`passive_class` 必须是 `active_required`，且 `expected_need_set` 恰好一侧为空、另一侧非空；此前“两侧都非空但不同”“同 World 伪造不同 Need”会误判为 `active_required`。
- 公开等价与机器 QA 门禁补回归：篡改一侧 `public_rule` 并同步 `manifest.sha256`/`qa.json` 仍被拒（`counterfactual public rules differ`）；把 `qa-machine.json` 改为 `reject` 并同步 `qa.json` 哈希仍在 `verify_release` 被拒（`machine QA rule or model mismatch`）。Appointment/Files 的 v2 发布包（v6 机器规则）继续按原规则版本重放，本步未放行旧 v6 采集目录冒充 v7。
- 像素比较保持 fail-closed：发布包级只比较 1080×2400 的第 132–2336 行 App 区，不做 volatile 区域豁免。Contacts A/B 严格差异为第 199–239 行共 41 行，报告固定 `status=reject`、`gui_track_passive_class=unverified`，测试现逐行锁定该 41 行，不把 41 行差异当噪声放过。
- 旧采集目录不静默补签：新增回归确认 `runtime/gui-files-machine-{a,b}-20260925-v2`（v6 规则）用当前 v7 `audit_capture` 重新审计仍被拒，未改写旧收据、旧发布包或旧 `.private` 证据。
- 验证：`python3 -m unittest tests.test_gui_track_pair -v` 7 项通过（含 4 项新增）；不含本地 Controller 的 12 个 GUI/静态模块共 90 项通过；`gui_pair_qa.py --releases` 对 Files 输出 `pass`、对 Contacts 输出 `reject`（exit 1）。全量 discover 仍有 25 项既存错误，原因是本沙箱禁止 `socket()`（`PermissionError: Operation not permitted`）导致本地 Controller 无法启动，与本次改动无关；未启动 AVD，未修改 KU 源码与任何旧 release/runtime 证据。

## 2026-09-26 · 主模型复审：GUI Track P/A 不继承文本标签

- 审核发现初版 `audit_release_pair` 仍要求私有 Oracle 的 `passive_class=active_required`，等于借用 Text 标签；现仅校验相反且正确绑定 Episode/World 的私有 Need Set、两侧公开规则/packet 等价、已发布原图的机器 QA 与 App 区严格同像素，由这组 GUI 证据独立给出 `gui_track_passive_class`。空 `variant_id` 也改为拒收，测试证明即使 Text 标签改为 `passive_sufficient`，GUI 对照仍按像素和 Need 判为 `active_required`（仅测试分类逻辑，测试中 mock 了发布验签）。
- 新生成私有报告 `runtime/gui-files-track-qa-20260926.json` 与 `runtime/gui-appointment-track-qa-20260926.json` 均 `pass/active_required`；`runtime/gui-contact-update-track-qa-20260926.json` 因 App 区 41 行像素差异保持 `reject/unverified`。这些只证明特定已发布 A/B 试点的 GUI Track 标签，不能代替四簇完整 Gold 或把旧 v6 采集收据重签为 v7。
- 主环境 `python3 -m unittest discover -s tests -q` 为 178 项通过（存在既有资源关闭警告）；未改 KU 模拟器原始文件，未改写历史发布包。

## 2026-09-26 · 发布包级 GUI Track 受控 volatile 区域豁免（仅显式绑定采集目录）

- `scripts/gui_pair_qa.py::audit_release_pair` 增加可选 `left_capture/right_capture`：只有两侧对应采集目录都显式传入才启用豁免，缺任一侧立即拒收；不传时保持原有 `gui-release-pixels-v1` 严格逐像素比较，上一节“不做 volatile 区域豁免”的结论仍适用于该默认严格路径。启用豁免时报告 `rule_version=gui-release-pixels-v2`。
- 启用路径的绑定顺序：先对两侧发布目录执行 `verify_release`；再要求发布 `.private/receipt.json` 与采集 `receipt.json` **字节完全相同**（Contacts v3 两侧 SHA-256 分别为 `6ff0444649bf…`、`588b8df3657b…`，两处一致）；再把公开 Manifest 的每个 packet/frame 绑到采集收据帧，核对 packet_id/frame_id 一一对应、帧数一致、PNG 路径与 SHA-256 相同，并对采集目录内的 PNG 原文件重新哈希。错绑另一侧或伪造收据都会在此拒收。
- 逐帧读取采集目录内 UI XML 前先核对 `ui_dump_sha256`，并把路径限制在采集目录内、拒绝符号链接；只收集白名单 `resource-id=com.google.android.apps.messaging:id/searchbar` 的 bounds（v7 之前的 `bound_volatile_rectangles` 只解析、不参与发布放行）。逐像素比较只跳过两侧受信矩形的**交集**（`intersect_rectangles`，不是并集，也不整行跳过）：交集外的任一像素差异仍使该 pair `reject`。
- Contacts `runtime/gui-contact-update-formal-machine-{a,b}-20260925-v3` 的 UI 快照白名单节点均为 `[42,153][1038,279]`，App 区严格差异为第 199–239 行共 41 行（x 385–647），全部落在交集内。显式绑定这两个采集目录后：`runtime/gui-contact-update-formal-release-{a,b}-20260925-v1` 对照为 `pass/active_required`，`exempted_rows=[199..239]`、`app_changed_rows=[]`；不传采集目录时仍为 `reject/unverified` 并逐行锁定这 41 行。旧报告 `runtime/gui-contact-update-track-qa-20260926.json` 未改写，默认严格路径重放结果与其逐字段一致。
- 回归新增：交集≠并集且不整行跳过（合成行）；篡改采集 UI XML 追加字节后 `capture UI snapshot hash mismatch` 拒收；错绑采集目录（用 B 采集配 A 发布）`capture receipt differs from published private receipt` 拒收；只给一侧采集目录在函数与 CLI 两层拒收；复制 release/`.private`/capture 到临时目录后修改交集外第 300 行像素并同步重签 manifest/manifest.sha256/qa.json/receipt/qa-machine 整条哈希链，仍因该行不在交集内判 `reject`。Files/Appointment 严格路径继续 `pass`。
- 验证：`python3 -m unittest tests.test_gui_track_pair -v` 13 项通过；相邻 GUI/静态模块（`test_gui_pipeline`、`test_gui_contract`、`test_gui_files_search`、`test_gui_contact_search`、`test_gui_appointment_search`、`test_g4_candidates`）合计 59 项通过；CLI 对 Files `pass`（exit 0）、Contacts 严格 `reject`（exit 1）、Contacts 绑定 v3 采集目录 `pass`（exit 0）、只给一侧采集目录报错（exit 2）。未修改 KU 源码、旧发布包、旧 `.private` 或旧 runtime 收据。

## 2026-09-26 · 主模型复审纠正：Contacts 受控豁免撤销

- 上节声称显式绑定 Contacts v3 后 `pass/active_required` 的结论**撤销**：两侧原始 UI XML 搜索栏的可见文本分别为 `Search places` 与 `Search conversations`，属于查询前的语义差异，而非可忽略的系统像素噪声；即使 41 行差异全部落在白名单矩形内，也不能认定反事实同流。
- `scripts/gui_pair_qa.py` 仅在双方白名单节点的 `resource-id`、`text`、`content-desc`、`class`、`package` 完全一致时，才取矩形交集豁免纯像素差异。Contacts 显式绑定 v3 的 `volatile_intersection=[]`、`exempted_rows=[]`、第 199–239 行仍计为 App 差异，结果 `reject/unverified`；默认严格路径与历史 `runtime/gui-contact-update-track-qa-20260926.json` 不变。
- 定向 13 项、全量 183 项测试通过（既有资源关闭警告）；未改 KU 原文件、旧发布包或旧收据。Contacts 仍需重新采集语义一致的 A/B 才可能升级 GUI Gold。

## 2026-09-26 · 旧 audit_pair 的 volatile 矩形改为绑定语义交集

- 此前审计语义（保留记录）：`scripts/gui_pair_qa.py::audit_pair` 逐帧读取采集目录 `ui_dump` 时**不校验** `ui_dump_sha256`、不限制路径与符号链接，只按白名单 `resource-id=com.google.android.apps.messaging:id/searchbar` 收集 bounds，并把两侧矩形**取并集**作为豁免区；`differs_outside` 只要差异像素落在任一侧矩形内就跳过该像素。于是 Contacts v3 两侧搜索栏文案不同（A 可见文本 `Search places`、B 可见文本 `Search conversations`）时，App 区第 199–239 行差异全部落在"任一侧"矩形内，历史报告 `runtime/gui-contact-update-formal-pair-qa-20260925-v3.json`（旧 `rule_version=app-pixels-1080x2400-v1`）给出 `status=pass`、`comparable_app_changed_rows=[]`，把查询前的公开文本差异当成了可豁免噪声。该历史报告属既有证据，本轮未改写。
- 本步修复（只改 `scripts/gui_pair_qa.py`）：`audit_pair` 改为复用 `bound_volatile_rectangles` 逐帧绑定 UI XML——路径必须匹配 `ui/<32hex>.xml`、必须带 64 位十六进制 `ui_dump_sha256`、解析后路径必须位于采集目录内且拒绝符号链接、XML 字节哈希必须等于收据哈希；再复用 `intersect_rectangles` 取**双侧交集**。只有两侧白名单节点的 `resource-id`、`text`、`content-desc`、`class`、`package` 五元组完全一致时，才把两侧矩形两两求交（`intersect_rectangles` 只返回交集矩形，单侧矩形不产生豁免范围）；任一差异像素落在交集之外即进入 `comparable_*_rows` 并判 `reject`，状态栏差异仍单独逐行记录。
- 报告结构同步：pair 新增 `volatile_intersection`（受信交集）与 `exempted_rows`（App 区内因交集被豁免的行），移除旧并集字段 `volatile_regions`；`rule_version` 升为 `app-pixels-1080x2400-v2` 以区分旧并集语义，并删除不再使用、无哈希绑定的 `volatile_rectangles` 读取函数。对 Contacts v3 采集，两侧搜索栏语义不匹配（`Search places` ≠ `Search conversations`）→ `volatile_intersection=[]`、`exempted_rows=[]`，第 199–239 行全部计为 `comparable_app_changed_rows`，`status=reject`。

## 2026-09-26 · audit_pair 绑定语义交集的定向回归

- `tests/test_gui_track_pair.py` 新增 3 项最小回归：Contacts v3 原始采集 `audit_pair` 仍 `reject`，`volatile_intersection=[]`、`exempted_rows=[]`，第 199–239 行 41 行全部进入 `comparable_app_changed_rows`；把 B 侧搜索栏 XML 文本改为与 A 侧相同的 `Search places`（并同步更新临时副本 `receipt.json` 的 `ui_dump_sha256`）后，语义一致的 `[42,153][1038,279]` 交集生效，`status=pass`、`exempted_rows=[199..239]`、`comparable_app_changed_rows=[]`；只把 B 侧矩形挪到 `[42,300][1038,426]`（语义仍相同但两侧无交集）时交集为空、41 行不可豁免，仍 `reject`，证明单侧范围不构成豁免。
- 夹具为测试模块顶层新增的 `machine_qa_for()` 与 `edit_searchbar()`：前者只把历史 v6 `qa-machine.json` 规范化为当前规则形态并 mocks `gui_pair_qa.audit_capture` 以便隔离像素/volatile 规则，不写入任何 runtime 目录；后者在临时目录内改写白名单节点并同步该副本的 UI 哈希。未改 KU 原文件、旧发布包或旧 runtime 收据。

## 2026-09-26 · audit_pair 语义交集修复验证

- 定向 `python3 -m unittest tests.test_gui_track_pair -v`：16 项通过（原 13 项 + 新增 3 项），含 Contacts 语义不同拒收、语义相同交集豁免、单侧矩形不豁免三条回归。
- 相邻无设备模块 `tests.test_gui_pipeline`、`test_gui_contract`、`test_ui_provenance`、`test_static` 合计 41 项通过；`test_gui_contact_search`、`test_gui_files_search`、`test_gui_appointment_search`、`test_g4_candidates`、`test_notification_contract`、`test_product_gaps` 合计 33 项通过。
- 未执行真实 AVD 采集与 CLI 真机对照（运行时目录中没有可用的 v7 A/B 采集对）；历史报告 `runtime/gui-contact-update-formal-pair-qa-20260925-v3.json`、发布包与旧收据均未改写。旧 v6 采集默认 v7 机器 QA 仍按 `both Worlds need accepted machine frames and reset receipts` 拒收，本次修复未放宽该门禁。

## 2026-09-26 · 主模型复审：子节点语义也不得豁免

- 上节五元组只检查白名单父节点，仍可能忽略搜索栏内部子节点的公开文字。现在对该节点及其所有 UI 子节点逐一比对 `resource-id/text/content-desc/class/package`，只要任一子节点不一致就不生成可豁免交集；原始 Contacts 的 `open_search_bar_text_view` 也分别显示 `Search places` / `Search conversations`。
- 修正临时测试夹具：语义相同的正例同时修改父节点和实际可见的子文本；另加子文本单独不同的拒收回归。定向 17 项、全量 187 项通过（既有资源关闭警告）。此前日志中的“16 项”记录为修正前的运行结果，并非最终验收数字；历史证据不改写。

## 2026-09-26 · B0 逐 packet 可呈现性审计（正式 pilot 首批）

- 契约补足：`contracts/gui-scenario-audit.json` 为四个簇补 `formal_pilot_datasets`（`reminder_coverage` 为空，其余三簇分别绑定 `gui-appointment-formal-pilot` / `gui-files-formal-pilot` / `gui-contact-update-formal-pilot` 及其 `cluster-base.json` 的 `cluster_id`），每个候选补 `packet_source` 与 `required_assertions`，并新增 `match_fields` / `conclusion_rules` 说明；原有 `status`、`pilot_view`、`gap` 等历史结论逐字未改（contact_update/messages 仍为 `pilot`，其余仍为 `missing`）。
- 新增最小 CLI `scripts/gui_scenario_audit.py`（`--audit`/`--datasets`/`--capabilities`/`--output`）：先按 `cluster_id` 校验数据集 `cluster-base.json`，再按 packet 的 `source`/`capture.app_id`/`capture.view_id`/`capture.visible_assertions` 匹配候选；未声明的正式 pilot 目录、声明但缺失的数据集、cluster_id 不一致、重复 packet_id 均直接拒收。
- 结论纪律：候选 `status=pilot` 一律 `missing`，能力表声明 view 不升级结论；未映射 source/app/view 记 `candidate_unmapped`，能力表未声明记 `capability_surface_missing`，断言越出候选/能力表记 `assertions_exceed_candidate` / `assertions_exceed_capability`，非 Text 且无 capture 记 `capture_spec_missing`；`text_only` 仅限 `type=text` 且无 capture 的 packet；只有候选与能力表同时 `supported` 且字段全被覆盖才输出 `supported`。
- 示例报告（新文件）`runtime/gui-b0-audit-20260926.json`：三份正式 pilot 共 3 条 packet 全部 `missing`，`supported=0`、`text_only=0` —— appointment 的 `mail/mail_detail` 与 files 的 `files/download_file` 为 `candidate_status_missing`，contact_update 的 `messages/conversation_list` 为 `candidate_status_pilot`；`reminder_coverage` 本批没有正式 pilot 资产，按空集记录且不补造结论。运行示例：`python3 scripts/gui_scenario_audit.py --output runtime/gui-b0-audit-20260926.json`。
- 定向测试 `python3 -m unittest tests.test_gui_scenario_audit -v`：10 项通过，覆盖缺失、匹配、未映射、能力表未声明、字段越权、text_only 边界、pilot 不升级、supported 双门槛与数据集绑定拒收；相邻资产模块 `test_gui_contract`、`test_gui_pipeline`、`test_g4_candidates`、`test_notification_contract`、`test_static`、`test_ui_provenance` 连同本模块合计 66 项通过。
- 本步只读契约资产，未接触设备、未改 KU 源码、未改任何历史证据或既有 runtime 收据，报告只含契约级结论与来源 SHA-256；**四个场景簇（含 contact_update）结论仍非 GUI Gold**。附注：本环境全量 `unittest discover` 中依赖 local-controller 子进程的旧模块（acceptance/backend_regressions 等）在启动阶段读取 stdout JSON 失败报错 25 项，与本次改动无关；资产与静态检查模块全部通过。

## 2026-09-26 · B0 主模型复审：必需字段与表面类型门禁

- 审核发现首版 `supported` 分支只验证“已断言字段不越界”，未要求候选声明的全部 `required_assertions` 出现在 packet；同时未验证 `capture.surface`，可能把假系统横幅沿 App 屏视图误归类为支持。现在缺必需字段输出 `required_assertions_missing`，非 `app_screen` 输出 `unsupported_capture_surface`，二者均为 `missing`；数据集映射名限制为安全的正式 pilot 目录名。
- 更新契约说明、定向回归与新 B0 示例报告的源哈希；现有三条正式 pilot packet 仍全部 `missing`，四簇仍非 Gold。本地全量 `python3 -m unittest discover -s tests -q` **197 项通过**（既有资源关闭警告）；上节关于本环境全量有 25 项失败的描述是子代理环境观察，并非本轮主环境最终结果。未改 KU 原文件或旧收据。

## 2026-09-26 · B1 reminder_coverage 首批 A/B 正式资产输入

- 新增 `datasets/gui-reminder-formal-pilot/` 的十份正式形状资产：`manifest-{a,b}.json`、`global-base.json`、`cluster-base.json`、`world-{a,b}.json`、`passive.json`、`compatibility.json`、`oracle-{a,b}.json`。Passive 只有一条 Mail `mail_detail` 的 app_screen packet（seed 的已读详情页，不声称真实收件、也不使用系统横幅），私有目标是同一条 Clock `alarm_card` 闹钟记录；A 世界 `clock-covered` 已启用 14:30，B 世界 `clock-disabled` 只把 `enabled` 改为 `false`，因此查询前公开 packet、公开规则与 Mail 记录逐字相同。
- `scripts/gui_formal.py` 新增 `ensure_reminder_coverage` goal：从 Mail 正文中的 `activity_title`、`approved_start_at/end_at` 与 `approved_reminder_minutes_before` 推导目标提醒时刻（start − lead，`%H:%M`）；校验同日活动、设备时区偏移一致、提前量不跨日期不正不负、Mail 发件人与正文身份、Clock 标签与活动标题一致，并要求至少一条 Passive 观察早于目标提醒时刻，Need Set 只在闹钟未同时满足 `time` 与 `enabled=true` 时给出 `update_clock_alarm`。
- 明确受控边界：`declared_channel` 只接受 `clock`（本批 single-app 目标，不冒称 Calendar/Clock 等效渠道或完整覆盖）；World patch 只允许命中私有目标闹钟且只允许 `time`/`enabled`，改 Mail、改标签、改到别的实体都会拒收。本批只完成契约编译，未采设备、未发布、未改 KU 原文件或任何历史 runtime 证据；不构成 P0/G1 或 GUI Gold。

## 2026-09-26 · reminder_coverage B0 映射同步与定向回归

- `contracts/gui-scenario-audit.json` 只把 `reminder_coverage.formal_pilot_datasets` 从空集改为绑定 `gui-reminder-formal-pilot`（`cluster_id=reminder-coverage-mail-clock-pilot`）；mail/calendar/clock 三个候选的 `status`、`pilot_view`、`gap` 逐字未改，新增 Mail packet 命中 mail 候选后仍输出 `candidate_status_missing`，不因新增资产升格。
- 新报告 `runtime/gui-b0-audit-20260926-reminder.json`：四簇共 4 条 packet 全部 `missing`，`supported=0`、`text_only=0`、`gui_gold=false`、`device_evidence=not_used`；历史 `runtime/gui-b0-audit-20260926.json` 与旧收据均未改写。
- `tests/test_gui_contract.py` 新增两项定向回归：`test_reminder_formal_clock_alarm_opposite_need` 验证 A/B 均可编译、公开等价、A 空 Need 与 B `update_clock_alarm` 相反、公开规则一致且只有私有闹钟 `enabled` 不同；`test_reminder_formal_rejects_illegal_time_source_and_world_change` 覆盖 end<start、目标时区不符、提前量跨日期、bool 提前量、`declared_channel` 越界、Mail 发件人/正文身份、闹钟标签身份、World 改 Mail、World 改标签与 oracle Need Set 不符共 11 个拒收分支。
- `tests/test_gui_scenario_audit.py` 同步为 4 条正式 pilot packet 全 `missing`、四个数据集映射齐备；定向运行 `tests.test_gui_contract`、`tests.test_gui_scenario_audit`、`tests.test_gui_pipeline`、`tests.test_g4_candidates`、`tests.test_notification_contract`、`tests.test_static`、`tests.test_ui_provenance` 共 68 项通过；`tests.test_gui_files_search`、`tests.test_gui_contact_search`、`tests.test_gui_appointment_search`、`tests.test_gui_track_pair` 共 23 项通过。

## 2026-09-26 · reminder_coverage B1 收口：验证与剩余能力差距

- 追加一条 fail-closed 门禁：私有 Clock 目标一旦出现在 Passive packet 里（`capture.app_id/subject_ref` 命中目标闹钟）立即以 `reminder goal target must stay private until EOF` 拒收，防止 A/B 目标状态泄漏到查询前公开画面；定向回归同步增加该分支，共 12 个拒收分支全部通过。
- 验证命令与结果：`python3 -m unittest tests.test_gui_contract tests.test_gui_scenario_audit tests.test_gui_pipeline tests.test_g4_candidates tests.test_notification_contract tests.test_static tests.test_ui_provenance` 68 项通过；`tests.test_gui_files_search tests.test_gui_contact_search tests.test_gui_appointment_search tests.test_gui_track_pair` 23 项通过；四个正式 pilot 的 8 份 manifest 全部可编译。`python3 -m unittest discover -s tests -q` 在本沙箱为 141 项、25 项报错，全部落在依赖 local-controller 子进程的旧模块（acceptance 5、backend_regressions 15、episode_lifecycle 2、adapter_contract 1、journal_packets 1、typed_data_layer 1）；直接运行 `local-controller/server.py` 复现 `PermissionError: [Errno 1] Operation not permitted` 的 `socket()` 禁止，为既有环境限制，与本次资产/编译器改动无关。
- 剩余能力差距（本批明确未完成）：① 目标日期与重复语义不可表达——`clock.alarm_card` 与 Clock 数据都没有日期/星期/重复字段，编译器只按公开规则把 `HH:MM` 绑定到活动日期，Clock 的“目标日期有效覆盖”仍是缺口，设备上也只验证过 disabled 10:45 单帧（`runtime/gui-clock-env-v7-20260926-v3`），enabled 目标态的 B2/B3/B6 验收未做；② Calendar 渠道不可表达“无覆盖/错误提前量”——现有 Calendar pilot 只编译 `reminder_minutes_before=30`，没有设备验证过的未覆盖状态，因此本批不冒充 Calendar/Clock 等效渠道或 OR 覆盖规则；③ Mail 只是 seed 的已读 `mail_detail` app_screen，真实收件、发件人权威与 heads-up 都不成立，`system_surface.heads_up_notification` 继续 `missing`；④ 本批只到 B1 契约编译，无设备采集、无机器像素 QA、无发布，不构成 P0/G1，更非 GUI Gold；B0 四簇结论仍全部 `missing`。

## 2026-09-26 · reminder_coverage 主模型复审与时间门禁

- 审核补上 EOF 与最后一条 Mail 观察都必须早于目标提醒时刻的检查；此前只检验最早观察，可能允许后续观察或冻结发生在提醒之后却仍称可决策。新增 EOF 越界拒收回归。
- 主环境定向 `tests.test_gui_contract tests.test_gui_scenario_audit` 28 项、全量 `python3 -m unittest discover -s tests -q` 199 项通过（既有资源关闭警告）。子代理所记沙箱 25 项错误不代表本地主环境结果。Clock 的 HH:MM 不证明特定日期提醒真实有效；这仍只是合法性受限的 B1 计划，不能计为完成 reminder_coverage 的 B2/B3/B6 或 GUI Gold。
