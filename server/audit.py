"""Deterministic evidence and consistency audit for saved CQA assessments.

No LLM calls. This module flags review candidates; it does NOT independently
certify a semantic score as right/wrong and never changes saved assessments.
"""
import json
import re

from .scoring import compute
from .validation import validate_value

GREETING = re.compile(r'здравств|добрый|доброго|сәлем|қайырлы|hello\b|\bhi\b|good (morning|afternoon|evening)', re.I)
INTRO = re.compile(r'меня зовут|менің атым|менің есімім|\bмен\s+[А-ЯӘІҢҒҮҰҚӨҺЁ][а-яәіңғүұқөһё]+|\bя\s+[А-ЯЁ][а-яё]+\b|my name is|this is [A-Z]|i am [A-Z]|i\x27m [A-Z]', re.I)
IDENTITY = re.compile(r'личност[ьи] (клиента )?подтвержден|идентификаци[яю] (клиента )?(успешно )?(пройден|завершен)|личность подтверждена|identity verified|identification completed|verification successful|жеке басы (расталды|расталған)', re.I)
CONTRADICTION = re.compile(r'не (ответил[аи]?|предоставил[аи]?|раскрыл[аи]?|уточнил[аи]?|рассмотрел[аи]?|решил[аи]?)[^.!?]{0,90}(вопрос|запрос|комисс|проблем|срок|детал)|остал[аио]сь? (без ответа|открыт)|пропущен[^.!?]{0,55}(вопрос|детал|ответ)|did not answer|question (was )?unanswered|not addressed', re.I)
NO_VIOLATIONS = re.compile(r'(груб\w* (нарушен\w*|ошиб\w*)|оскорблен\w*|разглашен\w*|раскрыт\w*).*?(не выявлен\w*|нет|не зафиксирован\w*|отсутств\w*)|не (выявлен\w*|зафиксирован\w*).*?(груб\w*|нарушен\w*|оскорблен\w*|разглашен\w*)|no (critical|serious) (violations|errors)|no breach', re.I)


def _short(s, max_chars=380):
    s = str(s or '').strip()
    return s if len(s) <= max_chars else s[:max_chars - 1] + '…'


def _value(v):
    return 'N/A' if v is None else 'Да' if v is True else 'Нет' if v is False else str(v)


def _criterion_map(result):
    return {x.get('criterion_id'): x for x in result.get('criteria', []) if isinstance(x, dict)}


def _canonical_config(rubric):
    # A saved assessment must be tested against the checklist in its own snapshot.
    return json.dumps({'criteria': rubric.get('criteria'), 'knowledge_base': rubric.get('knowledge_base', '')},
                      ensure_ascii=False, sort_keys=True)


def audit_evaluation(saved, conversation, others=()):
    """Return structured findings with exact message evidence from the saved assessment."""
    eid = saved['id']
    cid = saved['conversation_id']
    turns = conversation['transcript']
    rubric = saved['checklist_snapshot']
    result = saved.get('final_result') or {}
    original = saved.get('original_result') or {}
    issues = []
    criteria_review = []

    def add(code, severity, criterion_id, message, refs=(), extra=None):
        evidence = []
        for n in refs:
            if type(n) is int and 1 <= n <= len(turns):
                t = turns[n - 1]
                evidence.append({'number': n, 'speaker': t.get('speaker', ''),
                                 'timestamp': t.get('timestamp', ''), 'text': _short(t.get('text'), 900)})
        issue = {'code': code, 'severity': severity, 'criterion_id': criterion_id,
                 'message': message, 'evidence': evidence}
        if extra is not None:
            issue['comparison'] = extra
        issues.append(issue)

    if saved.get('status') != 'ok':
        add('assessment_failed', 'error', None, 'Оценка завершилась ошибкой; анализ доказательств невозможен.')
    else:
        criteria = result.get('criteria') or []
        found = _criterion_map(result)
        wanted = [c['id'] for c in rubric.get('criteria', [])]
        if len(criteria) != len(wanted) or len(found) != len(criteria) or set(found) != set(wanted):
            add('criteria_mismatch', 'error', None, 'Набор критериев в сохранённой оценке не соответствует её чек-листу.')
        ops = [(i, t) for i, t in enumerate(turns, 1) if t.get('speaker') == 'operator']
        first_operator = ops[0] if ops else None
        for c in rubric.get('criteria', []):
            cid_criterion = c['id']
            val = found.get(cid_criterion)
            if val is None:
                continue
            raw_value = val.get('value')
            rationale = str(val.get('rationale') or '').strip()
            refs = val.get('evidence_turns', [])
            if not isinstance(refs, list):
                refs = []
            try:
                validate_value(c, raw_value, conversation.get('channel'))
            except ValueError as exc:
                add('invalid_value', 'error', cid_criterion, str(exc), refs)
            if not rationale:
                add('missing_rationale', 'error', cid_criterion, 'Не заполнено обоснование оценки.', refs)
            if not refs:
                add('missing_evidence', 'error', cid_criterion, 'Не указаны номера подтверждающих реплик.', ())
            else:
                bad = [n for n in refs if type(n) is not int or n < 1 or n > len(turns)]
                if bad:
                    add('evidence_out_of_range', 'error', cid_criterion,
                        'Есть ссылки на отсутствующие реплики или некорректные номера.', ())
            valid_refs = [n for n in refs if type(n) is int and 1 <= n <= len(turns)]
            criteria_review.append({
                'criterion_id': cid_criterion,
                'title': c.get('title', cid_criterion),
                'value': raw_value,
                'weight': c.get('weight', 0),
                'rationale': rationale,
                'rule': _short(c.get('rule', ''), 750),
                'evidence': [{'number': n, 'speaker': turns[n - 1].get('speaker', ''),
                              'timestamp': turns[n - 1].get('timestamp', ''),
                              'text': _short(turns[n - 1].get('text', ''), 900)} for n in valid_refs]
            })
            operator_evidence = [n for n in valid_refs if turns[n - 1].get('speaker') == 'operator']
            if cid_criterion == 'contact' and raw_value is True:
                if not first_operator:
                    add('contact_no_operator', 'review', cid_criterion,
                        'Приветствие оценено «Да», но реплик оператора в транскрипте нет.', valid_refs)
                else:
                    n, t = first_operator
                    if n not in valid_refs:
                        add('contact_wrong_reference', 'review', cid_criterion,
                            'Для «Установления контакта» не указана первая реплика человека-оператора. Приветствие бота не засчитывается.',
                            [n] + valid_refs[:2])
                    if not (GREETING.search(t.get('text', '')) and INTRO.search(t.get('text', ''))):
                        add('contact_needs_review', 'review', cid_criterion,
                            'В первой реплике оператора не удалось одновременно распознать приветствие и представление. Проверьте вручную (особенно ASR-звонки).', [n])
            if cid_criterion == 'contact' and raw_value is False and first_operator:
                n, t = first_operator
                if GREETING.search(t.get('text', '')) and INTRO.search(t.get('text', '')):
                    add('contact_possible_false_negative', 'review', cid_criterion,
                        'В первой реплике оператора распознаны приветствие и представление, хотя оценено «Нет».', [n])
            if cid_criterion == 'identification' and raw_value is True:
                matches = [i for i, t in enumerate(turns, 1) if IDENTITY.search(t.get('text', '')) and t.get('speaker') in ('system', 'operator')]
                if not matches:
                    add('identification_unconfirmed', 'review', cid_criterion,
                        'Оценено «Да», но явного подтверждения успешной идентификации в сообщениях оператора или системы не найдено. Проверьте, требуется ли она.', valid_refs[:3])
            if cid_criterion == 'consultation' and raw_value == max(c.get('scale', [20])):
                if CONTRADICTION.search(rationale):
                    add('consultation_full_vs_rationale', 'review', cid_criterion,
                        'Максимальный балл за консультацию сочетается с описанием пропущенного/неотвеченного вопроса.', valid_refs[:3])
            if cid_criterion == 'critical' and raw_value is True and NO_VIOLATIONS.search(rationale):
                add('critical_flag_vs_rationale', 'review', cid_criterion,
                    'Флаг грубого нарушения установлен, но в объяснении сказано, что нарушений нет.', valid_refs[:3])
            if cid_criterion in ('contact', 'empathy', 'dialogue', 'closure', 'consultation') and raw_value is not None:
                if not operator_evidence and valid_refs:
                    add('operator_evidence_missing', 'review', cid_criterion,
                        'В доказательствах нет ни одной реплики человека-оператора. Критерий оценивает именно его работу.', valid_refs[:3])

        try:
            vals = {key: x.get('value') for key, x in found.items()}
            actual = compute(rubric, vals)['final_score']
            recorded = (result.get('score') or {}).get('final_score')
            if actual != recorded:
                add('score_inconsistent', 'error', None,
                    f'Сохранённый итог {recorded}, а пересчёт по чек-листу даёт {actual}.')
            actual_critical = bool(vals.get('critical'))
            if actual_critical != (result.get('score') or {}).get('critical_flag'):
                add('critical_flag_inconsistent', 'error', 'critical', 'Итоговый флаг грубой ошибки не совпадает со значением критерия critical.')
        except (KeyError, TypeError, ValueError, ZeroDivisionError, AttributeError) as exc:
            add('scoring_unavailable', 'error', None,
                'Не удалось надёжно пересчитать итог: ' + _short(exc))

        # Compare previous independently created AI evaluations of the SAME checklist,
        # excluding changes made by reviewers and excluding demo/mock results.
        if saved.get('mode') in ('ai', 'imported_ai'):
            for prev in others:
                if prev['id'] == eid or prev.get('status') != 'ok' or prev.get('mode') not in ('ai', 'imported_ai'):
                    continue
                if _canonical_config(prev['checklist_snapshot']) != _canonical_config(rubric):
                    continue
                prev_criteria = _criterion_map(prev.get('original_result') or {})
                here_criteria = _criterion_map(original)
                changes = [{'criterion_id': k, 'previous': _value(prev_criteria[k].get('value')),
                            'current': _value(here_criteria[k].get('value'))}
                           for k in here_criteria.keys() & prev_criteria.keys()
                           if here_criteria[k].get('value') != prev_criteria[k].get('value')]
                if changes:
                    add('history_disagreement', 'review', None,
                        'Две независимые оценки того же диалога по тому же чек-листу расходятся. Это повод изучить доказательства, а не автоматически менять итог.',
                        (), {'evaluation_id': prev['id'], 'model': prev.get('model'),
                             'criteria_changes': sorted(changes, key=lambda x: x['criterion_id'])})
                    break

    return {'evaluation_id': eid, 'conversation_id': cid,
            'group_id': conversation.get('group_id'), 'language': conversation.get('language'),
            'checklist_id': saved.get('checklist_id'), 'score': (result.get('score') or {}).get('final_score'),
            'status': saved.get('status'), 'issues': issues, 'criteria_review': criteria_review,
            'errors': sum(i['severity'] == 'error' for i in issues),
            'warnings': sum(i['severity'] == 'review' for i in issues),
            'note': 'Автоматический аудит проверяет формальные несоответствия и отмечает подозрительные места. Он не доказывает, что балл верен или неверен.'}
