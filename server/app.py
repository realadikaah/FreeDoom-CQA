"""FastAPI HTTP server for the Freedom Broker Conversation Quality Analytics hackathon."""
import json
import os
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Union

from fastapi import FastAPI, HTTPException, Body, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import evaluator, llm, store, audit, insights

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
app = FastAPI(title='FreeDoom CQA API',version='1.0.0',description='Freedom Broker chat quality analytics')
executor = ThreadPoolExecutor(max_workers=3)
_fingerprint_guard = threading.Lock()
_fingerprint_locks = {}


def evaluation_lock(fingerprint):
    with _fingerprint_guard:
        return _fingerprint_locks.setdefault(fingerprint, threading.Lock())


@app.on_event('startup')
def startup():
    store.init_db()


def bad(e):
    raise HTTPException(400,str(e))


@app.get('/api/health')
def health():
    return {'status':'ok','model':llm.model_name(),'ai_configured':llm.api_key_configured(),
            'ensemble':int(os.getenv('ENSEMBLE','1')),'team':os.getenv('TEAM_NAME','FreeDoom'),
            'demo_mode':os.getenv('MOCK')=='1'}


@app.get('/api/insights/root-causes')
def root_causes(group_id: str = 'DEV'):
    try:
        return insights.root_cause_report(group_id.upper())
    except ValueError as e:
        bad(e)


class CoachRequest(BaseModel):
    conversation_id: str
    draft: str = Field(min_length=1, max_length=3000)
    mode: str = 'local'  # local rules, or explicit external AI
    acknowledged_external: bool = False


@app.post('/api/coach/analyze')
def coach_analyze(req: CoachRequest):
    if req.mode not in ('local', 'ai'):
        raise HTTPException(400, 'Режим должен быть local или ai')
    record = store.get_conversation(req.conversation_id)
    if not record:
        raise HTTPException(404, 'Диалог не найден')
    if req.mode == 'local':
        try:
            return insights.preview_draft(record, req.draft)
        except ValueError as e:
            bad(e)
    if not req.acknowledged_external:
        raise HTTPException(400, 'Подтвердите передачу транскрипта и черновика внешнему провайдеру Gemini')
    if not llm.api_key_configured() or os.getenv('MOCK') == '1':
        raise HTTPException(503, 'Для AI Coach настройте настоящий Gemini API-ключ в .env')
    cl = store.checklist_get('official-v2')
    try:
        return insights.ai_draft(record, req.draft, cl)
    except (llm.LLMError, ValueError, RuntimeError) as e:
        raise HTTPException(502, 'Не удалось выполнить анализ Gemini: ' + str(e)[:180])


@app.get('/api/checklists')
def checklists():
    return store.checklist_list()


@app.get('/api/checklists/{cid}')
def checklist(cid:str):
    obj = store.checklist_get(cid)
    if not obj: raise HTTPException(404,'Чек-лист не найден')
    return obj


@app.post('/api/checklists')
def create_checklist(payload:dict=Body(...)):
    try: return store.checklist_save(payload)
    except ValueError as e: bad(e)


@app.put('/api/checklists/{cid}')
def new_revision(cid:str,payload:dict=Body(...)):
    if not store.checklist_get(cid): raise HTTPException(404,'Чек-лист не найден')
    try: return store.checklist_save(payload,parent_id=cid)
    except ValueError as e: bad(e)


@app.get('/api/conversations')
def conversations(group_id:Optional[str]=None,language:Optional[str]=None,channel:Optional[str]=None,q:Optional[str]=None):
    return store.list_conversations(group_id,language,channel,q)


@app.get('/api/groups')
def groups():
    with store.db() as con:
        return [dict(r) for r in con.execute('SELECT group_id,COUNT(*) AS count FROM conversations GROUP BY group_id ORDER BY group_id')]


@app.get('/api/conversations/{cid}')
def conversation(cid:str):
    r=store.get_conversation(cid)
    if not r: raise HTTPException(404,'Диалог не найден')
    return r


@app.get('/api/conversations/{cid}/evaluation-history')
def conversation_evaluation_history(cid:str):
    if not store.get_conversation(cid): raise HTTPException(404,'Диалог не найден')
    return store.list_conversation_evaluations(cid)


class PrimaryEvaluationRequest(BaseModel):
    evaluation_id:str


@app.post('/api/conversations/{cid}/primary-evaluation')
def set_primary_evaluation(cid:str,req:PrimaryEvaluationRequest):
    try: return store.set_primary_evaluation(cid,req.evaluation_id)
    except ValueError as e: bad(e)


@app.post('/api/conversations/import')
def import_json(payload:Union[dict,list]=Body(...),group_id:str='DEV'):
    try: return store.import_records(payload,group_id)
    except ValueError as e: bad(e)


class UrlImport(BaseModel):
    url:str
    group_id:str='PROD'


@app.post('/api/conversations/import-url')
def import_url(req:UrlImport):
    """Server-side CORS workaround for organizer Power Platform HTTPS endpoint."""
    parsed=urllib.parse.urlsplit(req.url)
    host=(parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not (host.endswith('.powerplatform.com') or host.endswith('.powerautomate.com')):
        raise HTTPException(400,'Для безопасности URL должен быть HTTPS на домене Power Platform')
    try:
        request=urllib.request.Request(req.url,headers={'Accept':'application/json'})
        # No redirects to different hosts; prevent redirect SSRF.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        opener=urllib.request.build_opener(NoRedirect)
        with opener.open(request,timeout=45) as response:
            raw=response.read(15_000_001)
        if len(raw)>15_000_000: raise ValueError('JSON больше 15 МБ')
        return store.import_records(json.loads(raw),req.group_id)
    except (ValueError,urllib.error.URLError,TimeoutError) as e:
        raise HTTPException(400,'Не удалось загрузить набор по ссылке: '+str(e)[:180])


class EvalRequest(BaseModel):
    conversation_ids:list[str] = Field(min_length=1,max_length=2000)
    checklist_id:str='default-v1'
    mode:str='ai'      # ai or demo
    ensemble:Optional[int]=Field(default=None,ge=1,le=5)
    force_new:bool=False  # deliberate, separate analysis; scores may differ
    approve_new:bool=False  # do not bill or change analysis history unintentionally


def _effective_ensemble(value, mode):
    return 1 if mode == 'demo' else int(value or os.getenv('ENSEMBLE','1'))


def _assessment_params(rec, cl, mode, ensemble):
    model = 'mock-heuristic' if mode == 'demo' else llm.model_name()
    sig = evaluator.cache_key(cl, rec, ensemble=ensemble, demo=(mode=='demo'))
    legacy = (mode == 'ai' and ensemble == 1 and os.getenv('TEMPERATURE','0') == '0'
              and os.getenv('THINKING_LEVEL','low') in ('', 'low'))
    return model, sig, legacy


def _evaluation_preview(req):
    if req.mode not in ('ai','demo'):
        raise HTTPException(400,'mode должен быть ai или demo')
    cl=store.checklist_get(req.checklist_id)
    if not cl: raise HTTPException(404,'Чек-лист не найден')
    ids=list(dict.fromkeys(req.conversation_ids))
    ensemble=_effective_ensemble(req.ensemble,req.mode)
    if not 1 <= ensemble <= 5: raise HTTPException(400,'ENSEMBLE должен быть от 1 до 5')
    items=[]
    for cid in ids:
        rec=store.get_conversation(cid)
        if rec is None: raise HTTPException(404,f'Диалог не найден: {cid}')
        model,fp,legacy=_assessment_params(rec,cl,req.mode,ensemble)
        matched=None if req.force_new else store.find_matching_evaluation(
            cid,req.checklist_id,cl,model,req.mode,fp,legacy=legacy)
        history=store.list_conversation_evaluations(cid)
        primary=next((r for r in history if r['is_primary']),None)
        if matched:
            action='reuse'
            explanation='Совпали чек-лист, модель, версия инструкций и параметры проверки; используется сохранённая оценка.'
        elif req.force_new:
            action='new'
            explanation='Принудительная независимая переоценка. Нужен новый вызов Gemini; результат может отличаться.'
        elif primary:
            action='new'
            explanation='Параметры отличаются от сохранённой оценки либо её версия больше не совпадает с текущей. Нужен новый вызов Gemini.'
        else:
            action='new'
            explanation='Ранее успешной ИИ-оценки не было; требуется первый запуск модели.'
        items.append({'conversation_id':cid,'action':action,'reason':explanation,
                      'primary_evaluation_id':primary['id'] if primary else None,
                      'primary_score':primary['final_score'] if primary else None,
                      'primary_checklist_id':primary['checklist_id'] if primary else None,
                      'matching_evaluation_id':matched,
                      'existing_evaluations':len(history)})
    return {'total':len(items),'reused':sum(x['action']=='reuse' for x in items),
            'new_calls':sum(x['action']=='new' for x in items)*ensemble,
            'new_conversations':sum(x['action']=='new' for x in items),
            'checklist_id':req.checklist_id,'model':llm.model_name() if req.mode=='ai' else 'demo',
            'ensemble':ensemble,'items':items,
            'notice':'При новой оценке основной результат НЕ меняется автоматически; он выбирается вручную из истории.'}


@app.post('/api/evaluations/preview')
def preview_evaluations(req:EvalRequest):
    """No Gemini calls, no new evaluations. Useful before confirming billing."""
    return _evaluation_preview(req)


def _process_job(jid, ids, cl, cl_id, mode, ensemble, force_new=False):
    try:
        for cid in ids:
            rec = store.get_conversation(cid)
            model, fingerprint, legacy = _assessment_params(rec, cl, mode, ensemble)
            with evaluation_lock(fingerprint):
                prior = None if force_new else store.find_matching_evaluation(
                    cid, cl_id, cl, model, mode, fingerprint, legacy=legacy)
                if prior:
                    store.update_job(jid, prior, reused=True)
                    continue
                try:
                    result = evaluator.evaluate(cl, rec, ensemble=ensemble, demo=(mode=='demo'))
                except Exception as exc:
                    result = {'conversation_id':cid,'status':'error','criteria':[],'score':None,
                              'error':str(exc)[:200]}
                eid = store.save_evaluation(cid, cl_id, cl, model, mode, result, fingerprint)
                store.update_job(jid, eid, error=result['status']!='ok')
    finally:
        store.update_job(jid,finished=True)


@app.post('/api/evaluations/start', status_code=202)
def start_evaluations(req:EvalRequest):
    preview=_evaluation_preview(req)
    if preview['new_conversations'] and not req.approve_new:
        raise HTTPException(status_code=409,detail={
            'message':'Новый анализ требует подтверждения. Сначала вызовите /api/evaluations/preview.',
            'new_conversations':preview['new_conversations'],'new_calls':preview['new_calls']})
    cl = store.checklist_get(req.checklist_id)
    if not cl: raise HTTPException(404,'Чек-лист не найден')
    ids = list(dict.fromkeys(req.conversation_ids))
    for cid in ids:
        if not store.get_conversation(cid):
            raise HTTPException(404,f'Диалог не найден: {cid}')
    ensemble = _effective_ensemble(req.ensemble, req.mode)
    if ensemble < 1 or ensemble > 5:
        raise HTTPException(400,'ENSEMBLE должен быть от 1 до 5')
    # Reusing a saved AI score is allowed even without a current API key.
    if req.mode == 'ai' and not req.force_new:
        missing = []
        for cid in ids:
            rec=store.get_conversation(cid)
            model,fp,legacy=_assessment_params(rec,cl,req.mode,ensemble)
            if not store.find_matching_evaluation(cid,req.checklist_id,cl,model,req.mode,fp,legacy=legacy):
                missing.append(cid)
    else:
        missing=ids
    if req.mode == 'ai' and missing and (not llm.api_key_configured() or os.getenv('MOCK')=='1'):
        raise HTTPException(400,'Для новой ИИ-оценки нужен GEMINI_API_KEY и MOCK=0. Сохранённые оценки доступны без ключа.')
    jid=store.create_job(ids)
    executor.submit(_process_job,jid,ids,cl,req.checklist_id,req.mode,ensemble,req.force_new)
    return {'job_id':jid,'status':'running','total':len(ids),
            'expected_reused':preview['reused'],'expected_new_calls':preview['new_calls'],
            'message':'Новая оценка создаётся отдельно и не меняет закреплённую основную оценку.'}


@app.get('/api/jobs/{jid}')
def job(jid:str):
    r=store.get_job(jid)
    if not r: raise HTTPException(404,'Задача не найдена')
    return r


class AuditRequest(BaseModel):
    group_id:str='DEV'
    conversation_ids:list[str] = Field(default_factory=list,max_length=500)


def _audit_for_conversation(cid, group_id):
    rec = store.get_conversation(cid)
    if not rec: raise HTTPException(404, f'Диалог не найден: {cid}')
    if rec['group_id'] != group_id:
        raise HTTPException(400, f'Диалог {cid} не относится к {group_id}')
    with store.db() as con:
        rows=con.execute('''SELECT id,mode,status FROM evaluations WHERE conversation_id=?
                              ORDER BY created_at DESC,rowid DESC''',(cid,)).fetchall()
    ai_ids=[r['id'] for r in rows if r['mode'] in ('ai','imported_ai')]
    if not ai_ids:
        return {'conversation_id':cid,'group_id':group_id,'score':None,'status':'not_evaluated',
                'issues':[], 'errors':0,'warnings':0,'evaluation_id':None,
                'note':'Для диалога нет сохранённой настоящей ИИ-оценки. Запрос к Gemini не выполнялся.'}
    primary_id=rec.get('active_evaluation_id')
    chosen_id=primary_id if primary_id in ai_ids else next(
        (r['id'] for r in rows if r['id'] in ai_ids and r['status']=='ok'),ai_ids[0])
    saved=store.get_evaluation(chosen_id)
    peers=[store.get_evaluation(rid) for rid in ai_ids if rid != chosen_id][:20]
    return audit.audit_evaluation(saved,rec,peers)


@app.post('/api/audit/run')
def run_audit(req:AuditRequest):
    """Audit saved assessments offline; never asks LLM and never changes scores."""
    group=req.group_id.upper()
    if group not in ('DEV','PROD'):
        raise HTTPException(400,'Нужно выбрать DEV или PROD')
    if req.conversation_ids:
        ids=list(dict.fromkeys(req.conversation_ids))
    else:
        ids=[c['conversation_id'] for c in store.list_conversations(group_id=group)]
    reports=[_audit_for_conversation(cid,group) for cid in ids]
    return {'group_id':group,'total':len(reports),
            'with_issues':sum(bool(r['issues']) for r in reports),
            'not_evaluated':sum(r['status']=='not_evaluated' for r in reports),
            'errors':sum(r['errors'] for r in reports),
            'warnings':sum(r['warnings'] for r in reports),
            'reports':reports,
            'llm_calls':0,
            'notice':'Это проверка согласованности и доказательств. Она не доказывает семантическую правильность оценок.'}


@app.get('/api/audit/evaluations/{eid}')
def audit_saved_evaluation(eid:str):
    saved=store.get_evaluation(eid)
    if not saved: raise HTTPException(404,'Оценка не найдена')
    rec=store.get_conversation(saved['conversation_id'])
    with store.db() as con:
        rows=con.execute('''SELECT id FROM evaluations WHERE conversation_id=? AND id<>?
                            AND mode IN ('ai','imported_ai')
                            ORDER BY created_at DESC,rowid DESC LIMIT 20''',
                         (saved['conversation_id'],eid)).fetchall()
    return audit.audit_evaluation(saved,rec,[store.get_evaluation(r['id']) for r in rows])


@app.get('/api/evaluations')
def evaluations(group_id:Optional[str]=None):
    return store.list_evaluations(group_id)


@app.get('/api/evaluations/{eid}')
def evaluation(eid:str):
    r=store.get_evaluation(eid)
    if not r: raise HTTPException(404,'Оценка не найдена')
    return r


class AppealRequest(BaseModel):
    criterion_id:str
    comment:str


@app.post('/api/evaluations/{eid}/appeals',status_code=201)
def appeal(eid:str,req:AppealRequest):
    try: return {'appeal_id':store.create_appeal(eid,req.criterion_id,req.comment)}
    except ValueError as e: bad(e)


@app.get('/api/appeals')
def appeals(status:Optional[str]=None):
    return store.list_appeals(status)


@app.get('/api/appeals/{aid}/context')
def appeal_context(aid: str):
    result = store.appeal_context(aid)
    if result is None: raise HTTPException(404, 'Апелляция или доказательства не найдены')
    return result


class DecisionRequest(BaseModel):
    decision:str
    value:Optional[Union[bool,float]]=None
    reason:str


@app.post('/api/appeals/{aid}/decision')
def decision(aid:str,req:DecisionRequest):
    try: return store.decide_appeal(aid,req.decision,req.value,req.reason)
    except ValueError as e: bad(e)


def make_export(group_id):
    from .validation import validate_value
    records=store.list_conversations(group_id=group_id)
    results=[]
    all_models=[]
    for rec in records:
        with store.db() as con:
            rows=con.execute('''SELECT * FROM evaluations WHERE conversation_id=? ORDER BY created_at DESC,rowid DESC''',(rec['conversation_id'],)).fetchall()
        chosen=next((r for r in rows if r['id']==rec['evaluation_id']
                     and r['status']=='ok' and r['mode'] in ('ai','imported_ai')
                     and r['model']!='mock-heuristic'),None)
        if not chosen:
            results.append({'conversation_id':rec['conversation_id'],'status':'error','criteria':[], 'score':None,
                            'error':'Нет завершённой ИИ-оценки. Демо-оценки не подходят для сдачи.'})
            continue
        orig=json.loads(chosen['final_result'])
        cl=json.loads(chosen['checklist_snapshot'])
        wanted=['contact','discovery','identification','consultation','empathy','dialogue','closure','critical']
        got={c['criterion_id']:c for c in orig['criteria']}
        try:
            if set(got)!=set(wanted): raise ValueError('В чек-листе не 8 обязательных критериев')
            for c in cl['criteria']:
                if c['id'] in got: validate_value(c,got[c['id']]['value'],store.get_conversation(rec['conversation_id'])['channel'])
            result={'conversation_id':rec['conversation_id'],'status':'ok',
                    'criteria':[{k:got[cid][k] for k in ('criterion_id','value','rationale','evidence_turns')} for cid in wanted],
                    'score':{'final_score':orig['score']['final_score'],'critical_flag':orig['score']['critical_flag']}}
            results.append(result)
            all_models.append(chosen['model'])
        except (ValueError,KeyError) as exc:
            results.append({'conversation_id':rec['conversation_id'],'status':'error','criteria':[],'score':None,'error':str(exc)})
    return {'schema_version':'1.0','team_name':os.getenv('TEAM_NAME','FreeDoom'),
            'model':{'mode':'external_api','name':', '.join(sorted(set(all_models))) if all_models else llm.model_name()},
            'results':results}


@app.get('/api/export/{group_id}/summary')
def export_summary(group_id:str):
    if group_id.upper() not in ('DEV','PROD'): raise HTTPException(400,'Для сдачи используются DEV и PROD')
    x=make_export(group_id.upper())
    return {'total':len(x['results']),'ok':sum(r['status']=='ok' for r in x['results']),
            'errors':sum(r['status']=='error' for r in x['results']),'model':x['model']}


@app.get('/api/export/{group_id}')
def export_results(group_id:str):
    if group_id.upper() not in ('DEV','PROD'): raise HTTPException(400,'Для сдачи используются DEV и PROD')
    obj=make_export(group_id.upper())
    return Response(content=json.dumps(obj,ensure_ascii=False,indent=2),media_type='application/json',
                    headers={'Content-Disposition':f'attachment; filename="{group_id.lower()}-results.json"'})


@app.post('/api/export/{group_id}/write')
def write_results(group_id:str):
    if group_id.upper() not in ('DEV','PROD'): raise HTTPException(400,'Для сдачи используются DEV и PROD')
    x=make_export(group_id.upper())
    dst=ROOT/'results'/group_id.lower()/'results.json'
    dst.parent.mkdir(parents=True,exist_ok=True)
    dst.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
    ok=sum(r['status']=='ok' for r in x['results'])
    return {'saved':str(dst.relative_to(ROOT)),'total':len(x['results']),'ok':ok,'errors':len(x['results'])-ok}


@app.get('/')
def index():
    return FileResponse(WEB/'index.html')

app.mount('/static',StaticFiles(directory=WEB),name='static')
