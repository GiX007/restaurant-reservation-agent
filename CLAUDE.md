# Rules for working on this project

## Code style
- Python only.
- Simple code. Standard library first. Ask before adding any dependency.
- Short functions, one job each.
- Type hints always: def book(name: str, party: int) -> bool:
- Short docstring on every function: purpose, params, return.
- Simple comments in simple words explaining what happens.
  Comment the logic, not obvious lines. I must understand every line.
- No clever tricks.

## How to work with me
- One concept per response. Do not build ahead.
- Explain briefly why we need it and how it works.
- Give a minimal test for that one thing only.
- Then stop and ask if I understood. Wait for my OK.
- Be direct and honest. If an approach is weak, say so.
- If an approach gets messy, explain the better one before changing code.

## Project facts
- Target venue: Interni (Mykonos). Uses i-host. No API available yet.
- Channels: email, WhatsApp, phone. Text first, voice last.
- A dialogue can be 10-15 turns. State must persist across turns.
- Must be re-skinnable to another restaurant by editing config files only.
