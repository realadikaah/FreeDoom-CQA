"""Offline saved-result audit and regression tests. No Gemini credentials needed."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from server import store, audit
from server.app import app


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_path = store.DB_PATH
        store.DB_PATH = Path(self.temporary.name) / 'test.sqlite'
        store.init_db()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        store.DB_PATH = self.previous_path
        self.temporary.cleanup()

    def test_audit_65_existing_scores_without_any_model_call_or_changes(self):
        with store.db() as con:
            before = con.execute('SELECT COUNT(*) AS n FROM evaluations').fetchone()['n']
            score_before = con.execute('SELECT id,final_result FROM evaluations ORDER BY id').fetchall()
            score_before = [(r['id'],r['final_result']) for r in score_before]
        with patch('server.llm.call_json', side_effect=AssertionError('The audit must not call Gemini')):
            response = self.client.post('/api/audit/run', json={'group_id': 'DEV'})
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()
        self.assertEqual(65, result['total'])
        self.assertEqual(0, result['llm_calls'])
        self.assertEqual(65, len(result['reports']))
        self.assertTrue(all(len(r['criteria_review']) == 8 for r in result['reports']))
        with store.db() as con:
            after = con.execute('SELECT COUNT(*) AS n FROM evaluations').fetchone()['n']
            score_after = [(r['id'],r['final_result']) for r in con.execute('SELECT id,final_result FROM evaluations ORDER BY id')]
        self.assertEqual(before, after)
        self.assertEqual(score_before, score_after)
        self.assertEqual((65, 65, 0), tuple(self.client.get('/api/export/DEV/summary').json()[k] for k in ('total','ok','errors')))

    def test_audit_subset_and_prod_separation(self):
        response = self.client.post('/api/audit/run', json={'group_id':'DEV','conversation_ids':['p1-ru-001','p1-ru-003']})
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, response.json()['total'])
        self.assertEqual(400, self.client.post('/api/audit/run', json={'group_id':'PROD','conversation_ids':['p1-ru-001']}).status_code)
        response = self.client.post('/api/audit/run', json={'group_id':'PROD'})
        self.assertEqual(200, response.status_code)
        self.assertEqual(0, response.json()['total'])

    def test_structural_inconsistency_and_evidence_comparison(self):
        raw = store.list_evaluations('DEV')
        e = store.get_evaluation(raw[0]['id'])
        conversation = store.get_conversation(e['conversation_id'])
        copy_e = copy.deepcopy(e)
        copy_e['final_result']['criteria'][0]['evidence_turns'] = [99999]
        copy_e['final_result']['score']['final_score'] = -1
        report = audit.audit_evaluation(copy_e, conversation)
        issues = {i['code'] for i in report['issues']}
        self.assertIn('evidence_out_of_range', issues)
        self.assertIn('score_inconsistent', issues)

    def test_detects_changed_scores_on_identical_checklist(self):
        raw = store.list_evaluations('DEV')
        e = store.get_evaluation(raw[0]['id'])
        conversation = store.get_conversation(e['conversation_id'])
        peer = copy.deepcopy(e)
        peer['id'] = 'other-evaluation'
        peer['original_result']['criteria'][0]['value'] = not bool(peer['original_result']['criteria'][0]['value'])
        report = audit.audit_evaluation(e, conversation, [peer])
        self.assertIn('history_disagreement', {x['code'] for x in report['issues']})

    def test_audit_single_endpoint_and_static_navigation(self):
        raw = store.list_evaluations('DEV')
        self.assertEqual(200, self.client.get('/api/audit/evaluations/'+raw[0]['id']).status_code)
        html = self.client.get('/').text
        script = self.client.get('/static/app.js').text
        self.assertIn('Аудит оценок', html)
        self.assertIn('score_asc', script)
        self.assertIn('score_desc', script)
        self.assertIn('Аудировать выбранные', script)
        self.assertIn('criteria_review', script)


if __name__ == '__main__':
    unittest.main()
