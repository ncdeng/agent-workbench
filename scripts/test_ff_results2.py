import cst.results, cst.interface, os, json, time, tempfile

from _local_cst_sample import sample_project_path, scratch_directory

project = sample_project_path()
out_file = str(scratch_directory() / 'ff_tree_check.txt')

# Open via cst.results (correct API)
try:
    rm = cst.results.ProjectFile(project)
    r3d = rm.get_3d()
    keys = list(r3d.keys())
    ff_keys = [k for k in keys if 'farfield' in k.lower()]
    print(json.dumps({'keys_count': len(keys), 'ff_keys': ff_keys, 'first_20': keys[:20]}))
except Exception as e:
    print(json.dumps({'results_err': str(e)}))

# Check via interface with VBA writing to file
try:
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
    proj.activate(); time.sleep(2)
    mws = proj.model3d
    safe_out = out_file.replace('\\', '\\\\')
    vba = f'''Sub Main
    Dim s As String
    Dim fNum As Integer
    fNum = FreeFile()
    Open "{safe_out}" For Output As #fNum
    s = Resulttree.GetFirstChildName("Farfields")
    Print #fNum, "FF1=" + s
    Dim s2 As String
    s2 = Resulttree.GetFirstChildName("")
    Print #fNum, "Root1=" + s2
    Close #fNum
End Sub'''
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.bas', delete=False, encoding='utf-8', dir=scratch_directory()
    ) as f:
        f.write(vba); vpath = f.name
    mws.RunScript(vpath)
    time.sleep(1)
    if os.path.exists(out_file):
        print(open(out_file, encoding='utf-8').read())
    os.unlink(vpath)
except Exception as e:
    print(json.dumps({'vba_err': str(e)}))
