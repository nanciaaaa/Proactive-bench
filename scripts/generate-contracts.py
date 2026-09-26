#!/usr/bin/env python3
"""生成 P0 契约冻结资产：六份 contracts/entity-catalog/{app}.json、
六份 apps/{app}/ui-data-provenance.json、六份 apps/{app}/ui-fidelity-checklist.md。
数据源：apps/app-definitions.json + local-controller/datasets.py(RESOURCES) + 共享模板静态文案。
产物是冻结资产，测试（tests/test_adapter_contract.py）锁定其与 GUI 可见字段的一致性。
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'local-controller'))
from datasets import RESOURCES  # noqa: E402

APPS_DEF = json.loads((ROOT / 'apps/app-definitions.json').read_text())
APPS = ['dudu', 'lianqi', 'starrail', 'babaquna', 'wangyiyun', 'chaowei']

# 共享模板静态文案（apps/shared/src/main.js 中不注入的业务骨架文案）
SHARED_CHROME = [
    '操作成功', '重试', '加载更多', '没有找到相关内容', '这里还没有记录', '知道了', '返回',
    '确认', '保存', '取消', '完成', '查看全部', '为你精选以下内容', '查看全部回答', '更多 ›',
    '下单时间 ', '订单', '收藏', '优惠券', '正在播放', '已更新',
    '已关闭', '刚刚', '价格已更新，请重新确认', '当前名额不足，请选择其他方案',
    '该时间与已有安排冲突', '已经收藏过了', '暂时无法刷新，请稍后重试', '页面信息已更新，请重试',
    '暂时没有可操作的订单', '暂时无法完成，请稍后重试', '充足', '热度 ', '关闭',
]
LAYOUT_CHROME = {
    'map': ['附近推荐'],
    'schedule': ['专属出行管家', '专业司机 · 标准服务 · 全程守护', '查询车辆', '可选服务'],
    'ticket': ['出发地', '到达地', '周日', '查询车票', '铁路出行温馨提示', '查看详情 ›', '查询结果'],
    'travel': ['搜索目的地、酒店或景点', '周末酒店', '精选套餐 6 折起', '城市玩乐', '热门门票随心订', '精选推荐'],
    'music': ['搜索音乐、歌手、歌单', '私人推荐', '今天也要听点喜欢的', '▶ 播放', '为你推荐'],
    'reader': ['推荐', '热榜', '关注', '专栏', '今日热议', '有哪些看似普通，却值得认真回答的问题？',
               '来自你关注的科技、旅行与生活话题', '查看讨论', '搜问题、文章和话题', '推荐内容'],
}
RESOURCE_LABELS = {'places': '地点', 'vehicle_quotes': '车型', 'routes': '用车', 'coupons': '优惠',
                   'stations': '车站', 'trains': '车次', 'seats': '席别', 'flights': '机票',
                   'hotels': '酒店', 'attractions': '门票', 'songs': '单曲', 'playlists': '歌单',
                   'articles': '推荐', 'tags': '话题'}
DISPLAY_FIELDS = ['semantic_ref', 'title', 'subtitle', 'meta', 'price', 'available', 'rank']

def singular(entity):
    return entity[:-1] if entity.endswith('s') else entity

def display_ops(entity):
    one = singular(entity)
    return [
        {'name': f'search_{entity}', 'granularity': 'metadata',
         'selector_fields': ['keyword', 'limit', 'offset'],
         'result_fields': ['semantic_ref', 'title', 'subtitle', 'rank'],
         'permission': 'query.read', 'coverage': 'complete_for_query_scope'},
        {'name': f'get_{one}_details', 'granularity': 'details',
         'selector_fields': ['semantic_ref'],
         'result_fields': DISPLAY_FIELDS + (['body'] if entity in ('articles', 'tags', 'attractions') else []) + (['topic_id'] if entity == 'articles' else []) + (['track_ids'] if entity == 'playlists' else []),
         'permission': 'query.read', 'coverage': 'complete_single_record'},
    ]

def user_state_entities(app):
    cfg = APPS_DEF[app]
    entities = [
        {'name': 'ui_content', 'fields': ['semantic_ref', 'hero', 'planner', 'profile_name'],
         'query_operations': [
             {'name': 'get_ui_content_details', 'granularity': 'details',
              'selector_fields': ['semantic_ref'],
              'result_fields': ['semantic_ref', 'hero', 'planner', 'profile_name'],
              'permission': 'query.read', 'coverage': 'complete_single_record'}]},
        {'name': 'order', 'fields': ['semantic_ref', 'item_id', 'title', 'subtitle', 'base_price', 'discount', 'coupon_title', 'price', 'fare_difference', 'status', 'created_at', 'payments', 'origin', 'destination', 'service_type', 'reservation_time', 'train', 'train_schedule', 'seat', 'traveler_name', 'traveler_phone', 'vehicle_plate', 'driver', 'eta_minutes'],
         'query_operations': [
             {'name': 'list_orders', 'granularity': 'metadata', 'selector_fields': ['limit', 'offset'],
              'result_fields': ['semantic_ref', 'item_id', 'price', 'status', 'created_at'],
              'permission': 'query.read', 'coverage': 'complete_for_query_scope'},
             {'name': 'get_order_details', 'granularity': 'details', 'selector_fields': ['semantic_ref'],
              'result_fields': ['semantic_ref', 'item_id', 'title', 'subtitle', 'base_price', 'discount', 'coupon_title', 'price', 'fare_difference', 'status', 'created_at', 'payments', 'origin', 'destination', 'service_type', 'reservation_time', 'train', 'train_schedule', 'seat', 'traveler_name', 'traveler_phone', 'vehicle_plate', 'driver', 'eta_minutes'],
              'permission': 'query.read', 'coverage': 'complete_single_record'}]},
        {'name': 'popup', 'fields': ['semantic_ref', 'kind', 'title', 'message'],
         'query_operations': [
             {'name': 'list_active_popups', 'granularity': 'metadata', 'selector_fields': ['limit'],
              'result_fields': ['semantic_ref', 'kind', 'title', 'message'],
              'permission': 'query.read', 'coverage': 'complete_for_query_scope'}]},
        {'name': 'favorite', 'fields': ['semantic_ref', 'item_id'],
         'query_operations': [
             {'name': 'list_favorites', 'granularity': 'metadata', 'selector_fields': ['limit'],
              'result_fields': ['semantic_ref', 'item_id'],
              'permission': 'query.read', 'coverage': 'complete_for_query_scope'}]},
        {'name': 'account_field', 'fields': ['semantic_ref', 'key', 'value'],
         'query_operations': [
             {'name': 'list_account_fields', 'granularity': 'metadata', 'selector_fields': ['limit'],
              'result_fields': ['semantic_ref', 'key', 'value'],
              'permission': 'query.read', 'coverage': 'complete_for_query_scope'}]},
    ]
    if app in ('dudu', 'lianqi', 'babaquna'):
        entities.append({'name': 'coupon_wallet', 'fields': ['semantic_ref', 'code', 'title', 'discount'],
                         'query_operations': [
                             {'name': 'list_coupons', 'granularity': 'metadata', 'selector_fields': ['limit'],
                              'result_fields': ['semantic_ref', 'code', 'title', 'discount'],
                              'permission': 'query.read', 'coverage': 'complete_for_query_scope'}]})
    if app in ('starrail', 'babaquna'):
        entities.append({'name': 'passenger', 'fields': ['semantic_ref', 'name', 'phone'],
                         'query_operations': [
                             {'name': 'list_passengers', 'granularity': 'metadata', 'selector_fields': ['limit'],
                              'result_fields': ['semantic_ref', 'name', 'phone'],
                              'permission': 'query.read', 'coverage': 'complete_for_query_scope'}]})
    if app == 'wangyiyun':
        entities += [
            {'name': 'play_history', 'fields': ['semantic_ref', 'item_id', 'at', 'playing'],
             'query_operations': [
                 {'name': 'list_play_history', 'granularity': 'metadata', 'selector_fields': ['limit'],
                  'result_fields': ['semantic_ref', 'item_id', 'at', 'playing'],
                  'permission': 'query.read', 'coverage': 'complete_for_query_scope'}]},
            {'name': 'user_playlist', 'fields': ['semantic_ref', 'name', 'songs'],
             'query_operations': [
                 {'name': 'list_user_playlists', 'granularity': 'metadata', 'selector_fields': ['limit'],
                  'result_fields': ['semantic_ref', 'name', 'songs'],
                  'permission': 'query.read', 'coverage': 'complete_for_query_scope'}]},
        ]
    if app == 'chaowei':
        entities.append({'name': 'note', 'fields': ['semantic_ref', 'item_id', 'text'],
                         'query_operations': [
                             {'name': 'list_notes', 'granularity': 'metadata', 'selector_fields': ['limit'],
                              'result_fields': ['semantic_ref', 'item_id', 'text'],
                              'permission': 'query.read', 'coverage': 'complete_for_query_scope'}]})
    if app in ('wangyiyun', 'chaowei'):
        entities = [e for e in entities if e['name'] != 'order']
    return entities

def mutation_types(app):
    cfg = APPS_DEF[app]
    types = {u['type'] for u in cfg['utilities']} | {cfg['primary']}
    if app == 'wangyiyun':
        types |= {'add_to_playlist', 'like', 'pause', 'resume', 'play_all'}
    if app == 'chaowei':
        types.add('toggle_favorite')
    if app not in ('wangyiyun', 'chaowei'):
        types.add('checkout_pay')
    if app in ('dudu', 'lianqi', 'babaquna'):
        types.add('remove_coupon')
    types |= {'trigger_popup', 'dismiss_popup', 'favorite', 'save_account_field'}
    return sorted(types)

def build_catalog(app):
    cfg = APPS_DEF[app]
    display = [{'name': r, 'fields': DISPLAY_FIELDS + (['body'] if r in ('articles', 'tags', 'attractions') else []) + (['topic_id'] if r == 'articles' else []) + (['track_ids'] if r == 'playlists' else []),
                'query_operations': display_ops(r)} for r in RESOURCES[app]]
    return {
        'schema_version': '0.1.0',
        'app_id': app,
        'kind': 'android_app',
        'android_package': f'com.ku.simulator.{app}',
        'adapter_version': f'{app}-adapter-v1',
        'adapter_contract_version': '1.0',
        'storage_scope': ['local_controller_sqlite'],
        'layout': cfg['layout'],
        'entity_types': display + user_state_entities(app),
        'permissions': ['query.read'],
        'gui_mutation_types': mutation_types(app),
        'passive_event_types': ['popup_permission', 'popup_promotion', 'popup_payment',
                                'popup_cancel_loss', 'popup_membership', 'business_error',
                                'user_action', 'gui_frame'],
        'readback_scope': [r for r in RESOURCES[app]] + [e['name'] for e in user_state_entities(app)],
        'reset_scope': [r for r in RESOURCES[app]] + [e['name'] for e in user_state_entities(app)],
        'static_chrome': {'shared': SHARED_CHROME, 'app_specific': app_chrome(app)},
    }

def app_chrome(app):
    cfg = APPS_DEF[app]
    parts = [cfg['name'], cfg['subtitle'], cfg['primaryLabel'], cfg['recordTitle'],
             cfg['profileSubtitle'], *(entry['label'] for entry in cfg['accountMenu']),
             *(stat['label'] for stat in cfg['accountStats']),
             *(s['label'] for s in cfg['services']),
             *(s['label'] for s in cfg.get('decorativeServices', [])),
             *(p['label'] for p in cfg['planner']),
             *cfg['nav'], *(u['label'] for u in cfg['utilities']),
             *(u['confirm'] for u in cfg['utilities'] if u.get('confirm')),
             *(u['input']['label'] for u in cfg['utilities'] if u.get('input')),
             *(u['input']['default'] for u in cfg['utilities'] if u.get('input')),
             *(RESOURCE_LABELS[r] for r in RESOURCES[app]),
             *LAYOUT_CHROME[cfg['layout']], *cfg['capabilities']]
    return sorted({p for p in parts if p})

def build_provenance(app):
    cfg = APPS_DEF[app]
    seed = f'POST /local/v1/apps/{app}/seed（typed entity，episode_scope+idempotency_key）'
    qry = f'POST /local/v1/apps/{app}/query'
    entries = []
    for r in RESOURCES[app]:
        visible = ['title', 'subtitle']
        if cfg['layout'] != 'reader': visible.append('price')
        if cfg['layout'] not in ('reader', 'music'): visible.append('available')
        entries.append({
            'ui_location': f'首页卡片列表（resource={r}）',
            'data_points': [{'field': field, 'semantic_ref': f'{r}.*', 'inject': seed, 'readback': qry}
                            for field in visible],
            'verified': False,
        })
    orders_title = cfg['recordTitle']
    record_fields = ([('play_history','item_id'), ('favorite','item_id'), ('user_playlist','name')]
                     if cfg['layout'] == 'music' else
                     [('favorite','item_id'), ('note','text')]
                     if cfg['layout'] == 'reader' else
                     [('order', field) for field in ('item_id','price','status','created_at')]
                     + [('favorite','item_id')]
                     + ([('passenger','name')] if app == 'starrail' else [])
                     + ([('coupon_wallet','title'),('coupon_wallet','discount')]
                        if app in ('dudu','lianqi','babaquna') else []))
    entries += [
        {'ui_location': '首页文案、计划器及账户昵称', 'data_points': [
            {'field': field, 'semantic_ref': 'ui_content.*', 'inject': seed, 'readback': qry}
            for field in (('hero',) if cfg['layout'] in ('schedule','travel','music') else ())
            + (('planner',) if cfg['layout'] in ('map','schedule','ticket','travel') else ())
            + ('profile_name',)], 'verified': False},
        {'ui_location': f'记录页（{orders_title}）', 'data_points': [
            {'field': field, 'semantic_ref': entity+'.*', 'inject': seed, 'readback': qry}
            for entity,field in record_fields], 'verified': False},
        {'ui_location': '账户页统计', 'data_points': [
            {'field': f"{stat['key']}.length", 'semantic_ref': {
                'orders': 'order', 'favorites': 'favorite', 'coupons': 'coupon_wallet',
                'play_history': 'play_history', 'playlists': 'user_playlist',
                'notes': 'note', 'passengers': 'passenger'}[stat['key']] + '.*',
             'inject': seed, 'readback': qry} for stat in cfg['accountStats']], 'verified': False},
        {'ui_location': '业务弹窗（title/message/kind）', 'data_points': [
            {'field': 'title', 'semantic_ref': 'popup.*', 'inject': seed, 'readback': qry},
            {'field': 'message', 'semantic_ref': 'popup.*', 'inject': seed, 'readback': qry}], 'verified': False},
        {'ui_location': '服务面板（title/subtitle）', 'data_points': [
            {'field': 'title', 'semantic_ref': f"{cfg['services'][0]['resource']}.*", 'inject': seed, 'readback': qry},
            {'field': 'subtitle', 'semantic_ref': f"{cfg['services'][0]['resource']}.*", 'inject': seed, 'readback': qry}], 'verified': False},
    ]
    if cfg['layout'] == 'music':
        entries.append({'ui_location': 'mini-player（最近播放）', 'data_points': [
            {'field': 'item_id', 'semantic_ref': 'play_history.*', 'inject': seed, 'readback': qry}], 'verified': False})
    return {'schema_version': '0.1.0', 'app_id': app, 'status': 'skeleton',
            'provenance': entries,
            'note': 'P0 骨架：data_points 由共享模板（apps/shared/src/main.js）推导，verified 留待 P1 实机回读验证。'}

REF_APKS = {'dudu': 'app/滴滴.apk', 'lianqi': 'app/曹操出行.apk', 'starrail': 'app/铁路12306.apk',
            'babaquna': 'app/携程.apk', 'wangyiyun': 'app/网易云.apk', 'chaowei': 'app/知乎.apk'}

def build_checklist(app):
    cfg = APPS_DEF[app]
    rows = [
        ('T1 信息架构', '首页直接进业务内容、底部导航/宫格与参考 App 同构', '待对照'),
        ('T2 视觉近似', '主题色/密度/列表项/弹窗/底部窗口形态贴近参考', '待对照'),
        ('T3 流程等价', '核心流程 GUI 可操作且产生真实状态变化（journal 佐证）', '待执行'),
        ('T4 逐页对照', '参考 APK 可进入页面逐一截图并排记录', '待补齐'),
    ]
    lines = [
        f'# {cfg["name"]}（{app}）UI 拟真对照清单',
        '',
        f'> 参考 APK：{REF_APKS[app]}；目标：{cfg["prototype"]}。',
        '> 依据：../../_archive/contract-upgrade-20260926/CONTRACT_UPGRADE_BLUEPRINT.md §5。P0 骨架，逐项状态随 T 层工作更新；新增页面/组件必须同步更新本清单。',
        '',
        '| 层 | 检查项 | 状态 |',
        '|---|---|---|',
        *[f'| {l} | {c} | {s} |' for l, c, s in rows],
        '',
        '## 对照截图记录',
        '',
        '| 页面 | 参考截图 | 本 App 截图 | 差异说明 |',
        '|---|---|---|---|',
        '| 首页 | （待补） | （待补） | |',
        '',
        '## 已知差异 / 未完成项',
        '',
        '- 参考 APK 首启隐私/权限流程未全通，T4 逐页对照需在可进入业务页后补齐。',
        '',
    ]
    return '\n'.join(lines)

for app in APPS:
    (ROOT / 'contracts/entity-catalog').mkdir(exist_ok=True)
    cat_path = ROOT / 'contracts/entity-catalog' / f'{app}.json'
    cat_path.write_text(json.dumps(build_catalog(app), ensure_ascii=False, indent=2) + '\n')
    prov_path = ROOT / 'apps' / app / 'ui-data-provenance.json'
    if not prov_path.exists() or json.loads(prov_path.read_text()).get('status') != 'verified':
        prov_path.write_text(json.dumps(build_provenance(app), ensure_ascii=False, indent=2) + '\n')
    cl_path = ROOT / 'apps' / app / 'ui-fidelity-checklist.md'
    if not cl_path.exists():cl_path.write_text(build_checklist(app))
print(json.dumps({'catalogs': 6, 'provenance': 6, 'checklists': 6}, ensure_ascii=False))
