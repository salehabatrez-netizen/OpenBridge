"""Opt-in real Quick Tunnel recovery, isolated process and temporary workspace.
Never touches the production GUI, its cloudflared child, or existing workspace tools.
"""
import json
import tempfile
import threading
import time
import urllib.request
from bridge import BridgeService

ready = threading.Event()
statuses = []

def status(value):
    statuses.append(value)
    if value == 'RUNNING_ONLINE':
        ready.set()

def rpc(url, method, params):
    # Retry only these side-effect-free discovery methods, never a tools/call action.
    assert method in ('initialize', 'tools/list')
    for attempt in range(4):
        try:
            return rpc_once(url, method, params)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 3:
                raise
            print('DISCOVERY TRANSIENT RETRY:', method, type(exc).__name__, flush=True)
            time.sleep(1 + attempt)

def rpc_once(url, method, params):
    request = urllib.request.Request(url, data=json.dumps({
        'jsonrpc': '2.0', 'id': 'recovery-smoke', 'method': method, 'params': params}).encode(),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.loads(response.read())
        assert response.status == 200 and 'error' not in value, value
        return value['result']

def verify(url):
    info = rpc(url, 'initialize', {'protocolVersion':'2024-11-05', 'capabilities':{},
        'clientInfo':{'name':'isolated-recovery-smoke', 'version':'1'}})
    assert info['serverInfo']['name'] == 'openbridge-mcp'
    tools = rpc(url, 'tools/list', {})['tools']
    assert len(tools) == 17, len(tools)
    return len(tools)

with tempfile.TemporaryDirectory(prefix='openbridge-recovery-test-') as workspace:
    svc = BridgeService(workspace_dir=workspace, status_callback=status, log_callback=lambda _: None)
    try:
        svc.start()
        assert ready.wait(75), 'Initial tunnel never became ready'
        original_port, original_secret = svc.port, svc.secret
        first_url = svc.public_url
        print('INITIAL PUBLIC INITIALIZE + TOOLS/LIST:', verify(first_url), flush=True)
        first_proc = svc.tunnel_proc
        ready.clear()
        started = time.monotonic()
        # Fault injection: terminate only the child created by this test service.
        svc._terminate_tunnel(first_proc)
        assert ready.wait(90), 'Supervisor did not recover after child exit'
        assert svc.tunnel_proc is not first_proc and svc.tunnel_proc.poll() is None
        assert svc.port == original_port and svc.secret == original_secret
        assert not any(svc.computer_use.enabled.values())
        assert 'RECONNECTING' in statuses and svc.reconnect_count >= 1
        print('RECOVERED PUBLIC INITIALIZE + TOOLS/LIST:', verify(svc.public_url), flush=True)
        print('FORCED CHILD EXIT -> AUTOMATIC RECOVERY PASS; seconds=%.1f; domain_changed=%s; local_port_and_secret_unchanged=True' %
              (time.monotonic() - started, first_url != svc.public_url), flush=True)
    finally:
        svc.stop()
        assert not svc._tunnel_thread or not svc._tunnel_thread.is_alive()
        print('ISOLATED TEST SERVICE STOPPED; PRODUCTION NOT RESTARTED', flush=True)
