# Relay-enabled illustrative tables

Not measured; see ANALYSIS.md for limitations.

| Task / burst | C us | B us | R us | D us | Interpretation |
|---|---:|---:|---:|---:|---|
| N2 IRQ batch | 270 | 5 | 275 | 300 | within assumption |
| N2 RX driver batch | 2970 | 50 | 3290 | 300 | not established |
| N2 dispatch batch | 1080 | 5 | 4325 | 300 | not established |
| N2 TX A/B + RELAY service batch | 780 | 50 | 5150 | 2000 | not established |
| N2 BLINK_F | 40 | 5 | 5145 | 500 | not established |
| N2 STEER | 55 | 5 | 5200 | 1000 | not established |
| N2 TEST/FAULT enqueue | 30 | 5 | 5230 | 10000 | within assumption |
| N2 HB enqueue | 10 | 5 | 5240 | 20000 | within assumption |
| N2 HB_MON | 30 | 5 | 5270 | 20000 | within assumption |
| N2 FORCE enqueue | 10 | 5 | 5280 | 50000 | within assumption |
| N2 watchdog supervisor | 20 | 5 | 5300 | 100000 | within assumption |
| N3 IRQ batch | 270 | 5 | 275 | 300 | within assumption |
| N3 RX driver batch | 2970 | 50 | 3290 | 300 | not established |
| N3 dispatch batch | 1080 | 5 | 4325 | 300 | not established |
| N3 TX A/B + RELAY service batch | 730 | 50 | 5100 | 2000 | not established |
| N3 DRIVE | 55 | 50 | 5155 | 500 | not established |
| N3 BLINK_R | 40 | 5 | 5150 | 500 | not established |
| N3 TEST/FAULT enqueue | 30 | 5 | 5180 | 10000 | within assumption |
| N3 PID_SIM | 200 | 5 | 5380 | 10000 | within assumption |
| N3 HB enqueue | 10 | 5 | 5590 | 20000 | within assumption |
| N3 HB_MON | 30 | 5 | 5620 | 20000 | within assumption |
| N3 watchdog supervisor | 20 | 5 | 5640 | 100000 | within assumption |

## Ideal bus envelope: original catalog

| Message | J us | C us | B us | w us | R us | D us |
|---|---:|---:|---:|---:|---:|---:|
| CTRL | 500 | 270 | 270 | 270 | 1040 | 2000 |
| FAULT_N1 | 500 | 270 | 270 | 540 | 1310 | 100000 |
| FAULT_N2 | 5000 | 270 | 270 | 810 | 6080 | 100000 |
| FAULT_N3 | 5000 | 270 | 270 | 1080 | 6350 | 100000 |
| HB_N1 | 500 | 270 | 270 | 1350 | 2120 | 20000 |
| HB_N2 | 5000 | 270 | 270 | 1620 | 6890 | 20000 |
| HB_N3 | 5000 | 270 | 270 | 1890 | 7160 | 20000 |
| BLINK | 500 | 270 | 270 | 2160 | 2930 | 100000 |
| FORCE | 5000 | 270 | 0 | 2160 | 7430 | 50000 |

Per-bus envelope utilization: **11.34%**.


## Ideal bus envelope: with relay copies

| Message | J us | C us | B us | w us | R us | D us |
|---|---:|---:|---:|---:|---:|---:|
| CTRL | 500 | 270 | 270 | 810 | 1580 | 2000 |
| FAULT_N1 | 500 | 270 | 270 | 1620 | 2390 | 100000 |
| FAULT_N2 | 5000 | 270 | 270 | 2430 | 7700 | 100000 |
| FAULT_N3 | 5000 | 270 | 270 | 3240 | 8510 | 100000 |
| HB_N1 | 500 | 270 | 270 | 4050 | 4820 | 20000 |
| HB_N2 | 5000 | 270 | 270 | 4860 | 10130 | 20000 |
| HB_N3 | 5000 | 270 | 270 | 5670 | 10940 | 20000 |
| BLINK | 500 | 270 | 270 | 6480 | 7250 | 100000 |
| FORCE | 5000 | 270 | 0 | 7020 | 12290 | 50000 |
| CTRL relay copy 1 | 10000 | 270 | 270 | 810 | 11080 | 2000 |
| FAULT_N1 relay copy 1 | 10000 | 270 | 270 | 1620 | 11890 | 100000 |
| FAULT_N2 relay copy 1 | 10000 | 270 | 270 | 2430 | 12700 | 100000 |
| FAULT_N3 relay copy 1 | 10000 | 270 | 270 | 3240 | 13510 | 100000 |
| HB_N1 relay copy 1 | 10000 | 270 | 270 | 4050 | 14320 | 20000 |
| HB_N2 relay copy 1 | 10000 | 270 | 270 | 4860 | 15130 | 20000 |
| HB_N3 relay copy 1 | 10000 | 270 | 270 | 5670 | 15940 | 20000 |
| BLINK relay copy 1 | 10000 | 270 | 270 | 6480 | 16750 | 100000 |
| FORCE relay copy 1 | 10000 | 270 | 0 | 7020 | 17290 | 50000 |
| CTRL relay copy 2 | 10000 | 270 | 270 | 810 | 11080 | 2000 |
| FAULT_N1 relay copy 2 | 10000 | 270 | 270 | 1620 | 11890 | 100000 |
| FAULT_N2 relay copy 2 | 10000 | 270 | 270 | 2430 | 12700 | 100000 |
| FAULT_N3 relay copy 2 | 10000 | 270 | 270 | 3240 | 13510 | 100000 |
| HB_N1 relay copy 2 | 10000 | 270 | 270 | 4050 | 14320 | 20000 |
| HB_N2 relay copy 2 | 10000 | 270 | 270 | 4860 | 15130 | 20000 |
| HB_N3 relay copy 2 | 10000 | 270 | 270 | 5670 | 15940 | 20000 |
| BLINK relay copy 2 | 10000 | 270 | 270 | 6480 | 16750 | 100000 |
| FORCE relay copy 2 | 10000 | 270 | 0 | 7020 | 17290 | 50000 |

Per-bus envelope utilization: **34.02%**.

Illustrative two-hop brake sum = **20.535 ms**, against 2 ms.
This mixes assumed stage envelopes and is not a validated end-to-end bound.
