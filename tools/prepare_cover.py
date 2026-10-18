#!/usr/bin/env python3
import argparse
import json
import os
import re
import tempfile
from hashlib import sha256
from pathlib import Path
from validate_covers import load_catalog, month_key, png_pixels, require, validate_catalog


def prepare(design_path, month, revision, root, dry_run=False):
    require(month_key(month), 'month must be YYYY-MM')
    catalog_path = root / 'monthly-album-covers.json'
    catalog = load_catalog(catalog_path)
    validate_catalog(catalog, root)
    design = json.loads(design_path.read_text(encoding='utf-8'))
    files = {}
    for layer in design['layers']:
        source = design_path.parent / layer.pop('file')
        require(source.stat().st_size <= 2_000_000, 'source PNG exceeds 2 MB')
        data = source.read_bytes()
        png_pixels(data)
        digest = sha256(data).hexdigest()
        source_stem = re.sub(r'^[0-9a-f]{64}-', '', source.stem)
        stem = re.sub(r'[^a-zA-Z0-9_-]', '-', source_stem)[:40] or 'layer'
        path = 'assets/' + month + '/' + digest + '-' + stem + '.png'
        layer['asset'] = {'path': path, 'sha256': digest, 'byte_count': len(data)}
        files[path] = data
    catalog.setdefault('layered_covers', {})[month] = design
    catalog['revision'] = revision
    validate_catalog(catalog, root, verify_files=False)
    unique = {sha256(data).hexdigest(): data for data in files.values()}
    require(sum(png_pixels(data) for data in unique.values()) <= 8_388_608, 'cover exceeds pixel budget')
    result = json.dumps(catalog, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
    require(len(result) <= 128_000, 'formatted catalog exceeds 128 KB')
    if not dry_run:
        for path, data in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                require(target.read_bytes() == data, 'immutable asset path already contains different bytes')
            else:
                target.write_bytes(data)
        validate_catalog(catalog, root)
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as file:
            file.write(result)
            temporary = file.name
        os.replace(temporary, catalog_path)
    print('READY:', month, 'layers:', len(design['layers']), 'revision:', revision, 'dry-run:', dry_run)
    return catalog


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Prepare custom PNG layers and update the monthly cover catalog')
    parser.add_argument('design', type=Path)
    parser.add_argument('--month', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    prepare(args.design.resolve(), args.month, args.revision, args.root.resolve(), args.dry_run)
