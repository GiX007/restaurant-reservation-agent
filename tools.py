### tools.py
"""The eight tools the agent may call.

Phase 4 fixes the signatures. Phase 5 writes the bodies.

The rule the README diagram makes: the agent never touches the database.
It calls one of these, and the tool does.

Times are ISO 8601 strings everywhere - "2026-07-12" for a date, "20:00" for a
clock time, "2026-07-07T18:40:00+03:00" for a moment. The database stores TEXT,
the dialogue files store strings, and the model only ever sees strings. Parsing
happens inside a tool, never outside one.
"""

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


DB_PATH = "db/venue.db"
POLICY_PATH = "config/policy.json"
TABLES_PATH = "config/tables.json"
ESCALATIONS_PATH = "runs/escalations.jsonl"

# Shown to the guest wherever a real payment link would go. Phase 5 has no
# real link yet, so this is what every dialogue file expects to see.
PAYMENT_LINK_PLACEHOLDER = "{payment_link}"

# Venue X operates only within Greek daylight-saving time (May-October), so
# the UTC offset never changes across a booking. See policy.json venue.timezone.
TZ_OFFSET = "+03:00"


def _load_json(path: str) -> dict:
    """
    Read one config file.

    Params: path - path to a JSON file.
    Return: its contents as a dict. Loaded once, at import time - config does
            not change while the agent is running.
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


POLICY = _load_json(POLICY_PATH)
TABLES = _load_json(TABLES_PATH)


# ---------------------------------------------------------------------------
# What the tools hand back
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Customer:
    """
    One customers row, as the agent is allowed to see it.

    Purpose: carry the four fields policy.md #1 permits, plus the id.
    Frozen: #1 says the agent reads the record and never writes it.
    """
    id: int
    name: str
    phone: str
    email: str
    deposit_required: bool


@dataclass(frozen=True)
class Booking:
    """
    One bookings row, plus what it means right now.

    Purpose: the agent reads a booking and its consequences in one go, so it
             never has to work anything out itself.
    Note: modify_allowed and refund_if_cancelled_now are not stored in the
          database. They are computed against the `now` that was passed in.
    """
    id: int
    customer_id: int
    service_date: str
    start_at: str
    end_at: str
    party_size: int
    product: str
    area: str
    status: str
    hold_expires_at: str | None
    deposit_eur: int
    payment_link: str | None
    modify_allowed: bool
    refund_if_cancelled_now: int


@dataclass(frozen=True)
class Availability:
    """
    The answer to "does this fit?".

    Purpose: one call gives the yes/no, the area, whether the chair buffer was
             needed, and what to offer instead when the answer is no.
    """
    fits: bool
    area: str | None
    needs_buffer: bool
    alternative_dates: list[str]


@dataclass(frozen=True)
class Quote:
    """
    Every figure a guest can be told about a booking.

    Purpose: minimum spend and deposit always travel together, so they are one
             answer and can never disagree with each other.
    Note: deposit_extra_eur is what the guest pays now. For a new booking that
          is the whole deposit. For a change it is the difference, and 0 when
          the party shrinks.
    """
    minimum_spend_pp: int
    minimum_spend_total: int
    deposit_eur: int
    deposit_extra_eur: int
    hold_hours: int
    deposit_deducted_from_bill: bool


@dataclass(frozen=True)
class Cancellation:
    """
    What a cancellation did.

    Purpose: the cancelled booking and the money in one answer.
    """
    booking: Booking
    refund_eur: int
    deposit_kept: bool


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    """
    Open the venue database.

    Return: a connection whose rows can be read by column name.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_now(now: str) -> datetime:
    """
    Turn an ISO 8601 moment into a plain datetime, dropping the timezone.

    Venue X has one timezone (Europe/Athens, see policy.json), so the offset
    carries no extra information once it is parsed.

    Params: now - "2026-07-15T21:14:00+03:00" or similar.
    Return: a naive datetime.
    """
    return datetime.fromisoformat(now).replace(tzinfo=None)


def _season(date: str) -> str:
    """
    High or low season for a date. policy.md #3.

    The boundaries are month-day only, so they repeat every year without
    naming one.

    Params: date - "YYYY-MM-DD".
    Return: "high" or "low".
    """
    month_day = date[5:]
    calendar = POLICY["calendar"]
    if calendar["high_season_start"] <= month_day <= calendar["high_season_end"]:
        return "high"
    return "low"


def _days_before(date: str, now: str) -> int:
    """
    Whole calendar days between now and a date, ignoring the time of day.

    Params: date - "YYYY-MM-DD", the night in question.
            now  - ISO 8601, the moment asked from.
    Return: how many days ahead of `now` the date is. Negative if it has
            already passed.
    """
    day = datetime.strptime(date, "%Y-%m-%d").date()
    today = _parse_now(now).date()
    return (day - today).days


def _modify_allowed(service_date: str, now: str) -> bool:
    """
    Can this booking still be changed right now? policy.md #10.

    Params: service_date - "YYYY-MM-DD", the booking's night.
            now           - ISO 8601, the moment asked from.
    Return: True when the change is still inside the deadline for the season.
    """
    season = _season(service_date)
    deadline = POLICY["modify"]["deadline_days"][season]
    return _days_before(service_date, now) >= deadline


def _refund_if_cancelled_now(service_date: str, now: str, deposit_eur: int) -> int:
    """
    What a guest would get back if they cancelled at this moment. policy.md #11.

    Params: service_date - "YYYY-MM-DD", the booking's night.
            now           - ISO 8601, the moment asked from.
            deposit_eur   - the deposit paid on this booking.
    Return: deposit_eur when still outside the refund deadline, else 0.
    """
    deadline = POLICY["cancel"]["refund_deadline_days"]
    if _days_before(service_date, now) >= deadline:
        return deposit_eur
    return 0


def _minutes_after_open(time: str) -> int:
    """
    Turn "HH:MM" into minutes past midnight, pushed a day later if it is
    really after-midnight time on the same service night.

    The venue's night runs 19:30 to 03:00. A plain "hour * 60 + minute" would
    put "01:00" before "19:30", when on the night it comes after. Every valid
    booking time has an hour of 19-23 or 0-3, so "hour < 12" safely means
    after midnight.

    Params: time - "HH:MM".
    Return: minutes past midnight, +1440 when the time is after midnight.
    """
    hour, minute = (int(part) for part in time.split(":"))
    total = hour * 60 + minute
    if hour < 12:
        total += 24 * 60
    return total


def _in_band(time: str, band_from: str, band_to: str) -> bool:
    """
    Is a time inside a [from, to] band, both ends included?

    Params: time      - "HH:MM" to test.
            band_from - "HH:MM", the band's start.
            band_to   - "HH:MM", the band's end.
    Return: True when time falls inside the band.
    """
    minutes = _minutes_after_open(time)
    return _minutes_after_open(band_from) <= minutes <= _minutes_after_open(band_to)


def _slot_id(start_time: str) -> int:
    """
    Which pricing slot a start time falls into. policy.md #7, "the three slots".

    Params: start_time - "HH:MM".
    Return: 1, 2 or 3.
    """
    for slot in POLICY["slots"]:
        if _in_band(start_time, slot["from"], slot["to"]):
            return slot["id"]
    raise ValueError(f"no pricing slot covers {start_time}")


def _dinner_pp(start_time: str) -> int:
    """
    High-season minimum spend per person for dinner. policy.md #7.

    Params: start_time - "HH:MM".
    Return: euros per person.
    """
    slot = _slot_id(start_time)
    return POLICY["minimum_spend"]["dinner_by_slot_pp"][str(slot)]


def _bottle_service_pp(start_time: str) -> int:
    """
    High-season minimum spend per person for bottle service. policy.md #7.

    Three separate prices, read straight from the table - not computed.

    Params: start_time - "HH:MM".
    Return: euros per person.
    """
    for band in POLICY["minimum_spend"]["bottle_service_by_start_pp"]:
        if _in_band(start_time, band["from"], band["to"]):
            return band["pp"]
    raise ValueError(f"no bottle service price covers {start_time}")


def _both_pp(start_time: str) -> int:
    """
    High-season minimum spend per person for "both". policy.md #7.

    The dinner price for the slot the guest arrives in, then extra_slot_pp
    for every slot after that one.

    Params: start_time - "HH:MM".
    Return: euros per person.
    """
    slot = _slot_id(start_time)
    extra_slots = len(POLICY["slots"]) - slot
    return _dinner_pp(start_time) + extra_slots * POLICY["minimum_spend"]["extra_slot_pp"]


def _minimum_spend_pp(date: str, start_time: str, product: str, area: str) -> int:
    """
    Minimum spend per person for a request. policy.md #7.

    Params: date       - "YYYY-MM-DD", decides the season.
            start_time - "HH:MM", decides the slot.
            product    - "dinner", "bottle_service" or "both".
            area       - "main" or "bar".
    Return: euros per person. 0 in low season, always.
    """
    if _season(date) != "high":
        return 0
    if area == "bar" and product == "dinner":
        # Dinner at the bar never has a minimum spend (policy.md #6, #7).
        return POLICY["minimum_spend"]["bar"]["dinner_pp"]
    if product == "dinner":
        return _dinner_pp(start_time)
    if product == "bottle_service":
        # Same price at the bar as in the main area (policy.json).
        return _bottle_service_pp(start_time)
    return _both_pp(start_time)


def _party_size_of(booking_id: int) -> int:
    """
    The party size a booking currently has in the database.

    Used to work out the deposit already paid, before a change overwrites it.

    Params: booking_id - which booking.
    Return: its current party_size.
    """
    conn = _connect()
    row = conn.execute("SELECT party_size FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    conn.close()
    return row["party_size"]


def _to_iso(db_time: str | None) -> str | None:
    """
    Turn a database timestamp into an ISO 8601 moment.

    Params: db_time - "YYYY-MM-DD HH:MM", or None.
    Return: "YYYY-MM-DDTHH:MM:SS+03:00", or None.
    """
    if db_time is None:
        return None
    return db_time.replace(" ", "T") + f":00{TZ_OFFSET}"


def _to_db_time(dt: datetime) -> str:
    """
    Format a datetime the way the database stores one.

    Params: dt - a naive datetime, Venue X local time.
    Return: "YYYY-MM-DD HH:MM".
    """
    return dt.strftime("%Y-%m-%d %H:%M")


def _start_datetime(date: str, start_time: str) -> datetime:
    """
    The real clock moment a booking starts.

    An after-midnight start_time (hour < 12) belongs to the night that began
    the evening before, so it rolls onto the next calendar day.

    Params: date       - "YYYY-MM-DD", the service night.
            start_time - "HH:MM".
    Return: a naive datetime, Venue X local time.
    """
    day = datetime.strptime(date, "%Y-%m-%d")
    hour, minute = (int(part) for part in start_time.split(":"))
    start = day.replace(hour=hour, minute=minute)
    if hour < 12:
        start += timedelta(days=1)
    return start


def _table_units(party_size: int, product: str) -> int:
    """
    How many tables one booking needs. policy.md #6.

    "both" uses the dinner limit, not the bottle service one - those guests
    sit down to eat first.

    Params: party_size - how many people.
            product    - "dinner", "bottle_service" or "both".
    Return: tables needed, rounded up.
    """
    key = "bottle_service" if product == "bottle_service" else "dinner"
    return math.ceil(party_size / TABLES["max_per_table"][key])


def _chair_units(party_size: int, product: str) -> int:
    """
    How many chairs one booking uses. policy.md #6.

    Bottle service guests stand, so chairs are not counted at all.

    Params: party_size - how many people.
            product    - "dinner", "bottle_service" or "both".
    Return: chairs needed.
    """
    return 0 if product == "bottle_service" else party_size


def _existing_bookings(area: str, ignore_booking_id: int | None) -> list[sqlite3.Row]:
    """
    Every live booking in one area, for a capacity check.

    Params: area              - "main" or "bar".
            ignore_booking_id - a booking to leave out (it does not count
                                against itself, policy.md #6), or None.
    Return: rows with status confirmed or pending_deposit.
    """
    conn = _connect()
    rows = conn.execute(
        "SELECT start_at, end_at, party_size, product, id FROM bookings"
        " WHERE area = ? AND status IN ('confirmed', 'pending_deposit')",
        (area,),
    ).fetchall()
    conn.close()
    return [row for row in rows if row["id"] != ignore_booking_id]


def _fits_main(
    date: str, start_time: str, party_size: int, product: str, ignore_booking_id: int | None
) -> tuple[bool, bool]:
    """
    Does this request fit in the main area? policy.md #6.

    Walks the 2-hour window in 30-minute steps. Tables have no buffer, ever;
    chairs allow the venue's chair_buffer_percent.

    Params: as check_availability.
    Return: (fits, needed the chair buffer).
    """
    total_tables = sum(1 for table in TABLES["tables"] if table["area"] == "main")
    total_chairs = TABLES["total_chairs"]
    buffered_chairs = int(total_chairs * (1 + TABLES["chair_buffer_percent"] / 100))

    new_tables = _table_units(party_size, product)
    new_chairs = _chair_units(party_size, product)
    existing = _existing_bookings("main", ignore_booking_id)

    window_start = _start_datetime(date, start_time)
    duration = POLICY["booking"]["duration_minutes"]
    step = POLICY["capacity"]["check_step_minutes"]

    needed_buffer = False
    for offset in range(0, duration, step):
        point = window_start + timedelta(minutes=offset)
        tables_used = new_tables
        chairs_used = new_chairs
        for row in existing:
            start_dt = datetime.strptime(row["start_at"], "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(row["end_at"], "%Y-%m-%d %H:%M")
            if start_dt <= point < end_dt:
                tables_used += _table_units(row["party_size"], row["product"])
                chairs_used += _chair_units(row["party_size"], row["product"])

        if tables_used > total_tables:
            return False, False  # a table cannot be borrowed, ever
        if chairs_used > total_chairs:
            if chairs_used > buffered_chairs:
                return False, False
            needed_buffer = True

    return True, needed_buffer


def _bar_cap(product: str) -> int:
    """
    The bar's hard party-size cap for a product. policy.md #6.

    "both" starts as dinner, so it uses the dinner cap.

    Params: product - "dinner", "bottle_service" or "both".
    Return: the largest party the bar can seat for that product.
    """
    key = "bottle_service" if product == "bottle_service" else "dinner"
    return TABLES["bar_max_party"][key]


def _bar_dinner_slot_allowed(date: str, start_time: str) -> bool:
    """
    Is dinner at the bar sold at this time of night? policy.md #6.

    Params: date       - "YYYY-MM-DD".
            start_time - "HH:MM".
    Return: True when the slot is one the season allows at the bar.
    """
    season = _season(date)
    allowed_slots = POLICY["areas"]["bar"]["allowed_slots"]["dinner"][season]
    return _slot_id(start_time) in allowed_slots


def _fits_bar(date: str, start_time: str, party_size: int, product: str, ignore_booking_id: int | None) -> bool:
    """
    Does this request fit at the bar? policy.md #6.

    The bar is one spot that cannot be joined, so any overlapping booking
    there at all means full.

    Params: as check_availability.
    Return: True when the bar is free for the whole window and the party
            fits under the bar's cap.
    """
    if party_size > _bar_cap(product):
        return False
    if product == "dinner" and not _bar_dinner_slot_allowed(date, start_time):
        return False

    window_start = _start_datetime(date, start_time)
    window_end = window_start + timedelta(minutes=POLICY["booking"]["duration_minutes"])
    for row in _existing_bookings("bar", ignore_booking_id):
        start_dt = datetime.strptime(row["start_at"], "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(row["end_at"], "%Y-%m-%d %H:%M")
        if start_dt < window_end and window_start < end_dt:
            return False
    return True


def _fits(
    date: str, start_time: str, party_size: int, product: str, ignore_booking_id: int | None = None
) -> tuple[bool, str | None, bool]:
    """
    Does this request fit anywhere? Tries main first, then the bar.

    Params: as check_availability.
    Return: (fits, area it fits in or None, needed the chair buffer).
    """
    for area in POLICY["areas"]["search_order"]:
        if area == "main":
            fits, needs_buffer = _fits_main(date, start_time, party_size, product, ignore_booking_id)
            if fits:
                return True, "main", needs_buffer
        elif _fits_bar(date, start_time, party_size, product, ignore_booking_id):
            return True, "bar", False
    return False, None, False


def _alternative_dates(
    date: str, start_time: str, party_size: int, product: str, ignore_booking_id: int | None = None
) -> list[str]:
    """
    The nearest free dates around a full night. policy.md #9.

    Looks one day back, then one day forward, then two back, and so on, up to
    the policy window, and stops as soon as one side finds a fit. A date that
    does not fit is skipped silently.

    Params: as check_availability, minus now.
    Return: up to two dates, nearest-before then nearest-after.
    """
    window = POLICY["no_availability"]["alternative_days_window"]
    base = datetime.strptime(date, "%Y-%m-%d").date()

    alternatives = []
    for step in (-1, 1):
        for delta in range(1, window + 1):
            candidate = (base + timedelta(days=step * delta)).isoformat()
            fits, _, _ = _fits(candidate, start_time, party_size, product, ignore_booking_id)
            if fits:
                alternatives.append(candidate)
                break
    return alternatives


def _create_customer(name: str, phone: str, email: str, now: str) -> int:
    """
    Register a guest who has never booked before.

    Not one of the agent's tools - policy.md #1 says the agent reads the
    record and never writes it. This is the engine's own bookkeeping, done
    once a new guest has given a name and email, so create_booking has a
    customer_id to attach the booking to. Every new guest needs a deposit
    until a person says otherwise (policy.md #8).

    Params: name, phone, email - the three fields the agent has gathered.
            now                - ISO 8601, for created_at.
    Return: the new customer's id.
    """
    conn = _connect()
    cursor = conn.execute(
        "INSERT INTO customers (name, phone, email, deposit_required, created_at)"
        " VALUES (?, ?, ?, 1, ?)",
        (name, phone, email, _to_db_time(_parse_now(now))),
    )
    conn.commit()
    conn.close()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def find_customer(channel: str, handle: str) -> Customer | None:
    """
    Find the guest who sent this message. policy.md #1, "Who is writing".

    Params:
        channel - "whatsapp", "email" or "phone".
        handle  - the sender: a phone number on whatsapp and phone,
                  an email address on email.
    Return: the Customer, or None when nobody matches.
    """
    # Which column identifies a guest depends on the channel, and that
    # mapping lives in policy.json so a different venue can change it.
    column = POLICY["identity"]["lookup_key_by_channel"][channel]

    conn = _connect()
    row = conn.execute(
        f"SELECT id, name, phone, email, deposit_required FROM customers WHERE {column} = ?",
        (handle,),
    ).fetchone()
    conn.close()

    if row is None:
        return None
    return Customer(
        id=row["id"],
        name=row["name"],
        phone=row["phone"],
        email=row["email"],
        deposit_required=bool(row["deposit_required"]),
    )


def find_bookings(customer_id: int, now: str) -> list[Booking]:
    """
    The guest's live bookings. policy.md #1, "Which booking they mean".

    Only this customer's rows, only confirmed and pending_deposit, only nights
    that have not finished. Empty list means they have none. More than one
    means the agent must ask which - it never picks.

    Params:
        customer_id - from find_customer.
        now         - the moment to judge against, ISO 8601.
    Return: the bookings, each already carrying modify_allowed and
            refund_if_cancelled_now for this moment.
    """
    conn = _connect()
    rows = conn.execute(
        """
        SELECT bookings.*, customers.deposit_required AS customer_deposit_required
        FROM bookings
        JOIN customers ON customers.id = bookings.customer_id
        WHERE bookings.customer_id = ?
          AND bookings.status IN ('confirmed', 'pending_deposit')
        """,
        (customer_id,),
    ).fetchall()
    conn.close()

    now_dt = _parse_now(now)
    per_person = POLICY["deposit"]["per_person"]

    bookings = []
    for row in rows:
        end_dt = datetime.strptime(row["end_at"], "%Y-%m-%d %H:%M")
        if end_dt <= now_dt:
            continue  # the night is over, it does not count (policy.md #1)

        deposit_eur = row["party_size"] * per_person if row["customer_deposit_required"] else 0
        bookings.append(
            Booking(
                id=row["id"],
                customer_id=row["customer_id"],
                service_date=row["service_date"],
                start_at=_to_iso(row["start_at"]),
                end_at=_to_iso(row["end_at"]),
                party_size=row["party_size"],
                product=row["product"],
                area=row["area"],
                status=row["status"],
                hold_expires_at=_to_iso(row["hold_expires_at"]),
                deposit_eur=deposit_eur,
                payment_link=PAYMENT_LINK_PLACEHOLDER if row["status"] == "pending_deposit" else None,
                modify_allowed=_modify_allowed(row["service_date"], now),
                refund_if_cancelled_now=_refund_if_cancelled_now(row["service_date"], now, deposit_eur),
            )
        )
    return bookings


def check_availability(
    date: str,
    start_time: str,
    party_size: int,
    product: str,
    now: str,
    ignore_booking_id: int | None = None,
) -> Availability:
    """
    Does this request fit? policy.md #6, "When the venue is full".

    Walks the 2-hour window in 30-minute steps and counts tables and chairs.
    confirmed and pending_deposit count, cancelled never does.

    Params:
        date              - "YYYY-MM-DD", the night.
        start_time        - "HH:MM".
        party_size        - how many people.
        product           - "dinner", "bottle_service" or "both".
        now               - ISO 8601, for the season and booking-window rules.
        ignore_booking_id - the booking being changed. It does not count
                            against itself (#6). None for a new booking.
    Return: fits, the area it fits in, whether the chair buffer was needed,
            and alternative dates when it does not fit.
    """
    fits, area, needs_buffer = _fits(date, start_time, party_size, product, ignore_booking_id)
    if fits:
        return Availability(fits=True, area=area, needs_buffer=needs_buffer, alternative_dates=[])
    alternatives = _alternative_dates(date, start_time, party_size, product, ignore_booking_id)
    return Availability(fits=False, area=None, needs_buffer=False, alternative_dates=alternatives)


def quote_booking(
    date: str,
    start_time: str,
    party_size: int,
    product: str,
    area: str,
    deposit_required: bool,
    existing_booking_id: int | None = None,
) -> Quote:
    """
    Every figure for this request. policy.md #7 and #8.

    The model never does this arithmetic.

    Params:
        date                - "YYYY-MM-DD".
        start_time          - "HH:MM". Decides the slot, and the price.
        party_size          - how many people.
        product             - "dinner", "bottle_service" or "both".
        area                - "main" or "bar". Dinner at the bar has no
                              minimum spend.
        deposit_required    - from the Customer record. A regular pays none.
        existing_booking_id - when quoting a change, the booking being
                              changed, so deposit_extra_eur is the difference
                              and not the whole deposit again.
    Return: minimum spend per person and total, the deposit, what is due now,
            and the hold length.
    """
    per_person = POLICY["deposit"]["per_person"]
    new_deposit_full = party_size * per_person if deposit_required else 0

    if existing_booking_id is None:
        # A new booking pays the whole deposit now.
        deposit_eur = new_deposit_full
        deposit_extra_eur = new_deposit_full
    else:
        # A change never lowers the deposit already paid (policy.md #10).
        old_party_size = _party_size_of(existing_booking_id)
        old_deposit = old_party_size * per_person if deposit_required else 0
        deposit_eur = max(old_deposit, new_deposit_full)
        deposit_extra_eur = deposit_eur - old_deposit

    minimum_spend_pp = _minimum_spend_pp(date, start_time, product, area)

    return Quote(
        minimum_spend_pp=minimum_spend_pp,
        minimum_spend_total=minimum_spend_pp * party_size,
        deposit_eur=deposit_eur,
        deposit_extra_eur=deposit_extra_eur,
        hold_hours=POLICY["pending"]["hold_hours"],
        deposit_deducted_from_bill=POLICY["deposit"]["deducted_from_bill"],
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def create_booking(
    customer_id: int,
    date: str,
    start_time: str,
    party_size: int,
    product: str,
    area: str,
    now: str,
) -> Booking:
    """
    Put a new booking in the database. policy.md #9.

    Starts as pending_deposit with a hold, or as confirmed when the guest
    needs no deposit. Never call this before check_availability says it fits.

    Params:
        customer_id - from find_customer.
        date        - "YYYY-MM-DD".
        start_time  - "HH:MM".
        party_size  - how many people.
        product     - "dinner", "bottle_service" or "both".
        area        - "main" or "bar", from check_availability.
        now         - ISO 8601. The hold counts from here.
    Return: the new Booking, with its id, status, hold and payment link.
    """
    conn = _connect()
    customer_row = conn.execute(
        "SELECT deposit_required FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    deposit_required = bool(customer_row["deposit_required"])

    start_dt = _start_datetime(date, start_time)
    end_dt = start_dt + timedelta(minutes=POLICY["booking"]["duration_minutes"])
    created_dt = _parse_now(now)

    if deposit_required:
        status = "pending_deposit"
        # The hold never runs past the booking's own start time (policy.md #8).
        hold_dt = min(created_dt + timedelta(hours=POLICY["pending"]["hold_hours"]), start_dt)
        hold_expires_at = _to_db_time(hold_dt)
    else:
        status = "confirmed"
        hold_expires_at = None

    cursor = conn.execute(
        """
        INSERT INTO bookings
            (customer_id, service_date, start_at, end_at, party_size, product,
             area, status, created_at, hold_expires_at, cancel_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            customer_id,
            date,
            _to_db_time(start_dt),
            _to_db_time(end_dt),
            party_size,
            product,
            area,
            status,
            _to_db_time(created_dt),
            hold_expires_at,
        ),
    )
    conn.commit()
    booking_id = cursor.lastrowid
    conn.close()

    per_person = POLICY["deposit"]["per_person"]
    deposit_eur = party_size * per_person if deposit_required else 0

    return Booking(
        id=booking_id,
        customer_id=customer_id,
        service_date=date,
        start_at=_to_iso(_to_db_time(start_dt)),
        end_at=_to_iso(_to_db_time(end_dt)),
        party_size=party_size,
        product=product,
        area=area,
        status=status,
        hold_expires_at=_to_iso(hold_expires_at),
        deposit_eur=deposit_eur,
        payment_link=PAYMENT_LINK_PLACEHOLDER if status == "pending_deposit" else None,
        modify_allowed=_modify_allowed(date, now),
        refund_if_cancelled_now=_refund_if_cancelled_now(date, now, deposit_eur),
    )


def modify_booking(
    booking_id: int,
    now: str,
    party_size: int | None = None,
    date: str | None = None,
    start_time: str | None = None,
    product: str | None = None,
) -> Booking:
    """
    Change a booking that already exists. policy.md #10.

    Pass only what changes; leave the rest None. The deposit carries over and
    the hold is never restarted. Never call this before check_availability
    says the new shape fits.

    Params:
        booking_id - which booking.
        now        - ISO 8601. Decides whether the change is still allowed.
        party_size - the new number, or None.
        date       - the new date, or None.
        start_time - the new time, or None.
        product    - the new product, or None.
    Return: the booking as it now stands.
    """
    conn = _connect()
    row = conn.execute(
        """
        SELECT bookings.*, customers.deposit_required AS customer_deposit_required
        FROM bookings JOIN customers ON customers.id = bookings.customer_id
        WHERE bookings.id = ?
        """,
        (booking_id,),
    ).fetchone()

    old_party_size = row["party_size"]
    new_party_size = party_size if party_size is not None else old_party_size
    new_date = date if date is not None else row["service_date"]
    new_start_time = start_time if start_time is not None else row["start_at"].split(" ")[1]
    new_product = product if product is not None else row["product"]

    start_dt = _start_datetime(new_date, new_start_time)
    end_dt = start_dt + timedelta(minutes=POLICY["booking"]["duration_minutes"])

    conn.execute(
        "UPDATE bookings SET party_size = ?, service_date = ?, start_at = ?, end_at = ?, product = ?"
        " WHERE id = ?",
        (new_party_size, new_date, _to_db_time(start_dt), _to_db_time(end_dt), new_product, booking_id),
    )
    conn.commit()
    conn.close()

    # The deposit carries over and never decreases, even when the party
    # shrinks (policy.md #10) - the hold is untouched, on purpose.
    per_person = POLICY["deposit"]["per_person"]
    deposit_required = bool(row["customer_deposit_required"])
    old_deposit = old_party_size * per_person if deposit_required else 0
    new_deposit_full = new_party_size * per_person if deposit_required else 0
    deposit_eur = max(old_deposit, new_deposit_full)

    return Booking(
        id=booking_id,
        customer_id=row["customer_id"],
        service_date=new_date,
        start_at=_to_iso(_to_db_time(start_dt)),
        end_at=_to_iso(_to_db_time(end_dt)),
        party_size=new_party_size,
        product=new_product,
        area=row["area"],
        status=row["status"],
        hold_expires_at=_to_iso(row["hold_expires_at"]),
        deposit_eur=deposit_eur,
        payment_link=PAYMENT_LINK_PLACEHOLDER if row["status"] == "pending_deposit" else None,
        modify_allowed=_modify_allowed(new_date, now),
        refund_if_cancelled_now=_refund_if_cancelled_now(new_date, now, deposit_eur),
    )


def cancel_booking(booking_id: int, reason: str, now: str) -> Cancellation:
    """
    Cancel a booking and settle the deposit. policy.md #11.

    Params:
        booking_id - which booking.
        reason     - "guest_cancelled", "hold_expired" or "no_show".
        now        - ISO 8601. Decides refund or no refund.
    Return: the cancelled booking, the refund, and whether the deposit was
            kept.
    """
    conn = _connect()
    row = conn.execute(
        """
        SELECT bookings.*, customers.deposit_required AS customer_deposit_required
        FROM bookings JOIN customers ON customers.id = bookings.customer_id
        WHERE bookings.id = ?
        """,
        (booking_id,),
    ).fetchone()

    per_person = POLICY["deposit"]["per_person"]
    deposit_eur = row["party_size"] * per_person if row["customer_deposit_required"] else 0

    if reason == "hold_expired":
        # No deposit was ever paid, so there is nothing to keep or refund (policy.md #8).
        refund_eur = 0
        deposit_kept = False
    elif reason == "no_show":
        refund_eur = 0
        deposit_kept = deposit_eur > 0
    else:
        refund_eur = _refund_if_cancelled_now(row["service_date"], now, deposit_eur)
        deposit_kept = deposit_eur > 0 and refund_eur == 0

    conn.execute(
        "UPDATE bookings SET status = 'cancelled', cancel_reason = ?, hold_expires_at = NULL WHERE id = ?",
        (reason, booking_id),
    )
    conn.commit()
    conn.close()

    booking = Booking(
        id=row["id"],
        customer_id=row["customer_id"],
        service_date=row["service_date"],
        start_at=_to_iso(row["start_at"]),
        end_at=_to_iso(row["end_at"]),
        party_size=row["party_size"],
        product=row["product"],
        area=row["area"],
        status="cancelled",
        hold_expires_at=None,
        deposit_eur=deposit_eur,
        payment_link=None,
        modify_allowed=False,
        refund_if_cancelled_now=0,
    )
    return Cancellation(booking=booking, refund_eur=refund_eur, deposit_kept=deposit_kept)


def escalate(
    conversation_id: str,
    reason: str,
    guest_request_verbatim: str,
    collected_fields: dict[str, object],
    rule_that_triggered: str,
    agent_recommendation: str,
    already_told_guest: str,
) -> None:
    """
    Hand the conversation to a person. policy.md #13.

    The five case fields are required arguments on purpose: #13 says an
    escalation is never empty-handed, so the agent cannot send one that is.
    Sets the conversation to handed_to_human. The agent stops replying until a
    person hands it back.

    Params:
        conversation_id        - which conversation. TODO: str is a guess for
                                 now. We fix it when we decide where the
                                 conversation is stored.
        reason                 - one of escalate.reasons in policy.json.
        guest_request_verbatim - the guest's own words, not a summary.
        collected_fields       - every booking slot gathered so far.
        rule_that_triggered    - the policy rule that sent this to a person.
        agent_recommendation   - what the agent would have done.
        already_told_guest     - what the guest has been told so far.
    Return: None.
    """
    # There is no real phone line or inbox in this prototype (policy.md #13),
    # and no escalations table in the database - so the queue is a plain
    # file, one JSON case per line.
    case = {
        "conversation_id": conversation_id,
        "reason": reason,
        "guest_request_verbatim": guest_request_verbatim,
        "collected_fields": collected_fields,
        "rule_that_triggered": rule_that_triggered,
        "agent_recommendation": agent_recommendation,
        "already_told_guest": already_told_guest,
    }
    path = Path(ESCALATIONS_PATH)
    path.parent.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(case) + "\n")
