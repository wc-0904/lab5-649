/* SPDX-License-Identifier: Apache-2.0 */
/** @file lab_protocol.h
 * @brief Shared catalog and pure payload validation.
 */
#ifndef LAB_PROTOCOL_H_
#define LAB_PROTOCOL_H_
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
/** @brief Seven Lab 3 template IDs; FAULT carries its originating zone in byte 1. */
enum lab_id { LAB_CTRL = 0x010, LAB_FAULT = 0x020, LAB_HB1 = 0x100,
	LAB_HB2 = 0x101, LAB_HB3 = 0x102, LAB_BLINK = 0x200, LAB_FORCE = 0x300 };
/** @brief Numeric node IDs; the index into the configured nodes list plus one. */
enum lab_node { LAB_NODE_COCKPIT = 1, LAB_NODE_STEERING = 2, LAB_NODE_DRIVETRAIN = 3 };
/** @brief Lab 5 consensus and time identifiers.
 *
 * REF sits below LAB_CTRL so the clock reference wins arbitration against every
 * other frame, including APPEND, as Part 6 requires. Everything else sits above
 * LAB_FAULT so safety traffic still outranks consensus. The four per-node bases
 * are added to a node ID from enum lab_node, because lab_can_send() numbers a
 * sequence per identifier and so allows only one producer per ID. Each base is
 * four-aligned and base + 0 stays unused, so node slots never run into the next
 * base. Transmitted IDs are therefore 0x040, 0x045..0x047, 0x049..0x04b,
 * 0x04d..0x04f, 0x051..0x053 and 0x054, all inside the masked filter range.
 */
enum lab5_id { LAB5_REF = 0x008, LAB5_APPEND = 0x040, LAB5_APPEND_ACK_BASE = 0x044,
	LAB5_REQ_VOTE_BASE = 0x048, LAB5_VOTE_BASE = 0x04c,
	LAB5_CLIENT_REQ_BASE = 0x050, LAB5_CLIENT_REDIRECT = 0x054 };
/* CLIENT_REDIRECT is a per-sender base: replies use 0x055..0x057.
 * Followers and leader can reply together, so they must arbitrate on unique IDs.
 */
/** @brief Masked filter covering every consensus ID in one RX filter slot.
 *
 * The MCP2515 driver offers 16 software filter slots and Lab 3 already uses
 * seven, so the eleven consensus identifiers are accepted as the single range
 * 0x040..0x05f instead of one slot each.
 */
enum lab5_filter { LAB5_FILTER_ID = 0x040, LAB5_FILTER_MASK = 0x7e0 };
/** @brief Identifier bit marking a frame as a relayed copy.
 *
 * The relay flag lives in the identifier rather than the payload, for three
 * reasons. Every Lab 5 payload is already full to eight bytes. A flag in the
 * identifier is visible in a `candump` without decoding anything, which the
 * checkoff reads directly. And it costs nothing to filter on.
 *
 * It is a high bit, so a relayed copy sorts **below** every original frame in
 * arbitration. That is the right priority: a relayed copy is by definition a
 * second chance at something, and it must never delay first-hand traffic. Lab
 * 3's catalog tops out at 0x300, so 0x400 and above is free.
 *
 * A node never relays a frame that already carries this bit, which is the whole
 * loop prevention: exactly one hop, and with three nodes one hop always
 * suffices (TASKS.md D24).
 */
#define LAB5_RELAY_FLAG 0x400U
/** @brief Shift of the relaying node's ID within a relayed identifier.
 *
 * Two nodes can hold both links at once, and then both relay. If every copy
 * travelled under one shared identifier they would collide destructively: CAN
 * arbitrates on the identifier alone, so two frames sharing an ID and
 * differing in the data field leave the node sending recessive while reading
 * dominant with a bit error, and the transmit counter rises by eight for both.
 * Their queues are offset by a few hundred microseconds, so they relay
 * different frames at the same moment routinely.
 *
 * On the bench this appeared the moment the cockpit came off bus B: a frame
 * originating on bus A was then relayed onto B by **both** Nucleos, while a
 * frame originating on B had only one relayer left for A. Errors appeared on
 * bus B and never on A, with the transmit counter reaching 128 and the receive
 * counter staying at zero, which is the signature of a node losing a bit
 * contest during its own transmission.
 *
 * Carrying the relayer in the identifier makes the two copies arbitrate
 * normally instead: one wins, the other defers and retries.
 */
#define LAB5_RELAY_NODE_SHIFT 8U
/** @brief Mask of the relaying node's ID within a relayed identifier. */
#define LAB5_RELAY_NODE_MASK 0x3U
/** @brief Report whether a frame is a relayed copy.
 * @param id CAN identifier as received.
 * @return True when the relay flag is set.
 */
static inline bool lab5_is_relayed(uint32_t id)
{
	return (id & LAB5_RELAY_FLAG) != 0;
}
/** @brief Identifier a relayed copy of a frame travels under.
 * @param id Original identifier.
 * @param node Node ID doing the relaying.
 * @return Identifier carrying the relay flag and the relayer.
 */
static inline uint32_t lab5_relay_id(uint32_t id, int node)
{
	return LAB5_RELAY_FLAG |
		((uint32_t)((node - 1) & LAB5_RELAY_NODE_MASK) <<
		 LAB5_RELAY_NODE_SHIFT) | id;
}
/** @brief Original identifier of a possibly relayed frame.
 * @param id CAN identifier as received.
 * @return Identifier with the relay flag and relayer cleared.
 */
static inline uint32_t lab5_origin_id(uint32_t id)
{
	return id & ~(LAB5_RELAY_FLAG |
		      ((uint32_t)LAB5_RELAY_NODE_MASK << LAB5_RELAY_NODE_SHIFT));
}
/** @brief Node that relayed a frame.
 * @param id CAN identifier as received.
 * @return Relaying node ID, meaningless when the frame is not relayed.
 */
static inline int lab5_relay_node(uint32_t id)
{
	return (int)((id >> LAB5_RELAY_NODE_SHIFT) & LAB5_RELAY_NODE_MASK) + 1;
}
/** @brief Decoded REF payload: the master's clock, cycle counter and identity.
 *
 * Wire layout, little endian: bytes 0..5 are the master clock in microseconds
 * **since LAB5_EPOCH_US** as a 48 bit unsigned value, bytes 6 and the low six
 * bits of byte 7 are a 14 bit cycle counter, and the top two bits of byte 7
 * carry the master's node ID minus one.
 *
 * The epoch offset matters: a CLOCK_REALTIME value in 2026 is about 1.8e15
 * microseconds, which needs 51 bits and would be silently truncated to a 1973
 * date. Measuring from a fixed recent epoch keeps microsecond resolution, the
 * unit the PPS spread is reported in.
 *
 * The identity matters because REF and APPEND share one identifier each and
 * neither otherwise names its sender. With a pre-defined leader that is
 * harmless, but `default_leader=random` boots every node with no leader known,
 * and two behaviours key off who the master is: the one way delay constant,
 * which belongs to the master's transmit path rather than to the bus, and the
 * hazard rate, which is 2 Hz while the cockpit leads and 4 Hz once an STM32
 * does. Checkoff steps 6 and 8 read both.
 *
 * The counter gave up the two bits because it has them to spare: at 14 bits it
 * still wraps only every 16384 heartbeats, five and a half minutes, which is
 * far longer than any gap it is meant to detect.
 */
struct lab5_ref { uint64_t master_us; uint16_t cycle; int master; };
/** @brief Bits of the REF payload given to the cycle counter. */
#define LAB5_REF_CYCLE_BITS 14
/** @brief Largest cycle value the REF payload can carry. */
#define LAB5_REF_CYCLE_MAX ((1U << LAB5_REF_CYCLE_BITS) - 1U)
/** @brief Decoded AppendEntries header.
 *
 * Wire layout, little endian: byte 0 the leader's term, bytes 1..2 prevLogIndex
 * in the low fifteen bits with the entry count in bit fifteen, byte 3
 * prevLogTerm, bytes 4..5 leaderCommit in the low fifteen bits, byte 6 the
 * **carried entry's own term**, byte 7 its event. Fifteen bits of index covers
 * the configured log_size of 30000, and the count is one bit because an append
 * carries at most one entry.
 *
 * The entry's term is not the leader's term and the two must not be conflated.
 * This originally carried only the event and left a follower to stamp what it
 * stored with the header's term, which is correct exactly as long as the
 * cluster never changes term: an entry written under an earlier leader would be
 * recorded under the current one, so the next consistency check on that index
 * compares the leader's real prevLogTerm against the follower's rewritten one,
 * fails, truncates, refills and fails again. On the bench that deadlocked both
 * Nucleos into storing index 1 for ever and emptied the committed graph. One
 * entry needs its own term on the wire; the bits came from the two index fields
 * and the count, which were wider than they ever needed to be.
 */
struct lab5_append { uint8_t term; uint16_t prev_index; uint8_t prev_term;
	uint16_t commit; uint8_t count; uint8_t entry_term; uint8_t event; };
/** @brief Largest log index the AppendEntries header can carry. */
#define LAB5_APPEND_INDEX_MAX 0x7fffU
/** @brief Zero point of the REF clock field: 2026-01-01T00:00:00Z in microseconds.
 *
 * The 48 bit field spans about 8.9 years from here, so it covers the course and
 * then some. Both endpoints must use this same constant.
 */
/** @brief One way delay from the master stamping L to a node capturing F.
 *
 * Indexed by the master's node ID, because this delay belongs to the master's
 * transmit path and not to the bus. Measured on the bench with the Pi leading:
 * differencing the scope gap between the Pi's PPS edge and a Nucleo's against
 * the Pi's own self reported scheduling lateness gives about 658 microseconds,
 * of which only 256 is time on the wire. The rest is Python, the socket, the
 * SocketCAN queue, the mcp251x workqueue and a 4 MHz SPI transaction standing
 * between the timestamp and the first bit transmitted.
 *
 * The STM32 figure is not measured yet and is a calculation: 256 microseconds
 * on the wire, a transmit worker handoff, and two SPI transactions. P9 measures
 * it the same way, and more easily, since a master applies no delay to its own
 * edge, so the gap to a follower is the error directly.
 */
enum lab5_delay {
	LAB5_DELAY_COCKPIT_US = 658,
	LAB5_DELAY_STM32_US = 400,
};
/** @brief Extra one way delay added when a reference arrived by relay.
 *
 * Checkoff steps 5 and 9 pull two links so a node is reachable only through a
 * third one, which means its REF now arrives as a relayed copy: queued by the
 * bridging node, picked up by a separate thread, and retransmitted on the other
 * bus. That adds real transit time the per-master constants above were never
 * calibrated to include, because they were measured from direct reception.
 *
 * Measured on the bench as the FL/RL skew between a directly synced node and
 * one reachable only by relay, with the master's own constant already applied:
 * mean 1.966 ms, standard deviation 0.23 ms, in the exact two-link-pulled
 * topology step 5 specifies. A directly synced pair had already measured in
 * the tens of microseconds, so that skew is attributable almost entirely to
 * the relay hop and not to residual error in the base constant, which is why
 * it is added on top of whichever master delay applies rather than replacing
 * it: the two delays are physically in series, master transmit then relay
 * transmit, and the model should say so.
 *
 * Without this, the relay path meets checkoff step 5's "wheel still steers,
 * blinks and brakes" but misses its "FL and RL still within 1 ms" by about a
 * factor of two.
 */
#define LAB5_RELAY_DELAY_US 2000U

/**
 * @brief Receive silence, in ms, after which a node treats itself as cut off
 * and does not stand for election.
 *
 * Checkoff step 10 requires the term to change only if the isolated node was
 * the leader. Plain Raft fails that: an isolated follower times out every 60 to
 * 99 ms, raises its term each time, and when reconnected its higher term makes
 * the leader step down. Every live node heartbeats on both buses every 20 ms,
 * so a node that has heard nothing for 30 ms is cut off and could not win
 * anyway. Shorter than the 60 ms minimum election timeout so the guard is in
 * place before the first timeout fires. Must match ISOLATION_WINDOW in
 * pi/demo/raft_agent.py.
 */
#define LAB5_ISOLATION_MS 30U

/**
 * @brief Silence from a peer, in ms, after which the leader records it down.
 *
 * The handout's event table has the leader append NODE_X_DOWN when node X goes
 * down, and nothing else can: a node cut off from both buses cannot reach the
 * leader to report itself. Any frame from the peer counts, heartbeat or
 * consensus, so a node with a latched local failure, which stops heartbeating
 * but still acknowledges appends at least every 200 ms, is not dropped. Must
 * match PEER_DOWN in pi/demo/raft_agent.py.
 */
#define LAB5_PEER_DOWN_MS 500U

/**
 * @brief Silence on both buses, in ms, after which a node forgets its own
 * reported state and re-reports itself on reconnection.
 *
 * Longer than LAB5_PEER_DOWN_MS, so by the time a cut-off node resets, the
 * leader has already recorded it down; on reconnection it reports NODE_X_UP and
 * then each link it can hear, exactly as at boot, instead of delivering a stale
 * report it was holding. Must match ISOLATED_RESET in pi/demo/raft_agent.py.
 */
#define LAB5_ISOLATED_RESET_MS 600U

#define LAB5_EPOCH_US 1767225600000000ULL
/** @brief Decoded AppendEntries acknowledgement.
 *
 * Wire layout, little endian: byte 0 the follower's term, bytes 1..2 the
 * highest index it now matches the leader on, byte 3 the outcome from
 * enum raft_append_result. A CAN frame ack only means someone heard the frame,
 * so step 5 of the client write path needs this separate frame per follower
 * (TASKS.md D10); the identifier carries which follower it came from.
 */
struct lab5_ack { uint8_t term; uint16_t match_index; uint8_t result; };
/** @brief A client asking the leader to record one event.
 *
 * Wire layout: byte 0 the event, bytes 1..2 a request id the client chooses so
 * it can match the reply. The identifier says which node asked.
 */
struct lab5_client { uint8_t event; uint16_t request; };
/** @brief The leader's reply to a client, carrying either a commit or a redirect.
 *
 * Wire layout: byte 0 the result, byte 1 the node believed to be leader,
 * bytes 2..3 the request id, bytes 4..5 the index the entry was given, byte 6
 * the node being answered. A client that reached a follower gets
 * LAB5_REPLY_REDIRECT naming the leader, which is step 6 of the write path
 * failing closed rather than silently.
 *
 * The target node is not redundant. One identifier carries every reply, and
 * clients number their own requests independently, so two followers routinely
 * have a request id 1 outstanding at once. Without the target, one client's
 * acknowledgement clears the other's pending write and that entry is lost.
 */
struct lab5_reply {
	uint8_t result; uint8_t leader; uint16_t request; uint16_t index; uint8_t node;
};
/** @brief Reply results. */
enum lab5_reply_result { LAB5_REPLY_COMMITTED = 0, LAB5_REPLY_REDIRECT = 1 };
/** @brief A candidate asking for a vote.
 *
 * Wire layout, little endian: byte 0 the candidate's term, bytes 1..2 the index
 * of the last entry in its log, byte 3 that entry's term. The identifier says
 * which node is standing, so no candidate field is needed.
 *
 * The log fields are what stop a node that missed entries from winning. Raft
 * grants a vote only to a candidate whose log is at least as up to date as the
 * voter's, which is what guarantees a committed entry survives an election.
 * Here that matters directly: an STM32 that was isolated has a short log, and
 * electing it would lose link state the rest of the cluster had agreed on.
 */
struct lab5_vote_request { uint8_t term; uint16_t last_index; uint8_t last_term; };
/** @brief A vote, granted or refused.
 *
 * Wire layout: byte 0 the voter's term, byte 1 nonzero when granted, byte 2 the
 * candidate being answered. The identifier carries which node voted.
 *
 * The candidate field is needed for the same reason the reply frame carries a
 * target: two candidates can stand in the same term, and a vote meant for one
 * must not count towards the other's majority.
 */
struct lab5_vote { uint8_t term; uint8_t granted; uint8_t candidate; };
/** @brief Largest offset from LAB5_EPOCH_US the 48 bit REF field can carry. */
#define LAB5_REF_US_MAX 0xffffffffffffULL
/** @brief Central timing choices in milliseconds; recovery is measured separately. */
enum lab_time { LAB_HB_MS = 20, LAB_TIMEOUT_MS = 60, LAB_PID_MS = 10,
	LAB_FORCE_MS = 50, LAB_BLINK_MS = 500, LAB_DEDUP_MS = 200 };
/** @brief Failure reasons carried by FAULT. */
enum lab_reason { LAB_TEST = 1, LAB_RANGE, LAB_QUEUE, LAB_IO };
/** @brief Decoded command: steering -1000..1000 and pedals 0..100 percent. */
struct lab_control { int16_t steer; uint8_t throttle; uint8_t brake; uint8_t buttons; };
/** @brief CRC-8/ATM (poly 0x07, init 0, no reflection/xor).
 * @param data Bytes to cover.
 * @param size Byte count.
 * @return CRC byte.
 */
uint8_t lab_crc(const uint8_t *data, size_t size);
/** @brief Map catalog ID to a compact slot.
 * @param id Standard identifier.
 * @return Slot 0..6 or -1 for unknown.
 */
int lab_slot(uint32_t id);
/** @brief Validate reserved bytes, flags, ranges and CTRL CRC.
 * @param id Identifier.
 * @param data Eight bytes.
 * @return True for a valid catalog payload.
 */
bool lab_valid(uint32_t id, const uint8_t data[8]);
/** @brief Decode an already validated CTRL payload.
 * @param data Eight bytes.
 * @return Command in team-defined units.
 */
struct lab_control lab_decode(const uint8_t data[8]);
/** @brief Encode a REF payload.
 * @param data Eight bytes to fill.
 * @param master_us Leader clock in microseconds; only the low 48 bits travel.
 * @param cycle Cycle counter, wrapping at LAB5_REF_CYCLE_BITS bits.
 * @param master Node ID of the transmitting master.
 */
void lab5_ref_encode(uint8_t data[8], uint64_t master_us, uint16_t cycle, int master);
/** @brief Decode a REF payload.
 * @param data Eight received bytes.
 * @return Master clock and cycle counter.
 */
struct lab5_ref lab5_ref_decode(const uint8_t data[8]);
/** @brief Encode an AppendEntries header.
 * @param data Eight bytes to fill.
 * @param header Fields to encode; count zero means an empty append.
 */
void lab5_append_encode(uint8_t data[8], const struct lab5_append *header);
/** @brief Decode an AppendEntries header.
 * @param data Eight received bytes.
 * @return Decoded fields.
 */
struct lab5_append lab5_append_decode(const uint8_t data[8]);
/** @brief Encode an AppendEntries acknowledgement.
 * @param data Eight bytes to fill.
 * @param ack Fields to encode.
 */
void lab5_ack_encode(uint8_t data[8], const struct lab5_ack *ack);
/** @brief Decode an AppendEntries acknowledgement.
 * @param data Eight received bytes.
 * @return Decoded fields.
 */
struct lab5_ack lab5_ack_decode(const uint8_t data[8]);
/** @brief Encode a client request.
 * @param data Eight bytes to fill.
 * @param request Fields to encode.
 */
void lab5_client_encode(uint8_t data[8], const struct lab5_client *request);
/** @brief Decode a client request.
 * @param data Eight received bytes.
 * @return Decoded fields.
 */
struct lab5_client lab5_client_decode(const uint8_t data[8]);
/** @brief Encode a leader reply.
 * @param data Eight bytes to fill.
 * @param reply Fields to encode.
 */
void lab5_reply_encode(uint8_t data[8], const struct lab5_reply *reply);
/** @brief Decode a leader reply.
 * @param data Eight received bytes.
 * @return Decoded fields.
 */
struct lab5_reply lab5_reply_decode(const uint8_t data[8]);
/** @brief Encode a vote request.
 * @param data Eight bytes to fill.
 * @param request Request to encode.
 */
void lab5_vote_request_encode(uint8_t data[8], const struct lab5_vote_request *request);
/** @brief Decode a vote request.
 * @param data Eight received bytes.
 * @return Decoded request.
 */
struct lab5_vote_request lab5_vote_request_decode(const uint8_t data[8]);
/** @brief Encode a vote.
 * @param data Eight bytes to fill.
 * @param vote Vote to encode.
 */
void lab5_vote_encode(uint8_t data[8], const struct lab5_vote *vote);
/** @brief Decode a vote.
 * @param data Eight received bytes.
 * @return Decoded vote.
 */
struct lab5_vote lab5_vote_decode(const uint8_t data[8]);
/** @brief Build a per-node Lab 5 identifier.
 * @param base One of the LAB5_*_BASE values.
 * @param node Node ID from enum lab_node.
 * @return Identifier owned by that node.
 */
uint16_t lab5_id_for(uint16_t base, int node);
/** @brief Recognize any Lab 5 consensus identifier, REF excluded.
 * @param id CAN identifier.
 * @return True when the ID falls in the consensus range.
 */
bool lab5_is_consensus(uint32_t id);
/** @brief Map a node ID to its configured name.
 * @param node Node ID from enum lab_node.
 * @return Lowercase name, or "?" when out of range.
 */
const char *lab_node_name(int node);
/** @brief Identify the shared FAULT message.
 * @param id CAN identifier.
 * @return True for 0x020.
 */
bool lab_is_fault(uint32_t id);
/** @brief Map a validated catalog frame to its sole originating node.
 * @param id Catalog identifier.
 * @param data Validated eight-byte payload, including the FAULT zone.
 * @return Node 1..3, or zero for unknown ID.
 */
int lab_origin(uint32_t id, const uint8_t data[8]);
#endif
