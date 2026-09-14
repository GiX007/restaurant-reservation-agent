### tests/test_json.py. Run it with: python -m tests.test_json

import json
import math

# Test tables.json
with open("config/tables.json", encoding="utf-8") as f:
    cfg = json.load(f)
    # print(json.dumps(cfg, indent=2, ensure_ascii=False))

main_tables = [t for t in cfg["tables"] if t["area"] == "main"]
bar_spots = [t for t in cfg["tables"] if t["area"] == "bar"]
countable_chairs = math.floor(cfg["total_chairs"] * (1 + cfg["chair_buffer_percent"] / 100))

print("main tables:", len(main_tables))
print("bar spots:", len(bar_spots))
print("real chairs:", cfg["total_chairs"], "-> countable:", countable_chairs)
print("max dinner guests:", len(main_tables) * cfg["max_per_table"]["dinner"])
print("max bottle service guests:", len(bar_spots) * cfg["max_per_table"]["bottle_service"] + len(main_tables) * cfg["max_per_table"]["bottle_service"])
