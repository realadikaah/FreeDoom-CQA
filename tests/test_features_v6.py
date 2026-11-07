"""Regression: new insights and coaching cannot change official ratings or bypass consent."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from server import store
from server.app import app


class ProductIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DB_PATH
        store.DB_PATH = Path(self.tmp.name) / 'quality.sqlite'
        self.client = TestClient(app)
        store.init_db()

    def tearDown(self):
        self.client.close()
        store.DB_PATH = self.previous
        self.tmp.cleanup()

    def test_root_cause_facts_and_pinned_results_unmodified(self):
        before = self.client.get('/api/export/DEV').json()
        insight = self.client.get('/api/insights/root-causes?group_id=DEV')
        self.assertEqual(200, insight.status_code)
        data = insight.json()
        self.assertEqual((65, 65, 0), (data['total'], data['scored'], data['api_calls']))
        self.assertTrue(data['patterns'])
        for group in data['patterns']:
            self.assertGreater(group['count'], 0)
            self.assertLessEqual(group['count'], group['applicable'])
            for item in group['examples']:
                rec = store.get_conversation(item['conversation_id'])
                for turn in item['evidence']:
                    self.assertEqual(rec['transcript'][turn['number']-1]['text'][:450], turn['text'])
        self.assertEqual(before, self.client.get('/api/export/DEV').json())

    def test_insights_do_not_claim_unevaluated_conversations_scored(self):
        record = {'conversation_id':'prod-new-v6', 'language':'EN', 'channel':'agent chat',
                  'transcript':[{'timestamp':'00:00','speaker':'client','text':'Hello!'}]}
        self.assertEqual(200,self.client.post('/api/conversations/import?group_id=PROD',json=[record]).status_code)
        data = self.client.get('/api/insights/root-causes?group_id=PROD').json()
        self.assertEqual((1,0,1,0), (data['total'],data['scored'],data['unscored'],len(data['patterns'])))

    def test_local_coach_zero_api_requests_and_no_score_changes(self):
        data = {'conversation_id':'p1-ru-003', 'draft':'Хорошего дня!', 'mode':'local'}
        before = self.client.get('/api/conversations/p1-ru-003/evaluation-history').json()
        with patch('server.insights.llm.call_json',side_effect=AssertionError('no API calls')):
            response = self.client.post('/api/coach/analyze',json=data)
        self.assertEqual(200,response.status_code)
        self.assertEqual('local_rules',response.json()['mode'])
        self.assertEqual(0,response.json()['calls'])
        self.assertEqual(before,self.client.get('/api/conversations/p1-ru-003/evaluation-history').json())

    def test_gemini_requires_explicit_consent_and_key(self):
        data = {'conversation_id':'p1-ru-003','draft':'Вам необходимо обратиться в поддержку.', 'mode':'ai'}
        self.assertEqual(400,self.client.post('/api/coach/analyze',json=data).status_code)
        with patch('server.app.llm.api_key_configured',return_value=False):
            self.assertEqual(503,self.client.post('/api/coach/analyze',json={**data,'acknowledged_external':True}).status_code)

    def test_gemini_response_validation_and_original_export_unchanged(self):
        before = self.client.get('/api/export/DEV').json()
        payload={'risk_level':'high','summary':'Требует проверки','issues':[{
            'severity':'high','title':'Проверьте идентификацию',
            'explanation':'Перед персональными данными нужна проверка','criterion_id':'identification',
            'evidence_turns':[1, 100000, -2, '2']}],
            'suggested_reply':'Я уточню порядок проверки личности.'}
        req={'conversation_id':'p1-ru-003','draft':'Сведения по вашему счёту: ...',
             'mode':'ai','acknowledged_external':True}
        with patch('server.app.llm.api_key_configured',return_value=True), \
             patch('server.app.llm.model_name',return_value='gemini-3.5-flash-lite'), \
             patch('server.insights.llm.call_json',return_value=payload):
            response=self.client.post('/api/coach/analyze',json=req)
        self.assertEqual(200,response.status_code)
        self.assertEqual('ai',response.json()['mode'])
        self.assertEqual([1],response.json()['issues'][0]['evidence_turns'])
        self.assertEqual(before,self.client.get('/api/export/DEV').json())

if __name__=='__main__':unittest.main()
