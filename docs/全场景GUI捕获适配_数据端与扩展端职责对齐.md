# 全场景 GUI 捕获适配：数据端(PB) 与 扩展端(simulator-extension) 职责对齐

> 目标：根据文本数据集获取 GUI 图片数据。范围不含评测（release_gate / coverage_audit / LLM 打分一律不做）。
> 本文件不参考 `simulator-extension/docs/ProactiveBench_GUI_全流程对齐与后端扩充实施蓝图_20260926.md`。

## 一、结论速览

1. 你的判断方向对，但有一处要澄清：**view_id / subject_ref / visible_assertions 目前不在 PB 数据里，而是扩展端 compile 时硬编码出来的**。
   - PB 的 `stream.jsonl` 只有 `packet_id / sequence / tag / source / observed_at / delivered_at / text_event / screenshot_ref / screenshot_sha256`，**没有** view_id/subject_ref/visible_assertions。
   - 这三样是 `simulator-extension/scripts/pb_gui_capture_compile.py` 里硬编码的 `EP_0004_PACKETS` / `EP_0005_PACKETS` 表驱动出来的。
   - 所以「数据端产表包」目前是愿景，不是现状。要做全场景，必须把这套映射从扩展端下沉/外化为一份**三方共享契约**。

2. 三个「ViewList」排查结果：
   - **KU**：没有声明式 ViewList。app 的 UI 知识散在代码里。
   - **PB**：有 `shared/source_capabilities.json`，但它描述的是**实体/数据字段能力**（list_fields / details.fields），不是 GUI view。PB 没有 view_id 概念。
   - **simulator-extension**：有 `contracts/gui-capabilities.json`，这才是真正的 **GUI view 表**（app → view_id → 字段），但目前只覆盖 core 6 + extension 6。

3. 一句话结论：要把「文本数据集 → GUI 图片」做成全场景可配置，三方（KU / PB / extension）要对齐一份 `source → entity(语义ref前缀) → view_id → visible_assertion字段` 的共享契约，扩展端从「硬编码分支」改成「消费这张表」。

---

## 二、现状链路（谁在哪一环）

数据流（V1 十步闭环）：

```
PB 数据
  cluster.json / active_world{initialization,history,variants} / stream.jsonl / private_gt.json / manifest.json
        │
        ▼  pb_gui_bridge.py（拉取 PB 数据）
        ▼  pb_backend_plan.py（生成 seed/read 计划）         ← SUPPORTED_SOURCES 只有 4 个
        ▼  pb_backend_apply.py（把实体 seed 到 Android/KU）
        ▼  pb_gui_capture_compile.py（生成「表包」）          ← 硬编码 EPISODE_PROFILES / PACKETS 表
        ▼  gui_capture.py（导航 + 截图 + uiautomator + 断言） ← 硬编码 per-source 分支
        ▼  gui_machine_qa.py（QA）
        ▼  pb_gui_image_host.py（回写 screenshot_ref）
        ▼  replay / release_gate / coverage_audit（评测，本次不做）
```

关键点：**「表包」（packet → view_id/subject_ref/visible_assertions）是 compile 阶段生成的，生成规则硬编码在扩展端，没有从数据端读取。**

---

## 三、三个「ViewList」配置文件现状

### 3.1 KU —— 无声明式 ViewList

app 专属 UI 知识全部在代码里：

| 位置 | 内容 |
|---|---|
| `src/knowu_bench/runtime/utils/models.py` | 一大张 `package名 → 中文 app label` 映射（APP_NAME 映射，calendar/mail/clock/contacts/files/messages/gallery/camera/chrome/maps/settings/mattermost/mastodon 等 300+ 条目） |
| `src/knowu_bench/runtime/app_helpers/*.py` | 每 app 的 seed/read 逻辑（`fossify_calendar.py` 直写 `/data/user/0/org.fossify.calendar/databases/events.db`，`mail.py` push `state.json` 到 com.gmailclone 等） |
| `src/knowu_bench/runtime/setup/*.py` | 每 app 初始化/seed |
| `src/knowu_bench/tasks/definitions/*` | 任务用 `app_names=set({'Calendar','Mattermost',...})` + `is_successful` 直调 helper |

**KU 没有 view_id / subject_ref / visible_assertion 概念。**

### 3.2 PB —— source_capabilities.json（实体/字段能力表，非 GUI view）

`shared/source_capabilities.json` 是 PB 唯一的「实体/字段能力表」，最接近 ViewList，但描述的是 **DATA 字段而非 GUI view**。15 个 source 各含 `source_id / kind / package / access`：

- `access.id_field`（如 calendar 的 event_id）
- `access.list_fields` / `access.details.fields`
- `access.details.action_prefix` → 决定语义 ref 前缀：`calendar.event` / `calendar.reminders` / `mail.body` / `clock.alarm` / `messages.get` / `contacts.get` / `mattermost.get` / `mastodon.get` / `taodian.get` / `files.get` / `docreader.get` / `gallery.get` / `camera.get` / `chrome.get` / `maps.get` / `settings.get`

**PB 没有 view_id 概念，也不声明「哪个 view 显示哪些字段」。**

### 3.3 simulator-extension —— gui-capabilities.json（真正的 GUI view 表）

`contracts/gui-capabilities.json`：

- `core_apps`：calendar(event_editor/calendar_list)、mail(mail_detail/mail_inbox)、clock(alarm_card)、contacts(contact_detail)、files(download_file/docreader_content)、messages(conversation_list)
- `extension_apps`：6 款（dudu/lianqi/starrail/babaquna/wangyiyun/chaowei）均只有 `popup` view，chaowei 额外有 `article_detail` view

`contracts/entity-catalog/*.json`（6 款扩展 app 实体目录）：

- `chaowei`：layout=reader，entity_types=articles（字段 semantic_ref/title/subtitle/meta/price/available/rank/body/topic_id）
- `dudu`：layout=map，entity_types=places（字段 semantic_ref/title/subtitle/meta/price/available/rank）

**但这是扩展端私有的表，PB 侧没有对应的 view 声明，两边靠硬编码对齐。**

---

## 四、全场景 gap 清单

| # | gap | 现状 | 影响 |
|---|-----|------|------|
| G1 | 源覆盖不对齐 | PB 15 source（含 mattermost/mastodon/taodian/gallery/camera/chrome/maps/settings），但 extension gui-capabilities 只有 core 6 + 扩展 6；扩展 6 款又不在 PB 的 source 里 | 全场景捕获缺源 |
| G2 | 适配器只支持 4 源 | `pb_backend_plan.py` `SUPPORTED_SOURCES=('calendar','mail','clock','contacts')`，其余全部 unsupported | 即使 gui_capture 有 files/docreader/messages 分支，backend_plan 不认 |
| G3 | 导航/readback 硬编码 | `gui_capture.py` `run()` 每 source 一个硬编码分支（calendar 用 direct_event、mail tap 主题、clock am start、contacts content URI、files content URI、扩展 process stop/start + tap title） | 新增 app 要改代码 |
| G4 | 「表包」硬编码在 compile.py | `EP_0004_PACKETS`/`EP_0005_PACKETS` 表驱动 packet→view/subject_ref/visible_assertions，靠 `EPISODE_PROFILES` 按 (cluster,episode) 注册 | 每新增 episode 要改代码 |
| G5 | 语义 ref 命名不一致 | 扩展 compile.py 用 `mail.message.mail_002`，PB GT 用 `mail.body.mail_002`（source_capabilities action_prefix 是 mail.body） | 对齐时对不上 |
| G6 | 扩展 app 无 PB 侧 source 定义 | 6 款 `com.ku.simulator.<app>` 不在 PB source_capabilities / cluster.json | 数据端无法给扩展 app 产实体/流/GT |

---

## 五、数据端（PB/数据集）的要求与问题

### 数据端要提供什么（职责）

1. **实体语义 ref + 字段值**（`active_world/{initialization,history,variants}`）—— 已有，但需扩展到全 15+ 源。
2. **被动流** `stream.jsonl`（text_event + screenshot_ref）—— 已有。
3. **每个 gui 包的捕获意图**：view_id + subject_ref + visible_assertions —— **目前缺失**，需新增或由共享契约推导。
4. **GT**（`private_gt.json`：need + evidence_sets，`A:<semantic_ref>` / `P:<packet_id>`）—— 已有，但语义 ref 需与扩展端对齐（G5）。

### 数据端的问题

- **P1**：PB 没有 view_id 概念，也没有「哪个 view 显示哪些字段」的声明 → 需要补一张 GUI view 能力表，或认领 extension 的 `gui-capabilities.json` 作为共享契约。
- **P2**：`source_capabilities.json` 描述的是 active_world 数据字段，与 GUI 屏幕上「可见字段」是两回事（数据字段 ≠ 屏幕显示字段），需要显式区分/映射。
- **P3**：`stream.jsonl` 里 gui 包的 `text_event` 是自然语言投影，不是结构化的 view/字段断言，扩展端无法直接从 text_event 得到 visible_assertions。
- **P4**：15 源里很多（gallery/camera/chrome/maps/settings 等）是「环境/背景」，未必有稳定的 GUI view，需要数据端明确哪些源要产 gui 图片、哪些只是背景 seed。
- **P5**：扩展 6 款 app 不在 PB source 里 → 数据端目前无法为它们产实体/流/GT，需先补 source 定义（或明确这些 app 走独立 pipeline）。

---

## 六、扩展端（simulator-extension）的要求与问题

### 扩展端要做什么（职责）

1. 消费「表包」（packet → view_id/subject_ref/visible_assertions），生成 capture 指令并执行（打开 app、导航、截图、uiautomator dump、断言）。
2. 导航/readback 从「硬编码分支」改成「表驱动」（每 app 一个 view 导航配置）。
3. seed/read 适配器覆盖全源（G2：SUPPORTED_SOURCES 从 4 扩到全）。
4. 校验/QA/发布（已有 gui_machine_qa / pb_gui_image_host / publish）。

### 扩展端的问题

- **E1**：`navigation_mode` / 导航路径硬编码在 `gui_capture.py`，扩展 app 只支持 popup + article_detail 两种 view，全场景需要每 app 声明「如何到达每个 view」。
- **E2**：`gui-capabilities.json` 是扩展端私有的，与 PB `source_capabilities.json` 没有绑定关系 → 需要一份把 source 实体映射到 view 的契约。
- **E3**：compile.py 的 `EPISODE_PROFILES` / `PACKETS` 表是手写的，不通用 → 需要下沉为「从 PB 数据 + 共享契约自动推导表包」。
- **E4**：visible_assertions 的字段名是扩展端自定的（如 CALENDAR_LIST_FIELDS），与 PB 的字段名（source_capabilities details.fields）需做一次字段映射。
- **E5**：扩展 app 的 entity-catalog 字段（title/subtitle/price/available/rank 等）在 PB 侧无对应 source → 全场景对齐时这些字段要纳入共享契约。

---

## 七、最小对齐建议（只做「文本数据集 → GUI 图片」，不含评测）

分三步，由浅入深：

**第 1 步：确立一份共享契约（三方认领一份 ViewList）**
- 以 `simulator-extension/contracts/gui-capabilities.json` 为蓝本，扩充成 `source → entity(语义ref前缀) → view_id → visible_assertion字段` 的完整表，覆盖 PB 的 15 源 + 6 扩展 app。
- 字段名以 PB `source_capabilities.json` 的 `details.fields` 为准（修正 G5 的 `mail.message` → `mail.body`）。
- 权威副本建议放 PB `shared/` 下，扩展端 compile 时读它（而非硬编码）。

**第 2 步：把「表包」生成从硬编码改为契约驱动**
- 扩展端 compile.py 不再手写 `EP_0004/EP_0005_PACKETS`，改为：读 PB `stream.jsonl` 的 gui 包 + 共享契约 → 自动推导 view_id/subject_ref/visible_assertions。
- subject_ref 从 active_world 实体 ref + GT 的 `A:ref` 对齐得到。

**第 3 步：把导航/readback/seed 从硬编码分支改为表驱动**
- `gui_capture.py` 的 per-source 分支改成读「每 app 的 view 导航配置」（reach_view: 动作序列 + 目标 view + 断言字段）。
- `pb_backend_plan.py` SUPPORTED_SOURCES 从 4 扩到全（或至少扩到要出图的源），每源配 seed/read 适配器。

**明确不做**：评测（release_gate / coverage_audit / LLM 打分）、数据集 GT 之外的任何评测逻辑。

---

## 八、给你的一句话答复

> view_id / subject_ref / visible_assertions 是「捕获意图」，本质是**数据端的语义**，但现在**被硬编码在扩展端**。要做全场景，正确姿势是：数据端(PB)认领语义（实体 ref + 字段 + view 契约），扩展端认领执行（消费契约 → 导航 → 截图 → 断言），中间靠一份共享的 `source → entity → view → fields` 契约打通，而不是各自维护一份硬编码表。
