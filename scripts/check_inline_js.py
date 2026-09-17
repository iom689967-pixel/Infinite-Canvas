"""Syntax-check first-party inline browser scripts, without executing them."""
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys
import tempfile


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.current = None
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag != 'script':
            return
        attrs = dict(attrs)
        kind = attrs.get('type', '').lower()
        if 'src' not in attrs and kind in {'', 'text/javascript', 'application/javascript', 'module'}:
            self.current = (kind, [])

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.scripts.append((self.current[0], ''.join(self.current[1])))
            self.current = None


def main():
    root = Path(__file__).resolve().parents[1]
    count = 0
    with tempfile.TemporaryDirectory(prefix='mio-inline-syntax-') as directory:
        for html in sorted((root / 'static').glob('*.html')):
            parser = Scripts()
            parser.feed(html.read_text(encoding='utf-8'))
            for index, (kind, code) in enumerate(parser.scripts):
                if not code.strip():
                    continue
                file = Path(directory) / (html.stem + '-' + str(index) + ('.mjs' if kind == 'module' else '.js'))
                file.write_text(code, encoding='utf-8')
                result = subprocess.run([sys.argv[1], '--check', str(file)], capture_output=True, text=True)
                if result.returncode:
                    print(f'Inline JavaScript failed: {html.relative_to(root)} script {index}', file=sys.stderr)
                    print(result.stderr, file=sys.stderr)
                    return result.returncode
                count += 1
    print(f'Inline JavaScript syntax: {count} scripts passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
