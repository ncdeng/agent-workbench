# CST 原生接口 vs 项目自拼 VBA 审计

> 目的：盘点项目里哪些自拼 VBA 可以替换为 CST 官方原生 API 或模板，作为后续 B 层（原生接口对齐）改造的依据。
> 来源：交叉对比 `D:/Program Files (x86)/CST Studio Suite 2025/Library/Result Templates/` 与 `Library/Macros/` 中的官方 .rtp / .mcr 文件，与 `cst_agent_workbench/cst/controller.py` + `primitives.py` + `rectangular_patch_fast.py` + `dipole_fast.py` 的现有实现。
> 完成日期：2026-04-28

---

## 1. 远场与结果导出

### 1.1 已经对齐 CST 原生（不必动）

| 项目方法 | 文件:行 | 用的 CST API |
|---|---|---|
| `list_farfield_tree_items` | controller.py:580 | `ResultTree.GetFirstChildName / GetNextItemName` ✓ |
| `probe_farfield_tree_items` | controller.py:596 | 同上模式 ✓ |
| 导出 ASCII 主链路 | controller.py:654 | `FarfieldPlot.*` + `ASCIIExport.*` ✓ |

### 1.2 偏离官方需要替换/补强

| 当前实现 | 偏离点 | 官方做法 | 优先级 |
|---|---|---|---|
| `export_farfield_ascii` | 缺 `.Parametric` 开关；CST 参数扫描时无法批量自动导出 | 官方模板 `Export Farfields in ASCII Format^+MWS+DS.rtp` 用 `.Parametric` + timer ID 前缀自动多文件 | **HIGH** |
| ~~`get_farfield_numeric` 用 `AddListEvaluationPoint`~~ | ~~非主流 API~~ | ~~换成 `AddListItem`~~ | ~~MEDIUM~~ ~~→ **NO-OP（结论修正）**~~ |
| 不支持 GRASP / SATIMO 格式 | 项目只有 ASCII 一种导出 | 官方 `.mcr` 现成可改 Python | LOW（除非有外部工具链需求） |

---

## 2. 端口定义

### 2.1 已对齐（不必动）

| 类型 | 实现 | 评价 |
|---|---|---|
| Discrete port（dipole） | `primitives.create_discrete_port` 用 SetP1/SetP2 + Type "SParameter" | 与官方 `Multiple discrete Ports^+MWS.mcr` 模式完全一致 ✓ |
| Microstrip waveguide port（patch） | `rectangular_patch_fast.build_side_waveguide_port_vba` 用 Pick.PickFaceFromPoint + Port.Coordinates "Picks" + .XrangeAdd/.ZrangeAdd | 这是 CST 推荐的 pick-based 模式，正确 ✓ |
| 几何原语 brick / cylinder | `primitives.create_brick / create_cylinder` 直接用 `Brick.With / Cylinder.With` 对象 | 完全对齐 ✓ |
| Material 创建（含 tand 修复） | `primitives.create_material` 已配对 `.TanDGiven "True"` + `.TanDModel "ConstTanD"` | 关键修复已就位 ✓ |

### 2.2 可以加固但不紧急

| 现状 | 改进 | 优先级 |
|---|---|---|
| Pick-based waveguide port 拾取若失败没备选 | 加 `build_explicit_waveguide_port_vba`（用绝对坐标 + Port.Coordinates "Free"），作为 fallback | LOW |

---

## 3. Mesh / Boundary

### 3.1 Mesh

**现状**：`controller.set_mesh_by_frequency` 只设全局 `FDSolver.MaxStepSize`（λ₀/15，硬限 0.1~5mm）。

**差距**：
- 没分区细化（端口、馈电线、地板边缘这些"电场密集区"需要更细网格）
- 没自适应（CST 2025 GUI 支持 adaptive mesh refinement，项目没用）

**改进方向**（按收益降序）：
1. **HIGH**：新增 `primitives.create_mesh_refinement(region, max_step)`，给端口/馈线区域单独加细。简单天线收益小，但优化器跑多轮时端口附近网格质量直接影响 S11 重复性
2. MEDIUM：把 adaptive refinement 切换暴露成 controller 选项，让用户能在精度 vs 时间之间选

### 3.2 Boundary

**现状**：dipole 和 patch 都用六面 `"expanded open"`。

**差距（patch 而言）**：patch 底面有完整接地铜板，物理上是 PEC（完美电导体）边界。用 expanded open 等于"接地板下面留一层空气吸收"，**虽然 CST 不会报错，但仿真精度会受影响**（地板边缘绕射被人为吸收掉）。

**改进**（patch 专属）：
```python
# patch 现在：六面都 expanded open
# 改为：z-min 接地，其他五面 expanded open
set_boundary(
    xmin="expanded open", xmax="expanded open",
    ymin="expanded open", ymax="expanded open",
    zmin="electric",       # ← 接地板边界
    zmax="expanded open",
)
```

**优先级**：MEDIUM。dipole 不动（自由空间正确），只动 patch fast path。

---

## 4. 推荐替换排序（按"动手价值 vs 风险"）

| 排序 | 项 | 动手成本 | 物理收益 | 工程收益 |
|---|---|---|---|---|
| 1 | **patch 的 z-min 改 electric**（boundary 修正） | 极低（5 行） | 中（地板物理更准） | 低（snapshot 会变需要更新） |
| ~~2~~ | ~~**`get_farfield_numeric` 改用 `AddListItem`**~~ | — | — | ~~**取消**：见下方结论修正~~ |
| ~~3~~ | ~~**`export_farfield_ascii` 加 `.Parametric` 开关**~~ | — | — | ~~**取消**：见下方结论修正~~ |
| 4 | **新增 `create_mesh_refinement`（端口/馈线分区）** | 中（约 80 行+） | 中（高 ε 基板更稳） | 低（需要测试网格收敛） |
| 5 | GRASP/SATIMO 格式导出 | 高（200~300 行） | 无（只是格式转换） | 仅当有外部测量/仿真工具链需求 |

---

## 5. 不在本批改造范围

- 项目几何原语（brick / cylinder / material）已对齐官方，不动
- discrete port 和 pick-based waveguide port 都对齐官方，不动
- adaptive mesh refinement 功能（CST 2025 GUI 用得多但 VBA 暴露较少，先不投入）

## 5.1 结论修正（2026-04-28，B-2 落地前发现）

> 原审计建议把 `get_farfield_numeric` 的 `AddListEvaluationPoint` 换成 `AddListItem`。**取消这条建议。**

去 CST 安装目录实际查证后发现：
- `Library/Macros/Results/- Import and Export/Export Farfield in GRASP format^+MWS.mcr` 等少数宏确实在用 `AddListItem(theta, phi, radius)`
- 但 CST **现代主力** result template `Library/Result Templates/Farfield and Antenna Properties/Farfield Result^+MWS+DS.rtp` 在 line 51 有明确注释：
  > `26-Apr-2017 fsr: ... replaced legacy AddListItem* with AddListEvaluationPoint`

  并在 line 2718-2755 等多处使用 `FarfieldPlot.AddListEvaluationPoint(theta, phi, 0, "spherical", ...)`，签名和项目完全一致。

**结论**：项目用的 `AddListEvaluationPoint` **才是 CST 现代 API**，`AddListItem` 是 2017 年被官方废弃的 legacy 形式。**不要改。** Audit 第一轮基于 GRASP/SATIMO 宏样本得出的结论与 CST 主流方向相反。

教训：写审计要交叉对照"宏 / Result Template / Python API 文档"三类来源，单看宏容易踩 legacy。

## 5.2 结论修正（2026-04-28，B-3 落地前发现）

> 原审计建议给 `export_farfield_ascii` 加 `.Parametric` 开关。**取消这条建议。**

实际去读 `Library/Result Templates/Farfield and Antenna Properties/Export Farfields in ASCII Format^+MWS+DS.rtp:174-201` 后发现：
- `.Parametric` 不是 ASCIIExport 对象的方法，是 CST result template 脚本层面的 GetScriptSetting 开关
- 行为是：当 CST 跑内置 parameter sweep 时，每次迭代用 `Timer*100` 算个 timestamp 作文件名前缀，避免覆盖
- 我们项目**不用 CST 内置 parameter sweep**，优化器在 Python 里循环、每次自己传不同 output_path
- 所以加这个开关对当前工作流几乎无用，属于防御性过度工程

**结论**：跳过。

## 5.3 实际下一项（替代原 B-3）

**Mesh refinement primitive**：在 `primitives.py` 加 `create_mesh_refinement(name, region, max_step)`，给端口和馈线区域单独控制网格密度。

**为什么真有收益**：
- 当前 `set_mesh_by_frequency` 只设全局最大步长（λ₀/15）。port 截面附近的电场密集区其实需要更细网格才能算准阻抗匹配
- 优化器跑多轮时，端口网格的微小变化会让 S11 谐振点漂移，导致同样参数两次 solve 给出不同 S11，影响收敛
- 加 region refinement 能让端口区域稳定到亚毫米级，提升优化稳定性

这是 CST 官方推荐做法（见 `Library/Macros/Mesh and Solver/`），不是凭空假设的需求。

---

## 6. 下一步建议

按顺序选一个开干：

- **B-1（最便宜的开门红）**：patch boundary z-min 改 electric。改 5 行代码 + 更新 2 个 patch snapshot。物理上更准。
- **B-2**：`get_farfield_numeric` 切到官方 `AddListItem` API。这个动了能让 CST 版本升级时不踩坑。
- **B-3**：`export_farfield_ascii` 加 `.Parametric` 开关。优化器跑多频/多端口时实用。

每个都是独立 commit。**当前不推荐 GRASP / SATIMO** —— 项目没有外部工具链消费这些格式，先别花时间。
