import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from core import Clip, probe, render_command, save_project, load_project


class LayeredEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.blue = cls.root/'blue.png'
        cls.alpha = cls.root/'transparent overlay.png'
        cls.video = cls.root/'clip.mp4'
        Image.new('RGB',(160,90),'blue').save(cls.blue)
        im = Image.new('RGBA',(160,90),(0,0,0,0))
        ImageDraw.Draw(im).rectangle((40,15,120,75), fill='red')
        im.save(cls.alpha)
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=30','-t','3','-c:v','libx264',str(cls.video)],check=True)
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def make_editor(self):
        from app import Editor, STYLE
        from PyQt5.QtCore import QSettings
        self.app.setStyleSheet(STYLE)
        e = Editor()
        e.config = QSettings(str(self.root/'layout.ini'),QSettings.IniFormat)
        e.show()
        e.join_windows()
        e.resize(1400,950)
        self.app.processEvents()
        self.addCleanup(self.close_editor,e)
        return e

    def close_editor(self,e):
        e.stop_playback()
        if e.job:
            e.cancel_job()
            e.job['process'].waitForFinished(3000)
            self.app.processEvents()
        e.dirty = False
        e.text_timer.stop()
        e.close()
        self.app.processEvents()

    def test_overlay_alpha_timing_and_text_export(self):
        bg, front = probe(self.blue), probe(self.alpha)
        bg.duration = 1.4
        front.duration, front.offset, front.layer = .5, .3, 1
        front.x, front.y, front.scale = 320, 360, .5
        text = Clip('', 'text', 86400, duration=.5, offset=.3, layer=2, text='字幕 日本語', x=1000,y=600,font_size=90)
        out = self.root/'layers.mp4'
        result = subprocess.run(render_command([bg,front,text],out,'320x180',30,True, self.temp.name),capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr[-4000:])
        def frame(t):
            raw = subprocess.check_output(['ffmpeg','-v','error','-ss',str(t),'-i',str(out),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
            return Image.frombytes('RGB',(320,180),raw)
        before, during, after = frame(.1), frame(.5), frame(1.1)
        self.assertGreater(before.getpixel((80,90))[2],200)
        self.assertGreater(during.getpixel((80,90))[0],200)
        self.assertGreater(during.getpixel((200,90))[2],200)
        self.assertGreater(after.getpixel((80,90))[2],200)
        self.assertTrue(any(r>150 and g>150 for r,g,b in during.crop((190,125,315,175)).getdata()))
        path = self.root/'new.lmb'
        save_project(path,[bg,front,text],{'resolution':'1280x720','fps':30})
        restored,_=load_project(path)
        self.assertEqual(restored,[bg,front,text])
        self.assertEqual(restored[2].path,'')

    def test_v1_project_migration_keeps_sequence(self):
        from dataclasses import asdict
        first, second = probe(self.video), probe(self.blue)
        first.duration, second.duration = .7, .4
        old_keys = ('path','kind','source_duration','start','duration','offset','volume','has_audio','uid')
        records = [{key:asdict(c)[key] for key in old_keys} for c in (first,second)]
        data={'application':'Linux Movie Builder','version':1,'clips':records,'settings':{'resolution':'1280x720','fps':30}}
        path=self.root/'old.lmb'
        path.write_text(json.dumps(data))
        restored,_=load_project(path)
        self.assertEqual(restored[0].offset,0)
        self.assertAlmostEqual(restored[1].offset,.7)
        self.assertEqual(restored[0].width,160)

    def test_space_from_export_button_only_streams_and_text_keeps_spaces(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest
        from PyQt5.QtWidgets import QPushButton
        e=self.make_editor()
        e.clips=[probe(self.video)];e.refresh()
        export_button=next(b for b in e.findChildren(QPushButton) if b.text()=='MP4 書き出し')
        export_button.setFocus();e.activateWindow();QTest.qWait(40)
        with patch.object(e,'start_job') as write_job, patch.object(e,'export_dialog') as export_dialog, patch('app.QFileDialog.getSaveFileName') as save_dialog:
            QTest.keyClick(export_button,Qt.Key_Space)
            QTest.qWait(650)
            self.assertIsNotNone(e.playback)
            self.assertGreater(e.playback_frames,4)
            self.assertFalse(write_job.called)
            self.assertFalse(export_dialog.called)
            self.assertFalse(save_dialog.called)
            self.assertIsNone(e.job)
            self.assertFalse(e.progress.isVisible())
            self.assertFalse(list(Path(e.temp.name).glob('*.mp4')))
            QTest.keyClick(export_button,Qt.Key_Space)
            self.assertIsNone(e.playback)
            self.assertIsNone(e.image.pixmap())
        e.add_text('hello',0,2)
        e.text_editor.setFocus()
        e.text_editor.moveCursor(e.text_editor.textCursor().End)
        QTest.keyClick(e.text_editor,Qt.Key_Space)
        QTest.qWait(350)
        self.assertEqual(e.current().text,'hello ')
        self.assertIsNone(e.playback)

    def test_timeline_scrub_and_move_update_before_release(self):
        from PyQt5.QtCore import Qt,QPointF,QEvent
        from PyQt5.QtGui import QMouseEvent
        from PyQt5.QtTest import QTest
        e=self.make_editor();e.clips=[probe(self.video)];e.refresh();QTest.qWait(180)
        timeline=e.timeline
        def event(kind,x,y,button,buttons):
            self.app.sendEvent(timeline,QMouseEvent(kind,QPointF(x,y),button,buttons,Qt.NoModifier))
        event(QEvent.MouseButtonPress,105,20,Qt.LeftButton,Qt.LeftButton)
        for x in (118,131,144,157,170):
            event(QEvent.MouseMove,x,20,Qt.NoButton,Qt.LeftButton)
            QTest.qWait(90)
        self.assertAlmostEqual(e.position,1,delta=.04)
        self.assertIn(e.clips[0].uid,e.frame_cache)
        self.assertGreater(e.frame_cache[e.clips[0].uid][0],.4)
        self.assertIsNone(e.image.pixmap())
        self.assertTrue(e.canvas.items_by_id)
        event(QEvent.MouseButtonRelease,170,20,Qt.LeftButton,Qt.NoButton)
        event(QEvent.MouseButtonPress,130,60,Qt.LeftButton,Qt.LeftButton)
        event(QEvent.MouseMove,195,118,Qt.NoButton,Qt.LeftButton)
        self.assertAlmostEqual(e.clips[0].offset,1,delta=.04)
        self.assertEqual(e.clips[0].layer,1)
        self.assertTrue(e.dirty)
        self.assertTrue(e.canvas.items_by_id)
        event(QEvent.MouseButtonRelease,195,118,Qt.LeftButton,Qt.NoButton)
        e.undo()
        self.assertEqual(e.clips[0].offset,0)
        self.assertEqual(e.clips[0].layer,0)

    def test_timeline_drop_canvas_drag_and_properties(self):
        from PyQt5.QtCore import Qt,QPointF,QPoint,QMimeData,QUrl,QEvent
        from PyQt5.QtGui import QDropEvent,QDragEnterEvent,QMouseEvent
        from PyQt5.QtTest import QTest
        e=self.make_editor()
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(self.alpha))])
        enter=QDragEnterEvent(QPoint(105,100),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier)
        self.app.sendEvent(e.timeline,enter)
        drop=QDropEvent(QPointF(105,100),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier)
        self.app.sendEvent(e.timeline,drop)
        self.assertEqual(len(e.clips),1)
        c=e.clips[0]
        self.assertEqual(c.layer,1)
        self.assertEqual(c.offset,0)
        self.assertIn(c.uid,e.canvas.items_by_id)
        e.fields['scale'].setValue(40)
        QTest.qWait(50)
        c=e.current()
        before=(c.x,c.y)
        pos=e.canvas.mapFromScene(QPointF(c.x,c.y))
        viewport=e.canvas.viewport()
        QTest.mousePress(viewport,Qt.LeftButton,Qt.NoModifier,pos)
        target=pos+QPoint(40,20)
        self.app.sendEvent(viewport,QMouseEvent(QEvent.MouseMove,QPointF(target),Qt.NoButton,Qt.LeftButton,Qt.NoModifier))
        self.assertGreater(e.current().x,before[0]+20)
        self.assertGreater(e.current().y,before[1]+10)
        self.assertAlmostEqual(e.fields['x'].value(),e.current().x,delta=.1)
        QTest.mouseRelease(viewport,Qt.LeftButton,Qt.NoModifier,target)
        e.undo()
        self.assertAlmostEqual(e.current().x,before[0],delta=.1)
        e.add_text('sample text',0,2)
        self.assertIn(e.current().uid,e.canvas.items_by_id)
        self.assertTrue(e.text_editor.isVisible())

if __name__=='__main__':unittest.main()
