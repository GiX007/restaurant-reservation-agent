# restaurant-reservation-agent

Reservations arrive by WhatsApp, by email and by phone. This agent reads the
venue's rules and its bookings database, then answers on its own, at any hour.

Five actions: **book, modify, cancel, inform, escalate to a human.**

**Scope.** A prototype. Venue X, its rules and all the data here are fictional.

---

## What it does

![How a conversation flows](docs/flow.svg)

On every message the agent reads the venue's rules and calls its tools for a
price, for a free table, to look up a guest or to save a booking. It informs,
books, changes and cancels. When only a person can decide, it hands over and
stops.

---

## Demo

<!-- TODO (phase 7): Hugging Face Space link + a short gif of a real conversation -->

*Coming in phase 7: a live Space and a recorded conversation.*

---

## How it works

Six layers, each with one job. The point of the split is that the language
model only handles language and it never computes a figure and never decides
whether a table is free.

| layer | its job | where it lives |
|---|---|---|
| instructions | who the agent is, how it speaks, when it stops | `prompts/` |
| policy as data | the venue's rules, as JSON the code reads | `config/policy.json` |
| deterministic tools | money, availability, reading and writing bookings | `tools/` *(phase 5)* |
| guardrails | check the request before acting, check the reply before sending | *(phase 5)* |
| human handoff | pass everything known to a person, then go quiet | `config/policy.md` §13 |
| evaluation | 58 policy cases, 11 dialogues, five metrics | `evals/` |

Re-skinning to another restaurant means editing the second row. Nothing else.

---

## Contents

- config/
  policy.md          the venue's rules, for people
  policy.json        the same rules, for the code
  tables.json        the inventory: tables, chairs, caps
  faq.md             what the agent may answer from
- db/
  schema.py          bookings + customers
  seed.py            a fake week, built to hit the hard cases
- evals/
  policy_cases.md    58 worked examples - the answer key for the functions
  schema.py          the frozen vocabulary for dialogue files
  dialogues/         11 hand-written conversations, 121 turns
- prompts/
  few_shot.md        four examples: refuse vs escalate vs just answer
- tests/

---

## Evaluation

<!-- TODO (phase 6): fill in from a real run -->

| metric | what it answers | score |
|---|---|---|
| money accuracy | did it quote the right figure? | — |
| action accuracy | did it pick the right one of five? | — |
| joint goal accuracy | did it track the booking state? | — |
| over-escalation | did it wake a person unnecessarily? | — |
| under-escalation | did it decide something it should not have? | — |

Reply wording is deliberately **not** scored. What matters is the figure and
the decision, not the phrasing.

The eleven dialogues follow the shape of MultiWOZ 2.2. User turns carry a belief
state, system turns carry an action. They are written by hand and kept unseen
by the model, so they stay a test set.

---

## Run it

<!-- TODO (phase 5): install, seed the DB, run the agent -->

```bash
# coming in phase 5
```

---

## Status

- [x] 0 · Setup
- [x] 1 · Policy rules
- [x] 2 · Fake restaurant + reservations DB
- [x] 3 · Test dialogues (11 hard cases, by hand)
- [ ] 4 · Architecture
- [ ] 5 · Engine
- [ ] 6 · Evaluation metrics
- [ ] 7 · Web UI (Gradio + Hugging Face Spaces)
- [ ] 8 · Channels: email, then WhatsApp, then voice

**Stack:** Python 3.11, SQLite, JSON, Anthropic API

---

## What comes next

Ideas beyond the phases above, roughly in the order they would pay off.

- **It shows what the rules are missing.** Every time it hands over, it has
  hit a question the rules do not cover. Write those answers into the policy
  and it handles them by itself next time.
- **More languages.** Today it refuses politely in a language it does not
  know. It should just reply in it.
- **Stricter moderation.** A public number gets rude messages, spam, and people
  trying to trick the agent. It should spot these and stop.  
- **Menu and allergens.** These questions go to a person today because no
  file can answer them. One menu file would fix that.
- **A real booking system.** The bookings live in SQLite here. A restaurant's
  own system would go behind the same tools.
- **Voice.** Same agent, speech in and speech out. The hard part is that a
  caller will not wait while a tool runs.
- **Many venues, one install.** A restaurant is just a `config/` folder.
- **A stronger engine.** Several agents instead of one, a fine-tuned model,
  prompt tuning with DSPy. Keep whichever one raises the scores above.

---

## License

MIT - see [LICENSE](LICENSE).
