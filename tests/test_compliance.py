"""Compliance regression checks for the organizer's JSON 1.0 and workflow.
Run with: python3 -m unittest discover -s tests -v
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from server import store
from server.app import app
from server.validation import validate_value


class ComplianceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_path = store.DB_PATH
        store.DB_PATH = Path(self.temporary.name) / 'test.sqlite'
        self.client = TestClient(app)
        store.init_db()

    def tearDown(self):
        self.client.close()
        store.DB_PATH = self.previous_path
        self.temporary.cleanup()

    def test_official_checklist_disallows_contact_na(self):
        official = store.checklist_get('official-v2')
        old = store.checklist_get('default-v1')
        self.assertEqual(8, len(official['criteria']))
        self.assertFalse(official['criteria'][0]['na_allowed'])
        self.assertTrue(old['criteria'][0]['na_allowed'])
        with self.assertRaises(ValueError):
            validate_value(official['criteria'][0], None, 'voice call')

    def test_historical_evaluations_and_export_remain_available(self):
        summary = self.client.get('/api/export/DEV/summary').json()
        self.assertEqual((65, 65, 0), (summary['total'],summary['ok'],summary['errors']))
        data = self.client.get('/api/export/DEV').json()
        self.assertEqual(65,len(data['results']))
        for result in data['results']:
            self.assertEqual('ok',result['status'])
            self.assertEqual(8,len(result['criteria']))

    def test_explicit_control_group_overrides_metadata(self):
        payload = {'isSuccess': True, 'data': {'dataset_group_id': 'DEV', 'records': [{
            'conversation_id':'prod-test-001', 'channel':'agent chat', 'language':'KZ',
            'transcript':[{'timestamp':'00:00','speaker':'client','text':'Сәлем'}]
        }]}}
        resp = self.client.post('/api/conversations/import?group_id=PROD',json=payload)
        self.assertEqual(200, resp.status_code)
        self.assertEqual('PROD', resp.json()['group_id'])
        self.assertEqual(1,len(self.client.get('/api/conversations?group_id=PROD').json()))
        payload['data']['records'][0]['conversation_id']='p1-ru-001'
        resp = self.client.post('/api/conversations/import?group_id=PROD', json=payload)
        self.assertEqual(400, resp.status_code)
        self.assertEqual(65,len(self.client.get('/api/conversations?group_id=DEV').json()))

    def test_appeal_shows_original_rationale_and_evidence(self):
        ev = self.client.get('/api/evaluations?group_id=DEV').json()[0]
        payload = {'criterion_id':'contact','comment':'Проверьте приветствие'}
        appeal = self.client.post(f'/api/evaluations/{ev["id"]}/appeals', json=payload)
        self.assertEqual(201,appeal.status_code)
        aid=appeal.json()['appeal_id']
        context=self.client.get(f'/api/appeals/{aid}/context')
        self.assertEqual(200,context.status_code)
        self.assertTrue(context.json()['rationale'])
        self.assertTrue(context.json()['evidence'])
        self.assertIn('number',context.json()['evidence'][0])
        decision=self.client.post(f'/api/appeals/{aid}/decision',json={'decision':'uphold','reason':'Оригинальная оценка подтверждена'})
        self.assertEqual(200,decision.status_code)

    def test_current_ui_hides_model_and_demo_button(self):
        page=self.client.get('/').text
        script=self.client.get('/static/app.js').text
        self.assertNotIn('Экспорт в Git', page)
        self.assertIn('Результаты и выгрузка',page)
        self.assertNotIn("btn('Демо-оценка'",script)
        self.assertIn("btn('Загрузить по ссылке'",script)
        self.assertIn('Язык разговора: все', script)
        self.assertIn('Звонок: транскрипт может не содержать начало разговора', script)
        self.assertIn('id=\"knowledgeBase\"', script)


if __name__=='__main__':
    unittest.main()
