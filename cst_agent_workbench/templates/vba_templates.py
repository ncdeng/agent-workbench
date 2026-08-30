# 常用 CST VBA 模板定义
# 说明：
# 1. 所有模板都使用 Python .format() 风格占位符
# 2. 所有模板都包含完整的 Sub ... End Sub 结构
# 3. 部分 CST VBA 接口名称可能因版本差异存在变化，已在注释中标注“需要验证”

VBA_TEMPLATES = {
    "patch_antenna": '''Sub Main()
    With Units
        .Geometry "mm"
        .Frequency "GHz"
        .Time "ns"
    End With

    With Material
        .Reset
        .Name "PatchSubstrate"
        .Folder ""
        .Type "Normal"
        .Epsilon "{substrate_er}"
        .Mue "1.0"
        .Kappa "0"
        .TanD "0.02"
        .TanDFreq "10"
        .Colour "0.2", "0.8", "0.2"
        .Create
    End With

    With Brick
        .Reset
        .Name "Substrate"
        .Component "PatchAntenna"
        .Material "PatchSubstrate"
        .Xrange "-20", "20"
        .Yrange "-20", "20"
        .Zrange "0", "{substrate_thickness}"
        .Create
    End With

    With Brick
        .Reset
        .Name "Ground"
        .Component "PatchAntenna"
        .Material "PEC"
        .Xrange "-20", "20"
        .Yrange "-20", "20"
        .Zrange "-0.035", "0"
        .Create
    End With

    With Brick
        .Reset
        .Name "Patch"
        .Component "PatchAntenna"
        .Material "PEC"
        .Xrange "-{patch_length}/2", "{patch_length}/2"
        .Yrange "-{patch_width}/2", "{patch_width}/2"
        .Zrange "{substrate_thickness}", "{substrate_thickness}+0.035"
        .Create
    End With

    ' 已验证：DiscretePort.SetP1/SetP2 需要 4 个参数 (picked, x, y, z)
    With DiscretePort
        .Reset
        .PortNumber "1"
        .Type "SParameter"
        .Impedance "50"
        .SetP1 "False", "{feed_x}", "0", "0"
        .SetP2 "False", "{feed_x}", "0", "{substrate_thickness}+0.035"
        .InvertDirection "False"
        .LocalCoordinates "False"
        .Monitor "True"
        .Radius "0.0"
        .Create
    End With
End Sub''',

    "rectangular_waveguide": '''Sub Main()
    With Units
        .Geometry "mm"
        .Frequency "GHz"
        .Time "ns"
    End With

    ' 需要验证：这里采用外壁 PEC + 内腔 Vacuum 的方式构造矩形波导
    With Brick
        .Reset
        .Name "OuterWall"
        .Component "Waveguide"
        .Material "PEC"
        .Xrange "-({width}/2+2)", "({width}/2+2)"
        .Yrange "-({height}/2+2)", "({height}/2+2)"
        .Zrange "0", "{length}"
        .Create
    End With

    With Brick
        .Reset
        .Name "AirChannel"
        .Component "Waveguide"
        .Material "Vacuum"
        .Xrange "-{width}/2", "{width}/2"
        .Yrange "-{height}/2", "{height}/2"
        .Zrange "0", "{length}"
        .Create
    End With

    ' 需要验证：Solid.Subtract 的参数格式可能随 CST 版本略有差异
    Solid.Subtract "Waveguide:OuterWall", "Waveguide:AirChannel"

    ' 需要验证：波导端口对象和方向参数需要在目标 CST 环境中确认
    With Port
        .Reset
        .PortNumber "1"
        .Label "WG_Port_1"
        .PortOnBound "True"
        .Coordinates "Picks"
        .Orientation "zmin"
        .Xrange "-{width}/2", "{width}/2"
        .Yrange "-{height}/2", "{height}/2"
        .Zrange "0", "0"
        .Create
    End With

    With Port
        .Reset
        .PortNumber "2"
        .Label "WG_Port_2"
        .PortOnBound "True"
        .Coordinates "Picks"
        .Orientation "zmax"
        .Xrange "-{width}/2", "{width}/2"
        .Yrange "-{height}/2", "{height}/2"
        .Zrange "{length}", "{length}"
        .Create
    End With
End Sub''',

    "dipole_antenna": '''Sub Main()
    With Units
        .Geometry "mm"
        .Frequency "GHz"
        .Time "ns"
    End With

    With Cylinder
        .Reset
        .Name "DipoleTop"
        .Component "Dipole"
        .Material "PEC"
        .OuterRadius "{radius}"
        .InnerRadius "0"
        .Axis "z"
        .Xcenter "0"
        .Ycenter "0"
        .Zrange "{gap}/2", "{total_length}/2"
        .Create
    End With

    With Cylinder
        .Reset
        .Name "DipoleBottom"
        .Component "Dipole"
        .Material "PEC"
        .OuterRadius "{radius}"
        .InnerRadius "0"
        .Axis "z"
        .Xcenter "0"
        .Ycenter "0"
        .Zrange "-{total_length}/2", "-{gap}/2"
        .Create
    End With

    ' 已验证：DiscretePort.SetP1/SetP2 需要 4 个参数 (picked, x, y, z)
    With DiscretePort
        .Reset
        .PortNumber "1"
        .Type "SParameter"
        .Impedance "73"
        .SetP1 "False", "0", "0", "-{gap}/2"
        .SetP2 "False", "0", "0", "{gap}/2"
        .InvertDirection "False"
        .LocalCoordinates "False"
        .Monitor "True"
        .Radius "0.0"
        .Create
    End With
End Sub''',

    "frequency_setup": '''Sub Main()
    With Units
        .Geometry "mm"
        .Frequency "GHz"
        .Time "ns"
    End With

    Solver.FrequencyRange "{fmin}", "{fmax}"
End Sub''',

    "open_boundary": '''Sub Main()
    ' 已验证：Boundary 对象不需要 .Create 调用（参考 pcb_3d_simulation.py）
    With Boundary
        .Xmin "open"
        .Xmax "open"
        .Ymin "open"
        .Ymax "open"
        .Zmin "open"
        .Zmax "open"
        .Xsymmetry "none"
        .Ysymmetry "none"
        .Zsymmetry "none"
        .ApplyInAllDirections "False"
    End With
End Sub''',

    "run_hf_solver": '''Sub Main()
    ' 需要验证：不同 CST 工程模板下，时域求解器的显式选择方法可能不同
    ' 当前实现直接启动求解器，默认使用工程当前配置的高频求解器
    Solver.Start
End Sub''',
}
