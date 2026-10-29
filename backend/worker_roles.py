from settings import ROOT
"""Local speaker grouping with the existing CosyVoice CAM++ encoder, CPU only.

Feature extraction follows CosyVoice's _extract_spk_embedding. Groups are acoustic
speakers, not claimed visual identities; the editor supports merging/renaming them.
"""
import json
import sys
import wave
from pathlib import Path


def identify(folder, model):
    import numpy as np
    import torch
    import onnxruntime as ort
    from torchaudio.compliance.kaldi import fbank
    from sklearn.cluster import AgglomerativeClustering

    folder=Path(folder)
    rows=json.loads((folder/'asr.json').read_text(encoding='utf-8'))['segments']
    if not rows:raise RuntimeError('没有可识别的台词，请换一段有清晰对白的视频。')
    with wave.open(str(folder/'audio.wav'),'rb') as wav:
        if (wav.getframerate(),wav.getnchannels(),wav.getsampwidth())!=(16000,1,2):
            raise RuntimeError('请先准备原视频的音频。')
        samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype=np.int16).astype(np.float32)/32768
    torch.set_num_threads(2)
    options=ort.SessionOptions();options.intra_op_num_threads=2
    session=ort.InferenceSession(str(model),sess_options=options,providers=['CPUExecutionProvider'])
    vectors=[]
    for row in rows:
        clip=samples[max(0,round(row['start']*16000)):round(row['end']*16000)]
        if len(clip)<800:raise RuntimeError('存在过短的台词，请重新提取原文后识别角色。')
        # Repeat very short excerpts without borrowing speech from a neighbouring turn.
        if len(clip)<12800:clip=np.tile(clip,int(np.ceil(12800/len(clip))))[:12800]
        embeddings=[]
        for start in range(0,len(clip),128000):
            part=clip[start:start+128000]
            if len(part)<8000 and embeddings:continue
            features=fbank(torch.from_numpy(part.copy()).unsqueeze(0),num_mel_bins=80,dither=0,sample_frequency=16000)
            features-=features.mean(0,keepdim=True)
            vector=session.run(None,{session.get_inputs()[0].name:features.unsqueeze(0).numpy()})[0].flatten()
            embeddings.append(vector/max(float(np.linalg.norm(vector)),1e-9))
        vector=np.mean(embeddings,axis=0);vectors.append(vector/max(float(np.linalg.norm(vector)),1e-9))
    x=np.stack(vectors)
    labels=AgglomerativeClustering(n_clusters=None,metric='cosine',linkage='average',distance_threshold=.65).fit_predict(x) if len(rows)>1 else [0]
    order=list(dict.fromkeys(int(label) for label in labels))
    if len(order)>12:raise RuntimeError('声音变化较多，未能可靠归为角色。请先截取更短的片段。')
    roles=[];assignments={};confidence={}
    for index,label in enumerate(order):
        indices=[i for i,value in enumerate(labels) if value==label]
        role_id=f'role-{index+1}'
        center=np.mean(x[indices],axis=0);center/=max(float(np.linalg.norm(center)),1e-9)
        representative=max(indices,key=lambda i:float(x[i]@center)*min(2.0,rows[i]['end']-rows[i]['start']))
        roles.append({'id':role_id,'name':f'角色 {index+1}','sample_segment':rows[representative]['id']})
        for i in indices:
            assignments[str(rows[i]['id'])]=role_id
            confidence[str(rows[i]['id'])]=round(float(x[i]@center),3)
    result={'model':'CAM++ · local CosyVoice encoder','roles':roles,'assignments':assignments,'confidence':confidence}
    (folder/'speakers.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    try:
        result=identify(Path(sys.argv[1]),ROOT/'models/CosyVoice3/campplus.onnx')
        print(json.dumps({'roles':len(result['roles'])}))
    except Exception as exc:
        print(str(exc),file=sys.stderr);raise SystemExit(1)
