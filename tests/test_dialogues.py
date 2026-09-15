### tests/test_dialogues.py, Run with: python -m tests.test_dialogues
import json
import pathlib

d = json.loads(pathlib.Path("evals/dialogues/dlg-01-party-size-change.json").read_text(encoding="utf-8"))

print(d["dialogue_id"], "-", len(d["turns"]), "turns")

# party_size at every user turn, in order
sizes = [t["state"]["party_size"] for t in d["turns"] if t["speaker"] == "USER"]
print("party_size over time:", sizes)   # expect [6, 6, 6, 6, 6, 4, 4, 4]