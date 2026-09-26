"""Hermetic activation tests for the KU simulator startup chain.

Each test builds a temporary extension root and puts fake docker/adb/curl/KU
CLI binaries on PATH, so the real scripts run their real control flow without
Docker, ADB, the network or any KU credential file.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ('dudu', 'lianqi', 'starrail', 'babaquna', 'wangyiyun', 'chaowei')
INSTANCE = 'i1'
KU_IMAGE = 'ghcr.io/yaosqz/knowu-bench:latest'
CONTAINER = f'ku_simulator_extension_{INSTANCE}_0'
SCRIPTS = (
    'start-simulator.sh', 'start-ku-environment.sh', 'start-novnc.sh', 'stop-novnc.sh',
    'stop-ku-environment.sh', 'ensure-controller.sh', 'start-instance.sh', 'stop-instance.sh',
    'reset-instance.sh', 'ensure-adb-reverse.sh', 'controller-supervisor.sh',
)

DOCKER_FAKE = r'''#!/usr/bin/env bash
set -u
echo "docker $*" >>"${FAKE_DOCKER_LOG:?}"
STATE="${FAKE_DOCKER_STATE:?}"
case "$*" in
  "image inspect "*)
    for arg in "$@"; do [[ "$arg" == *'{{.Id}}'* ]] && { echo 'sha256:fakeimage'; exit 0; }; done
    exit 0 ;;
  "container inspect "*)
    [[ -f "$STATE/container" ]] || exit 1
    for arg in "$@"; do
      case "$arg" in
        *'{{.State.Status}}'*) cat "$STATE/status"; exit 0 ;;
        *'{{.State.Running}}'*) [[ "$(cat "$STATE/status")" == running ]] && echo true || echo false; exit 0 ;;
        *'{{.Config.Image}}'*) cat "$STATE/image"; exit 0 ;;
      esac
    done
    exit 0 ;;
  "start "*|"container start "*)
    [[ -f "$STATE/container" ]] || exit 1
    if [[ "${FAKE_DOCKER_START_FAIL:-0}" == 1 ]]; then echo 'docker: cannot start container' >&2; exit 1; fi
    echo running >"$STATE/status"; exit 0 ;;
  "inspect "*)
    for arg in "$@"; do case "$arg" in *'{{.Image}}'*) echo 'sha256:fakeimage'; exit 0 ;; esac; done
    exit 0 ;;
  "port "*)
    [[ -f "$STATE/container" ]] || exit 1
    cat "$STATE/ports"; exit 0 ;;
  "exec "*)
    shift
    case "$*" in
      *"adb get-state"*) echo "${FAKE_ADB_STATE:-device}"; exit 0 ;;
      *"shell getprop sys.boot_completed"*) echo "${FAKE_BOOT_STATE:-1}"; exit 0 ;;
      *) exit 0 ;;
    esac ;;
esac
exit 0
'''

ADB_FAKE = r'''#!/usr/bin/env bash
set -u
echo "adb $*" >>"${FAKE_ADB_LOG:?}"
STATE="${FAKE_DOCKER_STATE:?}"
if [[ "${1:-}" == "devices" ]]; then
  echo 'List of devices attached'
  [[ "${FAKE_ADB_ONLINE:-0}" == 1 ]] && printf '%s\tdevice\n' "${FAKE_ADB_SERIAL:-127.0.0.1:5556}"
  exit 0
fi
if [[ "${1:-}" == "-s" ]]; then shift 2; fi
case "$*" in
  "connect "*) exit 0 ;;
  "get-state") echo "${FAKE_GET_STATE:-device}"; exit 0 ;;
  "wait-for-device") exit 0 ;;
  "reverse --remove tcp:8080") exit 0 ;;
  "reverse tcp:8080 tcp:"*) echo "${3##*:}" >"$STATE/reverse_port"; exit 0 ;;
  "reverse --list") printf 'tcp:8080 tcp:%s\n' "$(cat "$STATE/reverse_port" 2>/dev/null || echo 0)"; exit 0 ;;
esac
case "$*" in
  *"shell getprop sys.boot_completed"*) echo "${FAKE_BOOT_STATE:-1}"; exit 0 ;;
  *"shell am force-stop "*) exit 0 ;;
  *"shell pm path "*) all="$*"; pkg="${all##*shell pm path }"; printf 'package:/data/app/%s/base.apk\n' "$pkg"; exit 0 ;;
  *"shell sha256sum "*) all="$*"; remote="${all##*shell sha256sum }"; pkg="${remote%/base.apk}"; app="${pkg##*.}"
    sha256sum "${FAKE_APK_DIR:?}/$app-debug.apk" | cut -d' ' -f1; exit 0 ;;
esac
exit 0
'''

CURL_FAKE = r'''#!/usr/bin/env bash
set -u
echo "curl $*" >>"${FAKE_CURL_LOG:?}"
url=""
for arg in "$@"; do case "$arg" in http://*|https://*) url="$arg" ;; esac; done
case "$url" in
  */vnc.html)
    if [[ "${FAKE_VNC_OK:-1}" == 1 ]]; then exit 0; fi
    echo 'curl: (7) Failed to connect to 127.0.0.1' >&2; exit 7 ;;
esac
exit 0
'''

KU_CLI_FAKE = r'''#!/usr/bin/env bash
set -u
echo "mobile-world $*" >>"${FAKE_KU_LOG:?}"
STATE="${FAKE_DOCKER_STATE:?}"
if [[ "${FAKE_KU_FAIL:-0}" == 1 ]]; then echo 'ku cli: docker run failed' >&2; exit 1; fi
if [[ "${1:-} ${2:-}" == "env rm" ]]; then
  rm -f "$STATE/container" "$STATE/status" "$STATE/ports" "$STATE/image"; exit 0
fi
prefix=""; backend=""; viewer=""; vnc=""; adb=""; prev=""
for arg in "$@"; do
  case "$prev" in
    --name-prefix) prefix="$arg" ;;
    --backend-start-port) backend="$arg" ;;
    --viewer-start-port) viewer="$arg" ;;
    --vnc-start-port) vnc="$arg" ;;
    --adb-start-port) adb="$arg" ;;
  esac
  prev="$arg"
done
if [[ "${FAKE_KU_SHIFT:-0}" == 1 ]]; then
  backend=$((backend + 1)); viewer=$((viewer + 1)); vnc=$((vnc + 1)); adb=$((adb + 1))
fi
name="${prefix}_0"
echo "$name" >"$STATE/container"
echo running >"$STATE/status"
echo "${FAKE_KU_IMAGE:-ghcr.io/yaosqz/knowu-bench:latest}" >"$STATE/image"
{
  printf '6800/tcp -> 0.0.0.0:%s\n' "$backend"
  printf '7860/tcp -> 0.0.0.0:%s\n' "$viewer"
  printf '5800/tcp -> 0.0.0.0:%s\n' "$vnc"
  printf '5556/tcp -> 0.0.0.0:%s\n' "$adb"
} >"$STATE/ports"
echo "container $name ready"
exit 0
'''

SETSID_FAKE = r'''#!/usr/bin/env bash
set -u
echo "setsid $*" >>"${FAKE_SETSID_LOG:?}"
instance="${2:-}"; runtime=""; port=""; serial=""; snapshot="init_state"; container=""; prev=""
for arg in "$@"; do
  case "$prev" in
    --runtime) runtime="$arg" ;;
    --port) port="$arg" ;;
    --adb-serial) serial="$arg" ;;
    --snapshot) snapshot="$arg" ;;
    --ku-container) container="$arg" ;;
  esac
  prev="$arg"
done
python3 - "$runtime" "$instance" "$port" "$serial" "$snapshot" "$container" <<'PY'
import json, pathlib, secrets, sys
runtime, instance, port, serial, snapshot, container = sys.argv[1:]
path = pathlib.Path(runtime)
path.mkdir(parents=True, exist_ok=True)
(path / 'instance.json').write_text(json.dumps({
    'instance_id': instance, 'adb_serial': serial, 'avd': 'Pixel_8_API_34_x86_64', 'snapshot': snapshot,
    'ku_container': container, 'ku_image': '', 'ku_image_id': '', 'baseline_version': 'fake',
    'apk_versions': {}, 'dataset_version': 'fake', 'seed': 42,
    'simulated_time': '2026-01-01T00:00:00+08:00', 'controller_port': int(port),
    'lifecycle': 'READY', 'runner_key': secrets.token_urlsafe(32),
}, ensure_ascii=False, indent=2) + '\n')
PY
echo 'fake controller started'
exit 0
'''


def write_executable(path, body):
    path.write_text(body)
    path.chmod(0o755)


class SimulatorActivationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.logs = self.root / 'logs'
        self.bin = self.root / 'bin'
        for path in (self.state, self.logs, self.bin):
            path.mkdir(parents=True)
        (self.root / 'scripts/lib').mkdir(parents=True)
        (self.root / 'config').mkdir()
        shutil.copy(ROOT / 'scripts/lib/ku-runtime.sh', self.root / 'scripts/lib/ku-runtime.sh')
        shutil.copy(ROOT / 'config/ku-runtime.env', self.root / 'config/ku-runtime.env')
        for name in SCRIPTS:
            target = self.root / 'scripts' / name
            shutil.copy(ROOT / 'scripts' / name, target)
            target.chmod(0o755)
        for app in APPS:
            apk = self.root / f'dist/apks/{app}-debug.apk'
            apk.parent.mkdir(parents=True, exist_ok=True)
            apk.write_bytes(f'{app}-apk-bytes'.encode())
        self.ku = self.root / 'fake-ku'
        (self.ku / '.venv/bin').mkdir(parents=True)
        self.ku_env = self.root / 'fake-ku.env'
        self.ku_env.write_text('KU_PLACEHOLDER=1\n')
        write_executable(self.ku / '.venv/bin/mobile-world', KU_CLI_FAKE)
        write_executable(self.bin / 'docker', DOCKER_FAKE)
        write_executable(self.bin / 'adb', ADB_FAKE)
        write_executable(self.bin / 'curl', CURL_FAKE)
        write_executable(self.bin / 'setsid', SETSID_FAKE)

    def tearDown(self):
        self.tmp.cleanup()

    def env(self, **extra):
        values = {
            'PATH': f'{self.bin}:/usr/local/bin:/usr/bin:/bin',
            'HOME': str(self.root),
            'KU_REPO': str(self.ku),
            'KU_CLI': str(self.ku / '.venv/bin/mobile-world'),
            'KU_ENV_FILE': str(self.ku_env),
            'FAKE_DOCKER_LOG': str(self.logs / 'docker.log'),
            'FAKE_DOCKER_STATE': str(self.state),
            'FAKE_ADB_LOG': str(self.logs / 'adb.log'),
            'FAKE_CURL_LOG': str(self.logs / 'curl.log'),
            'FAKE_KU_LOG': str(self.logs / 'ku.log'),
            'FAKE_SETSID_LOG': str(self.logs / 'setsid.log'),
            'FAKE_APK_DIR': str(self.root / 'dist/apks'),
            'FAKE_KU_IMAGE': KU_IMAGE,
            'FAKE_ADB_SERIAL': '127.0.0.1:5556',
            'KU_ADB_READY_TIMEOUT_SECONDS': '6',
            'KU_NOVNC_READY_TIMEOUT_SECONDS': '2',
        }
        values.update(extra)
        return values

    def run_script(self, script, *args, **extra):
        return subprocess.run([str(self.root / 'scripts' / script), *args],
                              capture_output=True, text=True, env=self.env(**extra), timeout=90)

    def seed_container(self, *, status='running', image=KU_IMAGE, vnc=5800):
        (self.state / 'container').write_text(CONTAINER + '\n')
        (self.state / 'status').write_text(status + '\n')
        (self.state / 'image').write_text(image + '\n')
        (self.state / 'ports').write_text(
            f'6800/tcp -> 0.0.0.0:6800\n7860/tcp -> 0.0.0.0:7860\n'
            f'5800/tcp -> 0.0.0.0:{vnc}\n5556/tcp -> 0.0.0.0:5556\n')

    def seed_instance(self, port=39999):
        runtime = self.root / f'runtime/instances/{INSTANCE}'
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / 'instance.json').write_text(json.dumps({
            'instance_id': INSTANCE, 'adb_serial': '127.0.0.1:5556', 'avd': 'Pixel_8_API_34_x86_64',
            'snapshot': 'init_state', 'ku_container': CONTAINER, 'ku_image': KU_IMAGE,
            'ku_image_id': 'sha256:fakeimage', 'baseline_version': 'fake', 'apk_versions': {},
            'dataset_version': 'fake', 'seed': 42, 'simulated_time': '2026-01-01T00:00:00+08:00',
            'controller_port': port, 'lifecycle': 'READY', 'runner_key': 'k' * 40,
        }, ensure_ascii=False, indent=2) + '\n')

    def seed_novnc_metadata(self):
        runtime = self.root / f'runtime/instances/{INSTANCE}'
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / 'ku-environment.json').write_text(json.dumps({
            'container': CONTAINER, 'image': KU_IMAGE, 'image_id': 'sha256:fakeimage',
            'avd': 'Pixel_8_API_34_x86_64', 'snapshot': 'init_state',
            'backend_port': 6800, 'viewer_port': 7860, 'vnc_port': 5800, 'adb_port': 5556,
        }) + '\n')

    def test_start_activates_environment_when_no_device_is_online(self):
        result = self.run_script('start-simulator.sh', INSTANCE, '6800', '7860', '5800', '5556', '39999')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('vnc=http://127.0.0.1:5800/vnc.html', result.stdout)
        self.assertIn('controller=http://127.0.0.1:39999/console/', result.stdout)
        self.assertIn('ku_backend=http://127.0.0.1:6800', result.stdout)
        ku_log = (self.logs / 'ku.log').read_text()
        self.assertIn('env run', ku_log)
        self.assertIn('--vnc', ku_log)
        self.assertNotIn('docker start', (self.logs / 'docker.log').read_text())
        self.assertIn('connect 127.0.0.1:5556', (self.logs / 'adb.log').read_text())
        metadata = json.loads((self.root / f'runtime/instances/{INSTANCE}/ku-environment.json').read_text())
        self.assertEqual(metadata['vnc_port'], 5800)
        reset_log = (self.root / f'runtime/instances/{INSTANCE}/reset.log').read_text()
        self.assertIn('reset status=ok', reset_log)

    def test_existing_running_instance_is_reused_not_recreated(self):
        self.seed_container(status='running')
        self.seed_instance()
        result = self.run_script('start-simulator.sh', INSTANCE, '6800', '7860', '5800', '5556', '39999')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('vnc=http://127.0.0.1:5800/vnc.html', result.stdout)
        self.assertFalse((self.logs / 'ku.log').exists(), 'KU CLI must not run when the environment is reused')
        self.assertNotIn('docker start', (self.logs / 'docker.log').read_text())
        self.assertTrue((self.root / f'runtime/instances/{INSTANCE}/reset.log').exists())

    def test_existing_stopped_container_is_activated_through_ku_entrypoint(self):
        self.seed_container(status='exited')
        result = self.run_script('start-ku-environment.sh', INSTANCE, '6800', '7860', '5800', '5556')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('vnc=http://127.0.0.1:5800/vnc.html', result.stdout)
        self.assertIn('activating existing KU container', result.stderr)
        self.assertIn('docker start', (self.logs / 'docker.log').read_text())

    def test_container_start_failure_prints_no_url(self):
        self.seed_container(status='exited')
        result = self.run_script('start-ku-environment.sh', INSTANCE, '6800', '7860', '5800', '5556',
                                 FAKE_DOCKER_START_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('vnc.html', result.stdout)
        self.assertNotIn('vnc=', result.stdout)

    def test_existing_container_with_foreign_image_is_refused(self):
        self.seed_container(status='running', image='example.invalid/not-ku:latest')
        result = self.run_script('start-ku-environment.sh', INSTANCE, '6800', '7860', '5800', '5556')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('refusing to reuse it', result.stderr)
        self.assertNotIn('vnc=', result.stdout)

    def test_ku_cli_port_shift_fails_closed_and_removes_created_container(self):
        result = self.run_script('start-ku-environment.sh', INSTANCE, '6800', '7860', '5800', '5556',
                                 FAKE_KU_SHIFT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('instead of the requested', result.stderr)
        self.assertNotIn('vnc=', result.stdout)
        self.assertIn('env rm', (self.logs / 'ku.log').read_text())
        self.assertFalse((self.state / 'container').exists())

    def test_ku_cli_launch_failure_prints_no_url(self):
        result = self.run_script('start-ku-environment.sh', INSTANCE, '6800', '7860', '5800', '5556',
                                 FAKE_KU_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('vnc.html', result.stdout)
        self.assertNotIn('vnc=', result.stdout)

    def test_emulator_never_ready_prints_no_url(self):
        result = self.run_script('start-ku-environment.sh', INSTANCE, '6800', '7860', '5800', '5556',
                                 FAKE_ADB_STATE='offline', KU_ADB_READY_TIMEOUT_SECONDS='2')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('vnc.html', result.stdout + result.stderr)
        self.assertNotIn('vnc=', result.stdout)

    def test_novnc_timeout_prints_no_url(self):
        result = self.run_script('start-ku-environment.sh', INSTANCE, '6800', '7860', '5800', '5556',
                                 FAKE_VNC_OK='0', KU_NOVNC_READY_TIMEOUT_SECONDS='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('noVNC did not answer', result.stderr)
        self.assertNotIn('vnc=', result.stdout)

    def test_start_simulator_prints_no_url_when_novnc_is_unreachable(self):
        result = self.run_script('start-simulator.sh', INSTANCE, '6800', '7860', '5800', '5556', '39999',
                                 FAKE_VNC_OK='0', KU_NOVNC_READY_TIMEOUT_SECONDS='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('vnc.html', result.stdout)
        self.assertNotIn('controller=http', result.stdout)

    def test_start_novnc_prints_url_only_when_verified(self):
        self.seed_container(status='running')
        self.seed_novnc_metadata()
        ok = self.run_script('start-novnc.sh', INSTANCE)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn('http://127.0.0.1:5800/vnc.html', ok.stdout)
        down = self.run_script('start-novnc.sh', INSTANCE, FAKE_VNC_OK='0', KU_NOVNC_READY_TIMEOUT_SECONDS='1')
        self.assertNotEqual(down.returncode, 0)
        self.assertNotIn('vnc.html', down.stdout)

    def test_stop_scripts_never_claim_a_url(self):
        for script in ('stop-novnc.sh', 'stop-ku-environment.sh'):
            result = self.run_script(script, INSTANCE)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('http', result.stdout)

    def test_fail_closed_guards_stay_in_place(self):
        environment = (ROOT / 'scripts/start-ku-environment.sh').read_text()
        self.assertIn('ku_require_published_ports', environment)
        self.assertIn('wait_for_ku_novnc', environment)
        simulator = (ROOT / 'scripts/start-simulator.sh').read_text()
        self.assertIn('ensure-controller.sh', simulator)
        self.assertIn('wait_for_ku_novnc', simulator)
        self.assertIn('wait_for_ku_novnc', (ROOT / 'scripts/start-novnc.sh').read_text())
        library = (ROOT / 'scripts/lib/ku-runtime.sh').read_text()
        self.assertIn('http://127.0.0.1:$port/vnc.html', library)


if __name__ == '__main__':
    unittest.main()
