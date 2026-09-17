"""Pinned official Caddy fixture only; never install globally or start a service."""
import hashlib
from io import BytesIO
import os
from pathlib import Path
import platform
import sys
import tarfile
import urllib.request

VERSION = '2.11.4'
CHECKSUMS = {
    ('Linux', 'x86_64'): ('linux_amd64', '8220d1f013b6f27510247b2360c9e0ca9f018feebd82515f07635318b34ff9777ccc8fd0b6e6f2486ce3a33fe389fbb7db12d05baa474f4587509fb4f5ebf1c9'),
    ('Darwin', 'arm64'): ('mac_arm64', '3190ae0df98b59ab4b6021556fa35adc3c526a4f3e138776b0eaec8a037cc26121cbbb1ad53453f565551b47d37d5ba4755e2c2c3652256737fe2ce9e53c8ec0'),
}


def main():
    destination = Path(sys.argv[1]).absolute()
    if destination.exists():
        raise SystemExit('Refuse to overwrite an existing Caddy fixture')
    name, checksum = CHECKSUMS[(platform.system(), platform.machine())]
    url = f'https://github.com/caddyserver/caddy/releases/download/v{VERSION}/caddy_{VERSION}_{name}.tar.gz'
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(64*1024**2+1)
    if len(data) > 64*1024**2 or hashlib.sha512(data).hexdigest() != checksum:
        raise SystemExit('Caddy fixture checksum failed')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=BytesIO(data)) as archive:
        member = archive.getmember('caddy')
        if not member.isfile() or member.size > 128*1024**2:
            raise SystemExit('Unexpected Caddy fixture')
        with destination.open('xb') as file:
            file.write(archive.extractfile(member).read())
    destination.chmod(0o700)
    print('Official Caddy 2.11.4 SHA512 verified; no service started')


if __name__ == '__main__':
    main()
