# Relay-enabled task communication graph

This graph is a design artifact for the implemented relay demo, not a timing trace.
All CAN edges below exist on A and B. Local arrows are queues, snapshots or events.

```mermaid
flowchart TD
  UDP["N1 UDP_RX: wheel 20 ms"] --> CT["N1 CTRL_TX"]
  UDP --> BT["N1 BLINK_TX: change / 500 ms"]
  HT1["N1 HB_TX: 20 ms"] --> BUS["CAN A + CAN B"]
  CT -->|"0x010, 20 ms, E2E 2 ms"| BUS
  BT -->|"0x200, min 20 ms, start 100 ms"| BUS
  BUS --> RX2["N2 IRQ + CAN_RX_A/B workers"]
  BUS --> RX3["N3 IRQ + CAN_RX_A/B workers"]
  RX2 --> D2["N2 dispatch + dedup"]
  RX3 --> D3["N3 dispatch + dedup"]
  D2 -->|"CTRL snapshot/event; 20 ms; local 1 ms"| S["STEER -> servo PWM"]
  D3 -->|"CTRL snapshot/event; 20 ms; local 0.5 ms"| DR["DRIVE -> PWM/DIR"]
  DR -->|"shared output mutex + state"| PID["PID_SIM: 10 ms; D=10 ms"]
  PID --> PWM["scope PWM output"]
  DR --> PWM
  D2 -->|"BLINK queue; min20 ms; D=0.5 ms"| BF["BLINK_F -> FL/FR"]
  D3 -->|"BLINK queue; min20 ms; D=0.5 ms"| BR["BLINK_R -> RL/RR"]
  D2 --> H2["HB_MON N2: 20 ms -> state/DEG"]
  D3 --> H3["HB_MON N3: 20 ms -> state/DEG"]
  H2 -->|"fault event"| S
  H2 -->|"fault event"| BF
  H3 -->|"fault event"| DR
  H3 -->|"fault event"| BR
  T2["N2 TEST_BTN: min50 ms"] -->|"local error <=10 ms"| H2
  T3["N3 TEST_BTN: min50 ms"] -->|"local error <=10 ms"| H3
  T2 -->|"0x021 FAULT_N2; min20 ms; D100 ms"| BUS
  T3 -->|"0x022 FAULT_N3; min20 ms; D100 ms"| BUS
  HBT2["N2 HB_TX: stops on local failure"] -->|"0x101, 20 ms, D20 ms"| BUS
  HBT3["N3 HB_TX: stops on local failure"] -->|"0x102, 20 ms, D20 ms"| BUS
  FT["N2 FORCE_TX: dummy"] -->|"0x300, 50 ms, D50 ms"| BUS
  BUS --> HM1["N1 HB_MON / FORCE_RX"]
  HM1 --> BT
  HM1 -->|"optional diagnostic UDP"| FUDP["dummy FORCE destination"]
  D2 -->|"first copy; unchanged sequence/payload"| REL["N2 RELAY priority 2"]
  REL -->|"opposite bus: CTRL, HB_N1, HB_N3, BLINK, FAULT_N1/N3"| TX2["N2 CAN_TX_A/B priority queues + recovery"]
  TX2 --> BUS
  D3 --> REL3["N3 RELAY priority 2"]
  REL3 --> TX3["N3 CAN_TX_A/B priority queues + recovery"]
  TX3 --> BUS
  HM1 --> REL1["Pi forwarding in selector loop"]
  REL1 --> BUS
  WD2["N2 supervisor: 100 ms"] --> W2["IWDG"]
  WD3["N3 supervisor: 100 ms"] --> W3["IWDG"]
```

HB_N1 edge is 0x100, 20 ms, D=20 ms. Heartbeat monitor snapshots are shared with
HB_TX. Test/fault task also directly wakes output and blink tasks; its error path
is not delayed until the next HB_MON poll. N1 local test/range faults also emit
0x020 and stop HB_N1. Supervisors read progress from every enabled worker; they do
not send CAN traffic. Recovery shares SPI/controller resources with TX and RX.

Trace these paths in the final timeline figure:

1. Brake/throttle: UDP_RX -> CTRL_TX -> 0x010 -> N3 CAN_RX -> dispatch -> DRIVE -> PWM/DIR.
2. Steering: same to N2 -> STEER -> servo PWM.
3. Blink: UDP_RX -> BLINK_TX -> 0x200 -> each RX/dispatch -> BLINK_F/R -> LED edge.
4. Error: TEST_BTN -> immediate local error/FAULT + stop own HB -> peer HB_MON
   (three missed HB, up to one poll interval) -> DRIVE/brake and hazards.

Label the first three paths with their event-driven response times. The heartbeat
fallback path includes the 60 ms timeout and up to 20 ms polling phase. A real
measured timeline is still required; Mermaid spacing conveys no timing magnitude.


All STM32 local HB/FAULT/FORCE producers enqueue to their CAN_TX_A/B workers;
these workers serialize hardware sends by identifier. The second brake path is
UDP_RX -> CTRL_TX -> A -> N2 RX/dispatch -> RELAY -> TX_B -> B -> N3 RX/dispatch
-> DRIVE. With reversed isolation, swap A and B. All nodes relay; source-ID rejection and bounded dedup suppress returned copies. Dedup suppresses same-bus as well as opposite-bus copies.
