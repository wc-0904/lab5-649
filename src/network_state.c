/*
 * SPDX-License-Identifier: Apache-2.0
 */
/** @file network_state.c
 * @brief The four mutators, the read, and log replay.
 */
#include "network_state.h"

/** @brief Bit for a node in the nodes mask.
 * @param node Node ID 1..3.
 * @return Mask bit, or zero when the node is invalid.
 */
static uint8_t node_bit(int node)
{
	if (node < 1 || node > NETWORK_NODES) {
		return 0;
	}
	return (uint8_t)(1U << (node - 1));
}

/** @brief Bit for one node's link to one bus in the links mask.
 * @param node Node ID 1..3.
 * @param bus Bus number 1 or 2.
 * @return Mask bit, or zero when node or bus is invalid.
 */
static uint8_t link_bit(int node, int bus)
{
	if (node < 1 || node > NETWORK_NODES || bus < 1 || bus > NETWORK_BUSES) {
		return 0;
	}
	return (uint8_t)(1U << ((node - 1) * NETWORK_BUSES + (bus - 1)));
}

/** @brief Every link bit belonging to one node.
 * @param node Node ID 1..3.
 * @return Mask of that node's link bits.
 */
static uint8_t node_links(int node)
{
	return (uint8_t)(link_bit(node, 1) | link_bit(node, 2));
}

/** @brief Reset to the empty graph.
 * @param net Graph to clear.
 */
void network_reset(struct network *net)
{
	net->nodes = 0;
	net->links = 0;
	net->failure_mode = false;
}

/** @brief Mark the link between a node and a bus up.
 * @param net Graph to change.
 * @param node Node ID 1..3.
 * @param bus Bus number 1 or 2.
 */
void network_add_link(struct network *net, int node, int bus)
{
	net->links |= link_bit(node, bus);
}

/** @brief Mark that link down; node and bus stay in the graph.
 * @param net Graph to change.
 * @param node Node ID 1..3.
 * @param bus Bus number 1 or 2.
 */
void network_drop_link(struct network *net, int node, int bus)
{
	net->links &= (uint8_t)~link_bit(node, bus);
}

/** @brief Add a node, or mark it alive again, with no links.
 * @param net Graph to change.
 * @param node Node ID 1..3.
 */
void network_add_node(struct network *net, int node)
{
	net->nodes |= node_bit(node);
	/* Part 4: a node comes back with no links until addLink runs for it. */
	net->links &= (uint8_t)~node_links(node);
}

/** @brief Remove a node and every link attached to it.
 * @param net Graph to change.
 * @param node Node ID 1..3.
 */
void network_drop_node(struct network *net, int node)
{
	net->nodes &= (uint8_t)~node_bit(node);
	net->links &= (uint8_t)~node_links(node);
}

/** @brief Read the whole graph.
 * @param net Graph to read.
 * @return Copy of the current graph.
 */
struct network network_get(const struct network *net)
{
	return *net;
}

/** @brief Apply one event.
 * @param net Graph to change.
 * @param event Encoded event from enum raft_event.
 */
void network_apply(struct network *net, uint8_t event)
{
	if (event == RAFT_COCKPIT_OK) {
		net->failure_mode = false;
		return;
	}
	if (event == RAFT_COCKPIT_ERROR) {
		net->failure_mode = true;
		return;
	}
	if (event > RAFT_NODE_UP_BASE && event <= RAFT_NODE_UP_BASE + NETWORK_NODES) {
		network_add_node(net, event - RAFT_NODE_UP_BASE);
		return;
	}
	if (event > RAFT_NODE_DOWN_BASE && event <= RAFT_NODE_DOWN_BASE + NETWORK_NODES) {
		network_drop_node(net, event - RAFT_NODE_DOWN_BASE);
		return;
	}
	if (event >= RAFT_LINK_UP_BASE &&
	    event < RAFT_LINK_UP_BASE + NETWORK_NODES * NETWORK_BUSES) {
		int slot = event - RAFT_LINK_UP_BASE;

		network_add_link(net, slot / NETWORK_BUSES + 1, slot % NETWORK_BUSES + 1);
		return;
	}
	if (event >= RAFT_LINK_DOWN_BASE &&
	    event < RAFT_LINK_DOWN_BASE + NETWORK_NODES * NETWORK_BUSES) {
		int slot = event - RAFT_LINK_DOWN_BASE;

		network_drop_link(net, slot / NETWORK_BUSES + 1, slot % NETWORK_BUSES + 1);
	}
	/* An unknown event changes nothing, so replay stays total. */
}

/** @brief Rebuild the graph from the log.
 * @param net Destination graph.
 * @param log Log to replay.
 * @param through Last index to apply, or 0 for the whole log.
 */
void network_replay(struct network *net, const struct raft_log *log, uint32_t through)
{
	uint32_t last = raft_log_last_index(log);

	if (through == 0 || through > last) {
		through = last;
	}
	network_reset(net);
	for (uint32_t index = raft_log_first_index(log); index != 0 && index <= through;
	     index++) {
		struct raft_entry entry;

		if (raft_log_get(log, index, &entry)) {
			network_apply(net, entry.event);
		}
	}
}

/** @brief Report whether a node is up.
 * @param net Graph to read.
 * @param node Node ID 1..3.
 * @return True when the node is alive.
 */
bool network_node_up(const struct network *net, int node)
{
	uint8_t bit = node_bit(node);

	return bit != 0 && (net->nodes & bit) != 0;
}

/** @brief Report whether a node's link to a bus is up.
 * @param net Graph to read.
 * @param node Node ID 1..3.
 * @param bus Bus number 1 or 2.
 * @return True when the node is up and that link is up.
 */
bool network_link_up(const struct network *net, int node, int bus)
{
	uint8_t bit = link_bit(node, bus);

	return bit != 0 && network_node_up(net, node) && (net->links & bit) != 0;
}

/** @brief Work out how one node reaches another from the committed graph.
 * @param net Graph to read.
 * @param from Sending node ID.
 * @param to Destination node ID.
 * @return Route; reachable is false when no path exists.
 */
struct network_route network_route_to(const struct network *net, int from, int to)
{
	struct network_route route = {.bus = 0, .relay = 0, .reachable = false};

	if (from == to || !network_node_up(net, from) || !network_node_up(net, to)) {
		return route;
	}
	for (int bus = 1; bus <= NETWORK_BUSES; bus++) {
		if (network_link_up(net, from, bus) && network_link_up(net, to, bus)) {
			route.bus = bus;
			route.reachable = true;
			return route;
		}
	}
	/* No shared bus: look for one node that bridges the two. */
	for (int relay = 1; relay <= NETWORK_NODES; relay++) {
		if (relay == from || relay == to || !network_node_up(net, relay)) {
			continue;
		}
		int in = 0;
		int out = 0;

		for (int bus = 1; bus <= NETWORK_BUSES; bus++) {
			if (in == 0 && network_link_up(net, from, bus) &&
			    network_link_up(net, relay, bus)) {
				in = bus;
			}
			if (network_link_up(net, relay, bus) &&
			    network_link_up(net, to, bus)) {
				out = bus;
			}
		}
		if (in != 0 && out != 0) {
			route.bus = in;
			route.relay = relay;
			route.reachable = true;
			return route;
		}
	}
	return route;
}

/** @brief Render the graph as one comparable line.
 * @param net Graph to render.
 * @param out Destination buffer.
 * @param size Size of out, at least NETWORK_PRINT_SIZE.
 */
void network_print(const struct network *net, char *out, size_t size)
{
	static const char initial[NETWORK_NODES] = {'C', 'S', 'D'};
	char text[NETWORK_PRINT_SIZE];
	size_t at = 0;

	const char *prefix = "net nodes=";

	for (const char *p = prefix; *p != '\0'; p++) {
		text[at++] = *p;
	}
	for (int node = 1; node <= NETWORK_NODES; node++) {
		text[at++] = network_node_up(net, node) ? initial[node - 1] : '.';
	}
	const char *middle = " links=";

	for (const char *p = middle; *p != '\0'; p++) {
		text[at++] = *p;
	}
	for (int node = 1; node <= NETWORK_NODES; node++) {
		for (int bus = 1; bus <= NETWORK_BUSES; bus++) {
			text[at++] = network_link_up(net, node, bus) ?
				(char)('0' + bus) : '.';
		}
	}
	const char *tail = net->failure_mode ? " mode=FAIL" : " mode=OK";

	for (const char *p = tail; *p != '\0'; p++) {
		text[at++] = *p;
	}
	text[at] = '\0';
	for (size_t i = 0; i < size; i++) {
		out[i] = i < at ? text[i] : '\0';
		if (i >= at) {
			return;
		}
	}
	if (size > 0) {
		out[size - 1] = '\0';
	}
}

/** @brief Compare two graphs.
 * @param a First graph.
 * @param b Second graph.
 * @return True when both describe the same network and mode.
 */
bool network_equal(const struct network *a, const struct network *b)
{
	return a->nodes == b->nodes && a->links == b->links &&
		a->failure_mode == b->failure_mode;
}

/** @brief Report whether the committed log puts the vehicle in failure mode.
 * @param net Network state.
 * @return True when the cockpit is recorded as down.
 */
bool network_failure_mode(const struct network *net)
{
	return net->failure_mode;
}
