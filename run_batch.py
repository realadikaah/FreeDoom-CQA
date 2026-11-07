"""Пакетная оценка набора -> results/<dev|prod>/results.json (формат JSON 1.0).
Запуск: python run_batch.py data/dev.json results/dev/results.json [--workers 4]"""
import sys, json, os, time, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "server"))
import evaluator, llm
from server import store

def load_records(path):
    d = json.load(open(path, encoding="utf-8-sig"))
    return d["data"]["records"] if "data" in d else d

def run(records, cl, workers=2, cache_dir=None, use_cache=True):
    cache_dir = cache_dir or os.path.join(os.path.dirname(__file__), "data", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    store.init_db()
    model=llm.model_name()
    checklist_id='default-v1'  # for saved default results; custom CLI configs use disk cache only
    cl_is_default = {k:v for k,v in cl.items() if k not in ('id','created_at','version')} == {k:v for k,v in store.checklist_get('default-v1').items() if k not in ('id','created_at','version')}
    def one(rec):
        fp=evaluator.cache_key(cl, rec)
        ck = os.path.join(cache_dir, fp + ".json")
        if use_cache and cl_is_default:
            found=store.find_matching_evaluation(rec['conversation_id'],checklist_id,store.checklist_get(checklist_id),model,'ai',fp,
               legacy=(int(os.getenv('ENSEMBLE','1'))==1 and os.getenv('TEMPERATURE','0')=='0'
                       and os.getenv('THINKING_LEVEL','low') in ('','low')))
            if found:
                item=store.get_evaluation(found)['final_result']
                return {k:v for k,v in item.items() if k in ('conversation_id','status','criteria','score')}
        if use_cache and os.path.exists(ck):
            existing = json.load(open(ck, encoding="utf-8"))
            if existing.get("status") == "ok" and existing.get("conversation_id") == rec["conversation_id"]: return existing
        try:
            r = evaluator.evaluate(cl, rec); r.pop("_breakdown", None)
            if cl_is_default:
                store.save_evaluation(rec['conversation_id'],checklist_id,store.checklist_get(checklist_id),model,
                                      'demo' if os.getenv('MOCK')=='1' else 'ai',r,fp)
            with open(ck, "w", encoding="utf-8") as f: json.dump(r, f, ensure_ascii=False)
            return r
        except Exception as e:
            return {"conversation_id": rec["conversation_id"], "status": "error", "criteria": [], "score": None, "error": str(e)[:300]}
    out = [None] * len(records); done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(one, r): i for i, r in enumerate(records)}
        for f in as_completed(futs):
            i = futs[f]; out[i] = f.result(); done += 1
            sc = out[i]["score"]["final_score"] if out[i]["status"] == "ok" else out[i].get("error", "")[:80]
            print(f"[{done}/{len(records)}] {out[i]['conversation_id']} {out[i]['status']} {sc}", flush=True)
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--workers", type=int, default=2); ap.add_argument("--no-cache", action="store_true"); ap.add_argument("--team", default=None)
    ap.add_argument("--checklist", default=os.path.join(os.path.dirname(__file__), "server", "checklist_default.json")); a = ap.parse_args()
    cl = json.load(open(a.checklist, encoding="utf-8")); recs = load_records(a.src)
    # A previous successful evaluation can be reused offline; a missing key will
    # fail only for dialogues that genuinely require a fresh remote inference.
    t0 = time.time(); res = run(recs, cl, a.workers, use_cache=not a.no_cache)
    out = {"schema_version": "1.0", "team_name": a.team or os.environ.get("TEAM_NAME", "FreeDoom"), "model": {"mode": "local" if os.environ.get("MOCK") == "1" else "external_api", "name": llm.model_name()}, "results": res}
    dst_dir = os.path.dirname(os.path.abspath(a.dst))
    os.makedirs(dst_dir, exist_ok=True)
    ok = sum(r["status"] == "ok" for r in res)
    if ok == 0:
        raise SystemExit("Все оценки завершились ошибкой. Файл results.json не создаётся и не перезаписывается. Проверьте GEMINI_MODEL и GEMINI_API_KEY.")
    import tempfile
    fd, temp = tempfile.mkstemp(prefix='.results-', suffix='.json', dir=dst_dir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        os.replace(temp, a.dst)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    ok = sum(r["status"] == "ok" for r in res); print(f"{ok}/{len(res)} ok, {time.time()-t0:.1f}s -> {a.dst}")
