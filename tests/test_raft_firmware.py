"""Run the actual firmware Raft agent on the host with transport/kernel stubs."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from gen_config import emit, load

KERNEL = '''
#ifndef TEST_KERNEL_H
#define TEST_KERNEL_H
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
typedef int k_timeout_t;
typedef int k_spinlock_key_t;
struct k_spinlock { int unused; };
static inline int k_spin_lock(struct k_spinlock *l) { (void)l; return 0; }
static inline void k_spin_unlock(struct k_spinlock *l, int k) { (void)l; (void)k; }
static inline int64_t k_uptime_get(void) { return 1000; }
#endif
'''
HARNESS = r'''
#include <assert.h>
#include <string.h>
#include "raft_agent.c"
static int transmissions;
static uint16_t sent_id;
static uint8_t sent[8];
void lab_can_send_lab5(uint16_t id, const uint8_t data[8])
{ transmissions++; sent_id = id; memcpy(sent, data, 8); }
bool lab_can_heard_within(uint32_t ms) { (void)ms; return true; }
bool lab_can_link_healthy(int bus) { (void)bus; return true; }
void lab_can_link_state(int b, int *s, int *t, int *r)
{ (void)b; *s = *t = *r = 0; }
void lab_io_set(enum lab_pin p, bool v) { (void)p; (void)v; }
void lab_io_toggle(enum lab_pin p) { (void)p; }
uint32_t pps_now(void) { return 1000000; }
static void reset(void)
{
    memset(&state, 0, sizeof(state));
    transmissions = 0;
    raft_agent_init();
    time_sync_reset();
}
int main(void)
{
    uint8_t data[8];
    reset();
    state.term = 2;
    raft_log_append(&state.log, 2, RAFT_COCKPIT_OK);
    struct lab5_ack ack = {.term=1, .match_index=1, .result=0};
    lab5_ack_encode(data, &ack);
    raft_on_ack(data, 3);
    assert(state.commit_index == 0 && state.match_index[3] == 0);
    ack.term = 2;
    ack.match_index = 100;
    lab5_ack_encode(data, &ack);
    raft_on_ack(data, 3);
    assert(state.commit_index == 0);

    reset();
    state.role = RAFT_FOLLOWER;
    raft_log_append(&state.log, 1, raft_event_node(1, true));
    raft_log_append(&state.log, 1, raft_event_node(3, true));
    struct lab5_append append = {.term=2, .prev_index=1, .prev_term=1, .commit=2};
    lab5_append_encode(data, &append);
    raft_on_append(data);
    assert(state.commit_index == 1 && !network_node_up(&state.net, 3));

    reset();
    struct lab5_client req = {.event=RAFT_COCKPIT_ERROR, .request=7};
    lab5_client_encode(data, &req);
    raft_on_client_request(data, 3);
    raft_on_client_request(data, 3);
    assert(transmissions == 0 && raft_log_last_index(&state.log) == 1);
    ack = (struct lab5_ack){.term=1, .match_index=1, .result=0};
    lab5_ack_encode(data, &ack);
    raft_on_ack(data, 1);
    assert(state.commit_index == 1);
    lab5_client_encode(data, &req);
    raft_on_client_request(data, 3);
    assert(transmissions == 1 && sent_id == LAB5_CLIENT_REDIRECT + LAB_NODE_ID);
    assert(lab5_reply_decode(sent).index == 1);

    reset();
    for (int n=1; n<=3; n++) { raft_log_append(&state.log, 1, raft_event_node(n, true)); }
    state.match_index[3] = 3;
    state.next_index[3] = 4;
    ack = (struct lab5_ack){.term=1, .match_index=0, .result=RAFT_APPEND_MISSING};
    lab5_ack_encode(data, &ack);
    raft_on_ack(data, 3);
    ack.match_index=1; ack.result=RAFT_APPEND_ACCEPTED;
    lab5_ack_encode(data, &ack);
    raft_on_ack(data, 3);
    assert(state.next_index[3] == 2);

    reset();
    time_sync_assume_master(1000000, 1000);
    assert(time_sync_global_us(900) == 999900);
    for (uint64_t t=1000000; t<5000000000ULL; t+=1000000) {
        time_sync_holdover((uint32_t)(1000+t));
        assert(time_sync_global_us((uint32_t)(1000+t)) == 1000000+t);
    }
    raft_agent_suspend();
    assert(raft_role() == RAFT_FOLLOWER && raft_term() == 1);
    return 0;
}
'''

@unittest.skipUnless(shutil.which('cc'), 'requires host C compiler')
class FirmwareRaftTests(unittest.TestCase):
    def test_actual_firmware_state_transitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work/'zephyr/drivers').mkdir(parents=True)
            (work/'zephyr/sys').mkdir()
            (work/'zephyr/kernel.h').write_text(KERNEL)
            (work/'zephyr/drivers/can.h').write_text(
                '#include <zephyr/kernel.h>\nstruct can_frame { uint32_t id; uint8_t dlc; uint8_t data[8]; };\n')
            (work/'zephyr/sys/printk.h').write_text('#include <stdio.h>\n#define printk printf\n')
            config = dict(load(), Debug=False, default_leader_id=2)
            (work/'cluster_config.h').write_text(emit(config, 'test'))
            (work/'harness.c').write_text(HARNESS)
            sources = ['raft_log.c', 'network_state.c', 'election.c', 'lab_protocol.c', 'time_sync.c']
            subprocess.run(['cc', '-std=c11', '-DLAB_NODE_ID=2', '-I'+str(work),
                            '-I'+str(ROOT/'include'), '-I'+str(ROOT/'src'),
                            str(work/'harness.c'), *[str(ROOT/'src'/s) for s in sources],
                            '-o', str(work/'harness')], check=True, capture_output=True, text=True)
            subprocess.run([str(work/'harness')], check=True, capture_output=True, text=True)

if __name__ == '__main__':
    unittest.main()
