"""Compile production receive-health functions and inject timestamp preemption."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    end = source.index('\n}', start) + 2
    return source[start:end]


@unittest.skipUnless(shutil.which('cc'), 'requires a host C compiler')
class ReceiveAgeTests(unittest.TestCase):
    def test_preemption_wrap_and_real_silence(self):
        source = (ROOT / 'src/lab_can.c').read_text()
        functions = []
        if 'static bool received_within' in source:
            functions.append(function(source, 'static bool received_within'))
        functions += [function(source, 'bool lab_can_link_healthy'),
                      function(source, 'bool lab_can_heard_within')]
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
typedef int32_t atomic_t;
static atomic_t last_rx_ms[2];
static atomic_t heard[2];
static struct { atomic_t started; } buses[2];
#define LAB_LINK_SILENCE_MS 100U
static uint32_t now_ms;
static bool preempt;
static atomic_t atomic_get(const atomic_t *value) { return *value; }
static uint32_t k_uptime_get_32(void)
{
    uint32_t sampled = now_ms;
    if (preempt) {
        /* RX workers run after the clock sample but before its caller resumes. */
        preempt = false;
        now_ms++;
        last_rx_ms[0] = last_rx_ms[1] = (atomic_t)now_ms;
    }
    return sampled;
}
''' + '\n'.join(functions) + r'''
int main(void)
{
    buses[0].started = buses[1].started = 1;
    heard[0] = heard[1] = 1;
    now_ms = 1000;
    last_rx_ms[0] = last_rx_ms[1] = 1000;
    preempt = true;
    assert(lab_can_heard_within(600));
    preempt = true;
    assert(lab_can_link_healthy(0));

    /* Actual silence still trips the original thresholds. */
    now_ms = 1601;
    last_rx_ms[0] = last_rx_ms[1] = 1000;
    assert(!lab_can_heard_within(600));
    assert(!lab_can_link_healthy(0));
    last_rx_ms[1] = 1600;
    assert(lab_can_heard_within(600));
    assert(!lab_can_link_healthy(0));
    assert(lab_can_link_healthy(1));
    assert(!lab_can_link_healthy(-1));
    assert(!lab_can_link_healthy(2));
    buses[1].started = 0;
    assert(!lab_can_link_healthy(1));

    /* Normal uint32 uptime wrap must still produce a short positive age. */
    now_ms = 5;
    last_rx_ms[0] = (atomic_t)(UINT32_MAX - 10);
    heard[1] = 0;
    assert(lab_can_heard_within(20));
    assert(!lab_can_heard_within(10));
    heard[0] = 0;
    assert(!lab_can_heard_within(600));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / 'test.c').write_text(harness)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(work / 'test.c'), '-o', str(work / 'test')],
                           check=True, capture_output=True, text=True)
            result = subprocess.run([str(work / 'test')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == '__main__':
    unittest.main()
