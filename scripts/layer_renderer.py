#!/usr/bin/env python3
"""Deterministic moving layers, real video inserts and per-frame person occlusion.

Run on locked CFR base, before ordinary captions. Pillow + FFmpeg; no model upload.
"""
import argparse, json, math, os, subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageChops
from effects_renderer import tile, number
from subtitle_renderer import choose_font
from video_pipeline import probe, streams, validate, verify, write, encoder_args
from person_mask import sha

def at(keys, t):
    if t <= keys[0]['t']: return keys[0]
    if t >= keys[-1]['t']: return keys[-1]
    a,b=next((a,b) for a,b in zip(keys,keys[1:]) if a['t'] <= t < b['t'])
    u=(t-a['t'])/(b['t']-a['t'])
    if b.get('ease','smooth')=='smooth': u=u*u*(3-2*u)
    elif b.get('ease')!='linear': raise ValueError('ease must be linear or smooth')
    out={k:a[k]+(b[k]-a[k])*u for k in ['cx','cy','scale','rotation','opacity']}
    if 'control' in b:
        # A quadratic Bezier path, endpoints and control point in canvas fractions.
        out['cx']=(1-u)**2*a['cx']+2*(1-u)*u*b['control'][0]+u*u*b['cx']
        out['cy']=(1-u)**2*a['cy']+2*(1-u)*u*b['control'][1]+u*u*b['cy']
    return out

def keys_for(e):
    box=e['box']; default=dict(cx=box[0]+box[2]/2,cy=box[1]+box[3]/2,scale=1.,rotation=0.,opacity=1.)
    keys=[dict(default,**k) for k in e.get('keyframes',[{'t':0}])]
    last=-1
    for k in keys:
        number(k['t'],0,e['end']-e['start'],'keyframe t')
        if k['t']<=last: raise ValueError('keyframes must be strictly ordered')
        last=k['t']
        for field,lo,hi in [('cx',-2,3),('cy',-2,3),('scale',.01,3),('rotation',-360,360),('opacity',0,1)]: number(k[field],lo,hi,field)
        if k.get('ease','smooth') not in {'smooth','linear'}: raise ValueError('unknown easing')
        if 'control' in k:
            if len(k['control'])!=2: raise ValueError('control requires x,y')
            for c in k['control']: number(c,-2,3,'Bezier control')
    return keys

def validate_layers(plan,base):
    validate(plan);v,a=streams(probe(base));fps=plan['fps'];w,h=v['width'],v['height']
    if (w,h)!=(plan['width'],plan['height']) or abs(float(v['duration'])-plan['duration'])>1/fps: raise ValueError('base differs from locked plan')
    rate=eval_rate(v['r_frame_rate'])
    if abs(rate-fps)>.001: raise ValueError('base must use plan CFR')
    ids=set(); expected=sum(c['frames'] for c in plan['clips']); base_hash=None
    for e in plan.get('layers',[]):
        if not e.get('id') or e['id'] in ids: raise ValueError('duplicate/missing layer id')
        ids.add(e['id'])
        if e['type'] not in {'text','image','video','person','solid','focus'}: raise ValueError('unknown layer type')
        number(e['start'],0,plan['duration'],'start'); number(e['end'],e['start'],plan['duration'],'end')
        if round(e['end']*fps)<=round(e['start']*fps): raise ValueError('empty interval')
        if len(e['box'])!=4: raise ValueError('box requires x,y,w,h')
        x,y,bw,bh=e['box']
        for n in e['box']: number(n,0,1,'box')
        if bw<=0 or bh<=0 or x+bw>1.000001 or y+bh>1.000001: raise ValueError('box outside canvas')
        e['_keys']=keys_for(e)
        if e['type']=='person' and 'maskBox' in e:
            mx,my,mw,mh=e['maskBox']
            for value in e['maskBox']: number(value,0,1,'maskBox')
            if mw<=0 or mh<=0 or mx+mw>1.000001 or my+mh>1.000001: raise ValueError('maskBox outside canvas')
        if e['type']=='focus':
            number(e.get('dim',.55),0,1,'dim');number(e.get('feather',8),0,100,'feather')
            if e.get('shape','ellipse') not in {'ellipse','rectangle'}: raise ValueError('unknown focus shape')
        if e['type']=='text' and not e.get('text','').strip(): raise ValueError('empty text')
        if e['type'] in {'image','video','person'}:
            p=Path(e['mask'] if e['type']=='person' else e['path'])
            if not p.is_absolute() or not p.is_file() or p.resolve()==Path(base).resolve() and e['type']=='person': raise ValueError('missing/invalid layer asset')
        if e['type']=='video':
            number(e.get('offset',0),0,86400,'offset');iv,_=streams(probe(e['path']))
            if float(iv['duration'])+1/fps < e.get('offset',0)+e['end']-e['start']: raise ValueError('video layer too short; no silent freeze')
        if e['type']=='person':
            m=json.loads(p.with_suffix('.json').read_text());base_hash=base_hash or sha(base)
            if m['baseSha256']!=base_hash or m['maskSha256']!=sha(p) or (m['width'],m['height'],m['frames'],m['fps'])!=(w,h,expected,fps): raise ValueError('mask does not match this locked base')
    return w,h,fps,expected

def eval_rate(s):
    n,d=map(float,s.split('/'));return n/d

def decoder(path,w,h,fps,gray=False,offset=0,fit=False):
    vf=f'fps={fps},scale={w}:{h}'
    if fit: vf+=f':force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1'
    return subprocess.Popen(['ffmpeg','-v','error','-ss',str(offset),'-i',str(path),'-an','-vf',vf, '-f','rawvideo','-pix_fmt','gray' if gray else 'rgb24','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)

def read_frame(proc,w,h,gray=False):
    size=w*h*(1 if gray else 3);data=bytearray()
    while len(data)<size:
        block=proc.stdout.read(size-len(data))
        if not block: break
        data.extend(block)
    if len(data)!=size: raise RuntimeError('unexpected media EOF; refusing frozen/missing frames')
    return Image.frombytes('L' if gray else 'RGB',(w,h),bytes(data))

def render(plan,base,out,work,font=None):
    w,h,fps,count=validate_layers(plan,base);out=Path(out).resolve();work=Path(work).resolve()
    if out.exists() or out==Path(base).resolve(): raise FileExistsError(out)
    out.parent.mkdir(parents=True,exist_ok=True);work.mkdir(parents=True,exist_ok=True)
    font=choose_font(font) if any(e['type']=='text' for e in plan.get('layers',[])) else None
    processes=[]
    with tempfile.TemporaryDirectory(prefix='layers-',dir=work) as tmp:
        tmp=Path(tmp);baseproc=decoder(base,w,h,fps);processes.append(baseproc);assets={}
        for i,e in enumerate(plan.get('layers',[])):
            bw,bh=max(2,round(e['box'][2]*w)),max(2,round(e['box'][3]*h))
            if e['type'] in {'solid','focus'}: continue
            if e['type'] in {'text','image'}:
                png=tmp/f'{i}.png';tile(dict(e,background=e.get('background','#00000000')),w,h,font,png);assets[e['id']]=Image.open(png).convert('RGBA')
            elif e['type']=='person':
                p=decoder(e['mask'],w,h,fps,True);processes.append(p);assets[e['id']]=p
            else:
                p=decoder(e['path'],bw,bh,fps,offset=e.get('offset',0),fit=True);processes.append(p);assets[e['id']]=p
        staged=tmp/'video.mp4'
        enc=subprocess.Popen(['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{w}x{h}','-r',str(fps),'-i','pipe:0','-an']+encoder_args('libx264',intermediate=True)+['-pix_fmt','yuv420p',str(staged)],stdin=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            for n in range(count):
                original=read_frame(baseproc,w,h).convert('RGBA')
                canvas=Image.new('RGBA',(w,h),plan['canvasColor']) if plan.get('canvasColor') else original.copy()
                for e in plan.get('layers',[]):
                    active=round(e['start']*fps)<=n<round(e['end']*fps);asset=assets.get(e['id'])
                    # Masks are decoded on the base timeline even outside the layer interval.
                    mask=read_frame(asset,w,h,True) if e['type']=='person' else None
                    if not active: continue
                    k=at(e['_keys'],n/fps-e['start'])
                    if e['type']=='focus':
                        alpha=Image.new('L',(w,h),round(255*e.get('dim',.55)*k['opacity']))
                        dx,dy=e['box'][2]*w*k['scale']/2,e['box'][3]*h*k['scale']/2
                        hole=(round(k['cx']*w-dx),round(k['cy']*h-dy),round(k['cx']*w+dx),round(k['cy']*h+dy))
                        draw=ImageDraw.Draw(alpha)
                        (draw.ellipse if e.get('shape','ellipse')=='ellipse' else draw.rectangle)(hole,fill=0)
                        alpha=alpha.filter(ImageFilter.GaussianBlur(e.get('feather',8)*min(w/1080,h/1920)))
                        focus=Image.new('RGBA',(w,h),'black');focus.putalpha(alpha);canvas.alpha_composite(focus);continue
                    if e['type']=='solid': im=Image.new('RGBA',(round(e['box'][2]*w),round(e['box'][3]*h)),e['color'])
                    elif e['type']=='person':
                        if 'maskBox' in e:
                            x,y,bw,bh=e['maskBox'];roi=Image.new('L',(w,h));ImageDraw.Draw(roi).rectangle((round(x*w),round(y*h),round((x+bw)*w)-1,round((y+bh)*h)-1),fill=255);mask=ImageChops.multiply(mask,roi)
                        im=original.copy();im.putalpha(mask)
                    elif e['type']=='video': im=read_frame(asset,max(2,round(e['box'][2]*w)),max(2,round(e['box'][3]*h))).convert('RGBA')
                    else: im=asset.copy()
                    target=(max(1,round(e['box'][2]*w)),max(1,round(e['box'][3]*h)))
                    if e['type'] in {'person','video'}:
                        from PIL import ImageOps
                        im=ImageOps.contain(im,target,Image.Resampling.LANCZOS)
                    size=(max(1,round(im.width*k['scale'])),max(1,round(im.height*k['scale'])))
                    im=im.resize(size,Image.Resampling.LANCZOS)
                    if k['rotation']: im=im.rotate(k['rotation'],resample=Image.Resampling.BICUBIC,expand=True)
                    if k['opacity']<1: im.putalpha(im.getchannel('A').point(lambda x:round(x*k['opacity'])))
                    x,y=round(k['cx']*w-im.width/2),round(k['cy']*h-im.height/2)
                    canvas.alpha_composite(im,(x,y))
                enc.stdin.write(canvas.convert('RGB').tobytes())
            enc.stdin.close();err=enc.stderr.read();code=enc.wait()
            if code: raise RuntimeError(err.decode())
            mux=tmp/'mux.mp4'
            subprocess.run(['ffmpeg','-v','error','-i',str(staged),'-i',str(base),'-map','0:v:0','-map','1:a:0?','-c','copy','-movflags','+faststart',str(mux)],check=True)
            checks=verify(mux,plan);checks['file']=str(out)
            import shutil
            with tempfile.NamedTemporaryFile(dir=out.parent,suffix='.mp4',delete=False) as f: target=Path(f.name);shutil.copyfileobj(mux.open('rb'),f)
            if out.exists(): target.unlink();raise FileExistsError(out)
            os.replace(target,out)
        finally:
            if enc.poll() is None: enc.kill();enc.wait()
            for p in processes:
                p.stdout.close()
                if p.poll() is None: p.terminate()
                p.wait()
    report=dict(output=str(out),implemented=[e['id'] for e in plan.get('layers',[])],technicalChecks=checks,visualReview='required',audio='base packets copied; no mixing',matting='per-frame mask; hair/hands/occlusion must be reviewed')
    write(work/'layers-report.json',report);return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('plan');p.add_argument('--base',required=True);p.add_argument('--out',required=True);p.add_argument('--work',required=True);p.add_argument('--font');a=p.parse_args()
    print(json.dumps(render(json.loads(Path(a.plan).read_text()),a.base,a.out,a.work,a.font),ensure_ascii=False,indent=2))
