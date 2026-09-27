import json,re,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class StaticCase(unittest.TestCase):
 def test_packages_are_unique_and_exact(self):
  defs=json.loads((ROOT/'apps/app-definitions.json').read_text())
  self.assertEqual(set(defs),{'dudu','lianqi','starrail','babaquna','wangyiyun','chaowei'})
  self.assertEqual({v['package'] for v in defs.values()},{f'com.ku.simulator.{x}' for x in defs})
  self.assertEqual({v['layout'] for v in defs.values()},{'map','schedule','ticket','travel','music','reader'})
  self.assertTrue(all(len(v['planner'])==3 for v in defs.values()))
  self.assertTrue(all(v['icon']==f'/icons/{app}.png' for app,v in defs.items()))
  for app in defs:
   self.assertTrue((ROOT/f'apps/assets/icons/{app}.png').is_file())
   self.assertTrue((ROOT/f'apps/shared/public/icons/{app}.png').is_file())
 def test_controller_binds_loopback(self):
  source=(ROOT/'local-controller/server.py').read_text();self.assertIn("('127.0.0.1',a.port)",source);self.assertNotIn("('0.0.0.0'",source)
 def test_no_bench_import_or_endpoints(self):
  needles=('knowu'+'_bench','/task/'+'callback','/suite_'+'family')
  for path in list((ROOT/'local-controller').glob('*.py'))+list((ROOT/'apps/shared/src').glob('*')):
   text=path.read_text()
   for needle in needles:self.assertNotIn(needle,text,path)
 def test_all_native_projects_and_labels_exist(self):
  defs=json.loads((ROOT/'apps/app-definitions.json').read_text())
  for app in defs:
   self.assertTrue((ROOT/f'apps/{app}/android/app/src/main/AndroidManifest.xml').exists())
   self.assertTrue((ROOT/f'apps/{app}/capacitor.config.json').exists());self.assertTrue((ROOT/f'apps/{app}/pages/index/index.vue').exists())
  source=(ROOT/'apps/shared/src/main.js').read_text();self.assertIn('aria-label',source);self.assertIn('data-ready',source);self.assertIn('role="alert"',source)
  self.assertNotIn('prompt(',source);self.assertIn('servicePanel',source);self.assertIn('plannerEditor',source);self.assertIn('__FALLBACK_DATA__',source);self.assertIn('utility-input',source)
  self.assertIn('service-icon',source);self.assertIn("App.addListener('backButton'",source);self.assertNotIn('{{entry.icon}}',source)
  self.assertNotIn('<header>',source);self.assertNotIn('{{cfg.name}}',source);self.assertNotIn('{{cfg.subtitle}}',source)
  for hidden in ('实例 revision','模拟时间：','数据来源：','研究用合成数据','确定性场景','Failed to fetch'):self.assertNotIn(hidden,source)
 def test_contract_schemas_and_lifecycle_scripts_exist(self):
  for name in ('bootstrap.schema.json','action.schema.json','event.schema.json','data-injection.schema.json'):json.loads((ROOT/'contracts'/name).read_text())
  for name in ('start-instance.sh','controller-supervisor.sh','ensure-controller.sh','reset-instance.sh','start-ku-environment.sh','stop-ku-environment.sh','start-simulator.sh','check-ku-runtime.sh','build-apks.sh'):self.assertTrue((ROOT/'scripts'/name).exists())
  fixture=json.loads((ROOT/'datasets/fixtures/research-seed-v1.json').read_text())
  self.assertEqual(fixture['origin'],'synthetic_research_fixture');self.assertEqual(len(fixture['resources']),14)
  self.assertTrue((ROOT/'datasets/fixtures/README.md').is_file())
  for rows in fixture['resources'].values():
   self.assertGreaterEqual(len(rows),5)
   for title,subtitle,price,available,*body in rows:
    self.assertIsInstance(title,str);self.assertIsInstance(subtitle,str);self.assertIsInstance(price,int);self.assertIsInstance(available,int)
    self.assertFalse(any(word in title+subtitle for word in ('仿真','模拟','本地','离线','合成','研究','seed')))
 def test_android_toolchain_matches_ku(self):
  package=json.loads((ROOT/'package.json').read_text())
  for dependency in ('@capacitor/core','@capacitor/android','@capacitor/cli'):self.assertEqual(package['dependencies'][dependency],'6.2.1')
  self.assertEqual(package['dependencies']['@capacitor/app'],'6.0.3')
  for app in ('dudu','lianqi','starrail','babaquna','wangyiyun','chaowei'):
   self.assertEqual(json.loads((ROOT/f'apps/{app}/package.json').read_text())['dependencies']['@capacitor/app'],'6.0.3')
   variables=(ROOT/f'apps/{app}/android/variables.gradle').read_text()
   build=(ROOT/f'apps/{app}/android/build.gradle').read_text()
   capacitor=(ROOT/f'apps/{app}/android/app/capacitor.build.gradle').read_text()
   wrapper=(ROOT/f'apps/{app}/android/gradle/wrapper/gradle-wrapper.properties').read_text()
   self.assertIn('compileSdkVersion = 34',variables);self.assertIn('targetSdkVersion = 34',variables)
   self.assertIn("com.android.tools.build:gradle:8.2.1",build)
   self.assertIn('JavaVersion.VERSION_17',capacitor);self.assertIn('gradle-8.2.1-all.zip',wrapper)
 def test_runtime_delegates_to_ku(self):
  config=dict(line.split('=',1) for line in (ROOT/'config/ku-runtime.env').read_text().splitlines() if line and not line.startswith('#'))
  self.assertEqual(config['KU_IMAGE'],'ghcr.io/yaosqz/knowu-bench:latest')
  self.assertEqual(config['KU_AVD_NAME'],'Pixel_8_API_34_x86_64');self.assertEqual(config['KU_SNAPSHOT'],'init_state')
  self.assertEqual(config['KU_ANDROID_API'],'34');self.assertEqual(config['KU_BUILD_TOOLS'],'34.0.0')
  launch=(ROOT/'scripts/start-ku-environment.sh').read_text();self.assertIn('"$KU_CLI" env run',launch);self.assertNotIn('--entrypoint',launch)
  simulator=(ROOT/'scripts/start-simulator.sh').read_text();self.assertIn('export KU_IMAGE KU_SNAPSHOT',simulator)
  start=(ROOT/'scripts/start-instance.sh').read_text();self.assertIn('controller-supervisor.sh',start);self.assertIn("curl -fsS \"http://127.0.0.1:$PORT/health\"",start)
  reset=(ROOT/'scripts/reset-instance.sh').read_text();self.assertIn('ensure-controller.sh',reset)
  self.assertIn('ADB_RESET_TIMEOUT_SECONDS',reset);self.assertIn('APK install timed out or failed',reset)
  self.assertIn('LOCAL_HASH',reset);self.assertIn('continue',reset)
  reset=(ROOT/'scripts/reset-instance.sh').read_text();self.assertIn('docker exec "$KU_CONTAINER" adb emu avd snapshot load "$SNAPSHOT"',reset);self.assertNotIn('snapshot save',reset)
  self.assertIn('instance remains CONTAMINATED',reset)
  setup=(ROOT/'scripts/setup-android-sdk.sh').read_text();self.assertNotIn('sdkmanager',setup);self.assertNotIn('curl ',setup)
  build=(ROOT/'scripts/build-apks.sh').read_text();self.assertIn('HOME=/workspace/.gradle-cache/ku-home',build);self.assertIn('-Duser.home="$HOME"',build);self.assertNotIn('HOME=/tmp/ku-extension-home',build)
if __name__=='__main__':unittest.main()
