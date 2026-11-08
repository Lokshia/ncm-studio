import base64
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from mutagen.flac import FLAC
import numpy as np
from PIL import Image
import soundfile as sf

from ncmstudio.core import (CORE_KEY, META_KEY, Cancelled, convert, file_hash, inspect_track,
                            parse_ncm, scan, validate_roots)


def make_fixture(root, name='测试歌曲.ncm', bits=24, codec='FLAC', bad_md5=False, metadata=True, cover=True):
    """Self-generated audio only; no commercial music is included in the test suite."""
    root = Path(root)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if codec == 'FLAC':
        buffer = io.BytesIO()
        signal = np.sin(np.arange(10000) * 0.18) * 0.3
        sf.write(buffer, np.column_stack((signal, -signal)), 44100, format='FLAC', subtype=f'PCM_{bits}')
        payload = bytearray(buffer.getvalue())
        if bad_md5:
            payload[26] ^= 1
    else:
        payload = bytearray(b'ID3' + bytes(100))
    secret = b'test-audio-key-123'

    def encrypt(data, key):
        padder = PKCS7(128).padder()
        data = padder.update(data) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        return cipher.update(data) + cipher.finalize()

    key_block = bytes(v ^ 0x64 for v in encrypt(b'neteasecloudmusic' + secret, CORE_KEY))
    meta = {'musicName': '测试歌曲', 'artist': [['测试歌手', 1]], 'album': '测试专辑', 'format': codec.lower()}
    meta_block = b''
    if metadata:
        content = b'music:' + json.dumps(meta).encode()
        encoded = b"163 key(Don't modify):" + base64.b64encode(encrypt(content, META_KEY))
        meta_block = bytes(v ^ 0x63 for v in encoded)
    cover_data = b''
    if cover:
        picture = io.BytesIO()
        Image.new('RGB', (32, 32), '#20815f').save(picture, format='PNG')
        cover_data = picture.getvalue()
    box = list(range(256))
    cursor = 0
    for index in range(256):
        cursor = (cursor + box[index] + secret[index % len(secret)]) % 256
        box[index], box[cursor] = box[cursor], box[index]
    for index in range(len(payload)):
        j = (index + 1) % 256
        payload[index] ^= box[(box[j] + box[(box[j] + j) % 256]) % 256]
    u32 = lambda value: value.to_bytes(4, 'little')
    path.write_bytes(b'CTENFDAM' + bytes(2) + u32(len(key_block)) + key_block +
                     u32(len(meta_block)) + meta_block + bytes(5) +
                     u32(len(cover_data)) * 2 + cover_data + payload)
    return path


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ncm-studio-test-')
        self.root = Path(self.temp.name).resolve()
        self.source, self.output = self.root / 'input', self.root / 'output'
        self.source.mkdir()

    def tearDown(self):
        self.assertTrue(self.root.name.startswith('ncm-studio-test-'))
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.temp.cleanup()

    def test_convert_16_and_24bit_preserve_frames_tags_cover_lyrics(self):
        for bits in (16, 24):
            path = make_fixture(self.source, f'专辑 {bits}/同名.ncm', bits)
            path.with_suffix('.lrc').write_text('[00:01.00]测试', encoding='utf-8')
            original = file_hash(path)
            track = inspect_track(path, self.source)
            self.assertTrue(track.convertible)
            result = convert(track, self.source, self.output)
            self.assertTrue(result['full_decode'])
            target = Path(result['output'])
            audio = FLAC(target)
            self.assertEqual(audio.info.bits_per_sample, bits)
            self.assertEqual(audio['title'], ['测试歌曲'])
            self.assertEqual(len(audio.pictures), 1)
            self.assertEqual(file_hash(path), original)
            self.assertEqual(path.with_suffix('.lrc').read_bytes(), target.with_suffix('.lrc').read_bytes())

    def test_scan_bad_and_mp3_and_missing_metadata(self):
        make_fixture(self.source, 'nested/小写.ncm', metadata=False)
        make_fixture(self.source, '大写.NCM', codec='MP3')
        (self.source / 'bad.ncm').write_bytes(b'bad')
        (self.source / 'ignored.txt').write_text('unused')
        tracks = list(scan(self.source))
        self.assertEqual(len(tracks), 3)
        self.assertEqual(sum(t.convertible for t in tracks), 1)
        self.assertEqual({t.status for t in tracks}, {'可转换', '不转换', '无法读取'})

    def test_md5_failure_not_published(self):
        path = make_fixture(self.source, bad_md5=True)
        with self.assertRaisesRegex(ValueError, '校验失败'):
            convert(inspect_track(path, self.source), self.source, self.output)
        self.assertFalse(list(self.output.rglob('*.flac')))
        self.assertFalse(list((self.output / '.ncm-studio' / 'work').iterdir()))

    def test_resume_and_existing_output_protection(self):
        path = make_fixture(self.source)
        track = inspect_track(path, self.source)
        result = convert(track, self.source, self.output)
        self.assertEqual(convert(track, self.source, self.output)['status'], '已完成，跳过')
        target = Path(result['output'])
        with target.open('ab') as stream:
            stream.write(b'changed')
        changed = file_hash(target)
        with self.assertRaises(FileExistsError):
            convert(track, self.source, self.output)
        self.assertEqual(file_hash(target), changed)

    def test_cancel_during_decode(self):
        path = make_fixture(self.source)
        stop = threading.Event()

        def progress(phase, fraction):
            if phase == '整曲无损校验':
                stop.set()
        with self.assertRaises(Cancelled):
            convert(inspect_track(path, self.source), self.source, self.output, stop, progress)
        self.assertFalse(list(self.output.rglob('*.flac')))

    def test_overlap_and_outside_source_rejected(self):
        with self.assertRaises(ValueError):
            validate_roots(self.source, self.source / 'output')
        path = make_fixture(self.root, 'outside.ncm')
        with self.assertRaises(ValueError):
            convert(inspect_track(path, self.root), self.source, self.output)

    def test_source_change_detected(self):
        path = make_fixture(self.source)
        modified = [False]

        def progress(phase, fraction):
            if phase == '整曲无损校验' and not modified[0]:
                with path.open('ab') as stream:
                    stream.write(b'changed')
                modified[0] = True
        with self.assertRaisesRegex(ValueError, '源文件'):
            convert(inspect_track(path, self.source), self.source, self.output, progress=progress)
        self.assertFalse(list(self.output.rglob('*.flac')))

    def test_lyrics_conflict_kept_and_late_lyrics_copied(self):
        path = make_fixture(self.source)
        track = inspect_track(path, self.source)
        result = convert(track, self.source, self.output)
        target = Path(result['output']).with_suffix('.lrc')
        path.with_suffix('.lrc').write_text('later', encoding='utf-8')
        convert(track, self.source, self.output)
        self.assertEqual(target.read_text(), 'later')
        path.with_suffix('.lrc').write_text('new', encoding='utf-8')
        self.assertTrue(convert(track, self.source, self.output)['warnings'])
        self.assertEqual(target.read_text(), 'later')


if __name__ == '__main__':
    unittest.main()
