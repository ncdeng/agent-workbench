import cst.results, cst.interface, os, json, time

from _local_cst_sample import sample_project_path, scratch_directory

project = sample_project_path()

# Open via cst.results
try:
    rm = cst.results.ProjectFile(project, allow_invalid=True)
    keys = list(rm.get_3d().keys())
    ff_keys = [k for k in keys if 'farfield' in k.lower() or 'Farfield' in k]
    print(json.dumps({'all_keys_count': len(keys), 'ff_keys': ff_keys, 'first_20': keys[:20]}))
except Exception as e:
    print(json.dumps({'err': str(e)}))

# Also try via interface
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
    # Try Resulttree via VBA
    import tempfile
    vba = 'Sub Main\nDim s As String\ns = Resulttree.GetFirstChildName("Farfields")\nReportInformationToWindow "FF1=" + s\nEnd Sub'
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.bas', delete=False, encoding='utf-8', dir=scratch_directory()
    ) as f:
        f.write(vba); vpath = f.name
    try:
        mws.RunScript(vpath)
        print(json.dumps({'run_script': 'ok'}))
    except Exception as e2:
        print(json.dumps({'run_script_err': str(e2)}))
except Exception as e:
    print(json.dumps({'interface_err': str(e)}))
