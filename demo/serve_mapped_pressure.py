"""Serve authored or explicitly configured mapped pressure records on localhost."""
import argparse
from pathlib import Path
from urllib.parse import urlparse
import serve
from mapped_pressure import MappedPressureService


class Handler(serve.Handler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/pressure' and getattr(serve.PRESSURE,'configuration',None):
            if not self.pressure_origin(): return
            if url.query: return self.send({'error':'Unexpected URL parameters'},400)
            script = (serve.ROOT/'pressure.js').read_text()+'\n'+(serve.ROOT/'configured_pressure.js').read_text()
            page = (serve.ROOT/'pressure.html').read_text().replace('/*PRESSURE_CSS*/',(serve.ROOT/'pressure.css').read_text()).replace('/*PRESSURE_JS*/',script)
            return self.send(page.encode(),mime='text/html')
        return super().do_GET()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument('--config',type=Path,help='Startup source, request, source-review and mapping configuration')
    sources.add_argument('--mapping-dir', type=Path, help='Startup-only mapping pack for the fixed authored source fixture')
    args = parser.parse_args(argv)
    service = MappedPressureService(args.mapping_dir,config=args.config)
    serve.PRESSURE.close(); serve.PRESSURE = service
    server = serve.ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Mapped pressure service: http://127.0.0.1:{server.server_port}/pressure', flush=True)
    try: server.serve_forever()
    finally: server.server_close(); service.close()


if __name__ == '__main__': main()
