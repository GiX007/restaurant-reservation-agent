### db/seed.py. Run it with: python -m db.seed

import os
import sqlite3
from datetime import datetime, timedelta

from db.schema import create_bookings_table, create_customers_table

DB_PATH = "db/venue.db"
BOOKING_MINUTES = 120
MADE_AT = "2026-07-01 12:00"          # every fake row was "made" at the same moment


def end_of(start_at: str) -> str:
    """
    Add the booking length to a start time.

    Params: start_at - 'YYYY-MM-DD HH:MM'.
    Return: the end time in the same format, rolling into the next day if needed.
    """
    start = datetime.strptime(start_at, "%Y-%m-%d %H:%M")
    return (start + timedelta(minutes=BOOKING_MINUTES)).strftime("%Y-%m-%d %H:%M")


# (name, phone, email, deposit_required)
CUSTOMERS = [
    ("Nikos Pappas",    "+306940000001", "nikos@example.com",   1),
    ("Elena Marino",    "+306940000002", "elena@example.com",   1),
    ("Jean Dupont",     "+306940000003", "jean@example.com",    1),
    ("Sofia Ricci",     "+306940000004", "sofia@example.com",   1),
    ("Yiannis Alexiou", "+306940000005", "yiannis@example.com", 0),   # the regular: no deposit
]

# (customer_id, service_date, start_at, party_size, product, status, hold_expires_at, cancel_reason)
BOOKINGS = [
    # 12 July - the busy night. Tables run out at 21:00.
    (1, "2026-07-12", "2026-07-12 19:30", 4, "dinner",         "confirmed",       None,               None),
    (2, "2026-07-12", "2026-07-12 20:00", 2, "dinner",         "confirmed",       None,               None),
    (3, "2026-07-12", "2026-07-12 21:00", 4, "dinner",         "pending_deposit", "2026-07-12 21:00", None),
    (4, "2026-07-12", "2026-07-12 20:00", 4, "dinner",         "cancelled",       None,               "guest_cancelled"),
    (5, "2026-07-12", "2026-07-12 23:30", 6, "bottle_service", "confirmed",       None,               None),
    (1, "2026-07-12", "2026-07-13 00:00", 5, "bottle_service", "confirmed",       None,               None),

    # 13 July - two parties of 4. A third one only fits inside the chair buffer.
    (2, "2026-07-13", "2026-07-13 20:00", 4, "dinner", "confirmed", None, None),
    (3, "2026-07-13", "2026-07-13 20:30", 4, "dinner", "confirmed", None, None),

    # 14 July - nobody paid within 6 hours.
    (5, "2026-07-14", "2026-07-14 21:30", 4, "dinner", "cancelled", "2026-07-01 18:00", "hold_expired"),

    # 10 May - low season, quiet.
    (4, "2026-05-10", "2026-05-10 21:00", 2, "dinner", "confirmed", None, None),
]


def seed(db_path: str = DB_PATH) -> None:
    """
    Build a fresh venue.db and fill it with the fake customers and bookings.

    Params: db_path - where to write the database file.
    Return: None.
    """
    # Always start from nothing: CREATE TABLE IF NOT EXISTS would silently skip
    # a changed schema against an old file.
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    create_customers_table(conn)
    create_bookings_table(conn)

    conn.executemany(
        "INSERT INTO customers (name, phone, email, deposit_required, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        [(name, phone, email, flag, MADE_AT) for name, phone, email, flag in CUSTOMERS],
    )

    conn.executemany(
        "INSERT INTO bookings (customer_id, service_date, start_at, end_at, party_size,"
        " product, area, status, created_at, hold_expires_at, cancel_reason)"
        " VALUES (?, ?, ?, ?, ?, ?, 'main', ?, ?, ?, ?)",
        [
            (cid, date, start, end_of(start), size, product, status, MADE_AT, hold, reason)
            for cid, date, start, size, product, status, hold, reason in BOOKINGS
        ],
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    seed()
    print("seeded", DB_PATH)