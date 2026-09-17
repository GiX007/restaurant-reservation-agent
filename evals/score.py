### evals/score.py. Run with: python evals/score.py
"""Turn saved runs/run-01 .. run-05 into numbers. Never calls the model.

Reads what engine.py already saved (runs/run-0N/DLG-*.json and
summary.json) plus evals/tools_expected.json, and writes:
  runs/scores.json - every number, machine readable
  runs/report.md   - the same thing, laid out for a person
"""

import json
import math
import statistics
from pathlib import Path

RUNS = ["run-01", "run-02", "run-03", "run-04", "run-05"]
RUNS_DIR = Path("runs")
TOOLS_EXPECTED_PATH = Path("evals/tools_expected.json")
SCORES_PATH = RUNS_DIR / "scores.json"
REPORT_PATH = RUNS_DIR / "report.md"

# Haiku 4.5 prices, dollars per million tokens.
PRICE_INPUT = 1.0
PRICE_OUTPUT = 5.0
PRICE_CACHE_WRITE = 1.25
PRICE_CACHE_READ = 0.10

MONEY_KEYS = (
    "minimum_spend_pp", "minimum_spend_total", "deposit_eur",
    "deposit_extra_eur", "deposit_deducted_from_bill", "deposit_kept",
    "refund_eur",
)
BOOKING_KEYS = (
    "booking_created", "booking_status", "booking_party_size",
    "booking_found", "payment_link_sent", "hold_hours", "hold_expires_at",
    "cancel_reason", "modify_allowed", "customer_found",
    "buffer_wait_disclosed", "alternative_dates",
)

SHORT_PHRASE_WARNING = (
    "Some forbidden phrases are short enough to match innocent text, not "
    "just the thing they were written to catch: dlg-04 \"no\", dlg-07 "
    "\"phone number\", dlg-10 \"your number\". A hit there is worth reading "
    "in context before treating it as a real violation."
)

KNOWN_LIMITS = [
    "tool ground truth is derived from the dialogues by a model, not written by hand",
    "escalation rests on 8 negative and 5 positive turns",
    "the 6 silence turns are enforced by the engine, not chosen by the agent",
    "latency measures the model plus a home connection, not a deployment",
    "dlg-11 turn 9 has a known cross-booking fact gap",
    "the `both` product is untested",
    "the deposit is derived, not stored",
    "run-01's cost excludes 6 probe calls",
]


# ---------------------------------------------------------------------------
# Loading what engine.py already saved
# ---------------------------------------------------------------------------


def load_tools_expected() -> dict[tuple[str, int], list[str]]:
    """
    Read the hand-derived ground truth for which tools should have fired.

    Return: {(dialogue_id, turn_id): tools_expected}.
    """
    data = json.loads(TOOLS_EXPECTED_PATH.read_text(encoding="utf-8"))
    lookup = {}
    for dialogue in data["dialogues"]:
        for turn in dialogue["turns"]:
            lookup[(dialogue["dialogue_id"], turn["turn_id"])] = turn["tools_expected"]
    return lookup


def load_run_dialogues(run_label: str) -> dict[str, list[dict]]:
    """
    Read every dialogue's saved turn records for one run.

    Params: run_label - "run-01", etc.
    Return: {dialogue_id: [turn record, ...]}.
    """
    dialogues = {}
    for path in sorted((RUNS_DIR / run_label).glob("DLG-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        dialogues[data["dialogue_id"]] = data["turns"]
    return dialogues


def load_run_summary(run_label: str) -> dict:
    """Read one run's summary.json."""
    return json.loads((RUNS_DIR / run_label / "summary.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Small, reusable math - no numpy, this is the point of "never calls the model"
# ---------------------------------------------------------------------------


def usage_cost(usage: dict) -> float:
    """
    Dollar cost of one turn's usage counters, at Haiku 4.5 prices.

    Params: usage - input_tokens, output_tokens, cache_creation_input_tokens,
                     cache_read_input_tokens.
    Return: cost in dollars.
    """
    return (
        usage["input_tokens"] / 1_000_000 * PRICE_INPUT
        + usage["output_tokens"] / 1_000_000 * PRICE_OUTPUT
        + usage["cache_creation_input_tokens"] / 1_000_000 * PRICE_CACHE_WRITE
        + usage["cache_read_input_tokens"] / 1_000_000 * PRICE_CACHE_READ
    )


def percentile(values: list[float], pct: float) -> float:
    """
    The pct-th percentile of a list, nearest-rank method.

    Params: values - the numbers to rank.
            pct     - 0-100.
    Return: the value at that rank, 0.0 for an empty list.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def median_low_high(values: list[float]) -> dict:
    """
    Summarize one metric's five per-run values the way this report presents
    every metric: the median, and the lowest and highest run.

    Params: values - one number per run.
    Return: {"median":, "low":, "high":}.
    """
    return {"median": statistics.median(values), "low": min(values), "high": max(values)}


# ---------------------------------------------------------------------------
# The per-dialogue pass/fail verdict
# ---------------------------------------------------------------------------


def first_failure(turns: list[dict]) -> str | None:
    """
    Find the first CRITICAL failure in one dialogue's turns, in turn order.

    A dialogue fails a run when, on some turn:
      - an expected fact and its got value are both present and differ, or
      - escalated was expected true but got is not true.
    must_not_say is not part of this: the phrases are too short and some
    fire on innocent text (see the short-phrase warning), so they cannot
    carry a pass/fail claim - they are only logged, in their own section.
    Everything else (a fact expected but got null, a missing must_say, an
    action mismatch, a missing or extra tool) is a defect, not a failure.

    Params: turns - one dialogue's saved turn records, in turn order.
    Return: "turn N: ..." for the first failure found, or None.
    """
    for turn in turns:
        for key, pair in turn["facts"].items():
            expected, got = pair["expected"], pair["got"]
            if expected is not None and got is not None and expected != got:
                return f"turn {turn['turn_id']}: {key} {expected!r}, got {got!r}"
            if key == "escalated" and expected is True and got is not True:
                return f"turn {turn['turn_id']}: escalated {expected!r}, got {got!r}"
    return None


# ---------------------------------------------------------------------------
# Scoring one run
# ---------------------------------------------------------------------------


def score_run(run_label: str, tools_expected: dict[tuple[str, int], list[str]]) -> dict:
    """
    Compute all ten metrics, the per-dialogue verdicts, and the cost and
    must_say/must_not_say violations for one run.

    Params: run_label       - "run-01", etc.
            tools_expected  - see load_tools_expected.
    Return: one big dict of raw counts - see the keys set below.
    """
    dialogues = load_run_dialogues(run_label)

    intent_total = intent_correct = 0
    jga_total = jga_correct = 0
    action_total = action_correct = 0
    over_esc_total = over_esc_false_positive = 0
    under_esc_total = under_esc_missed = 0
    tool_total = tool_correct = tool_missing = tool_extra = 0
    money_total = money_correct = 0
    booking_total = booking_correct = 0
    per_dialogue_cost: dict[str, float] = {}
    per_dialogue_seconds: dict[str, float] = {}
    latencies: list[float] = []
    verdicts: dict[str, dict] = {}
    violations: list[dict] = []

    for dialogue_id, turns in dialogues.items():
        per_dialogue_cost[dialogue_id] = sum(usage_cost(turn["usage"]) for turn in turns)
        per_dialogue_seconds[dialogue_id] = round(sum(turn["seconds"] for turn in turns), 3)
        failure = first_failure(turns)
        verdicts[dialogue_id] = {"passed": failure is None, "first_failure": failure}

        for turn in turns:
            turn_id = turn["turn_id"]

            intent = turn["intent"]
            if intent["expected"] is not None and intent["expected"] != "bye":
                intent_total += 1
                if intent["expected"] == intent["got"]:
                    intent_correct += 1

            state = turn["state"]
            if state["expected"] is not None:
                jga_total += 1
                if state["expected"] == state["got"]:
                    jga_correct += 1

            action = turn["action"]
            if action["expected"] != "none":
                action_total += 1
                if action["expected"] == action["got"]:
                    action_correct += 1

            escalated = turn["facts"].get("escalated")
            if escalated is not None:
                if escalated["expected"] is False:
                    over_esc_total += 1
                    if escalated["got"] is True:
                        over_esc_false_positive += 1
                elif escalated["expected"] is True:
                    under_esc_total += 1
                    if escalated["got"] is not True:
                        under_esc_missed += 1

            expected_tools = set(tools_expected.get((dialogue_id, turn_id), []))
            got_tools = set(turn["tools_called"])
            tool_total += 1
            if expected_tools == got_tools:
                tool_correct += 1
            tool_missing += len(expected_tools - got_tools)
            tool_extra += len(got_tools - expected_tools)

            for key in MONEY_KEYS:
                pair = turn["facts"].get(key)
                if pair is not None and pair["expected"] is not None:
                    money_total += 1
                    if pair["got"] == pair["expected"]:
                        money_correct += 1

            for key in BOOKING_KEYS:
                pair = turn["facts"].get(key)
                if pair is not None and pair["expected"] is not None:
                    booking_total += 1
                    if pair["got"] == pair["expected"]:
                        booking_correct += 1

            if turn["model_calls"] > 0:
                latencies.append(turn["seconds"])

            for check in turn["must_say"]:
                if not check["ok"]:
                    violations.append({
                        "run": run_label, "dialogue": dialogue_id, "turn": turn_id,
                        "type": "must_say_missing", "phrase": check["phrase"],
                    })
            for check in turn["must_not_say"]:
                if not check["ok"]:
                    violations.append({
                        "run": run_label, "dialogue": dialogue_id, "turn": turn_id,
                        "type": "must_not_say_appeared", "phrase": check["phrase"],
                    })

    return {
        "intent": {"correct": intent_correct, "total": intent_total},
        "slot_jga": {"correct": jga_correct, "total": jga_total},
        "action": {"correct": action_correct, "total": action_total},
        "over_escalation": {"count": over_esc_false_positive, "total": over_esc_total},
        "under_escalation": {"count": under_esc_missed, "total": under_esc_total},
        "tool": {"correct": tool_correct, "total": tool_total, "missing": tool_missing, "extra": tool_extra},
        "money": {"correct": money_correct, "total": money_total},
        "booking": {"correct": booking_correct, "total": booking_total},
        "per_dialogue_cost": per_dialogue_cost,
        "total_cost": sum(per_dialogue_cost.values()),
        "per_dialogue_seconds": per_dialogue_seconds,
        "latency_median": statistics.median(latencies) if latencies else 0.0,
        "latency_p95": percentile(latencies, 95),
        "verdicts": verdicts,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Combining the five runs
# ---------------------------------------------------------------------------


def rate_series(per_run: list[dict]) -> dict:
    """
    Turn five {"correct":, "total":} dicts into a median[low-high] summary.

    The denominator only ever depends on the frozen dialogue files, never on
    what the model did, so it is the same every run - only "correct" moves.

    Params: per_run - one {"correct":, "total":} dict per run.
    Return: {"total":, "correct": median_low_high(...)}
    """
    total = per_run[0]["total"]
    corrects = [run["correct"] for run in per_run]
    return {"total": total, "correct": median_low_high(corrects)}


def count_series(per_run: list[dict], key: str) -> dict:
    """median[low-high] of one raw count (not a rate) across the five runs."""
    return median_low_high([run[key] for run in per_run])


def dialogue_summary(per_run_scores: list[dict]) -> dict[str, dict]:
    """
    "Passed N of 5" plus the first run that failed, per dialogue.

    Params: per_run_scores - the five score_run() results, in run order.
    Return: {dialogue_id: {"passed": N, "first_failure_run":, "first_failure":}}
    """
    dialogue_ids = sorted(per_run_scores[0]["verdicts"])
    summary = {}
    for dialogue_id in dialogue_ids:
        passed = sum(1 for scores in per_run_scores if scores["verdicts"][dialogue_id]["passed"])
        first_failure_run = None
        first_failure_text = None
        for run_label, scores in zip(RUNS, per_run_scores):
            verdict = scores["verdicts"][dialogue_id]
            if not verdict["passed"]:
                first_failure_run = run_label
                first_failure_text = verdict["first_failure"]
                break
        summary[dialogue_id] = {
            "passed": passed,
            "out_of": len(RUNS),
            "first_failure_run": first_failure_run,
            "first_failure": first_failure_text,
        }
    return summary


def cost_summary(per_run_scores: list[dict]) -> dict:
    """Per-dialogue and total cost, median[low-high] across the five runs."""
    dialogue_ids = sorted(per_run_scores[0]["per_dialogue_cost"])
    per_dialogue = {
        dialogue_id: median_low_high([scores["per_dialogue_cost"][dialogue_id] for scores in per_run_scores])
        for dialogue_id in dialogue_ids
    }
    total = median_low_high([scores["total_cost"] for scores in per_run_scores])
    return {"per_dialogue": per_dialogue, "total": total}


def seconds_summary(per_run_scores: list[dict]) -> dict:
    """Per-dialogue total seconds, median[low-high] across the five runs."""
    dialogue_ids = sorted(per_run_scores[0]["per_dialogue_seconds"])
    return {
        dialogue_id: median_low_high([scores["per_dialogue_seconds"][dialogue_id] for scores in per_run_scores])
        for dialogue_id in dialogue_ids
    }


def run_table(run_labels: list[str]) -> dict:
    """api_calls, the four token counts, and cost, straight from each summary.json."""
    table = {}
    for run_label in run_labels:
        summary = load_run_summary(run_label)
        tokens = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        api_calls = 0
        for dialogue in summary["dialogues"]:
            api_calls += dialogue["api_calls"]
            for key in tokens:
                tokens[key] += dialogue["tokens"][key]
        table[run_label] = {"api_calls": api_calls, "tokens": tokens, "cost": usage_cost(tokens)}
    return table


def silence_temptation() -> dict:
    """
    How often the model would have replied on a turn the guard silenced.

    Read only from run-01, the one run with --silence-probe.

    Return: {"broke_silence":, "total_probed":}.
    """
    broke_silence = total_probed = 0
    for turns in load_run_dialogues("run-01").values():
        for turn in turns:
            if turn.get("probe_broke_silence") is not None:
                total_probed += 1
                if turn["probe_broke_silence"]:
                    broke_silence += 1
    return {"broke_silence": broke_silence, "total_probed": total_probed}


# ---------------------------------------------------------------------------
# Writing the two output files
# ---------------------------------------------------------------------------


def fmt_rate(rate: dict) -> str:
    """'correct/total [low/total - high/total]' for one rate_series() result."""
    total = rate["total"]
    c = rate["correct"]
    return f"{c['median']:.0f}/{total} [{c['low']:.0f}/{total} - {c['high']:.0f}/{total}]"


def fmt_count(count: dict) -> str:
    """'median [low - high]' for one count_series() result, whole numbers."""
    return f"{count['median']:.0f} [{count['low']:.0f} - {count['high']:.0f}]"


def fmt_seconds(count: dict) -> str:
    """'median [low - high]' for a seconds value, three decimals."""
    return f"{count['median']:.3f} [{count['low']:.3f} - {count['high']:.3f}]"


def fmt_dollars(count: dict) -> str:
    """'median [low - high]' for a dollar value, four decimals."""
    return f"${count['median']:.4f} [${count['low']:.4f} - ${count['high']:.4f}]"


def collapse_must_not_say(violations: list[dict]) -> list[dict]:
    """
    One row per must_not_say phrase, across all five runs.

    Reply wording is not scored, so this is a log, not a metric - but a
    phrase that keeps appearing is still worth a human's eye, hence one row
    per phrase instead of one row per occurrence.

    Params: violations - every run's violations (score_run's "violations").
    Return: [{"phrase":, "runs_hit":, "locations": [(dialogue, turn), ...]}],
            most-hit phrase first.
    """
    by_phrase: dict[str, dict] = {}
    for v in violations:
        if v["type"] != "must_not_say_appeared":
            continue
        entry = by_phrase.setdefault(v["phrase"], {"runs": set(), "locations": set()})
        entry["runs"].add(v["run"])
        entry["locations"].add((v["dialogue"], v["turn"]))

    rows = [
        {"phrase": phrase, "runs_hit": len(entry["runs"]), "locations": sorted(entry["locations"])}
        for phrase, entry in by_phrase.items()
    ]
    rows.sort(key=lambda row: (-row["runs_hit"], row["phrase"]))
    return rows


def build_report_md(
    table: dict, metrics: dict, dialogues: dict, cost: dict, seconds: dict, silence: dict,
    must_not_say_rows: list[dict],
) -> str:
    """Lay out everything computed above as Markdown for a person to read."""
    lines = ["# Phase 6 scoring report", ""]

    lines.append("## Run table")
    lines.append("")
    lines.append("| run | api_calls | input | output | cache_creation | cache_read | cost |")
    lines.append("|---|---|---|---|---|---|---|")
    for run_label in RUNS:
        row = table[run_label]
        t = row["tokens"]
        lines.append(
            f"| {run_label} | {row['api_calls']} | {t['input_tokens']} | {t['output_tokens']} | "
            f"{t['cache_creation_input_tokens']} | {t['cache_read_input_tokens']} | ${row['cost']:.4f} |"
        )
    lines.append("")
    lines.append(
        "run-01 was run with --silence-probe. Its 6 probe calls are counted in "
        "api_calls but their tokens were not recorded, so run-01's cost understates its "
        "true spend by about $0.03. Fixed in engine.py; the saved run-01 data is left "
        "as it is."
    )
    lines.append("")

    lines.append("## Metrics across the five runs")
    lines.append("")
    lines.append("| metric | median [lowest - highest] |")
    lines.append("|---|---|")
    lines.append(f"| 1. intent accuracy | {fmt_rate(metrics['intent'])} |")
    lines.append(f"| 2. slot accuracy (JGA) | {fmt_rate(metrics['slot_jga'])} |")
    lines.append(f"| 3. action accuracy | {fmt_rate(metrics['action'])} |")
    lines.append(
        f"| 4. over-escalation | {fmt_count(metrics['over_escalation'])} of "
        f"{metrics['over_escalation_total']} (never a %) |"
    )
    lines.append(
        f"| 5. under-escalation | {fmt_count(metrics['under_escalation'])} of "
        f"{metrics['under_escalation_total']} (never a %) |"
    )
    lines.append(f"| 6. tool accuracy | {fmt_rate(metrics['tool'])} |")
    lines.append(f"| 6b. tools missing (total) | {fmt_count(metrics['tool_missing'])} |")
    lines.append(f"| 6c. tools extra (total) | {fmt_count(metrics['tool_extra'])} |")
    lines.append(f"| 7. money accuracy (per key) | {fmt_rate(metrics['money'])} |")
    lines.append(f"| 8. booking accuracy (per key) | {fmt_rate(metrics['booking'])} |")
    lines.append(f"| 10. latency, median seconds | {fmt_seconds(metrics['latency_median'])} |")
    lines.append(f"| 10. latency, p95 seconds | {fmt_seconds(metrics['latency_p95'])} |")
    lines.append("")
    lines.append("Metric 9 (cost) is its own section below.")
    lines.append("")

    lines.append("## Cost per dialogue")
    lines.append("")
    lines.append("| dialogue | cost, median [lowest - highest] | seconds, median [lowest - highest] |")
    lines.append("|---|---|---|")
    for dialogue_id in sorted(cost["per_dialogue"]):
        lines.append(
            f"| {dialogue_id} | {fmt_dollars(cost['per_dialogue'][dialogue_id])} | "
            f"{fmt_seconds(seconds[dialogue_id])} |"
        )
    lines.append(f"| **total per conversation (all 11)** | **{fmt_dollars(cost['total'])}** | |")
    lines.append("")

    lines.append("## Per-dialogue verdicts")
    lines.append("")
    for dialogue_id in sorted(dialogues):
        d = dialogues[dialogue_id]
        lines.append(f"- {dialogue_id}: passed {d['passed']} of {d['out_of']}")
    lines.append("")

    failed = {k: v for k, v in dialogues.items() if v["passed"] < v["out_of"]}
    if failed:
        lines.append("### Failures - first critical failure per dialogue")
        lines.append("")
        for dialogue_id in sorted(failed):
            d = failed[dialogue_id]
            failed_count = d["out_of"] - d["passed"]
            lines.append(f"{dialogue_id} failed {failed_count} of {d['out_of']} - {d['first_failure']}")
        lines.append("")

    lines.append("## Silence temptation")
    lines.append("")
    lines.append(
        f"On {silence['broke_silence']} of {silence['total_probed']} silenced turns "
        "the model would have replied (run-01, --silence-probe)."
    )
    lines.append("")

    lines.append("## Reply wording")
    lines.append("")
    lines.append(
        "Reply wording is not scored. must_say phrases were logged but are not part of "
        "any metric - there are many correct ways to say the same thing. Comparing the "
        "agent's wording against a reference answer is future work."
    )
    lines.append("")
    lines.append(SHORT_PHRASE_WARNING)
    lines.append("")
    if must_not_say_rows:
        lines.append("| phrase | runs it appeared in (of 5) | where |")
        lines.append("|---|---|---|")
        for row in must_not_say_rows:
            where = ", ".join(f"{dialogue} t{turn}" for dialogue, turn in row["locations"])
            lines.append(f"| {row['phrase']!r} | {row['runs_hit']} | {where} |")
    else:
        lines.append("No must_not_say phrase ever appeared.")
    lines.append("")

    lines.append("## Known limits")
    lines.append("")
    for limit in KNOWN_LIMITS:
        lines.append(f"- {limit}")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    """Score all five runs and write scores.json and report.md."""
    tools_expected = load_tools_expected()
    per_run_scores = [score_run(run_label, tools_expected) for run_label in RUNS]

    metrics = {
        "intent": rate_series([s["intent"] for s in per_run_scores]),
        "slot_jga": rate_series([s["slot_jga"] for s in per_run_scores]),
        "action": rate_series([s["action"] for s in per_run_scores]),
        "over_escalation": count_series([s["over_escalation"] for s in per_run_scores], "count"),
        "over_escalation_total": per_run_scores[0]["over_escalation"]["total"],
        "under_escalation": count_series([s["under_escalation"] for s in per_run_scores], "count"),
        "under_escalation_total": per_run_scores[0]["under_escalation"]["total"],
        "tool": rate_series([s["tool"] for s in per_run_scores]),
        "tool_missing": count_series([s["tool"] for s in per_run_scores], "missing"),
        "tool_extra": count_series([s["tool"] for s in per_run_scores], "extra"),
        "money": rate_series([s["money"] for s in per_run_scores]),
        "booking": rate_series([s["booking"] for s in per_run_scores]),
        "latency_median": median_low_high([s["latency_median"] for s in per_run_scores]),
        "latency_p95": median_low_high([s["latency_p95"] for s in per_run_scores]),
    }
    dialogues = dialogue_summary(per_run_scores)
    cost = cost_summary(per_run_scores)
    seconds = seconds_summary(per_run_scores)
    table = run_table(RUNS)
    silence = silence_temptation()
    all_violations = [v for s in per_run_scores for v in s["violations"]]
    must_not_say_rows = collapse_must_not_say(all_violations)

    scores = {
        "runs": RUNS,
        "run_table": table,
        "metrics": metrics,
        "dialogues": dialogues,
        "cost": cost,
        "seconds": seconds,
        "silence_temptation": silence,
        "violations": all_violations,
        "must_not_say_summary": must_not_say_rows,
        "known_limits": KNOWN_LIMITS,
    }
    SCORES_PATH.write_text(json.dumps(scores, indent=2, default=str), encoding="utf-8")

    report = build_report_md(table, metrics, dialogues, cost, seconds, silence, must_not_say_rows)
    REPORT_PATH.write_text(report, encoding="utf-8")

    print(f"saved {SCORES_PATH}")
    print(f"saved {REPORT_PATH}")


if __name__ == "__main__":
    main()
