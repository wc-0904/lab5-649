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
/** @brief Masked filter covering every consensus ID in one RX filter slot.
 *
 * The MCP2515 driver offers 16 software filter slots and Lab 3 already uses
 * seven, so the eleven consensus identifiers are accepted as the single range
 * 0x040..0x05f instead of one slot each.
 */
enum lab5_filter { LAB5_FILTER_ID = 0x040, LAB5_FILTER_MASK = 0x7e0 };
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
