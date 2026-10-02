"""One operation per isolated process: parent enforces 8s timeout, no retry."""
import json
import sys
from .native import Native

def main():
    operation=None
    try:
        raw=sys.stdin.buffer.readline(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('Request too large')
        request=json.loads(raw);operation=request['operation'];native=Native()
        if operation=='windows':result={'windows':native.backend.windows()}
        elif operation=='snapshot':result=native.snapshot(**request['arguments'])
        elif operation=='action':result=native.action(**request['arguments'])
        else:raise ValueError('Unknown operation')
    except Exception as exc:
        result={'error':str(exc),'state':'unknown' if operation=='action' else 'rejected','do_not_replay':operation=='action'}
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf-8'));sys.stdout.buffer.flush()

if __name__=='__main__':main()
