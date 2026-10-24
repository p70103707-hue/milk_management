import sqlite3
import os

db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'milk_delivery.db'))
print(f"Connecting to {db_path}...")

conn = sqlite3.connect(db_path)
c = conn.cursor()

# Check columns of customers table
c.execute("PRAGMA table_info(customers)")
columns = [col[1] for col in c.fetchall()]
print(f"Existing columns in customers: {columns}")

if 'milkman_id' not in columns:
    print("Adding milkman_id column to customers table...")
    c.execute("ALTER TABLE customers ADD COLUMN milkman_id INTEGER REFERENCES milkmans(id) ON DELETE SET NULL")
    c.execute("CREATE INDEX IF NOT EXISTS ix_customers_milkman_id ON customers(milkman_id)")
    conn.commit()
    print("Column milkman_id and index successfully added!")
else:
    print("milkman_id column already exists.")

conn.close()
