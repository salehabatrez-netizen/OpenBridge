"""Hot toggle and independent-client integration, using harmless fake stdio MCP."""
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import Mock, patch
from computer_use import ComputerUseManager
from bridge import McpHandler, ThreadedHTTPServer, WorkspaceTools, DEFAULT_PROMPT_TEMPLATE

FAKE = '''import sys,json
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r: continue
 method=r['method']
 if method=='initialize': result={'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'fake','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'browser_snapshot','inputSchema':{'type':'object','properties':{}}}]}
 else: result={'content':[{'type':'text','text':'ready'},{'type':'image','mimeType':'image/png','data':'YWJj'}],'isError':False}
 print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)
'''

class HotTests(unittest.TestCase):
    def test_toggle_all_and_clear_cache(self):
        m=ComputerUseManager()
        child=Mock();m.children['browser']=child;m.tools['browser']={'old':{}}
        m.set_enabled(True)
        self.assertTrue(all(m.enabled.values()))
        self.assertEqual(m.children,{})
        self.assertEqual(m.tools,{})
        child.close.assert_called_once()
        m.set_enabled(False);self.assertFalse(any(m.enabled.values()))
        m.set_enabled(True);self.assertTrue(all(m.enabled.values()))
        m.shutdown()

    def test_shutdown_cannot_be_revived(self):
        m=ComputerUseManager();m.shutdown()
        with self.assertRaises(RuntimeError):m.set_enabled(True)
        self.assertFalse(any(m.enabled.values()))

    def test_stale_discovery_rejected(self):
        m=ComputerUseManager(browser=True)
        old=Mock();old.request.return_value={'tools':[{'name':'browser_snapshot'}]}
        m.children['browser']=Mock()
        with patch.object(m,'_client',return_value=old):
            with self.assertRaises(PermissionError):m.list_tools('browser')
        self.assertNotIn('browser',m.tools)
        m.stop()

    def test_prompt_self_contained(self):
        prompt=DEFAULT_PROMPT_TEMPLATE.format(url='https://example.test/mcp/secret',workspace='C:/project')
        for marker in ['initialize','tools/list','computer_use_status','computer_use_tools','computer_use_call','blender','application/json']:
            self.assertIn(marker,prompt)
        self.assertIn('https://example.test/mcp/secret',prompt)

    def test_two_new_clients_and_hot_restart_same_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'fake.py';script.write_text(FAKE)
            manager=ComputerUseManager(directory=directory)
            old_state=(McpHandler.tools,McpHandler.computer_use,McpHandler.secret)
            McpHandler.tools=WorkspaceTools(directory)
            McpHandler.computer_use=manager
            McpHandler.secret=secrets.token_hex(16)
            server=ThreadedHTTPServer(('127.0.0.1',0),McpHandler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url='http://127.0.0.1:%d/mcp/%s' % (server.server_port,McpHandler.secret)
            def rpc(client,method,params=None,notification=False):
                body={'jsonrpc':'2.0','method':method,'params':params or {}}
                if not notification:body['id']=client+'-'+secrets.token_hex(3)
                request=urllib.request.Request(url,data=json.dumps(body).encode(),headers={
                    'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
                # Each request creates a new connection; no shared session id/cookie/history.
                with urllib.request.urlopen(request,timeout=10) as response:
                    self.assertIsNone(response.headers.get('mcp-session-id'))
                    return json.load(response)['result'] if response.status!=202 else None
            def tool(client,name,args={}):return rpc(client,'tools/call',{'name':name,'arguments':args})
            try:
                with patch.object(manager,'_command',return_value=([sys.executable,'-u',str(script)],None)):
                    for client in ('new-chat-A','new-chat-B'):
                        init=rpc(client,'initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':client,'version':'1'}})
                        self.assertIn('stateless',init['instructions'])
                        rpc(client,'notifications/initialized',notification=True)
                        self.assertEqual(len(rpc(client,'tools/list')['tools']),17)
                    self.assertTrue(tool('new-chat-A','computer_use_tools',{'target':'browser'})['isError'])
                    manager.set_enabled(True)
                    schema=tool('new-chat-B','computer_use_tools',{'target':'browser'})
                    self.assertFalse(schema['isError'])
                    first=manager.children['browser'].process
                    args={'target':'browser','name':'browser_snapshot','arguments':{}}
                    self.assertEqual(tool('new-chat-A','computer_use_call',args)['content'][1]['type'],'image')
                    manager.set_enabled(False)
                    self.assertIsNotNone(first.poll())
                    self.assertTrue(tool('new-chat-B','computer_use_call',args)['isError'])
                    manager.set_enabled(True)
                    self.assertFalse(tool('new-chat-B','computer_use_tools',{'target':'browser'})['isError'])
                    self.assertIsNot(manager.children['browser'].process,first)
                    self.assertEqual(tool('new-chat-B','computer_use_call',args)['content'][1]['data'],'YWJj')
                    # No HTTP/tunnel restart was performed anywhere above.
                    self.assertTrue(thread.is_alive())
                    self.assertEqual(len(rpc('new-chat-C','tools/list')['tools']),17)
            finally:
                manager.shutdown();server.shutdown();server.server_close();thread.join(timeout=2)
                McpHandler.tools,McpHandler.computer_use,McpHandler.secret=old_state

if __name__=='__main__':unittest.main()
