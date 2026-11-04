"""Cloud Run launcher: the owner-authenticated workspace behind a loopback relay.

The workspace refuses owner authentication on anything but a loopback address, so that
its Basic credential never crosses a network in the clear. Cloud Run terminates TLS at
Google's front end and delivers requests to 0.0.0.0:$PORT. This launcher keeps the
workspace on 127.0.0.1 and relays the platform's connections to it byte for byte. It
parses no HTTP: authentication, routing and errors stay with the workspace, which sees
each request exactly as the platform sent it.

The owner secret is mounted from Secret Manager and copied into an owner-private file
before the workspace starts, exactly as the local release does. State lives in a fresh
private directory on the instance's in-memory filesystem, so it ends with the instance.
The workspace is a single-owner, single-process design: deploy it with one instance, or
a session's revisions would be spread across processes that do not share them.
"""
import asyncio
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile

from deploy.local_runtime import prepare_credentials

SECRET = os.environ.get('PTM_OWNER_SECRET', '/run/secrets/ptm-owner/owner')
APP_PORT = int(os.environ.get('PTM_APP_PORT', '8081'))
CHUNK = 65536


async def pipe(reader, writer):
    """Copy one direction until it ends, then pass the end on as a half-close."""
    try:
        while data := await reader.read(CHUNK):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()
        except (ConnectionError, OSError):
            pass


async def relay(client_reader, client_writer, port):
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection('127.0.0.1', port)
    except OSError:
        client_writer.close()
        return
    try:
        await asyncio.gather(pipe(client_reader, upstream_writer), pipe(upstream_reader, client_writer))
    finally:
        for writer in (upstream_writer, client_writer):
            writer.close()


async def ready(port, process, timeout=120):
    """Wait until the workspace accepts connections, failing if it exits first."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'workspace exited with status {process.returncode} before listening')
        try:
            _, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.close()
            return
        except OSError:
            await asyncio.sleep(0.2)
    raise RuntimeError('workspace did not start listening in time')


async def serve(listen_port, app_port, process, host='0.0.0.0'):
    """Relay until the platform stops the instance or the workspace exits.

    Returns True when stopped by a signal. Open connections are not waited for: an
    instance being stopped has already been taken out of rotation, and an idle
    keep-alive connection would otherwise hold the shutdown open indefinitely.
    """
    await ready(app_port, process)
    server = await asyncio.start_server(lambda r, w: relay(r, w, app_port), host, listen_port,
                                        family=socket.AF_INET, reuse_address=True)
    loop = asyncio.get_running_loop()
    stop, signalled = asyncio.Event(), []
    for name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(name, lambda: (signalled.append(True), stop.set()))
    exited = loop.run_in_executor(None, process.wait)
    exited.add_done_callback(lambda _: stop.set())
    await stop.wait()
    server.close()
    if process.poll() is None:
        process.terminate()
        try:
            await asyncio.wait_for(asyncio.shield(exited), 10)
        except asyncio.TimeoutError:
            process.kill()
    return bool(signalled)


def main():
    if os.getuid() == 0:
        raise ValueError('The workspace must run as a non-root user')
    private = Path(tempfile.mkdtemp(prefix='ptm-owner-'))
    credential = prepare_credentials(SECRET, private / 'owner')
    state = Path(tempfile.mkdtemp(prefix='ptm-state-'))
    process = subprocess.Popen([sys.executable, '-m', 'app.server', '--host', '127.0.0.1',
                                '--port', str(APP_PORT), '--state-dir', str(state),
                                '--auth-file', str(credential)])
    try:
        signalled = asyncio.run(serve(int(os.environ.get('PORT', '8080')), APP_PORT, process))
    finally:
        if process.poll() is None:
            process.kill()
    # A requested stop is a clean exit; the workspace dying on its own is not.
    sys.exit(0 if signalled else (process.returncode or 1))


if __name__ == '__main__':
    main()
