"""Deterministic connection-health decisions; no network, UI or process effects."""
import ipaddress
import urllib.parse

PROBE_PATH_FAILURES = frozenset({
    'tls_eof','tls_error','tls_certificate','dns','timeout','probe_deadline',
    'connection_reset','connection_refused','probe_worker_error','probe_error',
    'http_401','http_403','http_404','http_405','http_502','invalid_rpc',
})

def public_request_origin(headers, expected_origin, request):
    """Liveness evidence only, NOT a replacement for secret-path authentication.
    Caller must authenticate first. Local requests and self probes do not count.
    Host must match the CURRENT candidate and Cloudflare routing headers exist.
    """
    if not expected_origin or not isinstance(request,dict):
        return ''
    method=request.get('method')
    if method not in ('initialize','tools/list','tools/call','ping'):
        return ''
    params=request.get('params') or {}
    if not isinstance(params,dict):
        return ''
    client=params.get('clientInfo') or {}
    if request.get('id')=='bridge-health' or (isinstance(client,dict) and client.get('name')=='openbridge-health'):
        return ''
    normalized={str(k).lower():str(v) for k,v in headers.items()}
    expected=urllib.parse.urlsplit(expected_origin)
    host=normalized.get('host','').lower()
    expected_host=expected.netloc.lower()
    if host not in (expected_host,expected_host+':443'):
        return ''
    if normalized.get('x-forwarded-proto','').lower()!='https':
        return ''
    if not normalized.get('cf-ray'):
        return ''
    try: ipaddress.ip_address(normalized.get('cf-connecting-ip',''))
    except ValueError: return ''
    return expected_origin.rstrip('/')

class HealthTracker:
    def __init__(self, started_at, failure_threshold=3, rebuild_after=120,
                 max_outage=180, edge_rebuild_after=45):
        self.started_at=started_at
        self.failure_threshold=failure_threshold
        self.rebuild_after=rebuild_after
        self.max_outage=max_outage
        self.edge_rebuild_after=edge_rebuild_after
        self.failures=0
        self.outage_since=None
        self.ever_healthy=False
        self.edge_since=None
        self.edge_failures=0
        self.connector_ready=None
        self.failure_kind=''
        self.public_evidence=False
        self.rebuild_reason=''

    def observe(self, healthy, now, public_activity=False,
                connector_ready=None, failure_kind=''):
        self.connector_ready=connector_ready
        self.failure_kind=failure_kind
        self.public_evidence=bool(public_activity)
        if healthy:
            self.failures=0;self.outage_since=None
            self.edge_since=None;self.edge_failures=0
            self.ever_healthy=True
            return 'RUNNING_ONLINE'
        self.failures+=1
        if public_activity:
            # A credential-authenticated request reached THIS generation through
            # Cloudflare. A failed local self-probe cannot negate that evidence.
            self.outage_since=None;self.edge_since=None;self.edge_failures=0
            self.ever_healthy=True
            return 'DEGRADED'
        if self.outage_since is None:
            self.outage_since=now
        if failure_kind=='cloudflare_1033':
            if self.edge_since is None:self.edge_since=now
            self.edge_failures+=1
        else:
            self.edge_since=None;self.edge_failures=0
        if connector_ready is True and failure_kind in PROBE_PATH_FAILURES:
            # /ready proves edge connections, not end-to-end public MCP. Keep a
            # previously verified address as DEGRADED, never claim probe success.
            return 'DEGRADED' if self.ever_healthy else 'RECONNECTING'
        if self.ever_healthy and self.failures<self.failure_threshold:
            return 'DEGRADED'
        return 'RECONNECTING'

    def should_rebuild(self, now, last_client_activity=0):
        self.rebuild_reason=''
        if self.public_evidence:
            return False
        if self.outage_since is None and self.ever_healthy:
            return False
        # Conclusive 1033 is not hidden by local polling or a stale /ready=200.
        if (self.edge_since is not None and self.edge_failures>=self.failure_threshold
                and now-self.edge_since>=self.edge_rebuild_after):
            self.rebuild_reason='confirmed_cloudflare_1033'
            return True
        if self.connector_ready is True and self.failure_kind in PROBE_PATH_FAILURES:
            return False
        since=self.outage_since if self.outage_since is not None else self.started_at
        if now-since>=self.max_outage:
            self.rebuild_reason='unverified_outage_deadline'
            return True
        # Legacy embedding API retained; production no longer passes generic
        # last_client_activity, because loopback traffic does not prove a tunnel.
        if last_client_activity and now-last_client_activity<45:
            return False
        if now-since>=self.rebuild_after:
            self.rebuild_reason='unverified_outage_timeout'
            return True
        return False
