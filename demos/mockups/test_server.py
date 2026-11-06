"""The mockup server: static files, the engine's health, and matching over HTTP."""
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import server  # noqa: E402

EXAMPLE = HERE.parents[1] / 'examples/trajectory-graph'


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.serve('127.0.0.1', 0)
        cls.url = f'http://127.0.0.1:{cls.httpd.server_port}'
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, body, content_type='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        with urlopen(Request(self.url + '/api/trajectory-graph/match', data=data,
                             headers={'Content-Type': content_type}), timeout=30) as response:
            return json.load(response)

    def refused(self, body, content_type='application/json'):
        with self.assertRaises(HTTPError) as caught:
            self.post(body, content_type)
        return caught.exception.code, json.load(caught.exception)['error']

    def test_static_pages_and_health_are_served(self):
        with urlopen(self.url + '/trajectory/index.html', timeout=10) as response:
            self.assertIn(b'<html', response.read().lower())
        with urlopen(self.url + '/api/health', timeout=10) as response:
            self.assertEqual(json.load(response), {'profile': 'trajectory-graph-1.0'})

    def test_matching_returns_the_engines_results(self):
        query = json.loads((EXAMPLE / 'query.json').read_text())
        patients = json.loads((EXAMPLE / 'patients.json').read_text())
        result = self.post({'query': query, 'patients': patients})
        self.assertEqual(result['profile'], 'trajectory-graph-1.0')
        self.assertEqual([r['status'] for r in result['results']], ['exact', 'possible', 'relaxed', 'none', 'excluded'])

    def test_requests_outside_the_profile_are_refused_by_name(self):
        query = json.loads((EXAMPLE / 'query.json').read_text())
        self.assertEqual(self.refused({'query': {**query, 'profile': 'other'}, 'patients': []}), (400, 'UNSUPPORTED_PROFILE'))
        self.assertEqual(self.refused({'query': query}), (400, 'REQUEST_FIELDS_MUST_BE_QUERY_AND_PATIENTS'))
        self.assertEqual(self.refused(b'{not json'), (400, 'MALFORMED_REQUEST'))
        self.assertEqual(self.refused(b'{}', 'text/plain'), (415, 'JSON_REQUIRED'))


if __name__ == '__main__':
    unittest.main()
