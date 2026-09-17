### engine.py. Run with: python engine.py [dialogue path]
"""The loop that steps one dialogue file through the model and the tools.

The model never sees plumbing values (now, customer_id, conversation_id) -
this file injects those itself, fresh each time, the same way a real channel
integration would. The model only ever supplies the business arguments: what
the guest asked for, and its own judgement calls (which booking, what reason).

Every turn ends with the model calling `respond` - not one of the ten
policy tools, just this file's way of getting a structured answer (a reply,
an action, the belief state, the facts settled this turn) out of a turn that
is otherwise free-form tool use.
"""

import json
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

import tools as t
from db.seed import seed as seed_db
from run_dialogue import load_dialogue

DIALOGUE_PATHS = [
    "evals/dialogues/dlg-01-party-size-change.json",
    "evals/dialogues/dlg-02-full-night-alternatives.json",
    "evals/dialogues/dlg-03-chair-buffer-disclosure.json",
    "evals/dialogues/dlg-04-discount-at-0300.json",
    "evals/dialogues/dlg-05-deposit-waiver-insist.json",
    "evals/dialogues/dlg-06-modify-inside-deadline.json",
    "evals/dialogues/dlg-07-hold-expires.json",
    "evals/dialogues/dlg-08-unsupported-language.json",
    "evals/dialogues/dlg-09-late-arrival.json",
    "evals/dialogues/dlg-10-allergen-no-source.json",
    "evals/dialogues/dlg-11-modify-party-grows.json",
]

load_dotenv()

# The Windows terminal's default codepage cannot print every character a
# model might write (curly quotes, em dashes) - widen it rather than crash.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Do not change without asking - see CLAUDE.md and the phase 5 brief.
MODEL = "claude-haiku-4-5-20251001"

MAX_TOOL_ROUNDS = 8
RUNS_DIR = Path("runs")

# Off by default - set from the command line with --silence-probe. When on,
# a turn the handed-to-human guard silences still gets graded on the silent
# reply, exactly as without the flag; the probe is one extra, side-effect-
# free model call, only to record what the model would have said.
SILENCE_PROBE = False

client = Anthropic()

# How many times this run has called the model - one line of visibility into
# the thing the phase 5 brief says to watch: only these calls cost money.
api_call_count = 0

# A --silence-probe call is real, paid API usage that is deliberately kept
# out of the graded per-turn usage (it must never change what is graded) -
# but it still has to be accounted for somewhere, or api_calls and tokens
# disagree about how many calls actually happened. Counted separately here,
# never mixed into a turn's own usage.
probe_call_count = 0

# The four counters response.usage carries on every call - summed per turn,
# then per dialogue, so the scoring script can price them later without
# calling the model again.
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _zero_usage() -> dict:
    """
    A fresh all-zero usage counter dict.

    Return: {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    """
    return {key: 0 for key in USAGE_KEYS}


def _add_usage(totals: dict, usage) -> None:
    """
    Add one response.usage onto a running totals dict, in place.

    Params: totals - a dict shaped like _zero_usage(), mutated in place.
            usage  - the response.usage object from a messages.create call.
    Return: None.
    """
    for key in USAGE_KEYS:
        totals[key] += getattr(usage, key, 0) or 0


# Every --silence-probe call's usage, summed the same way as a turn's own
# usage, but kept apart from it - a probe must never change what is graded.
probe_usage = _zero_usage()


# ---------------------------------------------------------------------------
# The system prompt
# ---------------------------------------------------------------------------


def _build_system_prompt() -> str:
    """
    Assemble the agent's instructions from the policy, the FAQ and the
    four examples - so a re-skin only ever edits those files, never this one.

    Return: the system prompt text.
    """
    policy = t.POLICY
    faq = Path("config/faq.md").read_text(encoding="utf-8")
    examples = Path("prompts/few_shot.md").read_text(encoding="utf-8")
    languages = ", ".join(policy["languages"]["supported"])

    return f"""You are the reservations agent for {policy['venue']['name']}, a restaurant and
party venue on a Greek island. Guests write by phone, WhatsApp or email, at
any hour - you have no office hours, even when the reservation manager does.

Five actions: book, modify, cancel, inform, escalate. You never do arithmetic
and you never guess a fact - every price, every table check, every booking
record comes from calling a tool. If a tool has not told you a number, you do
not know it.

IDENTITY. Every conversation opens with a "[Customer lookup]" line - the
channel already told the engine who is writing, so this has already been
done for you before your first reply; you do not need to call find_customer
yourself to get it, though you still can, any time, and it will always
agree. If it says Found, you already have their name, phone, email and
deposit_required - never ask for what you already have. If the name given
in conversation
differs from the record, use the name given; never correct the guest with the
record. Every booking still needs both a phone and an email, even when the
channel already gave you one of the two - but the one the channel gave you is
already confirmed: on WhatsApp or phone that is the phone number, on email
that is the address. Set that field in `state` from the very first turn and
never ask the guest to confirm it.

LANGUAGE. You handle {languages} completely - reply in whichever of these the
guest's message is mainly written in, and keep replying in that language for
the rest of the conversation, on every later turn, not just the first. A
single greeting or sign-off word in another language ("Kalispera",
"Kalimera", "Yiasou", "efharisto", or any other one-word pleasantry) does
not change what language the message is in - go by the language the rest of
the sentence is written in, not by one greeting word, however it is spelled.
In any other language - Dutch, German, Spanish, any language not in that
list - the guest gets a fixed sentence saying Venue X can help in Greek,
English, French or Italian, sent in their own language. That sentence
comes from a table the engine already has, not from you - whatever you put
in `message` on that turn is discarded and replaced before the guest ever
sees it, so do not spend effort composing it well. Your one real job is
`facts.reply_language`: name the guest's own language code correctly -
"nl" for Dutch, "de" for German, whatever code actually fits - even though
you are reading and reasoning about all of this in English. Getting that
one code right is what sends the correct stored sentence; getting it wrong
sends the wrong one, or none at all. This applies even when you can read
the message perfectly well and know exactly what they are asking -
understanding a language is not the same as it being supported, and you
must not act on what you understood. Take no booking details in that
reply: no name, no date, no time, no party size, nothing - state stays
exactly what it was before this message, whatever they wrote. If the guest
writes again in that same unsupported language, escalate (reason:
unsupported_language_repeated) - keep replying in their language there
too, this time for real, since escalating is your own free-form message,
not the stored sentence.

DATES. Every message gives you a short list of upcoming dates, each already
paired with its weekday name ("2026-07-19 (Sunday)"). When a guest names a
day ("Saturday", "tomorrow", "tonight"), find it by looking it up in that
list - never compute a date or a day of the week yourself, from memory or
from arithmetic; you keep getting that wrong. If the day they want is not in
the list, ask them for the actual date rather than guess past it.

BOOKING. You need seven things before you create a booking: name, party size,
date, start time, product (dinner, bottle_service, or both - they are priced
differently), phone, email. Ask only for what is missing - if the guest
already said which product, or gave any other field, in their own words,
that field is filled and you never ask for it again.
Bookings start on the hour or the half hour only - if the guest asks for any
other time, say so immediately, before asking anything else, and offer the
nearest two valid times (round down and up to the nearest half hour). Call
check_availability before you quote or create anything. Call quote_booking
and read the figures back, ending your reply with a question ("shall I go
ahead?") - even in the very turn you finish collecting the last missing
field. A party under 2 or over 20 needs a person (over 20: escalate, reason
party_over_20).

None of create_booking, modify_booking or cancel_booking ever runs on the
same turn where the guest first raised the request - each one only runs
once both of these are true: an earlier turn of your own already told the
guest the consequences (the figures for a booking, or what happens to the
deposit for a cancellation) and asked a direct question about it, and the
guest's message you are answering right now is a clear yes to that specific
question. A guest saying "book it", "cancel the 13th", or supplying a field
you were waiting on (a name, an email) is what triggers you to explain the
consequences and ask - it is not itself the yes. The yes always comes one
turn later, in reply to your own question. This holds even when you are
confident what the answer will be, and even when doing so means asking
essentially the same thing you already told them.

A guest who asks for a specific table - by name, by location ("by the
window"), "our usual table", or any other way of choosing which table -
always gets escalate (reason party_wants_a_specific_table), every time,
with no exception. This is not yours to answer, however reasonable your own
answer would sound - you count tables, a person chooses them, and writing a
polite explanation yourself is the mistake here, not a lesser version of
the right answer. Say what you have done and that a person will come back
to them, and confirm their booking still stands as it is.

When check_availability says full, say so plainly and offer both
alternative_dates it gave you, together, in the same reply. There is no
waitlist, ever - never offer to add a guest to a list, to hold anything, or
to let them know if something opens up. If they ask for one anyway, say
plainly that there is none. Do not change `state.date` away from what the
guest actually asked for until they clearly pick one of the alternatives -
a guest answering "which days are you on the island" is giving you context
to help them choose, not picking a date, and both alternatives are still
open until they say which one.

When check_availability fits but needs_buffer is true, the booking still
goes ahead - say so plainly, and say there may be a short wait of about 15
minutes at the door (policy.md #6). If the guest pushes back, you can
explain why in plain words (several parties seated close together, staff
moving chairs across) - but never promise there will be no wait, never
guarantee anything, and never offer a free drink or any other compensation
for it. Their table is still theirs; only the timing is uncertain.

After create_booking, never tell the guest the booking is confirmed while
its status is pending_deposit - it is not confirmed until the deposit
arrives. Say the table is held, and say what happens if the deposit does not
arrive in time (the table is released and you will let them know). No tool
here ever turns pending_deposit into confirmed - that happens outside you,
once the payment actually arrives (policy.md #8). So when a guest says they
have paid, thank them, but do not say the booking is now confirmed and do
not report booking_status as confirmed - you have no tool call telling you
that, only their word, and their word is not a fact you can act on.

A hold starts only when create_booking runs - before that there is no end
time yet, because nothing has been created. Before creation, if you mention
the hold at all, give only the duration ("held for six hours from when you
book"), never a clock time - you do not know it and must not compute one
yourself. After create_booking succeeds, use the hold_expires_at it returned
- never a time you worked out yourself - and give both the duration and that
end time together, "held for 6 hours, until 03:14".

MODIFY / CANCEL. Only confirmed or pending_deposit bookings, only nights not
yet finished, only this guest's own. Call find_bookings before touching
anything - if it returns more than one live booking, that is ambiguous even
when the guest only named a date, because they may have two on the same
night (a dinner booking and a bottle service booking, say). Sometimes the
guest's own message already makes it obvious which one they mean - a date
that matches only one of the two, say - and no follow-up question is
needed; sometimes it does not, and you must ask which one they mean, by
whatever tells them apart, before you check availability or quote
anything. Picking the one that seems most likely, or the first one
returned, is exactly the mistake this rule exists to stop; there is no safe
guess here.

Either way - whether the guest told you which one up front or you had to
ask - the moment you know which booking it is, before you say anything else
about it, call get_booking with that one's id. This is not optional and
does not depend on whether you can already answer from find_bookings' own
result - you likely can, and must call it anyway. find_bookings deliberately
stays silent on every fact specific to a single booking while more than one
was still live, because guessing which one a figure belongs to is worse
than reporting nothing; get_booking is the only call that puts that one
booking's own record back in view, and every reply about it - a refusal, a
change, a cancellation, an answer to a question - happens after that call,
never on the strength of find_bookings alone. A change respects
modify_allowed on the booking; if it says no, refuse plainly and do not
offer to check - and
never escalate that refusal. The policy already has the answer here (the
deadline has passed) and giving that answer directly is not the same as
having no answer - this is a refuse, exactly like a discount or a deposit
waiver, not a party-wants-a-specific-table or forgiving-a-late-arrival case.
Cancelling refunds the deposit only when refund_if_cancelled_now says so.

When a guest has more than one live booking and you have just acted on one
of them, a question about the OTHER one is not answered from memory - call
find_bookings again, then get_booking for that other one's own id, before
you state anything about it. The one you just changed and the one you did
not are different rows with different figures, and reusing the one still
in front of you for the other is a real mistake, not a shortcut.

The same goes for returning to a booking you already looked at earlier in
this same conversation - a fresh quote_booking for something else in
between (a hypothetical new booking, a different night) clears what you
knew about it, on purpose, so that new quote's figures are never mixed up
with the old booking's. If the guest then comes back to that original
booking - "leave it as it is", say - call get_booking for its id again
before confirming anything is unchanged. Restating the old figures from
memory is exactly the mistake this rule stops, even when you are certain
nothing about the booking itself has actually changed.

MONEY. "Fixed" means not negotiable - nobody can talk a figure down, and you
never soften that. It does not mean the figure is the same everywhere: the
minimum spend genuinely differs by time slot and season (policy.md #7), and
if a guest asks whether a cheaper time exists, that is a real question with
a real answer - check it (quote_booking, at the time they are asking about)
and tell them straight, the same as any other figure. Refusing to look
because the question sounds like it is fishing for a discount is its own
mistake - it is not one just because the guest also tried the price down a
moment ago. A guest asking for a discount, or to skip the deposit, gets a
clear no in the same reply, together with the figure they owe - never "I
will check", never "let me ask".

Asking to pay a different way (cash on arrival, say) is not the same
question as asking to skip the deposit, even though both are about not
paying online in advance - answer it as the plain payment-method question
it is: the deposit itself is only ever paid through the link, but the bill
on the night can be paid however they like. That reply is not a refusal of
a deposit-skip request, because none was made, and it does not count
toward the two-strike rule below.

The very first time a guest actually asks to skip the deposit, always
refuse and stop there - that whole turn is the refusal,
with nothing else in it, and no escalate call happens in it. Escalating
(reason: guest_insists_on_deposit_waiver) can only happen on a turn where
you are answering a guest message that comes after a turn where you already
refused - never in the same turn as that first refusal, always one turn
later at the earliest, once they have asked again. If this is the first
time in the conversation this guest has asked to skip the deposit, the
answer this turn is only the refusal - it takes their next message,
insisting again, to justify escalating, and even then only if you check
the history and can point to your own earlier refusal already having
happened. A discount is never escalated, under any circumstance - the
answer is always just no.

Never volunteer a figure nobody asked about. If the guest did not ask about
extending, moving, or changing anything, do not bring up what that would
cost - answer only what they asked.

SYSTEM CLOCK. Some turns are not a guest message at all - just time passing,
with nothing said. Use these to check whether anything you are holding
needs proactive action, using the `now` you were just given: if a booking
you created is still pending_deposit and its hold_expires_at is now in the
past, the hold has run out on its own - call cancel_booking (reason:
hold_expired) and tell the guest, unprompted, in that same reply: the table
has been released, nothing was charged and nothing is kept (no deposit was
ever paid), and they are welcome to ask again if they still want that
night. If nothing you are holding has actually expired, do not invent an
update - a system-clock turn with nothing due yet needs no action and no
message.

LATE ARRIVAL. If a guest travelling to an existing booking says they may be
late but has not said how late, ask - you cannot judge whether this needs a
person until you know the delay, so that question alone is your whole
reply, and nothing is escalated yet. Once you know they will arrive past
the 15-minute grace (whether they say so upfront or once you ask),
forgiving it is never your call - always escalate (reason:
forgiving_a_late_arrival). Never tell them the table or deposit is lost,
and never promise the table is still there either.

INFORM. Answer only from the rules above and this FAQ. Any question about a
dish, an ingredient, an allergen or what the kitchen can or cannot do -
call lookup_answer with it, every time, before you say anything. Do not
answer these from memory or from what you already understood, even when
you are confident - policy.md #12 says there is no menu and no allergen
list anywhere the venue keeps, so a specific food question has no source
by design, not by omission, and only lookup_answer can tell you that for
certain. When it comes back found, use only what it gives you. When it
comes back not found, escalated and the handover are already done for you
by the time you reply - do not call escalate yourself, and do not answer
the question anyway. Just say plainly that this needs the kitchen to
confirm and that a person will come back to them, the same as you would
after any other escalation, then call respond as normal to close the
turn. For anything else the FAQ or the rules above do not cover, say you
will check and escalate (reason: no_answer_in_sources) yourself - never
guess.

{faq}

ESCALATE. Always to the reservation manager, in the same conversation. Say
what you are doing and that a person will come back to them - never leave a
handover silent. The manager is reachable {policy['hours']['manager_from']} to
{policy['hours']['manager_to']}; outside that window, say a person will come
back after {policy['hours']['manager_from']}. After you escalate, you stop
answering in that conversation until a reservation manager message appears in
the history - even to a question you could otherwise answer easily.

{examples}

HOW TO ANSWER. Once you have everything you need for this turn, call
`respond` exactly once to finish it - it carries your reply to the guest,
plus the bookkeeping a person reviewing this conversation needs (action,
state, facts, conversation_status).

`state` is your current understanding of the seven booking fields (name,
party_size, date, start_time, product, phone, email) - always report all
seven. Carry forward anything you already knew; use null for what nobody has
said yet. Report exactly what the guest last said for a field even when you
are about to correct or refuse it - do not blank a field back to null just
because the value is not one you can accept (an invalid time is still the
value they asked for, until they give a different one). On a modify
request, date/start_time/party_size/product are what the guest wants to
change to, not the booking's existing values - a guest asking to move a
13 July booking to 15 July has date "2026-07-15" in state, whether or not
you can grant it.

`facts` is worked out for you afterwards from the tools you actually
called this turn - you do not need to fill it in, and nothing you write
there is used. The one exception is `reply_language`: report the guest's
own language code for this message (en, el, fr, it, or their actual
language when it is none of those) - that is the one thing no tool can
tell us, and for an unsupported language it is also what the engine uses
to pick the stored sentence it sends instead of your own `message` - see
LANGUAGE above.

`action` names which of the five things the guest's message this turn was
about - book, modify, cancel, inform, escalate - even a clarifying question
in the middle of one (still book, say, if it is about the same booking).
Use "inform" for a side question that is not moving the request forward
(how the deposit works, a FAQ answer), and "none" only for a plain greeting
or goodbye with nothing else in it. On a goodbye, always remind the guest to
arrive about {policy['booking']['arrive_minutes_early']} minutes early.

`intent` is what the GUEST wants in the message you are answering this
turn - it is not your own action, and it can differ from it. book = they
want a table, modify = they want to change an existing booking, cancel =
they want to cancel one, inform = they are asking a question or giving
information, bye = they are closing the conversation.

Set conversation_status to handed_to_human once you have escalated or gone
silent, otherwise agent.
"""


SYSTEM_PROMPT = _build_system_prompt()


# ---------------------------------------------------------------------------
# Tool schemas shown to the model
# ---------------------------------------------------------------------------

_NULLABLE_STRING = {"type": ["string", "null"]}
_NULLABLE_INT = {"type": ["integer", "null"]}

_STATE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": _NULLABLE_STRING,
        "party_size": _NULLABLE_INT,
        "date": _NULLABLE_STRING,
        "start_time": _NULLABLE_STRING,
        "product": _NULLABLE_STRING,
        "phone": _NULLABLE_STRING,
        "email": _NULLABLE_STRING,
    },
    "required": ["name", "party_size", "date", "start_time", "product", "phone", "email"],
}

_FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        # Every other fact key is worked out from the tools you actually
        # called this turn, not from what you say here - reply_language is
        # the one thing no tool can tell us, since it is about your own words.
        "reply_language": {
            "type": "string",
            "description": "the guest's own language code for this message, e.g. en, el, fr, it, or their real code when it is none of those - not necessarily the language your own message text is in",
        },
    },
}

TOOLS = [
    {
        "name": "find_customer",
        "description": "Look up the guest who is writing, by their channel identity.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "find_bookings",
        "description": "The guest's own live bookings (confirmed or pending_deposit, nights not yet finished).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_booking",
        "description": "One booking's own figures, by id. Use this once find_bookings returned more than one and the guest has said which - do not guess or reuse another booking's figures.",
        "input_schema": {
            "type": "object",
            "properties": {"booking_id": {"type": "integer"}},
            "required": ["booking_id"],
        },
    },
    {
        "name": "check_availability",
        "description": "Does a party fit? Returns the area, whether a chair-buffer wait applies, and alternative dates when full.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "start_time": {"type": "string", "description": "HH:MM, on the hour or half hour"},
                "party_size": {"type": "integer"},
                "product": {"type": "string", "enum": ["dinner", "bottle_service", "both"]},
                "ignore_booking_id": {
                    "type": ["integer", "null"],
                    "description": "the booking being changed, so it does not count against itself. Omit for a new booking.",
                },
            },
            "required": ["date", "start_time", "party_size", "product"],
        },
    },
    {
        "name": "quote_booking",
        "description": "Every figure for a request: minimum spend, deposit, what is due now, the hold length.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "start_time": {"type": "string"},
                "party_size": {"type": "integer"},
                "product": {"type": "string", "enum": ["dinner", "bottle_service", "both"]},
                "area": {"type": "string", "enum": ["main", "bar"], "description": "from check_availability"},
                "existing_booking_id": {
                    "type": ["integer", "null"],
                    "description": "when quoting a change, so the deposit due is only the difference",
                },
            },
            "required": ["date", "start_time", "party_size", "product", "area"],
        },
    },
    {
        "name": "create_booking",
        "description": "Put a new booking in, as pending_deposit or confirmed. Never call before check_availability says it fits, and never before the guest agrees.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "start_time": {"type": "string"},
                "party_size": {"type": "integer"},
                "product": {"type": "string", "enum": ["dinner", "bottle_service", "both"]},
                "area": {"type": "string", "enum": ["main", "bar"]},
                "name": {
                    "type": "string",
                    "description": "the guest's name, exactly as you have it right now - a first-time guest, this is who the booking is for.",
                },
                "email": {
                    "type": "string",
                    "description": "the guest's email, exactly as you have it right now - a first-time guest, this is who the booking is for.",
                },
            },
            "required": ["date", "start_time", "party_size", "product", "area", "name", "email"],
        },
    },
    {
        "name": "modify_booking",
        "description": "Change a booking that already exists. Pass only what changes. Never call before check_availability says the new shape fits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "integer"},
                "party_size": _NULLABLE_INT,
                "date": _NULLABLE_STRING,
                "start_time": _NULLABLE_STRING,
                "product": {"type": ["string", "null"], "enum": ["dinner", "bottle_service", "both", None]},
            },
            "required": ["booking_id"],
        },
    },
    {
        "name": "cancel_booking",
        "description": "Cancel a booking and settle the deposit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "integer"},
                "reason": {"type": "string", "enum": ["guest_cancelled", "hold_expired", "no_show"]},
            },
            "required": ["booking_id", "reason"],
        },
    },
    {
        "name": "lookup_answer",
        "description": "Search config/faq.md and config/policy.md for a sourced answer. Required, every time, for any question about a dish, an ingredient, an allergen or the kitchen - never answer those from memory. Returns a sourced answer, or {\"found\": false} - when it comes back false, the engine has already escalated and closed your turn for you.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "the guest's question, in your own words"}},
            "required": ["question"],
        },
    },
    {
        "name": "escalate",
        "description": "Hand the conversation to the reservation manager. Never empty-handed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "enum": t.POLICY["escalate"]["reasons"]},
                "guest_request_verbatim": {"type": "string"},
                "collected_fields": {"type": "object"},
                "rule_that_triggered": {"type": "string"},
                "agent_recommendation": {"type": "string"},
                "already_told_guest": {"type": "string"},
            },
            "required": [
                "reason",
                "guest_request_verbatim",
                "collected_fields",
                "rule_that_triggered",
                "agent_recommendation",
                "already_told_guest",
            ],
        },
    },
    {
        "name": "respond",
        "description": "Finish your turn: your reply to the guest, plus the bookkeeping fields.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "action": {"type": "string", "enum": ["book", "modify", "cancel", "inform", "escalate", "none"]},
                "intent": {"type": "string", "enum": ["book", "modify", "cancel", "inform", "bye"]},
                "state": _STATE_SCHEMA,
                "facts": _FACTS_SCHEMA,
                "conversation_status": {"type": "string", "enum": ["agent", "handed_to_human"]},
            },
            "required": ["message", "action", "intent", "state", "facts", "conversation_status"],
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatching a tool call
# ---------------------------------------------------------------------------


def _serialize(value):
    """
    Turn a tools.py dataclass (or a list of them) into plain JSON-able data.

    Params: value - a Customer, Booking, Availability, Quote, Cancellation,
                    a list of these, or a plain value.
    Return: the same shape, as dicts/lists/plain values.
    """
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    return value


def _booking_is_active(booking_id: int) -> bool:
    """
    Is a booking still confirmed or pending_deposit, not cancelled?

    Used to tell a genuine modify-quote (an active booking, being changed)
    apart from a guest re-booking after their old one was cancelled - the
    model sometimes references the old booking's id there out of habit, and
    that should be treated as a fresh quote, not a modify of a dead row.

    Params: booking_id - the id to check.
    Return: True when the row exists and is not cancelled.
    """
    conn = t._connect()
    row = conn.execute("SELECT status FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    conn.close()
    return row is not None and row["status"] != "cancelled"


_SINGLE_BOOKING_KEYS = (
    "booking_status", "booking_party_size", "hold_expires_at",
    "payment_link_sent", "modify_allowed", "cancel_reason",
    "refund_eur", "deposit_kept",
)


def _clear_single_booking_facts(known_facts: dict) -> None:
    """
    Drop the facts that only mean something about one specific booking.

    Used whenever we can no longer say which booking they still describe -
    a fresh new-booking quote, or find_bookings turning up more than one
    match with no way here to tell which one the guest means. Showing
    nothing is honest; carrying over a figure from a different booking is not.

    Params: known_facts - the conversation's running facts dict, mutated in place.
    Return: None.
    """
    for key in _SINGLE_BOOKING_KEYS:
        known_facts.pop(key, None)


def _harvest_booking(known_facts: dict, booking, created: bool | None = None) -> None:
    """
    Pull the fact keys a Booking carries into the conversation's known facts.

    Whichever tool handed us a specific booking - create, modify, cancel's
    own booking, or find_bookings when exactly one came back - the fields
    mean the same thing, so one place reads them.

    Params: known_facts - the conversation's running facts dict, mutated in place.
            booking      - the Booking to read.
            created      - True/False to also set booking_created, or None to
                          leave it untouched (modify and a plain lookup do
                          not create or un-create anything).
    Return: None.
    """
    known_facts["booking_found"] = True
    known_facts["booking_status"] = booking.status
    known_facts["booking_party_size"] = booking.party_size
    known_facts["booking_date"] = booking.service_date
    known_facts["booking_start_time"] = datetime.fromisoformat(booking.start_at).strftime("%H:%M")
    known_facts["hold_expires_at"] = booking.hold_expires_at
    known_facts["payment_link_sent"] = booking.payment_link is not None
    known_facts["modify_allowed"] = booking.modify_allowed
    known_facts["deposit_eur"] = booking.deposit_eur
    if booking.deposit_extra_eur:
        # 0 almost always means "nothing extra to collect", not "the figure
        # is zero euros" - only modify_booking's own reply, right after
        # raising the deposit, has anything worth reporting here. Leaving
        # this key alone otherwise avoids implying a fact from a plain
        # find_bookings/get_booking/create_booking read, which never means
        # to say anything about it either way.
        known_facts["deposit_extra_eur"] = booking.deposit_extra_eur
    if created is not None:
        known_facts["booking_created"] = created


def _dispatch(name: str, args: dict, ctx: dict):
    """
    Run one tool call for real, filling in the plumbing the model never sees.

    Also updates ctx["known_facts"] with whatever this result tells us -
    that dict, not the model's own word, is what ends up in a turn's
    `facts` (evals/dialogues/schema.py: facts is what the deterministic
    functions computed).

    Params: name - the tool's name.
            args - what the model supplied.
            ctx  - channel, handle, now, conversation_id, state, known_facts
                   (see run_dialogue_file).
    Return: the tool's result, JSON-serializable.
    """
    customer = t.find_customer(ctx["channel"], ctx["handle"])
    known_facts = ctx["known_facts"]

    if name == "find_customer":
        result = customer
        known_facts["customer_found"] = customer is not None
    elif name == "find_bookings":
        result = t.find_bookings(customer.id, ctx["now"]) if customer else []
        known_facts["booking_found"] = len(result) > 0
        if len(result) == 1:
            _harvest_booking(known_facts, result[0])
        else:
            # None found, or more than one with no way to say which the
            # guest means yet - either way, no single booking's facts apply.
            # get_booking is how the model reads one cleanly once it knows.
            _clear_single_booking_facts(known_facts)
    elif name == "get_booking":
        result = t.get_booking(booking_id=args["booking_id"], now=ctx["now"])
        known_facts["booking_found"] = True
        _harvest_booking(known_facts, result)
    elif name == "check_availability":
        result = t.check_availability(
            date=args["date"],
            start_time=args["start_time"],
            party_size=args["party_size"],
            product=args["product"],
            now=ctx["now"],
            ignore_booking_id=args.get("ignore_booking_id"),
        )
        known_facts["buffer_wait_disclosed"] = result.needs_buffer
        known_facts["alternative_dates"] = result.alternative_dates
        ctx["quoted_this_turn"] = True  # freshly checked availability also counts as "told them something new"
    elif name == "quote_booking":
        deposit_required = customer.deposit_required if customer else True
        result = t.quote_booking(
            date=args["date"],
            start_time=args["start_time"],
            party_size=args["party_size"],
            product=args["product"],
            area=args["area"],
            deposit_required=deposit_required,
            now=ctx["now"],
            existing_booking_id=args.get("existing_booking_id"),
        )
        known_facts["minimum_spend_pp"] = result.minimum_spend_pp
        known_facts["minimum_spend_total"] = result.minimum_spend_total
        known_facts["deposit_eur"] = result.deposit_eur
        known_facts["deposit_extra_eur"] = result.deposit_extra_eur
        known_facts["hold_hours"] = result.hold_hours
        known_facts["deposit_deducted_from_bill"] = result.deposit_deducted_from_bill
        existing_id = args.get("existing_booking_id")
        if existing_id is None or not _booking_is_active(existing_id):
            # A fresh quote for a new booking - not created yet, and whatever
            # booking we last discussed (maybe a cancelled one, maybe someone
            # else's, from earlier in this same conversation) no longer
            # applies here. This also catches a guest re-booking after their
            # old booking was cancelled, when the model still names its id.
            known_facts["booking_created"] = False
            _clear_single_booking_facts(known_facts)
        ctx["quoted_this_turn"] = True
    elif name == "create_booking":
        # This has broken three separate dialogues by skipping straight from
        # a fresh quote to creating, with no "shall I go ahead?" turn in
        # between for the guest to actually answer - a prompt rule alone did
        # not reliably stop it, so this is now a hard, deterministic block.
        # confirmation_pending is True only when an EARLIER, already-completed
        # turn quoted fresh figures and did not create - i.e. the guest's
        # current message is the reply to that turn's own question. A quote
        # called in this same turn does not count; neither does just now
        # having every field.
        if not ctx.get("confirmation_pending"):
            raise ValueError(
                "You have not yet asked 'shall I go ahead?' on an earlier turn and had the guest "
                "say yes to it. Call quote_booking and present the figures with that question "
                "instead, then wait for their next message before calling create_booking."
            )
        if customer is not None:
            customer_id = customer.id
        else:
            # The identity fields come from THIS call's own arguments first -
            # ctx["state"] is last_model_state, what the model reported at
            # the END of the PREVIOUS turn. A guest who gives their name and
            # email in the very turn the model books them (the normal path
            # for a first-time guest) would otherwise be refused for fields
            # they just gave, because state has not caught up yet. state is
            # only a fallback, for a model that forgets to pass them.
            state = ctx["state"]
            guest_name = args.get("name") or state.get("name")
            guest_email = args.get("email") or state.get("email")
            guest_phone = state.get("phone")
            missing = [
                field_name
                for field_name, value in (("name", guest_name), ("phone", guest_phone), ("email", guest_email))
                if not value
            ]
            if missing:
                raise ValueError(f"cannot create a booking yet - still missing: {', '.join(missing)}")
            customer_id = t._create_customer(
                name=guest_name, phone=guest_phone, email=guest_email, now=ctx["now"]
            )
        result = t.create_booking(
            customer_id=customer_id,
            date=args["date"],
            start_time=args["start_time"],
            party_size=args["party_size"],
            product=args["product"],
            area=args["area"],
            now=ctx["now"],
        )
        _harvest_booking(known_facts, result, created=True)
        known_facts["hold_hours"] = t.POLICY["pending"]["hold_hours"]  # not on Booking itself
        known_facts["deposit_deducted_from_bill"] = t.POLICY["deposit"]["deducted_from_bill"]
        ctx["created_this_turn"] = True
    elif name == "modify_booking":
        # Destructive, same as create_booking, and guarded the same way: an
        # earlier, already-completed turn must have quoted the new figures
        # and asked, with the guest's current message answering yes to it.
        # A quote called in this same turn does not count, exactly as for
        # create_booking - confirmation_pending already encodes that.
        if not ctx.get("confirmation_pending"):
            raise ValueError(
                "You have not yet asked 'shall I go ahead?' on an earlier turn and had the guest "
                "say yes to it. Call quote_booking with this booking's id and present the new "
                "figures with that question instead, then wait for their next message before "
                "calling modify_booking."
            )
        result = t.modify_booking(
            booking_id=args["booking_id"],
            now=ctx["now"],
            party_size=args.get("party_size"),
            date=args.get("date"),
            start_time=args.get("start_time"),
            product=args.get("product"),
        )
        _harvest_booking(known_facts, result)  # a modify never creates or un-creates anything
        known_facts["deposit_deducted_from_bill"] = t.POLICY["deposit"]["deducted_from_bill"]
        ctx["modified_this_turn"] = True
    elif name == "cancel_booking":
        # Destructive too, guarded the same way - but only when the guest is
        # the one cancelling. hold_expired and no_show are the engine's own
        # housekeeping (system-clock turns, policy.md #8), never something a
        # guest is asked to confirm, so they are exempt.
        if args["reason"] == "guest_cancelled" and not ctx.get("confirmation_pending"):
            raise ValueError(
                "You have not yet stated what happens to the deposit (kept or refunded) and "
                "asked 'shall I still cancel it?' on an earlier turn, with the guest's current "
                "message answering yes to it. State the consequence and ask first, then wait "
                "for their next message before calling cancel_booking."
            )
        result = t.cancel_booking(booking_id=args["booking_id"], reason=args["reason"], now=ctx["now"])
        _harvest_booking(known_facts, result.booking, created=False)
        known_facts["refund_eur"] = result.refund_eur
        known_facts["deposit_kept"] = result.deposit_kept
        known_facts["cancel_reason"] = args["reason"]
        ctx["cancelled_this_turn"] = True
    elif name == "lookup_answer":
        result = t.lookup_answer(question=args["question"])
        if not result["found"]:
            # F-07: the model has correctly reasoned its way to "I do not
            # know this" before now and still, sometimes, not called
            # escalate - so the engine does not wait for it to. This is
            # the one place being wrong costs a guest their health, not
            # just a euro figure, so the escalation happens here,
            # deterministically, the moment the source search comes back
            # empty - not on the strength of the model noticing and acting
            # on it a second time.
            t.escalate(
                conversation_id=ctx["conversation_id"],
                reason="no_answer_in_sources",
                guest_request_verbatim=args["question"],
                collected_fields=dict(ctx["state"]),
                rule_that_triggered="policy.md #12 - no menu or allergen source exists for this",
                agent_recommendation="a person confirms with the kitchen and answers the guest directly",
                already_told_guest="a person will check and come back to them here",
            )
            known_facts["escalated"] = True
            known_facts["escalation_reason"] = "no_answer_in_sources"
            ctx["engine_forced_escalation"] = True
    elif name == "escalate":
        result = t.escalate(
            conversation_id=ctx["conversation_id"],
            reason=args["reason"],
            guest_request_verbatim=args["guest_request_verbatim"],
            collected_fields=args["collected_fields"],
            rule_that_triggered=args["rule_that_triggered"],
            agent_recommendation=args["agent_recommendation"],
            already_told_guest=args["already_told_guest"],
        )
        known_facts["escalated"] = True
        known_facts["escalation_reason"] = args["reason"]
    else:
        raise ValueError(f"unknown tool: {name}")

    return _serialize(result)


# ---------------------------------------------------------------------------
# One model turn
# ---------------------------------------------------------------------------


def _generate_reply(history: list, ctx: dict) -> dict:
    """
    Call the model, running tool calls, until it calls `respond`.

    Params: history - the running Anthropic messages list, extended in place.
            ctx      - see _dispatch. This turn's summed usage is left on
                       ctx["turn_usage"], how many messages.create calls it
                       took on ctx["turn_model_calls"], and the tools it called
                       (in order, excluding respond) on ctx["turn_tools_called"]
                       - one {"name", "ok", "error"} record per call, ok being
                       whether _dispatch actually ran it rather than refusing
                       it - for the caller to read afterwards.
    Return: the arguments `respond` was called with.
    """
    global api_call_count
    ctx["turn_usage"] = _zero_usage()
    ctx["turn_model_calls"] = 0
    ctx["turn_tools_called"] = []
    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=history,
            tools=TOOLS,
        )
        api_call_count += 1
        ctx["turn_model_calls"] += 1
        print(f"  usage: {response.usage}")
        _add_usage(ctx["turn_usage"], response.usage)
        history.append({"role": "assistant", "content": response.content})

        finishing = None
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "respond":
                finishing = block.input
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": "noted"})
                continue
            try:
                result = _dispatch(block.name, block.input, ctx)
                content = json.dumps(result, default=str)
                is_error = False
                error_text = None
            except Exception as exc:  # a bad tool call must not crash the run
                content = str(exc)
                is_error = True
                error_text = content
            ctx["turn_tools_called"].append({"name": block.name, "ok": not is_error, "error": error_text})
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
            )

        if tool_results:
            history.append({"role": "user", "content": tool_results})
        if finishing is not None:
            # facts come from the tools, not the model's own word (schema.py:
            # facts is what the deterministic functions computed) - only
            # reply_language has no tool to come from, so that one key alone
            # survives from what the model itself reported.
            model_reply_language = (finishing.get("facts") or {}).get("reply_language")
            finishing["facts"] = dict(ctx["known_facts"])
            finishing["facts"]["agent_replied"] = True
            if model_reply_language is not None:
                finishing["facts"]["reply_language"] = model_reply_language
                is_unsupported = model_reply_language not in t.POLICY["languages"]["supported"]
                if is_unsupported and finishing.get("action") != "escalate":
                    # policy.md #1, "any other language": a fixed sentence
                    # never costs a token - the engine says it, from a table
                    # it already has, and whatever the model put in message
                    # for this turn is discarded. The model's only real job
                    # here was getting reply_language right. Only the fixed
                    # refusal is templated this way - once the guest writes
                    # again and the agent escalates, that message is its own
                    # free-form handover notice, not this stored sentence.
                    templates = t.POLICY["languages"]["unsupported_reply"]
                    fallback_lang = t.POLICY["languages"]["unsupported_reply_fallback"]
                    finishing["message"] = templates.get(model_reply_language, templates[fallback_lang])
            if ctx.get("engine_forced_escalation"):
                # F-07: lookup_answer came back with nothing this turn, and
                # the engine has already escalated for it (see _dispatch) -
                # conversation_status is not left to the model's own word
                # here either, for the same reason reply_language's stored
                # sentence is not: this is the one place a missed handover
                # is not just a wrong figure, it is a guest never hearing
                # back about something that could hurt them.
                finishing["conversation_status"] = "handed_to_human"
            return finishing
        if not tool_results:
            # Plain text, no tool call at all - nudge it back on track.
            history.append({"role": "user", "content": "Please call the respond tool to finish your turn."})

    raise RuntimeError("model did not call respond within the round limit")


def _next_confirmation_pending(ctx: dict, pending: dict) -> bool:
    """
    Whether create_booking, modify_booking or cancel_booking may run on the
    NEXT turn, from what this turn actually did.

    False the moment one of the three destructive calls succeeds, so a
    guest's next "yes" cannot be spent twice. Otherwise True when this turn
    told the guest a real consequence to answer: fresh figures
    (check_availability/quote_booking, shared by create and modify), or, for
    a cancellation - which has no quote to call - this turn's own reported
    action being "cancel" without cancel_booking itself having run. Neither
    condition changes anything; the previous value carries forward
    unchanged, exactly as before this covered modify and cancel too.

    Params: ctx     - this turn's context, read for *_this_turn flags and
                       the confirmation_pending it was given.
            pending - what this turn's _generate_reply (or _silent_reply)
                      returned, read for its own reported "action".
    Return: the confirmation_pending value to carry into the next turn.
    """
    if ctx.get("created_this_turn") or ctx.get("modified_this_turn") or ctx.get("cancelled_this_turn"):
        return False
    if ctx.get("quoted_this_turn") or pending.get("action") == "cancel":
        return True
    return ctx.get("confirmation_pending", False)


def _calendar_strip(now: str, days: int = 8) -> str:
    """
    The next few real dates with their weekday names, so the model can look
    a day name up instead of computing one - it keeps getting that
    arithmetic wrong on its own (a week off, then a day off).

    Params: now  - ISO 8601, the moment "today" is judged from.
            days - how many days to list, including today.
    Return: "2026-07-16 (Thursday, today), 2026-07-17 (Friday), ...".
    """
    today = t._parse_now(now).date()
    entries = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        label = day.strftime("%A") + (", today" if offset == 0 else "")
        entries.append(f"{day.isoformat()} ({label})")
    return ", ".join(entries)


def _customer_lookup_note(customer: t.Customer | None) -> str:
    """
    The one-line note the engine hands the model for the identity lookup it
    now does itself, at the start of every conversation (policy.md #1: "the
    agent looks the guest up before it asks for anything") - the same
    reasoning as the weekday injection: a deterministic thing belongs in
    code, not left to the model to remember to call.

    Params: customer - the Customer from tools.find_customer, or None.
    Return: one line, prepended to the very first turn's prefix.
    """
    if customer is None:
        return "[Customer lookup] Not found - this is a new guest."
    return (
        f"[Customer lookup] Found: name={customer.name}, phone={customer.phone}, "
        f"email={customer.email}, deposit_required={customer.deposit_required}."
    )


def _silent_reply(state: dict) -> dict:
    """
    The forced, silent turn for a conversation already handed to a person.

    Params: state - the last known belief state, carried forward unchanged.
    Return: a reply shaped like the model's, but produced with no model call.
    """
    return {
        "message": "",
        "action": "none",
        "state": state,
        "facts": {"agent_replied": False},
        "conversation_status": "handed_to_human",
    }


def _probe_silence(history: list) -> dict:
    """
    Call the model once, read-only, to see what it would have said on a
    turn the handed-to-human guard is about to silence.

    Never dispatches any tool call and never appends anything to history -
    the guard's own silent reply is still the only thing that gets graded;
    this is purely an extra look, so it must not touch any shared state.

    A single round is not always enough for the model to reach `respond` -
    it may only get as far as plain text, or a different tool call, before
    running out of turns. That is a real, distinct outcome from genuinely
    choosing to say nothing, so it is kept apart via "probe_finished"
    rather than folded into "message" as if it were a considered answer.

    Params: history - the running Anthropic messages list, read but never
                       modified.
    Return: "message"/"action"/"conversation_status" from the model's
            `respond` call, or all None when it did not call respond this
            round; "text" - any plain text blocks it wrote, joined ("" for
            none); "probe_finished" - True only when it did call respond.
    """
    global api_call_count, probe_call_count
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=list(history),
        tools=TOOLS,
    )
    api_call_count += 1
    probe_call_count += 1
    _add_usage(probe_usage, response.usage)
    print(f"  probe usage: {response.usage}")

    text = "\n".join(block.text for block in response.content if block.type == "text")
    respond_input = next(
        (block.input for block in response.content if block.type == "tool_use" and block.name == "respond"),
        None,
    )

    return {
        "message": respond_input.get("message") if respond_input else None,
        "action": respond_input.get("action") if respond_input else None,
        "conversation_status": respond_input.get("conversation_status") if respond_input else None,
        "text": text,
        "probe_finished": respond_input is not None,
    }


# ---------------------------------------------------------------------------
# Stepping a dialogue file
# ---------------------------------------------------------------------------

EMPTY_STATE = {
    "name": None, "party_size": None, "date": None,
    "start_time": None, "product": None, "phone": None, "email": None,
}


def _check_phrases(message: str, phrases: list[str], should_appear: bool) -> list[dict]:
    """
    Check a list of required or forbidden phrases against the produced reply.

    Plain substring matching - a phrase either appears in the reply or it
    does not.

    Params: message       - the produced reply text.
            phrases       - substrings to check for.
            should_appear - True for must_say, False for must_not_say.
    Return: one record per phrase: the phrase, whether it appeared, and
            whether that satisfies the rule.
    """
    results = []
    for phrase in phrases:
        appeared = phrase.lower() in message.lower()
        ok = appeared if should_appear else not appeared
        results.append({"phrase": phrase, "appeared": appeared, "ok": ok})
    return results


def _grade(
    turn: dict,
    produced: dict,
    expected_state: dict | None,
    expected_intent: str | None,
    usage: dict,
    seconds: float,
    model_calls: int,
    tools_called: list[dict],
    probe: dict | None,
) -> dict:
    """
    Compare one produced reply against what the dialogue file expects.

    Params: turn            - the expected SYSTEM turn.
            produced        - what _generate_reply (or _silent_reply) returned.
            expected_state  - the state on the USER turn this reply answers,
                               or None when there is nothing to compare.
            expected_intent - the intent on the USER turn this reply answers,
                               or None when this turn was triggered by the
                               system clock, not a guest message.
            usage           - this turn's summed token counters, shaped like
                               _zero_usage() (all zero for a silent turn).
            seconds         - wall time from the guest's message going into
                               history to the model calling respond.
            model_calls     - how many messages.create calls this turn took
                               (0 for a silent, handed-to-human turn).
            tools_called    - the policy tools the model actually called this
                               turn, in order, excluding respond - one
                               {"name", "ok", "error"} record per call ([] is
                               a real result, not "not recorded").
            probe           - what _probe_silence returned, only on a turn
                               the handed-to-human guard silenced with
                               SILENCE_PROBE on; None otherwise (not "not
                               applicable" vs "not recorded" - just unused).
    Return: one record, ready to print and to save.
    """
    expected_facts = turn.get("facts", {})
    got_facts = produced.get("facts", {})
    facts = {
        key: {"expected": expected_facts[key], "got": got_facts.get(key)}
        for key in expected_facts
    }
    message = produced.get("message", "")

    return {
        "turn_id": turn["turn_id"],
        "action": {"expected": turn.get("action"), "got": produced.get("action")},
        "intent": {"expected": expected_intent, "got": produced.get("intent")},
        "facts": facts,
        "state": {"expected": expected_state, "got": produced.get("state")},
        "conversation_status": {
            "expected": turn.get("conversation_status"),
            "got": produced.get("conversation_status"),
        },
        "must_say": _check_phrases(message, turn.get("must_say", []), should_appear=True),
        "must_not_say": _check_phrases(message, turn.get("must_not_say", []), should_appear=False),
        "expected_utterance": turn.get("utterance"),
        "got_message": message,
        "usage": usage,
        "seconds": round(seconds, 3),
        "model_calls": model_calls,
        "tools_called": tools_called,
        "probe": probe,
        "probe_broke_silence": (
            bool(probe["message"]) or bool(probe["text"]) if probe is not None else None
        ),
    }


def _print_record(record: dict) -> None:
    """Print one turn's expected-vs-got, for a human to read."""
    print(f"\n--- turn {record['turn_id']} ---")
    print(f"  expected utterance: {record['expected_utterance']!r}")
    print(f"  got message:        {record['got_message']!r}")
    print(f"  action   expected={record['action']['expected']!r} got={record['action']['got']!r}")
    for key, pair in record["facts"].items():
        print(f"  fact {key}: expected={pair['expected']!r} got={pair['got']!r}")
    print(f"  state    expected={record['state']['expected']} got={record['state']['got']}")
    print(
        "  status   expected="
        f"{record['conversation_status']['expected']!r} got={record['conversation_status']['got']!r}"
    )
    for check in record["must_say"]:
        mark = "PASS" if check["ok"] else "FAIL"
        print(f"  must_say     [{mark}] {check['phrase']!r}")
    for check in record["must_not_say"]:
        mark = "PASS" if check["ok"] else "FAIL"
        print(f"  must_not_say [{mark}] {check['phrase']!r}")


def run_dialogue_file(path: str, out_dir: Path = RUNS_DIR) -> dict:
    """
    Step through one dialogue file, turn by turn, grading every SYSTEM turn.

    Params: path    - path to a dialogue JSON file.
            out_dir - folder to save the result under (runs/ by default, or
                      runs/<run label>/ when several runs must sit side by
                      side).
    Return: the summary that was also saved under out_dir.
    """
    global api_call_count, probe_call_count, probe_usage
    api_call_count = 0
    probe_call_count = 0
    probe_usage = _zero_usage()

    seed_db()  # a fresh, known DB before every dialogue - a previous run (single or "all") may have written real bookings into it
    dialogue = load_dialogue(path)
    channel = dialogue["channel"]
    handle = dialogue["from"]
    now = dialogue["now"]
    conversation_status = "agent"

    history: list = []
    last_user_state = None  # the dialogue file's own ground truth, for grading only
    last_model_state = EMPTY_STATE  # what the model itself has reported so far
    confirmation_pending = False  # an earlier turn quoted fresh figures and asked "shall I go ahead?"
    # The conversation's real facts, built from tool results as they happen
    # (evals/dialogues/schema.py: facts is what the deterministic functions
    # computed) - carried forward turn to turn, since a fact stays true
    # until the tool that produces it is called again, not just for one turn.
    # customer_found is seeded here rather than left for the model to set by
    # calling find_customer - the channel and handle are already known to
    # the engine on every conversation's first turn, so this is done for it
    # (policy.md #1), the same reasoning as the weekday injection below. The
    # model still has the tool and may call it again later, but nothing has
    # to actually get called for this fact to be true from the start.
    identified_customer = t.find_customer(channel, handle)
    known_facts: dict = {
        "escalated": False, "booking_created": False, "customer_found": identified_customer is not None,
    }
    pending = None
    pending_usage = None  # this turn's token counters, waiting alongside `pending` to be graded
    pending_seconds = None  # wall time from the guest's message to the model calling respond
    pending_model_calls = None  # how many messages.create calls that turn took
    pending_intent = None  # the guest turn's own intent, or None when a system-clock turn triggered this reply
    pending_tools_called = None  # the policy tools actually called this turn, in order (name/ok/error each)
    pending_probe = None  # what the model would have said, only set on a silenced turn with SILENCE_PROBE on
    dialogue_usage = _zero_usage()  # summed across every turn, for the summary
    records = []
    is_first_turn = True  # only the very first turn gets the customer-lookup note

    print(f'{dialogue["dialogue_id"]} - {len(dialogue["turns"])} turns')

    for turn in dialogue["turns"]:
        if "at" in turn:
            now = turn["at"]

        calendar = _calendar_strip(now)
        prefix = f"(now: {now}, upcoming dates: {calendar}, channel: {channel}, sender: {handle})"
        if is_first_turn:
            prefix += f"\n{_customer_lookup_note(identified_customer)}"
            is_first_turn = False

        if turn["speaker"] in ("USER", "HUMAN"):
            if turn["speaker"] == "HUMAN":
                conversation_status = "agent"  # a person has handed it back
                known_facts["escalated"] = False
                known_facts.pop("escalation_reason", None)
                pending_intent = None  # a manager message carries no guest intent
                history.append(
                    {"role": "user", "content": f'{prefix}\n[The reservation manager writes] {turn["utterance"]}'}
                )
            else:
                last_user_state = turn["state"]
                pending_intent = turn["intent"]
                history.append({"role": "user", "content": f'{prefix}\n{turn["utterance"]}'})

            turn_start = time.perf_counter()
            if conversation_status == "agent":
                ctx = {
                    "channel": channel, "handle": handle, "now": now,
                    "conversation_id": f"{channel}:{handle}",
                    "state": last_model_state,
                    "quoted_this_turn": False,
                    "confirmation_pending": confirmation_pending,
                    "known_facts": known_facts,
                }
                pending = _generate_reply(history, ctx)
                pending_usage = ctx["turn_usage"]
                pending_model_calls = ctx["turn_model_calls"]
                pending_tools_called = ctx["turn_tools_called"]
                confirmation_pending = _next_confirmation_pending(ctx, pending)
            else:
                pending = _silent_reply(last_model_state)
                pending_usage = _zero_usage()
                pending_model_calls = 0
                pending_tools_called = []
                pending_probe = _probe_silence(history) if SILENCE_PROBE else None
            pending_seconds = time.perf_counter() - turn_start
            continue

        # SYSTEM turn. If nothing is pending, the clock triggered this one,
        # not a message - inject a note and let the agent react to it.
        if pending is None:
            history.append({"role": "user", "content": f"{prefix}\n[System clock] Time has moved on."})
            pending_intent = None  # no guest message triggered this turn
            turn_start = time.perf_counter()
            if conversation_status == "agent":
                ctx = {
                    "channel": channel, "handle": handle, "now": now,
                    "conversation_id": f"{channel}:{handle}",
                    "state": last_model_state,
                    "quoted_this_turn": False,
                    "confirmation_pending": confirmation_pending,
                    "known_facts": known_facts,
                }
                pending = _generate_reply(history, ctx)
                pending_usage = ctx["turn_usage"]
                pending_model_calls = ctx["turn_model_calls"]
                pending_tools_called = ctx["turn_tools_called"]
                confirmation_pending = _next_confirmation_pending(ctx, pending)
            else:
                pending = _silent_reply(last_model_state)
                pending_usage = _zero_usage()
                pending_model_calls = 0
                pending_tools_called = []
                pending_probe = _probe_silence(history) if SILENCE_PROBE else None
            pending_seconds = time.perf_counter() - turn_start

        record = _grade(
            turn, pending, last_user_state, pending_intent,
            pending_usage, pending_seconds, pending_model_calls, pending_tools_called, pending_probe,
        )
        records.append(record)
        _print_record(record)
        for key in USAGE_KEYS:
            dialogue_usage[key] += pending_usage[key]

        conversation_status = pending.get("conversation_status", "agent")
        last_model_state = pending.get("state") or last_model_state
        pending = None
        pending_usage = None
        pending_seconds = None
        pending_intent = None
        pending_model_calls = None
        pending_tools_called = None
        pending_probe = None

    summary = {
        "dialogue_id": dialogue["dialogue_id"],
        "source": path,
        "api_calls": api_call_count,
        "tokens": dialogue_usage,
        "probe_calls": probe_call_count,
        "probe_tokens": probe_usage,
        "turns": records,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{dialogue["dialogue_id"]}.json'
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n{len(records)} SYSTEM turns graded, {api_call_count} model calls")
    print(f"saved {out_path}")
    return summary


def _turn_passed(record: dict) -> bool:
    """
    Did one graded turn fully match - action, every fact, state, and status?

    must_say/must_not_say are saved alongside this but not part of it -
    phase 5 does not score wording (README); this is the structured
    correctness phase 6's real metrics (money accuracy, action accuracy,
    JGA) will be counted from.

    Params: record - one turn record, as built by _grade.
    Return: True when everything structured matches.
    """
    if record["action"]["expected"] != record["action"]["got"]:
        return False
    for pair in record["facts"].values():
        if pair["expected"] != pair["got"]:
            return False
    expected_state = record["state"]["expected"]
    if expected_state is not None and expected_state != record["state"]["got"]:
        return False
    if record["conversation_status"]["expected"] != record["conversation_status"]["got"]:
        return False
    return True


def run_all_dialogues(run_label: str | None = None) -> None:
    """
    Run all eleven dialogues in order, save each one under runs/, then print
    one verdict line per dialogue.

    The saved files are what Phase 6 counts instead of re-running everything
    and spending credits again - each one already carries expected-vs-got
    for every turn, so no model call is needed to score them later.

    Params: run_label - when given, results go under runs/<run_label>/
                        instead of straight under runs/, and a summary.json
                        is written there too, so several runs can sit side
                        by side instead of overwriting each other. With no
                        label, behaves exactly as before.
    Return: None.
    """
    out_dir = RUNS_DIR if run_label is None else RUNS_DIR / run_label
    started_at_utc = datetime.now(timezone.utc).isoformat()

    if run_label is None and RUNS_DIR.exists():
        for old_file in RUNS_DIR.glob("*.jsonl"):
            old_file.unlink()

    verdicts = []
    dialogue_totals = []
    for path in DIALOGUE_PATHS:
        summary = run_dialogue_file(path, out_dir=out_dir)  # seeds the DB itself, fresh, every call
        passed = sum(1 for turn in summary["turns"] if _turn_passed(turn))
        total = len(summary["turns"])
        verdicts.append((summary["dialogue_id"], passed, total))
        dialogue_totals.append({
            "dialogue_id": summary["dialogue_id"],
            "turns": total,
            "api_calls": summary["api_calls"],
            "tokens": summary["tokens"],
            "probe_calls": summary["probe_calls"],
            "probe_tokens": summary["probe_tokens"],
            "seconds": round(sum(turn["seconds"] for turn in summary["turns"]), 3),
        })

    print("\n=== VERDICT ===")
    for dialogue_id, passed, total in verdicts:
        mark = "PASS" if passed == total else "FAIL"
        print(f"{dialogue_id}: {mark} ({passed}/{total} turns fully matched)")

    if run_label is not None:
        run_summary = {
            "run_label": run_label,
            "model": MODEL,
            "started_at_utc": started_at_utc,
            "dialogues": dialogue_totals,
        }
        summary_path = out_dir / "summary.json"
        summary_path.write_text(json.dumps(run_summary, indent=2, default=str), encoding="utf-8")
        print(f"saved {summary_path}")


if __name__ == "__main__":
    cli_args = sys.argv[1:]
    if "--silence-probe" in cli_args:
        SILENCE_PROBE = True
        cli_args.remove("--silence-probe")

    if cli_args and cli_args[0] == "all":
        run_label = cli_args[1] if len(cli_args) > 1 else None
        run_all_dialogues(run_label)
    else:
        dialogue_path = cli_args[0] if cli_args else "evals/dialogues/dlg-01-party-size-change.json"
        run_dialogue_file(dialogue_path)
