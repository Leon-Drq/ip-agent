from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Validated screenplay and prompts for fresh creation, separate from source editing."""
import json
import re
from pathlib import Path
from pydantic import BaseModel, Field, model_validator
from settings import ROOT

VISION_MODEL = ROOT/'models/Qwen3-VL-4B'
VISION_PYTHON = venv_python('.visionvenv')

def readiness():
    files=[VISION_PYTHON,VISION_MODEL/'source.json',VISION_MODEL/'model-00001-of-00002.safetensors',VISION_MODEL/'model-00002-of-00002.safetensors']
    return {'ready':all(p.is_file() for p in files),'model':'Qwen3-VL · 4B',
            'missing':[] if all(p.is_file() for p in files) else ['需单独安装 Qwen3-VL 引擎，或连接已有工作室']}

class StoryCharacter(BaseModel):
    id: str = Field(pattern=r'^character-[1-4]$')
    name: str = Field(min_length=1,max_length=40)
    appearance: str = Field(default='',max_length=800)
    wardrobe: str = Field(default='',max_length=400)
    photo_id: str = ''
    source_role_id: str = ''

class StoryShot(BaseModel):
    index: int = Field(ge=0,le=39)
    seconds: int = Field(ge=4,le=15)
    camera: str = Field(default='中景，固定镜头',max_length=400)
    action: str = Field(min_length=1,max_length=1600)
    character_ids: list[str] = Field(default_factory=list,max_length=4)
    dialogue: str = Field(default='',max_length=1600)
    look_start: str = Field(default='',max_length=1200)
    look_end: str = Field(default='',max_length=1200)

class Screenplay(BaseModel):
    summary: str = Field(min_length=1,max_length=2000)
    setting: str = Field(default='',max_length=1400)
    characters: list[StoryCharacter] = Field(min_length=1,max_length=4)
    shots: list[StoryShot] = Field(min_length=1,max_length=40)

    @model_validator(mode='after')
    def references(self):
        ids=[c.id for c in self.characters]
        if len(ids)!=len(set(ids)):raise ValueError('角色编号不能重复')
        if [s.index for s in self.shots]!=list(range(len(self.shots))):raise ValueError('镜头编号必须连续')
        if any(set(s.character_ids)-set(ids) for s in self.shots):raise ValueError('镜头引用了不存在的角色')
        if sum(s.seconds for s in self.shots)>240:raise ValueError('新视频总时长请控制在 4 分钟内')
        return self

def parse_json(text):
    start=text.find('{');end=text.rfind('}')
    if start<0 or end<start:raise ValueError('视频理解未返回完整剧本，请重试。')
    return json.loads(text[start:end+1])

def grounded_shot(row, source, characters):
    """Keep ASR words/order authoritative; a visual model only assigns speakers."""
    names={c['id']:c['name'] for c in characters}
    known={c.get('source_role_id'):c['name'] for c in characters if c.get('source_role_id')}
    predicted=row.get('dialogue_speakers',[])
    if not isinstance(predicted,list):predicted=[]
    lines=[]
    utterances=source.get('utterances') or ([{'text':source['text']}] if source.get('text') else [])
    for i,item in enumerate(utterances):
        speaker=known.get(item.get('source_role_id')) or item.get('source_name')
        if not speaker and i<len(predicted):speaker=names.get(predicted[i])
        lines.append((speaker+'：' if speaker else '')+item['text'])
    return {**row,'index':source['index'],'seconds':source['seconds'],'dialogue':'\n'.join(lines)}

def plan_shots(p):
    return p['screenplay']['shots'] if p.get('edit_mode')=='imitate' else p['analysis']['shots']

def plan_text(p):
    names=[c['name'] for c in p['screenplay']['characters']]+[c['id'] for c in p['screenplay']['characters']]
    pattern=r'(?m)^\s*(?:'+ '|'.join(re.escape(n) for n in names)+r')\s*[：:]\s*'
    return re.sub(pattern,'','\n'.join(s['dialogue'] for s in p['screenplay']['shots'] if s.get('dialogue')))

def cast_text(p):
    return '角色名称只用于对应台词，不决定性别、发型或服装；人物照片与明确的服装设定优先于名称。\n'+'\n'.join(f"{c['id']}（台词标签：{c['name']}）：{c.get('appearance','')}；基础服装：{c.get('wardrobe') or '沿用统一定妆图中的服装和配饰'}。"
                     for c in p['screenplay']['characters'])


def look_text(shot):
    return ('\n本镜头开始时的造型：'+(shot.get('look_start') or '按本镜头动作描述确定，未说明变化时沿用上一镜结束状态')+
            '\n本镜头结束时的造型：'+(shot.get('look_end') or '按本镜头动作描述确定，未说明变化时保持本镜头开始状态'))

def location_text(p):
    if p.get('background_id'):
        return '以门店背景参考照片为场景依据，沿用其中的装修、色彩、布局和光线；背景照片中的人物不作为演员。'+p.get('imitation_scene','')
    return p.get('imitation_scene') or p['screenplay']['setting']

def master_prompt(p,roles):
    return ('拍摄一张真实手机视频质感的完整场景定妆照，背景清晰，皮肤自然，避免过度磨皮。'
            '所有下列角色同时出现在同一个门店场景中，人物之间有明确空间距离，脸部清楚可辨。'
            '这是一幅新拍摄的画面。上传人物照片决定对应角色身份、五官、身形与真实年龄。'
            '身份保持一致；发型、假发、妆容和服装是剧情状态，按镜头变化，不能把最终造型提前放到开场。'
            '这张定妆照只呈现剧本开场已有的造型，不要提前执行后续镜头的戴假发、化妆或换装；基础服装以角色设定为准。\n角色设定：\n'+cast_text(p)+
            '\n故事开场造型：'+(p['screenplay']['shots'][0].get('look_start') or p['screenplay']['shots'][0]['action'])+
            '\n场景：'+location_text(p)+'\n参考图顺序：'+roles+'\n拍摄要求：'+p.get('instructions','')+
            '\n人物照片对应的角色保持照片中的真实人物外貌，不沿用原角色的动物形态。不要额外字幕、水印、画中画、角色标签或拼贴。')

def shot_prompt(p,shot,image=False):
    if shot.get('look_start'):
        # A full timeline plus an opening-state master repeatedly made the image
        # model draw the opening hairstyle in later shots. Send this shot only.
        identities='\n'.join(f"{c['id']}（台词标签：{c['name']}）：脸、身形与真实年龄沿用参考分镜或定妆图中的对应人物。"
            for c in p['screenplay']['characters'] if c['id'] in shot['character_ids'])
        prompt=('当前镜头的明确造型优先于参考图的发型与姿态。\n本镜头开始时的造型：'+shot['look_start']+
            '\n景别：'+shot['camera']+'\n角色身份：'+identities+
            '\n角色名称只用于台词对应，不决定性别或服装。保留同一张脸、身形、真实年龄与未指定改变的衣服。'
            '\n本镜头造型状态和动作优先于定妆图；参考图提供身份和门店，不要求复制其中的开场发型或手中物品。'
            '\n门店：'+location_text(p))
        if image:
            return (prompt+'\n本镜头构图、人物位置与道具参考：'+shot['action']+
                '\n只拍摄本镜头开始状态的一张真实手机视频首帧。再次确认这张图片中的造型：'+shot['look_start']+
                '\n不要执行其他镜头的剧情，不要字幕、水印或拼贴。')
        return (prompt+'\n本镜头结束时的造型：'+(shot.get('look_end') or shot['look_start'])+
            '\n本镜头动作：'+shot['action']+'\n严格按角色及顺序说这些台词：'+shot['dialogue']+
            f"\n在 {shot['seconds']} 秒内自然连贯完成动作，同一演员不可换脸、改变年龄。"+
            ('每个角色全片保持同一音色。' if p['voice_mode']=='native' else '不生成额外声音，声音由后期配入。')+
            '\n不生成字幕、水印或界面。')
    all_cast=cast_text(p)
    visible='、'.join(shot['character_ids']) or '本镜头不出现人物'
    return ('依据以下分镜拍摄全新的故事画面。统一定妆图负责全部角色身份、基础服装、门店布局与光线，'
            '人物照片决定对应人物的五官、身形、真实年龄与性别。跨镜头保持同一张脸和同一位演员，不能中途变回其他人。'
            '发型、假发、妆容和服装允许随剧情有明确变化。本镜头造型状态和动作优先于定妆图及人物照片中的造型。'
            '未说明变化的外观保持一致；不能提前展示改造后的结果，不能把变美表现成换脸或改变真实年龄。'
            '\n全部固定角色：\n'+all_cast+'\n本镜头出场：'+visible+'。只出现本镜头指定的角色，其他角色不入画。\n门店：'+location_text(p)+
            '\n景别与镜头：'+shot['camera']+'\n人物动作和互动：'+shot['action']+
            look_text(shot)+
            '\n本镜头台词（按角色和顺序表演）：'+shot['dialogue']+
            ('\n生成本镜头开始时的自然画面，表演尚未完成。' if image else
             f"\n在 {shot['seconds']} 秒内完成一个连贯镜头，动作清楚、自然，禁止插入其他演员或其他地点。"+
             ('只说上述台词，角色对应声音整片一致。' if p['voice_mode']=='native' else
              '自然表演台词对应的口部动作，声音由后期配入，不生成额外对白或音乐。'))+
            '\n补充要求：'+p.get('instructions','')+'\n不生成字幕、水印或界面。')
