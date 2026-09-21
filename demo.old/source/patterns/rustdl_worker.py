"""Private, time-bounded-by-parent rustDL worker. No remote ontology loading."""
import hashlib
import json
from pathlib import Path
import platform
import sys
import tempfile
import warnings

VERSION = '0.4.28'


def run(request):
    import rustdl
    import rustdl._native
    if rustdl.__version__ != VERSION:
        return {'status': 'UNSUPPORTED_BACKEND_VERSION', 'version': rustdl.__version__}
    with tempfile.TemporaryDirectory(prefix='trajectory-semantic-') as directory:
        path = Path(directory) / 'module.ofn'
        path.write_text(request['ofn'], encoding='utf-8')
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            dropped = rustdl.dropped_axioms(str(path))
            if any(dropped.values()):
                return {'status': 'DROPPED_AXIOMS', 'dropped': dropped}
            consistent = rustdl.is_consistent(str(path))
            memberships = {cls: sorted(rustdl.instances_of(str(path), cls))
                           for cls in request['classes']} if consistent else {}
        return {'status': 'OK', 'consistent': consistent, 'memberships': memberships,
                'dropped': dropped, 'warnings': [str(w.message) for w in caught],
                'backend': {'name': 'rustdl', 'version': rustdl.__version__,
                            'python': platform.python_version(),
                            'native_sha256': hashlib.sha256(Path(rustdl._native.__file__).read_bytes()).hexdigest(),
                            'wrapper_sha256': hashlib.sha256(Path(rustdl.__file__).read_bytes()).hexdigest(),
                            'instance_api_reports_truncation': False}}


def main():
    try:
        result = run(json.load(sys.stdin))
    except Exception as error:
        result = {'status': 'BACKEND_ERROR', 'error_type': type(error).__name__, 'reason': str(error)}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
