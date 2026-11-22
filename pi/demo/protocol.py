"""Lab 3 catalog. All multi-byte fields are little endian; no real motor feedback."""
import struct

CTRL, FAULT, HB1, HB2, HB3, BLINK, FORCE = 0x10, 0x20, 0x100, 0x101, 0x102, 0x200, 0x300
FAULT_IDS = (FAULT,)
CATALOG = (CTRL, *FAULT_IDS, HB1, HB2, HB3, BLINK, FORCE)
CAN_FRAME = struct.Struct("=IB3x8s")

# Lab 5 identifiers; these must stay equal to enum lab5_id in
# include/lab_protocol.h, which tests/test_demo.py checks.
REF = 0x008
APPEND = 0x040
APPEND_ACK_BASE = 0x044
REQ_VOTE_BASE = 0x048
VOTE_BASE = 0x04c
CLIENT_REQ_BASE = 0x050
CLIENT_REDIRECT = 0x054

#: One masked filter accepting the whole consensus range, matching enum lab5_filter.
LAB5_FILTER_ID = 0x040
LAB5_FILTER_MASK = 0x7e0

#: Bases that take a node ID added to them, because one producer owns each ID.
LAB5_PER_NODE_BASES = (APPEND_ACK_BASE, REQ_VOTE_BASE, VOTE_BASE, CLIENT_REQ_BASE)


def lab5_id_for(base, node):
    """Build the per-node Lab 5 identifier, mirroring lab5_id_for() in C.

    :param base: One of the LAB5 base identifiers.
    :param node: Numeric node ID, 1 to 3.
    :return: Identifier owned by that node, or the base when the node is invalid.
    """
    if node < 1 or node > 3:
        return base
    return base + node


#: Identifier bit marking a frame as a relayed copy. Must match
#: LAB5_RELAY_FLAG in include/lab_protocol.h. A high bit, so a relayed copy
#: sorts below every original in arbitration: it is a second chance at
#: something and must never delay first-hand traffic.
LAB5_RELAY_FLAG = 0x400


def is_relayed(can_id):
    """Report whether a frame is a relayed copy.

    :param can_id: CAN identifier as received.
    :return: True when the relay flag is set.
    """
    return bool(can_id & LAB5_RELAY_FLAG)


def relay_id(can_id):
    """Identifier a relayed copy travels under.

    :param can_id: Original identifier.
    :return: Identifier with the relay flag set.
    """
    return can_id | LAB5_RELAY_FLAG


def origin_id(can_id):
    """Original identifier of a possibly relayed frame.

    :param can_id: CAN identifier as received.
    :return: Identifier with the relay flag cleared.
    """
    return can_id & ~LAB5_RELAY_FLAG


def is_consensus(can_id):
    """Report whether an identifier falls in the masked consensus range.

    :param can_id: CAN identifier.
    :return: True for consensus traffic, which excludes REF.
    """
    return can_id & LAB5_FILTER_MASK == LAB5_FILTER_ID


def crc8(data):
    """CRC-8/ATM, polynomial 0x07, init zero, no reflection/xor."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (7 if crc & 128 else 0)) & 255
    return crc


def payload(can_id, sequence, body=b""):
    """Construct catalog payload; CTRL body includes reserved byte six."""
    data = bytearray(8)
    data[0] = sequence & 15
    if len(body) > 7:
        raise ValueError("payload too long")
    data[1:1 + len(body)] = body
    if can_id == CTRL:
        data[7] = crc8(data[:7])
    return bytes(data)


def valid(can_id, data):
    """Match firmware validation, including reserved bytes and pedal limits."""
    if can_id not in CATALOG or len(data) != 8 or data[0] & 240:
        return False
    if can_id == CTRL:
        steer = struct.unpack_from("<h", data, 1)[0]
        return (-1000 <= steer <= 1000 and data[3] <= 100 and data[4] <= 100
                and data[6] == 0 and data[7] == crc8(data[:7]))
    if can_id in FAULT_IDS:
        return 1 <= data[1] <= 3 and 1 <= data[2] <= 4 and not any(data[3:])
    if can_id in (HB1, HB2, HB3):
        return data[1] <= 1 and data[2] <= 3 and not any(data[3:])
    if can_id == BLINK:
        return data[1] <= 3 and data[2] <= 1 and not any(data[3:])
    return not any(data[2:])


def decode_wheel(data):
    """Decode Lab 2's 276-byte counter + DIJOYSTATE2 datagram.

    Axes are signed -32768..32767. Pedals decrease when pressed.
    Buttons retain indices 0..7; button 3 is self-test, 4 right, 5 left.
    """
    if len(data) != 276:
        raise ValueError("expected 276-byte wheel datagram")
    sequence = struct.unpack_from("<I", data)[0]
    steer, throttle = struct.unpack_from("<ii", data, 4)
    brake = struct.unpack_from("<i", data, 24)[0]
    if any(not -32768 <= axis <= 32767 for axis in (steer, throttle, brake)):
        raise ValueError("wheel axis out of range")
    buttons = sum(1 << i for i in range(8) if data[52 + i] & 128)
    steer = round(steer * 1000 / (32768 if steer < 0 else 32767))
    throttle = (32767 - throttle) * 100 // 65535
    brake = (32767 - brake) * 100 // 65535
    return sequence, steer, throttle, brake, buttons


class Dedup:
    """Direct and relayed (ID, seq, payload) suppression bounded to 200 ms.

    Same-bus repeats inside this window are also suppressed. A four-bit
    sequence cannot distinguish arbitrary delayed traffic across reboot/wrap.
    """
    def __init__(self):
        self.cache = {}

    def duplicate(self, can_id, data, bus, now):
        """Return true only for a matching recent copy on either bus."""
        origin = data[1] if can_id in FAULT_IDS else 0
        key = can_id, origin, data[0] & 15
        old = self.cache.get(key)
        if old and now - old[1] < .200 and old[2] == data:
            return True
        self.cache[key] = bus, now, data
        return False


def origin(can_id, data):
    """Return the original producer, irrespective of ingress or forwarding node."""
    if can_id in FAULT_IDS:
        return data[1]
    if can_id in (HB1, HB2, HB3):
        return can_id - HB1 + 1
    if can_id in (CTRL, BLINK):
        return 1
    return 2 if can_id == FORCE else 0
