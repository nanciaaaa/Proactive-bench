#!/usr/bin/env python3
"""Round-5 thin host between a private release-ready GUI bundle and PB image mode.

ProactiveBench declares image capability only through two entry points: an
in-process ``Session(..., gui_mode="image")`` whose ``model_messages`` needs a
host-supplied ``model_capabilities`` declaration, and the offline
``code/runtime.py replay --gui-mode image --image-capacity N`` exporter. Neither
entry point consumes a device capture bundle, and ``code/run.py`` is fixed to
``gui_mode="summary"``. This host therefore lives outside the PB runtime and
does three things:

* ``preflight`` re-verifies the release chain (private release report -> capture
  receipts -> device PNG bytes), proves the seven device frames cover exactly
  the GUI packets of the real PB Episode/Passive, refuses frames that merely
  republish a PB original screenshot, and materialises a *derived* staged input
  view under a simulator-extension runtime directory: PB metadata is copied
  unchanged, the seven ``passive_inputs/<passive>/gui/<packet_id>.png`` files
  are replaced by the verified device frames and those packets' declared
  ``screenshot_sha256`` values are rewritten to the device frame hashes. PB
  itself is never written to and no PB original screenshot is read, copied or
  re-hashed.
* ``replay`` drives the real PB ``Session(gui_mode="image")`` over the staged
  view with an explicit image capacity declaration, checks every attached image
  byte against the bound device frame, proves GUI events carry no caption and
  emits a per-window receipt. This is offline input replay, not a model run.
* ``writeback`` materialises the persistent private image bundle for the PB
  Passive (blueprint round 4): after a verified replay it copies the bound
  device frames and the rewritten ``screenshot_ref``/``screenshot_sha256``
  pairs into a fixed-name ``runtime/pb-gui-passive-image-bundle-...`` directory
  with its own ``manifest.json``. The PB tree itself is still never written to.
* ``live`` is the only path that may call a model. It fails closed unless the
  host receipt is ``bound`` with ``live_readiness.status=ready``, the caller
  passed ``--confirm-live``, the endpoint advertises the requested model and the
  target adapter declared ``input_modalities=["text","image"]`` with enough
  capacity. The declaration must also name its wire ``provider`` (``openai`` or
  ``anthropic``); a missing or unknown provider is never guessed, and an offline
  adapter dry run translates a synthetic request before any live call. When no
  endpoint/credentials exist it writes an explicit gap report with
  ``model_called=false`` instead of faking a live run.

Invariants: PB runtime/prompts/data are read-only here; no summaries are
substituted for images in image mode (a missing capability declaration aborts,
never falls back to text); no GT/Need/oracle material is read or emitted; no
Gold or publication claim is made; failed attempts are preserved.

Usage::

    python3 scripts/pb_gui_image_host.py preflight \
        --release-report REPORT --capture-dir CAPTURE --pb-root PB_ROOT \
        --stage-dir STAGE --output HOST_RECEIPT [--capabilities CAPS.json] \
        [--endpoint URL] [--model ID] [--require-live]
    python3 scripts/pb_gui_image_host.py replay \
        --host-receipt HOST_RECEIPT --stage-dir STAGE \
        --output REPLAY_RECEIPT [--image-capacity N | --capabilities CAPS.json]
    python3 scripts/pb_gui_image_host.py writeback \
        --host-receipt HOST_RECEIPT --replay-receipt REPLAY_RECEIPT \
        --pb-root PB_ROOT [--stage-dir STAGE] [--bundle-dir BUNDLE]
    python3 scripts/pb_gui_image_host.py live \
        --host-receipt HOST_RECEIPT --stage-dir STAGE --output DIR \
        --endpoint URL --model ID --capabilities CAPS.json --confirm-live
"""
import argparse
import base64
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pb_backend_plan as pb

HOST_SCHEMA = 'pb-gui-image-host-receipt.v1'
STAGE_SCHEMA = 'pb-gui-image-stage-manifest.v1'
REPLAY_SCHEMA = 'pb-gui-image-replay-receipt.v1'
LIVE_SCHEMA = 'pb-gui-image-live-receipt.v1'
CAPABILITY_SCHEMA = 'pb-gui-image-capabilities.v1'
RELEASE_SCHEMA = 'pb-gui-release-report.v1'
CAPTURE_RECEIPT_SCHEMA = '1.0'
BUNDLE_SCHEMA = 'pb-gui-passive-image-bundle.v1'
BUNDLE_STATUS = 'image_refs_written'
HOST_STATUS = 'bound'
REPLAY_STATUS = 'replay_verified'
LIVE_COMPLETED = 'live_completed'
LIVE_FAILED = 'live_failed'
LIVE_BLOCKED = 'live_blocked'
IMAGE_MODE = 'image'
SUMMARY_FALLBACK = 0
HEX64 = re.compile(r'^[0-9a-f]{64}$')
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
MAX_IMAGE_BYTES = 8 * 1024 * 1024
PB_SOURCE_FILES = ('code/runtime.py', 'code/run.py', 'code/layout.py',
                   'code/world_model.py', 'code/validate.py', 'code/evaluate.py')
LIVE_ENDPOINT_ENV = 'PB_IMAGE_ENDPOINT'
LIVE_MODEL_ENV = 'PB_IMAGE_MODEL'
PROVIDER_OPENAI = 'openai'
PROVIDER_ANTHROPIC = 'anthropic'
ANTHROPIC_VERSION_DEFAULT = '2023-06-01'
PROVIDER_PROFILES = {
    PROVIDER_OPENAI: {
        'auth_header': 'Authorization',
        'auth_scheme': 'Bearer',
        'health_path': '/health',
        'models_path': '/v1/models',
        'request_path': '/v1/chat/completions',
        'extra_headers': {},
    },
    PROVIDER_ANTHROPIC: {
        'auth_header': 'x-api-key',
        'auth_scheme': None,
        'health_path': '/health',
        'models_path': '/v1/models',
        'request_path': '/v1/messages',
        'extra_headers': {'anthropic-version': ANTHROPIC_VERSION_DEFAULT},
    },
}


class HostError(Exception):
    """A fail-closed condition: nothing may be claimed or executed."""


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as out:
        json.dump(payload, out, ensure_ascii=False, indent=2)
        out.write('\n')
    return path


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise HostError(f'{label} must be a nonempty string')
    return value


def require_object(value, label):
    if not isinstance(value, dict):
        raise HostError(f'{label} must be a JSON object')
    return value


def require_hex64(value, label):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise HostError(f'{label} must be 64 lowercase hex characters')
    return value


def require_positive_int(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise HostError(f'{label} must be a positive integer')
    return value


def ensure_outside(path, pb_root, label):
    resolved = Path(path).resolve()
    pb_root = Path(pb_root).resolve()
    if resolved == pb_root or resolved.is_relative_to(pb_root):
        raise HostError(f'{label} must not be written inside --pb-root: {path}')
    return resolved


def relative_to_or_error(path, root, label):
    resolved = Path(path).resolve()
    root = Path(root).resolve()
    if not resolved.is_relative_to(root):
        raise HostError(f'{label} must stay inside {root}')
    return resolved.relative_to(root).as_posix()


def sha256_tree(root):
    entries = []
    for path in sorted(Path(root).rglob('*')):
        if path.is_file():
            entries.append({'path': path.relative_to(root).as_posix(),
                            'sha256': sha256_file(path), 'bytes': path.stat().st_size})
    return entries


def scan_private(document):
    """Internal guard: GT/Need/oracle words must not appear in emitted receipts.

    The fixed ``claims`` block is the only allowed place: it is a set of negative
    assertions (``gold: false``, ...) and never carries private material.
    """
    forbidden = {'gt', 'need', 'oracle', 'expected', 'private_gt', 'need_set',
                 'expected_need_set', 'gold', 'answer'}
    hits = []

    def walk(value, trail):
        if trail and trail[0] == 'claims':
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in forbidden:
                    hits.append('/'.join(trail + (str(key),)))
                walk(item, trail + (str(key),))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, trail + (str(index),))

    walk(document, ())
    return hits


def load_release_report(path):
    if not Path(path).is_file():
        raise HostError(f'release report not found: {path}')
    report = pb.load_json(Path(path), 'release report')
    require_object(report, 'release report')
    if report.get('schema_version') != RELEASE_SCHEMA:
        raise HostError(f'release report schema_version must be {RELEASE_SCHEMA!r}')
    if report.get('status') != 'release_ready' or report.get('release_ready') is not True:
        raise HostError('release report must be status=release_ready and release_ready=true')
    scope = require_object(report.get('episode_scope'), 'release report episode_scope')
    for field in ('cluster_id', 'episode_id', 'passive_id', 'world_variant_id',
                  'track', 'decision_point', 'timezone'):
        require_text(scope.get(field), f'release report episode_scope.{field}')
    frames = report.get('frames')
    if not isinstance(frames, list) or not frames:
        raise HostError('release report frames must be a nonempty list')
    seen = set()
    for frame in frames:
        require_object(frame, 'release report frame')
        packet_id = require_text(frame.get('packet_id'), 'frame packet_id')
        if packet_id in seen:
            raise HostError(f'release report repeats packet {packet_id!r}; one frame per GUI packet is required')
        seen.add(packet_id)
        require_text(frame.get('png'), f'frame {packet_id} png')
        require_hex64(frame.get('sha256'), f'frame {packet_id} sha256')
        require_positive_int(frame.get('width'), f'frame {packet_id} width')
        require_positive_int(frame.get('height'), f'frame {packet_id} height')
        if frame.get('status') != 'pass':
            raise HostError(f'frame {packet_id} is not a pass frame')
    if report.get('counts', {}).get('frames') != len(frames):
        raise HostError('release report counts.frames does not match the frame list')
    if report.get('counts', {}).get('pass_frames') != len(frames):
        raise HostError('release report counts.pass_frames does not match the frame list')
    return report


def verify_capture_bundle(capture_dir, report):
    capture_dir = Path(capture_dir).resolve()
    receipt_path = capture_dir / 'receipt.json'
    plan_path = capture_dir / 'private-plan.json'
    if not receipt_path.is_file() or not plan_path.is_file():
        raise HostError('capture directory needs receipt.json and private-plan.json')
    report_bindings = require_object(report.get('bindings'), 'release report bindings')
    plan_binding = require_object(report_bindings.get('private_plan'), 'bindings.private_plan')
    receipt_binding = require_object(report_bindings.get('capture_receipt'), 'bindings.capture_receipt')
    if sha256_file(plan_path) != require_hex64(plan_binding.get('sha256'), 'bindings.private_plan.sha256'):
        raise HostError('capture private plan bytes no longer match the release report binding')
    receipt_sha = sha256_file(receipt_path)
    if receipt_sha != require_hex64(receipt_binding.get('sha256'), 'bindings.capture_receipt.sha256'):
        raise HostError('capture receipt bytes no longer match the release report binding')
    receipt = pb.load_json(receipt_path, 'capture receipt')
    require_object(receipt, 'capture receipt')
    if receipt.get('schema_version') != CAPTURE_RECEIPT_SCHEMA:
        raise HostError(f'capture receipt schema_version must be {CAPTURE_RECEIPT_SCHEMA!r}')
    if receipt.get('status') != 'capture_complete' or receipt.get('reset_verified') is not True:
        raise HostError('capture receipt must be capture_complete with reset_verified=true')
    if receipt.get('plan_sha256') != sha256_file(plan_path):
        raise HostError('capture receipt plan_sha256 does not bind the private plan bytes')
    receipt_frames = receipt.get('frames')
    if not isinstance(receipt_frames, list):
        raise HostError('capture receipt frames must be a list')
    indexed = {}
    for frame in receipt_frames:
        require_object(frame, 'capture receipt frame')
        key = (require_text(frame.get('packet_id'), 'receipt frame packet_id'),
               require_text(frame.get('frame_id'), 'receipt frame frame_id'))
        if key in indexed:
            raise HostError(f'capture receipt repeats frame {key[0]}/{key[1]}')
        indexed[key] = frame
    frames = []
    for reported in report['frames']:
        packet_id = reported['packet_id']
        candidates = [[key, frame] for key, frame in indexed.items() if key[0] == packet_id]
        if len(candidates) != 1:
            raise HostError(f'capture receipt must carry exactly one frame for {packet_id!r}, '
                            f'found {len(candidates)}')
        key, frame = candidates[0]
        png = capture_dir / reported['png']
        if not png.is_file():
            raise HostError(f'capture frame png is missing: {reported["png"]}')
        resolved = png.resolve()
        if not resolved.is_relative_to(capture_dir):
            raise HostError(f'capture frame escapes the capture directory: {reported["png"]}')
        raw = resolved.read_bytes()
        if sha256_bytes(raw) != reported['sha256']:
            raise HostError(f'capture frame {packet_id} bytes do not match the release report sha256')
        if frame.get('sha256') != reported['sha256'] or frame.get('png') != reported['png']:
            raise HostError(f'capture receipt frame {packet_id} does not bind the reported png/sha256')
        require_hex64(frame.get('sha256'), f'capture receipt frame {packet_id} sha256')
        require_text(frame.get('source'), f'capture receipt frame {packet_id} source')
        require_text(frame.get('observed_at'), f'capture receipt frame {packet_id} observed_at')
        width, height = png_dimensions(raw, packet_id)
        if (width, height) != (reported['width'], reported['height']):
            raise HostError(f'capture frame {packet_id} dimensions differ from the release report')
        frames.append({'packet_id': packet_id, 'frame_id': key[1], 'png': resolved,
                       'sha256': reported['sha256'], 'bytes': len(raw), 'width': width,
                       'height': height, 'source': frame['source'],
                       'observed_at': frame['observed_at']})
    return {'capture_dir': capture_dir, 'receipt': receipt,
            'receipt_path': receipt_path, 'receipt_sha256': receipt_sha,
            'plan_path': plan_path, 'plan_sha256': sha256_file(plan_path),
            'frames': frames}


def png_dimensions(raw, label):
    if len(raw) < 24 or raw[:8] != PNG_MAGIC or not 24 <= len(raw) <= MAX_IMAGE_BYTES:
        raise HostError(f'{label} is not a bounded PNG image')
    width, height = int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')
    if not (0 < width <= 8192 and 0 < height <= 8192):
        raise HostError(f'{label} has invalid PNG dimensions')
    return width, height


def locate_pb_episode(pb_root, cluster_id, episode_id):
    pb_root = Path(pb_root).resolve()
    if not (pb_root / 'benchmark.json').is_file():
        raise HostError(f'--pb-root has no benchmark.json: {pb_root}')
    benchmark = pb.load_json(pb_root / 'benchmark.json', 'benchmark')
    found_cluster, cluster_dir, manifest_path, manifest, cluster = pb.find_episode(
        pb_root, benchmark, episode_id, cluster_id)
    if found_cluster != cluster_id:
        raise HostError(f'episode {episode_id!r} belongs to cluster {found_cluster!r}, not {cluster_id!r}')
    require_text(cluster.get('cluster_id'), 'cluster.cluster_id')
    passive_id = require_text(manifest.get('passive_id'), 'manifest.passive_id')
    passive_entries = [entry for entry in cluster.get('passive_variants', [])
                       if entry.get('passive_id') == passive_id]
    if len(passive_entries) != 1:
        raise HostError(f'cluster must declare exactly one passive entry for {passive_id!r}')
    passive = passive_entries[0]
    if passive.get('stream_ref') != manifest.get('passive_ref'):
        raise HostError('cluster passive stream_ref does not match the manifest passive_ref')
    return {'pb_root': pb_root, 'benchmark': benchmark, 'cluster_dir': cluster_dir,
            'manifest_path': manifest_path,
            'cluster': cluster, 'manifest': manifest, 'passive': passive}


def load_pb_stream(source):
    cluster_dir, manifest = Path(source['cluster_dir']), source['manifest']
    stream_path = cluster_dir / require_text(manifest.get('passive_ref'), 'manifest.passive_ref')
    if not stream_path.is_file():
        raise HostError(f'PB passive stream is missing: {manifest["passive_ref"]}')
    packets = []
    for line in stream_path.read_text(encoding='utf8').splitlines():
        if line.strip():
            packets.append(json.loads(line))
    if not packets:
        raise HostError('PB passive stream is empty')
    gui = []
    passive_id = manifest['passive_id']
    for index, packet in enumerate(packets, 1):
        require_object(packet, f'passive packet #{index}')
        if packet.get('tag') != 'gui':
            continue
        packet_id = require_text(packet.get('packet_id'), f'passive packet #{index} packet_id')
        expected_ref = f'passive_inputs/{passive_id}/gui/{packet_id}.png'
        if packet.get('screenshot_ref') != expected_ref:
            raise HostError(f'GUI packet {packet_id} screenshot_ref must be {expected_ref!r}')
        require_hex64(packet.get('screenshot_sha256'), f'GUI packet {packet_id} screenshot_sha256')
        source_png = cluster_dir / expected_ref
        if not source_png.is_file():
            raise HostError(f'PB declares GUI asset {expected_ref} but the file is absent')
        gui.append({'packet_id': packet_id, 'sequence': packet.get('sequence'),
                    'source': packet.get('source'), 'observed_at': packet.get('observed_at'),
                    'delivered_at': packet.get('delivered_at'),
                    'screenshot_ref': expected_ref,
                    'declared_sha256': packet['screenshot_sha256']})
    if not gui:
        raise HostError('PB Passive has no GUI packets; image mode is unsupported')
    return {'stream_path': stream_path, 'stream_sha256': sha256_file(stream_path),
            'packets': packets, 'gui_packets': gui,
            'window_size': load_contract_window(source['pb_root'])}


def load_contract_window(pb_root):
    contract = pb.load_json(Path(pb_root) / 'prompts' / 'agent_contract.json', 'agent contract')
    window = require_object(contract.get('passive_input'), 'contract passive_input').get('window')
    window = require_object(window, 'contract passive_input.window')
    return require_positive_int(window.get('size'), 'contract window size')


def simulate_window_counts(packets, window_size):
    gui_flags = [packet.get('tag') == 'gui' for packet in packets]
    cursor = min(window_size, len(gui_flags))
    counts = [sum(gui_flags[max(0, cursor - window_size):cursor])]
    while cursor < len(gui_flags):
        cursor += 1
        counts.append(sum(gui_flags[max(0, cursor - window_size):cursor]))
    return counts


def check_report_alignment(report, source, stream):
    scope, cluster, manifest = report['episode_scope'], source['cluster'], source['manifest']
    checks = [('cluster_id', cluster.get('cluster_id')), ('episode_id', manifest.get('episode_id')),
              ('passive_id', manifest.get('passive_id')),
              ('world_variant_id', manifest.get('world_variant_id')),
              ('track', manifest.get('track')), ('decision_point', manifest.get('decision_point')),
              ('timezone', manifest.get('timezone'))]
    for field, actual in checks:
        if scope.get(field) != actual:
            raise HostError(f'release report {field} {scope.get(field)!r} does not match PB {actual!r}')
    report_ids = sorted(frame['packet_id'] for frame in report['frames'])
    pb_ids = sorted(packet['packet_id'] for packet in stream['gui_packets'])
    if report_ids != pb_ids:
        raise HostError(f'release bundle packets {report_ids} do not equal current PB GUI packets {pb_ids}')
    return {'report_packets': report_ids, 'pb_gui_packets': pb_ids}


def check_device_frames(report, bundle, stream):
    binding = {frame['packet_id']: frame for frame in bundle['frames']}
    declared = {packet['packet_id']: packet for packet in stream['gui_packets']}
    for packet_id, frame in binding.items():
        if frame['sha256'] == declared[packet_id]['declared_sha256']:
            raise HostError(f'device frame {packet_id} equals the declared PB screenshot sha256; '
                            'a republished PB original is not a device capture')
    return binding


def resolve_provider(value, label='capability declaration provider'):
    """Fail closed on an unknown provider; missing stays None (never guessed)."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in PROVIDER_PROFILES:
        raise HostError(f'{label} must be one of {sorted(PROVIDER_PROFILES)!r}, got {value!r}')
    return value


def load_capabilities(path, model=None):
    if not Path(path).is_file():
        raise HostError(f'capability declaration not found: {path}')
    document = pb.load_json(Path(path), 'capability declaration')
    require_object(document, 'capability declaration')
    if document.get('schema_version') != CAPABILITY_SCHEMA:
        raise HostError(f'capability declaration schema_version must be {CAPABILITY_SCHEMA!r}')
    provider = resolve_provider(document.get('provider'))
    version = document.get('anthropic_version')
    if version is not None:
        require_text(version, 'capability declaration anthropic_version')
    if provider == PROVIDER_ANTHROPIC and version is None:
        version = ANTHROPIC_VERSION_DEFAULT
    if provider != PROVIDER_ANTHROPIC and version is not None:
        raise HostError('capability declaration anthropic_version is only valid for '
                        f'provider {PROVIDER_ANTHROPIC!r}')
    modalities = document.get('input_modalities')
    if not isinstance(modalities, list) or 'image' not in modalities:
        raise HostError('capability declaration must include "image" in input_modalities')
    capacity = require_positive_int(document.get('max_images_per_request'),
                                    'capability declaration max_images_per_request')
    declared_model = document.get('model')
    if declared_model is not None:
        require_text(declared_model, 'capability declaration model')
    return {'schema_version': CAPABILITY_SCHEMA, 'model': declared_model,
            'provider': provider, 'provider_declared': provider is not None,
            'anthropic_version': version,
            'input_modalities': list(modalities), 'max_images_per_request': capacity,
            'source': str(document.get('source') or 'target adapter declaration; not an endpoint probe'),
            'sha256': sha256_file(path)}


def provider_headers(provider, api_key_env=None, *, version=None, base=None):
    """Build request headers without ever returning or logging the secret value."""
    profile = PROVIDER_PROFILES[provider]
    headers = dict(base or {})
    headers.setdefault('Content-Type', 'application/json')
    if profile['extra_headers']:
        headers.update(profile['extra_headers'])
    if provider == PROVIDER_ANTHROPIC and version:
        headers['anthropic-version'] = version
    credential_present = bool(api_key_env and os.environ.get(api_key_env))
    if credential_present:
        prefix = f'{profile["auth_scheme"]} ' if profile['auth_scheme'] else ''
        headers[profile['auth_header']] = prefix + os.environ[api_key_env]
    return headers, credential_present


def http_json(url, *, payload=None, headers=None, timeout=15):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf8')
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response), None
    except urllib.error.HTTPError as exc:
        return None, f'HTTP {exc.code}'
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'


def probe_endpoint(endpoint, model, timeout, api_key_env=None, provider=PROVIDER_OPENAI,
                   anthropic_version=None):
    """Metadata-only probe (health + model list); never a model/inference request."""
    provider = resolve_provider(provider, 'probe provider')
    profile = PROVIDER_PROFILES[provider]
    endpoint = endpoint.rstrip('/')
    headers, credential_present = provider_headers(provider, api_key_env,
                                                   version=anthropic_version)
    result = {'endpoint': endpoint, 'provider': provider,
              'auth_header': profile['auth_header'], 'request_path': profile['request_path'],
              'requests': [], 'credential_env': api_key_env,
              'credential_present': credential_present,
              'health_status': None, 'model_advertised': False,
              'models_advertised': None, 'auth_rejected': False, 'error': None}
    result['requests'].append('health:' + profile['health_path'])
    health, error = http_json(endpoint + profile['health_path'], headers=headers, timeout=timeout)
    if error:
        result['error'] = error
        result['auth_rejected'] = 'HTTP 401' in error or 'HTTP 403' in error
        return result
    result['health_status'] = health.get('status') if isinstance(health, dict) else None
    result['requests'].append('models:' + profile['models_path'])
    models, error = http_json(endpoint + profile['models_path'], headers=headers, timeout=timeout)
    if error:
        result['error'] = error
        result['auth_rejected'] = 'HTTP 401' in error or 'HTTP 403' in error
        return result
    advertised = [item.get('id') for item in models.get('data', [])] if isinstance(models, dict) else []
    result['models_advertised'] = len(advertised)
    result['model_advertised'] = model in advertised
    return result


def live_gaps(capabilities, probe, max_images_per_window, model, adapter=None):
    gaps = []
    if capabilities is None:
        gaps.append('missing_capability_declaration')
    else:
        if capabilities.get('provider') is None:
            gaps.append('missing_provider_declaration')
        if capabilities['max_images_per_request'] < max_images_per_window:
            gaps.append(f'insufficient_image_capacity: declared '
                        f'{capabilities["max_images_per_request"]} < required {max_images_per_window}')
        if capabilities.get('model') and capabilities['model'] != model:
            gaps.append(f'capability_model_mismatch: declaration {capabilities["model"]!r} '
                        f'!= --model {model!r}')
        if adapter is not None and adapter.get('status') == 'failed':
            gaps.append('adapter_preflight_failed: ' + str(adapter.get('error', 'unknown')))
    if probe is None:
        gaps.append('missing_endpoint')
    elif probe.get('error'):
        gaps.append('no_reachable_model_endpoint')
        if probe.get('auth_rejected'):
            gaps.append('endpoint_auth_rejected')
    else:
        if probe.get('health_status') != 'ok':
            gaps.append('model_endpoint_unhealthy')
        if not probe.get('model_advertised'):
            gaps.append('model_not_advertised')
        if (capabilities and capabilities.get('provider') == PROVIDER_ANTHROPIC
                and not probe.get('credential_present')):
            gaps.append('credential_env_not_set' if probe.get('credential_env')
                        else 'missing_api_key_env_for_anthropic')
    return gaps


def stage_plan_entries(pb_root, source, stream, binding):
    cluster_dir = Path(source['cluster_dir'])
    cluster_rel = cluster_dir.relative_to(Path(pb_root).resolve())
    manifest, cluster = source['manifest'], source['cluster']
    variant = next((entry for entry in cluster['world_variants']
                    if entry['world_variant_id'] == manifest['world_variant_id']), None)
    if variant is None:
        raise HostError('manifest world variant is not registered in cluster.json')
    for manifest_key, variant_key in (('access_ref', 'access_ref'), ('binding_ref', 'binding_ref'),
                                      ('history_ref', 'history_ref')):
        if manifest[manifest_key] != variant[variant_key]:
            raise HostError(f'manifest {manifest_key} does not match the registered world variant')
    entries = [{'dest': Path('benchmark.json'), 'src': Path(pb_root) / 'benchmark.json', 'role': 'pb_copy'}]
    shared_refs = [cluster['global_base_ref'], cluster['source_registry_ref']]
    for ref in shared_refs:
        if not str(ref).startswith('shared://'):
            raise HostError(f'expected a shared:// reference, got {ref!r}')
        entries.append({'dest': Path('shared') / str(ref)[len('shared://'):],
                        'src': pb.resolve_ref(source['pb_root'], [('cluster', cluster_dir)],
                                              ref, f'cluster {ref}'), 'role': 'pb_copy'})
    cluster_refs = ['cluster.json', cluster['cluster_base_ref'], variant['patch_ref'],
                    variant['access_ref'], variant['binding_ref'], variant['history_ref'],
                    manifest['passive_ref'],
                    f'episodes/{manifest["episode_id"]}/manifest.json']
    for ref in cluster_refs:
        entries.append({'dest': cluster_rel / ref,
                        'src': pb.resolve_ref(source['pb_root'], [('cluster', cluster_dir)],
                                              ref, f'cluster {ref}'), 'role': 'pb_copy'})
    for frame in binding.values():
        dest = cluster_rel / f'passive_inputs/{manifest["passive_id"]}/gui/{frame["packet_id"]}.png'
        entries.append({'dest': dest, 'src': frame['png'], 'role': 'device_frame'})
    seen = {}
    for entry in entries:
        key = entry['dest'].as_posix()
        if key in seen:
            raise HostError(f'stage plan repeats destination {key}')
        seen[key] = entry
    return entries


def build_stage(stage_dir, pb_root, source, stream, binding, report, capture_binding):
    stage_dir = ensure_outside(stage_dir, pb_root, '--stage-dir')
    if stage_dir.exists() and any(stage_dir.iterdir()):
        raise HostError(f'--stage-dir must be empty or absent, refusing to overwrite {stage_dir}')
    stage_dir.mkdir(parents=True, exist_ok=True)
    cluster_rel = Path(source['cluster_dir']).relative_to(Path(pb_root).resolve())
    stream_dest_rel = cluster_rel / manifest_passive_ref(source)
    entries = stage_plan_entries(pb_root, source, stream, binding)
    staged = []
    stream_dest = None
    for entry in entries:
        destination = stage_dir / entry['dest']
        destination.parent.mkdir(parents=True, exist_ok=True)
        if entry['role'] == 'device_frame':
            shutil.copyfile(entry['src'], destination)
        else:
            shutil.copyfile(entry['src'], destination)
        digest = sha256_file(destination)
        source_digest = sha256_file(entry['src'])
        if digest != source_digest:
            raise HostError(f'staged copy drifted for {entry["dest"]}')
        record = {'path': entry['dest'].as_posix(), 'role': entry['role'],
                  'sha256': digest, 'bytes': destination.stat().st_size,
                  'source_sha256': source_digest}
        staged.append(record)
        if entry['dest'] == stream_dest_rel:
            stream_dest = destination
    if stream_dest is None:
        raise HostError('stage plan did not include the passive stream')
    rewritten = rewrite_staged_stream(stream_dest, binding)
    stream_record = next(item for item in staged if item['path'] == stream_dest_rel.as_posix())
    stream_record['role'] = 'staged_stream'
    stream_record['sha256'] = sha256_file(stream_dest)
    stream_record['rewrites'] = rewritten
    manifest = {'schema_version': STAGE_SCHEMA, 'generated_at': now_utc(),
                'episode_scope': report['episode_scope'],
                'source': {'release_report_sha256': capture_binding['report_sha256'],
                           'capture_receipt_sha256': capture_binding['receipt_sha256'],
                           'capture_plan_sha256': capture_binding['plan_sha256'],
                           'pb_stream_sha256': stream['stream_sha256']},
                'entries': staged,
                'counts': {'files': len(staged), 'device_frames': len(binding),
                           'stream_rewrites': rewritten, 'bytes': sum(item['bytes'] for item in staged)},
                'checks': {'pb_writes': 0, 'pb_original_pngs_read': 0,
                           'images_copied_from_pb': 0, 'device_frames_bound': len(binding)}}
    manifest_path = write_json(stage_dir / 'stage-manifest.json', manifest)
    return {'stage_dir': stage_dir, 'manifest_path': manifest_path,
            'manifest_sha256': sha256_file(manifest_path),
            'cluster_dir': stage_dir / cluster_rel, 'entries': staged}


def manifest_passive_ref(source):
    return source['manifest']['passive_ref']


def rewrite_staged_stream(stream_path, binding):
    packets = [json.loads(line) for line in stream_path.read_text(encoding='utf8').splitlines() if line.strip()]
    rewrites = 0
    for packet in packets:
        frame = binding.get(packet.get('packet_id'))
        if frame is None:
            continue
        if packet.get('tag') != 'gui':
            raise HostError(f'bound packet {packet.get("packet_id")} is not a GUI packet')
        packet['screenshot_sha256'] = frame['sha256']
        rewrites += 1
    if rewrites != len(binding):
        raise HostError('staged stream did not rewrite every bound device frame')
    stream_path.write_text(''.join(json.dumps(packet, ensure_ascii=False) + '\n' for packet in packets),
                           encoding='utf8')
    return rewrites


def import_pb_runtime(pb_root):
    code_dir = (Path(pb_root) / 'code').resolve()
    if not (code_dir / 'runtime.py').is_file():
        raise HostError(f'PB runtime.py not found under {code_dir}')
    if str(code_dir) not in sys.path:
        sys.path.insert(0, str(code_dir))
    import runtime as pb_runtime
    if Path(pb_runtime.__file__).resolve().parent != code_dir:
        raise HostError(f'another runtime module is already imported from {pb_runtime.__file__}')
    return pb_runtime


def capabilities_payload(capabilities):
    return {'input_modalities': list(capabilities['input_modalities']),
            'max_images_per_request': capabilities['max_images_per_request']}


def decode_data_url(block, label):
    url = require_object(block.get('image_url'), f'{label} image_url').get('url')
    prefix = 'data:image/png;base64,'
    if not isinstance(url, str) or not url.startswith(prefix):
        raise HostError(f'{label} is not an inline PNG data URL; no summary fallback')
    raw = base64.b64decode(url[len(prefix):], validate=True)
    width, height = png_dimensions(raw, label)
    return {'sha256': sha256_bytes(raw), 'bytes': len(raw), 'width': width, 'height': height}


def image_data_url_bytes(block, label):
    url = require_object(block.get('image_url'), f'{label} image_url').get('url')
    if not isinstance(url, str) or not url.startswith('data:image/'):
        raise HostError(f'{label} is not an inline PNG data URL; no summary fallback')
    header, separator, encoded = url.partition(',')
    if not separator or not header.endswith(';base64'):
        raise HostError(f'{label} data URL is not base64 image data')
    media_type = header[len('data:'):-len(';base64')]
    if media_type != 'image/png':
        raise HostError(f'{label} media type must be image/png, got {media_type!r}')
    raw = base64.b64decode(encoded, validate=True)
    png_dimensions(raw, label)
    return raw, media_type


def adapted_content_blocks(provider, content, label):
    if isinstance(content, str):
        return [{'type': 'text', 'text': require_text(content, f'{label} text')}]
    if not isinstance(content, list):
        raise HostError(f'{label} content must be text or a content block list')
    blocks = []
    for index, block in enumerate(content, 1):
        require_object(block, f'{label} block #{index}')
        kind = block.get('type')
        if kind == 'text':
            blocks.append({'type': 'text',
                           'text': require_text(block.get('text'), f'{label} block #{index} text')})
        elif kind == 'image_url':
            raw, media_type = image_data_url_bytes(block, f'{label} image #{index}')
            blocks.append({'type': 'image',
                           'source': {'type': 'base64', 'media_type': media_type,
                                      'data': base64.b64encode(raw).decode('ascii')}})
        else:
            raise HostError(f'{label} carries an unsupported content block {kind!r}')
    return blocks


def adapt_request(provider, payload):
    """Translate the canonical PB image-mode payload into the provider wire shape."""
    if provider == PROVIDER_OPENAI:
        return copy.deepcopy(payload)
    if provider != PROVIDER_ANTHROPIC:
        raise HostError(f'unsupported provider {provider!r}; refusing to guess a wire format')
    if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list):
        raise HostError('adapter input must carry a message list')
    messages, system_chunks = [], []
    for index, message in enumerate(payload['messages'], 1):
        require_object(message, f'adapter message #{index}')
        role, label = message.get('role'), f'adapter message #{index}'
        if role == 'system':
            if not isinstance(message.get('content'), str):
                raise HostError(f'{label} system content must be plain text')
            system_chunks.append(require_text(message['content'], f'{label} system content'))
            continue
        if role not in ('user', 'assistant'):
            raise HostError(f'{label} has unsupported role {role!r}')
        messages.append({'role': role,
                         'content': adapted_content_blocks(provider, message.get('content'), label)})
    if not messages:
        raise HostError('adapter translation dropped every non-system message')
    adapted = {'model': require_text(payload.get('model'), 'adapter model'),
               'max_tokens': require_positive_int(payload.get('max_tokens'), 'adapter max_tokens'),
               'messages': messages}
    if 'temperature' in payload:
        adapted['temperature'] = payload['temperature']
    if system_chunks:
        adapted['system'] = '\n\n'.join(system_chunks)
    return adapted


def adapter_dropped_params(provider, payload):
    if provider != PROVIDER_ANTHROPIC:
        return []
    return [key for key in ('seed', 'enable_thinking', 'stream') if key in payload]


def parse_response(provider, response):
    """Extract action text, stop reason and usage for the selected provider."""
    if not isinstance(response, dict):
        raise HostError('model endpoint returned a non-object response')
    if provider == PROVIDER_ANTHROPIC:
        blocks = response.get('content')
        if not isinstance(blocks, list):
            raise HostError('anthropic response content must be a list')
        texts = [block.get('text') for block in blocks
                 if isinstance(block, dict) and block.get('type') == 'text'
                 and isinstance(block.get('text'), str)]
        if not texts:
            raise HostError('anthropic response carries no text block')
        stop = response.get('stop_reason')
        if stop != 'end_turn':
            raise HostError(f'anthropic generation did not end_turn (stop_reason={stop!r}); '
                            'no silent truncation repair')
        usage = response.get('usage') or {}
        prompt, completion = usage.get('input_tokens', 0), usage.get('output_tokens', 0)
        return {'text': ''.join(texts), 'stop': stop,
                'usage': {'prompt_tokens': prompt, 'completion_tokens': completion,
                          'total_tokens': prompt + completion}}
    if provider != PROVIDER_OPENAI:
        raise HostError(f'unsupported provider {provider!r}')
    choices = response.get('choices')
    if not isinstance(choices, list) or not choices:
        raise HostError('model endpoint response carries no choices')
    choice = choices[0]
    if choice.get('finish_reason') != 'stop':
        raise HostError('generation did not stop normally; no silent truncation repair')
    message = choice.get('message') or {}
    if not isinstance(message.get('content'), str):
        raise HostError('model response content is not text')
    usage = response.get('usage') or {}
    return {'text': message['content'], 'stop': choice['finish_reason'],
            'usage': {key: usage.get(key, 0)
                      for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}}


def provider_stub_response(provider):
    if provider == PROVIDER_ANTHROPIC:
        return {'stop_reason': 'end_turn',
                'content': [{'type': 'text', 'text': '{"action":"noop"}'}],
                'usage': {'input_tokens': 1, 'output_tokens': 1}}
    return {'choices': [{'finish_reason': 'stop',
                         'message': {'content': '{"action":"noop"}'}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}


def _adapter_blocks(adapted):
    blocks = []
    for message in adapted.get('messages', []):
        content = message.get('content')
        if isinstance(content, list):
            blocks.extend(block for block in content if isinstance(block, dict))
    return blocks


def _adapter_image_count(adapted):
    return sum(1 for block in _adapter_blocks(adapted)
               if block.get('type') in ('image', 'image_url'))


def _adapter_text_count(adapted):
    return sum(1 for block in _adapter_blocks(adapted) if block.get('type') == 'text')


def _adapter_image_sha256(adapted):
    for block in _adapter_blocks(adapted):
        if block.get('type') == 'image':
            return sha256_bytes(base64.b64decode(block['source']['data'], validate=True))
        if block.get('type') == 'image_url':
            url = block.get('image_url', {}).get('url', '')
            return sha256_bytes(base64.b64decode(url.split(',', 1)[-1], validate=True))
    return None


def adapter_dry_run(capabilities):
    """Hermetic adapter preflight: no network, no model call, no PB image."""
    if capabilities is None or capabilities.get('provider') is None:
        return {'status': 'not_applicable', 'provider': None,
                'reason': 'missing_provider_declaration', 'model_called': False,
                'network_calls': 0}
    provider = capabilities['provider']
    raw = (PNG_MAGIC + b'\x00' * 8 + (1080).to_bytes(4, 'big') + (2400).to_bytes(4, 'big')
           + b'\x00' * 16)
    canonical = {'model': capabilities.get('model') or 'adapter-preflight-model',
                 'messages': [{'role': 'system', 'content': 'adapter preflight; no model call'},
                              {'role': 'user', 'content': [
                                  {'type': 'text', 'text': json.dumps({'phase': 'adapter_preflight'})},
                                  {'type': 'text', 'text': json.dumps({'packet_id': 'p-preflight'})},
                                  {'type': 'image_url',
                                   'image_url': {'url': 'data:image/png;base64,'
                                                        + base64.b64encode(raw).decode('ascii')}}]}],
                 'max_tokens': 64, 'temperature': 0.0, 'enable_thinking': False,
                 'seed': 0, 'stream': False}
    try:
        adapted = adapt_request(provider, canonical)
        parsed = parse_response(provider, provider_stub_response(provider))
    except HostError as exc:
        return {'status': 'failed', 'provider': provider, 'error': str(exc),
                'model_called': False, 'network_calls': 0}
    wire_path = '/v1/messages' if provider == PROVIDER_ANTHROPIC else '/v1/chat/completions'
    checks = {'image_count_preserved': _adapter_image_count(adapted) == 1,
              'image_bytes_preserved': _adapter_image_sha256(adapted) == sha256_bytes(raw),
              'text_block_count_preserved': _adapter_text_count(adapted) == 2,
              'provider_path_declared': PROVIDER_PROFILES[provider]['request_path'] == wire_path,
              'response_text_parsed': parsed['text'] == '{"action":"noop"}'}
    if provider == PROVIDER_ANTHROPIC:
        checks['message_count_preserved'] = len(adapted.get('messages', [])) == 1
        checks['system_lifted_to_top_level'] = (
            adapted.get('system') == canonical['messages'][0]['content'])
        checks['no_openai_image_url_blocks_left'] = 'image_url' not in json.dumps(adapted)
    else:
        checks['message_count_preserved'] = (
            len(adapted.get('messages', [])) == len(canonical['messages']))
    return {'status': 'pass' if all(checks.values()) else 'failed', 'provider': provider,
            'checks': checks, 'wire_request_sha256': digest(adapted),
            'wire_fields': sorted(adapted),
            'dropped_params': adapter_dropped_params(provider, canonical),
            'model_called': False, 'network_calls': 0}


def inspect_window(session, capabilities):
    messages = session.model_messages(model_capabilities=capabilities_payload(capabilities))
    if messages[0]['role'] != 'system' or not isinstance(messages[0]['content'], str):
        raise HostError('PB model_messages returned an unexpected system message')
    content = messages[-1]['content']
    if not isinstance(content, list) or not content or content[0].get('type') != 'text':
        raise HostError('PB image-mode request must start with the ordered state text block')
    state = json.loads(content[0]['text'])
    gui_state = [item for item in state.get('passive', []) if 'packet_id' in item
                 and 'text_event' not in item]
    pending = None
    images = []
    for block in content[1:]:
        kind = block.get('type')
        if kind == 'text':
            if pending is not None:
                raise HostError(f'GUI event {pending} has no image before the next text block')
            identifier = json.loads(block['text'])
            if set(identifier) != {'packet_id'}:
                raise HostError('image-mode GUI text block must contain only packet_id; caption fallback detected')
            pending = identifier['packet_id']
        elif kind == 'image_url':
            if pending is None:
                raise HostError('image attachment without a preceding packet id block')
            decoded = decode_data_url(block, f'image for {pending}')
            images.append({'packet_id': pending, **decoded})
            pending = None
        else:
            raise HostError(f'unexpected content block type {kind!r}')
    if pending is not None:
        raise HostError(f'GUI event {pending} has no image attachment')
    if len(images) != len(gui_state):
        raise HostError('state passive GUI count does not match attached image count')
    if [item['packet_id'] for item in gui_state] != [image['packet_id'] for image in images]:
        raise HostError('attached image order does not match the ordered GUI event list')
    return {'request': messages,
            'state': {'delivered_count': state['passive_window']['delivered_count'],
                      'eof': state['passive_window']['eof'],
                      'phase': state['phase'], 'current_time': state['current_time']},
            'gui_packet_ids': [image['packet_id'] for image in images], 'images': images,
            'request_sha256': digest(messages)}


def inspect_session(pb_root, stage_cluster, episode_id, capabilities, *, full):
    pb_runtime = import_pb_runtime(pb_root)
    session = pb_runtime.Session(episode_id, stage_cluster, gui_mode=IMAGE_MODE)
    if session.gui_mode != IMAGE_MODE:
        raise HostError('PB Session did not stay in image mode')
    for bad in ({'input_modalities': ['text'], 'max_images_per_request': 64}, None):
        try:
            if bad is None:
                session.model_messages()
            else:
                session.model_messages(model_capabilities=bad)
        except ValueError as exc:
            if 'no summary fallback' not in str(exc):
                raise HostError(f'unexpected capability guard message: {exc}')
        else:
            raise HostError('PB image mode accepted a request without declared image capacity; '
                            'summary fallback risk')
    session.start_rounds()
    windows = []
    while True:
        windows.append(inspect_window(session, capabilities))
        if not full or session.phase != 'streaming':
            break
        session.next_packet()
    return {'runtime_file': str(Path(pb_runtime.__file__).resolve()),
            'runtime_sha256': sha256_file(Path(pb_runtime.__file__)),
            'window_size': session.window_size, 'windows': windows,
            'search_budget': session.contract['search_budget']}


def verify_bound_images(inspect, binding, *, require_all=True):
    problems = []
    seen = set()
    attachments = 0
    for window in inspect['windows']:
        for image in window['images']:
            attachments += 1
            frame = binding.get(image['packet_id'])
            if frame is None:
                problems.append(f'unbound image packet {image["packet_id"]}')
                continue
            seen.add(image['packet_id'])
            if image['sha256'] != frame['sha256']:
                problems.append(f'image {image["packet_id"]} sha256 {image["sha256"]} '
                                f'!= bound device frame {frame["sha256"]}')
    missing = sorted(set(binding) - seen)
    if require_all and missing:
        problems.append(f'bound frames never attached in replay: {missing}')
    return {'attachments': attachments, 'distinct_frames': len(seen), 'missing': missing,
            'problems': problems}


def source_snapshot(pb_root, output_dir):
    copied = []
    for relative in PB_SOURCE_FILES:
        source = Path(pb_root) / relative
        if not source.is_file():
            raise HostError(f'PB source file is missing: {relative}')
        destination = Path(output_dir) / 'source_snapshot' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append({'path': relative, 'sha256': sha256_file(destination),
                       'source_sha256': sha256_file(source)})
    prompts = pb.load_json(Path(pb_root) / 'benchmark.json', 'benchmark').get('prompts', {})
    for key, ref in prompts.items():
        source = pb.resolve_ref(Path(pb_root), [('pb-root', Path(pb_root))], ref, f'prompt {key}')
        destination = Path(output_dir) / 'source_snapshot' / ref
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append({'path': ref, 'sha256': sha256_file(destination),
                       'source_sha256': sha256_file(source)})
    return copied


def pb_entry_hashes(pb_root):
    hashes = {}
    for relative in PB_SOURCE_FILES:
        path = Path(pb_root) / relative
        if path.is_file():
            hashes[relative] = sha256_file(path)
    prompts = pb.load_json(Path(pb_root) / 'benchmark.json', 'benchmark').get('prompts', {})
    for key, ref in prompts.items():
        try:
            hashes[f'prompts/{key}'] = sha256_file(
                pb.resolve_ref(Path(pb_root), [('pb-root', Path(pb_root))], ref, f'prompt {key}'))
        except Exception:
            hashes[f'prompts/{key}'] = None
    return hashes


def command_preflight(args):
    report_path = Path(args.release_report).resolve()
    report = load_release_report(report_path)
    capture = verify_capture_bundle(args.capture_dir, report)
    source = locate_pb_episode(args.pb_root, report['episode_scope']['cluster_id'],
                               report['episode_scope']['episode_id'])
    stream = load_pb_stream(source)
    alignment = check_report_alignment(report, source, stream)
    binding = check_device_frames(report, capture, stream)
    contract = pb.load_json(Path(args.pb_root) / 'prompts' / 'agent_contract.json',
                            'agent contract')
    search_budget = require_positive_int(contract.get('search_budget'), 'contract search_budget')
    window_counts = simulate_window_counts(stream['packets'], stream['window_size'])
    max_images_per_window = max(window_counts)
    capabilities = load_capabilities(args.capabilities, args.model) if args.capabilities else None
    adapter = adapter_dry_run(capabilities)
    probe = None
    if args.endpoint:
        probe = probe_endpoint(args.endpoint, args.model, args.timeout, args.api_key_env,
                               provider=(capabilities or {}).get('provider') or PROVIDER_OPENAI,
                               anthropic_version=(capabilities or {}).get('anthropic_version'))
    gaps = live_gaps(capabilities, probe, max_images_per_window, args.model, adapter=adapter)
    bundle_sha = digest([{'packet_id': packet_id, 'sha256': binding[packet_id]['sha256']}
                         for packet_id in sorted(binding)])
    stage = {'materialized': False}
    session_check = {'session_constructs': False}
    if args.stage_dir:
        stage_result = build_stage(args.stage_dir, args.pb_root, source, stream, binding,
                                   report, {'report_sha256': sha256_file(report_path),
                                            'receipt_sha256': capture['receipt_sha256'],
                                            'plan_sha256': capture['plan_sha256']})
        stage_inside = Path(stage_result['stage_dir']).resolve().is_relative_to(PROJECT)
        stage = {'materialized': True,
                 'ref': (relative_to_or_error(stage_result['stage_dir'], PROJECT, '--stage-dir')
                         if stage_inside else None),
                 'manifest_sha256': stage_result['manifest_sha256'],
                 'files': len(stage_result['entries']),
                 'device_frames': len(binding)}
        if capabilities is not None and capabilities['max_images_per_request'] >= max_images_per_window:
            inspected = inspect_session(args.pb_root, stage_result['cluster_dir'],
                                        report['episode_scope']['episode_id'], capabilities, full=False)
            bound = verify_bound_images(inspected, binding, require_all=False)
            if bound['problems']:
                raise HostError('staged session probe failed: ' + '; '.join(bound['problems']))
            session_check = {'session_constructs': True, 'window_size': inspected['window_size'],
                             'first_window_images': bound['attachments'],
                             'first_window_packets': inspected['windows'][0]['gui_packet_ids'],
                             'first_window_shas': {image['packet_id']: image['sha256']
                                                   for image in inspected['windows'][0]['images']},
                             'summary_fallback': SUMMARY_FALLBACK,
                             'runtime_sha256': inspected['runtime_sha256']}
    receipt = {
        'schema_version': HOST_SCHEMA, 'status': HOST_STATUS, 'command': 'preflight',
        'generated_at': now_utc(),
        'episode_scope': report['episode_scope'],
        'bindings': {
            'release_report': {'schema_version': RELEASE_SCHEMA, 'sha256': sha256_file(report_path),
                               'status': report['status'], 'frames': len(report['frames'])},
            'capture_receipt': {'schema_version': capture['receipt']['schema_version'],
                                'sha256': capture['receipt_sha256'],
                                'status': capture['receipt']['status'],
                                'reset_verified': capture['receipt']['reset_verified'],
                                'capture_run_id': capture['receipt'].get('capture_run_id'),
                                'frames': len(capture['frames'])},
            'capture_plan': {'schema_version': 'pb-gui-private-capture-plan.v1',
                             'sha256': capture['plan_sha256']},
            'pb': {'cluster_json_sha256': sha256_file(Path(source['cluster_dir']) / 'cluster.json'),
                   'manifest_sha256': sha256_file(Path(source['cluster_dir']) / 'episodes'
                                                  / report['episode_scope']['episode_id'] / 'manifest.json'),
                   'stream_sha256': stream['stream_sha256'],
                   'entry': pb_entry_hashes(args.pb_root)},
            'device_bundle': {'sha256': bundle_sha, 'mode': IMAGE_MODE,
                              'frames': len(binding)},
        },
        'pb_invariants': {
            'gui_mode': IMAGE_MODE, 'window_size': stream['window_size'],
            'search_budget': search_budget, 'summary_fallback': SUMMARY_FALLBACK,
            'pb_gui_packets': len(stream['gui_packets']), 'device_frames': len(binding),
            'max_images_per_window': max_images_per_window,
            'window_image_counts': window_counts,
            'pb_passive_release_status': source['passive'].get('release_status'),
            'pb_passive_provenance': source['passive'].get('provenance'),
            'pb_world_variant_id': source['manifest'].get('world_variant_id'),
            'image_capacity_declared': (capabilities or {}).get('max_images_per_request'),
        },
        'frames': [{'packet_id': frame['packet_id'], 'frame_id': frame['frame_id'],
                    'source': frame['source'],
                    'device_sha256': frame['sha256'], 'bytes': frame['bytes'],
                    'width': frame['width'], 'height': frame['height'],
                    'pb_declared_sha256': next(packet['declared_sha256']
                                               for packet in stream['gui_packets']
                                               if packet['packet_id'] == frame['packet_id']),
                    'staged_ref': f'passive_inputs/{source["manifest"]["passive_id"]}/gui/'
                                  f'{frame["packet_id"]}.png'}
                   for frame in capture['frames']],
        'capability_declaration': capabilities,
        'adapter_preflight': adapter,
        'stage': stage,
        'live_readiness': {'status': 'ready' if not gaps else 'blocked',
                           'endpoint': args.endpoint, 'model': args.model,
                           'provider': (capabilities or {}).get('provider'),
                           'adapter_preflight': adapter.get('status') if adapter else None,
                           'probe': probe, 'capability': capabilities, 'gaps': gaps},
        'gaps': gaps,
        'checks': {'release_chain_verified': True,
                   'frames_rehashed': len(capture['frames']),
                   'pb_gui_packets': len(stream['gui_packets']),
                   'report_covers_pb_gui': alignment['report_packets'] == alignment['pb_gui_packets'],
                   'device_frames_differ_from_pb': True,
                   'pb_original_pngs_read': 0, 'pb_writes': 0,
                   'images_copied_into_receipt': 0,
                   'stage_materialized': stage['materialized'],
                   'session_probe': session_check.get('session_constructs', False),
                   'adapter_preflight_pass': bool(adapter and adapter.get('status') == 'pass'),
                   'max_images_per_window': max_images_per_window,
                   'capacity_sufficient': bool(capabilities and
                                               capabilities['max_images_per_request']
                                               >= max_images_per_window)},
        'claims': {'model_called': False, 'live_model_run': False, 'pb_evaluate_run': False,
                   'gold': False, 'published': False, 'device_writes': False},
    }
    private_hits = scan_private(receipt)
    if private_hits:
        raise HostError(f'host receipt would carry private fields: {private_hits}')
    output = ensure_outside(args.output, args.pb_root, '--output')
    write_json(output, receipt)
    print(json.dumps({'status': receipt['status'], 'receipt': str(output),
                      'frames': len(binding), 'max_images_per_window': max_images_per_window,
                      'live_readiness': receipt['live_readiness']['status'],
                      'gaps': gaps, 'model_called': False}, ensure_ascii=False))
    if args.require_live and gaps:
        print(f'live run blocked (fail closed): {", ".join(gaps)}', file=sys.stderr)
        return 2
    return 0


def load_host_receipt(path):
    if not Path(path).is_file():
        raise HostError(f'host receipt not found: {path}')
    receipt = pb.load_json(Path(path), 'host receipt')
    require_object(receipt, 'host receipt')
    if receipt.get('schema_version') != HOST_SCHEMA or receipt.get('status') != HOST_STATUS:
        raise HostError(f'host receipt must be {HOST_SCHEMA} with status={HOST_STATUS!r}')
    return receipt


def load_replay_receipt(path):
    if not Path(path).is_file():
        raise HostError(f'replay receipt not found: {path}')
    replay = pb.load_json(Path(path), 'replay receipt')
    require_object(replay, 'replay receipt')
    if replay.get('schema_version') != REPLAY_SCHEMA or replay.get('status') != REPLAY_STATUS:
        raise HostError(f'replay receipt must be {REPLAY_SCHEMA} with status={REPLAY_STATUS!r}')
    if replay.get('mode') != IMAGE_MODE or replay.get('summary_fallbacks') != SUMMARY_FALLBACK:
        raise HostError('replay receipt must be image mode with zero summary fallbacks')
    checks = require_object(replay.get('checks'), 'replay receipt checks')
    if checks.get('all_bound_frames_attached') is not True:
        raise HostError('replay receipt did not attach every bound device frame')
    return replay


def resolve_stage_dir(receipt, explicit):
    """A host receipt records the stage ref project-relative (see build_stage)."""
    if explicit:
        return Path(explicit).resolve()
    ref = receipt['stage'].get('ref')
    if not ref:
        raise HostError('host receipt does not record a reusable stage; pass --stage-dir')
    stage_dir = (PROJECT / ref).resolve()
    if not stage_dir.is_relative_to(PROJECT):
        raise HostError('host receipt stage ref escapes the simulator-extension project')
    return stage_dir


def verify_stage(stage_dir, receipt):
    stage_dir = Path(stage_dir).resolve()
    manifest_path = stage_dir / 'stage-manifest.json'
    if not manifest_path.is_file():
        raise HostError('stage directory has no stage-manifest.json')
    if sha256_file(manifest_path) != receipt['stage']['manifest_sha256']:
        raise HostError('stage manifest bytes drifted from the host receipt')
    manifest = pb.load_json(manifest_path, 'stage manifest')
    if manifest.get('schema_version') != STAGE_SCHEMA:
        raise HostError(f'stage manifest schema_version must be {STAGE_SCHEMA!r}')
    if manifest.get('episode_scope') != receipt['episode_scope']:
        raise HostError('stage manifest episode scope drifted from the host receipt')
    for entry in manifest['entries']:
        path = (stage_dir / entry['path']).resolve()
        if not path.is_relative_to(stage_dir) or not path.is_file():
            raise HostError(f'stage entry is missing or escapes the stage: {entry["path"]}')
        if sha256_file(path) != entry['sha256']:
            raise HostError(f'stage entry drifted: {entry["path"]}')
    required = {entry['path'] for entry in manifest['entries']}
    if manifest_passive_ref_from_manifest(required) is None:
        raise HostError('stage manifest does not include the passive stream')
    return {'stage_dir': stage_dir, 'manifest': manifest,
            'cluster_dir': stage_dir / manifest_cluster_rel(required),
            'binding': {entry['path']: entry for entry in manifest['entries']}}


def manifest_cluster_rel(paths):
    for path in paths:
        parts = Path(path).parts
        if len(parts) >= 3 and parts[-1] == 'cluster.json':
            return Path(*parts[:-1])
    raise HostError('stage manifest has no cluster.json entry')


def manifest_passive_ref_from_manifest(paths):
    for path in paths:
        if 'passive_inputs' in Path(path).parts and path.endswith('stream.jsonl'):
            return path
    return None


def stage_frame_binding(receipt, stage_view):
    frames = {entry['packet_id']: entry for entry in receipt['frames']}
    staged = {}
    for path, entry in stage_view['binding'].items():
        if entry['role'] != 'device_frame':
            continue
        packet_id = Path(path).stem
        if packet_id not in frames:
            raise HostError(f'stage carries an unbound device frame {packet_id}')
        staged[packet_id] = {'sha256': entry['sha256'], 'path': path,
                             'device_sha256': frames[packet_id]['device_sha256']}
    if sorted(staged) != sorted(frames):
        raise HostError('stage device frames do not equal the host receipt frames')
    for packet_id, entry in staged.items():
        if entry['sha256'] != entry['device_sha256']:
            raise HostError(f'stage frame {packet_id} does not carry the bound device frame bytes')
    return {packet_id: {'sha256': frame['device_sha256'], 'staged_ref': frame['staged_ref'],
                        'frame_id': frame['frame_id'], 'source': frame['source']}
            for packet_id, frame in frames.items()}


def replay_capacity(args, receipt, required):
    capacity = None
    source = None
    if args.capabilities:
        capabilities = load_capabilities(args.capabilities, receipt['live_readiness']['model'])
        capacity = capabilities['max_images_per_request']
        source = 'capability declaration'
    elif args.image_capacity is not None:
        capacity = args.image_capacity
        source = '--image-capacity'
    else:
        declared = receipt.get('live_readiness', {}).get('capability')
        if declared:
            capacity = declared['max_images_per_request']
            source = 'host receipt capability declaration'
    if capacity is None:
        raise HostError('image replay requires --image-capacity or --capabilities; no summary fallback')
    require_positive_int(capacity, 'image capacity')
    if capacity < required:
        raise HostError(f'declared image capacity {capacity} < required {required} for this Episode')
    return {'input_modalities': ['text', 'image'], 'max_images_per_request': capacity,
            'source': source}


def command_replay(args):
    receipt_path = Path(args.host_receipt).resolve()
    receipt = load_host_receipt(receipt_path)
    stage = verify_stage(resolve_stage_dir(receipt, args.stage_dir), receipt)
    binding = stage_frame_binding(receipt, stage)
    required = receipt['pb_invariants']['max_images_per_window']
    capabilities = replay_capacity(args, receipt, required)
    if pb_entry_hashes(args.pb_root) != receipt['bindings']['pb']['entry']:
        raise HostError('PB code/prompt bytes drifted from the host receipt; rebuild the stage')
    inspected = inspect_session(args.pb_root,
                                stage['cluster_dir'], receipt['episode_scope']['episode_id'],
                                capabilities, full=True)
    bound = verify_bound_images(inspected, binding)
    if bound['problems']:
        raise HostError('replay image binding failed: ' + '; '.join(bound['problems']))
    if inspected['window_size'] != receipt['pb_invariants']['window_size']:
        raise HostError('replay window size drifted from the host receipt')
    if len(inspected['windows']) != len(receipt['pb_invariants']['window_image_counts']):
        raise HostError('replay request count drifted from the host receipt')
    images_per_window = [len(window['images']) for window in inspected['windows']]
    if images_per_window != receipt['pb_invariants']['window_image_counts']:
        raise HostError('replay per-window image counts drifted from the host receipt')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise HostError(f'replay receipt already exists; refusing to overwrite {output}')
    windows_path = Path(args.windows_output) if args.windows_output else output.with_suffix('.windows.jsonl')
    if windows_path.exists():
        raise HostError(f'window log already exists; refusing to overwrite {windows_path}')
    with windows_path.open('x', encoding='utf8') as out:
        for index, window in enumerate(inspected['windows']):
            out.write(json.dumps({'request_index': index + 1,
                                  'delivered_count': window['state']['delivered_count'],
                                  'phase': window['state']['phase'], 'eof': window['state']['eof'],
                                  'current_time': window['state']['current_time'],
                                  'gui_packet_ids': window['gui_packet_ids'],
                                  'image_sha256s': {image['packet_id']: image['sha256']
                                                    for image in window['images']},
                                  'request_sha256': window['request_sha256']}, ensure_ascii=False) + '\n')
    replay_receipt = {
        'schema_version': REPLAY_SCHEMA, 'status': REPLAY_STATUS, 'command': 'replay',
        'generated_at': now_utc(),
        'episode_scope': receipt['episode_scope'],
        'bindings': {'host_receipt': {'schema_version': HOST_SCHEMA,
                                      'sha256': sha256_file(receipt_path)},
                     'bundle': dict(receipt['bindings']['device_bundle']),
                     'stage_manifest_sha256': receipt['stage']['manifest_sha256']},
        'mode': IMAGE_MODE, 'window_size': inspected['window_size'],
        'capability': capabilities,
        'request_count': len(inspected['windows']),
        'windows_log': relative_to_or_error(windows_path, output.parent, 'windows log'),
        'image_attachments': bound['attachments'],
        'distinct_frames': bound['distinct_frames'],
        'max_images_per_request': max(images_per_window),
        'summary_fallbacks': SUMMARY_FALLBACK,
        'checks': {'gui_blocks_carry_only_packet_id': True,
                   'attached_bytes_match_device_frames': True,
                   'all_bound_frames_attached': not bound['missing'],
                   'full_window_warmup': True,
                   'summary_fallback_refused_without_capacity': True,
                   'pb_source_snapshot_sha256': receipt['bindings']['pb']['entry'],
                   'images_copied': 0},
        'claims': {'model_called': False, 'live_model_run': False, 'pb_evaluate_run': False,
                   'gold': False, 'published': False, 'device_writes': False},
    }
    write_json(output, replay_receipt)
    print(json.dumps({'status': replay_receipt['status'], 'receipt': str(output),
                      'windows': replay_receipt['request_count'],
                      'image_attachments': replay_receipt['image_attachments'],
                      'distinct_frames': replay_receipt['distinct_frames'],
                      'model_called': False}, ensure_ascii=False))
    return 0


def image_bundle_dir_name(scope):
    return ('pb-gui-passive-image-bundle-'
            f'{scope["cluster_id"]}-{scope["episode_id"]}-{scope["passive_id"]}-v1')


def read_staged_image_refs(stream_path, binding, frames):
    """The rewritten PB image references the staged view now carries."""
    refs = {}
    for line in Path(stream_path).read_text(encoding='utf8').splitlines():
        if not line.strip():
            continue
        packet = json.loads(line)
        packet_id = packet.get('packet_id')
        if packet_id is None:
            raise HostError('staged stream packet has no packet_id')
        if packet_id not in binding:
            continue
        if packet.get('tag') != 'gui':
            raise HostError(f'bound packet {packet_id} is not a GUI packet in the staged stream')
        declared = require_hex64(packet.get('screenshot_sha256'),
                                 f'staged {packet_id} screenshot_sha256')
        if declared != binding[packet_id]['sha256']:
            raise HostError(f'staged image reference {packet_id} does not carry the bound '
                            'device frame')
        if packet.get('screenshot_ref') != frames[packet_id]['staged_ref']:
            raise HostError(f'staged image reference {packet_id} drifted from the host receipt ref')
        refs[packet_id] = {'packet_id': packet_id, 'sequence': packet.get('sequence'),
                           'tag': 'gui', 'source': packet.get('source'),
                           'observed_at': packet.get('observed_at'),
                           'delivered_at': packet.get('delivered_at'),
                           'screenshot_ref': packet.get('screenshot_ref'),
                           'screenshot_sha256': declared}
    if sorted(refs) != sorted(binding):
        raise HostError('staged stream does not carry every bound device frame reference')
    return refs


def command_writeback(args):
    """Write the verified PB image references into a persistent private bundle."""
    receipt_path = Path(args.host_receipt).resolve()
    receipt = load_host_receipt(receipt_path)
    replay_path = Path(args.replay_receipt).resolve()
    replay = load_replay_receipt(replay_path)
    replay_bindings = require_object(replay.get('bindings'), 'replay receipt bindings')
    host_binding = require_object(replay_bindings.get('host_receipt'),
                                  'replay receipt bindings.host_receipt')
    if require_hex64(host_binding.get('sha256'),
                     'replay receipt bindings.host_receipt.sha256') != sha256_file(receipt_path):
        raise HostError('replay receipt does not bind these host receipt bytes')
    if replay.get('episode_scope') != receipt['episode_scope']:
        raise HostError('replay receipt episode scope drifted from the host receipt')
    if replay_bindings.get('bundle') != receipt['bindings']['device_bundle']:
        raise HostError('replay receipt device bundle drifted from the host receipt')
    stage = verify_stage(resolve_stage_dir(receipt, args.stage_dir), receipt)
    binding = stage_frame_binding(receipt, stage)
    if pb_entry_hashes(args.pb_root) != receipt['bindings']['pb']['entry']:
        raise HostError('PB code/prompt bytes drifted from the host receipt; rebuild the stage')
    staged_stream = next((entry for entry in stage['manifest']['entries']
                          if entry['role'] == 'staged_stream'), None)
    if staged_stream is None:
        raise HostError('stage manifest has no staged stream entry')
    frames = {frame['packet_id']: frame for frame in receipt['frames']}
    frame_paths = {Path(path).stem: entry['path'] for path, entry in stage['binding'].items()
                   if entry['role'] == 'device_frame'}
    refs = read_staged_image_refs(stage['stage_dir'] / staged_stream['path'], binding, frames)
    bundle_dir = Path(args.bundle_dir) if args.bundle_dir else (
        PROJECT / 'runtime' / image_bundle_dir_name(receipt['episode_scope']))
    bundle_dir = ensure_outside(bundle_dir, args.pb_root, '--bundle-dir')
    if bundle_dir.exists() and any(bundle_dir.iterdir()):
        raise HostError(f'--bundle-dir must be empty or absent, refusing to overwrite {bundle_dir}')
    bundle_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for packet_id in sorted(binding):
        frame = frames[packet_id]
        destination = bundle_dir / 'gui' / f'{packet_id}.png'
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(stage['stage_dir'] / frame_paths[packet_id], destination)
        digest_value = sha256_file(destination)
        if digest_value != frame['device_sha256']:
            raise HostError(f'bundle frame {packet_id} does not carry the bound device frame bytes')
        entries.append({'packet_id': packet_id, 'frame_id': frame['frame_id'],
                        'source': frame['source'], 'sha256': digest_value,
                        'bytes': destination.stat().st_size, 'width': frame['width'],
                        'height': frame['height'],
                        'pb_declared_sha256': frame['pb_declared_sha256'],
                        'screenshot_ref': frame['staged_ref'],
                        'bundle_path': f'gui/{packet_id}.png'})
    refs_path = bundle_dir / 'image-refs.jsonl'
    with refs_path.open('x', encoding='utf8') as out:
        for packet_id in sorted(refs):
            out.write(json.dumps(refs[packet_id], ensure_ascii=False) + '\n')
    files = [{'path': entry['bundle_path'], 'sha256': entry['sha256'], 'bytes': entry['bytes']}
             for entry in entries]
    files.append({'path': 'image-refs.jsonl', 'sha256': sha256_file(refs_path),
                  'bytes': refs_path.stat().st_size})
    manifest = {
        'schema_version': BUNDLE_SCHEMA, 'status': BUNDLE_STATUS, 'generated_at': now_utc(),
        'bundle_id': image_bundle_dir_name(receipt['episode_scope']),
        'episode_scope': receipt['episode_scope'],
        'writeback': {'mode': 'extension_private_bundle', 'pb_tree_modified': False,
                      'pb_writes': 0, 'device_frames': len(binding), 'image_refs': len(refs)},
        'bindings': {'release_report': receipt['bindings']['release_report'],
                     'capture_receipt': receipt['bindings']['capture_receipt'],
                     'capture_plan': receipt['bindings']['capture_plan'],
                     'host_receipt': {'schema_version': HOST_SCHEMA,
                                      'sha256': sha256_file(receipt_path)},
                     'replay_receipt': {'schema_version': REPLAY_SCHEMA,
                                        'sha256': sha256_file(replay_path),
                                        'status': replay['status'],
                                        'image_attachments': replay.get('image_attachments')},
                     'pb': receipt['bindings']['pb'],
                     'device_bundle': receipt['bindings']['device_bundle']},
        'frames': entries,
        'files': files,
        'counts': {'frames': len(entries), 'image_refs': len(refs), 'files': len(files),
                   'bytes': sum(entry['bytes'] for entry in files)},
        'checks': {'release_chain_verified': True, 'host_receipt_verified': True,
                   'replay_verified': True, 'stage_verified': True,
                   'refs_match_device_frames': len(refs),
                   'device_frames_differ_from_pb': all(entry['sha256'] !=
                                                       entry['pb_declared_sha256']
                                                       for entry in entries),
                   'pb_writes': 0, 'pb_original_pngs_read': 0, 'images_copied_from_pb': 0},
        'claims': {'model_called': False, 'live_model_run': False, 'pb_evaluate_run': False,
                   'gold': False, 'published': False, 'device_writes': False,
                   'pb_tree_modified': False},
    }
    private_hits = scan_private(manifest)
    if private_hits:
        raise HostError(f'image bundle manifest would carry private fields: {private_hits}')
    manifest_path = write_json(bundle_dir / 'manifest.json', manifest)
    print(json.dumps({'status': manifest['status'], 'bundle': str(bundle_dir),
                      'manifest': str(manifest_path), 'frames': len(entries),
                      'image_refs': len(refs), 'pb_writes': 0}, ensure_ascii=False))
    return 0


def live_payload(messages, model, max_tokens, seed):
    if not isinstance(messages, list) or not messages:
        raise HostError('model_messages returned no messages')
    return {'model': model, 'messages': copy.deepcopy(messages), 'max_tokens': max_tokens,
            'temperature': 0.0, 'enable_thinking': False, 'seed': seed, 'stream': False}


def parse_command(text):
    if not isinstance(text, str):
        raise HostError('model response content is not text')
    command = json.loads(text.strip())
    if not isinstance(command, dict):
        raise HostError('model response must be one JSON action object')
    return command


def request_model(endpoint, payload, timeout, api_key_env=None, provider=PROVIDER_OPENAI,
                  anthropic_version=None):
    profile = PROVIDER_PROFILES[provider]
    headers, _ = provider_headers(provider, api_key_env, version=anthropic_version)
    response, error = http_json(endpoint.rstrip('/') + profile['request_path'],
                                payload=payload, headers=headers, timeout=timeout)
    if error:
        raise HostError(f'model request failed: {error}')
    if not isinstance(response, dict):
        raise HostError('model endpoint returned a non-object response')
    return response


def request_fingerprint(payload):
    images = []
    for message in payload['messages']:
        content = message.get('content')
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'image_url':
                    url = block.get('image_url', {}).get('url', '')
                    raw = base64.b64decode(url.split(',', 1)[-1]) if ',' in url else b''
                    images.append(sha256_bytes(raw))
    return {'request_sha256': digest(payload), 'image_count': len(images),
            'image_sha256s': images, 'message_roles': [message.get('role') for message in payload['messages']]}


def verify_request_against_binding(payload, binding, seen):
    """Refuse to send any live request whose images are not the bound device frames."""
    content = payload['messages'][-1]['content']
    if not isinstance(content, list) or not content or content[0].get('type') != 'text':
        raise HostError('live request does not start with the ordered state text block')
    state = json.loads(content[0]['text'])
    gui_state = [item['packet_id'] for item in state.get('passive', [])
                 if 'packet_id' in item and 'text_event' not in item]
    pending = None
    order = []
    for block in content[1:]:
        if block.get('type') == 'text':
            if pending is not None:
                raise HostError(f'live request: GUI event {pending} has no image attachment')
            identifier = json.loads(block['text'])
            if set(identifier) != {'packet_id'}:
                raise HostError('live request GUI block must contain only packet_id; caption fallback detected')
            pending = identifier['packet_id']
        elif block.get('type') == 'image_url':
            if pending is None:
                raise HostError('live request: image attachment without a packet id block')
            raw = base64.b64decode(block['image_url']['url'].split(',', 1)[-1], validate=True)
            identifier = pending
            pending = None
            frame = binding.get(identifier)
            if frame is None:
                raise HostError(f'live request carries an unbound image packet {identifier}')
            if sha256_bytes(raw) != frame['sha256']:
                raise HostError(f'live request image {identifier} does not match the bound device frame')
            order.append(identifier)
            seen.add(identifier)
        else:
            raise HostError(f'live request carries an unexpected content block {block.get("type")!r}')
    if pending is not None:
        raise HostError(f'live request: GUI event {pending} has no image attachment')
    if order != gui_state:
        raise HostError('live request image order does not match the ordered GUI event list')


def command_live(args):
    receipt_path = Path(args.host_receipt).resolve()
    receipt = load_host_receipt(receipt_path)
    if receipt['live_readiness']['status'] != 'ready':
        raise HostError('host receipt live readiness is blocked: ' + ', '.join(receipt['gaps']))
    if not args.confirm_live:
        raise HostError('live model run requires --confirm-live; no live run was started')
    if not args.capabilities:
        raise HostError('live model run requires --capabilities; no implicit capability guess')
    capabilities = load_capabilities(args.capabilities, args.model)
    required = receipt['pb_invariants']['max_images_per_window']
    if capabilities['max_images_per_request'] < required:
        raise HostError(f'declared capacity {capabilities["max_images_per_request"]} < required {required}')
    provider = capabilities.get('provider')
    if provider is None:
        raise HostError('capability declaration must name provider '
                        f'({PROVIDER_OPENAI!r} or {PROVIDER_ANTHROPIC!r}); no implicit provider guess')
    if provider == PROVIDER_ANTHROPIC and args.seed:
        raise HostError(f'provider {PROVIDER_ANTHROPIC!r} cannot honor --seed; refusing a live run '
                        'instead of silently dropping it')
    adapter = adapter_dry_run(capabilities)
    if adapter.get('status') != 'pass':
        raise HostError('adapter preflight failed before any model call: '
                        + str(adapter.get('error', adapter.get('status'))))
    output = Path(args.output).resolve()
    if not output.is_relative_to(PROJECT):
        raise HostError('live output must stay inside the simulator-extension project')
    if output.exists() and any(output.iterdir()):
        raise HostError(f'live output must be a fresh directory: {output}')
    output.mkdir(parents=True, exist_ok=True)
    stage = verify_stage(resolve_stage_dir(receipt, args.stage_dir), receipt)
    binding = stage_frame_binding(receipt, stage)
    if pb_entry_hashes(args.pb_root) != receipt['bindings']['pb']['entry']:
        raise HostError('PB code/prompt bytes drifted from the host receipt; rebuild the stage')
    probe = probe_endpoint(args.endpoint, args.model, args.timeout, args.api_key_env,
                           provider=provider,
                           anthropic_version=capabilities.get('anthropic_version'))
    blocked_reason = None
    if probe.get('error'):
        blocked_reason = 'no_reachable_model_endpoint'
    elif probe.get('health_status') != 'ok':
        blocked_reason = 'model_endpoint_unhealthy'
    elif not probe.get('model_advertised'):
        blocked_reason = 'model_not_advertised'
    elif provider == PROVIDER_ANTHROPIC and not probe.get('credential_present'):
        blocked_reason = 'missing_api_key_for_anthropic'
    if blocked_reason:
        write_json(output / 'connection_failure.json',
                   {'endpoint': args.endpoint, 'model': args.model, 'provider': provider,
                    'reason': blocked_reason, 'probe': probe, 'adapter_preflight': adapter,
                    'model_called': False})
        write_json(output / 'live-receipt.json',
                   {'schema_version': LIVE_SCHEMA, 'status': LIVE_BLOCKED, 'generated_at': now_utc(),
                    'episode_scope': receipt['episode_scope'], 'endpoint': args.endpoint,
                    'model': args.model, 'provider': provider, 'gui_mode': IMAGE_MODE,
                    'reason': blocked_reason, 'probe': probe, 'adapter_preflight': adapter,
                    'host_receipt_sha256': sha256_file(receipt_path),
                    'bundle_sha256': receipt['bindings']['device_bundle']['sha256'],
                    'model_called': False,
                    'claims': {'live_model_run': False, 'gold': False, 'published': False}})
        print(json.dumps({'status': LIVE_BLOCKED, 'receipt': str(output / 'live-receipt.json'),
                          'reason': blocked_reason, 'model_called': False}, ensure_ascii=False))
        return 2
    snapshot = source_snapshot(args.pb_root, output)
    drifted = [item for item in snapshot if item['sha256'] != item['source_sha256']]
    if drifted:
        raise HostError('source snapshot drifted while copying')
    config = {'created_utc': now_utc(), 'endpoint': args.endpoint, 'model': args.model,
              'provider': provider, 'gui_mode': IMAGE_MODE,
              'episode': receipt['episode_scope']['episode_id'],
              'cluster': receipt['episode_scope']['cluster_id'],
              'passive_id': receipt['episode_scope']['passive_id'],
              'host_receipt_sha256': sha256_file(receipt_path),
              'bundle_sha256': receipt['bindings']['device_bundle']['sha256'],
              'capability': capabilities, 'adapter_preflight': adapter,
              'temperature': 0.0, 'enable_thinking': False,
              'seed': args.seed, 'max_tokens': args.max_tokens, 'max_turns': args.max_turns,
              'scope': 'PB image-mode live model over a staged device-frame input view; '
                       'no Android or GUI execution, no summary fallback',
              'source_snapshot': snapshot}
    write_json(output / 'config.json', config)
    pb_runtime = import_pb_runtime(args.pb_root)
    session = pb_runtime.Session(receipt['episode_scope']['episode_id'], stage['cluster_dir'],
                                 gui_mode=IMAGE_MODE)
    if session.gui_mode != IMAGE_MODE:
        raise HostError('PB Session did not stay in image mode')
    calls = []
    live_seen = set()
    usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    started = time.monotonic()

    def decide(messages):
        payload = live_payload(messages, args.model, args.max_tokens, args.seed)
        verify_request_against_binding(payload, binding, live_seen)
        fingerprint = request_fingerprint(payload)
        wire_payload = adapt_request(provider, payload)
        entry = {'call': len(calls) + 1, 'at': session.now, 'request': fingerprint,
                 'wire': {'provider': provider,
                          'path': PROVIDER_PROFILES[provider]['request_path'],
                          'payload_sha256': digest(wire_payload),
                          'dropped_params': adapter_dropped_params(provider, payload)}}
        tick = time.monotonic()
        try:
            response = request_model(args.endpoint, wire_payload, args.timeout, args.api_key_env,
                                     provider=provider,
                                     anthropic_version=capabilities.get('anthropic_version'))
            entry['response'] = response
            parsed = parse_response(provider, response)
            for key in usage:
                usage[key] += parsed['usage'].get(key, 0)
            command = parse_command(parsed['text'])
            entry['parsed_command'] = command
            return command
        except Exception as exc:
            entry['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            entry['seconds'] = round(time.monotonic() - tick, 3)
            calls.append(entry)
            with (output / 'model_calls.jsonl').open('a', encoding='utf8') as out:
                out.write(json.dumps(entry, ensure_ascii=False) + '\n')

    try:
        run = pb_runtime.run_agent(session, decide,
                                   model_capabilities=capabilities_payload(capabilities),
                                   max_turns=args.max_turns)
        if live_seen != set(binding):
            raise HostError('live run ended without attaching every bound frame: '
                            f'{sorted(set(binding) - live_seen)}')
    except Exception as exc:
        write_json(output / 'failed_attempt.json',
                   {'error': f'{type(exc).__name__}: {exc}', 'phase': session.phase,
                    'final': session.result, 'calls': calls,
                    'audit': session.audit_snapshot()})
        receipt_document = {'schema_version': LIVE_SCHEMA, 'status': LIVE_FAILED,
                            'generated_at': now_utc(), 'episode_scope': receipt['episode_scope'],
                            'endpoint': args.endpoint, 'model': args.model, 'provider': provider,
                            'adapter_preflight': adapter,
                            'calls': len(calls), 'usage': usage, 'error': f'{type(exc).__name__}: {exc}',
                            'model_called': bool(calls),
                            'claims': {'live_model_run': bool(calls), 'gold': False, 'published': False}}
        write_json(output / 'live-receipt.json', receipt_document)
        print(json.dumps({'status': LIVE_FAILED, 'error': receipt_document['error'],
                          'calls': len(calls)}, ensure_ascii=False))
        return 1
    write_json(output / 'run.json', run)
    receipt_document = {'schema_version': LIVE_SCHEMA, 'status': LIVE_COMPLETED,
                        'generated_at': now_utc(), 'episode_scope': receipt['episode_scope'],
                        'endpoint': args.endpoint, 'model': args.model, 'provider': provider,
                        'adapter_preflight': adapter,
                        'capability': capabilities, 'gui_mode': IMAGE_MODE,
                        'calls': len(calls), 'seconds': round(time.monotonic() - started, 3),
                        'usage': usage, 'queries_used': session.used,
                        'final': run.get('final'), 'run_sha256': digest(run),
                        'host_receipt_sha256': sha256_file(receipt_path),
                        'bundle_sha256': receipt['bindings']['device_bundle']['sha256'],
                        'frames_attached': sorted(live_seen), 'model_called': True,
                        'summary_fallbacks': SUMMARY_FALLBACK,
                        'claims': {'live_model_run': True, 'pb_evaluate_run': False,
                                   'gold': False, 'published': False, 'device_writes': False}}
    write_json(output / 'live-receipt.json', receipt_document)
    print(json.dumps({'status': LIVE_COMPLETED, 'receipt': str(output / 'live-receipt.json'),
                      'calls': len(calls), 'model_called': True}, ensure_ascii=False))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    preflight = sub.add_parser('preflight', help='verify the bundle and stage a PB image input view')
    preflight.add_argument('--release-report', required=True)
    preflight.add_argument('--capture-dir', required=True)
    preflight.add_argument('--pb-root', required=True)
    preflight.add_argument('--stage-dir')
    preflight.add_argument('--output', required=True)
    preflight.add_argument('--capabilities')
    preflight.add_argument('--endpoint', default=os.environ.get(LIVE_ENDPOINT_ENV, 'http://127.0.0.1:18771'))
    preflight.add_argument('--model', default=os.environ.get(LIVE_MODEL_ENV, 'qwen27b-local'))
    preflight.add_argument('--api-key-env')
    preflight.add_argument('--timeout', type=int, default=5)
    preflight.add_argument('--require-live', action='store_true')
    preflight.set_defaults(handler=command_preflight)

    replay = sub.add_parser('replay', help='offline image-mode replay over the staged view')
    replay.add_argument('--host-receipt', required=True)
    replay.add_argument('--stage-dir')
    replay.add_argument('--output', required=True)
    replay.add_argument('--windows-output')
    replay.add_argument('--image-capacity', type=int)
    replay.add_argument('--capabilities')
    replay.add_argument('--pb-root', required=True)
    replay.set_defaults(handler=command_replay)

    writeback = sub.add_parser('writeback',
                               help='write verified image refs into a private bundle')
    writeback.add_argument('--host-receipt', required=True)
    writeback.add_argument('--replay-receipt', required=True)
    writeback.add_argument('--stage-dir')
    writeback.add_argument('--bundle-dir')
    writeback.add_argument('--pb-root', required=True)
    writeback.set_defaults(handler=command_writeback)

    live = sub.add_parser('live', help='guarded live model run over the staged view')
    live.add_argument('--host-receipt', required=True)
    live.add_argument('--stage-dir')
    live.add_argument('--output', required=True)
    live.add_argument('--pb-root', required=True)
    live.add_argument('--capabilities', required=True)
    live.add_argument('--endpoint', default=os.environ.get(LIVE_ENDPOINT_ENV, 'http://127.0.0.1:18771'))
    live.add_argument('--model', default=os.environ.get(LIVE_MODEL_ENV, 'qwen27b-local'))
    live.add_argument('--api-key-env')
    live.add_argument('--max-turns', type=int, default=128)
    live.add_argument('--max-tokens', type=int, default=2048)
    live.add_argument('--seed', type=int, default=0)
    live.add_argument('--timeout', type=int, default=180)
    live.add_argument('--confirm-live', action='store_true')
    live.set_defaults(handler=command_live)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == 'live':
            if not 1 <= args.max_turns <= 256 or not 1 <= args.max_tokens <= 4096 or args.timeout <= 0:
                raise HostError('invalid token/turn/time limits')
            if not 0 <= args.seed < 2 ** 32:
                raise HostError('seed must be an unsigned 32-bit integer')
        return args.handler(args)
    except HostError as exc:
        print(f'{parser.prog}: error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
