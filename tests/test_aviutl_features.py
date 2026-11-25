"""Effects, blend modes, shapes, speed, ranges and codecs added for AviUtl parity."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from core import (Clip, probe, render_command, frame_command, save_project, load_project, EFFECTS, BLEND_MODES,
                  SHAPES, CODECS, new_effect, effect_preview_image, shape_image)


class AviUtlFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.video = cls.root/'clip.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=30', '-f', 'lavfi', '-i', 'sine=f=440',
                        '-t', '4', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(cls.video)], check=True)
        cls.png = cls.root/'dot.png'
        image = Image.new('RGBA', (100, 60), (0, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((20, 10, 80, 50), fill='red')
        image.save(cls.png)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def render(self, clips, name='out.mp4', **options):
        out = self.root/name
        result = subprocess.run(render_command(clips, out, '320x180', 30, True, self.temp.name, **options), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        return out

    def pixel(self, path, t, xy):
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', str(path), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
        return Image.frombytes('RGB', (320, 180), raw).getpixel(xy)

    def test_every_effect_renders_on_objects_and_filter_objects(self):
        for kind in EFFECTS:
            with self.subTest(kind=kind):
                base = probe(self.video); base.duration = .5
                image = probe(self.png); image.layer = 1; image.duration = .5; image.effects = [new_effect(kind)]
                layer_filter = Clip('', 'filter', 86400, duration=.5, layer=3, filter_type='effect', effects=[new_effect(kind)])
                self.render([base, image, layer_filter])
                self.assertTrue(Path(effect_preview_image(image, str(self.png), self.temp.name, 1.0)).is_file())

    def test_blend_modes_match_expected_colours(self):
        expected = {'normal': (255, 136, 0), 'add': (255, 255, 255), 'multiply': (0, 0, 0), 'subtract': (0, 0, 0)}
        for mode, colour in expected.items():
            with self.subTest(mode=mode):
                white = Clip('', 'shape', 86400, duration=1, width=1280, height=720, color='#ffffff' if mode != 'multiply' else '#000000')
                orange = Clip('', 'shape', 86400, duration=1, layer=1, width=1280, height=720, color='#ff8800', blend=mode)
                if mode == 'subtract':
                    white.color = '#ff8800'
                out = self.render([white, orange])
                for got, want in zip(self.pixel(out, 0.5, (160, 90)), colour):
                    self.assertAlmostEqual(got, want, delta=6)
        self.assertEqual(set(BLEND_MODES), {'normal', 'add', 'subtract', 'multiply', 'screen', 'overlay', 'lighten', 'darken', 'difference'})

    def test_visual_fade_and_shapes(self):
        for kind in SHAPES:
            self.assertTrue(Path(shape_image(Clip('', 'shape', 86400, shape_type=kind, width=90, height=60, line_width=3), self.temp.name)).is_file())
        white = Clip('', 'shape', 86400, duration=2, width=1280, height=720, color='#ffffff', fade_in=1.0)
        out = self.render([white])
        early, late = self.pixel(out, 0.2, (160, 90))[0], self.pixel(out, 1.5, (160, 90))[0]
        self.assertLess(early, 90)
        self.assertGreater(late, 240)

    def test_speed_range_codecs_and_frame_export(self):
        fast = probe(self.video); fast.speed = 2.0; fast.duration = 2.0; fast.pan = -.5
        for codec, spec in CODECS.items():
            with self.subTest(codec=codec):
                out = self.render([fast], 'range'+spec['ext'], start=.5, end=1.5, codec=codec)
                duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(out)], text=True))
                self.assertAlmostEqual(duration, 1.0, delta=.08)
        png = self.root/'frame.png'
        result = subprocess.run(frame_command([fast], 1.0, png, '320x180', 30, self.temp.name), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(Image.open(png).size, (320, 180))
        overrun = probe(self.video); overrun.speed = 2.0  # 4 s at 2x needs 8 s of source
        with self.assertRaises(ValueError):
            render_command([overrun], self.root/'x.mp4', '320x180', 30, True, self.temp.name)

    def test_hidden_layers_and_project_roundtrip(self):
        red = Clip('', 'shape', 86400, duration=1, width=1280, height=720, color='#ff0000')
        blue = Clip('', 'shape', 86400, duration=1, layer=1, width=1280, height=720, color='#0000ff', effects=[new_effect('blur')], blend='screen')
        out = self.render([red, blue], hidden_layers=[1])
        self.assertGreater(self.pixel(out, .5, (160, 90))[0], 240)
        path = self.root/'features.lmb'
        settings = {'resolution': '1280x720', 'fps': 30, 'hidden_layers': [1], 'locked_layers': [0], 'media_scale': 'native'}
        save_project(path, [red, blue], settings)
        clips, restored = load_project(path)
        self.assertEqual(clips, [red, blue])
        self.assertEqual(restored, settings)


if __name__ == '__main__':
    unittest.main()
