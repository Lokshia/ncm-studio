"""Offline NCM parsing and lossless extraction. No account or network access.

Container format reference: taurusxin/ncmdump (MIT), see THIRD_PARTY.md.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from mutagen.flac import FLAC, Picture
import numpy as np
from PIL import Image
import soundfile as sf

CHUNK = 1024 * 1024
CORE_KEY = bytes.fromhex('687a4852416d736f356b496e62617857')
META_KEY = bytes.fromhex('2331346c6a6b5f215c5d2630553c2728')


class Cancelled(Exception):
    pass


def check_cancel(stop=None):
    if stop is not None and stop.is_set():
        raise Cancelled('已停止')


def file_hash(filename, stop=None):
    digest = hashlib.sha256()
    with Path(filename).open('rb') as stream:
        while chunk := stream.read(CHUNK):
            check_cancel(stop)
            digest.update(chunk)
    return digest.hexdigest()


def is_link(path):
    return path.is_symlink() or os.path.isjunction(path)


def validate_roots(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_dir():
        raise ValueError('下载文件夹不存在，请重新选择。')
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError('下载目录与保存目录要分开，不能相同或互相包含。')
    if output.exists() and not output.is_dir():
        raise ValueError('保存位置不是文件夹。')
    return source, output


def save_json(filename, data):
    filename.parent.mkdir(parents=True, exist_ok=True)
    temporary = filename.with_name(filename.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, filename)
    finally:
        temporary.unlink(missing_ok=True)


def aes_decode(data, key):
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


@dataclass
class Container:
    path: Path
    size: int
    offset: int
    pattern: bytes
    metadata: dict
    cover: bytes
    codec: str
    audio: dict


@dataclass
class Track:
    path: Path
    relative: str
    title: str = ''
    artist: str = ''
    album: str = ''
    codec: str = '未知'
    quality: str = '—'
    size: int = 0
    status: str = '待检查'
    reason: str = ''
    convertible: bool = False
    audio: dict = field(default_factory=dict)


def parse_ncm(filename):
    filename = Path(filename)
    with filename.open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size

        def read(length):
            if length < 0 or length > 16 * CHUNK or stream.tell() + length > size:
                raise ValueError('文件不完整或 NCM 数据段长度异常')
            value = stream.read(length)
            if len(value) != length:
                raise ValueError('文件读取不完整')
            return value

        def section():
            return read(int.from_bytes(read(4), 'little'))

        if read(8) != b'CTENFDAM':
            raise ValueError('不是可识别的 NCM 文件')
        read(2)
        unwrapped = aes_decode(bytes(v ^ 0x64 for v in section()), CORE_KEY)
        if not unwrapped.startswith(b'neteasecloudmusic') or len(unwrapped) <= 17:
            raise ValueError('不支持的 NCM 密钥版本')
        key = unwrapped[17:]
        metadata = {}
        block = section()
        if block:
            encoded = bytes(v ^ 0x63 for v in block)
            plain = aes_decode(base64.b64decode(encoded[22:], validate=True), META_KEY)
            if not plain.startswith(b'music:'):
                raise ValueError('不支持的 NCM 信息版本')
            metadata = json.loads(plain[6:])
            if not isinstance(metadata, dict):
                raise ValueError('歌曲信息不是对象')
        read(5)
        reserved = int.from_bytes(read(4), 'little')
        cover_length = int.from_bytes(read(4), 'little')
        if cover_length > reserved or stream.tell() + reserved > size:
            raise ValueError('封面数据不完整')
        cover = read(cover_length)
        stream.seek(reserved - cover_length, 1)
        offset = stream.tell()
        box = list(range(256))
        cursor = 0
        for i in range(256):
            cursor = (cursor + box[i] + key[i % len(key)]) & 255
            box[i], box[cursor] = box[cursor], box[i]
        pattern = bytes(box[(box[j] + box[(box[j] + j) & 255]) & 255]
                        for j in [(i + 1) & 255 for i in range(256)])
        raw = read(min(42, size - offset))
        head = bytes(v ^ pattern[i] for i, v in enumerate(raw))
        codec, audio = '未知', {}
        if head.startswith(b'fLaC'):
            if len(head) < 42 or head[4] & 127 or int.from_bytes(head[5:8], 'big') != 34:
                raise ValueError('FLAC 音频头损坏')
            packed = int.from_bytes(head[18:26], 'big')
            audio = {'sample_rate': packed >> 44, 'channels': ((packed >> 41) & 7) + 1,
                     'bits': ((packed >> 36) & 31) + 1, 'samples': packed & ((1 << 36) - 1),
                     'md5': head[26:42].hex()}
            codec = 'FLAC'
        elif head.startswith(b'ID3') or (len(head) >= 2 and head[0] == 255 and head[1] & 224 == 224):
            codec = 'MP3'
        return Container(filename, size, offset, pattern, metadata, cover, codec, audio)


def lyrics_path(filename):
    expected = filename.with_suffix('.lrc')
    return expected if expected.is_file() and not is_link(expected) else None


def inspect_track(filename, root):
    track = Track(filename, str(filename.relative_to(root)), title=filename.stem)
    try:
        container = parse_ncm(filename)
        meta, audio = container.metadata, container.audio
        artists = meta.get('artist', [])
        track.title = str(meta.get('musicName') or filename.stem)
        track.artist = ' / '.join(str(a[0]) for a in artists if isinstance(a, list) and a)
        track.album = str(meta.get('album') or '')
        track.codec, track.audio, track.size = container.codec, audio, container.size
        if container.codec != 'FLAC':
            track.status, track.reason = '不转换', '内部不是 FLAC；转存为 FLAC 不会提升原音质。'
        elif audio['bits'] not in (16, 24) or not audio['sample_rate'] or not audio['samples']:
            track.status, track.reason = '不支持', '仅支持包含完整样本信息的 16/24-bit FLAC。'
        elif audio['md5'] == '0' * 32:
            track.status, track.reason = '缺少校验值', '缺少音频 MD5，当前严格模式不转换。'
        else:
            track.convertible, track.status = True, '可转换'
        if audio:
            track.quality = f"{audio['bits']} bit / {audio['sample_rate'] / 1000:g} kHz"
        companions = []
        if lyrics_path(filename):
            companions.append('有 LRC')
        if container.cover:
            companions.append('有封面')
        if not track.reason:
            track.reason = ' · '.join(companions) or '将保留音频内已有标签与封面'
    except Exception as error:
        track.status, track.reason = '无法读取', str(error)
    return track


def scan(root, stop=None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('请选择存在的下载文件夹。')

    def walk_error(error):
        raise OSError(f'无法读取部分目录：{error.filename}') from error

    for directory, folders, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        check_cancel(stop)
        folders[:] = sorted(d for d in folders if not is_link(Path(directory) / d))
        for name in sorted(filenames):
            check_cancel(stop)
            candidate = Path(directory) / name
            if candidate.suffix.lower() == '.ncm' and not is_link(candidate):
                yield inspect_track(candidate, root)


def hash_frames(filename, stop=None):
    with filename.open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        if stream.read(4) != b'fLaC':
            raise ValueError('FLAC 标记缺失')
        while True:
            header = stream.read(4)
            if len(header) != 4:
                raise ValueError('FLAC 标签不完整')
            length = int.from_bytes(header[1:], 'big')
            if stream.tell() + length > size:
                raise ValueError('FLAC 标签长度越界')
            stream.seek(length, 1)
            if header[0] & 128:
                break
        digest = hashlib.sha256()
        while data := stream.read(CHUNK):
            check_cancel(stop)
            digest.update(data)
        return digest.hexdigest()


def tag_audio(filename, container):
    track = FLAC(filename)
    meta = container.metadata
    artists = [str(a[0]) for a in meta.get('artist', []) if isinstance(a, list) and a]
    for tag, value in [('title', meta.get('musicName')), ('album', meta.get('album')), ('artist', artists)]:
        if value:
            track[tag] = value if isinstance(value, list) else [str(value)]
    # Preserve existing albumartist for compilations instead of replacing it with track artists.
    warnings = []
    if not track.pictures and container.cover:
        try:
            with Image.open(io.BytesIO(container.cover)) as image:
                width, height = image.size
                if image.format not in ('JPEG', 'PNG'):
                    raise ValueError('封面不是 JPEG/PNG')
                image.verify()
                mime = 'image/jpeg' if image.format == 'JPEG' else 'image/png'
            picture = Picture()
            picture.type, picture.mime = 3, mime
            picture.width, picture.height, picture.depth = width, height, 24
            picture.data = container.cover
            track.add_picture(picture)
        except Exception as error:
            warnings.append(f'封面未嵌入：{error}')
    if not track.pictures:
        warnings.append('没有本地封面，未联网获取')
    track.save()
    return warnings


def verify_audio(filename, expected, stop=None, progress=lambda value: None):
    frames, digest = 0, hashlib.md5()
    with sf.SoundFile(filename) as audio:
        if (audio.samplerate, audio.channels, audio.subtype) != (
                expected['sample_rate'], expected['channels'], f"PCM_{expected['bits']}"):
            raise ValueError('解码参数与原音频头不一致')
        for block in audio.blocks(blocksize=65536, dtype='int32', always_2d=True):
            check_cancel(stop)
            frames += len(block)
            if expected['bits'] == 24:
                pcm = np.ascontiguousarray(block, dtype='<i4').view(np.uint8).reshape(-1, 4)[:, 1:]
                digest.update(pcm.tobytes())
            else:
                digest.update((block >> 16).astype('<i2').tobytes())
            progress(min(frames / expected['samples'], 1))
    if frames != expected['samples'] or digest.hexdigest() != expected['md5']:
        raise ValueError('整曲音频校验失败，未保存到最终音乐目录')
    return {'pcm_md5': digest.hexdigest(), 'samples': frames, 'full_decode': True}


def publish(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        os.rename(source, destination)  # On Windows, existing destination is never replaced.
    else:
        os.link(source, destination)
        source.unlink()


def copy_lyrics(source, destination):
    source = lyrics_path(source)
    if source is None:
        return []
    try:
        data = source.read_bytes()
        # Preserve supplied lyrics byte-for-byte; report incompatibility rather than guessing encodings.
        warnings = []
        try:
            data.decode('utf-8-sig')
        except UnicodeDecodeError:
            warnings.append('歌词不是 UTF-8，Walkman 可能显示乱码')
        if len(data) > 512 * 1024:
            warnings.append('歌词超过 Walkman 的 512 KB 限制')
        if destination.exists():
            if destination.read_bytes() != data:
                warnings.append('同名歌词不同，保留已有歌词')
        else:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.lyrics-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            try:
                publish(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return warnings
    except OSError as error:
        return [f'歌词未复制：{error}']


def convert(track, source_root, output_root, stop=None, progress=lambda phase, fraction: None):
    source_root, output_root = validate_roots(source_root, output_root)
    source = track.path.resolve()
    if not source.is_relative_to(source_root) or is_link(track.path):
        raise ValueError('源文件不在已选目录内')
    relative = source.relative_to(source_root)
    destination = output_root / 'Music' / relative.with_suffix('.flac')
    # Reject output junctions/symlinks leading outside the selected destination.
    if not destination.resolve().is_relative_to(output_root):
        raise ValueError('输出路径经过目录链接，超出了保存位置')
    identity = hashlib.sha256(str(relative).casefold().encode('utf-8')).hexdigest()
    receipt = output_root / '.ncm-studio' / 'state' / f'{identity}.json'
    progress('检查源文件', 0)
    original_hash = file_hash(source, stop)
    if destination.exists():
        previous = {}
        try:
            previous = json.loads(receipt.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        if (previous.get('source_sha256') == original_hash and
                previous.get('output_sha256') == file_hash(destination, stop)):
            warnings = copy_lyrics(source, destination.with_suffix('.lrc'))
            return {'status': '已完成，跳过', 'output': str(destination), 'warnings': warnings}
        raise FileExistsError('同名文件已存在且没有匹配的校验记录；未覆盖，请更换保存位置。')
    container = parse_ncm(source)
    if container.codec != 'FLAC' or container.audio.get('bits') not in (16, 24):
        raise ValueError('不是支持的无损 FLAC，未转换')
    if not container.audio['samples'] or container.audio['md5'] == '0' * 32:
        raise ValueError('缺少完整校验信息')
    work = output_root / '.ncm-studio' / 'work'
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='track-', dir=work) as folder:
        stage = Path(folder).resolve()
        if not stage.is_relative_to(work.resolve()):
            raise ValueError('临时目录越界')
        audio_path = stage / 'audio.flac'
        with source.open('rb') as incoming, audio_path.open('xb') as outgoing:
            incoming.seek(container.offset)
            processed = 0
            pattern = np.frombuffer(container.pattern, dtype=np.uint8)
            while chunk := incoming.read(CHUNK):
                check_cancel(stop)
                mask = np.resize(np.roll(pattern, -(processed % 256)), len(chunk))
                outgoing.write(np.bitwise_xor(np.frombuffer(chunk, dtype=np.uint8), mask).tobytes())
                processed += len(chunk)
                progress('提取原始音频', 0.05 + 0.30 * processed / max(container.size - container.offset, 1))
        before = hash_frames(audio_path, stop)
        warnings = tag_audio(audio_path, container)
        if hash_frames(audio_path, stop) != before:
            raise ValueError('整理标签时音频帧发生变化')
        verified = verify_audio(audio_path, container.audio, stop,
                                lambda fraction: progress('整曲无损校验', 0.4 + 0.5 * fraction))
        if file_hash(source, stop) != original_hash:
            raise ValueError('源文件仍在下载或发生变化，请下载完成后重新扫描')
        output_hash = file_hash(audio_path, stop)
        check_cancel(stop)
        progress('保存文件', 0.98)
        publish(audio_path, destination)
        warnings += copy_lyrics(source, destination.with_suffix('.lrc'))
        record = {'source_sha256': original_hash, 'output_sha256': output_hash,
                  'audio': container.audio, 'verification': verified, 'encoded_frames_sha256': before,
                  'output_relative': str(destination.relative_to(output_root)),
                  'created': datetime.now().astimezone().isoformat()}
        try:
            save_json(receipt, record)
        except OSError as error:
            warnings.append(f'音频已保存，但无法写入重复运行记录：{error}')
        progress('完成', 1)
        return {'status': '转换成功', 'output': str(destination), 'warnings': warnings, **verified}
