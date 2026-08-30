With Units
    .Geometry "mm"
    .Frequency "GHz"
    .Time "ns"
End With

StoreParameter "arm_length", "29.354678"

StoreParameter "arm_width", "1.467734"

StoreParameter "gap", "0.587094"

StoreParameter "copper_t", "0.035"

StoreParameter "f0", "2.4"

With Brick
    .Reset
    .Name "upper_arm"
    .Component "dipole"
    .Material "Copper (annealed)"
    .Xrange "-arm_width/2", "arm_width/2"
    .Yrange "-copper_t/2", "copper_t/2"
    .Zrange "gap/2", "gap/2+arm_length"
    .Create
End With

With Brick
    .Reset
    .Name "lower_arm"
    .Component "dipole"
    .Material "Copper (annealed)"
    .Xrange "-arm_width/2", "arm_width/2"
    .Yrange "-copper_t/2", "copper_t/2"
    .Zrange "-(gap/2+arm_length)", "-gap/2"
    .Create
End With

With DiscretePort
    .Reset
    .PortNumber "1"
    .Type "SParameter"
    .Impedance "50"
    .SetP1 "False", "0", "0", "-0.293547"
    .SetP2 "False", "0", "0", "0.293547"
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

Solver.FrequencyRange "1.68", "3.12"

With Monitor
    .Reset
    .Name "farfield (f=2.4)"
    .Domain "Frequency"
    .FieldType "Farfield"
    .Frequency "2.4"
    .UseSubvolume "False"
    .Create
End With
