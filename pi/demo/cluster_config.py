"""Parse config/cluster.conf, shared by the Pi agent and the STM32 header generator.

The file must be byte-identical on every node. The hash here is computed over the
raw bytes, so the Pi and both Nucleos print the same value at boot and a stale
copy shows up immediately.
"""
import hashlib
from pathlib import Path

#: Keys every node must define; a missing one is a hard error, not a default.
REQUIRED = ("N", "default_leader", "heartbeat_ms", "missed_heartbeats",
            "election_timeout_jitter_ms", "log_size", "nodes", "Debug",
            "election_seed")

#: Keys parsed as non-negative integers.
INTEGER_KEYS = ("N", "heartbeat_ms", "missed_heartbeats",
                "election_timeout_jitter_ms", "log_size", "election_seed")

#: default_leader values that mean "hold an election at boot" (TASKS.md assumption 5).
ELECT_AT_BOOT = ("none", "random")

#: Length of the short hash printed at boot.
HASH_CHARS = 8


class ConfigError(ValueError):
    """Raised when the configuration file is missing a key or is inconsistent."""


def config_path():
    """Locate config/cluster.conf relative to this file.

    :return: Path to the repository's cluster configuration.
    """
    return Path(__file__).resolve().parents[2] / "config" / "cluster.conf"


def parse(text):
    """Split key=value lines, ignoring blanks and comments.

    :param text: Raw file contents.
    :return: Mapping of key to unparsed string value.
    """
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ConfigError("line %d is not key=value: %r" % (number, line))
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key in values:
            raise ConfigError("duplicate key %r on line %d" % (key, number))
        values[key] = value.strip()
    return values


def load(path=None):
    """Read, validate and normalise the cluster configuration.

    :param path: Configuration file, or None for the repository copy.
    :return: Mapping with parsed values plus derived keys: hash, majority,
        default_leader_id, election_min_ms and election_max_ms.
    """
    path = Path(path) if path is not None else config_path()
    raw = path.read_bytes()
    values = parse(raw.decode())

    missing = [key for key in REQUIRED if key not in values]
    if missing:
        raise ConfigError("missing keys: %s" % ", ".join(missing))

    config = {}
    for key in INTEGER_KEYS:
        try:
            config[key] = int(values[key])
        except ValueError:
            raise ConfigError("%s must be an integer, got %r" % (key, values[key]))
        if config[key] < 0:
            raise ConfigError("%s must not be negative" % key)

    config["nodes"] = [name.strip() for name in values["nodes"].split(",") if name.strip()]
    if len(config["nodes"]) != len(set(config["nodes"])):
        raise ConfigError("nodes contains a duplicate")
    if len(config["nodes"]) != config["N"]:
        raise ConfigError("N is %d but nodes lists %d entries"
                          % (config["N"], len(config["nodes"])))

    config["Debug"] = values["Debug"].strip().lower() in ("yes", "true", "1", "on")

    leader = values["default_leader"].strip()
    if leader not in ELECT_AT_BOOT and leader not in config["nodes"]:
        raise ConfigError("default_leader %r is not in nodes and is not none/random"
                          % leader)
    config["default_leader"] = leader
    config["default_leader_id"] = (0 if leader in ELECT_AT_BOOT
                                   else config["nodes"].index(leader) + 1)

    if config["heartbeat_ms"] == 0:
        raise ConfigError("heartbeat_ms must be nonzero")
    config["majority"] = config["N"] // 2 + 1
    config["election_min_ms"] = config["missed_heartbeats"] * config["heartbeat_ms"]
    config["election_max_ms"] = (config["election_min_ms"]
                                 + config["election_timeout_jitter_ms"])
    config["hash"] = hashlib.sha256(raw).hexdigest()[:HASH_CHARS]
    config["path"] = path
    return config


def node_id(config, name):
    """Map a configured node name to its numeric ID.

    :param config: Mapping from load().
    :param name: Node name as written in the nodes key.
    :return: Numeric node ID starting at 1.
    """
    if name not in config["nodes"]:
        raise ConfigError("unknown node %r" % name)
    return config["nodes"].index(name) + 1


def node_name(config, identifier):
    """Map a numeric node ID back to its configured name.

    :param config: Mapping from load().
    :param identifier: Numeric node ID starting at 1.
    :return: Node name, or "?" when the ID is out of range.
    """
    if 1 <= identifier <= len(config["nodes"]):
        return config["nodes"][identifier - 1]
    return "?"
