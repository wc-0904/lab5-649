"""Host tests for the Lab 5 configuration file and identifier allocation.

These check the two endpoints agree, as CLAUDE.md requires of any protocol
change. They do not build or flash firmware.
"""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "demo"))

import protocol as P
from cluster_config import ConfigError, load, node_id, node_name

HEADER = (ROOT / "include" / "lab_protocol.h").read_text()
CONF = ROOT / "config" / "cluster.conf"


def c_enum(name):
    """Extract one C enum's name/value pairs from lab_protocol.h.

    :param name: Enum tag, for example "lab5_id".
    :return: Mapping of enumerator name to integer value.
    """
    body = re.search(r"enum\s+%s\s*\{(.*?)\}" % name, HEADER, re.S).group(1)
    return {m.group(1): int(m.group(2), 0)
            for m in re.finditer(r"(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)", body)}


class IdentifierTests(unittest.TestCase):
    def test_python_mirrors_firmware_ids(self):
        ids = c_enum("lab5_id")
        self.assertEqual(ids["LAB5_REF"], P.REF)
        self.assertEqual(ids["LAB5_APPEND"], P.APPEND)
        self.assertEqual(ids["LAB5_APPEND_ACK_BASE"], P.APPEND_ACK_BASE)
        self.assertEqual(ids["LAB5_REQ_VOTE_BASE"], P.REQ_VOTE_BASE)
        self.assertEqual(ids["LAB5_VOTE_BASE"], P.VOTE_BASE)
        self.assertEqual(ids["LAB5_CLIENT_REQ_BASE"], P.CLIENT_REQ_BASE)
        self.assertEqual(ids["LAB5_CLIENT_REDIRECT"], P.CLIENT_REDIRECT)
        mask = c_enum("lab5_filter")
        self.assertEqual(mask["LAB5_FILTER_ID"], P.LAB5_FILTER_ID)
        self.assertEqual(mask["LAB5_FILTER_MASK"], P.LAB5_FILTER_MASK)

    def test_reference_wins_arbitration_against_everything(self):
        # Lower identifier wins on CAN, so REF must be below every other ID.
        for other in (*P.CATALOG, P.APPEND, P.CLIENT_REDIRECT):
            self.assertLess(P.REF, other)

    def test_transmitted_ids_are_distinct(self):
        sent = [P.APPEND, P.CLIENT_REDIRECT]
        sent += [P.lab5_id_for(base, node)
                 for base in P.LAB5_PER_NODE_BASES for node in (1, 2, 3)]
        self.assertEqual(len(sent), len(set(sent)))

    def test_one_masked_filter_covers_consensus_only(self):
        sent = [P.APPEND, P.CLIENT_REDIRECT]
        sent += [P.lab5_id_for(base, node)
                 for base in P.LAB5_PER_NODE_BASES for node in (1, 2, 3)]
        for can_id in sent:
            self.assertTrue(P.is_consensus(can_id), hex(can_id))
        # REF has its own filter, and no Lab 3 identifier may be swallowed.
        self.assertFalse(P.is_consensus(P.REF))
        for can_id in P.CATALOG:
            self.assertFalse(P.is_consensus(can_id), hex(can_id))

    def test_consensus_ids_are_not_lab3_catalog(self):
        # lab_valid() rejects them, so dispatch must branch before the dedup cache.
        for can_id in (P.REF, P.APPEND, P.CLIENT_REDIRECT):
            self.assertNotIn(can_id, P.CATALOG)
            self.assertFalse(P.valid(can_id, bytes(8)))

    def test_per_node_helper_matches_c_guard(self):
        for bad in (0, 4, -1):
            self.assertEqual(P.lab5_id_for(P.VOTE_BASE, bad), P.VOTE_BASE)


class ConfigTests(unittest.TestCase):
    def test_repository_config_is_valid_and_derived_values_match_handout(self):
        config = load(CONF)
        self.assertEqual(config["N"], 3)
        self.assertEqual(config["majority"], 2)
        self.assertEqual(config["nodes"], ["cockpit", "steering", "drivetrain"])
        self.assertEqual(config["default_leader_id"], 1)
        # Handout: 3 * 20 + U(0, 39) is 60 to 99 ms.
        self.assertEqual((config["election_min_ms"], config["election_max_ms"]), (60, 99))

    def test_node_ids_match_the_firmware_enum(self):
        config = load(CONF)
        nodes = c_enum("lab_node")
        self.assertEqual(nodes["LAB_NODE_COCKPIT"], node_id(config, "cockpit"))
        self.assertEqual(nodes["LAB_NODE_STEERING"], node_id(config, "steering"))
        self.assertEqual(nodes["LAB_NODE_DRIVETRAIN"], node_id(config, "drivetrain"))
        self.assertEqual(node_name(config, 2), "steering")
        self.assertEqual(node_name(config, 9), "?")

    def write(self, text):
        """Write a temporary config file.

        :param text: File contents.
        :return: Path to the written file.
        """
        handle = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        handle.write(text)
        handle.close()
        return Path(handle.name)

    def base_text(self, **overrides):
        """Render a valid config with optional overridden keys.

        :param overrides: Keys to replace.
        :return: Config file text.
        """
        values = {"N": 3, "default_leader": "cockpit", "heartbeat_ms": 20,
                  "missed_heartbeats": 3, "election_timeout_jitter_ms": 39,
                  "log_size": 30000, "nodes": "cockpit,steering,drivetrain",
                  "Debug": "Yes", "election_seed": 1}
        values.update(overrides)
        return "".join("%s=%s\n" % item for item in values.items())

    def test_rejects_inconsistent_or_incomplete_files(self):
        cases = {
            "N disagrees with nodes": self.base_text(N=4),
            "leader not a node": self.base_text(default_leader="trailer"),
            "duplicate node": self.base_text(nodes="cockpit,cockpit,steering"),
            "non-integer": self.base_text(heartbeat_ms="fast"),
            "zero heartbeat": self.base_text(heartbeat_ms=0),
            "missing key": "N=3\n",
            "not key=value": self.base_text() + "garbage\n",
        }
        for reason, text in cases.items():
            with self.subTest(reason):
                with self.assertRaises(ConfigError):
                    load(self.write(text))

    def test_none_and_random_mean_elect_at_boot(self):
        for leader in ("none", "random"):
            config = load(self.write(self.base_text(default_leader=leader)))
            self.assertEqual(config["default_leader_id"], 0)

    def test_hash_changes_with_content_and_ignores_nothing(self):
        first = load(self.write(self.base_text()))
        same = load(self.write(self.base_text()))
        other = load(self.write(self.base_text(election_seed=2)))
        self.assertEqual(first["hash"], same["hash"])
        self.assertNotEqual(first["hash"], other["hash"])


class GeneratedHeaderTests(unittest.TestCase):
    def generate(self, source):
        """Run tools/gen_config.py and return the header text.

        :param source: Config file to generate from.
        :return: Generated header contents.
        """
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cluster_config.h"
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "gen_config.py"),
                 str(source), str(output)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return output.read_text()

    def test_header_matches_the_parsed_config(self):
        config = load(CONF)
        text = self.generate(CONF)
        self.assertIn('#define LAB5_CFG_HASH "%s"' % config["hash"], text)
        self.assertIn("#define LAB5_CFG_N 3", text)
        self.assertIn("#define LAB5_CFG_MAJORITY 2", text)
        self.assertIn("#define LAB5_CFG_DEFAULT_LEADER 1", text)
        self.assertIn("#define LAB5_CFG_ELECTION_MIN_MS 60", text)
        self.assertIn("#define LAB5_CFG_ELECTION_MAX_MS 99", text)
        self.assertIn("#define LAB5_CFG_DEBUG 1", text)
        self.assertIn('#define LAB5_CFG_NODE_NAMES { "cockpit", "steering", '
                      '"drivetrain" }', text)

    def test_generator_fails_on_a_bad_config(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        handle.write("N=3\n")
        handle.close()
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "gen_config.py"),
                 handle.name, str(Path(directory) / "out.h")],
                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing keys", result.stderr)


if __name__ == "__main__":
    unittest.main()
