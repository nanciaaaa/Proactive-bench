#!/usr/bin/env python3
"""Compile one ProactiveBench episode's passive stream into a private GUI plan.

Read-only bridge between a PB tree (``benchmark.json`` + ``clusters[c].path``)
and the device capture side: it finds the episode manifest under
``episodes/*/manifest.json``, checks that every manifest ``*_ref`` string stays
inside ``--pb-root`` and exists, then projects
``passive_inputs/<passive_id>/stream.jsonl`` into a JSON plan that keeps only
the public packet fields plus the paired GUI screenshot fields.

Fail-closed rules:

* references and screenshot paths must resolve inside the benchmark root and
  exist; private GT references are only checked, never emitted;
* non-GUI packets must not carry screenshot fields; GUI screenshot pairs must
  live at ``<passive>/gui/<packet_id>.png`` and match their declared sha256;
* legacy ``type`` / ``text_projection`` packet fields are rejected;
* private GT material (``need`` / ``evidence_sets`` / ``gold`` ...) is never
  read, and any private key found in the emitted plan aborts the run.

Usage::

    python3 scripts/pb_gui_bridge.py plan --pb-root PB --episode-id ID --output plan.json
    python3 scripts/pb_gui_bridge.py validate --pb-root PB --episode-id ID
    python3 scripts/pb_gui_bridge.py list --pb-root PB
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PLAN_SCHEMA = 'pb-gui-bridge.plan.v1'
COMMANDS = ('plan', 'list', 'validate')
GUI_TAG = 'gui'
PACKET_FIELDS = ('packet_id', 'sequence', 'tag', 'source', 'observed_at',
                 'delivered_at', 'text_event')
SCREENSHOT_FIELDS = ('screenshot_ref', 'screenshot_sha256')
LEGACY_FIELDS = ('type', 'text_projection')
STREAM_REF_KEYS = ('passive_ref', 'passive_stream_ref', 'stream_ref')
REF_ROLES = {'passive_stream': STREAM_REF_KEYS,
             'history': ('history_ref', 'history_trace_ref'),
             'initialization': ('initialization_ref', 'initialisation_ref', 'init_ref')}
PRIVATE_REF_KEYS = ('gt_ref', 'private_gt_ref', 'ground_truth_ref', 'label_ref', 'answer_ref')
PRIVATE_GT_FIELDS = frozenset(
    re.sub(r'[^a-z0-9]', '', name) for name in (
        'gt', 'gold', 'label', 'answer', 'oracle', 'private_gt', 'ground_truth',
        'need', 'need_set', 'expected_need', 'expected_need_set',
        'evidence', 'evidence_set', 'evidence_sets',
        'passive_class', 'public_rule', 'support', 'contradict'))


def normalize(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path, root):
    return path.relative_to(root).as_posix()


def load_json(path, label):
    try:
        return json.loads(path.read_text(encoding='utf8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f'cannot read {label} {path}: {error}') from None


def timestamp(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{label} is not an ISO-8601 timestamp: {value!r}') from None
    if parsed.utcoffset() is None:
        raise ValueError(f'{label} must carry a UTC offset: {value!r}')
    return parsed


def walk_keys(value, trail=()):
    if isinstance(value, dict):
        for key, item in value.items():
            here = trail + (str(key),)
            yield '/'.join(here), key
            yield from walk_keys(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_keys(item, trail + (str(index),))


def private_paths(value, trail=()):
    found = []
    for path, key in walk_keys(value, trail):
        if normalize(key) in PRIVATE_GT_FIELDS:
            found.append(path)
    return found


def resolve_ref(root, cluster_dir, ref, label):
    """Resolve a manifest reference, refusing anything outside ``root``."""
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError(f'{label} must be a nonempty string')
    base = Path(ref)
    candidates = ([('absolute', base)] if base.is_absolute()
                  else [('cluster', cluster_dir / base), ('pb-root', root / base)])
    missing, escaped = [], None
    for base_name, candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            escaped = escaped or ref
            continue
        if resolved.exists():
            return resolved, base_name
        missing.append(base_name)
    if escaped is not None:
        raise ValueError(f'{label} escapes --pb-root: {ref!r}')
    raise ValueError(f'{label} does not exist relative to {" or ".join(missing)}: {ref!r}')


def load_benchmark(root):
    if not root.is_dir():
        raise ValueError(f'--pb-root is not a directory: {root}')
    benchmark_path = root / 'benchmark.json'
    if not benchmark_path.is_file():
        raise ValueError(f'benchmark.json not found under --pb-root: {benchmark_path}')
    payload = load_json(benchmark_path, 'benchmark')
    clusters = payload.get('clusters') if isinstance(payload, dict) else None
    if not isinstance(clusters, dict) or not clusters:
        raise ValueError('benchmark.json requires a nonempty clusters object')
    directories = {}
    for cluster_id, entry in clusters.items():
        path_value = entry.get('path') if isinstance(entry, dict) else None
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValueError(f'clusters[{cluster_id!r}] requires a path string')
        cluster_dir = (root / path_value).resolve()
        if not cluster_dir.is_relative_to(root):
            raise ValueError(f'clusters[{cluster_id!r}].path escapes --pb-root: {path_value!r}')
        if not cluster_dir.is_dir():
            raise ValueError(f'clusters[{cluster_id!r}].path is not a directory: {path_value!r}')
        directories[cluster_id] = cluster_dir
    return payload, directories


def cluster_manifests(cluster_dir):
    return sorted(cluster_dir.glob('**/episodes/*/manifest.json'))


def find_episode(clusters, episode_id, cluster_filter):
    if not isinstance(episode_id, str) or not episode_id.strip():
        raise ValueError('--episode-id must be a nonempty string')
    named, others, inspected = [], [], []
    for cluster_id, cluster_dir in clusters.items():
        if cluster_filter and cluster_id != cluster_filter:
            continue
        inspected.append(cluster_id)
        for manifest_path in cluster_manifests(cluster_dir):
            if manifest_path.parent.name == episode_id:
                named.append((cluster_id, cluster_dir, manifest_path))
            else:
                others.append((cluster_id, cluster_dir, manifest_path))
    if not named:
        for cluster_id, cluster_dir, manifest_path in others:
            payload = load_json(manifest_path, 'manifest')
            if isinstance(payload, dict) and payload.get('episode_id') == episode_id:
                named.append((cluster_id, cluster_dir, manifest_path))
    if not named:
        raise ValueError(f'episode {episode_id!r} not found in clusters {sorted(inspected)}')
    if len(named) > 1:
        raise ValueError(f'episode {episode_id!r} is ambiguous across clusters '
                         f'{sorted({item[0] for item in named})}; pass --cluster')
    return named[0]


def collect_references(root, cluster_dir, manifest):
    entries = {}
    for key, value in manifest.items():
        if key.endswith('_ref'):
            resolved, base = resolve_ref(root, cluster_dir, value, f'manifest {key}')
            entries[key] = {'ref': value, 'path': relative(resolved, root),
                            'base': base, 'resolved': resolved}
    roles = {}
    for role, keys in REF_ROLES.items():
        matched = next((key for key in keys if key in entries), None)
        if matched is None and role == 'passive_stream':
            passive_id = manifest.get('passive_id')
            if not isinstance(passive_id, str) or not passive_id.strip():
                raise ValueError('manifest needs a passive stream ref or a passive_id fallback')
            fallback = f'passive_inputs/{passive_id}/stream.jsonl'
            resolved, base = resolve_ref(root, cluster_dir, fallback, 'passive stream fallback')
            entries['passive_stream_fallback'] = {'ref': fallback, 'path': relative(resolved, root),
                                                  'base': base, 'resolved': resolved}
            matched = 'passive_stream_fallback'
        if matched is None:
            raise ValueError(f'manifest declares no {role} reference: expected one of {list(keys)}')
        roles[role] = entries[matched]
    return entries, roles


def project_packet(packet, line_no, stream_label, root, cluster_dir, gui_dir):
    if not isinstance(packet, dict):
        raise ValueError(f'{stream_label}: line {line_no} is not a JSON object')
    missing = [field for field in PACKET_FIELDS if field not in packet]
    if missing:
        raise ValueError(f'{stream_label}: line {line_no} misses packet fields {missing}')
    packet_id = packet['packet_id']
    if not isinstance(packet_id, str) or not packet_id.strip():
        raise ValueError(f'{stream_label}: line {line_no} needs a nonempty packet_id')
    sequence = packet['sequence']
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ValueError(f'{stream_label} packet {packet_id!r} needs a positive integer sequence')
    tag, source = packet['tag'], packet['source']
    if (not isinstance(tag, str) or not tag.strip()) or (not isinstance(source, str) or not source.strip()):
        raise ValueError(f'{stream_label} packet {packet_id!r} needs nonempty tag and source')
    observed = timestamp(packet['observed_at'], f'{stream_label} packet {packet_id!r} observed_at')
    delivered = timestamp(packet['delivered_at'], f'{stream_label} packet {packet_id!r} delivered_at')
    if delivered < observed:
        raise ValueError(f'{stream_label} packet {packet_id!r} delivered_at precedes observed_at')
    text_event = packet['text_event']
    if not isinstance(text_event, str) or not text_event.strip():
        raise ValueError(f'{stream_label} packet {packet_id!r} needs a nonempty text_event')
    for path, key in walk_keys(packet):
        lowered = str(key).lower()
        top_level = path == str(key)
        if top_level and lowered in LEGACY_FIELDS:
            raise ValueError(f'{stream_label} packet {packet_id!r} carries legacy field {path}')
        if top_level and normalize(key) in PRIVATE_GT_FIELDS:
            raise ValueError(f'{stream_label} packet {packet_id!r} leaks private field {path}')
        if 'screenshot' in lowered:
            if tag != GUI_TAG:
                raise ValueError(f'{stream_label} non-GUI packet {packet_id!r} carries screenshot field {path}')
            if not top_level or key not in SCREENSHOT_FIELDS:
                raise ValueError(f'{stream_label} GUI packet {packet_id!r} carries unknown or nested '
                                 f'screenshot field {path}')
    emitted = {field: packet[field] for field in PACKET_FIELDS}
    present = [field for field in SCREENSHOT_FIELDS if field in packet]
    if present and set(present) != set(SCREENSHOT_FIELDS):
        raise ValueError(f'{stream_label} packet {packet_id!r} must pair {" and ".join(SCREENSHOT_FIELDS)}')
    if present:
        declared = packet['screenshot_sha256']
        if not isinstance(declared, str) or not re.fullmatch(r'[0-9a-f]{64}', declared):
            raise ValueError(f'{stream_label} packet {packet_id!r} has an invalid screenshot_sha256')
        label = f'{stream_label} packet {packet_id!r} screenshot_ref'
        resolved, _ = resolve_ref(root, cluster_dir, packet['screenshot_ref'], label)
        if resolved.parent != gui_dir or resolved.name != f'{packet_id}.png':
            raise ValueError(f'{label} must live at {relative(gui_dir, root)}/{packet_id}.png')
        actual = digest(resolved)
        if actual != declared:
            raise ValueError(f'{stream_label} packet {packet_id!r} screenshot sha256 mismatch: '
                             f'declared {declared}, actual {actual}')
        emitted['screenshot_ref'] = packet['screenshot_ref']
        emitted['screenshot_sha256'] = declared
    return emitted


def compile_episode(root, benchmark, cluster_id, cluster_dir, manifest_path):
    manifest = load_json(manifest_path, 'manifest')
    if not isinstance(manifest, dict):
        raise ValueError(f'manifest is not a JSON object: {manifest_path}')
    episode_id = manifest.get('episode_id')
    if not isinstance(episode_id, str) or not episode_id.strip():
        raise ValueError(f'manifest needs a nonempty episode_id: {manifest_path}')
    if manifest_path.parent.name != episode_id:
        raise ValueError(f'manifest directory {manifest_path.parent.name!r} does not match '
                         f'episode_id {episode_id!r}')
    entries, roles = collect_references(root, cluster_dir, manifest)
    stream_path = roles['passive_stream']['resolved']
    stream_label = relative(stream_path, root)
    gui_dir = (stream_path.parent / 'gui').resolve()
    try:
        lines = stream_path.read_text(encoding='utf8').splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f'cannot read passive stream {stream_path}: {error}') from None
    packets, seen_ids, last_sequence = [], set(), 0
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            packet = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f'{stream_label}: line {line_no} is not JSON: {error}') from None
        projected = project_packet(packet, line_no, stream_label, root, cluster_dir, gui_dir)
        packet_id = projected['packet_id']
        if packet_id in seen_ids:
            raise ValueError(f'{stream_label} packet_id {packet_id!r} is duplicated')
        if projected['sequence'] <= last_sequence:
            raise ValueError(f'{stream_label} packet {packet_id!r} sequence must strictly increase')
        seen_ids.add(packet_id)
        last_sequence = projected['sequence']
        packets.append(projected)
    if not packets:
        raise ValueError(f'{stream_label} declares no packets')
    tags = {}
    for packet in packets:
        tags[packet['tag']] = tags.get(packet['tag'], 0) + 1
    screenshots = sum('screenshot_ref' in packet for packet in packets)
    plan = {
        'schema_version': PLAN_SCHEMA,
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'pb_root': str(root),
        'layout_version': benchmark.get('layout_version'),
        'cluster_id': cluster_id,
        'cluster_path': relative(cluster_dir, root),
        'episode_id': episode_id,
        'episode_dir': relative(manifest_path.parent, root),
        'track': manifest.get('track'),
        'decision_point': manifest.get('decision_point'),
        'passive_id': manifest.get('passive_id'),
        'stream_path': stream_label,
        'screenshot_dir': relative(gui_dir, root),
        'source_assets': {
            'benchmark': {'path': 'benchmark.json', 'sha256': digest(root / 'benchmark.json')},
            'manifest': {'path': relative(manifest_path, root), 'sha256': digest(manifest_path)},
            'passive_stream': {'path': stream_label, 'sha256': digest(stream_path)},
        },
        'references': {key: {'ref': value['ref'], 'path': value['path'], 'base': value['base']}
                       for key, value in entries.items() if key not in PRIVATE_REF_KEYS},
        'counts': {
            'packets': len(packets),
            'gui_packets': tags.get(GUI_TAG, 0),
            'gui_screenshots': screenshots,
            'gui_pending_screenshots': tags.get(GUI_TAG, 0) - screenshots,
            'tags': dict(sorted(tags.items())),
        },
        'checks': {
            'references_within_root_and_existing': len(entries),
            'packets_projected': len(packets),
            'gui_screenshot_hashes_verified': screenshots,
            'legacy_type_or_text_projection': 0,
            'leaked_private_fields': 0,
        },
        'packets': packets,
    }
    leaked = private_paths(plan)
    if leaked:
        raise ValueError(f'private GT fields leaked into the plan: {leaked}')
    return plan


def compile_for_args(args, root, clusters):
    if args.cluster and args.cluster not in clusters:
        raise ValueError(f'--cluster {args.cluster!r} is not registered in benchmark.json')
    cluster_id, cluster_dir, manifest_path = find_episode(clusters, args.episode_id, args.cluster)
    return compile_episode(root, args._benchmark, cluster_id, cluster_dir, manifest_path)


def write_json(output, payload):
    target = Path(output).expanduser().resolve()
    if target.is_dir():
        raise ValueError(f'--output is a directory: {target}')
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    except OSError as error:
        raise ValueError(f'cannot write --output {target}: {error}') from None
    return target


def plan_summary(plan):
    counts = plan['counts']
    return {'status': 'ok', 'cluster_id': plan['cluster_id'], 'episode_id': plan['episode_id'],
            'passive_id': plan['passive_id'], 'stream_path': plan['stream_path'],
            'references_checked': plan['checks']['references_within_root_and_existing'],
            'packets': counts['packets'], 'gui_packets': counts['gui_packets'],
            'gui_screenshots': counts['gui_screenshots'],
            'gui_pending_screenshots': counts['gui_pending_screenshots']}


def command_plan(args):
    plan = compile_for_args(args, args._root, args._clusters)
    if args.output:
        target = write_json(args.output, plan)
        print(json.dumps({**plan_summary(plan), 'output': str(target)}, ensure_ascii=False))
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def command_validate(args):
    plan = compile_for_args(args, args._root, args._clusters)
    summary = plan_summary(plan)
    if args.output:
        target = write_json(args.output, plan)
        summary['output'] = str(target)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def command_list(args):
    if args.cluster and args.cluster not in args._clusters:
        raise ValueError(f'--cluster {args.cluster!r} is not registered in benchmark.json')
    episodes, failures = [], 0
    for cluster_id, cluster_dir in args._clusters.items():
        if args.cluster and cluster_id != args.cluster:
            continue
        for manifest_path in cluster_manifests(cluster_dir):
            episode_id = manifest_path.parent.name
            try:
                plan = compile_episode(args._root, args._benchmark, cluster_id, cluster_dir, manifest_path)
                counts = plan['counts']
                episodes.append({'cluster_id': cluster_id, 'episode_id': plan['episode_id'],
                                 'passive_id': plan['passive_id'], 'status': 'ok',
                                 'packets': counts['packets'], 'gui_packets': counts['gui_packets'],
                                 'gui_screenshots': counts['gui_screenshots'],
                                 'gui_pending_screenshots': counts['gui_pending_screenshots']})
            except ValueError as error:
                failures += 1
                episodes.append({'cluster_id': cluster_id, 'episode_id': episode_id,
                                 'manifest': relative(manifest_path, args._root),
                                 'status': 'error', 'error': str(error)})
    if args.output:
        report = {'schema_version': 'pb-gui-bridge.list.v1', 'pb_root': str(args._root),
                  'layout_version': args._benchmark.get('layout_version'),
                  'episodes': episodes, 'failures': failures}
        target = write_json(args.output, report)
        print(json.dumps({'status': 'fail' if failures else 'ok', 'episodes': len(episodes),
                          'failures': failures, 'output': str(target)}, ensure_ascii=False))
    else:
        for episode in episodes:
            if episode['status'] == 'ok':
                print(f"{episode['cluster_id']}\t{episode['episode_id']}\t{episode['passive_id']}\t"
                      f"{episode['packets']}\t{episode['gui_packets']}\t{episode['gui_screenshots']}\tok")
            else:
                print(f"{episode['cluster_id']}\t{episode['episode_id']}\t-\t-\t-\t-\t"
                      f"error: {episode['error']}")
    return 1 if failures else 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pb_gui_bridge.py',
        description='Compile a PB episode passive stream into a private GUI plan '
                    '(default command is plan).')
    subparsers = parser.add_subparsers(dest='command')
    for name, help_text in (('plan', 'write the private plan for one episode'),
                            ('validate', 'run the same checks without requiring an output file'),
                            ('list', 'list every registered cluster episode with its plan counts')):
        subparser = subparsers.add_parser(name, help=help_text)
        subparser.add_argument('--pb-root', type=Path, required=True,
                               help='ProactiveBench root containing benchmark.json')
        if name != 'list':
            subparser.add_argument('--episode-id', required=True, help='episode id, e.g. ep_0001')
        subparser.add_argument('--cluster', help='restrict the search to one registered cluster id')
        subparser.add_argument('--output', type=Path,
                               help='write the plan (or list report) to this JSON file')
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('-h', '--help'):
        pass
    elif not argv or argv[0] not in COMMANDS:
        argv = ['plan', *argv]
    args = build_parser().parse_args(argv)
    try:
        args._root = Path(args.pb_root).expanduser().resolve()
        args._benchmark, args._clusters = load_benchmark(args._root)
        if args.command == 'list':
            return command_list(args)
        if args.command == 'validate':
            return command_validate(args)
        return command_plan(args)
    except ValueError as error:
        print(f'pb_gui_bridge.py: error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
