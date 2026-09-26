import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class NotificationContractTest(unittest.TestCase):
    def test_dudu_declares_native_high_importance_notification_receiver(self):
        manifest = (ROOT / 'apps/dudu/android/app/src/main/AndroidManifest.xml').read_text()
        source = (ROOT / 'apps/dudu/android/app/src/main/java/com/ku/simulator/dudu/NotificationReceiver.java').read_text()
        icon = ROOT / 'apps/dudu/android/app/src/main/res/drawable/ic_stat_notification.xml'
        self.assertIn('android.permission.POST_NOTIFICATIONS', manifest)
        self.assertIn('android:name=".NotificationReceiver"', manifest)
        self.assertIn('NotificationManager.IMPORTANCE_HIGH', source)
        self.assertIn('com.ku.simulator.dudu.PUBLISH_NOTIFICATION', source)
        self.assertIn('setContentIntent', source)
        self.assertIn('R.drawable.ic_stat_notification', source)
        self.assertTrue(icon.is_file())

    def test_notification_surface_is_explicitly_capability_scoped(self):
        capabilities = json.loads((ROOT / 'contracts/gui-capabilities.json').read_text())
        self.assertEqual(capabilities['system_surface']['heads_up_notification'], 'missing')

    def test_controller_notification_route_is_runner_key_protected(self):
        server = (ROOT / 'local-controller/server.py').read_text()
        self.assertIn("r'/local/v1/apps/([^/]+)/notification'", server)
        self.assertIn("NOTIFICATION_APPS={'dudu'}", server)
        self.assertIn("android.permission.POST_NOTIFICATIONS", server)

    def test_messages_native_sms_pilot_is_declared_and_wired(self):
        capabilities = json.loads((ROOT / 'contracts/gui-capabilities.json').read_text())
        messages = capabilities['core_apps']['messages']
        self.assertTrue(messages['authority'].startswith('Android SMS provider'))
        self.assertEqual(messages['views']['conversation_list'], ['sender', 'body'])
        source = (ROOT / 'scripts/gui_messages.py').read_text()
        capture = (ROOT / 'scripts/gui_capture.py').read_text()
        qa = (ROOT / 'scripts/gui_machine_qa.py').read_text()
        self.assertIn("adb(serial, 'emu', 'sms', 'send'", source)
        self.assertIn('ready_message', capture)
        self.assertIn("'conversation_list'", qa)

    def test_messages_contact_label_keeps_phone_as_seed_authority(self):
        from scripts.gui_contract import messages_visible
        visible = messages_visible({'sender': 'GuiPilotTwin', 'sender_phone': '13800138000',
                                    'body': 'Your contact update is ready.'})
        self.assertEqual(visible['sender'], 'GuiPilotTwin')

    def test_messages_pilot_episode_compiles(self):
        from scripts.gui_contract import compile_capture_plan
        plan = compile_capture_plan(ROOT / 'datasets/gui-messages-pilot/author.json')
        self.assertEqual(plan['packets'][0]['capture']['expected_visible']['sender'], '13800138000')
        self.assertEqual(plan['packets'][0]['capture']['expected_visible']['body'], 'Your contact update is ready.')
