/*
 * SPDX-License-Identifier: Apache-2.0
 */
/** @file lab_protocol.c
 * @brief Pure catalog encoding rules, also testable without Zephyr.
 */
#include "lab_protocol.h"
#include "cluster_config.h"

/** @brief Compute CRC-8/ATM.
 * @param data Input bytes.
 * @param size Byte count.
 * @return CRC.
 */
uint8_t lab_crc(const uint8_t *data, size_t size)
{
	uint8_t crc = 0;
	for (size_t i = 0; i < size; i++) {

		crc ^= data[i];
		for (int bit = 0; bit < 8; bit++) {
			crc = (uint8_t)((crc << 1) ^ ((crc & 0x80U) != 0U ? 7U : 0U));
		}
	}
	return crc;
}

/** @brief Find catalog index.
 * @param id CAN ID.
 * @return Index or -1.
 */
int lab_slot(uint32_t id)
{
	static const uint16_t ids[] = {LAB_CTRL, LAB_FAULT, LAB_HB1, LAB_HB2,
		LAB_HB3, LAB_BLINK, LAB_FORCE};
	for (size_t i = 0; i < sizeof(ids) / sizeof(ids[0]); i++) {
		if (ids[i] == id) {
			return (int)i;
		}
	}
	return -1;
}

/** @brief Decode CTRL fields.
 * @param data Eight-byte payload.
 * @return Decoded values.
 */
struct lab_control lab_decode(const uint8_t data[8])
{
	struct lab_control command = {
		.steer = (int16_t)((uint16_t)data[1] | ((uint16_t)data[2] << 8)),
		.throttle = data[3], .brake = data[4], .buttons = data[5],
	};
	return command;
}

/** @brief Validate catalog content before dispatch.
 * @param id CAN ID.
 * @param data Eight-byte payload.
 * @return Whether payload is accepted.
 */
bool lab_valid(uint32_t id, const uint8_t data[8])
{
	if (lab_slot(id) < 0 || (data[0] & 0xf0U) != 0U) {
		return false;
	}
	if (id == LAB_CTRL) {
		struct lab_control command = lab_decode(data);
		return command.steer >= -1000 && command.steer <= 1000 &&

			command.throttle <= 100 && command.brake <= 100 &&
			data[6] == 0 && lab_crc(data, 7) == data[7];
	}
	int first_reserved = 3;
	if (lab_is_fault(id)) {

		if ((data[1] < 1 || data[1] > 3) || data[2] < LAB_TEST || data[2] > LAB_IO) {
			return false;
		}
	} else if (id >= LAB_HB1 && id <= LAB_HB3) {
		if (data[1] > 1 || data[2] > 3) {
			return false;
		}
	} else if (id == LAB_BLINK) {
		if (data[1] > 3 || data[2] > 1) {
			return false;
		}
	} else {
		first_reserved = 2;
	}
	for (int i = first_reserved; i < 8; i++) {
		if (data[i] != 0) {
			return false;
		}
	}
	return true;
}

/** @brief Build a per-node Lab 5 identifier.
 * @param base One of the LAB5_*_BASE values.
 * @param node Node ID from enum lab_node.
 * @return Identifier owned by that node, or the base when the node is invalid.
 */
uint16_t lab5_id_for(uint16_t base, int node)
{
	if (node < LAB_NODE_COCKPIT || node > LAB_NODE_DRIVETRAIN) {
		return base;
	}
	return (uint16_t)(base + node);
}

/** @brief Recognize any Lab 5 consensus identifier, REF excluded.
 * @param id CAN identifier.
 * @return True when the ID falls in the masked consensus range.
 */
bool lab5_is_consensus(uint32_t id)
{
	return (id & LAB5_FILTER_MASK) == LAB5_FILTER_ID;
}

/** @brief Map a node ID to its configured name.
 * @param node Node ID from enum lab_node.
 * @return Lowercase name, or "?" when out of range.
 */
const char *lab_node_name(int node)
{
	static const char *const names[] = LAB5_CFG_NODE_NAMES;

	if (node < 1 || node > LAB5_CFG_NODE_COUNT) {
		return "?";
	}
	return names[node - 1];
}

/** @brief Recognize the template shared FAULT identifier.
 * @param id CAN ID.
 * @return Whether this is a fault frame.
 */
bool lab_is_fault(uint32_t id)
{
	return id == LAB_FAULT;
}

/** @brief Identify original producers independently of which node forwarded a frame.
 * @param id Catalog identifier.
 * @param data Validated payload.
 * @return Original node number, or zero.
 */
int lab_origin(uint32_t id, const uint8_t data[8])
{
	if (lab_is_fault(id)) {
		return data[1];
	}
	if (id >= LAB_HB1 && id <= LAB_HB3) {
		return id - LAB_HB1 + 1;
	}
	if (id == LAB_CTRL || id == LAB_BLINK) {
		return 1;
	}
	return id == LAB_FORCE ? 2 : 0;
}
