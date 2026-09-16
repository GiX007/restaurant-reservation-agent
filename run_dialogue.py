### run_dialogue.py. Run with: python run_dialogue.py [path]
"""Load one dialogue file and print its turns.

Step 1 of Phase 5. Does nothing else yet - no tools, no model, no checking.
"""

import json
import sys
from pathlib import Path

DEFAULT_PATH = "evals/dialogues/dlg-01-party-size-change.json"


def load_dialogue(path: str) -> dict:
    """
    Read one dialogue JSON file.

    Params: path - path to the dialogue file.
    Return: the parsed dialogue, as a dict.
    """
    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def print_turns(dialogue: dict) -> None:
    """
    Print every turn of a dialogue, one line each.

    Params: dialogue - a dict loaded by load_dialogue.
    Return: None.
    """
    print(dialogue["dialogue_id"], "-", len(dialogue["turns"]), "turns")
    for turn in dialogue["turns"]:
        # HUMAN turns have no "utterance" key check needed - they do have one.
        print(f'  [{turn["turn_id"]}] {turn["speaker"]}: {turn["utterance"]}')


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    dialogue = load_dialogue(path)
    print_turns(dialogue)
