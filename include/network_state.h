/* SPDX-License-Identifier: Apache-2.0 */
/** @file network_state.h
 * @brief The Raft state machine: the network graph built by replaying the log.
 *
 * Pure C with no Zephyr dependency, so the host tests exercise the same code
 * the firmware runs. The state machine is a pure function of the log: nothing
 * here may be changed except by applying an entry, and replaying the same log
 * on any node must produce the same graph.
 */
#ifndef NETWORK_STATE_H_
#define NETWORK_STATE_H_
#include "raft_log.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
/** @brief Nodes in the cluster. */
#define NETWORK_NODES 3
/** @brief CAN buses each node attaches to. */
#define NETWORK_BUSES 2
/** @brief Characters a printed graph needs, including the terminator. */
#define NETWORK_PRINT_SIZE 64
/** @brief The whole network graph, plus the vehicle failure mode.
 *
 * Three nodes and six links fit in two bitmasks (TASKS.md D9), which makes the
 * state trivial to compare between nodes at the demo: one line of text per node
 * that must match character for character. Bit n-1 of nodes is node n; bit
 * (n-1)*2 + (b-1) of links is node n on bus b. A link bit is only meaningful
 * while its node bit is set, since dropNode removes a node's links with it.
 */
struct network { uint8_t nodes; uint8_t links; bool failure_mode; };
/** @brief Reset to the empty graph, which is what an empty log replays to.
 * @param net Graph to clear.
 */
void network_reset(struct network *net);
/** @brief Mark the link between a node and a bus up, adding it if absent.
 * @param net Graph to change.
 * @param node Node ID 1..3.
 * @param bus Bus number 1 or 2.
 */
void network_add_link(struct network *net, int node, int bus);
/** @brief Mark that link down. The node and the bus stay in the graph.
 * @param net Graph to change.
 * @param node Node ID 1..3.
 * @param bus Bus number 1 or 2.
 */
void network_drop_link(struct network *net, int node, int bus);
/** @brief Add a node, or mark it alive again, with no links until addLink runs.
 * @param net Graph to change.
 * @param node Node ID 1..3.
 */
void network_add_node(struct network *net, int node);
/** @brief Remove a node and every link attached to it.
 * @param net Graph to change.
 * @param node Node ID 1..3.
 */
void network_drop_node(struct network *net, int node);
/** @brief Read the whole graph, as the Raft client does to build its routes.
 * @param net Graph to read.
 * @return Copy of the current graph.
 */
struct network network_get(const struct network *net);
/** @brief Apply one event. The only way the graph may change during replay.
 * @param net Graph to change.
 * @param event Encoded event from enum raft_event.
 */
void network_apply(struct network *net, uint8_t event);
/** @brief Rebuild the graph from the log, up to and including one index.
 *
 * Replays from the oldest retained entry, so with a full ring the result
 * depends on the retained window. The whole log is replayed when through is
 * zero or beyond the last index.
 * @param net Destination graph.
 * @param log Log to replay.
 * @param through Last index to apply, or 0 for the whole log.
 */
void network_replay(struct network *net, const struct raft_log *log, uint32_t through);
/** @brief Report whether a node is up.
 * @param net Graph to read.
 * @param node Node ID 1..3.
 * @return True when the node is in the graph and alive.
 */
bool network_node_up(const struct network *net, int node);
/** @brief Report whether the committed log puts the vehicle in failure mode.
 * @param net Network state.
 * @return True when the cockpit is recorded as down.
 */
bool network_failure_mode(const struct network *net);
/** @brief Report whether a node's link to a bus is up.
 * @param net Graph to read.
 * @param node Node ID 1..3.
 * @param bus Bus number 1 or 2.
 * @return True when the node is up and that link is up.
 */
bool network_link_up(const struct network *net, int node, int bus);
/** @brief One destination's entry in a node's routing table.
 *
 * With three nodes and two buses the table is this small: either a bus to send
 * on directly, or a node to relay through. Part 5.1 says each node distils the
 * graph into the subset it needs, and this is that subset.
 */
struct network_route { int bus; int relay; bool reachable; };
/** @brief Work out how one node reaches another from the committed graph.
 *
 * Prefers a bus both ends are on. Failing that, looks for a single node that
 * shares a bus with each end, which is the relayed hop of Part 5.2; with three
 * nodes one hop always suffices. The lowest numbered bus and relay are chosen
 * so every node derives the same table from the same log.
 * @param net Graph to read.
 * @param from Sending node ID.
 * @param to Destination node ID.
 * @return Route; reachable is false when no path exists.
 */
struct network_route network_route_to(const struct network *net, int from, int to);
/** @brief Render the graph as one comparable line.
 *
 * Format: "net nodes=CSD links=c1c2s1s2d1d2 mode=OK", where each node position
 * is its initial when up and a dot when down, each link position is 1 or 2 when
 * up and a dot when down. Every node prints the same characters for the same
 * log, which is what the demo compares.
 * @param net Graph to render.
 * @param out Destination buffer.
 * @param size Size of out, at least NETWORK_PRINT_SIZE.
 */
void network_print(const struct network *net, char *out, size_t size);
/** @brief Compare two graphs.
 * @param a First graph.
 * @param b Second graph.
 * @return True when both describe the same network and mode.
 */
bool network_equal(const struct network *a, const struct network *b);
#endif
