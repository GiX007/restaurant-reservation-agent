### db/schema.py

import sqlite3

# The bookings table. One row = one party, one night, one time window.
BOOKINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id              INTEGER PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers(id),

    service_date    TEXT NOT NULL,          -- 'YYYY-MM-DD', the night it belongs to
    start_at        TEXT NOT NULL,          -- 'YYYY-MM-DD HH:MM', real clock time
    end_at          TEXT NOT NULL,          -- start + 120 min, may be after midnight

    party_size      INTEGER NOT NULL,
    product         TEXT NOT NULL CHECK (product IN ('dinner', 'bottle_service', 'both')),
    area            TEXT NOT NULL CHECK (area IN ('main', 'bar')),
    status          TEXT NOT NULL CHECK (status IN ('pending_deposit', 'confirmed', 'cancelled')),

    created_at      TEXT NOT NULL,          -- when the agent made it, needed for the hold
    hold_expires_at TEXT,                   -- NULL = never expires (deposit_required = false)
    cancel_reason   TEXT                    -- NULL unless status = 'cancelled'
);
"""


def create_bookings_table(conn: sqlite3.Connection) -> None:
    """
    Create the bookings table if it does not exist.

    Params: conn - an open SQLite connection.
    Return: None.
    """
    conn.execute(BOOKINGS_SCHEMA)
    conn.commit()


# The customers table. One row = one guest the venue knows.
CUSTOMERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    phone            TEXT NOT NULL UNIQUE,   -- how the agent recognises a returning guest
    email            TEXT,

    -- §8: only a person sets this. The agent reads it and never writes it.
    deposit_required INTEGER NOT NULL DEFAULT 1 CHECK (deposit_required IN (0, 1)),

    created_at       TEXT NOT NULL
);
"""


def create_customers_table(conn: sqlite3.Connection) -> None:
    """
    Create the customers table if it does not exist.

    Params: conn - an open SQLite connection.
    Return: None.
    """
    conn.execute(CUSTOMERS_SCHEMA)
    conn.commit()
