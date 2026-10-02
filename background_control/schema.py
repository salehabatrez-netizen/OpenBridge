ACTION = {'type':'string','enum':['invoke','set_value','select','toggle','expand','collapse','scroll_up','scroll_down','message_click']}
def tool(name, description, properties, required=()):
    return {'name':name,'description':description,'inputSchema':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}
TEXT={'type':'string','minLength':1,'maxLength':128}
TOOLS=[
 tool('BackgroundWindows','List non-minimized visible windows by exact HWND and process identity. Does not focus.',{}),
 tool('BackgroundSnapshot','Read a target UIA/control tree without focusing. Optional PrintWindow image may be blank/stale for unsupported applications; never uses screen-region capture. IDs expire in 30s (message coordinates 5s). Returns session prefix for action IDs.',
      {'window_id':{'type':'integer','minimum':1},'capture':{'type':'boolean','default':False},'format':{'type':'string','enum':['png','jpeg'],'default':'png'},'max_size':{'type':'integer','minimum':320,'maximum':1920,'default':1280},'marks':{'type':'array','maxItems':16,'items':{'type':'array','items':{'type':'integer','minimum':0},'minItems':2,'maxItems':2}}},['window_id']),
 tool('BackgroundAction','One explicit UIA action or opt-in PostMessage click on snapshot element. Never focuses, moves the cursor, uses clipboard or retries. Target app MAY activate itself. Payment/delete/send require user confirmation. Message delivery is NOT application success. Use session_prefix + unique suffix as action_id. On uncertain outcome query status, never mint another ID to replay.',
      {'snapshot_id':TEXT,'element_id':TEXT,'action_id':TEXT,'action':ACTION,'text':{'type':'string','maxLength':10000},'x':{'type':'integer','minimum':0,'maximum':32767},'y':{'type':'integer','minimum':0,'maximum':32767},'button':{'type':'string','enum':['left','right','middle'],'default':'left'}},['snapshot_id','element_id','action_id','action']),
 tool('BackgroundActionStatus','Read in-memory receipt without replay. Unknown or process restart does NOT prove non-execution.',{'action_id':TEXT},['action_id'])]
NAMES={t['name'] for t in TOOLS}
