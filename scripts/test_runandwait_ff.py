import sys, os, tempfile, subprocess

from _local_cst_sample import sample_project_path, scratch_directory

project = sample_project_path()
proj_dir = project.replace('.cst', '')
ascii_out = os.path.join(proj_dir, 'Export', 'Farfield', 'manual_runandwait.txt')
os.makedirs(os.path.dirname(ascii_out), exist_ok=True)
vba_path = str(scratch_directory() / 'cst_ff_export_src.bas')
ascii_out_vba = ascii_out.replace('\\', '\\\\')

vba_script = f'''Sub Main
    Dim sFF As String
    sFF = Resulttree.GetFirstChildName("Farfields")
    If sFF = "Farfields\\Farfield Cuts" Then sFF = Resulttree.GetNextItemName(sFF)
    If sFF = "" Then
        ReportError "No farfield result found"
        Exit Sub
    End If
    ReportInformationToWindow "Found: " + sFF
    FarfieldPlot.StoreAllSettings("cst_export_manual")
    FarfieldPlot.Reset
    FarfieldPlot.Plottype "3d"
    SelectTreeItem sFF
    FarfieldPlot.SetLockSteps False
    FarfieldPlot.Step 5
    FarfieldPlot.Step2 5
    FarfieldPlot.Plot
    FarfieldPlot.UseFarfieldApproximation True
    FarfieldPlot.SetPlotMode "Gain"
    FarfieldPlot.SetScaleLinear False
    FarfieldPlot.SetLogRange 50
    FarfieldPlot.DBUnit "0"
    FarfieldPlot.Plot
    Plot.Update
    FarfieldPlot.ASCIIExportVersion "2010"
    With ASCIIExport
        .Reset
        .FileName "{ascii_out_vba}"
        .Execute
    End With
    FarfieldPlot.RestoreAllSettings("cst_export_manual")
End Sub
'''
with open(vba_path, 'w', encoding='utf-8') as f:
    f.write(vba_script)

cst_script = f'''import cst.interface, json, os, sys, time
de = cst.interface.DesignEnvironment.connect_to_any_or_new()
de.set_quiet_mode(True)
projects = de.get_open_projects()
target = r"{project}"
norm_target = os.path.normcase(os.path.normpath(target))
proj = None
for p in projects:
    try:
        if os.path.normcase(os.path.normpath(str(p.filename()))) == norm_target:
            proj = p
            break
    except:
        pass
if proj is None:
    proj = de.open_project(target)
    time.sleep(4)
proj.activate()
time.sleep(2)
mws = proj.model3d
try:
    mws.RunAndWait(r"{vba_path}")
    print(json.dumps({{"run": "ok"}}))
except Exception as e:
    print(json.dumps({{"run_err": str(e)}}))
'''
with tempfile.NamedTemporaryFile(
    mode='w', suffix='.py', delete=False, encoding='utf-8', dir=scratch_directory()
) as f:
    f.write(cst_script)
    sp = f.name
r = subprocess.run([sys.executable, sp], capture_output=True, text=True, timeout=120)
print(r.stdout[:2000])
if r.stderr:
    print('ERR', r.stderr[:500])
os.unlink(sp)
if os.path.exists(ascii_out):
    print('SUCCESS bytes=', os.path.getsize(ascii_out))
else:
    print('NO_FILE')
