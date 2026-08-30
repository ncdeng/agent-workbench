# CST AI 建模助手 - 修改记录

当前路径说明：
- `app.py` 仍保留为根目录启动入口。
- 主要运行时代码现位于 `cst_agent_workbench/` 包内。
- 下方历史记录中出现的 `agent.py`、`config.py`、`cst_controller.py` 等名称，多数描述的是包化之前的根目录路径。

## 运行方法

```bash
cd D:\claudecode\cst_agent
pip install -r requirements.txt   # 首次运行需要安装依赖
python app.py                      # 启动应用
```

启动后浏览器打开 `http://127.0.0.1:7860` 即可使用。

**当前行为**：应用会自动尝试启动并连接 CST Studio Suite 2025；连接成功后默认切回交互模式。

---

## 修改历史

### 2026-03-14 - 支持自动启动 CST 并切回交互模式

**文件**: `cst_controller.py`

**问题**: 之前即使机器支持 `cst.interface` 自动拉起 CST，项目仍然要求用户先手动打开 CST 才能连接。

**根因**: 连接逻辑使用了 `cst.interface.DesignEnvironment.connect_to_any()`，该方法只能连接已有实例；没有正在运行的 DE 时会返回 `No DEs found to connect to.`。

**修复**:
- 连接入口改为 `cst.interface.DesignEnvironment.connect_to_any_or_new()`，优先连接已有实例，不存在时自动启动新的 CST Design Environment。
- 连接成功后调用 `de.set_quiet_mode(False)`，默认切回交互模式，避免停留在 Quiet/Scripting mode。
- 失败提示改为“自动连接或启动 CST 失败”，与当前行为一致。

---

### 2026-03-14 - 彻底解决 CST 连接问题（使用官方 Python API）

**文件**: `cst_controller.py`, `启动.bat`

**根因**: CST 2025 的旧 COM 接口（`Active3D()` 等）全部返回"2 args required"错误，是 CST 2025 的 breaking change，旧 COM 方式完全不可用。

**诊断过程**:
1. 分析 gen_py 类型库，确认 `Active3D()` 定义为 0 参数但运行时报错
2. 发现 `cst-studio-suite-link`（官方 Python 包）虽标注 `<3.12`，但实为纯 Python 包，可用 `--ignore-requires-python` 安装
3. 通过 `dir()` 探索发现正确 API：`project.model3d.add_to_history()`（不是 `AddToHistory`）

**正确 API（CST 2025）**:
```python
import cst.interface
de = cst.interface.DesignEnvironment.connect_to_any_or_new()
de.set_quiet_mode(False)
proj = de.get_open_projects()[0]  # 或 de.new_mws()
mws = proj.model3d
mws.add_to_history("label", vba_code)
```

**修改**: `_COM_SCRIPT` 精简为单一方案（纯 `cst.interface`），删除所有 COM 回退逻辑。

---

**文件**: `cst_controller.py`, `config.py`, `启动.bat`

**背景**: CST 2025 的 `Active3D()` COM 方法签名改变（需要 2 个参数），导致旧的 COM 连接方式失败。CST 2025 提供了官方 Python 包 `cst-studio-suite-link`，可通过本地仓库安装。

**修改**:
- `_COM_SCRIPT` 重构为两阶段连接：**方案1** `cst.interface.DesignEnvironment()`（CST 2025 官方接口），**方案2** COM 多方式回退（兼容旧版）
- 连接成功后在消息中显示实际使用的连接方式（便于调试）
- `config.py` 新增 `CST_PYTHON_REPO` 配置项指向 CST 本地 Python 仓库路径
- `启动.bat` 新增自动安装 CST 官方 Python 包的步骤

---

**文件**: `cst_controller.py`

**问题**: CST 已打开但应用始终显示"未连接"。

**根因**: `_get_or_create_project_script()` 返回多行 Python 代码片段，通过 `{proj_script}` 嵌入 f-string。这导致：
1. 多行字符串插入 f-string 时，只有第一行有正确缩进，后续行都跑到顶层 → 子进程脚本产生 `IndentationError`
2. 代码片段中的 `{}` 字符（如 `json.dumps({...})`）可能与外层 f-string 冲突

**修复方案**: 彻底重写脚本生成方式：
- COM 脚本改为**静态常量字符串** `_COM_SCRIPT`，不使用 f-string，无转义问题
- 变量数据（工程路径、VBA 文件路径）通过 `sys.argv` 命令行参数传递
- 脚本写入临时 `.py` 文件执行（而非 `python -c` 内联）
- 删除了 `_get_or_create_project_script()` 方法
- `connect()` 和 `execute_vba()` 共用同一个脚本，通过 `"connect"` / `"execute"` 参数区分

---

### 2026-03-14（早期） - 修复 VBA 不执行问题

**文件**: `cst_controller.py`

**问题**: CST 连接成功，但 VBA 脚本未出现在 CST History List 中。

**根因**: 多行 VBA 代码通过 f-string 嵌入子进程脚本时，转义出错导致脚本内容丢失。

**修复**: VBA 写入临时 `.vba` 文件，子进程读取文件内容后调用 `mws.AddToHistory("cst_agent", vba_content)`。

---

### 2026-03-14（早期） - 修复 CST API 调用方式

**文件**: `cst_controller.py`, `agent.py`

**问题**: 原来使用 `mws.RunScript(file_path)` 执行 VBA，CST 不识别。

**修复**: 参照用户的 MATLAB 脚本 `Pixel.m`，改为 `mws.AddToHistory(label, raw_vba_string)`。VBA 脚本在执行前自动去掉 `Sub Main()` / `End Sub` 包装。

---

### 2026-03-14（早期） - 修复 UI 卡死问题

**文件**: `agent.py`, `app.py`

**问题**: 发送消息后 UI 组件一直显示"processing"。

**根因**: `agent.chat()` 是生成器函数（yield），配合 Gradio 逐字符流式输出，产生数百次 UI 更新导致卡死。

**修复**: `agent.chat()` 改为普通函数（return），`app.py` 的 `respond()` 也从生成器改为普通函数。

---

### 2026-03-14（早期） - 修复 COM 线程问题

**文件**: `cst_controller.py`

**问题**: `win32com.client.GetActiveObject()` 在 Gradio 回调线程中失败。

**根因**: COM 要求在同一线程 apartment 中操作，Gradio 的回调运行在不同线程。

**修复**: 所有 COM 操作改为通过 `subprocess` 在独立子进程中执行，彻底避免线程问题。

---

### 2026-03-14（早期） - 修复 Active3D() 返回 int

**文件**: `cst_controller.py`

**问题**: CST 没有打开工程时，`app.Active3D()` 返回一个整数而非 COM 对象。

**修复**: 用 `try/except Exception` 捕获，并增加 `hasattr(mws, 'AddToHistory')` 检查。若无工程则自动新建或打开默认工程。

---

### 2026-03-14（早期） - 修复 Gradio 兼容性

**文件**: `app.py`

**问题**:
1. `gr.Chatbot(type="messages")` → Gradio 6.9.0 不支持 `type` 参数
2. `gr.Code(language="vba")` → VBA 不是支持的语言

**修复**: 删除 `type="messages"`，`language` 改为 `None`。

---

## 项目文件说明

| 文件 | 作用 |
|------|------|
| `config.py` | 机器相关路径、模型配置等非敏感配置 |
| `cst_controller.py` | CST Python 接口连接与 VBA 执行 |
| `agent.py` | LLM Agent，调用 GPT 生成 VBA |
| `vba_templates.py` | 常用天线/结构的 VBA 模板 |
| `app.py` | Gradio 网页界面 |
| `requirements.txt` | Python 依赖 |

## 安全说明

- Gradio 默认只监听 `127.0.0.1`（本机），外部无法访问
- 敏感凭据必须通过环境变量提供，不应提交到仓库
