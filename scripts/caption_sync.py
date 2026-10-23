#!/usr/bin/env python3
"""Bind captions to words timed on locked final voice audio; never invent word times.

This checks consistency with evidence, not the acoustic accuracy of an aligner.
"""
import argparse
import hashlib
import json
import math
import unicodedata
import wave
from pathlib import Path

from video_pipeline import digest, read, validate, write


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold()
                   if unicodedata.category(c)[0] in ('L', 'N') or c in '+-−±%‰°¥$€£><≤≥=×÷')


def timeline_id(plan):
    keys=('sourceStart', 'sourceEnd', 'outputStart', 'duration', 'frames')
    data={'source':str(Path(plan['source']).resolve()), 'fps':plan['fps'],
          'duration':plan['duration'],
          'clips':[{k:c[k] for k in keys} for c in plan['clips']]}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def check_evidence(evidence, plan):
    validate(plan)
    if evidence.get('timeline_sha256') != timeline_id(plan):
        raise ValueError('timing evidence belongs to a different edit/fps timeline; realign final voice')
    audio=Path(evidence['audio_file'])
    if not audio.is_file() or digest(audio) != evidence['audio_sha256']:
        raise ValueError('final voice audio missing or changed; realign captions')
    with wave.open(str(audio),'rb') as wav:
        audio_duration=wav.getnframes()/wav.getframerate()
    if abs(audio_duration-plan['duration'])>1/plan['fps']+.001:
        raise ValueError('reference PCM WAV duration differs from locked final voice timeline')
    words=evidence.get('words', [])
    if not words: raise ValueError('no independently timed words in evidence')
    previous_start=-1
    ids=set()
    for w in words:
        if not isinstance(w.get('id'), int) or isinstance(w['id'],bool) or w['id'] in ids:
            raise ValueError('word IDs must be unique integers')
        ids.add(w['id'])
        a,b=float(w['start']),float(w['end'])
        if not all(math.isfinite(x) for x in (a,b)) or a<0 or b<=a or b>plan['duration']+.001:
            raise ValueError(f'invalid/zero-duration timed word: {w["id"]}')
        if a<previous_start: raise ValueError('word timestamps are not chronological')
        if not normalized(w['text']): raise ValueError('word evidence must contain speech, not punctuation only')
        previous_start=a
    if evidence.get('review', {}).get('ambiguous_word_ids'):
        raise ValueError('unresolved ambiguous words; review/re-align them before final captions')
    return words


def import_whisper(transcript, audio, plan):
    words=[]
    for segment in transcript.get('segments', []):
        for word in segment.get('words', []):
            text=word.get('word',word.get('text','')).strip()
            if normalized(text):
                words.append({'id':len(words),'text':text,'start':float(word['start']),
                              'end':float(word['end'])})
    result={'version':1,'alignment_method':'ASR word timestamps; not forced alignment',
            'audio_file':str(Path(audio).resolve()),'audio_sha256':digest(audio),
            'timeline_sha256':timeline_id(plan),'words':words,
            'review':{'text_reviewed':False,'timing_reviewed':False,'audio_reviewed':False,
                      'ambiguous_word_ids':[],
                      'notes':['Import does not establish acoustic timing accuracy. Review uncertain words and timing locally.']}}
    check_evidence(result,plan)
    return result


def audit(plan, evidence=None):
    binding=plan.get('subtitleSync',{})
    if evidence is None:
        if not binding.get('evidence'): raise ValueError('missing final-voice subtitleSync evidence')
        evidence=read(binding['evidence'])
    words=check_evidence(evidence,plan)
    if binding.get('audio_sha256') != evidence['audio_sha256']:
        raise ValueError('plan subtitle audio binding differs from evidence')
    if not evidence.get('review',{}).get('text_reviewed'):
        raise ValueError('caption text/terms have not been reviewed')
    defaults=read(Path(__file__).resolve().parent.parent/'assets/defaults.json')['subtitles']
    byid={w['id']:w for w in words}; order={w['id']:i for i,w in enumerate(words)}
    covered=[]; checks=[]
    for i,cap in enumerate(plan.get('captions',[])):
        ids=cap.get('word_ids',[])
        if not ids or any(w not in byid for w in ids):
            raise ValueError(f'caption {i}: missing/unknown timed word IDs')
        positions=[order[w] for w in ids]
        if positions != list(range(positions[0],positions[0]+len(ids))):
            raise ValueError(f'caption {i}: words are skipped or reordered')
        expected=''.join(byid[w]['text'] for w in ids)
        if normalized(cap['text']) != normalized(expected):
            raise ValueError(f'caption {i}: caption differs from the timed spoken words')
        first,last=byid[ids[0]],byid[ids[-1]]
        lead=cap['start']-first['start']; tail=cap['end']-last['end']
        if not (-defaults['timing_start_early_seconds']-.000001 <= lead <= defaults['timing_start_late_seconds']+.000001):
            raise ValueError(f'caption {i}: start differs from word evidence by {lead:.3f}s')
        if not (-defaults['timing_end_early_seconds']-.000001 <= tail <= defaults['timing_end_late_seconds']+.000001):
            raise ValueError(f'caption {i}: end differs from word evidence by {tail:.3f}s')
        frame_error=max(abs(cap[k]*plan['fps']-round(cap[k]*plan['fps'])) for k in ('start','end'))
        if frame_error>1e-5: raise ValueError(f'caption {i}: not on locked frame grid')
        covered+=ids
        checks.append({'caption':i,'word_ids':ids,'start_offset_seconds':round(lead,6),
                       'end_offset_seconds':round(tail,6)})
    if covered != [w['id'] for w in words]:
        raise ValueError('caption coverage has omitted, duplicated or reordered speech words')
    return {'timing_evidence_check':'passed','captions':len(checks),'words':len(words),
            'audio_sha256':evidence['audio_sha256'],'timeline_sha256':timeline_id(plan),
            'alignment_method':evidence['alignment_method'],'checks':checks,
            'review':evidence.get('review',{}),
            'limitation':'Evidence consistency only; does not certify word timestamp accuracy, rendered text or lip sync.'}


def build(plan, evidence, groups, evidence_path):
    words=check_evidence(evidence,plan); byid={w['id']:w for w in words};fps=plan['fps']
    caps=[]
    for group in groups:
        ids=group['word_ids']
        if not ids or any(i not in byid for i in ids): raise ValueError('unknown or empty word group')
        cap=dict(group)
        cap['start']=max(0,round((byid[ids[0]]['start']-.04)*fps)/fps)
        cap['end']=min(math.floor(plan['duration']*fps+1e-6)/fps,
                       round((byid[ids[-1]]['end']+.08)*fps)/fps)
        caps.append(cap)
    for left,right in zip(caps,caps[1:]):
        if left['end']>right['start']:
            # Only settle the boundary BETWEEN timed words. Never divide text by character count.
            boundary=(byid[left['word_ids'][-1]]['end']+byid[right['word_ids'][0]]['start'])/2
            boundary=round(boundary*fps)/fps
            left['end']=right['start']=boundary
    plan=dict(plan,captions=caps,subtitleSync={'evidence':str(Path(evidence_path).resolve()),
                                            'audio_sha256':evidence['audio_sha256']})
    validate(plan);audit(plan,evidence)
    return plan


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('import-whisper');q.add_argument('transcript');q.add_argument('--audio',required=True);q.add_argument('--plan',required=True);q.add_argument('--out',required=True)
    q=sub.add_parser('build');q.add_argument('plan');q.add_argument('--evidence',required=True);q.add_argument('--groups',required=True);q.add_argument('--out',required=True)
    q=sub.add_parser('audit');q.add_argument('plan');q.add_argument('--out',required=True)
    a=p.parse_args()
    if a.command=='import-whisper': result=import_whisper(read(a.transcript),a.audio,read(a.plan))
    elif a.command=='build': result=build(read(a.plan),read(a.evidence),read(a.groups),a.evidence)
    else: result=audit(read(a.plan))
    write(a.out,result)
    print(json.dumps({'output':str(Path(a.out).resolve()),'command':a.command},ensure_ascii=False))


if __name__=='__main__': main()
