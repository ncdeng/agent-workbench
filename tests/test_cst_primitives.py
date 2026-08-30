import pytest

from cst_agent_workbench.cst import primitives as cp



def setup_function():
    cp.reset_created_objects()



def test_cst_primitives_register_and_summary_output():
    cp.register_material("MySub")
    cp.register_parameter("patch_L", "10.5")
    cp.register_object("Antenna", "Patch", "Copper (annealed)")
    cp.register_port(1, "50")

    summary = cp.get_model_summary()

    assert summary["objects"] == {"Antenna:Patch": "Copper (annealed)"}
    assert summary["parameters"] == {"patch_L": "10.5"}
    assert summary["ports"] == [{"port_number": 1, "impedance": "50"}]



def test_store_parameter_and_material_and_brick_vba_shapes():
    label, param_vba = cp.store_parameter("patch_L", "10.5")
    assert label == "define parameter: patch_L=10.5"
    assert 'StoreParameter "patch_L", "10.5"' == param_vba

    label, material_vba = cp.create_material("MySub", epsilon=2.2, tand=0.0009, tand_freq=9.4)
    assert label == "define material: MySub"
    assert '.Name "MySub"' in material_vba
    assert '.Epsilon "2.2"' in material_vba
    assert '.TanD "0.0009"' in material_vba
    assert '.TanDGiven "True"' in material_vba

    cp.register_material("MySub")
    label, brick_vba = cp.create_brick("Patch", "Antenna", "MySub", "-1", "1", "-2", "2", "0", "1")
    assert label == "define brick: Antenna:Patch"
    assert 'With Brick' in brick_vba
    assert '.Component "Antenna"' in brick_vba
    assert '.Material "MySub"' in brick_vba



def test_create_farfield_monitor_vba_shape():
    label, vba = cp.create_farfield_monitor("farfield (f=9.4)", "9.4")
    assert label == "create farfield monitor: farfield (f=9.4)"
    assert 'With Monitor' in vba
    assert '.Name "farfield (f=9.4)"' in vba
    assert '.Domain "Frequency"' in vba
    assert '.FieldType "Farfield"' in vba
    assert '.Frequency "9.4"' in vba
    assert '.MonitorValue' not in vba
    assert '.UseSubvolume "False"' in vba



def test_primitives_registry_contains_core_builders():
    for name in [
        "set_units",
        "store_parameter",
        "create_material",
        "create_brick",
        "create_extruded_polygon",
        "transform_shape",
        "set_wcs",
        "create_discrete_port",
        "create_waveguide_port",
        "create_farfield_monitor",
        "create_frequency_field_monitor",
        "create_mesh_refinement",
        "add_solid_to_mesh_group",
        "add_solids_to_mesh_group",
        "run_solver",
    ]:
        assert name in cp.PRIMITIVES


def test_create_extruded_polygon_generates_official_pointlist_shape():
    label, vba = cp.create_extruded_polygon(
        "USlot",
        "Antenna",
        "PEC",
        [["-slot_w/2", "0"], ["slot_w/2", "0"], ["slot_w/2", "slot_l"], ["-slot_w/2", "slot_l"]],
        "copper_t",
        origin=["0", "0", "substrate_h"],
    )

    assert label == "define extruded polygon: Antenna:USlot"
    assert 'With Extrude' in vba
    assert '.Mode "Pointlist"' in vba
    assert '.Origin "0", "0", "substrate_h"' in vba
    assert '.Point "-slot_w/2", "0"' in vba
    assert '.LineTo "-slot_w/2", "slot_l"' in vba
    assert '.Height "copper_t"' in vba


def test_create_extruded_polygon_rejects_injection_and_invalid_profiles():
    import pytest

    with pytest.raises(ValueError, match="3 到 512"):
        cp.create_extruded_polygon("slot", "Antenna", "PEC", [["0", "0"], ["1", "0"]], "1")
    with pytest.raises(ValueError, match="VBA"):
        cp.create_extruded_polygon(
            "slot",
            "Antenna",
            "PEC",
            [["0", "0"], ["1", "0"], ['1"\nSolver.Start', "1"]],
            "1",
        )


def test_transform_shape_generates_bounded_copy_transform():
    label, vba = cp.transform_shape(
        "Antenna:Element",
        "translate",
        vector=["period_x", "0", "0"],
        copy=True,
        repetitions=4,
    )

    assert label == "translate shape: Antenna:Element"
    assert '.Name "Antenna:Element"' in vba
    assert '.Vector "period_x", "0", "0"' in vba
    assert '.MultipleObjects "True"' in vba
    assert '.Repetitions "4"' in vba
    assert '.Transform "Shape", "Translate"' in vba


def test_transform_shape_rejects_unbounded_or_noop_transform():
    import pytest

    with pytest.raises(ValueError, match="1 到 64"):
        cp.transform_shape("Antenna:Element", "translate", vector=["1", "0", "0"], repetitions=65)
    with pytest.raises(ValueError, match="不能全部为 0"):
        cp.transform_shape("Antenna:Element", "rotate", angle=["0", "0", "0"])


def test_set_wcs_supports_explicit_local_and_global_modes():
    label, local_vba = cp.set_wcs(
        "local",
        origin=["feed_x", "feed_y", "0"],
        u_vector=["0", "1", "0"],
        normal=["0", "0", "1"],
    )
    assert label == "define local WCS"
    assert 'WCS.SetOrigin "feed_x", "feed_y", "0"' in local_vba
    assert 'WCS.SetUVector "0", "1", "0"' in local_vba
    assert local_vba.endswith('WCS.ActivateWCS "local"')

    assert cp.set_wcs("global") == ("activate global WCS", 'WCS.ActivateWCS "global"')


# ── Mesh refinement primitive ───────────────────────────────────────────


def test_create_mesh_refinement_vba_shape():
    label, vba = cp.create_mesh_refinement("feed_zone", 0.6)
    assert "create mesh refinement: feed_zone" in label
    assert 'Group.Add "feed_zone", "mesh"' in vba
    assert 'With MeshSettings' in vba
    assert '.ItemMeshSettings("group$feed_zone")' in vba
    assert '.SetMeshType "Hex"' in vba
    assert '.Set "Step", "0.6", "0.6", "0.6"' in vba
    assert '.SetMeshType "Tet"' in vba
    assert '.Set "Size", "0.6"' in vba


def test_create_mesh_refinement_strips_trailing_zeros():
    """0.6000 应输出为 "0.6"（与 format_mm 一致），避免雕花式精度污染。"""
    _, vba = cp.create_mesh_refinement("g", 1.0)
    assert '"1"' in vba
    assert '"1.0000"' not in vba


def test_create_mesh_refinement_rejects_invalid_input():
    import pytest
    with pytest.raises(ValueError):
        cp.create_mesh_refinement("", 0.5)
    with pytest.raises(ValueError):
        cp.create_mesh_refinement("bad:name", 0.5)
    with pytest.raises(ValueError):
        cp.create_mesh_refinement("g", 0)
    with pytest.raises(ValueError):
        cp.create_mesh_refinement("g", -0.1)


def test_add_solid_to_mesh_group_vba_shape():
    label, vba = cp.add_solid_to_mesh_group("AntennaFP1:Patch", "feed_zone")
    assert "add to mesh group" in label
    assert 'Group.AddItem "solid$AntennaFP1:Patch", "feed_zone"' == vba


def test_add_solid_to_mesh_group_rejects_invalid_input():
    import pytest
    with pytest.raises(ValueError):
        cp.add_solid_to_mesh_group("missing_colon", "g")  # 无 ":"
    with pytest.raises(ValueError):
        cp.add_solid_to_mesh_group("AntennaFP1:Patch", "")  # 空 group


def test_frequency_field_monitor_uses_official_frequency_and_dimensions():
    _, e_vba = cp.create_frequency_field_monitor("efield (f=9.4)", 9.4, "Efield")
    _, h_vba = cp.create_frequency_field_monitor("hfield (f=9.4)", 9.4, "Hfield")
    _, farfield_vba = cp.create_frequency_field_monitor("farfield (f=9.4)", 9.4, "Farfield")

    assert '.Dimension "Volume"' in e_vba
    assert '.FieldType "Efield"' in e_vba
    assert '.Dimension "Volume"' in h_vba
    assert '.FieldType "Hfield"' in h_vba
    assert '.Dimension' not in farfield_vba
    assert '.FieldType "Farfield"' in farfield_vba
    assert all('.Frequency "9.4"' in value for value in (e_vba, h_vba, farfield_vba))
    assert all(".MonitorValue" not in value for value in (e_vba, h_vba, farfield_vba))


def test_frequency_field_monitor_rejects_invalid_inputs():
    import pytest

    with pytest.raises(ValueError, match="frequency"):
        cp.create_frequency_field_monitor("e", 0, "Efield")
    with pytest.raises(ValueError, match="field_type"):
        cp.create_frequency_field_monitor("e", 1, "Electric")
    with pytest.raises(ValueError, match="VBA"):
        cp.create_frequency_field_monitor('bad"\nSolver.Start', 1, "Efield")


def test_add_solids_to_mesh_group_uses_structured_references_in_order():
    label, vba = cp.add_solids_to_mesh_group(
        [
            {"component": "Antenna", "name": "Feed"},
            {"component": "Antenna", "name": "Patch"},
        ],
        "feed_zone",
    )

    assert label == "add 2 solids to mesh group: feed_zone"
    assert vba.splitlines() == [
        'Group.AddItem "solid$Antenna:Feed", "feed_zone"',
        'Group.AddItem "solid$Antenna:Patch", "feed_zone"',
    ]


def test_waveguide_port_free_mode_uses_official_ranges():
    label, vba = cp.create_waveguide_port(
        1,
        "Free",
        "zmax",
        number_of_modes=2,
        ranges={"x": ["-1", "1"], "y": ["-0.3", "0.2"], "z": ["1.1", "1.1"]},
        port_on_bound=False,
        reference_plane_distance=-5,
    )

    assert label == "define waveguide port: 1"
    assert '.Coordinates "Free"' in vba
    assert '.Orientation "zmax"' in vba
    assert '.NumberOfModes "2"' in vba
    assert '.Xrange "-1", "1"' in vba
    assert '.ReferencePlaneDistance "-5"' in vba


def test_waveguide_port_picks_mode_emits_pick_before_port():
    _, vba = cp.create_waveguide_port(
        2,
        "Picks",
        "Positive",
        pick={"solid": "Waveguide:Body", "face_id": 3},
        range_add={"x": [0, 0], "y": ["-0.2", "0.2"], "z": [0, 0]},
    )

    assert vba.startswith('Pick.ClearAllPicks\nPick.PickFaceFromId "Waveguide:Body", "3"')
    assert '.Coordinates "Picks"' in vba
    assert '.YrangeAdd "-0.2", "0.2"' in vba


def test_waveguide_port_full_mode_uses_boundary_without_ranges():
    _, vba = cp.create_waveguide_port(2, "Full", "zmin")

    assert '.Coordinates "Full"' in vba
    assert '.Orientation "zmin"' in vba
    assert "range" not in vba.lower()


def test_waveguide_port_picks_mode_defaults_range_add_but_rejects_explicit_empty_object():
    _, vba = cp.create_waveguide_port(
        2,
        "Picks",
        "Positive",
        pick={"solid": "Waveguide:Body", "face_id": 3},
    )

    assert '.XrangeAdd "0", "0"' in vba
    with pytest.raises(ValueError, match="range_add"):
        cp.create_waveguide_port(
            2,
            "Picks",
            "Positive",
            pick={"solid": "Waveguide:Body", "face_id": 3},
            range_add={},
        )


def test_waveguide_port_rejects_mixed_mode_arguments_and_orientation():
    with pytest.raises(ValueError, match="ranges"):
        cp.create_waveguide_port(1, "Free", "zmin")
    with pytest.raises(ValueError, match="orientation"):
        cp.create_waveguide_port(1, "Picks", "zmin", pick={"solid": "A:B", "face_id": 1})
    with pytest.raises(ValueError, match="不接受"):
        cp.create_waveguide_port(1, "Full", "zmin", ranges={"x": [0, 0], "y": [0, 0], "z": [0, 0]})
