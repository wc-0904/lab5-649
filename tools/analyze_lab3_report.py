#!/usr/bin/env python3
"""Clarification RTA in microseconds; measured inputs plus explicit burst envelopes."""
import json
import math


def analyze(node, copies=1, relay=False):
    rx_c = 45 if relay else (80 if node == 2 else 206)
    tasks = [('CAN_RX', 1, rx_c, 7 * copies, 20000, 300)]
    if relay:
        tasks.append(('RELAY', 2, 213, 5, 20000, None))
    if node == 2:
        tasks += [('BLINK_F', 3, 20, 1, 20000, 500),
                  ('STEER', 4, 50, 1, 20000, 1000),
                  ('TEST_BTN', 5, 20, 1, 50000, 10000),
                  ('HB_TX', 6, 120, 1, 20000, 20000),
                  ('HB_MON', 7, 30, 1, 20000, 20000),
                  ('FORCE_TX', 8, 120, 1, 50000, 50000)]
    else:
        tasks += [('DRIVE', 3, 47, 1, 20000, 500),
                  ('BLINK_R', 4, 20, 1, 20000, 500),
                  ('TEST_BTN', 5, 20, 1, 50000, 10000),
                  ('PID', 6, 200, 1, 10000, 10000),
                  ('HB_TX', 7, 120, 1, 20000, 20000),
                  ('HB_MON', 8, 30, 1, 20000, 20000)]
    result = []
    for index, (name, priority, c, n, period, deadline) in enumerate(tasks):
        b = 0 if index == len(tasks) - 1 else 84
        ceff = c*n
        r = ceff+b
        sequence = [r]
        for _ in range(100):
            interference = sum(math.ceil(r/j['T'])*j['Ceff'] for j in result)
            new = ceff+b+interference
            sequence.append(new)
            if new == r:
                break
            r = new
        else:
            raise RuntimeError('nonconvergence')
        assert r == ceff+b+sum(math.ceil(r/j['T'])*j['Ceff'] for j in result)
        result.append(dict(name=name, p=priority, C=c, n=n, Ceff=ceff, T=period,
                           D=deadline, B=b, R=r, iterations=sequence,
                           hp=sum(j['Ceff'] for j in result), meets=None if deadline is None else r <= deadline))
    return result


def main():
    models = dict(n2=analyze(2), n3=analyze(3), n2_dual=analyze(2, 2),
                  n3_dual=analyze(3, 2), n2_relay=analyze(2, 2, True))
    lookup = {key: {r['name']:r['R'] for r in rows} for key, rows in models.items()}
    a,b,c=lookup['n2'],lookup['n3'],lookup['n2_relay']
    chain={}
    for p in [1180,2000]:
        chain[str(p)] = {
            'single_brake': p+540+b['CAN_RX']+b['DRIVE'],
            'single_steer': p+540+a['CAN_RX']+a['STEER']+20000,
            'single_blink': 20000+p+1890+max(a['CAN_RX']+a['BLINK_F'],b['CAN_RX']+b['BLINK_R']),
            'single_skew': max(a['CAN_RX']+a['BLINK_F'],b['CAN_RX']+b['BLINK_R']),
            'single_error': max(a['TEST_BTN'],b['TEST_BTN'])+66000+20000+max(a['HB_MON'],b['HB_MON']),
            'relay_j':p+540+c['CAN_RX']+c['RELAY'],
            'relay_ctrl_r':p+540+c['CAN_RX']+c['RELAY']+540,
            'relay_brake':p+540+c['CAN_RX']+c['RELAY']+540+b['CAN_RX']+b['DRIVE'],
            'relay_steer':p+540+c['CAN_RX']+c['STEER']+20000,
            'relay_blink':20000+p+1890+c['CAN_RX']+c['RELAY']+1890+b['CAN_RX']+b['BLINK_R'],
            'relay_skew':max(c['CAN_RX']+c['BLINK_F'],c['CAN_RX']+c['RELAY']+1890+b['CAN_RX']+b['BLINK_R']),
            'relay_error':max(c['TEST_BTN'],b['TEST_BTN'])+66000+20000+max(c['HB_MON'],b['HB_MON']),
        }
    result=dict(models=models, chains=chain,
                utilization=dict(base=6*270/20000+270/50000,
                                 scenario_A=6*270/20000+270/50000+2*270/20000,
                                 scenario_B=6*270/20000+270/50000+4*270/20000,
                                 all_connected_envelope=3*(6*270/20000+270/50000)))
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
