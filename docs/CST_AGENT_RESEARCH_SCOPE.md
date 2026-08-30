# CST Agent 研究范围与官方接口证据

## 系统定位

本项目的主体是 **CST Agent**。它覆盖意图理解、Planning、Context/Memory、工具调用、
CST 建模与仿真、结果分析、优化、Recovery、Trace 和 Eval。

Harness 是 CST Agent 内部的一个核心模块，负责模型执行循环、上下文封装、动态工具目录、
控制协议与运行事件。Native 和 Pi 是可替换 Harness，但都不能替代完整 CST Agent，也不持有
CST COM 句柄或可变 Session 真值。

## 毕业论文边界

CST Agent 只计划作为毕业论文中的一个系统/方法章节，服务于谐波雷达标签及其他天线研究的
参数化建模、仿真执行、结果分析和设计迭代。毕业论文的总体主线仍是天线研究，不把项目泛化为
CAE Agent，也不把 Harness 作为整篇论文主题。

未发表论文和其中的未公开结构、参数、结果默认不作为开发输入。只有在作者明确确认后，才选择
必要且可脱敏的内容作为真实案例。

## CST 版本与官方接口真值

当前本机验证目标为 CST Studio Suite 2025.2：

- 安装目录：`D:\Program Files (x86)\CST Studio Suite 2025`
- `Image_Version`：`2025.0 RELEASE`
- `Patch_Version`：`2025.2 RELEASE`
- Python API 索引：`Online Help\Python\source\cst.interface.html`

开源仓库只用于能力盘点和实现风险发现。新增 CST 操作必须优先依据本机官方 Help、官方宏或 GUI
History，并通过真实 CST smoke 验证，不能因为第三方仓库注册了同名工具就视为接口正确。

## 已定位的官方接口

| 能力 | CST 2025.2 官方出处 | 实现前必须验证 |
|---|---|---|
| Project lifecycle | `AMD64\python_cst_libraries\cst\interface\studio.py`；Python Help 中的 `DesignEnvironment` / `Project` | `new_mws/open_project/save/save_as/close`、绝对路径、覆盖与未保存弹窗 |
| Waveguide port | `Online Help\mergedProjects\VBA_3D\special_vbaports\special_vbaports_port_object.htm` | Picks 前置条件、端口法向、range add、modes 与默认值 |
| Local mesh | `Library\Macros\Solver\Mesh\Apply Mesh Refinement To Dummy Object^-DS.mcr` | Hex/Tet 参数语义、mesh group 顺序、单位和表达式 |
| Field monitor | `Library\Macros\Solver\Monitors and Probes\Broadband Field Monitors^+MWS+PS.mcr` | FieldType 字符串、同名行为、频率单位、Plane/Volume |
| Result export | `Online Help\mergedProjects\VBA_3D\common_vbaimpexp\asciiexport_object.htm` 及官方 Farfield/3D Field result templates | tree path、覆盖、编码、导出 mode、失败后 `UseSubvolume` 状态恢复 |
| Solid inventory | 官方宏 `Calculate port extension coefficient^+MWS.mcr` 中的 `Solid.GetNumberOfShapes / GetNameOfShapeFromIndex` | 私有即时执行入口的版本兼容、输出文件存在性、空模型和探针失败区分 |

## 已完成的真实 CST 证据

Project lifecycle 已在本机 CST 2025.2 完成真实 6-stage smoke：

`create → save(include_results=False) → save-as → close → reopen → final close`

报告：
`D:\cst_agent_rag_data\agent_eval\project_lifecycle\20260812T070702Z\report.json`

报告记录两个 `.cst` 文件均真实存在，最终 controller project path 为空。该结果只证明工程生命周期
接口与文件持久化成功，不等价于几何正确、求解成功或物理指标达标。

## 每项能力的完成标准

一个新工具只有同时满足以下条件，才视为 CST Agent 能力完成：

1. canonical catalog 中只有一份 typed JSON Schema，Native、Pi 和未来 MCP 共用；
2. 参数在任何 CST 副作用前完成 required/type/enum/range/unknown-field 校验；
3. active-step 权限、确认门禁、Trace、Recovery 和结果保留语义明确；
4. 离线单元测试覆盖 contract、VBA/API 生成和失败路径；
5. 在 D 盘测试工作区运行真实 CST，并保存工程、日志、结果或重开核验证据；
6. 记录 CST 版本、输入、输出、失败类型、延迟与可复算报告，供评测和论文章节使用。

API 调用成功不等于建模成功，建模成功不等于求解成功，求解成功也不等于得到符合目标的物理
结果。评测必须分层报告这些终点。

## Agent 内部 Harness 的安全控制面（2026-08-12）

- Agent 的 42 个 canonical tools 共享 Python Host Runtime；Native/Pi 只替换模型执行循环，不持有
  CST COM 句柄，也不能绕过 active-step allowlist、JSON Schema、Recovery 或 Trace。
- 首批人工审批仅覆盖 `execute_vba_script`。这是能够绕过 typed contract 的 raw VBA escape hatch；
  普通 typed 建模工具、`run_solver` 与结果读取保持自主执行，因此安全门禁不会把 CST Agent 退化成
  每步人工点击的 workflow。
- approval capability 绑定 tool name、应用 schema defaults 后的 canonical arguments SHA-256、固定本地
  actor、过期时间，并在第一次执行尝试前消费。参数变化、失败重试、Recovery 参数修复、会话清空或
  CST 工程切换都要求重新批准。
- Web 批准动作不接受客户端重新提交工具参数。服务端取回原始 pending request，并在发 grant 前再次
  执行当前 allowlist 与 schema preflight；计划已经推进或 schema 已变化时 request 标为 stale，不会
  留下可复用 grant。Native/Pi 获得同一 `approval_required` 结果和 request identity。
- 当前身份模型仅适用于未认证的本地单用户桌面应用，固定 actor 为 `local-desktop-user`。这不是
  多租户认证、RBAC 或网络隔离方案；如对外开放服务，必须另行引入可信认证与本地访问边界。
- 离线证据覆盖 hash/default normalization、actor/expiry/single-use、allowlist/schema 优先级、失败也
  消耗、Recovery 参数变化、stale-plan TOCTOU、clear/project-transition 撤销，以及 Native/Pi 等价
  传递。该证据证明 Host 安全语义，不证明任意 raw VBA 本身安全。

## Typed complex geometry 契约（2026-08-12）

- `boolean_add` 已进入唯一 canonical schema、`tool/geometry` active-step allowlist 与统一 Host
  Runtime；`boolean_add/boolean_subtract` 的实体引用均要求 `Component:Name`，格式错误在 CST
  副作用前拒绝。底层仍复用领域原语注册表，不维护第二套 Native/Pi 实现。
- `CSTController.list_solids` 是证据收集用只读 probe，不向模型暴露为新工具。它依据官方宏中的
  `Solid.GetNumberOfShapes / GetNameOfShapeFromIndex` 枚举实体名，探针未生成输出文件时 fail closed；
  临时文件强制位于 `config.CST_TEMP_DIR` 指定的 D 盘路径并在正常/异常路径清理。
- canonical `execute_vba` 只有在 CST `add_to_history` 返回成功并由 Host 标记 `executed=true` 时，
  才统一携带 `verification=history_accepted`；即时控制 VBA 不获得该标签，runner 不再事后补写证据。

真实 CST 2025.2 证据：

- 最终报告：
  `D:\cst_agent_rag_data\agent_eval\complex_geometry\20260812T113910Z\report.json`，SHA-256 为
  `9636ad36abe20316c4f21e31a7c09e6cfdd243ba14733eb0a546f1bc3119173b`；
- 10 个 typed History 操作覆盖 extruded polygon、local/global WCS、transform、Boolean union/
  subtract、cylinder、brick 与 transform copy/repetitions/unite；17 个阶段全部成功；
- save 前与 close/reopen 后均回读到精确实体集合 `complex:array, complex:body`，工程 SHA-256 为
  `59ece90ed9a13aa1655befbc60b573b6d259a7a24db4751f426b987ac9cf0de8`，最终 controller
  project path 为空；报告绑定 runner、schema、Host Runtime、Planner allowlist、领域原语和
  controller 源码 SHA，均已本地复算一致。

该 smoke 只证明 typed 命令被 CST History 接受、Boolean 操作后的实体名称符合预期，以及工程保存/
重开后的 inventory 一致；它没有独立测量几何体积、拓扑或坐标，也没有运行 solver，因此不证明端口
有效、mesh 收敛、电磁物理正确或任何天线指标达标。`model3d._execute_vba_code` 仍是 CST Python 包的
私有即时入口，已显式记为版本绑定兼容风险。

## Typed monitor 与 mesh 契约（2026-08-12）

- `create_frequency_field_monitor` 依据本机 CST 2025.2 官方宏使用 `.Frequency`，而不是旧实现的
  `.MonitorValue`；`Efield/Hfield` 明确设置 `Dimension "Volume"`，`Farfield` 不设置
  `Dimension`。旧 `create_farfield_monitor` 保留为 solver preflight、Recovery、fast path 和
  frozen eval 的兼容工具，但内部同样切换到官方 `.Frequency`。
- `create_mesh_refinement` 与 `add_solids_to_mesh_group` 依据官方 mesh macro 的
  `Group.Add → MeshSettings.ItemMeshSettings → Group.AddItem` 顺序；批量成员使用结构化
  `{component, name}`，模型不直接拼接 `solid$...`。
- 首次真实 smoke 证明旧 `set_mesh_by_frequency` 的 `.StepsPerWavelengthTS` 在 CST 2025.2
  触发 ActiveX 10091。新 canonical tool 改为 `set_global_hexahedral_mesh`，直接映射官方 Help 的
  `MeshType("PBA") / LinesPerWavelength / MinimumStepNumber / Automesh(True)`；不再暴露未真实
  控制官方 API 的 `f0_ghz/epsilon_r` 假参数。它不等同于 CST adaptive mesh refinement。
- 真实 smoke 的首个可声明终点是 `history_accepted`；保存/重开只能进一步证明持久化，未运行
  solver 前不得声明 monitor 结果已生成或 mesh 已产生物理有效结果。

真实 CST 2025.2 证据：

- 首次失败报告：`D:\cst_agent_rag_data\agent_eval\monitor_mesh\20260812T074927Z\report.json`
  （定位旧 `.StepsPerWavelengthTS` 的 ActiveX 10091，工程最终安全关闭）；
- 修正后通过报告：`D:\cst_agent_rag_data\agent_eval\monitor_mesh\20260812T075250Z\report.json`；
- 通过链路：3 类 monitor + local mesh + global PBA mesh 共 5 个 canonical tool 全部
  `history_accepted`，随后 `save → close → reopen → final close` 全部成功；工程 SHA-256 与完整
  Host Tool Trace 已写入报告。该证据不包含 solver 或物理目标终点。

## Typed waveguide port 契约（2026-08-12）

- canonical `create_waveguide_port` 直接映射本机官方 Port object，支持 `Free`、`Full` 与
  `Picks` 三种 coordinate mode，不再把矩形贴片的专用端口宏冒充通用端口工具。
- 模型可见 JSON Schema 与 Host Runtime 使用同一份条件契约：`Free` 必须提供完整
  `x/y/z ranges`；`Full` 禁止 `ranges/pick/range_add`；`Picks` 必须提供
  `{solid, face_id}`，可选完整 `x/y/z range_add`。Free/Full 的 orientation 只能是
  `xmin/xmax/ymin/ymax/zmin/zmax`，Picks 只能是 `Positive/Negative`。
- Picks 在 Port block 前调用 `Pick.ClearAllPicks` 与 `Pick.PickFaceFromId`；省略
  `range_add` 时使用三个零扩展，显式空对象视为契约错误。所有模式互斥与字符串/数值校验均在
  CST 副作用前完成。
- `NumberOfModes`、`ReferencePlaneDistance`、`PortOnBound`、
  `ClipPickedPortToBound` 和 `SingleEnded(False)` 均明确生成，不依赖 GUI 隐含状态。

真实 CST 2025.2 Free/Full 证据：

- 最终 SHA-bound 报告：
  `D:\cst_agent_rag_data\agent_eval\waveguide_port\20260812T081848.753097Z\report.json`；
- 输入覆盖 Free（2 modes、显式三轴 ranges、`PortOnBound=False`、reference plane `-5`）与
  Full（1 mode、`zmin`、`PortOnBound=True`）；两个工具调用均为 `history_accepted`；
- `save → close → reopen → final close` 全部成功，Host Tool Trace 恰好记录两个成功调用，
  最终 controller project path 为空，工程 SHA-256 为
  `c2201fa9bc6c2781b38a14cb99379fdac1b9b599037a584fd7f1fb4785d8d38e`。报告同时绑定 runner、
  canonical schema、契约校验、Host Runtime 与领域原语的源码 SHA-256；本地复算全部一致；
- `history_accepted` 的精确定义是 `execute_vba` 返回 `success=true` 且 `executed=true`。
  当前没有在重开后回读 Port object，因此不能声称端口对象及参数已完成持久化回读，也没有证明
  激励有效、模式物理正确、求解成功或 S 参数达标；Picks 仍需真实 solid face 的 geometry-backed
  smoke。

## Typed result 与 ASCII export 契约（2026-08-12）

- Agent 结果能力统一为稳定 discriminator：`scalar / curve_1d / s_parameter / farfield_cut /
  artifact_ref / unsupported_3d`。底层 reader 的原始 `x/y` 远场曲线仍是 `curve_1d`，只有经过
  角度/增益字段转换和波束指标计算后才提升为 `farfield_cut`，避免把“可读取曲线”和“领域方向图”
  混为一谈。
- `list_results` 使用 `offset/limit/category/query` 分页与过滤，不再通过 `all_items` 或嵌套
  farfield 字段把全量结果树塞进模型上下文；`read_result/get_s_parameter` 使用可选 `max_points`
  确定性降采样，同时保留端点、全局最小值和最大值，完整曲线仍用于 min/max 等摘要计算。
- `export_result_ascii` 复用同一 canonical Host Runtime，底层严格使用官方
  `SelectTreeItem → ASCIIExport.Reset → FileName → Execute`。输出只允许新的 D 盘 `.txt/.csv`
  绝对路径并拒绝覆盖；成功条件同时要求 CST 返回成功且文件存在、非空。
- CST 结果子进程、COM bridge 和本阶段 VBA 临时脚本默认使用
  `D:\cst_agent_rag_data\tmp\cst_agent_workbench`，可用 `CST_TEMP_DIR` 覆盖，不再默认占用 C 盘
  系统 TEMP。

真实 CST 2025.2 证据：

- 最终 SHA-bound 报告：
  `D:\cst_agent_rag_data\agent_eval\typed_results_v1\final_20260812T183000\report.json`；
- 已保存工程结果树共 28 项，`S1,1` 共 1001 点；64 点 bounded view 保留完整摘要与全局极值；
- 官方 ASCIIExport 生成 58,184-byte 非空文件，SHA-256 为
  `694bebbd5ba9853ce874bd9b890a8ca388b8b2fb199f75649bebf4d28f7d77e5`，验证后工程已关闭；
- 报告绑定工程、导出文件、官方 Help、runner 与 6 个生产源文件 SHA。该证据证明结果读取、
  typed contract 与导出链路成功，不证明本轮重新求解、solver 收敛、物理正确或远场 3D 支持。
