#!/usr/bin/env python3
"""Offline macOS Vision person masks, frame-for-frame on a locked CFR base."""
import argparse, hashlib, json, subprocess, tempfile
from pathlib import Path
from video_pipeline import probe, streams

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def create(base, output):
    base=Path(base).resolve(); output=Path(output).resolve()
    if output.exists() or output.with_suffix('.json').exists(): raise FileExistsError(output)
    v,_=streams(probe(base)); n,d=map(float,v['r_frame_rate'].split('/')); fps=n/d
    if abs(fps-round(fps))>1e-6: raise ValueError('Use locked integer CFR base')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='person-',dir=output.parent) as tmp:
        root=Path(tmp); frames=root/'frames'; masks=root/'masks'; frames.mkdir(); masks.mkdir()
        subprocess.run(['ffmpeg','-v','error','-i',str(base),'-map','0:v:0','-fps_mode','passthrough',str(frames/'%08d.png')],check=True)
        subprocess.run(['swift',str(Path(__file__).with_suffix('.swift')),str(frames),str(masks)],check=True)
        count=len(list(masks.glob('*.png')))
        if count != len(list(frames.glob('*.png'))): raise ValueError('mask count differs from source')
        staged=root/'mask.mkv'
        subprocess.run(['ffmpeg','-v','error','-framerate',str(round(fps)),'-i',str(masks/'%08d.png'),'-vf','format=gray','-c:v','ffv1',str(staged)],check=True)
        import shutil
        shutil.copyfile(staged,output)
    manifest=dict(base=str(base),baseSha256=sha(base),maskSha256=sha(output),frames=count,width=v['width'],height=v['height'],fps=fps,method='macOS Vision accurate; per-frame masks; manual edge review required')
    output.with_suffix('.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(manifest,ensure_ascii=False))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('base');p.add_argument('--out',required=True);a=p.parse_args();create(a.base,a.out)
