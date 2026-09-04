* Gallery: semiconductor devices — BJTs, JFETs, MOSFETs, opamps.
* Kinds are trailing args: Q1 c b e pnp, J1 d g s pjf, M1 d g s pmos.
V1 N0 N10 {12}
Q1 N1 N2 N3
Q2 N10 N11 N3 pnp
J1 N1 N4 N5
J2 N10 N6 N7 pjf
M1 N1 N8 N9
M2 N10 N11 N12 pmos
E1 N13 G0 opamp N1 G0 {10000}
E2 N14 G0 fdopamp N4 G4 N13 Ad=1000
E3 N15 G0 inamp N4 G4 G0 {10}
R1 N1 N10 {1k}
R2 N3 G0 {1k}
R3 N5 G0 {1k}
R4 N7 G0 {1k}
R5 N9 G0 {1k}
W G0 N0
