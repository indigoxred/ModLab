"""Read installed author documents without executing their embedded content."""
from html.parser import HTMLParser
from pathlib import Path
import configparser
import re
from urllib.parse import urlsplit

EXTENSIONS = {'.txt', '.md', '.rst', '.html', '.htm'}
LIMIT = 1024 * 1024


def download_page(mods_root, provider):
    """Return a Nexus files page identified by MO2 metadata, not a guessed mod name."""
    if not provider or provider in {'.', '..'} or any(c in provider for c in '/\\:'):
        return ''
    try:
        root = Path(mods_root).resolve(strict=True)
        mod = root / provider
        if mod.resolve(strict=True).parent != root or mod.is_symlink() or mod.is_junction():
            return ''
        meta = mod / 'meta.ini'
        if meta.is_symlink() or meta.stat().st_size > LIMIT:
            return ''
        config = configparser.RawConfigParser(strict=False)
        config.read_string(meta.read_text(encoding='utf-8-sig'))
        section = config['General']
        url = urlsplit(section.get('url', ''))
        match = re.fullmatch(r'/skyrimspecialedition/mods/([1-9]\d*)/?', url.path)
        if url.scheme == 'https' and url.netloc.casefold() == 'www.nexusmods.com' and match:
            modid = match[1]
        elif (section.get('gamename', '').casefold() in {'skyrimse', 'skyrim special edition'}
              and section.get('repository', 'Nexus').casefold() == 'nexus'
              and re.fullmatch(r'[1-9]\d*', section.get('modid', ''))):
            modid = section['modid']
        else:
            return ''
        return 'https://www.nexusmods.com/skyrimspecialedition/mods/' + modid + '?tab=files'
    except (OSError, ValueError, KeyError, configparser.Error):
        return ''  # Missing source metadata must not stop the actual binary inspection.


def documents(root):
    root = Path(root)
    found = []
    pending = [(root, 0)]
    while pending and len(found) < 256:
        directory, depth = pending.pop()
        for path in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
            if path.is_symlink() or path.is_junction():
                continue
            if path.is_file() and path.suffix.casefold() in EXTENSIONS:
                found.append(path)
                if len(found) == 256:
                    break
            elif path.is_dir() and depth < 2 and (depth > 0 or path.name.casefold() in
                    {'docs', 'doc', 'documentation', 'readmes', 'readme'}):
                pending.append((path, depth + 1))
    return tuple(sorted(found, key=lambda p: p.relative_to(root).as_posix().casefold()))


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        elif not self.hidden:
            if tag in {'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'tr'}:
                self.parts.append('\n')
            if tag == 'a':
                href = dict(attrs).get('href', '')
                if href.startswith(('https://', 'http://')):
                    self.parts.append(' [' + href + '] ')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {'p', 'div', 'li', 'h1', 'h2', 'h3', 'tr'} and not self.hidden:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def read_document(root, path):
    root, path = Path(root).resolve(strict=True), Path(path)
    try:
        relative = path.resolve(strict=True).relative_to(root)
    except ValueError:
        raise ValueError('The selected document is outside this mod.') from None
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink() or current.is_junction():
            raise ValueError('Linked documents must be inspected separately.')
    if path.suffix.casefold() not in EXTENSIONS:
        raise ValueError('This document format must be opened from the mod folder.')
    with path.open('rb') as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError('The document is too large for this preview; open the mod folder.')
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        text = data.decode('cp1252', errors='replace')
    if path.suffix.casefold() in {'.html', '.htm'}:
        parser = _Text(); parser.feed(text)
        text = ''.join(parser.parts)
    return text
