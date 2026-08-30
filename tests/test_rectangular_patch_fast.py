from cst_agent_workbench.cst.rectangular_patch_fast import (
    DEFAULT_PATCH_CONDUCTOR_THICKNESS_MM,
    DEFAULT_PATCH_EPSILON_R,
    DEFAULT_PATCH_LOSS_TANGENT,
    DEFAULT_PATCH_SUBSTRATE_NAME,
    DEFAULT_PATCH_SUBSTRATE_THICKNESS_MM,
    RectangularPatchRequest,
    build_explicit_waveguide_port_vba,
    build_rectangular_patch_vba_artifact,
    build_side_waveguide_port_vba,
    parse_rectangular_patch_request,
    resolve_rectangular_patch_request,
    synthesize_rectangular_patch,
)


def test_parse_rectangular_patch_request_uses_standard_defaults():
    req = parse_rectangular_patch_request("创建一个 9.4GHz 矩形贴片", "microstrip")

    assert req is not None
    assert req.f0_ghz == 9.4
    assert req.substrate_name == DEFAULT_PATCH_SUBSTRATE_NAME
    assert req.epsilon_r == DEFAULT_PATCH_EPSILON_R
    assert req.loss_tangent == DEFAULT_PATCH_LOSS_TANGENT
    assert req.substrate_thickness_mm == DEFAULT_PATCH_SUBSTRATE_THICKNESS_MM
    assert req.conductor_thickness_mm == DEFAULT_PATCH_CONDUCTOR_THICKNESS_MM
    assert req.feed_strategy == "microstrip"


def test_parse_rectangular_patch_request_standard():
    text = (
        "创建一个矩形微带贴片天线, 中心频率 9.4 GHz, 基板材料 Rogers5880, "
        "介电常数 2.2, 损耗角正切 0.0009, 基板厚度 1.6 mm, 铜厚 0.035 mm"
    )
    req = parse_rectangular_patch_request(text, "microstrip")

    assert req is not None
    assert req.f0_ghz == 9.4
    assert req.substrate_name == "Rogers5880"
    assert req.epsilon_r == 2.2
    assert req.loss_tangent == 0.0009
    assert req.substrate_thickness_mm == 1.6
    assert req.conductor_thickness_mm == 0.035
    assert req.feed_strategy == "microstrip"



def test_resolve_rectangular_patch_request_followup_inherits_previous():
    previous = RectangularPatchRequest(
        f0_ghz=9.4,
        substrate_name="Rogers5880",
        epsilon_r=2.2,
        loss_tangent=0.0009,
        substrate_thickness_mm=1.6,
        conductor_name="Copper (annealed)",
        conductor_thickness_mm=0.035,
        feed_strategy="microstrip",
    )

    req = resolve_rectangular_patch_request("其他都一样，只改成 10.2 GHz，再做一个", "microstrip", previous)

    assert req is not None
    assert req.f0_ghz == 10.2
    assert req.substrate_name == previous.substrate_name
    assert req.epsilon_r == previous.epsilon_r
    assert req.loss_tangent == previous.loss_tangent
    assert req.substrate_thickness_mm == previous.substrate_thickness_mm
    assert req.conductor_thickness_mm == previous.conductor_thickness_mm


def test_resolve_rectangular_patch_followup_preserves_probe_feed_without_explicit_feed():
    previous = RectangularPatchRequest(
        f0_ghz=9.4,
        substrate_name="Rogers5880",
        epsilon_r=2.2,
        loss_tangent=0.0009,
        substrate_thickness_mm=0.508,
        conductor_name="Copper (annealed)",
        conductor_thickness_mm=0.035,
        feed_strategy="probe",
    )

    req = resolve_rectangular_patch_request(
        "same as previous one, only change the center frequency to 10.2 GHz and create another patch",
        "microstrip",
        previous,
    )

    assert req is not None
    assert req.f0_ghz == 10.2
    assert req.feed_strategy == "probe"


def test_resolve_rectangular_patch_followup_allows_explicit_microstrip_override():
    previous = RectangularPatchRequest(
        f0_ghz=9.4,
        substrate_name="Rogers5880",
        epsilon_r=2.2,
        loss_tangent=0.0009,
        substrate_thickness_mm=0.508,
        conductor_name="Copper (annealed)",
        conductor_thickness_mm=0.035,
        feed_strategy="probe",
    )

    req = resolve_rectangular_patch_request(
        "same as previous one, change the center frequency to 10.2 GHz, use microstrip feed, and create another patch",
        "microstrip",
        previous,
    )

    assert req is not None
    assert req.feed_strategy == "microstrip"


def test_parse_rectangular_patch_request_recognizes_standalone_rogers_5880_preset():
    req = parse_rectangular_patch_request(
        "在工程中建立一个 9.4 GHz、Rogers 5880、厚度 0.508 mm 的标准矩形微带贴片天线",
        "microstrip",
    )

    assert req is not None
    assert req.f0_ghz == 9.4
    assert req.substrate_name == "Rogers5880"
    assert req.epsilon_r == 2.2
    assert req.loss_tangent == 0.0009
    assert req.substrate_thickness_mm == 0.508



def test_synthesize_rectangular_patch_returns_positive_complete_dimensions():
    req = RectangularPatchRequest(
        f0_ghz=9.4,
        substrate_name="Rogers5880",
        epsilon_r=2.2,
        loss_tangent=0.0009,
        substrate_thickness_mm=1.6,
        conductor_name="Copper (annealed)",
        conductor_thickness_mm=0.035,
        feed_strategy="microstrip",
    )

    dims = synthesize_rectangular_patch(req)

    expected_keys = {
        "patch_w", "patch_l", "feed_w", "feed_l", "inset_gap", "notch_w",
        "inset_depth", "sub_w", "sub_l", "ground_w", "ground_l",
        "substrate_margin", "rear_margin", "fmin", "fmax",
    }
    assert expected_keys.issubset(dims.keys())
    assert all(dims[key] > 0 for key in expected_keys)
    assert dims["fmin"] < req.f0_ghz < dims["fmax"]



def test_build_rectangular_patch_vba_artifact_contains_complete_sections():
    req = RectangularPatchRequest(
        f0_ghz=9.4,
        substrate_name="Rogers5880",
        epsilon_r=2.2,
        loss_tangent=0.0009,
        substrate_thickness_mm=1.6,
        conductor_name="Copper (annealed)",
        conductor_thickness_mm=0.035,
        feed_strategy="microstrip",
    )

    artifact = build_rectangular_patch_vba_artifact(req)

    assert set(artifact["sections"].keys()) == {"setup", "geometry", "port", "farfield"}
    assert artifact["dims"]["patch_w"] > 0
    assert artifact["dims"]["patch_l"] > 0
    assert artifact["parameter_values"]["f0"] == 9.4
    vba = artifact["vba_code"]
    assert 'StoreParameter "f0", "9.4"' in vba
    assert "With Brick" in vba
    assert 'Solid.Subtract "AntennaFP1:Patch", "FeedFP1:InsetGap"' in vba
    assert "MS_WG_Port_1" in vba
    assert '.Coordinates "Picks"' in vba
    assert 'Pick.PickFaceFromPoint "AntennaFP1:Patch"' in vba
    assert '.FieldType "Farfield"' in vba



def test_build_probe_fed_patch_vba_artifact_contains_probe_sections():
    req = RectangularPatchRequest(
        f0_ghz=9.4,
        substrate_name="Rogers5880",
        epsilon_r=2.2,
        loss_tangent=0.0009,
        substrate_thickness_mm=1.6,
        conductor_name="Copper (annealed)",
        conductor_thickness_mm=0.035,
        feed_strategy="probe",
    )

    artifact = build_rectangular_patch_vba_artifact(req)

    assert set(artifact["sections"].keys()) == {"setup", "geometry", "port", "farfield"}
    assert artifact["probe"]["probe_radius_mm"] > 0
    assert artifact["parameter_values"]["probe_R"] == artifact["probe"]["probe_radius_mm"]
    vba = artifact["vba_code"]
    assert 'StoreParameter "probe_R"' in vba
    assert 'StoreParameter "hole_R"' in vba
    assert 'With Cylinder' in vba
    assert 'With DiscretePort' in vba
    assert 'Solid.Subtract "AntennaFP1:Ground", "FeedFP1:GroundHole"' in vba



def test_build_side_waveguide_port_vba_contains_key_statements():
    vba = build_side_waveguide_port_vba(
        port_number=1,
        pick_solid="Comp:Patch",
        pick_x="0",
        pick_y="-10",
        pick_z="1.6175",
        y_pos="-patch_L/2-feed_L",
        feed_width="feed_W",
        substrate_height="substrate_h",
        copper_thickness="copper_t",
    )

    assert 'Pick.PickFaceFromPoint "Comp:Patch", 0, -10, 1.6175' in vba
    assert '.Coordinates "Picks"' in vba
    assert '.PortNumber "1"' in vba
    assert '.Label "MS_WG_Port_1"' in vba
    assert '.Xrange "-feed_W/2", "feed_W/2"' in vba
    assert '.Yrange "-patch_L/2-feed_L", "-patch_L/2-feed_L"' in vba
    assert '.Zrange "substrate_h", "substrate_h+copper_t"' in vba
    assert '.Create' in vba


def test_build_explicit_waveguide_port_vba_uses_free_parametric_coordinates():
    vba = build_explicit_waveguide_port_vba(
        port_number=1,
        y_pos="-patch_L/2-feed_L",
        feed_width="feed_W",
        substrate_height="substrate_h",
        copper_thickness="copper_t",
    )

    assert 'Pick.PickFaceFromPoint' not in vba
    assert '.Coordinates "Free"' in vba
    assert '.Orientation "ymin"' in vba
    assert '.PortOnBound "False"' in vba
    assert '.PortNumber "1"' in vba
    assert '.Label "MS_WG_Port_1"' in vba
    assert '.Xrange "-feed_W/2", "feed_W/2"' in vba
    assert '.Yrange "-patch_L/2-feed_L", "-patch_L/2-feed_L"' in vba
    assert '.Zrange "substrate_h", "substrate_h+copper_t"' in vba
    assert '.Create' in vba
