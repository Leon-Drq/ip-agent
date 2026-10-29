import base64
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

class CryptoTest(unittest.TestCase):
    def test_credentials_roundtrip_and_tampering(self):
        from remix_hub import protect
        from cryptography.exceptions import InvalidTag
        with patch.dict(os.environ,{'IP_AGENT_MASTER_KEY':'11'*32}):
            encoded=protect(b'test-only-secret')
            self.assertNotIn(b'test-only-secret',encoded)
            self.assertEqual(protect(encoded,True),b'test-only-secret')
            with self.assertRaises(InvalidTag):protect(encoded[:-1]+bytes([encoded[-1]^1]),True)
        with patch.dict(os.environ,{'IP_AGENT_MASTER_KEY':'22'*32}):
            with self.assertRaises(InvalidTag):protect(encoded,True)

class UploadTest(unittest.TestCase):
    def test_signed_upload_does_not_send_api_key_to_storage(self):
        from hub_upload import upload_reference
        calls=[]
        class Hub:
            def key(self):return 'never-send-this-to-storage'
            def request(self,method,url,body=None):
                calls.append((url,body))
                if url=='/files/uploads':return {'upload_mode':'s3_multipart','upload_id':'test','part_size':3,'part_count':2}
                if url.endswith('/parts'):
                    p=body['parts'][0]
                    return {'parts':[{'part_number':p['part_number'],'url':'https://storage.example/part',
                        'headers':{'content-length':str(p['bytes']),'x-amz-checksum-sha256':p['checksum_sha256']}}]}
                if url.endswith('/complete'):return {'url':'https://storage.example/result'}
                raise AssertionError(url)
        sent=[]
        def put(url,**kwargs):
            sent.append(kwargs)
            return httpx.Response(200,headers={'etag':'test-etag'},request=httpx.Request('PUT',url))
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'test.mp4';file.write_bytes(b'abcdef')
            with patch('remix_hub.validate_asset_url'),patch('hub_upload.httpx.put',side_effect=put):
                self.assertEqual(upload_reference(Hub(),file),'https://storage.example/result')
        self.assertEqual(len(sent),2)
        self.assertTrue(all('authorization' not in s['headers'] for s in sent))
        self.assertEqual(calls[-1][1]['parts'][1]['part_number'],2)
        self.assertEqual(calls[0][1]['sha256'],hashlib.sha256(b'abcdef').hexdigest())

if __name__=='__main__':unittest.main()
