"""Content-versioned program assets only. Never classify user data by extension."""
from functools import lru_cache
import hashlib
from pathlib import Path
import re
from urllib.parse import parse_qs

from instance_access import PUBLIC_STATIC, WORKBENCH_STATIC

PRIVATE_CACHE = 'no-store, private'
IMMUTABLE_CACHE = 'public, max-age=31536000, immutable'
SHORT_CACHE = 'public, max-age=300, must-revalidate'
EXTENSIONS = {'.js', '.css', '.ttf', '.woff', '.woff2', '.ico', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'}
PROGRAM_RESOURCES = frozenset(p for p in WORKBENCH_STATIC | PUBLIC_STATIC |
    {'/static/js/api-settings.js', '/static/js/instance-api-settings.js'} if Path(p).suffix in EXTENSIONS)


class ProgramAssets:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.files = {}
        digest = hashlib.sha256(b'mio-program-assets-v1\0' + Path(__file__).read_bytes())
        for url in sorted(PROGRAM_RESOURCES):
            path = self.root / url.removeprefix('/static/')
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(self.root):
                continue
            data = path.read_bytes()
            self.files[url] = data
            digest.update(url.encode() + b'\0' + hashlib.sha256(data).digest())
        # Bundle identity includes dependencies; changing a font or i18n module also busts its loader.
        self.version = digest.hexdigest()[:24]
        alternatives = '|'.join(re.escape(p) for p in sorted(self.files, key=len, reverse=True)) or r'(?!)'
        self.pattern = re.compile(r'(?P<url>' + alternatives + r')(?:\?[^\s\"\'<>)]*)?(?=[\s\"\'<>)]|$)')

    def rewrite(self, text):
        return self.pattern.sub(lambda m: m['url'] + '?v=' + self.version, text)

    def html(self, text):
        text = self.rewrite(text)
        return re.sub(r'<head(?:\s[^>]*)?>', lambda m: m[0] +
                      '<meta name="mio-asset-version" content="' + self.version + '">', text, count=1, flags=re.I)

    def cache_control(self, path, query, method, status, has_cookie=False):
        if method not in {'GET', 'HEAD'} or status not in {200, 304} or has_cookie or path not in self.files:
            return PRIVATE_CACHE
        values = parse_qs(query.decode() if isinstance(query, bytes) else query)
        return IMMUTABLE_CACHE if values.get('v') == [self.version] else SHORT_CACHE

    @lru_cache(maxsize=128)
    def representation(self, path):
        data = self.files[path]
        if path.endswith('.css'):
            data = self.rewrite(data.decode('utf-8')).encode('utf-8')
        return data, '"' + hashlib.sha256(data).hexdigest() + '"'


@lru_cache(maxsize=8)
def program_assets(root):
    # Immutable until process restart. Production deploys restart gateway/workers together.
    return ProgramAssets(root)
