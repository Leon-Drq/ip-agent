"""Local, time-aligned dialogue replacement. Unselected PCM samples stay unchanged."""
import wave
from pathlib import Path
from remix_media import ffmpeg, probe


def selected_groups(rows, selected):
    groups=[]
    for row in rows:
        if row['id'] not in selected:
            continue
        if groups and row['id']==groups[-1]['ids'][-1]+1 and 0<=row['start']-groups[-1]['end']<=.12:
            groups[-1]['end']=row['end']
            groups[-1]['text']+='，'+row['text']
            groups[-1]['ids'].append(row['id'])
        else:
            groups.append({**row,'ids':[row['id']]})
    return groups


def replace_dialogue(source, entries, folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    original=folder/'original.wav'
    ffmpeg('-i',source,'-vn','-ar',48000,'-ac',2,'-c:a','pcm_s16le',original)
    with wave.open(str(original),'rb') as wav:
        params=wav.getparams(); pcm=bytearray(wav.readframes(wav.getnframes()))
    width=params.nchannels*params.sampwidth
    for index,(row,audio) in enumerate(entries):
        start=round(row['start']*params.framerate);end=round(row['end']*params.framerate)
        if not 0<=start<end<=params.nframes:raise RuntimeError('台词片段时间超出原视频。')
        duration=(end-start)/params.framerate
        length=float(probe(audio)['format']['duration']);ratio=length/duration
        tempo=[]
        while ratio>2:tempo.append('atempo=2');ratio/=2
        while ratio<.5:tempo.append('atempo=0.5');ratio/=.5
        tempo.append(f'atempo={ratio:.9f}')
        fade=min(.005,duration/4)
        filters=','.join([*tempo,'apad',f'atrim=duration={duration:.9f}',
            f'afade=t=in:d={fade}',f'afade=t=out:st={duration-fade:.9f}:d={fade}'])
        fitted=folder/f'fitted-{index:03d}.wav'
        ffmpeg('-i',audio,'-af',filters,'-ar',48000,'-ac',2,'-c:a','pcm_s16le',fitted)
        with wave.open(str(fitted),'rb') as wav:data=wav.readframes(end-start)
        count=(end-start)*width
        pcm[start*width:end*width]=data[:count].ljust(count,b'\0')
    output=folder/'voice.wav'
    with wave.open(str(output),'wb') as wav:
        wav.setparams(params);wav.writeframes(pcm)
    return output
