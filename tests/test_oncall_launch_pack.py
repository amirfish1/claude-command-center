"""Offline invariants for real footage, truthful edits, and post-send user turns.

No browser, socket binding, Docker, model calls, or production state.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


pack = module('oncall-launch-pack')
session = module('oncall-session-proof')
gallery = module('oncall-gallery')


def user(text, stamp='2026-01-01T00:00:10Z'):
    return {'type':'response_item','timestamp':stamp,
            'payload':{'role':'user','content':[{'type':'input_text','text':text}]}}


def test_initial_prompt_blue_cannot_prove_phone_receipt():
    rows=[user('Ask me to choose blue or green.')]
    assert session.new_reply_turn(rows,1,'blue',1767225605) is None
    rows.append(user('Owner answer: blue'))
    receipt=session.new_reply_turn(rows,1,'blue',1767225605)
    assert receipt['ordinal']==1
    assert receipt['text']=='Owner answer: blue'


@pytest.mark.parametrize('text,stamp',[
    ('The answer is blue','2026-01-01T00:00:10Z'),
    ('blue','2026-01-01T00:00:10Z'),
    ('Owner answer: blue','2026-01-01T00:00:01Z'),
])
def test_wrong_or_presend_user_message_is_rejected(text,stamp):
    with pytest.raises(AssertionError,match='Next new user turn'):
        session.new_reply_turn([user(text,stamp)],0,'blue',1767225605)


def test_later_matching_user_message_cannot_mask_wrong_next_turn():
    with pytest.raises(AssertionError):
        session.new_reply_turn([user('green'),user('Owner answer: blue')],0,'blue',1767225605)


def source(tmp_path, seconds=20, gap=None):
    # Bytes are deliberately NOT a picture. These unit fixtures cannot be used
    # as screen recording footage; the offline tests only validate planning.
    frames=[]
    for i in range(seconds*2+1):
        file=f'frame-{i:07d}.jpg'
        (tmp_path/file).write_bytes(b'unit-test-non-image')
        frames.append({'file':file,'seconds':i*.5+(gap if gap and i else 0),
                       'sha256':hashlib.sha256(b'unit-test-non-image').hexdigest()})
    (tmp_path/'recording.json').write_text(json.dumps({'kind':'real-timed-browser-frames','frames':frames,'failure':None}))
    return tmp_path


def test_plan_preserves_real_time_and_never_slows(tmp_path):
    directory=source(tmp_path)
    meta,speed,duration,deltas=pack.recording_plan(directory,8)
    assert duration==20
    assert speed==2.5
    assert deltas==[.5]*40
    assert len(meta['frames'])==41


def test_editor_refuses_still_padding_or_slow_motion(tmp_path):
    directory=source(tmp_path,seconds=2)
    with pytest.raises(ValueError,match='Never slow or freeze'):
        pack.recording_plan(directory,8)


def test_editor_rejects_capture_gap(tmp_path):
    with pytest.raises(ValueError,match='gap exceeds'):
        pack.recording_plan(source(tmp_path,gap=3),8)


def test_editor_hashes_every_frame(tmp_path):
    directory=source(tmp_path)
    (directory/'frame-0000003.jpg').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='hash mismatch'):
        pack.recording_plan(directory,8)


def test_editor_refuses_reconstruction_metadata(tmp_path):
    (tmp_path/'recording.json').write_text(json.dumps({'kind':'reconstruction'}))
    with pytest.raises(ValueError,match='Not a successful real'):
        pack.recording_plan(tmp_path,8)


def test_exact_cut_totals_and_separate_run_disclosure():
    assert sum(scene[2] for scene in pack.FILM)==40
    assert sum(scene[2] for scene in pack.LOOP)==12
    assert all('Separate real run' in scene[3] for scene in pack.FILM if scene[0]=='session')
    assert all(scene[0]=='session' for scene in pack.LOOP)


def proof_fixtures(directory):
    (directory/'agent').mkdir()
    (directory/'session').mkdir()
    for role in ('agent','session'):
        (directory/role/'cleanup.json').write_text(json.dumps({'status':'PASS',
            'remaining_private_home_process_count':0,'containers_removed':1}))
    agent={'status':'PASS','automatic_dispatch':True,'unchanged_test_passed':True,
           'model':'gpt-6.1-sol','observed_worker_model':'gpt-6.1-sol','observed_worker_engine':'codex',
           'notice_held_until_approval':True,'mailbox_before_count':0,'mailbox_after_count':1,
           'phone_approval_sent_at':10,'mailbox_before_approval_at':9,'mailbox_received_at':11,'run_id':'a'}
    reply={'status':'PASS','run_id':'b','model':'gpt-6.1-sol','transcript_model_verified':'gpt-6.1-sol','next_user_turn':{'text':'Owner answer: blue','answer':'blue','role':'user',
           'ordinal':3,'timestamp':'2026-01-01T00:00:10Z'},'baseline_rows':3,'send_at':1767225605,'alert_closed':True}
    (directory/'agent/agent-proof.json').write_text(json.dumps(agent))
    (directory/'session/session-proof.json').write_text(json.dumps(reply))
    return agent,reply


def test_approval_order_is_a_render_gate(tmp_path):
    agent,_=proof_fixtures(tmp_path)
    pack.validate_proofs(tmp_path)
    agent['mailbox_received_at']=8
    (tmp_path/'agent/agent-proof.json').write_text(json.dumps(agent))
    with pytest.raises(ValueError,match='ordering'):
        pack.validate_proofs(tmp_path)


def test_reply_ordinal_is_a_render_gate(tmp_path):
    _,reply=proof_fixtures(tmp_path)
    reply['next_user_turn']['ordinal']=0
    (tmp_path/'session/session-proof.json').write_text(json.dumps(reply))
    with pytest.raises(ValueError,match='not a new post-send'):
        pack.validate_proofs(tmp_path)


def test_cleanup_failure_is_a_render_gate(tmp_path):
    proof_fixtures(tmp_path)
    (tmp_path/'agent/cleanup.json').write_text(json.dumps({'status':'FAIL',
        'remaining_private_home_process_count':1,'containers_removed':2}))
    with pytest.raises(ValueError,match='cleanup did not pass'):
        pack.validate_proofs(tmp_path)


def test_requested_model_alone_is_not_model_evidence(tmp_path):
    agent,_=proof_fixtures(tmp_path)
    agent['observed_worker_model']='other-model'
    (tmp_path/'agent/agent-proof.json').write_text(json.dumps(agent))
    with pytest.raises(ValueError,match='model is not verified'):
        pack.validate_proofs(tmp_path)


def test_gallery_inventories_nested_images_and_does_not_invent_provenance(tmp_path):
    (tmp_path/'nested').mkdir()
    png=b'\x89PNG\r\n\x1a\n'+b'\0'*8+struct.pack('>II',2880,1800)
    (tmp_path/'nested/unknown.png').write_bytes(png)
    entries=gallery.inventory(tmp_path)
    assert len(entries)==1
    assert entries[0]['file']=='nested/unknown.png'
    assert entries[0]['pixels']==[2880,1800]
    assert entries[0]['kind']=='unverified'


def test_output_cannot_escape_worktree_capture_root(tmp_path):
    with pytest.raises(ValueError,match='remain in launch-captures'):
        pack.contained(tmp_path)


def test_share_manifest_excludes_unlisted_media_and_private_metadata(tmp_path):
    entries=[{'file':'oncall-launch-40s.mp4','caption':'Real footage','bytes':123,
              'source':'private metadata','local_home':'must not export'},
             {'file':'unreviewed.png','bytes':456},
             {'file':'session/session-phone/frame-0000000.jpg','bytes':789}]
    (tmp_path/'capture-manifest.json').write_text(json.dumps(entries))
    report={'status':'verified-media','real_boundary':'Two real local runs',
            'separate_runs':['a','b'],'assets':[{'file':'oncall-launch-40s.mp4','clips':[]}]}
    result=pack.share_manifest(tmp_path,report)
    assert result['assets']==[{'file':'oncall-launch-40s.mp4','bytes':123,'caption':'Real footage'}]


def test_separate_runs_can_use_independently_verified_models(tmp_path):
    _, reply = proof_fixtures(tmp_path)
    reply['model'] = reply['transcript_model_verified'] = 'gpt-6.1-terra'
    (tmp_path / 'session/session-proof.json').write_text(json.dumps(reply))
    agent, session_proof = pack.validate_proofs(tmp_path)
    assert agent['model'] == 'gpt-6.1-sol'
    assert session_proof['model'] == 'gpt-6.1-terra'


def test_exact_oncall_service_header_is_preserved_as_evidence():
    text = '[sender: ccc:oncall (service) · msg 00000000-0000-0000-0000-000000000000]\nOwner answer: blue'
    receipt = session.new_reply_turn([user(text)], 0, 'blue', 1767225605)
    assert receipt['text'] == text
    assert receipt['body'] == 'Owner answer: blue'
    assert receipt['answer'] == 'blue'
    with pytest.raises(AssertionError):
        session.new_reply_turn([user(text.replace('ccc:oncall', 'another-service'))], 0, 'blue', 1767225605)


@pytest.mark.parametrize('target',['recording','still'])
def test_media_from_another_attempt_cannot_borrow_passing_proof(tmp_path,target):
    agent,session_proof=proof_fixtures(tmp_path)
    proofs={'agent':agent,'session':session_proof}
    for role,name in {(role,name) for role,name,_,_ in pack.FILM+pack.LOOP}:
        dest=tmp_path/role/name
        dest.mkdir()
        (dest/'recording.json').write_text(json.dumps({
            'run_id':proofs[role]['run_id'],'model':proofs[role]['model']}))
    for role,proof in proofs.items():
        (tmp_path/role/'browser-provenance.json').write_text(json.dumps({
            'run_id':proof['run_id'],'model':proof['model'],'screenshots':[
                {'file':name,'run_id':proof['run_id']} for source,name in pack.STILLS
                if source==role and not name.startswith('mail-')]}))
    pack.validate_capture_runs(tmp_path,agent,session_proof)
    file=(tmp_path/'session/session-phone/recording.json' if target=='recording'
          else tmp_path/'session/browser-provenance.json')
    data=json.loads(file.read_text());data['run_id']='another-attempt'
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='different proof run'):
        pack.validate_capture_runs(tmp_path,agent,session_proof)



def test_report_captions_change_only_at_observed_events():
    meta={'frames':[{'seconds':1}], 'events':[
        {'kind':'worker-observed','seconds':7},
        {'kind':'fixed-visible','seconds':37}]}
    beats=pack.caption_beats(meta,14,3,'unused',report_fix=True)
    assert [(beat['start'],beat['end']) for beat in beats]==[(0,2),(2,12),(12,14)]
    meta['events'][1]['seconds']=50
    with pytest.raises(ValueError,match='captions must follow'):
        pack.caption_beats(meta,14,3,'unused',report_fix=True)
