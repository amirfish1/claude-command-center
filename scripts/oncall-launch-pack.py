#!/usr/bin/env python3
"""Regenerate launch media from TWO separate real isolated proofs, never replays.

Run with the host's normal HOME (auth/cache discovery); the existing proof
runners, not this editor, create private allow-listed execution environments.
No publish, restart, or production service access. --render-only reuses actual
frames with their verified proof JSON, never reconstructed ccc oncall film output.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import time
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parent))
from oncall_capture import reply_body

REPO = Path(__file__).resolve().parents[1]
BASE = REPO / '.claude/screenshots/launch-captures'
MODEL = 'gpt-6.1-sol'
# Actual continuous source sequences, joined by labelled hard cuts. All session
# segments explicitly identify the separate run; no claim of a single take.
FILM = [('agent', 'report-fix', 14, 'A local report arrives. WatchTower picks it up; the agent fixes the code.'),
        ('agent', 'fixed-approval-alert', 2, 'The fixed notice waits for approval.'),
        ('agent', 'fixed-approval', 4, 'Cut | Reply yes through the real phone input.'),
        ('agent', 'fixed-result', 4, 'Approved. The local reporter receives the fix notice.'),
        ('session', 'session-alert', 3, 'Separate real run | A real agent question reaches the phone.'),
        ('session', 'session-phone', 5, 'Separate real run | Cut to the reply topic. Answer: blue.'),
        ('session', 'session-transcript', 8, 'Separate real run | The phone answer is a NEW user turn in the agent session.')]
LOOP = [('session', 'session-alert', 3, 'A real agent question reaches the phone.'),
        ('session', 'session-phone', 5, 'Cut to the reply topic. Answer blue from the phone.'),
        ('session', 'session-transcript', 4, 'The answer reaches the agent as a new user turn.')]
STILLS = [('session','session-alert-phone.png'),('session','session-alert-desktop.png'),
          ('session','session-reply-sent-phone.png'),('session','ccc-session-question.png'),
          ('session','ccc-session-reply.png'),('agent','report-fixed-desktop.png'),
          ('agent','report-fixed-phone.png'),('agent','fixed-result-phone.png'),
          ('session','session-reply-typed-phone.png'),('agent','fixed-alert-phone.png'),
          ('agent','fixed-reply-sent-phone.png'),('agent','mail-fixed-desktop.png'),
          ('agent','mail-fixed-phone.png')]


def load(file):
    return json.loads(Path(file).read_text())


def contained(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(BASE.resolve()):
        raise ValueError('All work must remain in launch-captures')
    return directory


def recording_plan(directory, target):
    """Validate source integrity, preserve actual irregular capture timing."""
    meta = load(directory / 'recording.json')
    if meta.get('kind') != 'real-timed-browser-frames' or meta.get('failure'):
        raise ValueError('Not a successful real browser recording')
    frames = meta['frames']
    if len(frames) < 2:
        raise ValueError('Missing real source frames')
    for frame in frames:
        file = Path(frame['file'])
        if file.name != str(file) or file.suffix != '.jpg':
            raise ValueError('Unsafe source frame path')
        if hashlib.sha256((directory / file).read_bytes()).hexdigest() != frame['sha256']:
            raise ValueError('Source frame hash mismatch')
    times = [frame['seconds'] for frame in frames]
    deltas = [b-a for a, b in zip(times, times[1:])]
    if any(d <= 0 or d > 2 for d in deltas):
        raise ValueError('Capture gap exceeds 2s or timestamp order is invalid; refusing a frozen-frame film')
    duration = times[-1] - times[0]
    if duration < target:
        raise ValueError(f'Real source {directory.name} is {duration:.3f}s; need at least {target}s. Never slow or freeze to fill.')
    # Floor to six decimals, then trim <1ms excess at the end. Label exactly
    # the same numeric setpts speed used by ffmpeg, including normal 1x.
    speed = math.floor(duration / target * 1_000_000) / 1_000_000
    return meta, speed, duration, deltas


def validate_proofs(directory):
    agent, session = load(directory/'agent/agent-proof.json'), load(directory/'session/session-proof.json')
    if agent['status'] != 'PASS' or session['status'] != 'PASS':
        raise ValueError('Both actual proofs must pass before media can be built')
    if not agent['automatic_dispatch'] or not agent['unchanged_test_passed']:
        raise ValueError('Missing automatic agent fix evidence')
    if (agent.get('observed_worker_model') != agent['model'] or agent.get('observed_worker_engine') != 'codex' or
            session.get('transcript_model_verified') != session['model']):
        raise ValueError('Actual fixing/session model is not verified')
    if not agent['notice_held_until_approval'] or agent['mailbox_after_count'] != agent['mailbox_before_count']+1:
        raise ValueError('Missing reporter approval evidence')
    send = agent['phone_approval_sent_at']
    if isinstance(send, str):
        send = datetime.fromisoformat(send.replace('Z','+00:00')).timestamp()
    if not agent['mailbox_before_approval_at'] <= send <= agent['mailbox_received_at']:
        raise ValueError('Invalid approval/delivery ordering')
    turn = session['next_user_turn']
    stamp = datetime.fromisoformat(turn['timestamp'].replace('Z','+00:00')).timestamp()
    if (reply_body(turn['text']) != 'Owner answer: blue' or turn.get('answer') != 'blue' or turn['role'] != 'user' or
            turn['ordinal'] < session['baseline_rows'] or stamp < session['send_at'] or not session['alert_closed']):
        raise ValueError('Reply is not a new post-send blue user turn')
    if agent['run_id'] == session['run_id']:
        raise ValueError('Expected two separately identified real runs')
    for role in ('agent','session'):
        cleanup=load(directory/role/'cleanup.json')
        if (cleanup['status']!='PASS' or cleanup['remaining_private_home_process_count'] or
                not cleanup['containers_removed'] or cleanup.get('docker_cleanup_errors')):
            raise ValueError('Private proof cleanup did not pass')
    return agent, session



def validate_capture_runs(directory, agent, session):
    """Passing proof evidence cannot bless footage from another attempt."""
    proofs={'agent':agent,'session':session}
    for role,name in {(role,name) for role,name,_,_ in FILM+LOOP}:
        meta=load(directory/role/name/'recording.json')
        proof=proofs[role]
        if meta.get('run_id')!=proof['run_id'] or meta.get('model')!=proof['model']:
            raise ValueError('Recording belongs to a different proof run: '+role+'/'+name)
    for role,proof in proofs.items():
        provenance=load(directory/role/'browser-provenance.json')
        if provenance.get('run_id')!=proof['run_id'] or provenance.get('model')!=proof['model']:
            raise ValueError('Browser still provenance belongs to a different proof run')
        shots={shot['file']:shot for shot in provenance.get('screenshots',[])}
        for source,name in STILLS:
            if source!=role or name.startswith('mail-'):
                continue  # Decoded email is explicitly labelled source rendering.
            if shots.get(name,{}).get('run_id')!=proof['run_id']:
                raise ValueError('Still belongs to a different proof run: '+name)


def ffprobe(file):
    result = subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(file)],
                            text=True,capture_output=True,check=True,timeout=30)
    return json.loads(result.stdout)


def verify_video(file, duration):
    info = ffprobe(file)
    video = [s for s in info['streams'] if s['codec_type']=='video']
    if len(video)!=1 or any(s['codec_type']=='audio' for s in info['streams']):
        raise ValueError('Expected exactly one video stream and no audio')
    if (video[0]['width'],video[0]['height']) != (1920,1080):
        raise ValueError('Expected 1080p')
    if abs(float(info['format']['duration'])-duration)>0.001:
        raise ValueError('Duration is not exact')
    if int(video[0].get('nb_frames', 0)) != duration*30:
        raise ValueError('Expected exact 30fps frame count')
    return info



def caption_beats(meta, seconds, speed, caption, report_fix=False):
    if not report_fix:
        return [{'start':0,'end':seconds,'text':caption}]
    events={event['kind']:event['seconds'] for event in meta.get('events',[])}
    start=meta['frames'][0]['seconds']
    worker=(events['worker-observed']-start)/speed
    fixed=(events['fixed-visible']-start)/speed
    if not 0 < worker < fixed < seconds:
        raise ValueError('Report captions must follow observed worker and visible fix events')
    return [{'start':0,'end':worker,'text':'A bug report arrives.'},
            {'start':worker,'end':fixed,'text':'The agent starts by itself.'},
            {'start':fixed,'end':seconds,'text':'The fix lands.'}]


def edit_clip(directory, role, name, seconds, caption, dest):
    source = directory/role/name
    meta, speed, duration, deltas = recording_plan(source, seconds)
    # Paths in ffconcat are relative to the source directory; no private
    # filesystem names appear in footage, captions, or shareable manifest.
    concat = ['ffconcat version 1.0']
    for i, frame in enumerate(meta['frames']):
        concat += [f"file '{frame['file']}'", f"duration {(deltas[i] if i<len(deltas) else deltas[-1]):.9f}"]
    listing = source/'frames.ffconcat'
    listing.write_text('\n'.join(concat)+'\n')
    exact_speed = f'{speed:.6f}'.rstrip('0').rstrip('.')
    beats=caption_beats(meta,seconds,speed,caption,report_fix=name=='report-fix')
    labels=[]
    filters=[]
    for index,beat in enumerate(beats):
        label=f"{beat['text']}\n{exact_speed}x speed | Real local browser recording"
        labels.append(label)
        textfile=dest.with_suffix(f'.caption-{index}.txt')
        textfile.write_text(label)
        filters.append(f"drawtext=textfile={textfile.name}:expansion=none:fontcolor=0x162332:fontsize=26:x=40:y=970:line_spacing=10:enable='gte(t,{beat['start']})*lt(t,{beat['end']})'")
    relative = os.path.relpath(listing,dest.parent)
    vf = (f'setpts=(PTS-STARTPTS)/{speed:.6f},'
          'scale=1840:900:force_original_aspect_ratio=decrease:flags=lanczos,'
          'pad=1920:1080:(ow-iw)/2:36:color=0xf3f5f7,setsar=1,'
          + ','.join(filters) + ',fps=30,format=yuv420p')
    subprocess.run(['/usr/bin/nice','-n','15','ffmpeg','-hide_banner','-loglevel','error','-y',
                    '-threads','1','-f','concat','-safe','0','-i',relative,'-vf',vf,
                    '-t',str(seconds),'-an','-c:v','libx264','-preset','fast','-crf','19',
                    '-threads','2','-movflags','+faststart',dest.name],cwd=dest.parent,check=True,timeout=600)
    verify_video(dest, seconds)
    return {'source':str(source.relative_to(directory)), 'run_id':meta['run_id'],
            'source_start_seconds':meta['frames'][0]['seconds'],
            'source_end_seconds':meta['frames'][0]['seconds']+seconds*speed,
            'available_source_seconds':duration,'output_duration':seconds,'speed':speed,
            'caption':' | '.join(labels),'caption_beats':beats,'real_boundary':'Actual local browser. Seeded test bug / scripted test prompts and phone input; real agent and services.',
            'recording':str((source/'recording.json').relative_to(directory))}


def render(directory):
    agent, session = validate_proofs(directory)
    validate_capture_runs(directory,agent,session)
    edit = directory/'edits'
    edit.mkdir(exist_ok=True)
    assets = []
    for name, scenes, expected in [('oncall-launch-40s.mp4', FILM,40),('oncall-hero-12s.mp4',LOOP,12)]:
        clips, provenance, cursor = [], [], 0
        for index,(role, recording, seconds, caption) in enumerate(scenes):
            clip = edit/f'{Path(name).stem}-{index}.mp4'
            data = edit_clip(directory,role,recording,seconds,caption,clip)
            data.update(output_start=cursor,output_end=cursor+seconds)
            cursor += seconds
            provenance.append(data)
            clips.append(f"file '{clip.name}'")
        listing = edit/(Path(name).stem+'.ffconcat')
        listing.write_text('ffconcat version 1.0\n'+'\n'.join(clips)+'\n')
        target = directory/name
        subprocess.run(['/usr/bin/nice','-n','15','ffmpeg','-hide_banner','-loglevel','error','-y',
            '-f','concat','-safe','0','-i',listing.name,'-c','copy','-an','-movflags','+faststart',str(target)],
            cwd=edit,check=True,timeout=120)
        verify_video(target, expected)
        # Actual decoded review frames, not replacements for recorded footage.
        reviews = directory/'review'
        reviews.mkdir(exist_ok=True)
        for at in ([1,13,15,19,21,23,25,31,33,39] if expected==40 else [1,7,9,11]):
            subprocess.run(['/usr/bin/nice','-n','15','ffmpeg','-hide_banner','-loglevel','error','-y',
                '-ss',str(at),'-i',str(target),'-frames:v','1','-threads','1',
                str(reviews/f'{target.stem}-{at:02d}s.png')],check=True,timeout=30)
        assets.append({'file':name,'seconds':expected,'width':1920,'height':1080,'audio':False,
                       'caption':'Captioned silent real local browser footage. Two separate runs.' if expected==40 else 'Silent real ntfy answer reaching the real agent, one session run.',
                       'clips':provenance})
    # Light-theme exact viewport stills already produced by the real helpers.
    for role, name in STILLS:
        src=directory/role/name
        if not src.is_file():
            raise ValueError('Missing required real still: '+name)
        if not name.startswith(('ccc-session-', 'mail-')):
            expected=(1170,2532) if name.endswith('phone.png') else (2880,1800)
            if struct.unpack('>II',src.read_bytes()[16:24]) != expected:
                raise ValueError('Incorrect native screenshot dimensions: '+name)
        shutil.copyfile(src,directory/name)
    report={'schema':1,'status':'verified-media','created_utc':datetime.now(timezone.utc).isoformat(),
            'models':{'agent':agent['model'],'session':session['model']},'separate_runs':[agent['run_id'],session['run_id']],
            'real_boundary':'Local isolated ntfy/CCC; example mailbox, seeded bug, scripted report/question/input, actual Codex fix/reply. No production users or services.',
            'visual_review':'Required: inspect decoded review PNGs before sharing. Automated media checks do not establish visual quality.',
            'assets':assets,'proofs':{'agent':'agent/agent-proof.json','session':'session/session-proof.json'}}
    (directory/'launch-pack.json').write_text(json.dumps(report,indent=2))
    return report


def execute(directory, model, session_model=None, resume=False):
    measurements=load(directory/'execution-private.json') if resume and (directory/'execution-private.json').exists() else []
    for role,script in [('agent','oncall-proof.py'),('session','oncall-session-proof.py')]:
        dest=directory/role
        selected_model=(session_model or model) if role=='session' else model
        if resume and dest.exists():
            proof=dest/(role+'-proof.json')
            cleanup=dest/'cleanup.json'
            if (proof.is_file() and cleanup.is_file() and load(proof).get('status')=='PASS'
                    and load(proof).get('model')==selected_model and load(cleanup).get('status')=='PASS'):
                print(f'Reusing verified {role} run {load(proof)["run_id"]}',flush=True)
                continue
            dest.rename(directory/('failed-'+role+'-'+uuid.uuid4().hex[:8]))
        dest.mkdir()
        run_id=role+'-'+uuid.uuid4().hex
        env=dict(os.environ,PROOF_CAPTURE_DIR=str(dest),PROOF_RUN_ID=run_id,PROOF_MODEL=selected_model)
        command=['/usr/bin/nice','-n','15',sys.executable,str(REPO/'scripts'/script),'--with-agent','--capture','--model',selected_model]
        started=time.time()
        print(f'Running {script}, run {run_id}',flush=True)
        with (dest/'proof.log').open('w') as log:
            proc=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                code=proc.wait(timeout=900)
            except BaseException:
                # Give the existing proof's signal handler/finally time to
                # reap detached private HOME children and remove containers.
                os.killpg(proc.pid,signal.SIGTERM)
                try:
                    proc.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL)
                    proc.wait(timeout=10)
                raise
        measurements.append({'run_id':run_id,'command':command,'started_utc':datetime.fromtimestamp(started,timezone.utc).isoformat(),
                             'runtime_seconds':time.time()-started,'exit_code':code})
        (directory/'execution-private.json').write_text(json.dumps(measurements,indent=2))
        if code:
            raise RuntimeError(f'{script} failed; inspect {role}/proof.log. No film success claimed.')


def share_manifest(directory, report):
    """A copy whitelist; raw logs, mail, JSONL, and auth are never export assets."""
    entries=load(directory/'capture-manifest.json')
    public=[]
    allowed=('file','bytes','sha256','pixels','seconds','audio','caption','kind','run_id','viewport','crop')
    media_names=({item['file'] for item in report['assets']} | {name for role,name in STILLS}
                 | {f'session-gallery-{n}.png' for n in range(1,7)})
    for item in entries:
        if item['file'] not in media_names:
            continue
        public.append({key:item[key] for key in allowed if key in item})
    manifest={'status':report['status'],'boundary':report['real_boundary'],
              'separate_real_runs':report['separate_runs'],'assets':public,
              'films':[{'file':video['file'],'clips':[
                  {key:clip[key] for key in ('run_id','source_start_seconds','source_end_seconds',
                    'output_start','output_end','output_duration','speed','caption','caption_beats')}
                  for clip in video['clips']]} for video in report['assets']]}
    (directory/'share-manifest.json').write_text(json.dumps(manifest,indent=2))
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',default=MODEL)
    parser.add_argument('--session-model',default='gpt-6.1-terra',help='Optional different model supported by the CCC session catalogue')
    parser.add_argument('--resume',type=Path,help='Reuse successful parts of an interrupted pack; retain failed attempts')
    parser.add_argument('--render-only',type=Path,help='Existing launch-captures pack with two successful real proofs')
    parser.add_argument('--output',type=Path,help='New directory strictly below launch-captures')
    args=parser.parse_args()
    directory=contained(args.render_only or args.resume or args.output or BASE/('pack-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')))
    if not args.render_only:
        directory.mkdir(parents=True,exist_ok=bool(args.resume))
        execute(directory,args.model,args.session_model,resume=bool(args.resume))
    started=time.monotonic()
    report=render(directory)
    subprocess.run([sys.executable,str(REPO/'scripts/oncall-gallery.py'),'--output',str(directory)],check=True)
    share_manifest(directory,report)
    print(f'Built and ffprobe-verified 40s film and 12s loop in {time.monotonic()-started:.2f}s: {directory}',flush=True)
    print('Inspect review/*.png and the real stills before exporting. share-manifest.json lists only final media; private logs and metadata are excluded. No files published or copied outside this worktree.')


if __name__=='__main__':
    main()
