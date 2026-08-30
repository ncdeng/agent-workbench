' --- setup ---
With Units
    .Geometry "mm"
    .Frequency "GHz"
    .Time "ns"
End With

StoreParameter "f0", "9.4"

StoreParameter "er", "2.2"

StoreParameter "tand", "0.0009"

StoreParameter "substrate_h", "1.6"

StoreParameter "copper_t", "0.035"

StoreParameter "patch_W", "12.6067"

StoreParameter "patch_L", "9.7008"

StoreParameter "feed_W", "4.971"

StoreParameter "feed_L", "7.2756"

StoreParameter "inset_gap", "0.64"

StoreParameter "notch_W", "6.251"

StoreParameter "inset_depth", "3.5518"

StoreParameter "sub_W", "31.8067"

StoreParameter "sub_L", "26.5763"

StoreParameter "ground_W", "31.8067"

StoreParameter "ground_L", "26.5763"

StoreParameter "rear_margin", "9.6"

With Material
    .Reset
    .Name "Rogers5880"
    .Folder ""
    .FrqType "hf"
    .Type "Normal"
    .MaterialUnit "Frequency", "GHz"
    .MaterialUnit "Geometry", "mm"
    .Epsilon "2.2"
    .Mue "1"
    .Kappa "0"
    .TanD "0.0009"
    .TanDFreq "9.4"
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

Solver.FrequencyRange "7.05", "11.75"

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
    .Material "Rogers5880"
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
    .Set "Step", "1.2427", "1.2427", "1.2427"
    .SetMeshType "Tet"
    .Set "Size", "1.2427"
  End With
End With

Group.AddItem "solid$AntennaFP1:Patch", "feed_refine_AntennaFP1"

' --- port ---
Pick.ClearAllPicks
Pick.PickFaceFromPoint "AntennaFP1:Patch", 0, -12.1259, 1.6175
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
    .Name "farfield (f=9.4)"
    .Domain "Frequency"
    .FieldType "Farfield"
    .Frequency "9.4"
    .UseSubvolume "False"
    .Create
End With
