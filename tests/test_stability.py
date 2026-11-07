"""Offline sanity checks. Run: python3 -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from server import store, evaluator

class RepeatabilityTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.prev_db=store.DB_PATH
        store.DB_PATH=Path(self.folder.name)/'test.sqlite'
        os.environ.setdefault('GEMINI_MODEL','gemini-3.5-flash-lite')
        store.init_db()

    def tearDown(self):
        store.DB_PATH=self.prev_db
        self.folder.cleanup()

    def test_imports_65_dev_dialogues(self):
        self.assertEqual(65,len(store.list_conversations('DEV')))

    def test_same_input_same_fingerprint(self):
        record=store.get_conversation('p1-ru-001')
        checklist=store.checklist_get('default-v1')
        x=evaluator.cache_key(checklist,record,ensemble=1)
        self.assertEqual(x,evaluator.cache_key(checklist,record,ensemble=1))
        self.assertNotEqual(x,evaluator.cache_key(checklist,record,ensemble=3))
        rec2=dict(record)
        rec2['transcript']=list(rec2['transcript'])
        rec2['transcript'][0]=dict(rec2['transcript'][0],text='Другой текст')
        self.assertNotEqual(x,evaluator.cache_key(checklist,rec2,ensemble=1))

    def test_no_invalid_scale_values_or_fake_evidence(self):
        record=store.get_conversation('p1-ru-001')
        cl=store.checklist_get('default-v1')
        raw={'criteria':[{'criterion_id':c['id'], 'value':(
            17 if c['id']=='consultation' else 10 if c['id']=='dialogue'
            else False if c['type']=='flag' else None if c.get('na_allowed')
            else True), 'rationale':'Проверка', 'evidence_turns':[1]}
            for c in cl['criteria']]}
        with self.assertRaises(ValueError):
            evaluator.normalize(cl,raw,len(record['transcript']),record['channel'])

if __name__=='__main__': unittest.main()
