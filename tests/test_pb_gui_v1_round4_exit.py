import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / 'scripts' / 'pb_gui_v1_round4_exit.sh'
BASH = shutil.which('bash')
# V2 entry points must stay out of the round-4 exit (blueprint sections 1.2/5).
FORBIDDEN = ('--confirm-live', '--require-live', 'gui_publish', 'evaluate.py',
             'pb_gui_release_gate')
STEPS = ('python3 "$HOST" preflight', 'python3 "$HOST" replay', 'python3 "$HOST" writeback',
         'python3 "$EVIDENCE" build')


@unittest.skipUnless(BASH, 'bash is required to run the round-4 exit wrapper')
class Round4ExitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host_dir = self.root / 'host'

    def run_script(self, *args):
        return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True)

    def required(self, release):
        return ('--pb-root', str(self.root / 'pb'), '--episode', 'ep_0001',
                '--release-report', str(release), '--capture-dir', str(self.root / 'capture'),
                '--backend-receipt', str(self.root / 'backend.json'),
                '--host-dir', str(self.host_dir), '--bundle-dir', str(self.root / 'bundle'))

    def test_help_and_argument_errors_are_fail_closed(self):
        result = self.run_script('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        for step in ('preflight', 'replay', 'writeback', 'evidence build'):
            self.assertIn(step, result.stdout)
        result = self.run_script('--episode', 'ep_0001')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--pb-root is required', result.stderr)
        result = self.run_script('--bogus')
        self.assertEqual(result.returncode, 2)
        self.assertIn('unknown argument --bogus', result.stderr)
        release = self.root / 'release.json'
        release.write_text('{}', encoding='utf8')
        result = self.run_script(*self.required(release), '--episode', '../evil')
        self.assertEqual(result.returncode, 2)
        self.assertIn('single safe name', result.stderr)

    def test_missing_release_report_stops_before_the_first_step(self):
        result = self.run_script(*self.required(self.root / 'absent.json'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('release report not found', result.stderr)
        self.assertNotIn('[1/4]', result.stderr)
        self.assertFalse(self.host_dir.exists())

    def test_a_failing_first_step_leaves_no_later_artifact(self):
        release = self.root / 'release.json'
        release.write_text(json.dumps({'episode_scope': {}}), encoding='utf8')
        result = self.run_script(*self.required(release))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('[1/4]', result.stderr)
        self.assertNotIn('[2/4]', result.stderr)
        self.assertFalse((self.host_dir / 'host-receipt.json').exists())
        self.assertFalse((self.host_dir / 'replay-receipt.json').exists())
        self.assertFalse((self.root / 'bundle').exists())

    def test_the_wrapper_only_wires_the_four_v1_steps_in_order(self):
        text = SCRIPT.read_text(encoding='utf8')
        positions = [text.index(step) for step in STEPS]
        self.assertEqual(positions, sorted(positions))
        # V2 entry points may be named in the header comment as exclusions, but
        # must never appear in an executed line.
        commands = '\n'.join(line for line in text.splitlines()
                             if not line.lstrip().startswith('#'))
        for needle in FORBIDDEN:
            self.assertNotIn(needle, commands)


class Round4ExitStaticDocsTest(unittest.TestCase):
    """The single V1 entry points are documented where a reviewer starts."""

    def test_readme_points_at_the_checklist_and_the_round4_exit(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf8')
        self.assertIn('docs/v1-acceptance-evidence-20260926/V1-ACCEPTANCE.md', readme)
        self.assertIn('scripts/pb_gui_v1_round4_exit.sh', readme)
        for path in ('scripts/pb_gui_v1_round4_exit.sh',
                     'docs/v1-acceptance-evidence-20260926/V1-ACCEPTANCE.md',
                     'docs/v1-acceptance-evidence-20260926/README.md'):
            self.assertTrue((ROOT / path).is_file(), path)


if __name__ == '__main__':
    unittest.main()
