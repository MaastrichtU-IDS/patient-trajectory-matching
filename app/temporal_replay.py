"""Recompute a temporal demonstration export against the same admitted checkout."""
import argparse
import json
from pathlib import Path

from app.temporal import TemporalWorkspace, digest


def verify(bundle, *, recorded_config=None):
    if isinstance(bundle, dict) and bundle.get('format') == 'recorded-journey-export-1':
        from app.recorded_export import verify_recorded
        return verify_recorded(bundle, config=recorded_config)
    if recorded_config is not None:
        raise ValueError('--recorded-config applies only to recorded-query exports')
    if isinstance(bundle, dict) and bundle.get('format') == 'patient-journey-export-1':
        from app.journey import verify as verify_journey
        return verify_journey(bundle)
    if isinstance(bundle, dict) and bundle.get('format') == 'interval-editor-export-1':
        from app.interval_editor import verify as verify_editor
        return verify_editor(bundle)
    if not isinstance(bundle, dict) or bundle.get('format') != 'temporal-workspace-export-1':
        raise ValueError('Unsupported temporal export')
    if bundle.get('report_id') != digest({k: v for k, v in bundle.items() if k != 'report_id'}):
        raise ValueError('Temporal export fingerprint differs')
    # Run only the locally admitted fixture, never execute supplied source/query
    # inputs. A rehashed, self-consistent alternate dataset is still rejected.
    expected = TemporalWorkspace().run(bundle.get('budget'))
    if expected != bundle:
        raise ValueError('Temporal export differs from replay on the admitted fixture and implementation')
    return {'verified': True, 'report_id': expected['report_id'],
            'robust_patient_ids': expected['result']['robust_patient_ids'],
            'added_robust_patient_ids': expected['added_robust_patient_ids']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--recorded-config', type=Path,
                        help='Explicit local startup configuration for recorded-query, pattern and feature-profile replay')
    args = parser.parse_args()
    with args.manifest.open('rb') as handle:
        raw = handle.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        parser.error('Temporal export exceeds 8 MiB')
    print(json.dumps(verify(json.loads(raw), recorded_config=args.recorded_config), indent=2))


if __name__ == '__main__':
    main()
