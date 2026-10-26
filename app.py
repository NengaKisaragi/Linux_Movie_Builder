#!/usr/bin/env python3
"""Linux Movie Builder — four-window desktop video editor."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from PyQt5.QtCore import Qt, QTimer, QProcess, QSettings, QUrl, QEvent
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap, QKeySequence, QDesktopServices
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QDockWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QAbstractItemView, QFormLayout, QDoubleSpinBox, QComboBox, QFileDialog, QMessageBox, QAction, QScrollArea, QSlider, QProgressBar, QShortcut, QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QInputDialog, QColorDialog, QDialog, QDialogButtonBox, QSpinBox, QCheckBox)
from core import Clip, probe, total_duration, save_project, load_project, render_command, active_visuals, animated_value, video_stream_command, audio_stream_command
from widgets import Timeline, CompositionView

STYLE = '''
QWidget { background:#171c25; color:#e5eaf3; font-size:13px; }
QMenuBar,QMenu,QToolBar,QStatusBar { background:#202735; }
QMenu::item:selected { background:#354863; }
QPushButton { background:#2a3547; border:1px solid #3c4b63; padding:7px 12px; border-radius:5px; }
QPushButton:hover,QToolButton:hover { background:#3a4b65; }
QToolButton { background:#2a3547; border:1px solid #3c4b63; padding:5px 8px; border-radius:4px; }
QToolButton:checked { background:#347bda; }
QPushButton:disabled { color:#68748a; }
QPushButton#primary { background:#347bda; border-color:#5596ef; color:white; font-weight:bold; }
QListWidget,QDoubleSpinBox,QComboBox { background:#111720; border:1px solid #354054; padding:5px; border-radius:4px; }
QListWidget::item { padding:9px; border-bottom:1px solid #273142; }
QListWidget::item:selected { background:#284c76; }
QDockWidget::title { background:#263246; padding:8px; }
QLabel#heading { font-size:22px; font-weight:bold; }
QLabel#muted { color:#92a1b8; }
QSlider::groove:horizontal { height:5px; background:#354054; }
QSlider::handle:horizontal { width:12px; margin:-5px 0; background:#6eacf8; border-radius:5px; }
QDoubleSpinBox:disabled { color:#707b8a; background:#242b35; border-color:#303946; }
QPushButton:disabled { color:#77818f; background:#222a35; border-color:#303946; }
QProgressBar { border:1px solid #354054; border-radius:3px; text-align:center; }
QProgressBar::chunk { background:#347bda; }
'''


def stamp(sec):
    return f'{int(sec)//60:02d}:{int(sec)%60:02d}.{int(sec*100)%100:02d}'


def button(text, callback, primary=False):
    b = QPushButton(text)
    if primary:
        b.setObjectName('primary')
    b.clicked.connect(callback)
    return b


class Editor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.clips, self.selected, self.position = [], None, 0.0
        self.selected_segment = 0
        self.output_resolution, self.output_fps = '1920x1080', 60
        self.history, self.future = [], []
        self.project = None
        self.dirty = False
        self.loading = False
        self.job = None
        self.preview_process = None
        self.frame_process = None
        self.frame_processes = {}
        self.frame_cache = {}
        self.playback_frame = None
        self.audio_decoder = None
        self.playback = None
        self.playback_buffer = bytearray()
        self.playback_frames = 0
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(15)
        self.play_timer.timeout.connect(self.display_playback_frame)
        self.temp = tempfile.TemporaryDirectory(prefix='linux-movie-builder-')
        self.config = QSettings(str(Path(__file__).with_name('window-layout.ini')), QSettings.IniFormat)
        self.setWindowTitle('Linux Movie Builder — 編集画面')
        self.resize(1160, 780)
        self.setAcceptDrops(True)
        self.build_edit()
        self.build_preview()
        self.build_properties()
        self.build_timeline()
        self.build_menu()
        self.frame_timer = QTimer(self)
        self.frame_timer.setSingleShot(True)
        self.frame_timer.timeout.connect(self.request_frame)
        self.statusBar().showMessage('素材を追加して編集を始めましょう')
        geometry = self.config.value('geometry')
        state = self.config.value('state')
        restore_layout = self.config.value('restore_layout', True, type=bool)
        if restore_layout and geometry and state:
            self.restoreGeometry(geometry)
            self.restoreState(state)
        else:
            self.layout_timer = QTimer(self)
            self.layout_timer.setSingleShot(True)
            self.layout_timer.timeout.connect(self.reset_to_default_layout)
            self.layout_timer.start(100)
        QApplication.instance().installEventFilter(self)
        self.refresh()

    def dock(self, name, title, widget, area):
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def build_edit(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        title = QLabel('Linux Movie Builder')
        title.setObjectName('heading')
        row.addWidget(title)
        row.addStretch()
        row.addWidget(button('保存', self.save))
        row.addWidget(button('MP4 書き出し', self.export_dialog))
        layout.addLayout(row)
        self.canvas = CompositionView(self)
        layout.addWidget(self.canvas, 1)
        hint = QLabel('編集画面：素材をドラッグして位置を変更  /  Space：再生・停止')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.setMaximumHeight(76)
        self.list.itemSelectionChanged.connect(self.list_selection)
        layout.addWidget(self.list)
        self.summary = QLabel()
        self.summary.setObjectName('muted')
        layout.addWidget(self.summary)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.cancel_button = button('書き出しをキャンセル', self.cancel_job)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)
        self.setCentralWidget(box)

    def build_preview(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        self.image = QLabel('プレビュー待機中\nSpace または ▶ で再生します')
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(240, 135)
        self.image.setStyleSheet('background:#090c12; color:#8190a6; border-radius:6px;')
        layout.addWidget(self.image, 1)
        self.clock = QLabel('00:00.00 / 00:00.00')
        self.clock.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.clock)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.valueChanged.connect(lambda v: self.seek(v/1000))
        layout.addWidget(self.slider)
        tip = QLabel('再生中の映像を表示します。再生操作はタイムラインから行います。')
        tip.setWordWrap(True)
        tip.setObjectName('muted')
        layout.addWidget(tip)
        self.preview_dock = self.dock('preview', 'プレビュー', box, Qt.RightDockWidgetArea)

    def build_properties(self):
        from PyQt5.QtWidgets import QGridLayout, QToolButton
        box = QWidget()
        layout = QVBoxLayout(box)
        self.clip_title = QLabel('クリップを選択してください')
        self.clip_title.setWordWrap(True)
        layout.addWidget(self.clip_title)
        self.fields = {}
        self.anim_ends = {}
        self.motion_buttons = {}
        self.motion_rows = {}
        self.motion_row_widgets = {}
        self.motion_panel = QWidget()
        self.motion_key = None
        properties = [('x','X',-10000,10000,1,10),('y','Y',-10000,10000,1,10),('scale','拡大率',1,500,1,5),('opacity','透明度',0,100,0,5),('rotation','回転',-3600,3600,1,5),('volume','音量',0,200,0,5)]
        column_head=QHBoxLayout();column_head.addWidget(QLabel('移動前'),1);column_head.addWidget(QLabel('項目'));column_head.addWidget(QLabel('移動後'),1);layout.addLayout(column_head)
        self.keyframe_segment_label=QLabel('タイムラインで区間を選択してください')
        self.keyframe_segment_label.setObjectName('muted');layout.addWidget(self.keyframe_segment_label)
        for key, label, low, high, decimals, step in properties:
            row_widget=QWidget();row=QHBoxLayout(row_widget);row.setContentsMargins(0,0,0,0)
            start=QDoubleSpinBox();start.setRange(low,high);start.setDecimals(decimals);start.setSingleStep(step);start.setKeyboardTracking(False)
            end=QDoubleSpinBox();end.setRange(low,high);end.setDecimals(decimals);end.setSingleStep(step);end.setKeyboardTracking(False)
            start.valueChanged.connect(lambda value,k=key:self.change_animation_side(k,'before',value))
            end.valueChanged.connect(lambda value,k=key:self.change_animation_side(k,'after',value))
            toggle=QToolButton();toggle.setText(label);toggle.setCheckable(True);toggle.setMinimumWidth(72);toggle.clicked.connect(lambda checked,k=key:self.toggle_motion(k))
            row.addWidget(start,1);row.addWidget(toggle);row.addWidget(end,1)
            layout.addWidget(row_widget)
            self.fields[key],self.anim_ends[key],self.motion_buttons[key]=start,end,toggle
            self.motion_rows[key]=row
            self.motion_row_widgets[key]=row_widget
        layout.addWidget(QLabel('プロパティ名を押すと、移動方法をここに表示します'))
        panel_layout=QVBoxLayout(self.motion_panel)
        panel_layout.setContentsMargins(2,4,2,6)
        self.motion_choice_layout=QGridLayout()
        panel_layout.addLayout(self.motion_choice_layout)
        names=[('none','移動なし'),('linear','直線移動'),('ease','加減速移動'),('curve','曲線移動'),('instant','瞬間移動'),('random','ランダム移動'),('repeat','反復移動')]
        self.mode_buttons={}
        for i,(mode,name) in enumerate(names):
            b=QPushButton(name);b.setCheckable(True);b.setAutoDefault(False);b.clicked.connect(lambda checked=False,m=mode:self.choose_motion(m))
            self.mode_buttons[mode]=b
            self.motion_choice_layout.addWidget(b,i//3,i%3)
        self.repeat_label=QLabel('反復回数');self.repeat_count=QDoubleSpinBox();self.repeat_count.setRange(1,1000);self.repeat_count.setDecimals(0);self.repeat_count.setValue(3)
        self.repeat_count.valueChanged.connect(self.change_cycles)
        self.repeat_row=QHBoxLayout();self.repeat_row.addWidget(self.repeat_label);self.repeat_row.addWidget(self.repeat_count);self.repeat_row.addStretch();panel_layout.addLayout(self.repeat_row)
        self.curve_label=QLabel('曲線の強さ');self.curve_strength=QDoubleSpinBox();self.curve_strength.setRange(1,5);self.curve_strength.setSingleStep(.25);self.curve_strength.setValue(3)
        self.curve_strength.valueChanged.connect(self.change_curve)
        self.curve_row=QHBoxLayout();self.curve_row.addWidget(self.curve_label);self.curve_row.addWidget(self.curve_strength);self.curve_row.addStretch();panel_layout.addLayout(self.curve_row)
        self.motion_panel.hide()
        layout.addWidget(self.motion_panel)
        form=QFormLayout()
        self.clip_fields={}
        self.clip_field_rows={}
        for key,label,low,high,decimals,step in [('start','素材の開始（秒）',0,86400,2,.1),('duration','長さ（秒）',.04,86400,2,.1),('offset','配置時刻（秒）',0,86400,2,.1),('font_size','文字サイズ',8,300,0,2)]:
            field=QDoubleSpinBox();field.setRange(low,high);field.setDecimals(decimals);field.setSingleStep(step);field.setKeyboardTracking(False)
            field.valueChanged.connect(self.apply_properties)
            self.clip_fields[key]=field
            label_widget=QLabel(label);form.addRow(label_widget,field)
            self.clip_field_rows[key]=(label_widget,field)
        layout.addLayout(form)
        self.text_editor=QTextEdit();self.text_editor.setPlaceholderText('テキストの内容');self.text_editor.setFixedHeight(85)
        self.text_timer=QTimer(self);self.text_timer.setSingleShot(True);self.text_timer.timeout.connect(self.apply_text);self.text_editor.textChanged.connect(self.text_edited)
        layout.addWidget(self.text_editor)
        self.color_button=button('文字色を変更…',self.choose_color);layout.addWidget(self.color_button)
        hint=QLabel('左右の値はタイムラインで選択した区間の移動前・後です。中間点は素材の右クリックから追加します。\nレイヤー番号が大きい素材を手前に表示します。')
        hint.setObjectName('muted');hint.setWordWrap(True);layout.addWidget(hint);layout.addStretch()
        scroll=QScrollArea();scroll.setWidget(box);scroll.setWidgetResizable(True)
        self.props_dock=self.dock('properties','プロパティ',scroll,Qt.RightDockWidgetArea)

    def build_timeline(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(button('✂ 分割', self.split))
        row.addWidget(button('元に戻す', self.undo))
        row.addWidget(button('やり直す', self.redo))
        row.addWidget(button('＋ 素材を追加', self.import_files, True))
        row.addWidget(button('＋ テキスト', self.add_text))
        row.addWidget(button('削除', self.delete))
        row.addSpacing(12)
        row.addWidget(button('◀ 1フレーム', lambda: self.seek(self.position-1/self.output_fps)))
        row.addWidget(button('▶ 再生 / 停止', self.preview, True))
        row.addWidget(button('■ 停止', self.stop_playback))
        row.addWidget(button('1フレーム ▶', lambda: self.seek(self.position+1/self.output_fps)))
        row.addStretch()
        row.addWidget(QLabel('ズーム'))
        zoom = QSlider(Qt.Horizontal)
        zoom.setRange(10, 200)
        zoom.setValue(65)
        zoom.setMaximumWidth(140)
        zoom.valueChanged.connect(self.zoom)
        row.addWidget(zoom)
        layout.addLayout(row)
        self.timeline = Timeline(self)
        scroll = QScrollArea()
        scroll.setWidget(self.timeline)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)
        hint = QLabel('目盛り・空白をドラッグ：シーク  /  素材をドラッグ：時刻・レイヤー変更  /  端をドラッグ：長さ変更  /  ファイルをドロップ：追加')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        layout.addWidget(hint)
        self.timeline_dock = self.dock('timeline', 'タイムライン', box, Qt.BottomDockWidgetArea)

    def build_menu(self):
        file_menu = self.menuBar().addMenu('ファイル')
        for text, shortcut, callback in [('新規', 'Ctrl+N', self.new), ('開く…', 'Ctrl+O', self.open), ('保存', 'Ctrl+S', self.save), ('名前を付けて保存…', 'Ctrl+Shift+S', lambda: self.save(True)), ('素材を追加…', 'Ctrl+I', self.import_files), ('MP4 書き出し…', 'Ctrl+E', self.export_dialog)]:
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(lambda checked=False, f=callback: f())
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction('環境設定…', self.environment_preferences)
        video_menu=self.menuBar().addMenu('動画')
        video_menu.addAction('動画の設定…',self.video_settings)
        edit = self.menuBar().addMenu('編集')
        for text, shortcut, callback in [('元に戻す', 'Ctrl+Z', self.undo), ('やり直す', 'Ctrl+Shift+Z', self.redo), ('分割', 'Ctrl+B', self.split), ('削除', 'Delete', self.delete)]:
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            edit.addAction(action)
        view = self.menuBar().addMenu('ウインドウ')
        view.addAction('4ウインドウに分割', self.four_windows)
        view.addAction('1ウインドウにまとめる', self.join_windows)
        view.addAction('ウインドウ位置をリセット', self.reset_window_layout)
        view.addSeparator()
        for dock in self.docks():
            view.addAction(dock.toggleViewAction())
        help_menu = self.menuBar().addMenu('ヘルプ')
        help_menu.addAction('使い方', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(__file__).with_name('README.md')))))

    def video_settings(self):
        dialog=QDialog(self);dialog.setWindowTitle('動画の設定')
        layout=QVBoxLayout(dialog);form=QFormLayout();layout.addLayout(form)
        resolution=QComboBox()
        for value in ('1920x1080','1280x720','854x480'):resolution.addItem(value,value)
        resolution.addItem('値を指定…','custom')
        width=QSpinBox();width.setRange(2,16384);width.setSingleStep(2)
        height=QSpinBox();height.setRange(2,16384);height.setSingleStep(2)
        custom_resolution=QWidget();custom_layout=QHBoxLayout(custom_resolution);custom_layout.setContentsMargins(0,0,0,0)
        custom_layout.addWidget(QLabel('幅'));custom_layout.addWidget(width);custom_layout.addWidget(QLabel('高さ'));custom_layout.addWidget(height)
        form.addRow('解像度',resolution);form.addRow('指定サイズ',custom_resolution)
        fps=QComboBox()
        for value in (24,30,60):fps.addItem(f'{value} fps',value)
        fps.addItem('値を指定…','custom')
        custom_fps=QSpinBox();custom_fps.setRange(1,240);custom_fps.setSuffix(' fps')
        form.addRow('フレームレート',fps);form.addRow('指定フレームレート',custom_fps)
        parts=self.output_resolution.split('x')
        try:current_w,current_h=map(int,parts)
        except (ValueError,TypeError):current_w,current_h=1920,1080
        width.setValue(current_w);height.setValue(current_h);custom_fps.setValue(int(self.output_fps))
        preset=resolution.findData(self.output_resolution)
        resolution.setCurrentIndex(preset if preset>=0 else resolution.count()-1)
        preset_fps=fps.findData(int(self.output_fps))
        fps.setCurrentIndex(preset_fps if preset_fps>=0 else fps.count()-1)
        custom_resolution.setVisible(resolution.currentData()=='custom')
        custom_fps.setVisible(fps.currentData()=='custom')
        resolution.currentIndexChanged.connect(lambda *_:custom_resolution.setVisible(resolution.currentData()=='custom'))
        fps.currentIndexChanged.connect(lambda *_:custom_fps.setVisible(fps.currentData()=='custom'))
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec_()!=QDialog.Accepted:return
        selected_resolution=resolution.currentData()
        selected_fps=fps.currentData()
        if selected_resolution=='custom':
            if width.value()%2 or height.value()%2:
                QMessageBox.warning(self,'設定エラー','解像度の幅と高さは偶数で指定してください。');return
            selected_resolution=f'{width.value()}x{height.value()}'
        if selected_fps=='custom':selected_fps=custom_fps.value()
        self.output_resolution=str(selected_resolution);self.output_fps=int(selected_fps)
        self.settings_changed()
        self.statusBar().showMessage(f'動画設定: {self.output_resolution} / {self.output_fps} fps',5000)

    def environment_preferences(self):
        dialog=QDialog(self);dialog.setWindowTitle('環境設定')
        layout=QVBoxLayout(dialog)
        restore=QCheckBox('起動時に前回のウインドウ配置を復元する')
        restore.setChecked(self.config.value('restore_layout',True,type=bool));layout.addWidget(restore)
        note=QLabel('動画の解像度やフレームレートは「動画」メニューの動画の設定で指定します。')
        note.setWordWrap(True);note.setObjectName('muted');layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec_()==QDialog.Accepted:
            self.config.setValue('restore_layout',restore.isChecked())


    def docks(self):
        return [self.preview_dock, self.props_dock, self.timeline_dock]

    def four_windows(self):
        if hasattr(self, 'layout_timer'):
            self.layout_timer.stop()
        screen = QApplication.primaryScreen().availableGeometry()
        x, y, w, h = screen.x()+20, screen.y()+35, screen.width()-40, screen.height()-80
        left, top = int(w*.49), int(h*.5)
        self.showNormal()
        self.setGeometry(x, y, left-10, top-20)
        positions = [(x+left+10, y, w-left-10, top-20), (x+left+10, y+top+20, w-left-10, h-top-20), (x, y+top+20, left-10, h-top-20)]
        for dock, rect in zip(self.docks(), positions):
            dock.setFloating(True)
            dock.setWindowTitle('Linux Movie Builder — '+dock.windowTitle().split(' — ')[-1])
            dock.setGeometry(*rect)
            dock.show()

    def reset_to_default_layout(self):
        if hasattr(self, 'layout_timer'):
            self.layout_timer.stop()
        for dock,title in zip(self.docks(),('プレビュー','プロパティ','タイムライン')):
            dock.setWindowTitle(title)
            dock.setFloating(False)
            dock.show()
        self.addDockWidget(Qt.RightDockWidgetArea,self.preview_dock)
        self.splitDockWidget(self.preview_dock,self.props_dock,Qt.Vertical)
        self.addDockWidget(Qt.BottomDockWidgetArea,self.timeline_dock)
        screen=QApplication.primaryScreen().availableGeometry()
        width=max(320,min(1280,screen.width()-40))
        height=max(360,min(850,screen.height()-80))
        self.setGeometry(screen.x()+(screen.width()-width)//2,screen.y()+(screen.height()-height)//2,width,height)
        self.showNormal()

    def reset_window_layout(self):
        self.config.remove('geometry')
        self.config.remove('state')
        self.reset_to_default_layout()
        self.config.setValue('geometry',self.saveGeometry())
        self.config.setValue('state',self.saveState())
        self.config.sync()
        self.statusBar().showMessage('ウインドウ配置を初期状態に戻しました',4000)

    def join_windows(self):
        if hasattr(self, 'layout_timer'):
            self.layout_timer.stop()
        for dock,title in zip(self.docks(),('プレビュー','プロパティ','タイムライン')):
            dock.setWindowTitle(title)
            dock.setFloating(False)
            dock.show()
        self.addDockWidget(Qt.RightDockWidgetArea, self.preview_dock)
        self.splitDockWidget(self.preview_dock, self.props_dock, Qt.Vertical)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.timeline_dock)
        screen=QApplication.primaryScreen().availableGeometry()
        width=max(320,min(1280,screen.width()-40))
        height=max(360,min(850,screen.height()-80))
        self.resize(width,height)

    def current(self):
        return next((c for c in self.clips if c.uid == self.selected), None)

    def settings(self):
        return {'resolution': self.output_resolution, 'fps': self.output_fps}

    def checkpoint(self):
        self.history.append(copy.deepcopy((self.clips, self.settings())))
        self.history = self.history[-80:]
        self.future.clear()

    def settings_changed(self):
        if not self.loading:
            self.dirty = True
            self.update_title()

    def changed(self):
        self.stop_playback()
        self.dirty = True
        self.refresh()
        self.request_edit_frame()

    def update_title(self):
        self.setWindowTitle(f'Linux Movie Builder — 編集画面  |  {Path(self.project).name if self.project else "新規プロジェクト"}{" *" if self.dirty else ""}')

    def refresh(self):
        self.loading = True
        self.list.clear()
        for c in self.clips:
            tag = {'video':'映像', 'audio':'音声', 'image':'画像', 'text':'文字','filter':'フィルタ'}[c.kind]
            layer_label='' if c.kind=='filter' else f'  L{c.layer+1}'
            item = QListWidgetItem(f'{tag}{layer_label}  {c.name}  ·  {stamp(c.offset)} — {stamp(c.offset+c.duration)}')
            item.setData(Qt.UserRole, c.uid)
            self.list.addItem(item)
            if c.uid == self.selected:
                item.setSelected(True)
        self.loading = False
        self.timeline.refresh()
        self.summary.setText(f'{len(self.clips)} クリップ   /   合計 {stamp(total_duration(self.clips))}')
        self.slider.blockSignals(True)
        self.slider.setMaximum(round(self.timeline.seek_limit()*1000))
        self.slider.blockSignals(False)
        self.update_properties()
        self.update_title()
        self.seek(self.position)

    def select(self, uid):
        if self.text_timer.isActive():
            self.apply_text()
        if uid != self.selected:self.selected_segment=0
        self.selected = uid
        self.loading = True
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setSelected(item.data(Qt.UserRole) == uid)
        self.loading = False
        self.update_properties()
        self.timeline.update()
        self.canvas.select()

    def list_selection(self):
        if self.loading:
            return
        items = self.list.selectedItems()
        uid = items[0].data(Qt.UserRole) if items else None
        self.select(uid)
        clip = self.current()
        if clip and not (clip.offset <= self.position < clip.offset+clip.duration):
            self.seek(clip.offset)

    def base_value(self,c,key):
        value=getattr(c,key)
        return value*100 if key in ('scale','opacity','volume') else value

    def update_properties(self):
        self.loading=True
        c=self.current()
        self.clip_title.setText(c.name if c else 'クリップを選択してください')
        visible_keys={
            'video':('x','y','scale','opacity','rotation','volume'),
            'audio':('volume',),
            'image':('x','y','scale','opacity','rotation'),
            'text':('x','y','scale','opacity','rotation'),
        }.get(c.kind if c else '',())
        if self.motion_key not in visible_keys:
            self.motion_key=None
            self.motion_panel.hide()
        for key,start in self.fields.items():
            allowed=c is not None and key in visible_keys and (key!='volume' or c.has_audio)
            self.motion_row_widgets[key].setVisible(allowed)
            start.setEnabled(allowed)
            end=self.anim_ends[key]
            end.setEnabled(allowed and c.animation.get(key,{}).get('mode','none')!='none')
            if c and allowed:
                before,after,begin_time,end_time=self.property_segment(c,key)
                start.setValue(self.display_value(key,before))
                end.setValue(self.display_value(key,after))
            else:
                start.setValue(0);end.setValue(0)
            self.motion_buttons[key].setEnabled(allowed)
            self.motion_buttons[key].setChecked(self.motion_key==key)
        for key,field in self.clip_fields.items():
            enabled=c is not None
            visible= c is not None and ((key=='start' and c.kind in ('video','audio')) or (key in ('duration','offset')) or (key=='font_size' and c.kind=='text'))
            label_widget,field_widget=self.clip_field_rows[key]
            label_widget.setVisible(visible);field_widget.setVisible(visible)
            if c:
                if key=='start':enabled=c.kind in ('video','audio')
                if key=='font_size':enabled=c.kind=='text'
            field.setEnabled(enabled)
            if key=='font_size':value=c.font_size if c else 64
            else:value=getattr(c,key) if c else 0
            field.setValue(value)
        is_text=c is not None and c.kind=='text'
        self.text_editor.setVisible(is_text);self.color_button.setVisible(is_text)
        if is_text and self.text_editor.toPlainText()!=c.text:self.text_editor.setPlainText(c.text)
        if c:
            _,_,a_time,b_time=self.property_segment(c,self.motion_key or next(iter(visible_keys),'x'))
            self.keyframe_segment_label.setText(f'区間 {self.selected_segment+1}  ·  {c.offset+a_time:.2f} 秒  →  {c.offset+b_time:.2f} 秒')
        else:self.keyframe_segment_label.setText('タイムラインで区間を選択してください')
        mode=c.animation.get(self.motion_key,{}).get('mode','none') if c and self.motion_key else 'none'
        for key,button_ in self.mode_buttons.items():button_.setChecked(mode==key)
        anim=c.animation.get(self.motion_key,{}) if c and self.motion_key else {}
        self.repeat_row_widget_mode='repeat' if mode=='repeat' else ''
        self.repeat_label.setEnabled(mode=='repeat');self.repeat_count.setEnabled(mode=='repeat')
        self.repeat_count.setValue(float(anim.get('cycles',3)))
        self.curve_label.setEnabled(mode=='curve');self.curve_strength.setEnabled(mode=='curve')
        self.curve_strength.setValue(float(anim.get('strength',3)))
        self.loading=False

    def toggle_motion(self,key):
        self.motion_key=None if self.motion_key==key and self.motion_panel.isVisible() else key
        self.motion_panel.setVisible(self.motion_key is not None)
        self.update_properties()

    def choose_motion(self,mode):
        c=self.current()
        if not c or not self.motion_key:return
        key=self.motion_key
        if mode=='none':
            self.checkpoint()
            c.animation.pop(key,None)
        else:
            self.checkpoint()
            base=float(getattr(c,key))
            anim=c.animation.setdefault(key,{'mode':mode,'end':base,'cycles':3,'strength':3})
            anim['mode']=mode
            anim.setdefault('end',base)
            anim.setdefault('cycles',3)
            anim.setdefault('strength',3)
        self.dirty=True
        self.update_title();self.update_properties();self.refresh_timeline_and_canvas()

    def display_value(self,key,value):
        return value*100 if key in ('scale','opacity','volume') else value

    def property_segment(self,c,key):
        points=c.keyframes
        right=max(0,min(int(self.selected_segment),len(points)))
        left_value=float(getattr(c,key))
        if right>0:
            left_value=float(points[right-1].get('values',{}).get(key,left_value))
        anim=c.animation.get(key,{'end':left_value})
        right_value=float(points[right].get('values',{}).get(key,anim.get('end',left_value))) if right<len(points) else float(anim.get('end',left_value))
        left_time=float(points[right-1]['time']) if right>0 else 0.0
        right_time=float(points[right]['time']) if right<len(points) else c.duration
        return left_value,right_value,left_time,right_time

    def change_animation_side(self,key,side,value):
        c=self.current()
        if self.loading or not c:return
        mode=c.animation.get(key,{}).get('mode','none')
        if side=='after' and mode=='none':return
        self.stop_playback();self.checkpoint()
        raw=value/(100 if key in ('scale','opacity','volume') else 1)
        left,right,_,_=self.property_segment(c,key)
        local=max(0.0,min(c.duration,self.position-c.offset))
        idx=max(0,min(int(self.selected_segment),len(c.keyframes)))
        if side=='before':
            if idx==0:setattr(c,key,raw)
            else:c.keyframes[idx-1].setdefault('values',{})[key]=raw
        else:
            if idx<len(c.keyframes):c.keyframes[idx].setdefault('values',{})[key]=raw
            else:
                anim=c.animation.setdefault(key,{'mode':'linear','end':right,'cycles':3,'strength':3})
                anim['end']=raw
        self.dirty=True;self.update_title();self.refresh()

    def select_segment(self,uid,position):
        c=self.by_id(uid)
        if not c:return
        local=max(0.0,min(c.duration,position-c.offset))
        segment=sum(1 for point in c.keyframes if local >= float(point['time']))
        self.select(uid)
        self.selected_segment=segment
        self.update_properties()
        self.timeline.update()

    def add_keyframe(self,uid,position):
        c=self.by_id(uid)
        if not c:return
        fps=self.output_fps
        local=round((position-c.offset)*fps)/fps
        if local<=0 or local>=c.duration:
            self.statusBar().showMessage('中間点はクリップの開始・終了の間に置いてください',4000)
            return
        existing=next((p for p in c.keyframes if abs(float(p['time'])-local)<.5/fps),None)
        if existing:
            self.select(uid);self.seek(c.offset+float(existing['time']))
            self.statusBar().showMessage('その位置には中間点があります',3000)
            return
        self.stop_playback();self.checkpoint()
        values={key:animated_value(c,key,c.offset+local) for key in ('x','y','scale','opacity','rotation','volume')}
        c.keyframes.append({'time':local,'values':values})
        c.keyframes.sort(key=lambda p:float(p['time']))
        self.select(uid);self.selected_segment=sum(1 for point in c.keyframes if float(point['time'])<=local);self.dirty=True;self.seek(c.offset+local);self.refresh()
        self.statusBar().showMessage(f'中間点を {local:.2f} 秒に追加しました',3000)

    def add_filter(self,filter_type,offset):
        if filter_type not in ('scenechange','fade','mosaic','blur'):return
        self.checkpoint()
        clip=Clip('', 'filter', 86400, duration=2.0, offset=max(0.0,offset), filter_type=filter_type)
        self.clips.append(clip);self.selected=clip.uid;self.selected_segment=0
        self.changed()
        self.statusBar().showMessage(f'{clip.name} フィルタを追加しました',3000)

    def delete_clip(self,uid):
        clip=self.by_id(uid)
        if not clip:return
        self.checkpoint();self.clips.remove(clip)
        if self.selected==uid:self.selected=None
        self.changed()

    def remove_keyframe(self,uid,local_time):
        c=self.by_id(uid)
        if not c:return
        point=next((p for p in c.keyframes if abs(float(p['time'])-local_time)<1e-6),None)
        if point is None:return
        self.stop_playback();self.checkpoint()
        removed_index=c.keyframes.index(point)
        c.keyframes.remove(point);self.selected=uid;self.selected_segment=max(0,min(self.selected_segment,removed_index,len(c.keyframes)))
        self.dirty=True;self.refresh()
        self.statusBar().showMessage(f'{local_time:.2f} 秒の中間点を削除しました',3000)

    def change_cycles(self,value):
        c=self.current()
        if self.loading or not c or not self.motion_key:return
        anim=c.animation.get(self.motion_key,{})
        if anim.get('mode')=='repeat':
            self.checkpoint();anim['cycles']=value;self.dirty=True;self.update_title();self.request_edit_frame()

    def change_curve(self,value):
        c=self.current()
        if self.loading or not c or not self.motion_key:return
        anim=c.animation.get(self.motion_key,{})
        if anim.get('mode')=='curve':
            self.checkpoint();anim['strength']=value;self.dirty=True;self.update_title();self.request_edit_frame()

    def refresh_timeline_and_canvas(self):
        self.timeline.refresh();self.canvas.sync();self.request_edit_frame()


    def apply_properties(self):
        if self.loading or not self.current():return
        self.stop_playback();c=self.current();self.checkpoint()
        old_duration=c.duration
        for key,field in self.clip_fields.items():
            val=field.value()
            if key!='font_size' or c.kind=='text':setattr(c,key,int(val) if key=='font_size' else val)
        if c.duration<old_duration:
            c.keyframes=[point for point in c.keyframes if float(point['time'])<c.duration-.04]
        self.changed()

    def zoom(self, value):
        self.timeline.scale = value
        self.timeline.refresh()

    def seek(self, value):
        if self.playback:
            self.stop_playback()
        self.position = max(0, min(value, self.timeline.seek_limit()))
        self.slider.blockSignals(True)
        self.slider.setValue(round(self.position*1000))
        self.slider.blockSignals(False)
        self.clock.setText(f'{stamp(self.position)} / {stamp(total_duration(self.clips))}')
        self.timeline.update()
        self.slider.setMaximum(round(self.timeline.seek_limit()*1000))
        self.update_properties()
        self.canvas.sync()
        self.request_edit_frame()

    def request_frame(self):
        if self.playback:
            return
        self.canvas.sync()
        for c in active_visuals(self.clips, self.position):
            if c.kind != 'video':
                continue
            source_time = c.start+self.position-c.offset
            cached = self.frame_cache.get(c.uid)
            if cached and abs(cached[0]-source_time) < .015:
                continue
            if c.uid in self.frame_processes:
                continue
            process = QProcess(self)
            self.frame_processes[c.uid] = (process, source_time)
            process.finished.connect(lambda code, status, uid=c.uid, p=process, t=source_time: self.frame_done(uid, p, t, code))
            process.errorOccurred.connect(lambda error, uid=c.uid, p=process, t=source_time: self.frame_done(uid, p, t, -1) if error == QProcess.FailedToStart else None)
            process.start('ffmpeg', ['-v', 'error', '-ss', str(source_time), '-i', c.path, '-frames:v', '1', '-vf', 'scale=960:540:force_original_aspect_ratio=decrease', '-threads', '1', '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1'])

    def frame_done(self, uid, process, source_time, code):
        if uid not in self.frame_processes or self.frame_processes[uid][0] is not process:
            return
        self.frame_processes.pop(uid)
        if code == 0:
            pixmap = QPixmap()
            pixmap.loadFromData(bytes(process.readAllStandardOutput()))
            if not pixmap.isNull():
                self.frame_cache[uid] = (source_time, pixmap)
                self.canvas.set_frame(uid, pixmap)
        process.deleteLater()
        c = self.by_id(uid)
        if code == 0 and c and c.offset <= self.position < c.offset+c.duration and abs(c.start+self.position-c.offset-source_time) >= .015:
            self.request_edit_frame()

    def show_frame(self, pixmap):
        if not pixmap.isNull():
            self.image.setPixmap(pixmap.scaled(self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def import_files(self, paths=None, offset=None, layer=None, point=None):
        if not isinstance(paths, list):
            paths, _ = QFileDialog.getOpenFileNames(self, '動画・画像・音声を追加', '', 'メディア (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.mpg *.mpeg *.mts *.mp3 *.wav *.ogg *.flac *.m4a *.aac *.opus *.png *.jpg *.jpeg *.webp *.bmp *.tiff *.txt);;すべて (*)')
        if not paths:
            return
        self.stop_playback()
        self.checkpoint()
        errors, added = [], []
        cursor = offset
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for path in paths:
                try:
                    if Path(path).suffix.lower() == '.txt':
                        content = Path(path).read_text(encoding='utf-8-sig')[:4000]
                        c = Clip('', 'text', 86400, duration=5, text=content, layer=2)
                    else:
                        c = probe(path)
                        c.layer = 1 if c.kind == 'image' else 0
                    if layer is not None:
                        c.layer = min(15, layer)
                    if cursor is not None:
                        c.offset = cursor
                    elif c.kind == 'video':
                        c.offset = max((v.offset+v.duration for v in self.clips if v.kind == 'video' and v.layer == c.layer), default=0)
                    else:
                        c.offset = self.position
                    if point and c.kind != 'audio':
                        c.x, c.y = point
                        if c.kind == 'image':
                            c.scale = .35
                    self.clips.append(c)
                    added.append(c)
                    self.selected = c.uid
                    if cursor is not None:
                        cursor += c.duration
                except Exception as e:
                    errors.append(f'{Path(path).name}: {e}')
        finally:
            QApplication.restoreOverrideCursor()
        if added:
            self.position = added[0].offset
            self.changed()
        if errors:
            QMessageBox.warning(self, '読み込みエラー', '\n'.join(errors))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.import_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])

    def split(self):
        c = self.current()
        if not c:
            return
        before = c.offset
        cut = self.position-before
        if cut < .04 or c.duration-cut < .04:
            self.statusBar().showMessage('選択クリップの内部に再生位置を移動してください', 5000)
            return
        self.checkpoint()
        right = copy.deepcopy(c)
        import uuid
        right.uid = uuid.uuid4().hex
        right.start += cut if c.kind in ('video', 'audio') else 0
        right.duration -= cut
        old_points=copy.deepcopy(c.keyframes)
        right.keyframes=[{'time':float(point['time'])-cut,'values':copy.deepcopy(point.get('values',{}))} for point in old_points if float(point['time'])>cut and float(point['time'])<c.duration-.04]
        right.animation=copy.deepcopy(c.animation)
        for key in ('x','y','scale','opacity','rotation','volume'):
            value=animated_value(c,key,c.offset+cut)
            if key in c.animation:c.animation[key]['end']=value
            setattr(right,key,value)
        right.offset += cut
        c.duration = cut
        c.keyframes=[point for point in c.keyframes if float(point['time'])<cut-.04]
        self.clips.insert(self.clips.index(c)+1, right)
        self.selected = right.uid
        self.changed()

    def delete(self):
        if self.current():self.delete_clip(self.current().uid)

    def reorder(self, delta):
        c = self.current()
        if c:
            self.checkpoint()
            c.layer = max(0, min(15, c.layer+delta))
            self.changed()

    def restore_snapshot(self, snapshot):
        self.clips, settings = copy.deepcopy(snapshot)
        self.loading = True
        self.output_resolution=settings['resolution']
        self.output_fps=int(settings['fps'])
        self.loading = False
        self.changed()

    def undo(self):
        self.text_timer.stop()
        if self.history:
            self.future.append(copy.deepcopy((self.clips, self.settings())))
            self.restore_snapshot(self.history.pop())

    def redo(self):
        self.text_timer.stop()
        if self.future:
            self.history.append(copy.deepcopy((self.clips, self.settings())))
            self.restore_snapshot(self.future.pop())

    def may_discard(self):
        if self.text_timer.isActive():
            self.apply_text()
        if self.job:
            QMessageBox.information(self, '処理中', '書き出しが完了するか、キャンセルしてから操作してください。')
            return False
        if not self.dirty:
            return True
        result = QMessageBox.question(self, '変更の保存', 'プロジェクトの変更を保存しますか？', QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        return self.save() if result == QMessageBox.Save else result == QMessageBox.Discard

    def new(self):
        if self.may_discard():
            self.stop_playback()
            self.clear_edit_decoders()
            self.frame_cache.clear()
            self.playback_frame=None
            self.image.setPixmap(QPixmap())
            self.image.setText('プレビュー待機中\nSpace または ▶ で再生します')
            self.clips, self.history, self.future = [], [], []
            self.selected, self.project, self.position, self.dirty = None, None, 0, False
            self.output_resolution,self.output_fps='1920x1080',60
            self.refresh()

    def save(self, save_as=False):
        if self.text_timer.isActive():
            self.apply_text()
        path = self.project
        if save_as or not path:
            path, _ = QFileDialog.getSaveFileName(self, 'プロジェクトを保存', path or '無題.lmb', 'Linux Movie Builder (*.lmb)')
        if not path:
            return False
        if not path.endswith('.lmb'):
            path += '.lmb'
        try:
            save_project(path, self.clips, self.settings())
            self.project, self.dirty = path, False
            self.update_title()
            self.statusBar().showMessage('プロジェクトを保存しました', 5000)
            return True
        except Exception as e:
            QMessageBox.warning(self, '保存エラー', str(e))
            return False

    def open(self):
        if not self.may_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, 'プロジェクトを開く', '', 'Linux Movie Builder (*.lmb)')
        if not path:
            return
        try:
            clips, settings = load_project(path)
            self.clear_edit_decoders()
            self.frame_cache.clear()
            self.playback_frame=None
            self.image.setPixmap(QPixmap())
            self.image.setText('プレビュー待機中\nSpace または ▶ で再生します')
            self.restore_snapshot((clips, settings))
            self.history, self.future = [], []
            self.selected, self.position, self.project, self.dirty = None, 0, path, False
            self.refresh()
        except Exception as e:
            QMessageBox.warning(self, '読み込みエラー', str(e))

    def export_dialog(self):
        if not self.clips or self.job:
            self.statusBar().showMessage('素材を追加してください。処理中の場合は完了をお待ちください。', 5000)
            return
        path, _ = QFileDialog.getSaveFileName(self, 'MP4 書き出し', 'movie.mp4', 'MP4 (*.mp4)')
        if path:
            if not path.lower().endswith('.mp4'):
                path += '.mp4'
            if any(Path(c.path).resolve() == Path(path).resolve() for c in self.clips):
                QMessageBox.warning(self, '出力エラー', '素材と同じファイルには書き出せません。')
                return
            self.start_job(path)

    def preview(self):
        if self.playback:
            self.stop_playback()
            self.request_edit_frame()
            return
        if self.clips:
            if self.text_timer.isActive():
                self.apply_text()
            if self.position >= total_duration(self.clips)-.001:
                self.statusBar().showMessage('再生できる映像・音声はありません',4000)
                return
            self.start_playback()

    def start_job(self, path, preview=False):
        if preview:
            self.preview()
            return
        if self.job:
            return
        if self.text_timer.isActive():
            self.apply_text()
        staged = None
        try:
            fd, staged = tempfile.mkstemp(prefix='.lmb-', suffix='.mp4', dir=Path(path).parent)
            os.close(fd)
            args = render_command(copy.deepcopy(self.clips), staged, self.output_resolution, self.output_fps, asset_dir=self.temp.name)
        except Exception as e:
            if staged:
                Path(staged).unlink(missing_ok=True)
            QMessageBox.warning(self, '出力エラー', str(e))
            return
        process = QProcess(self)
        self.job = {'process':process, 'path':path, 'staged':staged, 'duration':total_duration(self.clips), 'cancelled':False, 'log':'', 'buffer':''}
        process.readyReadStandardOutput.connect(self.job_progress)
        process.readyReadStandardError.connect(self.job_log)
        process.finished.connect(self.job_done)
        process.errorOccurred.connect(self.job_error)
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_button.show()
        self.statusBar().showMessage('MP4 を書き出し中…')
        process.start(args[0], args[1:])

    def job_progress(self):
        if not self.job:
            return
        self.job['buffer'] += bytes(self.job['process'].readAllStandardOutput()).decode(errors='replace')
        lines = self.job['buffer'].split('\n')
        self.job['buffer'] = lines.pop()
        for line in lines:
            if line.startswith('out_time_us='):
                try:
                    value = int(line.split('=')[1])/1000000/self.job['duration']*100
                    self.progress.setValue(min(99, int(value)))
                except ValueError:
                    pass

    def job_log(self):
        if self.job:
            self.job['log'] = (self.job['log']+bytes(self.job['process'].readAllStandardError()).decode(errors='replace'))[-10000:]

    def job_error(self, error):
        if error == QProcess.FailedToStart:
            self.job_done(-1, QProcess.CrashExit)

    def cancel_job(self):
        if self.job:
            self.job['cancelled'] = True
            self.job['process'].kill()

    def job_done(self, code, status):
        if not self.job:
            return
        self.job_log()
        job, self.job = self.job, None
        self.progress.hide()
        self.cancel_button.hide()
        job['process'].deleteLater()
        if code == 0 and not job['cancelled']:
            try:
                os.replace(job['staged'], job['path'])
            except OSError as e:
                Path(job['staged']).unlink(missing_ok=True)
                QMessageBox.warning(self, '出力エラー', str(e))
                return
            self.statusBar().showMessage('MP4 の書き出しが完了しました', 5000)
            QMessageBox.information(self, '書き出し完了', f'保存しました：\n{job["path"]}')
        else:
            Path(job['staged']).unlink(missing_ok=True)
            self.statusBar().showMessage('キャンセルしました' if job['cancelled'] else '書き出しに失敗しました', 5000)
            if not job['cancelled']:
                QMessageBox.warning(self, '書き出しエラー', job['log'][-3500:] or 'FFmpeg を起動できませんでした。')

    def start_playback(self):
        self.stop_playback()
        self.frame_timer.stop()
        snapshot = copy.deepcopy(self.clips)
        try:
            video_args = video_stream_command(snapshot, self.position, self.temp.name)
            audio_args = audio_stream_command(snapshot, self.position, self.temp.name)
        except Exception as e:
            QMessageBox.warning(self, '再生エラー', str(e))
            return
        self.playback_start = self.position
        self.playback_frames = 0
        self.playback_buffer = bytearray()
        self.playback_log = ''
        self.playback_eof = False
        self.playback_wall_start = None
        self.playback_exit_code = 0
        self.playback = QProcess(self)
        self.playback.readyReadStandardOutput.connect(self.playback_data)
        self.playback.readyReadStandardError.connect(self.playback_log_data)
        self.playback.finished.connect(self.playback_finished)
        self.playback.errorOccurred.connect(self.playback_error)
        try:
            self.audio_decoder = subprocess.Popen(audio_args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.preview_process = subprocess.Popen(['ffplay', '-nodisp', '-autoexit', '-hide_banner', '-loglevel', 'error', '-probesize', '32', '-analyzeduration', '0', '-f', 's16le', '-ar', '48000', '-ac', '2', '-i', 'pipe:0'], stdin=self.audio_decoder.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.audio_decoder.stdout.close()
        except OSError as e:
            self.stop_playback()
            QMessageBox.warning(self, '再生エラー', str(e))
            return
        self.image.setText('再生開始…')
        self.playback.start(video_args[0], video_args[1:])
        if self.playback:
            self.play_timer.start()
        self.statusBar().showMessage('再生中 / Spaceで停止')

    def playback_data(self):
        if self.playback:
            self.playback_buffer.extend(bytes(self.playback.readAllStandardOutput()))

    def display_playback_frame(self):
        if not self.playback:
            return
        from PyQt5.QtGui import QImage
        size = 800*450*3
        if self.playback_wall_start is None and len(self.playback_buffer) >= size:
            self.playback_wall_start = time.monotonic()
        frame = None
        if self.playback_wall_start is not None:
            target = int((time.monotonic()-self.playback_wall_start)*30)+1
            while len(self.playback_buffer) >= size and self.playback_frames < target:
                frame = bytes(self.playback_buffer[:size])
                del self.playback_buffer[:size]
                self.playback_frames += 1
        if frame is not None:
            image = QImage(frame, 800, 450, 800*3, QImage.Format_RGB888).copy()
            preview_pixmap=QPixmap.fromImage(image)
            self.show_frame(preview_pixmap)
            self.playback_frame=preview_pixmap
            self.position = min(total_duration(self.clips), self.playback_start+self.playback_frames/30)
            self.slider.blockSignals(True)
            self.slider.setValue(round(self.position*1000))
            self.slider.blockSignals(False)
            self.clock.setText(f'{stamp(self.position)} / {stamp(total_duration(self.clips))}')
            self.timeline.update()
            self.update_properties()
        if self.playback_eof and len(self.playback_buffer) < size:
            code, log = self.playback_exit_code, self.playback_log
            self.stop_playback()
            self.request_edit_frame()
            if code != 0:
                self.statusBar().showMessage('再生エラー: '+log[-400:], 10000)

    def playback_finished(self, code, status):
        self.playback_data()
        self.playback_log_data()
        self.playback_eof, self.playback_exit_code = True, code

    def stop_playback(self):
        was_playing = self.playback is not None
        self.play_timer.stop()
        if self.playback:
            process, self.playback = self.playback, None
            process.blockSignals(True)
            process.kill()
            process.waitForFinished(2000)
            process.deleteLater()
        self.playback_buffer = bytearray()
        for attr in ('preview_process', 'audio_decoder'):
            process = getattr(self, attr, None)
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            setattr(self, attr, None)
        if was_playing:
            if self.playback_frame is None:
                self.image.setPixmap(QPixmap())
                self.image.setText('プレビュー待機中\nSpace または ▶ で再生します')
            self.statusBar().showMessage('停止中')

    def clear_edit_decoders(self):
        for process, source_time in self.frame_processes.values():
            process.blockSignals(True)
            process.kill()
            process.waitForFinished(1000)
            process.deleteLater()
        self.frame_processes.clear()

    def by_id(self, uid):
        return next((c for c in self.clips if c.uid == uid), None)

    def request_edit_frame(self):
        if not self.frame_timer.isActive() and not self.playback:
            self.frame_timer.start(35)

    def add_text(self, text=None, offset=None, layer=None, point=None):
        if not isinstance(text, str):
            text, ok = QInputDialog.getMultiLineText(self, 'テキストを追加', '表示する文字', 'テキスト')
            if not ok:
                return
        self.stop_playback()
        self.checkpoint()
        c = Clip('', 'text', 86400, duration=5, text=text[:4000], offset=self.position if offset is None else offset, layer=2 if layer is None else layer)
        if point:
            c.x, c.y = point
        self.clips.append(c)
        self.selected, self.position = c.uid, c.offset
        self.changed()

    def text_edited(self):
        c = self.current()
        if not self.loading and c and c.kind == 'text':
            if not self.text_timer.isActive():
                self.checkpoint()
            self.stop_playback()
            c.text = self.text_editor.toPlainText()[:4000]
            self.dirty = True
            self.update_title()
            self.canvas.sync()
            self.timeline.update()
            self.text_timer.start(300)

    def apply_text(self):
        self.text_timer.stop()
        c = self.current()
        if c and c.kind == 'text' and c.text != self.text_editor.toPlainText():
            self.stop_playback()
            self.checkpoint()
            c.text = self.text_editor.toPlainText()[:4000]
            self.dirty = True
            self.update_title()
            self.canvas.sync()
            self.timeline.update()

    def choose_color(self):
        c = self.current()
        if c and c.kind == 'text':
            color = QColorDialog.getColor(QColor(c.color), self, '文字色')
            if color.isValid():
                self.checkpoint()
                c.color = color.name()
                self.changed()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.KeyPress, QEvent.KeyRelease) and event.key() == Qt.Key_Space and event.modifiers() == Qt.NoModifier:
            focus = QApplication.focusWidget()
            window = QApplication.activeWindow()
            ours = window is self or window in self.docks()
            if ours and not isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                if event.type() == QEvent.KeyPress and not event.isAutoRepeat():
                    self.preview()
                return True
        return super().eventFilter(obj, event)

    def playback_log_data(self):
        if self.playback:
            self.playback_log = (self.playback_log+bytes(self.playback.readAllStandardError()).decode(errors='replace'))[-4000:]

    def playback_error(self, error):
        if error == QProcess.FailedToStart:
            self.stop_playback()
            self.statusBar().showMessage('FFmpeg を起動できませんでした', 8000)

    def closeEvent(self, event):
        if self.text_timer.isActive():
            self.apply_text()
        if not self.may_discard():
            event.ignore()
            return
        self.stop_playback()
        self.frame_timer.stop()
        self.config.setValue('geometry', self.saveGeometry())
        self.config.setValue('state', self.saveState())
        for process, t in self.frame_processes.values():
            process.blockSignals(True)
            process.kill()
            process.waitForFinished(1000)
        self.frame_processes.clear()
        QApplication.instance().removeEventFilter(self)
        self.temp.cleanup()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Linux Movie Builder')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    editor = Editor()
    editor.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
