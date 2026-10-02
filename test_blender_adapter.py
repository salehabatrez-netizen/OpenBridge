import unittest
from unittest.mock import patch, Mock
from computer_use import ComputerUseManager, BLENDER_TOOLS, COMPUTER_USE_SPEC
from bridge import BridgeService

class BlenderAdapterTests(unittest.TestCase):
    def test_default_disabled(self):
        m=ComputerUseManager()
        self.assertFalse(m.status()['targets']['blender']['enabled'])
        with self.assertRaises(PermissionError):m.list_tools('blender')
    def test_no_auth(self):
        with self.assertRaises(ValueError):BridgeService(require_auth=False,enable_blender=True)
    def test_safe_local_command(self):
        with patch('pathlib.Path.is_file',return_value=True):
            cmd,env=ComputerUseManager(blender=True)._command('blender')
        self.assertEqual(env['BLENDER_HOST'],'127.0.0.1')
        self.assertEqual(env['BLENDER_MCP_SAFE_MODE'],'1')
        self.assertEqual(env['DISABLE_TELEMETRY'],'1')
        self.assertTrue(cmd[0].endswith('mcp-for-blender.exe'))
    def test_no_paid_generation(self):
        self.assertNotIn('generate_hyper3d_model_via_text',BLENDER_TOOLS)
        self.assertIn('execute_blender_code',BLENDER_TOOLS)
    def test_schema_allows_blender(self):
        for spec in COMPUTER_USE_SPEC:
            props=spec['inputSchema']['properties']
            if 'target' in props:self.assertIn('blender',props['target']['enum'])
    def test_safe_mode_rejection_is_error(self):
        m=ComputerUseManager(blender=True)
        m.tools['blender']={'execute_blender_code':{}}
        child=Mock()
        m.children['blender']=child
        child.request.return_value={'content':[{'type':'text','text':'Rejected by safe mode - blocked'}],'isError':False}
        with patch.object(m,'_client',return_value=child):
            self.assertTrue(m.call_tool('blender','execute_blender_code',{'code':'import subprocess'})['isError'])
    def test_stop_revokes_blender(self):
        m=ComputerUseManager(blender=True);m.stop()
        with self.assertRaises(PermissionError):m.list_tools('blender')

if __name__=='__main__':unittest.main()
