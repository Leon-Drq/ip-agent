const {test}=require('node:test');
const assert=require('node:assert/strict');
const {serviceURL,sameOrigin}=require('../desktop/security.cjs');
const {hashes}=require('../desktop/media.cjs');
test('service address accepts loopback and authenticated HTTPS origins only',()=>{
  assert.equal(serviceURL('http://127.0.0.1:7865'),'http://127.0.0.1:7865');
  assert.equal(serviceURL('https://studio.example.org/'),'https://studio.example.org');
  for(const value of ['file:///etc/passwd','javascript:alert(1)','http://192.168.1.5:7865','https://name:secret@host/','http://localhost:7865/#models'])
    assert.throws(()=>serviceURL(value));
});
test('session header origin boundaries reject redirects and lookalike hosts',()=>{
  assert(sameOrigin('http://127.0.0.1:1234/api/health','http://127.0.0.1:1234'));
  for(const value of ['https://evil.test','http://127.0.0.1:1235','http://127.0.0.1.evil.test:1234'])assert(!sameOrigin(value,'http://127.0.0.1:1234'));
});
test('all requested native platforms have pinned media hashes',()=>{
  for(const target of ['win32-x64','darwin-arm64','darwin-x64']) {
    assert.equal(hashes[target].length,2);
    hashes[target].forEach(hash=>assert.match(hash,/^[a-f0-9]{64}$/));
  }
});
