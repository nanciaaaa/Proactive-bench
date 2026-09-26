import copy
import hashlib
import json
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from gui_machine_qa import OCR_MODELS, RULE
from gui_pair_qa import (audit_pair, audit_release_pair, differs_outside, intersect_rectangles,
                         rgb_rows, semantic_rectangles)

RUNTIME = ROOT / 'runtime'
CONTACT_RELEASES = (RUNTIME / 'gui-contact-update-formal-release-a-20260925-v1',
                    RUNTIME / 'gui-contact-update-formal-release-b-20260925-v1')
CONTACT_CAPTURES = (RUNTIME / 'gui-contact-update-formal-machine-a-20260925-v3',
                    RUNTIME / 'gui-contact-update-formal-machine-b-20260925-v3')


def write_rgb_png(width, rows):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

    raw = b''.join(b'\x00' + bytes(row) for row in rows)
    header = struct.pack('>IIBBBBB', width, len(rows), 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def resign_png(tmp, release_name, capture_name, mutate_rows):
    """Copy one side and re-sign every hash after mutating its released PNG."""
    release = Path(tmp) / release_name
    private = Path(tmp) / (release_name + '.private')
    capture = Path(tmp) / capture_name
    shutil.copytree(RUNTIME / release_name, release)
    shutil.copytree(RUNTIME / (release_name + '.private'), private)
    shutil.copytree(RUNTIME / capture_name, capture)
    frame = json.loads((release / 'manifest.json').read_text())['packets'][0]['frames'][0]
    png_path = release / frame['screenshot_ref']
    width, height, rows = rgb_rows(png_path.read_bytes())
    png = write_rgb_png(width, mutate_rows(rows))
    png_path.write_bytes(png)
    (capture / frame['screenshot_ref']).write_bytes(png)
    sha256 = hashlib.sha256(png).hexdigest()
    manifest = json.loads((release / 'manifest.json').read_text())
    manifest['packets'][0]['frames'][0]['sha256'] = sha256
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    (release / 'manifest.json').write_bytes(manifest_raw)
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    (release / 'manifest.sha256').write_text(manifest_sha256 + '\n')
    receipt = json.loads((capture / 'receipt.json').read_text())
    receipt['frames'][0]['sha256'] = sha256
    receipt_raw = (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode()
    (capture / 'receipt.json').write_bytes(receipt_raw)
    (private / 'receipt.json').write_bytes(receipt_raw)
    machine = json.loads((capture / 'qa-machine.json').read_text())
    machine['frames'][0]['sha256'] = sha256
    machine_raw = (json.dumps(machine, ensure_ascii=False, indent=2) + '\n').encode()
    (capture / 'qa-machine.json').write_bytes(machine_raw)
    (private / 'qa-machine.json').write_bytes(machine_raw)
    qa = json.loads((private / 'qa.json').read_text())
    qa['public_manifest_sha256'] = manifest_sha256
    qa['receipt_sha256'] = hashlib.sha256(receipt_raw).hexdigest()
    qa['machine_qa_sha256'] = hashlib.sha256(machine_raw).hexdigest()
    qa['machine_frames'][0]['sha256'] = sha256
    (private / 'qa.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2) + '\n')
    return release, capture


def private_plan(release):
    return json.loads((release.with_name(release.name + '.private') / 'private-plan.json').read_text())


def machine_qa_for(directory):
    qa = json.loads((Path(directory) / 'qa-machine.json').read_text())
    qa['rule_version'] = RULE
    qa.setdefault('ocr_models', OCR_MODELS)
    for frame in qa['frames']:
        frame.setdefault('language', 'eng')
        frame.setdefault('ocr_model_sha256', OCR_MODELS['eng'])
        for field in frame['fields']:
            field.setdefault('scale', 1)
    return qa


def edit_searchbar(tmp, capture_name, text=None, bounds=None):
    capture = Path(tmp) / capture_name
    shutil.copytree(RUNTIME / capture_name, capture)
    ui = next((capture / 'ui').glob('*.xml'))
    tree = ElementTree.parse(ui)
    node = next(node for node in tree.getroot().iter('node')
                if node.attrib.get('resource-id') == 'com.google.android.apps.messaging:id/searchbar')
    if text is not None:
        node.set('text', text)
        for descendant in node.iter('node'):
            if descendant.attrib.get('resource-id') == 'com.google.android.apps.messaging:id/open_search_bar_text_view':
                descendant.set('text', text)
    if bounds is not None:
        node.set('bounds', bounds)
    raw = ElementTree.tostring(tree.getroot(), encoding='utf-8', xml_declaration=True)
    ui.write_bytes(raw)
    receipt_path = capture / 'receipt.json'
    receipt = json.loads(receipt_path.read_text())
    receipt['frames'][0]['ui_dump_sha256'] = hashlib.sha256(raw).hexdigest()
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    return capture


class GuiTrackPairTest(unittest.TestCase):
    def test_volatile_parent_does_not_hide_child_text(self):
        def nodes(text):
            return ElementTree.fromstring(
                f'<hierarchy><node resource-id="com.google.android.apps.messaging:id/searchbar" '
                f'bounds="[42,153][1038,279]"><node text="{text}"/></node></hierarchy>')

        left = semantic_rectangles(nodes('Search places'))
        right = semantic_rectangles(nodes('Search conversations'))
        self.assertNotEqual(left[0][1], right[0][1])

    def test_published_files_and_appointment_have_independent_gui_class(self):
        for prefix in ('gui-files', 'gui-appointment'):
            with self.subTest(prefix=prefix):
                report = audit_release_pair(RUNTIME / f'{prefix}-release-a-v2',
                                            RUNTIME / f'{prefix}-release-b-v2')
                self.assertEqual(report['status'], 'pass')
                self.assertEqual(report['gui_track_passive_class'], 'active_required')
                self.assertTrue(all(not item['app_changed_rows'] for item in report['pairs']))

    def test_contact_pixels_are_not_silently_accepted(self):
        report = audit_release_pair(*CONTACT_RELEASES)
        self.assertEqual(report['rule_version'], 'gui-release-pixels-v1')
        self.assertEqual(report['status'], 'reject')
        self.assertEqual(report['gui_track_passive_class'], 'unverified')
        self.assertEqual(report['pairs'][0]['app_changed_rows'], list(range(199, 240)))

    def test_contact_volatile_rows_need_explicitly_bound_captures(self):
        report = audit_release_pair(*CONTACT_RELEASES, left_capture=CONTACT_CAPTURES[0],
                                    right_capture=CONTACT_CAPTURES[1])
        self.assertEqual(report['rule_version'], 'gui-release-pixels-v2')
        self.assertEqual(report['status'], 'reject')
        self.assertEqual(report['gui_track_passive_class'], 'unverified')
        pair = report['pairs'][0]
        self.assertEqual(pair['volatile_intersection'], [])
        self.assertEqual(pair['exempted_rows'], [])
        self.assertEqual(pair['app_changed_rows'], list(range(199, 240)))

    def test_contact_audit_pair_rejects_semantically_different_searchbar(self):
        with mock.patch('gui_pair_qa.audit_capture', side_effect=machine_qa_for):
            report = audit_pair(*CONTACT_CAPTURES)
        self.assertEqual(report['rule_version'], 'app-pixels-1080x2400-v2')
        self.assertEqual(report['status'], 'reject')
        pair = report['pairs'][0]
        self.assertEqual(pair['volatile_intersection'], [])
        self.assertEqual(pair['exempted_rows'], [])
        self.assertEqual(pair['app_changed_rows'], list(range(199, 240)))
        self.assertEqual(pair['comparable_app_changed_rows'], list(range(199, 240)))

    def test_audit_pair_exempts_semantically_identical_searchbar_intersection(self):
        with tempfile.TemporaryDirectory() as tmp:
            left = Path(tmp) / CONTACT_CAPTURES[0].name
            shutil.copytree(CONTACT_CAPTURES[0], left)
            right = edit_searchbar(tmp, CONTACT_CAPTURES[1].name, text='Search places')
            with mock.patch('gui_pair_qa.audit_capture', side_effect=machine_qa_for):
                report = audit_pair(left, right)
        self.assertEqual(report['status'], 'pass')
        pair = report['pairs'][0]
        self.assertEqual(pair['volatile_intersection'], [[42, 153, 1038, 279]])
        self.assertEqual(pair['exempted_rows'], list(range(199, 240)))
        self.assertEqual(pair['comparable_app_changed_rows'], [])

    def test_audit_pair_does_not_exempt_one_sided_volatile_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            left = Path(tmp) / CONTACT_CAPTURES[0].name
            shutil.copytree(CONTACT_CAPTURES[0], left)
            right = edit_searchbar(tmp, CONTACT_CAPTURES[1].name, text='Search places',
                                   bounds='[42,300][1038,426]')
            with mock.patch('gui_pair_qa.audit_capture', side_effect=machine_qa_for):
                report = audit_pair(left, right)
        self.assertEqual(report['status'], 'reject')
        pair = report['pairs'][0]
        self.assertEqual(pair['volatile_intersection'], [])
        self.assertEqual(pair['exempted_rows'], [])
        self.assertEqual(pair['comparable_app_changed_rows'], list(range(199, 240)))

    def test_volatile_exemption_uses_intersection_not_union_or_whole_rows(self):
        right = bytes(30)

        def row(pixels):
            return b''.join(bytes((1 if x in pixels else 0, 0, 0)) for x in range(10))

        disjoint = intersect_rectangles([(0, 0, 5, 10)], [(5, 0, 10, 10)])
        self.assertEqual(disjoint, [])
        self.assertTrue(differs_outside(row({1}), right, 0, 10, disjoint))
        both = intersect_rectangles([(0, 0, 5, 10)], [(0, 0, 5, 10)])
        self.assertFalse(differs_outside(row({1}), right, 0, 10, both))
        self.assertTrue(differs_outside(row({1, 7}), right, 0, 10, both))

    def test_tampered_capture_ui_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / CONTACT_CAPTURES[1].name
            shutil.copytree(CONTACT_CAPTURES[1], capture)
            ui = next((capture / 'ui').glob('*.xml'))
            ui.write_bytes(ui.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'UI snapshot hash mismatch'):
                audit_release_pair(*CONTACT_RELEASES, left_capture=CONTACT_CAPTURES[0],
                                   right_capture=capture)

    def test_misbound_capture_directories_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'receipt differs'):
            audit_release_pair(*CONTACT_RELEASES, left_capture=CONTACT_CAPTURES[1],
                               right_capture=CONTACT_CAPTURES[1])
        with self.assertRaisesRegex(ValueError, 'both capture directories'):
            audit_release_pair(*CONTACT_RELEASES, left_capture=CONTACT_CAPTURES[0])

    def test_pixels_outside_trusted_intersection_still_reject(self):
        frame = json.loads((CONTACT_RELEASES[0] / 'manifest.json').read_text())['packets'][0]['frames'][0]
        _, _, left_rows = rgb_rows((CONTACT_RELEASES[0] / frame['screenshot_ref']).read_bytes())

        def mutate(rows):
            rows = [bytearray(row) for row in rows]
            offset = 10 * 3
            original, other = bytes(rows[300][offset:offset + 3]), left_rows[300][offset:offset + 3]
            replacement = next(candidate for candidate in (b'\x00\x00\x00', b'\xff\xff\xff', b'\x01\x02\x03')
                               if candidate not in (original, other))
            rows[300][offset:offset + 3] = replacement
            return [bytes(row) for row in rows]

        with tempfile.TemporaryDirectory() as tmp:
            release, capture = resign_png(tmp, CONTACT_RELEASES[1].name, CONTACT_CAPTURES[1].name, mutate)
            report = audit_release_pair(CONTACT_RELEASES[0], release,
                                        left_capture=CONTACT_CAPTURES[0], right_capture=capture)
            self.assertEqual(report['status'], 'reject')
            self.assertIn(300, report['pairs'][0]['app_changed_rows'])
            self.assertNotIn(300, report['pairs'][0]['exempted_rows'])

    def test_opposite_need_sets_are_required(self):
        source = RUNTIME / 'gui-files-release-a-v2'
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'copy'
            private = target.with_name(target.name + '.private')
            target.mkdir()
            private.mkdir()
            (target / 'manifest.json').write_bytes((source / 'manifest.json').read_bytes())
            (private / 'private-plan.json').write_bytes(
                (source.with_name(source.name + '.private') / 'private-plan.json').read_bytes())
            with mock.patch('gui_pair_qa.verify_release'):
                with self.assertRaisesRegex(ValueError, 'opposite'):
                    audit_release_pair(source, target)

    def test_forged_or_non_opposite_need_sets_are_rejected(self):
        plan_a = private_plan(RUNTIME / 'gui-files-release-a-v2')
        plan_b = private_plan(RUNTIME / 'gui-files-release-b-v2')
        restore = {'type': 'restore_file_content', 'target': 'file-01', 'content': 'GuiPilotContentV1'}
        update = {'type': 'update_calendar_event', 'target': 'meeting-01'}
        cases = (
            ('same-world', plan_a, plan_a, [], [restore]),
            ('both-required', plan_a, plan_b, [restore], [update]),
            ('same-required', plan_a, plan_b, [restore], [copy.deepcopy(restore)]),
            ('empty-variant', plan_a, plan_b, [], [restore]),
        )
        for label, left_plan, right_plan, left_need, right_need in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                releases = []
                for name, plan, need in (('a', left_plan, left_need), ('b', right_plan, right_need)):
                    release = Path(tmp) / f'release-{name}'
                    release.mkdir()
                    private = release.with_name(release.name + '.private')
                    private.mkdir()
                    forged = copy.deepcopy(plan)
                    forged['formal']['oracle']['expected_need_set'] = need
                    if label == 'empty-variant' and name == 'b':
                        forged['formal']['variant_id'] = ''
                    (private / 'private-plan.json').write_text(json.dumps(forged, ensure_ascii=False))
                    releases.append(release)
                with mock.patch('gui_pair_qa.verify_release'):
                    with self.assertRaisesRegex(ValueError, 'opposite'):
                        audit_release_pair(releases[0], releases[1])

    def test_gui_class_does_not_inherit_text_oracle_class(self):
        source_a = RUNTIME / 'gui-files-release-a-v2'
        source_b = RUNTIME / 'gui-files-release-b-v2'
        with tempfile.TemporaryDirectory() as tmp:
            changed = Path(tmp) / 'gui-files-release-b-v2'
            shutil.copytree(source_b, changed)
            private = changed.with_name(changed.name + '.private')
            shutil.copytree(source_b.with_name(source_b.name + '.private'), private)
            plan_path = private / 'private-plan.json'
            plan = json.loads(plan_path.read_text())
            plan['formal']['oracle']['passive_class'] = 'passive_sufficient'
            plan_path.write_text(json.dumps(plan))
            with mock.patch('gui_pair_qa.verify_release'):
                self.assertEqual(audit_release_pair(source_a, changed)['gui_track_passive_class'],
                                 'active_required')

    def test_public_rule_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            releases = []
            for name in ('a', 'b'):
                release = Path(tmp) / f'gui-files-release-{name}-v2'
                shutil.copytree(RUNTIME / release.name, release)
                shutil.copytree(RUNTIME / f'{release.name}.private',
                                release.with_name(release.name + '.private'))
                releases.append(release)
            manifest_path = releases[0] / 'manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['public_rule'] += ' tampered'
            raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
            manifest_path.write_bytes(raw)
            manifest_hash = hashlib.sha256(raw).hexdigest()
            (releases[0] / 'manifest.sha256').write_text(manifest_hash + '\n')
            qa_path = releases[0].with_name(releases[0].name + '.private') / 'qa.json'
            qa = json.loads(qa_path.read_text())
            qa['public_manifest_sha256'] = manifest_hash
            qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + '\n')
            with self.assertRaisesRegex(ValueError, 'public rules differ'):
                audit_release_pair(releases[0], releases[1])

    def test_rejected_machine_qa_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp) / 'gui-files-release-a-v2'
            shutil.copytree(RUNTIME / release.name, release)
            shutil.copytree(RUNTIME / f'{release.name}.private', release.with_name(release.name + '.private'))
            private = release.with_name(release.name + '.private')
            machine_path = private / 'qa-machine.json'
            machine = json.loads(machine_path.read_text())
            machine['status'] = 'reject'
            machine_path.write_text(json.dumps(machine, ensure_ascii=False, indent=2) + '\n')
            qa_path = private / 'qa.json'
            qa = json.loads(qa_path.read_text())
            qa['machine_qa_sha256'] = hashlib.sha256(machine_path.read_bytes()).hexdigest()
            qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + '\n')
            with self.assertRaisesRegex(ValueError, 'machine QA rule or model mismatch'):
                audit_release_pair(release, RUNTIME / 'gui-files-release-b-v2')

    def test_v6_capture_dirs_are_not_resigned_under_v7(self):
        with self.assertRaisesRegex(ValueError, 'accepted machine frames'):
            audit_pair(RUNTIME / 'gui-files-machine-a-20260925-v2',
                       RUNTIME / 'gui-files-machine-b-20260925-v2')


if __name__ == '__main__':
    unittest.main()
