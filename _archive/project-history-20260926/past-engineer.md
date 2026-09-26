# Past Engineer Reference（历史工程参考）

> 本文档仅为既往轮次（2026-09-20 至 2026-09-21）工程历史与参考信息的归档，
> **不具有当前工程汇总的工程指导意义**。当前工程状态、进度与下一步事项以
> `../contract-upgrade-20260926/read.md` 为准；契约、阶段与验收标准以 `../contract-upgrade-20260926/CONTRACT_UPGRADE_BLUEPRINT.md` 为准。
> 仅供对之前工作的参考。

---

## 1. 历史时间线（ENGINEERING_LOG.md 归档）

### 第一轮：可运行闭环
- 建立 `apps/`、`local-controller/`、`contracts/`、`datasets/`、`debug-console/`、`emulator/`、`scripts/`、`tests/` 目录。
- 六个包名：`com.ku.simulator.{dudu,lianqi,starrail,babaquna,wangyiyun,chaowei}`。
- 纯标准库 Controller，绑定 `127.0.0.1`；实现全部核心 HTTP 路由（instance init/reset/status/export、bootstrap、数据分页、action、state、telemetry、events、health）。
- SQLite 权威状态、action 幂等、乐观锁、domain event 与 telemetry 分离、JSONL 追加事件。
- 修复：`events.jsonl` 初始化缺失；导出改用 SQLite backup API 保证 WAL 一致性。

### 第二轮：对照审查与补充
- CORS/OPTIONS；bootstrap ETag/304；数据 API 强制分页（单页上限 200）；模拟时钟读写；确定性故障规则（离线/延迟/超时/间歇失败/HTTP 错误/分页失败）。
- 错误码路径：`REVISION_CONFLICT`、`PRICE_CHANGED`、`INVENTORY_CHANGED`、`TIME_CONFLICT`、`SIMULATED_NETWORK_FAILURE` 等；订单成功同一事务扣库存并生成 domain event。
- 数据集 `2026.09.2`：14 类资源 ×10,000 条 = 140,000 条，SQLite 索引 + FTS5；版本检测 + SHA-256 checksum + manifest；实例只读打开（`mode=ro`）。
- 六域业务：嘟嘟（地点/车型/叫车/支付/取消）、连汽（路线/优惠券/订单/ETA 导航）、星穹（车站/车次/席别/乘客/退改）、爸爸去哪（机/酒/景点/出行人/取消）、网抑云（歌曲/歌单/播放/红心/历史）、超威（文章/标签/收藏/笔记/知识历史）。
- reset-instance.sh 流程：停六 App → 加载 `init_state` → 等 boot → 重装 APK → 清通知 → 复位 Controller → 重建 reverse → 校验六包；失败标 `FAILED`。
- 基线采集与前后 manifest 严格比较（API/ABI、系统指纹、镜像/快照/AVD 哈希、屏幕/语言/时区/输入法/证书/网络、既有包哈希/签名/UID/权限/AppOps/launcher）。
- 验证：14 项测试、100 次连续 reset、8 路并发写保护、P95 门槛、六套 Vue 构建。

### 第三轮：浏览器验收与语义修复
- Vite 默认构建不含 Vue 运行时模板编译器导致六 App 空白页；切换 `vue.esm-bundler` 修复。
- 列表价格与权威订单价格不一致；数据集升级 `2026.09.3`，价格/库存结构化，Controller 同公式生成。
- reset 从"逐表删除"改为丢弃 SQLite/WAL/SHM 重建新库，`database_id` 验证；reset 窗口拒绝业务写入返回 `SESSION_NOT_READY`。
- 数据集 checksum 不一致 → `CONTAMINATED`，bootstrap 拒绝继续；reset 不掩盖污染。
- `expected_revision` 与对象型 `payload` 成为强制字段；跨 App 或不同 payload 复用幂等键 → `IDEMPOTENCY_KEY_REUSED`；空订单支付/取消/退改 → `ORDER_NOT_FOUND`；取消/退票恢复库存；重复收藏 → `ALREADY_FAVORITED`。
- 调试控制台：时钟、故障编辑、SQLite/WAL 摘要、快照、设备状态、截图、App 启停、实例事件流。
- Uni-app 接入 DCloud Vue 3 编译链，六项目产出独立 `uni-dist` H5；APK 构建把 Uni-app 产物并入 Capacitor assets。
- 验证：19 项测试；1,000 请求基准 P50 2.020ms、P95 4.284ms、最大 16.093ms；RSS ≈27.5MB；磁盘增长 ≈2.73MB（报告 `tests/results/local-performance.json`）。
- 当时的阻塞（已被第四轮作废）：本地另装 Android Platform 35 需接受 Google SDK 许可，安全策略禁止代理自动接受。

### 第四轮：KU 环境收敛（本段之后为权威口径）
- 确认 KU 基线：`ghcr.io/yaosqz/knowu-bench:latest`（当时 image ID `sha256:e513d473f298961699f7a8a4bc99730cf03d7b700db6104290ba6ba3a0116a52`）；JDK 17、Android Platform/Build Tools 34、API 34 Google APIs x86_64、`Pixel_8_API_34_x86_64` AVD、完整 `init_state` 快照。
- KU 原启动链：`mobile-world env run` → 镜像默认 entrypoint → `/app/docker/start_emulator.sh` 与 `/app/docker/start_novnc.sh`；确定性初始化经 `adb emu avd snapshot load init_state`。
- 新增 `config/ku-runtime.env` 锁定 KU 镜像/AVD/snapshot/SDK/端口；`setup-android-sdk.sh` 改为仅校验 KU 环境。
- Capacitor 7.4.3 → 6.2.1；Java 17、AGP/Gradle 8.2.1、compile/target SDK 34。
- 验证：真实容器约 106 秒 Ready；六 APK 编译+包名/SHA-256/签名校验；`manifest-diff.json` 结论 `safe: true`（240 包中仅新增六扩展包，无删改）。
- 设备 soak 由 100 次改为 50 次；实跑 51 次连续成功（含停止信号时在途的一轮）。

### 第五轮：六应用场景完工与最终验收
- bootstrap 下发十类确定性场景：权限/促销/库存不足/价格变化/支付确认/取消损失/时间冲突/重复收藏/会员内容/网络失败。
- FTS 搜索限制当前资源；数据集 `2026.09.4`。
- 真实设备复测发现旧库与新 manifest 错配：仅当数据集版本明确变化才升级只读库；同版本 checksum 不一致保持 `CONTAMINATED`；宿主脚本不会用 `FAILED` 覆盖 `CONTAMINATED`。
- 性能：P50 2.258ms、P95 4.510ms、最大 18.924ms；RSS 27,578,368 bytes；磁盘增长 2,725,253 bytes。
- 干净实例导出 `runtime/instances/alignment-final/exports/alignment-final.zip`，SHA-256 `7772131e58f79a0be805cfcb94a7f0ca7f526b7ec3ebeb44e3c6cf323d727431`。
- 启停脚本改为校验 `/proc/<pid>/cmdline`、Controller 路径与 instance ID，防 PID 复用误杀。
- APK manifest 增加构建环境证明；验收增加实际 package name 与签名校验；`verify.sh` 隔离搜索排除自身正则文本。

### 第六轮：产品拟真 UI、原创图标与临时数据注入
- 六套独立视觉结构（聚合出行/品质预约/铁路票务/旅行比价/音乐社区/知识社区），应用名/原型/宫格/规划器/导航/主题色全部由 `apps/app-definitions.json` 驱动。
- 内置图像生成模式制作六枚无文字无商标原创品类图标（launcher/round/foreground/splash）。
- 新增 `datasets/fixtures/research-seed-v1.json`（14 资源 ×5 条写实合成记录）；数据集 `2026.09.5`，bootstrap 明示 `synthetic_research_fixture`。
- 音乐符号 WebView 字体回退乱码 → 改中文标记 + 纯 CSS 播放图形。
- debug 签名跨构建稳定方案：`-Duser.home=$HOME`（仅 `HOME` 环境变量不足以改变 JVM `user.home`），密钥固定到 `.gradle-cache/ku-home/.android/debug.keystore`；连续两容器构建密钥 SHA-256 `7ac9c4d7c2e9b123cf68f9dd229da9ee2f0cecde49de16840ab1a05512b230c5`、证书指纹 `1821dbd79ea13b66f1df96f6a6d9711b4fa2f3ef5e3be419fdb6f6f3523a38ed`。
- 截图证据：`runtime/instances/visual-refresh/screenshots/final-*.png`。

### 第七轮：移除统一标题壳与接口实机复验
- 参考 APK 首页结构为"状态栏后直接进入业务内容、无统一介绍顶栏"；共享模板删除整块顶栏；静态回归禁止 `<header>`、`{{cfg.name}}`、`{{cfg.subtitle}}`。
- 参考 APK（滴滴/曹操出行/铁路12306/携程/网易云/知乎）首启受隐私协议/通知权限/启动页阻挡，仅采纳可确认的无标题壳结构，不虚构未进入页面细节。
- 注入端到端复验：`POST /local/v1/apps/starrail/data/stations` 注入"上海南站" → 运行时读回一致 → KU AVD 星轨首页第一条实时显示"上海南站 / 上海 · 轨道交通 1/3/15 号线"；统一 reset 后恢复"上海虹桥"。
- `scripts/verify.sh` 完整通过：25 项测试、六套 Vite/Uni-app 构建、边界扫描、六 APK 校验。
- 末轮删共享 CSS 全部失效顶栏选择器，公共 CSS 15.22 kB → 14.21 kB。

## 2. 计划符合性审查归档（IMPLEMENTATION_STATUS.md）

- 审查对象为当时的 `../ku-simulator-extension-plan.md`（"KU 拥有完整 Android 环境，本工程只负责扩展与复位编排"）。
- 已实现并验证：独立扩展布局、六包名、loopback Controller、SQLite 权威、140,000 行确定性数据集、debug console、完整本地 API 契约（分页/搜索/ETag/幂等/revision 冲突/事务 domain event/telemetry 分离/faults）、六套 Vue/Uni-app、十类确定性场景、reset 重建 SQLite、50 次连续 reset。
- KU 对齐：`config/ku-runtime.env` 锁定镜像/AVD/`init_state`；APK 在 KU 镜像内构建（Capacitor 6.2.1、Java 17、AGP/Gradle 8.2.1、SDK 34）；reset 在容器内加载 `init_state` 后只装六扩展 APK。
- 真实 KU 验证：六 APK 编译+签名+哈希+工具链 manifest；noVNC/ADB relay/`init_state`/reverse/六冷启动；前后 manifest 对比 `safe: true`；50 次 soak 通过；飞行模式离线冷启动通过；P95 4.510ms。
- 结论：所有检查项实现并验证，无 Android 环境依赖或 SDK 许可阻塞。

## 3. 工程快照参考（原 README.md）

- 定位：扩展 KU 已发布模拟器，不建第二套 Android 环境；KU 是 Docker/JDK/SDK/AVD/emulator/noVNC/`init_state` 的唯一真源。
- 锁定默认值在 `config/ku-runtime.env`：镜像 `ghcr.io/yaosqz/knowu-bench:latest`、JDK 17、SDK/Build Tools 34、API 34 x86_64、AVD `Pixel_8_API_34_x86_64`、snapshot `init_state`。
- 快速开始：`./scripts/bootstrap.sh` → `check-ku-runtime.sh` → `verify.sh` → `build-apks.sh` → `start-simulator.sh demo`；停止 `./scripts/stop-ku-environment.sh demo`；并行实例传非冲突端口组 `demo-2 6900 7960 5900 5656`。
- 构建策略：宿主机构建 Vue/Uni-app 资产 → Capacitor 同步 → KU 镜像内编译六 Android 工程（Capacitor 6、Java 17、compile/targetSdk 34）。
- reset 策略（`scripts/reset-instance.sh <instance-id>`）：① 阻断写入并停六 App → ② 容器内 `adb emu avd snapshot load init_state` → ③ 等 boot 后重装六 APK → ④ 重建 Controller SQLite 与 `adb reverse` → ⑤ 校验六 APK 哈希。绝不保存/覆盖 `init_state`、不改镜像、不清 KU 全局通知。
- 50 次设备 soak：`./scripts/device-reset-soak.sh demo 50`。
- App 只访问 `http://127.0.0.1:8080`，由 `adb reverse` 映射到 loopback Controller。
- 目录职责：`apps/`（六 Vue3/Uni-app/Capacitor 应用）、`local-controller/`（无依赖 Controller + SQLite 权威）、`contracts/`、`datasets/`、`debug-console/`、`config/ku-runtime.env`、`emulator/`（KU 基线采集与前后比较）、`scripts/`（KU 委托/APK 构建/Controller/reset 编排）、`tests/`。

## 4. KU AVD 工作流（原 emulator/README.md）

- 扩展不克隆/不创建/不启动自有 AVD；由 `scripts/start-ku-environment.sh` 或 `scripts/start-simulator.sh` 委托 KU CLI/entrypoint/emulator/noVNC 脚本。
- 初始化或每次完整 reset 前在容器内执行与 KU 任务相同的：`adb emu avd snapshot load init_state`。
- 禁止执行 `snapshot save init_state`、禁止改内嵌 AVD、禁止提交一次性容器。
- 基线比较：`capture-baseline.py --serial 127.0.0.1:5556 --avd Pixel_8_API_34_x86_64 --snapshot init_state --ku-container ku_simulator_extension_demo_0 --output baseline-manifest.json`；装六 APK 后同命令出 `expanded-manifest.json`，再 `compare-manifests.py baseline-manifest.json expanded-manifest.json --output manifest-diff.json`。比较只允许新增六个扩展包名，其余任何变化判失败。
- `start-overlay.sh` 仅作兼容包装，委托 `start-simulator.sh`，不含 emulator 参数或快照实现。

## 5. 数据集说明（原 datasets/README.md）

- `generated/catalog.sqlite` 由 `scripts/bootstrap.sh` 可再生生成：14 类资源 ×10,000 条（140,000 总）+ FTS5 索引。
- Controller 将其复制进每个实例并以只读方式打开；SHA-256 checksum 由 bootstrap 发布。
