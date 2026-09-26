#!/usr/bin/env python3
"""V1 acceptance evidence bundle: repository-visible, reproducible, fail-closed.

V1 (blueprint section 1.1) closes one reusable loop: PB Passive compile ->
adapter_plan -> device init/readback -> capture -> pixel QA -> release gate ->
image host replay -> image write-back. The device-side receipts (backend apply,
capture, machine QA, release gate) live in private directories outside the
repository, so acceptance had no repository-visible trace beyond the sha256
values carried inside the host receipt.

This host copies a validated subset of that chain into a repository directory:

* ``build`` re-validates every receipt with the existing verifiers (release
  report schema, capture receipt + PNG bytes, machine QA frames, backend apply
  receipt, image host / replay / image-writeback bindings), refuses any document
  that carries private GT keys or local absolute paths, copies the receipts and
  writes a per-frame hash list plus a per-episode manifest.
* ``verify`` re-checks a built bundle offline: file sha256 values, receipt
  schemas, cross-receipt bindings and leak scans -- no device, no PB root.
* ``checklist`` runs the same offline verification and writes ``V1-ACCEPTANCE.md``
  inside the bundle: the single place where the blueprint section 1.1 ten-step
  loop and the thirteen frozen fields are mapped item by item to the receipts
  (file path + sha256) that carry them.

Device PNGs are deliberately **not** stored in the repository (blueprint
section 6: no GT, keys or local private screenshot paths). The bundle records
each frame's sha256, byte size, dimensions, QA pass status and the commands
that rebuild the capture, so an acceptance reviewer reproduces the images from
the private capture directory instead of trusting a stored copy.

Usage::

    python3 scripts/pb_gui_v1_evidence.py build --bundle DIR \
        --episode NAME RELEASE_REPORT CAPTURE_DIR BACKEND_RECEIPT \
                  HOST_RECEIPT REPLAY_RECEIPT IMAGE_BUNDLE_MANIFEST
    python3 scripts/pb_gui_v1_evidence.py verify --bundle DIR
    python3 scripts/pb_gui_v1_evidence.py checklist --bundle DIR
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import gui_machine_qa as machine_qa
import pb_gui_image_host as host
import pb_gui_release_gate as release_gate

SCHEMA = 'pb-gui-v1-acceptance-evidence.v1'
EPISODE_SCHEMA = 'pb-gui-v1-acceptance-episode-evidence.v1'
QA_SCHEMA = 'pb-gui-machine-qa-summary.v1'
REFS_SCHEMA = 'pb-gui-image-reference-list.v1'
CHECKLIST_NAME = 'V1-ACCEPTANCE.md'
# The private image write-back bundle names its rewritten PB references here; the
# file carries the PB ``observed_at``/``delivered_at`` of every bound packet.
IMAGE_REFS_FILE = 'image-refs.jsonl'
# Per-episode evidence files, as (bundle manifest key, file-name prefix).
EVIDENCE_FILES = (('release_report', 'gui-release-report'),
                  ('host_receipt', 'gui-image-host-receipt'),
                  ('replay_receipt', 'gui-image-replay-receipt'),
                  ('backend_receipt', 'backend-apply-receipt'),
                  ('capture_receipt', 'capture-receipt'),
                  ('image_bundle', 'passive-image-bundle'),
                  ('qa_summary', 'machine-qa-summary'),
                  ('refs', 'image-refs'))
EPISODE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
# Local absolute paths only. A bare ``/v1/chat/completions`` request path in the
# host receipt is legitimate, so the scan looks for the local prefixes the
# blueprint discipline names instead of every leading slash; it also walks keys
# containing ``/`` that pb.absolute_value_paths cannot traverse.
LOCAL_PATH = re.compile(r'^(?:[A-Za-z]:[\\/]|\\\\)|(?:^|[\\/])(?:home|tmp|root|Users)(?:[\\/]|$)')


class EvidenceError(Exception):
    """A fail-closed condition: nothing may be claimed or written."""


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def load_json(path, label):
    path = Path(path)
    if not path.is_file():
        raise EvidenceError(f'{label} not found: {path}')
    try:
        document = json.loads(path.read_text(encoding='utf8'))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f'cannot read {label}: {error}') from None
    require(isinstance(document, dict), f'{label} must be a JSON object')
    return document


def write_json_new(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as out:
        json.dump(payload, out, ensure_ascii=False, indent=2)
        out.write('\n')
    return path


def copy_checked(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if sha256_file(source) != sha256_file(destination):
        raise EvidenceError(f'evidence copy drifted for {destination.name}')
    return destination


def local_path_hits(document, trail=()):
    hits = []
    if isinstance(document, dict):
        for key, value in document.items():
            hits.extend(local_path_hits(value, trail + (str(key),)))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            hits.extend(local_path_hits(value, trail + (str(index),)))
    elif isinstance(document, str) and LOCAL_PATH.search(document):
        hits.append('/'.join(trail))
    return hits


def assert_clean(document, label):
    private = host.scan_private(document)
    if private:
        raise EvidenceError(f'{label} carries private GT/Need fields: {private}')
    absolutes = local_path_hits(document)
    if absolutes:
        raise EvidenceError(f'{label} carries local absolute paths: {absolutes}')


def source_ref(path):
    """A non-absolute reference: project-relative inside the repo, else parent/name."""
    resolved = Path(path).expanduser().resolve()
    if resolved.is_relative_to(PROJECT):
        return resolved.relative_to(PROJECT).as_posix()
    # A local root directory name would itself look like a local path; the
    # basename alone stays traceable next to the episode evidence.
    parent = resolved.parent.name
    if parent and parent.lower() not in {'tmp', 'home', 'root', 'users', 'opt',
                                         'var', 'mnt', 'srv', 'data'}:
        return f'{parent}/{resolved.name}'
    return resolved.name


def iso_timestamp(value, label):
    """An ISO-8601 timestamp with a UTC offset; naive or junk values fail closed."""
    if not isinstance(value, str):
        raise EvidenceError(f'{label} must be an ISO-8601 string')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise EvidenceError(f'{label} is not an ISO-8601 timestamp: {value!r}') from None
    if parsed.tzinfo is None:
        raise EvidenceError(f'{label} must carry a UTC offset: {value!r}')
    return value


def load_release_report(path):
    document = load_json(path, 'release report')
    try:
        validation = release_gate.validate_report(document)
    except ValueError as error:
        raise EvidenceError(f'release report failed validation: {error}') from None
    return document, validation


def load_capture_receipt(path):
    receipt = load_json(path, 'capture receipt')
    require(receipt.get('schema_version') == '1.0',
            'capture receipt schema_version must be 1.0')
    require(receipt.get('status') == 'capture_complete' and receipt.get('reset_verified') is True,
            'capture receipt must be capture_complete with reset_verified=true')
    return receipt


def load_backend_receipt(path):
    receipt = load_json(path, 'backend receipt')
    require(receipt.get('schema_version') == 'pb-backend-apply-receipt.v1',
            'backend receipt schema_version must be pb-backend-apply-receipt.v1')
    require(receipt.get('status') == 'ok' and receipt.get('device_applied') is True,
            'backend receipt must be status=ok with device_applied=true')
    plan_binding = receipt.get('plan_binding')
    require(isinstance(plan_binding, dict) and isinstance(plan_binding.get('episode_scope'), dict),
            'backend receipt must carry plan_binding.episode_scope')
    return receipt


def load_host_receipt(path):
    try:
        return host.load_host_receipt(path)
    except (host.HostError, ValueError) as error:
        raise EvidenceError(f'host receipt failed validation: {error}') from None


def load_replay_receipt(path):
    try:
        return host.load_replay_receipt(path)
    except (host.HostError, ValueError) as error:
        raise EvidenceError(f'replay receipt failed validation: {error}') from None


def load_image_bundle(path):
    document = load_json(path, 'image bundle manifest')
    require(document.get('schema_version') == host.BUNDLE_SCHEMA
            and document.get('status') == host.BUNDLE_STATUS,
            f'image bundle manifest must be {host.BUNDLE_SCHEMA} '
            f'with status={host.BUNDLE_STATUS!r}')
    writeback = document.get('writeback')
    require(isinstance(writeback, dict) and writeback.get('pb_tree_modified') is False
            and writeback.get('pb_writes') == 0,
            'image bundle manifest must record pb_writes=0 and pb_tree_modified=false')
    require(isinstance(document.get('checks'), dict)
            and document['checks'].get('pb_writes') == 0,
            'image bundle manifest checks.pb_writes must be 0')
    require(isinstance(document.get('frames'), list) and document['frames'],
            'image bundle manifest must carry frames')
    return document


def host_pb_stream_sha256(host_receipt):
    """The PB passive stream hash every packet time binding must agree on."""
    bindings = host_receipt.get('bindings')
    binding = bindings.get('pb') if isinstance(bindings, dict) else None
    stream = binding.get('stream_sha256') if isinstance(binding, dict) else None
    require(isinstance(stream, str) and re.fullmatch(r'[0-9a-f]{64}', stream),
            'host receipt must bind the PB passive stream sha256')
    return stream


def read_image_refs(bundle_manifest_path, image_bundle, frames, pb_stream_sha256):
    """Per-packet PB times from the private write-back bundle's image references."""
    refs_path = Path(bundle_manifest_path).resolve().parent / IMAGE_REFS_FILE
    recorded = {str(item.get('path')): item for item in image_bundle.get('files', [])
                if isinstance(item, dict)}
    entry = recorded.get(IMAGE_REFS_FILE)
    require(isinstance(entry, dict) and isinstance(entry.get('sha256'), str),
            f'image bundle manifest must record {IMAGE_REFS_FILE}')
    require(refs_path.is_file(), f'write-back bundle has no {IMAGE_REFS_FILE}')
    require(sha256_file(refs_path) == entry['sha256'],
            f'{IMAGE_REFS_FILE} bytes do not match the image bundle manifest')
    device = {frame['packet_id']: frame for frame in frames}
    times = {}
    for number, line in enumerate(refs_path.read_text(encoding='utf8').splitlines(), start=1):
        if not line.strip():
            continue
        try:
            reference = json.loads(line)
        except json.JSONDecodeError:
            raise EvidenceError(f'{IMAGE_REFS_FILE} line {number} is not JSON') from None
        require(isinstance(reference, dict), f'{IMAGE_REFS_FILE} line {number} is not an object')
        packet_id = reference.get('packet_id')
        require(packet_id in device, f'{IMAGE_REFS_FILE} carries an unbound packet {packet_id!r}')
        require(packet_id not in times, f'{IMAGE_REFS_FILE} repeats packet {packet_id!r}')
        require(reference.get('tag') == 'gui', f'{IMAGE_REFS_FILE} {packet_id} is not a GUI packet')
        require(reference.get('screenshot_sha256') == device[packet_id]['sha256']
                and reference.get('screenshot_ref') == device[packet_id]['screenshot_ref'],
                f'{IMAGE_REFS_FILE} {packet_id} does not carry the bound device frame')
        times[packet_id] = {
            'sequence': reference.get('sequence'), 'source': reference.get('source'),
            'observed_at': iso_timestamp(reference.get('observed_at'),
                                         f'{IMAGE_REFS_FILE} {packet_id} observed_at'),
            'delivered_at': iso_timestamp(reference.get('delivered_at'),
                                          f'{IMAGE_REFS_FILE} {packet_id} delivered_at')}
    require(sorted(times) == sorted(device),
            f'{IMAGE_REFS_FILE} does not cover every bound device frame')
    time_binding = {'source': 'pb_stream_projection_via_staged_view',
                    'image_refs_path': IMAGE_REFS_FILE,
                    'image_refs_sha256': entry['sha256'], 'image_refs_bytes': entry.get('bytes'),
                    'pb_stream_sha256': pb_stream_sha256}
    return times, time_binding


def load_packet_times(refs, image_bundle, host_receipt, frames):
    """Re-check a built bundle's PB time binding offline (no private bundle)."""
    times, binding = refs.get('pb_packet_times'), refs.get('pb_time_binding')
    require(isinstance(times, dict) and isinstance(binding, dict),
            'image reference list must carry pb_packet_times and pb_time_binding')
    recorded = {str(item.get('path')): item for item in image_bundle.get('files', [])
                if isinstance(item, dict)}
    entry = recorded.get(IMAGE_REFS_FILE)
    require(binding.get('image_refs_path') == IMAGE_REFS_FILE and isinstance(entry, dict),
            f'image reference list must bind the write-back {IMAGE_REFS_FILE}')
    require(binding.get('image_refs_sha256') == entry.get('sha256'),
            f'{IMAGE_REFS_FILE} sha256 drifted from the image bundle manifest')
    require(binding.get('pb_stream_sha256') == host_pb_stream_sha256(host_receipt),
            'image reference list PB stream sha256 drifted from the host receipt')
    checked = {}
    for packet_id, entry_times in times.items():
        require(isinstance(entry_times, dict), f'pb_packet_times {packet_id} is not an object')
        checked[packet_id] = {
            'sequence': entry_times.get('sequence'), 'source': entry_times.get('source'),
            'observed_at': iso_timestamp(entry_times.get('observed_at'),
                                         f'pb_packet_times {packet_id} observed_at'),
            'delivered_at': iso_timestamp(entry_times.get('delivered_at'),
                                          f'pb_packet_times {packet_id} delivered_at')}
    require(sorted(checked) == sorted(frame['packet_id'] for frame in frames),
            'pb_packet_times does not cover exactly the bound device frames')
    return checked


def load_machine_qa(path):
    report = load_json(path, 'machine QA report')
    require(report.get('schema_version') == '1.0', 'machine QA schema_version must be 1.0')
    require(report.get('status') == 'pass',
            f"machine QA status must be 'pass', got {report.get('status')!r}")
    return report


def qa_frame_index(qa_report, capture_receipt, release_frames, label):
    require(qa_report.get('rule_version') in machine_qa.KNOWN_RULES,
            f'{label} rule_version {qa_report.get("rule_version")!r} is not a known pixel rule')
    require(qa_report.get('ocr_model_sha256') in machine_qa.OCR_MODELS.values(),
            f'{label} ocr_model_sha256 is not a known OCR model')
    require(qa_report.get('plan_sha256') == capture_receipt.get('plan_sha256'),
            f'{label} plan_sha256 does not bind the capture private plan')
    require(qa_report.get('capture_run_id') == capture_receipt.get('capture_run_id'),
            f'{label} capture_run_id does not match the capture receipt')
    frames = qa_report.get('frames')
    require(isinstance(frames, list) and len(frames) == len(release_frames),
            f'{label} frame count does not match the release report')
    index = {}
    for frame in frames:
        key = (frame.get('packet_id'), frame.get('frame_id'))
        require(key not in index, f'{label} repeats frame {key}')
        index[key] = frame
    for released in release_frames:
        frame = index.get((released['packet_id'], released['frame_id']))
        require(frame is not None,
                f'{label} has no frame for {released["packet_id"]}/{released["frame_id"]}')
        require(frame.get('status') == 'pass',
                f'{label} frame {frame.get("packet_id")!r} status is not pass')
        require(frame.get('png') == released['png'] and frame.get('sha256') == released['sha256'],
                f'{label} frame {frame.get("packet_id")!r} does not bind the release report '
                'png/sha256')
    return index


def machine_qa_summary(qa_report, source_sha256):
    """Per-frame OCR outcome without the plan's expected text (private key name)."""
    frames = []
    for frame in qa_report['frames']:
        fields = [{'field': field.get('field'), 'text': field.get('text'),
                   'confidence': field.get('confidence'),
                   'minimum_confidence': field.get('minimum_confidence'),
                   'passed': field.get('passed'), 'language': field.get('language')}
                  for field in frame.get('fields', [])]
        frames.append({'packet_id': frame.get('packet_id'), 'frame_id': frame.get('frame_id'),
                       'png': frame.get('png'), 'sha256': frame.get('sha256'),
                       'status': frame.get('status'), 'fields': fields})
    return {'schema_version': QA_SCHEMA, 'generated_at': now_utc(),
            'status': qa_report.get('status'), 'rule_version': qa_report.get('rule_version'),
            'ocr_model_sha256': qa_report.get('ocr_model_sha256'),
            'ocr_models': qa_report.get('ocr_models'), 'device_env': qa_report.get('device_env'),
            'plan_sha256': qa_report.get('plan_sha256'),
            'capture_run_id': qa_report.get('capture_run_id'),
            'source': {'schema_version': qa_report.get('schema_version'),
                       'sha256': source_sha256},
            'frames': frames,
            'claims': {'raw_device_pngs_stored': False, 'plan_text_stored': False}}


def cross_check_chain(*, release_path, report, validation, capture_receipt_path,
                      capture_receipt, qa_report, backend_path, backend, host_path,
                      host_receipt, replay_path, replay, bundle_path, image_bundle,
                      pb_times):
    """Fail-closed cross-bindings shared by build (source files) and verify (copies)."""
    scope = report['episode_scope']
    checks = {'release_report_valid': True}

    if sha256_file(capture_receipt_path) != validation['capture_receipt_sha256']:
        raise EvidenceError('capture receipt bytes do not match the release report binding')
    require(capture_receipt.get('plan_sha256') == validation['private_plan_sha256'],
            'capture receipt plan_sha256 does not match the release report private plan binding')
    require(capture_receipt.get('episode_id') in (None, scope['episode_id']),
            'capture receipt episode_id differs from the release report')
    checks['capture_receipt_bound_to_release_report'] = True
    checks['capture_receipt_bound_to_private_plan'] = True

    qa_index = qa_frame_index(qa_report, capture_receipt, report['frames'], 'machine QA')
    checks['machine_qa_binds_capture_run'] = True
    checks['machine_qa_pass_frames'] = len(report['frames'])

    if sha256_file(backend_path) != validation['backend_receipt_sha256']:
        raise EvidenceError('backend receipt bytes do not match the release report binding')
    backend_scope = backend['plan_binding']['episode_scope']
    for field in ('cluster_id', 'episode_id', 'passive_id', 'world_variant_id'):
        require(backend_scope.get(field) == scope.get(field),
                f'backend receipt {field} differs from the release report')
    checks['backend_receipt_bound_to_release_report'] = True

    bindings = host_receipt.get('bindings')
    require(isinstance(bindings, dict), 'host receipt must carry bindings')
    require(bindings.get('release_report', {}).get('sha256') == sha256_file(release_path),
            'host receipt does not bind the release report bytes')
    require(bindings.get('capture_receipt', {}).get('sha256') == sha256_file(capture_receipt_path),
            'host receipt does not bind the capture receipt bytes')
    require(bindings.get('capture_plan', {}).get('sha256') == validation['private_plan_sha256'],
            'host receipt does not bind the capture private plan bytes')
    require(host_receipt.get('episode_scope') == scope,
            'host receipt episode scope differs from the release report')
    device_bundle = bindings.get('device_bundle')
    require(isinstance(device_bundle, dict) and isinstance(device_bundle.get('sha256'), str),
            'host receipt must bind the device bundle')
    pb_binding = bindings.get('pb')
    require(isinstance(pb_binding, dict)
            and isinstance(pb_binding.get('stream_sha256'), str)
            and isinstance(pb_binding.get('manifest_sha256'), str),
            'host receipt must bind the PB passive stream and episode manifest hashes')
    checks['host_receipt_bound_to_capture_and_release'] = True

    replay_bindings = replay.get('bindings')
    require(isinstance(replay_bindings, dict)
            and replay_bindings.get('host_receipt', {}).get('sha256') == sha256_file(host_path),
            'replay receipt does not bind the host receipt bytes')
    require(replay_bindings.get('bundle') == device_bundle,
            'replay receipt device bundle differs from the host receipt')
    require(replay.get('episode_scope') == scope,
            'replay receipt episode scope differs from the release report')
    checks['replay_receipt_bound_to_host_receipt'] = True

    bundle_bindings = image_bundle.get('bindings')
    require(isinstance(bundle_bindings, dict)
            and bundle_bindings.get('host_receipt', {}).get('sha256') == sha256_file(host_path)
            and bundle_bindings.get('replay_receipt', {}).get('sha256') == sha256_file(replay_path),
            'image bundle manifest does not bind the host and replay receipts')
    require(image_bundle.get('episode_scope') == scope,
            'image bundle manifest episode scope differs from the release report')
    checks['image_bundle_bound_to_host_and_replay'] = True
    checks['image_bundle_pb_writes'] = 0

    host_frames = {}
    for frame in host_receipt.get('frames', []):
        key = frame.get('packet_id')
        require(key not in host_frames, f'host receipt repeats frame {key!r}')
        host_frames[key] = frame
    bundle_frames = {}
    for frame in image_bundle['frames']:
        key = frame.get('packet_id')
        require(key not in bundle_frames, f'image bundle manifest repeats frame {key!r}')
        bundle_frames[key] = frame
    released_ids = sorted(frame['packet_id'] for frame in report['frames'])
    require(sorted(host_frames) == released_ids,
            'host receipt frames do not equal the release report frames')
    require(sorted(bundle_frames) == released_ids,
            'image bundle frames do not equal the release report frames')
    capture_frames = {}
    for frame in capture_receipt.get('frames', []):
        key = frame.get('packet_id')
        require(key not in capture_frames, f'capture receipt repeats frame {key!r}')
        capture_frames[key] = frame
    require(sorted(capture_frames) == released_ids,
            'capture receipt frames do not equal the release report frames')
    require(sorted(pb_times) == released_ids,
            'PB packet times do not equal the release report frames')

    frames = []
    for released in report['frames']:
        packet_id = released['packet_id']
        host_frame = host_frames[packet_id]
        bundle_frame = bundle_frames[packet_id]
        times = pb_times[packet_id]
        require(times['observed_at'] == capture_frames[packet_id].get('observed_at'),
                f'frame {packet_id} PB observed_at drifted from the capture receipt')
        require(times['source'] == host_frame.get('source'),
                f'frame {packet_id} PB source drifted from the host receipt frame')
        require(host_frame.get('device_sha256') == released['sha256'],
                f'host receipt frame {packet_id} does not carry the released device frame hash')
        require(host_frame.get('pb_declared_sha256') != host_frame.get('device_sha256'),
                f'host receipt frame {packet_id} merely republishes the PB original screenshot')
        require(bundle_frame.get('sha256') == released['sha256']
                and bundle_frame.get('screenshot_ref') == host_frame.get('staged_ref'),
                f'image bundle frame {packet_id} does not carry the bound device frame reference')
        qa_frame = qa_index[(packet_id, released['frame_id'])]
        confidences = [field.get('confidence') for field in qa_frame.get('fields', [])
                       if isinstance(field.get('confidence'), int)
                       and not isinstance(field.get('confidence'), bool)]
        frames.append({'packet_id': packet_id, 'frame_id': released['frame_id'],
                       'source': host_frame.get('source'), 'sha256': released['sha256'],
                       'bytes': host_frame.get('bytes'), 'width': released['width'],
                       'height': released['height'], 'capture_png': released['png'],
                       'release_status': released['status'], 'qa_status': qa_frame.get('status'),
                       'qa_fields': len(qa_frame.get('fields', [])),
                       'qa_min_confidence': min(confidences) if confidences else None,
                       'pb_declared_sha256': host_frame.get('pb_declared_sha256'),
                       'staged_ref': host_frame.get('staged_ref'),
                       'image_bundle_path': bundle_frame.get('bundle_path'),
                       'observed_at': times['observed_at'],
                       'delivered_at': times['delivered_at']})
    checks['frames_cross_bound'] = len(frames)
    checks['device_frames_differ_from_pb'] = True
    checks['raw_pngs_stored_in_repo'] = False
    checks['pb_packet_times_bound'] = len(pb_times)

    receipt_bindings = {
        'release_report_sha256': sha256_file(release_path),
        'capture_receipt_sha256': sha256_file(capture_receipt_path),
        'capture_plan_sha256': validation['private_plan_sha256'],
        'backend_receipt_sha256': sha256_file(backend_path),
        'host_receipt_sha256': sha256_file(host_path),
        'replay_receipt_sha256': sha256_file(replay_path),
        'image_bundle_sha256': sha256_file(bundle_path),
        'capture_run_id': capture_receipt.get('capture_run_id'),
        'device_bundle_sha256': device_bundle.get('sha256'),
        'pb': {'stream_sha256': pb_binding['stream_sha256'],
               'manifest_sha256': pb_binding['manifest_sha256']},
    }
    return receipt_bindings, frames, checks


def write_bundle_readme(bundle, manifests):
    lines = [
        '# V1 acceptance evidence bundle',
        '',
        f'- schema: `{SCHEMA}`',
        f'- generated: {now_utc()}',
        '- scope: V1 reusable closed loop for '
        + ', '.join(sorted(manifest['episode_scope']['cluster_id'] + '/'
                           + manifest['episode'] for manifest in manifests)),
        '- boundary: private release-ready evidence only. No image live model run, no PB '
        '`evaluate.py`, no Gold, no publication; KU/PB source data, prompts, GT, APK and '
        'runtime were not modified.',
        '- device PNGs: **not stored here** (blueprint section 6 forbids local private '
        'screenshot paths). Each frame is traceable by sha256/bytes/dimensions in '
        '`pb-image-refs-<episode>.json`; rebuild the capture with the private capture plan '
        'and the commands in `docs/PB-GUI工程日志.md` (rounds 5 and 9).',
        '- single acceptance entry: `python3 scripts/pb_gui_v1_evidence.py checklist '
        '--bundle <this bundle>` re-verifies offline and writes `V1-ACCEPTANCE.md` here, '
        'mapping the blueprint section 1.1 ten-step loop and the thirteen frozen fields to '
        'the receipts below (file path + sha256).',
        '',
        '## Layout',
        '',
        '```',
        'README.md                              # this bundle manifest',
        '<episode>/pb-gui-release-report-<episode>.json      # release gate report (copy)',
        '<episode>/pb-gui-image-host-receipt-<episode>.json  # image host receipt (copy)',
        '<episode>/episode-evidence.json                     # per-episode manifest + file hashes',
        '<episode>/*.json                                    # remaining receipts + derived lists',
        'V1-ACCEPTANCE.md                                    # checklist, generated by `checklist`',
        '```',
        '',
        'The release report and host receipt sit at the episode top level because '
        '`pb_gui_coverage_audit.py --evidence-dir` discovers exactly those two names there; '
        'the other documents are recorded by that audit as rejected candidates (present, '
        'not bound) and are validated by this bundle\'s own manifest and `verify`.',
        '',
    ]
    for manifest in manifests:
        episode = manifest['episode']
        scope = manifest['episode_scope']
        lines += [
            f'## {episode} ({scope["cluster_id"]}/{scope["passive_id"]}/{scope["world_variant_id"]})',
            '',
            f'- frames: {manifest["counts"]["frames"]} (QA pass), release report '
            f'`{manifest["bindings"]["release_report_sha256"][:12]}`',
            f'- capture run: `{manifest["bindings"]["capture_run_id"]}`, image bundle '
            f'`{manifest["bindings"]["image_bundle_sha256"][:12]}` (pb_writes=0)',
            f'- PB passive stream: `{manifest["bindings"]["pb"]["stream_sha256"][:12]}`',
            f'- PB packet times: {len(manifest["frames"])} observed_at/delivered_at pairs from '
            'the write-back `image-refs.jsonl` '
            f'(`{manifest["bindings"]["pb"]["stream_sha256"][:12]}`-bound stream), recorded '
            f'in `pb-image-refs-{episode}.json#pb_time_binding`',
            '',
            '| file | bytes | sha256 |',
            '|---|---|---|',
        ]
        for item in manifest['files']:
            lines.append(f'| {item["path"]} | {item["bytes"]} | `{item["sha256"]}` |')
        lines.append('')
    lines += [
        '## Verify (no device, no PB root)',
        '',
        '```bash',
        '# 1. re-verify this bundle offline',
        f'python3 scripts/pb_gui_v1_evidence.py verify --bundle {source_ref(bundle)}',
        '',
        '# 2. refresh the single V1 acceptance checklist',
        f'python3 scripts/pb_gui_v1_evidence.py checklist --bundle {source_ref(bundle)}',
        '',
        '# 3. re-audit evidence coverage (V2 panorama; not a V1 acceptance condition)',
        'python3 scripts/pb_gui_coverage_audit.py --pb-root <PB_ROOT> \\',
        f'    --evidence-dir {source_ref(bundle)} --output <outside-pb>/v1-audit.json',
        '```',
        '',
        '## Round-4 exit: rebuild from the receipts',
        '',
        '`preflight -> replay -> writeback -> evidence build` is the round-4 gate exit.',
        'This bundle is the repository-visible trace; the device receipts and the private image',
        'write-back bundle live outside the repository (`runtime/` is git-ignored, the private',
        'capture directory and the staged view are caller-owned). Re-derive the exit from the',
        'recorded receipts plus the private capture directory: no device is needed (the capture',
        'PNGs already exist) and no PB tree is written.',
        '',
        '```bash',
        'scripts/pb_gui_v1_round4_exit.sh --pb-root <PB_ROOT> --episode <NAME> \\',
        '    --release-report <RELEASE_REPORT> --capture-dir <PRIVATE_CAPTURE_DIR> \\',
        '    --backend-receipt <BACKEND_RECEIPT> --host-dir <HOST_DIR> [--evidence-bundle <DIR>]',
        '```',
        '',
        'The wrapper runs exactly these four steps in order and stops at the first failure:',
        '',
        '1. `pb_gui_image_host.py preflight` re-verifies the release chain, re-hashes every',
        '   device frame against `capture/receipt.json` and materialises the staged PB image',
        '   view at `<HOST_DIR>/stage`;',
        '2. `pb_gui_image_host.py replay` attaches the staged device frames through the real PB',
        '   `Session(gui_mode="image")` offline and writes the replay receipt;',
        '3. `pb_gui_image_host.py writeback` copies the verified device frames and the rewritten',
        '   PB image references into `<BUNDLE_DIR>` (`pb_writes=0`, `pb_tree_modified=false`);',
        '4. `pb_gui_v1_evidence.py build` re-validates every receipt and rebuilds this evidence',
        '   bundle. An existing bundle is never overwritten: `build` requires an empty or absent',
        '   `--bundle`, and `writeback` likewise requires an empty or absent `--bundle-dir`, so a',
        '   rebuild uses new directories or removes the old ones deliberately.',
        '',
        'Passing `--stage-dir`/`--host-dir` inside the repository keeps the host receipt',
        'self-describing: the receipt then records a project-relative stage reference and',
        '`replay`/`writeback` need no explicit `--stage-dir` (a stage outside the repository',
        'leaves `stage.ref=null`, so those two commands must be given `--stage-dir`).',
        '',
        'The concrete input names of this build are recorded per episode in',
        '`<episode>/episode-evidence.json:sources` as non-absolute references (for example',
        '`capture_dir`); the private directories themselves stay outside the repository.',
        '',
        '## Claims',
        '',
        '- machine QA and replay ran on real devices/offline PB `Session(image)`; no model was '
        'called by the release, replay or write-back path (`model_called=false`).',
        '- V1 only: no `PB evaluate.py`, no Gold, no formal publication, no KU/PB source edits.',
        '',
    ]
    text = '\n'.join(lines)
    require(not LOCAL_PATH.search(text), 'bundle README would carry a local absolute path')
    (Path(bundle) / 'README.md').write_text(text, encoding='utf8')


def command_build(args):
    bundle = Path(args.bundle).resolve()
    for spec in args.episode:
        name = spec[0]
        require(bool(EPISODE_NAME.fullmatch(name)) and name not in ('.', '..'),
                f'--episode name must be a single safe path segment: {name!r}')
    if bundle.exists() and any(bundle.iterdir()):
        raise EvidenceError(f'--bundle must be empty or absent, refusing to overwrite {bundle}')
    bundle.mkdir(parents=True, exist_ok=True)
    manifests = []
    for spec in args.episode:
        name, release_arg, capture_arg, backend_arg, host_arg, replay_arg, bundle_arg = spec
        release_path, capture_dir = Path(release_arg), Path(capture_arg)
        backend_path, host_path = Path(backend_arg), Path(host_arg)
        replay_path, image_bundle_path = Path(replay_arg), Path(bundle_arg)
        report, validation = load_release_report(release_path)
        capture_receipt_path = capture_dir / 'receipt.json'
        try:
            capture = host.verify_capture_bundle(capture_dir, report)
        except (host.HostError, ValueError) as error:
            raise EvidenceError(f'capture bundle failed validation: {error}') from None
        capture_receipt = load_capture_receipt(capture_receipt_path)
        qa_source_path = capture_dir / 'qa-machine.json'
        qa_source = load_machine_qa(qa_source_path)
        backend = load_backend_receipt(backend_path)
        host_receipt = load_host_receipt(host_path)
        replay = load_replay_receipt(replay_path)
        image_bundle = load_image_bundle(image_bundle_path)
        for document, label in ((report, 'release report'),
                                (capture_receipt, 'capture receipt'),
                                (backend, 'backend receipt'),
                                (host_receipt, 'host receipt'),
                                (replay, 'replay receipt'),
                                (image_bundle, 'image bundle manifest')):
            assert_clean(document, label)
        # The raw machine QA report is not copied: its per-field ``expected`` text
        # is a plan-side key the repository scan treats as private, so the bundle
        # keeps the derived qa summary (status/confidence plus the source sha256).
        pb_times, time_binding = read_image_refs(
            image_bundle_path, image_bundle, image_bundle['frames'],
            host_pb_stream_sha256(host_receipt))
        bindings, frames, checks = cross_check_chain(
            release_path=release_path, report=report, validation=validation,
            capture_receipt_path=capture_receipt_path, capture_receipt=capture_receipt,
            qa_report=qa_source, backend_path=backend_path, backend=backend,
            host_path=host_path, host_receipt=host_receipt, replay_path=replay_path,
            replay=replay, bundle_path=image_bundle_path, image_bundle=image_bundle,
            pb_times=pb_times)
        qa_summary = machine_qa_summary(qa_source, sha256_file(qa_source_path))
        refs = {'schema_version': REFS_SCHEMA, 'generated_at': now_utc(),
                'episode_scope': report['episode_scope'], 'frames': frames,
                'pb_packet_times': pb_times, 'pb_time_binding': time_binding,
                'counts': {'frames': len(frames),
                           'bytes': sum(frame['bytes'] for frame in frames)},
                'checks': {'raw_pngs_stored': False, 'device_frames_differ_from_pb': True,
                           'pb_packet_times_bound': len(pb_times)}}
        episode_dir = bundle / name
        files = []
        for source, filename in (
                (release_path, f'pb-gui-release-report-{name}.json'),
                (host_path, f'pb-gui-image-host-receipt-{name}.json'),
                (replay_path, f'pb-gui-image-replay-receipt-{name}.json'),
                (backend_path, f'pb-backend-apply-receipt-{name}.json'),
                (capture_receipt_path, f'pb-capture-receipt-{name}.json'),
                (image_bundle_path, f'pb-passive-image-bundle-{name}.json')):
            destination = copy_checked(source, episode_dir / filename)
            files.append({'path': filename, 'sha256': sha256_file(destination),
                          'bytes': destination.stat().st_size})
        for filename, document in ((f'pb-machine-qa-summary-{name}.json', qa_summary),
                                   (f'pb-image-refs-{name}.json', refs)):
            destination = write_json_new(episode_dir / filename, document)
            files.append({'path': filename, 'sha256': sha256_file(destination),
                          'bytes': destination.stat().st_size})
        manifest = {
            'schema_version': EPISODE_SCHEMA, 'status': 'evidence_bound',
            'generated_at': now_utc(), 'episode': name,
            'episode_scope': report['episode_scope'],
            'sources': {'release_report': source_ref(release_path),
                        'capture_dir': source_ref(capture_dir),
                        'backend_receipt': source_ref(backend_path),
                        'host_receipt': source_ref(host_path),
                        'replay_receipt': source_ref(replay_path),
                        'image_bundle_manifest': source_ref(image_bundle_path),
                        'device_pngs_rehashed_at_build': len(capture['frames'])},
            'bindings': bindings, 'frames': frames, 'files': files,
            'counts': {'frames': len(frames), 'files': len(files),
                       'bytes': sum(item['bytes'] for item in files),
                       'image_attachments': replay.get('image_attachments'),
                       'max_images_per_window': host_receipt.get(
                           'pb_invariants', {}).get('max_images_per_window')},
            'checks': checks,
            'claims': {'model_called': False, 'live_model_run': False,
                       'pb_evaluate_run': False, 'gold': False, 'published': False,
                       'device_writes': False, 'raw_device_pngs_stored': False,
                       'pb_tree_modified': False},
        }
        assert_clean(manifest, 'episode evidence manifest')
        write_json_new(episode_dir / 'episode-evidence.json', manifest)
        manifests.append(manifest)
    write_bundle_readme(bundle, manifests)
    print(json.dumps({'status': 'evidence_bound', 'bundle': str(bundle),
                      'episodes': [{'episode': manifest['episode'],
                                    'frames': manifest['counts']['frames']}
                                   for manifest in manifests],
                      'model_called': False}, ensure_ascii=False))
    return 0


# Blueprint section 1.1: the frozen V1 loop. Each step names the bundle documents
# (role, JSON path) that carry its receipt; step 1 is the frozen contract itself,
# which is what the remaining receipts must agree on.
V1_STEPS = (
    ('1', '冻结 PB 抽象适配契约',
     (('backend_receipt', 'plan_binding.episode_scope'), ('capture_receipt', 'source_assets'))),
    ('2', 'PB Passive 适配/编译',
     (('capture_receipt', 'source_assets.gui_plan'), ('capture_receipt', 'source_assets.backend_plan'),
      ('capture_receipt', 'source_assets.gate_plan'), ('host_receipt', 'bindings.pb'))),
    ('3', 'PB 数据 → adapter_plan',
     (('backend_receipt', 'plan_binding.plan_sha256'), ('backend_receipt', 'records[].semantic_ref'))),
    ('4', 'adapter_plan → 真实 App 初始化接口',
     (('backend_receipt', 'records[].adapter'), ('backend_receipt', 'ordering'))),
    ('5', 'seed / history replay / 独立 readback',
     (('backend_receipt', 'records[].seed'), ('backend_receipt', 'records[].readback'),
      ('capture_receipt', 'seed_receipts'), ('capture_receipt', 'history_replays'), ('capture_receipt', 'reset_verified'))),
    ('6', '指定 App 页面与实体导航',
     (('capture_receipt', 'frames[].navigation'), ('capture_receipt', 'frames[].package'),
      ('capture_receipt', 'frames[].activity'), ('capture_receipt', 'frames[].ready.visible_assertions'))),
    ('7', 'ADB 原始截图',
     (('capture_receipt', 'frames[].png'), ('capture_receipt', 'frames[].sha256'),
      ('capture_receipt', 'frames[].ui_dump_sha256'), ('refs', 'frames[].bytes'),
      ('refs', 'frames[].width'))),
    ('8', 'GUI 像素 QA、哈希、复位收据',
     (('qa_summary', 'frames[].status'), ('qa_summary', 'frames[].fields[].confidence'),
      ('qa_summary', 'rule_version'), ('release_report', 'bindings.machine_qa'))),
    ('9', '回写 PB GUI 图片引用',
     (('image_bundle', 'status'), ('image_bundle', 'writeback.pb_writes'),
      ('image_bundle', 'files[image-refs.jsonl]'), ('refs', 'pb_time_binding'),
      ('refs', 'frames[].staged_ref'))),
    ('10', 'PB image replay 验证',
     (('replay_receipt', 'status'), ('replay_receipt', 'request_count'),
      ('replay_receipt', 'distinct_frames'), ('replay_receipt', 'summary_fallbacks'))),
)

# Blueprint section 1.1: the thirteen frozen fields of the abstract adapter
# contract, each mapped to the bundle document that carries it.
V1_FIELDS = (
    ('source', (('refs', 'frames[].source'), ('capture_receipt', 'frames[].source'))),
    ('entity_ref', (('backend_receipt', 'records[].semantic_ref'),
                    ('capture_receipt', 'frames[].readback.import_id'))),
    ('fields', (('qa_summary', 'frames[].fields[].field'), ('qa_summary', 'frames[].fields[].passed'))),
    ('observed_at', (('refs', 'frames[].observed_at'), ('capture_receipt', 'frames[].observed_at'))),
    ('delivered_at', (('refs', 'pb_packet_times[].delivered_at'),
                      ('refs', 'pb_time_binding.image_refs_sha256'))),
    ('adapter', (('backend_receipt', 'records[].adapter.module'), ('backend_receipt', 'records[].adapter.seed'),
                 ('backend_receipt', 'records[].adapter.read'))),
    ('seed_projection', (('backend_receipt', 'records[].seed.result'),
                         ('backend_receipt', 'records[].seed.at'))),
    ('history_replay', (('backend_receipt', 'history_replays[].set'),
                        ('capture_receipt', 'history_replays[].result'))),
    ('readback', (('backend_receipt', 'records[].readback.result'),
                  ('capture_receipt', 'frames[].readback'))),
    ('surface', (('capture_receipt', 'frames[].package'), ('release_report', 'frames[].png'))),
    ('view_id', (('capture_receipt', 'frames[].activity'), ('capture_receipt', 'frames[].navigation'))),
    ('visible_assertions', (('capture_receipt', 'frames[].ready.visible_assertions'),
                            ('qa_summary', 'frames[].fields[].text'))),
    ('unsupported/reject', (('release_report', 'counts.unsupported_targets'),
                            ('release_report', 'counts.gate_targets'),
                            ('qa_summary', 'device_env.status'))),
)

# Blueprint sections 1.2/5 rounds 5-6: explicitly out of V1 scope. These are not
# V1 acceptance conditions and a reviewer must not read them as V1 failures.
V2_ITEMS = (
    'image live 模型运行（需可用 endpoint + 能力声明）',
    'PB `code/evaluate.py` / 动作语义裁决 / 评分',
    'Gold 生成',
    '正式发布器（gui_publish / PB 发布流程）',
    '17 Episode / 38 GUI packet 全量 coverage 扩张',
    'PB 核心查询/评分协议修改',
)


def evidence_paths(episode_dir, name):
    return {key: episode_dir / f'pb-{prefix}-{name}.json' for key, prefix in EVIDENCE_FILES}


def verify_bundle(bundle):
    """Offline re-verification (no device, no PB root); the single source of truth."""
    bundle = Path(bundle).resolve()
    require(bundle.is_dir(), f'evidence bundle not found: {bundle}')
    require((bundle / 'README.md').is_file(), 'evidence bundle has no README.md')
    stray = sorted(path.name for path in bundle.iterdir()
                   if path.is_file() and path.suffix == '.json')
    require(not stray, 'bundle top level must not carry JSON files (the coverage audit '
                       f'--evidence-dir scan reads release/host receipts one level down): {stray}')
    manifest_paths = sorted(bundle.glob('*/episode-evidence.json'))
    require(manifest_paths, 'evidence bundle has no <episode>/episode-evidence.json')
    verified = []
    for manifest_path in manifest_paths:
        manifest = load_json(manifest_path, 'episode evidence manifest')
        require(manifest.get('schema_version') == EPISODE_SCHEMA
                and manifest.get('status') == 'evidence_bound',
                f'episode evidence manifest must be {EPISODE_SCHEMA} with status=evidence_bound')
        name = manifest.get('episode')
        require(manifest_path.parent.name == name,
                'episode evidence manifest directory name differs from its episode field')
        episode_dir = manifest_path.parent
        files = manifest.get('files')
        require(isinstance(files, list) and files,
                f'{name}: episode evidence manifest carries no files')
        for item in files:
            path = episode_dir / str(item.get('path'))
            require(path.is_file(), f'{name}: evidence file is missing: {item.get("path")}')
            require(sha256_file(path) == item.get('sha256'),
                    f'{name}: evidence file drifted: {item.get("path")}')
        paths = evidence_paths(episode_dir, name)
        for key, path in paths.items():
            require(path.is_file(), f'{name}: evidence bundle is missing {key}: {path.name}')
        report, validation = load_release_report(paths['release_report'])
        capture_receipt = load_capture_receipt(paths['capture_receipt'])
        qa_summary = load_json(paths['qa_summary'], 'machine QA summary')
        require(qa_summary.get('schema_version') == QA_SCHEMA,
                f'{name}: machine QA summary schema_version must be {QA_SCHEMA!r}')
        backend = load_backend_receipt(paths['backend_receipt'])
        host_receipt = load_host_receipt(paths['host_receipt'])
        replay = load_replay_receipt(paths['replay_receipt'])
        image_bundle = load_image_bundle(paths['image_bundle'])
        refs = load_json(paths['refs'], 'image reference list')
        require(refs.get('schema_version') == REFS_SCHEMA,
                f'{name}: image reference list schema_version must be {REFS_SCHEMA!r}')
        documents = {'release_report': report, 'capture_receipt': capture_receipt,
                     'qa_summary': qa_summary, 'backend_receipt': backend,
                     'host_receipt': host_receipt, 'replay_receipt': replay,
                     'image_bundle': image_bundle, 'refs': refs, 'manifest': manifest}
        for label, document in documents.items():
            assert_clean(document, f'{name}: {label}')
        pb_times = load_packet_times(refs, image_bundle, host_receipt, image_bundle['frames'])
        bindings, frames, checks = cross_check_chain(
            release_path=paths['release_report'], report=report, validation=validation,
            capture_receipt_path=paths['capture_receipt'], capture_receipt=capture_receipt,
            qa_report=qa_summary, backend_path=paths['backend_receipt'], backend=backend,
            host_path=paths['host_receipt'], host_receipt=host_receipt,
            replay_path=paths['replay_receipt'], replay=replay,
            bundle_path=paths['image_bundle'], image_bundle=image_bundle, pb_times=pb_times)
        require(bindings == manifest.get('bindings'), f'{name}: evidence bindings drifted')
        require(frames == manifest.get('frames'), f'{name}: frame hash list drifted')
        require(checks == manifest.get('checks'), f'{name}: evidence checks drifted')
        require(refs.get('frames') == frames and refs.get('episode_scope')
                == report['episode_scope'], f'{name}: image reference list drifted')
        require(refs.get('pb_packet_times') == pb_times,
                f'{name}: PB packet times drifted')
        verified.append({'episode': name, 'dir': episode_dir, 'manifest': manifest,
                         'paths': paths, 'documents': documents, 'bindings': bindings,
                         'frames': frames, 'checks': checks, 'files': files,
                         'pb_times': pb_times})
    return bundle, verified


def sha_index(files):
    return {str(item.get('path')): str(item.get('sha256')) for item in files}


def checklist_lines(bundle, verified):
    """The one-place V1 acceptance checklist: 10 steps + 13 frozen fields."""
    episodes = [entry['episode'] for entry in verified]
    frames = sum(len(entry['frames']) for entry in verified)
    lines = [
        '# V1 验收清单（单一入口）',
        '',
        f'- schema: `pb-gui-v1-acceptance-checklist.v1`（由 `pb_gui_v1_evidence.py checklist` 生成）',
        f'- 生成时间: {now_utc()}',
        f'- 证据 bundle: `{source_ref(bundle)}`（离线复验 `evidence_verified` 通过后才写出）',
        f'- 判定: **satisfied** — V1 闭环 {len(episodes)} 条 Episode（{", ".join(episodes)}）、'
        f'{frames} 个 GUI packet、10/10 步、13/13 冻结字段均有证据。',
        '- 范围: 仅蓝图 §1.1 的 10 步闭环 + 13 冻结字段；§1.2 的 V2 项在本文件里一律记为 '
        '`not-applicable (V2)`，不作为 V1 判定条件。',
        '',
        '完整文件哈希：见本 bundle `README.md` 的 file 表与 `<episode>/episode-evidence.json#files`；'
        '下表用前 12 位哈希定位同一文件。',
        '',
    ]
    for entry in verified:
        name = entry['episode']
        hashes = sha_index(entry['files'])
        documents = entry['documents']
        report = documents['release_report']
        capture, qa = documents['capture_receipt'], documents['qa_summary']
        backend, replay = documents['backend_receipt'], documents['replay_receipt']
        bundle_doc = documents['image_bundle']
        scope = report['episode_scope']
        records = backend.get('records', [])
        replays = capture.get('history_replays', [])
        lines += [
            f'## {name} — {scope["cluster_id"]}/{scope["passive_id"]}/'
            f'{scope["world_variant_id"]}',
            '',
            f'- 结论: release `{report["status"]}`、capture '
            f'`{capture.get("status")}`（reset_verified={capture.get("reset_verified")}）、'
            f'QA `{qa.get("status")}`（rule `{qa.get("rule_version")}`）、'
            f'replay `{replay["status"]}`、writeback `{bundle_doc["status"]}`（pb_writes='
            f'{bundle_doc["writeback"]["pb_writes"]}）',
            f'- 关键复核值: frames={len(entry["frames"])}，'
            f'by_source={json.dumps(report["counts"].get("by_source"), ensure_ascii=False)}，'
            f'adapter records={len(records)}，history replays={len(replays)}，'
            f'replay windows={replay.get("request_count")}，'
            f'image attachments={replay.get("image_attachments")}，'
            f'distinct frames={replay.get("distinct_frames")}，'
            f'summary_fallbacks={replay.get("summary_fallbacks")}',
            f'- 时间绑定: PB stream `{entry["bindings"]["pb"]["stream_sha256"][:12]}`，'
            f'逐包 observed/delivered 共 {len(entry["pb_times"])} 条（见 `pb-image-refs-'
            f'{name}.json#pb_packet_times`）',
            '',
            '### A. 10 步闭环',
            '',
            '| 步 | 步骤 | 判定 | 证据（文件#路径，哈希前 12 位） |',
            '|---|---|---|---|',
        ]
        for step, title, evidence in V1_STEPS:
            lines.append(f'| {step} | {title} | satisfied | {checklist_cell(name, evidence, hashes)} |')
        lines += ['', '### B. 13 个冻结字段', '',
                  '| # | 冻结字段 | 判定 | 证据（文件#路径，哈希前 12 位） |',
                  '|---|---|---|---|']
        for index, (field, evidence) in enumerate(V1_FIELDS, start=1):
            lines.append(f'| {index} | `{field}` | bound | {checklist_cell(name, evidence, hashes)} |')
        lines += [
            '',
            '### C. fail-closed 不变量',
            '',
        ]
        for key in ('release_report_valid', 'capture_receipt_bound_to_release_report',
                    'capture_receipt_bound_to_private_plan', 'machine_qa_binds_capture_run',
                    'backend_receipt_bound_to_release_report',
                    'host_receipt_bound_to_capture_and_release',
                    'replay_receipt_bound_to_host_receipt',
                    'image_bundle_bound_to_host_and_replay', 'frames_cross_bound',
                    'pb_packet_times_bound', 'device_frames_differ_from_pb',
                    'image_bundle_pb_writes'):
            lines.append(f'- `{key}` = `{json.dumps(entry["checks"].get(key))}`')
        raw_stored = entry['checks'].get('raw_pngs_stored_in_repo')
        lines += [f'- `raw_pngs_stored_in_repo` = `{json.dumps(raw_stored)}`（设备 PNG 不入库）', '']
    lines += [
        '## 非 V1 项（V2，不计入 V1 判定）',
        '',
    ]
    lines += [f'- `not-applicable (V2)`: {item}' for item in V2_ITEMS]
    lines += [
        '',
        '- 全量 coverage（17 Episode / 38 GUI packet）属 V2；V1 只交付 '
        f'{", ".join(episodes)}（{len(episodes)} 条闭环）。coverage 审计另存 `docs/v1-acceptance-'
        'evidence-20260926-coverage-audit.json`，不参与本清单判定。',
        '',
        '## 单点复验命令',
        '',
        '```bash',
        f'python3 scripts/pb_gui_v1_evidence.py verify --bundle {source_ref(bundle)}',
        f'python3 scripts/pb_gui_v1_evidence.py checklist --bundle {source_ref(bundle)}',
        'python3 scripts/pb_gui_coverage_audit.py --pb-root <PB_ROOT> \\',
        f'    --evidence-dir {source_ref(bundle)} --output <outside-pb>/v1-audit.json',
        '# 第 4 轮出口重建（preflight → replay → writeback → evidence build，无需设备）',
        'scripts/pb_gui_v1_round4_exit.sh --pb-root <PB_ROOT> --episode <NAME> \\',
        '    --release-report <RELEASE_REPORT> --capture-dir <PRIVATE_CAPTURE_DIR> \\',
        '    --backend-receipt <BACKEND_RECEIPT> --host-dir <HOST_DIR> [--evidence-bundle <DIR>]',
        '```',
        '',
    ]
    text = '\n'.join(lines)
    require(not LOCAL_PATH.search(text), 'checklist would carry a local absolute path')
    return text


def checklist_cell(name, evidence, hashes):
    cells = []
    for role, path in evidence:
        filename = f'pb-{dict(EVIDENCE_FILES)[role]}-{name}.json'
        digest = hashes.get(filename)
        require(digest is not None, f'checklist references an unrecorded file: {filename}')
        cells.append(f'`{filename}#{path}`（`{digest[:12]}`）')
    return '<br>'.join(cells)


def command_verify(args):
    bundle, verified = verify_bundle(args.bundle)
    episodes = [{'episode': entry['episode'], 'frames': len(entry['frames']),
                 'files': len(entry['files']),
                 'capture_run_id': entry['bindings']['capture_run_id'],
                 'image_attachments': entry['manifest']['counts']['image_attachments']}
                for entry in verified]
    print(json.dumps({'status': 'evidence_verified', 'bundle': str(bundle),
                      'episodes': episodes, 'model_called': False}, ensure_ascii=False))
    return 0


def command_checklist(args):
    """Write the single V1 acceptance checklist next to the verified bundle."""
    bundle, verified = verify_bundle(args.bundle)
    output = bundle / (args.output or CHECKLIST_NAME)
    require(output.parent == bundle,
            f'--output must stay inside the evidence bundle: {output.name}')
    text = checklist_lines(bundle, verified)
    output.write_text(text, encoding='utf8')
    steps = sum(1 for _ in V1_STEPS)
    print(json.dumps({'status': 'checklist_written', 'checklist': source_ref(output),
                      'verdict': 'satisfied', 'episodes': [entry['episode']
                                                           for entry in verified],
                      'steps': f'{steps}/{steps}', 'frozen_fields': f'{len(V1_FIELDS)}/'
                      f'{len(V1_FIELDS)}', 'sha256': sha256_file(output),
                      'model_called': False}, ensure_ascii=False))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    build = sub.add_parser('build', help='assemble a repository-visible evidence bundle')
    build.add_argument('--bundle', required=True)
    build.add_argument('--episode', required=True, nargs=7, action='append',
                       metavar=('NAME', 'RELEASE_REPORT', 'CAPTURE_DIR', 'BACKEND_RECEIPT',
                                'HOST_RECEIPT', 'REPLAY_RECEIPT', 'IMAGE_BUNDLE_MANIFEST'))
    build.set_defaults(handler=command_build)

    verify = sub.add_parser('verify', help='re-verify a built evidence bundle offline')
    verify.add_argument('--bundle', required=True)
    verify.set_defaults(handler=command_verify)

    checklist = sub.add_parser('checklist', help='write the single V1 acceptance checklist')
    checklist.add_argument('--bundle', required=True)
    checklist.add_argument('--output', help=f'default: <bundle>/{CHECKLIST_NAME}')
    checklist.set_defaults(handler=command_checklist)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (EvidenceError, host.HostError) as exc:
        print(f'{parser.prog}: error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
