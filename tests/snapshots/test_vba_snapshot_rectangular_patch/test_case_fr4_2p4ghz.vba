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

StoreParameter "feed_W", "3.0829"

StoreParameter "feed_L", "22.0662"

StoreParameter "inset_gap", "0.64"

StoreParameter "notch_W", "4.3629"

StoreParameter "inset_depth", "10.7724"

StoreParameter "sub_W", "57.21"

StoreParameter "sub_L", "61.0878"

StoreParameter "ground_W", "57.21"

StoreParameter "ground_L", "61.0878"

StoreParameter "rear_margin", "9.6"

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
    .Ymin "open"
    .Ymax "expanded open"
    .Zmin "electric"
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
    .Yrange "-patch_L/2-feed_L", "patch_L/2+rear_margin"
    .Zrange "-copper_t", "0"
    .Create
End With

With Brick
    .Reset
    .Name "Substrate"
    .Component "AntennaFP1"
    .Material "FR-4 (lossy)"
    .Xrange "-ground_W/2", "ground_W/2"
    .Yrange "-patch_L/2-feed_L", "patch_L/2+rear_margin"
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

With Brick
    .Reset
    .Name "InsetGap"
    .Component "FeedFP1"
    .Material "Vacuum"
    .Xrange "-notch_W/2", "notch_W/2"
    .Yrange "-patch_L/2", "-patch_L/2+inset_depth"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "FeedLine"
    .Component "FeedFP1"
    .Material "Copper (annealed)"
    .Xrange "-feed_W/2", "feed_W/2"
    .Yrange "-patch_L/2-feed_L", "-patch_L/2+inset_depth"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

Solid.Subtract "AntennaFP1:Patch", "FeedFP1:InsetGap"

Solid.Add "AntennaFP1:Patch", "FeedFP1:FeedLine"

Group.Add "feed_refine_AntennaFP1", "mesh"

With MeshSettings
  With .ItemMeshSettings("group$feed_refine_AntennaFP1")
    .SetMeshType "Hex"
    .Set "Step", "0.7707", "0.7707", "0.7707"
    .SetMeshType "Tet"
    .Set "Size", "0.7707"
  End With
End With

Group.AddItem "solid$AntennaFP1:Patch", "feed_refine_AntennaFP1"

' --- port ---
Pick.ClearAllPicks
Pick.PickFaceFromPoint "AntennaFP1:Patch", 0, -36.777, 1.6175
With Port
    .Reset
    .PortNumber "1"
    .Label "MS_WG_Port_1"
    .Folder ""
    .NumberOfModes "1"
    .AdjustPolarization "False"
    .PolarizationAngle "0.0"
    .ReferencePlaneDistance "0.0"
    .TextSize "50"
    .TextMaxLimit "1"
    .Coordinates "Picks"
    .Orientation "positive"
    .PortOnBound "False"
    .ClipPickedPortToBound "False"
    .Xrange "-feed_W/2", "feed_W/2"
    .Yrange "-patch_L/2-feed_L", "-patch_L/2-feed_L"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .XrangeAdd "3*substrate_h", "3*substrate_h"
    .YrangeAdd "0.0", "0.0"
    .ZrangeAdd "substrate_h", "3*substrate_h"
    .SingleEnded "False"
    .WaveguideMonitor "False"
    .Create
End With
Pick.ClearAllPicks

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
