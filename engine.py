### engine.py. Run with: python engine.py [dialogue path]
"""The loop that steps one dialogue file through the model and the tools.

The model never sees plumbing values (now, customer_id, conversation_id) -
this file injects those itself, fresh each time, the same way a real channel
integration would. The model only ever supplies the business arguments: what
the guest asked for, and its own judgement calls (which booking, what reason).

Every turn ends with the model calling `respond` - not one of the eight
policy tools, just this file's way of getting a structured answer (a reply,
an action, the belief state, the facts settled this turn) out of a turn that
is otherwise free-form tool use.
"""

import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import timedelta
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

client = Anthropic()

# How many times this run has called the model - one line of visibility into
# the thing the phase 5 brief says to watch: only these calls cost money.
api_call_count = 0


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

IDENTITY. Call find_customer first, before asking the guest anything. If they
are found, you already have their name, phone, email and deposit_required -
never ask for what you already have. If the name given in conversation
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
list - reply in the guest's own language with one fixed sentence: that
Venue X can help in Greek, English, French or Italian. This applies even
when you can read the message perfectly well and know exactly what they
are asking - understanding a language is not the same as it being
supported, and you must not act on what you understood. Take no booking
details in that reply: no name, no date, no time, no party size, nothing -
state stays exactly what it was before this message, whatever they wrote.
If the guest writes again in that same unsupported language, escalate
(reason: unsupported_language_repeated).

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
night (a dinner booking and a bottle service booking, say). You must ask
which one they mean, by whatever tells them apart - the time and the
product, if that is what differs - before you check availability or quote
anything. Picking the one that seems most likely, or the first one
returned, is exactly the mistake this rule exists to stop; there is no safe
guess here. Only proceed once the guest has said which. A change respects
modify_allowed on the booking, from
find_bookings; if it says no, refuse plainly and do not offer to check - and
never escalate that refusal. The policy already has the answer here (the
deadline has passed) and giving that answer directly is not the same as
having no answer - this is a refuse, exactly like a discount or a deposit
waiver, not a party-wants-a-specific-table or forgiving-a-late-arrival case.
Cancelling refunds the deposit only when refund_if_cancelled_now says so.

When a guest has more than one live booking and you have just acted on one
of them, a question about the OTHER one is not answered from memory - call
find_bookings again before you state anything about it. The one you just
changed and the one you did not are different rows with different figures,
and reusing the one still in front of you for the other is a real mistake,
not a shortcut.

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

INFORM. Answer only from the rules above and this FAQ. If the answer is not
here, say you will check and escalate (reason: no_answer_in_sources) - never
guess, especially about food, ingredients or allergies.

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
there is used. The one exception is `reply_language`: report the language
code you replied in (en, el, fr, it, or the guest's own language when it is
none of those) - that is the one thing no tool can tell us.

`action` names which of the five things the guest's message this turn was
about - book, modify, cancel, inform, escalate - even a clarifying question
in the middle of one (still book, say, if it is about the same booking).
Use "inform" for a side question that is not moving the request forward
(how the deposit works, a FAQ answer), and "none" only for a plain greeting
or goodbye with nothing else in it. On a goodbye, always remind the guest to
arrive about {policy['booking']['arrive_minutes_early']} minutes early.

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
        "reply_language": {"type": "string", "description": "the language code you replied in, e.g. en, el, fr, it"},
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
            },
            "required": ["date", "start_time", "party_size", "product", "area"],
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
                "state": _STATE_SCHEMA,
                "facts": _FACTS_SCHEMA,
                "conversation_status": {"type": "string", "enum": ["agent", "handed_to_human"]},
            },
            "required": ["message", "action", "state", "facts", "conversation_status"],
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
    known_facts["hold_expires_at"] = booking.hold_expires_at
    known_facts["payment_link_sent"] = booking.payment_link is not None
    known_facts["modify_allowed"] = booking.modify_allowed
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
            _clear_single_booking_facts(known_facts)
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
            state = ctx["state"]
            missing = [field for field in ("name", "phone", "email") if not state.get(field)]
            if missing:
                raise ValueError(f"cannot create a booking yet - still missing: {', '.join(missing)}")
            customer_id = t._create_customer(
                name=state["name"], phone=state["phone"], email=state["email"], now=ctx["now"]
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
    elif name == "cancel_booking":
        result = t.cancel_booking(booking_id=args["booking_id"], reason=args["reason"], now=ctx["now"])
        _harvest_booking(known_facts, result.booking, created=False)
        known_facts["refund_eur"] = result.refund_eur
        known_facts["deposit_kept"] = result.deposit_kept
        known_facts["cancel_reason"] = args["reason"]
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
            ctx      - see _dispatch.
    Return: the arguments `respond` was called with.
    """
    global api_call_count
    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=history,
            tools=TOOLS,
        )
        api_call_count += 1
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
            except Exception as exc:  # a bad tool call must not crash the run
                content = str(exc)
                is_error = True
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
            return finishing
        if not tool_results:
            # Plain text, no tool call at all - nudge it back on track.
            history.append({"role": "user", "content": "Please call the respond tool to finish your turn."})

    raise RuntimeError("model did not call respond within the round limit")


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


def _grade(turn: dict, produced: dict, expected_state: dict | None) -> dict:
    """
    Compare one produced reply against what the dialogue file expects.

    Params: turn           - the expected SYSTEM turn.
            produced       - what _generate_reply (or _silent_reply) returned.
            expected_state - the state on the USER turn this reply answers,
                              or None when there is nothing to compare.
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


def run_dialogue_file(path: str) -> dict:
    """
    Step through one dialogue file, turn by turn, grading every SYSTEM turn.

    Params: path - path to a dialogue JSON file.
    Return: the summary that was also saved under runs/.
    """
    global api_call_count
    api_call_count = 0

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
    known_facts: dict = {"escalated": False, "booking_created": False}
    pending = None
    records = []

    print(f'{dialogue["dialogue_id"]} - {len(dialogue["turns"])} turns')

    for turn in dialogue["turns"]:
        if "at" in turn:
            now = turn["at"]

        calendar = _calendar_strip(now)
        prefix = f"(now: {now}, upcoming dates: {calendar}, channel: {channel}, sender: {handle})"

        if turn["speaker"] in ("USER", "HUMAN"):
            if turn["speaker"] == "HUMAN":
                conversation_status = "agent"  # a person has handed it back
                known_facts["escalated"] = False
                known_facts.pop("escalation_reason", None)
                history.append(
                    {"role": "user", "content": f'{prefix}\n[The reservation manager writes] {turn["utterance"]}'}
                )
            else:
                last_user_state = turn["state"]
                history.append({"role": "user", "content": f'{prefix}\n{turn["utterance"]}'})

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
                if ctx.get("created_this_turn"):
                    confirmation_pending = False
                elif ctx.get("quoted_this_turn"):
                    confirmation_pending = True
            else:
                pending = _silent_reply(last_model_state)
            continue

        # SYSTEM turn. If nothing is pending, the clock triggered this one,
        # not a message - inject a note and let the agent react to it.
        if pending is None:
            history.append({"role": "user", "content": f"{prefix}\n[System clock] Time has moved on."})
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
                if ctx.get("created_this_turn"):
                    confirmation_pending = False
                elif ctx.get("quoted_this_turn"):
                    confirmation_pending = True
            else:
                pending = _silent_reply(last_model_state)

        record = _grade(turn, pending, last_user_state)
        records.append(record)
        _print_record(record)

        conversation_status = pending.get("conversation_status", "agent")
        last_model_state = pending.get("state") or last_model_state
        pending = None

    summary = {
        "dialogue_id": dialogue["dialogue_id"],
        "source": path,
        "api_calls": api_call_count,
        "turns": records,
    }
    RUNS_DIR.mkdir(exist_ok=True)
    out_path = RUNS_DIR / f'{dialogue["dialogue_id"]}.json'
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


def run_all_dialogues() -> None:
    """
    Run all eleven dialogues in order, save each one under runs/, then print
    one verdict line per dialogue.

    The saved files are what Phase 6 counts instead of re-running everything
    and spending credits again - each one already carries expected-vs-got
    for every turn, so no model call is needed to score them later.
    """
    if RUNS_DIR.exists():
        for old_file in RUNS_DIR.glob("*.jsonl"):
            old_file.unlink()

    verdicts = []
    for path in DIALOGUE_PATHS:
        seed_db()  # a fresh, known DB before every dialogue - earlier runs wrote real bookings into it
        summary = run_dialogue_file(path)
        passed = sum(1 for turn in summary["turns"] if _turn_passed(turn))
        total = len(summary["turns"])
        verdicts.append((summary["dialogue_id"], passed, total))

    print("\n=== VERDICT ===")
    for dialogue_id, passed, total in verdicts:
        mark = "PASS" if passed == total else "FAIL"
        print(f"{dialogue_id}: {mark} ({passed}/{total} turns fully matched)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "all":
        run_all_dialogues()
    else:
        dialogue_path = sys.argv[1] if len(sys.argv) > 1 else "evals/dialogues/dlg-01-party-size-change.json"
        run_dialogue_file(dialogue_path)
