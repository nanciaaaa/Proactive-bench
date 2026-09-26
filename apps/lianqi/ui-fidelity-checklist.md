# 连汽约车（lianqi）UI 拟真对照清单

> 参考 APK：app/曹操出行.apk；目标：品质专车服务。
> 依据：../../_archive/contract-upgrade-20260926/CONTRACT_UPGRADE_BLUEPRINT.md §5。本清单按 2026-09-23 当前六包设备报告更新。

| 层 | 检查项 | 状态 |
|---|---|---|
| T1 信息架构 | 首页直接进业务内容、底部导航/宫格与参考 App 同构 | 已完成 |
| T2 视觉近似 | 主题色/密度/列表项/弹窗/底部窗口形态贴近参考 | 已完成 |
| T3 流程等价 | 核心流程 GUI 可操作且产生真实状态变化（journal 佐证） | 已完成 |
| T4 逐页对照 | 参考 APK 业务内页无法稳定进入时，使用本地参考视频及公开图对照结构与视觉 | 替代验收通过 |

## 对照截图记录

| 页面 | 参考截图 | 本 App 截图 | 差异说明 |
|---|---|---|---|
| 首页及业务页 | 参考视频/公开界面图（见 ../../_archive/contract-upgrade-20260926/read.md） | `runtime/instances/acceptance/provenance/lianqi-current-service.png` | 同一 KU AVD，宽度 412 CSS px，无横向溢出 |

## 已知差异 / 未完成项

- 参考 APK 在此 AVD 未取得完整可用业务页；按用户要求以本地参考视频和公开界面图片作结构对照，已完成可操作流程及数据状态验收。

## 本轮设备证据

- 六款 GUI 数据溯源：`runtime/instances/acceptance/current-provenance-report.json`；本款所有检查通过。
- 第三轮九项针对性真实点击：`runtime/instances/acceptance/third-round-gui-report.json`。
- 六款主流程真实点击：`runtime/instances/acceptance/second-round-gui-report.json`。
- 订单取消/话题条目/歌单资源保护：`tests/test_product_gaps.py`。
