"""Maintainer-only acquisition of unmodified public license texts. Not used at runtime."""
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    'LICENSE': ('https://raw.githubusercontent.com/qt/qtbase/dev/LICENSES/GPL-3.0-only.txt', 'GNU GENERAL PUBLIC LICENSE'),
    'LICENSES/LGPL-3.0.txt': ('https://raw.githubusercontent.com/qt/qtbase/dev/LICENSES/LGPL-3.0-only.txt', 'GNU LESSER GENERAL PUBLIC LICENSE'),
    'LICENSES/LGPL-2.1.txt': ('https://raw.githubusercontent.com/libsndfile/libsndfile/master/COPYING', 'GNU LESSER GENERAL PUBLIC LICENSE'),
}

if __name__ == '__main__':
    for name, (url, marker) in SOURCES.items():
        text = urlopen(url, timeout=30).read().decode('utf-8')
        if marker not in text or len(text) < 5000:
            raise ValueError('Unexpected license text: ' + url)
        path = ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        print(name)
