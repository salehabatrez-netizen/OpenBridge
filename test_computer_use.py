"""Offline unit and protocol tests; never operate the desktop."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from computer_use import ComputerUseManager, StdioMCP
from bridge import BridgeService, McpHandler, WorkspaceTools

FAKE = '''import sys,json
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r: continue
 m=r['method']
 if m=='hang': continue
 result={'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'fake','version':'1'}} if m=='initialize' else {'content':[{'type':'image','mimeType':'image/png','data':'YWJj'}]}
 print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)
'''

class AdapterTests(unittest.TestCase):
    def test_disabled_never_spawns(self):
        m = ComputerUseManager()
        with patch('computer_use.subprocess.Popen') as popen:
            for target in ('desktop','browser'):
                with self.assertRaises(PermissionError): m.list_tools(target)
            popen.assert_not_called()

    def test_no_auth_cannot_enable(self):
        with self.assertRaises(ValueError): BridgeService(require_auth=False,enable_desktop=True)

    def test_unknown_target(self):
        with self.assertRaises(ValueError): ComputerUseManager().list_tools('../evil')

    def test_allowlist_filters_dangerous_tools(self):
        m=ComputerUseManager(browser=True)
        child=Mock()
        child.request.return_value={'tools':[{'name':'browser_snapshot'},{'name':'browser_evaluate'}]}
        m.children['browser']=child
        with patch.object(m,'_client',return_value=child):
            self.assertEqual([t['name'] for t in m.list_tools('browser')['tools']],['browser_snapshot'])
            with self.assertRaises(PermissionError): m.call_tool('browser','browser_evaluate',{})

    def test_images_and_error_flags_preserved(self):
        m=ComputerUseManager(desktop=True)
        m.tools['desktop']={'Screenshot':{}}
        result={'content':[{'type':'image','mimeType':'image/png','data':'YWJj'}],'isError':False}
        child=Mock();child.request.return_value=result
        m.children['desktop']=child
        with patch.object(m,'_client',return_value=child):
            self.assertIs(m.call_tool('desktop','Screenshot',{}),result)

    def test_stop_revokes_all(self):
        m=ComputerUseManager(browser=True,desktop=True)
        child=Mock();m.children['desktop']=child
        m.stop()
        self.assertFalse(any(m.enabled.values()))
        child.close.assert_called_once()
        with self.assertRaises(PermissionError): m.list_tools('desktop')

    def test_browser_isolated_and_telemetry_disabled(self):
        m=ComputerUseManager(browser=True)
        with patch('computer_use.shutil.which',return_value='node'),patch('pathlib.Path.is_file',return_value=True):
            cmd,env=m._command('browser')
            self.assertIn('--isolated',cmd)
            self.assertNotIn('--cdp-endpoint',cmd)
            self.assertEqual(env['ANONYMIZED_TELEMETRY'],'false')

    def test_stdio_handshake_and_image(self):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'fake.py';script.write_text(FAKE)
            child=StdioMCP([sys.executable,'-u',str(script)],directory)
            try:
                child.start()
                self.assertEqual(child.request('tools/call')['content'][0]['type'],'image')
            finally: child.close()
            self.assertIsNotNone(child.process.poll())

    def test_timeout_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'fake.py';script.write_text(FAKE)
            child=StdioMCP([sys.executable,'-u',str(script)],directory)
            try:
                child.start()
                with self.assertRaises(TimeoutError): child.request('hang',timeout=0.05)
                self.assertTrue(child.closed.is_set())
            finally: child.close()

    def test_http_handler_routes_rich_results_and_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            handler=object.__new__(McpHandler)
            handler.tools=WorkspaceTools(directory)
            handler.computer_use=ComputerUseManager()
            req=lambda name,args={}: handler._handle({'jsonrpc':'2.0','id':12,'method':'tools/call','params':{'name':name,'arguments':args}})
            self.assertTrue(req('computer_use_tools',{'target':'desktop'})['result']['isError'])
            names=[t['name'] for t in handler._handle({'id':1,'method':'tools/list'})['result']['tools']]
            self.assertEqual(len(names),17)
            self.assertIn('computer_use_call',names)
            rich={'content':[{'type':'image','data':'YWJj','mimeType':'image/png'}],'isError':False}
            handler.computer_use=Mock();handler.computer_use.call_tool.return_value=rich
            self.assertIs(req('computer_use_call',{'target':'desktop','name':'Screenshot'})['result'],rich)

if __name__=='__main__': unittest.main()
