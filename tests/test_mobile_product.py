import io
import struct
import unittest

from gpmc.harbor_core import create_task, transition, dispatch
from gpmc.media_identity import apple_photo_id, apple_video_id

IDENTIFIER=b'12345678-1234-1234-1234-123456789abc'

def atom(kind,body):
    return struct.pack('>I',len(body)+8)+kind+body

class MobileContractTests(unittest.TestCase):
    def task(self):
        return create_task('task',{'local_id':'asset','filename':'photo.jpg','media_kind':'photo',
            'size':20,'sha1':'a'*40,'sha256':'b'*64})['task']

    def test_revision_counts_real_transitions_and_completion_needs_evidence(self):
        task=self.task()
        for state in ['preparing','uploading','submitting','confirming']:
            previous=task['revision'];task=transition(task,state)['task']
            self.assertEqual(task['revision'],previous+1)
            self.assertEqual(transition(task,state)['task']['revision'],task['revision'])
        self.assertEqual(transition(task,'done'),{'ok':False,'error':'CLOUD_CONFIRMATION_REQUIRED'})
        done=transition(task,'done',cloud_confirmed=True)['task']
        self.assertTrue(done['cloud_confirmed'])
        self.assertEqual(transition(done,'queued')['error'],'INVALID_TRANSITION')

    def test_filename_and_uuid_text_do_not_prove_pairing(self):
        self.assertIsNone(apple_photo_id(b'IMG_001.JPG '+IDENTIFIER))
        self.assertIsNone(apple_video_id(io.BytesIO(b'IMG_001.MOV '+IDENTIFIER)))

    def test_explicit_apple_photo_maker_note(self):
        note=b'Apple iOS\x00\x00\x01MM'+struct.pack('>H',1)
        note+=struct.pack('>HHII',17,2,37,32)+b'\x00'*4+IDENTIFIER+b'\x00'
        self.assertEqual(apple_photo_id(b'\xff\xd8'+note),IDENTIFIER.decode())

    def test_quicktime_metadata_key_resolves_identifier(self):
        key=b'com.apple.quicktime.content.identifier'
        keys=atom(b'keys',b'\x00'*4+struct.pack('>I',1)+struct.pack('>I',len(key)+8)+b'mdta'+key)
        item=atom(struct.pack('>I',1),atom(b'data',b'\x00\x00\x00\x01'+b'\x00'*4+IDENTIFIER))
        movie=atom(b'ftyp',b'qt  ')+atom(b'moov',atom(b'meta',b'\x00'*4+keys+atom(b'ilst',item)))
        self.assertEqual(apple_video_id(io.BytesIO(movie)),IDENTIFIER.decode())
        self.assertIsNone(apple_video_id(io.BytesIO(movie[:-5])))

    def test_proxy_sticks_to_healthy_connection_then_fails_over(self):
        payload={'policy':{'selection':'automatic','exhaustion':'wait'},'current':'B',
            'nodes':[{'name':'A','latency':10},{'name':'B','latency':50}]}
        self.assertEqual(dispatch('select_proxy',payload)['node'],'B')
        payload['nodes'][1]['cooling']=True
        self.assertEqual(dispatch('select_proxy',payload)['node'],'A')
        payload['nodes'][0]['healthy']=False
        self.assertTrue(dispatch('select_proxy',payload)['waiting'])
        payload['policy']['exhaustion']='direct'
        self.assertTrue(dispatch('select_proxy',payload)['direct'])

    def test_cancelled_task_cannot_reappear_after_restart(self):
        cancelled=transition(self.task(),'cancelled')['task']
        for state in ['queued','uploading','confirming','done']:
            self.assertFalse(transition(cancelled,state,cloud_confirmed=True).get('ok',True))

if __name__=='__main__':unittest.main()
