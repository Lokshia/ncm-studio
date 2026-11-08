# Third-party notices

NCM Studio original code is licensed under GPL-3.0-or-later, without warranty.
See LICENSE. This does not relicense dependencies; their respective notices apply.

The NCM container layout and decryption algorithm were implemented with reference
to [taurusxin/ncmdump](https://github.com/taurusxin/ncmdump), licensed under MIT.
Its upstream notice is preserved in LICENSES/ncmdump-MIT.txt, including its original
placeholder copyright line. No upstream executable is bundled.

Runtime components:

| Component | Upstream / source | License family |
| --- | --- | --- |
| Python | https://www.python.org/downloads/source/ | PSF |
| PySide6 / Shiboken | https://code.qt.io/cgit/pyside/pyside-setup.git/ | LGPL-3.0 / GPL alternatives |
| Qt | https://download.qt.io/archive/qt/ | LGPL / GPL and third-party notices |
| Mutagen | https://github.com/quodlibet/mutagen | GPL-2.0-or-later |
| SoundFile | https://github.com/bastibe/python-soundfile | BSD-3-Clause |
| libsndfile | https://github.com/libsndfile/libsndfile | LGPL-2.1-or-later |
| NumPy and bundled math libraries | https://github.com/numpy/numpy | BSD and bundled notices |
| Cryptography | https://github.com/pyca/cryptography | Apache-2.0 / BSD |
| CFFI | https://foss.heptapod.net/pypy/cffi | MIT |
| Pillow | https://github.com/python-pillow/Pillow | MIT-CMU and bundled notices |
| PyInstaller bootloader | https://github.com/pyinstaller/pyinstaller | GPL with distribution exception |

The build copies installed dependency license notices and version metadata into
`licenses/`. General GPL/LGPL texts are included in LICENSES/. Qt is dynamically
linked in `_internal`; users may replace compatible library binaries and rebuild
from source. We do not prohibit reverse engineering for debugging modifications
to LGPL components. Dependency links identify upstream projects, not a promise
that all corresponding-source distribution obligations are already fulfilled.

Before public binary distribution, audit the exact bundled components and their
corresponding source, build instructions and notices. Preserve the source release
alongside each binary release; do not claim the whole bundle is MIT-licensed.
