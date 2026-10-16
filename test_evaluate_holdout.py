"""Holdout preparation tests: no model or database access."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import evaluate_rag


HOLDOUT = evaluate_rag.ROOT / "eval_cases_holdout.json"


class HoldoutTests(unittest.TestCase):
    def test_valid_new_questions_and_coverage(self):
        cases = evaluate_rag.load_cases(HOLDOUT)
        original = evaluate_rag.load_cases()
        self.assertEqual(len(original), 25)
        self.assertGreaterEqual(len(cases), 20)
        self.assertFalse({c['question'] for c in cases} & {c['question'] for c in original})
        self.assertFalse({c['case_id'] for c in cases} & {c['case_id'] for c in original})
        ids = {pid for c in cases for pid in c['expected_policy_ids']}
        self.assertEqual(ids, {'hotel_001', 'hotel_002', 'hotel_003', 'meal_001',
                               'transport_001', 'leave_001', 'leave_002'})
        self.assertTrue(all(c['expected_facts'] and c['forbidden_claims'] for c in cases))

    def test_both_aliases_are_static_by_default(self):
        for option in ('--cases', '--cases-path'):
            with patch.object(evaluate_rag, 'build_service') as build, patch('sys.stdout', io.StringIO()):
                self.assertEqual(evaluate_rag.main([option, str(HOLDOUT)]), 0)
            build.assert_not_called()

    def test_holdout_does_not_bypass_run_and_path_requirements(self):
        for extra in (['--run'], ['--test-db-path', 'copy'], ['--rerank-threshold', '0.4']):
            with patch.object(evaluate_rag, 'build_service') as build, patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit):
                    evaluate_rag.main(['--cases-path', str(HOLDOUT)] + extra)
            build.assert_not_called()

    def test_report_identifies_dataset_actual_threshold_and_unique_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'knowledge_base').mkdir()
            (root / 'knowledge_base' / 'company_policy.txt').write_bytes(b'policy fixture')
            service = Mock(rerank_threshold=0.5)
            with patch.object(evaluate_rag, 'ROOT', root), patch.object(evaluate_rag, 'build_service', return_value=service) as build, patch.object(evaluate_rag, 'evaluate_cases', return_value=[]) as run, patch('sys.stdout', io.StringIO()):
                for _ in range(2):
                    self.assertEqual(evaluate_rag.main(['--cases-path', str(HOLDOUT), '--run', '--test-db-path', 'copy']), 0)
            build.assert_called_with('copy', rerank_threshold=0.5)
            self.assertEqual(len(run.call_args.args[1]), 24)
            reports = list((root / 'eval_reports').glob('*.json'))
            self.assertEqual(len(reports), 2)
            for path in reports:
                report = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(report['cases_path'], str(HOLDOUT.resolve()))
                self.assertEqual(report['rerank_threshold'], 0.5)
                self.assertEqual(len(report['cases_sha256']), 64)


if __name__ == '__main__':
    unittest.main()
