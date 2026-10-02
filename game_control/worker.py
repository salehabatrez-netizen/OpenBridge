"""Private stdio broker: no TCP listener; stdin EOF and shutdown release inputs."""
import json
import sys
import threading
import time
from .engine import Controller, ControlError, integer, MAX_SETTLE_MS
from .schema import TOOLS, RUNTIME_INFO, CONTROL_TOOLS
from .windows import WindowsBackend
from . import trust


def call_game_tool(controller, name, arguments):
    """Validate capture options before input; a capture failure never replays an action."""
    methods={'GameWindows':controller.windows,'GameRequestControl':controller.request,
             'GameControlStatus':controller.status,'GameFocus':controller.focus,'GameObserve':controller.observe,
             'GameAct':controller.act,'GameActionStatus':controller.action_status,'GameRelease':controller.release}
    if name not in methods:raise ControlError('NOT_ALLOWED','Unknown input tool')
    started = time.monotonic()
    arguments=dict(arguments)
    if name=='GameAct':
        size=integer(arguments.pop('observation_max_size',1280),320,1920,'observation_max_size')
        settle=integer(arguments.pop('observation_settle_ms',100),0,MAX_SETTLE_MS,'observation_settle_ms')
    data=methods[name](**arguments);png=data.pop('png',None)
    if name=='GameControlStatus':data['runtime']=dict(RUNTIME_INFO)
    if name=='GameAct' and data['state']=='completed' and not data.get('deduplicated'):
        try:
            observation=controller.observe(arguments['lease_id'],max_size=size,settle_ms=settle)
            png=observation.pop('png');data['observation']=observation['metadata']
        except Exception as exc:
            data['observation_error']=str(exc)
            data['observation_error_code']=getattr(exc,'code','CAPTURE_ERROR')
            data['do_not_replay']=True
    if name=='GameAct':
        data['tool_elapsed_ms']=round((time.monotonic()-started)*1000,3)
    content=[{'type':'text','text':json.dumps(data,ensure_ascii=False)}]
    if png:content.append({'type':'image','mimeType':'image/png','data':png})
    return {'content':content,'isError':data.get('state') in ('aborted','fault') or 'observation_error' in data}


def main():
    output_lock=threading.Lock();jobs=threading.BoundedSemaphore(4)
    status_jobs=threading.BoundedSemaphore(4)
    release_jobs=threading.BoundedSemaphore(1)
    def send(obj):
        with output_lock:
            sys.stdout.write(json.dumps(obj,ensure_ascii=False)+'\n');sys.stdout.flush()
    def notify(method,params):send({'jsonrpc':'2.0','method':method,'params':params})
    controller=Controller(WindowsBackend(),notify,policy=trust.policy())
    def execute(request, admission):
        try:
            params=request.get('params',{});method=request['method']
            if method=='initialize':result={'protocolVersion':'2024-11-05','capabilities':{'tools':{},'experimental':{'openbridge/game-control':dict(RUNTIME_INFO)}},'serverInfo':{'name':'OpenBridge-BoundedInput','version':RUNTIME_INFO['version']}}
            elif method=='tools/list':result={'tools':TOOLS,'_meta':{'openbridge/game-control':dict(RUNTIME_INFO)}}
            elif method=='control/approve':result=controller.approve(**params)
            elif method=='control/state':result=controller.status()
            elif method=='tools/call':
                result=call_game_tool(controller,params['name'],params.get('arguments',{}))
            else:raise ControlError('NOT_ALLOWED','Unknown private broker method')
            send({'jsonrpc':'2.0','id':request['id'],'result':result})
        except Exception as exc:
            data={'error_code':getattr(exc,'code','INVALID_REQUEST'),'error':str(exc)}
            send({'jsonrpc':'2.0','id':request['id'],'result':{'content':[{'type':'text','text':json.dumps(data)}],'isError':True}})
        finally:admission.release()
    try:
        while True:
            line=sys.stdin.buffer.readline(1024*1024+1)
            if not line:break
            if len(line)>1024*1024:break
            request=json.loads(line)
            if request.get('method') in ('control/revoke','control/shutdown'):
                result=controller.revoke('Local permission revoked')
                notify('control/released',result)
                if request['method']=='control/shutdown':break
                continue
            if 'id' not in request:continue
            params=request.get('params',{})
            name=params.get('name') if isinstance(params,dict) and request.get('method')=='tools/call' else None
            # A slow capture or several status reads cannot consume release capacity.
            admission=release_jobs if name=='GameRelease' else status_jobs if name in CONTROL_TOOLS or request.get('method')=='control/state' else jobs
            if not admission.acquire(False):
                send({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32000,'message':'Broker lane busy'}});continue
            threading.Thread(target=execute,args=(request,admission),daemon=True).start()
    finally:
        result=controller.close()
        try:notify('control/released',result)
        except (BrokenPipeError,OSError):pass

if __name__=='__main__':main()
