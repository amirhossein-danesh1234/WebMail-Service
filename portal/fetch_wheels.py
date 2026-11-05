"""Download pinned compatible wheels from official PyPI; verify SHA256.

The small version-specific metadata avoids downloading the entire PyPI index
on the VPS's slow connection. No package code runs during this step.
Usage: python fetch_wheels.py /PRIVATE/OUTSIDE/REPOSITORY/wheels
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


def download(curl, item, path):
    """Bounded range requests tolerate this network's stalled large responses."""
    size = item['size']
    block = 16384
    ranges = [(start, min(start + block, size) - 1) for start in range(0, size, block)]

    def chunk(bounds):
        start, end = bounds
        data = b''
        for attempt in range(4):
            result = subprocess.run([curl, '-4fsS', '--http1.1', '--max-time', '15',
                '--range', f'{start + len(data)}-{end}', item['url']], capture_output=True)
            remaining = end - start + 1 - len(data)
            if len(result.stdout) > remaining:
                raise RuntimeError('Server did not honor requested range')
            data += result.stdout
            if len(data) == end - start + 1:
                return data
        raise RuntimeError('Incomplete package range')

    temp = path.with_suffix('.partial')
    with ThreadPoolExecutor(max_workers=8) as pool, temp.open('wb') as handle:
        for index, data in enumerate(pool.map(chunk, ranges)):
            handle.write(data)
            if index and index % 128 == 0:
                print(f'Progress: {path.name} {min((index + 1) * block, size)}/{size}', flush=True)
    temp.replace(path)

try:
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename
except ImportError:
    from pip._vendor.packaging.tags import sys_tags
    from pip._vendor.packaging.utils import parse_wheel_filename


def main():
    target = Path(sys.argv[1]).resolve()
    root = Path(__file__).resolve().parent
    if target == root or root in target.parents:
        raise SystemExit('Wheel cache must be outside the source repository')
    target.mkdir(parents=True, exist_ok=True)
    curl = shutil.which('curl.exe') or shutil.which('curl')
    if not curl:
        raise SystemExit('curl is required')
    tags = {tag: i for i, tag in enumerate(sys_tags())}
    manifest = []
    for line in (root / 'requirements.txt').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        package, version = line.split('==')
        print('Metadata: ' + package, flush=True)
        raw = subprocess.check_output([curl, '-4fsS', '--compressed', '--retry', '1',
            '--max-time', '45', f'https://pypi.org/pypi/{package}/{version}/json'])
        meta = json.loads(raw)
        choices = []
        for item in meta['urls']:
            if item['filename'].endswith('.whl') and not item.get('yanked'):
                _, _, _, wheel_tags = parse_wheel_filename(item['filename'])
                ranks = [tags[t] for t in wheel_tags if t in tags]
                if ranks:
                    choices.append((min(ranks), item))
        if not choices:
            raise SystemExit('No compatible wheel: ' + line)
        item = min(choices, key=lambda choice: choice[0])[1]
        if not item['url'].startswith('https://files.pythonhosted.org/'):
            raise SystemExit('Unexpected package origin')
        path = target / item['filename']
        expected = item['digests']['sha256']
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            print('Downloading: ' + item['filename'], flush=True)
            download(curl, item, path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit('Checksum mismatch: ' + path.name)
        manifest.append({'package': package, 'version': version,
                         'filename': path.name, 'sha256': expected})
        print('Verified: ' + line, flush=True)
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
