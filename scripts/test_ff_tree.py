import cst.interface, os, json, time

from _local_cst_sample import sample_project_path

project = sample_project_path()

de = cst.interface.DesignEnvironment.connect_to_any_or_new()
de.set_quiet_mode(True)
norm = lambda p: os.path.normcase(os.path.normpath(p))
proj = None
for p in de.get_open_projects():
    try:
        if norm(str(p.filename())) == norm(project):
            proj = p
            break
    except Exception:
        pass
if proj is None:
    proj = de.open_project(project)
    time.sleep(4)
proj.activate()
time.sleep(2)
mws = proj.model3d

# Check result tree
try:
    rt = mws.Resulttree
    ff1 = rt.GetFirstChildName('Farfields')
    ff2 = rt.GetNextItemName(ff1) if ff1 else ''
    print(json.dumps({'ff1': ff1, 'ff2': ff2}))
except Exception as e:
    print(json.dumps({'err': str(e)}))

# Try EvaluateResultTemplates and check output
try:
    mws.EvaluateResultTemplates()
    time.sleep(5)
    out_dir = project.replace('.cst', '') + '/Export/Farfield'
    files = os.listdir(out_dir) if os.path.isdir(out_dir) else []
    print(json.dumps({'templates_ok': True, 'files': files}))
except Exception as e:
    print(json.dumps({'templates_err': str(e)}))
