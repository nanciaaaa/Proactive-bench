"""User-reported account, billing, popup and music regressions."""
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'local-controller'))
from store import Store
from store import BusinessError


class ProductGapsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name)
        self.store = Store(path / 'state.sqlite', 'product-gaps', 42,
                           '2026-09-23T09:00:00+08:00', path / 'events.jsonl')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def action(self, app, typ, item_id='sample', **extra):
        revision, _ = self.store.state(app)
        return self.store.action(app, {'action_id': str(uuid.uuid4()), 'type': typ,
                                       'payload': {'item_id': item_id, **extra},
                                       'expected_revision': revision})

    def test_launch_has_no_unsolicited_popup(self):
        for app in ('dudu', 'lianqi', 'starrail', 'babaquna', 'wangyiyun', 'chaowei'):
            self.assertEqual(self.store.state(app)[1]['popup_queue'], [])

    def test_selected_ticket_refund_and_change_price(self):
        self.store.inject_records('starrail', 'seats', [
            {'id': 'old', 'title': '二等座', 'subtitle': 'G100', 'price': 70, 'available': 4},
            {'id': 'new', 'title': '一等座', 'subtitle': 'G200', 'price': 110, 'available': 4}])
        first = self.action('starrail', 'book', 'old', train='G100', train_schedule='08:00 上海 → 09:00 杭州')['state']['orders'][-1]
        self.assertIn('08:00', first['train_schedule'])
        second = self.action('starrail', 'book', 'old')['state']['orders'][-1]
        for order in (first, second):
            self.action('starrail', 'pay', order_id=order['id'])
        with self.assertRaises(BusinessError):
            self.action('starrail', 'change_order', order_id=first['id'], new_item_id='unknown', train='G200')
        changed = self.action('starrail', 'change_order', order_id=first['id'], new_item_id='new', train='G200', train_schedule='10:00 上海 → 11:00 杭州')['state']['orders']
        self.assertEqual((changed[0]['price'], changed[0]['fare_difference'], changed[0]['train'], changed[1]['status']),
                         (110, 40, 'G200', 'PAID'))
        self.assertIn('10:00', changed[0]['train_schedule'])
        refunded = self.action('starrail', 'refund', order_id=second['id'])['state']['orders']
        self.assertEqual((refunded[0]['status'], refunded[1]['status']), ('CHANGED', 'REFUNDED'))

    def test_traveler_is_saved_to_selected_order_before_checkout(self):
        first = self.action('babaquna', 'book', 'hotel-1')['state']['orders'][-1]
        second = self.action('babaquna', 'book', 'hotel-2')['state']['orders'][-1]
        orders = self.action('babaquna', 'set_traveler', order_id=first['id'], name='张三', phone='13800138000')['state']['orders']
        self.assertEqual(orders[0]['traveler_name'], '张三')
        self.assertEqual(orders[1]['traveler_name'], '')
        with self.assertRaises(BusinessError):
            self.action('babaquna', 'set_traveler', order_id=second['id'], name='李四', phone='bad')

    def test_order_has_semantic_bill_discount_and_payment_history(self):
        self.store.inject_records('lianqi', 'routes', [
            {'id': 'trip-1', 'title': '机场接送', 'subtitle': '虹桥机场 → 人民广场',
             'price': 64, 'available': 8}])
        self.store.inject_records('lianqi', 'coupons', [
            {'id': 'coupon-1', 'title': '满 50 减 10', 'subtitle': '满 50 减 10',
             'price': 0, 'available': 8}])
        order = self.action('lianqi', 'create_order', 'trip-1', expected_price=64)['state']['orders'][-1]
        self.assertEqual((order['title'], order['subtitle'], order['base_price']),
                         ('机场接送', '虹桥机场 → 人民广场', 64))
        discounted = self.action('lianqi', 'use_coupon', 'coupon-1', order_id=order['id'])['state']['orders'][-1]
        self.assertEqual((discounted['discount'], discounted['price']), (10, 54))
        paid = self.action('lianqi', 'pay', order_id=order['id'])['state']['orders'][-1]
        self.assertEqual(paid['status'], 'PAID')
        self.assertEqual(paid['payments'][0]['amount'], 54)
        self.assertEqual(paid['payments'][0]['method'], '账户余额')

    def test_account_menus_and_music_controls_are_contextual(self):
        source = (ROOT / 'apps/shared/src/main.js').read_text()
        self.assertNotIn('@click="openService(entry)"', source.split('class="settings-list"')[1])
        self.assertIn('class="account-section"', source)
        self.assertIn('class="song-like"', source)
        self.assertIn('class="mini-player"', source)

    def test_account_field_is_saved_and_queryable(self):
        state = self.action('dudu', 'save_account_field', key='home_address', value='上海虹桥站')['state']
        self.assertEqual(state['account_fields']['home_address'], '上海虹桥站')
        queried = self.store.query('dudu', 'list_account_fields')
        self.assertIn({'semantic_ref': 'home_address', 'key': 'home_address',
                       'value': '上海虹桥站'}, queried['data'])

    def test_coupon_threshold_and_percentage_cap(self):
        self.store.inject_records('lianqi', 'routes', [
            {'id': 'route', 'title': '机场接送', 'subtitle': '虹桥机场 → 市区', 'price': 100, 'available': 8}])
        self.store.inject_records('lianqi', 'coupons', [
            {'id': 'threshold', 'title': '满 120 减 20', 'subtitle': '满 120 减 20', 'price': 0, 'available': 8},
            {'id': 'percent', 'title': '企业 8 折', 'subtitle': '最高减 15', 'price': 0, 'available': 8}])
        order = self.action('lianqi', 'create_order', 'route', expected_price=100)['state']['orders'][-1]
        with self.assertRaises(BusinessError):
            self.action('lianqi', 'use_coupon', 'threshold', order_id=order['id'])
        discounted = self.action('lianqi', 'use_coupon', 'percent', order_id=order['id'])['state']['orders'][-1]
        self.assertEqual((discounted['discount'], discounted['price']), (15, 85))

    def test_choose_coupon_then_choose_none(self):
        self.store.inject_records('lianqi', 'routes', [
            {'id': 'ride', 'title': '即时用车·舒适型', 'subtitle': '预计 7 分钟', 'price': 82, 'available': 8}])
        self.store.inject_records('lianqi', 'coupons', [
            {'id': 'small', 'title': '满 50 减 8', 'subtitle': '满 50 减 8'},
            {'id': 'large', 'title': '满 80 减 12', 'subtitle': '满 80 减 12'}])
        order = self.action('lianqi', 'create_order', 'ride', expected_price=82, reservation_time='明天 09:30')['state']['orders'][-1]
        self.assertEqual(order['reservation_time'], '明天 09:30')
        first = self.action('lianqi', 'use_coupon', 'small', order_id=order['id'])['state']['orders'][-1]
        self.assertEqual(first['price'], 74)
        second = self.action('lianqi', 'use_coupon', 'large', order_id=order['id'])['state']['orders'][-1]
        self.assertEqual((second['discount'], second['price']), (12, 70))
        none = self.action('lianqi', 'remove_coupon', order_id=order['id'])['state']['orders'][-1]
        self.assertEqual((none['discount'], none['price'], none['coupon_title']), (0, 82, ''))

    def test_checkout_requires_traveler_and_six_digit_password(self):
        self.store.inject_records('babaquna', 'hotels', [
            {'id': 'hotel', 'title': '湖畔酒店', 'subtitle': '大床房', 'price': 200, 'available': 5}])
        order = self.action('babaquna', 'book', 'hotel', expected_price=200,
                            traveler_name='张三', traveler_phone='13800138000')['state']['orders'][-1]
        with self.assertRaises(BusinessError):
            self.action('babaquna', 'checkout_pay', order_id=order['id'], method='微信支付', password='123')
        paid = self.action('babaquna', 'checkout_pay', order_id=order['id'],
                           method='支付宝支付', password='123456')['state']['orders'][-1]
        self.assertEqual(paid['payments'][0]['method'], '支付宝支付')
        self.assertNotIn('123456', str(paid))

    def test_common_traveler_keeps_phone_for_checkout(self):
        passenger = self.action('babaquna', 'add_passenger', 'person-1',
                                name='李四', phone='13900139000')['state']['passengers'][0]
        self.assertEqual(passenger['phone'], '13900139000')
        self.assertEqual(self.store.query('babaquna', 'list_passengers')['data'][0]['phone'], '13900139000')

    def test_topic_playlist_and_cancel_keep_their_own_state(self):
        self.store.inject_records('chaowei', 'articles', [
            {'id': 'article-1', 'title': '夜间列车体验', 'subtitle': '出行记录', 'topic_id': 'topic-1'}])
        self.assertEqual(self.store.injected_records('chaowei', 'articles')[0]['topic_id'], 'topic-1')
        self.store.inject_records('wangyiyun', 'songs', [
            {'id': 'song-1', 'title': '第一首', 'subtitle': '歌手'},
            {'id': 'song-2', 'title': '第二首', 'subtitle': '歌手'}])
        self.store.inject_records('wangyiyun', 'playlists', [
            {'id': 'playlist-1', 'title': '歌单', 'subtitle': '两首', 'track_ids': ['song-1', 'song-2']}])
        playlist = self.store.injected_records('wangyiyun', 'playlists')[0]
        self.assertIn('song-2', playlist['track_ids'])
        self.action('wangyiyun', 'add_playlist', 'my-list', name='我的歌单')
        with self.assertRaises(BusinessError):
            self.action('wangyiyun', 'add_to_playlist', 'playlist-1', playlist_id='my-list')
        self.action('wangyiyun', 'add_to_playlist', 'song-1', playlist_id='my-list')
        self.assertEqual(self.store.state('wangyiyun')[1]['playlists'][0]['songs'], ['song-1'])
        self.store.inject_records('dudu', 'vehicle_quotes', [
            {'id': 'ride', 'title': '特惠快车', 'subtitle': '预计 4 分钟', 'price': 32, 'available': 8}])
        order = self.action('dudu', 'call_car', 'ride', expected_price=32)['state']['orders'][-1]
        with self.assertRaises(BusinessError):
            self.action('dudu', 'cancel_order', order_id=order['id'], confirm_loss=True)

    def test_paid_reservation_cancels_and_records_refund_once(self):
        self.store.inject_records('lianqi', 'routes', [
            {'id': 'ride', 'title': '预约用车', 'subtitle': '09:30 出发', 'price': 82, 'available': 4}])
        order = self.action('lianqi', 'create_order', 'ride', expected_price=82)['state']['orders'][-1]
        paid = self.action('lianqi', 'checkout_pay', order_id=order['id'], method='微信支付', password='123456')['state']['orders'][-1]
        self.assertEqual(paid['status'], 'PAID')
        cancelled = self.action('lianqi', 'cancel_order', order_id=order['id'])['state']['orders'][-1]
        self.assertEqual(cancelled['status'], 'CANCELLED')
        self.assertEqual([(entry['method'], entry['amount']) for entry in cancelled['payments']],
                         [('微信支付', 82), ('预约取消退款', -82)])
        with self.assertRaises(BusinessError):
            self.action('lianqi', 'cancel_order', order_id=order['id'])


if __name__ == '__main__':
    unittest.main()
