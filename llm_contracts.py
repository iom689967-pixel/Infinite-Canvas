"""Pure LLM response contracts. No credentials, identity, HTTP or storage effects."""
import json
from dataclasses import dataclass

class ContractError(ValueError):
    def __init__(self,category,*,complete=False):
        self.category,self.complete=category,complete
        super().__init__(category)

@dataclass(frozen=True)
class TextResult:
    text:str
    usage:dict|None=None


def checked_object(data):
    if not isinstance(data,dict):raise ContractError('response_structure')
    if data.get('error'):raise ContractError('business_error',complete=True)
    if 'code' in data and data['code'] not in (None,0,200,'200','0'):raise ContractError('business_error',complete=True)
    if 'data' in data and isinstance(data['data'],dict) and not any(k in data for k in ('choices','candidates')):return checked_object(data['data'])
    return data


def text_content(value):
    if isinstance(value,str):return value
    if isinstance(value,list):
        parts=[]
        for part in value:
            if not isinstance(part,dict):raise ContractError('response_structure')
            if part.get('type')=='refusal':raise ContractError('refused',complete=True)
            text=part.get('text',part.get('content',''))
            if not isinstance(text,str):raise ContractError('response_structure')
            parts.append(text)
        return '\n'.join(parts)
    if value is None:return ''
    raise ContractError('response_structure')


def safe_usage(data):
    raw=data.get('usage') or data.get('usageMetadata')
    if not isinstance(raw,dict):return None
    keys={'prompt_tokens','completion_tokens','total_tokens','input_tokens','output_tokens','promptTokenCount','candidatesTokenCount','totalTokenCount','cachedContentTokenCount','thoughtsTokenCount'}
    result={k:v for k,v in raw.items() if k in keys and type(v) in {int,float} and v>=0}
    return result or None


def parse_response(data):
    data=checked_object(data)
    if data.get('promptFeedback',{}).get('blockReason'):raise ContractError('refused',complete=True)
    if 'candidates' in data:
        candidates=data['candidates']
        if not isinstance(candidates,list) or not candidates or not isinstance(candidates[0],dict):raise ContractError('response_structure')
        item=candidates[0]
        if item.get('finishReason') in {'SAFETY','RECITATION','BLOCKLIST','PROHIBITED_CONTENT'}:raise ContractError('refused',complete=True)
        content=item.get('content',{})
        if not isinstance(content,dict) or not isinstance(content.get('parts'),list):raise ContractError('response_structure')
        text=text_content(content['parts'])
    else:
        choices=data.get('choices')
        if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):raise ContractError('response_structure')
        message=choices[0].get('message')
        if not isinstance(message,dict):raise ContractError('response_structure')
        if message.get('refusal') or choices[0].get('finish_reason')=='content_filter':raise ContractError('refused',complete=True)
        text=text_content(message.get('content'))
    if not text.strip():raise ContractError('empty_result')
    return TextResult(text,safe_usage(data))


class SSEText:
    """OpenAI-compatible data events. EOF is not a terminal event."""
    def __init__(self):
        self.lines=[];self.complete=False;self.text='';self.usage=None
    def line(self,line):
        if line=='':
            if not self.lines:return ''
            data='\n'.join(self.lines);self.lines=[]
            if data.strip()=='[DONE]':self.complete=True;return ''
            try:obj=checked_object(json.loads(data))
            except (json.JSONDecodeError,UnicodeError):raise ContractError('response_parse') from None
            choices=obj.get('choices')
            if choices==[] and isinstance(obj.get('usage'),dict):self.usage=safe_usage(obj);return ''
            if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):raise ContractError('response_structure')
            item=choices[0];delta=item.get('delta',{})
            if not isinstance(delta,dict):raise ContractError('response_structure')
            if delta.get('refusal') or item.get('finish_reason')=='content_filter':raise ContractError('refused',complete=True)
            text=text_content(delta.get('content',''))
            if item.get('finish_reason') in {'stop','length'}:self.complete=True
            elif item.get('finish_reason') is not None:raise ContractError('response_structure')
            self.text+=text
            return text
        if line.startswith('data:'):self.lines.append(line[5:].lstrip(' '))
        elif line.startswith((':','event:','id:','retry:')):pass
        elif line.strip():raise ContractError('response_parse')
        return ''
    def finish(self):
        if self.lines or not self.complete:raise ContractError('incomplete_stream')
        if not self.text.strip():raise ContractError('empty_result',complete=True)
        return TextResult(self.text,self.usage)

# Existing network protocols only. Official RunningHub text traffic has one
# explicit cross-origin contract; custom relays keep their configured origin.
RH_OFFICIAL_HOSTS=frozenset({'runninghub.ai','www.runninghub.ai','api.runninghub.ai','runninghub.cn','www.runninghub.cn','api.runninghub.cn','llm.runninghub.ai'})
RH_TEXT_URL='https://llm.runninghub.ai/v1/chat/completions'

def approved_runninghub_text_target(base,url):
    from urllib.parse import urlsplit
    p=urlsplit(base)
    return (p.scheme=='https' and p.hostname in RH_OFFICIAL_HOSTS and p.port in (None,443)
            and not p.username and not p.password and not p.query and not p.fragment and url==RH_TEXT_URL)

def chat_base(protocol,base):
    from urllib.parse import urlsplit,urlunsplit
    p=urlsplit(base)
    if p.scheme not in {'https','http'} or not p.hostname or p.username or p.password or p.query or p.fragment:raise ContractError('invalid_parameters')
    if protocol=='runninghub' and approved_runninghub_text_target(base,RH_TEXT_URL):return RH_TEXT_URL.removesuffix('/chat/completions')
    path=p.path.rstrip('/')
    if path.endswith('/chat/completions'):path=path.removesuffix('/chat/completions')
    if protocol=='gemini':
        while any(path.endswith(s) for s in ('/v1beta/models','/v1/models','/v1beta','/v1')):
            suffix=next(s for s in ('/v1beta/models','/v1/models','/v1beta','/v1') if path.endswith(s));path=path[:-len(suffix)]
        path+='/v1beta'
    else:
        suffix='/api/v3' if protocol=='volcengine' else '/v1'
        # An explicit alternative version (e.g. /v2) is a user's exact API root.
        import re
        if not path.endswith(suffix) and not re.search(r'/v\d+(?:beta\d*)?$',path):path+=suffix
    return urlunsplit((p.scheme,p.netloc,path,'',''))

def compose_messages(system,history,current,images=(),videos=()):
    import copy
    messages=copy.deepcopy(history)
    if system:messages.insert(0,{'role':'system','content':system})
    content=[{'type':'text','text':current}]
    content.extend({'type':'image_url','image_url':{'url':v}} for v in images)
    content.extend({'type':'video_url','video_url':{'url':v}} for v in videos)
    messages.append({'role':'user','content':content})
    return messages

def gemini_body(messages):
    import mimetypes
    from urllib.parse import urlsplit
    contents=[];system=[]
    for message in messages:
        role=message['role'];content=message['content']
        parts=[]
        for p in content if isinstance(content,list) else [{'type':'text','text':content}]:
            if p.get('type')=='text':parts.append({'text':p['text']});continue
            kind=p.get('type');media=p.get(kind,{})
            url=media.get('url') if isinstance(media,dict) else media
            if kind not in {'image_url','video_url'} or not isinstance(url,str):raise ContractError('invalid_parameters')
            if url.startswith('data:') and ';base64,' in url:
                header,encoded=url.split(';base64,',1);parts.append({'inlineData':{'mimeType':header[5:],'data':encoded}})
            elif url.startswith(('https://','http://')):
                mime=mimetypes.guess_type(urlsplit(url).path)[0] or ('video/mp4' if kind=='video_url' else 'image/png')
                parts.append({'fileData':{'mimeType':mime,'fileUri':url}})
            else:raise ContractError('invalid_parameters')
        if role=='system':system.extend(parts)
        else:contents.append({'role':'model' if role=='assistant' else 'user','parts':parts})
    body={'contents':contents}
    if system:body['systemInstruction']={'parts':system}
    return body

def build_request(protocol,base,model,messages,*,stream=False,max_output_tokens=None,temperature=None):
    import copy,math
    from urllib.parse import quote
    if protocol not in {'openai','gemini','volcengine','runninghub','apimart'}:raise ContractError('invalid_parameters')
    if not isinstance(model,str) or not model or any(ord(c)<32 for c in model):raise ContractError('invalid_parameters')
    root=chat_base(protocol,base)
    if protocol=='gemini':
        # 'models/' is a Gemini resource prefix, not a fallback/model alias.
        resource=model.removeprefix('models/')
        url=root+'/models/'+quote(resource,safe='')+':generateContent';body=gemini_body(messages)
        params=body.setdefault('generationConfig',{}) if max_output_tokens is not None or temperature is not None else None
    else:
        url=root+'/chat/completions';body={'model':model,'messages':copy.deepcopy(messages)};params=body
        if stream:body['stream']=True
        elif protocol=='apimart':body['stream']=False
    if max_output_tokens is not None:
        if type(max_output_tokens) is not int or max_output_tokens<=0:raise ContractError('invalid_parameters')
        params['maxOutputTokens' if protocol=='gemini' else 'max_tokens']=max_output_tokens
    if temperature is not None:
        if type(temperature) not in {int,float} or not math.isfinite(temperature):raise ContractError('invalid_parameters')
        params['temperature']=temperature
    return url,body
