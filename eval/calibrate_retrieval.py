"""Offline retrieval calibration (no API). Prints top score for labelled relevant / irrelevant queries."""
import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from agent import retrieval

RELEVANT = [  # (query, expected chunk id)
    ("The payment portal failed, can I pay next working day?", "payment-portal-failure"),
    ("portal is down on due date", "payment-portal-failure"),
    ("what is the late fee after the due date", "fee-payment"),
    ("can I pay my fees in parts", "fee-payment"),
    ("who is eligible for a merit scholarship", "scholarships"),
    ("do I need an income certificate for need based scholarship", "scholarships"),
    ("can I switch my elective in week 5", "course-change"),
    ("change course after two weeks", "course-change"),
    ("minimum attendance for exams", "attendance"),
    ("attendance below 75 percent condonation", "attendance"),
    ("how long does a support ticket take to resolve", "support-requests"),
]
IRRELEVANT = [
    "refund policy for the hostel security deposit",
    "what is the dress code",
    "where is the cafeteria",
    "parking permit for my car",
    "who is the director of the institute",
    "placement statistics for last year",
    "wifi password for the library",
    "can I bring a laptop to the exam hall",
]

def run():
    hit = [(q, retrieval.search(q)) for q, _ in RELEVANT]
    miss = [(q, retrieval.search(q)) for q in IRRELEVANT]
    return hit, miss

if __name__ == "__main__":
    hit, miss = run()
    top = lambda r: r[0]["score"] if r else 0.0
    ok = 0
    for (q, exp), (_, r) in zip(RELEVANT, hit):
        good = bool(r) and r[0]["id"] == exp; ok += good
        print(f"{'OK ' if good else 'BAD'} rel  {top(r):5.2f} {[c['id'] for c in r]}  <- {q}")
    fp = 0
    for q, r in miss:
        fp += bool(r)
        print(f"{'FP ' if r else 'OK '} irr  {top(r):5.2f} {[c['id'] for c in r]}  <- {q}")
    print(f"\nTHRESH={retrieval.THRESH} REL={retrieval.REL}: relevant top-1 correct {ok}/{len(RELEVANT)}, irrelevant false positives {fp}/{len(IRRELEVANT)}")
