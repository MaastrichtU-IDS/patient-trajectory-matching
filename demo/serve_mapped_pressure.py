"""Serve the authored mapped pressure demonstration on localhost."""
import argparse
from pathlib import Path
import serve
from mapped_pressure import MappedPressureService


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--mapping-dir', type=Path, help='Startup-only mapping pack for the fixed authored source fixture')
    args = parser.parse_args(argv)
    service = MappedPressureService(args.mapping_dir)
    serve.PRESSURE.close(); serve.PRESSURE = service
    server = serve.ThreadingHTTPServer(('127.0.0.1', args.port), serve.Handler)
    print(f'Authored mapped pressure demo: http://127.0.0.1:{server.server_port}/pressure', flush=True)
    try: server.serve_forever()
    finally: server.server_close(); service.close()


if __name__ == '__main__': main()
