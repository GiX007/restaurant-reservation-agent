# Policy examples

Worked examples for `config/policy.md`. Every one has its answer already
computed by hand.

These are the test for `config/policy.json` and for the functions that read
it. If the code does not reproduce these numbers, the code is wrong.

---

### BOOK

**BOOK-1.** 4 people, dinner, 14 July, 20:00.
High season, slot 1 → **no minimum spend**. Deposit 4 × 50 = **€200**.
Table held until 22:00.

**BOOK-2.** 2 people, dinner, 14 July, 22:00.
High season, slot 2 → **250 pp = €500 minimum**. Deposit **€100**.
Table held until 00:00.

**BOOK-3.** 6 people, bottle service, 14 July, 23:30.
High season, the show table → **400 pp = €2,400 minimum**. Deposit **€300**.
Six guests on bottle service take **one** table, not two, and their chairs
are not counted.

**BOOK-4.** 6 people, bottle service, 14 July, 22:00.
Same party size, earlier start → **250 pp = €1,500 minimum**. Deposit
**€300**.

**BOOK-5.** 2 people, bottle service, 14 July, 01:00.
One slot left → **200 pp = €400 minimum**. Deposit **€100**.

**BOOK-6.** 4 people, dinner, 10 May, 22:00.
Low season → **no minimum spend**. Deposit **€200**.

**BOOK-7.** Guest asks for 22:15.
Not a valid start time → agent offers **22:00 or 22:30**.

**BOOK-8.** Guest asks for dinner at 00:30.
Past the latest dinner start → agent offers **bottle service at 00:30**, or
**dinner at 00:00**.

**BOOK-9.** Guest asks for 14 July, venue is full.
Agent says it is full, asks which days they are on the island, and offers
the nearest free slot **within 2–3 days** of 14 July.

**BOOK-10.** Venue has 2 tables and 12 chairs, buffer 20% → 14 countable chairs,
`max_per_table.dinner` = 4. A booking exists 21:00–23:00 for 4 people on
dinner (1 table, 4 chairs). A guest asks for 22:00 for 6 people, also dinner.
From 22:00 to 23:00 the two overlap: chairs 4 + 6 = 10, under 14 which is fine.
Tables 1 + ceil(6/4) = **3 needed, only 2 exist** → **full**. The buffer does
not help, because tables have no buffer.

**BOOK-11.** Same venue, a booking exists 21:00–23:00 for 4 people on dinner. A
guest asks for 22:00 for 4 more, also dinner.
Tables 1 + 1 = 2, exactly what exists which is fine. Chairs 4 + 4 = 8, under 12 —
fits without the buffer. → **confirmed, no wait mentioned.**

**BOOK-12.** Same again, but the existing booking is for 10 people on dinner
(3 tables, so assume 3 tables exist) using 10 chairs, and a guest asks for
4 more at 22:00, also dinner.
Chairs 10 + 4 = 14 → over the 12 real chairs, inside the 14 with buffer.
→ **confirmed, and the agent says there may be a short wait of about 15
minutes at the door.**

**BOOK-13.** Same venue, 2 tables. A booking exists 21:00–23:00 for 4 people on
dinner (1 table). A guest asks for 22:00 for 6 people on **bottle service**.
Tables 1 + ceil(6/6) = **2 needed, 2 exist**, so it fits. Chairs are not
counted for bottle service, so 6 guests standing around a table with 4
chairs is fine. → **confirmed**. Exactly the request that failed in BOOK-10,
but on bottle service it fits.

**BOOK-14.** 3 people, dinner, 14 July, 21:00. The main area is full at that
time, the bar has a free spot.
3 is under the bar dinner cap → the agent offers **the bar**, and says so.
It does not pretend it is a normal table.

**BOOK-15.** 5 people, bottle service, 14 July, 23:30. Only the bar is free.
The bar bottle service cap is 4, and bar spots cannot be joined → **full**.
The agent offers another date, per §9.

**BOOK-16.** 4 people ask for the bar by name, dinner, 14 July.
4 is over the bar dinner cap of 3 → the bar is not possible. The agent
offers a normal table if one is free, and says why.

**BOOK-17.** 4 people, dinner at a table 14 July 19:30, then bottle service at
the bar from 21:30.
High season. Dinner in slot 1 → no minimum. The move is a continuation, so
2 extra slots × 200 pp = **400 pp = €1,600 minimum**. Their table is
released at 21:30 and one bar spot is taken. Deposit stays **€200**.

**BOOK-18.** 6 people, dinner at a table, then they ask to move to the bar for
bottle service.
6 is over the bar bottle service cap of 4 → not possible. The agent offers
to keep their table instead, at the same +200 pp per extra slot.

**BOOK-19.** 2 people, dinner at the bar, 14 July (high season), 21:30.
High season allows bar dinner in slot 1 only, and 21:30 is slot 2 →
**refused**. The agent offers the bar at 19:30 to 21:00, or a normal table
at 21:30.

**BOOK-20.** 2 people, dinner at the bar, 10 May (low season), 21:30.
Low season allows slot 2 at the bar → **allowed**, and no minimum spend
because it is low season. Deposit **€100**.

**BOOK-21.** 30 people, dinner, 14 July, 20:30.
More than 20 → **escalate**. No confirmation, no price. A person will offer
a set menu.

**BOOK-22.** In February 2026 a guest asks for August 2027.
Future season → agent says books for 2027 open in late February 2027.

**BOOK-23.** 4 people, dinner, 14 July, 19:30. All seven fields given.
The agent creates the booking as **`pending_deposit`**, sends the payment
link for **€200**, and says the hold lasts 6 hours. The table is held for
those 6 hours. If nothing is paid it is released and the guest is told.

**BOOK-24.** Same booking, but made at 18:30 on 14 July itself, one hour before
the 19:30 start.
The hold cannot run past the start time → it lasts **1 hour**, not 6.

**BOOK-25.** Same booking, but the guest's record has `deposit_required: false`.
No link is sent and there is nothing to pay, so the hold **never expires**.
The reservation stands and waits for a person to confirm it.

**BOOK-26.** The booking as BOOK-23, and nothing is paid within 6 hours.
The booking becomes **`cancelled`**, the tables and chairs go back into the
pool, and the guest is told in the channel they wrote in. No deposit was
paid, so nothing is kept and nothing is refunded.

### MODIFY

**MODIFY-1.** Booking 20 July (high season), guest asks on 14 July to move to 22
July.
6 days before → **allowed**, if 22 July has space. Deposit carries over.

**MODIFY-2.** Same booking, guest asks on 18 July.
2 days before → **refused**. The 20 July booking stands.

**MODIFY-3.** Same booking (20 July, high season), guest asks on 16 July.
Exactly 4 days before → **allowed**, if the new slot has space.

**MODIFY-4.** Booking 10 May (low season), guest asks on 9 May to move to 12 May.
Low season → **allowed**, if 12 May has space.

**MODIFY-5.** Booking for 4 becomes 6, high season, dinner at 22:00.
Extra deposit 2 × 50 = **€100**. Minimum spend recalculated: 6 × 250 =
**€1,500**.

**MODIFY-6.** Booking for 6 becomes 4.
Deposit **kept in full**, €300, no refund. Minimum spend recalculated:
4 × 250 = **€1,000**.

### CANCEL

**CANCEL-1.** Booking 14 July, guest cancels 9 July (5 days before).
4 or more days → **deposit refunded in full**.

**CANCEL-2.** Same booking, guest cancels 13 July (1 day before).
Under 4 days → **deposit kept**.

**CANCEL-3.** Booking 14 July, guest cancels on 10 July.
Exactly 4 days before → **deposit refunded in full**.

**CANCEL-4.** Booking 14 July, guest cancels on 11 July (3 days before).
Under 4 days → **deposit kept**. And the same guest cannot modify instead:
§10 uses the same 4-day deadline.

**CANCEL-5.** At 22:10 a guest messages that they are stuck in traffic and will be
about 20 minutes late for their 22:00 booking.
By the rule the table and the deposit are lost, but forgiving it is a
person's call (§11) → **escalate**. The agent does not tell the guest they
have lost the table, and it does not promise the table is still there.

### INFORM

**INFORM-1.** "Do you have parking?"
Answer from `faq.md`. If it is not there → **escalate**.

**INFORM-2.** "Is the octopus gluten free?"
Answer from `allergens.md` only. If it is not there → **escalate**. The
agent never guesses about food.

### ESCALATE

**ESCALATE-1.** "I come every summer, can you skip the deposit?"
No matching customer record with `deposit_required: false` → the agent
**refuses** and states the amount due, for example €200 for 4 people. It does
not escalate, and it does not say it will check.

**ESCALATE-2.** The same guest insists.
Now it goes to the **reservation manager** (§13), the person who can set the
flag.

**ESCALATE-3.** A message written in French asking to book 4 people for 14 July.
French is supported → the agent **books it normally, in French**.

**ESCALATE-4.** A message written in Dutch.
Not supported → the agent replies **in Dutch** with the stored sentence
asking them to continue in Greek, English, French or Italian. It takes no
booking details.

**ESCALATE-5.** The same guest writes again in Dutch.
→ **escalate**. A person takes over.

**ESCALATE-6.** A WhatsApp message arrives at 04:00 asking to book for tomorrow.
The agent answers **immediately** and books it if the rules allow. Nothing
waits for 10:00 unless it needs the manager.

**ESCALATE-7.** A guest asks at 03:00 for a discount.
The agent **refuses at 03:00**, politely and clearly. Prices are fixed (§7).
Nothing is escalated, nobody is woken, and the guest has a real answer in
seconds instead of waiting until 10:00.

**ESCALATE-8.** A guest on WhatsApp asks something with no answer in the §12 sources.
The agent replies **in WhatsApp**, says it will check and that a person will
come back to them, marks the conversation `handed_to_human`, and puts the
case in the queue. It does **not** answer their next WhatsApp message.

**ESCALATE-9.** The reservation manager answers in that same WhatsApp thread, sorts
it out, and hands the conversation back.
The agent takes over again, with the manager's messages in its history, and
carries on from where the booking had stopped.

**ESCALATE-10.** A guest calls at 14:00 wanting a table for 30 people.
Over 20 → escalate. The manager is reachable, so **the call passes to a
person**.

**ESCALATE-11.** The same call at 04:00.
Nobody is reachable. The agent takes the name, number and request, says a
person will call back after 10:00, and puts the case in the queue.
