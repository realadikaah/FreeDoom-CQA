"""Офлайн-заглушка для проверки пайплайна без ключа. НЕ для сдачи результатов."""
import re
GREET = re.compile(r"здравств|добр|сәлем|сәлеметсіз|hello|good (morning|afternoon|evening)|hi\b", re.I)
BYE = re.compile(r"до свидан|хорошего дня|всего доброго|сау бол|күніңіз|goodbye|have a (nice|great)|bye", re.I)
def fake(cl, rec):
    t = rec["transcript"]; ops = [(i, x) for i, x in enumerate(t, 1) if x["speaker"] == "operator"]
    first = ops[0][0] if ops else 1; last = ops[-1][0] if ops else 1
    ctx = {"contact": bool(ops and GREET.search(ops[0][1]["text"])), "discovery": None, "identification": None,
           "consultation": 15, "empathy": True, "dialogue": 7, "closure": bool(ops and BYE.search(ops[-1][1]["text"])), "critical": False}
    out = []
    for c in cl["criteria"]:
        v = ctx.get(c["id"], True if c["type"] == "bool" else (c["scale"][-1] if c["type"] == "scale" else False))
        out.append({"criterion_id": c["id"], "value": v, "rationale": "mock", "evidence_turns": [last if c["id"] == "closure" else first]})
    return {"criteria": out}
