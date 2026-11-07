"""Движок баллов. Работает от конфига чек-листа. Точная арифметика, округление half-up до 2 знаков."""
from fractions import Fraction
from decimal import Decimal, ROUND_HALF_UP, getcontext
getcontext().prec = 60

def r2(f):
    d = Decimal(f.numerator) / Decimal(f.denominator)
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def _w(c): return Fraction(str(c["weight"]))

def _earned(c, v):
    if c["type"] == "bool":
        return _w(c) if v is True else Fraction(0)
    top = Fraction(str(max(c["scale"])))
    return _w(c) * Fraction(str(v)) / top if top else Fraction(0)

def compute(checklist, values):
    """values: {criterion_id: True/False/None/число}. Возвращает итог и разбивку."""
    rows, tot_w, tot_e = [], Fraction(0), Fraction(0)
    for c in checklist["criteria"]:
        if c["type"] == "flag":
            continue
        v = values.get(c["id"]); na = v is None
        if not na:
            tot_w += _w(c); tot_e += _earned(c, v)
        rows.append({"id": c["id"], "section": c.get("section"), "weight": float(_w(c)), "na": na,
                     "_w": _w(c), "_e": None if na else _earned(c, v)})
    if tot_w == 0:
        return {"final_score": None, "rows": [], "sections": {}}
    k = Fraction(100) / tot_w                      # перераспределение веса N/A
    secs = {}
    for r in rows:
        if r["na"]:
            r["eff_weight"] = 0.0; r["points"] = None; r["max_points"] = 0.0
        else:
            r["eff_weight"] = r["max_points"] = r2(r["_w"] * k); r["points"] = r2(r["_e"] * k)
            s = secs.setdefault(r["section"], [Fraction(0), Fraction(0)])
            s[0] += r["_e"] * k; s[1] += r["_w"] * k
        r["earned_raw"] = None if r["na"] else float(r["_e"])
        del r["_w"], r["_e"]
    return {"final_score": r2(tot_e * k), "rows": rows,
            "sections": {n: {"points": r2(a), "max": r2(b)} for n, (a, b) in secs.items()}}
