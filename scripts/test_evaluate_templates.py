import cst.interface, os, time, tempfile

from _local_cst_sample import sample_project_path, scratch_directory

project = sample_project_path()

de = cst.interface.DesignEnvironment.connect_to_any_or_new()
de.set_quiet_mode(True)
norm = lambda p: os.path.normcase(os.path.normpath(p))
proj = None
for p in de.get_open_projects():
    try:
        if norm(str(p.filename())) == norm(project):
            proj = p; break
    except: pass
if proj is None:
    proj = de.open_project(project); time.sleep(4)
proj.activate()
time.sleep(2)
mws = proj.model3d

# Try EvaluateResultTemplates via RunScript (post-processing context)
vba = '''Sub Main
    EvaluateResultTemplates
End Sub'''
with tempfile.NamedTemporaryFile(
    mode='w', suffix='.bas', delete=False, encoding='utf-8', dir=scratch_directory()
) as f:
    f.write(vba); vpath = f.name
try:
    mws.RunScript(vpath)
    time.sleep(5)
    print('RunScript EvaluateResultTemplates: ok')
except Exception as e:
    print('RunScript err:', e)
os.unlink(vpath)

export_dir = project.replace('.cst', '') + '/Export/Farfield'
print('files:', os.listdir(export_dir) if os.path.isdir(export_dir) else 'no dir')
