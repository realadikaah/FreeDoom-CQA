import json, os, hashlib
try:
    from .scoring import compute
    from . import llm
except ImportError:
    from scoring import compute
    import llm

SYSTEM = """Ты — строгий QA-аудитор клиентских диалогов брокера Freedom Broker. Оцениваешь ТОЛЬКО работу человека-оператора (speaker=operator).
Реплики bot и system — контекст: бот отвечает первым, затем системное событие об эскалации, затем подключается оператор.
Язык диалога RU, KZ или EN; применяй один и тот же чек-лист. Пояснения (rationale) пиши по-русски кратко (1–2 предложения), цитируя суть реплики.
Правила:
- Оценивай каждый критерий НЕЗАВИСИМО. Перед ответом мысленно проверь его условие и найди реальную подтверждающую реплику.
- НЕ подменяй отсутствие доказательства положительной оценкой. Если действие не сделано и оно требовалось, ставь «Нет» или соответствующую низкую ступень шкалы.
- Для identification ставь true только если факт идентификации ПОДТВЕРЖДЁН в транскрипте ДО раскрытия сведений или защищённого действия; false — если оператор раскрыл данные/выполнил действие без проверки; null — если не было необходимости идентифицировать. Не ставь true за то, что идентификация просто не потребовалась.
- Для critical проверь каждый вид грубой ошибки ОТДЕЛЬНО: оскорбление, платная услуга в обход компании, разглашение конфиденциальных сведений. Если выявлено разглашение без подтверждённой идентификации, critical=true и identification=false. Сам запрос данных клиентом не является разглашением.
- Не повышай консультацию до максимальной ступени, если остался существенный вопрос клиента без ответа.
- Игнорируй порог первой реакции в 3 минуты, теги и вложения — они не влияют на этот чек-лист.
- Прежде чем дать итог, сопоставь каждое value с правилом критерия и evidence_turns; никаких ссылок на несуществующие реплики.
- Приветствие и представление бота НЕ считаются установлением контакта оператором.
- «Нет» — действие было нужно, но пропущено/выполнено неверно. N/A (null) — только если критерий допускает N/A и выполнено его условие. Если N/A не разрешён — N/A не ставь.
- Для шкальных критериев выбирай ТОЛЬКО значения из заданной шкалы.
- evidence_turns — номера реплик (с 1, по исходному массиву, включая клиента, бота и system), подтверждающие вывод. Для пропущенного действия укажи участок, где оно ожидалось (например, первые реплики оператора). Для N/A — реплики, показывающие условие неприменимости. Минимум один номер.
- Для звонков транскрипт — машинное распознавание: возможны мусор, дубли реплик и искажённые имена; не наказывай оператора за ошибки распознавания, оценивай суть.
- Если начало звонка не попало в запись, не заявляй, будто оператор точно не поздоровался: ограниченность доказательств нужно честно отразить в rationale. Для contact строго следуй выбранному чек-листу: в официальной версии ответ только true/false; если приветствие нельзя подтвердить по доступной записи, выбери false и объясни, что это отсутствие подтверждения, а не доказанная грубость оператора. Нельзя придумывать приветствие.
- Не придумывай факты, опирайся только на текст. Отвечай строго JSON."""

def fmt_transcript(rec):
    return "\n".join(f"[{i}] {t['timestamp']} {t['speaker']}: {t['text']}" for i, t in enumerate(rec["transcript"], 1))

def build_user(checklist, rec):
    lines = []
    for c in checklist["criteria"]:
        t = {"bool": "true/false" + ("/null(N/A)" if c.get("na_allowed") else ""),
             "scale": f"одно из {c.get('scale')}" + (" или null(N/A)" if c.get("na_allowed") else ""),
             "flag": "true (нарушение выявлено) / false"}[c["type"]]
        if c.get("na_channels"): t += f" (N/A — только для channel={', '.join(c['na_channels'])})"
        lines.append(f'- criterion_id="{c["id"]}" · {c["title"]} · вес {c["weight"]} · value: {t}\n  Правило: {c["rule"]}')
    kb = checklist.get("knowledge_base", "").strip()
    kb = f"БАЗА ЗНАНИЙ КОМПАНИИ (опирайся на неё при оценке корректности консультации; если факта в ней нет, оценивай логику ответа):\n{kb}\n\n" if kb else ""
    return (kb + f"КРИТЕРИИ:\n" + "\n".join(lines) +
            f"\n\nДИАЛОГ {rec['conversation_id']} · канал: {rec['channel']} · язык: {rec['language']}\n{fmt_transcript(rec)}\n\n"
            'Верни JSON: {"criteria":[{"criterion_id":"...","value":...,"rationale":"...","evidence_turns":[n,...]}]} — по одному объекту на каждый критерий из списка, в том же порядке.')

_TRUE = {"true", "yes", "да", "иә", "y", "1", "+"}
_FALSE = {"false", "no", "нет", "жоқ", "n", "0", "-"}
_NA = {"null", "none", "n/a", "na", "н/п", "неприменимо", ""}

def _unwrap(raw):
    """Модель может вернуть список, или объект с другим именем ключа."""
    if isinstance(raw, list): return raw
    if isinstance(raw, dict):
        if isinstance(raw.get("criteria"), list): return raw["criteria"]
        for v in raw.values():
            if isinstance(v, list) and v and isinstance(v[0], dict): return v
        if all(isinstance(v, dict) for v in raw.values()) and raw:   # {"contact": {...}, ...}
            return [dict(v, criterion_id=v.get("criterion_id", k)) for k, v in raw.items()]
    raise ValueError("неожиданный формат ответа модели")

def _coerce(c, v):
    if isinstance(v, str):
        t = v.strip().lower()
        if t in _NA: return None
        if c["type"] != "scale":
            if t in _TRUE: return True
            if t in _FALSE: return False
            raise ValueError(f"{c['id']}: bad value {v!r}")
        try: return float(t.replace(",", "."))
        except ValueError: raise ValueError(f"{c['id']}: bad value {v!r}")
    return v

def normalize(checklist, raw, n_turns, channel=None):
    items = _unwrap(raw)
    if not all(isinstance(x, dict) for x in items):
        raise ValueError('Все критерии должны быть объектами')
    ids = [x.get('criterion_id') for x in items]
    expected = [c['id'] for c in checklist['criteria']]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError('Неполный, дублированный или посторонний критерий')
    got = {x['criterion_id']: x for x in items}
    out = []
    for c in checklist["criteria"]:
        x = got.get(c["id"])
        if x is None: raise ValueError(f"нет критерия {c['id']}")
        v = _coerce(c, x.get("value"))
        if c["type"] in ("bool", "flag"):
            if v is not None and type(v) is not bool: raise ValueError(f"{c['id']}: bad value {v!r}")
        elif v is not None:
            if type(v) not in (int, float): raise ValueError(f"{c['id']}: bad scale value {v!r}")
            if v not in c['scale']:
                raise ValueError(f"{c['id']}: оценка {v!r} отсутствует в шкале {c['scale']}")
        allowed = c.get("na_allowed") and (not c.get("na_channels") or channel in c["na_channels"])
        if v is None and not allowed:
            raise ValueError(f"{c['id']}: N/A запрещён")
        ev = x.get("evidence_turns", [])
        ev = ev if isinstance(ev, list) else [ev]
        ev = sorted({int(t) for t in ev if str(t).strip().lstrip("-").isdigit() and 1 <= int(t) <= n_turns})
        rationale = str(x.get("rationale", "")).strip()
        if not rationale:
            raise ValueError(f"{c['id']}: отсутствует обоснование")
        if not ev:
            raise ValueError(f"{c['id']}: нет корректных доказательств")
        out.append({"criterion_id": c["id"], "value": v, "rationale": rationale, "evidence_turns": ev})
    return out

def _one_sample(checklist, rec, demo=False):
    if demo or os.environ.get("MOCK") == "1":
        try:
            from .mock import fake
        except ImportError:
            from mock import fake
        raw = fake(checklist, rec)
    else:
        # Re-ask only when the response is malformed; never invent values.
        for attempt in range(2):
            raw = llm.call_json(SYSTEM, build_user(checklist, rec))
            try:
                return normalize(checklist, raw, len(rec['transcript']), rec.get('channel'))
            except ValueError:
                if attempt == 1: raise
    return normalize(checklist, raw, len(rec['transcript']), rec.get('channel'))

def aggregate(checklist, samples):
    """Голосование по критериям: большинство для да/нет/N/A, медиана для шкал."""
    from collections import Counter
    import statistics
    out = []
    for i, c in enumerate(checklist["criteria"]):
        col = [s[i] for s in samples]
        votes = Counter(x["value"] for x in col)
        if c["type"] == "scale":
            nums = [x["value"] for x in col if x["value"] is not None]
            val = None if len(nums) * 2 <= len(col) else statistics.median_low(sorted(nums))
        else:
            top = max(votes.values()); val = next(x["value"] for x in col if votes[x["value"]] == top)
        pick = next(x for x in col if x["value"] == val)
        row = dict(pick); row["value"] = val
        if len(samples) > 1: row["votes"] = f"{sum(1 for x in col if x['value'] == val)}/{len(col)}"
        out.append(row)
    return out

def evaluate(checklist, rec, ensemble=None, demo=False):
    """Оценка одного диалога, совместимая с HTTP-сервером и batch.

    ensemble: число независимых ИИ-оценок; demo: локальный mock, всегда 1 образец.
    """
    n = 1 if (demo or os.environ.get("MOCK") == "1") else max(1, min(5, int(ensemble or os.environ.get("ENSEMBLE", "1"))))
    samples, last = [], None
    for _ in range(n):
        try: samples.append(_one_sample(checklist, rec, demo=demo))
        except Exception as ex: last = ex
    if len(samples) != n:
        raise ValueError(f"Успешных проверок {len(samples)}/{n}; результат не подтверждён: {last}")
    crit = aggregate(checklist, samples) if len(samples) > 1 else samples[0]
    vals = {c["criterion_id"]: c["value"] for c in crit}
    s = compute(checklist, vals)
    return {"conversation_id": rec["conversation_id"], "status": "ok", "criteria": crit,
            "score": {"final_score": s["final_score"], "critical_flag": bool(vals.get("critical"))}, "_breakdown": s}

def cache_key(checklist, rec, ensemble=None, demo=False):
    """Immutable signature for same transcript, checklist, model and generation rules.

    Prompt and environment knobs are part of the key so a *changed* rubric or
    model creates a new assessment; repeated clicks do not create random scores.
    """
    n = 1 if (demo or os.environ.get('MOCK') == '1') else max(1, min(5, int(ensemble or os.environ.get('ENSEMBLE', '1'))))
    spec = {
        'schema': 'cqa-evaluator-fingerprint-v2',
        'checklist': {k: v for k,v in checklist.items() if k not in ('id','created_at','version')},
        'record': {k: rec.get(k) for k in ('conversation_id','channel','language','transcript','audio_url')},
        'system_prompt': SYSTEM, 'model': 'mock-heuristic' if demo else llm.model_name(),
        'mode': 'demo' if demo else 'ai', 'ensemble': n,
        'temperature': str(os.environ.get('TEMPERATURE','0')),
        'thinking_level': str(os.environ.get('THINKING_LEVEL','')),
    }
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(',',':')).encode('utf-8')).hexdigest()
