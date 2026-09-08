import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from modlab.resources.mo2_hub.assessment import Plugin
from modlab.resources.mo2_hub import winning_records as wr


class WinningRecordsTests(unittest.TestCase):
    def test_complete_order_includes_later_patch_and_rejects_wrong_master_order(self):
        plugins = [Plugin('Skyrim.esm', 0), Plugin('A.esp', 1, ('Skyrim.esm',)),
                   Plugin('Fix.esp', 2, ('A.esp',)), Plugin('Disabled.esp', -1)]
        self.assertEqual(('Skyrim.esm', 'A.esp', 'Fix.esp'), wr.active_order(plugins))
        with self.assertRaisesRegex(ValueError, 'order'):
            wr.active_order([Plugin('A.esp', 0, ('Fix.esp',)), Plugin('Fix.esp', 1)])

    def report(self):
        return ('MODLAB-WINNERS-1\tjob\nL\tSkyrim.esm\nL\tA.esp\nL\tFix.esp\n'
                'P\tA.esp\t3\t2\t0\nP\tFix.esp\t2\t0\t1\n'
                'E\tFix.esp\t01000800\tBroken recipe\tItems\\Item\tWrong reference type\nEND\tjob\n')

    def test_report_proves_exact_loaded_context_and_target_coverage(self):
        parsed = wr.parse_report(self.report(), 'job', ('Skyrim.esm','A.esp','Fix.esp'), ('A.esp','Fix.esp'))
        self.assertEqual(2, parsed['A.esp']['superseded'])
        self.assertEqual(1, parsed['Fix.esp']['errors'])
        self.assertIn('Broken recipe', parsed['Fix.esp']['details'])
        for bad in (self.report().replace('END\tjob','END\tother'),
                    self.report().replace('L\tFix.esp\n',''),
                    self.report().replace('P\tA.esp\t3\t2\t0\n',''),
                    self.report().replace('01000800','not a form'),
                    self.report().replace('P\tFix.esp\t2\t0\t1','P\tFix.esp\t2\t0\t0')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                wr.parse_report(bad, 'job', ('Skyrim.esm','A.esp','Fix.esp'), ('A.esp','Fix.esp'))

    def test_later_patch_changes_invalidate_the_result_for_whole_setup(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); plugins=[Plugin('A.esp',0),Plugin('Fix.esp',1)]
            for p in plugins: (root/p.name).write_bytes(b'first')
            old=wr.signature(plugins,lambda n:root/n,{})
            (root/'Fix.esp').write_bytes(b'repair changed')
            self.assertNotEqual(old,wr.signature(plugins,lambda n:root/n,{}))

    def test_successful_cache_rechecks_report_hash_and_context(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); plugins=[Plugin('A.esp',0)]
            (root/'A.esp').write_bytes(b'plugin'); job=root/'job';job.mkdir()
            report='MODLAB-WINNERS-1\tjob\nL\tA.esp\nP\tA.esp\t2\t0\t0\nEND\tjob\n'
            (job/'winning-records.tsv').write_text(report)
            (job/'tool.log').write_text('completed')
            calls=[]
            def run():
                calls.append(1)
                return job, {'winning_records':wr.parse_report(report,'job',('A.esp',),('A.esp',))},None,None
            cache=root/'cache.json';resolve=lambda n:root/n
            wr.check_setup(plugins,resolve,{},cache,run)
            findings,_=wr.check_setup(plugins,resolve,{},cache,run)
            self.assertEqual(1,len(calls))
            self.assertEqual(['record-checks-current'],[f.code for f in findings])
            (job/'winning-records.tsv').write_text(report+'tampered')
            self.assertEqual('record-checks-pending',wr.inspect_setup(plugins,resolve,{},cache)[0].code)

    def test_error_findings_name_winning_provider_and_dont_claim_cleaning(self):
        parsed=wr.parse_report(self.report(),'job',('Skyrim.esm','A.esp','Fix.esp'),('A.esp','Fix.esp'))
        findings=wr.findings(parsed,[Plugin('A.esp',1,(), 'Original'),Plugin('Fix.esp',2,(), 'Repair mod')],Path('job'))
        issue=next(f for f in findings if f.code=='record-errors')
        self.assertIn('Repair mod',issue.title)
        self.assertIn('final active load order',issue.explanation)
        self.assertIn('not a repair',issue.action)


if __name__=='__main__':unittest.main()
