#!/usr/bin/env python3
import argparse
import json
import math
import re
import struct
import zlib
from hashlib import sha256
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, low, high):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high


def color(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{6}', value) is not None


def month_key(value):
    return isinstance(value, str) and re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value, flags=re.ASCII) and 2026 <= int(value[:4]) <= 9999


def frame(value):
    require(isinstance(value, dict), 'frame must be an object')
    require(all(number(value.get(k), -0.15, 1) for k in ('x', 'y')), 'invalid frame position')
    require(all(number(value.get(k), 0.005, 1.2) for k in ('width', 'height')), 'invalid frame size')
    require(value['x'] + value['width'] > 0 and value['y'] + value['height'] > 0, 'frame is outside cover')


def png_pixels(data):
    require(33 <= len(data) <= 2_000_000 and data[:8] == b'\x89PNG\r\n\x1a\n', 'expected PNG up to 2 MB')
    require(data[12:16] == b'IHDR' and data[24] == 8, 'export an 8-bit PNG')
    width, height = struct.unpack('>II', data[16:24])
    require(1 <= width <= 2048 and 1 <= height <= 2048, 'PNG dimensions must be 1...2048')
    offset, seen_idat, complete = 8, False, False
    while offset + 12 <= len(data):
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        end = offset + 12 + length
        require(end <= len(data), 'truncated PNG chunk')
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack('>I', data[end - 4:end])[0]
        require(zlib.crc32(kind + payload) & 0xffffffff == crc, 'PNG checksum mismatch')
        require(kind != b'acTL', 'animated PNG is unsupported; use separate layers')
        seen_idat = seen_idat or kind == b'IDAT'
        offset = end
        if kind == b'IEND':
            complete = length == 0 and offset == len(data)
            break
    require(complete and seen_idat, 'incomplete PNG')
    return width * height


def validate_catalog(catalog, root, verify_files=True):
    require(catalog.get('schema_version') == 1, 'unsupported schema_version')
    revision = catalog.get('revision')
    require(isinstance(revision, str) and 1 <= len(revision.encode()) <= 80, 'invalid revision')
    require(len(json.dumps(catalog, ensure_ascii=False).encode()) <= 128_000, 'catalog exceeds 128 KB')
    native = catalog.get('covers')
    custom = catalog.get('layered_covers', {})
    require(isinstance(native, dict) and isinstance(custom, dict) and len(native) <= 240 and len(custom) <= 240, 'invalid cover dictionaries')
    for month, cover in native.items():
        require(month_key(month), 'invalid month key')
        require(cover.get('motif') in ('sprout', 'echo', 'orbit', 'ribbon'), 'unsupported motif')
        palette = cover.get('palette', {})
        require(all(color(palette.get(k)) for k in ('background', 'ink', 'first_digit', 'accent')), 'invalid palette')
        for key in ('first_digit_frame', 'second_digit_frame', 'mascot_frame'):
            frame(cover.get(key))
        require(number(cover.get('zero_thickness'), 0.03, 0.35), 'invalid zero thickness')
    for month, cover in custom.items():
        require(month_key(month) and color(cover.get('background')) and color(cover.get('label_ink')), 'invalid layered cover')
        layers = cover.get('layers')
        require(isinstance(layers, list) and 1 <= len(layers) <= 12, 'use 1...12 layers')
        ids, assets, pixels, total_bytes = set(), {}, 0, 0
        for layer in layers:
            ident = layer.get('id')
            require(isinstance(ident, str) and 1 <= len(ident.encode()) <= 64 and ident not in ids, 'invalid or duplicate layer ID')
            ids.add(ident)
            frame(layer.get('frame'))
            require(number(layer.get('rotation', 0), -180, 180), 'invalid rotation')
            require(number(layer.get('opacity', 1), 0, 1), 'invalid opacity')
            require(isinstance(layer.get('blinks', False), bool), 'blinks must be boolean')
            motion = layer.get('motion')
            if motion is not None:
                kind = motion.get('type')
                require(kind in ('float', 'sway', 'pulse', 'slide'), 'unsupported motion')
                require(number(motion.get('amount'), 0, 8 if kind == 'sway' else 0.08), 'motion amount is too large')
                anchor = motion.get('anchor', {'x': 0.5, 'y': 0.5})
                require(all(number(anchor.get(k), 0, 1) for k in ('x', 'y')), 'invalid motion anchor')
            asset = layer.get('asset', {})
            path, digest, size = (asset.get(k) for k in ('path', 'sha256', 'byte_count'))
            require(isinstance(path, str) and len(path.encode()) <= 240 and re.fullmatch(r'assets/[a-zA-Z0-9_./-]+\.png', path), 'invalid asset path')
            require(all(part not in ('', '.', '..') for part in path.split('/')), 'asset path traversal')
            require(isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest), 'invalid SHA256')
            require(isinstance(size, int) and not isinstance(size, bool) and 1 <= size <= 2_000_000, 'invalid byte_count')
            require(digest not in assets or assets[digest] == size, 'inconsistent size for one asset hash')
            total_bytes += size
            if verify_files:
                file = (root / path).resolve()
                require(file.is_relative_to(root.resolve()), 'asset symlink escapes repository')
                require(file.stat().st_size == size, 'asset byte_count mismatch')
                data = file.read_bytes()
                require(sha256(data).hexdigest() == digest, 'asset SHA256 mismatch')
                if digest not in assets:
                    pixels += png_pixels(data)
            assets[digest] = size
        require(total_bytes <= 8_000_000 and pixels <= 8_388_608, 'cover exceeds byte/pixel budget')


def load_catalog(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result
    require(path.stat().st_size <= 128_000, 'catalog exceeds 128 KB')
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Validate monthly covers and all referenced PNG layers')
    parser.add_argument('catalog', nargs='?', type=Path, default=Path(__file__).resolve().parents[1] / 'monthly-album-covers.json')
    args = parser.parse_args()
    catalog = load_catalog(args.catalog)
    validate_catalog(catalog, args.catalog.parent)
    print('PASS:', catalog['revision'], 'native:', len(catalog['covers']), 'layered:', len(catalog.get('layered_covers', {})))
