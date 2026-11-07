"""Protection against an unexpected 30 -> 12.50 change in FreeDoom.

Does not access Gemini; checks the same routes as the browser.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from server import store
from server.app import app
from server.scoring import compute


class NoSilentReassessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.previous_path=store.DB_PATH
        store.DB_PATH=Path(self.temp.name)/'test.sqlite'
        os.environ.setdefault('GEMINI_MODEL','gemini-3.5-flash-lite')
        store.init_db()
        self.client=TestClient(app)
        self.cid='p1-ru-001'

    def tearDown(self):
        self.client.close()
        store.DB_PATH=self.previous_path
        self.temp.cleanup()

    def test_new_checklist_must_be_approved_and_primary_is_unchanged(self):
        initial=self.client.get('/api/conversations/'+self.cid).json()
        primary=initial['active_evaluation_id']
        initial_score=next(c['final_score'] for c in self.client.get('/api/conversations').json()
                           if c['conversation_id']==self.cid)
        self.assertIsNotNone(primary)
        self.assertEqual(initial_score,100.0)
        req={'conversation_ids':[self.cid], 'checklist_id':'official-v2','mode':'ai','ensemble':1}
        # The old baseline uses v1, so v2 must NOT silently run a new model call.
        preview=self.client.post('/api/evaluations/preview',json=req)
        self.assertEqual(200,preview.status_code,preview.text)
        self.assertEqual(1,preview.json()['new_conversations'])
        self.assertEqual(1,preview.json()['new_calls'])
        with patch('server.evaluator.evaluate',side_effect=AssertionError('unexpected Gemini call')):
            blocked=self.client.post('/api/evaluations/start',json=req)
        self.assertEqual(409,blocked.status_code,blocked.text)
        self.assertEqual(1,len(self.client.get('/api/conversations/'+self.cid+'/evaluation-history').json()))
        # A deliberate, confirmed second analysis is separate from the baseline.
        def second_score(checklist, rec, **kwargs):
            old=store.get_evaluation(primary)['original_result']
            vals={r['criterion_id']:r['value'] for r in old['criteria']}
            vals['contact']=False
            new_criteria=[dict(row,value=vals[row['criterion_id']]) for row in old['criteria']]
            sc=compute(checklist,vals)
            return {'conversation_id':rec['conversation_id'],'status':'ok','criteria':new_criteria,
                    'score':{'final_score':sc['final_score'],'critical_flag':False},'_breakdown':sc}
        with patch('server.llm.api_key_configured',return_value=True), \
             patch('server.evaluator.evaluate',side_effect=second_score) as mocked:
            start=self.client.post('/api/evaluations/start',json={**req,'approve_new':True})
            self.assertEqual(202,start.status_code,start.text)
            jid=start.json()['job_id']
            for _ in range(50):
                state=self.client.get('/api/jobs/'+jid).json()
                if state['status']=='completed':break
                time.sleep(.02)
            self.assertEqual('completed',state['status'])
            self.assertEqual(1,mocked.call_count)
        history=self.client.get('/api/conversations/'+self.cid+'/evaluation-history').json()
        self.assertEqual(2,len(history))
        self.assertEqual(1,sum(bool(h['is_primary']) for h in history))
        self.assertEqual(primary,next(h['id'] for h in history if h['is_primary']))
        # Primary score is unchanged in the conversation list AND in submitted JSON.
        curr=next(c for c in self.client.get('/api/conversations').json() if c['conversation_id']==self.cid)
        self.assertEqual(initial_score,curr['final_score'])
        exported=next(r for r in self.client.get('/api/export/DEV').json()['results'] if r['conversation_id']==self.cid)
        self.assertEqual(initial_score,exported['score']['final_score'])
        # An explicit human decision selects the other history entry without erasing any data.
        other=next(h for h in history if h['id']!=primary)
        self.assertNotEqual(initial_score,other['final_score'])
        pinned=self.client.post('/api/conversations/'+self.cid+'/primary-evaluation',json={'evaluation_id':other['id']})
        self.assertEqual(200,pinned.status_code,pinned.text)
        new_curr=next(c for c in self.client.get('/api/conversations').json() if c['conversation_id']==self.cid)
        self.assertEqual(other['final_score'],new_curr['final_score'])
        self.assertEqual(2,len(self.client.get('/api/conversations/'+self.cid+'/evaluation-history').json()))

    def test_exact_existing_assessment_never_calls_gemini(self):
        req={'conversation_ids':[self.cid],'checklist_id':'default-v1','mode':'ai','ensemble':1}
        # The model and prior imported result must match for legacy reuse.
        with patch('server.llm.model_name',return_value='gemini-3.5-flash-lite'):
            preview=self.client.post('/api/evaluations/preview',json=req)
            self.assertEqual(200,preview.status_code,preview.text)
            self.assertEqual(0,preview.json()['new_conversations'])
            with patch('server.evaluator.evaluate',side_effect=AssertionError('must use saved result')):
                start=self.client.post('/api/evaluations/start',json=req)
                self.assertEqual(202,start.status_code,start.text)
                jid=start.json()['job_id']
                for _ in range(50):
                    state=self.client.get('/api/jobs/'+jid).json()
                    if state['status']=='completed':break
                    time.sleep(.02)
                self.assertEqual(1,state['reused'])
        self.assertEqual(1,len(self.client.get('/api/conversations/'+self.cid+'/evaluation-history').json()))

    def test_frontend_requires_confirmation_and_exposes_history(self):
        js=self.client.get('/static/app.js').text
        self.assertIn('/evaluations/preview',js)
        self.assertIn('approve_new:approved',js)
        self.assertIn('evaluation-history',js)
        self.assertIn('Сделать основной',js)


if __name__=='__main__':unittest.main()
