"""Parent-side receipts survive disposable worker timeout and local OFF/ON."""
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from .schema import NAMES

class BackgroundError(Exception): pass

def check(ok, message):
    if not ok: raise BackgroundError(message)

def int_range(value, low, high): return type(value) is int and low <= value <= high

def content(data):
    data=dict(data); image=data.pop('image',None)
    blocks=[{'type':'text','text':json.dumps(data,ensure_ascii=False)}]
    if image: blocks.append({'type':'image',**image})
    return {'content':blocks,'isError':data.get('state') in ('unknown','rejected') or bool(data.get('error'))}

class BackgroundService:
    def __init__(self, directory, allowed, clock=time.monotonic):
        self.directory=Path(directory);self.allowed=allowed;self.clock=clock
        self.prefix=secrets.token_hex(12)+'_';self.snapshots={};self.receipts={}
        self.lock=threading.RLock();self.process_lock=threading.Lock();self.process=None;self.generation=0

    def stop(self):
        with self.process_lock:
            self.generation+=1
            if self.process is not None and self.process.poll() is None:
                try:self.process.kill()
                except OSError:
                    # Revocation must still reach the other adapters. Worker output
                    # is rejected by generation/permission checks even if kill fails.
                    pass
        # No input is held by us; an application may have received a partial message pair.
        # Keep receipts: re-enable is not permission to replay unknown actions.

    def _run(self, operation, arguments):
        executable=self.directory/'.computer-use/windows/Scripts/python.exe'
        check(executable.is_file(),'Background dependencies missing; install locally')
        with self.process_lock:
            check(self.allowed(),'Computer Use disabled locally')
            generation=self.generation
            arguments=dict(arguments)
            expected=arguments.pop('_generation',generation)
            check(expected==generation,'Authorization generation changed; old snapshot rejected')
            p=subprocess.Popen([str(executable),'-u','-m','background_control.worker'],cwd=self.directory,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.process=p
        try:
            try:
                output,_=p.communicate(json.dumps({'operation':operation,'arguments':arguments}).encode('utf-8'),timeout=8)
            except subprocess.TimeoutExpired:
                p.kill();p.communicate();raise BackgroundError('WORKER_TIMEOUT: outcome unknown; no retry')
            check(len(output)<=12*1024*1024,'Oversized worker response')
            check(p.returncode==0,'Worker stopped; outcome unknown')
            result=json.loads(output)
            check(isinstance(result,dict),'Invalid worker response')
            check(generation==self.generation and self.allowed(),'Permission revoked; outcome unknown')
            return result
        finally:
            with self.process_lock:
                if self.process is p:self.process=None
            for stream in (p.stdin,p.stdout,p.stderr):
                if stream and not stream.closed:stream.close()

    def call(self,name,args):
        check(self.allowed(),'Computer Use disabled locally')
        check(name in NAMES and isinstance(args,dict),'Invalid tool or arguments')
        if name=='BackgroundActionStatus':
            check(set(args)=={'action_id'} and isinstance(args['action_id'],str),'Invalid status arguments')
            with self.lock:return content(dict(self.receipts.get(args['action_id'],{}).get('result',{'state':'unknown','do_not_replay':True}),session_prefix=self.prefix))
        with self.lock:
            if name=='BackgroundWindows':
                check(not args,'Unexpected arguments');return content(self._run('windows',{}))
            if name=='BackgroundSnapshot':return self.snapshot(args)
            return self.action(args)

    def snapshot(self,args):
        check(not set(args)-{'window_id','capture','format','max_size','marks'},'Unexpected snapshot arguments')
        check(int_range(args.get('window_id'),1,2**63-1),'Invalid window_id')
        check(type(args.get('capture',False)) is bool,'Invalid capture')
        check(args.get('format','png') in ('png','jpeg'),'Invalid image format')
        check(int_range(args.get('max_size',1280),320,1920),'Invalid max_size')
        marks=args.get('marks',[])
        check(isinstance(marks,list) and len(marks)<=16 and all(isinstance(p,list) and len(p)==2 and all(int_range(n,0,32767) for n in p) for p in marks),'Invalid marks')
        check(not marks or args.get('capture',False),'marks require capture=true')
        began=self.clock();generation=self.generation;result=self._run('snapshot',args)
        if result.get('error'):return content(result)
        elements=result.pop('_elements');window=result['window']
        ident=secrets.token_hex(16)
        self.snapshots={k:v for k,v in self.snapshots.items() if began-v['time']<=30}
        check(len(self.snapshots)<64,'Snapshot capacity reached; wait for expiration')
        self.snapshots[ident]={'time':began,'generation':generation,'window':window,'elements':elements,'consumed':False}
        return content(dict(result,snapshot_id=ident,session_prefix=self.prefix,expires_in_seconds=max(0,round(30-(self.clock()-began),2))))

    def action(self,args):
        check(not set(args)-{'snapshot_id','element_id','action_id','action','text','x','y','button'},'Unexpected action arguments')
        action_id=args.get('action_id');check(isinstance(action_id,str) and re.fullmatch(re.escape(self.prefix)+r'[A-Za-z0-9_-]{1,64}',action_id),'Use current snapshot session_prefix + unique suffix')
        digest=hashlib.sha256(json.dumps(args,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        previous=self.receipts.get(action_id)
        if previous:
            check(previous['digest']==digest,'ACTION_ID_CONFLICT: same ID, different arguments')
            return content(dict(previous['result'],deduplicated=True))
        check(len(self.receipts)<1024,'Receipt capacity reached; no eviction/replay. Restart locally after inspection.')
        snap=self.snapshots.get(args.get('snapshot_id',''));check(snap is not None,'Unknown snapshot; observe again')
        check(snap['generation']==self.generation,'Authorization changed; old snapshot rejected')
        check(not snap['consumed'],'Snapshot consumed; observe again, do not replay prior action')
        action=args.get('action');age=5 if action=='message_click' else 30
        check(self.clock()-snap['time']<=age,'Snapshot expired; observe again')
        element=snap['elements'].get(args.get('element_id',''));check(element is not None,'Unknown snapshot element')
        check(action in element['actions'],'Unsupported action; no foreground fallback')
        if action=='set_value':check(isinstance(args.get('text'),str) and len(args['text'])<=10000,'set_value requires <=10000 characters')
        else:check('text' not in args,'text only allowed for set_value')
        if action=='message_click':
            check(int_range(args.get('x'),0,32767) and int_range(args.get('y'),0,32767),'Click requires target-client x/y')
            check(args.get('button','left') in ('left','right','middle'),'Invalid button')
        else:check(not set(args)&{'x','y','button'},'Coordinates only allowed for explicit message_click')
        receipt={'state':'unknown','action_id':action_id,'do_not_replay':True}
        self.receipts[action_id]={'digest':digest,'result':receipt};snap['consumed']=True
        try:
            result=self._run('action',{'_generation':snap['generation'],'window':snap['window'],'element':element,'action':action,
                                     **{k:args[k] for k in ('text','x','y','button') if k in args}})
            receipt.update(result)
        except Exception as exc:receipt.update(error=str(exc),state='unknown')
        return content(receipt)
