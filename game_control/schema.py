"""Reviewed desktop extensions. Local approval is deliberately absent."""
import hashlib
import json
from . import __version__
from .engine import KEYS, MAX_STEP_MS, MAX_BATCH_MS, MAX_SETTLE_MS, INPUT_TICK_MS
S={'type':'string'}
def tool(name,description,properties=None,required=None):
    return {'name':name,'description':description,'inputSchema':{'type':'object','properties':properties or {},'required':required or [],'additionalProperties':False}}
LEASE={'lease_id':S}
STEP={'type':'object','required':['duration_ms'],'additionalProperties':False,'properties':{
    'duration_ms':{'type':'integer','minimum':20,'maximum':MAX_STEP_MS},
    'keys':{'type':'array','items':{'type':'string','enum':sorted(KEYS)},'maxItems':8,'uniqueItems':True},
    'buttons':{'type':'array','items':{'type':'string','enum':['left','right','middle']},'maxItems':3,'uniqueItems':True},
    'dx':{'type':'integer','minimum':-1000,'maximum':1000},'dy':{'type':'integer','minimum':-1000,'maximum':1000},
    'pointer':{'type':'array','items':{'type':'integer','minimum':0},'minItems':2,'maxItems':2}}}
TOOLS=[
 tool('GameWindows','List visible top-level windows and physical client rectangles. Does not focus or approve anything.'),
 tool('GameRequestControl','Request local approval for exactly one window. Only one lease at a time. F8 cancels. If the local owner enabled a trust rule for this game, the reply already contains state=approved and lease_id; otherwise poll GameControlStatus with request_id while the owner confirms.',{'window_id':{'type':'integer','minimum':1}},['window_id']),
 tool('GameControlStatus','Read safety state and loaded runtime/schema revision on the independent control lane. Provide your request_id to retrieve a locally approved lease; process running is not target ready.',{'request_id':S}),
 tool('GameFocus','Try to focus only the locally approved window. If Windows refuses, ask the owner to focus it manually.',LEASE,['lease_id']),
 tool('GameObserve','Capture foreground bound client area. Metadata maps image pixels to physical screen rect. Latest frame expires in 5 seconds; other windows/overlays may occlude it.',dict(LEASE,max_size={'type':'integer','minimum':320,'maximum':1920},settle_ms={'type':'integer','minimum':0,'maximum':MAX_SETTLE_MS,'default':0}),['lease_id']),
 tool('GameAct','Execute validated simultaneous input states, total <=%dms (each step <=%dms), auto-release' % (MAX_BATCH_MS, MAX_STEP_MS) + ' then fresh screenshot (default 100ms render settle; configurable observation_max_size and observation_settle_ms). Uses an absolute bounded timeline with timing summaries; an entirely missed step aborts rather than replaying late input. Consecutive steps with identical held states do not release between steps; prefer bounded multi-step batches over separate requests. Each step keys/buttons is the complete held state. dx/dy are relative deltas spread across duration; pointer is image-pixel [x,y], mutually exclusive with deltas. Requires latest unconsumed frame. Use unique action_id, query status after uncertain outcome; NEVER replay with a new id.',dict(LEASE,observation_max_size={'type':'integer','minimum':320,'maximum':1920,'default':1280},observation_settle_ms={'type':'integer','minimum':0,'maximum':MAX_SETTLE_MS,'default':100},frame_id=S,action_id={'type':'string','pattern':'^[A-Za-z0-9_-]{1,64}$'},steps={'type':'array','items':STEP,'minItems':1,'maxItems':16}),['lease_id','frame_id','action_id','steps']),
 tool('GameActionStatus','Read action receipt without replay. unknown does not mean not executed.',dict(LEASE,action_id=S),['lease_id','action_id']),
 tool('GameRelease','Release your approved window on an independent control lane without queuing behind an action or screenshot. Requires the matching lease; cancellation and cleanup are best effort, not hard real time. Local F8/master OFF can stop any lease.',LEASE,['lease_id'])]
NAMES={t['name'] for t in TOOLS}

# Advertise the schema loaded by this broker, not a copy imported by the GUI.
SCHEMA_REVISION = hashlib.sha256(json.dumps(TOOLS, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
RUNTIME_INFO = {
    'version': __version__, 'protocol_revision': 2, 'schema_revision': SCHEMA_REVISION,
    'concurrent_control': True, 'timing_version': 1, 'input_tick_ms': INPUT_TICK_MS,
    'max_step_ms': MAX_STEP_MS, 'max_batch_ms': MAX_BATCH_MS,
}
CONTROL_TOOLS = frozenset({'GameControlStatus', 'GameActionStatus', 'GameRelease'})
