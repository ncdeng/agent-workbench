import cst.interface, os, time, tempfile

from _local_cst_sample import sample_project_path, scratch_directory

project = sample_project_path()
out_dir = project.replace('.cst', '') + '\\Export\\Farfield'
os.makedirs(out_dir, exist_ok=True)
out_file = out_dir + '\\farfield (f=18.8) [1].txt'

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

safe_out = out_file.replace('\\', '\\\\')
vba = f'''Sub Main
    Dim sFF As String
    sFF = Resulttree.GetFirstChildName("Farfields")
    If sFF = "Farfields\\Farfield Cuts" Then sFF = Resulttree.GetNextItemName(sFF)
    If sFF = "" Then
        ReportError "No farfield found"
        Exit Sub
    End If
    SelectTreeItem sFF
    FarfieldPlot.Reset
    FarfieldPlot.Plottype "3d"
    FarfieldPlot.SetLockSteps False
    FarfieldPlot.Step 1
    FarfieldPlot.Step2 1
    FarfieldPlot.Plot
    FarfieldPlot.SetPlotMode "Gain"
    FarfieldPlot.SetScaleLinear False
    FarfieldPlot.ASCIIExportVersion "2010"
    ASCIIExport.Reset
    ASCIIExport.FileName "{safe_out}"
    ASCIIExport.Execute
    ReportInformationToWindow "Exported to {safe_out}"
End Sub'''
with tempfile.NamedTemporaryFile(
    mode='w', suffix='.bas', delete=False, encoding='utf-8', dir=scratch_directory()
) as f:
    f.write(vba); vpath = f.name
try:
    mws.RunScript(vpath)
    time.sleep(3)
    print('RunScript ok')
except Exception as e:
    print('RunScript err:', e)
os.unlink(vpath)
print('exists:', os.path.exists(out_file), 'size:', os.path.getsize(out_file) if os.path.exists(out_file) else 0)
