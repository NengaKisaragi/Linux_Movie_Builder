"""Timeline, portable projects, and shared live/export FFmpeg composition."""
from dataclasses import dataclass, asdict, field
from pathlib import Path
import hashlib
import json
import math
import os
import subprocess
import tempfile
import uuid
from PIL import Image, ImageDraw, ImageFont

BASE_W, BASE_H = 1280, 720
IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff'}
FONT = '/usr/share/fonts/truetype/fonts-japanese-gothic.ttf'
if not Path(FONT).is_file():
    FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

@dataclass
class Clip:
    path: str
    kind: str
    source_duration: float
    start: float = 0.0
    duration: float = 0.0
    offset: float = 0.0
    volume: float = 1.0
    has_audio: bool = False
    uid: str = ''
    layer: int = 0
    x: float = BASE_W/2
    y: float = BASE_H/2
    scale: float = 1.0
    width: int = 1280
    height: int = 720
    text: str = ''
    font_size: int = 64
    color: str = '#ffffff'
    opacity: float = 1.0
    rotation: float = 0.0
    animation: dict = field(default_factory=dict)
    keyframes: list = field(default_factory=list)
    filter_type: str = ''

    def __post_init__(self):
        if not self.uid:
            self.uid = uuid.uuid4().hex

    @property
    def name(self):
        if self.kind=='filter':return {'scenechange':'シーンチェンジ','fade':'フェード','mosaic':'モザイク','blur':'ぼかし'}.get(self.filter_type,'フィルタ')
        return self.text.replace('\n', ' ')[:40] or 'テキスト' if self.kind == 'text' else Path(self.path).name


def probe(path):
    path = str(Path(path).resolve())
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path], capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    video = next((s for s in data['streams'] if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    audio = any(s.get('codec_type') == 'audio' for s in data['streams'])
    if Path(path).suffix.lower() in IMAGE_EXT:
        if not video:
            raise ValueError('画像を読み込めません')
        return Clip(path, 'image', 86400, duration=5, width=int(video['width']), height=int(video['height']))
    duration = float(data.get('format', {}).get('duration', 0))
    if not (video or audio) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('長さの取得できないメディアです')
    width, height = (int(video['width']), int(video['height'])) if video else (1280, 720)
    # FFmpeg autorotates smartphone footage before filtering.
    if video and any(abs(float(s.get('rotation', 0))) % 180 == 90 for s in video.get('side_data_list', [])):
        width, height = height, width
    return Clip(path, 'video' if video else 'audio', duration, duration=duration, has_audio=audio, width=width, height=height)


def total_duration(clips):
    return max((c.offset+c.duration for c in clips if c.kind!='filter'), default=0.0)


def active_visuals(clips, position):
    return sorted((c for c in clips if c.kind not in ('audio','filter') and c.offset <= position < c.offset+c.duration), key=lambda c: (c.layer, clips.index(c)))


def text_image(clip, directory):
    """Same RGBA text raster for the editor, playback and export."""
    key = hashlib.sha256(json.dumps([clip.text, clip.font_size, clip.color, FONT], ensure_ascii=False).encode()).hexdigest()
    path = Path(directory)/f'text-{key}.png'
    if not path.is_file():
        font = ImageFont.truetype(FONT, clip.font_size)
        measure = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
        left, top, right, bottom = measure.multiline_textbbox((0, 0), clip.text or ' ', font=font, spacing=8, stroke_width=2)
        image = Image.new('RGBA', (min(8192, max(8, right-left+12)), min(4096, max(8, bottom-top+12))), (0, 0, 0, 0))
        ImageDraw.Draw(image).multiline_text((6-left, 6-top), clip.text or ' ', font=font, fill=clip.color, spacing=8, stroke_width=2, stroke_fill='#161c26')
        image.save(path)
    return str(path)


def visual_size(clip, directory=None):
    if clip.kind == 'text':
        if directory is None:
            with tempfile.TemporaryDirectory() as directory:
                with Image.open(text_image(clip, directory)) as image:
                    return image.size
        with Image.open(text_image(clip, directory)) as image:
            return image.size
    return clip.width, clip.height


def placed_size(clip, directory=None):
    w, h = visual_size(clip, directory)
    fit = 1 if clip.kind == 'text' else min(BASE_W/w, BASE_H/h)
    return max(2, w*fit*clip.scale), max(2, h*fit*clip.scale)


def validate(clips):
    ids = set()
    for c in clips:
        if c.kind not in ('video', 'image', 'audio', 'text','filter'):
            raise ValueError('素材の種類が無効です')
        if c.kind not in ('text','filter') and not Path(c.path).is_file():
            raise ValueError(f'素材が見つかりません: {c.path}')
        if c.kind=='filter' and c.filter_type not in ('scenechange','fade','mosaic','blur'):
            raise ValueError('フィルタの種類が無効です')
        for n in (c.source_duration, c.start, c.duration, c.offset, c.volume, c.x, c.y, c.scale, c.opacity, c.rotation, c.width, c.height, c.font_size):
            if not isinstance(n, (float, int)) or not math.isfinite(n):
                raise ValueError('数値が無効です')
        if c.duration < .04 or c.start < 0 or c.offset < 0 or not 0 <= c.volume <= 2:
            raise ValueError('クリップの範囲または音量が無効です')
        if c.start+c.duration > c.source_duration+.05:
            raise ValueError('素材の長さを超えています')
        if not isinstance(c.keyframes,list):
            raise ValueError('中間点の形式が無効です')
        times=[]
        for keyframe in c.keyframes:
            if not isinstance(keyframe,dict) or not math.isfinite(float(keyframe.get('time',-1))) or not 0 < float(keyframe['time']) < c.duration or not isinstance(keyframe.get('values',{}),dict):
                raise ValueError('中間点の位置または値が無効です')
            if any(k not in ('x','y','scale','opacity','rotation','volume') or not math.isfinite(float(v)) for k,v in keyframe['values'].items()):
                raise ValueError('中間点のプロパティ値が無効です')
            times.append(float(keyframe['time']))
        if times != sorted(set(times)):
            raise ValueError('中間点が重複または順不同です')
        if not 0 <= c.opacity <= 1 or not -3600 <= c.rotation <= 3600 or not isinstance(c.animation, dict):
            raise ValueError('不透明度・回転・アニメーションが無効です')
        for key, anim in c.animation.items():
            if key not in ('x','y','scale','opacity','rotation','volume') or not isinstance(anim,dict) or anim.get('mode') not in ('none','linear','ease','curve','instant','random','repeat') or not math.isfinite(float(anim.get('end',0))) or not 0 < float(anim.get('cycles',3)) <= 1000:
                raise ValueError('アニメーション設定が無効です')
        if not isinstance(c.layer, int) or not 0 <= c.layer <= 15 or not .01 <= c.scale <= 5 or min(c.width, c.height) < 1:
            raise ValueError('レイヤーまたは拡大率が無効です')
        if c.kind == 'text':
            if not isinstance(c.text, str) or len(c.text) > 4000 or not 8 <= c.font_size <= 300:
                raise ValueError('テキストの長さまたは文字サイズが無効です')
            from PIL import ImageColor
            ImageColor.getrgb(c.color)
        if c.uid in ids:
            raise ValueError('クリップIDが重複しています')
        ids.add(c.uid)


def save_project(path, clips, settings):
    validate(clips)
    base = Path(path).resolve().parent
    records = []
    for clip in clips:
        record = asdict(clip)
        record['path'] = os.path.relpath(clip.path, base) if clip.kind not in ('text','filter') else ''
        records.append(record)
    data = {'application': 'Linux Movie Builder', 'version': 3, 'settings': settings, 'clips': records}
    fd, tmp = tempfile.mkstemp(dir=base, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_project(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('application') != 'Linux Movie Builder' or data.get('version') not in (1, 2, 3):
        raise ValueError('対応するプロジェクトファイルではありません')
    clips, elapsed = [], 0.0
    for record in data['clips']:
        if record['kind'] not in ('text','filter'):
            record['path'] = str((Path(path).resolve().parent/record['path']).resolve())
        if data['version'] == 1:
            if record['kind'] != 'audio':
                record['offset'] = elapsed
                elapsed += record['duration']
            meta = probe(record['path'])
            record['width'], record['height'] = meta.width, meta.height
        elif data['version']==2:
            for key,anim in record.get('animation',{}).items():
                if key in ('scale','opacity','volume') and float(anim.get('end',0)) > {'scale':5,'opacity':1,'volume':2}[key]:
                    anim['end']=float(anim['end'])/100
        clips.append(Clip(**record))
    validate(clips)
    settings = data.get('settings', {'resolution':'1920x1080','fps':60})
    try:
        width,height=map(int,str(settings.get('resolution','')).split('x'))
        fps=int(settings.get('fps'))
    except (TypeError,ValueError):
        raise ValueError('出力設定が無効です')
    if not 2<=width<=16384 or not 2<=height<=16384 or width%2 or height%2 or not 1<=fps<=240:
        raise ValueError('出力設定が無効です')
    settings={'resolution':f'{width}x{height}','fps':fps}
    return clips, settings


def _remaining(clip, position):
    skip = max(0, position-clip.offset)
    return skip, clip.duration-skip, max(0, clip.offset-position)


def animation_nodes(clip, key, start=None):
    base = float(getattr(clip,key) if start is None else start)
    anim = clip.animation.get(key, {'mode':'none','end':base})
    nodes=[(0.0,base)]
    for point in clip.keyframes:
        if key in point.get('values',{}):
            nodes.append((float(point['time']),float(point['values'][key])))
    nodes.append((float(clip.duration),float(anim.get('end',base))))
    return nodes


def _progress(mode, p, anim, t, t0, t1, comma):
    if mode=='linear':return p
    if mode=='ease':return f'(0.5-0.5*cos(PI*({p})))'
    if mode=='curve':return f'pow(({p}){comma}{float(anim.get("strength",3)):.6g})'
    if mode=='instant':return f'if(lte({t}{comma}{t0}){comma}0{comma}1)'
    if mode=='random':return f'abs(sin(floor(max(0{comma}{t}-{t0})*8)*12.9898+78.233))'
    cycles=int(anim.get('cycles',3))
    phase=f'mod(({p})*{cycles}*2{comma}2)'
    return f'if(lt({phase}{comma}1){comma}{phase}{comma}2-({phase}))'


def animated_value(clip, key, position, start=None):
    nodes=animation_nodes(clip,key,start)
    anim=clip.animation.get(key,{'mode':'none'})
    mode=anim.get('mode','none')
    if mode=='none':return nodes[0][1]
    t=max(0.0,min(clip.duration,position-clip.offset))
    for i,((t0,v0),(t1,v1)) in enumerate(zip(nodes,nodes[1:])):
        if t < t1 or i==len(nodes)-2:
            p=max(0.0,min(1.0,(t-t0)/max(.001,t1-t0)))
            f=_progress(mode,p,anim,t,t0,t1,',')
            if mode=='linear':f=p
            elif mode=='ease':f=(1-math.cos(math.pi*p))/2
            elif mode=='curve':f=p**float(anim.get('strength',3))
            elif mode=='instant':f=0.0 if t<=t0 else 1.0
            elif mode=='random':f=abs(math.sin(math.floor(max(0,t-t0)*8)*12.9898+78.233))
            return v0+(v1-v0)*f
    return nodes[-1][1]


def _expr(clip, key, base, time='t'):
    anim=clip.animation.get(key,{'mode':'none'})
    nodes=animation_nodes(clip,key,base)
    if anim.get('mode','none')=='none':return f'{nodes[0][1]:.9g}'
    sep=r'\,'
    result=f'{nodes[-1][1]:.9g}'
    for (t0,v0),(t1,v1) in reversed(list(zip(nodes,nodes[1:]))):
        absolute0=clip.offset+t0
        absolute1=clip.offset+t1
        p=f'min(max(({time}-{absolute0:.9g})/{max(.001,t1-t0):.9g}{sep}0){sep}1)'
        f=_progress(anim['mode'],p,anim,time,absolute0,absolute1,sep)
        expression=f'({v0:.9g}+(({v1:.9g})-({v0:.9g}))*({f}))'
        result=f'if(lt({time}{sep}{absolute1:.9g}){sep}{expression}{sep}{result})'
    return result


def composition(clips, resolution, fps, directory, position=0, realtime=False, video=True, audio=True):
    """One composition graph used by export and streaming, seeking inputs directly."""
    validate(clips)
    if not clips:
        raise ValueError('素材を追加してください')
    width, height = map(int, resolution.split('x'))
    if min(width, height) < 2 or max(width,height)>16384 or width % 2 or height % 2 or not 1<=int(fps)<=240:
        raise ValueError('出力設定が無効です')
    duration = total_duration(clips)-position
    if duration <= 0:
        raise ValueError('終端です。再生位置を前に戻してください')
    args = ['ffmpeg', '-hide_banner', '-nostdin', '-filter_complex_threads', '1', '-threads', '2']
    filters, audio_tracks = [], []
    next_input = 0
    if video:
        if realtime:
            args += ['-re', '-readrate_initial_burst', '0']
        args += ['-f', 'lavfi', '-i', f'color=c=black:s={width}x{height}:r={fps}:d={duration:.6f}']
        filters.append('[0:v]format=rgba[base0]')
        next_input = 1
    if audio:
        if realtime and not video:
            args += ['-re', '-readrate_initial_burst', '0']
        index = next_input
        next_input += 1
        args += ['-f', 'lavfi', '-i', f'anullsrc=r=48000:cl=stereo:d={duration:.6f}']
        filters.append(f'[{index}:a]anull[silence]')
        audio_tracks.append('[silence]')
    visual_index = 0
    global_filters=sorted((c for c in clips if c.kind=='filter'),key=lambda c:(c.offset,clips.index(c)))
    for c in sorted((c for c in clips if c.kind!='filter'), key=lambda c: (c.layer, clips.index(c))):
        skip, remaining, delay = _remaining(c, position)
        use_video = video and c.kind != 'audio'
        use_audio = audio and c.has_audio
        if remaining <= 0 or not (use_video or use_audio):
            continue
        i = next_input
        next_input += 1
        path = text_image(c, directory) if c.kind == 'text' else c.path
        if c.kind in ('image', 'text'):
            args += ['-loop', '1', '-framerate', str(fps)]
        else:
            args += ['-ss', f'{c.start+skip:.6f}']
        args += ['-threads', '2', '-t', f'{remaining:.6f}', '-i', path]
        if use_video:
            w, h = placed_size(c, directory)
            w, h = max(2, round(w/c.scale*width/BASE_W)), max(2, round(h/c.scale*height/BASE_H))
            sx = _expr(c, 'scale', c.scale, time=f't+{position:.9g}')
            xx = _expr(c, 'x', c.x, time=f't+{position:.9g}')
            yy = _expr(c, 'y', c.y, time=f't+{position:.9g}')
            angle = _expr(c, 'rotation', c.rotation, time=f't+{position:.9g}')
            alpha = _expr(c, 'opacity', c.opacity, time=f't+{position:.9g}')
            chain=[f'fps={fps}',f'trim=duration={remaining:.6f}',f'setpts=PTS-STARTPTS+{delay:.6f}/TB',f"scale=w='max(2,trunc({w}*({sx})/2)*2)':h='max(2,trunc({h}*({sx})/2)*2)':eval=frame",'setsar=1','format=rgba']
            if c.rotation!=0 or c.animation.get('rotation',{}).get('mode','none')!='none':
                chain.append(f"rotate=a='({angle})*PI/180':ow='rotw(iw)':oh='roth(ih)':c=none")
            if c.opacity!=1 or c.animation.get('opacity',{}).get('mode','none')!='none':
                chain.append(f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({alpha})'")
            filters.append(f"[{i}:v]"+','.join(chain)+f'[layer{visual_index}]')
            x = f"(({xx})*main_w/{BASE_W}-overlay_w/2)"
            y = f"(({yy})*main_h/{BASE_H}-overlay_h/2)"
            filters.append(f"[base{visual_index}][layer{visual_index}]overlay=x='{x}':y='{y}':eof_action=pass:repeatlast=0:enable='between(t\\,{delay:.6f}\\,{delay+remaining:.6f})'[base{visual_index+1}]")
            visual_index += 1
        if use_audio:
            millis = round(delay*1000)
            label = f'a{i}'
            vol = _expr(c, 'volume', c.volume, time=f't+{position:.9g}')
            filters.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=duration={remaining:.6f},asetpts=PTS-STARTPTS+{delay:.6f}/TB,volume=volume='{vol}':eval=frame,adelay={millis}|{millis}[{label}]")
            audio_tracks.append(f'[{label}]')
    if video:
        current=f'base{visual_index}'
        filter_no=0
        for clip in global_filters:
            start=max(0.0,clip.offset-position)
            end=min(duration,clip.offset+clip.duration-position)
            if end<=start:continue
            enable=f"between(t\\,{start:.6f}\\,{end:.6f})"
            if clip.filter_type=='blur':chain=f"boxblur=20:enable='{enable}'"
            elif clip.filter_type=='mosaic':chain=f"pixelize=width=24:height=24:enable='{enable}'"
            elif clip.filter_type=='fade':
                half=min(.25,(end-start)/2)
                chain=f"fade=t=in:st={start:.6f}:d={half:.6f},fade=t=out:st={end-half:.6f}:d={half:.6f}"
            else:
                half=min(.3,(end-start)/2)
                chain=f"fade=t=out:st={start:.6f}:d={half:.6f},fade=t=in:st={end-half:.6f}:d={half:.6f}"
            label=f'global{filter_no}'
            filters.append(f'[{current}]{chain}[{label}]')
            current=label;filter_no+=1
        filters.append(f'[{current}]trim=duration={duration:.6f},format=rgb24[video]')
    if audio:
        filters.append(''.join(audio_tracks)+f'amix=inputs={len(audio_tracks)}:duration=longest:normalize=0,alimiter=limit=0.95:level=0:latency=1,atrim=duration={duration:.6f}[audio]')
    args += ['-filter_complex', ';'.join(filters)]
    return args, duration


def render_command(clips, output, resolution='1920x1080', fps=60, preview=False, asset_dir=None):
    directory = asset_dir or str(Path(output).parent)
    args, duration = composition(clips, resolution, fps, directory)
    args += ['-y', '-map', '[video]', '-map', '[audio]', '-t', f'{duration:.6f}', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'ultrafast' if preview else 'veryfast', '-crf', '26' if preview else '20', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', str(output)]
    return args


def video_stream_command(clips, position, directory):
    args, duration = composition(clips, '800x450', 30, directory, position, True, video=True, audio=False)
    return args+['-map', '[video]', '-t', f'{duration:.6f}', '-pix_fmt', 'rgb24', '-threads', '1', '-f', 'rawvideo', 'pipe:1']


def audio_stream_command(clips, position, directory):
    args, duration = composition(clips, '800x450', 30, directory, position, True, video=False, audio=True)
    return args+['-map', '[audio]', '-t', f'{duration:.6f}', '-ac', '2', '-ar', '48000', '-f', 's16le', 'pipe:1']
