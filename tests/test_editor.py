import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import probe, render_command, save_project, load_project, total_duration

class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.video = cls.root/'動画 clip.mp4'
        cls.audio = cls.root/'BGM.wav'
        cls.image = cls.root/'still.png'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=30','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','1.2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(cls.video)], check=True)
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=880:sample_rate=48000','-t','1.8',str(cls.audio)], check=True)
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=160x90','-frames:v','1','-threads','1',str(cls.image)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_project_roundtrip_and_missing_media(self):
        clips = [probe(self.video), probe(self.audio), probe(self.image)]
        clips[0].start, clips[0].duration = .2, .7
        path = self.root/'project.lmb'
        save_project(path, clips, {'resolution':'1280x720','fps':30})
        restored, settings = load_project(path)
        self.assertEqual(restored, clips)
        self.assertFalse(Path(json.loads(path.read_text())['clips'][0]['path']).is_absolute())
        raw = json.loads(path.read_text())
        raw['clips'][0]['path'] = 'missing.mp4'
        path.write_text(json.dumps(raw))
        restored, _ = load_project(path)  # the stored absolute path still finds the media
        self.assertEqual(restored[0].path, clips[0].path)
        raw['clips'][0]['abs_path'] = ''
        path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            load_project(path)

    def render(self, clips, name):
        path = self.root/name
        result = subprocess.run(render_command(clips, path, '320x180', 30, True), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        data = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))
        self.assertEqual({s['codec_type'] for s in data['streams']}, {'video','audio'})
        self.assertAlmostEqual(float(data['format']['duration']), total_duration(clips), delta=.1)
        decoded = subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'], capture_output=True)
        self.assertEqual(decoded.returncode, 0)
        return path

    def test_trim_concat_still_and_delayed_audio(self):
        v, a, image = probe(self.video), probe(self.audio), probe(self.image)
        v.start, v.duration = .2, .5
        image.duration = .4
        a.offset, a.duration, a.volume = .3, 1.2, .3
        self.render([v, image, a], 'mixed.mp4')

    def test_audio_only_and_silent_still(self):
        a = probe(self.audio)
        a.offset, a.duration = .2, .4
        self.render([a], 'audio_only.mp4')
        image = probe(self.image)
        image.duration = .4
        self.render([image], 'silent.mp4')

    def test_gui_split_undo_four_windows_and_playback(self):
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import QSettings, QEventLoop, QTimer
        from app import Editor
        app = QApplication.instance() or QApplication([])
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, self.tmp.name)
        editor = Editor()
        editor.config = QSettings(str(self.root/'test-layout.ini'), QSettings.IniFormat)
        editor.show()
        editor.clips = [probe(self.video)]
        editor.selected = editor.clips[0].uid
        editor.refresh()
        editor.four_windows()
        self.assertTrue(all(d.isFloating() for d in editor.docks()))
        editor.seek(.5)
        editor.split()
        self.assertEqual(len(editor.clips), 2)
        self.assertAlmostEqual(editor.clips[1].start, .5)
        editor.undo()
        self.assertEqual(len(editor.clips), 1)
        editor.redo()
        self.assertEqual(len(editor.clips), 2)
        editor.join_windows()
        self.assertTrue(all(not d.isFloating() for d in editor.docks()))
        editor.position = 0
        editor.start_playback()
        loop = QEventLoop()
        QTimer.singleShot(850, loop.quit)
        loop.exec_()
        self.assertGreater(editor.playback_frames, 5)
        self.assertFalse(editor.image.pixmap().isNull())
        editor.stop_playback()
        from unittest.mock import patch
        output = self.root/'gui-export.mp4'
        with patch('app.QMessageBox.information') as info, patch('app.QMessageBox.warning') as warning:
            editor.resolution.setCurrentText('854x480')
            editor.start_job(str(output), False)
            loop = QEventLoop()
            poll = QTimer()
            poll.timeout.connect(lambda: loop.quit() if editor.job is None else None)
            poll.start(30)
            QTimer.singleShot(15000, loop.quit)
            loop.exec_()
            poll.stop()
            self.assertIsNone(editor.job)
            self.assertTrue(output.is_file())
            self.assertFalse(warning.called)
            self.assertTrue(info.called)
            original = output.read_bytes()
            editor.start_job(str(output), False)
            editor.cancel_job()
            loop = QEventLoop()
            poll.start(30)
            QTimer.singleShot(5000, loop.quit)
            loop.exec_()
            poll.stop()
            self.assertIsNone(editor.job)
            self.assertEqual(output.read_bytes(), original)
        editor.dirty = False
        editor.close()
        app.processEvents()

if __name__ == '__main__':
    unittest.main()
