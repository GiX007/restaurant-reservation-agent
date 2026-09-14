### tests/test_schema.py. Run it with: python -m tests.test_schema

import sqlite3

from db.schema import create_bookings_table, create_customers_table

conn = sqlite3.connect(":memory:")  # in-memory DB for testing
conn.row_factory = sqlite3.Row  # rows come back with column names attached

create_customers_table(conn)
create_bookings_table(conn)

# Test that the customers table exists and has the expected columns.
# A new guest. We do not mention deposit_required at all.
conn.execute(
    "INSERT INTO customers (name, phone, email, created_at)"
    " VALUES ('Nikos Pappas', '+306940000001', 'nikos@example.com', '2026-07-10 12:00')"
)
print("\nTest customer insertion:")
# print(dict(conn.execute("SELECT name, deposit_required FROM customers").fetchone()))
print([dict(row) for row in conn.execute("SELECT * FROM customers").fetchall()])

# The same phone twice is refused.
try:
    conn.execute(
        "INSERT INTO customers (name, phone, created_at)"
        " VALUES ('Someone Else', '+306940000001', '2026-07-11 09:00')"
    )
except sqlite3.IntegrityError as e:
    print("rejected:", e)

# Test that the bookings table exists and has the expected columns.
# A normal booking: 6 people, bottle service, the show table on 14 July.
conn.execute(
    "INSERT INTO bookings (customer_id, service_date, start_at, end_at, party_size,"
    " product, area, status, created_at, hold_expires_at)"
    " VALUES (1, '2026-07-14', '2026-07-14 23:30', '2026-07-15 01:30', 6,"
    " 'bottle_service', 'main', 'pending_deposit', '2026-07-10 12:00', '2026-07-10 18:00')"
)
print("\nTest booking insertion:")
# print(conn.execute("SELECT start_at, end_at, status FROM bookings").fetchone())
# print(conn.execute("SELECT * FROM bookings").fetchall())
print([dict(row) for row in conn.execute("SELECT * FROM bookings").fetchall()])

# The DB refuses a status the policy does not have.
try:
    conn.execute(
        "INSERT INTO bookings (customer_id, service_date, start_at, end_at, party_size,"
        " product, area, status, created_at)"
        " VALUES (1, '2026-07-14', '2026-07-14 20:00', '2026-07-14 22:00', 2,"
        " 'dinner', 'main', 'paid', '2026-07-10 12:00')"
    )
except sqlite3.IntegrityError as e:
    print("rejected:", e)