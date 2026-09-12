# reservation-agent
AI agent that handles restaurant reservations arriving by email, WhatsApp and phone calls.
Takes a customer message, reads the reservations database and the venue policy, then answers.
Five actions: book, modify, cancel, inform, escalate to human.

## Status
Phase 0 - setup

## Phases
- [x] 0. Setup
- [ ] 1. Policy rules
- [ ] 2. Fake restaurant + reservations DB
- [ ] 3. Test dialogues (10 hard cases, by hand)
- [ ] 4. Architecture
- [ ] 5. Engine
- [ ] 6. Evaluation metrics
- [ ] 7. Web UI (Gradio + Hugging Face Spaces)
- [ ] 8. Channels: email, then WhatsApp, then voice

## Stack
Python 3.11, SQLite, JSON, Anthropic API
