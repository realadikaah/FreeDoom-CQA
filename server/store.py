"""Persistent CQA storage: SQLite, immutable checklist revisions, audit-friendly reviews."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv('CQA_DATABASE', str(ROOT / 'data' / 'cqa.sqlite')))
DEFAULT_CHECKLIST_PATH = ROOT / 'server' / 'checklist_default.json'
LEGACY_CHECKLIST_PATH = ROOT / 'server' / 'checklist_legacy_v1.json'


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def uid():
    return str(uuid.uuid4())


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=30000')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''
        CREATE TABLE IF NOT EXISTS checklists (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL,
          config TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversations (
          id TEXT PRIMARY KEY, group_id TEXT NOT NULL, channel TEXT NOT NULL,
          language TEXT NOT NULL, transcript TEXT NOT NULL, audio_url TEXT,
          imported_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_conversations_group ON conversations(group_id);
        CREATE TABLE IF NOT EXISTS evaluations (
          id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
          checklist_id TEXT NOT NULL REFERENCES checklists(id),
          checklist_snapshot TEXT NOT NULL, model TEXT NOT NULL, mode TEXT NOT NULL,
          status TEXT NOT NULL, original_result TEXT, final_result TEXT,
          error TEXT, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_evaluation_conversation ON evaluations(conversation_id,created_at);
        CREATE TABLE IF NOT EXISTS appeals (
          id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
          criterion_id TEXT NOT NULL, comment TEXT NOT NULL,
          original_value TEXT NOT NULL, status TEXT NOT NULL,
          decision TEXT, final_value TEXT, reviewer_reason TEXT,
          created_at TEXT NOT NULL, decided_at TEXT);
        CREATE INDEX IF NOT EXISTS ix_appeals_status ON appeals(status);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, status TEXT NOT NULL, total INTEGER NOT NULL,
          completed INTEGER NOT NULL, errors INTEGER NOT NULL, item_ids TEXT NOT NULL,
          evaluation_ids TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT);
        ''')
        # Safe schema upgrades for databases created by earlier FreeDoom versions.
        cols = {r['name'] for r in con.execute('PRAGMA table_info(evaluations)')}
        if 'fingerprint' not in cols:
            con.execute('ALTER TABLE evaluations ADD COLUMN fingerprint TEXT')
        con.execute('CREATE INDEX IF NOT EXISTS ix_evaluations_fingerprint ON evaluations(fingerprint)')
        convo_cols = {r['name'] for r in con.execute('PRAGMA table_info(conversations)')}
        if 'active_evaluation_id' not in convo_cols:
            con.execute('ALTER TABLE conversations ADD COLUMN active_evaluation_id TEXT')
        cols = {r['name'] for r in con.execute('PRAGMA table_info(jobs)')}
        if 'reused' not in cols:
            con.execute('ALTER TABLE jobs ADD COLUMN reused INTEGER NOT NULL DEFAULT 0')
        legacy = json.loads(LEGACY_CHECKLIST_PATH.read_text(encoding='utf-8'))
        official = json.loads(DEFAULT_CHECKLIST_PATH.read_text(encoding='utf-8'))
        # Keep v1 immutable: historic Gemini scores and appeals rely on its exact rules.
        con.execute('INSERT OR IGNORE INTO checklists VALUES (?,?,?,?,?)',
                    ('default-v1', legacy['name'], 1, json.dumps(legacy, ensure_ascii=False), utcnow()))
        # New official config is independently selectable for all new assessments.
        con.execute('INSERT OR IGNORE INTO checklists VALUES (?,?,?,?,?)',
                    ('official-v2', official['name'], 2, json.dumps(official, ensure_ascii=False), utcnow()))
    # DEV data is provided by the organizers; don't touch it if it has already been imported.
    dev = ROOT / 'data' / 'dev.json'
    if dev.is_file():
        import_records(json.loads(dev.read_text(encoding='utf-8-sig')), 'DEV')
    previous = ROOT / 'data' / 'previous_results.json'
    if previous.is_file():
        import_previous_results(previous)
    # Keep historical evaluations and all appeals: pin the first genuine result
    # only when the user has not previously chosen a baseline.
    pin_missing_baselines()


def pin_missing_baselines():
    """Migration: prefer the earliest successful real AI score as a stable baseline."""
    with db() as con:
        con.execute('''UPDATE conversations SET active_evaluation_id = (
            SELECT e.id FROM evaluations e WHERE e.conversation_id=conversations.id
            AND e.status='ok' AND e.mode IN ('ai','imported_ai')
            ORDER BY e.created_at ASC,e.rowid ASC LIMIT 1
        ) WHERE active_evaluation_id IS NULL''')


def list_conversation_evaluations(cid):
    with db() as con:
        rows = con.execute('''SELECT e.id,e.conversation_id,e.checklist_id,e.model,e.mode,
            e.status,e.created_at,e.fingerprint,
            json_extract(e.final_result,'$.score.final_score') AS final_score,
            json_extract(e.original_result,'$.score.final_score') AS original_score,
            CASE WHEN c.active_evaluation_id=e.id THEN 1 ELSE 0 END AS is_primary
            FROM evaluations e JOIN conversations c ON c.id=e.conversation_id
            WHERE e.conversation_id=? ORDER BY e.created_at DESC,e.rowid DESC''',(cid,)).fetchall()
    return [dict(r) for r in rows]


def set_primary_evaluation(cid, eid):
    """Explicit human selection; scores and evidence are not rewritten."""
    with db() as con:
        row = con.execute('''SELECT id FROM evaluations
            WHERE id=? AND conversation_id=? AND status='ok'
            AND mode IN ('ai','imported_ai')''',(eid,cid)).fetchone()
        if not row:
            raise ValueError('Закрепить можно только успешную настоящую ИИ-оценку этого диалога')
        con.execute('UPDATE conversations SET active_evaluation_id=? WHERE id=?',(eid,cid))
    return {'conversation_id':cid,'active_evaluation_id':eid}



def import_records(payload, group_id='DEV'):
    if isinstance(payload, dict):
        if payload.get('isSuccess') is False:
            raise ValueError('API организаторов вернул isSuccess=false')
        data = payload.get('data', payload)
        # Explicit group selected in the UI is authoritative. An API response can
        # advertise DEV even when imported into a separate PROD workspace.
        records = data.get('records', [])
    elif isinstance(payload, list):
        records = payload
    else:
        raise ValueError('Ожидался JSON с data.records или массив диалогов')
    if not isinstance(records, list) or not records:
        raise ValueError('В наборе отсутствуют диалоги')
    if len(records) > 5000:
        raise ValueError('Слишком большой набор: максимум 5000 диалогов')
    if not isinstance(group_id, str) or group_id not in ('DEV', 'PROD'):
        raise ValueError('Некорректный group_id')
    ids = set()
    clean = []
    for r in records:
        if not isinstance(r, dict) or not isinstance(r.get('transcript'), list):
            raise ValueError('Некорректный transcript')
        cid = r.get('conversation_id')
        if not isinstance(cid, str) or not (1 <= len(cid) <= 200) or cid in ids:
            raise ValueError(f'Пустой/повторяющийся conversation_id: {cid}')
        ids.add(cid)
        if any(not isinstance(t, dict) or not all(isinstance(t.get(k), str) for k in ('timestamp', 'speaker', 'text')) for t in r['transcript']):
            raise ValueError(f'Неверный формат реплик: {cid}')
        if len(r['transcript']) > 3000:
            raise ValueError(f'Очень длинный диалог: {cid}')
        clean.append((cid, group_id, r.get('channel', 'agent chat'), r.get('language', 'RU'),
                      json.dumps(r['transcript'], ensure_ascii=False), r.get('audio_url'), utcnow()))
    with db() as con:
        # Prevent overwriting transcripts that already have scored evaluations.
        updated = 0
        for row in clean:
            old = con.execute('SELECT transcript,group_id FROM conversations WHERE id=?', (row[0],)).fetchone()
            if old and old['group_id'] != group_id:
                raise ValueError(f'ID {row[0]} уже находится в наборе {old["group_id"]}. Импорт отменён.')
            scored = con.execute('SELECT 1 FROM evaluations WHERE conversation_id=? LIMIT 1', (row[0],)).fetchone()
            if old and scored and old['transcript'] != row[4]:
                raise ValueError(f'Диалог {row[0]} уже оценён. Нельзя изменить исходный транскрипт.')
            con.execute('''INSERT INTO conversations (id,group_id,channel,language,transcript,audio_url,imported_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET group_id=excluded.group_id,
                channel=excluded.channel,language=excluded.language,transcript=excluded.transcript,
                audio_url=excluded.audio_url''', row)
            updated += 1
    return {'imported': updated, 'group_id': group_id}


def import_previous_results(filename):
    """One-time import of genuine previously computed results.

    Distinct mode=imported_ai keeps provenance explicit. Never makes Gemini API calls.
    Refuses corrupted/incomplete rows rather than assigning fabricated ratings.
    """
    from .validation import validate_value
    from .scoring import compute
    source = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
    if source.get('model',{}).get('mode') != 'external_api':
        return {'imported':0,'reason':'Results were not marked external_api'}
    model = source.get('model',{}).get('name','').strip()
    if not model: return {'imported':0,'reason':'Model not specified'}
    results = source.get('results',[])
    checklist = json.loads(LEGACY_CHECKLIST_PATH.read_text(encoding='utf-8'))
    ids, valid = set(), []
    with db() as con:
        for r in results:
            cid = r.get('conversation_id')
            if not cid or cid in ids: raise ValueError(f'Duplicate/empty previous result ID: {cid}')
            ids.add(cid)
            row = con.execute('SELECT id,channel,transcript FROM conversations WHERE id=?',(cid,)).fetchone()
            if not row: raise ValueError(f'Unknown historical result: {cid}')
            if r.get('status')!='ok': continue
            crit=r.get('criteria',[])
            if len(crit)!=len(checklist['criteria']) or [x.get('criterion_id') for x in crit] != [x['id'] for x in checklist['criteria']]:
                raise ValueError(f'Bad historical criteria {cid}')
            values={}
            nturns=len(json.loads(row['transcript']))
            for c,answer in zip(checklist['criteria'],crit):
                validate_value(c, answer['value'], row['channel'])
                turns=answer.get('evidence_turns',[])
                if not isinstance(turns,list) or not turns or any(type(x)!=int or x<1 or x>nturns for x in turns):
                    raise ValueError(f'Bad evidence {cid}:{c["id"]}')
                values[c['id']] = answer['value']
            calc=compute(checklist,values)['final_score']
            if calc != r['score']['final_score'] or bool(values.get('critical')) != r['score']['critical_flag']:
                raise ValueError(f'Wrong historical scoring {cid}')
            valid.append((cid,r))
        for cid,r in valid:
            exists = con.execute('SELECT 1 FROM evaluations WHERE conversation_id=? AND status=? LIMIT 1', (cid,'ok')).fetchone()
            if exists: continue
            ident=uid()
            con.execute("""INSERT INTO evaluations (id,conversation_id,checklist_id,checklist_snapshot,model,mode,status,original_result,final_result,error,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (ident,cid,'default-v1',json.dumps(checklist,ensure_ascii=False),model,'imported_ai', 'ok',
                 json.dumps(r,ensure_ascii=False),json.dumps(r,ensure_ascii=False),None,utcnow()))
    return {'imported':len(valid),'source':'previous_results.json'}

def checklist_list():
    with db() as con:
        rows = con.execute('SELECT * FROM checklists ORDER BY created_at DESC, version DESC').fetchall()
    return [dict(id=r['id'], name=r['name'], version=r['version'], created_at=r['created_at']) for r in rows]


def checklist_get(checklist_id):
    with db() as con:
        row = con.execute('SELECT * FROM checklists WHERE id=?', (checklist_id,)).fetchone()
    if not row:
        return None
    return {**json.loads(row['config']), 'id': row['id'], 'version': row['version'], 'created_at': row['created_at']}


def checklist_save(cfg, parent_id=None):
    from .validation import validate_checklist
    clean = validate_checklist(cfg)
    with db() as con:
        parent = con.execute('SELECT version FROM checklists WHERE id=?', (parent_id,)).fetchone() if parent_id else None
        version = parent['version'] + 1 if parent else 1
        clean['version'] = version
        ident = ('revision-' if parent else 'custom-') + uid()[:12]
        con.execute('INSERT INTO checklists VALUES (?,?,?,?,?)',
                    (ident, clean['name'], version, json.dumps(clean, ensure_ascii=False), utcnow()))
    return checklist_get(ident)


def list_conversations(group_id=None, language=None, channel=None, search=None, limit=2000):
    where, args = [], []
    if group_id:
        where.append('c.group_id=?'); args.append(group_id)
    if language:
        where.append('c.language=?'); args.append(language)
    if channel:
        where.append('c.channel=?'); args.append(channel)
    if search:
        where.append('c.id LIKE ?'); args.append('%'+search+'%')
    sql = '''SELECT c.id AS conversation_id,c.group_id,c.channel,c.language,c.audio_url,
        json_array_length(c.transcript) AS turns,
        c.active_evaluation_id AS evaluation_id,
        json_extract(e.final_result,'$.score.final_score') AS final_score
        FROM conversations c LEFT JOIN evaluations e ON e.id=c.active_evaluation_id'''
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY c.group_id,c.id LIMIT ?'
    args.append(limit)
    with db() as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def get_conversation(cid):
    with db() as con:
        r = con.execute('SELECT * FROM conversations WHERE id=?', (cid,)).fetchone()
    return (dict(conversation_id=r['id'],group_id=r['group_id'],channel=r['channel'], language=r['language'],
                 transcript=json.loads(r['transcript']),audio_url=r['audio_url'],
                 active_evaluation_id=r['active_evaluation_id'])) if r else None


def save_evaluation(cid, checklist_id, cl, model, mode, result, fingerprint=None):
    ident = uid()
    with db() as con:
        con.execute('''INSERT INTO evaluations
            (id,conversation_id,checklist_id,checklist_snapshot,model,mode,status,
             original_result,final_result,error,created_at,fingerprint)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (ident, cid, checklist_id, json.dumps(cl,ensure_ascii=False), model, mode, result['status'],
             json.dumps(result,ensure_ascii=False), json.dumps(result,ensure_ascii=False),
             result.get('error'), utcnow(), fingerprint))
        if result.get('status') == 'ok' and mode in ('ai','imported_ai'):
            con.execute('''UPDATE conversations SET active_evaluation_id=?
                           WHERE id=? AND active_evaluation_id IS NULL''',(ident,cid))
    return ident


def find_matching_evaluation(cid, checklist_id, cl, model, mode, fingerprint, legacy=False):
    """Find a successful prior result, never a failed or mock AI result.

    Legacy imported rows have no fingerprint; allow reuse only for the supplied
    default rubric, same model and single-shot, zero-temperature generation.
    The original result remains immutable; appeals stay in final_result.
    """
    with db() as con:
        rows = con.execute("""SELECT id,checklist_snapshot,model,mode,fingerprint
            FROM evaluations WHERE conversation_id=? AND checklist_id=? AND status='ok'
            ORDER BY created_at DESC, rowid DESC""",(cid, checklist_id)).fetchall()
    for r in rows:
        if r['model'] != model or r['mode'] != mode:
            # Historical actual Gemini evaluations have a distinct provenance tag.
            if not (legacy and mode == 'ai' and r['mode'] == 'imported_ai'):
                continue
        if r['fingerprint'] == fingerprint:
            return r['id']
    if not legacy or mode != 'ai' or checklist_id != 'default-v1':
        return None
    def canonical(value):
        return {k:v for k,v in value.items() if k not in ('id','created_at','version')}
    target = canonical(cl)
    for r in rows:
        if r['fingerprint'] is not None or r['model'] != model or r['mode'] not in ('ai','imported_ai'):
            continue
        if canonical(json.loads(r['checklist_snapshot'])) == target:
            # Adopt the existing assessment as the baseline, preserving appeal history.
            with db() as con:
                con.execute('UPDATE evaluations SET fingerprint=? WHERE id=? AND fingerprint IS NULL', (fingerprint,r['id']))
            return r['id']
    return None


def list_evaluations(group_id=None):
    sql = '''SELECT e.id,e.conversation_id,e.checklist_id,e.model,e.mode,e.status,e.created_at,
             CASE WHEN c.active_evaluation_id=e.id THEN 1 ELSE 0 END AS is_primary,
             json_extract(e.final_result,'$.score.final_score') AS final_score,
             (SELECT COUNT(*) FROM appeals a WHERE a.evaluation_id=e.id AND a.status='pending') AS pending_appeals
             FROM evaluations e JOIN conversations c ON e.conversation_id=c.id'''
    args = []
    if group_id: sql += ' WHERE c.group_id=?'; args.append(group_id)
    sql += ' ORDER BY e.created_at DESC,e.rowid DESC LIMIT 3000'
    with db() as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def get_evaluation(ident):
    with db() as con:
        r = con.execute('SELECT * FROM evaluations WHERE id=?', (ident,)).fetchone()
        appeals = con.execute('SELECT * FROM appeals WHERE evaluation_id=? ORDER BY created_at', (ident,)).fetchall()
    if not r: return None
    data = dict(r)
    for k in ('checklist_snapshot','original_result','final_result'):
        data[k] = json.loads(data[k]) if data[k] else None
    data['appeals'] = []
    for a in appeals:
        d = dict(a)
        for k in ('original_value','final_value'):
            d[k] = json.loads(d[k]) if d[k] is not None else None
        data['appeals'].append(d)
    return data


def create_appeal(evaluation_id, criterion_id, comment):
    e = get_evaluation(evaluation_id)
    if not e: raise ValueError('Оценка не найдена')
    if e['status'] != 'ok': raise ValueError('Нельзя оспорить неуспешную оценку')
    found = next((c for c in e['original_result']['criteria'] if c['criterion_id'] == criterion_id), None)
    if not found: raise ValueError('Критерий отсутствует в оценке')
    if not comment or not comment.strip(): raise ValueError('Укажите причину апелляции')
    if len(comment) > 3000: raise ValueError('Комментарий слишком длинный')
    with db() as con:
        pending = con.execute('SELECT 1 FROM appeals WHERE evaluation_id=? AND criterion_id=? AND status=?',
                              (evaluation_id, criterion_id, 'pending')).fetchone()
        if pending: raise ValueError('Уже есть открытая апелляция по этому критерию')
        ident = uid()
        con.execute('''INSERT INTO appeals(id,evaluation_id,criterion_id,comment,original_value,status,created_at)
                       VALUES(?,?,?,?,?,?,?)''',
                    (ident,evaluation_id,criterion_id,comment.strip(),json.dumps(found['value']), 'pending', utcnow()))
    return ident


def list_appeals(status=None):
    sql = '''SELECT a.*,e.conversation_id,e.model,e.mode,e.status AS evaluation_status
             FROM appeals a JOIN evaluations e ON a.evaluation_id=e.id'''
    args = []
    if status: sql += ' WHERE a.status=?'; args.append(status)
    sql += ' ORDER BY a.created_at DESC,a.rowid DESC LIMIT 1000'
    with db() as con:
        rows = [dict(r) for r in con.execute(sql, args).fetchall()]
    for row in rows:
        for k in ('original_value','final_value'):
            row[k] = json.loads(row[k]) if row[k] is not None else None
    return rows


def appeal_context(aid):
    """Provide original AI rationale and precisely numbered evidence for review."""
    with db() as con:
        row = con.execute('''SELECT a.*,e.conversation_id,e.original_result,e.checklist_snapshot,
                            c.transcript,c.audio_url,c.channel
                            FROM appeals a
                            JOIN evaluations e ON a.evaluation_id=e.id
                            JOIN conversations c ON e.conversation_id=c.id
                            WHERE a.id=?''', (aid,)).fetchone()
    if row is None:
        return None
    original = json.loads(row['original_result'])
    matched = next((x for x in original.get('criteria', [])
                    if x.get('criterion_id') == row['criterion_id']), None)
    if matched is None:
        return None
    rubric = json.loads(row['checklist_snapshot'])
    criterion = next((x for x in rubric['criteria'] if x['id'] == row['criterion_id']), None)
    transcript = json.loads(row['transcript'])
    evidence = []
    for n in matched.get('evidence_turns', []):
        if type(n) is int and 1 <= n <= len(transcript):
            evidence.append({'number': n, **transcript[n - 1]})
    return {'appeal_id': aid, 'conversation_id': row['conversation_id'],
            'criterion_id': row['criterion_id'], 'criterion_title': criterion['title'] if criterion else row['criterion_id'],
            'original_value': matched['value'], 'rationale': matched.get('rationale', ''),
            'evidence': evidence, 'comment': row['comment'], 'audio_url': row['audio_url'],
            'channel': row['channel']}

def decide_appeal(aid, decision, value, reason):
    from .validation import validate_value
    from .scoring import compute
    if decision not in ('uphold','change'): raise ValueError('Нужно выбрать uphold или change')
    if not reason or not reason.strip(): raise ValueError('Для решения нужно пояснение')
    with db() as con:
        appeal = con.execute('SELECT * FROM appeals WHERE id=?', (aid,)).fetchone()
        if not appeal: raise ValueError('Апелляция не найдена')
        if appeal['status'] != 'pending': raise ValueError('Апелляция уже рассмотрена')
        e = con.execute('SELECT * FROM evaluations WHERE id=?', (appeal['evaluation_id'],)).fetchone()
        cl = json.loads(e['checklist_snapshot'])
        criterion = next((c for c in cl['criteria'] if c['id']==appeal['criterion_id']), None)
        if not criterion: raise ValueError('Критерий отсутствует')
        result = json.loads(e['final_result'])
        current = next(c for c in result['criteria'] if c['criterion_id']==appeal['criterion_id'])
        if decision == 'change':
            validate_value(criterion, value, con.execute('SELECT channel FROM conversations WHERE id=?', (e['conversation_id'],)).fetchone()['channel'])
            current['value'] = value
            current['rationale'] = f"Решение проверяющего: {reason.strip()}"
        else:
            value = current['value']
        values = {c['criterion_id']: c['value'] for c in result['criteria']}
        breakdown = compute(cl, values)
        result['score'] = {'final_score':breakdown['final_score'], 'critical_flag':bool(values.get('critical'))}
        result['_breakdown'] = breakdown
        con.execute('UPDATE appeals SET status=?, decision=?,final_value=?,reviewer_reason=?,decided_at=? WHERE id=?',
                    ('resolved',decision,json.dumps(value),reason.strip(),utcnow(),aid))
        con.execute('UPDATE evaluations SET final_result=? WHERE id=?', (json.dumps(result,ensure_ascii=False), e['id']))
    return get_evaluation(e['id'])


def create_job(ids):
    jid = uid()
    with db() as con:
        con.execute('''INSERT INTO jobs
                    (id,status,total,completed,errors,item_ids,evaluation_ids,created_at,finished_at,reused)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (jid,'running',len(ids),0,0,json.dumps(ids),json.dumps([]),utcnow(),None,0))
    return jid


def update_job(jid, evaluation_id=None, error=False, finished=False, reused=False):
    with db() as con:
        r = con.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not r: return
        eids = json.loads(r['evaluation_ids'])
        if evaluation_id: eids.append(evaluation_id)
        completed = r['completed'] + (1 if evaluation_id else 0)
        con.execute('''UPDATE jobs SET completed=?, errors=?, reused=?, evaluation_ids=?, status=?, finished_at=? WHERE id=?''',
                    (completed,r['errors']+int(error),r['reused']+int(reused),json.dumps(eids),
                     'completed' if finished else 'running',utcnow() if finished else None,jid))


def get_job(jid):
    with db() as con:
        r = con.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    if not r: return None
    return dict(**{k:r[k] for k in r.keys() if k not in ('item_ids','evaluation_ids')},
                item_ids=json.loads(r['item_ids']),evaluation_ids=json.loads(r['evaluation_ids']))
