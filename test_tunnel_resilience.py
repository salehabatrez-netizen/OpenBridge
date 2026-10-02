"""Offline/loopback-only regression tests for 1033, EOF and address publication."""
import io
import json
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bridge import BridgeService, McpHandler
from connection_health import HealthTracker, public_request_origin
from connection_probe import (REVISION, NoRedirect, error_kind, redact, probe_public_bounded,
    public_probe_once, connector_readiness)

ORIGIN='https://current.example.test'
URL=ORIGIN+'/mcp/0123456789abcdef0123456789abcdef'
HEADERS={'Host':'current.example.test','CF-Connecting-IP':'203.0.113.10',
         'CF-Ray':'fake-edge-ray','X-Forwarded-Proto':'https'}
REQ={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}}

class PolicyTests(unittest.TestCase):
    def test_28_minutes_real_public_traffic_never_kills_live_route(self):
        tracker=HealthTracker(0);tracker.observe(True,1)
        for now in range(10,1690,10):
            state=tracker.observe(False,now,public_activity=True,failure_kind='tls_eof')
            self.assertEqual(state,'DEGRADED')
            self.assertFalse(tracker.should_rebuild(now))

    def test_28_minutes_local_polling_cannot_hide_1033(self):
        tracker=HealthTracker(0);tracker.observe(True,1)
        rebuilt_at=None
        for now in range(10,1690,5):
            tracker.observe(False,now,connector_ready=False,failure_kind='cloudflare_1033')
            if tracker.should_rebuild(now,last_client_activity=now):
                rebuilt_at=now;break
        self.assertEqual(rebuilt_at,55)
        self.assertEqual(tracker.rebuild_reason,'confirmed_cloudflare_1033')

    def test_ready_connector_is_not_destroyed_by_local_tls_probe(self):
        tracker=HealthTracker(0);tracker.observe(True,1)
        for now in range(10,1690,10):
            self.assertEqual(tracker.observe(False,now,connector_ready=True,failure_kind='tls_eof'),'DEGRADED')
            self.assertFalse(tracker.should_rebuild(now))

    def test_initial_ready_connector_not_falsely_public_verified(self):
        tracker=HealthTracker(0)
        self.assertEqual(tracker.observe(False,10,connector_ready=True,failure_kind='tls_eof'),'RECONNECTING')
        self.assertFalse(tracker.ever_healthy)

    def test_1033_is_not_overruled_by_stale_ready_endpoint(self):
        tracker=HealthTracker(0);tracker.observe(True,1)
        for now in (10,25,55):
            tracker.observe(False,now,connector_ready=True,failure_kind='cloudflare_1033')
        self.assertTrue(tracker.should_rebuild(55))

    def test_530_without_1033_not_misclassified(self):
        error=urllib.error.HTTPError(URL,530,'unknown',{},None)
        self.assertEqual(error_kind(error,b'<h1>Error 1016</h1>'),'http_530')
        self.assertEqual(error_kind(error,b'<h1>Error 1033</h1>'),'cloudflare_1033')

    def test_real_public_evidence_expires_when_no_new_requests(self):
        tracker=HealthTracker(0);tracker.observe(False,100,public_activity=True,failure_kind='tls_eof')
        self.assertFalse(tracker.should_rebuild(100))
        for now in (160,180,205):
            tracker.observe(False,now,connector_ready=False,failure_kind='cloudflare_1033')
        self.assertTrue(tracker.should_rebuild(205))

    def test_recovered_probe_resets_error_streak(self):
        tracker=HealthTracker(0)
        for now in (1,10,20):tracker.observe(False,now,failure_kind='cloudflare_1033')
        tracker.observe(True,30)
        self.assertFalse(tracker.should_rebuild(200))
        self.assertEqual(tracker.edge_failures,0)

    def test_unknown_probe_failure_keeps_absolute_ceiling(self):
        tracker=HealthTracker(0);tracker.observe(False,1,failure_kind='probe_deadline')
        self.assertTrue(tracker.should_rebuild(181,last_client_activity=180))

class LivenessTests(unittest.TestCase):
    def test_only_current_origin(self):
        self.assertEqual(public_request_origin(HEADERS,ORIGIN,REQ),ORIGIN)
        self.assertEqual(public_request_origin(HEADERS,'https://rotated.example.test',REQ),'')
    def test_local_request_does_not_count(self):
        self.assertEqual(public_request_origin({'Host':'127.0.0.1:8765'},ORIGIN,REQ),'')
    def test_self_probe_does_not_count(self):
        request={'id':'bridge-health','method':'initialize','params':{}}
        self.assertEqual(public_request_origin(HEADERS,ORIGIN,request),'')
        request={'id':2,'method':'initialize','params':{'clientInfo':{'name':'openbridge-health'}}}
        self.assertEqual(public_request_origin(HEADERS,ORIGIN,request),'')
    def test_missing_cloudflare_headers_do_not_count(self):
        for header in ('CF-Connecting-IP','CF-Ray','X-Forwarded-Proto'):
            h=dict(HEADERS);h.pop(header)
            self.assertEqual(public_request_origin(h,ORIGIN,REQ),'')
    def test_stale_public_activity_does_not_count(self):
        svc=BridgeService(persist_url=False);svc.log=Mock()
        with patch.object(McpHandler,'last_public_origin',ORIGIN),patch.object(McpHandler,'last_public_activity',100):
            self.assertTrue(svc._recent_public_activity(ORIGIN,144))
            self.assertFalse(svc._recent_public_activity(ORIGIN,145))
            self.assertFalse(svc._recent_public_activity('https://old.example.test',101))

class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.svc=BridgeService(workspace_dir=self.temp.name,persist_url=True)
        self.svc.log=Mock();self.svc._url_file_armed=True
    def tearDown(self):self.temp.cleanup()
    def state(self):return json.loads(Path(self.svc.url_file_path()).read_text())
    def test_status_only_change_persisted(self):
        self.svc._publish_connection('RUNNING_ONLINE',URL)
        generation=self.state()['tunnel_generation']
        self.svc._publish_connection('DEGRADED',URL)
        self.assertEqual(self.state()['status'],'DEGRADED')
        self.assertEqual(self.state()['tunnel_generation'],generation)
    def test_same_origin_clear_restore_not_counted_as_rotations(self):
        self.svc._publish_connection('RUNNING_ONLINE',URL)
        generation=self.state()['tunnel_generation']
        for _ in range(30):
            self.svc._publish_connection('RECONNECTING','')
            self.svc._publish_connection('RUNNING_ONLINE',URL)
        self.assertEqual(self.state()['tunnel_generation'],generation)
    def test_new_origin_counts_exactly_once(self):
        self.svc._publish_connection('RUNNING_ONLINE',URL)
        generation=self.state()['tunnel_generation']
        self.svc._publish_connection('RECONNECTING','')
        self.svc._publish_connection('RUNNING_ONLINE',URL.replace('current.','new.'))
        self.assertEqual(self.state()['tunnel_generation'],generation+1)
    def test_empty_url_state_change_not_lost(self):
        self.svc._publish_connection('TUNNELING','')
        self.svc._publish_connection('RECONNECTING','')
        self.assertEqual(self.state()['status'],'RECONNECTING')
        self.assertEqual(self.state()['url'],'')

class ProbeTests(unittest.TestCase):
    def test_timeout_boundary(self):
        with patch('connection_probe.subprocess.run',side_effect=subprocess.TimeoutExpired(['worker'],12)):
            result=probe_public_bounded(URL)
        self.assertFalse(result['healthy']);self.assertEqual(result['kind'],'probe_deadline')
    def test_secret_sent_over_stdin_not_argv(self):
        completed=subprocess.CompletedProcess([],0,json.dumps({'healthy':True,'kind':'ok','error':''}),'')
        with patch('connection_probe.subprocess.run',return_value=completed) as run:
            result=probe_public_bounded(URL)
        self.assertTrue(result['healthy'])
        self.assertNotIn(URL,str(run.call_args.args))
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['url'],URL)
        self.assertEqual(run.call_args.kwargs['timeout'],12)
    def test_worker_invalid_reply_is_not_success(self):
        completed=subprocess.CompletedProcess([],0,'not json','')
        with patch('connection_probe.subprocess.run',return_value=completed):
            self.assertFalse(probe_public_bounded(URL)['healthy'])
    def test_tls_error_classes(self):
        self.assertEqual(error_kind(urllib.error.URLError(ssl.SSLEOFError(8,'EOF'))),'tls_eof')
        self.assertEqual(error_kind(urllib.error.URLError(ssl.SSLCertVerificationError(1,'cert'))),'tls_certificate')
        self.assertEqual(error_kind(socket.gaierror(-2,'no host')),'dns')
    def test_redaction(self):
        value=redact('Failed '+URL,URL)
        self.assertNotIn('0123456789abcdef',value)
        self.assertNotIn('current.example.test',value)
    def test_ready_endpoint_restricted_to_loopback(self):
        self.assertIsNone(connector_readiness('https://untrusted.example.test'))
    def test_probe_readiness_does_not_claim_mcp(self):
        response=Mock(status=200);response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with patch('connection_probe.urllib.request.build_opener') as build:
            build.return_value.open.return_value=response
            self.assertTrue(connector_readiness('http://127.0.0.1:20241'))
            self.assertEqual(build.return_value.open.call_args.args[0],'http://127.0.0.1:20241/ready')

class RealLoopbackTests(unittest.TestCase):
    def test_worker_and_unauthorized_activity_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            svc=BridgeService(workspace_dir=directory,use_tunnel=False,persist_url=False)
            svc.log=Mock();svc.start()
            try:
                McpHandler.public_candidate_origin=ORIGIN
                # A real child HTTP probe must succeed without counting itself.
                result=probe_public_bounded(svc.local_url)
                self.assertTrue(result['healthy'],result)
                self.assertEqual(McpHandler.last_public_activity,0)
                health_opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with health_opener.open('http://127.0.0.1:%d/health'%svc.port,timeout=3) as response:
                    health=json.load(response)
                self.assertEqual(health['transport_revision'],REVISION)
                self.assertNotIn(svc.secret,json.dumps(health))
                self.assertNotIn('/mcp/',json.dumps(health))
                headers=dict(HEADERS);headers.update({'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
                bad=urllib.request.Request('http://127.0.0.1:%d/mcp/wrong'%svc.port,
                    data=json.dumps(REQ).encode(),headers=headers)
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with self.assertRaises(urllib.error.HTTPError) as error:opener.open(bad,timeout=3)
                error.exception.close()
                self.assertEqual(McpHandler.last_public_activity,0)
                good=urllib.request.Request(svc.local_url,data=json.dumps(REQ).encode(),headers=headers)
                with opener.open(good,timeout=3) as response:self.assertEqual(response.status,200)
                self.assertGreater(McpHandler.last_public_activity,0)
                self.assertFalse(any(svc.computer_use.enabled.values()))
            finally:svc.stop()

    def test_real_probe_process_deadline(self):
        class Slow(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length',0)))
                time.sleep(2)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Slow)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            start=time.monotonic()
            result=probe_public_bounded('http://127.0.0.1:%d/mcp/test'%server.server_port,total_timeout=0.3)
            self.assertEqual(result['kind'],'probe_deadline',result)
            self.assertLess(time.monotonic()-start,4)
        finally:server.shutdown();server.server_close();worker.join(2)


class ProbeParserTests(unittest.TestCase):
    GOOD={'jsonrpc':'2.0','id':'bridge-health','result':{'serverInfo':{'name':'openbridge-mcp'}}}
    class Response:
        def __init__(self,data,content_type='application/json',status=200,chunks=None):
            self.status=status;self.headers={'Content-Type':content_type}
            self.chunks=list(chunks) if chunks is not None else [data]
        def read1(self,size):return self.chunks.pop(0) if self.chunks else b''
        def __enter__(self):return self
        def __exit__(self,*args):return False
    def probe(self,message,content_type='application/json',status=200,chunks=None):
        body=json.dumps(message).encode() if not isinstance(message,bytes) else message
        response=self.Response(body,content_type,status,chunks)
        with patch('connection_probe.urllib.request.build_opener') as build:
            build.return_value.open.return_value=response
            result=public_probe_once(URL)
        return result,build
    def test_valid_json_initialize(self):
        self.assertTrue(self.probe(self.GOOD)[0]['healthy'])
    def test_wrong_server_rejected_in_actual_parser(self):
        wrong={'jsonrpc':'2.0','id':'bridge-health','result':{'serverInfo':{'name':'another-server'}}}
        result,_=self.probe(wrong)
        self.assertFalse(result['healthy']);self.assertEqual(result['kind'],'invalid_rpc')
    def test_wrong_id_and_jsonrpc_rejected(self):
        for key,value in [('id',123),('jsonrpc','1.0')]:
            wrong=dict(self.GOOD);wrong[key]=value
            self.assertFalse(self.probe(wrong)[0]['healthy'])
    def test_malformed_result_cannot_claim_success(self):
        for result in (None,[],1,'string',{'serverInfo':None},{'serverInfo':[]}):
            wrong=dict(self.GOOD);wrong['result']=result
            actual,_=self.probe(wrong)
            self.assertFalse(actual['healthy']);self.assertEqual(actual['kind'],'invalid_rpc')
    def test_json_list_html_and_garbage_rejected(self):
        for body in (b'[]',b'<html>OK</html>',b'not json',b''):
            self.assertFalse(self.probe(body)[0]['healthy'])
    def test_non_200_rejected_even_with_correct_rpc(self):
        self.assertFalse(self.probe(self.GOOD,status=502)[0]['healthy'])
    def test_split_json_message_parses(self):
        raw=json.dumps(self.GOOD).encode()
        self.assertTrue(self.probe(b'',chunks=[raw[:12],raw[12:]])[0]['healthy'])
    def test_sse_ignores_unrelated_event(self):
        raw=b'event: message\r\ndata: {"jsonrpc":"2.0","id":"other"}\r\n\r\n'
        raw+=b'data: '+json.dumps(self.GOOD).encode()+b'\r\n\r\n'
        self.assertTrue(self.probe(raw,content_type='text/event-stream')[0]['healthy'])
    def test_sse_split_message_parses(self):
        raw=b'data: '+json.dumps(self.GOOD).encode()+b'\n\n'
        self.assertTrue(self.probe(b'',content_type='text/event-stream',chunks=[raw[:15],raw[15:]])[0]['healthy'])
    def test_public_probe_preserves_certificate_verification(self):
        _,build=self.probe(self.GOOD)
        handler=next(x for x in build.call_args.args if isinstance(x,urllib.request.HTTPSHandler))
        self.assertTrue(handler._context.check_hostname)
        self.assertEqual(handler._context.verify_mode,ssl.CERT_REQUIRED)
    def test_redirects_never_followed_with_credential_url(self):
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://other.test'))
    def test_http_1033_body_classified(self):
        error=urllib.error.HTTPError(URL,530,'edge error',{},io.BytesIO(b'<h1>Error 1033</h1>'))
        with patch('connection_probe.urllib.request.build_opener') as build:
            build.return_value.open.side_effect=error
            result=public_probe_once(URL)
        self.assertFalse(result['healthy']);self.assertEqual(result['kind'],'cloudflare_1033')
    def test_ready_503_reports_disconnected(self):
        error=urllib.error.HTTPError('http://127.0.0.1:20241/ready',503,'not ready',{},io.BytesIO(b''))
        with patch('connection_probe.urllib.request.build_opener') as build:
            build.return_value.open.side_effect=error
            self.assertFalse(connector_readiness('http://127.0.0.1:20241'))

class SupervisorResilienceTests(unittest.TestCase):
    def test_unexpected_exception_does_not_end_supervision_silently(self):
        svc=BridgeService(persist_url=False);svc.log=Mock()
        svc._stop_event=Mock();svc._stop_event.is_set.return_value=False
        svc._stop_event.wait.return_value=True
        with patch('bridge.find_cloudflared',return_value='fake'), \
             patch.object(svc,'_run_tunnel_once',side_effect=RuntimeError('test worker fault')), \
             patch.object(svc,'_terminate_tunnel') as terminate:
            svc._start_tunnel()
        terminate.assert_called_once()
        svc._stop_event.wait.assert_called_once_with(1)
        self.assertEqual(svc.reconnect_count,1)
        self.assertEqual(svc._connection_state,'RECONNECTING')

if __name__=='__main__':unittest.main(verbosity=2)
