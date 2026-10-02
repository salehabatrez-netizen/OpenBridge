"""End-to-end HTTP -> OpenBridge -> real browser stdio MCP -> image response.
Uses loopback on a random port, a temporary secret, and an isolated about:blank browser.
"""
import json
import secrets
import tempfile
import threading
import urllib.request
from bridge import McpHandler, ThreadedHTTPServer, WorkspaceTools
from computer_use import ComputerUseManager

def main():
    manager=ComputerUseManager(browser=True)
    with tempfile.TemporaryDirectory() as directory:
        # This script is a separate process; it does not mutate the active GUI's handler.
        McpHandler.tools=WorkspaceTools(directory)
        McpHandler.computer_use=manager
        McpHandler.secret=secrets.token_hex(16)
        server=ThreadedHTTPServer(('127.0.0.1',0),McpHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        url='http://127.0.0.1:%d/mcp/%s' % (server.server_port,McpHandler.secret)
        def rpc(method,params=None):
            data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params or {}}).encode()
            req=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
            with urllib.request.urlopen(req,timeout=90) as response:
                return json.load(response)['result']
        def tool(name,args):
            result=rpc('tools/call',{'name':name,'arguments':args})
            assert not result.get('isError'),result
            return result
        try:
            assert len(rpc('tools/list')['tools'])==17
            denied=rpc('tools/call',{'name':'computer_use_tools','arguments':{'target':'desktop'}})
            assert denied['isError']
            tools=json.loads(tool('computer_use_tools',{'target':'browser'})['content'][0]['text'])
            print('HTTP browser tools:',len(tools['tools']),flush=True)
            tool('computer_use_call',{'target':'browser','name':'browser_navigate','arguments':{'url':'about:blank'}})
            result=tool('computer_use_call',{'target':'browser','name':'browser_take_screenshot','arguments':{'scale':'css'}})
            images=[c for c in result['content'] if c['type']=='image']
            assert images and len(images[0]['data'])>100
            print('HTTP image blocks:',len(images),'base64 length:',len(images[0]['data']),flush=True)
            tool('computer_use_call',{'target':'browser','name':'browser_close','arguments':{}})
            manager.stop()
            assert rpc('tools/call',{'name':'computer_use_tools','arguments':{'target':'browser'}})['isError']
            # Local hot enable, without restarting HTTP or changing the URL.
            manager.set_enabled(True)
            init=rpc('initialize',{'protocolVersion':'2024-11-05','capabilities':{},
                'clientInfo':{'name':'independent-new-chat','version':'1'}})
            assert 'stateless' in init['instructions']
            assert len(rpc('tools/list')['tools'])==17
            fresh=json.loads(tool('computer_use_tools',{'target':'browser'})['content'][0]['text'])
            assert fresh['tools']
            tool('computer_use_call',{'target':'browser','name':'browser_navigate','arguments':{'url':'about:blank'}})
            result=tool('computer_use_call',{'target':'browser','name':'browser_take_screenshot','arguments':{'scale':'css'}})
            assert any(c['type']=='image' for c in result['content'])
            tool('computer_use_call',{'target':'browser','name':'browser_close','arguments':{}})
            print('HTTP LIVE HOT-TOGGLE + NEW-CLIENT IMAGE PASS',flush=True)
        finally:
            manager.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

if __name__=='__main__':main()
