With Units
    .Geometry "mm"
    .Frequency "GHz"
    .Time "ns"
End With

StoreParameter "substrate_h", "1.6"

StoreParameter "copper_t", "0.035"

StoreParameter "total_w", "8.0"

StoreParameter "total_l", "8.0"

StoreParameter "feed_x", "5.0"

StoreParameter "feed_y", "3.0"

StoreParameter "cell_size", "2.0"

StoreParameter "f0", "9.4"

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

With Brick
    .Reset
    .Name "ground"
    .Component "ground_plane"
    .Material "PEC"
    .Xrange "0", "total_w"
    .Yrange "0", "total_l"
    .Zrange "-copper_t", "0"
    .Create
End With

With Brick
    .Reset
    .Name "substrate"
    .Component "substrate"
    .Material "FR-4 (lossy)"
    .Xrange "0", "total_w"
    .Yrange "0", "total_l"
    .Zrange "0", "substrate_h"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_0_0"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "0.0", "2.0"
    .Yrange "6.0", "8.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_0_1"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "2.0", "4.0"
    .Yrange "6.0", "8.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_0_2"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "4.0", "6.0"
    .Yrange "6.0", "8.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_0_3"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "6.0", "8.0"
    .Yrange "6.0", "8.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_1_0"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "0.0", "2.0"
    .Yrange "4.0", "6.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_1_1"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "2.0", "4.0"
    .Yrange "4.0", "6.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_1_2"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "4.0", "6.0"
    .Yrange "4.0", "6.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_1_3"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "6.0", "8.0"
    .Yrange "4.0", "6.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_2_0"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "0.0", "2.0"
    .Yrange "2.0", "4.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_2_1"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "2.0", "4.0"
    .Yrange "2.0", "4.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "feed_pixel"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "4.0", "6.0"
    .Yrange "2.0", "4.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_2_3"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "6.0", "8.0"
    .Yrange "2.0", "4.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_3_0"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "0.0", "2.0"
    .Yrange "0.0", "2.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_3_1"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "2.0", "4.0"
    .Yrange "0.0", "2.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_3_2"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "4.0", "6.0"
    .Yrange "0.0", "2.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With Brick
    .Reset
    .Name "pixel_3_3"
    .Component "pixel_patch"
    .Material "PEC"
    .Xrange "6.0", "8.0"
    .Yrange "0.0", "2.0"
    .Zrange "substrate_h", "substrate_h+copper_t"
    .Create
End With

With DiscretePort
    .Reset
    .PortNumber "1"
    .Type "SParameter"
    .Impedance "50"
    .SetP1 "False", "5.0", "3.0", "0"
    .SetP2 "False", "5.0", "3.0", "1.6"
    .InvertDirection "False"
    .LocalCoordinates "False"
    .Monitor "True"
    .Radius "0.0"
    .Create
End With

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

Solver.FrequencyRange "6.58", "12.22"

With Monitor
    .Reset
    .Name "farfield (f=9.4)"
    .Domain "Frequency"
    .FieldType "Farfield"
    .Frequency "9.4"
    .UseSubvolume "False"
    .Create
End With
