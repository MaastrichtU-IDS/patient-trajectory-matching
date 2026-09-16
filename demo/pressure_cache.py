"""Bounded, process-local storage of complete reviewed query snapshots."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ('demo/pressure.py', 'demo/pressure_cache.py', 'data/clinical-source-demo-pin.json',
         'patterns/prepared_mixed_query.py')


def implementation_stamp():
    # The session separately binds every engine/schema/ontology artifact in its FILES.
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}


class ResultCache:
    def __init__(self, max_entries=3, max_bytes=32 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.bytes = 0

    def clear(self):
        self.entries.clear()
        self.bytes = 0

    def get(self, key):
        if key not in self.entries:
            return None
        self.entries.move_to_end(key)
        return deepcopy(self.entries[key][0])

    def put(self, key, result, origin_job_id):
        if result['status'] != 'COMPLETED':
            return False
        entry = {'result': result, 'origin_job_id': origin_job_id}
        size = len(json.dumps(entry, sort_keys=True, separators=(',', ':')).encode())
        if size > self.max_bytes:
            return False
        if key in self.entries:
            self.bytes -= self.entries.pop(key)[1]
        while self.entries and (len(self.entries) >= self.max_entries or self.bytes + size > self.max_bytes):
            self.bytes -= self.entries.popitem(last=False)[1][1]
        self.entries[key] = (deepcopy(entry), size)
        self.bytes += size
        return True
