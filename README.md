# KU 本地模拟器扩展（simulator-extension）使用文档

本包在已发布的 KU 模拟器之上做扩展，**不创建第二个 Android 环境**。KU 始终是 Docker、JDK/SDK、AVD、模拟器启动、noVNC 与 `init_state` 快照的唯一真源。本仓库只额外加入：六款 APK、本地 Controller 与数据覆盖层，以及复位编排。

锁定的运行时默认值在 `config/ku-runtime.env`：

- 镜像：`ghcr.io/yaosqz/knowu-bench:latest`
- JDK 17，Android SDK / Build Tools 34，API 34 x86_64
- AVD：`Pixel_8_API_34_x86_64`
- 快照：`init_state`

---

## 目录

0. [前置依赖与分发须知](#0-前置依赖与分发须知)
1. [快速开始（安装与启动）](#1-快速开始安装与启动)
2. [六款扩展 App 及其功能](#2-六款扩展-app-及其功能)
3. [Controller 通信模型与接口](#3-controller-通信模型与接口)
4. [Passive 流与 GUI 数据获取](#4-passive-流与-gui-数据获取)
5. [PB 契约](#5-pb-契约)
6. [生成 GUI 数据的 V1 闭环](#6-生成-gui-数据的-v1-闭环)
7. [安全与纪律](#7-安全与纪律)
8. [目录结构](#8-目录结构)
9. [工程文档导航](#9-工程文档导航)

---

## 0. 前置依赖与分发须知

> 打包分发给他人前请先确认以下三个外部依赖；本仓库只是扩展层，以下三者均**不在本仓库内**，缺其一对应功能无法运行。

1. **KU 运行时**：镜像 `ghcr.io/yaosqz/knowu-bench:latest`、Docker、KU 的 `mobile-world env run` 工具链。接收方要能拉镜像、有 Docker、装好 KU 客户端。本仓库只是扩展层，不含 KU。
2. **PB 基准树**：`pb_gui_bridge.py` / `pb_backend_plan.py` 靠 `--pb-root` 指向 ProactiveBench 树（`benchmark.json` + clusters + `episodes/*/manifest.json` + `passive_inputs/*/stream.jsonl`）。这份数据在仓库外，没有它 V1 闭环（10 步 / 13 字段 / 证据）跑不起来。
3. **真实设备帧**：`runtime/` 的回写 bundle、replay/证据 receipt 只以 sha256 绑定真实设备 PNG，7 帧不在仓库。对方只能复核离线链 + 派生 replay，无法离线复现 `ep_0004`/`ep_0005` 设备帧（除非有模拟器重采）。

---

## 1. 快速开始（安装与启动）

```bash
./scripts/bootstrap.sh          # 引导：校验 KU 环境、生成派生数据
./scripts/check-ku-runtime.sh   # 检查 KU 运行时（镜像/JDK/SDK/AVD 一致性）
./scripts/verify.sh             # 生成 web / Uni-app 输出
./scripts/build-apks.sh         # 构建六款 APK
./scripts/start-simulator.sh demo   # 启动一个实例
```

`start-simulator.sh` 不需要在线 ADB 设备：它把环境启动委托给 KU 自带的 `mobile-world env run`、默认镜像入口、`start_emulator.sh` 与 `start_novnc.sh`。已存在的 KU 容器会被激活（停止的容器经 KU 入口重启）或复用而不是重复创建；容器已发布的宿主端口必须与请求的端口集一致，否则 fail-closed。KU 报告模拟器就绪后，扩展会复用健康的 Controller 或新起一个，执行与 KU 任务相同的显式 `adb emu avd snapshot load init_state`，安装六款 APK，并建立 `adb reverse`。

命令只会在 Controller 健康端点与 KU 自身回环 noVNC 页（`/vnc.html`）都响应后，才打印 Controller 控制台、KU noVNC 与 KU backend 的 URL；任何启动失败都以非零退出且不打印 URL。`scripts/start-novnc.sh <id>` 为独立监控执行同样的 noVNC 探测。就绪等待时间可用 `KU_ADB_READY_TIMEOUT_SECONDS`（默认 600）与 `KU_NOVNC_READY_TIMEOUT_SECONDS`（默认 120）调整。停止一次性环境：

```bash
./scripts/stop-ku-environment.sh demo
```

并行实例传不冲突的 KU 端口集：

```bash
./scripts/start-simulator.sh demo-2 6900 7960 5900 5656
```

### 1.1 构建策略

`build-apks.sh` 在宿主机构建 Vue/Uni-app 资源，同步 Capacitor，然后在锁定的 KU 镜像内编译每个 Android 工程。工程使用 Capacitor 6、Java 17、`compileSdk 34` 与 `targetSdk 34`，与 KU 一致。无需私有 Android SDK 安装或单独 license 步骤。

`setup-android-sdk.sh` 仅作兼容命令保留，现在只校验 KU 镜像，不再下载另一套工具链。

### 1.2 复位策略

`scripts/reset-instance.sh <instance-id>` 围绕 KU 既有快照机制执行扩展复位：

1. 阻塞扩展写入并停止六款扩展 App；
2. 在 KU 容器内执行 `adb emu avd snapshot load init_state`；
3. 等待 Android 启动，重新安装六款 APK；
4. 重建 Controller SQLite 状态与 `adb reverse`；
5. 校验所有扩展 APK 哈希。

该脚本从不保存或覆盖 `init_state`，从不修改镜像，也不清理 KU 全局通知或数据。删除一次性 KU 容器即丢弃其可写 Android 覆盖层。

打包的只读数据集只在 manifest 版本变化时升级；同版本下校验和失配会把实例标记为 `CONTAMINATED`，复位不会掩盖或修复该状态。

按蓝图要求的 50 次设备复位浸泡测试：

```bash
./scripts/device-reset-soak.sh demo 50
```

---

## 2. 六款扩展 App 及其功能

六款 App 是请求产品品类的独立研究复刻：聚合出行、预约出行、官方铁路购票、旅行比价、音乐社区、知识社区。它们的启动器图均为原创，不含任何抄袭商标；产品标签与公开事实可在 `apps/app-definitions.json` 中配置。

| App ID | 名称 | 包名 | 领域 | 原型 |
|---|---|---|---|---|
| `dudu` | 嘟嘟 | `com.ku.simulator.dudu` | 出行 | 滴滴式聚合出行 |
| `lianqi` | 连骑 | `com.ku.simulator.lianqi` | 出行 | 预约制出行 |
| `starrail` | 星轨 | `com.ku.simulator.starrail` | 出行 | 官方铁路购票 |
| `babaquna` | 八八去哪 | `com.ku.simulator.babaquna` | 出行 | 旅行比价 |
| `wangyiyun` | 王易云 | `com.ku.simulator.wangyiyun` | 音乐 | 音乐社区 |
| `chaowei` | 超维 | `com.ku.simulator.chaowei` | 知识 | 知识社区 |

每款 App 有专属的领域 planner 与产品布局。「我的」标签页内置 10 个 bootstrap 配置的确定性场景：权限请求、促销弹窗、库存不足、价格变化、支付确认、取消损失、时间冲突、重复收藏、会员内容、网络失败。乘车人、歌单与笔记输入使用 App 自有表单；支付、取消、退票、改签使用 App 自有确认弹窗。

临时真实感数据来自 `datasets/fixtures/research-seed-v1.json`，显式标记为 `synthetic_research_fixture`；前若干人工精选行之后跟着相同的确定性生成目录，保持每种资源 1 万行。`datasets/fixtures/README.md` 中的替换规则保证接入后续数据源时 bootstrap、列表、动作、复位与快照接口不变。

App 只使用 `http://127.0.0.1:8080`；`adb reverse` 把该地址映射到仅回环的 Controller。真机飞行模式冷启动测试确认扩展在无公网、无远端服务时也能工作。

---

## 3. Controller 通信模型与接口

### 3.1 启动方式与认证

Controller 是零依赖的本地 HTTP 服务（`local-controller/server.py`，`ThreadingHTTPServer`，监听 `127.0.0.1`），SQLite 权威在 `local-controller/store.py`。启动：

```bash
python3 local-controller/server.py \
  --instance-id demo \
  --runtime runtime/instances/demo \
  --port 8080 \
  --adb-serial emulator-5554 \
  --avd Pixel_8_API_34_x86_64 \
  --snapshot init_state \
  --ku-container <容器名> \
  --sim-time '2026-09-20T09:00:00+08:00'
```

- **Runner 管理路由**（`/local/v1/...`）需要请求头 `X-Runner-Key`，值来自该实例 `runtime/instances/<id>/instance.json` 里的 `runner_key`（首次生成，32+ 字符，`secrets.token_urlsafe`）。
- 结构化 `query` 与 Passive `events` 是只读路由。
- bootstrap 声明 `adapter_contract_version: 1.0`。

### 3.2 统一返回信封

适配器接口（capabilities / restore_baseline / seed / read_state / query / drain_events / read_mutations / teardown / advance_time / user_memory）统一返回信封（`contracts/envelope.schema.json`，已冻结）：

```json
{
  "status": "ok",
  "source": "dudu",
  "operation": "read_state",
  "semantic_ref": null,
  "record_version": "dudu.v3",
  "observed_at": "2026-09-20T09:00:00+08:00",
  "coverage": null,
  "data": {},
  "error": null
}
```

- `status` 枚举：`ok` / `not_found` / `permission_denied` / `stale` / `timeout` / `backend_error` / `feature_off`。
- `record_version` 以 `app.vN` 形式暴露；`semantic_ref` / `record_version` / `coverage` / `error` 可为 null。
- 错误码全集在 `contracts/error-codes.json`（冻结，新增/删除/改名必须先改蓝图并同步 `tests/test_adapter_contract.py`）。

### 3.3 路由分组

- **`/runtime/v1/...`**：App 侧调用（bootstrap、数据读取、actions、telemetry、state）。
- **`/local/v1/...`**：Runner/外部消费者调用（seed、query、passive 流、设备、复位），多数需 `X-Runner-Key`。
- 世界增量 `add`/`update`/`delete` 要求精确声明 `allowed_changed_fields`。

### 3.4 接口清单

**健康 / 状态（无需鉴权）**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | `{status, lifecycle, instance_id}` |
| GET | `/local/v1/instance/status` | 实例信息 + 生命周期 |
| GET | `/local/v1/device/status` | ADB 状态、boot 完成、reverse 列表、各 APK 是否安装 |
| GET | `/local/v1/snapshot/status` | KU 容器内 AVD 快照列表 |
| GET | `/local/v1/sqlite/summary` | SQLite 各表行数与字节统计 |
| GET | `/local/v1/clock` | 当前模拟时钟 |

**App 运行时接口（`/runtime/v1/...`）**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/runtime/v1/apps/<app>/bootstrap` | 返回 `contract_version:1.0`、`adapter_contract_version:1.0`、`instance`、`dataset`、`seed`、`simulated_time`、`simulated_location`、`feature_flags`、`initial_state_ref`、`scenarios`；带 `ETag`，`If-None-Match` 命中返回 304 |
| GET | `/runtime/v1/apps/<app>/data/<resource>` | 分页读取资源（`limit`/`offset`/`q`） |
| POST | `/runtime/v1/apps/<app>/actions` | 提交一个动作（需 `action_id`、`type`、`payload`、`expected_revision`，幂等） |
| POST | `/runtime/v1/apps/<app>/telemetry` | 上报 UI 事件 |
| GET | `/runtime/v1/apps/<app>/state` | 当前 `{revision, state}`（`/local/v1/...` 同） |

**适配器接口（`/local/v1/...`，多数需 `X-Runner-Key`）**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/local/v1/apps/<app>/seed` | 幂等 seed：`common_records` + `world_deltas`，返回 receipt（含 `readback_hash`、`applied`） |
| POST | `/local/v1/apps/<app>/read_state` | 读取 App 状态（`scope`） |
| POST | `/local/v1/apps/<app>/query` | 结构化查询（`operation`/`selector`/`projection`/`as_of`） |
| POST | `/local/v1/apps/<app>/restore_baseline` | 恢复基线（校验 `baseline_ref` 与 `episode_scope`） |
| POST | `/local/v1/apps/<app>/teardown` | 恢复 Episode 的 GUI 前状态并清理注入数据（保留 mutation 审计） |
| GET | `/local/v1/apps/<app>/capabilities` | 返回该 App 实体目录（`contracts/entity-catalog/<app>.json`） |
| GET | `/local/v1/apps/<app>/mutations?since_cursor=N` | 变更审计流（mutation journal） |
| GET | `/local/v1/apps/<app>/passive/events?cursor=N&limit=N` | **抽取被动流**（见 §4） |
| GET | `/local/v1/apps/<app>/events?limit=N` | 事件流（event.schema.json） |
| POST | `/local/v1/apps/<app>/data/<resource>` | 注入记录（`items`） |
| POST | `/local/v1/apps/<app>/process` | `{action: start|stop}` 起停 App 进程（`am start` / `force-stop`） |
| POST | `/local/v1/apps/<app>/notification` | 推送通知（仅 `dudu`；经广播 `NotificationReceiver`） |
| POST | `/local/v1/apps/user_memory/query` | 用户记忆（默认 `feature_off`，需 `feature_flag` 开启） |

**实例 / 设备 / 故障注入**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/local/v1/instance/advance_time` | 推进模拟时钟（`logical_time`，ISO-8601 带时区，只增不减） |
| POST | `/local/v1/instance/freeze` / `unfreeze` | 冻结 / 解冻写操作 |
| POST | `/local/v1/instance/feature_flag` | 切换特性开关（`{flag, enabled}`） |
| POST | `/local/v1/instance/reset` | 校验数据集哈希后重建 SQLite 状态 |
| POST | `/local/v1/instance/export` | 导出实例 zip（instance.json + state.sqlite + events + 数据集） |
| POST | `/local/v1/instance/init` / `lifecycle` | 生命周期迁移 |
| POST | `/local/v1/device/screenshot` | `adb exec-out screencap -p` 截图并存入被动流 |
| GET | `/local/v1/screenshots/<ts>.png` | 取回截图 |
| GET/POST | `/local/v1/faults` | 查询 / 注入故障（`offline`/`delay`/`timeout`/`http_error`/`intermittent`/`pagination_failure`） |
| GET | `/local/v1/events?limit=N` | 全量事件流 |

调试控制台在 `/console`（`debug-console/index.html`）。

---

## 4. Passive 流与 GUI 数据获取

### 4.1 被动流（`/local/v1/apps/<app>/passive/events`）

App 的动作、弹窗、通知、截图与故障注入会以被动包（passive packet）写入 SQLite `passive_packets` 表。消费方用游标增量抽取：

```bash
curl -s -H 'X-Runner-Key: <runner_key>' \
  'http://127.0.0.1:8080/local/v1/apps/dudu/passive/events?cursor=0&limit=100'
```

返回（信封 `data` 内）：

```json
{
  "status": "ok",
  "source": "dudu",
  "operation": "drain_events",
  "observed_at": "2026-09-20T09:00:00+08:00",
  "data": {
    "packets": [
      {
        "packet_id": "<uuid>",
        "sequence": 1,
        "source": "dudu",
        "type": "user_action",
        "observed_at": "2026-09-20T09:00:00+08:00",
        "delivered_at": "2026-09-20T09:00:05+08:00",
        "text_projection": "订单已确认",
        "screenshot_ref": null
      }
    ],
    "next_cursor": 1,
    "has_more": false
  }
}
```

- 抽取后（未冻结时）`delivered_at` 会被置为当前模拟时钟。
- `type` 取值：`gui`（GUI 动作 / 截图）、`user_action`（用户动作）、`app_event`（弹窗脚本）、`notification`（通知 / 故障注入）。
- `screenshot_ref` 仅在截图相关包中非空（指向 `/local/v1/screenshots/<ts>.png`）。

### 4.2 事件流与变更审计流

- **事件流** `GET /local/v1/apps/<app>/events`：`event.schema.json` 结构，`event_kind` ∈ `domain` / `telemetry` / `debug`，含 `correlation_id`、`causation_id`、`simulated_at`（模拟时间）与 `written_at`（墙钟时间）。
- **变更审计流** `GET /local/v1/apps/<app>/mutations`：`before_version` / `after_version`（`app.vN`）、`changed_fields`、`actor`、`status`（`committed`/`rejected`）、`trace_ref`，按 `since_cursor` 增量读取。

---

## 5. PB 契约

本包为 ProactiveBench（PB）GUI 全流程对齐定义了冻结契约。所有新 schema/receipt 一律 **fail-closed**，任何校验失配都中止而非猜测。

### 5.1 十三个冻结字段（V1 最小交付）

`source` / `entity_ref` / `fields` / `observed_at` / `delivered_at` / `adapter` / `seed_projection` / `history_replay` / `readback` / `surface` / `view_id` / `visible_assertions` / `unsupported`（及 `reject`）。

### 5.2 PB 树结构与 stream.jsonl 格式

PB 树由 `benchmark.json` 注册 clusters，每个 cluster 的 manifest 通过 `*_ref` 指向 passive 流、history 与 initialization。`pb_gui_bridge.py` 只读地把一条 Episode 的被动流投影为私有 GUI 计划。

**`passive_inputs/<passive_id>/stream.jsonl` 每行一个包，必需字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `packet_id` | string | 包唯一 ID |
| `sequence` | int ≥ 1 | 严格递增 |
| `tag` | string | 包类型；`gui` 表示 GUI 包 |
| `source` | string | 来源 |
| `observed_at` | string | ISO-8601，必须带 UTC 偏移 |
| `delivered_at` | string | 同上，且 ≥ `observed_at` |
| `text_event` | string | 非空文本投影 |
| `screenshot_ref` | string | 仅 `tag=gui` 可带，指向 `gui/<packet_id>.png` |
| `screenshot_sha256` | string | 与 `screenshot_ref` 成对，64 位 hex，须与实际 PNG 哈希一致 |

被拒绝的内容（fail-closed）：遗留字段 `type` / `text_projection`；非 GUI 包携带截图字段；任何私有 GT 字段（`gt` / `gold` / `label` / `answer` / `oracle` / `need` / `evidence_sets` / `passive_class` / `public_rule` / `support` / `contradict` …）；`*_ref` 逃逸出 `--pb-root` 或不存在；`screenshot_sha256` 与磁盘不符。

### 5.3 Schema 清单

| Schema | 产物 / 命令 | 说明 |
|---|---|---|
| `pb-gui-bridge.plan.v1` | `pb_gui_bridge.py plan` | PB passive 流 → 私有 GUI 计划（仅公开字段 + 配对截图） |
| `pb-backend-plan.v1` | `pb_backend_plan.py plan` | PB Episode → 离线后端计划（`seed.data` / `readback.data` / `history` / `unsupported`） |
| `pb-gui-private-capture-plan.v1` | `pb_gui_capture_compile.py` | 桥 + 后端计划 + apply 收据 + gate → 私有设备采集计划 |
| `pb-gui-capture-gate.v1` | `pb_gui_capture_gate.py` | 采集闸门（`ready_for_device_capture` 才放行） |
| `pb-gui-release-report.v1` | `pb_gui_release_gate.py` | 发布报告（绑定 QA 与采集收据） |
| `pb-gui-image-host-receipt.v1` / `pb-gui-image-capabilities.v1` | `pb_gui_image_host.py` | 图片回放宿主 + 能力声明 |
| `pb-gui-passive-image-bundle.v1` | `pb_gui_image_host.py writeback` | 回写 PB GUI 图片引用的持久私有 bundle（`pb_writes=0`） |
| `pb-gui-v1-acceptance-evidence.v1` | `pb_gui_v1_evidence.py build/verify/checklist` | V1 验收证据 bundle |

### 5.4 后端适配器契约（`pb-backend-plan.v1`）

`ADAPTERS` 映射（`scripts/pb_backend_plan.py`）：

| source | module | seed 接口 | read 接口 |
|---|---|---|---|
| `calendar` | `scripts/gui_calendar.py` | `seed_event` | `read_event` |
| `mail` | `scripts/gui_mail.py` | `seed_mail` | `read_mail` |
| `clock` | `scripts/gui_clock.py` | `seed_alarm` | `read_alarm` |
| `contacts` | `scripts/gui_contacts.py` | `seed_contact` | `read_contact` |

其它可访问的 world source 一律以 `unsupported` + 原因输出（9 类来源）。每个实体记录 `record_version` 暴露为 `app.vN`。

### 5.5 GUI 能力清单（`contracts/gui-capabilities.json`）

**核心 App（pilot）**：`calendar`（Fossify events.db，视图 `event_editor` / `calendar_list`）、`mail`（Gmailclone state.json，`mail_detail` / `mail_inbox`）、`clock`（DeskClock alarms.db，`alarm_card`）、`contacts`（Android ContactsProvider，`contact_detail`）、`files`（`download_file` / `docreader_content`）、`messages`（`conversation_list`）。

**扩展 App（supported）**：六款 App 均以 `popup` 视图（`title` / `message`）受支持；`chaowei` 额外有 `article_detail`（`title` / `subtitle` / `body`，`article_detail_device_status=unverified`）。

系统级 `heads_up_notification` 当前为 `missing`。

### 5.6 私有采集输入契约（`pb-gui-private-capture-plan.v1`）

采集入口 `gui_capture.py` 只接受 `pb_gui_capture_compile.py` 编译的 PB 私有计划，并在 `run` 前对 schema 版本、`episode_scope`、`source_assets`、时钟窗口与 GT/绝对路径泄漏做 fail-closed 复验。

---

## 6. 生成 GUI 数据的 V1 闭环

V1 范围冻结为「一条可复用闭环」（蓝图 §1.1），覆盖 `meeting-preparation-001` 的 `ep_0004`（p002/w001）与 `ep_0005`（p003/w002）。十步链（均为 scripts 下的可执行脚本）：

1. **冻结契约** → `pb_gui_bridge.py`
2. **PB Passive 适配/编译** → `pb_backend_plan.py`
3. **PB 数据 → adapter_plan** → `pb_backend_plan.py`
4. **adapter_plan → 真实 App 初始化接口** → `pb_backend_apply.py`（经真 `adb` 调 `seed_*` / `read_*` / `assert_absent` / `apply_history_change`）
5. **seed / history replay / 独立 readback** → 同上（Calendar reminder `[-1,-1,-1]→[10,-1,-1]`、Mail unread→read 重放）
6. **指定页面/实体导航** → `pb_gui_capture_compile.py`（EPISODE_PROFILES 表驱动 source/view_id/visible_assertions）
7. **ADB 原始截图** → `gui_capture.py`（`exec-out screencap -p`、`uiautomator dump`、`input tap`、截图前 `date -Is` 墙钟采样）
8. **GUI 像素 QA / 哈希 / 复位收据** → `gui_machine_qa.py`（规则 `gui-pixel-ocr-v8`，阈值 80，CJK `chi_sim` / 状态 `eng`）
9. **回写 PB GUI 图片引用** → `pb_gui_image_host.py writeback`（`pb-gui-passive-image-bundle.v1`）
10. **PB image replay 验证** → `pb_gui_image_host.py`（replay receipt）

各闸门用 sha256 互相绑定形成依赖锁：`pb_gui_capture_gate.py` → `gui_capture.py` → `gui_machine_qa.py` → `pb_gui_release_gate.py` → `pb_gui_image_host.py` → `pb_gui_coverage_audit.py`。

**V1 单一验收入口**（离线复验后刷新验收清单）：

```bash
python3 scripts/pb_gui_v1_evidence.py checklist --bundle docs/v1-acceptance-evidence-20260926
# → 生成 docs/v1-acceptance-evidence-20260926/V1-ACCEPTANCE.md
#   verdict: satisfied，steps=10/10，frozen_fields=13/13
```

证据 bundle（每 Episode 8 个收据文件 + 7 帧设备帧，设备 PNG 以哈希 + 复现命令代替，不入库）：`docs/v1-acceptance-evidence-20260926/`。

---

## 7. 安全与纪律

- **不改真源**：不修改 KU 原码 / 镜像 / APK / `init_state` / PB runtime / prompts / GT。
- **不泄漏**：`pb.private_key_paths` 与本机绝对路径不得进入任何产物；所有 `*_ref` 必须落在 `--pb-root` 内。
- **fail-closed**：任何哈希 / 范围 / 版本漂移都中止；拒遗留 `type`/`text_projection` 字段、坏哈希、GT 泄漏。
- **不污染 PB 树**：回写落点是仓库内持久私有 bundle，`pb_writes=0`、`pb_tree_modified=false`，不把 PB 原图冒充设备帧（`device_frames_differ_from_pb=true`）。
- **设备帧不入库**：真实设备 PNG 以 sha256 绑定，重建需私有采集目录。
- **不导入 KU 代码**：扩展代码不 import `knowu_bench`，不调用 KU Bench 任务 API。

---

## 8. 目录结构

- `apps/`：六款 Vue 3 / Uni-app / Capacitor 应用（含 `app-definitions.json`）。
- `local-controller/`：零依赖本地 Controller（`server.py` / `store.py` / `datasets.py`）与 SQLite 权威。
- `contracts/`：扩展契约（信封 / 事件 / 错误码 / GUI 采集 / 能力 / 实体目录）。
- `datasets/`：合成夹具与生成目录（`fixtures/`、`generated/`）。
- `config/ku-runtime.env`：锁定的 KU 运行时身份与端口默认值。
- `scripts/`：KU 委托、APK 构建、Controller 与复位编排、PB GUI 全链路。
- `tests/`：契约、复位、性能与 KU 对齐检查。
- `runtime/`：各实例运行时数据与派生 stage（回写 bundle、replay receipt 等）。
- `docs/`：蓝图、工程日志、V1 验收证据。

---

## 9. 工程文档导航

- 当前实施蓝图：[`docs/ProactiveBench_GUI_全流程对齐与后端扩充实施蓝图_20260926.md`](docs/ProactiveBench_GUI_全流程对齐与后端扩充实施蓝图_20260926.md)
- 当前工程日志：[`docs/PB-GUI工程日志.md`](docs/PB-GUI工程日志.md)
- V1 验收证据 bundle（ep_0004/ep_0005，仓库可见、可离线复验）：[`docs/v1-acceptance-evidence-20260926/README.md`](docs/v1-acceptance-evidence-20260926/README.md)
- **V1 单一验收入口**：`python3 scripts/pb_gui_v1_evidence.py checklist --bundle docs/v1-acceptance-evidence-20260926`
