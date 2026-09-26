"""Fail when a new visible Vue binding has no declared data source."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ('dudu', 'lianqi', 'starrail', 'babaquna', 'wangyiyun', 'chaowei')
RESOURCES = {'dudu': ('vehicle_quotes',), 'lianqi': ('routes',),
             'starrail': ('trains',), 'babaquna': ('flights',),
             'wangyiyun': ('songs',), 'chaowei': ('articles',)}

# These are interface labels or local feedback; business bindings are checked below.
CHROME = {
    'JSON.stringify({app:cfg.id,state},null,2)', 'LABELS[name]||name',
    "cfg.layout==='map'?'附近推荐':cfg.layout==='schedule'?'可选服务':cfg.layout==='ticket'?'查询结果':cfg.layout==='travel'?'精选推荐':cfg.layout==='music'?'为你推荐':'推荐内容'",
    'cfg.primaryLabel', 'cfg.recordTitle', 'cfg.profileSubtitle', 'entry.label', 'stat.label',
    "cfg.id==='lianqi'?'支付预约订单':'支付订单'",
    'accountSection.label',
    'error', 'form.entry.input.label', 'form.entry.label', 'label',
    'plannerEditor.label', 'servicePanel.label', 'toast',
    'utilityConfirm.entry.confirm', 'utilityConfirm.entry.label',
    "cfg.layout==='travel'?'查询'+servicePage.label:'查看车型'",
    "cfg.id==='lianqi'?'取消预约':'取消预订'",
    "cfg.layout==='map'?'确认并呼叫':'确认'+cfg.primaryLabel",
    'orderAction?.label', "tab==='change-train'?'选择新车次':'选择新席次'",
    "cfg.layout==='music'&&resource==='playlists'?'查看歌单':cfg.primaryLabel",
}
BUSINESS = {
    'cardMark(item)', 'item.subtitle', 'item.title', "item.title.split('｜')[0]",
    'itemFooter(item)', 'order.price', 'pending.subtitle', 'pending.title',
    'popup.message', 'popup.title', 'statCount(stat.key)', 'itemName(id)',
    'list.name', 'list.songs?.length||0', 'note.text', 'person.name', 'person.phone', "person.phone||'未填写手机号'", 'coupon.title', 'coupon.discount',
    'itemName(currentTrack)', 'orderName(order)', 'orderName(couponOrder)',
    'orderStatus(order.status)', 'couponOrder.base_price??couponOrder.price',
    'coupon.discount||coupon.subtitle',
    'servicePage.label', 'trainChoice.title', 'item.price', 'order.subtitle',
    'currentOrder.origin', 'currentOrder.destination', 'currentOrder.subtitle', 'currentOrder.service_type', 'currentOrder.reservation_time', 'currentOrder.train', 'currentOrder.train_schedule', 'currentOrder.seat',
    'currentOrder.fare_difference', "currentOrder.fare_difference>0?'+':''",
    'currentOrder.traveler_name', 'currentOrder.traveler_phone', 'currentOrder.price',
    'currentOrder.base_price??currentOrder.price', 'currentOrder.discount', 'currentOrder.coupon_title',
    "currentOrder.coupon_title||'选择优惠券'", 'orderName(currentOrder)', 'orderStatus(currentOrder.status)',
    'p.method', 'p.amount', 'formatTime(p.at)', 'songItem.title', 'songItem.subtitle',
    'order.origin', 'order.destination', 'trainChoice.subtitle', 'trainChoice.price',
    'flowItem.title', 'flowItem.subtitle', 'flowItem.price', 'flowItem.body||flowItem.subtitle',
    "tab==='change-seat'?(item.price-currentOrder.base_price>=0?'+':'')+(item.price-currentOrder.base_price):''",
    'song.title', 'song.subtitle', 'playlistPage.name||playlistPage.title', 'readerItem.title',
    'readerItem.subtitle', "readerItem.body||'这条内容暂时没有正文。'",
    'readingTopic.title', 'readingTopic.subtitle', 'article.title', 'article.subtitle',
    'playlistTracks.length', 'place.title', 'place.subtitle', 'value',
    'activeRide.vehicle_plate', 'activeRide.driver', 'activeRide.eta_minutes',
    'rideOrigin', 'rideDestination', "couponOrder.coupon_title===coupon.title?'已选择':'选择此券'",
    "state.favorites?.includes(songItem.id)?'♥ 已红心':'♡ 红心'",
    "state.favorites?.includes(readerItem.id)?'取消收藏':'收藏内容'",
    "state.playing?'正在播放':'已暂停'", "state.playing?'Ⅱ':'▶'",
    "state.favorites?.includes(currentTrack)?'♥':'♡'", "state.favorites?.includes(item.id)?'♥':'♡'",
    'cfg.hero', 'cfg.profileName', 'entry.value', 'plannerValues[0].value',
    'plannerValues[1].value', 'plannerValues[2].value',
}


class UIProvenanceCase(unittest.TestCase):
    def test_all_visible_bindings_are_classified(self):
        source = (ROOT / 'apps/shared/src/main.js').read_text()
        bindings = set(re.findall(r'{{\s*(.*?)\s*}}', source))
        self.assertEqual(bindings, CHROME | BUSINESS)
        self.assertNotIn('FALLBACK[servicePanel.value?.resource]', source)
        self.assertNotIn('(FALLBACK.playlists||[])', source)

    def test_business_fields_have_catalog_and_provenance(self):
        for app in APPS:
            catalog = json.loads((ROOT / 'contracts/entity-catalog' / f'{app}.json').read_text())
            types = {e['name']: set(e['fields']) for e in catalog['entity_types']}
            provenance = json.loads((ROOT / 'apps' / app / 'ui-data-provenance.json').read_text())
            points = {(p['semantic_ref'].split('.')[0], p['field'])
                      for entry in provenance['provenance'] for p in entry['data_points']}
            self.assertEqual(provenance['status'], 'verified')
            self.assertTrue(all(entry['verified'] for entry in provenance['provenance']))
            required = set(RESOURCES[app]) | {'popup', 'ui_content', 'account_field'}
            if app not in ('wangyiyun', 'chaowei'):required.add('order')
            if app == 'wangyiyun':required.update(('play_history','user_playlist'))
            if app == 'chaowei':required.add('note')
            for entity in required:
                self.assertTrue(any(ref == entity for ref, _ in points), f'{app}.{entity} is not mapped')
            if app not in ('wangyiyun', 'chaowei'):
                self.assertTrue({'title', 'origin', 'destination', 'base_price', 'discount', 'coupon_title', 'payments'} <=
                                {field for ref, field in points if ref == 'order'})
            if app == 'wangyiyun':
                self.assertIn(('play_history', 'playing'), points)
            if app == 'chaowei':
                self.assertIn(('articles', 'body'), points)
            for ref, field in points:
                self.assertIn(ref, types, f'{app}.{ref} has no typed entity')
                bare = field.split('.')[0]
                if field.endswith('.length'):
                    self.assertIn(ref, ('order', 'favorite', 'coupon_wallet', 'play_history',
                                        'user_playlist', 'note', 'passenger'))
                else:
                    self.assertIn(bare, types[ref], f'{app}.{ref}.{field} is undeclared')

    def test_record_and_profile_sections_match_each_app(self):
        configs = json.loads((ROOT / 'apps/app-definitions.json').read_text())
        self.assertEqual(len({tuple(x['label'] for x in c['accountMenu']) for c in configs.values()}), 6)
        for app in APPS:
            config = configs[app]
            keys = {stat['key'] for stat in config['accountStats']}
            self.assertEqual(len(keys), len(config['accountStats']))
            self.assertTrue(config['recordTitle'])
            catalog = json.loads((ROOT / 'contracts/entity-catalog' / f'{app}.json').read_text())
            types = {e['name'] for e in catalog['entity_types']}
            self.assertEqual('coupon_wallet' in types, 'coupons' in keys)
            chrome = set(catalog['static_chrome']['app_specific'])
            self.assertIn(config['profileSubtitle'], chrome)
            self.assertTrue({x['label'] for x in config['accountMenu']} <= chrome)
            if app == 'wangyiyun': self.assertEqual(keys, {'play_history', 'favorites', 'playlists'})
            if app == 'chaowei': self.assertEqual(keys, {'favorites', 'notes'})


if __name__ == '__main__':
    unittest.main()
