"""Install and verify the full demo in .venv, then launch with that interpreter."""
from pathlib import Path
import argparse, subprocess, sys, tempfile, venv

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--setup-only', action='store_true')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        print('Full-demo setup uses Python 3.12. Run: python3.12 demo/start_demo.py')
        print('On Windows: py -3.12 demo/start_demo.py')
        print(f'Current interpreter: {sys.executable} ({sys.version.split()[0]})')
        return 2
    env = ROOT / '.venv'
    python = env / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.exists():
        print('Creating the demo environment...', flush=True)
        venv.EnvBuilder(with_pip=True).create(env)
    probe = subprocess.run([str(python), '-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
                           capture_output=True, text=True)
    if probe.returncode or probe.stdout.strip() != '3.12':
        print('The existing .venv is not a working Python 3.12 environment.')
        print('Move it aside, then rerun this launcher to create a fresh demo environment.')
        print(probe.stderr)
        return 2
    print('Installing pinned graph and Rust dependencies...', flush=True)
    result = subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                             '-r', str(SOURCE / 'patterns/requirements-semantic.lock.txt')])
    if result.returncode:
        print('\nSetup stopped. The installation error above is the actual cause.')
        print('If rustdl has no compatible distribution for your platform, do not change the pinned version.')
        print('The recorded demonstration remains available by opening Patient_Trajectory_Demo.html.')
        return result.returncode
    for module in ('patterns.pro_solid', 'patterns.semantic_support'):
        print(f'Checking {module}...', flush=True)
        with tempfile.TemporaryDirectory(prefix='trajectory-setup-') as directory:
            try:
                result = subprocess.run([str(python), '-m', module, '--output', directory],
                                         cwd=SOURCE, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                print('Setup stopped: this pipeline exceeded the 60-second startup check.')
                return 2
            print(result.stdout, end='')
            if result.stderr:
                print(result.stderr, file=sys.stderr, end='')
            if result.returncode:
                report = Path(directory) / 'result.json'
                if report.exists():
                    import json
                    data = json.loads(report.read_text())
                    print(json.dumps({'status': data.get('status'),
                                      'backend': data.get('semantic_support', {}).get('backend_result')}, indent=2))
                print('Setup stopped because this pipeline did not pass. Keep the diagnostic output above.')
                return result.returncode
    print('Both pipelines passed.', flush=True)
    if args.setup_only:
        return 0
    print(f'Starting with {python}', flush=True)
    try:
        return subprocess.call([str(python), str(ROOT / 'serve.py'), '--port', str(args.port)], cwd=ROOT)
    except KeyboardInterrupt:
        return 0

if __name__ == '__main__':
    raise SystemExit(main())
