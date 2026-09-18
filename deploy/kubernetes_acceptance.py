"""Authored-only acceptance in a newly created, disposable kind cluster.

Never accepts an existing kubeconfig or cluster. Every Kubernetes operation uses
this run's private kubeconfig and explicitly named context. No real source data.
"""
import argparse
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
NODE_IMAGE = 'kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5'
NAMESPACE = 'ptm-acceptance'
RELEASE = 'research'
DEPLOYMENT = 'research-trajectory'
BASE = '/api/journey/recorded'
FEATURES = {'schema': 'recorded-clinical-features-1', 'features': [
    {'id': 'latest_value', 'weight': '2', 'scale': '10'},
    {'id': 'heart_rate', 'weight': '1', 'scale': '10'},
    {'id': 'respiratory_rate', 'weight': '1', 'scale': '5'}]}


def command(arguments, *, stdin=None, timeout=240):
    result = subprocess.run(arguments, input=stdin, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Never print stdin: it may contain the generated test owner Secret.
        raise RuntimeError(f'{arguments[0]} failed ({result.returncode}): {result.stderr[-4000:]}')
    return result.stdout


class EphemeralCluster:
    def __init__(self, root):
        self.name = 'ptm-ci-' + secrets.token_hex(6)
        self.context = 'kind-' + self.name
        self.kubeconfig = Path(root) / 'kubeconfig'
        self.namespace_created = False
        self.creation_attempted = False

    def kubectl_args(self, *args):
        return ['kubectl', '--kubeconfig', str(self.kubeconfig), '--context', self.context,
                '--namespace', NAMESPACE, *args]

    def kubectl(self, *args, **options):
        return command(self.kubectl_args(*args), **options)

    def helm(self, *args):
        return command(['helm', '--kubeconfig', str(self.kubeconfig), '--kube-context', self.context,
                        '--namespace', NAMESPACE, *args])

    def create(self, image):
        if self.name in command(['kind', 'get', 'clusters'], timeout=30).splitlines():
            raise ValueError('Refusing to reuse an existing kind cluster')
        self.creation_attempted = True
        command(['kind', 'create', 'cluster', '--name', self.name, '--kubeconfig', str(self.kubeconfig),
                 '--image', NODE_IMAGE, '--wait', '180s'], timeout=360)
        command(['kind', 'load', 'docker-image', image, '--name', self.name], timeout=240)
        self.kubectl('create', 'namespace', NAMESPACE)
        self.namespace_created = True

    def close(self):
        try:
            if self.namespace_created:
                self.kubectl('delete', 'namespace', NAMESPACE, '--wait=false', timeout=30)
        finally:
            if self.creation_attempted:
                command(['kind', 'delete', 'cluster', '--name', self.name], timeout=180)

    def ready_pod(self, excluded_uid=None):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            pods = json.loads(self.kubectl('get', 'pods', '--selector',
                              'app.kubernetes.io/instance=' + RELEASE, '-o', 'json', timeout=20))['items']
            ready = [pod for pod in pods if not pod['metadata'].get('deletionTimestamp')
                     and pod['metadata']['uid'] != excluded_uid
                     and any(c['type'] == 'Ready' and c['status'] == 'True'
                             for c in pod.get('status', {}).get('conditions', []))]
            if len(ready) == 1:
                return ready[0]
            time.sleep(.5)
        raise AssertionError('Replacement research pod did not become ready')

    @contextmanager
    def forward(self, pod):
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            port = available.getsockname()[1]
        process = subprocess.Popen(self.kubectl_args('port-forward', '--address', '127.0.0.1',
                                    'pod/' + pod['metadata']['name'], f'{port}:8080'),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = f'http://127.0.0.1:{port}'
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError('Ephemeral pod port-forward terminated')
                try:
                    with urlopen(url + '/healthz', timeout=2) as response:
                        if json.load(response)['status'] == 'ok':
                            break
                except (URLError, OSError):
                    pass
                time.sleep(.1)
            else:
                raise AssertionError('Ephemeral pod port-forward did not become ready')
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


class Client:
    def __init__(self, url, credential):
        self.url = url
        self.authorization = 'Basic ' + base64.b64encode(credential.encode()).decode()

    def request(self, path, body=None, *, authorization=True, raw=False):
        headers = {'Origin': self.url}
        if authorization:
            headers['Authorization'] = self.authorization if authorization is True else authorization
        if body is not None:
            headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode() if body is not None else None
        with urlopen(Request(self.url + path, data=data, headers=headers), timeout=30) as response:
            value = response.read()
            return value if raw else json.loads(value)

    def check_auth(self):
        wrong = 'Basic ' + base64.b64encode(b'owner:incorrect').decode()
        for path in ('/journey', BASE + '/history'):
            for authorization in (False, wrong):
                try:
                    self.request(path, authorization=authorization)
                except HTTPError as error:
                    assert error.code == 401, 'Missing/wrong owner credentials must receive 401'
                else:
                    raise AssertionError('Missing/wrong owner credentials were accepted')
        assert b'<html' in self.request('/journey', raw=True)

    def exercise(self):
        self.check_auth()
        job = self.request(BASE + '/jobs', {'profile': 'reviewed', 'stratum': 'synthetic',
                           'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}})
        deadline = time.monotonic() + 120
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.1)
            job = self.request(f"{BASE}/reviewed/jobs/{job['id']}")
        assert job['status'] == 'COMPLETED', 'Authored query did not complete'
        path = f"{BASE}/reviewed/jobs/{job['id']}"
        pattern = self.request(path + '/pattern')['default_pattern']
        pattern['baseline'].update(operator='ge', value_lexical='60')
        revised = self.request(BASE + '/pattern', {'profile': 'reviewed', 'job_id': job['id'], 'pattern': pattern})
        assert revised['status'] == 'COMPLETED', 'Authored pattern did not complete'
        path = f"{BASE}/reviewed/jobs/{revised['id']}"
        references = self.request(BASE + '/references', {'profile': 'reviewed', 'job_id': revised['id'],
                                                        'feature_profile': FEATURES})
        reference = next(row for row in references['anchors'] if row['feature_status'] == 'AVAILABLE')
        payload = {'profile': 'reviewed', 'job_id': revised['id'], 'reference_token': reference['token'],
                   'top_k': 1, 'feature_profile': FEATURES}
        comparison = self.request(BASE + '/compare', payload)
        assert comparison['ranked_patients'], 'No eligible authored comparison peer'
        assert {row['feature_id'] for row in comparison['ranked_patients'][0]['feature_contributions']} == {
            'latest_value', 'heart_rate', 'respiratory_rate'}
        exported = self.request(BASE + '/export', payload, raw=True)
        report = json.loads(exported)
        assert report['source_mode'] == 'synthetic' and 'clinical_features' in report['snapshot']
        return {'path': path, 'job': revised, 'payload': payload,
                'comparison': comparison, 'exported': exported}

    def verify_restored(self, retained):
        self.check_auth()
        assert self.request(retained['path']) == retained['job'], 'Retained job changed after pod replacement'
        assert self.request(BASE + '/compare', retained['payload']) == retained['comparison'], 'Comparison changed'
        assert self.request(BASE + '/export', retained['payload'], raw=True) == retained['exported'], 'Export bytes changed'
        history = self.request(BASE + '/history')
        assert history['durable'] and any(row['id'] == retained['job']['id'] for row in history['jobs'])


def run(image):
    if ':' not in image or image.endswith(':latest') or '@' in image:
        raise ValueError('Use an explicitly tagged local test image')
    with tempfile.TemporaryDirectory(prefix='ptm-kind-') as temporary:
        cluster = EphemeralCluster(temporary)
        try:
            cluster.create(image)
            credential = 'owner:' + secrets.token_urlsafe(48)
            secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'owner', 'namespace': NAMESPACE},
                      'type': 'Opaque', 'data': {'owner': base64.b64encode((credential + '\n').encode()).decode()}}
            cluster.kubectl('create', '-f', '-', stdin=json.dumps(secret))
            repository, tag = image.rsplit(':', 1)
            cluster.helm('install', RELEASE, str(ROOT / 'deploy/helm/patient-trajectory'), '--wait', '--timeout', '180s',
                         '--set-string', 'image.repository=' + repository, '--set-string', 'image.tag=' + tag,
                         '--set', 'image.pullPolicy=Never', '--set', 'localResearch.enabled=true',
                         '--set', 'localResearch.ownerSecret=owner', '--set-string',
                         'localResearch.clinicalFeaturesPath=/opt/trajectory/examples/clinical-features/authored-pack.json')
            assert json.loads(cluster.kubectl('get', 'services,ingresses', '-o', 'json'))['items'] == []
            claim = json.loads(cluster.kubectl('get', 'pvc', DEPLOYMENT, '-o', 'json'))
            assert claim['status']['phase'] == 'Bound' and claim['spec']['accessModes'] == ['ReadWriteOnce']
            old = cluster.ready_pod()
            with cluster.forward(old) as url:
                retained = Client(url, credential).exercise()
            cluster.kubectl('delete', 'pod', old['metadata']['name'], '--wait=true', '--timeout=90s', timeout=120)
            replacement = cluster.ready_pod(excluded_uid=old['metadata']['uid'])
            with cluster.forward(replacement) as url:
                Client(url, credential).verify_restored(retained)
            restored_claim = json.loads(cluster.kubectl('get', 'pvc', DEPLOYMENT, '-o', 'json'))
            assert restored_claim['metadata']['uid'] == claim['metadata']['uid']
            result = {'schema': 'kubernetes-research-acceptance-1', 'status': 'PASSED',
                      'environment': 'isolated-disposable-kind', 'source': 'authored-fixtures',
                      'node_image': NODE_IMAGE, 'application_image': image,
                      'checks': ['helm_install', 'owner_auth', 'pvc_bound', 'no_service_or_ingress',
                                 'distinct_variable_comparison', 'pattern_export', 'pod_replacement',
                                 'same_pvc', 'retained_job', 'identical_export_bytes'],
                      'institutional_cluster_validated': False}
        finally:
            cluster.close()
        result['ephemeral_cluster_deleted'] = True
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='patient-trajectory-matching:test')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = run(args.image)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True))
