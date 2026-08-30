' --- setup ---
With Units
    .Geometry "mm"
    .Frequency "GHz"
    .Time "ns"
End With

StoreParameter "f0", "2.4"

StoreParameter "er", "4.4"

StoreParameter "tand", "0.02"

StoreParameter "substrate_h", "1.6"

StoreParameter "copper_t", "0.035"

StoreParameter "patch_W", "38.01"

StoreParameter "patch_L", "29.4216"

StoreParameter "sub_W", "57.21"

StoreParameter "sub_L", "61.0878"

StoreParameter "ground_W", "57.21"

StoreParameter "ground_L", "61.0878"

StoreParameter "rear_margin", "9.6"

StoreParameter "probe_R", "0.5"

StoreParameter "probe_Y", "-3.9384"

StoreParameter "hole_R", "0.8"

With Material
    .Reset
    .Name "FR-4 (lossy)"
    .Folder ""
    .FrqType "hf"
    .Type "Normal"
    .MaterialUnit "Frequency", "GHz"
    .MaterialUnit "Geometry", "mm"
    .Epsilon "4.4"
    .Mue "1"
    .Kappa "0"
    .TanD "0.02"
    .TanDFreq "2.4"
    .TanDGiven "True"
    .TanDModel "ConstTanD"
    .SetConstTanDStrategyEps "AutomaticOrder"
    .ConstTanDModelOrderEps "3"
    .DjordjevicSarkarUpperFreqEps "0"
    .SetElParametricConductivity "False"
    .KappaM "0.0"
    .TanDM "0.0"
    .TanDMFreq "0.0"
    .TanDMGiven "False"
    .TanDMModel "ConstSigma"
    .SetConstTanDStrategyMu "AutomaticOrder"
    .ConstTanDModelOrderMu "3"
    .DjordjevicSarkarUpperFreqMu "0"
    .SetMagParametricConductivity "False"
    .DispModelEps "None"
    .DispModelMue "None"
    .Rho "0.0"
    .Colour "0.85", "0.65", "0.20"
    .Wireframe "False"
    .Transparency "0"
    .Create
End With

Solver.FrequencyRange "1.8", "3"

With Boundary
    .Xmin "expanded open"
    .Xmax "expanded open"
    .Ymin "expanded open"
    .Ymax "expanded open"
    .Zmin "expanded open"
    .Zmax "expanded open"
    .Xsymmetry "none"
    .Ysymmetry "none"
    .Zsymmetry "none"
    .ApplyInAllDirections "False"
End With

' --- geometry ---
With Brick
    .Reset
    .Name "Ground"
    .Component "AntennaFP1"
    .Material "Copper (annealed)"
    .Xrange "-ground_W/2", "ground_W/2"
    .Yrange "-sub_L/2", "sub_L/2"
    .Zrange "-copper_t", "0"
    .Create
End With

With Brick
    .Reset
    .Name "Substrate"
    .Component "AntennaFP1"
    .Material "FR-4 (lossy)"
    .Xrange "-ground_W/2", "ground_W/2"
    .Yrange "-sub_L/2", "sub_L/2"
    .Zrange "0", "substrate_h"
    .Create
End With

With Brick
    .Reset
    .Name "Patch"
    .Component "AntennaFP1"
    .Material "Copper (annealed)"
    .Xrange "-patch_W/2", "patch_W/2"
    .Yrange "-patch_L/2", "patch_L/2"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Cylinder
    .Reset
    .Name "Probe"
    .Component "FeedFP1"
    .Material "Copper (annealed)"
    .OuterRadius "probe_R"
    .InnerRadius "0"
    .Axis "z"
    .Xcenter "0"
    .Ycenter "probe_Y"
    .Zrange "0", "substrate_h+copper_t"
    .Create
End With

With Cylinder
    .Reset
    .Name "GroundHole"
    .Component "FeedFP1"
    .Material "Vacuum"
    .OuterRadius "hole_R"
    .InnerRadius "0"
    .Axis "z"
    .Xcenter "0"
    .Ycenter "probe_Y"
    .Zrange "-copper_t", "0"
    .Create
End With

Solid.Subtract "AntennaFP1:Ground", "FeedFP1:GroundHole"

Group.Add "probe_refine_AntennaFP1", "mesh"

With MeshSettings
  With .ItemMeshSettings("group$probe_refine_AntennaFP1")
    .SetMeshType "Hex"
    .Set "Step", "0.25", "0.25", "0.25"
    .SetMeshType "Tet"
    .Set "Size", "0.25"
  End With
End With

Group.AddItem "solid$FeedFP1:Probe", "probe_refine_AntennaFP1"

' --- port ---
With DiscretePort
    .Reset
    .PortNumber "1"
    .Type "SParameter"
    .Impedance "50"
    .SetP1 "False", "0", "-3.9384", "-0.035"
    .SetP2 "False", "0", "-3.9384", "0"
    .InvertDirection "False"
    .LocalCoordinates "False"
    .Monitor "True"
    .Radius "0.0"
    .Create
End With

' --- farfield ---
With Monitor
    .Reset
    .Name "farfield (f=2.4)"
    .Domain "Frequency"
    .FieldType "Farfield"
    .Frequency "2.4"
    .UseSubvolume "False"
    .Create
End With
