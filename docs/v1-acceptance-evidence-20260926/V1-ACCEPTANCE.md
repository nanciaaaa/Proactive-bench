# V1 验收清单（单一入口）

- schema: `pb-gui-v1-acceptance-checklist.v1`（由 `pb_gui_v1_evidence.py checklist` 生成）
- 生成时间: 2026-09-26T14:58:52.305370+00:00
- 证据 bundle: `docs/v1-acceptance-evidence-20260926`（离线复验 `evidence_verified` 通过后才写出）
- 判定: **satisfied** — V1 闭环 2 条 Episode（ep_0004, ep_0005）、14 个 GUI packet、10/10 步、13/13 冻结字段均有证据。
- 范围: 仅蓝图 §1.1 的 10 步闭环 + 13 冻结字段；§1.2 的 V2 项在本文件里一律记为 `not-applicable (V2)`，不作为 V1 判定条件。

完整文件哈希：见本 bundle `README.md` 的 file 表与 `<episode>/episode-evidence.json#files`；下表用前 12 位哈希定位同一文件。

## ep_0004 — meeting-preparation-001/p002/w001

- 结论: release `release_ready`、capture `capture_complete`（reset_verified=True）、QA `pass`（rule `gui-pixel-ocr-v8`）、replay `replay_verified`、writeback `image_refs_written`（pb_writes=0）
- 关键复核值: frames=7，by_source={"calendar": 3, "mail": 4}，adapter records=4，history replays=2，replay windows=13，image attachments=51，distinct frames=7，summary_fallbacks=0
- 时间绑定: PB stream `47a0b52fa2de`，逐包 observed/delivered 共 7 条（见 `pb-image-refs-ep_0004.json#pb_packet_times`）

### A. 10 步闭环

| 步 | 步骤 | 判定 | 证据（文件#路径，哈希前 12 位） |
|---|---|---|---|
| 1 | 冻结 PB 抽象适配契约 | satisfied | `pb-backend-apply-receipt-ep_0004.json#plan_binding.episode_scope`（`91edad08c5e2`）<br>`pb-capture-receipt-ep_0004.json#source_assets`（`6fea1d7a30ac`） |
| 2 | PB Passive 适配/编译 | satisfied | `pb-capture-receipt-ep_0004.json#source_assets.gui_plan`（`6fea1d7a30ac`）<br>`pb-capture-receipt-ep_0004.json#source_assets.backend_plan`（`6fea1d7a30ac`）<br>`pb-capture-receipt-ep_0004.json#source_assets.gate_plan`（`6fea1d7a30ac`）<br>`pb-gui-image-host-receipt-ep_0004.json#bindings.pb`（`4a8c5c002529`） |
| 3 | PB 数据 → adapter_plan | satisfied | `pb-backend-apply-receipt-ep_0004.json#plan_binding.plan_sha256`（`91edad08c5e2`）<br>`pb-backend-apply-receipt-ep_0004.json#records[].semantic_ref`（`91edad08c5e2`） |
| 4 | adapter_plan → 真实 App 初始化接口 | satisfied | `pb-backend-apply-receipt-ep_0004.json#records[].adapter`（`91edad08c5e2`）<br>`pb-backend-apply-receipt-ep_0004.json#ordering`（`91edad08c5e2`） |
| 5 | seed / history replay / 独立 readback | satisfied | `pb-backend-apply-receipt-ep_0004.json#records[].seed`（`91edad08c5e2`）<br>`pb-backend-apply-receipt-ep_0004.json#records[].readback`（`91edad08c5e2`）<br>`pb-capture-receipt-ep_0004.json#seed_receipts`（`6fea1d7a30ac`）<br>`pb-capture-receipt-ep_0004.json#history_replays`（`6fea1d7a30ac`）<br>`pb-capture-receipt-ep_0004.json#reset_verified`（`6fea1d7a30ac`） |
| 6 | 指定 App 页面与实体导航 | satisfied | `pb-capture-receipt-ep_0004.json#frames[].navigation`（`6fea1d7a30ac`）<br>`pb-capture-receipt-ep_0004.json#frames[].package`（`6fea1d7a30ac`）<br>`pb-capture-receipt-ep_0004.json#frames[].activity`（`6fea1d7a30ac`）<br>`pb-capture-receipt-ep_0004.json#frames[].ready.visible_assertions`（`6fea1d7a30ac`） |
| 7 | ADB 原始截图 | satisfied | `pb-capture-receipt-ep_0004.json#frames[].png`（`6fea1d7a30ac`）<br>`pb-capture-receipt-ep_0004.json#frames[].sha256`（`6fea1d7a30ac`）<br>`pb-capture-receipt-ep_0004.json#frames[].ui_dump_sha256`（`6fea1d7a30ac`）<br>`pb-image-refs-ep_0004.json#frames[].bytes`（`071393252a07`）<br>`pb-image-refs-ep_0004.json#frames[].width`（`071393252a07`） |
| 8 | GUI 像素 QA、哈希、复位收据 | satisfied | `pb-machine-qa-summary-ep_0004.json#frames[].status`（`ca7b51dd7c3c`）<br>`pb-machine-qa-summary-ep_0004.json#frames[].fields[].confidence`（`ca7b51dd7c3c`）<br>`pb-machine-qa-summary-ep_0004.json#rule_version`（`ca7b51dd7c3c`）<br>`pb-gui-release-report-ep_0004.json#bindings.machine_qa`（`902257b3de44`） |
| 9 | 回写 PB GUI 图片引用 | satisfied | `pb-passive-image-bundle-ep_0004.json#status`（`2f0e6731cb70`）<br>`pb-passive-image-bundle-ep_0004.json#writeback.pb_writes`（`2f0e6731cb70`）<br>`pb-passive-image-bundle-ep_0004.json#files[image-refs.jsonl]`（`2f0e6731cb70`）<br>`pb-image-refs-ep_0004.json#pb_time_binding`（`071393252a07`）<br>`pb-image-refs-ep_0004.json#frames[].staged_ref`（`071393252a07`） |
| 10 | PB image replay 验证 | satisfied | `pb-gui-image-replay-receipt-ep_0004.json#status`（`b121c795d4c4`）<br>`pb-gui-image-replay-receipt-ep_0004.json#request_count`（`b121c795d4c4`）<br>`pb-gui-image-replay-receipt-ep_0004.json#distinct_frames`（`b121c795d4c4`）<br>`pb-gui-image-replay-receipt-ep_0004.json#summary_fallbacks`（`b121c795d4c4`） |

### B. 13 个冻结字段

| # | 冻结字段 | 判定 | 证据（文件#路径，哈希前 12 位） |
|---|---|---|---|
| 1 | `source` | bound | `pb-image-refs-ep_0004.json#frames[].source`（`071393252a07`）<br>`pb-capture-receipt-ep_0004.json#frames[].source`（`6fea1d7a30ac`） |
| 2 | `entity_ref` | bound | `pb-backend-apply-receipt-ep_0004.json#records[].semantic_ref`（`91edad08c5e2`）<br>`pb-capture-receipt-ep_0004.json#frames[].readback.import_id`（`6fea1d7a30ac`） |
| 3 | `fields` | bound | `pb-machine-qa-summary-ep_0004.json#frames[].fields[].field`（`ca7b51dd7c3c`）<br>`pb-machine-qa-summary-ep_0004.json#frames[].fields[].passed`（`ca7b51dd7c3c`） |
| 4 | `observed_at` | bound | `pb-image-refs-ep_0004.json#frames[].observed_at`（`071393252a07`）<br>`pb-capture-receipt-ep_0004.json#frames[].observed_at`（`6fea1d7a30ac`） |
| 5 | `delivered_at` | bound | `pb-image-refs-ep_0004.json#pb_packet_times[].delivered_at`（`071393252a07`）<br>`pb-image-refs-ep_0004.json#pb_time_binding.image_refs_sha256`（`071393252a07`） |
| 6 | `adapter` | bound | `pb-backend-apply-receipt-ep_0004.json#records[].adapter.module`（`91edad08c5e2`）<br>`pb-backend-apply-receipt-ep_0004.json#records[].adapter.seed`（`91edad08c5e2`）<br>`pb-backend-apply-receipt-ep_0004.json#records[].adapter.read`（`91edad08c5e2`） |
| 7 | `seed_projection` | bound | `pb-backend-apply-receipt-ep_0004.json#records[].seed.result`（`91edad08c5e2`）<br>`pb-backend-apply-receipt-ep_0004.json#records[].seed.at`（`91edad08c5e2`） |
| 8 | `history_replay` | bound | `pb-backend-apply-receipt-ep_0004.json#history_replays[].set`（`91edad08c5e2`）<br>`pb-capture-receipt-ep_0004.json#history_replays[].result`（`6fea1d7a30ac`） |
| 9 | `readback` | bound | `pb-backend-apply-receipt-ep_0004.json#records[].readback.result`（`91edad08c5e2`）<br>`pb-capture-receipt-ep_0004.json#frames[].readback`（`6fea1d7a30ac`） |
| 10 | `surface` | bound | `pb-capture-receipt-ep_0004.json#frames[].package`（`6fea1d7a30ac`）<br>`pb-gui-release-report-ep_0004.json#frames[].png`（`902257b3de44`） |
| 11 | `view_id` | bound | `pb-capture-receipt-ep_0004.json#frames[].activity`（`6fea1d7a30ac`）<br>`pb-capture-receipt-ep_0004.json#frames[].navigation`（`6fea1d7a30ac`） |
| 12 | `visible_assertions` | bound | `pb-capture-receipt-ep_0004.json#frames[].ready.visible_assertions`（`6fea1d7a30ac`）<br>`pb-machine-qa-summary-ep_0004.json#frames[].fields[].text`（`ca7b51dd7c3c`） |
| 13 | `unsupported/reject` | bound | `pb-gui-release-report-ep_0004.json#counts.unsupported_targets`（`902257b3de44`）<br>`pb-gui-release-report-ep_0004.json#counts.gate_targets`（`902257b3de44`）<br>`pb-machine-qa-summary-ep_0004.json#device_env.status`（`ca7b51dd7c3c`） |

### C. fail-closed 不变量

- `release_report_valid` = `true`
- `capture_receipt_bound_to_release_report` = `true`
- `capture_receipt_bound_to_private_plan` = `true`
- `machine_qa_binds_capture_run` = `true`
- `backend_receipt_bound_to_release_report` = `true`
- `host_receipt_bound_to_capture_and_release` = `true`
- `replay_receipt_bound_to_host_receipt` = `true`
- `image_bundle_bound_to_host_and_replay` = `true`
- `frames_cross_bound` = `7`
- `pb_packet_times_bound` = `7`
- `device_frames_differ_from_pb` = `true`
- `image_bundle_pb_writes` = `0`
- `raw_pngs_stored_in_repo` = `false`（设备 PNG 不入库）

## ep_0005 — meeting-preparation-001/p003/w002

- 结论: release `release_ready`、capture `capture_complete`（reset_verified=True）、QA `pass`（rule `gui-pixel-ocr-v8`）、replay `replay_verified`、writeback `image_refs_written`（pb_writes=0）
- 关键复核值: frames=7，by_source={"calendar": 3, "mail": 4}，adapter records=4，history replays=3，replay windows=19，image attachments=58，distinct frames=7，summary_fallbacks=0
- 时间绑定: PB stream `ef2ae30c5bbc`，逐包 observed/delivered 共 7 条（见 `pb-image-refs-ep_0005.json#pb_packet_times`）

### A. 10 步闭环

| 步 | 步骤 | 判定 | 证据（文件#路径，哈希前 12 位） |
|---|---|---|---|
| 1 | 冻结 PB 抽象适配契约 | satisfied | `pb-backend-apply-receipt-ep_0005.json#plan_binding.episode_scope`（`3557a5608f34`）<br>`pb-capture-receipt-ep_0005.json#source_assets`（`f6b44faba963`） |
| 2 | PB Passive 适配/编译 | satisfied | `pb-capture-receipt-ep_0005.json#source_assets.gui_plan`（`f6b44faba963`）<br>`pb-capture-receipt-ep_0005.json#source_assets.backend_plan`（`f6b44faba963`）<br>`pb-capture-receipt-ep_0005.json#source_assets.gate_plan`（`f6b44faba963`）<br>`pb-gui-image-host-receipt-ep_0005.json#bindings.pb`（`9613367fba80`） |
| 3 | PB 数据 → adapter_plan | satisfied | `pb-backend-apply-receipt-ep_0005.json#plan_binding.plan_sha256`（`3557a5608f34`）<br>`pb-backend-apply-receipt-ep_0005.json#records[].semantic_ref`（`3557a5608f34`） |
| 4 | adapter_plan → 真实 App 初始化接口 | satisfied | `pb-backend-apply-receipt-ep_0005.json#records[].adapter`（`3557a5608f34`）<br>`pb-backend-apply-receipt-ep_0005.json#ordering`（`3557a5608f34`） |
| 5 | seed / history replay / 独立 readback | satisfied | `pb-backend-apply-receipt-ep_0005.json#records[].seed`（`3557a5608f34`）<br>`pb-backend-apply-receipt-ep_0005.json#records[].readback`（`3557a5608f34`）<br>`pb-capture-receipt-ep_0005.json#seed_receipts`（`f6b44faba963`）<br>`pb-capture-receipt-ep_0005.json#history_replays`（`f6b44faba963`）<br>`pb-capture-receipt-ep_0005.json#reset_verified`（`f6b44faba963`） |
| 6 | 指定 App 页面与实体导航 | satisfied | `pb-capture-receipt-ep_0005.json#frames[].navigation`（`f6b44faba963`）<br>`pb-capture-receipt-ep_0005.json#frames[].package`（`f6b44faba963`）<br>`pb-capture-receipt-ep_0005.json#frames[].activity`（`f6b44faba963`）<br>`pb-capture-receipt-ep_0005.json#frames[].ready.visible_assertions`（`f6b44faba963`） |
| 7 | ADB 原始截图 | satisfied | `pb-capture-receipt-ep_0005.json#frames[].png`（`f6b44faba963`）<br>`pb-capture-receipt-ep_0005.json#frames[].sha256`（`f6b44faba963`）<br>`pb-capture-receipt-ep_0005.json#frames[].ui_dump_sha256`（`f6b44faba963`）<br>`pb-image-refs-ep_0005.json#frames[].bytes`（`6643863a8afb`）<br>`pb-image-refs-ep_0005.json#frames[].width`（`6643863a8afb`） |
| 8 | GUI 像素 QA、哈希、复位收据 | satisfied | `pb-machine-qa-summary-ep_0005.json#frames[].status`（`6fb8c176a83f`）<br>`pb-machine-qa-summary-ep_0005.json#frames[].fields[].confidence`（`6fb8c176a83f`）<br>`pb-machine-qa-summary-ep_0005.json#rule_version`（`6fb8c176a83f`）<br>`pb-gui-release-report-ep_0005.json#bindings.machine_qa`（`99be7918f5f4`） |
| 9 | 回写 PB GUI 图片引用 | satisfied | `pb-passive-image-bundle-ep_0005.json#status`（`4606563195bb`）<br>`pb-passive-image-bundle-ep_0005.json#writeback.pb_writes`（`4606563195bb`）<br>`pb-passive-image-bundle-ep_0005.json#files[image-refs.jsonl]`（`4606563195bb`）<br>`pb-image-refs-ep_0005.json#pb_time_binding`（`6643863a8afb`）<br>`pb-image-refs-ep_0005.json#frames[].staged_ref`（`6643863a8afb`） |
| 10 | PB image replay 验证 | satisfied | `pb-gui-image-replay-receipt-ep_0005.json#status`（`953b1ac1cd06`）<br>`pb-gui-image-replay-receipt-ep_0005.json#request_count`（`953b1ac1cd06`）<br>`pb-gui-image-replay-receipt-ep_0005.json#distinct_frames`（`953b1ac1cd06`）<br>`pb-gui-image-replay-receipt-ep_0005.json#summary_fallbacks`（`953b1ac1cd06`） |

### B. 13 个冻结字段

| # | 冻结字段 | 判定 | 证据（文件#路径，哈希前 12 位） |
|---|---|---|---|
| 1 | `source` | bound | `pb-image-refs-ep_0005.json#frames[].source`（`6643863a8afb`）<br>`pb-capture-receipt-ep_0005.json#frames[].source`（`f6b44faba963`） |
| 2 | `entity_ref` | bound | `pb-backend-apply-receipt-ep_0005.json#records[].semantic_ref`（`3557a5608f34`）<br>`pb-capture-receipt-ep_0005.json#frames[].readback.import_id`（`f6b44faba963`） |
| 3 | `fields` | bound | `pb-machine-qa-summary-ep_0005.json#frames[].fields[].field`（`6fb8c176a83f`）<br>`pb-machine-qa-summary-ep_0005.json#frames[].fields[].passed`（`6fb8c176a83f`） |
| 4 | `observed_at` | bound | `pb-image-refs-ep_0005.json#frames[].observed_at`（`6643863a8afb`）<br>`pb-capture-receipt-ep_0005.json#frames[].observed_at`（`f6b44faba963`） |
| 5 | `delivered_at` | bound | `pb-image-refs-ep_0005.json#pb_packet_times[].delivered_at`（`6643863a8afb`）<br>`pb-image-refs-ep_0005.json#pb_time_binding.image_refs_sha256`（`6643863a8afb`） |
| 6 | `adapter` | bound | `pb-backend-apply-receipt-ep_0005.json#records[].adapter.module`（`3557a5608f34`）<br>`pb-backend-apply-receipt-ep_0005.json#records[].adapter.seed`（`3557a5608f34`）<br>`pb-backend-apply-receipt-ep_0005.json#records[].adapter.read`（`3557a5608f34`） |
| 7 | `seed_projection` | bound | `pb-backend-apply-receipt-ep_0005.json#records[].seed.result`（`3557a5608f34`）<br>`pb-backend-apply-receipt-ep_0005.json#records[].seed.at`（`3557a5608f34`） |
| 8 | `history_replay` | bound | `pb-backend-apply-receipt-ep_0005.json#history_replays[].set`（`3557a5608f34`）<br>`pb-capture-receipt-ep_0005.json#history_replays[].result`（`f6b44faba963`） |
| 9 | `readback` | bound | `pb-backend-apply-receipt-ep_0005.json#records[].readback.result`（`3557a5608f34`）<br>`pb-capture-receipt-ep_0005.json#frames[].readback`（`f6b44faba963`） |
| 10 | `surface` | bound | `pb-capture-receipt-ep_0005.json#frames[].package`（`f6b44faba963`）<br>`pb-gui-release-report-ep_0005.json#frames[].png`（`99be7918f5f4`） |
| 11 | `view_id` | bound | `pb-capture-receipt-ep_0005.json#frames[].activity`（`f6b44faba963`）<br>`pb-capture-receipt-ep_0005.json#frames[].navigation`（`f6b44faba963`） |
| 12 | `visible_assertions` | bound | `pb-capture-receipt-ep_0005.json#frames[].ready.visible_assertions`（`f6b44faba963`）<br>`pb-machine-qa-summary-ep_0005.json#frames[].fields[].text`（`6fb8c176a83f`） |
| 13 | `unsupported/reject` | bound | `pb-gui-release-report-ep_0005.json#counts.unsupported_targets`（`99be7918f5f4`）<br>`pb-gui-release-report-ep_0005.json#counts.gate_targets`（`99be7918f5f4`）<br>`pb-machine-qa-summary-ep_0005.json#device_env.status`（`6fb8c176a83f`） |

### C. fail-closed 不变量

- `release_report_valid` = `true`
- `capture_receipt_bound_to_release_report` = `true`
- `capture_receipt_bound_to_private_plan` = `true`
- `machine_qa_binds_capture_run` = `true`
- `backend_receipt_bound_to_release_report` = `true`
- `host_receipt_bound_to_capture_and_release` = `true`
- `replay_receipt_bound_to_host_receipt` = `true`
- `image_bundle_bound_to_host_and_replay` = `true`
- `frames_cross_bound` = `7`
- `pb_packet_times_bound` = `7`
- `device_frames_differ_from_pb` = `true`
- `image_bundle_pb_writes` = `0`
- `raw_pngs_stored_in_repo` = `false`（设备 PNG 不入库）

## 非 V1 项（V2，不计入 V1 判定）

- `not-applicable (V2)`: image live 模型运行（需可用 endpoint + 能力声明）
- `not-applicable (V2)`: PB `code/evaluate.py` / 动作语义裁决 / 评分
- `not-applicable (V2)`: Gold 生成
- `not-applicable (V2)`: 正式发布器（gui_publish / PB 发布流程）
- `not-applicable (V2)`: 17 Episode / 38 GUI packet 全量 coverage 扩张
- `not-applicable (V2)`: PB 核心查询/评分协议修改

- 全量 coverage（17 Episode / 38 GUI packet）属 V2；V1 只交付 ep_0004, ep_0005（2 条闭环）。coverage 审计另存 `docs/v1-acceptance-evidence-20260926-coverage-audit.json`，不参与本清单判定。

## 单点复验命令

```bash
python3 scripts/pb_gui_v1_evidence.py verify --bundle docs/v1-acceptance-evidence-20260926
python3 scripts/pb_gui_v1_evidence.py checklist --bundle docs/v1-acceptance-evidence-20260926
python3 scripts/pb_gui_coverage_audit.py --pb-root <PB_ROOT> \
    --evidence-dir docs/v1-acceptance-evidence-20260926 --output <outside-pb>/v1-audit.json
# 第 4 轮出口重建（preflight → replay → writeback → evidence build，无需设备）
scripts/pb_gui_v1_round4_exit.sh --pb-root <PB_ROOT> --episode <NAME> \
    --release-report <RELEASE_REPORT> --capture-dir <PRIVATE_CAPTURE_DIR> \
    --backend-receipt <BACKEND_RECEIPT> --host-dir <HOST_DIR> [--evidence-bundle <DIR>]
```
