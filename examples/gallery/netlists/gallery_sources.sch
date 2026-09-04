* Gallery: sources — independent (dc/ac/sin), dependent (F/H/E/G),
* meters, battery, crystal, antenna, port, transformer.
V1 N0 N1 {12}
V2 N1 N2 ac {1}
V3 N2 N3 sin {1}
I1 N2 G1 {1m}
I2 N3 G2 ac {1m}
F1 N3 G2 V1 5
H1 N4 G0 V1 10
E1 N4 G0 N1 G0 2
G1 N4 G0 N1 G0 {2m}
AM1 N5 N6
VM1 N6 N7
BAT1 N7 N8 {9}
XT1 N8 N9
ANT1 N9
P1 N8 N9
TF1 N1 N3 N5 N7
k1 N9 N10 {100}
m1 N10 N11 {1}
r1 N11 N0 {0.5}
Vcc1 N9 0
Vee1 N8 0
W G0 N0
W G0 G1
W G0 G2
