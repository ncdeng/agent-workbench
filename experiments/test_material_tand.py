"""
验证 RO4350B 材料 TanD 是否真正生效
运行方式：python experiments/test_material_tand.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from cst_agent_workbench.cst.primitives import create_material, reset_created_objects, register_material
from cst_agent_workbench.cst.controller import CSTController


def fresh_project():
    """关闭所有旧工程，新建空白 MWS"""
    script = (
        'import json, time, cst.interface\n'
        'de = cst.interface.DesignEnvironment.connect_to_any_or_new()\n'
        'de.set_quiet_mode(False)\n'
        'for p in de.get_open_projects():\n'
        '    try: p.close()\n'
        '    except: pass\n'
        'time.sleep(1)\n'
        'proj = de.new_mws()\n'
        'time.sleep(2)\n'
        'print(json.dumps({"success": True}))\n'
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(script)
        sf = f.name
    try:
        r = subprocess.run([sys.executable, sf], capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout.strip()).get("success", False)
    finally:
        os.unlink(sf)


def main():
    reset_created_objects()

    print("1. Creating fresh CST project...")
    if not fresh_project():
        print("   FAILED to create fresh project")
        return
    print("   OK")

    print("2. Connecting controller...")
    ctrl = CSTController()
    connect_result = ctrl.connect()
    if not connect_result.get("success"):
        print(f"   FAILED: {ctrl.last_message}")
        return
    print(f"   OK: {ctrl.get_status()}")

    print("3. Creating RO4350B material (Dk=3.48, Df=0.0037)...")
    label, vba = create_material(
        "RO4350B",
        epsilon=3.48,
        tand=0.0037,
        tand_freq=9.4,
        colour_r="0.94",
        colour_g="0.82",
        colour_b="0.56",
    )
    print(f"   VBA ({len(vba.splitlines())} lines):")
    for line in vba.splitlines():
        print(f"     {line}")

    result = ctrl.execute_vba(vba, label=label)
    if result.get("success"):
        print("   OK - Material created successfully")
        register_material("RO4350B")
    else:
        print(f"   FAILED: {result.get('message')}")
        return

    print()
    print("=" * 60)
    print("Please check in CST:")
    print("  1. Navigation Tree > Materials > RO4350B > double-click")
    print("  2. Conductivity tab:")
    print("     - Tangent delta el. = 0.0037 (NOT greyed out)")
    print("     - Model: Const. Tangent Delta")
    print("     - Frequency: 9.4 GHz")
    print("=" * 60)


if __name__ == "__main__":
    main()
