"""Bounded, verified MCP health probes. No tools/call or permission changes.
A child process bounds DNS/TLS/read stalls. URL travels via stdin, not argv/logs.
"""
import json
import os
import re
import socket
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REVISION = '1033-repair-r1'
IO_TIMEOUT = 8
TOTAL_TIMEOUT = 12

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def redact(message, url=''):
    text = str(message)
    if url:
        parsed = urllib.parse.urlsplit(url)
        for secret in (url, parsed.path, parsed.hostname):
            if secret:
                text = text.replace(secret, '[redacted]')
    text = re.sub(r'https?://\S+', '[URL redacted]', text)
    text = re.sub(r'/mcp/[A-Za-z0-9_-]+', '/mcp/[redacted]', text)
    return text[:240]

def error_kind(exc, body=b''):
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 530 and re.search(rb'\b1033\b', body):
            return 'cloudflare_1033'
        return 'http_%d' % exc.code
    reason = getattr(exc, 'reason', exc)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return 'tls_certificate'
    if isinstance(reason, ssl.SSLEOFError):
        return 'tls_eof'
    if isinstance(reason, ssl.SSLError):
        return 'tls_error'
    if isinstance(reason, socket.gaierror):
        return 'dns'
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return 'timeout'
    if isinstance(reason, (ConnectionResetError, ConnectionAbortedError)):
        return 'connection_reset'
    if isinstance(reason, ConnectionRefusedError):
        return 'connection_refused'
    return 'probe_error'

def _probe_via_curl(url, payload, timeout=IO_TIMEOUT):
    curl_bin = shutil.which('curl.exe') or shutil.which('curl')
    if not curl_bin:
        return None
    cmd = [curl_bin, '-s', '-S', '--max-time', str(max(1, int(timeout))),
           '-X', 'POST', url,
           '-H', 'Content-Type: application/json',
           '-H', 'Accept: application/json, text/event-stream',
           '--data-raw', json.dumps(payload)]
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2, creationflags=flags)
        if p.returncode != 0:
            return None
        text = p.stdout.strip()
        message = None
        if 'data:' in text:
            for event in text.replace('\r\n', '\n').split('\n\n'):
                lines = [line[5:].lstrip() for line in event.splitlines() if line.startswith('data:')]
                t = '\n'.join(lines)
                try:
                    candidate = json.loads(t)
                    if isinstance(candidate, dict) and candidate.get('id') == 'bridge-health':
                        message = candidate
                        break
                except ValueError:
                    pass
        else:
            try:
                message = json.loads(text)
            except ValueError:
                pass
        if isinstance(message, dict):
            result = message.get('result') if isinstance(message, dict) else None
            server = result.get('serverInfo') if isinstance(result, dict) else None
            healthy = (message.get('jsonrpc') == '2.0'
                       and message.get('id') == 'bridge-health'
                       and isinstance(server, dict) and server.get('name') == 'openbridge-mcp')
            if healthy:
                return {'healthy': True, 'kind': 'ok', 'error': ''}
    except Exception:
        return None
    return None

def public_probe_once(url, timeout=IO_TIMEOUT):
    payload = {'jsonrpc':'2.0', 'id':'bridge-health', 'method':'initialize',
               'params':{'protocolVersion':'2024-11-05','capabilities':{},
                         'clientInfo':{'name':'openbridge-health','version':'2'}}}
    context = ssl.create_default_context()
    context.set_alpn_protocols(['http/1.1'])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(),
        urllib.request.HTTPSHandler(context=context), NoRedirect())
    request = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type':'application/json',
                 'Accept':'application/json, text/event-stream'}, method='POST')
    try:
        with opener.open(request, timeout=timeout) as response:
            body = bytearray()
            deadline = time.monotonic() + timeout
            message = None
            while len(body) < 65536 and time.monotonic() < deadline:
                chunk = response.read1(min(8192, 65536-len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if 'text/event-stream' in response.headers.get('Content-Type',''):
                    for event in bytes(body).decode('utf-8','replace').replace('\r\n','\n').split('\n\n'):
                        text='\n'.join(line[5:].lstrip() for line in event.splitlines() if line.startswith('data:'))
                        try:
                            candidate=json.loads(text)
                            if isinstance(candidate,dict) and candidate.get('id')=='bridge-health':
                                message=candidate; break
                        except ValueError:
                            pass
                else:
                    try: message=json.loads(bytes(body))
                    except ValueError: pass
                if message is not None:
                    break
            result = message.get('result') if isinstance(message,dict) else None
            server = result.get('serverInfo') if isinstance(result,dict) else None
            healthy = (response.status == 200 and isinstance(message,dict)
                and message.get('jsonrpc') == '2.0'
                and message.get('id') == 'bridge-health'
                and isinstance(server,dict) and server.get('name') == 'openbridge-mcp')
            return {'healthy':healthy, 'kind':'ok' if healthy else 'invalid_rpc',
                    'error':'' if healthy else 'Unexpected MCP initialization response'}
    except Exception as exc:
        body=b''
        if isinstance(exc,urllib.error.HTTPError):
            try: body=exc.read(8192)
            except Exception: pass
            finally: exc.close()
        kind = error_kind(exc,body)
        if sys.platform == 'win32' and kind in ('tls_eof', 'tls_error', 'connection_reset'):
            curl_res = _probe_via_curl(url, payload, timeout)
            if curl_res is not None:
                return curl_res
        return {'healthy':False,'kind':kind,
                'error':redact('%s: %s' % (type(exc).__name__,exc),url)}

def probe_public_bounded(url, total_timeout=TOTAL_TIMEOUT):
    executable=sys.executable
    if executable.lower().endswith('pythonw.exe'):
        console=os.path.join(os.path.dirname(executable),'python.exe')
        if os.path.isfile(console): executable=console
    command=[executable,os.path.abspath(__file__),'--worker']
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0) if sys.platform=='win32' else 0
    try:
        completed=subprocess.run(command,
            input=json.dumps({'url':url,'timeout':IO_TIMEOUT}),
            capture_output=True,text=True,encoding='utf-8',errors='replace',
            timeout=total_timeout,creationflags=flags,check=False)
        if completed.returncode != 0:
            return {'healthy':False,'kind':'probe_worker_error',
                    'error':'Health probe worker exited with code %s' % completed.returncode}
        result=json.loads(completed.stdout)
        if not isinstance(result,dict) or not isinstance(result.get('healthy'),bool):
            raise ValueError('Invalid worker reply')
        result['error']=redact(result.get('error',''),url)
        return result
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps only this probe child before raising.
        return {'healthy':False,'kind':'probe_deadline',
                'error':'Health probe exceeded total deadline; probe child reaped'}
    except Exception as exc:
        return {'healthy':False,'kind':'probe_worker_error',
                'error':redact('%s: %s' % (type(exc).__name__,exc),url)}

def connector_readiness(metrics_origin):
    """Only a parsed loopback metrics endpoint, never arbitrary host input."""
    if not metrics_origin:
        return None
    parsed=urllib.parse.urlsplit(metrics_origin)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','::1','localhost'):
        return None
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(metrics_origin.rstrip('/')+'/ready',timeout=2) as response:
            return response.status==200
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close()
        return False if code==503 else None
    except Exception:
        return None

def worker_main():
    try:
        data=json.loads(sys.stdin.read(32768))
        result=public_probe_once(data['url'],min(IO_TIMEOUT,max(1,int(data.get('timeout',IO_TIMEOUT)))))
    except Exception as exc:
        result={'healthy':False,'kind':'probe_worker_error','error':type(exc).__name__}
    sys.stdout.write(json.dumps(result,ensure_ascii=True))

if __name__=='__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('This is an internal health-probe worker, not a control endpoint.')
    worker_main()
