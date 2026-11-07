"""Validate checklist configurations and decisions without coercing their meaning."""
import re


def validate_value(c, value, channel=None):
    typ = c['type']
    if value is None:
        if not c.get('na_allowed',False): raise ValueError(f"N/A запрещён для {c['id']}")
        if channel is not None and c.get('na_channels') and channel not in c['na_channels']:
            raise ValueError(f"N/A допустим только для канала {c['na_channels']}")
        return
    if typ in ('bool','flag'):
        if type(value) is not bool: raise ValueError(f"{c['id']}: ожидается Да/Нет")
    elif typ == 'scale':
        if type(value) not in (int,float) or value not in c['scale']:
            raise ValueError(f"{c['id']}: значение должно быть из шкалы {c['scale']}")
    else:
        raise ValueError(f'Неизвестный тип: {typ}')


def validate_checklist(cfg):
    if not isinstance(cfg,dict): raise ValueError('Чек-лист должен быть объектом')
    name = str(cfg.get('name','')).strip()
    if not 1 <= len(name) <= 120: raise ValueError('Укажите название чек-листа (до 120 символов)')
    criteria = cfg.get('criteria')
    if not isinstance(criteria,list) or not 1 <= len(criteria) <= 30:
        raise ValueError('В чек-листе должно быть от 1 до 30 критериев')
    seen, arr = set(), []
    for c in criteria:
        if not isinstance(c,dict): raise ValueError('Неверный критерий')
        cid = c.get('id','')
        if not isinstance(cid,str) or not re.fullmatch(r'[a-z][a-z0-9_]{1,60}',cid) or cid in seen:
            raise ValueError('ID критерия должен быть уникален: латиница, цифры, _ (2–61 символ)')
        seen.add(cid)
        typ = c.get('type')
        if typ not in ('bool','scale','flag'): raise ValueError(f'Неизвестный тип у {cid}')
        weight = c.get('weight',0)
        if type(weight) not in (int,float) or weight < 0 or weight > 100:
            raise ValueError(f'Некорректный вес {cid}')
        if typ == 'flag' and weight != 0: raise ValueError('Флаг должен иметь вес 0')
        title = str(c.get('title','')).strip()
        rule = str(c.get('rule','')).strip()
        if not title or not rule or len(title)>160 or len(rule)>4000:
            raise ValueError(f'Укажите название и правило для {cid}')
        na = c.get('na_allowed',False)
        if type(na) is not bool: raise ValueError(f'Неверное N/A у {cid}')
        if typ=='flag' and na: raise ValueError('Флаг не поддерживает N/A')
        item = dict(id=cid,title=title,type=typ,weight=weight,na_allowed=na,
                    section=str(c.get('section','')).strip()[:100],rule=rule)
        if typ=='scale':
            vals = c.get('scale')
            if not isinstance(vals,list) or len(vals)<2 or len(vals)>20 or any(type(v) not in (int,float) or v<0 for v in vals) or vals != sorted(set(vals)) or vals[-1]<=0:
                raise ValueError(f'Задайте возрастающую шкалу для {cid}, например 0,5,10,15,20')
            item['scale'] = vals
        channels = c.get('na_channels', [])
        if channels:
            if not na or not isinstance(channels,list) or any(not isinstance(x,str) or not 1 <= len(x) <= 50 for x in channels):
                raise ValueError(f'Некорректные na_channels для {cid}')
            item['na_channels'] = list(dict.fromkeys(channels))
        arr.append(item)
    if not any(c['type']!='flag' and c['weight']>0 for c in arr):
        raise ValueError('Нужен хотя бы один критерий с весом')
    kb = cfg.get('knowledge_base','')
    if not isinstance(kb,str) or len(kb)>20000: raise ValueError('knowledge_base слишком длинная')
    return {'name':name,'criteria':arr,'knowledge_base':kb}
