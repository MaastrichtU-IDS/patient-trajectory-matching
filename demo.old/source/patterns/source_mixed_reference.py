"""SQLite reference over admitted raw rows, sharing only the explicit selection boundary.

No temporal network, local-coordinate normalizer, query compiler, or graph is used.
The caller supplies already validated exact source records and the query contract.
"""
from datetime import datetime
from decimal import Decimal, localcontext
import sqlite3


def coordinate(label):
    delta = datetime.fromisoformat(label) - datetime(2000, 1, 1)
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def compare(value, operator, threshold):
    a, b = Decimal(value), Decimal(threshold)
    return int({'lt': a < b, 'le': a <= b, 'eq': a == b, 'ge': a >= b, 'gt': a > b}[operator])


def difference(baseline, followup):
    with localcontext() as context:
        context.prec = 800
        return format(Decimal(followup) - Decimal(baseline), 'f')


def execute(interval_rows, measurement_rows, treatment_item, query):
    """Rows contain id plus raw CSV fields; return exact positive bindings, including empty follow-up."""
    with sqlite3.connect(':memory:') as db:
        db.create_function('decimal_test', 3, compare, deterministic=True)
        db.create_function('decimal_difference', 2, difference, deterministic=True)
        db.executescript('''
          CREATE TABLE treatment (id TEXT PRIMARY KEY, patient TEXT, stay TEXT, item TEXT, start INTEGER, end INTEGER);
          CREATE TABLE measurement (id TEXT PRIMARY KEY, patient TEXT, stay TEXT, item TEXT, time INTEGER, value TEXT, unit TEXT);
          CREATE TABLE baseline_item (item TEXT PRIMARY KEY);
          CREATE TABLE followup_item (item TEXT PRIMARY KEY);
        ''')
        db.executemany('INSERT INTO treatment VALUES (?,?,?,?,?,?)',
            [(r['id'], r['subject_id'], r['stay_id'], r['itemid'], coordinate(r['starttime']), coordinate(r['endtime'])) for r in interval_rows])
        db.executemany('INSERT INTO measurement VALUES (?,?,?,?,?,?,?)',
            [(r['id'], r['subject_id'], r['stay_id'], r['itemid'], coordinate(r['charttime']), r['valuenum'], r['valueuom']) for r in measurement_rows])
        b, f = query['baseline'], query['followup']
        db.executemany('INSERT INTO baseline_item VALUES (?)', [(i,) for i in b['item_ids']])
        db.executemany('INSERT INTO followup_item VALUES (?)', [(i,) for i in f['item_ids']])
        rows = db.execute('''
          WITH eligible AS (
            SELECT t.id AS treatment, t.patient, t.stay, t.start, t.end,
                   b.id AS baseline, b.item AS baseline_item, b.value AS baseline_value, b.unit AS baseline_unit
            FROM treatment t JOIN measurement b ON t.patient=b.patient AND t.stay=b.stay
            WHERE t.item=:treatment_item AND b.item IN (SELECT item FROM baseline_item)
              AND b.unit=:baseline_unit AND decimal_test(b.value,:operator,:threshold)
              AND t.start-b.time BETWEEN :baseline_min AND :baseline_max
          )
          SELECT e.patient,e.stay,e.treatment,e.baseline,f.id,
                 CASE WHEN f.id IS NOT NULL AND e.baseline_item=f.item AND e.baseline_unit=f.unit
                      THEN decimal_difference(e.baseline_value,f.value) ELSE NULL END AS delta,
                 CASE WHEN f.id IS NOT NULL AND e.baseline_item=f.item AND e.baseline_unit=f.unit
                      THEN f.unit ELSE NULL END AS delta_unit
          FROM eligible e LEFT JOIN measurement f
            ON f.patient=e.patient AND f.stay=e.stay AND f.id<>e.baseline
           AND f.item IN (SELECT item FROM followup_item) AND f.unit=:followup_unit
           AND f.time-(CASE WHEN :anchor='start' THEN e.start ELSE e.end END) BETWEEN :followup_min AND :followup_max
           AND (:within_interval=0 OR (e.start<=f.time AND f.time<e.end))
          ORDER BY e.patient,e.stay,e.treatment,e.baseline,f.id
        ''', {'treatment_item': treatment_item, 'baseline_unit': b['unit_lexical'], 'operator': b['operator'],
              'threshold': b['value_lexical'], 'baseline_min': b['min_before_start_us'], 'baseline_max': b['max_before_start_us'],
              'followup_unit': f['unit_lexical'], 'anchor': f['anchor'], 'followup_min': f['min_after_anchor_us'],
              'followup_max': f['max_after_anchor_us'], 'within_interval': int(f['within_interval'])}).fetchall()
    return [list(row) for row in rows]
