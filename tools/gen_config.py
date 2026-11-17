#!/usr/bin/env python3
"""Turn config/cluster.conf into a C header for the STM32 build.

Run from CMake. The Pi reads the same file at run time through
pi/demo/cluster_config.py, and both sides compute the same hash so a node
booting with a stale config is visible on the console.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi" / "demo"))
from cluster_config import ConfigError, load


def emit(config, source):
    """Render the generated header text.

    :param config: Parsed configuration mapping from cluster_config.load.
    :param source: Path of the configuration file, for the header comment.
    :return: Header text.
    """
    nodes = config["nodes"]
    lines = [
        "/* SPDX-License-Identifier: Apache-2.0 */",
        "/** @file cluster_config.h",
        " * @brief Generated from %s by tools/gen_config.py. Do not edit." % source,
        " */",
        "#ifndef CLUSTER_CONFIG_H_",
        "#define CLUSTER_CONFIG_H_",
        "",
        "/** @brief Hash of the configuration file every node must agree on. */",
        '#define LAB5_CFG_HASH "%s"' % config["hash"],
        "/** @brief Cluster size; a majority is LAB5_CFG_MAJORITY. */",
        "#define LAB5_CFG_N %d" % config["N"],
        "/** @brief Votes needed to win an election or commit an entry. */",
        "#define LAB5_CFG_MAJORITY %d" % (config["N"] // 2 + 1),
        "/** @brief Numeric ID of the pre-defined leader, or 0 for none/random. */",
        "#define LAB5_CFG_DEFAULT_LEADER %d" % config["default_leader_id"],
        "/** @brief Configured default_leader as written in the file. */",
        '#define LAB5_CFG_DEFAULT_LEADER_NAME "%s"' % config["default_leader"],
        "/** @brief Leader heartbeat and clock-sync period in milliseconds. */",
        "#define LAB5_CFG_HEARTBEAT_MS %d" % config["heartbeat_ms"],
        "/** @brief Missed REFs before a follower declares the leader dead. */",
        "#define LAB5_CFG_MISSED_HEARTBEATS %d" % config["missed_heartbeats"],
        "/** @brief Random range in milliseconds added to the election timeout. */",
        "#define LAB5_CFG_JITTER_MS %d" % config["election_timeout_jitter_ms"],
        "/** @brief Shortest election timeout in milliseconds. */",
        "#define LAB5_CFG_ELECTION_MIN_MS %d" % config["election_min_ms"],
        "/** @brief Longest election timeout in milliseconds. */",
        "#define LAB5_CFG_ELECTION_MAX_MS %d" % config["election_max_ms"],
        "/** @brief Logical log capacity; the STM32 ring is smaller, see TASKS.md D7. */",
        "#define LAB5_CFG_LOG_SIZE %d" % config["log_size"],
        "/** @brief Print election and append messages when nonzero. */",
        "#define LAB5_CFG_DEBUG %d" % (1 if config["Debug"] else 0),
        "/** @brief Election jitter seed, combined with the local node ID. */",
        "#define LAB5_CFG_ELECTION_SEED %d" % config["election_seed"],
        "/** @brief Number of configured node names. */",
        "#define LAB5_CFG_NODE_COUNT %d" % len(nodes),
        "/** @brief Node names in configured order; index + 1 is the node ID. */",
        "#define LAB5_CFG_NODE_NAMES { %s }" % ", ".join('"%s"' % n for n in nodes),
        "",
        "#endif",
        "",
    ]
    return "\n".join(lines)


def main():
    """Parse arguments, validate the config and write the header if it changed.

    :return: Process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="path to cluster.conf")
    parser.add_argument("output", type=Path, help="path of the header to write")
    args = parser.parse_args()
    try:
        config = load(args.source)
    except (ConfigError, OSError) as error:
        print("gen_config: %s" % error, file=sys.stderr)
        return 1
    text = emit(config, args.source.name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Avoid touching the mtime when nothing changed, so ninja does not rebuild.
    if not args.output.exists() or args.output.read_text() != text:
        args.output.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
