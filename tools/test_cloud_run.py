"""The Cloud Run launcher: a byte relay in front of the owner-authenticated workspace."""
import asyncio
import base64
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from deploy import cloud_run

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class RelayTests(unittest.TestCase):
    def test_bytes_cross_the_relay_both_ways_and_a_half_close_is_passed_on(self):
        async def scenario():
            async def echo(reader, writer):
                data = await reader.read()  # until the client's half-close arrives
                writer.write(data.upper())
                await writer.drain()
                writer.close()
            upstream = await asyncio.start_server(echo, '127.0.0.1', 0)
            port = upstream.sockets[0].getsockname()[1]
            front = await asyncio.start_server(lambda r, w: cloud_run.relay(r, w, port), '127.0.0.1', 0)
            reader, writer = await asyncio.open_connection('127.0.0.1', front.sockets[0].getsockname()[1])
            writer.write(b'hello across the relay')
            writer.write_eof()
            answer = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            upstream.close()
            front.close()
            return answer
        self.assertEqual(asyncio.run(scenario()), b'HELLO ACROSS THE RELAY')

    def test_an_unreachable_workspace_closes_the_connection(self):
        async def scenario():
            closed = free_port()
            front = await asyncio.start_server(lambda r, w: cloud_run.relay(r, w, closed), '127.0.0.1', 0)
            reader, writer = await asyncio.open_connection('127.0.0.1', front.sockets[0].getsockname()[1])
            answer = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            front.close()
            return answer
        self.assertEqual(asyncio.run(scenario()), b'')


class LauncherTests(unittest.TestCase):
    """The launcher as Cloud Run runs it: a mounted secret, $PORT, and SIGTERM to stop."""

    def launch(self, secret_text):
        folder = Path(tempfile.mkdtemp())
        secret = folder / 'owner'
        secret.write_text(secret_text)
        port, app_port = free_port(), free_port()
        env = {**os.environ, 'PORT': str(port), 'PTM_APP_PORT': str(app_port), 'PTM_OWNER_SECRET': str(secret)}
        process = subprocess.Popen([sys.executable, '-m', 'deploy.cloud_run'], cwd=ROOT, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process, port

    def wait_for(self, port, process, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if process.poll() is not None:
                self.fail(f'launcher exited early: {process.stderr.read()[-800:]}')
            try:
                with urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2) as response:
                    return response.status
            except OSError:
                time.sleep(0.3)
        self.fail('launcher did not start')

    def test_the_workspace_answers_only_the_owner_and_stops_on_sigterm(self):
        secret = 'owner:' + 'k' * 48
        process, port = self.launch(secret + '\n')
        self.assertEqual(self.wait_for(port, process), 200)
        with self.assertRaises(HTTPError) as anonymous:
            urlopen(f'http://127.0.0.1:{port}/', timeout=5)
        self.assertEqual(anonymous.exception.code, 401)
        self.assertIn('Basic', anonymous.exception.headers.get('WWW-Authenticate', ''))
        wrong = Request(f'http://127.0.0.1:{port}/', headers={
            'Authorization': 'Basic ' + base64.b64encode(b'owner:' + b'x' * 48).decode()})
        with self.assertRaises(HTTPError) as rejected:
            urlopen(wrong, timeout=5)
        self.assertEqual(rejected.exception.code, 401)
        owner = Request(f'http://127.0.0.1:{port}/', headers={
            'Authorization': 'Basic ' + base64.b64encode(secret.encode()).decode()})
        with urlopen(owner, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b'<html', response.read().lower())
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=30), 0)
        process.stderr.close()

    def test_a_weak_secret_is_refused_before_the_workspace_starts(self):
        process, _ = self.launch('owner:short\n')
        self.assertNotEqual(process.wait(timeout=30), 0)
        with process.stderr:
            self.assertIn('Owner secret must contain', process.stderr.read())


if __name__ == '__main__':
    unittest.main()
