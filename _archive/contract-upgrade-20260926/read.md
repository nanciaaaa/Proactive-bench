# KU 模拟器扩展：目标与进度

更新时间：2026-09-24（蓝图契约复查；旧轮次验收记录保留在下方）

## 2026-09-24 蓝图契约复查

- 第一轮对照 `CONTRACT_UPGRADE_BLUEPRINT.md` §2.2、§4.3–§4.4、§7–§8：发现 Passive API 把内部 `payload`（含 action 的内部 ID/revision）暴露给受控只读方，包的 `source` 与 envelope 的 `source` 未标识 App；bootstrap 未返回 adapter 契约版本；query 与 Passive 游标允许非法分页；订单 query 遗漏已声明的 `fare_difference`、`reservation_time`、`train_schedule`。
- 已在 Controller 的统一读出路径修正上述偏差，bootstrap schema 同步声明版本字段。新增回归检查覆盖包字段白名单、App 来源、版本、非法分页及订单字段读回；针对性 24 项通过。
- 修改后再次执行 `./scripts/verify.sh`：106 项测试通过，共享 Vue 模板及六款 Vite/Uni-app 构建、envelope 白名单和现有六 APK 校验均通过。本轮仅改 Controller、契约 schema 和测试，未改 APK 源码；既有 50/50 次设备复位与 GUI 证据保留为上一批 APK 的证据，不作为本轮 Controller 修改的实机复验。
- 第二轮复查蓝图 D5 与 §4.4：补齐 `add`/`delete` 的 `allowed_changed_fields` 精确匹配，并修正新投递 packet 首次返回时 `delivered_at` 为空的问题。新增错误与成功路径测试；针对性 29 项通过，随后再次跑全套校验。
- 第二轮修改后复查：`./scripts/verify.sh` 通过 107 项测试、共享模板、六款 Vite/Uni-app、隔离扫描与六 APK 校验。继续检查输入边界时发现空 `projection` 被当成缺省值、管理凭据拒绝信封的 `source` 未标识目标 App；已修正并补回归，进入最终一轮验证。
- 第三轮复查蓝图 §2.2/§3.2 的版本格式：允许 fixture 输入 `app.vN`，query envelope、read_state 和 mutation journal 对外统一给出 `app.vN` 字符串；旧 SQLite 整数 journal 记录在读出时归一化。针对性 41 项通过，待全量再验。
- 第三轮修改后再次执行 `./scripts/verify.sh`：108 项测试通过，六款 Vite/Uni-app、共享 Vue 模板、隔离扫描、envelope 白名单和六 APK 校验均通过。本轮没有修改六款 APK 的前端或 Android 源码，故未重建 APK、未宣称新的 KU AVD GUI 证据；已有 50/50 reset soak 结果仍是先前版本证据。
- 第四轮复查蓝图 §4.6 的“推进”语义：`advance_time` 原可接受任意字符串和倒退时间。现只接受带 UTC offset 的 ISO 8601 时间，且不得早于当前逻辑时间；生命周期 12 项测试通过，随后复跑全套校验。
- 第四轮修改后 `./scripts/verify.sh` 再次通过：108 项测试、共享模板、六款 Vite/Uni-app、隔离扫描、envelope 白名单与现有六 APK 校验。当前代码契约复查发现的上述偏差均已修正；因 Controller 已改动，旧 KU AVD 流程及 50 次复位证据不能当成本轮运行时代码的重新验收，下一步仍需对新 Controller 在设备上复跑闭环和复位。
- 本轮设备可用性检查：`docker ps` 无运行中的容器，旧 `acceptance` Controller 端口 34444 拒绝连接；因此无法直接复用先前 KU AVD 做当前 Controller 实机闭环。未把历史设备报告算作本轮复验。
- 独立 `blueprint-audit` 首次启动时磁盘耗尽，未创建 Controller；删除本轮临时校验日志及失败容器后恢复空间。第二次 KU 容器启动但 AVD 四分钟仍无 adb device，已停止并删除这两次启动产生的匿名 Docker volume；旧 volume、APK、运行报告均保留。清理后磁盘可用约 13 GB，准备再试设备启动。
- 第三次启动 KU AVD 成功。新 Controller 下 `verify-current-provenance.py blueprint-audit` 六款均 `passed=true`，证据 `runtime/instances/blueprint-audit/current-provenance-report.json`。旧 `verify-gui-flows.py` 引用已撤下的 `.cards article` 入口，不能代表当前 UI；第四轮脚本首试在异步页面切换时抢先断言，已在共用 `pay()` 等待确认页后重新复位，`verify-fourth-round-gui.py blueprint-audit` 八组交互全部通过，证据 `runtime/instances/blueprint-audit/fourth-round-gui-report.json`。
- 同一设备与当前 Controller 执行 `CONTROLLER_PORT=34445 ./scripts/device-reset-soak.sh blueprint-audit 50`，**50/50 全过**，耗时 224 秒；六包安装、boot、reverse 及无状态残留均通过，报告 `tests/results/device-reset-soak.json`。测试后按用户要求停止本轮 KU 容器，并清理本轮及更早遗留的 17 个未使用匿名 Docker volume、`.gradle-cache`、六款前端/Uni-app/Android 构建目录、Python 缓存和根目录测试日志；保留六个 APK、源代码、运行报告与截图。清理后 Docker 活跃容器/volume 均为 0，磁盘可用约 98 GB，`verify-apks.sh` 六包仍通过。
- 清理后用 `python3 -B -m unittest discover -s tests -q` 复核 108 项通过；报告断言确认六款溯源、第四轮 GUI 与 50 次复位均通过，且构建中间目录已消失。`dist/apks/` 中的六包与 SHA256 清单保留。

## 2026-09-23 五轮截图反馈（已完成）

- 已确认：优惠券选择按钮仅为 8px 内边距且长券列表未滚动；音乐 `.music-services span` 把静态入口外层也涂成红色圆形，造成图标/文字重叠；超维“切换收藏”和连汽“开始导航”是复用记录页的占位操作。订单状态当前只允许未支付取消，且嘟嘟仍暴露取消入口。
- 当前待办：放大券选择触控区并让券列表可滚动；限定音乐图标样式作用域，移除占位工具；连汽允许指定已支付预约取消并记录退款，嘟嘟前后端均禁用取消。随后更新契约、测试、六包构建和 KU 实机点击验收，复位实例。
- 代码阶段已完成：券选项最小 112×48px 且列表可滚动；网抑云静态图标红底只在内层图标；超维移除记录页“切换收藏”、连汽替换导航为“取消预约”，嘟嘟移除取消入口且 Controller 拒绝。连汽已支付预约取消记录负向退款流水并阻止重复取消，未支付预约仍可取消。契约已重生成，针对性回归 15 项及 `verify.sh` 全套测试/模板/六款前端构建通过。待办：六个 APK 重建、KU 点击、溯源复核和复位。
- **最终验收完成**：`./scripts/verify.sh` 通过 93 项测试、共享 Vue 模板编译、六款前端/Uni-app 构建与 APK 校验；`./scripts/build-apks.sh` 重新构建六包。KU 实机扩展 `verify-fourth-round-gui.py` 点击验证券按钮尺寸及滚动、嘟嘟无取消入口、连汽已支付预约取消并记录退款、超维无“切换收藏”、网抑云图标几何；证据 `runtime/instances/acceptance/fourth-round-gui-report.json`。网抑云截图 `runtime/instances/acceptance/screenshots/fifth-music.png` 已人工查看，无图标/文字重叠；六款 `verify-current-provenance.py` 全通过。最终 `reset-instance.sh acceptance` 将六个已安装包核对 SHA256 并恢复 READY，六款 revision=0、订单为空。
- 预约支付页补充完成：连汽订单快照记录预约时间，支付页显示车型、预约时间、起终点和付款后取消提示；Controller、契约及测试同步。最终 `verify.sh` 93 项测试与模板/前端构建通过，六 APK 重建；最终包实机点击验证预约摘要、券触控面积、已支付取消退款、嘟嘟无取消及超维/网抑云入口，六款 GUI 溯源复核通过，复位后实例 READY 且六款 revision=0、订单为空。
- **当前待办：无本轮范围内未完成项。** §16 中嘟嘟可取消的旧设计以 §17 最新要求为准，现已禁用；连汽支付后取消为模拟退款，不连接真实支付渠道。

## 2026-09-23 四轮订单与业务详情修正（已完成）

- 需求阶段已完成：将本轮七组反馈写入 `CONTRACT_UPGRADE_BLUEPRINT.md` §16，涉及嘟嘟总车型查询和券、连汽首页重复车型、铁路/旅行订单确认出行人、按订单取消/退票/改签、车次/景点详情及超维重复入口。
- 根因复现：当前 Controller 的 `dudu`、`babaquna` 未声明 `coupons` 展示资源，实际访问其券接口返回 404，导致账户优惠券页报错且支付页无法列券；`change_order`/`refund` 固定操作最后一单，改签未选新车次席次也未计算差价；铁路和旅行的出行人表单仍位于结果选择页。这些路径正在修复，完成后将新增回归、重建六包并在 KU 逐项点击。
- 代码阶段：嘟嘟与旅行声明优惠券资源；订单确认页保存出行人后才开放支付；订单操作先选订单，铁路改签再选车次、席次并按旧新票价记差额；车次/景点进入详情页。嘟嘟新增统一车型查询并改为“确认并呼叫”，连汽首页去掉预选车型，超维删除重复关注导航。景点正文写入种子数据，契约生成器同步字段；券和订单状态由 Controller 回读。
- 验证阶段：`python3 -m unittest discover -s tests -q` 92 项通过；六款 Vite 与 Uni-app 构建、共享 Vue 模板编译和旧 APK 校验通过。新增退改指定订单与出行人保存回归。待办：重新生成最终 APK、安装 KU AVD、真实点击七组反馈流程与重采溯源，最后复位实例；在这些完成前不标记本轮验收通过。
- **最终构建与验收完成**：`./scripts/verify.sh` 通过 92 项测试、Vue 模板编译、六款前端/Uni-app 构建与 APK 校验；`./scripts/build-apks.sh` 六个 Android APK 构建成功。`verify-fourth-round-gui.py acceptance` 在 KU AVD 最终包真实点击嘟嘟统一车型/叫车/优惠券/支付/指定取消、星穹车次详情/出行人/支付/指定改签和退票、爸爸去哪景点介绍/出行人/优惠券/支付、连汽车型与优惠券、超维导航，报告 `runtime/instances/acceptance/fourth-round-gui-report.json` 均通过。`verify-current-provenance.py acceptance` 六款全部通过；`reset-instance.sh acceptance` 校验六个安装包 SHA256 与本地一致后复位 READY，测试订单清空。
- **当前待办：图标外观需用户指明具体位置/期望样式。** 本轮已删除超维重复的“关注”导航，但“图标做的有问题”未给出可辨认的图标细节，暂不臆改。参考 APK 业务内页无法稳定进入，T4 仍沿用之前的视频和公开图片作为结构参考，不声明逐像素一致。

## 2026-09-23 三轮页面与状态修正（已完成）

- 需求阶段：已将用户新反馈逐项写入 `CONTRACT_UPGRADE_BLUEPRINT.md` §15：超维话题下属条目、静态功能图标和嘟嘟推荐地点、取消状态、各账户统计独立页面、铁路查询状态清理、歌单与单曲分离。下一步先在当前 KU 包上复现关键错误，再改数据与页面。
- 复现阶段：`python3 scripts/verify-third-round-gui.py acceptance` 在旧包返回 `passed=false`，9 项点击检查全部复现截图中的问题。已确认相关共享路径：音乐首页 `selectItem` 不按资源区分歌单与单曲；铁路新查询未清理 `trainChoice`；个人统计卡统一执行 `tab='orders'`；订单列表不显示状态；首页配置只保留可交互服务。数据阶段进行中：文章显式 `topic_id`、歌单显式 `track_ids`，并增加每个话题的条目和各歌单的多首歌曲。
- 数据与页面阶段已完成：文章绑定话题 ID，每个示例话题至少两篇对应条目；歌单存多首歌曲 ID，歌单卡进入歌单页，单曲页才可红心或加入个人歌单。各 App 未实现服务恢复静态图标，嘟嘟根据常用地址和地点数据列推荐目的地且不显示收藏统计；车次重新查询清空旧选项，席次页才显示已选车次；账户头像、统计及菜单各进对应页面；订单列表显示取消状态。数据契约、离线资源及最小回归已同步。
- 首次 APK 装机发现 Vue 模板对象键的连字符导致 WebView 语法错误；已改正并加入模板编译检查。第二次六包构建与 KU 安装完成。`verify-third-round-gui.py` 九项设备点击全部通过；`verify-second-round-gui.py` 六款主要业务流程全部通过。第三轮独立状态测试覆盖话题关系、歌单歌曲保护和取消后 `CANCELLED` 状态。当前正在重采六款 GUI 溯源、核对全量测试与清单并最终复位。
- **最终构建与验收完成**：最终六个 APK 已在 KU Docker AVD 安装且与本地 SHA256 一致。`./scripts/verify.sh` 通过 90 项测试、共享 Vue 模板编译、六款 Vite/Uni-app 构建与六包校验；`verify-third-round-gui.py` 最终包 10/10 项真实点击通过（新增取消行程后订单“已取消”），`verify-second-round-gui.py` 最终包六款完整业务流程全部通过；`verify-current-provenance.py` 最终包六款均通过，412 CSS px 视口无横向溢出。六份 catalog、溯源表和 UI 对照清单已同步。最后执行 `reset-instance.sh acceptance`，六包保持安装，实例 `READY`，六款 revision=0、订单与播放历史为空。
- **待办**：本轮要求无未完成项。参考 APK 在此 AVD 无法稳定进入业务内页，沿用本地参考视频与公开界面图片进行结构/视觉对照，不作逐像素一致性声明；这是蓝图 T4 已记录的外部参考边界，不影响本轮完整构建和真实流程验收。

## 2026-09-23 二轮真实交互升级（已完成）

- 需求阶段已完成：已将本轮五组要求写入 `CONTRACT_UPGRADE_BLUEPRINT.md` §14，包含独立服务/查询/订单/支付/歌单/阅读页、顺序选择、微信/支付宝假密码支付、旅游出行人手机号、打车车牌 ETA、数据契约与 A–D 验收阶段。相关视频位于 `相关文件/{滴滴,携程,网易云,知乎,铁路}.mp4`，正在抽帧核对；此阶段尚未修改产品代码。
- 当时待办（已完成）：先完成视频和现有实现对照，再按蓝图 B/C 修改 Controller、共享 UI、应用配置与最小回归；随后重建六包，KU 实机逐流程验收、补溯源证据并复位。
- A 阶段已完成：已抽帧查看五段参考视频。滴滴：先地图/起终点，后车型列表与呼叫；铁路：站点日期→车次→席别→订单确认；携程：业务入口进入酒店等独立查询页，查询后为结果列表；网易云：单曲播放页的红心与“收藏到歌单”选择器、歌单中的单曲列表；知乎：信息流点入文章/回答详情并可进一步查看评论。现有共享 UI 则将资源标签与业务卡片并列放首页、服务图标只开通用列表，订单和歌单没有独立详情，是本轮主要差距。后续 B 阶段已实现独立业务页与顺序选择。
- B/C 代码阶段已完成（后续已装机验收）：首页有效功能已收窄；服务图标进入独立列表页。嘟嘟/连汽在独立页确认起终点和车型，星穹按车次→席别→乘车人，旅行按业务结果→常用或新出行人（姓名、手机号）创建订单。订单列表增加独立详情入口；详情进入微信/支付宝支付页，可选择优惠券并输入六位假密码，Controller 只校验不保存密码，付款记录保存支付方式。音乐新增单曲详情、红心、收藏到指定歌单、歌单详情与播放全部；“播客”改为“音乐”。超维文章和话题进入正文详情后再收藏。Controller 最小支付/乘客回归通过，六款 Vite 编译通过；后续完整回归、APK 重建及 KU 实机点击均已执行。
- B/C 跨应用流程复查（进行中）：已根据用户澄清把「业务入口→查询→选择→订单详情→支付」原则应用到所有相关类型。嘟嘟和连汽现先填地点、再查看车型；爸爸去哪各业务先填查询条件、再看对应结果；首页去掉了支付类应用直接并列的资源列表和账单内联支付捷径，改由订单详情进入支付。修正打车地点在订单创建时被首页旧值覆盖，以及后端下单失败仍跳转的错误；音乐歌单资源进入歌单详情。当时待办均已完成：阅读正文数据、契约/溯源同步、六包构建及 KU 实机逐应用点击。
- C 数据阶段已完成（后续已设备复核）：超维五篇文章及五个话题增加可阅读的模拟正文，Controller 数据库、注入数据、离线数据及详情查询契约均暴露 `body` 字段；正文不再只是所有条目共用的一段占位话。更新前六包构建/87 项测试通过，正文改动后需重新运行全量构建和 KU 点击。逐字段溯源已在 D 阶段重采。
- D 设备首轮点击：新 APK 已装入 KU。嘟嘟、连汽的地点→车型→订单详情→支付宝六位假密码，以及爸爸去哪酒店查询→出行人姓名/手机号→支付、网抑云创建歌单→单曲红心/收藏→歌单详情、超维入口→文章正文→收藏均通过真实 WebView 点击。星穹发现从首页查询车票未置结果状态，导致车次列表空白，已修正后续已重新打包复验。用户新反馈指出连汽每类车型选项不足、优惠券应逐张选择且能选不使用；已把连汽四种业务扩成多车型并增加支付券选择/取消动作，后续已完成再次构建与设备验收。
- D 复核前代码阶段：连汽即时、预约、接送机、商务各有三种车型，列表按业务前缀筛选。支付页优惠券选择器逐张列出券及优惠说明，可换券或选“不使用优惠券”；Controller `remove_coupon` 恢复原价，保留券供之后再选。`test_choose_coupon_then_choose_none` 覆盖换券与恢复金额。六款全量 88 项测试、Vite/Uni-app 构建和现有 APK 静态校验通过；最终 APK 后续已重建、装机点击并重采溯源。
- D 完整设备点击已通过：`runtime/instances/acceptance/second-round-gui-report.json` 六款全部 `passed=true`，涵盖嘟嘟/连汽地点→车型→订单→支付宝假密码，星穹站点→车次→席别→出行人→支付，爸爸去哪酒店查询→新出行人→支付，网抑云创建歌单→单曲详情/红心/收藏→歌单单曲，超维入口→文章正文→收藏。连汽专项 `coupon-choice-gui-report.json` 四类入口各有三车型，同笔 ¥64 订单选第一张券 ¥52、换第二张 ¥49、不用券恢复 ¥64。`common-traveler-gui-report.json` 证实常用出行人王五及手机号可点选带入订单。六款当前界面 seed→GUI→query/read_state→teardown 报告 `current-provenance-report.json` 均通过，视觉宽度无溢出；旧版首页资源卡片/通用服务弹层溯源表已替换为本版实际页面与数据点。截图复看发现连汽默认地点取自时间/ETA，已改正；支付历史时间改为中文日期格式，最后一批 APK 已装机复核。
- **D 最终构建与验收完成**：上述默认地点及中文时间修正后，`./scripts/verify.sh` 通过 89 项测试、六 Vite、六 Uni-app、隔离扫描及六 APK 校验；最终六包重建并安装到 KU Docker AVD。最终包重跑 `verify-second-round-gui.py` 六款全通过、`verify-current-provenance.py` 六款全通过，六款截图均 `viewport=content=412`；再跑 `verify-coupon-choice-gui.py` 和 `verify-common-traveler-gui.py` 均通过。六份 UI 溯源表和 T3 清单已指向本版证据。最后 `reset-instance.sh acceptance` 将实例复位 READY，ADB 查到六款均已安装，六款 revision=0、订单及播放历史为空。
- **当前待办：无本轮范围内未完成项。** T4 仍受参考 APK 无法进入内页限制，只能依据用户视频与已有截图核对结构和流程，不声称逐像素一致；未来取得可用同版本参考 APK 后，可补做逐页像素对照。

## 2026-09-23 用户截图反馈修复（已完成）

- 已复现：账户菜单全走通用“为你精选”服务面板；订单只显示内部 `item_id`；订单状态没有券后金额与支付流水；六款默认启动均带场景弹窗；音乐红心在记录工具栏而非歌曲卡片。`python3 -m unittest tests.test_product_gaps -v` 的三项针对性检查均按预期失败。
- 正在修复：账户选项接入各自的记录/表单视图；订单快照写入语义标题、路线/内容、原价、券减和支付记录；默认弹窗改为显式场景触发；音乐每首歌提供红心、播放后保留歌曲状态栏。修复后重建六包并装机验证。
- Controller 阶段已完成：下单保存商品标题/说明及原价；优惠券按“满 X 减 Y”或“减 Y”规则抵扣未支付订单；支付写入金额、方式和时间；订单详情可经 `get_order_details` 投影读回；无显式 seed/trigger 时六款默认无弹窗。针对性后端回归与既有生命周期/验收测试共 48 项运行后仅剩账户/音乐 UI 静态检查未通过。
- 账户与音乐 UI 阶段已完成（待装机）：六款账户菜单改为领域记录、可保存表单或实际浏览入口；账户字段通过 `save_account_field` 写入权威状态并可 `list_account_fields` 读回。账单卡片改为可展开的语义标题、路线/内容、原价、券减、应付及支付流水；未支付订单可选择券后再支付。音乐单曲卡片增加独立红心，播放栏固定于底部并可暂停/继续。六款 Vite 构建和 7 项针对性/溯源静态测试通过；下一步更新逐字段溯源与实机流程测试。
- 代码回归阶段已完成：85 项单测和六款 Vite 构建通过。账户发票列表按已支付记录判断空状态，收藏/播放记录入口加载对应资源，页面不再以内部编号代替内容标题。下一步重建 APK、安装到 KU AVD 并执行实际点击与截图检查；新字段的逐项 GUI 溯源证据仍待重采。
- KU 实机流程阶段已完成：六个新 APK 已覆盖安装到 `ku_simulator_extension_acceptance_0`；`verify-gui-flows.py acceptance` 六款主业务均通过，且均无默认启动弹窗。账户逐项点击与表单保存检查走完六款各五个入口；连汽券后账单实测原价 ¥64、减 ¥12、支付 ¥52，支付记录金额为 ¥52；网抑云状态栏暂停/继续及当前歌曲红心写入权威状态。支付/音乐证据见 `runtime/instances/acceptance/product-feedback-report.json`。逐字段溯源表已新增账户字段、账单摘要与支付、播放状态，待重新执行 seed→GUI→query/read_state→teardown 后再标记 verified。
- 最终溯源阶段已完成：六份 `ui-data-provenance.json` 均为 `verified`；新增账户字段、账单标题/路线/原价/抵扣/支付流水、音乐播放状态均经过 seed→Android WebView→query/read_state→teardown，并留存于 `runtime/instances/acceptance/provenance/`。人工复看了账单、账户表单与固定歌曲状态栏截图；所有六款截图宽度为 412 CSS px，无横向溢出。
- 最终构建与验收已完成：补充“折扣且最高减额”的券价计算和不适用提示；`scripts/verify.sh` 通过 **86 项测试**、六 Vite/Uni-app、隔离扫描、六 APK 校验；`scripts/build-apks.sh` 重新生成六包并由 `reset-instance.sh acceptance` 覆盖安装到 KU Docker AVD。最终包逐项点击了六款各五个账户选项，表单保存后从权威状态读回；报告 `runtime/instances/acceptance/product-feedback-report.json` 同时保留券后支付和音乐控件结果。
- 交付状态：`acceptance` 已复位到 READY，六款 state 均为 `revision=0`，订单、收藏、弹窗、账户字段为空；最终六包仍安装在 KU AVD。原蓝本的 50/50 次设备复位 soak 证据见 `tests/results/device-reset-soak.json`，本轮反馈未改复位编排，因此未重复执行长时 soak。本轮五项反馈均已闭环，无必需待办。

## 2026-09-23 恢复执行记录

**审查已完成**：复核蓝本 §5、§6、§9、§10、现有 73 项测试、六份溯源骨架及共享界面。发现服务面板在 Controller 在线时仍固定从 APK 降级副本取数据，导致 seed 后的服务面板与 query 可能不同源；音乐首页歌单架也固定取降级副本。当前 ADB 无在线设备，旧 P4 截图需在 AVD 恢复后重新采集。

**正在完成**：先修复上述显示链路，建立逐项溯源覆盖断言及 seed→GUI→readback 证据；随后完成 T2/T4 对照、重建与设备验收。

**代码修复已完成（待构建/实机验证）**：共享 UI 的服务面板改为按所选 resource 从运行时数据路由读取并缓存；音乐首页歌单架也从同一路由读取。Controller 不可达时两处仍使用同一数据集的 APK 只读副本。下一步补覆盖测试和端到端证据。

**覆盖测试阶段已完成**：新增 `tests/test_ui_provenance.py`，枚举共享 Vue 模板全部可见插值，未分类的新绑定会失败；逐 App 检查业务字段同时存在于 typed catalog 和溯源表，且服务面板/歌单架不能回退到固定 `FALLBACK`。此阶段 2 项新测试通过。审查同时发现计划器默认值和账户名仍属业务/用户数据，下一阶段将使其可经固定 seed 接口覆盖并读回；此时不把 D8 标成完成。

**类型化内容阶段已完成**：六款 catalog 增加 `ui_content`（hero/planner/profile_name）和 `coupon_wallet`；Controller seed/query/read_state/teardown 与 GUI 的首页文案、计划器、账户名打通。溯源骨架按各布局实际可见字段修正，不再把音乐/阅读未显示的库存、弹窗不可见的 kind 误记为 GUI 数据点。新增闭环单测通过。`scripts/verify.sh` 现为 **76 项测试、六 Vite、六 Uni-app、泄漏扫描、旧 APK 校验全通过**；包含新代码的 APK 正在重建，设备验收尚未完成。

**用户校正（执行中）**：账户页不可六款一律显示“订单/收藏/优惠券”。音乐 App 和超维不应有优惠券；各 App 的记录页和个人页必须体现领域差异。上段“六款 catalog 增加 coupon_wallet”为阶段性实现，现正收窄到有优惠业务的 App，并修改共享模板按 App 配置渲染对应用户状态。前一轮构建不作为最终包。

**领域页面修正已完成（待实机验收）**：六款 App 的记录页标题、账户统计已按领域配置；网抑云显示最近播放/红心/歌单及各自记录，超维显示收藏/想法，星穹显示车票/乘车人/收藏；优惠券仅嘟嘟、连汽、爸爸去哪三款可见且在 catalog 中声明。共享模板的记录内容也分别来自 `play_history`、`favorite`、`user_playlist`、`note`、`passenger`、`coupon_wallet` 等可 seed/readback 的用户状态。六套 Vite 编译通过；覆盖测试补上“无优惠券”断言，下一步复跑完整验证并装机截图。

**回归阶段已完成**：`scripts/verify.sh` 在领域页面修正后通过：**77 项测试**、六 Vite、六 Uni-app、隔离扫描、现有 APK 校验均通过。新增 `scripts/verify-ui-provenance.py` 准备在 Android WebView 逐 App 执行 seed→页面各区→query/read_state→teardown，并按条写回溯源 verified/evidence。最终 APK 正重建，未把旧 APK 校验冒充为本轮最终包验收。

**视觉审查进行中**：已用图像能力复核既有六款截图，不再沿用旧记录“本 Agent 无视觉能力”的豁免。发现爸爸去哪首页推荐卡片在 360dp 宽设备上使用双列而横向裁切，已删除该覆盖规则，让卡片采用共享的单列流式布局；修正后的 APK 正重建，待同分辨率截图确认。参考 APK 崩溃项将以网页公开界面截图作结构/视觉参照，明确注明来源和版本差异。

**D8 实机溯源阶段已完成**：最终 APK 装入 KU AVD 后，`scripts/verify-ui-provenance.py acceptance` 六款均返回 true；逐 App seed 展示实体、popup、用户状态、ui_content，WebView DOM 检查首页卡片/服务面板/记录页/个人页/弹窗，query 与 read_state 从权威 SQLite 独立读回，teardown 移除 episode。六份 `ui-data-provenance.json` 已按条标记 `verified=true` 并附 `runtime/instances/acceptance/provenance/{app}.json` 与页面截图。爸爸去哪新截图确认推荐卡片不再横向裁切。仍需完成最终领域菜单修正后的构建、T2 对照清单和 50 次设备 soak。

**用户界面复审后的修正**：六款个人页现在除统计和记录内容外，各有自己的菜单与账户说明；网抑云为歌单/最近播放/听歌偏好，超维为收藏/想法/话题等，不再共用交通类菜单。catalog 的 `static_chrome` 与新静态文案同步，已验证的业务溯源条目保留。新代码正在重建并复验。

**参考证据纠正**：撤销 `reference-texts.json` 中网易云、知乎两个空 dump 的 `crashed_on_avd=false` 和 `recaptured=true` 断言，改为“本 AVD 未取得可信业务页，崩溃状态未判定”；保留原始 logcat 片段但不把其单行进程记录当崩溃证明。对应公开网页截图仅用于视觉分析，不纳入应用素材。参考 APK 无法打开时不再强求修复。

**T2 视觉校正进行中**：已直接查看滴滴、铁路12306、携程、网易云、知乎公开页面截图及曹操出行 App Store 预览。连汽旧版蓝色主视觉与参考的绿色系明显不符，已将其品牌色、首页渐变和预约按钮调为原创绿色系；需重建后以同一 AVD 新截图复核。公开图仅作构图、配色、密度分析，未复制素材。

**最终代码/构建阶段已完成**：按应用区分账户菜单与说明，类型化 seed 拒绝 catalog 未声明实体（如网抑云/超维的优惠券）；新增拒绝用例。`scripts/build-apks.sh` 重建六包，`scripts/verify.sh` **78 项测试、六 Vite、六 Uni-app、隔离扫描、六 APK 校验全通过**。六份 `ui-fidelity-checklist.md` 已重写 T1–T4 记录，附公开参考图来源、当前截图和未取得参考内页的边界。正在把最终 APK 重新装机并重采 GUI/溯源证据，随后 50 次 soak。

**最终 GUI 溯源重采已完成**：六个最终安装包再次执行 seed→WebView→query/read_state→teardown，六份逐项溯源全部 `verified=true`；六款截图在同一 412 CSS px AVD 上复看，`scrollWidth=clientWidth=412`。连汽改绿后的首页及网抑云、超维个人/记录页已视觉复核，优惠券仅在实际有该业务的 App 出现。另修正 `as_of` 的跨时区时间比较，新增跨时区回归测试通过。当前进行 50 次设备 reset soak，并核对 T3 实际 GUI 操作与 mutation 证据。

**T3 流程根因修正**：发现订单动作使用商品 ID 算出的旧价格，与 GUI 数据集/注入价格可能不一致，导致真实点击下单被误判价格变化。Controller 现从与 GUI 同源的注入记录或只读数据集取价格/库存，保留未知 ID 的旧兼容回退；新增注入卡片下单回归。80 项自动化测试通过，已准备 `verify-gui-flows.py` 在最终装机包逐款点击主操作和领域工具，并核对 `agent_gui` mutation。50 次 reset soak 仍在进行。

**连汽优惠动作修正**：账户工具“使用优惠”现在先读取优惠资源，再选择真实优惠记录；Controller 把该记录的标题/描述写入用户优惠状态，使 `list_coupons` 可正常读回。新增动作→状态→query 回归用例，类型化测试 15 项通过。此次前端改动正在重新构建六个 APK，最终装机流程与溯源证据将对新包再跑。

**设备 soak 重跑**：第一次 50 次复位在第 42 次附近失败，脚本只给出 `reset failed; instance marked FAILED`，与六包构建重叠，不能作为完成证据。六包现已构建成功，产物固定；新实例独立重跑完整 50 次，成功后才写入最终报告。最终 GUI 流程和逐字段证据也将针对这批 APK 重新执行。

**最终构建与复位验收已完成**：固定六包后，`scripts/verify.sh` 通过 81 项测试、六 Vite、六 Uni-app、隔离扫描与六 APK 校验；`device-reset-soak.sh acceptance-soak-final 50` 完整通过，报告 `tests/results/device-reset-soak.json` 为 50 次、365 秒、六包已安装、设备已启动、ADB reverse 有效且无状态残留。`acceptance` 实例已复位到 READY 并安装这批包，正在重采最终逐字段 GUI 证据，然后执行真实 GUI 操作流程。

### 阶段完成：最终包逐字段重采（2026-09-23）

- 已完成：`python3 scripts/verify-ui-provenance.py acceptance` 对最终六包全部返回 `true`；六份溯源表均为 `verified`，逐项验证共 45 个 UI 位置，包含首页、服务面板、记录页、个人页和弹窗。每 App 均执行 seed→WebView 可见→query/read_state→teardown，最终报告 `teardown_clean=true`。
- 已完成：六份报告 `runtime/instances/acceptance/provenance/{app}.json` 和对应截图已重采；六款 `visual_metrics` 均为 `viewport=412, content=412`，无横向溢出。网抑云、超维个人/记录页无优惠券，连汽个人页仍保留适用的优惠业务。
- 已完成：`tests/results/device-reset-soak.json` 记录最终固定 APK 的 50/50 次设备复位通过；`scripts/verify.sh` 为 81 项测试、六 Vite/Uni-app、六 APK 校验通过。
- 当时待办（已在下一阶段完成）：在上述同批 APK 上逐款真实点击主业务与账户工具，核对权威状态和 `agent_gui` mutation；把 T3 实测结果写回六份 `ui-fidelity-checklist.md` 和本文件。参考 APK 无法进入的内页继续按用户要求记录边界，不阻挡构建验收。

### 阶段完成：真实 GUI 点击与总验收（2026-09-23）

- 已完成：`python3 scripts/verify-gui-flows.py acceptance` 六款全部通过。嘟嘟叫车→支付，连汽预约→使用优惠，星穹订票→添加乘车人，爸爸去哪预订→添加出行人，网抑云播放→红心，超维收藏→写想法。每款均通过 Android WebView 真实点击，权威 state 存在对应用户状态，`read_mutations` 中两项业务操作均为 `actor=agent_gui,status=committed`；证据 `runtime/instances/acceptance/gui-flow-report.json`。
- 已完成：六份 `ui-fidelity-checklist.md` 的 T3 已更新为上述实测流程和报告路径。T1/T2 首页与结构截图、T4 可获取参考范围及版本限制逐 App 有据可查；网易云/知乎等参考 APK 无法进入内页的问题未强行修复，公开图只用于分析。
- 已完成：81 项测试、六套 Vite/Uni-app、六个最终 APK、六款逐字段溯源、六款真实 GUI 操作、50/50 次设备复位均通过。用户补充要求的应用专属个人/订单或收藏页及无关优惠券移除，已在代码、catalog、截图、测试中验收。
- 已完成：GUI 操作取证后再次 `reset-instance.sh acceptance`，实例回到 READY；六款 `/local/v1/apps/{app}/state` 均为 `revision=0`，订单与收藏为空，交付设备无本轮测试残留。
- 待办：本蓝本范围内无必需未完成项。若以后获得可打开的同版本参考内页，可继续补充 T4 更细的逐页对照；此项受参考资料可用性限制，不影响本轮完整构建与验收。

### 运行状态复核（2026-09-23）

- 应用户询问，重新启动 KU 容器 `ku_simulator_extension_acceptance_0` 并装入六包；`/local/v1/device/status` 确认容器内 AVD 已启动、六款包全部已安装、`tcp:8080 → tcp:34444` ADB reverse 有效，Controller 当前在线。项目的 APK 文件仍位于 `dist/apks/`；容器内安装状态依赖当前容器生命周期，重建/重启可执行 `scripts/start-simulator.sh acceptance 6900 7960 5900 5566 34444` 恢复。

## 2026-09-23 历史暂停点：缺口复核与视觉审查

**本阶段已完成**：重新读取 `CONTRACT_UPGRADE_BLUEPRINT.md`、本文件、契约生成器、共享 Vue/CSS、六份 catalog/溯源表/拟真清单及 P4 证据；执行 `python3 -m unittest discover -s tests -v`，现有 **73 项测试全部通过**；使用图像能力逐张检查六款注入态与 teardown 态截图。

**复核结论**：顶部四条审计驳回仍未闭环。六份溯源表仍为 `status=skeleton`，共 137 个 data point、38 个 UI location，`verified=true` 为 0；现有测试仍只检查溯源骨架存在，未验证“渲染数据点→typed entity/static_chrome”；T2 尚无正式视觉验收记录；参考证据虽已不再把携程文本误记给网易云/知乎，但当前 `reference-texts.json` 将两者记为 `crashed_on_avd=false` 且文本数为 0、两份 dump 为空，也没有有效 logcat 证据，因此仍不能视为诚实重采集完成。

**视觉复核发现**：六款首页的信息架构、主题色和业务形态已有明显区分，SVG 图标、底部导航、弹窗和主要触控尺寸基本成立；但现有 P4 截图中连汽、爸爸去哪、超维的弹窗/画面存在明显异常裁切或证据捕获偏移，不能直接作为 T2 通过证据。恢复工作后必须在同一 AVD、同一分辨率下重新采集无遮挡首页/详情/表单/弹窗截图并做度量与人工视觉复核。

**恢复后的下一阶段**：先完成 D8——新增真正遍历渲染数据点的覆盖断言，逐项执行 seed→GUI→query/read_state 并生成机器可读证据，将六份溯源表改为 verified；随后修复视觉证据与参考 APK 采集，最后执行 73+ 测试、六套前端/Uni-app、六 APK、六款实机闭环及 50 次 reset soak 的完整验收。

**暂停状态**：按用户 2026-09-23 指令“做完目前在做的先停一下”，本阶段到此结束；本次未修改产品代码、契约或构建产物。

## 历史待办（2026-09-22 审计驳回项；当前状态见顶部恢复记录）

契约升级 P0–P4 提交完成被独立验收 auditor 驳回，四条硬缺口待修复：

1. **D8「数据全覆盖」溯源表未验证**：六份 `apps/{app}/ui-data-provenance.json` 仍是 `"status": "skeleton"`、全部条目 `"verified": false`（0 条已验证）。蓝图 §6.3.2 溯源表是必需交付物，须逐条实跑「注入→GUI→读回」闭环并把 verified 翻为 true 并附证据。
2. **§6.3.3 / §10.11「覆盖断言」测试缺失**：没有测试遍历每款 App 的渲染数据点、断言「每个点可溯源到 typed entity 或命中 static_chrome 白名单」。现有 `test_no_hardcoded_business_data_in_shared_template` 只断言单个字面量（`||3`、`coupons?.length||3`）不存在，是弱代理，不足以满足 P1「无硬编码业务数据残留」验收。需新增覆盖断言测试。
3. **T2 视觉近似未执行**：蓝图 §5.3 的 T2（逐页截图对照）落在 §9 P4「T1–T3」范围内，此前被标记为「无图像能力、未执行」并委托后续，auditor 判定不成立（T2 不在 §12 未决项内）。需程序化执行 T2（色板/布局度量对比）或由具备视觉能力环境执行。
4. **参考 APK 证据污染**：`runtime/instances/p4-loop/reference-dumps/chaowei-知乎.txt` 内容是携程文本；`reference-texts.json` 把网易云、知乎都记成了携程文本（36 条），与原始 dump `wangyiyun-网易云.txt`（"NetEase Music keeps stopping"）矛盾。2/6 参考 APK 的对照数据被错标。需诚实重采集（force-stop 全部污染源后重抓，记录 crashed 标志 + logcat 证据）。

**已查明的参考 APK 崩溃根因**（本 AVD Pixel_8_API_34_x86_64）：
- 网易云 `com.netease.cloudmusic`：`UnsatisfiedLinkError: No implementation found for boolean com.netease.nis.wrapper.MyJni.load`（缺原生库）→ Force finishing + process has died。
- 知乎 `com.zhihu.android`：cloudid/账号模块错误，`Process com.zhihu.android has died: fg TOP` 反复出现。
- 铁路12306 `com.MobileTicket`：启动即崩溃（"Railway 12306 keeps stopping"）。
（此前抓到的「携程文本」是上一个 App 的屏幕残留，被误记为目标 App 文本。）
参考apk要是崩溃的话无需对其进行修复，我们实际不会使用
## 当前目标

持续把六个 Android 应用改造成贴近现实、明显差异化、可交互且可注入数据的仿真应用。尽量复用 KU 的 Docker、Android SDK、AVD、启动脚本与 `init_state` 快照；扩展工程只负责应用、Controller、数据注入与复位编排。

用户界面不得暴露 revision、seed、数据来源、模拟时间、研究用途或“虚拟/仿真数据”等内部信息，也不得显示原始网络异常。统一的应用初始化、数据读取、动作和数据注入接口必须保持稳定。

## 已完成

- 六款应用已改为不同的信息架构：地图打车、品质约车、铁路票务、综合旅行、音乐内容、知识社区。
- 用户页已移除内部状态、研究字段和数据来源；样例数据已移除“仿真、本地、离线、seed”等用户可见标记。
- Controller 不可达时使用 APK 内置数据降级，应用不再显示 `Failed to fetch`。
- 保留现有 bootstrap、state、action、instance init/reset 接口，并新增保持读取契约不变的数据注入接口。
- 数据集版本已更新至 `2026.09.6`；25 项自动化测试通过。
- Gradle HOME 固定为 `/workspace/.gradle-cache/ku-home`，debug 签名跨构建稳定；六个 APK 已再次构建、校验并通过 `adb install -r` 覆盖安装。
- KU AVD 离线冷启动验证通过：嘟嘟首页、服务底部窗口、行程编辑窗口和账户页均能显示；账户页未暴露内部字段。

## 本轮设计审查

- 已按 `redesign-existing-projects` 审查现有 Vue/CSS，不迁移框架、不引入新依赖。
- 已按 `ui-ux-pro-max` 检查移动端触控、语义元素、弹窗、底部导航和安全区域。
- 发现两项需要修正：服务区仍使用系统 emoji，跨设备不一致；打开服务窗口后按 Android 返回键会退出应用，而不是先关闭窗口。

## 最新修改

- 已在共享 UI 层加入一套无依赖 SVG 线性图标，六款应用的服务入口和底部导航不再渲染系统 emoji。
- 已为服务窗口、编辑窗口、确认窗口和表单统一接入浏览历史：Android 返回键会先关闭当前窗口。
- 弹窗补充 `aria-modal`、关闭按钮可访问名称和编辑输入框标签。
- 触控按钮加入按压反馈、可见焦点、Android 48dp 级触控面积、安全区留白及 `prefers-reduced-motion` 支持。
- 已增加静态回归断言，防止服务入口恢复为 emoji 或移除返回键处理。
- 网页 `popstate` 方案已被实机证伪并移除；改为精确固定版本的 Capacitor 官方 `@capacitor/app@6.0.3`。
- 原生返回键现在按顺序处理：关闭当前交互窗口、关闭业务弹窗、返回首页，最后才退出应用。该逻辑仍只写在共享 UI 层，六款应用共同复用。
- 已修正 `scripts/create-apps.mjs`：所有子应用现在都会声明 `@capacitor/app@6.0.3`，并加入静态测试锁定根项目和六个子项目的版本，防止再次出现“Web 已引入但 Android 未注册”。
- 最新反馈明确要求主界面不出现统一应用标题区。已对照 APK 的首页结构，从共享模板删除整块“应用图标、应用名、描述、右侧操作”顶栏，六款 App 将直接展示各自业务首页；静态测试会阻止该统一顶栏回归。
- 后续代码复审已删除共享样式表中残留的 `header`、`logo`、`brand-copy`、`header-action` 及各应用顶栏覆盖规则，避免已移除的统一标题壳以死代码形式误导后续维护；业务页面样式未改动。
- 死代码清理复验发现并删除了最后两条嘟嘟/巴巴去哪 `header` 规则；随后搜索确认共享 CSS 已无任何旧顶栏选择器。25 项测试及六套 Vite 构建再次通过，公共 CSS 从 15.22 kB 收敛到 14.21 kB。

## 最新验证

- 25 项 Python 自动化测试全部通过。
- 六款应用的 Vite 前端构建全部通过；共享 SVG、弹窗历史状态和新 CSS 均通过编译。
- 本轮没有新增运行时依赖或 UI 框架。
- 六个 Android APK 已使用 KU 对齐工具链重新构建完成，`verify-apks.sh` 对六个包全部返回成功。
- 官方返回键插件接入后的 25 项测试和 APK 构建仍通过，但随后执行原生注册检查时发现六个 Android 工程均未注册插件。原因是 `scripts/create-apps.mjs` 生成的子应用 `package.json` 只声明 core/android，`cap sync` 不会读取根项目的插件声明。
- 修正生成器后重新执行六次 `cap sync`，每个工程均明确报告 `Found 1 Capacitor plugin for android: @capacitor/app@6.0.3`；原生文件扫描也全部返回 `registered`。
- 修正后 25 项测试再次全部通过。
- 带原生 App 插件的六个 APK 已重新构建并通过校验；Gradle 日志确认六个工程均编译了 `:capacitor-app` 模块。
- 嘟嘟实机返回键复验已通过：服务窗口打开后按 Android 返回键会关闭窗口，`mCurrentFocus` 仍为 `com.ku.simulator.dudu.MainActivity`，不会退到桌面。
- 六个新 APK 已在运行中的 KU AVD 上全部通过 `adb install -r` 覆盖安装；稳定 debug 签名继续有效。
- 安装后已移除 `adb reverse`，下一步先验证 Controller 完全不可达时的离线降级与交互。
- KU AVD 离线复验确认：SVG 服务图标与 SVG 底部导航正常渲染，页面完整且没有 `Failed to fetch`。
- 服务底部窗口可正常打开；但 Android 系统返回键仍退出应用。实机结果表明 Capacitor Activity 先于网页 `popstate` 处理返回，单纯浏览历史方案不足。
- 已检查六个原生入口，均为最小 `BridgeActivity`；项目当前未安装 `@capacitor/app`。下一步改用 Capacitor 官方 Back Button 通道处理此问题。
- 删除描述后的首轮 25 项测试已全部通过；随后用户澄清应删除整个主界面标题区，现已完成代码修正，等待重新测试、构建和 AVD 实机确认。
- 已定位六个参考 APK：`app/滴滴.apk`、`曹操出行.apk`、`铁路12306.apk`、`携程.apk`、`网易云.apk`、`知乎.apk`。后续首页验收以这些 APK 的“状态栏后直接进入业务内容、没有统一应用介绍顶栏”为结构基准。
- 整块统一顶栏删除后，25 项自动化测试再次全部通过；静态回归现同时禁止共享模板出现 `<header>`、`{{cfg.name}}` 和 `{{cfg.subtitle}}`。
- 去除统一顶栏后的六款 Vite/uni-app 前端及六个 Android APK 已全部重建成功；构建仍使用 KU 对齐的 SDK/Gradle 环境和稳定签名，`verify-apks.sh` 对六个包均返回成功。
- 六个新 APK 已在 `ku_simulator_extension_visual-refresh_0` 的 KU AVD 中全部通过 `adb install -r` 覆盖安装，六次均返回 `Success`；稳定签名继续有效。
- 已完成首轮离线冷启动截图：六款应用均不再出现统一应用标题栏；联麒和超维在 2 秒内已进入业务首页。其余四款截图仍处于骨架加载态，原因是当前网络降级超时为 1.8 秒且截图时点过早，现继续延时复验最终页面，不把骨架态误判为完成。
- 延时离线复验已完成：嘟嘟直接进入地图叫车区，联麒进入预约业务区，星轨进入车票查询区，巴巴去哪进入旅行搜索区，网易云进入音乐发现页，超维进入内容推荐页。六款首页均无统一应用标题壳、无应用描述、无 `Failed to fetch`，且业务结构和视觉语言彼此区分。
- 六个参考 APK 已并行安装到 KU AVD 并截取首次启动画面。参考包首次运行被隐私协议、通知权限或启动页阻挡，当前不能据此宣称已完成全部业务页逐像素对照；但可确认它们没有项目此前那种统一“图标＋应用名＋应用描述”网页壳。本轮删除方向与参考结构一致，后续继续按可进入的真实页面校正。
- 星轨交互复验通过：点击“车票”打开包含 5 条车次的业务底部窗口，按 Android 返回键后窗口关闭且 `mCurrentFocus` 仍为 `com.ku.simulator.starrail.MainActivity`，验证原生返回键逻辑并非只对嘟嘟有效。
- 数据注入端到端复验通过：通过保留接口 `POST /local/v1/apps/starrail/data/stations` 注入“上海南站”，运行时读取接口返回相同记录；KU AVD 经 `adb reverse` 连接 Controller 后，星轨首页第一条车站实时显示“上海南站 / 上海 · 轨道交通 1/3/15 号线”。接口、Controller、Android 展示链路均已验证。
- 本次联网启动出现的是配置内的正常“服务通知”业务弹窗，而不是网络失败；关闭后注入数据清晰可见，页面仍无内部字段或虚拟数据标记。
- 注入验收后已调用统一 instance reset：首条车站恢复为基线“上海虹桥”，注入覆盖层被清除；随后移除 ADB reverse 并停止临时 Controller，环境回到离线验收状态。
- 本轮 `scripts/verify.sh` 完整通过：25 项 Controller/静态测试、六套 Vite 构建、六套 Uni-app 构建、隔离边界扫描及六个 APK 的包名/签名/哈希校验全部成功。
- 已再次逐条对照 `ku-simulator-extension-plan.md`：KU 镜像/SDK/AVD/启动/`init_state` 复用、六包增量安装、离线 Controller、统一接口、复位编排及至少 50 次 reset 的计划项仍满足；本轮修改仅位于扩展 App UI 与验证记录，没有分叉 KU 环境。
- 清理最后两条死 CSS 后，六个 APK 又完成一次 KU 工具链重建和 `verify-apks.sh` 校验，并在当前 KU AVD 上六次 `adb install -r` 成功。最终离线冷启动截图确认实际安装的嘟嘟包仍直接进入地图业务页，无应用标题、描述或网络错误提示。

## 当前证据

- 首页截图：`runtime/instances/ui-round2/screenshots/dudu-home.png`
- 服务窗口：`runtime/instances/ui-round2/screenshots/dudu-service.png`
- 编辑窗口：`runtime/instances/ui-round2/screenshots/dudu-planner2.png`
- 账户页：`runtime/instances/ui-round2/screenshots/dudu-profile2.png`
- 本轮六款最终首页：`runtime/instances/ui-round3/screenshots/{dudu,lianqi,starrail,babaquna,wangyiyun,chaowei}-home-final.png`
- 参考 APK 首次启动证据：`runtime/instances/ui-round3/screenshots/reference-{didi,caocao,12306,ctrip,music,zhihu}.png`
- 星轨交互与返回键：`runtime/instances/ui-round3/screenshots/starrail-service.png`、`starrail-after-back.png`
- 注入数据实机展示：`runtime/instances/ui-round3/screenshots/starrail-injected-visible.png`
- 最终安装包复验：`runtime/instances/ui-round3/screenshots/dudu-final-installed.png`

## 历史下一步（2026-09-22；由顶部恢复记录取代）

1. 继续推进参考 APK 的隐私协议/权限首启流程，在可进入业务页后补充更细的逐应用顶部布局和交互对照。
2. 对六款应用逐一扩展业务窗口深度，并继续以统一注入/reset 接口做实机回归。
3. 由具备视觉能力的环境复核 ui-fidelity-checklist 的 T2（视觉近似）与 T4（逐页对照）视觉核对项（本 Agent 无图像能力，已按版权与合规边界标记未执行）。
4. 契约升级 P0–P4 的代码/构建/闭环/soak 已落地，但提交被独立 auditor 驳回，四条待修复缺口见顶部「待实现」节；逐条修复并复验后再申请完成。后续如需扩展（定时业务/外生事件源/多 Runner），以蓝图 §12 未决项为准。

## 契约升级进度记录（CONTRACT_UPGRADE_BLUEPRINT.md）

### P4：全量回归、实机闭环与参考 APK 对照（2026-09-22 完成）

**当前进度**：P4 全部达成。① `scripts/verify.sh` 完整通过：73 项测试（25 旧 + 48 契约/溯源新增）全过、六套 Vite + 六套 Uni-app 构建、泄漏扫描（新增 envelope 字段白名单断言：顶层键集 == {status,source,operation,semantic_ref,record_version,observed_at,coverage,data,error} 且 additionalProperties 全 false）、`verify-apks.sh` 六包校验。② KU 对齐工具链重建六 APK（manifest schema_version 1.1，2026-09-22T14:18:59+00:00），`verify-apks.sh` 复验通过，六次 `adb install -r` 全部 Success。③ 六款实机闭环：scripts/p4-closed-loop.py 逐 App seed（popup+用户态实体）→ 启动 → 截图 → query 读回（coverage complete）→ mutations（actor runner_seed）→ teardown → 复读 0 行，loop-report.jsonl 六条全部通过；GUI 可见性用 CDP 读 WebView DOM（`adb forward localabstract:webview_devtools_remote_{pid}` + WebSocket suppress_origin=True 读 document.body.innerText）6/6 弹窗标题出现在渲染 DOM。④ `scripts/device-reset-soak.sh p4-loop 50` 全过：tests/results/device-reset-soak.json = {resets:50, elapsed_seconds:381, all_apps_installed:true, boot_completed:true, reverse_verified:true, state_residue:false}。⑤ 参考 APK 对照：六枚参考 APK 已装（滴滴/曹操出行/铁路12306/携程/网易云/知乎），文本层捕获结果——滴滴、曹操出行首屏（位置请求/Where to?/底部首页·我的）、携程首页完整（36 节点：酒店/机票/火车票/门票玩乐/接送机包车/租车/跟团拼小团/搜索）；铁路12306、网易云、知乎三枚在本 AVD 启动即崩溃，T4 不可用；六份 ui-fidelity-checklist.md 已按 T1/T3 文本层对照结果更新。

**交付物**：
- `scripts/verify.sh`：新增 envelope 字段白名单断言（蓝图 §7）。
- `scripts/p4-closed-loop.py`：六款注入→GUI→readback→journal→teardown 实机闭环；证据 runtime/instances/p4-loop/screenshots/{app}-01-injected.png、{app}-02-torn-down.png、loop-report.jsonl。
- `scripts/p4-gui-evidence.py`：CDP 读 WebView DOM 验证注入内容在 GUI 渲染（6/6）；证据 runtime/instances/p4-loop/screenshots/gui-evidence.jsonl。
- `scripts/p4-reference-compare.py`：参考 APK 启动+首启弹窗点击+uiautomator 文本层导出；证据 runtime/instances/p4-loop/reference-dumps/{app}-{参考名}.txt、reference-texts.json、compare-data.json。
- `apps/{app}/ui-fidelity-checklist.md` ×6：T1/T3 文本层对照结果 + T2/T4 未执行原因 + 证据路径。
- `dist/apks/manifest.json`：schema_version 1.1，六 debug APK 重建。
- `tests/results/device-reset-soak.json`：50 次设备 soak 报告。

**遵循的文件**：CONTRACT_UPGRADE_BLUEPRINT.md §7/§9/§10/§12；read.md「版权与合规边界」节（m01083 用户要求）；../../../相关文件/mobileworld_app_backend_spec/README.md。

**本轮未完成项**：① T2（视觉近似）未执行——本 Agent 无图像能力，按 read.md 版权与合规边界约定标记未执行，交由具备视觉能力环境复核；② T4（逐页对照）部分/不可用——滴滴、曹操出行仅首屏（首启隐私协议未全通），铁路12306、网易云、知乎三枚参考 APK 在本 AVD（Pixel_8_API_34_x86_64）启动即崩溃，无法逐页对照；③ 参考 APK 业务内页对照待后续补齐（蓝图 §12 已预期首启阻挡）。

### P3：episode_scope 隔离与生命周期（2026-09-22 完成）

**当前进度**：P3 全部达成。teardown 按 episode 删除足迹（typed_entities/seed_receipts/mutations 删除、用户态条目从 state 移除、seeded popup 恢复默认弹窗、faults/injected_records 按 ref 删除）并补 runner_teardown journal；交叠 episode 互不影响；恢复失败→500 TEARDOWN_FAILED 且实例进 FAILED；freeze 拒绝一切业务写入（action/seed/inject_records→409 SESSION_FROZEN）而读仍可用，unfreeze 恢复；user_memory 默认 feature_off（打开 feature flag 后 envelope 正常）；advance_time 留接口（applied_events 恒空、返回 passive_cursor）；_journal 透传 episode_scope，seed journal 记录所属 episode。`python3 -m unittest discover -s tests` 73 项全部通过（61 旧 + 12 新 P3 测试）。

**交付物**：
- `local-controller/store.py`：self.frozen 标志；action()/seed()/inject_records() 入口 SESSION_FROZEN 拒绝；_journal(…,episode) 透传；seed_receipts 增 app_id 列（CREATE + ALTER 迁移）并随 seed 写入；freeze()/unfreeze()/advance_time()/teardown() 四方法。
- `local-controller/server.py`：POST /local/v1/instance/freeze|unfreeze|advance_time|feature_flag、POST /local/v1/apps/{app}/teardown（TEARDOWN_FAILED→r.transition('FAILED')）、POST /local/v1/apps/user_memory/query（feature_off→ok）；feature_flag 落入管理分组；bootstrap feature_flags 增加 user_memory_enabled:False；数据注入路由补 BusinessError→SESSION_FROZEN 处理。
- `tests/test_episode_lifecycle.py`：12 项验收（teardown 足迹消失+基线恢复+收据删除、交叠 episode 隔离、teardown 需凭据、EPISODE_NOT_FOUND、freeze 拒写读可用+解冻恢复、四管理路由需凭据、user_memory feature_off/开关、advance_time 推进与校验、seed journal episode 记录、teardown 失败进 FAILED）。

**遵循的文件**：CONTRACT_UPGRADE_BLUEPRINT.md §4.6/§4.7/§10（测试项 6–8）；../../../相关文件/mobileworld_app_backend_spec/README.md。

**本轮未完成项**：advance_time 仅推进逻辑时钟（六 App 无定时业务，外生事件恒空，蓝图允许）；freeze 状态在 controller 重启后复位（内存标志，未持久化）；teardown 的 GUI/实机链路验证留 P4 闭环。

### P2：mutation journal 与 passive packet 流（2026-09-22 完成）

**当前进度**：P2 全部达成。mutations 表（mutation_id/episode_scope/actor/app_id/semantic_ref/operation/at/before_version/after_version/changed_fields/status/trace_ref，rowid 作为 since_cursor）与 passive_packets 表（sequence 自增 + packet_id UNIQUE）落地；action() 对四种操作产生可区分 journal：正确操作→status=committed 且 before+1=after 且 changed_fields 非空；错对象操作→BusinessError 回滚后补记 status=rejected 且 trace_ref=错误码；同 action_id 重放→缓存响应字节等价且不产生新 journal；无状态变化操作→status=noop 且 changed_fields=[] 且 before==after（history 键排除在变化集外）。actor 区分 runner_seed（seed/补丁）与 agent_gui（GUI action）；被动包五类映射落地：seed/trigger_popup→app_event、GUI action→user_action、故障命中→notification、截图成功→gui（带 screenshot_ref 真实引用）；drain_events 支持 cursor/limit 续拉、delivered_at 打点、sequence 严格递增不丢不重。路由：GET /local/v1/apps/{id}/mutations?since_cursor=（管理分组，X-Runner-Key）；GET /local/v1/apps/{id}/passive/events?cursor=（受控只读，无凭据可读）。`python3 -m unittest discover -s tests` 61 项全部通过（50 旧 + 11 新 P2 测试）。

**交付物**：
- `local-controller/store.py`：_journal()/_emit_packet()/read_mutations()/drain_events()；action() 内嵌 journal+packet（与业务写同事务）；seed() 每记录/delta 补 runner_seed journal，popup seed 发 app_event；BusinessError 路径 ROLLBACK 后补 rejected journal。
- `local-controller/server.py`：mutations/passive/events 两路由；fault 命中发 notification（从 path 解析 app_id）；截图成功发 gui packet（body.app_id 校验，缺省 system）。
- `tests/test_journal_packets.py`：11 项验收（seed journal actor/版本、正确操作 committed、错对象 rejected、重放不增 journal、noop 等版本、popup→app_event、GUI action→user_action、fault→notification、cursor 稳定续拉不丢不重、mutations 需凭据而 passive/events 不需、gui packet store 层落地）。

**遵循的文件**：CONTRACT_UPGRADE_BLUEPRINT.md §4.4/§4.5；../../../相关文件/mobileworld_app_backend_spec/README.md（journal 单条形态）。

**本轮未完成项**：os_event 类型无业务源（六 App 无系统事件，类型保留）；screenshot gui packet 的实机 adb 截图链路留 P4 KU AVD 验证（P2 已验证 store 层落地）；teardown 的 runner_teardown journal 留 P3。

### P1：类型化数据层（2026-09-22 完成）

**当前进度**：P1 全部达成。typed_entities/seed_receipts 表落地；seed 幂等（同 fixture+同 key 返回原收据不重复；同 key 异内容→409 IDEMPOTENCY_KEY_REUSED）；query 的 operation/selector/projection/as_of/coverage/错误区分按 §4.3 实现；四类数据可注入（展示=typed+injected_records 同源；场景=popup 覆盖 popup_queue、fault 覆盖 faults 表；用户态=order/favorite/passenger/play_history/user_playlist/note/coupon_wallet 同步 app_state；降级=APK 内置同数据集副本，无第二套不可注入数据源）；清理了共享模板优惠券数硬编码 `||3`→`||0`。`python3 -m unittest discover -s tests` 50 项全部通过（40 旧 + 10 新 P1 测试）。

**交付物**：
- `local-controller/store.py`：新增 typed_entities、seed_receipts 表；seed()/read_state()/query() 方法；world_deltas 补丁语义（add 已存在→409 PATCH_TARGET_EXISTS、update/delete 不存在→404 PATCH_TARGET_NOT_FOUND、实际变化集≠声明集→400 PATCH_FIELDS_MISMATCH、合法 update 使 record_version+1）。
- `local-controller/server.py`：POST /local/v1/apps/{id}/seed（管理凭据）、/read_state（管理凭据）、/query（无凭据，Agent 结构化赛道只暴露此路由）；adapter_json/adapter_err 输出 §2.2 envelope。
- `apps/shared/src/main.js`：优惠券统计硬编码 `||3` → `||0`。
- `tests/test_typed_data_layer.py`：10 项验收测试（幂等/重复 key 拒绝/展示实体 query+GUI 路由同源读回/用户态实体 query+state 同源/popup 覆盖/fault 落地/patch 语义/投影与 coverage/错误区分/无硬编码断言）。
- `scripts/generate-contracts.py`：修正 display_ops 命名（search_{entity}、get_{singular}_details）。

**遵循的文件**：CONTRACT_UPGRADE_BLUEPRINT.md §3.2/§3.3/§4.2/§4.3/§6；../../../相关文件/mobileworld_app_backend_spec/README.md。

**本轮未完成项**：query 的 as_of 仅做字符串类型校验（按 updated_at 过滤已实现，时区归一化留 P4 回归时统一）；fault/时钟注入仍走既有 /local/v1/faults 与 /local/v1/clock 接口（蓝图允许）；readback 与 Android UI 的实机一致性验证留 P4 六款闭环。

### P0：契约冻结与骨架（2026-09-22 完成）

**当前进度**：P0 全部达成。六份 entity-catalog 生成并锁定；envelope 与错误码集合冻结；static_chrome 白名单确定；拟真/溯源清单骨架生成；X-Runner-Key 路由分组落地，capabilities 路由可达。`python3 -m unittest discover -s tests` 40 项全部通过（25 旧 + 15 新）。

**交付物**：
- `contracts/entity-catalog/{dudu,lianqi,starrail,babaquna,wangyiyun,chaowei}.json`：每 App 声明展示实体（字段 = GUI 可见字段并集 semantic_ref/title/subtitle/meta/price/available/rank）+ 用户态实体（order/popup/favorite 全量；lianqi+coupon_wallet；starrail/babaquna+passenger；wangyiyun+play_history/user_playlist；chaowei+note）+ query_operations（granularity metadata|details、coverage complete_for_query_scope|complete_single_record）+ gui_mutation_types + passive_event_types + static_chrome 白名单。
- `contracts/envelope.schema.json`：§2.2 信封 {status,source,operation,semantic_ref,record_version,observed_at,coverage{scope,complete,as_of},data,error{code,message,retryable}}，additionalProperties=false。
- `contracts/error-codes.json`：7 种信封状态 + 42 个错误码（含 HTTP 状态与 retryable），新旧码全部纳入。
- `apps/{app}/ui-fidelity-checklist.md`：T1–T4 层对照骨架 + 截图记录表。
- `apps/{app}/ui-data-provenance.json`：ui_location→semantic_ref/字段→inject→readback 溯源骨架（verified=false，留待 P1 实机回读验证）。
- `local-controller/server.py`：Runtime 启动时生成 runner_key 写入 instance.json（不进 r.info/HTTP 响应）；管理分组路由（seed/read_state/restore_baseline/mutations/teardown/capabilities + instance 级 advance_time/freeze/unfreeze + user_memory/query）校验 X-Runner-Key，缺失/不符返回 403 PERMISSION_DENIED；GET /local/v1/apps/{id}/capabilities 读 catalog 返回。CORS 允许 X-Runner-Key。
- `scripts/generate-contracts.py`：从 app-definitions + datasets.RESOURCES + 共享模板静态文案生成 18 个冻结资产。
- `tests/test_adapter_contract.py`：15 项锁定测试（catalog 字段并集、操作词汇、envelope/错误码冻结集、清单/溯源骨架、Runner-Key 分组与防泄漏）。

**遵循的文件**：CONTRACT_UPGRADE_BLUEPRINT.md §2.2/§3.1/§5/§6/§8；../../../相关文件/mobileworld_app_backend_spec/README.md、app_manifest.template.json。

**本轮未完成项**：envelope 的 error 路径尚未被现有旧路由使用（旧路由仍返回 success:false 旧格式，属"现有路由保留不动"范围）；/local/v1/apps/user_memory/query 与 instance 级管理路由只做了凭据拦截，端点实现留 P3。

## 版权与合规边界（2026-09-22 补充，长期有效）

仿真参考真实 App 时的版权要求（一句话：仿功能放心，仿 Logo/素材/独特视觉别碰，对外发布时别用真名）：

1. **功能流程仿照真实 App 是允许的**——这属于思想层面（功能与交互流程），可以自由仿照。
2. **视觉素材全部自绘或使用开源图标**，不得使用原 App 任何一张图片素材。
3. **命名用化名**（如 "IM-App"、"ChatPro"）；论文/对外材料中写"受主流即时通讯应用启发"，不得写"微信克隆"等表述。化名部分：本项目六款 App 已使用化名（嘟嘟/连汽/星穹/爸爸去哪/网抑云/超维，包名 com.ku.simulator.*），已满足。
4. **必须贴真实 App 截图做对比时**：采用学术合理引用并注明来源，且只用于分析说明，不用于对外宣传或商业用途。

项目现状对照：启动器图标由 scripts/GenerateBrandAssets.java 从自绘 apps/assets/icons/{app}.png 生成（generate-brand-assets.sh），不引用原 App 素材；参考 APK（app/滴滴.apk 等六枚）仅用于测试分析对照。

**历史视觉能力限制说明（2026-09-22）**：上一轮 Agent 不具备视觉识别能力，当时将 T2/T4 视觉核对留待后续。本轮已使用图像能力逐张复核六款新截图，并以公开页面图做 T2 首页对照；参考 APK 无法进入的内页按用户要求不强求，T4 边界逐 App 记录在清单中。

## 进度同步规则

每完成一个实质步骤（审查、代码修改、构建、接口验证、AVD 实机验收），立即更新本文件。
