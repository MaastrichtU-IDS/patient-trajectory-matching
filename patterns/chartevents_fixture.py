"""A synthetic chartevents fixture for route (b) tests: invented patients, items and values, no MIMIC rows.

`write(directory, variant)` writes `chartevents.csv`, `icustays.csv` and `d_items.csv` in the MIMIC-IV
demo 2.2 column layout. The committed copy under examples/mimic-chartevents/ is `write(..., 'full')`.

- `full`: three patients with chart rows (one with a stay of 40 admitted rows, so two claim stores, and
  a second stay), one patient with a stay and no chart rows; every admission outcome (textual,
  qualified, missing unit, flagged and unknown warnings, a value disagreement, an unknown item, an item
  of another table, an exact duplicate); value spellings with signs, fractions, exponents and leading
  or trailing points; a row without storetime; a record outside its stay; an item without category.
- `importer`: the same, but no stay holds more than 32 admitted rows and no more than 16 items are
  admitted, so `mimic_measurement_import.run` imports it whole and the stores can be compared.
"""
from __future__ import annotations

import csv
from pathlib import Path

CHART = 'subject_id hadm_id stay_id caregiver_id charttime storetime itemid value valuenum valueuom warning'.split()
STAYS = 'subject_id hadm_id stay_id first_careunit last_careunit intime outtime los'.split()
ITEMS = 'itemid label abbreviation linksto category unitname param_type lownormalvalue highnormalvalue'.split()

ITEM_ROWS = [
    ['910001', 'Synthetic rate A', '', 'chartevents', 'Synthetic Vitals', 'per min', 'Numeric', '', ''],
    ['910002', 'Synthetic level B', '', 'chartevents', 'Synthetic Labs', 'unit-b', 'Numeric', '', ''],
    ['910003', 'Synthetic scale C', '', 'chartevents', 'Synthetic Scales', '', 'Text', '', ''],
    ['910004', 'Synthetic uncategorised D', '', 'chartevents', '', 'unit-d', 'Numeric', '', ''],
    ['910005', 'Synthetic signed E', '', 'chartevents', 'Synthetic Labs', 'unit-e', 'Numeric', '', ''],
    ['920001', 'Synthetic input F', '', 'inputevents', 'Synthetic Inputs', 'mL', 'Solution', '', ''],
]
STAY_ROWS = [
    ['5000001', '6000001', '7000001', 'Unit X', 'Unit X', '2190-03-01 08:00:00', '2190-03-04 08:00:00', '3'],
    ['5000001', '6000002', '7000002', 'Unit X', 'Unit Y', '2190-05-10 10:00:00', '2190-05-11 10:00:00', '1'],
    ['5000002', '6000003', '7000003', 'Unit Y', 'Unit Y', '2185-01-01 00:00:00', '2185-01-02 00:00:00', '1'],
    ['5000003', '6000004', '7000004', 'Unit Z', 'Unit Z', '2201-07-07 07:00:00', '2201-07-08 07:00:00', '1'],
    ['5000004', '6000005', '7000005', 'Unit Z', 'Unit Z', '2170-02-02 02:00:00', '2170-02-03 02:00:00', '1'],
]


def _row(pid, hadm, stay, time, item, value, valuenum, unit, warning='0', storetime='same'):
    stored = '' if storetime is None else (time if storetime == 'same' else storetime)
    return [pid, hadm, stay, '', time, stored, item, value, valuenum, unit, warning]


def chart_rows(variant):
    rows = []
    long_stay = 40 if variant == 'full' else 28
    for i in range(long_stay):  # patient 1, stay 1: many rate readings, minute by minute
        minute = f'2190-03-01 {9 + i // 60:02d}:{i % 60:02d}:00'
        rows.append(_row('5000001', '6000001', '7000001', minute, '910001', str(60 + i), str(60 + i), 'per min'))
    rows += [
        _row('5000001', '6000001', '7000001', '2190-03-01 07:30:00', '910002', '7.350', '7.35', 'unit-b'),  # before intime
        _row('5000001', '6000001', '7000001', '2190-03-02 12:00:00', '910002', '.5', '.5', 'unit-b', storetime=None),
        _row('5000001', '6000001', '7000001', '2190-03-02 12:05:00', '910002', '5.', '5', 'unit-b'),
        _row('5000001', '6000001', '7000001', '2190-03-02 12:10:00', '910003', 'Alert', '', ''),  # textual
        _row('5000001', '6000001', '7000001', '2190-03-02 12:15:00', '910003', '15 Alert', '15', ''),
        _row('5000001', '6000001', '7000001', '2190-03-02 12:20:00', '910001', '<5', '5', 'per min'),  # qualified
        _row('5000001', '6000001', '7000001', '2190-03-02 12:25:00', '910001', '80', '80', 'per min', warning='1'),
        _row('5000001', '6000001', '7000001', '2190-03-02 12:30:00', '910001', '81', '81', 'per min', warning=''),
        _row('5000001', '6000001', '7000001', '2190-03-02 12:35:00', '910001', '82', '82', ''),  # no unit
        _row('5000001', '6000001', '7000001', '2190-03-02 12:40:00', '910001', '83', '84', 'per min'),  # disagree
        _row('5000001', '6000001', '7000001', '2190-03-02 12:45:00', '999999', '1', '1', 'x'),  # unknown item
        _row('5000001', '6000001', '7000001', '2190-03-02 12:50:00', '920001', '1', '1', 'mL'),  # other table
        _row('5000001', '6000001', '7000001', '2190-03-02 12:00:00', '910002', '.5', '.5', 'unit-b', storetime=None),
        _row('5000001', '6000002', '7000002', '2190-05-10 11:00:00', '910005', '-1.25', '-1.25', 'unit-e'),
        _row('5000001', '6000002', '7000002', '2190-05-10 11:05:00', '910005', '+2', '2', 'unit-e'),
        _row('5000001', '6000002', '7000002', '2190-05-10 11:10:00', '910005', '1e-7', '1e-7', 'unit-e'),
        _row('5000001', '6000002', '7000002', '2190-05-10 11:15:00', '910004', '12E2', '1200', 'unit-d'),
        _row('5000002', '6000003', '7000003', '2185-01-01 06:00:00', '910001', '70', '70', 'per min'),
        _row('5000002', '6000003', '7000003', '2185-01-01 06:00:00', '910002', '7.40', '7.4', 'unit-b'),
        _row('5000002', '6000003', '7000003', '2185-01-01 05:00:00', '910003', 'Alert', '', ''),
        _row('5000002', '6000003', '7000003', '2185-01-02 03:00:00', '910001', '71', '71', 'per min'),  # after outtime
        _row('5000003', '6000004', '7000004', '2201-07-07 09:00:00', '910003', 'Drowsy', '', ''),  # nothing admitted
    ]
    # Interleave patients as an unsorted export might: the converter must not rely on file order.
    return rows[::2] + rows[1::2]


def write(directory, variant='full'):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, header, rows in (('chartevents.csv', CHART, chart_rows(variant)), ('icustays.csv', STAYS, STAY_ROWS),
                               ('d_items.csv', ITEMS, ITEM_ROWS)):
        with (directory / name).open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, lineterminator='\n')
            writer.writerow(header)
            writer.writerows(rows)
    return directory


def write_combined(directory, store_fixture):
    """The MIMIC store fixture (inputevents, icustays, d_items) plus chartevents for its own patients and stays,
    with the chartevents items appended to its dictionary: synthetic, for the combined route (D27). Patient 14 gets
    no chart row, so chartevents does not cover its stay; patient 13 only rows admission leaves out."""
    import shutil
    directory, store_fixture = Path(directory), Path(store_fixture)
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(store_fixture / 'inputevents.csv', directory / 'inputevents.csv')
    shutil.copy(store_fixture / 'icustays.csv', directory / 'icustays.csv')
    text = (store_fixture / 'd_items.csv').read_text(encoding='utf-8')
    with (directory / 'd_items.csv').open('w', encoding='utf-8', newline='') as handle:
        handle.write(text if text.endswith('\n') else text + '\n')
        csv.writer(handle, lineterminator='\n').writerows(r for r in ITEM_ROWS if r[3] == 'chartevents')
    rows = []
    for i in range(36):  # 36 readings in one stay: two claim stores
        t = f'2150-01-01 {9 + i // 6:02d}:{(i % 6) * 10:02d}:00'
        rows.append(_row('11', '110', '1101', t, '910001', str(70 + i), str(70 + i), 'per min'))
    rows += [
        _row('11', '110', '1101', '2150-01-01 07:45:00', '910002', '7.35', '7.35', 'unit-b'),  # before intime
        _row('11', '111', '1102', '2150-03-01 09:00:00', '910005', '-0.5', '-0.5', 'unit-e', storetime=None),
        _row('11', '111', '1102', '2150-03-01 09:05:00', '910003', 'Alert', '', ''),
        _row('12', '120', '1201', '2151-06-10 07:00:00', '910001', '88', '88', 'per min'),
        _row('12', '120', '1201', '2151-06-10 08:00:00', '910002', '1e-7', '1e-7', 'unit-b'),
        _row('12', '120', '1201', '2151-06-10 08:30:00', '910001', '90', '90', 'per min', warning='1'),
        _row('13', '130', '1301', '2149-02-01 11:00:00', '910003', 'Drowsy', '', ''),
    ]
    with (directory / 'chartevents.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(CHART)
        writer.writerows(rows[::2] + rows[1::2])
    return directory
