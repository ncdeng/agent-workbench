"""
9.4 GHz FR-4 微带贴片天线 — 端到端测试
参数化建模 + open (add space) 边界 + 远场监视器 + 求解器

运行方式：在 CST Python 环境下执行
  python experiments/test_patch_9p4ghz.py

前提：CST Design Environment 已打开（或能自动启动）
"""
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from cst_agent_workbench.cst.primitives import (
    reset_created_objects, register_parameter, register_material, register_object,
    set_units, store_parameter, create_material, create_brick,
    create_discrete_port, set_frequency_range, set_boundary, run_solver,
)
from cst_agent_workbench.cst.controller import CSTController


def execute_step(ctrl, step_num, label, vba):
    """执行单步 VBA 并打印结果"""
    print(f"\n[Step {step_num:2d}] {label}")
    vba_lines = vba.strip().split("\n")
    if len(vba_lines) <= 2:
        print(f"         VBA: {vba.strip()}")
    else:
        print(f"         VBA: {vba_lines[0].strip()} ... ({len(vba_lines)} lines)")
    result = ctrl.execute_vba(vba, label=label)
    ok = result.get("success", False)
    print(f"         {'OK' if ok else 'FAILED: ' + result.get('message', '?')}")
    return result


def main():
    reset_created_objects()

    print("=" * 60)
    print("9.4 GHz FR-4 微带贴片天线 端到端测试")
    print("=" * 60)

    ctrl = CSTController()
    connect_result = ctrl.connect()
    if not connect_result.get("success"):
        print(f"\nCST 连接失败: {ctrl.last_message}")
        print("请确保 CST Design Environment 已打开")
        return False
    print(f"CST 状态: {ctrl.get_status()}")

    step = 0
    all_steps = []

    all_steps.append(set_units("mm", "GHz", "ns"))

    params = [
        ("substrate_h", "1.6"),
        ("copper_t", "0.035"),
        ("patch_L", "6.9"),
        ("patch_W", "9.7"),
        ("ground_L", "30"),
        ("ground_W", "30"),
        ("feed_x", "2.3"),
    ]
    for pname, pval in params:
        all_steps.append(store_parameter(pname, pval))
        register_parameter(pname, pval)

    all_steps.append(create_material("FR4_sub", epsilon=4.4, tand=0.02, tand_freq=9.4))
    register_material("FR4_sub")

    all_steps.append(create_brick(
        name="Ground", component="Antenna", material="PEC",
        xmin="-ground_L/2", xmax="ground_L/2",
        ymin="-ground_W/2", ymax="ground_W/2",
        zmin="-copper_t", zmax="0",
    ))
    register_object("Antenna", "Ground")

    all_steps.append(create_brick(
        name="Substrate", component="Antenna", material="FR4_sub",
        xmin="-ground_L/2", xmax="ground_L/2",
        ymin="-ground_W/2", ymax="ground_W/2",
        zmin="0", zmax="substrate_h",
    ))
    register_object("Antenna", "Substrate")

    all_steps.append(create_brick(
        name="Patch", component="Antenna", material="PEC",
        xmin="-patch_L/2", xmax="patch_L/2",
        ymin="-patch_W/2", ymax="patch_W/2",
        zmin="substrate_h", zmax="substrate_h+copper_t",
    ))
    register_object("Antenna", "Patch")

    all_steps.append(create_discrete_port(
        port_number=1,
        p1_x="feed_x", p1_y="0", p1_z="0",
        p2_x="feed_x", p2_y="0", p2_z="substrate_h+copper_t",
        impedance="50",
    ))

    all_steps.append(set_frequency_range("7", "12"))
    all_steps.append(set_boundary())

    for label, vba in all_steps:
        step += 1
        result = execute_step(ctrl, step, label, vba)
        if not result.get("success"):
            print("\n!! 步骤失败，终止。")
            return False

    step += 1
    farfield_vba = (
        'With Monitor\n'
        '    .Reset\n'
        '    .Name "farfield (f=9.4)"\n'
        '    .Domain "Frequency"\n'
        '    .FieldType "Farfield"\n'
        '    .MonitorValue "9.4"\n'
        '    .UseSubvolume "False"\n'
        '    .Create\n'
        'End With'
    )
    result = execute_step(ctrl, step, "add farfield monitor (9.4 GHz)", farfield_vba)
    if not result.get("success"):
        print("\n!! 远场监视器失败")
        return False

    step += 1
    efield_vba = (
        'With Monitor\n'
        '    .Reset\n'
        '    .Name "e-field (f=9.4)"\n'
        '    .Domain "Frequency"\n'
        '    .FieldType "Efield"\n'
        '    .MonitorValue "9.4"\n'
        '    .Create\n'
        'End With'
    )
    result = execute_step(ctrl, step, "add e-field monitor (9.4 GHz)", efield_vba)
    if not result.get("success"):
        print("\n!! E-field 监视器失败")
        return False

    print("\n" + "=" * 60)
    print(f"模型构建完成（{step} 步全部成功）")
    print("即将启动 Time Domain 求解器...")
    print("预计耗时 2-10 分钟，取决于网格密度")
    print("=" * 60)

    step += 1
    label, vba = run_solver()
    result = execute_step(ctrl, step, label, vba)
    if not result.get("success"):
        print(f"\n!! 求解器失败: {result.get('message')}")
        print("请在 CST 中检查错误信息")
        return False

    print("\n" + "=" * 60)
    print("求解完成！请在 CST 中查看：")
    print("  - 1D Results > S-Parameters > S1,1    → S11 回波损耗")
    print("  - Farfield > farfield (f=9.4)         → 远场方向图")
    print("  - 2D/3D Results > E-Field > e-field... → 电场分布")
    print("=" * 60)

    if importlib.util.find_spec("cst_agent_workbench.results.reader") is not None:
        print("\n提示：仿真完成后，可在 Gradio 界面中使用：")
        print('  open_results("你的工程路径.cst")')
        print('  get_s_parameter(1, 1)')
        print("来读取 S11 数据。")

    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
