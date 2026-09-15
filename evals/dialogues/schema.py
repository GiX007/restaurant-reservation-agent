### evals/schema.py
"""The frozen vocabulary for dialogue files. One source of truth.

    guest message  ->  state (the order)  ->  functions  ->  facts (the answer)  ->  words

`state` is the input: what the guest wants, as far as the agent knows it -
from what they typed, from the channel, and from the customer record.
`facts` is the output: what the deterministic code worked out from it.

They are graded separately on purpose. `state` can be measured by JGA (did the
agent hear the order?), `facts` by money accuracy (did it compute the right
answer?). An agent can get one right and the other wrong.
"""

# Who is speaking in a turn.
#   USER   - the guest. Carries "intent" and "state".
#   SYSTEM - the agent. Carries "action", "facts", "must_say", "must_not_say",
#            "conversation_status".
#   HUMAN  - the reservation manager, after an escalation. Carries only
#            "utterance": it is input to the agent, never graded. A HUMAN turn
#            is how a conversation is handed back (policy.md #13).
SPEAKERS = {"USER", "SYSTEM", "HUMAN"}

# USER turns
INTENTS = {"book", "modify", "cancel", "inform", "bye"}

STATE_SLOTS = {
    "name", "party_size", "date", "start_time", "product", "phone", "email",
}

# SYSTEM turns. "none" is greetings and goodbyes - skipped in action accuracy.
ACTIONS = {"book", "modify", "cancel", "inform", "escalate", "none"}

# Optional on any turn: "at" (ISO timestamp) when the clock has moved since the
# previous turn - a hold expiring hours later, a manager replying later.
# Absent means the same moment as the turn before, and turn 0 means `now`.
TURN_KEYS_OPTIONAL = {"at"}

# Every key allowed in a `facts` dict. Absent = not checked at this turn.
FACT_KEYS = {
    # money
    "minimum_spend_pp",
    "minimum_spend_total",
    "deposit_eur",
    "deposit_deducted_from_bill",
    "deposit_kept",
    "refund_eur",
    # capacity
    "buffer_wait_disclosed",
    "alternative_dates",
    # booking record
    "booking_created",
    "booking_status",
    "booking_party_size",
    "booking_found",
    "payment_link_sent",
    "hold_hours",
    "hold_expires_at",
    "cancel_reason",
    "modify_allowed",
    # conversation
    "escalated",
    "escalation_reason",
    "agent_replied",
    "reply_language",
    # identity
    "customer_found",
}
