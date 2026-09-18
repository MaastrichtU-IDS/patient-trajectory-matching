"""Linux Docker acceptance: owner auth, durable evidence, restart and backup restore.

Uses authored inputs only. Requires a built image; never publishes patient evidence.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from deploy.state_archive import snapshot, restore, NAME

BASE = '/api/journey/recorded'


def run(image):
    name = 'ptm-acceptance-' + secrets.token_hex(5)
    url = 'http://127.0.0.1:8080'
    with tempfile.TemporaryDirectory(prefix='ptm-container-') as folder:
        root = Path(folder)
        state = root / 'state'
        state.mkdir(mode=0o700)
        credential = root / 'owner'
        fd = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        secret = 'owner:' + secrets.token_urlsafe(48)
        with os.fdopen(fd, 'w') as output:
            output.write(secret + '\n')
        header = 'Basic ' + base64.b64encode(secret.encode()).decode()

        def docker(*args):
            return subprocess.run(['docker', *args], check=True, capture_output=True, text=True, timeout=180).stdout

        def request(path, body=None, auth=header):
            headers = {'Origin': url}
            if auth is not None:
                headers['Authorization'] = auth
            if body is not None:
                headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode() if body is not None else None
            with urlopen(Request(url + path, data, headers), timeout=10) as response:
                return json.load(response)

        def started(folder):
            docker('run', '--detach', '--name', name, '--network', 'host',
                   '--user', f'{os.getuid()}:{os.getgid()}', '--read-only',
                   '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges',
                   '--env', 'PTM_CLINICAL_FEATURES=/opt/trajectory/examples/clinical-features/authored-pack.json',
                   '--mount', f'type=bind,src={folder},dst=/var/lib/trajectory',
                   '--mount', f'type=bind,src={credential},dst=/run/secrets/owner,readonly',
                   image, 'python', '-m', 'deploy.local_runtime')
            ready()

        def ready():
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    if request('/healthz', auth=None)['status'] == 'ok':
                        break
                except (URLError, OSError):
                    pass
                time.sleep(.2)
            else:
                raise AssertionError('Container did not become healthy')
            for auth in (None, 'Basic ' + base64.b64encode(b'owner:incorrect').decode()):
                try:
                    request(BASE + '/history', auth=auth)
                except HTTPError as error:
                    assert error.code == 401
                else:
                    raise AssertionError('Owner authentication did not reject request')

        try:
            started(state)
            job = request(BASE + '/jobs', {'profile': 'reviewed', 'stratum': 'synthetic',
                          'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}})
            deadline = time.monotonic() + 120
            while job['status'] == 'RUNNING' and time.monotonic() < deadline:
                time.sleep(.1)
                job = request(f"{BASE}/reviewed/jobs/{job['id']}")
            assert job['status'] == 'COMPLETED', 'Recorded computation did not complete'
            path = f"{BASE}/reviewed/jobs/{job['id']}"
            pattern = request(path + '/pattern')['default_pattern']
            pattern['baseline']['operator'] = 'ge'
            pattern['baseline']['value_lexical'] = '60'
            revised = request(BASE + '/pattern', {'profile': 'reviewed', 'job_id': job['id'], 'pattern': pattern})
            assert revised['status'] == 'COMPLETED'
            path = f"{BASE}/reviewed/jobs/{revised['id']}"
            features = {'schema': 'recorded-clinical-features-1', 'features': [
                {'id': 'latest_value', 'weight': '2', 'scale': '10'},
                {'id': 'heart_rate', 'weight': '1', 'scale': '10'},
                {'id': 'respiratory_rate', 'weight': '1', 'scale': '5'}]}
            refs = request(BASE + '/references', {'profile': 'reviewed', 'job_id': revised['id'],
                                                  'feature_profile': features})
            selected = next(anchor for anchor in refs['anchors'] if anchor['feature_status'] == 'AVAILABLE')
            payload = {'profile': 'reviewed', 'job_id': revised['id'],
                       'reference_token': selected['token'], 'top_k': 1, 'feature_profile': features}
            comparison = request(BASE + '/compare', payload)
            assert comparison['ranked_patients'], 'Distinct-variable comparison returned no eligible peer'
            assert {row['feature_id'] for row in comparison['ranked_patients'][0]['feature_contributions']} == {
                'latest_value', 'heart_rate', 'respiratory_rate'}
            original = request(BASE + '/export', payload)
            assert original['report_id']
            assert 'clinical_features' in original['snapshot']
            docker('stop', '--time', '30', name)
            backup = root / 'backup.sqlite3'
            snapshot(state / 'recorded' / NAME, backup)
            docker('start', name)
            ready()
            assert request(path) == revised, 'Job changed after restart'
            assert request(BASE + '/compare', payload) == comparison, 'Comparison changed after restart'
            assert request(BASE + '/export', payload) == original, 'Evidence changed after restart'
            docker('stop', '--time', '30', name)
            docker('rm', name)
            restored = root / 'restored'
            restored.mkdir(mode=0o700)
            restore(backup, restored / 'recorded')
            started(restored)
            assert request(path) == revised, 'Job changed after restore'
            assert request(BASE + '/compare', payload) == comparison, 'Comparison changed after restore'
            assert request(BASE + '/export', payload) == original, 'Evidence changed after restore'
            assert request(BASE + '/history')['durable'] is True
            print(json.dumps({'schema': 'container-release-acceptance-1', 'auth': 'passed',
                              'recorded_pattern_export': 'passed', 'distinct_variable_comparison': 'passed',
                              'restart': 'passed',
                              'backup_restore': 'passed', 'source': 'authored-fixtures'}))
        finally:
            subprocess.run(['docker', 'rm', '--force', name], capture_output=True, check=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='patient-trajectory-matching:test')
    run(parser.parse_args().image)
