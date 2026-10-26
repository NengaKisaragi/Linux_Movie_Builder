"""Interactive composition canvas and layered timeline."""
import copy
from PyQt5.QtCore import Qt, QRectF, QPointF, QPoint, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QApplication, QWidget, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsItem, QMenu
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
        self.selected_uids = {editor.selected} if editor.selected else set()
        self.selection_rect = None
        self.selection_origin = None
        self.selection_base = set()
        self.selection_mode = None
        self.right_press = None
        self.resize_lock = False
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumHeight(290)

    def layer_count(self):
        return max(4, max((c.layer+1 for c in self.editor.clips), default=4))

    def refresh(self):
        self.setMinimumWidth(max(1100, int(max(30,total_duration(self.editor.clips)+15)*self.scale)+185))
        self.setMinimumHeight(self.TOP+self.ROW*self.layer_count()+30)
        self.update()

    def seek_limit(self):
        desired_width=max(1100,int(max(30,total_duration(self.editor.clips)+15)*self.scale)+185)
        visible_width=max(self.width(),desired_width)
        return max(total_duration(self.editor.clips)+15,30.0,(visible_width-185)/self.scale)

    def time_at(self, x):
        fps = self.editor.output_fps
        return max(0, round((x-self.LEFT)/self.scale*fps)/fps)

    def layer_at(self, y):
        return max(0, min(15, int((y-self.TOP)/self.ROW)))

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
        for row in range(count):
            y = self.TOP+row*self.ROW
            p.setPen(QColor('#243044'))
            p.drawLine(0, y, self.width(), y)
            p.setPen(QColor('#94a6be'))
            label=f'Layer {row+1}'
            p.drawText(10, y+29, label)
        self.boxes = []
        for c in sorted(self.editor.clips, key=lambda c: (c.layer, self.editor.clips.index(c))):
            row = c.layer
            rect = QRectF(self.LEFT+c.offset*self.scale, self.TOP+row*self.ROW+5, max(5, c.duration*self.scale), self.ROW-10)
            p.setBrush(QColor({'audio':'#246c65','text':'#6f4f8b','image':'#795e36','video':'#294d7c','filter':'#8b4f5b'}[c.kind]))
            selected=c.uid in self.selected_uids
            p.setPen(QPen(QColor('#a0caff' if selected else '#47658b'), 2 if selected else 1))
            p.drawRoundedRect(rect, 4, 4)
            if selected:
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
                p.setPen(QPen(QColor('#a9d3ff'),2))
                p.drawLine(x,rect.top()-3,x,rect.bottom()+3)
                p.setBrush(QColor('#a9d3ff'))
                p.drawEllipse(QPointF(x,rect.top()-1),4.5,4.5)
        if self.selection_rect is not None:
            p.setPen(QPen(QColor(133,190,255,220),1,Qt.DashLine))
            p.setBrush(QColor(83,145,220,55))
            p.drawRect(self.selection_rect.normalized())
        x = int(self.LEFT+self.editor.position*self.scale)
        p.setPen(QPen(QColor('#ffbc67'), 2))
        p.drawLine(x, 26, x, self.height())
        p.end()

    def hit_clip(self, position):
        for clip in reversed(sorted(self.editor.clips,key=lambda c:(c.layer,self.editor.clips.index(c)))):
            row=clip.layer
            rect=QRectF(self.LEFT+clip.offset*self.scale,self.TOP+row*self.ROW+5,max(5,clip.duration*self.scale),self.ROW-10)
            if rect.adjusted(-1,-1,1,1).contains(position):
                return rect,clip
        return None

    def apply_selection(self, uids, active=None, position=None):
        chosen=set(uids)
        if active is not None:
            if position is None:self.editor.select(active)
            else:self.editor.select_segment(active,position)
        else:
            self.editor.select(None)
        self.selected_uids=chosen
        self.update()

    def select_in_rectangle(self, additive):
        rect=self.selection_rect.normalized() if self.selection_rect is not None else QRectF()
        chosen=set(self.selection_base) if additive else set()
        chosen.update(uid for box,uid in self.boxes if box.intersects(rect))
        active=next((uid for box,uid in reversed(self.boxes) if uid in chosen and box.intersects(rect)),next(iter(chosen),None))
        self.apply_selection(chosen,active,self.editor.position if active else None)

    def show_timeline_menu(self, position, global_position):
        position_time=self.time_at(position.x())
        layer=min(15,self.layer_at(position.y()))
        hit=self.hit_clip(position)
        menu=QMenu(self)
        if hit:
            clip=hit[1]
            if clip.kind=='filter':
                remove=menu.addAction('フィルタを削除')
            else:
                add=menu.addAction('中間点を追加')
                points=clip.keyframes
                nearest=min(points,key=lambda p:abs(clip.offset+p['time']-position_time),default=None)
                remove=menu.addAction(f'中間点を削除  ({clip.offset+nearest["time"]:.2f} 秒)' if nearest else '中間点を削除')
                remove.setEnabled(nearest is not None)
            menu.addSeparator()
        else:
            text_action=menu.addAction('テキストを追加')
            media_action=menu.addAction('素材を追加…')
            filter_menu=menu.addMenu('フィルタを追加')
            filter_actions={name:filter_menu.addAction(name) for name in ('シーンチェンジ','フェード','モザイク','ぼかし')}
            filter_types={'シーンチェンジ':'scenechange','フェード':'fade','モザイク':'mosaic','ぼかし':'blur'}
            menu.addSeparator()
        selected=set(self.selected_uids)
        if not selected and self.editor.selected:selected={self.editor.selected}
        copy_action=menu.addAction('コピー')
        cut_action=menu.addAction('切り取り')
        paste_action=menu.addAction('貼り付け')
        copy_action.setEnabled(bool(selected))
        cut_action.setEnabled(bool(selected))
        paste_action.setEnabled(bool(self.editor.timeline_clipboard))
        chosen=menu.exec_(global_position)
        if hit:
            clip=hit[1]
            if clip.kind=='filter' and chosen==remove:self.editor.delete_clip(clip.uid)
            elif clip.kind!='filter' and chosen==add:self.editor.add_keyframe(clip.uid,position_time)
            elif clip.kind!='filter' and chosen==remove and nearest:self.editor.remove_keyframe(clip.uid,nearest['time'])
        else:
            if chosen==text_action:self.editor.add_text(offset=position_time,layer=layer)
            elif chosen==media_action:self.editor.import_files(offset=position_time,layer=layer)
            else:
                selected_type=next((kind for name,kind in filter_types.items() if chosen==filter_actions[name]),None)
                if selected_type:self.editor.add_filter(selected_type,position_time,layer)
        if chosen==copy_action:self.editor.copy_timeline_clips(selected)
        elif chosen==cut_action:self.editor.cut_timeline_clips(selected)
        elif chosen==paste_action:self.editor.paste_timeline_clips(position_time)

    def mousePressEvent(self, event):
        if event.button()==Qt.RightButton:
            self.editor.stop_playback()
            hit=self.hit_clip(event.pos())
            self.right_press={'pos':QPoint(event.pos()),'global':QPoint(event.globalPos()),'hit':hit[1].uid if hit else None,'modifiers':event.modifiers(),'moved':False}
            self.drag=None
            self.scrubbing=False
            if hit is None:
                self.selection_mode='right'
                self.selection_origin=QPoint(event.pos())
                self.selection_current=QPoint(event.pos())
                additive=bool(event.modifiers() & (Qt.ControlModifier|Qt.ShiftModifier))
                self.selection_base=set(self.selected_uids) if additive else set()
                self.selection_rect=QRectF(self.selection_origin,self.selection_current)
            event.accept()
            return
        if event.button()!=Qt.LeftButton:return
        self.editor.stop_playback()
        self.drag=None
        self.scrubbing=False
        self.selection_mode=None
        self.selection_rect=None
        hit=self.hit_clip(event.pos()) if event.pos().y()>=self.TOP else None
        modifiers=event.modifiers()
        if hit:
            rect,c=hit
            click_position=self.time_at(event.pos().x())
            old_selection=set(self.selected_uids)
            ctrl=bool(modifiers & Qt.ControlModifier)
            shift=bool(modifiers & Qt.ShiftModifier)
            if ctrl:
                next_selection=set(old_selection)
                if c.uid in next_selection:next_selection.remove(c.uid)
                else:next_selection.add(c.uid)
                active=c.uid if c.uid in next_selection else next(iter(next_selection),None)
                self.apply_selection(next_selection,active,click_position if active==c.uid else self.editor.position)
            elif shift:
                next_selection=old_selection|{c.uid}
                self.apply_selection(next_selection,c.uid,click_position)
            elif c.uid in old_selection and len(old_selection)>1:
                self.editor.select_segment(c.uid,click_position)
                self.selected_uids=old_selection
            else:
                self.apply_selection({c.uid},c.uid,click_position)
            point_index=min(range(len(c.keyframes)),key=lambda i:abs(self.LEFT+(c.offset+float(c.keyframes[i]['time']))*self.scale-event.pos().x()),default=None)
            point_x=self.LEFT+(c.offset+float(c.keyframes[point_index]['time']))*self.scale if point_index is not None else None
            if point_index is not None and abs(point_x-event.pos().x())<=8:mode='keyframe'
            elif rect.width()<24:mode='move'
            else:
                edge_zone=min(8.0,rect.width()/3)
                mode='left' if event.pos().x()-rect.left()<edge_zone else 'right' if rect.right()-event.pos().x()<edge_zone else 'move'
            group=[{'uid':clip.uid,'offset':clip.offset,'layer':clip.layer} for clip in self.editor.clips if clip.uid in self.selected_uids] if mode=='move' else None
            self.drag={'uid':c.uid,'x':event.pos().x(),'y':event.pos().y(),'offset':c.offset,'layer':c.layer,'start':c.start,'duration':c.duration,'keyframes':copy.deepcopy(c.keyframes),'point_index':point_index,'mode':mode,'saved':False,'group':group,'modifiers':modifiers}
            event.accept()
            return
        if self.resize_lock:
            event.accept()
            return
        if modifiers & Qt.ControlModifier:
            self.selection_mode='left'
            self.selection_origin=QPoint(event.pos())
            self.selection_current=QPoint(event.pos())
            self.selection_base=set(self.selected_uids)
            self.selection_rect=QRectF(self.selection_origin,self.selection_current)
            event.accept()
            return
        self.scrubbing=True
        self.editor.seek(self.time_at(event.pos().x()))

    def mouseMoveEvent(self, event):
        if self.selection_mode:
            self.selection_current=QPoint(event.pos())
            self.selection_rect=QRectF(self.selection_origin,self.selection_current).normalized()
            if self.right_press:self.right_press['moved']=self.selection_rect.width()+self.selection_rect.height()>=5
            self.update()
            return
        dragging=bool((event.buttons()|QApplication.mouseButtons()) & (Qt.LeftButton|Qt.RightButton))
        if not dragging:
            hit=next(((rect,self.editor.by_id(uid)) for rect,uid in reversed(self.boxes) if rect.adjusted(-8,0,8,0).contains(event.pos())),None)
            resize=False
            move=bool(hit)
            if hit:
                rect,clip=hit
                resize=rect.width()>=24 and (event.pos().x()-rect.left()<min(8.0,rect.width()/3) or rect.right()-event.pos().x()<min(8.0,rect.width()/3))
                if clip:resize=resize or any(abs(self.LEFT+(clip.offset+float(point['time']))*self.scale-event.pos().x())<=8 for point in clip.keyframes)
            self.resize_lock=resize
            self.setCursor(Qt.SizeHorCursor if resize else Qt.SizeAllCursor if move else Qt.ArrowCursor)
            return
        if self.scrubbing:
            self.editor.seek(self.time_at(event.pos().x()))
            return
        self.drag_to(event.pos())
        event.accept()

    def drag_to(self, position):
        d=self.drag
        if not d or abs(position.x()-d['x'])+abs(position.y()-d['y'])<3:return
        c=self.editor.by_id(d['uid'])
        if c is None:return
        if not d['saved']:
            self.editor.checkpoint()
            d['saved']=True
        delta=round((position.x()-d['x'])/self.scale*self.editor.output_fps)/self.editor.output_fps
        if d['mode']=='keyframe':
            index=d['point_index']
            if index is not None and index<len(c.keyframes):
                points=d['keyframes'];frame=1/self.editor.output_fps
                lower=float(points[index-1]['time'])+frame if index else frame
                upper=float(points[index+1]['time'])-frame if index+1<len(points) else d['duration']-frame
                local=max(lower,min(upper,float(points[index]['time'])+delta))
                c.keyframes[index]['time']=local;c.keyframes.sort(key=lambda point:float(point['time']))
                self.editor.select_segment(c.uid,c.offset+local)
        elif d['mode']=='move':
            group=d['group'] or [{'uid':c.uid,'offset':d['offset'],'layer':d['layer']}]
            delta=max(-min(item['offset'] for item in group),delta)
            layer_delta=round((position.y()-d['y'])/self.ROW)
            layer_delta=max(-min(item['layer'] for item in group),min(15-max(item['layer'] for item in group),layer_delta))
            for item in group:
                clip=self.editor.by_id(item['uid'])
                if clip:clip.offset=item['offset']+delta;clip.layer=item['layer']+layer_delta
        elif d['mode']=='left':
            lower=max(-d['offset'],-d['start']) if c.kind in ('video','audio') else -d['offset']
            delta=min(d['duration']-.04,max(lower,delta));c.offset=d['offset']+delta;c.duration=d['duration']-delta
            c.keyframes=[{'time':float(point['time'])-delta,'values':copy.deepcopy(point.get('values',{}))} for point in d['keyframes'] if float(point['time'])>delta and float(point['time'])-delta<c.duration-.04]
            if c.kind in ('video','audio'):c.start=d['start']+delta
        else:
            c.duration=max(.04,min(c.source_duration-c.start,d['duration']+delta))
            c.keyframes=[point for point in d['keyframes'] if float(point['time'])<c.duration-.04]
        self.editor.dirty=True;self.refresh();self.editor.update_properties();self.editor.update_title()
        if d['mode']=='keyframe' and d['point_index'] is not None:
            self.editor.select_segment(c.uid,c.offset+float(c.keyframes[d['point_index']]['time']))

    def mouseReleaseEvent(self, event):
        if event.button()==Qt.RightButton and self.right_press:
            press=self.right_press
            moved=press['moved']
            if self.selection_mode=='right':
                if moved:self.select_in_rectangle(bool(press['modifiers'] & (Qt.ControlModifier|Qt.ShiftModifier)))
                self.selection_rect=None;self.selection_mode=None;self.update()
            if not moved:
                if press['hit']:
                    if press['hit'] not in self.selected_uids:self.apply_selection({press['hit']},press['hit'],self.time_at(event.pos().x()))
                    else:self.apply_selection(self.selected_uids,press['hit'],self.time_at(event.pos().x()))
                QTimer.singleShot(0,lambda p=QPoint(event.pos()),g=QPoint(event.globalPos()):self.show_timeline_menu(p,g))
            self.right_press=None
            event.accept();return
        if self.selection_mode=='left':
            self.selection_current=QPoint(event.pos());self.selection_rect=QRectF(self.selection_origin,self.selection_current).normalized()
            if self.selection_rect.width()+self.selection_rect.height()>=5:self.select_in_rectangle(True)
            self.selection_rect=None;self.selection_mode=None;self.update();event.accept();return
        if self.scrubbing:self.editor.seek(self.time_at(event.pos().x()))
        drag=self.drag
        self.scrubbing,self.drag=False,None
        moved=bool(drag and drag['saved'])
        if drag and not moved and drag['mode']=='move' and abs(event.pos().x()-drag['x'])+abs(event.pos().y()-drag['y'])>=3:
            group=drag['group'] or [{'uid':drag['uid'],'offset':drag['offset'],'layer':drag['layer']}]
            delta=round((event.pos().x()-drag['x'])/self.scale*self.editor.output_fps)/self.editor.output_fps
            delta=max(-min(item['offset'] for item in group),delta)
            layer_delta=round((event.pos().y()-drag['y'])/self.ROW)
            layer_delta=max(-min(item['layer'] for item in group),min(15-max(item['layer'] for item in group),layer_delta))
            if delta or layer_delta:
                self.editor.checkpoint()
                for item in group:
                    clip=self.editor.by_id(item['uid'])
                    if clip:clip.offset=item['offset']+delta;clip.layer=item['layer']+layer_delta
                self.editor.dirty=True;self.editor.update_title();moved=True
        if drag and not moved and drag['group'] and not drag['modifiers']:
            self.apply_selection({drag['uid']},drag['uid'],self.time_at(event.pos().x()))
        if moved:self.editor.refresh()

    def contextMenuEvent(self, event):
        event.accept()

    def leaveEvent(self, event):
        self.resize_lock=False
        self.unsetCursor()
        super().leaveEvent(event)

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
