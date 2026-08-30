PRIMITIVE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_units",
            "description": "设置 CST 工程的单位系统",
            "parameters": {
                "type": "object",
                "properties": {
                    "geometry": {"type": "string", "description": "长度单位", "enum": ["mm", "um", "m", "cm", "mil", "inch", "ft"], "default": "mm"},
                    "frequency": {"type": "string", "description": "频率单位", "enum": ["Hz", "kHz", "MHz", "GHz", "THz"], "default": "GHz"},
                    "time": {"type": "string", "description": "时间单位", "default": "ns"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "store_parameter",
            "description": "定义或更新可供几何引用的 CST 参数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "参数名"},
                    "value": {"type": "string", "description": "数值或参数表达式"},
                },
                "required": ["name", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_parameter",
            "description": "删除 CST 参数变量",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要删除的参数名"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_material",
            "description": "创建自定义介质材料（PEC/Vacuum 等内置材料无需创建）",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "材料名称"},
                    "epsilon": {"type": "number", "description": "相对介电常数", "default": 1.0},
                    "mue": {"type": "number", "description": "相对磁导率", "default": 1.0},
                    "tand": {"type": "number", "description": "损耗角正切", "default": 0},
                    "tand_freq": {"type": "number", "description": "损耗角正切参考频率(GHz)", "default": 0},
                },
                "required": ["name", "epsilon"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_brick",
            "description": "创建长方体。范围参数支持已定义的参数名（如 \"-ground_L/2\"、\"substrate_h+copper_t\"）或具体数值。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "对象名称（同一 component 下须唯一）"},
                    "component": {"type": "string", "description": "所属组件名"},
                    "material": {"type": "string", "description": "材料名（PEC/Vacuum/自定义）"},
                    "xmin": {"type": "string", "description": "X 最小值"},
                    "xmax": {"type": "string", "description": "X 最大值"},
                    "ymin": {"type": "string", "description": "Y 最小值"},
                    "ymax": {"type": "string", "description": "Y 最大值"},
                    "zmin": {"type": "string", "description": "Z 最小值"},
                    "zmax": {"type": "string", "description": "Z 最大值"},
                },
                "required": ["name", "component", "material", "xmin", "xmax", "ymin", "ymax", "zmin", "zmax"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_cylinder",
            "description": "创建圆柱体",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "对象名称"},
                    "component": {"type": "string", "description": "所属组件名"},
                    "material": {"type": "string", "description": "材料名"},
                    "axis": {"type": "string", "description": "轴方向", "enum": ["x", "y", "z"]},
                    "outer_radius": {"type": "string", "description": "外半径"},
                    "inner_radius": {"type": "string", "description": "内半径", "default": "0"},
                    "xcenter": {"type": "string", "description": "X 中心", "default": "0"},
                    "ycenter": {"type": "string", "description": "Y 中心", "default": "0"},
                    "zmin": {"type": "string", "description": "Z 最小值"},
                    "zmax": {"type": "string", "description": "Z 最大值"},
                },
                "required": ["name", "component", "material", "axis", "outer_radius", "zmin", "zmax"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_extruded_polygon",
            "description": "将二维闭合多边形沿法向拉伸为参数化实体，适合开槽、异形贴片和任意平面轮廓。顶点和高度可使用 CST 参数表达式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "生成实体名称"},
                    "component": {"type": "string", "description": "所属组件名"},
                    "material": {"type": "string", "description": "已存在的材料名"},
                    "points": {
                        "type": "array",
                        "description": "按顺序排列的二维 [u,v] 顶点，至少 3 个；无需重复首点闭合",
                        "minItems": 3,
                        "maxItems": 512,
                        "items": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 2,
                            "items": {"type": "string"},
                        },
                    },
                    "height": {"type": "string", "description": "沿局部法向的拉伸高度"},
                    "origin": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                        "default": ["0", "0", "0"],
                    },
                    "u_vector": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                        "default": ["1", "0", "0"],
                    },
                    "v_vector": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                        "default": ["0", "1", "0"],
                    },
                },
                "required": ["name", "component", "material", "points", "height"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transform_shape",
            "description": "平移或旋转现有 CST 实体；可选择保留原对象并有限次数复制。shape 使用 Component:Name。",
            "parameters": {
                "type": "object",
                "properties": {
                    "shape": {"type": "string", "description": "目标实体，格式 Component:Name"},
                    "operation": {"type": "string", "enum": ["translate", "rotate"]},
                    "vector": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                        "default": ["0", "0", "0"],
                    },
                    "angle": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                        "default": ["0", "0", "0"],
                    },
                    "center": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                        "default": ["0", "0", "0"],
                    },
                    "copy": {"type": "boolean", "default": False},
                    "repetitions": {"type": "integer", "minimum": 1, "maximum": 64, "default": 1},
                    "unite": {"type": "boolean", "default": False},
                },
                "required": ["shape", "operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_wcs",
            "description": "切换到全局坐标系，或通过原点、u 轴和法向定义局部工作坐标系。",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["global", "local"], "default": "global"},
                    "origin": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                        "default": ["0", "0", "0"],
                    },
                    "u_vector": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                        "default": ["1", "0", "0"],
                    },
                    "normal": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                        "default": ["0", "0", "1"],
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_frequency_range",
            "description": "设置仿真频率范围",
            "parameters": {
                "type": "object",
                "properties": {
                    "fmin": {"type": "string", "description": "最低频率"},
                    "fmax": {"type": "string", "description": "最高频率"},
                },
                "required": ["fmin", "fmax"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_boundary",
            "description": "设置六面边界条件。天线仿真推荐 'expanded open'（对应 GUI 的 open add space），自动添加空气间距。",
            "parameters": {
                "type": "object",
                "properties": {
                    "xmin": {"type": "string", "description": "X- 边界", "enum": ["open", "expanded open", "expanded lf open", "electric", "magnetic", "normal", "tangential", "periodic", "impedance", "unit cell"], "default": "expanded open"},
                    "xmax": {"type": "string", "description": "X+ 边界", "enum": ["open", "expanded open", "expanded lf open", "electric", "magnetic", "normal", "tangential", "periodic", "impedance", "unit cell"], "default": "expanded open"},
                    "ymin": {"type": "string", "description": "Y- 边界", "enum": ["open", "expanded open", "expanded lf open", "electric", "magnetic", "normal", "tangential", "periodic", "impedance", "unit cell"], "default": "expanded open"},
                    "ymax": {"type": "string", "description": "Y+ 边界", "enum": ["open", "expanded open", "expanded lf open", "electric", "magnetic", "normal", "tangential", "periodic", "impedance", "unit cell"], "default": "expanded open"},
                    "zmin": {"type": "string", "description": "Z- 边界", "enum": ["open", "expanded open", "expanded lf open", "electric", "magnetic", "normal", "tangential", "periodic", "impedance", "unit cell"], "default": "expanded open"},
                    "zmax": {"type": "string", "description": "Z+ 边界", "enum": ["open", "expanded open", "expanded lf open", "electric", "magnetic", "normal", "tangential", "periodic", "impedance", "unit cell"], "default": "expanded open"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_background",
            "description": "设置背景空气层到结构的距离。当边界使用 'expanded open' 时通常不需要；当边界用 'open' 时应手动设置足够空气间距。",
            "parameters": {
                "type": "object",
                "properties": {
                    "xmin": {"type": "string", "description": "X- 方向距离", "default": "0"},
                    "xmax": {"type": "string", "description": "X+ 方向距离", "default": "0"},
                    "ymin": {"type": "string", "description": "Y- 方向距离", "default": "0"},
                    "ymax": {"type": "string", "description": "Y+ 方向距离", "default": "0"},
                    "zmin": {"type": "string", "description": "Z- 方向距离", "default": "0"},
                    "zmax": {"type": "string", "description": "Z+ 方向距离", "default": "0"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_discrete_port",
            "description": "创建离散馈电端口，P1/P2 必须不同。贴片默认连接真实微带线，P1 接地、P2 接导体；probe/coax 馈电必须先用 create_cylinder 建物理探针，不能只放端口冒充。",
            "parameters": {
                "type": "object",
                "properties": {
                    "port_number": {"type": "integer", "description": "端口编号（从 1 开始）"},
                    "p1_x": {"type": "string", "description": "端点1 X坐标"},
                    "p1_y": {"type": "string", "description": "端点1 Y坐标"},
                    "p1_z": {"type": "string", "description": "端点1 Z坐标"},
                    "p2_x": {"type": "string", "description": "端点2 X坐标"},
                    "p2_y": {"type": "string", "description": "端点2 Y坐标"},
                    "p2_z": {"type": "string", "description": "端点2 Z坐标"},
                    "impedance": {"type": "string", "description": "端口阻抗(Ohm)", "default": "50"},
                },
                "required": ["port_number", "p1_x", "p1_y", "p1_z", "p2_x", "p2_y", "p2_z"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_farfield_monitor",
            "description": "创建远场监视器（兼容专用工具）。若用户要远场/方向图，必须在求解前先创建，否则结果树里通常不会出现可读取的 farfield 结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "monitor 名称，例如 'farfield (f=9.4)'"},
                    "frequency": {"type": "string", "description": "监视频率，单位使用当前工程频率单位"},
                    "use_subvolume": {"type": "boolean", "description": "是否启用 subvolume", "default": False}
                },
                "required": ["name", "frequency"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_frequency_field_monitor",
            "description": "创建频域 E/H/Farfield monitor。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "frequency": {"type": "number", "exclusiveMinimum": 0},
                    "field_type": {"type": "string", "enum": ["Efield", "Hfield", "Farfield"]}
                },
                "required": ["name", "frequency", "field_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "boolean_add",
            "description": "布尔并集，将 obj2 合并到 obj1；两个对象必须是已存在的实体引用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "obj1": {"type": "string", "pattern": "^[^:]+:[^:]+$", "description": "目标对象，格式 Component:Name"},
                    "obj2": {"type": "string", "pattern": "^[^:]+:[^:]+$", "description": "被合并对象，格式 Component:Name"},
                },
                "required": ["obj1", "obj2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "boolean_subtract",
            "description": "布尔减法，从 obj1 中减去 obj2",
            "parameters": {
                "type": "object",
                "properties": {
                    "obj1": {"type": "string", "pattern": "^[^:]+:[^:]+$", "description": "被减对象，格式 Component:Name"},
                    "obj2": {"type": "string", "pattern": "^[^:]+:[^:]+$", "description": "减去对象，格式 Component:Name"},
                },
                "required": ["obj1", "obj2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "change_solver_type",
            "description": "切换求解器类型",
            "parameters": {
                "type": "object",
                "properties": {
                    "solver": {"type": "string", "description": "求解器类型", "enum": ["HF Time Domain", "HF Frequency Domain", "HF IntegralEq", "HF Multilayer", "HF Eigenmode", "HF Asymptotic"]},
                },
                "required": ["solver"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_solver",
            "description": "启动当前配置的求解器",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_waveguide_port",
            "description": "创建 Free、Full 或 Picks 波导端口；具体互斥参数由 schema 约束。",
            "parameters": {
                "type": "object",
                "properties": {
                    "port_number": {"type": "integer", "minimum": 1},
                    "coordinate_mode": {"type": "string", "enum": ["Free", "Full", "Picks"]},
                    "orientation": {"type": "string", "enum": ["xmin", "xmax", "ymin", "ymax", "zmin", "zmax", "Positive", "Negative"]},
                    "number_of_modes": {"type": "integer", "minimum": 1, "maximum": 50, "default": 1},
                    "ranges": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "array", "minItems": 2, "maxItems": 2},
                            "y": {"type": "array", "minItems": 2, "maxItems": 2},
                            "z": {"type": "array", "minItems": 2, "maxItems": 2}
                        },
                        "required": ["x", "y", "z"],
                        "additionalProperties": False
                    },
                    "pick": {
                        "type": "object",
                        "properties": {
                            "solid": {"type": "string", "minLength": 3},
                            "face_id": {"type": "integer", "minimum": 1}
                        },
                        "required": ["solid", "face_id"],
                        "additionalProperties": False
                    },
                    "range_add": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "array", "minItems": 2, "maxItems": 2},
                            "y": {"type": "array", "minItems": 2, "maxItems": 2},
                            "z": {"type": "array", "minItems": 2, "maxItems": 2}
                        },
                        "required": ["x", "y", "z"],
                        "additionalProperties": False
                    },
                    "port_on_bound": {"type": "boolean", "default": True},
                    "clip_picked_port_to_bound": {"type": "boolean", "default": False},
                    "reference_plane_distance": {"type": "number", "default": 0.0}
                },
                "required": ["port_number", "coordinate_mode", "orientation"],
                "allOf": [
                    {
                        "if": {"properties": {"coordinate_mode": {"const": "Free"}}},
                        "then": {
                            "properties": {"orientation": {"enum": ["xmin", "xmax", "ymin", "ymax", "zmin", "zmax"]}},
                            "required": ["ranges"],
                            "not": {"anyOf": [{"required": ["pick"]}, {"required": ["range_add"]}]}
                        }
                    },
                    {
                        "if": {"properties": {"coordinate_mode": {"const": "Full"}}},
                        "then": {
                            "properties": {"orientation": {"enum": ["xmin", "xmax", "ymin", "ymax", "zmin", "zmax"]}},
                            "not": {"anyOf": [{"required": ["ranges"]}, {"required": ["pick"]}, {"required": ["range_add"]}]}
                        }
                    },
                    {
                        "if": {"properties": {"coordinate_mode": {"const": "Picks"}}},
                        "then": {
                            "properties": {"orientation": {"enum": ["Positive", "Negative"]}},
                            "required": ["pick"],
                            "not": {"required": ["ranges"]}
                        }
                    }
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_mesh_refinement",
            "description": "创建局部 Hex/Tet mesh group。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "step_mm": {"type": "number", "exclusiveMinimum": 0}
                },
                "required": ["name", "step_mm"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_solids_to_mesh_group",
            "description": "把 solids 加入局部 mesh group。",
            "parameters": {
                "type": "object",
                "properties": {
                    "group_name": {"type": "string", "minLength": 1},
                    "solids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "component": {"type": "string", "minLength": 1},
                                "name": {"type": "string", "minLength": 1}
                            },
                            "required": ["component", "name"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["group_name", "solids"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_global_hexahedral_mesh",
            "description": "设置全局 PBA hexahedral 自动网格。",
            "parameters": {
                "type": "object",
                "properties": {
                    "lines_per_wavelength": {"type": "integer", "minimum": 2, "default": 15},
                    "minimum_step_number": {"type": "integer", "minimum": 1, "default": 5}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_mesh_signature",
            "description": "读取实际网格签名；无已验证接口时返回 unsupported。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]

GENERAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_cst_project",
            "description": "在绝对 D 盘路径新建并保存一个空白 CST Microwave Studio 工程。目标文件已存在时拒绝覆盖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "minLength": 1, "description": "新的绝对 .cst 文件路径，测试与产物应位于 D 盘"},
                },
                "required": ["project_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_cst_project",
            "description": "打开一个已存在的绝对路径 .cst 工程，并切换 Agent 当前工程状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "minLength": 1, "description": "已存在的绝对 .cst 文件路径"},
                },
                "required": ["project_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_cst_project",
            "description": "保存当前 CST 工程。",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_results": {"type": "boolean", "default": True, "description": "是否将已有仿真结果一并保存"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_cst_project_as",
            "description": "将当前 CST 工程另存到新的绝对 D 盘 .cst 路径；目标存在时拒绝覆盖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_path": {"type": "string", "minLength": 1, "description": "新的绝对 .cst 文件路径"},
                    "include_results": {"type": "boolean", "default": True, "description": "是否将已有仿真结果一并保存"},
                },
                "required": ["target_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_and_close_cst_project",
            "description": "先保存当前 CST 工程，保存成功后再关闭；不会丢弃未保存修改。",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_results": {"type": "boolean", "default": True, "description": "关闭前保存时是否包含已有仿真结果"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_tool_result",
            "description": "按 tool_event_id 召回之前工具调用保存在 session 中的完整结果 payload。只有当摘要不够、确实需要完整数据点/VBA/远场数据时才调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_event_id": {"type": "string", "description": "工具摘要中返回的 tool_event_id"},
                },
                "required": ["tool_event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "use_template",
            "description": "使用预置 VBA 模板快速原型；优先使用 typed 原语。",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_name": {"type": "string", "description": "模板名称", "enum": ["patch_antenna", "dipole_antenna", "rectangular_waveguide", "frequency_setup", "open_boundary", "run_hf_solver"]},
                    "parameters": {"type": "object", "description": "模板参数（键值对）"},
                },
                "required": ["template_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_vba_script",
            "description": "在 CST 中执行自由 VBA 脚本（仅当原语工具无法覆盖时使用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "vba_code": {"type": "string", "description": "VBA 脚本代码（可含或不含 Sub Main/End Sub）"},
                    "description": {"type": "string", "description": "一句话说明这段脚本的作用"},
                },
                "required": ["vba_code", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_rectangular_patch_fast",
            "description": "标准矩形微带贴片 fast backend；也适合沿用上一版仅改频率或材料参数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "f0_ghz": {"type": "number", "description": "目标中心频率 GHz"},
                    "substrate_name": {"type": "string", "description": "基板名称，如 RO4350B"},
                    "epsilon_r": {"type": "number", "description": "基板介电常数 Dk"},
                    "loss_tangent": {"type": "number", "description": "基板损耗角正切 Df"},
                    "substrate_thickness_mm": {"type": "number", "description": "基板厚度 mm"},
                    "conductor_name": {"type": "string", "description": "导体名称，默认 Copper (annealed)"},
                    "conductor_thickness_mm": {"type": "number", "description": "导体厚度 mm"},
                    "inherit_previous_request": {"type": "boolean", "description": "若用户明确说沿用上一版/其他都一样，则设为 true"},
                    "feed_strategy": {"type": "string", "description": "目前仅支持 microstrip", "enum": ["microstrip", "probe"]},
                    "run_solver": {"type": "boolean", "description": "只有用户明确要求求解/仿真时才设为 true；默认只建模不运行 solver"},
                },
                "required": ["f0_ghz"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_dipole_fast",
            "description": "标准半波振子（dipole）fast backend。",
            "parameters": {
                "type": "object",
                "properties": {
                    "f0_ghz": {"type": "number", "description": "目标中心频率 GHz"},
                    "wire_or_plate": {"type": "string", "description": "plate=平板振子（默认），wire=细线振子", "enum": ["plate", "wire"]},
                    "arm_radius_mm": {"type": "number", "description": "线振子臂半径 mm（wire 模式使用）"},
                    "run_solver": {"type": "boolean", "description": "仅明确要求仿真时设为 true；成功须读回非空 S11"},
                },
                "required": ["f0_ghz"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_pixel_patch_fast",
            "description": "构建 N×M 二值网格像素化贴片；初始化网格，不代表已完成优化。",
            "parameters": {
                "type": "object",
                "properties": {
                    "f0_ghz": {"type": "number", "description": "目标中心频率 GHz"},
                    "n_rows": {"type": "integer", "description": "像素行数，默认8"},
                    "n_cols": {"type": "integer", "description": "像素列数，默认8"},
                    "pixel_size_mm": {"type": "number", "description": "单个像素边长 mm，默认3.0"},
                    "substrate_h_mm": {"type": "number", "description": "基板厚度 mm，默认1.6"},
                    "epsilon_r": {"type": "number", "description": "基板介电常数，默认4.4"},
                },
                "required": ["f0_ghz"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_cst_status",
            "description": "检查 CST 连接状态",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_results",
            "description": "打开 CST 工程文件用于读取仿真结果（不需要 CST 正在运行）",
            "parameters": {
                "type": "object",
                "properties": {
                    "cst_path": {"type": "string", "description": "CST 工程文件路径（.cst）"},
                },
                "required": ["cst_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_results",
            "description": "分页列出仿真结果树，可按一级分类或路径关键词过滤",
            "parameters": {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                    "category": {"type": "string", "description": "一级分类精确匹配，如 1D Results"},
                    "query": {"type": "string", "description": "结果路径关键词，不区分大小写"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_result",
            "description": "读取指定的仿真结果数据",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_path": {"type": "string", "description": "结果路径，例如 '1D Results\\S-Parameters\\S1,1'"},
                    "max_points": {"type": "integer", "minimum": 4, "maximum": 5000, "default": 500},
                },
                "required": ["item_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_s_parameter",
            "description": "读取 S 参数（便捷方法）",
            "parameters": {
                "type": "object",
                "properties": {
                    "port_i": {"type": "integer", "description": "端口 i", "default": 1},
                    "port_j": {"type": "integer", "description": "端口 j", "default": 1},
                    "max_points": {"type": "integer", "minimum": 4, "maximum": 5000, "default": 500},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_result_ascii",
            "description": "将一个结果树项导出为 D 盘 ASCII 文件；目标必须不存在",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_path": {"type": "string", "description": "list_results 返回的精确路径"},
                    "output_path": {"type": "string", "description": "D 盘绝对 .txt/.csv 路径"},
                    "timeout": {"type": "integer", "minimum": 10, "maximum": 600, "default": 120},
                },
                "required": ["item_path", "output_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_project_materials",
            "description": "列出当前可用的材料，包括 CST 内置材料和已通过 create_material 创建的自定义材料",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

TOOLS = PRIMITIVE_TOOLS + GENERAL_TOOLS

# The model-facing catalog and runtime validator share this exact object. Close
# every top-level argument object once here so new tools cannot silently accept
# misspelled or hallucinated fields. Nested free-form objects (for example a
# template's parameter map) keep their explicitly declared semantics.
for _tool in TOOLS:
    _parameters = (_tool.get("function") or {}).get("parameters")
    if isinstance(_parameters, dict) and _parameters.get("type") == "object":
        _parameters.setdefault("additionalProperties", False)


def get_tool_phase(tool_name: str) -> str:
    if tool_name in ["create_cst_project", "open_cst_project", "save_cst_project", "save_cst_project_as", "save_and_close_cst_project"]:
        return "0_CST工程生命周期"
    if tool_name in ["set_units", "store_parameter", "create_material", "delete_parameter", "list_project_materials"]:
        return "1_环境与材料"
    if tool_name in [
        "create_brick",
        "create_cylinder",
        "create_extruded_polygon",
        "transform_shape",
        "set_wcs",
        "boolean_add",
        "boolean_subtract",
        "create_farfield_monitor",
        "create_frequency_field_monitor",
    ]:
        return "2_几何建模"
    if tool_name in ["set_boundary", "set_background", "create_discrete_port", "create_waveguide_port", "set_frequency_range", "change_solver_type", "create_mesh_refinement", "add_solids_to_mesh_group", "set_global_hexahedral_mesh"]:
        return "3_边界与端口"
    if tool_name == "run_solver":
        return "4_运行仿真"
    if tool_name in ["open_results", "list_results", "read_result", "get_s_parameter", "get_mesh_signature", "export_result_ascii"]:
        return "5_读取结果"
    if tool_name in ["use_template", "execute_vba_script", "check_cst_status", "build_rectangular_patch_fast", "build_dipole_fast", "build_pixel_patch_fast"]:
        return "0_通用脚本"
    return "未知环节"
