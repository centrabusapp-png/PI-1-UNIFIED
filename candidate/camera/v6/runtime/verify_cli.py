#!/usr/bin/env python3
"""Camera-free entry-point/SIGTERM check; temporary JPGs cleaned on exit."""
import sys
sys.dont_write_bytecode = True
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent


def main():
    manifest = json.loads((HERE.parent/'centrabus_fast_runtime_v5_reliability_dev_20261019'
                           /'analysis/manifest.json').read_text())
    source = next(m['image_path'] for m in manifest if m['source_dataset'] == 'fast_batch_2')
    with tempfile.TemporaryDirectory(prefix='centrabus_cli_') as root:
        # Deliberately omit a cycle limit: stop an otherwise continuous process
        # externally, after its twelfth printed reading.
        process = subprocess.Popen([sys.executable, '-B', '-u', str(HERE/'fast_reader.py'),
                                    '--replay-image', source, '--output-root', root, '--diagnostics'],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   bufsize=0)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        count, lines, pending = 0, [], b''
        diagnostic_done = False
        deadline = time.monotonic()+120
        try:
            while count < 12 or not diagnostic_done:
                if time.monotonic() > deadline:
                    raise TimeoutError('No twelfth continuous reading within 120 seconds')
                if not selector.select(timeout=1):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('CLI ended before twelve cycles')
                pending += chunk
                while b'\n' in pending:
                    raw, pending = pending.split(b'\n', 1)
                    line = raw.decode('utf-8', errors='replace') + '\n'
                    lines.append(line)
                    # Exclude startup prose and stderr errors from the count.
                    if line.startswith('OIL: '):
                        count += 1
                    if line.startswith('Diagnostics 000012 |') and ' | OIL ' in line:
                        diagnostic_done = True
            process.send_signal(signal.SIGTERM)
            remainder, _ = process.communicate(timeout=15)
            lines.append((pending + remainder).decode('utf-8', errors='replace'))
        finally:
            selector.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        output = ''.join(lines)
        files = [p for p in Path(root).rglob('*') if p.is_file()]
        assert process.returncode == 0, output
        assert 'continuous, one capture/cycle' in output and 'CENTRABUS\n' in output
        assert 'Monitoring stopped; releasing camera.' in output, output
        assert 'Diagnostics 000012' in output, output
        assert count == 12 and len(files) == 10
        assert all(p.suffix == '.jpg' and p.name.endswith('_annotated.jpg') for p in files)
        print('PASS: CLI continuous default reached cycle 12; SIGTERM exit=0; diagnostics through cycle 12; ten JPGs only; no camera opened.')


if __name__ == '__main__':
    main()
