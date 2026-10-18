import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from prepare_cover import prepare
from validate_covers import load_catalog, png_pixels, validate_catalog

ROOT = Path(__file__).resolve().parents[1]


class CoversTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(ROOT / 'monthly-album-covers.json')

    def test_complete_published_design(self):
        validate_catalog(self.catalog, ROOT)

    def test_invalid_asset_paths(self):
        for path in ('assets/../escape.png', 'https://example.com/x.png', 'assets/%2e%2e/x.png', 'assets/a.png?x=1'):
            value = copy.deepcopy(self.catalog)
            value['layered_covers']['2026-06']['layers'][0]['asset']['path'] = path
            with self.assertRaises(ValueError):
                validate_catalog(value, ROOT)

    def test_invalid_hash_and_duplicate_layers(self):
        value = copy.deepcopy(self.catalog)
        cover = value['layered_covers']['2026-06']
        cover['layers'][0]['asset']['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            validate_catalog(value, ROOT)
        value = copy.deepcopy(self.catalog)
        cover = value['layered_covers']['2026-06']
        cover['layers'].append(copy.deepcopy(cover['layers'][0]))
        with self.assertRaises(ValueError):
            validate_catalog(value, ROOT)

    def test_bad_motion_and_nonfinite_geometry(self):
        for change in ({'motion': {'type': 'script', 'amount': 1}}, {'rotation': float('nan')}, {'opacity': 2}):
            value = copy.deepcopy(self.catalog)
            value['layered_covers']['2026-06']['layers'][0].update(change)
            with self.assertRaises(ValueError):
                validate_catalog(value, ROOT)

    def test_png_corruption(self):
        path = self.catalog['layered_covers']['2026-06']['layers'][0]['asset']['path']
        data = bytearray((ROOT / path).read_bytes())
        data[50] ^= 1
        with self.assertRaises(ValueError):
            png_pixels(bytes(data))

    def test_prepare_is_idempotent_and_failed_prepare_keeps_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(ROOT / 'monthly-album-covers.json', root)
            shutil.copytree(ROOT / 'assets', root / 'assets')
            shutil.copytree(ROOT / 'examples', root / 'examples')
            design_path = root / 'examples/june-design.json'
            prepared = prepare(design_path, '2026-06', 'test.1', root)
            self.assertEqual(prepared['layered_covers'], self.catalog['layered_covers'])
            before = (root / 'monthly-album-covers.json').read_bytes()
            prepare(design_path, '2026-06', 'test.1', root)
            self.assertEqual(before, (root / 'monthly-album-covers.json').read_bytes())
            design = json.loads(design_path.read_text())
            design['layers'][0]['frame']['width'] = -1
            design_path.write_text(json.dumps(design))
            with self.assertRaises(ValueError):
                prepare(design_path, '2026-11', 'test.2', root)
            self.assertEqual(before, (root / 'monthly-album-covers.json').read_bytes())

    def test_empty_catalog_allows_rollback(self):
        validate_catalog({'schema_version': 1, 'revision': 'rollback', 'covers': {}, 'layered_covers': {}}, ROOT)


if __name__ == '__main__':
    unittest.main()
