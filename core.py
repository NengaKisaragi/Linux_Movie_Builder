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
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont

BASE_W, BASE_H = 1280, 720


def base_for(resolution):
    """Editing canvas for an output size: height stays 720, width follows the aspect ratio."""
    try:
        w, h = map(int, str(resolution).split('x'))
        return max(2, round(BASE_H*w/h)), BASE_H
    except (ValueError, ZeroDivisionError):
        return BASE_W, BASE_H


def preview_size(resolution):
    """Even-sized live-preview frame (height 450) with the output aspect ratio."""
    bw, bh = base_for(resolution)
    w = max(2, round(450*bw/bh/2)*2)
    return min(w, 1600), 450
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
    fade_in: float = 0.0
    fade_out: float = 0.0
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
    font_family: str = '既定'
    color: str = '#ffffff'
    outline_color: str = '#161c26'
    outline_width: int = 2
    shadow_color: str = '#000000'
    shadow_size: int = 0
    shadow_blur: int = 0
    opacity: float = 1.0
    rotation: float = 0.0
    animation: dict = field(default_factory=dict)
    keyframes: list = field(default_factory=list)
    filter_type: str = ''
    filter_strength: int = 24
    speed: float = 1.0
    pan: float = 0.0
    blend: str = 'normal'
    effects: list = field(default_factory=list)
    shape_type: str = 'rect'
    line_width: int = 0
    text_align: str = 'left'
    line_spacing: int = 8

    def __post_init__(self):
        if not self.uid:
            self.uid = uuid.uuid4().hex

    @property
    def name(self):
        if self.kind=='filter':
            if self.filter_type=='effect':
                return ' + '.join(EFFECTS[e['type']]['name'] for e in self.effects if e.get('type') in EFFECTS)[:40] or 'フィルタ効果'
            return FILTER_NAMES.get(self.filter_type,'フィルタ')
        if self.kind=='shape':return SHAPES.get(self.shape_type,'図形')
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


FILTER_NAMES={'scenechange':'シーンチェンジ','fade':'フェード','mosaic':'モザイク','blur':'ぼかし','effect':'フィルタ効果'}
SHAPES={'rect':'四角形','rounded':'角丸四角形','circle':'円','triangle':'三角形','star':'星','hexagon':'六角形'}
BLEND_MODES={'normal':'通常','add':'加算','subtract':'減算','multiply':'乗算','screen':'スクリーン','overlay':'オーバーレイ','lighten':'比較(明)','darken':'比較(暗)','difference':'差分'}
# FFmpeg blend mode and the layer background colour that leaves the base unchanged.
_BLEND_FF={'add':('addition','black'),'subtract':('subtract','black'),'multiply':('multiply','white'),'screen':('screen','black'),'overlay':('overlay','0x808080'),'lighten':('lighten','black'),'darken':('darken','white'),'difference':('difference','black')}
ANIMATED_KEYS=('x','y','scale','opacity','rotation','volume')


def _p(key,label,low,high,default,kind='number'):
    return {'key':key,'label':label,'min':low,'max':high,'default':default,'type':kind}


# AviUtl-style "フィルタ効果". Numbers are in 1280x720 editing pixels where relevant.
EFFECTS={
    'color':{'name':'色調補正','params':[_p('brightness','明るさ',-100,100,0),_p('contrast','コントラスト',0,300,100),_p('saturation','彩度',0,200,100),_p('hue','色相',-180,180,0)]},
    'mono':{'name':'単色化','params':[_p('strength','強さ',0,100,100),_p('color','色',None,None,'#ffffff','color')]},
    'invert':{'name':'反転','params':[_p('hflip','左右反転',0,1,1,'bool'),_p('vflip','上下反転',0,1,0,'bool'),_p('negate','輝度反転',0,1,0,'bool')]},
    'blur':{'name':'ぼかし','params':[_p('size','範囲',0,200,5)]},
    'mosaic':{'name':'モザイク','params':[_p('size','サイズ',1,400,12)]},
    'sharpen':{'name':'シャープ','params':[_p('strength','強さ',0,100,40)]},
    'noise':{'name':'ノイズ','params':[_p('strength','強さ',0,100,20)]},
    'border':{'name':'縁取り','params':[_p('size','サイズ',1,100,4),_p('blur','ぼかし',0,50,0),_p('color','色',None,None,'#000000','color')]},
    'shadow':{'name':'シャドー','params':[_p('x','X',-200,200,8),_p('y','Y',-200,200,8),_p('opacity','濃さ',0,100,60),_p('blur','拡散',0,50,6),_p('color','色',None,None,'#000000','color')]},
    'glow':{'name':'発光','params':[_p('strength','強さ',0,100,60),_p('size','拡散',1,100,12)]},
    'chromakey':{'name':'クロマキー','params':[_p('color','キー色',None,None,'#00ff00','color'),_p('similarity','色差範囲',1,100,30),_p('blend','境界補正',0,100,10)]},
    'crop':{'name':'クリッピング','params':[_p('top','上（%）',0,100,0),_p('bottom','下（%）',0,100,0),_p('left','左（%）',0,100,0),_p('right','右（%）',0,100,0)]},
    'shift':{'name':'色ずれ','params':[_p('distance','ずれ幅',0,100,6)]},
    'vignette':{'name':'周辺減光','params':[_p('strength','強さ',0,100,50)]},
    'edge':{'name':'エッジ抽出','params':[]},
    'afterimage':{'name':'残像','params':[_p('decay','残る強さ',1,99,85)]},
}


def new_effect(kind):
    return {'type':kind,'enabled':True,'params':{p['key']:p['default'] for p in EFFECTS[kind]['params']}}


def _rgb(color):
    from PIL import ImageColor
    return ImageColor.getrgb(color)[:3]


def _hex(color):
    r,g,b=_rgb(color)
    return f'0x{r:02x}{g:02x}{b:02x}'


def _mixer(m):
    names=('rr','rg','rb','gr','gg','gb','br','bg','bb')
    return 'colorchannelmixer='+':'.join(f'{n}={max(-2,min(2,v)):.5f}' for n,v in zip(names,(x for row in m for x in row)))


def _matmul(a,b):
    return [[sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def effect_graph(effect, source, target, tag, factor=1.0):
    """FFmpeg filter-graph fragments turning [source] into [target], keeping RGBA alpha."""
    kind=effect['type']
    p={q['key']:effect.get('params',{}).get(q['key'],q['default']) for q in EFFECTS[kind]['params']}
    num=lambda key:float(p[key])
    px=lambda value:max(0.0,float(value)*factor)
    simple=lambda chain:[f'[{source}]{chain},format=rgba[{target}]']
    def keep_alpha(chain):
        return [f'[{source}]split[{tag}a][{tag}b]',f'[{tag}a]alphaextract[{tag}al]',f'[{tag}b]format=rgb24,{chain},format=rgba[{tag}c]',f'[{tag}c][{tag}al]alphamerge[{target}]']
    if kind=='color':
        c=num('contrast')/100;b=num('brightness')*2.55
        lut=f"clip((val-128)*{c:.5f}+128+{b:.5f}\\,0\\,255)"
        s=num('saturation')/100;h=math.radians(num('hue'));cs,sn=math.cos(h),math.sin(h)
        sat=[[.213+.787*s,.715-.715*s,.072-.072*s],[.213-.213*s,.715+.285*s,.072-.072*s],[.213-.213*s,.715-.715*s,.072+.928*s]]
        hue=[[.213+cs*.787-sn*.213,.715-cs*.715-sn*.715,.072-cs*.072+sn*.928],[.213-cs*.213+sn*.143,.715+cs*.285+sn*.140,.072-cs*.072-sn*.283],[.213-cs*.213-sn*.787,.715-cs*.715+sn*.715,.072+cs*.928+sn*.072]]
        return simple(f"lutrgb=r='{lut}':g='{lut}':b='{lut}',{_mixer(_matmul(hue,sat))}")
    if kind=='mono':
        t=num('strength')/100;r,g,b=(v/255 for v in _rgb(p['color']))
        gray=[[.299*k,.587*k,.114*k] for k in (r,g,b)]
        ident=[[1,0,0],[0,1,0],[0,0,1]]
        return simple(_mixer([[ident[i][j]*(1-t)+gray[i][j]*t for j in range(3)] for i in range(3)]))
    if kind=='invert':
        chain=[]
        if num('hflip'):chain.append('hflip')
        if num('vflip'):chain.append('vflip')
        if num('negate'):chain.append('lutrgb=r=negval:g=negval:b=negval')
        return simple(','.join(chain) or 'null')
    if kind=='blur':
        sigma=px(num('size'))/2
        if sigma<.1:return simple('null')
        m=math.ceil(sigma*3)
        return simple(f'pad=iw+{2*m}:ih+{2*m}:{m}:{m}:color=black@0,gblur=sigma={sigma:.4f}')
    if kind=='mosaic':
        size=max(1,round(px(num('size'))))
        return simple(f'pixelize=w={size}:h={size}')
    if kind=='sharpen':
        return simple(f'unsharp=5:5:{num("strength")/20:.4f}:5:5:0')
    if kind=='noise':
        return [f'[{source}]split[{tag}a][{tag}b]',f'[{tag}a]alphaextract[{tag}al]',f'[{tag}b]noise=c0s={num("strength"):.0f}:c1s={num("strength"):.0f}:c2s={num("strength"):.0f}:allf=t,format=rgba[{tag}c]',f'[{tag}c][{tag}al]alphamerge[{target}]']
    if kind=='crop':
        t,b,l,r=(num(k)/100 for k in ('top','bottom','left','right'))
        if t+b>=1 or l+r>=1:return [f'[{source}]lutrgb=a=0[{target}]']
        return simple(f"crop=w='max(1\\,trunc(iw*{1-l-r:.5f}))':h='max(1\\,trunc(ih*{1-t-b:.5f}))':x='trunc(iw*{l:.5f})':y='trunc(ih*{t:.5f})',"
                      f"pad=w='trunc(iw/{1-l-r:.5f})':h='trunc(ih/{1-t-b:.5f})':x='trunc(iw/{1-l-r:.5f}*{l:.5f})':y='trunc(ih/{1-t-b:.5f}*{t:.5f})':color=black@0")
    if kind=='shift':
        d=round(px(num('distance')))
        return simple(f'rgbashift=rh={-d}:bh={d}')
    if kind=='vignette':
        return keep_alpha(f'vignette=angle={num("strength")/100*math.pi/2:.5f}')
    if kind=='edge':
        return keep_alpha('edgedetect=mode=colormix:high=0.2:low=0.05')
    if kind=='afterimage':
        return simple(f'lagfun=decay={num("decay")/100:.3f}')
    if kind=='chromakey':
        return [f'[{source}]split[{tag}a][{tag}b]',f'[{tag}a]alphaextract[{tag}a1]',
                f'[{tag}b]colorkey={_hex(p["color"])}:{max(.01,num("similarity")/100):.3f}:{num("blend")/100:.3f},format=rgba,split[{tag}c][{tag}d]',
                f'[{tag}d]alphaextract[{tag}a2]',f'[{tag}a1][{tag}a2]blend=all_mode=multiply[{tag}am]',f'[{tag}c][{tag}am]alphamerge[{target}]']
    r,g,b=_rgb(p.get('color','#000000'))
    if kind=='border':
        size=max(1,px(num('size')));soft=px(num('blur'))
        m=math.ceil(size+soft*3+2);sigma=max(.5,size/2)
        thresh=max(1,int(255*math.exp(-2)))
        chain=[f'[{source}]pad=iw+{2*m}:ih+{2*m}:{m}:{m}:color=black@0,format=rgba,split[{tag}a][{tag}b]',
               f"[{tag}b]lutrgb=r={r}:g={g}:b={b},gblur=sigma={sigma:.3f},lutrgb=a='if(gte(val\\,{thresh})\\,255\\,0)'"+(f',gblur=sigma={soft/2:.3f}' if soft>0 else '')+f'[{tag}c]',
               f'[{tag}c][{tag}a]overlay=0:0:format=auto,format=rgba[{target}]']
        return chain
    if kind=='shadow':
        dx,dy=round(num('x')*factor),round(num('y')*factor);soft=px(num('blur'))
        m=math.ceil(abs(dx)+abs(dy)+soft*3+2)
        return [f'[{source}]pad=iw+{2*m}:ih+{2*m}:{m}:{m}:color=black@0,format=rgba,split=3[{tag}a][{tag}b][{tag}z]',
                f"[{tag}b]lutrgb=r={r}:g={g}:b={b}:a='val*{num('opacity')/100:.4f}'"+(f',gblur=sigma={soft/2:.3f}' if soft>0 else '')+f'[{tag}s]',
                f'[{tag}z]lutrgb=a=0[{tag}blank]',f'[{tag}blank][{tag}s]overlay={dx}:{dy}:format=auto[{tag}sh]',
                f'[{tag}sh][{tag}a]overlay=0:0:format=auto,format=rgba[{target}]']
    if kind=='glow':
        sigma=max(.5,px(num('size'))/2);m=math.ceil(sigma*3)
        return [f'[{source}]pad=iw+{2*m}:ih+{2*m}:{m}:{m}:color=black@0,format=rgba,split[{tag}a][{tag}b]',
                f'[{tag}b]gblur=sigma={sigma:.3f},format=gbrap[{tag}g]',f'[{tag}a]format=gbrap[{tag}o]',
                f'[{tag}o][{tag}g]blend=all_mode=screen:all_opacity={num("strength")/100:.3f},format=rgba[{target}]']
    raise ValueError('フィルタ効果の種類が無効です')


def effects_graph(effects, source, target, tag, factor=1.0):
    active=[e for e in effects if e.get('enabled',True)]
    if not active:
        return [f'[{source}]null[{target}]']
    graph=[];current=source
    for k,effect in enumerate(active):
        out=target if k==len(active)-1 else f'{tag}o{k}'
        graph+=effect_graph(effect,current,out,f'{tag}e{k}',factor)
        current=out
    return graph


def effect_preview_image(clip, source, directory, factor):
    """Apply a clip's effects to a still image for the editing canvas."""
    active=[e for e in clip.effects if e.get('enabled',True)]
    if not active:
        return source
    stat=Path(source).stat()
    key=hashlib.sha256(json.dumps([str(source),stat.st_mtime_ns,active,round(factor,3)],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    path=Path(directory)/f'fx-{key}.png'
    if not path.is_file():
        graph=';'.join(['[0:v]format=rgba[fx0]']+effects_graph(active,'fx0','fxout','p',factor))
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(source),'-filter_complex',graph,'-map','[fxout]','-frames:v','1','-pix_fmt','rgba',str(path)],check=True,timeout=30)
    return str(path)


def speed_chain(speed):
    """atempo stages for any speed between 0.1x and 10x."""
    stages=[];s=float(speed)
    while s>2.0:stages.append('atempo=2.0');s/=2.0
    while s<0.5:stages.append('atempo=0.5');s/=0.5
    if abs(s-1)>1e-6:stages.append(f'atempo={s:.6f}')
    return stages


def fade_factor(clip, position):
    t=position-clip.offset;f=1.0
    if clip.fade_in>0:f*=max(0.0,min(1.0,t/clip.fade_in))
    if clip.fade_out>0:f*=max(0.0,min(1.0,(clip.duration-t)/clip.fade_out))
    return f


def measure_loudness(clip):
    """Integrated loudness (LUFS) of the part of the clip's source that is used, or None if silent."""
    result = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-nostdin', '-ss', f'{clip.start:.3f}', '-t', f'{clip.duration*clip.speed:.3f}', '-i', clip.path,
                             '-map', '0:a:0', '-vn', '-af', 'ebur128', '-f', 'null', '-'], capture_output=True, text=True, timeout=300)
    summary = result.stderr.rsplit('Integrated loudness:', 1)
    if len(summary) != 2:
        return None
    for line in summary[1].splitlines():
        line = line.strip()
        if line.startswith('I:'):
            try:
                value = float(line.split()[1])
            except (IndexError, ValueError):
                return None
            return value if value > -70 else None
    return None


def total_duration(clips):
    return max((c.offset+c.duration for c in clips if c.kind!='filter'), default=0.0)


def active_visuals(clips, position, hidden_layers=()):
    return sorted((c for c in clips if c.kind not in ('audio','filter') and c.layer not in hidden_layers and c.offset <= position < c.offset+c.duration), key=lambda c: (c.layer, clips.index(c)))


@lru_cache(maxsize=1)
def font_families():
    try:
        result=subprocess.run(['fc-list','--format','%{family}\n'],capture_output=True,text=True,timeout=3,check=True)
        names={name.strip() for line in result.stdout.splitlines() for name in line.split(',') if name.strip()}
        return ('既定',*sorted(names,key=str.casefold))
    except (OSError,subprocess.SubprocessError):
        return ('既定',)


@lru_cache(maxsize=1)
def _font_files_by_family():
    """Map fontconfig family names to the actual files installed for them."""
    try:
        result=subprocess.run(['fc-list','--format','%{family}\t%{style}\t%{file}\n'],capture_output=True,text=True,timeout=3,check=True)
        candidates={}
        for line in result.stdout.splitlines():
            parts=line.split('\t',2)
            if len(parts)!=3:
                continue
            names,style,path=parts
            if not Path(path).is_file():
                continue
            style=style.casefold()
            priority=0 if style in ('regular','book','normal') else 1
            for name in names.split(','):
                key=' '.join(name.strip().casefold().split())
                if key and (key not in candidates or priority<candidates[key][0]):
                    candidates[key]=(priority,path)
        return {name:path for name,(_,path) in candidates.items()}
    except (OSError,subprocess.SubprocessError):
        return {}


@lru_cache(maxsize=128)
def font_path_for_family(family):
    if family=='既定' or not family:return FONT
    key=' '.join(family.strip().casefold().split())
    return _font_files_by_family().get(key,FONT)


def text_image(clip, directory):
    """Same RGBA text raster for the editor, playback and export."""
    font_path=font_path_for_family(clip.font_family)
    outline_width=max(0,int(clip.outline_width))
    shadow_size=max(0,int(clip.shadow_size))
    shadow_blur=max(0,int(clip.shadow_blur))
    spacing=int(clip.line_spacing)
    align=clip.text_align if clip.text_align in ('left','center','right') else 'left'
    key = hashlib.sha256(json.dumps([clip.text, clip.font_size, clip.font_family, font_path, clip.color, clip.outline_color, outline_width, clip.shadow_color, shadow_size, shadow_blur, spacing, align], ensure_ascii=False).encode()).hexdigest()
    path = Path(directory)/f'text-{key}.png'
    if not path.is_file():
        font = ImageFont.truetype(font_path, clip.font_size)
        measure = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
        left, top, right, bottom = measure.multiline_textbbox((0, 0), clip.text or ' ', font=font, spacing=spacing, align=align, stroke_width=outline_width)
        left, top, right, bottom = math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom)
        margin=8+outline_width+shadow_size+shadow_blur*2
        image = Image.new('RGBA', (min(8192, max(8, right-left+margin*2+shadow_size)), min(4096, max(8, bottom-top+margin*2+shadow_size))), (0, 0, 0, 0))
        position=(margin-left,margin-top)
        if shadow_size or shadow_blur:
            shadow=Image.new('RGBA',image.size,(0,0,0,0))
            shadow_pos=(position[0]+max(1,shadow_size//2),position[1]+max(1,shadow_size//2))
            ImageDraw.Draw(shadow).multiline_text(shadow_pos,clip.text or ' ',font=font,spacing=spacing,align=align,fill=clip.shadow_color,stroke_width=shadow_size,stroke_fill=clip.shadow_color)
            if shadow_blur:
                from PIL import ImageFilter
                shadow=shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
            image.alpha_composite(shadow)
        ImageDraw.Draw(image).multiline_text(position, clip.text or ' ', font=font, fill=clip.color, spacing=spacing, align=align, stroke_width=outline_width, stroke_fill=clip.outline_color)
        image.save(path)
    return str(path)


def shape_image(clip, directory):
    """Anti-aliased RGBA raster for 図形 objects."""
    w,h=max(1,int(clip.width)),max(1,int(clip.height))
    key=hashlib.sha256(json.dumps(['shape',clip.shape_type,w,h,clip.color,int(clip.line_width)]).encode()).hexdigest()
    path=Path(directory)/f'shape-{key}.png'
    if not path.is_file():
        ss=2 if max(w,h)<=4096 else 1
        W,H=w*ss,h*ss;lw=int(clip.line_width)*ss
        image=Image.new('RGBA',(W,H),(0,0,0,0))
        draw=ImageDraw.Draw(image)
        fill=None if lw else clip.color
        outline=clip.color if lw else None
        box=(lw//2,lw//2,W-1-lw//2,H-1-lw//2) if lw else (0,0,W-1,H-1)
        kind=clip.shape_type
        if kind=='rect':draw.rectangle(box,fill=fill,outline=outline,width=lw or 1)
        elif kind=='rounded':draw.rounded_rectangle(box,radius=min(W,H)*.2,fill=fill,outline=outline,width=lw or 1)
        elif kind=='circle':draw.ellipse(box,fill=fill,outline=outline,width=lw or 1)
        else:
            cx,cy=(box[0]+box[2])/2,(box[1]+box[3])/2;rx,ry=(box[2]-box[0])/2,(box[3]-box[1])/2
            if kind=='triangle':points=[(cx,box[1]),(box[2],box[3]),(box[0],box[3])]
            elif kind=='hexagon':points=[(cx+rx*math.cos(math.radians(a)),cy+ry*math.sin(math.radians(a))) for a in range(0,360,60)]
            else:points=[(cx+(rx if i%2==0 else rx*.4)*math.sin(math.pi*i/5),cy-(ry if i%2==0 else ry*.4)*math.cos(math.pi*i/5)) for i in range(10)]
            if lw:draw.line(points+[points[0],points[1]],fill=outline,width=lw,joint='curve')
            else:draw.polygon(points,fill=fill)
        if ss>1:image=image.resize((w,h),Image.LANCZOS)
        image.save(path)
    return str(path)


def static_image(clip, directory):
    if clip.kind=='text':return text_image(clip,directory)
    if clip.kind=='shape':return shape_image(clip,directory)
    return clip.path


def visual_size(clip, directory=None):
    if clip.kind == 'text':
        if directory is None:
            with tempfile.TemporaryDirectory() as directory:
                with Image.open(text_image(clip, directory)) as image:
                    return image.size
        with Image.open(text_image(clip, directory)) as image:
            return image.size
    return clip.width, clip.height


def placed_size(clip, directory=None, base=None, native=None):
    """Size in editing units. native=output height in px places media at 1 source px = 1 output px (AviUtl style)."""
    w, h = visual_size(clip, directory)
    bw, bh = base or (BASE_W, BASE_H)
    if clip.kind in ('text','shape'):fit = 1
    elif native:fit = bh/native
    else:fit = min(bw/w, bh/h)
    return max(2, w*fit*clip.scale), max(2, h*fit*clip.scale)


def validate(clips):
    ids = set()
    for c in clips:
        if c.kind not in ('video', 'image', 'audio', 'text','filter','shape'):
            raise ValueError('素材の種類が無効です')
        if c.kind not in ('text','filter','shape') and not Path(c.path).is_file():
            raise ValueError(f'素材が見つかりません: {c.path}')
        if c.kind=='filter' and c.filter_type not in FILTER_NAMES:
            raise ValueError('フィルタの種類が無効です')
        if c.kind=='filter' and not 1<=c.filter_strength<=100:
            raise ValueError('フィルタの強さは1〜100で指定してください')
        for n in (c.source_duration, c.start, c.duration, c.offset, c.volume, c.fade_in, c.fade_out, c.x, c.y, c.scale, c.opacity, c.rotation, c.width, c.height, c.font_size, c.filter_strength, c.speed, c.pan, c.line_width, c.line_spacing):
            if not isinstance(n, (float, int)) or not math.isfinite(n):
                raise ValueError('数値が無効です')
        if not .1 <= c.speed <= 10 or not -1 <= c.pan <= 1:
            raise ValueError('再生速度は10〜1000%、左右は-100〜100で指定してください')
        if c.kind not in ('video','audio') and c.speed != 1:
            raise ValueError('再生速度は映像・音声だけに指定できます')
        if c.blend not in BLEND_MODES:
            raise ValueError('合成モードが無効です')
        if not isinstance(c.effects,list) or len(c.effects)>32:
            raise ValueError('フィルタ効果の形式が無効です')
        for effect in c.effects:
            if not isinstance(effect,dict) or effect.get('type') not in EFFECTS or not isinstance(effect.get('params',{}),dict):
                raise ValueError('フィルタ効果の種類が無効です')
            for spec in EFFECTS[effect['type']]['params']:
                value=effect.get('params',{}).get(spec['key'],spec['default'])
                if spec['type']=='color':
                    from PIL import ImageColor
                    ImageColor.getrgb(str(value))
                elif not isinstance(value,(int,float)) or not math.isfinite(value) or not spec['min']<=value<=spec['max']:
                    raise ValueError(f'{EFFECTS[effect["type"]]["name"]}の{spec["label"]}が範囲外です')
        if c.kind=='shape':
            from PIL import ImageColor
            ImageColor.getrgb(c.color)
            if c.shape_type not in SHAPES or not 1<=c.width<=4096 or not 1<=c.height<=4096 or not 0<=c.line_width<=500:
                raise ValueError('図形の設定が無効です')
        if c.duration < .04 or c.start < 0 or c.offset < 0 or not 0 <= c.volume <= 30 or not 0 <= c.fade_in <= c.duration or not 0 <= c.fade_out <= c.duration:
            raise ValueError('クリップの範囲または音量が無効です')
        if c.start+c.duration*c.speed > c.source_duration+.05:
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
            ImageColor.getrgb(c.outline_color)
            ImageColor.getrgb(c.shadow_color)
            if not isinstance(c.font_family,str) or len(c.font_family)>128 or not 0<=c.outline_width<=40 or not 0<=c.shadow_size<=40 or not 0<=c.shadow_blur<=40 or c.text_align not in ('left','center','right') or not -50<=c.line_spacing<=300:
                raise ValueError('フォントまたは文字装飾の設定が無効です')
        if c.uid in ids:
            raise ValueError('クリップIDが重複しています')
        ids.add(c.uid)


def save_project(path, clips, settings):
    validate(clips)
    base = Path(path).resolve().parent
    records = []
    for clip in clips:
        record = asdict(clip)
        record['path'] = os.path.relpath(clip.path, base) if clip.kind not in ('text','filter','shape') else ''
        # The absolute path is a fallback for when the project file is moved away from its media.
        record['abs_path'] = str(Path(clip.path).resolve()) if clip.kind not in ('text','filter','shape') else ''
        records.append(record)
    data = {'application': 'Linux Movie Builder', 'version': 4, 'settings': settings, 'clips': records}
    fd, tmp = tempfile.mkstemp(dir=base, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class MissingMedia(ValueError):
    """Raised when media referenced by a project cannot be found anywhere we look."""
    def __init__(self, names):
        self.names = list(dict.fromkeys(names))
        super().__init__('素材が見つかりません: '+', '.join(self.names))


def _find_by_name(name, folders, depth=4):
    for folder in folders:
        folder = Path(folder)
        if (folder/name).is_file():
            return str((folder/name).resolve())
        base_depth = len(folder.parts)
        try:
            for root, dirs, files in os.walk(folder):
                if len(Path(root).parts)-base_depth >= depth:
                    dirs[:] = []
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                if name in files:
                    return str((Path(root)/name).resolve())
        except OSError:
            continue
    return None


def locate_media(record, project_dir, search_dirs=()):
    """Relative path, then stored absolute path, then the same file name near the project or in search_dirs."""
    relative = (Path(project_dir)/record['path']).resolve() if record.get('path') else None
    if relative and relative.is_file():
        return str(relative), False
    absolute = record.get('abs_path')
    if absolute and Path(absolute).is_file():
        return str(Path(absolute).resolve()), True
    names = [n for n in (Path(record.get('abs_path') or '').name, Path(record.get('path') or '').name) if n]
    project_dir = Path(project_dir)
    # Nearby places first: user-chosen folders, the project folder, then folders around where the media used to be.
    nearby = [*search_dirs, project_dir]
    for original in (Path(record['abs_path']).parent if record.get('abs_path') else None, relative.parent if relative else None):
        if original is None:
            continue
        for ancestor in [original, *list(original.parents)[:1]]:
            if len(ancestor.parts) > 3 and ancestor.is_dir():  # never sweep /, /media or a whole home folder
                nearby.append(ancestor)
    if len(project_dir.parts) > 3:
        nearby.append(project_dir.parent)
    nearby = list(dict.fromkeys(str(d) for d in nearby))
    for name in dict.fromkeys(names):
        found = _find_by_name(name, nearby[:len(search_dirs)+1], depth=4) or _find_by_name(name, nearby[len(search_dirs)+1:], depth=3)
        if found:
            return found, True
    return (str(relative) if relative else ''), None


def load_project(path, search_dirs=(), relinked=None):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('application') != 'Linux Movie Builder' or data.get('version') not in (1, 2, 3, 4):
        raise ValueError('対応するプロジェクトファイルではありません')
    clips, elapsed = [], 0.0
    missing = []
    for record in data['clips']:
        if record['kind'] not in ('text','filter','shape'):
            located, moved = locate_media(record, Path(path).resolve().parent, search_dirs)
            if moved is None:
                missing.append(Path(record.get('abs_path') or record['path']).name)
            elif moved and relinked is not None:
                relinked.append(located)
            record['path'] = located
    if missing:
        raise MissingMedia(missing)
    for record in data['clips']:
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
        known={f for f in Clip.__dataclass_fields__}
        clips.append(Clip(**{k:v for k,v in record.items() if k in known}))
    validate(clips)
    settings = data.get('settings', {'resolution':'1920x1080','fps':60})
    try:
        width,height=map(int,str(settings.get('resolution','')).split('x'))
        fps=int(settings.get('fps'))
    except (TypeError,ValueError):
        raise ValueError('出力設定が無効です')
    if not 2<=width<=16384 or not 2<=height<=16384 or width%2 or height%2 or not 1<=fps<=240:
        raise ValueError('出力設定が無効です')
    layers=lambda key:sorted({int(v) for v in settings.get(key,[]) if isinstance(v,int) and 0<=v<=15})
    # Projects saved before v1.10 fitted media inside the frame; keep them looking the same.
    settings={'resolution':f'{width}x{height}','fps':fps,'hidden_layers':layers('hidden_layers'),'locked_layers':layers('locked_layers'),'media_scale':settings.get('media_scale','fit') if settings.get('media_scale') in ('fit','native') else 'fit'}
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


def composition(clips, resolution, fps, directory, position=0, realtime=False, video=True, audio=True, hardware_decode=False, end=None, hidden_layers=(), native_media=False):
    """One composition graph used by export and streaming, seeking inputs directly."""
    validate(clips)
    clips=[c for c in clips if c.layer not in set(hidden_layers)]
    if not clips:
        raise ValueError('素材を追加してください')
    width, height = map(int, resolution.split('x'))
    if min(width, height) < 2 or max(width,height)>16384 or width % 2 or height % 2 or not 1<=int(fps)<=240:
        raise ValueError('出力設定が無効です')
    finish = total_duration(clips) if end is None else min(float(end), total_duration(clips))
    duration = finish-position
    if duration <= 0:
        raise ValueError('終端です。再生位置を前に戻してください')
    base_w, base_h = base_for(resolution)
    factor = height/base_h
    # native_media: True = relative to this render's height, or an int = the project's output height
    # (live preview renders smaller frames, but must place media exactly like the export).
    native_h = native_media if isinstance(native_media, int) and not isinstance(native_media, bool) else (height if native_media else None)
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
    current_video=f'base{visual_index}'
    filter_no=0
    sep=r'\,'
    clips_by_layer={}
    for clip in sorted((c for c in clips if c.kind!='filter'),key=lambda c:(c.layer,clips.index(c))):
        clips_by_layer.setdefault(clip.layer,[]).append(clip)
    filters_by_layer={}
    for clip in sorted((c for c in clips if c.kind=='filter'),key=lambda c:(c.layer,c.offset,clips.index(c))):
        filters_by_layer.setdefault(clip.layer,[]).append(clip)
    for layer in sorted(set(clips_by_layer)|set(filters_by_layer)):
        for clip in filters_by_layer.get(layer,[]) if video else []:
            start=max(0.0,clip.offset-position)
            stop=min(duration,clip.offset+clip.duration-position)
            if stop<=start:continue
            enable=f"between(t\\,{start:.6f}\\,{stop:.6f})"
            label=f'layerfilter{filter_no}'
            if clip.filter_type=='effect':
                if not any(e.get('enabled',True) for e in clip.effects):continue
                tag=f'ff{filter_no}'
                filters.append(f'[{current_video}]split[{tag}keep][{tag}src]')
                filters+=effects_graph(clip.effects,f'{tag}src',f'{tag}fx',tag,factor)
                filters.append(f'[{tag}fx]crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,format=rgba[{tag}crop]')
                filters.append(f"[{tag}keep][{tag}crop]overlay=0:0:enable='{enable}'[{label}]")
            else:
                if clip.filter_type=='blur':chain=f"boxblur={max(1,round(clip.filter_strength*factor))}:enable='{enable}'"
                elif clip.filter_type=='mosaic':chain=f"pixelize=width={max(1,round(clip.filter_strength*factor))}:height={max(1,round(clip.filter_strength*factor))}:enable='{enable}'"
                elif clip.filter_type=='fade':
                    half=min(clip.filter_strength/100,(stop-start)/2)
                    chain=f"fade=t=in:st={start:.6f}:d={half:.6f},fade=t=out:st={stop-half:.6f}:d={half:.6f}"
                else:
                    half=min(clip.filter_strength/100,(stop-start)/2)
                    chain=f"fade=t=out:st={start:.6f}:d={half:.6f},fade=t=in:st={stop-half:.6f}:d={half:.6f}"
                filters.append(f'[{current_video}]{chain}[{label}]')
            current_video=label
            filter_no+=1
        for c in clips_by_layer.get(layer,[]):
            skip, remaining, delay = _remaining(c, position)
            remaining=min(remaining,duration-delay)
            use_video = video and c.kind != 'audio'
            use_audio = audio and c.has_audio
            if remaining <= 0 or not (use_video or use_audio):
                continue
            i = next_input
            next_input += 1
            path = static_image(c, directory)
            speed = float(c.speed)
            if c.kind in ('image', 'text', 'shape'):
                args += ['-loop', '1', '-framerate', str(fps)]
                source_len = remaining
            else:
                if hardware_decode and realtime and use_video and c.kind == 'video':
                    args += ['-hwaccel', 'auto']
                args += ['-ss', f'{c.start+skip*speed:.6f}']
                source_len = remaining*speed
            args += ['-threads', '2', '-t', f'{source_len+.05:.6f}', '-i', path]
            when = f't+{position:.9g}'
            if use_video:
                w, h = placed_size(c, directory, (base_w, base_h), native_h)
                w, h = max(2, round(w/c.scale*width/base_w)), max(2, round(h/c.scale*height/base_h))
                sx = _expr(c, 'scale', c.scale, time=when)
                xx = _expr(c, 'x', c.x, time=when)
                yy = _expr(c, 'y', c.y, time=when)
                angle = _expr(c, 'rotation', c.rotation, time=when)
                # geq names the frame time T, not t.
                when_geq = f'T+{position:.9g}'
                alpha = _expr(c, 'opacity', c.opacity, time=when_geq)
                if c.fade_in>0:
                    alpha=f"({alpha})*min(max(({when_geq}-{c.offset:.9g})/{c.fade_in:.9g}{sep}0){sep}1)"
                if c.fade_out>0:
                    alpha=f"({alpha})*min(max(({c.offset+c.duration:.9g}-({when_geq}))/{c.fade_out:.9g}{sep}0){sep}1)"
                chain=[f'trim=duration={source_len:.6f}',f'setpts=(PTS-STARTPTS)/{speed:.6f}+{delay:.6f}/TB',f'fps={fps}',f"scale=w='max(2,trunc({w}*({sx})/2)*2)':h='max(2,trunc({h}*({sx})/2)*2)':eval=frame",'setsar=1','format=rgba']
                filters.append(f"[{i}:v]"+','.join(chain)+f'[pre{visual_index}]')
                current=f'pre{visual_index}'
                if any(e.get('enabled',True) for e in c.effects):
                    filters+=effects_graph(c.effects,current,f'fx{visual_index}',f'c{visual_index}',factor)
                    current=f'fx{visual_index}'
                post=[]
                if c.rotation!=0 or c.animation.get('rotation',{}).get('mode','none')!='none':
                    post.append(f"rotate=a='({angle})*PI/180':ow='rotw(iw)':oh='roth(ih)':c=none")
                if c.opacity!=1 or c.animation.get('opacity',{}).get('mode','none')!='none' or c.fade_in>0 or c.fade_out>0:
                    post.append(f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({alpha})'")
                filters.append(f"[{current}]{','.join(post) or 'null'}[layer{visual_index}]")
                x = f"(({xx})*main_w/{base_w}-overlay_w/2)"
                y = f"(({yy})*main_h/{base_h}-overlay_h/2)"
                overlay=f"overlay=x='{x}':y='{y}':eof_action=pass:repeatlast=0:enable='between(t\\,{delay:.6f}\\,{delay+remaining:.6f})'"
                n=visual_index
                if c.blend=='normal':
                    filters.append(f"[{current_video}][layer{n}]{overlay}[base{n+1}]")
                else:
                    mode,identity=_BLEND_FF[c.blend]
                    filters.append(f'[{current_video}]split[bk{n}][bi{n}]')
                    filters.append(f'[bi{n}]drawbox=c={identity}@1:t=fill[bid{n}]')
                    filters.append(f'[bid{n}][layer{n}]{overlay},format=gbrp[bly{n}]')
                    filters.append(f'[bk{n}]format=gbrp[bkg{n}]')
                    filters.append(f'[bkg{n}][bly{n}]blend=all_mode={mode},format=rgba[base{n+1}]')
                current_video=f'base{n+1}'
                visual_index += 1
            if use_audio:
                millis = round(delay*1000)
                label = f'a{i}'
                gain = _expr(c, 'volume', c.volume, time=when)
                if c.fade_in>0:
                    gain=f"({gain})*min(max(({when}-{c.offset:.9g})/{c.fade_in:.9g}{sep}0){sep}1)"
                if c.fade_out>0:
                    gain=f"({gain})*min(max(({c.offset+c.duration:.9g}-({when}))/{c.fade_out:.9g}{sep}0){sep}1)"
                chain=['aresample=48000','aformat=sample_fmts=fltp:channel_layouts=stereo',f'atrim=duration={source_len:.6f}','asetpts=PTS-STARTPTS',*speed_chain(speed),f'atrim=duration={remaining:.6f}',f'asetpts=PTS-STARTPTS+{delay:.6f}/TB',f"volume=volume='{gain}':eval=frame"]
                if c.pan:
                    left,right=min(1.0,1-c.pan),min(1.0,1+c.pan)
                    chain.append(f'pan=stereo|c0={left:.4f}*c0|c1={right:.4f}*c1')
                chain.append(f'adelay={millis}|{millis}')
                filters.append(f"[{i}:a]"+','.join(chain)+f'[{label}]')
                audio_tracks.append(f'[{label}]')
    if video:
        filters.append(f'[{current_video}]trim=duration={duration:.6f},format=rgb24[video]')
    if audio:
        filters.append(''.join(audio_tracks)+f'amix=inputs={len(audio_tracks)}:duration=longest:normalize=0,alimiter=limit=0.95:level=0:latency=1,atrim=duration={duration:.6f}[audio]')
    args += ['-filter_complex', ';'.join(filters)]
    return args, duration


CODECS={
    'h264':{'name':'MP4 (H.264 / AAC)','ext':'.mp4'},
    'h265':{'name':'MP4 (H.265 / AAC)','ext':'.mp4'},
    'vp9':{'name':'WebM (VP9 / Opus)','ext':'.webm'},
    'wav':{'name':'WAV（音声のみ）','ext':'.wav'},
    'mp3':{'name':'MP3（音声のみ）','ext':'.mp3'},
}


def render_command(clips, output, resolution='1920x1080', fps=60, preview=False, asset_dir=None, start=0.0, end=None, codec='h264', crf=None, audio_bitrate=192, hidden_layers=(), native_media=False):
    directory = asset_dir or str(Path(output).parent)
    audio_only = codec in ('wav','mp3')
    args, duration = composition(clips, resolution, fps, directory, start, video=not audio_only, end=end, hidden_layers=hidden_layers, native_media=native_media)
    args += ['-y']
    if not audio_only:
        args += ['-map', '[video]']
    args += ['-map', '[audio]', '-t', f'{duration:.6f}']
    quality = crf if crf is not None else (26 if preview else 20)
    if codec=='h264':
        args += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'ultrafast' if preview else 'veryfast', '-crf', str(quality), '-c:a', 'aac', '-b:a', f'{audio_bitrate}k', '-movflags', '+faststart']
    elif codec=='h265':
        args += ['-c:v', 'libx265', '-pix_fmt', 'yuv420p', '-preset', 'fast', '-crf', str(quality+4), '-tag:v', 'hvc1', '-c:a', 'aac', '-b:a', f'{audio_bitrate}k', '-movflags', '+faststart']
    elif codec=='vp9':
        args += ['-c:v', 'libvpx-vp9', '-pix_fmt', 'yuv420p', '-b:v', '0', '-crf', str(quality+11), '-row-mt', '1', '-deadline', 'good', '-cpu-used', '4', '-c:a', 'libopus', '-b:a', f'{audio_bitrate}k', '-f', 'webm']
    elif codec=='wav':
        args += ['-c:a', 'pcm_s16le', '-f', 'wav']
    elif codec=='mp3':
        args += ['-c:a', 'libmp3lame', '-b:a', f'{audio_bitrate}k', '-f', 'mp3']
    else:
        raise ValueError('出力形式が無効です')
    if codec=='h264' or codec=='h265':
        args += ['-f', 'mp4']
    args += ['-progress', 'pipe:1', '-nostats', str(output)]
    return args


def frame_command(clips, position, output, resolution='1920x1080', fps=60, asset_dir=None, hidden_layers=(), native_media=False):
    """Write the composed frame at `position` as a PNG image."""
    directory = asset_dir or str(Path(output).parent)
    end = min(total_duration(clips), position+2/max(1,int(fps)))
    args, _ = composition(clips, resolution, fps, directory, position, audio=False, end=end, hidden_layers=hidden_layers, native_media=native_media)
    return args+['-y', '-map', '[video]', '-frames:v', '1', '-update', '1', '-f', 'image2', str(output)]


def video_stream_command(clips, position, directory, hardware_decode=False, hidden_layers=(), resolution='1920x1080', native_media=False):
    pw, ph = preview_size(resolution)
    if native_media is True:
        native_media = int(resolution.split('x')[1])
    args, duration = composition(clips, f'{pw}x{ph}', 30, directory, position, True, video=True, audio=False, hardware_decode=hardware_decode, hidden_layers=hidden_layers, native_media=native_media)
    return args+['-map', '[video]', '-t', f'{duration:.6f}', '-pix_fmt', 'rgb24', '-threads', '1', '-f', 'rawvideo', 'pipe:1']


def audio_stream_command(clips, position, directory, hidden_layers=(), resolution='1920x1080'):
    args, duration = composition(clips, '800x450', 30, directory, position, True, video=False, audio=True, hidden_layers=hidden_layers)
    return args+['-map', '[audio]', '-t', f'{duration:.6f}', '-ac', '2', '-ar', '48000', '-f', 's16le', 'pipe:1']
