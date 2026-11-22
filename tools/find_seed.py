#!/usr/bin/env python3
"""Search for an election seed that makes a chosen node win the first election.

Checkoff step 8 ends with "the seed that makes the Pi win", so the operator has
to be able to produce one on demand. Every node draws its election timeout from
the shared seed mixed with its own node ID, so a seed fixes the whole cluster's
first round: the node with the shortest timeout starts its election first and,
with nothing else in flight, wins it.

Usage:
    python3 tools/find_seed.py                 # seeds where cockpit wins
    python3 tools/find_seed.py --node drivetrain --count 5 --margin 10
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "demo"))

from cluster_config import load  # noqa: E402
from election import Election  # noqa: E402


def first_timeouts(seed, config):
    """Draw every node's first election timeout for one seed.

    :param seed: Candidate seed.
    :param config: Mapping from cluster_config.load().
    :return: Dict of node name to timeout in milliseconds.
    """
    base = config["missed_heartbeats"] * config["heartbeat_ms"]
    jitter = config["election_timeout_jitter_ms"]
    return {name: Election(seed, index + 1).timeout_ms(base, jitter)
            for index, name in enumerate(config["nodes"])}


def main():
    """Print seeds whose first round elects the requested node."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="cockpit", help="node that should win")
    parser.add_argument("--count", type=int, default=3, help="how many seeds to print")
    parser.add_argument("--margin", type=int, default=5,
                        help="milliseconds the winner must lead by, so the result "
                             "survives a late heartbeat rather than being a tie")
    parser.add_argument("--limit", type=int, default=100000, help="seeds to try")
    parser.add_argument("--config", type=Path,
                        default=ROOT / "config" / "cluster.conf")
    args = parser.parse_args()

    config = load(args.config)
    if args.node not in config["nodes"]:
        raise SystemExit("unknown node %s; known: %s"
                         % (args.node, ", ".join(config["nodes"])))
    found = 0
    for seed in range(1, args.limit + 1):
        timeouts = first_timeouts(seed, config)
        mine = timeouts[args.node]
        others = [ms for name, ms in timeouts.items() if name != args.node]
        if mine + args.margin <= min(others):
            order = " ".join("%s=%d" % pair for pair in sorted(timeouts.items(),
                                                               key=lambda kv: kv[1]))
            print("election_seed=%d  %s" % (seed, order))
            found += 1
            if found >= args.count:
                return
    raise SystemExit("no seed found in %d tries; lower --margin" % args.limit)


if __name__ == "__main__":
    main()
