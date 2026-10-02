"""Explicit test against running Blender: inspect, create/remove ONLY a unique test cube.
Never resets the scene or saves a blend file. Save your work before running any live test.
"""
import json
import uuid
from computer_use import ComputerUseManager

def main():
    m=ComputerUseManager(blender=True)
    name='OpenBridge_Verify_'+uuid.uuid4().hex
    created=False
    def invoke(tool,args):
        r=m.call_tool('blender',tool,args)
        text='\n'.join(x.get('text','') for x in r.get('content',[]) if x.get('type')=='text')
        if r.get('isError') or text.lstrip().startswith(('Error','Connection failed')):
            raise RuntimeError(text)
        return r,text
    try:
        info=m.list_tools('blender')
        print('Blender tools:',[x['name'] for x in info['tools']],flush=True)
        for tool in ['get_addon_status','get_scene_info','execute_blender_code','get_viewport_screenshot']:
            spec=next(t for t in info['tools'] if t['name']==tool)
            print(tool,'schema:',json.dumps(spec['inputSchema']),flush=True)
        _,text=invoke('get_addon_status',{})
        print('ADDON:',text[:2500],flush=True)
        _,text=invoke('get_scene_info',{'user_prompt':'Verify connection; preserve the existing scene.'})
        print('SCENE:',text[:2500],flush=True)
        code="""import bpy
bpy.ops.mesh.primitive_cube_add(size=1, location=(3,0,0))
obj=bpy.context.object
obj.name=%r
mod=obj.modifiers.new(name='Verification bevel',type='BEVEL')
mod.width=0.08
mod.segments=3
print('VERIFY_CREATED',obj.name,len(obj.data.vertices))
""" % name
        _,text=invoke('execute_blender_code',{'code':code,'user_prompt':'Create a temporary test cube only; do not reset the scene.'})
        created=True
        assert 'VERIFY_CREATED' in text,text
        print('MODEL:',text[:1000],flush=True)
        r,text=invoke('get_viewport_screenshot',{'max_size':800})
        images=[c for c in r['content'] if c['type']=='image']
        assert images,'No screenshot image block'
        print('VIEWPORT IMAGE:',len(images[0]['data']),flush=True)
    finally:
        if created:
            code="""import bpy
obj=bpy.data.objects.get(%r)
if obj is not None:
    mesh=obj.data
    bpy.data.objects.remove(obj,do_unlink=True)
    if mesh.users==0:
        bpy.data.meshes.remove(mesh)
print('VERIFY_REMOVED')
""" % name
            _,text=invoke('execute_blender_code',{'code':code,'user_prompt':'Remove only the unique verification cube created by this test.'})
            assert 'VERIFY_REMOVED' in text,text
        m.stop()
    print('BLENDER LIVE MODEL AND SCREENSHOT PASS',flush=True)

if __name__=='__main__':main()
