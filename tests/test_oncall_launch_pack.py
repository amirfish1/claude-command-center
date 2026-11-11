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


def crop_source(tmp_path):
    directory = source(tmp_path, seconds=12)
    meta = json.loads((directory/'recording.json').read_text())
    # Geometry-only JPEG header, deliberately not decodable film content.
    header = b'\xff\xd8\xff\xc0' + struct.pack('>HBHH', 17, 8, 2532, 1170)
    for frame in meta['frames']:
        (directory/frame['file']).write_bytes(header)
        frame['sha256'] = hashlib.sha256(header).hexdigest()
    meta['run_id'] = 'test-run'
    (directory/'recording.json').write_text(json.dumps(meta))
    return directory


@pytest.mark.parametrize('crop', [
    [-1, 0, 1170, 1100], [0, 2000, 1170, 1100], [0, 0, 1171, 1100],
    [0, 0, 0, 1100], [0, 0, 1170.5, 1100], [0, 0, 1170],
])
def test_v2_rejects_invalid_native_crop(tmp_path, crop):
    scene = {**pack.LOOP_V2[0], 'crop': crop}
    with pytest.raises(ValueError, match='Crop'):
        pack.v2_plan(crop_source(tmp_path), scene)


@pytest.mark.parametrize('options,reason', [
    ({'start': -1}, 'outside'), ({'end': 13}, 'outside'),
    ({'start': 4, 'end': 2}, 'outside'), ({'start': 1, 'end': 3}, 'too short'),
    ({'seconds': 2}, 'at least 3'), ({'seconds': 3.01}, '30fps'),
    ({'scale': .5}, '756'), ({'scale': -1}, 'Uniform scale'),
    ({'scale': float('nan')}, 'finite'), ({'placement': [1000, 20]}, 'placement'),
    ({'caption_font_size': 53}, '54px'), ({'label_font_size': 31}, '32px'),
])
def test_v2_readability_and_real_interval_gates(tmp_path, options, reason):
    with pytest.raises(ValueError, match=reason):
        pack.v2_plan(crop_source(tmp_path), {**pack.LOOP_V2[0], **options})


def test_v2_provenance_describes_native_geometry_and_unrounded_time(tmp_path):
    data, _ = pack.v2_plan(crop_source(tmp_path), pack.LOOP_V2[1])
    assert data['native_dimensions'] == [1170, 2532]
    assert data['crop'] == [0, 1432, 1170, 1100]
    assert data['uniform_scale'] == .9
    assert data['scaled_dimensions'] == [1053, 990]
    assert data['output_placement'] == [48, 44]
    assert data['source_start_seconds'] == .3
    assert data['source_end_seconds'] == 3.5
    assert data['speed'] == pytest.approx(3.2/3)
    assert data['display_speed'] == '1.1x speed'
    assert data['speed']*data['output_duration'] == pytest.approx(
        data['source_end_seconds']-data['source_start_seconds'])


def test_v2_still_checks_hashes_outside_the_selected_interval(tmp_path):
    directory = crop_source(tmp_path)
    (directory/'frame-0000020.jpg').write_bytes(b'corrupt outside selected trim')
    with pytest.raises(ValueError, match='hash mismatch'):
        pack.v2_plan(directory, pack.LOOP_V2[0])


def test_v2_beat_budget_and_source_design():
    pack.validate_v2_timeline(pack.FILM_V2, 40)
    pack.validate_v2_timeline(pack.LOOP_V2, 12)
    for scene in (pack.LOOP_V2[-1], pack.FILM_V2[-1]):
        assert scene['crop'] == [24, 200, 1380, 580]
        assert scene['scale'] == 1.35
        assert scene['placement'] == [28, 170]
    assert pack.FILM_V2[0]['start'] >= 8
    assert pack.FILM_V2[0]['caption'] == 'The agent picks up the report.'
    assert 39 <= pack.FILM_V2[1]['start'] < pack.FILM_V2[1]['end'] <= 42.413
    assert pack.FILM_V2[1]['end']-pack.FILM_V2[1]['start'] >= 3
    assert pack.LOOP_V2[1]['crop'][1] > pack.LOOP_V2[2]['crop'][1]
    for scene in pack.FILM_V2+pack.LOOP_V2:
        assert scene['end']-scene['start'] >= scene['seconds']
        assert scene['crop'][3]*scene['scale'] >= 756
        assert scene['caption_font_size'] >= 54
    with pytest.raises(ValueError, match='durations'):
        pack.validate_v2_timeline(pack.LOOP_V2[:-1], 12)
    with pytest.raises(ValueError, match='at least 3'):
        pack.validate_v2_timeline([{**pack.LOOP_V2[0], 'seconds': 2}], 2)


def test_v2_merges_without_mutating_v1_assets():
    old = [{'file': 'oncall-launch-40s.mp4', 'clips': [{'speed': 3.014571}]},
           {'file': 'oncall-launch-40s-v2.mp4', 'clips': []}]
    before = json.dumps(old)
    added = {'file': 'oncall-launch-40s-v2.mp4', 'clips': [{'crop': [0, 0, 1170, 1100]}]}
    merged = pack.merge_assets(old, [added])
    assert merged[0] == old[0]
    assert merged[1] == added
    assert json.dumps(old) == before


def test_v2_share_manifest_retains_v1_and_carries_geometry(tmp_path):
    v1_asset = {'file': 'oncall-launch-40s.mp4', 'sha256': 'old-hash', 'caption': 'Original'}
    v1_film = {'file': 'oncall-launch-40s.mp4', 'clips': [{'speed': 3.014571}]}
    original = {'status': 'verified-media', 'assets': [v1_asset], 'films': [v1_film], 'retained': True}
    (tmp_path/'share-manifest.json').write_text(json.dumps(original))
    (tmp_path/'capture-manifest.json').write_text(json.dumps([
        v1_asset, {'file': 'oncall-launch-40s-v2.mp4', 'sha256': 'new-hash'}]))
    clip, _ = pack.v2_plan(crop_source(tmp_path), pack.LOOP_V2[0])
    report = {'status': 'verified-media', 'real_boundary': 'Real recordings', 'separate_runs': ['a', 'b'],
              'assets': [{'file': v1_asset['file'], 'clips': []},
                         {'file': 'oncall-launch-40s-v2.mp4', 'edit_version': 'v2', 'clips': [clip]}]}
    result = pack.share_manifest(tmp_path, report, 'v2')
    assert result['assets'][0] == v1_asset
    assert result['films'][0] == v1_film
    assert result['retained'] is True
    public = result['films'][1]['clips'][0]
    assert public['crop'] == clip['crop']
    assert public['uniform_scale'] == .9
    assert public['native_dimensions'] == [1170, 2532]
    assert public['display_speed'] == '1.4x speed'


def test_v2_review_inventory_identifies_decoded_frames(tmp_path):
    reviews = tmp_path/'review-v2'
    reviews.mkdir()
    (reviews/'oncall-hero-12s-v2-000.75s.png').write_bytes(
        b'\x89PNG\r\n\x1a\n'+b'\0'*8+struct.pack('>II', 960, 540))
    (tmp_path/'launch-pack.json').write_text(json.dumps({'status': 'verified-media', 'assets': []}))
    entries = gallery.inventory(tmp_path)
    assert entries[0]['pixels'] == [960, 540]
    assert entries[0]['kind'] == 'decoded-review-frame'
    assert entries[0]['source'] == 'launch-pack.json'
