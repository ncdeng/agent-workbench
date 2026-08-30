import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Sequence

from cst_agent_workbench import config

logger = logging.getLogger(__name__)

# 静态 CST 控制脚本，通过 sys.argv 接收参数，避免 f-string/缩进问题
# argv[1] = command: lifecycle / execution / result command
# argv[2] = project_path: CST 默认工程路径（可为空字符串）
# argv[3] = vba_file: VBA 临时文件路径（仅 execute 命令使用）
# argv[4] = label: history 条目标签（仅 execute 命令使用，默认 "cst_agent"）
_COM_SCRIPT = """\
import json
import os
import sys
import time

command = sys.argv[1]
project_path = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else ""
vba_file = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else ""
label = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else "cst_agent"
result_path = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else ""

try:
    import cst.interface

    # 优先连接已有 DE；如果没有，则自动启动新的 CST Design Environment
    de = cst.interface.DesignEnvironment.connect_to_any_or_new()
    # 启用安静模式，自动确认所有对话框（如 "Results May Get Incompatible With Model"），
    # 避免弹窗阻塞子进程。错误通过异常和超时机制捕获。
    de.set_quiet_mode(True)
    projects = de.get_open_projects()

    proj = None
    if command == "new_project":
        proj = de.new_mws()
        time.sleep(1)
        if project_path:
            folder = os.path.dirname(project_path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            proj.save(project_path)
            time.sleep(1)
    elif project_path:
        # 指定了目标工程路径：先在已打开工程中匹配
        norm_target = os.path.normcase(os.path.normpath(project_path))
        for p in projects:
            try:
                proj_file = os.path.normcase(os.path.normpath(str(p.filename())))
            except Exception:
                continue
            if proj_file == norm_target:
                proj = p
                break
        # 未匹配到则打开目标工程
        if proj is None and os.path.exists(project_path):
            proj = de.open_project(project_path)
            time.sleep(3)
        # 指定了路径但既未匹配也无法打开：报错，不 fallback 到别的工程
        if proj is None:
            print(json.dumps({"success": False, "message": "指定的工程路径未找到且无法打开: " + project_path}))
            sys.exit(0)
    elif projects:
        # 未指定路径：优先使用当前激活工程，回退到第一个已打开工程
        try:
            proj = de.active_project()
        except Exception:
            proj = projects[0]
    else:
        proj = de.new_mws()
        time.sleep(1)

    # model3d 是 MWS 3D 建模对象，包含 add_to_history
    mws = proj.model3d

    if command == "connect":
        proj_filename = str(proj.filename()) if proj else ""
        print(json.dumps({"success": True, "message": "已连接到 CST 工程（cst.interface，安静模式）", "project_file": proj_filename}))
    elif command == "new_project":
        proj_filename = str(proj.filename()) if proj else ""
        print(json.dumps({"success": True, "message": "已新建 CST 工程", "project_file": proj_filename}))
    elif command == "open_project":
        proj_filename = str(proj.filename()) if proj else ""
        print(json.dumps({"success": True, "message": "已打开 CST 工程", "project_file": proj_filename}))
    elif command == "save_project":
        include_results = str(label).lower() in {"1", "true", "yes", "on"}
        proj.save("", include_results, False)
        proj_filename = str(proj.filename()) if proj else ""
        print(json.dumps({
            "success": True,
            "message": "已保存 CST 工程",
            "project_file": proj_filename,
            "project_saved": True,
            "include_results": include_results,
        }))
    elif command == "save_project_as":
        if not result_path:
            print(json.dumps({"success": False, "message": "缺少另存为目标路径"}))
            sys.exit(0)
        include_results = str(label).lower() in {"1", "true", "yes", "on"}
        folder = os.path.dirname(result_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        proj.save(result_path, include_results, False)
        proj_filename = str(proj.filename()) if proj else result_path
        print(json.dumps({
            "success": True,
            "message": "CST 工程另存为成功",
            "project_file": proj_filename,
            "project_saved": True,
            "include_results": include_results,
        }))
    elif command == "execute":
        with open(vba_file, "r", encoding="utf-8") as f:
            vba_content = f.read()
        mws.add_to_history(label, vba_content)
        if project_path:
            proj.save(project_path)
        else:
            proj.save()
        proj_filename = str(proj.filename()) if proj else ""
        print(json.dumps({"success": True, "message": "VBA 已在 CST 中执行并保存工程", "project_file": proj_filename, "project_saved": True}))
    elif command == "execute_immediate":
        with open(vba_file, "r", encoding="utf-8") as f:
            vba_content = f.read()
        immediate = getattr(mws, "_execute_vba_code", None)
        if not callable(immediate):
            print(json.dumps({
                "success": False,
                "message": "当前 CST model3d 未暴露官方 bundled library 使用的 _execute_vba_code",
            }))
            sys.exit(0)
        immediate("Sub Main()\\n" + vba_content + "\\nEnd Sub")
        if project_path:
            proj.save(project_path)
        else:
            proj.save()
        proj_filename = str(proj.filename()) if proj else ""
        print(json.dumps({"success": True, "message": "VBA 已通过即时接口执行并保存工程（未写入 History）", "project_file": proj_filename, "project_saved": True}))
    elif command == "list_solids":
        output_path = result_path
        if not output_path:
            print(json.dumps({"success": False, "message": "缺少 solid inventory 输出路径"}))
            sys.exit(0)
        probe = (
            'Sub Main()\\n'
            'Dim i As Long\\n'
            'Dim fh As Integer\\n'
            'fh = FreeFile\\n'
            'Open "' + output_path.replace(chr(34), chr(34) * 2) + '" For Output As #fh\\n'
            'For i = 0 To Solid.GetNumberOfShapes() - 1\\n'
            '  Print #fh, Solid.GetNameOfShapeFromIndex(i)\\n'
            'Next i\\n'
            'Close #fh\\n'
            'End Sub'
        )
        immediate = getattr(mws, "_execute_vba_code", None)
        if not callable(immediate):
            print(json.dumps({"success": False, "message": "当前 CST model3d 未暴露 solid inventory 执行接口"}))
            sys.exit(0)
        immediate(probe)
        if not os.path.isfile(output_path):
            print(json.dumps({"success": False, "message": "solid inventory probe 未生成输出文件"}))
            sys.exit(0)
        names = []
        with open(output_path, "r", encoding="utf-8-sig", errors="replace") as handle:
            names = [line.strip() for line in handle if line.strip()]
        proj_filename = str(proj.filename()) if proj else ""
        print(json.dumps({"success": True, "message": "已读取 CST solid inventory", "project_file": proj_filename, "solids": names}))
    elif command == "evaluate_result_templates":
        try:
            proj.activate()
            time.sleep(1)
            mws.EvaluateResultTemplates()
            time.sleep(3)
            proj_filename = str(proj.filename()) if proj else ""
            print(json.dumps({"success": True, "message": "结果模板已触发", "project_file": proj_filename}))
        except Exception as exc:
            print(json.dumps({"success": False, "message": f"EvaluateResultTemplates 失败: {exc}"}))
    elif command == "run_solver_with_templates":
        mws.run_solver()
        time.sleep(2)
        # EvaluateResultTemplates is unreliable in subprocess; skip it
        proj_filename = str(proj.filename()) if proj else ""
        print(json.dumps({"success": True, "message": "求解器已完成", "project_file": proj_filename}))
    elif command == "run_solver":
        mws.run_solver()
        proj_filename = str(proj.filename()) if proj else ""
        print(json.dumps({"success": True, "message": "求解器已完成（原生 API）", "project_file": proj_filename}))
    elif command == "reopen_and_solve":
        # Close existing project, reopen fresh (so CST reads Model.rpp), then solve
        if proj:
            try:
                proj.close()
            except Exception as exc:
                print(f"failed to close CST project before reopen: {exc}", file=sys.stderr)
        time.sleep(3)
        if project_path and os.path.exists(project_path):
            proj = de.open_project(project_path)
            time.sleep(5)
            mws = proj.model3d
            mws.run_solver()
            time.sleep(8)  # wait for post-processing to complete
            proj_filename = str(proj.filename()) if proj else ""
            print(json.dumps({"success": True, "message": "工程重新打开并完成求解（farfield 模板已激活）", "project_file": proj_filename}))
        else:
            print(json.dumps({"success": False, "message": f"工程文件不存在: {project_path}"}))
    elif command in {"export_farfield_ascii", "export_result_ascii"}:
        if not result_path:
            print(json.dumps({"success": False, "message": "缺少 farfield 导出路径"}))
            sys.exit(0)
        folder = os.path.dirname(result_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(vba_file, "r", encoding="utf-8") as f:
            vba_code = f.read()
        import tempfile as _tf
        with _tf.NamedTemporaryFile(mode='w', suffix='.bas', delete=False, encoding='utf-8') as _f:
            _f.write(vba_code); _vpath = _f.name
        try:
            proj.activate()
            time.sleep(1)
            mws.RunScript(_vpath)
            time.sleep(2)
            proj_filename = str(proj.filename()) if proj else ""
            export_label = "Farfield ASCII" if command == "export_farfield_ascii" else "结果 ASCII"
            print(json.dumps({"success": True, "message": export_label + " 导出成功（RunScript）", "project_file": proj_filename, "result_file": result_path}))
        except Exception as exc:
            export_label = "Farfield" if command == "export_farfield_ascii" else "结果"
            print(json.dumps({"success": False, "message": export_label + f" RunScript 导出失败: {exc}"}))
        finally:
            try:
                os.unlink(_vpath)
            except OSError:
                pass
    elif command == "probe_farfield_tree_items":
        if not result_path:
            print(json.dumps({"success": False, "message": "缺少 farfield 探测输出路径"}))
            sys.exit(0)
        folder = os.path.dirname(result_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(vba_file, "r", encoding="utf-8") as f:
            vba_content = f.read()
        mws.add_to_history(label, vba_content)
        proj_filename = str(proj.filename()) if proj else ""
        print(json.dumps({"success": True, "message": "Farfield 结果树探测已在 CST 中执行", "project_file": proj_filename, "result_file": result_path}))
    elif command == "export_farfield_getlist":
        if not result_path:
            print(json.dumps({"success": False, "message": "缺少 farfield GetList 导出路径"}))
            sys.exit(0)
        folder = os.path.dirname(result_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(vba_file, "r", encoding="utf-8") as f:
            vba_content = f.read()
        import tempfile as _tf2
        with _tf2.NamedTemporaryFile(mode='w', suffix='.bas', delete=False, encoding='utf-8') as _f2:
            _f2.write(vba_content); _vpath2 = _f2.name
        try:
            proj.activate()
            time.sleep(1)
            mws.RunScript(_vpath2)
            time.sleep(2)
            proj_filename = str(proj.filename()) if proj else ""
            print(json.dumps({"success": True, "message": "Farfield GetList 数值导出已在 CST 中执行（RunScript）", "project_file": proj_filename, "result_file": result_path}))
        except Exception as exc:
            print(json.dumps({"success": False, "message": f"Farfield GetList RunScript 失败: {exc}"}))
        finally:
            try:
                os.unlink(_vpath2)
            except OSError:
                pass
    elif command == "list_farfield_tree_items":
        result_tree = getattr(mws, "ResultTree", None)
        if result_tree is None:
            result_tree = getattr(mws, "resulttree", None)
        if result_tree is None:
            print(json.dumps({"success": False, "message": "当前 CST model3d API 未暴露 ResultTree 对象"}))
            sys.exit(0)

        items = []
        ffname = result_tree.GetFirstChildName("Farfields")
        if ffname == "Farfields\\Farfield Cuts":
            ffname = result_tree.GetNextItemName(ffname)
        while ffname:
            items.append(str(ffname))
            ffname = result_tree.GetNextItemName(ffname)
        proj_filename = str(proj.filename()) if proj else ""
        print(json.dumps({"success": True, "message": f"找到 {len(items)} 个 Farfields 结果树项", "project_file": proj_filename, "items": items}))
    elif command == "get_active_project":
        # 尝试获取当前聚焦的工程路径。
        # 注意：CST Python API 没有明确的 "active/focused project" 概念，
        # 这里先尝试 de.active_project（如果新版本支持），否则回退到 projects[0]。
        active_proj = None
        if projects:
            # 尝试获取真正 active project（如果 API 支持）
            try:
                active_proj = de.active_project()
            except TypeError:
                active_proj = de.active_project
            except AttributeError:
                # API 不支持，回退到第一个已打开工程
                active_proj = projects[0]
            active_filename = str(active_proj.filename()) if active_proj else ""
            print(json.dumps({"success": True, "project_file": active_filename}))
        else:
            print(json.dumps({"success": False, "message": "当前无打开的工程"}))
    elif command == "close_project":
        proj_filename = str(proj.filename()) if proj else ""
        proj.close()
        print(json.dumps({"success": True, "message": "已关闭 CST 工程", "project_file": proj_filename}))
    elif command == "set_mesh":
        import json as _j
        steps = int(label) if label and label.isdigit() else 15
        minimum_steps = int(float(result_path)) if result_path else 5
        vba_mesh = f'''With Mesh
    .MeshType "PBA"
    .LinesPerWavelength {steps}
    .MinimumStepNumber {minimum_steps}
    .Automesh "True"
End With'''
        try:
            mws.add_to_history("set_global_hexahedral_mesh", vba_mesh)
            proj_filename = str(proj.filename()) if proj else ""
            print(_j.dumps({"success": True, "message": f"网格已写入历史 lines_per_wavelength={steps} minimum_step_number={minimum_steps}", "project_file": proj_filename}))
        except Exception as exc:
            print(_j.dumps({"success": False, "message": f"set_mesh 失败: {exc}"}))
    else:
        print(json.dumps({"success": False, "message": "未知命令: " + command}))

except Exception as e:
    print(json.dumps({"success": False, "message": str(e)}))
"""


_CST_SUPPORTED_PYTHON_MINORS = {8, 9, 10, 11, 12}


def _current_python_supports_cst() -> bool:
    return sys.version_info.major == 3 and sys.version_info.minor in _CST_SUPPORTED_PYTHON_MINORS


def _candidate_supports_cst_python(command: Sequence[str]) -> bool:
    probe = (
        "import sys\n"
        "ok = sys.version_info.major == 3 and sys.version_info.minor in {8, 9, 10, 11, 12}\n"
        "if not ok:\n"
        "    raise SystemExit(1)\n"
        "try:\n"
        "    import cst.interface\n"
        "except Exception:\n"
        "    raise SystemExit(1)\n"
    )
    try:
        result = subprocess.run(
            list(command) + ["-c", probe],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _cst_python_candidates() -> list[list[str]]:
    if os.name == "nt":
        return [["py", f"-3.{minor}"] for minor in (12, 11, 10, 9, 8)]
    return [[f"python3.{minor}"] for minor in (12, 11, 10, 9, 8)]


def _resolve_cst_python_command() -> list[str]:
    configured = getattr(config, "CST_PYTHON_EXECUTABLE", "").strip()
    if configured:
        return [configured]
    if _current_python_supports_cst():
        return [sys.executable]
    for command in _cst_python_candidates():
        if _candidate_supports_cst_python(command):
            return command
    return []


class CSTController:
    """负责管理 CST 连接与 VBA 执行。通过子进程调用，避免线程中的接口问题。"""

    def __init__(self):
        self.connected = False
        self.offline_mode = False
        self.cst_exe: Optional[str] = None
        self.last_message = "尚未连接 CST"
        self.project_path = ""
        self.cst_python_command = _resolve_cst_python_command()
        self._find_cst_exe()

    def _find_cst_exe(self) -> Optional[str]:
        for path in config.CST_POSSIBLE_PATHS:
            if os.path.exists(path):
                self.cst_exe = path
                return path
        self.cst_exe = None
        return None

    def _run_com_script(self, args: list, timeout: int = 30) -> dict:
        """将静态控制脚本写入临时文件，通过命令行参数传递数据。"""
        try:
            temp_dir = Path(config.CST_TEMP_DIR)
            temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8", dir=temp_dir
            ) as f:
                f.write(_COM_SCRIPT)
                script_file = f.name
        except Exception as exc:
            return {"success": False, "message": f"写入脚本文件失败: {exc}"}

        try:
            python_command = self.cst_python_command if hasattr(self, "cst_python_command") else _resolve_cst_python_command()
            if not python_command:
                return {
                    "success": False,
                    "message": "未找到 CST 支持的 Python 3.8-3.12；请安装 Python 3.12 并确保可 import cst.interface，或设置 CST_PYTHON_EXECUTABLE",
                }
            env = os.environ.copy()
            env["TMP"] = str(temp_dir)
            env["TEMP"] = str(temp_dir)
            result = subprocess.run(
                python_command + [script_file] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
            )
            if result.returncode == 0 and result.stdout.strip():
                # 只解析 stdout 最后一行 JSON，忽略 cst.interface 可能输出的 warning
                stdout_lines = result.stdout.strip().splitlines()
                for line in reversed(stdout_lines):
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            return json.loads(line)
                        except json.JSONDecodeError:
                            continue
                return {
                    "success": False,
                    "message": f"子进程输出中未找到有效 JSON: {result.stdout.strip()[-200:]}",
                }
            # returncode != 0：CST/Python traceback 通常在 stderr，优先取 stderr；
            # 仅当 stderr 完全为空时才回退到 stdout（例如子进程 crashed 前只 print 到 stdout）。
            stderr_text = result.stderr.strip()
            if not stderr_text:
                stderr_text = result.stdout.strip()
            return {
                "success": False,
                "message": f"子进程执行失败 (returncode={result.returncode}): {stderr_text[-200:]}",
            }
        except subprocess.TimeoutExpired:
            # subprocess.run 超时时会 SIGKILL 子进程，但 CST Design Environment 是
            # connect_to_any_or_new 拉起的独立 OS 进程，子进程死了 DE 还在，且可能仍卡在
            # 上一次 run_solver 调用里。我们无法从 Python 跨进程 abort DE 内的求解，
            # 只能：(1) 标记 connected=False，强制下次命令走 reconnect 路径；
            # (2) 让调用方知道上游 CST 可能处于不可用状态，由它决定是否提示用户。
            self.connected = False
            return {
                "success": False,
                "message": (
                    f"CST 操作超时（{timeout}秒）。子进程已终止，但 CST Design Environment "
                    "可能仍在执行上一次求解；下次命令将尝试重连。如 CST GUI 卡死请手动终止求解。"
                ),
                "timeout": True,
            }
        except Exception as exc:
            return {"success": False, "message": f"子进程异常: {exc}"}
        finally:
            try:
                os.unlink(script_file)
            except OSError:
                pass

    def connect(self) -> dict:
        """尝试自动连接或启动 CST，并确保有工程打开。"""
        self.connected = False
        self.offline_mode = False

        default_project = config.CST_DEFAULT_PROJECT.strip() if config.CST_DEFAULT_PROJECT else ""
        attempts = max(1, config.CST_CONNECT_RETRIES + 1)
        result = {}
        for attempt in range(1, attempts + 1):
            result = self._run_com_script(
                ["connect", default_project],
                timeout=config.CST_CONNECT_TIMEOUT_SEC,
            )
            if result.get("success"):
                break
            if attempt < attempts:
                time.sleep(max(0, config.CST_CONNECT_RETRY_DELAY_SEC))

        if result.get("success"):
            self.connected = True
            self.offline_mode = False
            self.last_message = result["message"]
            self.project_path = result.get("project_file", "")
        else:
            self.connected = False
            self.offline_mode = True
            prefix = "自动连接或启动 CST 失败。" if self.cst_exe else "未找到 CST 安装路径。"
            retry_note = f"（已尝试 {attempts} 次）" if attempts > 1 else ""
            self.last_message = f"{prefix}{retry_note}（{result['message']}）"
            self.project_path = ""

        result["message"] = self.last_message
        result["mode"] = "online" if self.connected else "offline"
        return result

    def _query_best_effort_project_path(self) -> Optional[str]:
        """尝试从 CST DE 查询当前聚焦的工程路径（best-effort）。

        注意：CST Python API 没有明确的 "active/focused project" 接口。
        此方法是近似查询：优先尝试 de.active_project（如果新版本支持），
        否则回退到已打开工程列表的第一个（projects[0]）。

        多工程场景限制：如果用户同时打开多个工程并在 GUI 中切换，
        此方法可能无法准确追踪到用户真正聚焦的工程，只能作为尽力而为的 fallback。

        返回：工程路径字符串，或 None（查询失败/无工程）
        """
        if self.offline_mode or not self.connected:
            return None
        try:
            result = self._run_com_script(["get_active_project", ""], timeout=10)
            if result.get("success") and result.get("project_file"):
                return result["project_file"]
        except Exception as exc:
            logger.debug("best-effort active CST project query failed: %s", exc)
        return None

    def _resolve_project_for_command(self) -> str:
        """优先使用已记录的目标工程；为空时再尝试查询当前激活工程。"""
        if self.project_path:
            return self.project_path
        queried_path = self._query_best_effort_project_path()
        if queried_path:
            self.project_path = queried_path
            return queried_path
        return config.CST_DEFAULT_PROJECT.strip() if config.CST_DEFAULT_PROJECT else ""

    def _update_project_path_from_result(self, result: dict, requested_project: str = "") -> None:
        project_file = result.get("project_file")
        if project_file and requested_project and os.path.normcase(os.path.normpath(project_file)) != os.path.normcase(os.path.normpath(requested_project)):
            self.project_path = requested_project
            return
        if project_file:
            self.project_path = project_file

    def evaluate_result_templates(self, timeout: int = 60) -> dict:
        """单独触发 CST 结果模板（不重新求解），用于已有结果时补充导出。"""
        if self.offline_mode or not self.connected:
            return {"success": False, "message": "当前为离线模式"}
        project = self._resolve_project_for_command()
        result = self._run_com_script(
            ["evaluate_result_templates", project],
            timeout=timeout,
        )
        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        return result

    def run_solver_with_templates(self, timeout: int = 300) -> dict:
        """运行求解器，求解完成后调用 EvaluateResultTemplates 触发模板导出（如 farfield ASCII）。"""
        if self.offline_mode or not self.connected:
            return {"success": False, "message": "当前为离线模式"}
        project = self._resolve_project_for_command()
        result = self._run_com_script(
            ["run_solver_with_templates", project],
            timeout=timeout,
        )
        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        return result

    def reopen_project_and_solve(self, timeout: int = 600) -> dict:
        """关闭并重新打开工程，让 CST 读取 Model.rpp 模板配置，然后求解。
        用于在安装 farfield export template 后需要让 CST 感知新配置时。"""
        if self.offline_mode or not self.connected:
            return {"success": False, "message": "当前为离线模式"}
        project = self._resolve_project_for_command()
        if not project:
            return {"success": False, "message": "未指定工程路径"}
        # Close
        close_result = self._run_com_script(["close_project", project], timeout=30)
        if not close_result.get("success"):
            # If close fails, just try to solve anyway
            return self.run_solver(timeout=timeout)
        import time
        time.sleep(2)
        # Reopen and solve
        result = self._run_com_script(["reopen_and_solve", project], timeout=timeout)
        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        return result

    def run_solver(self, timeout: int = 300) -> dict:
        """通过原生 Python API (model3d.run_solver()) 运行求解器。
        自带参数化更新，不走 add_to_history 的 structure macro 上下文。"""
        if self.offline_mode or not self.connected:
            self.last_message = "当前为离线模式，无法运行求解器"
            return {"success": False, "message": self.last_message}

        # 尝试查询当前聚焦的工程（best-effort），多工程场景可能不准确
        queried_path = self._query_best_effort_project_path()
        if queried_path:
            self.project_path = queried_path

        # 优先使用已连接/查询到的工程路径
        project = self._resolve_project_for_command()
        result = self._run_com_script(["run_solver", project], timeout=timeout)
        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        return result

    def new_project(self, project_path: str = "", timeout: int = 60) -> dict:
        """新建一个空白 CST 工程，并在提供路径时立即保存。"""
        bootstrapping_connection = self.offline_mode or not self.connected
        if bootstrapping_connection and not project_path:
            self.last_message = "未连接 CST 时新建工程必须提供明确保存路径"
            return {"success": False, "message": self.last_message}

        if project_path and os.path.exists(project_path):
            self.last_message = f"目标工程已存在，拒绝覆盖: {project_path}"
            return {"success": False, "message": self.last_message, "error_type": "target_exists"}
        result = self._run_com_script(["new_project", project_path], timeout=timeout)
        self.last_message = result.get("message", "")
        if result.get("success"):
            self.connected = True
            self.offline_mode = False
            self._update_project_path_from_result(result, project_path)
            if not result.get("project_file") and project_path:
                self.project_path = project_path
        return result

    def open_project(self, project_path: str, timeout: int = 60) -> dict:
        """打开一个已存在的 CST 工程并将其设为当前 controller 工程。"""
        if not project_path:
            return {"success": False, "message": "project_path 不能为空"}
        if not os.path.exists(project_path):
            return {"success": False, "message": f"工程文件不存在: {project_path}"}
        result = self._run_com_script(["open_project", project_path], timeout=timeout)
        self.last_message = result.get("message", "")
        if result.get("success"):
            self.connected = True
            self.offline_mode = False
            self._update_project_path_from_result(result, project_path)
        return result

    def save_project(self, include_results: bool = True, timeout: int = 60) -> dict:
        """保存当前工程；官方 CST 2025.2 ``Project.save`` 支持结果包含开关。"""
        if self.offline_mode or not self.connected:
            return {"success": False, "message": "当前为离线模式，无法保存 CST 工程"}
        project = self._resolve_project_for_command()
        if not project:
            return {"success": False, "message": "当前 CST 工程尚无可保存路径"}
        result = self._run_com_script(
            ["save_project", project, "", str(bool(include_results)).lower()],
            timeout=timeout,
        )
        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        return result

    def save_project_as(
        self,
        target_path: str,
        include_results: bool = True,
        timeout: int = 90,
    ) -> dict:
        """将当前工程另存到新路径；第一版始终禁止覆盖已有文件。"""
        if self.offline_mode or not self.connected:
            return {"success": False, "message": "当前为离线模式，无法另存 CST 工程"}
        if not target_path:
            return {"success": False, "message": "target_path 不能为空"}
        if os.path.exists(target_path):
            return {
                "success": False,
                "message": f"另存为目标已存在，拒绝覆盖: {target_path}",
                "error_type": "target_exists",
            }
        project = self._resolve_project_for_command()
        if not project:
            return {"success": False, "message": "当前无可另存的 CST 工程"}
        result = self._run_com_script(
            [
                "save_project_as",
                project,
                "",
                str(bool(include_results)).lower(),
                target_path,
            ],
            timeout=timeout,
        )
        self.last_message = result.get("message", "")
        if result.get("success"):
            self._update_project_path_from_result(result, target_path)
            if not result.get("project_file"):
                self.project_path = target_path
        return result

    def close_project(self, project_path: str, timeout: int = 30) -> dict:
        """关闭指定工程，便于清理旧的 fast-path 临时文件。"""
        if self.offline_mode or not self.connected:
            self.last_message = "当前为离线模式，无法关闭 CST 工程"
            return {"success": False, "message": self.last_message}
        if not project_path:
            return {"success": False, "message": "project_path 不能为空"}

        result = self._run_com_script(["close_project", project_path], timeout=timeout)
        self.last_message = result.get("message", "")
        if result.get("success") and self.project_path == project_path:
            self.project_path = ""
        return result

    def set_mesh_by_frequency(self, f0_ghz: float, epsilon_r: float = 1.0, steps: int = 15, timeout: int = 30, project_path: str = "") -> dict:
        """Deprecated compatibility wrapper for global hexahedral mesh."""
        if f0_ghz <= 0 or epsilon_r <= 0 or steps < 2:
            return {"success": False, "message": "f0_ghz、epsilon_r 必须 > 0，steps 必须 >= 2"}
        if self.offline_mode or not self.connected:
            return {"success": False, "message": "离线模式，跳过网格设置"}
        return self.set_global_hexahedral_mesh(
            lines_per_wavelength=steps,
            minimum_step_number=5,
            timeout=timeout,
            project_path=project_path,
        )

    def set_global_hexahedral_mesh(
        self,
        lines_per_wavelength: int = 15,
        minimum_step_number: int = 5,
        timeout: int = 30,
        project_path: str = "",
    ) -> dict:
        """Set CST's official PBA hexahedral automesh controls."""
        if lines_per_wavelength < 2 or minimum_step_number < 1:
            return {"success": False, "message": "lines_per_wavelength 必须 >= 2，minimum_step_number 必须 >= 1"}
        if self.offline_mode or not self.connected:
            return {"success": False, "message": "离线模式，跳过网格设置"}
        proj_path = project_path or self.project_path
        result = self._run_com_script(
            ["set_mesh", proj_path, "", str(lines_per_wavelength), str(minimum_step_number)],
            timeout=timeout,
        )
        self.last_message = result.get("message", "")
        return result

    def get_mesh_signature(self, timeout: int = 30) -> dict:
        """Fail closed until this CST version exposes a verified realized-mesh getter.

        Configured LinesPerWavelength/MinimumStepNumber are deliberately not
        returned as a mesh signature: History acceptance does not prove the
        generated mesh changed or report its cell count.
        """
        del timeout
        return {
            "success": False,
            "error_type": "unsupported_mesh_signature",
            "message": (
                "CST 2025 本机 Online Help 与已审计接口中尚未定位到可验证的 actual mesh "
                "cell-count/statistics getter；拒绝用配置值冒充 realized mesh signature"
            ),
            "configured_mesh_is_not_signature": True,
        }

    def execute_vba(self, vba_code: str, label: str = "cst_agent", timeout: int = 60) -> dict:
        """通过临时文件将 VBA 传给子进程，再用 add_to_history 在 CST 中执行。"""
        if self.offline_mode or not self.connected:
            self.last_message = "当前为离线模式，将只生成脚本，不会在 CST 中自动执行"
            return {"success": True, "executed": False, "message": self.last_message, "vba_code": vba_code}

        # 尝试查询当前聚焦的工程（best-effort），多工程场景可能不准确
        queried_path = self._query_best_effort_project_path()
        if queried_path:
            self.project_path = queried_path

        raw_vba = vba_code.strip()
        # 健壮地剥离 Sub Main() / End Sub 包装（处理空格、大小写、尾部空行）
        lines = raw_vba.splitlines()
        if lines and re.match(r'^\s*Sub\s+Main\s*\(\s*\)\s*$', lines[0], re.IGNORECASE):
            end_idx = len(lines) - 1
            while end_idx > 0:
                if re.match(r'^\s*End\s+Sub\s*$', lines[end_idx], re.IGNORECASE):
                    break
                end_idx -= 1
            if end_idx > 0:
                raw_vba = "\n".join(lines[1:end_idx]).strip()

        try:
            temp_dir = Path(config.CST_TEMP_DIR)
            if temp_dir.drive.upper() != "D:":
                return {
                    "success": False,
                    "executed": False,
                    "message": "CST VBA 临时目录必须位于 D 盘",
                    "vba_code": vba_code,
                }
            temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".vba", delete=False, encoding="utf-8", dir=temp_dir
            ) as f:
                f.write(raw_vba)
                vba_file = f.name
        except Exception as exc:
            return {"success": False, "message": f"写入临时文件失败: {exc}", "vba_code": vba_code}

        # 优先使用当前已连接的工程路径，避免多工程场景下操作错工程
        project = self._resolve_project_for_command()
        result = self._run_com_script(
            ["execute", project, vba_file, label], timeout=timeout
        )

        try:
            os.unlink(vba_file)
        except OSError:
            pass

        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        result["vba_code"] = vba_code
        # 显式给出 executed 信号：调用方据此决定是否登记建模副作用。
        # 子进程脚本不返回该字段，这里以 success 为准补齐（离线分支已显式 False）。
        result.setdefault("executed", bool(result.get("success", False)))
        if result["executed"]:
            result.setdefault("verification", "history_accepted")
        return result

    def execute_vba_immediate(self, vba_code: str, timeout: int = 60) -> dict:
        """Execute control VBA immediately without creating a History step."""

        if self.offline_mode or not self.connected:
            self.last_message = "当前为离线模式，无法即时执行 CST 控制命令"
            return {
                "success": False,
                "executed": False,
                "message": self.last_message,
                "vba_code": vba_code,
            }
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        queried_path = self._query_best_effort_project_path()
        if queried_path:
            self.project_path = queried_path
        raw_vba = vba_code.strip()
        lines = raw_vba.splitlines()
        if lines and re.match(r'^\s*Sub\s+Main\s*\(\s*\)\s*$', lines[0], re.IGNORECASE):
            end_idx = len(lines) - 1
            while end_idx > 0:
                if re.match(r'^\s*End\s+Sub\s*$', lines[end_idx], re.IGNORECASE):
                    break
                end_idx -= 1
            if end_idx > 0:
                raw_vba = "\n".join(lines[1:end_idx]).strip()

        try:
            temp_dir = Path(config.CST_TEMP_DIR)
            if temp_dir.drive.upper() != "D:":
                return {
                    "success": False,
                    "executed": False,
                    "message": "CST VBA 临时目录必须位于 D 盘",
                    "vba_code": vba_code,
                }
            temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".vba", delete=False, encoding="utf-8", dir=temp_dir
            ) as handle:
                handle.write(raw_vba)
                vba_file = handle.name
        except Exception as exc:
            return {
                "success": False,
                "executed": False,
                "message": f"写入临时文件失败: {exc}",
                "vba_code": vba_code,
            }

        project = self._resolve_project_for_command()
        result = self._run_com_script(
            ["execute_immediate", project, vba_file], timeout=timeout
        )
        try:
            os.unlink(vba_file)
        except OSError:
            pass
        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        result["vba_code"] = vba_code
        result.setdefault("executed", bool(result.get("success", False)))
        return result

    def list_farfield_tree_items(self, timeout: int = 30) -> dict:
        """直接通过 CST 在线 ResultTree 枚举 Farfields 下的真实结果树项。"""
        if self.offline_mode or not self.connected:
            self.last_message = "当前为离线模式，无法列出 CST Farfields 结果树"
            return {"success": False, "message": self.last_message}

        queried_path = self._query_best_effort_project_path()
        if queried_path:
            self.project_path = queried_path

        project = self._resolve_project_for_command()
        result = self._run_com_script(["list_farfield_tree_items", project], timeout=timeout)
        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        return result

    def probe_farfield_tree_items(self, output_path: str, timeout: int = 60) -> dict:
        """通过 VBA 探测在线 ResultTree 中 Farfields 下的真实子项，并写入文本文件。"""
        if self.offline_mode or not self.connected:
            self.last_message = "当前为离线模式，无法探测 CST Farfields 结果树"
            return {"success": False, "message": self.last_message}
        if not output_path:
            return {"success": False, "message": "output_path 不能为空"}

        queried_path = self._query_best_effort_project_path()
        if queried_path:
            self.project_path = queried_path

        output_path = str(Path(output_path))
        safe_output = output_path.replace('"', '""')
        vba_code = (
            'Dim ffname As String\n'
            'Dim itemCount As Long\n'
            'Open "' + safe_output + '" For Output As #1\n'
            'Print #1, "[Farfields]"\n'
            'ffname = Resulttree.GetFirstChildName("Farfields")\n'
            'If ffname = "Farfields\\Farfield Cuts" Then ffname = Resulttree.GetNextItemName(ffname)\n'
            'Do While ffname <> ""\n'
            '    Print #1, ffname\n'
            '    itemCount = itemCount + 1\n'
            '    ffname = Resulttree.GetNextItemName(ffname)\n'
            'Loop\n'
            'If itemCount = 0 Then\n'
            '    Print #1, "<empty>"\n'
            'End If\n'
            'Close #1\n'
        )

        try:
            temp_dir = Path(config.CST_TEMP_DIR)
            temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".vba", delete=False, encoding="utf-8", dir=temp_dir
            ) as f:
                f.write(vba_code)
                vba_file = f.name
        except Exception as exc:
            return {"success": False, "message": f"写入临时文件失败: {exc}"}

        project = self._resolve_project_for_command()
        result = self._run_com_script(
            ["probe_farfield_tree_items", project, vba_file, "probe_farfield_tree_items", output_path],
            timeout=timeout,
        )

        try:
            os.unlink(vba_file)
        except OSError:
            pass

        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        result["output_path"] = output_path
        result["vba_code"] = vba_code
        return result

    def export_farfield_ascii(
        self,
        item_path: str,
        output_path: str,
        theta_step_deg: float = 5.0,
        phi_step_deg: float = 5.0,
        plot_mode: str = "gain",
        use_db: bool = True,
        timeout: int = 120,
    ) -> dict:
        """通过 CST 在线 FarfieldPlot + ASCIIExport 导出单个 farfield 监视器到文本文件。"""
        if self.offline_mode or not self.connected:
            self.last_message = "当前为离线模式，无法导出 farfield ASCII"
            return {"success": False, "message": self.last_message}
        if not item_path:
            return {"success": False, "message": "item_path 不能为空"}
        if not output_path:
            return {"success": False, "message": "output_path 不能为空"}

        queried_path = self._query_best_effort_project_path()
        if queried_path:
            self.project_path = queried_path

        normalized_item = item_path.replace("/", "\\").strip()
        output_path = str(Path(output_path))
        safe_item = normalized_item.replace('"', '""')
        safe_output = output_path.replace('"', '""')
        mode_value = (plot_mode or "gain").strip().lower()
        plot_mode_name = {
            "gain": "Gain",
            "directivity": "Directivity",
            "realizedgain": "Realized Gain",
            "realized_gain": "Realized Gain",
            "efield": "Efield",
            "rcs": "RCS",
        }.get(mode_value, "Gain")
        linear_flag = "False" if use_db else "True"
        theta_step = max(0.1, float(theta_step_deg))
        phi_step = max(0.1, float(phi_step_deg))

        vba_code = (
            'Sub Main\n'
            '    FarfieldPlot.StoreAllSettings "cst_agent_export_farfield"\n'
            '    FarfieldPlot.Reset\n'
            '    FarfieldPlot.PlotType "3d"\n'
            f'    SelectTreeItem "{safe_item}"\n'
            '    FarfieldPlot.SetLockSteps False\n'
            f'    FarfieldPlot.Step {theta_step:.6f}\n'
            f'    FarfieldPlot.Step2 {phi_step:.6f}\n'
            '    FarfieldPlot.Plot\n'
            '    FarfieldPlot.UseFarfieldApproximation True\n'
            f'    FarfieldPlot.SetPlotMode "{plot_mode_name}"\n'
            f'    FarfieldPlot.SetScaleLinear {linear_flag}\n'
            '    FarfieldPlot.SetLogRange 50\n'
            '    FarfieldPlot.DBUnit "0"\n'
            '    FarfieldPlot.Plot\n'
            '    Plot.Update\n'
            '    With ASCIIExport\n'
            '        .Reset\n'
            '        .SetVersion "2010"\n'
            f'        .FileName "{safe_output}"\n'
            '        .Execute\n'
            '    End With\n'
            '    FarfieldPlot.RestoreAllSettings "cst_agent_export_farfield"\n'
            'End Sub\n'
        )

        try:
            temp_dir = Path(config.CST_TEMP_DIR)
            temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".vba", delete=False, encoding="utf-8", dir=temp_dir
            ) as f:
                f.write(vba_code)
                vba_file = f.name
        except Exception as exc:
            return {"success": False, "message": f"写入临时文件失败: {exc}"}

        project = self._resolve_project_for_command()
        result = self._run_com_script(
            ["export_farfield_ascii", project, vba_file, "export_farfield_ascii", output_path],
            timeout=timeout,
        )

        try:
            os.unlink(vba_file)
        except OSError:
            pass

        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        result["output_path"] = output_path
        result["item_path"] = normalized_item
        result["vba_code"] = vba_code
        return result

    def list_solids(self, timeout: int = 60) -> dict:
        """Read the current model's solid names without adding a History step."""
        if self.offline_mode or not self.connected:
            return {"success": False, "message": "当前为离线模式，无法读取 solid inventory"}
        project = self._resolve_project_for_command()
        temp_root = Path(config.CST_TEMP_DIR)
        if temp_root.drive.upper() != "D:":
            return {"success": False, "message": "solid inventory 临时目录必须位于 D 盘"}
        try:
            temp_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"success": False, "message": f"无法创建 solid inventory 临时目录: {exc}"}
        inventory_path = temp_root / f"cst_solids_{os.getpid()}_{int(time.time() * 1000)}.txt"
        try:
            result = self._run_com_script(
                ["list_solids", project, "", "", str(inventory_path)],
                timeout=timeout,
            )
        finally:
            try:
                inventory_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("failed to remove solid inventory artifact %s", inventory_path)
        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        return result

    def export_result_ascii(
        self,
        item_path: str,
        output_path: str,
        timeout: int = 120,
    ) -> dict:
        """Export a selected CST result-tree item with the official ASCIIExport object."""

        if self.offline_mode or not self.connected:
            self.last_message = "当前为离线模式，无法导出结果 ASCII"
            return {"success": False, "message": self.last_message}
        if not item_path:
            return {"success": False, "message": "item_path 不能为空"}
        if not output_path:
            return {"success": False, "message": "output_path 不能为空"}

        normalized_item = item_path.replace("/", "\\").strip()
        normalized_output = os.path.normpath(str(Path(output_path)))
        safe_item = normalized_item.replace('"', '""')
        safe_output = normalized_output.replace('"', '""')
        vba_code = (
            "Sub Main\n"
            f'    If Not SelectTreeItem("{safe_item}") Then Err.Raise 5, , "Result tree item not found"\n'
            "    With ASCIIExport\n"
            "        .Reset\n"
            f'        .FileName "{safe_output}"\n'
            "        .Execute\n"
            "    End With\n"
            "End Sub\n"
        )

        try:
            temp_dir = Path(config.CST_TEMP_DIR)
            temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".vba",
                delete=False,
                encoding="utf-8",
                dir=temp_dir,
            ) as f:
                f.write(vba_code)
                vba_file = f.name
        except Exception as exc:
            return {"success": False, "message": f"写入结果导出脚本失败: {exc}"}

        project = self._resolve_project_for_command()
        result = self._run_com_script(
            ["export_result_ascii", project, vba_file, "export_result_ascii", normalized_output],
            timeout=timeout,
        )
        try:
            os.unlink(vba_file)
        except OSError:
            pass

        if result.get("success"):
            output = Path(normalized_output)
            if not output.is_file() or output.stat().st_size <= 0:
                result = {
                    **result,
                    "success": False,
                    "message": "CST 报告导出成功，但输出文件不存在或为空",
                }
        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        result["output_path"] = normalized_output
        result["item_path"] = normalized_item
        result["vba_code"] = vba_code
        return result

    def get_farfield_numeric(
        self,
        item_path: str,
        output_path: str,
        theta_step_deg: float = 5.0,
        phi_step_deg: float = 5.0,
        plot_mode: str = "gain",
        use_db: bool = True,
        timeout: int = 120,
    ) -> dict:
        """通过 CST 在线 FarfieldPlot CalculateList/GetList 导出 farfield 数值到 JSON 文件。"""
        if self.offline_mode or not self.connected:
            self.last_message = "当前为离线模式，无法提取 farfield 数值"
            return {"success": False, "message": self.last_message}
        if not item_path:
            return {"success": False, "message": "item_path 不能为空"}
        if not output_path:
            return {"success": False, "message": "output_path 不能为空"}

        queried_path = self._query_best_effort_project_path()
        if queried_path:
            self.project_path = queried_path

        normalized_item = item_path.replace("/", "\\").strip()
        output_path = str(Path(output_path))
        safe_item = normalized_item.replace('"', '""')
        safe_output = output_path.replace('"', '""')
        mode_value = (plot_mode or "gain").strip().lower()
        plot_mode_name = {
            "gain": "Gain",
            "directivity": "Directivity",
            "realizedgain": "Realized Gain",
            "realized_gain": "Realized Gain",
            "efield": "Efield",
            "rcs": "RCS",
        }.get(mode_value, "Gain")
        linear_flag = "False" if use_db else "True"
        theta_step = max(0.1, float(theta_step_deg))
        phi_step = max(0.1, float(phi_step_deg))

        vba_code = (
            'FarfieldPlot.StoreAllSettings("cst_agent_getlist_farfield")\n'
            'FarfieldPlot.Reset\n'
            f'SelectTreeItem "{safe_item}"\n'
            'FarfieldPlot.PlotType("3d")\n'
            'FarfieldPlot.SetLockSteps(False)\n'
            f'FarfieldPlot.Step({theta_step:.6f})\n'
            f'FarfieldPlot.Step2({phi_step:.6f})\n'
            'FarfieldPlot.UseFarfieldApproximation(True)\n'
            f'FarfieldPlot.SetPlotMode("{plot_mode_name}")\n'
            f'FarfieldPlot.SetScaleLinear({linear_flag})\n'
            'FarfieldPlot.SetLogRange(50)\n'
            'FarfieldPlot.DBUnit("0")\n'
            'FarfieldPlot.Plot\n'
            'Plot.Update\n'
            'Dim thetaVal As Double, phiVal As Double\n'
            f'For thetaVal = 0 To 180 + 0.01 * {theta_step:.6f} Step {theta_step:.6f}\n'
            f'    For phiVal = 0 To 360 + 0.01 * {phi_step:.6f} Step {phi_step:.6f}\n'
            '        FarfieldPlot.AddListEvaluationPoint(thetaVal, phiVal, 0, "spherical", "", 0)\n'
            '    Next phiVal\n'
            'Next thetaVal\n'
            'FarfieldPlot.CalculateList("")\n'
            'Dim thetaList, phiList, valueList\n'
            'thetaList = FarfieldPlot.GetList("Point_T")\n'
            'phiList = FarfieldPlot.GetList("Point_P")\n'
            'valueList = FarfieldPlot.GetList("spherical abs")\n'
            'With ASCIIExport\n'
            '    .Reset\n'
            '    .SetVersion "2010"\n'
            f'    .FileName "{safe_output}"\n'
            '    .Mode("FixedWidth")\n'
            '    .SetHeader("theta_deg phi_deg value")\n'
            '    .SetData(thetaList, phiList, valueList)\n'
            '    .Execute\n'
            'End With\n'
            'FarfieldPlot.RestoreAllSettings("cst_agent_getlist_farfield")\n'
        )

        try:
            temp_dir = Path(config.CST_TEMP_DIR)
            temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".vba", delete=False, encoding="utf-8", dir=temp_dir
            ) as f:
                f.write(vba_code)
                vba_file = f.name
        except Exception as exc:
            return {"success": False, "message": f"写入临时文件失败: {exc}"}

        project = self._resolve_project_for_command()
        result = self._run_com_script(
            ["export_farfield_getlist", project, vba_file, "export_farfield_getlist", output_path],
            timeout=timeout,
        )

        try:
            os.unlink(vba_file)
        except OSError:
            pass

        self.last_message = result.get("message", "")
        self._update_project_path_from_result(result, project)
        result["output_path"] = output_path
        result["item_path"] = normalized_item
        result["vba_code"] = vba_code
        return result

    def is_connected(self) -> bool:
        return self.connected and not self.offline_mode

    def get_status(self) -> str:
        if self.is_connected():
            return "已连接 CST（在线模式）"
        if self.cst_exe:
            return f"未连接 CST（离线模式），已检测到安装路径：{self.cst_exe}"
        return "未连接 CST（离线模式），未检测到安装路径"
