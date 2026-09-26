import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import gui_capture


def write_executable(path, body):
    path.write_text('#!/usr/bin/env bash\n' + body)
    path.chmod(0o755)


class ResetScriptBudgetTest(unittest.TestCase):
    """Runs the real reset-instance.sh against fake adb/docker/curl in a temp root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'runtime/instances/i1').mkdir(parents=True)
        shutil.copy(ROOT / 'scripts/reset-instance.sh', self.root / 'scripts/reset-instance.sh')
        (self.root / 'scripts/reset-instance.sh').chmod(0o755)
        write_executable(self.root / 'scripts/ensure-controller.sh', 'exit 0\n')
        write_executable(self.root / 'scripts/ensure-adb-reverse.sh', 'exit 0\n')
        (self.root / 'runtime/instances/i1/instance.json').write_text(json.dumps({
            'adb_serial': '127.0.0.1:5556', 'controller_port': 39999, 'ku_container': '',
            'snapshot': 'init_state', 'runner_key': 'test-key'}))
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.adb_log = self.root / 'adb-calls.log'
        self.curl_log = self.root / 'curl-calls.log'
        write_executable(self.bin / 'adb', f'''echo "$*" >> "{self.adb_log}"
if [[ -n "${{FAKE_ADB_HANG:-}}" && "$*" == *"$FAKE_ADB_HANG"* ]]; then sleep "${{FAKE_ADB_SLEEP:-30}}"; fi
if [[ -n "${{FAKE_ADB_FAIL:-}}" && "$*" == *"$FAKE_ADB_FAIL"* ]]; then echo "adb boom" >&2; exit 9; fi
if [[ -n "${{FAKE_ADB_MISSING:-}}" && "$*" == *"$FAKE_ADB_MISSING"* ]]; then exit 1; fi
if [[ "$*" == *"getprop sys.boot_completed"* ]]; then echo "${{FAKE_ADB_BOOT_STATE:-1}}"; exit 0; fi
exit 0
''')
        write_executable(self.bin / 'docker', 'exit 0\n')
        write_executable(self.bin / 'curl', f'''echo "$*" >> "{self.curl_log}"
if [[ "$*" == *health* ]]; then echo '{{"lifecycle":"READY"}}'; fi
exit 0
''')

    def tearDown(self):
        self.tmp.cleanup()

    def run_reset(self, budget, adb_timeout='10', **extra):
        env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}',
                   RESET_TOTAL_BUDGET_SECONDS=str(budget), ADB_RESET_TIMEOUT_SECONDS=adb_timeout,
                   FAKE_ADB_LOG=str(self.adb_log), FAKE_CURL_LOG=str(self.curl_log), **extra)
        return subprocess.run([str(self.root / 'scripts/reset-instance.sh'), 'i1'],
                              capture_output=True, text=True, env=env, timeout=30)

    def log(self):
        return (self.root / 'runtime/instances/i1/reset.log').read_text()

    def test_failure_names_phase_and_marks_instance_failed(self):
        result = self.run_reset(30, FAKE_ADB_FAIL='emu avd snapshot load')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('phase=snapshot-load failed', result.stderr)
        self.assertIn('reset failed at phase=snapshot-load', result.stderr)
        log = self.log()
        self.assertIn('phase=force-stop-chaowei status=ok', log)
        self.assertIn('phase=snapshot-load status=failed code=9', log)
        self.assertIn('reset status=failed phase=snapshot-load exit_code=9', log)
        self.assertIn('duration=', log)
        self.assertIn('{"state":"FAILED"}', (self.root / 'curl-calls.log').read_text())

    def test_step_limit_is_capped_by_deadline_not_adb_timeout(self):
        started = time.monotonic()
        result = self.run_reset(3, adb_timeout='120', FAKE_ADB_HANG='emu avd snapshot load', FAKE_ADB_SLEEP='60')
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 20)
        self.assertIn('phase=snapshot-load failed', result.stderr)
        log = self.log()
        self.assertIn('phase=snapshot-load status=failed code=124', log)
        self.assertIn('budget=3s', log)

    def test_pm_path_timeout_does_not_fall_through_to_install(self):
        for app in ('dudu', 'lianqi', 'starrail', 'babaquna', 'wangyiyun', 'chaowei'):
            apk = self.root / f'dist/apks/{app}-debug.apk'
            apk.parent.mkdir(parents=True, exist_ok=True)
            apk.write_bytes(b'apk')
        result = self.run_reset(3, adb_timeout='120', FAKE_ADB_HANG='pm path', FAKE_ADB_SLEEP='60')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('phase=dudu-pm-path', result.stderr)
        self.assertNotIn(' install ', self.adb_log.read_text())

    def test_missing_package_pm_path_is_installable(self):
        for app in ('dudu', 'lianqi', 'starrail', 'babaquna', 'wangyiyun', 'chaowei'):
            apk = self.root / f'dist/apks/{app}-debug.apk'
            apk.parent.mkdir(parents=True, exist_ok=True)
            apk.write_bytes(b'apk')
        result = self.run_reset(30, FAKE_ADB_MISSING='pm path', FAKE_ADB_FAIL='install -r')
        self.assertIn('phase=dudu-pm-path status=missing', self.log())
        self.assertIn('phase=install-dudu', result.stderr)

    def test_boot_wait_uses_global_deadline_not_fixed_180s(self):
        started = time.monotonic()
        result = self.run_reset(3, FAKE_ADB_BOOT_STATE='0')
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 20)
        self.assertIn('phase=boot-wait', result.stderr)
        self.assertIn('phase=boot-wait status=failed', self.log())

    def test_budget_is_below_capture_timeout(self):
        source = (ROOT / 'scripts/gui_capture.py').read_text()
        self.assertIn('reset_instance(instance)', source)
        self.assertNotIn("reset-instance.sh'), instance, timeout=300", source)
        self.assertLess(gui_capture.RESET_TOTAL_BUDGET_SECONDS, gui_capture.RESET_CAPTURE_TIMEOUT_SECONDS)

    def test_controller_supervisor_does_not_inherit_recovery_lock(self):
        script = (ROOT / 'scripts/ensure-controller.sh').read_text()
        self.assertIn('9>&- </dev/null', script)


class CaptureResetHelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'runtime/instances/i1').mkdir(parents=True)
        (self.root / 'runtime/instances/i1/reset.log').write_text(
            'phase=snapshot-load status=start budget_remaining=240s\n'
            'phase=snapshot-load status=failed code=124 step_limit=120s duration=120s\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_helper_passes_budget_and_reports_failed_phase(self):
        class Process:
            pid = 4321
            returncode = 1

            def communicate(self, timeout=None):
                return b'', b'reset failed at phase=snapshot-load (exit=124)\n'

        with unittest.mock.patch.object(gui_capture, 'ROOT', self.root), \
             unittest.mock.patch.object(gui_capture.subprocess, 'Popen', return_value=Process()) as popen:
            with self.assertRaisesRegex(RuntimeError, 'snapshot-load'):
                gui_capture.reset_instance('i1')
        environment = popen.call_args.kwargs['env']
        self.assertEqual(environment['RESET_TOTAL_BUDGET_SECONDS'],
                         str(gui_capture.RESET_TOTAL_BUDGET_SECONDS))
        self.assertEqual(popen.call_args.args[0], (str(self.root / 'scripts/reset-instance.sh'), 'i1'))

    def test_outer_timeout_kills_group_and_is_not_silent(self):
        class Process:
            pid = 4321
            returncode = None

            def __init__(self):
                self.calls = 0

            def communicate(self, timeout=None):
                self.calls += 1
                if timeout is not None:
                    raise subprocess.TimeoutExpired('reset-instance.sh', timeout)
                return b'', b''

        process = Process()
        with unittest.mock.patch.object(gui_capture, 'ROOT', self.root), \
             unittest.mock.patch.object(gui_capture.subprocess, 'Popen', return_value=process), \
             unittest.mock.patch.object(gui_capture.os, 'killpg') as killpg:
            with self.assertRaisesRegex(RuntimeError, 'exceeded .* capture timeout'):
                gui_capture.reset_instance('i1')
        killpg.assert_called_once_with(process.pid, gui_capture.signal.SIGKILL)
        self.assertEqual(process.calls, 2)


if __name__ == '__main__':
    unittest.main()
