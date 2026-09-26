#!/usr/bin/env python3
"""Read-only PB GUI packet coverage audit across every registered episode.

The round-1..5 artifacts each answer one question for one episode; nothing yet
shows, for all registered development episodes at once, how many GUI packets a
PB episode declares, which of their sources have an extension adapter, and
which packets are already bound by the current private release / image-host
receipt. This audit is that read-only view:

* enumerate the legal benchmark -> cluster -> manifest -> passive stream
  bindings by reusing ``pb_gui_bridge.compile_episode`` (path/ID/schema/sha256
  checks, GT never read, images never copied);
* classify every ``tag=gui`` packet as ``supported`` when its source is in
  ``pb_backend_plan.SUPPORTED_SOURCES`` (calendar/mail/clock) and
  ``unsupported`` otherwise;
* optionally bind an existing ``pb-gui-release-report.v1`` (validated with
  ``pb_gui_release_gate.validate_report``) and/or
  ``pb-gui-image-host-receipt.v1`` (validated with
  ``pb_gui_image_host.load_host_receipt`` plus a PB packet/hash binding) and
  mark only those GUI packets as evidence-bound that the receipt really binds
  to the current PB manifest/stream bytes;
* optionally discover several such receipts in one ``--evidence-dir``: only
  explicitly named ``*release-report*.json`` / ``*host-receipt*.json`` files are
  read (the directory itself plus one level of per-episode bundles); any other
  JSON directly inside the directory aborts the audit, nested unrelated JSON is
  recorded as rejected, every discovered episode must be paired
  release+host, and a receipt that is also repeated by
  ``--release-report``/``--host-receipt`` still aborts;
* fail closed: receipts that are invalid, scope-drift, bind unknown packets,
  bind packets without a PB-declared screenshot, republish a PB original, or
  disagree with each other abort the audit (exit 2) instead of being ignored;
* a missing receipt is never reported as ready: ``coverage_complete`` is true
  only when every GUI packet of every audited episode is supported and
  evidence-bound, and per-episode ``status`` distinguishes
  ``covered`` / ``partial_evidence`` / ``missing_evidence`` /
  ``blocked_unsupported`` / ``no_gui_packets`` / ``invalid``.

The audit never writes inside ``--pb-root``, never opens private GT, never
copies or rewrites an image, never touches a device and never invokes scoring
or the publisher.

Usage::

    python3 scripts/pb_gui_coverage_audit.py --pb-root PB [--cluster CLUSTER] \
        [--release-report REPORT] [--host-receipt RECEIPT] \
        [--evidence-dir DIR] [--output AUDIT.json]
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pb_backend_plan as pb
import pb_gui_bridge as bridge
import pb_gui_capture_gate as capture_gate
import pb_gui_image_host as image_host
import pb_gui_release_gate as release_gate

SCHEMA = 'pb-gui-coverage-audit.v1'
GUI_TAG = bridge.GUI_TAG
STATUS_COVERED = 'covered'
STATUS_PARTIAL = 'partial_evidence'
STATUS_MISSING = 'missing_evidence'
STATUS_BLOCKED = 'blocked_unsupported'
STATUS_NO_GUI = 'no_gui_packets'
STATUS_INVALID = 'invalid'
SCOPE_REQUIRED = ('cluster_id', 'episode_id', 'passive_id', 'world_variant_id')
SCOPE_OPTIONAL = ('track', 'decision_point', 'timezone')
HOST_RECEIPT_MARKER = 'host-receipt'
RELEASE_REPORT_MARKER = 'release-report'


class AuditError(Exception):
    """A fail-closed audit condition: no report may be claimed."""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sanitize(text, root):
    return str(text).replace(str(root), '<pb-root>')


def ensure_outside(path, root, label):
    target = Path(path).expanduser().resolve()
    root = Path(root).resolve()
    if target == root or target.is_relative_to(root):
        raise AuditError(f'{label} must stay outside --pb-root: {target}')
    return target


def gui_packet_index(plan):
    return {packet['packet_id']: packet for packet in plan['packets'] if packet['tag'] == GUI_TAG}


def plan_matches_scope(plan, scope):
    for field in SCOPE_REQUIRED:
        if scope.get(field) != plan.get(field):
            return False
    for field in SCOPE_OPTIONAL:
        scope_value, plan_value = scope.get(field), plan.get(field)
        if scope_value is not None and plan_value is not None and scope_value != plan_value:
            return False
    return True


def load_release_evidence(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise AuditError(f'release report not found: {path}')
    try:
        report = pb.load_json(path, 'release report')
    except ValueError as error:
        raise AuditError(f'cannot read release report: {error}') from None
    try:
        release_gate.validate_report(report)
    except ValueError as error:
        raise AuditError(f'release report failed validation: {error}') from None
    scope = report['episode_scope']
    frames = [{'packet_id': frame['packet_id'], 'sha256': frame['sha256']}
              for frame in report['frames']]
    return {
        'kind': 'release_report', 'basename': path.name, 'sha256': digest(path),
        'schema_version': report['schema_version'], 'status': report['status'],
        'scope': {field: scope[field] for field in SCOPE_REQUIRED + SCOPE_OPTIONAL
                  if scope.get(field) is not None},
        'frames': frames,
    }


def load_host_evidence(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise AuditError(f'host receipt not found: {path}')
    try:
        receipt = image_host.load_host_receipt(path)
    except (image_host.HostError, ValueError) as error:
        raise AuditError(f'host receipt failed validation: {error}') from None
    try:
        scope = image_host.require_object(receipt.get('episode_scope'),
                                          'host receipt episode_scope')
        for field in SCOPE_REQUIRED:
            image_host.require_text(scope.get(field), f'host receipt episode_scope.{field}')
        for field in SCOPE_OPTIONAL:
            if scope.get(field) is not None:
                image_host.require_text(scope.get(field), f'host receipt episode_scope.{field}')
        bindings = image_host.require_object(receipt.get('bindings'), 'host receipt bindings')
        pb_binding = image_host.require_object(bindings.get('pb'), 'host receipt bindings.pb')
        stream_sha = image_host.require_hex64(pb_binding.get('stream_sha256'),
                                              'host receipt bindings.pb.stream_sha256')
        manifest_sha = image_host.require_hex64(pb_binding.get('manifest_sha256'),
                                                'host receipt bindings.pb.manifest_sha256')
        release_binding = bindings.get('release_report')
        if release_binding is not None:
            release_binding = image_host.require_object(release_binding,
                                                        'host receipt bindings.release_report')
            release_sha = image_host.require_hex64(
                release_binding.get('sha256'), 'host receipt bindings.release_report.sha256')
        else:
            release_sha = None
        frames = receipt.get('frames')
        if not isinstance(frames, list) or not frames:
            raise image_host.HostError('host receipt frames must be a nonempty list')
        bound, seen = [], set()
        for position, frame in enumerate(frames, start=1):
            label = f'host receipt frame #{position}'
            frame = image_host.require_object(frame, label)
            packet_id = image_host.require_text(frame.get('packet_id'), f'{label} packet_id')
            if packet_id in seen:
                raise image_host.HostError(f'host receipt repeats packet {packet_id!r}')
            seen.add(packet_id)
            bound.append({
                'packet_id': packet_id,
                'source': image_host.require_text(frame.get('source'), f'{label} source'),
                'staged_ref': image_host.require_text(frame.get('staged_ref'), f'{label} staged_ref'),
                'device_sha256': image_host.require_hex64(frame.get('device_sha256'),
                                                          f'{label} device_sha256'),
                'pb_declared_sha256': image_host.require_hex64(
                    frame.get('pb_declared_sha256'), f'{label} pb_declared_sha256'),
            })
    except (image_host.HostError, ValueError) as error:
        raise AuditError(f'host receipt failed validation: {error}') from None
    return {
        'kind': 'host_receipt', 'basename': path.name, 'sha256': digest(path),
        'schema_version': receipt['schema_version'], 'status': receipt['status'],
        'scope': {field: scope[field] for field in SCOPE_REQUIRED + SCOPE_OPTIONAL
                  if scope.get(field) is not None},
        'release_report_sha256': release_sha, 'stream_sha256': stream_sha,
        'manifest_sha256': manifest_sha, 'frames': bound,
    }


def classify_receipt_name(name):
    """Map an explicitly named receipt file to its evidence kind, else None."""
    if HOST_RECEIPT_MARKER in name:
        return 'host_receipt'
    if RELEASE_REPORT_MARKER in name:
        return 'release_report'
    return None


def evidence_candidates(directory):
    """Files in the evidence directory and its immediate subdirectories only."""
    top, nested = [], []
    try:
        entries = sorted(directory.iterdir())
    except OSError as error:
        raise AuditError(f'cannot scan evidence directory {directory}: {error}') from None
    for entry in entries:
        if entry.is_file():
            top.append(entry)
        elif entry.is_dir():
            try:
                nested.extend(sorted(child for child in entry.iterdir() if child.is_file()))
            except OSError as error:
                raise AuditError(f'cannot scan evidence bundle {entry}: {error}') from None
    return top, nested


def load_evidence_dir(directory):
    """Discover and load explicitly named receipts under ``--evidence-dir``.

    Only ``*release-report*.json`` / ``*host-receipt*.json`` files are read, at
    the top level and one level below (per-episode bundles); the scan never
    recurses into arbitrary JSON. A differently named JSON file directly in
    the directory is a hard error, while unrelated JSON next to a nested
    receipt is returned as a rejected candidate so the report can record it.
    """
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise AuditError(f'evidence directory not found: {directory}')
    top, nested = evidence_candidates(directory)
    loaded = {'release_report': [], 'host_receipt': []}
    rejected = []
    for candidate in top + nested:
        if candidate.suffix != '.json':
            continue
        kind = classify_receipt_name(candidate.name)
        if kind is None:
            if candidate in top:
                raise AuditError('--evidence-dir contains a JSON file that is not named as a '
                                 f'release report or host receipt: {candidate.name}')
            rejected.append(f'{candidate.parent.name}/{candidate.name}')
            continue
        loaded[kind].append(load_release_evidence(candidate) if kind == 'release_report'
                            else load_host_evidence(candidate))
    if not loaded['release_report'] and not loaded['host_receipt']:
        raise AuditError(f'--evidence-dir contains no named release report or host receipt: '
                         f'{directory}')
    return loaded, sorted(rejected), directory


def resolve_episode(evidence, plans):
    matches = [plan for plan in plans if plan_matches_scope(plan, evidence['scope'])]
    if not matches:
        raise AuditError(f'{evidence["kind"]} {evidence["basename"]} scope '
                         f'{evidence["scope"]} matches no registered audited episode')
    if len(matches) > 1:
        raise AuditError(f'{evidence["kind"]} {evidence["basename"]} matches several episodes')
    return matches[0]


def bind_release_evidence(evidence, plan):
    packets = gui_packet_index(plan)
    bound = {}
    for frame in evidence['frames']:
        packet_id = frame['packet_id']
        if packet_id not in packets:
            raise AuditError(f'release report {evidence["basename"]} binds packet '
                             f'{packet_id!r} that is not a GUI packet of '
                             f'{plan["cluster_id"]}/{plan["episode_id"]}')
        bound[packet_id] = {'sha256': frame['sha256'], 'source': packets[packet_id]['source']}
    return bound


def bind_host_evidence(evidence, plan):
    assets = plan['source_assets']
    if evidence['stream_sha256'] != assets['passive_stream']['sha256']:
        raise AuditError('host receipt PB stream sha256 does not match the current PB stream of '
                         f'{plan["cluster_id"]}/{plan["episode_id"]}')
    if evidence['manifest_sha256'] != assets['manifest']['sha256']:
        raise AuditError('host receipt PB manifest sha256 does not match the current PB manifest of '
                         f'{plan["cluster_id"]}/{plan["episode_id"]}')
    packets = gui_packet_index(plan)
    bound = {}
    for frame in evidence['frames']:
        packet_id = frame['packet_id']
        packet = packets.get(packet_id)
        if packet is None:
            raise AuditError(f'host receipt {evidence["basename"]} binds packet {packet_id!r} that '
                             f'is not a GUI packet of {plan["cluster_id"]}/{plan["episode_id"]}')
        if frame['source'] != packet['source']:
            raise AuditError(f'host receipt packet {packet_id!r} source {frame["source"]!r} does not '
                             f'match PB source {packet["source"]!r}')
        declared = packet.get('screenshot_sha256')
        if declared is None:
            raise AuditError(f'host receipt binds packet {packet_id!r} that has no PB-declared '
                             'screenshot; the frame cannot be bound to this PB stream')
        if frame['pb_declared_sha256'] != declared:
            raise AuditError(f'host receipt packet {packet_id!r} pb_declared_sha256 does not match '
                             'the current PB stream declaration')
        if frame['device_sha256'] == declared:
            raise AuditError(f'host receipt packet {packet_id!r} carries the PB original hash as a '
                             'device frame; a republished PB screenshot is not a capture')
        expected = {packet.get('screenshot_ref'),
                    f'passive_inputs/{plan["passive_id"]}/gui/{packet_id}.png'}
        if frame['staged_ref'] not in expected:
            raise AuditError(f'host receipt packet {packet_id!r} staged_ref '
                             f'{frame["staged_ref"]!r} is not the PB screenshot binding')
        bound[packet_id] = {'sha256': frame['device_sha256'], 'source': packet['source']}
    return bound


def receipt_list(value):
    """Normalize a receipts slot (None / single evidence / list) to a list."""
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def bind_receipts(receipts, plans, require_pairs=False):
    """Bind every provided receipt to exactly one audited episode (fail closed).

    Legacy callers pass at most one receipt per kind, keeping the singular
    ``release_report``/``host_receipt`` summaries. Directory callers pass lists
    and set ``require_pairs``: every episode with evidence must then carry both
    a release report and a host receipt that cross-bind.
    """
    index, pairs = {}, {}
    summary_lists = {'release_report': [], 'host_receipt': []}
    for kind in ('release_report', 'host_receipt'):
        for evidence in receipt_list(receipts.get(kind)):
            plan = resolve_episode(evidence, plans)
            bound = (bind_release_evidence(evidence, plan) if kind == 'release_report'
                     else bind_host_evidence(evidence, plan))
            key = (plan['cluster_id'], plan['episode_id'])
            entry = index.setdefault(key, {})
            if kind in entry:
                raise AuditError(f'{kind} binds {key[0]}/{key[1]} twice')
            entry[kind] = bound
            pairs.setdefault(key, {})[kind] = evidence
            summary_lists[kind].append({
                'basename': evidence['basename'], 'sha256': evidence['sha256'],
                'schema_version': evidence['schema_version'], 'status': evidence['status'],
                'episode_scope': evidence['scope'], 'frames': len(bound),
                'packets': sorted(bound),
            })
    for key in sorted(index):
        entry = index[key]
        if 'release_report' in entry and 'host_receipt' in entry:
            release, host = pairs[key]['release_report'], pairs[key]['host_receipt']
            if host['release_report_sha256'] != release['sha256']:
                raise AuditError('host receipt is not bound to the provided release report bytes')
            release_bound, host_bound = entry['release_report'], entry['host_receipt']
            if not set(host_bound) <= set(release_bound):
                raise AuditError('host receipt frames are not a subset of the release report frames')
            for packet_id in sorted(set(host_bound) & set(release_bound)):
                if host_bound[packet_id]['sha256'] != release_bound[packet_id]['sha256']:
                    raise AuditError(f'host receipt and release report disagree on packet '
                                     f'{packet_id!r} frame sha256')
        elif require_pairs:
            have = 'release report' if 'release_report' in entry else 'host receipt'
            missing = 'host receipt' if have == 'release report' else 'release report'
            raise AuditError(f'--evidence-dir evidence for {key[0]}/{key[1]} has a {have} '
                             f'but no paired {missing}')
    if not require_pairs and summary_lists['release_report'] and summary_lists['host_receipt'] \
            and len(index) > 1:
        raise AuditError('release report and host receipt scope different episodes')
    summaries = {kind: (summary_lists[kind][0] if len(summary_lists[kind]) == 1 else None)
                 for kind in ('release_report', 'host_receipt')}
    if require_pairs:
        summaries['release_reports'] = summary_lists['release_report']
        summaries['host_receipts'] = summary_lists['host_receipt']
    return index, summaries


def audit_episode(plan, bindings):
    packets = gui_packet_index(plan)
    sources = {}
    packet_rows = []
    supported = unsupported = bound_count = 0
    for packet in plan['packets']:
        if packet['tag'] != GUI_TAG:
            continue
        source = packet['source']
        is_supported = source in pb.SUPPORTED_SOURCES
        evidence_sources = sorted(kind for kind in ('release_report', 'host_receipt')
                                  if packet['packet_id'] in bindings.get(kind, {}))
        is_bound = bool(evidence_sources)
        supported += int(is_supported)
        unsupported += int(not is_supported)
        bound_count += int(is_bound)
        entry = sources.setdefault(source, {'gui_packets': 0, 'adapter_supported': is_supported,
                                            'evidence_bound': 0, 'missing_evidence': 0})
        entry['gui_packets'] += 1
        entry['evidence_bound'] += int(is_bound)
        entry['missing_evidence'] += int(not is_bound)
        packet_rows.append({
            'packet_id': packet['packet_id'], 'sequence': packet['sequence'], 'source': source,
            'adapter_supported': is_supported,
            'screenshot_declared': 'screenshot_sha256' in packet,
            'evidence_sources': evidence_sources,
            'status': 'evidence_bound' if is_bound else 'missing_evidence',
        })
    gui_total = plan['counts']['gui_packets']
    missing = gui_total - bound_count
    if gui_total == 0:
        status, reason = STATUS_NO_GUI, None
    elif unsupported:
        status = STATUS_BLOCKED
        reason = (f'{unsupported} of {gui_total} GUI packets have no adapter for sources '
                  f'{[source for source, entry in sorted(sources.items()) if not entry["adapter_supported"]]}')
    elif bound_count == gui_total:
        status, reason = STATUS_COVERED, None
    elif bound_count:
        status = STATUS_PARTIAL
        reason = f'{missing} of {gui_total} GUI packets have no bound receipt'
    else:
        status = STATUS_MISSING
        reason = f'{gui_total} GUI packets have no bound receipt'
    if reason is not None and plan['counts']['gui_pending_screenshots']:
        reason += (f'; {plan["counts"]["gui_pending_screenshots"]} GUI packets also lack a '
                   'PB-declared screenshot')
    return {
        'status': status, 'reason': reason,
        'gui_packets': gui_total,
        'declared_screenshots': plan['counts']['gui_screenshots'],
        'pending_screenshots': plan['counts']['gui_pending_screenshots'],
        'supported_gui_packets': supported,
        'unsupported_gui_packets': unsupported,
        'evidence_bound_gui_packets': bound_count,
        'missing_evidence_gui_packets': missing,
        'evidence_sources': {kind: len(bindings.get(kind, {}))
                             for kind in ('release_report', 'host_receipt')},
        'sources': {source: sources[source] for source in sorted(sources)},
        'packets': packet_rows,
    }


def audit_episode_plan(plan):
    return {
        'cluster_id': plan['cluster_id'], 'episode_id': plan['episode_id'],
        'cluster_path': plan['cluster_path'], 'manifest': plan['source_assets']['manifest']['path'],
        'episode_dir': plan['episode_dir'], 'stream': plan['stream_path'],
        'passive_id': plan['passive_id'], 'world_variant_id': plan.get('world_variant_id'),
        'track': plan.get('track'), 'decision_point': plan.get('decision_point'),
        'source_hashes': {name: entry['sha256'] for name, entry in plan['source_assets'].items()},
    }


def build_report(root, benchmark, clusters, cluster_filter, receipts,
                 evidence_dir_summary=None):
    plans, episodes, errors = [], [], []
    for cluster_id in sorted(clusters):
        if cluster_filter and cluster_id != cluster_filter:
            continue
        cluster_dir = clusters[cluster_id]
        for manifest_path in bridge.cluster_manifests(cluster_dir):
            try:
                plan = bridge.compile_episode(root, benchmark, cluster_id, cluster_dir,
                                              manifest_path)
                manifest = bridge.load_json(manifest_path, 'manifest')
            except ValueError as error:
                errors.append({'cluster_id': cluster_id,
                               'episode_id': manifest_path.parent.name,
                               'error': sanitize(error, root)})
                continue
            plan['world_variant_id'] = manifest.get('world_variant_id')
            plan['timezone'] = manifest.get('timezone')
            plans.append(plan)
    for error in errors:
        episodes.append({'cluster_id': error['cluster_id'], 'episode_id': error['episode_id'],
                         'status': STATUS_INVALID, 'reason': error['error'],
                         'gui_packets': 0, 'declared_screenshots': 0, 'pending_screenshots': 0,
                         'supported_gui_packets': 0, 'unsupported_gui_packets': 0,
                         'evidence_bound_gui_packets': 0, 'missing_evidence_gui_packets': 0,
                         'evidence_sources': {'release_report': 0, 'host_receipt': 0},
                         'sources': {}, 'packets': []})
    bindings, summaries = bind_receipts(receipts, plans,
                                        require_pairs=evidence_dir_summary is not None)
    if evidence_dir_summary is not None:
        summaries['evidence_dir'] = evidence_dir_summary
    for plan in plans:
        key = (plan['cluster_id'], plan['episode_id'])
        entry = audit_episode_plan(plan)
        entry.update(audit_episode(plan, bindings.get(key, {})))
        episodes.append(entry)
    counts = {
        'clusters': len({episode.get('cluster_id') for episode in episodes}),
        'episodes': len(episodes),
        'invalid_episodes': sum(episode['status'] == STATUS_INVALID for episode in episodes),
        'episodes_with_gui': sum(episode['gui_packets'] > 0 for episode in episodes),
        'episodes_without_gui': sum(episode['gui_packets'] == 0 for episode in episodes),
        'gui_packets': sum(episode['gui_packets'] for episode in episodes),
        'supported_gui_packets': sum(episode['supported_gui_packets'] for episode in episodes),
        'unsupported_gui_packets': sum(episode['unsupported_gui_packets'] for episode in episodes),
        'declared_screenshots': sum(episode['declared_screenshots'] for episode in episodes),
        'pending_screenshots': sum(episode['pending_screenshots'] for episode in episodes),
        'evidence_bound_gui_packets': sum(episode['evidence_bound_gui_packets']
                                          for episode in episodes),
        'missing_evidence_gui_packets': sum(episode['missing_evidence_gui_packets']
                                            for episode in episodes),
        'by_status': {status: sum(episode['status'] == status for episode in episodes)
                      for status in (STATUS_COVERED, STATUS_PARTIAL, STATUS_MISSING,
                                     STATUS_BLOCKED, STATUS_NO_GUI, STATUS_INVALID)},
    }
    gaps = [{'cluster_id': episode['cluster_id'], 'episode_id': episode['episode_id'],
             'status': episode['status'], 'reason': episode['reason'],
             'gui_packets': episode['gui_packets'],
             'unsupported_gui_packets': episode['unsupported_gui_packets'],
             'missing_evidence_gui_packets': episode['missing_evidence_gui_packets'],
             'pending_screenshots': episode['pending_screenshots']}
            for episode in episodes
            if episode['status'] not in (STATUS_COVERED, STATUS_NO_GUI)]
    report = {
        'schema_version': SCHEMA,
        'command': 'audit',
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'read_only': True,
        'pb': {
            'layout_version': benchmark.get('layout_version'),
            'benchmark_sha256': digest(root / 'benchmark.json'),
            'clusters': len(clusters),
            'episodes': len(episodes),
        },
        'receipts': summaries,
        'counts': counts,
        'coverage_complete': bool(counts['episodes'] and not counts['invalid_episodes']
                                  and not gaps),
        'covered_episodes': [f"{episode['cluster_id']}/{episode['episode_id']}"
                             for episode in episodes if episode['status'] == STATUS_COVERED],
        'episodes': episodes,
        'gaps': gaps,
        'checks': {'pb_writes': 0, 'gt_reads': 0, 'images_copied': 0, 'images_rewritten': 0,
                   'device_writes': 0, 'scoring_invoked': 0, 'publish_invoked': 0,
                   'missing_evidence_marked_ready': 0},
    }
    try:
        capture_gate.assert_private_free(report, 'coverage audit report')
        capture_gate.assert_no_absolute_paths(report, 'coverage audit report')
    except ValueError as error:
        raise AuditError(f'coverage audit report self-check failed: {error}') from None
    return report, counts['invalid_episodes']


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pb_gui_coverage_audit.py',
        description='Read-only GUI packet coverage audit across every registered PB episode.')
    parser.add_argument('--pb-root', type=Path, required=True,
                        help='ProactiveBench root containing benchmark.json')
    parser.add_argument('--cluster', help='restrict the audit to one registered cluster id')
    parser.add_argument('--release-report', type=Path,
                        help='optional current pb-gui-release-report.v1 to bind as evidence')
    parser.add_argument('--host-receipt', type=Path,
                        help='optional current pb-gui-image-host-receipt.v1 to bind as evidence')
    parser.add_argument('--evidence-dir', type=Path,
                        help='optional directory of explicitly named release reports/host '
                             'receipts (the directory itself and one level of per-episode '
                             'bundles); every discovered episode must be paired')
    parser.add_argument('--output', type=Path, help='write the JSON audit here (outside PB root)')
    return parser


def command_audit(args):
    root = Path(args.pb_root).expanduser().resolve()
    try:
        benchmark, clusters = bridge.load_benchmark(root)
    except ValueError as error:
        raise AuditError(str(error)) from None
    if args.cluster and args.cluster not in clusters:
        raise AuditError(f'--cluster {args.cluster!r} is not registered in benchmark.json')
    receipts = {'release_report': [], 'host_receipt': []}
    if args.release_report:
        receipts['release_report'].append(load_release_evidence(args.release_report))
    if args.host_receipt:
        receipts['host_receipt'].append(load_host_evidence(args.host_receipt))
    evidence_dir_summary = None
    if args.evidence_dir:
        discovered, rejected, directory = load_evidence_dir(args.evidence_dir)
        receipts['release_report'].extend(discovered['release_report'])
        receipts['host_receipt'].extend(discovered['host_receipt'])
        evidence_dir_summary = {
            'basename': directory.name,
            'release_reports': len(discovered['release_report']),
            'host_receipts': len(discovered['host_receipt']),
            'rejected_candidates': len(rejected),
            'rejected_sample': rejected[:5],
        }
    report, invalid = build_report(root, benchmark, clusters, args.cluster, receipts,
                                   evidence_dir_summary=evidence_dir_summary)
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        target = ensure_outside(args.output, root, '--output')
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf8')
        except OSError as error:
            raise AuditError(f'cannot write --output {target}: {error}') from None
        print(json.dumps({'status': 'invalid' if invalid else
                          ('gaps' if report['gaps'] else 'ok'),
                          'episodes': report['counts']['episodes'],
                          'gui_packets': report['counts']['gui_packets'],
                          'covered': report['counts']['by_status'][STATUS_COVERED],
                          'missing_evidence': report['counts']['missing_evidence_gui_packets'],
                          'unsupported': report['counts']['unsupported_gui_packets'],
                          'coverage_complete': report['coverage_complete'],
                          'output': str(args.output)}, ensure_ascii=False))
    else:
        print(text, end='')
    return 1 if invalid else 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return command_audit(args)
    except AuditError as error:
        print(f'pb_gui_coverage_audit.py: error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
