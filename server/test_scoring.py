import json, os
from scoring import compute
cl = json.load(open(os.path.join(os.path.dirname(__file__), "checklist_default.json"), encoding="utf-8"))
ex = dict(contact=True, discovery=None, identification=None, consultation=15, empathy=True, dialogue=7, closure=True, critical=False)
assert compute(cl, ex)["final_score"] == 85.38, compute(cl, ex)["final_score"]
full = dict(contact=True, discovery=True, identification=True, consultation=20, empathy=True, dialogue=10, closure=True, critical=False)
assert compute(cl, full)["final_score"] == 100.0
zero = dict(contact=False, discovery=False, identification=False, consultation=0, empathy=False, dialogue=0, closure=False, critical=True)
assert compute(cl, zero)["final_score"] == 0.0
allna = dict(contact=None, discovery=None, identification=None, consultation=None, empathy=None, dialogue=None, closure=None)
assert compute(cl, allna)["final_score"] is None
print("scoring OK")
