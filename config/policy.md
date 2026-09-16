# Reservation Policy

The rules the reservation agent follows. This file is for people,
`policy.json` is the same rules for the program. Change both together.

- **Part A**. The venue's facts. Every action needs these.
- **Part B**. One section per agent action: book, modify, cancel, inform,
  escalate.

Worked examples live in `evals/policy_cases.md`. They are the test for
`policy.json` and for the functions that read it.

---

# PART A. THE VENUE

## 1. Identity

| | |
|---|---|
| name | Venue X |
| city | Greek island |
| channels | phone, WhatsApp, email |
| languages | Greek, English, French, Italian |
| system of record | i-host (no API yet) |

The agent answers in the language the guest wrote in, as long as it is one of
the four above. In those four it does everything: books,
modifies, cancels, answers questions.

**Any other language:** the agent replies **in the guest's own language**,
with one fixed sentence, that Venue X can help in Greek, English, French or
Italian. That sentence is a stored template. It contains no price, no date
and no fact, so there is nothing in it the model can get wrong.

It does **not** take the booking in that language. If the guest writes again
in the same unsupported language → **escalate** (§13).

The list is in `policy.json`. A different venue changes the list without
touching code.

### Who is writing

Every message arrives on a channel, and the channel says who sent it. The
agent looks the guest up **before** it asks for anything.

| channel | what identifies the guest |
|---|---|
| WhatsApp | the phone number |
| email | the from address |
| phone | the caller ID |

`customers.phone` and `customers.email` are both unique: one number is one
record, one address is one record. A guest is found by **either** key — a
WhatsApp guest whose email is already on file is that same customer, not a new
one.

**Found.** The record supplies four things and nothing else: `name`, `phone`,
`email`, `deposit_required`. The agent treats those as already collected and
asks only for what is still missing. Asking a returning guest for their name
is the fastest way to look broken.

**Not found.** The agent collects the fields normally (§9) — but it still has
the channel's own field. A guest on WhatsApp is never asked for a phone
number. A guest on email is never asked for an email address.

**Every booking needs an email, on every channel.** The payment link is sent by
email, so a guest who calls or writes on WhatsApp is still asked for one. A
customer record therefore always carries all four fields.

**The agent reads the record. It never writes to it.** `deposit_required`
above all: only a person sets that flag (§8).

**The conversation beats the record.** If the name on file and the name the
guest gives are different — a shared phone, someone booking for a friend — the
name given in the conversation is the one used for this booking. The agent
does not correct the guest with the record.

Knowing who a guest is does not mean knowing what they did. The agent never
mentions a past booking it has not read.

### Which booking they mean

MODIFY (§10), CANCEL (§11) and a late arrival all act on a booking that
already exists. The agent finds it the same way it found the guest: by who
they are.

- **Whose.** Only bookings belonging to the customer the channel identified.
- **Which.** Only `confirmed` and `pending_deposit`. A cancelled booking is
  history and cannot be changed.
- **When.** Only nights that have not finished. Tonight's table still counts at
  20:12; last Tuesday's does not.
- **One match** → that is the one.
- **More than one** → the agent asks which, by date and time. It never picks.
- **None** → it says so plainly and offers to make one.

The agent never changes a booking it has not read, and never invents one.

---

## 2. What Venue X sells

Venue X is a restaurant early in the evening and a party venue after
midnight. A guest books one of three things:

| product | what it is |
|---|---|
| `dinner` | a table for eating. Kitchen is open 20:00–01:00. |
| `bottle_service` | a party table. Champagne, magnums. The show starts at 00:00 and runs 3 hours. |
| `both` | eat first, then carry on for the party, at the same table or at the bar. |

**The agent must know which one the guest wants before it can quote a
price.** The same start time costs a different amount for each. If the guest
has not said, the agent asks.

---

## 3. Calendar

The venue is closed most of the year. When open, it works every single day.

| period | dates | minimum spend applies? |
|---|---|---|
| closed | 6 Oct – 30 Apr | — |
| low season | 1 May – 24 May | no |
| high season | 25 May – 15 Sep | **yes** |
| low season | 16 Sep – 5 Oct | no |

- Bookings for the coming season open in **late February**.
- **Same season only.** In February 2026 a guest can book for August 2026,
  not for August 2027. A request for a future season → the agent says when
  books open for it.

These dates shift a little every year. They live in `policy.json` so they
can be changed without touching code.

---

## 4. Hours

| | |
|---|---|
| venue open | 19:30 – 03:00 |
| kitchen | 20:00 – 01:00 |
| show / party | 00:00 – 03:00 |
| agent answers | **always: 24 hours, every day** |
| reservation manager reachable | 10:00 – 23:00 |

**The agent has no office hours.** A request arriving at 04:00 is answered at
04:00. That is the point of having it.

The 10:00–23:00 window belongs to the **reservation manager**, and it only
matters for escalations (§13). A case raised at 04:00 sits in the queue until
10:00. The agent still replies immediately: it tells the guest a person will
come back to them, and it does not leave them waiting in silence.

---

## 5. How a booking works

A booking is **a start time plus 2 hours**. There is no fixed grid of
seatings. One table can start at 19:30 and another at 20:00; they finish at
21:30 and 22:00.

- Start times are on the hour or the half hour only: 19:30, 20:00, 20:30 …
  **22:15 is not a valid request.** The agent offers the nearest valid times.
- A seating lasts **120 minutes**. Staff bring the bill at 105 minutes.
- Earliest start for `bottle_service`: **21:30**. The party has not begun
  before that, so there is no bottle service table to sell.
- Latest start for `dinner`: **00:00**
- Latest start for `bottle_service`: **01:00**

The venue wants each table to turn about **3 times** a night. That target is
why the pricing in §7 is built around three 2-hour slots.

---

## 6. Capacity

Venue X has no fixed tables with fixed seats. It has a **pool of tables and
a pool of chairs**, and the staff combine them for each booking. Two guests
take one table and two chairs. Seven guests take two tables joined and seven
chairs.

So the venue counts **two resources, not one**.

### What one booking uses

How many people fit on one table depends on the product. Guests eating are
sitting down. Guests on bottle service are standing, dancing and moving
around the table, so more of them fit around the same table and the number
of chairs stops mattering.

| product | tables it uses | chairs it uses |
|---|---|---|
| `dinner` | `ceil(party_size / max_per_table.dinner)` | one per guest |
| `bottle_service` | `ceil(party_size / max_per_table.bottle_service)` | **not counted** |
| `both` | `ceil(party_size / max_per_table.dinner)` | one per guest |

A bottle service table with 6 guests and 4 chairs is normal. Nobody at the
venue counts those chairs, and the agent does not either.

`both` uses the **dinner** limit, not the bottle service one. Those guests
have to sit down and eat at the start of the night. Counting them as a party
would put six people around a table meant for four.

### Areas

Venue X has two areas and they are counted **separately**. A free spot in one
does not help a party that needs the other.

| area | `dinner` | `bottle_service` |
|---|---|---|
| main | `max_per_table.dinner` per table, tables can be joined | `max_per_table.bottle_service` per table, tables can be joined |
| bar | `bar_max_party.dinner`, one booking | `bar_max_party.bottle_service`, one booking |

Venue X's bar values: **3 people for dinner, 4 for bottle service**.

The bar numbers are a **hard cap on one booking**, not a per-table number.
Bar spots cannot be joined. A party of 5 cannot sit at the bar even when the
bar is completely empty.

The agent looks in the **main area first**. It offers the bar only when the
main area is full and the party fits under the bar cap. A guest who asks for
the bar by name gets the bar.

### Dinner at the bar

Dinner at the bar is limited by **slot**, not only by party size.

| season | dinner at the bar |
|---|---|
| high | **slot 1 only** (19:30 to 21:00) |
| low | **slots 1 and 2** (19:30 to 23:00) |

Slot 3 is never dinner at the bar, in any season. Bottle service at the bar
has no slot limit beyond the normal latest start in §5.

### Moving to the bar

A party that ate at a table can carry on at the bar, for drinks or for
bottle service. This is a **continuation of the same booking**, not a new
one.

- The **table is released** when they move. It goes back into the pool and
  can be sold to the next party.
- A **bar spot is taken** for the slots they stay.
- The party must fit the **bar bottle service cap**. Six people who ate at a
  table cannot all move to the bar.
- The money is the same as if they had stayed at their table: **+200 pp per extra slot** (§7). Moving to the bar is neither cheaper nor dearer.

### When the venue is full

A request does not fit if, **at any moment inside its 2-hour window**, either

- the free chairs are fewer than the chairs that booking uses, **or**
- the free tables are fewer than the tables that party needs.

Bookings start every 30 minutes and last 2 hours, so windows overlap. The
check walks the requested window in 30-minute steps and counts what every
overlapping booking is using at each step. If one step fails, the answer is
full.

The bookings come from the reservations database, and **status decides whether
a row is counted**:

| status | counted? |
|---|---|
| `confirmed` | yes |
| `pending_deposit` | yes — it holds tables and chairs like a confirmed one (§8) |
| `cancelled` | **never** |

A cancelled row is history. Counting one makes the venue look full when it is
not, and a guest is turned away from a table nobody is sitting at.

One more row is never counted: **a booking does not count against itself**.
When the agent checks whether a change fits, it is asking about the new shape,
and the old shape is about to disappear. Count both and a party of 5 growing
to 7 is measured as 12, so every change is refused as full.

This one is not a venue setting. It is true at every venue and in every
season, so it stays here and does not appear in `policy.json`.

### The chair buffer

Chairs are movable. In practice the staff find a few extra when a table runs
2–3 chairs short, and the next party waits a little at the door. So the count
above allows a buffer:

- **Chairs: the venue counts `chair_buffer_percent` more chairs than  it  owns.** Venue X's value is **20%**.
- **Tables: no buffer, ever.** A table cannot be borrowed. The table count
  is a hard limit.

The chair buffer only ever matters for `dinner` and `both`. Bottle service
does not count chairs at all, so nothing there can reach the buffer.

This is overbooking, and the file says so plainly. It is allowed only because
the cost lands on time, not on a table: a guest waits, a guest is never sent
away.

**A booking that only fits inside the buffer is a different promise, and the
agent must say so.** Not *"confirmed, 22:00"* but *"confirmed for 22:00,
there may be a short wait of about 15 minutes at the door"*. Booking a guest
into the buffer without telling them is forbidden. §11 asks them to arrive
15 minutes early, so the venue owes them the same honesty back.

Set `chair_buffer_percent` to `0` and the venue becomes strict. It is a
config number, not a rule in the code.

### Buffer versus overbooking

Two different things, and the file keeps them apart.

| | allowed? | what it costs the guest |
|---|---|---|
| **chair buffer** | **yes**, 20%, and the guest is told | a short wait at the door |
| **table overbooking** | **never** | being sent away |

`overbooking.tables_allowed` is `false`. The agent never confirms more tables
than exist, and it never cancels or bumps a booking it already confirmed, no
matter how much the other party would spend.

The venue itself does overbook tables, by instinct, with a person reading the
room. The agent does not, because the failure mode is a confirmed guest with
a paid deposit turned away at the door by software. When a party wants a
specific table the agent escalates (§13). Choosing which table a party sits at is the host's job on the night, not the agent's.

### The rules (this file)

- Smallest party: **2**.
- A party too big for one table is seated on **joined tables**.
- More than 20 people → **escalate** (§13). Groups up to about 40 happen in
  season, but they need a floor plan, so a person handles them.
- Large groups are usually given a **fixed set menu or a package offer**
  instead of the normal minimum spend. A person builds that offer, not the
  agent. The agent may say this is how it works, but never quotes a price.

### The numbers (not this file)

`total_tables`, `total_chairs`, `max_per_table.dinner`,
`max_per_table.bottle_service`, `chair_buffer_percent`, `bar_max_party.dinner`,
`bar_max_party.bottle_service`, how many bar spots exist,
and the area each table belongs to live in **`config/tables.json`**. They
change during a season and they are different at every venue, so this file
never names them.

---

## 7. Price rules

### The three slots

The slots are a **pricing band, not a booking grid**. A booking's slot is
decided by which band its start time falls into.

| slot | start times in this band | what it is |
|---|---|---|
| 1 | 19:30 – 21:00 | dinner, sun still up |
| 2 | 21:30 – 23:00 | late dinner |
| 3 | 23:30 – 01:00 | the party |

### Minimum spend

**Low season has no minimum spend at all.** Any product, any slot, any party
size. The whole of this section only fires between 25 May and 15 Sep.

In high season, per person:

| what the guest books | minimum spend |
|---|---|
| `dinner`, slot 1 (19:30–21:00) | none |
| `dinner`, slot 2 or 3 (21:30–01:00) | **250 pp** |
| `bottle_service`, start 21:30 – 23:00 | **250 pp** |
| `bottle_service`, start 23:30 – 00:30 | **400 pp** |
| `bottle_service`, start 01:00 | **200 pp** |
| `both` | the dinner price for the first slot, **+200 pp per extra slot** |
| keeping the table for one more slot | **+200 pp per extra slot** |
| moving from a table to the bar for one more slot | **+200 pp per extra slot** |
| `bottle_service` at the bar | **same price as the main area** |
| `dinner` at the bar | **never has a minimum spend** |


**Read the bottle service prices from the table. Do not try to compute
them.** They are three separate prices the venue charges, not one formula:

- 23:30–00:30 is the most expensive because it is the table for the show.
  The party starts at 00:00, and that is the seat people pay for.
- 21:30–23:00 is cheaper because the guest is sitting through an hour of
  nothing before the show begins.
- 01:00 is also cheap because only one slot is left before the venue closes.

The only thing that *is* a rule is the extension: **one extra slot always
costs 200 pp**, at a table or at the bar.

**Dinner at the bar never carries a minimum spend.** That is not a special
rule, it falls out of the others: in high season the bar only serves dinner
in slot 1, and slot 1 dinner has no minimum. In low season nothing has a
minimum. So the case cannot arise.

Worked through:

- Slot 1 guest who stays for the whole night adds slots 2 and 3 →
  200 × 2 = **400 pp on top of what they ate**.
- Slot 2 guest who stays for the party adds slot 3 → **+200 pp**, so
  250 + 200 = **450 pp** total. They pay more than the slot 1 guest because
  a fresh late-dinner arrival is worth more.
- The extension price is **per person**, whatever the party size. A table of
  6 keeping the party slot pays 6 × 200 = €1,200.

Minimum spend is a **floor, not a charge**. If a guest eats €300 in slot 1
that is fine, there was no floor. The floor is only checked against what
they actually spend.

### No discounts

Prices and minimum spends are **fixed**. The agent never offers a discount,
never agrees to one, and never hints that one might be possible.

A guest who asks for a discount gets a **clear no, straight away, in the same
reply**. The agent does not pass the question on, and it does not say it will
check.

---

## 8. Deposit and payment

- **€50 per person**, on every booking.
- Paid through a **payment link** the agent sends. Which methods the link
  offers is not this file's business.
- **The deposit is taken off the final bill.** It is an advance payment, not
  a fee.
- Exception: a customer record carrying the flag `deposit_required: false`
  pays no deposit. A person sets that flag in the customer DB. The agent
  only reads it and never sets it, never infers it from what a guest claims.
- A booking is not confirmed until the payment arrives. Until then it is
  `pending_deposit`.

### No waiver on request

The deposit is **not negotiable**. Being a regular is not something a guest
can talk their way into. The flag decides, and only the flag.

A guest who asks to skip the deposit gets a **clear no, straight away, in the
same reply**, together with the amount due. The agent does not pass it on and
does not say it will check.

Only if the guest **insists after being refused** does it go to the
reservation manager (§13), who is the person who can set the flag.

### Pending bookings

The agent takes a booking as far as **`pending_deposit`** and stops there. It
sends the guest a payment link. Confirmation happens afterwards, outside the
agent, and i-host holds the confirmed plan for the night.

- A pending booking **holds its tables and chairs**, exactly like a confirmed
  one. Otherwise two guests could both be told the same table is free.
- The hold lasts `pending_hold_hours`. Venue X's value is **6 hours**.
- The hold **never runs past the booking's own start time**. A booking made
  two hours before it starts is held for two hours, not six.
- When the hold expires the booking is **released automatically** and the
  guest is told, in the channel they wrote in.
- An expired hold makes the booking **`cancelled`**, with the reason recorded
  as the hold running out. No deposit was ever paid, so the §11 deposit rules
  do not apply: there is nothing to keep and nothing to refund.
- **Exception.** A booking whose customer has `deposit_required: false` has
  nothing to pay, so **its hold never expires**. The guest keeps the
  reservation and it waits for a person to confirm it.

---

# PART B. THE FIVE ACTIONS

## 9. BOOK

### What the agent must collect before it can create a booking

**First identify the guest (§1).** The channel and the customer record may
already supply some of the seven below.

1. name
2. number of guests
3. date
4. start time (a valid one, see §5)
5. product: `dinner`, `bottle_service`, or `both`
6. phone number
7. email

Anything missing → the agent asks for it. **It never invents a value.**

With all seven, the agent creates the booking as `pending_deposit` and sends
the payment link (§8). The deposit is not something the agent waits for. It
is what turns a pending booking into a confirmed one, after the agent's part
is done.

A customer record with `deposit_required: false` skips the link. The booking
still goes in as pending until a person confirms it, but it never expires
(§8).

### A request is invalid if

- the date is outside the season (§3) — offer the nearest open date
- the date is in a future season — say when books open for it
- the start time is not on the hour or half hour — offer the nearest two
- the start time is past the latest start for that product (§5) — offer the
  other product, or an earlier time
- the date has already passed — say so and ask for a new date
- the party is smaller than 2 — the smallest booking is 2 people (§6)
- the party is larger than 20 — **escalate** (§13)

### When there is no table

The agent says Venue X is full that night, then **offers the nearest free
slot within 2–3 days before or after**, chosen around the dates the guest is
on the island. So the agent asks when they arrive and when they leave, if it
does not already know.

There is no waitlist. The answer is a real alternative date, or nothing.

---

## 10. MODIFY

### When a change is allowed

| season | rule |
|---|---|
| low | **any change, any time** |
| high | **only 4 or more days before** the booked date |

High season example: a booking for 20 July can be changed on 16 July (4 days
before). A request on 17 July (3 days before) is **refused**.

The deadline is the same number as the cancellation refund deadline (§11), on
purpose. If modifying were harder than cancelling, a guest could cancel with a
full refund and rebook the new date instead, and the rule would mean nothing.

A permitted change still depends on **availability** of the new date and
time. If the new slot is full, the old booking stands unchanged and the agent
never cancels the old one before the new one is confirmed.

### What happens to the money

- The **deposit carries over** to the new booking. It is not taken again.
- **Party grows:** the guest pays €50 for each extra person, and the minimum
  spend is recalculated on the new number.
- **Party shrinks:** the deposit is **kept in full**. No refund for the
  people who dropped out. The minimum spend is recalculated on the new,
  smaller number.
- Changing the **product** (dinner → bottle service) or the **time** can
  change the minimum spend. The agent recalculates and tells the guest the
  new figure before confirming.

**A change does not restart the hold.** A `confirmed` booking stays
`confirmed` while the extra deposit is unpaid, and no new 6-hour hold begins.
The change is already in the database and the table is already the guest's.

Sending it back to `pending_deposit` would let the software cancel a booking
the guest has already paid a deposit on, because €100 arrived an hour late.
That is the same failure §6 refuses for tables — a confirmed guest turned away
by software. Chasing a late €100 is a person's job, not the agent's.

The same is true for a booking nobody has paid for yet. A `pending_deposit`
booking keeps the expiry it was given when it was made. Booked at 14:30 with
six hours, it dies at 20:30 — whether the guest changes it at 19:30 or never
touches it. Restart the clock on every change and a guest keeps a table for
ever by sending one small message every five hours, and never pays.

---

## 11. CANCEL

### Cancellation

| guest cancels | deposit |
|---|---|
| 4 or more days before | **refunded in full** |
| 3 days before or later | **kept** |
| no-show | **kept** |

### Arrival

- Guests should arrive **15 minutes before** their start time.
- **15 minutes late = the table and the deposit are lost.**
- In practice the venue often softens this and offers another date instead.
  **The agent never makes that call.** It escalates.

---

## 12. INFORM

The agent answers questions about the venue. It answers **only** from these
sources:

| source | holds |
|---|---|
| `config/policy.json` | the rules in this file |
| `config/faq.md` | dress code, children, payment methods, accessibility |

Venue X's menu is not published, so there is no `menu.md` and no
`allergens.md`. Every question about a dish, an ingredient or an allergen has
no source, and therefore **escalates**. That is deliberate, not a gap.

**If the answer is not in a source, the agent does not answer.** It says it
will check and **escalates**. It never guesses about food, allergies,
prices, or anything a guest could be harmed by or charged for.

`faq.md` lives in its own file, not in `policy.json`. What the venue offers
changes during a season; the rules do not.

---

## 13. ESCALATE

Escalating is not the same as saying no.

- The policy **has** an answer and the answer is no → the agent **refuses**,
  clearly and immediately. It does not escalate.
- The policy has **no** answer, or the answer needs a person's judgement →
  the agent **escalates**.

Sending every awkward question to a person makes the agent useless. Saying no
is also an answer.

### What gets escalated

| situation | why |
|---|---|
| a party wants a specific table | the agent counts tables, it does not choose them |
| a guest insists on a deposit waiver after being refused | only a person can set the flag |
| forgiving a late arrival | depends on the night, the guest, the room |
| a group of more than 20 | needs a floor plan and a set-menu offer |
| a request for a special event renting the entire venue | not in this file |
| a question with no answer in the §12 sources | never guess |
| a guest keeps writing in an unsupported language | untested language, real money and allergens at stake |
| anything this file does not cover | the honest answer |

### To whom, and where

Always the **reservation manager**, and always **in the same conversation the
guest is already in**. A person replaces the agent. The channel does not
change.

| channel | what the guest sees |
|---|---|
| phone | the call passes to a person. If nobody is free, the agent takes the name, number and request, and a person calls back |
| WhatsApp | the next message in the same thread comes from a person |
| email | the reply to the same thread comes from a person |

The guest never repeats anything and never has to move to another channel to
get an answer.

In the prototype there is no real phone line and no real inbox. The agent
writes the case to an **escalation queue** and marks the conversation as
handed over. The queue and the handover are what get demonstrated.

### The agent stops

An escalated conversation is marked **`handed_to_human`**.

- After its handover message, the agent **stops replying** in that
  conversation. It does not answer the next message, even one it could
  easily answer.
- Only a person hands it back. When they do, the conversation returns to the
  agent with everything the person said still in the history.
- Nothing is lost. Whatever the agent had already collected survives, so a
  half-finished booking continues instead of starting again.

Without this rule the person and the agent answer the same guest at the same
time, and contradict each other in front of them.

### When

The reservation manager is reachable **10:00–23:00** (§4). A case raised outside that
window waits in the queue until 10:00.

The guest does not wait. The agent answers straight away with what it knows,
says a person will confirm, and gives a realistic time like *"someone will come
back to you after 10:00"*. Escalating is never a reason to leave a message
unanswered.

### What an escalation contains

The agent never escalates empty-handed. Every case passed to a person
carries:

- what the guest asked, in the guest's own words
- everything already collected: name, date, time, party size, product,
  contact details
- which rule triggered the escalation
- the agent's recommendation, when it has one
- what the agent has already told the guest

The last line matters most. A person picking up the case has to know what was
already promised, or they will contradict it.

The analysis is automatic. The decision is not.

---
