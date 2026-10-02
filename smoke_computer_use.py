"""Explicit local smoke test. Discovers desktop tools; opens only isolated about:blank."""
import json
from computer_use import ComputerUseManager

def main():
    manager = ComputerUseManager(desktop=True, browser=True)
    try:
        for target in ('desktop','browser'):
            try:
                info = manager.list_tools(target)
                print(target, 'tools:', [t['name'] for t in info['tools']], flush=True)
                if target == 'desktop':
                    wait_tool = next(t for t in info['tools'] if t['name']=='Wait')
                    print('desktop Wait schema:', json.dumps(wait_tool['inputSchema']), flush=True)
                    args = {key: 1 for key in wait_tool['inputSchema'].get('required', [])}
                    reply = manager.call_tool('desktop','Wait',args)
                    if reply.get('isError'):
                        raise RuntimeError('Desktop Wait call failed: ' + str(reply))
                    print('desktop Wait: PASS', flush=True)
                if not info['tools']:
                    raise RuntimeError('No allowlisted tools discovered')
                if target == 'browser':
                    r = manager.call_tool('browser','browser_navigate',{'url':'about:blank'})
                    print('browser navigate:', json.dumps(r, ensure_ascii=True), flush=True)
                    if r.get('isError'):
                        raise RuntimeError('Browser navigation failed')
                    r = manager.call_tool('browser','browser_take_screenshot',{})
                    print('browser screenshot blocks:', [(x.get('type'),len(x.get('data',''))) for x in r.get('content',[])], flush=True)
                    if r.get('isError'):
                        raise RuntimeError('Screenshot failed')
                    manager.call_tool('browser','browser_close',{})
            except Exception as exc:
                print(target,'FAILED:',str(exc), flush=True)
                child = manager.children.get(target)
                if child:
                    print('stderr:',list(child.errors),flush=True)
                raise
    finally:
        manager.stop()
    print('LIVE SMOKE PASS',flush=True)

if __name__=='__main__':
    main()
