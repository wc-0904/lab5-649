"""Host Python tests; these do not build or flash firmware."""
import importlib.util
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi" / "demo"))
from protocol import CTRL, FAULT, HB1, HB2, HB3, BLINK, FORCE, crc8, payload, valid, decode_wheel, Dedup, CAN_FRAME, CATALOG, origin
from cluster_config import load as load_cluster_config, node_id as cluster_node_id
from raft_agent import RaftAgent
from cockpit import Cockpit, Pins, catalog_filters
from measure_can import frame as measurement_frame


class ProtocolTests(unittest.TestCase):
    def test_socket_filters_receive_catalog_data(self):
        filters = list(struct.iter_unpack("=II", catalog_filters()))
        self.assertEqual([can_id for can_id, _ in filters], list(CATALOG))
        for can_id, mask in filters:
            # Linux routes masks with CAN_ERR_FLAG to its error subscription list.
            self.assertEqual(mask & 0x20000000, 0)
            accepted = [candidate for candidate in range(0x800)
                        if candidate & mask == can_id & mask]
            self.assertEqual(accepted, [can_id])
            for flag in (0x80000000, 0x40000000):
                self.assertNotEqual((can_id | flag) & mask, can_id & mask)

    def test_measurement_stimulus(self):
        for t, brake in ((0, 0), (.99, 0), (1, 100), (2, 0)):
            can_id, length, data = CAN_FRAME.unpack(measurement_frame(17, t))
            self.assertEqual((can_id, length, data[0], data[3], data[4]),
                             (CTRL, 8, 1, 0, brake))
            self.assertTrue(valid(can_id, data))

    def test_crc_standard_vector(self):
        self.assertEqual(crc8(b"123456789"), 0xF4)

    def test_ctrl_ranges_and_crc(self):
        for steering in (-1000, 0, 1000):
            frame = payload(CTRL, 19, struct.pack("<hBBBB", steering, 100, 0, 0, 0))
            self.assertEqual(frame[0], 3)
            self.assertTrue(valid(CTRL, frame))
        for steering, throttle in ((1001, 0), (0, 101)):
            self.assertFalse(valid(CTRL, payload(CTRL, 0,
                                               struct.pack("<hBBBB", steering, throttle, 0, 0, 0))))
        bad = bytearray(frame)
        bad[4] ^= 1
        self.assertFalse(valid(CTRL, bad))

    def test_reserved_and_flags(self):
        self.assertTrue(valid(BLINK, payload(BLINK, 1, b"\x03\x01")))
        self.assertFalse(valid(BLINK, payload(BLINK, 1, b"\x04\x01")))
        bad = bytearray(payload(FORCE, 0, b"\xff"))
        bad[0] = 16
        self.assertFalse(valid(FORCE, bad))
        bad[0] = 0
        bad[7] = 1
        self.assertFalse(valid(FORCE, bad))

    def test_dedup_wrap_restart_and_source(self):
        dedup = Dedup()
        data = payload(BLINK, 1, b"\x01\x01")
        self.assertFalse(dedup.duplicate(BLINK, data, 0, 0))
        self.assertTrue(dedup.duplicate(BLINK, data, 1, .001))
        self.assertTrue(dedup.duplicate(BLINK, data, 0, .002))
        self.assertFalse(dedup.duplicate(BLINK, data, 1, .320))
        self.assertFalse(dedup.duplicate(FAULT, payload(FAULT, 1, b"\x02\x01"), 0, 1))
        self.assertFalse(dedup.duplicate(FAULT, payload(FAULT, 1, b"\x03\x01"), 1, 1.001))

    def test_wheel_endpoints(self):
        packet = bytearray(276)
        struct.pack_into("<Iii", packet, 0, 17, -32768, -32768)
        struct.pack_into("<i", packet, 24, 32767)
        packet[55] = 128
        self.assertEqual(decode_wheel(packet), (17, -1000, 100, 0, 8))
        with self.assertRaises(ValueError):
            decode_wheel(packet[:-1])
        struct.pack_into("<i", packet, 4, 99999)
        with self.assertRaises(ValueError):
            decode_wheel(packet)


    def test_shared_fault_source(self):
        self.assertEqual(len(CATALOG), 7)
        for zone in (1, 2, 3):
            data = payload(FAULT, 1, bytes((zone, 1)))
            self.assertTrue(valid(FAULT, data))
            self.assertEqual(origin(FAULT, data), zone)
        for zone in (0, 4):
            self.assertFalse(valid(FAULT, payload(FAULT, 1, bytes((zone, 1)))))
        self.assertFalse(valid(0x21, payload(FAULT, 1, b"\x02\x01")))

    def test_same_bus_relay_copy_not_applied_twice(self):
        dedup = Dedup()
        data = payload(CTRL, 4, struct.pack("<hBBBB", 123, 50, 0, 0, 0))
        self.assertFalse(dedup.duplicate(CTRL, data, 1, 1.0))
        self.assertTrue(dedup.duplicate(CTRL, data, 1, 1.001))
        self.assertTrue(dedup.duplicate(CTRL, data, 0, 1.002))
        # The suppressed copies do not extend the window indefinitely.
        self.assertFalse(dedup.duplicate(CTRL, data, 1, 1.201))

    def test_all_connected_link_topologies(self):
        # Exercise the actual Python dedup/origin rules; STM32 scheduling is not simulated.
        from collections import deque
        from itertools import product
        for combination in product(({0}, {1}, {0, 1}), repeat=3):
            for can_id in CATALOG:
                source = origin(can_id, payload(can_id, 1, b"\x01\x01")) - 1
                links = list(combination)
                dedup = [Dedup(), Dedup(), Dedup()]
                body = (struct.pack("<hBBBB", 0, 25, 0, 0, 0) if can_id == CTRL else
                        bytes((source + 1, 1)) if can_id == FAULT else
                        b"\0\0")
                data = payload(can_id, 1, body)
                queue = deque((source, bus) for bus in links[source])
                accepted = [0, 0, 0]
                forwarded = [0, 0, 0]
                steps = 0
                while queue:
                    sender, bus = queue.popleft()
                    steps += 1
                    self.assertLessEqual(steps, 4, "more than original two + two forwards")
                    for receiver in range(3):
                        if receiver == sender or bus not in links[receiver] or receiver == source:
                            continue
                        if dedup[receiver].duplicate(can_id, data, bus, 1 + steps * .0001):
                            continue
                        accepted[receiver] += 1
                        forwarded[receiver] += 1
                        if 1 - bus in links[receiver]:
                            queue.append((receiver, 1 - bus))
                reachable = {source}
                for _ in range(3):
                    reachable |= {n for n in range(3)
                                  if any(links[n] & links[r] for r in reachable)}
                for node in range(3):
                    self.assertEqual(accepted[node], int(node in reachable and node != source),
                                     (combination, can_id, node))
                    self.assertLessEqual(forwarded[node], 1)

    def test_wrap_and_delayed_loop_copy(self):
        dedup = Dedup()
        data = payload(HB3, 0, b"\0\3")
        self.assertFalse(dedup.duplicate(HB3, data, 0, 1))
        self.assertTrue(dedup.duplicate(HB3, data, 1, 1.150))
        self.assertFalse(dedup.duplicate(HB3, data, 1, 1.320))


class IndicatorTests(unittest.TestCase):
    def test_leader_and_sync_light_on_the_pi_and_track_the_role(self):
        app = Cockpit.__new__(Cockpit)
        app.pins = Pins(disabled=True)
        app.cluster = load_cluster_config()
        app.agent = RaftAgent(app.cluster, cluster_node_id(app.cluster, "cockpit"),
                              lambda can_id, data: None)
        app.show_raft_state()
        # Checkoff step 1: LEADER on the Pi only, SYNC on all three.
        self.assertTrue(app.pins.level[Pins.LEADER])
        self.assertTrue(app.pins.level[Pins.SYNC])
        # A follower drives neither; the Nucleos keep LEADER dark.
        app.agent = RaftAgent(app.cluster, cluster_node_id(app.cluster, "steering"),
                              lambda can_id, data: None)
        app.show_raft_state()
        self.assertFalse(app.pins.level[Pins.LEADER])
        self.assertFalse(app.pins.level[Pins.SYNC])


class StateTests(unittest.TestCase):
    def cockpit(self):
        app = Cockpit.__new__(Cockpit)
        app.pins = Mock()
        # monitor() drives the Lab 5 LEADER and SYNC indicators from the agent.
        app.cluster = load_cluster_config()
        app.agent = RaftAgent(app.cluster, cluster_node_id(app.cluster, "cockpit"),
                              lambda can_id, data: None)
        app.heartbeat = [[float("-inf")] * 2 for _ in range(3)]
        app.fault_at = [float("-inf")] * 3
        app.local_fail = False
        app.last_udp = 1
        app.dedup = Dedup()
        app.bad_frames = 0
        app.remote = None
        app.forwarded = [0, 0]
        app.send_errors = [0, 0]
        app.can = [Mock(), Mock()]
        app.recovery_press = None
        app.last_fault = float("-inf")
        app.sequence = {can_id: 0 for can_id in CATALOG}
        return app

    def test_fail_once_and_double_press_recover(self):
        app = self.cockpit()
        app.send = Mock()
        app.test_press(1)
        self.assertTrue(app.local_fail)
        app.fault(2, 1.1)
        app.send.assert_called_once_with(FAULT, bytes((1, 1)))
        app.test_press(2)
        app.test_press(2.501)  # Too late; starts another pair.
        self.assertTrue(app.local_fail)
        app.test_press(2.9)
        self.assertFalse(app.local_fail)
        app.monitor(2.901)  # No fresh peers/input: still in error.
        self.assertTrue(app.error)
        app.test_press(3)
        self.assertTrue(app.local_fail)
        self.assertEqual(app.send.call_count, 2)

    def test_cmd_marker_after_first_write_before_second(self):
        app = self.cockpit()
        app.pending_command = (struct.pack("<hBBBB", 0, 0, 0, 0, 0), 0)
        app.last_ctrl_tx = float("-inf")
        app.samples = []
        events = []
        app.can[0].send.side_effect = lambda _: events.append("A")
        app.can[1].send.side_effect = lambda _: events.append("B")
        app.pins.toggle.side_effect = lambda p: events.append(p)
        app.ctrl_tx(1)
        self.assertEqual(events, [2, "A", 1, 3, "B"])
        self.assertEqual(app.samples[0][2], 1)

    def test_foreign_fault_and_returned_own_fault(self):
        app = self.cockpit()
        for zone in (2, 3):
            app.can[0].recv.return_value = CAN_FRAME.pack(
                FAULT, 8, payload(FAULT, 1, bytes((zone, 1))))
            app.receive(0, 1)
            self.assertEqual(app.fault_at[zone - 1], 1)
        app.can[1].send.reset_mock()
        app.can[0].recv.return_value = CAN_FRAME.pack(
            FAULT, 8, payload(FAULT, 1, b"\x01\x01"))
        app.receive(0, 1.1)
        app.can[1].send.assert_not_called()

    def test_startup_one_bus_and_zone_loss(self):
        app = self.cockpit()
        app.monitor(1)
        self.assertTrue(app.error)
        app.heartbeat[1][0] = app.heartbeat[2][0] = 1
        app.monitor(1.001)
        self.assertFalse(app.error)
        self.assertEqual(app.degraded, [False, True])
        app.last_udp = 1.07
        app.monitor(1.07)
        self.assertTrue(app.error)

    def test_duplicate_heartbeat_updates_both_buses(self):
        app = self.cockpit()
        frame = CAN_FRAME.pack(HB2, 8, payload(HB2, 1, bytes((0, 3))))
        app.can[0].recv.return_value = frame
        app.can[1].recv.return_value = frame
        app.receive(0, 1)
        app.receive(1, 1.001)
        self.assertEqual(app.heartbeat[1], [1, 1.001])

    def test_pi_forwards_unchanged_once(self):
        app = self.cockpit()
        frame = CAN_FRAME.pack(HB2, 8, payload(HB2, 7, b"\0\3"))
        app.can[0].recv.return_value = frame
        app.can[1].recv.return_value = frame
        app.receive(0, 1)
        app.receive(1, 1.001)
        app.can[1].send.assert_called_once_with(frame)
        app.can[0].send.assert_not_called()
        self.assertEqual(app.forwarded, [0, 1])

    def test_pi_drops_returned_own_frame(self):
        app = self.cockpit()
        frame = CAN_FRAME.pack(HB1, 8, payload(HB1, 7, b"\0\3"))
        app.can[0].recv.return_value = frame
        app.receive(0, 1)
        app.can[1].send.assert_not_called()

    def test_locally_failed_pi_does_not_bridge(self):
        app = self.cockpit()
        app.local_fail = True
        app.can[0].recv.return_value = CAN_FRAME.pack(HB2, 8, payload(HB2, 7, b"\0\3"))
        app.receive(0, 1)
        app.can[1].send.assert_not_called()

    def test_fault_needs_new_heartbeat(self):
        app = self.cockpit()
        app.heartbeat[1][0] = app.heartbeat[2][0] = 1
        app.fault_at[1] = 1.001
        app.monitor(1.002)
        self.assertTrue(app.error)
        app.heartbeat[1][0] = 1.003
        app.monitor(1.004)
        self.assertFalse(app.error)

    def test_send_failure_does_not_skip_other_bus(self):
        app = self.cockpit()
        import errno
        app.sequence = {CTRL: 0}
        app.send_errors = [0, 0]
        app.can[0].send.side_effect = OSError(errno.ENETDOWN, "down")
        self.assertEqual(app.send(CTRL, struct.pack("<hBBBB", 0, 0, 0, 0, 0)), 1)
        app.can[1].send.assert_called_once()
        self.assertEqual(app.send_errors, [1, 0])


if __name__ == "__main__":
    unittest.main()
