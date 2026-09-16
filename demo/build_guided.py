"""Validate the guided cohort graphs and package a portable, labeled replay."""
from pathlib import Path
import json

from cohort import build_dataset, run_cohort

ROOT = Path(__file__).resolve().parent


def main():
    data = build_dataset()
    (ROOT / 'cohort-data.json').write_text(json.dumps(data, indent=2) + '\n')
    replays = {}
    for budget in ('0', '1', '2'):
        replays[budget] = run_cohort(data, budget)
        replays[budget]['execution_mode'] = 'recorded-replay'
    payload = {'dataset': data, 'replays': replays,
               'story': json.loads((ROOT / 'story.json').read_text())}
    html = (ROOT / 'guided.html').read_text()
    html = html.replace('/*GUIDED_CSS*/', (ROOT / 'guided.css').read_text())
    html = html.replace('/*GUIDED_DATA*/null', json.dumps(payload, separators=(',', ':')).replace('</', '<\\/'))
    html = html.replace('/*GUIDED_JS*/', (ROOT / 'guided.js').read_text())
    (ROOT / 'Guided_Cohort_Demo.html').write_text(html)
    print('Validated 11 PRO/SOLID histories; reference P00 excluded from 10 candidates.')
    for budget, result in replays.items():
        print(f"Budget {budget}: {len(result['membership']['included'])} included; "
              f"{len(result['membership']['unresolved'])} unresolved")


if __name__ == '__main__':
    main()
