import sqlite3
import shutil
import os

db_path = os.path.join(os.path.dirname(__file__), 'milk_delivery.db')
backup_path = os.path.join(os.path.dirname(__file__), 'milk_delivery_backup.db')

print(f"Creating backup from {db_path} to {backup_path}...")
shutil.copy2(db_path, backup_path)
print("Backup completed successfully.")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check current count
cursor.execute("SELECT COUNT(*) FROM milk_deliveries")
initial_count = cursor.fetchone()[0]
print(f"Initial delivery records count: {initial_count}")

# Check index/constraint list
cursor.execute("PRAGMA index_list(milk_deliveries)")
print("Current indexes:", cursor.fetchall())

print("Executing migration to remove UNIQUE(customer_id, delivery_date) constraint...")

# SQLite table migration pattern
cursor.executescript("""
BEGIN TRANSACTION;

-- 1. Create table without the unique constraint
CREATE TABLE milk_deliveries_new (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    milkman_id INTEGER NOT NULL REFERENCES milkmans(id) ON DELETE CASCADE,
    delivery_date DATE NOT NULL,
    quantity FLOAT NOT NULL,
    quantity_ml INTEGER NOT NULL DEFAULT 0,
    milk_rate FLOAT NOT NULL,
    amount FLOAT NOT NULL,
    delivery_status VARCHAR(20) NOT NULL DEFAULT 'Delivered'
);

-- 2. Copy all data
INSERT INTO milk_deliveries_new (id, customer_id, milkman_id, delivery_date, quantity, quantity_ml, milk_rate, amount, delivery_status)
SELECT id, customer_id, milkman_id, delivery_date, quantity, quantity_ml, milk_rate, amount, delivery_status
FROM milk_deliveries;

-- 3. Drop old table
DROP TABLE milk_deliveries;

-- 4. Rename new table
ALTER TABLE milk_deliveries_new RENAME TO milk_deliveries;

-- 5. Recreate indexes
CREATE INDEX ix_milk_deliveries_customer_id ON milk_deliveries(customer_id);
CREATE INDEX ix_milk_deliveries_milkman_id ON milk_deliveries(milkman_id);
CREATE INDEX ix_milk_deliveries_delivery_date ON milk_deliveries(delivery_date);

COMMIT;
""")

cursor.execute("SELECT COUNT(*) FROM milk_deliveries")
final_count = cursor.fetchone()[0]
print(f"Final delivery records count: {final_count}")

cursor.execute("PRAGMA index_list(milk_deliveries)")
print("Updated indexes:", cursor.fetchall())

if initial_count == final_count:
    print("MIGRATION SUCCESS: All records preserved and unique constraint removed!")
else:
    raise Exception(f"Count mismatch! Initial: {initial_count}, Final: {final_count}")

conn.close()
