"""Interactive composition canvas and layered timeline."""
import copy
from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QWidget, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsItem, QMenu
from core import BASE_W, BASE_H, total_duration, placed_size, active_visuals, text_image, animated_value


class VisualItem(QGraphicsPixmapItem):
    def __init__(self, view, clip):
        super().__init__()
        self.view, self.uid = view, clip.uid
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)
        self.setTransformationMode(Qt.SmoothTransformation)
        self.pressed = None
        self.saved = False

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.uid == self.view.editor.selected:
            pen = QPen(QColor('#8bc5ff'), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect())

    def mousePressEvent(self, event):
        self.view.editor.select(self.uid)
        self.pressed, self.saved = event.scenePos(), False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.pressed is not None and (event.scenePos()-self.pressed).manhattanLength() > 2 and not self.saved:
            self.view.editor.stop_playback()
            self.view.editor.checkpoint()
            self.saved = True
        super().mouseMoveEvent(event)
        if self.saved:
            clip = self.view.editor.by_id(self.uid)
            if clip:
                clip.x, clip.y = round(self.pos().x(), 2), round(self.pos().y(), 2)
                self.view.editor.dirty = True
                self.view.editor.update_properties()
                self.view.editor.update_title()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.pressed = None
        if self.saved:
            self.view.editor.request_edit_frame()
        self.saved = False


class CompositionView(QGraphicsView):
    def __init__(self, editor):
        self.scene = QGraphicsScene()
        super().__init__(self.scene)
        self.editor = editor
        self.scene.setSceneRect(0, 0, BASE_W, BASE_H)
        self.scene.setBackgroundBrush(QColor('black'))
        self.setBackgroundBrush(QColor('#090d14'))
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumSize(300, 170)
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.items_by_id = {}
        self.static_cache = {}

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def drawForeground(self, painter, rect):
        # Mask the letterbox area so off-canvas objects match the exported crop.
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        bars = [QRectF(visible.left(), visible.top(), max(0, -visible.left()), visible.height()),
                QRectF(BASE_W, visible.top(), max(0, visible.right()-BASE_W), visible.height()),
                QRectF(0, visible.top(), BASE_W, max(0, -visible.top())),
                QRectF(0, BASE_H, BASE_W, max(0, visible.bottom()-BASE_H))]
        for bar in bars:
            painter.fillRect(bar, QColor('#090d14'))
        pen = QPen(QColor('#324157'), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(0, 0, BASE_W, BASE_H))

    def sync(self):
        clips = active_visuals(self.editor.clips, self.editor.position)
        active = {c.uid for c in clips}
        for uid in list(self.items_by_id):
            if uid not in active:
                self.scene.removeItem(self.items_by_id.pop(uid))
        for z, c in enumerate(clips):
            item = self.items_by_id.get(c.uid)
            if item is None:
                item = VisualItem(self, c)
                self.scene.addItem(item)
                self.items_by_id[c.uid] = item
            if c.kind in ('image', 'text'):
                path = text_image(c, self.editor.temp.name) if c.kind == 'text' else c.path
                cache_key=(c.kind,path)
                if self.static_cache.get(c.uid,(None,None))[0] != cache_key:
                    self.static_cache[c.uid]=(cache_key,QPixmap(path))
                self.set_frame(c.uid,self.static_cache[c.uid][1])
            elif c.uid in self.editor.frame_cache:
                self.set_frame(c.uid, self.editor.frame_cache[c.uid][1])
            else:
                w, h = placed_size(c, self.editor.temp.name)
                pixmap = QPixmap(max(2, int(w)), max(2, int(h)))
                pixmap.fill(QColor('#1a2636'))
                self.set_frame(c.uid, pixmap)
            item.setTransformOriginPoint(0,0)
            item.setPos(animated_value(c,'x',self.editor.position), animated_value(c,'y',self.editor.position))
            item.setOpacity(animated_value(c,'opacity',self.editor.position))
            item.setRotation(animated_value(c,'rotation',self.editor.position))
            item.setZValue(z)
            item.setSelected(c.uid == self.editor.selected)
            item.update()
        self.viewport().update()

    def set_frame(self, uid, pixmap):
        item, clip = self.items_by_id.get(uid), self.editor.by_id(uid)
        if item is None or clip is None or pixmap.isNull():
            return
        item.setPixmap(pixmap)
        item.setOffset(-pixmap.width()/2, -pixmap.height()/2)
        width, _ = placed_size(clip, self.editor.temp.name)
        item.setScale(width/pixmap.width()*animated_value(clip,'scale',self.editor.position)/clip.scale)
        item.setOpacity(animated_value(clip,'opacity',self.editor.position))
        item.setRotation(animated_value(clip,'rotation',self.editor.position))

    def select(self):
        for uid, item in self.items_by_id.items():
            item.setSelected(uid == self.editor.selected)
            item.update()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        pos = self.mapToScene(event.pos())
        if event.mimeData().hasUrls():
            self.editor.import_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()], self.editor.position, None, (pos.x(), pos.y()))
        elif event.mimeData().hasText():
            self.editor.add_text(event.mimeData().text(), self.editor.position, None, (pos.x(), pos.y()))
        event.acceptProposedAction()


class Timeline(QWidget):
    LEFT, TOP, ROW = 105, 35, 58

    def __init__(self, editor):
        super().__init__()
        self.editor, self.scale = editor, 65
        self.boxes, self.drag = [], None
        self.scrubbing = False
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumHeight(290)

    def layer_count(self):
        return max(4, max((c.layer+1 for c in self.editor.clips if c.kind not in ('audio','filter')), default=4))

    def refresh(self):
        self.setMinimumWidth(max(1100, int(max(30,total_duration(self.editor.clips)+15)*self.scale)+185))
        self.setMinimumHeight(self.TOP+self.ROW*(1+self.layer_count()+max(1, sum(c.kind == 'audio' for c in self.editor.clips)))+30)
        self.update()

    def seek_limit(self):
        desired_width=max(1100,int(max(30,total_duration(self.editor.clips)+15)*self.scale)+185)
        visible_width=max(self.width(),desired_width)
        return max(total_duration(self.editor.clips)+15,30.0,(visible_width-185)/self.scale)

    def time_at(self, x):
        fps = self.editor.output_fps
        return max(0, round((x-self.LEFT)/self.scale*fps)/fps)

    def layer_at(self, y):
        return max(0, min(15, int((y-self.TOP)/self.ROW)-1))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor('#111721'))
        step = max(1, int(85/self.scale))
        for sec in range(0, int((self.width()-self.LEFT)/self.scale)+1, step):
            x = self.LEFT+sec*self.scale
            p.setPen(QColor('#263243'))
            p.drawLine(x, self.TOP, x, self.height())
            p.setPen(QColor('#8f9db2'))
            p.drawText(x+3, 22, f'{sec//60:02d}:{sec%60:02d}')
        count = self.layer_count()
        for row in range(1+count+max(1, sum(c.kind == 'audio' for c in self.editor.clips))):
            y = self.TOP+row*self.ROW
            p.setPen(QColor('#243044'))
            p.drawLine(0, y, self.width(), y)
            p.setPen(QColor('#94a6be'))
            label='フィルタ' if row==0 else f'Layer {row}' if row<=count else f'音声 {row-count}'
            p.drawText(10, y+29, label)
        self.boxes = []
        audio_row = count+1
        for c in sorted(self.editor.clips, key=lambda c: (c.layer, self.editor.clips.index(c))):
            row = 0 if c.kind=='filter' else audio_row if c.kind == 'audio' else c.layer+1
            if c.kind == 'audio':
                audio_row += 1
            rect = QRectF(self.LEFT+c.offset*self.scale, self.TOP+row*self.ROW+5, max(5, c.duration*self.scale), self.ROW-10)
            p.setBrush(QColor({'audio':'#246c65','text':'#6f4f8b','image':'#795e36','video':'#294d7c','filter':'#8b4f5b'}[c.kind]))
            p.setPen(QPen(QColor('#a0caff' if c.uid == self.editor.selected else '#47658b'), 2 if c.uid == self.editor.selected else 1))
            p.drawRoundedRect(rect, 4, 4)
            if c.uid == self.editor.selected:
                if c.keyframes:
                    segment=max(0,min(int(self.editor.selected_segment),len(c.keyframes)))
                    left_time=0.0 if segment==0 else float(c.keyframes[segment-1]['time'])
                    right_time=c.duration if segment==len(c.keyframes) else float(c.keyframes[segment]['time'])
                    zone=QRectF(self.LEFT+(c.offset+left_time)*self.scale,rect.top(),max(1,(right_time-left_time)*self.scale),rect.height())
                else:
                    zone=rect
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(133,190,255,115))
                p.drawRoundedRect(zone.intersected(rect),4,4)
                p.setPen(QPen(QColor('#a0caff'),2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect,4,4)
            p.save()
            p.setClipRect(rect.adjusted(5, 2, -4, -2))
            p.setPen(QColor('#eef4ff'))
            p.drawText(int(rect.x()+8), int(rect.y()+20), c.name)
            p.setPen(QColor('#b4c8df'))
            p.drawText(int(rect.x()+8), int(rect.y()+37), f'{c.duration:.2f}s')
            p.restore()
            self.boxes.append((rect, c.uid))
        p.setPen(QPen(QColor('#89bbf5'),1,Qt.DashLine))
        for clip in self.editor.clips:
            rect=next((r for r,uid in self.boxes if uid==clip.uid),None)
            if rect is None:
                continue
            for point in clip.keyframes:
                x=int(self.LEFT+(clip.offset+point['time'])*self.scale)
                p.drawLine(x,rect.top()-3,x,rect.bottom()+3)
                p.setBrush(QColor('#a9d3ff'))
                p.drawEllipse(QPointF(x,rect.top()-1),3.5,3.5)
        x = int(self.LEFT+self.editor.position*self.scale)
        p.setPen(QPen(QColor('#ffbc67'), 2))
        p.drawLine(x, 26, x, self.height())
        p.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self.editor.stop_playback()
        if event.pos().y() >= self.TOP:
            for rect, uid in reversed(self.boxes):
                if rect.contains(event.pos()):
                    c = self.editor.by_id(uid)
                    click_position=self.time_at(event.pos().x())
                    self.editor.select_segment(uid,click_position)
                    edge = 'left' if event.pos().x()-rect.left() < 6 else 'right' if rect.right()-event.pos().x() < 6 else 'move'
                    self.drag = {'uid':uid, 'x':event.pos().x(), 'y':event.pos().y(), 'offset':c.offset, 'layer':c.layer, 'start':c.start, 'duration':c.duration, 'keyframes':copy.deepcopy(c.keyframes), 'mode':edge, 'saved':False}
                    self.editor.seek(click_position)
                    return
        self.scrubbing = True
        self.editor.seek(self.time_at(event.pos().x()))

    def mouseMoveEvent(self, event):
        if not event.buttons() & Qt.LeftButton:
            return
        if self.scrubbing:
            self.editor.seek(self.time_at(event.pos().x()))
            return
        d = self.drag
        if not d or abs(event.pos().x()-d['x'])+abs(event.pos().y()-d['y']) < 3:
            return
        c = self.editor.by_id(d['uid'])
        if c is None:
            return
        if not d['saved']:
            self.editor.checkpoint()
            d['saved'] = True
        delta = round((event.pos().x()-d['x'])/self.scale*self.editor.output_fps)/self.editor.output_fps
        if d['mode'] == 'move':
            c.offset = max(0, d['offset']+delta)
            if c.kind not in ('audio','filter'):
                c.layer = min(self.layer_at(event.pos().y()), self.layer_count()-1)
        elif d['mode'] == 'left':
            lower = max(-d['offset'], -d['start']) if c.kind in ('video','audio') else -d['offset']
            delta = min(d['duration']-.04, max(lower, delta))
            c.offset, c.duration = d['offset']+delta, d['duration']-delta
            c.keyframes=[{'time':float(point['time'])-delta,'values':copy.deepcopy(point.get('values',{}))} for point in d['keyframes'] if float(point['time'])>delta and float(point['time'])-delta<c.duration-.04]
            if c.kind in ('video','audio'):
                c.start = d['start']+delta
        else:
            c.duration = max(.04, min(c.source_duration-c.start, d['duration']+delta))
            c.keyframes=[point for point in d['keyframes'] if float(point['time'])<c.duration-.04]
        self.editor.dirty = True
        self.refresh()
        self.editor.update_properties()
        self.editor.update_title()
        self.editor.seek(min(c.offset+min(.03, c.duration/2), total_duration(self.editor.clips)))

    def mouseReleaseEvent(self, event):
        if self.scrubbing:
            self.editor.seek(self.time_at(event.pos().x()))
        moved = self.drag and self.drag['saved']
        self.scrubbing, self.drag = False, None
        if moved:
            self.editor.refresh()

    def contextMenuEvent(self, event):
        position=self.time_at(event.pos().x())
        hit=next((self.editor.by_id(uid) for rect,uid in reversed(self.boxes) if rect.contains(event.pos())),None)
        menu=QMenu(self)
        if hit and hit.kind=='filter':
            remove=menu.addAction('フィルタを削除')
            if menu.exec_(event.globalPos())==remove:self.editor.delete_clip(hit.uid)
        elif hit:
            add=menu.addAction('中間点を追加')
            points=hit.keyframes
            nearest=min(points,key=lambda p:abs(hit.offset+p['time']-position),default=None)
            remove=menu.addAction(f'中間点を削除  ({hit.offset+nearest["time"]:.2f} 秒)' if nearest else '中間点を削除')
            remove.setEnabled(nearest is not None)
            menu.addSeparator()
            text_action=menu.addAction('テキストを追加…')
            chosen=menu.exec_(event.globalPos())
            if chosen==add:self.editor.add_keyframe(hit.uid,position)
            elif chosen==remove and nearest:self.editor.remove_keyframe(hit.uid,nearest['time'])
            elif chosen==text_action:self.editor.add_text(offset=position,layer=min(self.layer_at(event.pos().y()),self.layer_count()-1))
        else:
            media_action=menu.addAction('素材を追加…')
            filter_menu=menu.addMenu('フィルタを追加')
            filter_actions={name:filter_menu.addAction(name) for name in ('シーンチェンジ','フェード','モザイク','ぼかし')}
            filter_types={'シーンチェンジ':'scenechange','フェード':'fade','モザイク':'mosaic','ぼかし':'blur'}
            text_action=menu.addAction('テキストを追加…')
            chosen=menu.exec_(event.globalPos())
            if chosen==media_action:self.editor.import_files(offset=position,layer=min(self.layer_at(event.pos().y()),self.layer_count()-1))
            elif chosen==text_action:self.editor.add_text(offset=position,layer=min(self.layer_at(event.pos().y()),self.layer_count()-1))
            else:
                selected_type=next((kind for name,kind in filter_types.items() if chosen==filter_actions[name]),None)
                if selected_type:self.editor.add_filter(selected_type,position)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        self.editor.seek(self.time_at(event.pos().x()))
        event.acceptProposedAction()

    def dropEvent(self, event):
        offset = self.time_at(event.pos().x())
        row = self.layer_at(event.pos().y())
        if event.mimeData().hasUrls():
            self.editor.import_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()], offset, min(15, row))
        elif event.mimeData().hasText():
            self.editor.add_text(event.mimeData().text(), offset, min(15, row))
        event.acceptProposedAction()
