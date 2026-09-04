* Gallery: diodes and switches (all D kinds, all SW kinds, TP/ADC/DAC).
* Kind keywords are trailing args: D1 a b led, SW1 a b nc.
V1 N0 N14 {12}
D1 N1 N2
D2 N2 N3 led
D3 N3 N4 schottky
D4 N4 N5 zener
D5 N5 N6 photo
SW1 N6 N7
SW2 N7 N8 no
SW3 N8 N9 nc
SW4 N9 N14 push
SW5 N10 N11 N12 spdt
TP1 N1 G0 Nout G0
ADC1 N11 G0
DAC1 N12 G0
R1 N0 N1 {1k}
R2 N12 G0 {1k}
W G0 N0
