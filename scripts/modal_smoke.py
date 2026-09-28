import modal
from app.runtime import client, sandbox_image
from app.settings import settings

code = '''
import asyncio, pathlib, json
from runner.tools import Toolset, REPO
async def fake(endpoint, body): return {'id':'smoke'}
async def main():
    REPO.mkdir(parents=True,exist_ok=True)
    (REPO/'index.html').write_text('<h1>Visual check</h1><button onclick="this.textContent=\\\"Clicked\\\"">Click me</button>')
    tools=Toolset({'config':{'capabilities':['terminal','browser','computer'],'mcp_definitions':[]},'repository':{'start_command':'python -m http.server 3000','readiness_url':'http://localhost:3000'},'test_secrets':{}},fake)
    try:
        await tools.init()
        a=await tools.execute('browser',{'action':'navigate','url':'http://localhost:3000'})
        b=await tools.execute('browser',{'action':'click','selector':'text=Click me'})
        c=await tools.execute('computer',{'action':'key','keys':['ctrl','l']})
        d=await tools.execute('computer',{'action':'type','text':'http://localhost:3000'})
        e=await tools.execute('computer',{'action':'key','keys':['enter']})
        print(json.dumps({'browser_image':len(a['image']),'click_verified':'Clicked' in b['page'],'desktop_image':len(e['image']),'visual_calls':tools.visual_calls}),flush=True)
    finally: await tools.close()
asyncio.run(main())
'''
with modal.enable_output():
    c=client()
    app=modal.App.lookup(settings().modal_app_name,create_if_missing=True,client=c)
    sandbox=modal.Sandbox.create('python','-c',code,app=app,image=sandbox_image(),client=c,timeout=300,workdir='/opt/litereview',cpu=2,memory=4096)
    try:
        print('Sandbox:',sandbox.object_id,flush=True)
        sandbox.wait()
        print(sandbox.stdout.read())
        print(sandbox.stderr.read()[-6000:])
        if sandbox.returncode != 0: raise SystemExit(1)
    finally: sandbox.terminate()
