#!/usr/bin/env python3
"""B0 per-packet presentability audit for the declared formal pilot assets.

The audit is contract-only: it reads ``contracts/gui-scenario-audit.json``,
``contracts/gui-capabilities.json`` and the passive streams of the declared
``datasets/gui-*-formal-pilot`` episodes. It does not read device receipts, it
does not fabricate evidence, and it never marks a cluster as GUI Gold.

Conclusion rules (fail closed):

* ``supported`` requires the matched scenario candidate *and* the capability
  entry to be ``supported`` and to cover every asserted field.
* ``text_only`` requires a Text-class (``type == "text"``) packet without a GUI
  capture spec.
* every other packet is ``missing``, including pilot candidates, unbound
  source/app/view tuples, assertions beyond the declared coverage, and
  non-Text packets without a capture spec.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_TYPES = ('text',)
CONCLUSION_NAMES = ('supported', 'missing', 'text_only')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_capabilities(path):
    payload = json.loads(Path(path).read_text())
    capabilities = {}
    for group in ('core_apps', 'extension_apps'):
        entries = payload.get(group) or {}
        if not isinstance(entries, dict):
            raise ValueError(f'capability group {group} must be an object')
        for app_id, entry in entries.items():
            if app_id in capabilities:
                raise ValueError(f'capability app {app_id!r} is declared twice')
            if not isinstance(entry, dict) or not isinstance(entry.get('views'), dict):
                raise ValueError(f'capability app {app_id!r} requires a views object')
            capabilities[app_id] = {'status': entry.get('status'), 'views': entry['views']}
    return capabilities


def validate_candidates(cluster):
    cluster_id = cluster.get('cluster_id')
    candidates = cluster.get('candidates')
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(f'cluster {cluster_id!r} requires candidates')
    known = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError(f'cluster {cluster_id!r} candidate must be an object')
        missing = {'app_id', 'status', 'pilot_view', 'packet_source', 'required_assertions', 'gap'} - set(candidate)
        if missing:
            raise ValueError(f'cluster {cluster_id!r} candidate misses {sorted(missing)}')
        if candidate['status'] not in ('supported', 'pilot', 'missing'):
            raise ValueError(f'cluster {cluster_id!r} candidate status is not declared')
        assertions = candidate['required_assertions']
        if (not isinstance(assertions, list) or not assertions
                or any(not isinstance(field, str) or not field for field in assertions)
                or len(set(assertions)) != len(assertions)):
            raise ValueError(f'cluster {cluster_id!r} candidate required_assertions are invalid')
        key = (candidate['packet_source'], candidate['app_id'], candidate['pilot_view'])
        if key in known:
            raise ValueError(f'cluster {cluster_id!r} declares {key} twice')
        known[key] = candidate
    return candidates


def conclude(value, code, reason):
    return {'conclusion': value, 'reason_code': code, 'reason': reason}


def candidate_view(candidate):
    return {key: candidate[key] for key in ('app_id', 'surface', 'status', 'pilot_view',
                                            'packet_source', 'required_assertions', 'gap')}


def classify_packet(packet, candidates, capabilities):
    base = {'packet_id': packet.get('packet_id'), 'sequence': packet.get('sequence'),
            'type': packet.get('type'), 'source': packet.get('source'),
            'text_projection': packet.get('text_projection'), 'dataset': None,
            'capture': None, 'matched_candidate': None}
    capture = packet.get('capture')
    if not isinstance(capture, dict):
        if packet.get('type') in TEXT_TYPES:
            return {**base, **conclude(
                'text_only', 'text_packet_without_capture',
                'Text-class packet declares no GUI capture spec; it stays outside the GUI subset by declaration.')}
        return {**base, **conclude(
            'missing', 'capture_spec_missing',
            'Packet has no capture spec and is not Text-class; fail closed to missing.')}
    surface = {'app_id': capture.get('app_id'), 'view_id': capture.get('view_id'),
               'visible_assertions': capture.get('visible_assertions')}
    base['capture'] = surface
    if packet.get('type') != 'gui':
        return {**base, **conclude(
            'missing', 'non_gui_packet_with_capture',
            f"Packet type {packet.get('type')!r} carries a GUI capture spec; fail closed to missing.")}
    if capture.get('surface') != 'app_screen':
        return {**base, **conclude('missing', 'unsupported_capture_surface',
                                  'Only the declared app_screen surface is audited; fail closed to missing.')}
    source, app_id, view_id = packet.get('source'), surface['app_id'], surface['view_id']
    if any(not isinstance(value, str) or not value for value in (source, app_id, view_id)):
        return {**base, **conclude(
            'missing', 'invalid_capture_spec',
            'source/app_id/view_id must be nonempty strings; fail closed to missing.')}
    if source != app_id:
        return {**base, **conclude(
            'missing', 'packet_source_app_mismatch',
            f'packet source {source!r} differs from capture app_id {app_id!r}; fail closed to missing.')}
    assertions = surface['visible_assertions']
    if (not isinstance(assertions, list) or not assertions
            or any(not isinstance(field, str) or not field for field in assertions)
            or len(set(assertions)) != len(assertions)):
        return {**base, **conclude(
            'missing', 'invalid_visible_assertions',
            'visible_assertions must be a nonempty duplicate-free string list; fail closed to missing.')}
    matched = [candidate for candidate in candidates
               if (candidate['packet_source'], candidate['app_id'], candidate['pilot_view'])
               == (source, app_id, view_id)]
    if len(matched) > 1:
        return {**base, **conclude(
            'missing', 'candidate_ambiguous',
            f'cluster declares {len(matched)} candidates for {source}/{app_id}/{view_id}; fail closed to missing.')}
    candidate = matched[0] if matched else None
    if candidate is not None:
        base['matched_candidate'] = candidate_view(candidate)
    capability = capabilities.get(app_id)
    view_fields = capability['views'].get(view_id) if capability else None
    if view_fields is None:
        return {**base, **conclude(
            'missing', 'capability_surface_missing',
            f'capability table does not declare {app_id}/{view_id}; fail closed to missing without assuming the view.')}
    if not isinstance(view_fields, list) or not view_fields:
        raise ValueError(f'capability entry {app_id}/{view_id} must declare its fields')
    if candidate is None:
        return {**base, **conclude(
            'missing', 'candidate_unmapped',
            f'no candidate in this cluster declares source/app_id/view_id {source}/{app_id}/{view_id}; fail closed to missing.')}
    extra_candidate = sorted(set(assertions) - set(candidate['required_assertions']))
    if extra_candidate:
        return {**base, **conclude(
            'missing', 'assertions_exceed_candidate',
            f'visible_assertions {extra_candidate} are outside the candidate coverage; fail closed to missing.')}
    extra_capability = sorted(set(assertions) - set(view_fields))
    if extra_capability:
        return {**base, **conclude(
            'missing', 'assertions_exceed_capability',
            f'visible_assertions {extra_capability} are not declared by capability {app_id}/{view_id}; fail closed to missing.')}
    absent = sorted(set(candidate['required_assertions']) - set(assertions))
    if absent:
        return {**base, **conclude('missing', 'required_assertions_missing',
                                  f'Required candidate fields {absent} are not asserted; fail closed to missing.')}
    status = candidate['status']
    if status == 'supported':
        if capability['status'] == 'supported':
            return {**base, **conclude(
                'supported', 'contract_supported',
                'Scenario candidate and capability entry are both supported with full field coverage. '
                'Contract-level conclusion only: no device capture evidence and no GUI Gold are claimed.')}
        return {**base, **conclude(
            'missing', 'capability_status_not_supported',
            f"candidate is supported but capability entry status is {capability['status']!r}; fail closed to missing.")}
    if status == 'pilot':
        return {**base, **conclude(
            'missing', 'candidate_status_pilot',
            'Candidate is a pilot view only; B0 does not promote a pilot candidate to supported. '
            f"Gap: {candidate['gap']}")}
    return {**base, **conclude(
        'missing', 'candidate_status_missing',
        f"Candidate has no verified view for this surface. Gap: {candidate['gap']}")}


def audit_cluster(cluster, datasets_root, capabilities):
    packets, datasets = [], []
    for mapping in cluster['formal_pilot_datasets']:
        name = mapping['dataset']
        directory = datasets_root / name
        base_path, passive_path = directory / 'cluster-base.json', directory / 'passive.json'
        base = json.loads(base_path.read_text())
        if base.get('cluster_id') != mapping['cluster_id']:
            raise ValueError(f'dataset {name!r} cluster_id {base.get("cluster_id")!r} differs from declared '
                             f'{mapping["cluster_id"]!r}')
        passive = json.loads(passive_path.read_text())
        if not isinstance(passive.get('passive_id'), str) or not passive['passive_id']:
            raise ValueError(f'dataset {name!r} requires a passive_id')
        stream = passive.get('packets')
        if not isinstance(stream, list) or not stream:
            raise ValueError(f'dataset {name!r} requires packets')
        seen = set()
        for packet in stream:
            packet_id = packet.get('packet_id') if isinstance(packet, dict) else None
            if not isinstance(packet_id, str) or not packet_id or packet_id in seen:
                raise ValueError(f'dataset {name!r} has a duplicate or missing packet_id')
            seen.add(packet_id)
            record = classify_packet(packet, cluster['candidates'], capabilities)
            record['dataset'] = name
            packets.append(record)
        datasets.append({'dataset': name, 'cluster_id': mapping['cluster_id'],
                         'passive_id': passive['passive_id'],
                         'cluster_base_sha256': digest(base_path), 'passive_sha256': digest(passive_path)})
    counts = {name: sum(packet['conclusion'] == name for packet in packets) for name in CONCLUSION_NAMES}
    return {'cluster_id': cluster['cluster_id'], 'datasets': datasets, 'packets': packets,
            'counts': counts, 'gui_gold': False,
            'gold_evidence': 'none claimed: B0 is a contract audit; device capture, machine pixel QA '
                             'and release evidence stay out of scope.'}


def audit(audit_path, datasets_root, capabilities_path=None):
    audit_path = Path(audit_path).resolve()
    datasets_root = Path(datasets_root).resolve()
    capabilities_path = (Path(capabilities_path).resolve() if capabilities_path
                         else ROOT / 'contracts/gui-capabilities.json')
    contract = json.loads(audit_path.read_text())
    if contract.get('schema_version') != '1.0':
        raise ValueError('scenario audit contract schema_version must be 1.0')
    clusters = contract.get('clusters')
    if not isinstance(clusters, list) or not clusters:
        raise ValueError('scenario audit contract requires clusters')
    capabilities = load_capabilities(capabilities_path)
    declared, cluster_ids = {}, set()
    for cluster in clusters:
        cluster_id = cluster.get('cluster_id')
        if not isinstance(cluster_id, str) or not cluster_id or cluster_id in cluster_ids:
            raise ValueError('cluster_id must be a unique nonempty string')
        cluster_ids.add(cluster_id)
        validate_candidates(cluster)
        mappings = cluster.get('formal_pilot_datasets')
        if not isinstance(mappings, list):
            raise ValueError(f'cluster {cluster_id!r} requires formal_pilot_datasets')
        for mapping in mappings:
            if not isinstance(mapping, dict) or set(mapping) != {'dataset', 'cluster_id'}:
                raise ValueError(f'cluster {cluster_id!r} formal_pilot_datasets entries require dataset and cluster_id')
            if (not isinstance(mapping['dataset'], str)
                    or not re.fullmatch(r'gui-[a-z0-9-]+-formal-pilot', mapping['dataset'])
                    or not isinstance(mapping['cluster_id'], str) or not mapping['cluster_id']):
                raise ValueError('formal pilot dataset mapping requires a safe name and cluster_id')
            if mapping['dataset'] in declared:
                raise ValueError(f'dataset {mapping["dataset"]!r} is declared twice')
            declared[mapping['dataset']] = mapping['cluster_id']
    available = sorted(path.name for path in datasets_root.glob('gui-*-formal-pilot') if path.is_dir())
    undeclared = sorted(set(available) - set(declared))
    if undeclared:
        raise ValueError(f'undeclared formal pilot dataset(s): {undeclared}')
    absent = sorted(set(declared) - set(available))
    if absent:
        raise ValueError(f'declared formal pilot dataset(s) missing: {absent}')
    results = [audit_cluster(cluster, datasets_root, capabilities) for cluster in clusters]
    totals = {name: sum(result['counts'][name] for result in results) for name in CONCLUSION_NAMES}
    return {'schema_version': '1.0', 'audit': 'B0-per-packet-presentability',
            'source_assets': {'scenario_audit': {'path': str(audit_path), 'sha256': digest(audit_path)},
                              'capabilities': {'path': str(capabilities_path), 'sha256': digest(capabilities_path)}},
            'device_evidence': 'not_used',
            'gui_gold': False,
            'statement': 'Contract-level B0 audit only: every cluster stays not GUI Gold, no device evidence is '
                         'claimed, and pilot candidates are never promoted to supported.',
            'clusters': results, 'totals': totals}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='B0 per-packet presentability audit over formal pilot assets')
    parser.add_argument('--audit', type=Path, default=ROOT / 'contracts/gui-scenario-audit.json')
    parser.add_argument('--datasets', type=Path, default=ROOT / 'datasets')
    parser.add_argument('--capabilities', type=Path, default=ROOT / 'contracts/gui-capabilities.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = audit(args.audit, args.datasets, args.capabilities)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end='')
