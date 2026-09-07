import tempfile
import unittest
from pathlib import Path
import zlib

from modlab.resources.mo2_hub.cleaning_policy import cleaning_decisions


class CleaningPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'Mod.esp'; self.path.write_bytes(b'plugin version')
        self.crc = zlib.crc32(self.path.read_bytes())
        self.plugin = {'name': 'Mod.esp', 'dirty': [{'crc': self.crc, 'itm': 3, 'deletedReferences': 0,
                        'deletedNavmesh': 0, 'cleaningUtility': 'SSEEdit v4.1.5d'}]}
        self.rule = {'plugin': 'Mod.esp', 'crc': self.crc, 'decision': 'clean',
                     'source': 'https://example.org/author-instructions', 'reason': 'Author specifies Quick Auto Clean.'}

    def decide(self, rules=None):
        return cleaning_decisions({'plugins': [self.plugin]}, {'Mod.esp'}, lambda name: self.path,
                                  [self.rule] if rules is None else rules)[0]

    def test_matching_reviewed_rule_and_actual_checksum_allow_clean(self):
        self.assertEqual('clean', self.decide().action)

    def test_loot_alone_does_not_authorize_cleaning_unknown_mod(self):
        self.assertEqual('review', self.decide([]).action)

    def test_updated_file_invalidates_both_metadata_and_rule(self):
        self.path.write_bytes(b'new version')
        self.assertEqual('review', self.decide().action)

    def test_author_exception_takes_precedence_over_clean_rule(self):
        deny = dict(self.rule, decision='skip', reason='Author requires these overrides.')
        self.assertEqual('skip', self.decide([self.rule, deny]).action)

    def test_do_not_clean_message_prevents_automatic_cleaning(self):
        self.plugin['messages'] = [{'type': 'info', 'text': 'Do not clean this plugin.'}]
        self.assertEqual('skip', self.decide().action)

    def test_deleted_navmeshes_require_specific_remedy(self):
        self.plugin['dirty'][0]['deletedNavmesh'] = 1
        self.assertEqual('review', self.decide().action)

    def test_inactive_plugin_is_not_scheduled(self):
        self.assertEqual((), cleaning_decisions({'plugins': [self.plugin]}, set(), lambda name: self.path, [self.rule]))
