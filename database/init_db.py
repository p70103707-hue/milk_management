import sys
import os
from datetime import datetime, date, timedelta
import random

# Adjust path to import models and config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask import Flask
from config import Config
from models.models import (
    db, User, Society, Milkman, Customer, MilkRate, MilkDelivery, 
    MilkBill, Payment, HolidayMessage, SmsLog, AdminSetting, Notification
)

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    return app

def init_database():
    app = create_app()
    with app.app_context():
        db_dir = os.path.dirname(Config.SQLALCHEMY_DATABASE_URI.replace('sqlite:///', ''))
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)

        print("Dropping existing tables...")
        db.drop_all()
        
        print("Creating normalized tables (12 relational tables)...")
        db.create_all()

        print("1. Inserting Admin Settings & Milk Rate...")
        rate = MilkRate(rate_per_liter=70.0, effective_from=datetime.now() - timedelta(days=60))
        db.session.add(rate)

        # Configured sender number setting (6367335889)
        sms_setting = AdminSetting(
            key='SMS_SENDER_NUMBER',
            value='6367335889',
            description='Configured Sender / Mobile Number for SMS Service'
        )
        db.session.add(sms_setting)

        daily_limit_setting = AdminSetting(
            key='DAILY_SMS_LIMIT',
            value='50',
            description='Maximum allowed SMS messages per day'
        )
        db.session.add(daily_limit_setting)

        print("2. Inserting Admin User...")
        admin_user = User(
            name="Admin User",
            mobile_number="admin",
            role="admin",
            status="Active"
        )
        admin_user.set_password("admin123")
        db.session.add(admin_user)

        print("3. Inserting Societies...")
        societies_data = [
            Society(society_name="Shreeji Society", address="Near Ring Road, Rajkot", status="Active"),
            Society(society_name="Patel Nagar", address="Kalawad Road, Rajkot", status="Active"),
            Society(society_name="Green City", address="150 Feet Ring Road, Rajkot", status="Active"),
            Society(society_name="Krishna Park", address="University Road, Rajkot", status="Active"),
            Society(society_name="Royal Residency", address="Mavdi, Rajkot", status="Active")
        ]
        for s in societies_data:
            db.session.add(s)
        db.session.commit()

        print("4. Inserting Milkmen Users & Profiles...")
        milkmans_data = [
            {"name": "Ramesh Bhai", "mobile": "9988776655", "code": "MM-01", "details": "Morning route Shreeji & Patel Nagar"},
            {"name": "Suresh Bhai", "mobile": "9988776644", "code": "MM-02", "details": "Morning route Green City & Krishna Park"}
        ]
        milkmans = []
        for md in milkmans_data:
            u = User(name=md["name"], mobile_number=md["mobile"], role="milkman", status="Active")
            u.set_password("milkman123")
            db.session.add(u)
            db.session.flush()

            m = Milkman(user_id=u.id, employee_code=md["code"], details=md["details"], status="Active")
            db.session.add(m)
            milkmans.append(m)
        db.session.commit()

        print("5. Inserting Customer Users & Profiles...")
        customers_raw = [
            # Shreeji Society
            {"name": "Aarav Patel", "mobile": "9876543210", "house": "A-201", "address": "Block A-201, Shreeji Society", "society_id": societies_data[0].id, "default_qty": 1.0, "custom_rate": None},
            {"name": "Diya Shah", "mobile": "9876543211", "house": "Plot 12", "address": "Plot 12, Shreeji Society", "society_id": societies_data[0].id, "default_qty": 0.5, "custom_rate": 68.0},
            # Patel Nagar
            {"name": "Kabir Mehta", "mobile": "9876543212", "house": "C-104", "address": "C-104, Patel Nagar", "society_id": societies_data[1].id, "default_qty": 1.5, "custom_rate": None},
            {"name": "Ishaan Joshi", "mobile": "9876543213", "house": "B-303", "address": "B-303, Patel Nagar", "society_id": societies_data[1].id, "default_qty": 2.0, "custom_rate": None},
            # Green City
            {"name": "Meera Desai", "mobile": "9876543214", "house": "Villa 4", "address": "Green City, Villa 4", "society_id": societies_data[2].id, "default_qty": 1.0, "custom_rate": None},
            {"name": "Vihaan Sharma", "mobile": "9876543215", "house": "E-502", "address": "Green City, Block E-502", "society_id": societies_data[2].id, "default_qty": 2.5, "custom_rate": None},
            # Krishna Park
            {"name": "Ananya Vyas", "mobile": "9876543216", "house": "Rowhouse 7", "address": "Krishna Park, Rowhouse 7", "society_id": societies_data[3].id, "default_qty": 1.0, "custom_rate": None},
            # Royal Residency
            {"name": "Vivaan Trivedi", "mobile": "9876543217", "house": "Penthouse A", "address": "Royal Residency, Penthouse A", "society_id": societies_data[4].id, "default_qty": 3.0, "custom_rate": 72.0}
        ]

        customers = []
        for idx, cr in enumerate(customers_raw):
            u = User(
                name=cr["name"],
                mobile_number=cr["mobile"],
                address=cr["address"],
                role="customer",
                status="Active"
            )
            u.set_password("customer123")
            db.session.add(u)
            db.session.flush()

            # Allocate customer: half to milkmans[0], half to milkmans[1]
            allocated_milkman = milkmans[0] if (idx % 2 == 0) else milkmans[1]

            c = Customer(
                user_id=u.id,
                society_id=cr["society_id"],
                house_number=cr["house"],
                address=cr["address"],
                default_quantity=cr["default_qty"],
                custom_rate=cr["custom_rate"],
                status="Active",
                start_date=date(2026, 5, 1),
                milkman_id=allocated_milkman.id
            )
            db.session.add(c)
            customers.append(c)
        db.session.commit()

        print("6. Generating Deliveries, Bills, and Payments for June & July 2026...")
        start_june = date(2026, 6, 1)
        end_june = date(2026, 6, 30)
        start_july = date(2026, 7, 1)
        today_date = date(2026, 7, 14)
        global_rate = 70.0

        # June 2026 Deliveries & Bills
        for cust in customers:
            milkman = milkmans[0] if cust.id % 2 == 0 else milkmans[1]
            rate_used = cust.custom_rate if cust.custom_rate is not None else global_rate
            
            curr = start_june
            total_june_liters = 0.0
            total_june_amt = 0.0
            while curr <= end_june:
                rand = random.random()
                if rand < 0.05:
                    qty = 0.0
                    status = "Skipped"
                elif rand < 0.15:
                    qty = cust.default_quantity + random.choice([-0.5, 0.5, 1.0])
                    if qty < 0.5:
                        qty = 0.5
                    status = "Delivered"
                else:
                    qty = cust.default_quantity
                    status = "Delivered"

                amt = round(qty * rate_used, 2) if qty > 0 else 0.0
                delivery = MilkDelivery(
                    customer_id=cust.id,
                    milkman_id=milkman.id,
                    delivery_date=curr,
                    quantity=qty,
                    quantity_ml=int(round(qty * 1000)),
                    milk_rate=rate_used,
                    amount=amt,
                    delivery_status=status
                )
                db.session.add(delivery)
                if status == "Delivered":
                    total_june_liters += qty
                    total_june_amt += amt
                curr += timedelta(days=1)

            total_june_liters = round(total_june_liters, 2)
            total_june_amt = round(total_june_amt, 2)
            is_paid = cust.id not in [customers[1].id, customers[4].id]

            # Create June MilkBill
            bill_june = MilkBill(
                customer_id=cust.id,
                month=6,
                year=2026,
                billing_month_str="2026-06",
                total_quantity=total_june_liters,
                total_amount=total_june_amt,
                amount_paid=total_june_amt if is_paid else 0.0,
                remaining_amount=0.0 if is_paid else total_june_amt,
                bill_status="Paid" if is_paid else "Pending",
                generated_date=datetime(2026, 7, 1, 0, 0)
            )
            db.session.add(bill_june)
            db.session.flush()

            # Create June Payment Record
            pay_june = Payment(
                customer_id=cust.id,
                bill_id=bill_june.id,
                billing_month="2026-06",
                payment_method=random.choice(["UPI QR", "Cash", "Google Pay", "PhonePe"]) if is_paid else "Pending",
                amount=total_june_amt,
                payment_status="Paid" if is_paid else "Pending",
                payment_date=datetime(2026, 7, 3, random.randint(9, 18), random.randint(0, 59)) if is_paid else None,
                transaction_id=f"TXN202606{cust.id:04d}{random.randint(100, 999)}" if is_paid else None
            )
            db.session.add(pay_june)

        # July 2026 Deliveries & In-Progress Bill
        for cust in customers:
            milkman = milkmans[0] if cust.id % 2 == 0 else milkmans[1]
            rate_used = cust.custom_rate if cust.custom_rate is not None else global_rate
            
            curr = start_july
            total_july_liters = 0.0
            total_july_amt = 0.0
            while curr <= today_date:
                rand = random.random()
                if rand < 0.05:
                    qty = 0.0
                    status = "Skipped"
                elif rand < 0.15:
                    qty = cust.default_quantity + random.choice([-0.5, 0.5])
                    if qty < 0.5:
                        qty = 0.5
                    status = "Delivered"
                else:
                    qty = cust.default_quantity
                    status = "Delivered"

                amt = round(qty * rate_used, 2) if qty > 0 else 0.0
                delivery = MilkDelivery(
                    customer_id=cust.id,
                    milkman_id=milkman.id,
                    delivery_date=curr,
                    quantity=qty,
                    quantity_ml=int(round(qty * 1000)),
                    milk_rate=rate_used,
                    amount=amt,
                    delivery_status=status
                )
                db.session.add(delivery)
                if status == "Delivered":
                    total_july_liters += qty
                    total_july_amt += amt
                curr += timedelta(days=1)

            total_july_liters = round(total_july_liters, 2)
            total_july_amt = round(total_july_amt, 2)

            # July MilkBill
            bill_july = MilkBill(
                customer_id=cust.id,
                month=7,
                year=2026,
                billing_month_str="2026-07",
                total_quantity=total_july_liters,
                total_amount=total_july_amt,
                amount_paid=0.0,
                remaining_amount=total_july_amt,
                bill_status="Pending",
                generated_date=datetime.now()
            )
            db.session.add(bill_july)
            db.session.flush()

            # July Payment
            pay_july = Payment(
                customer_id=cust.id,
                bill_id=bill_july.id,
                billing_month="2026-07",
                payment_method="Pending",
                amount=total_july_amt,
                payment_status="Pending"
            )
            db.session.add(pay_july)

        print("7. Inserting Sample Holiday Message & SMS Logs (Sender: 6367335889)...")
        holiday_msg = HolidayMessage(
            milkman_id=milkmans[0].id,
            holiday_date=date(2026, 7, 20),
            message="નમસ્તે ગ્રાહક મિત્રો, જણાવવાનું કે 20-Jul-2026 ના રોજ રજા હોવાથી દૂધ વિતરણ બંધ રહેશે. નિયમિત વિતરણ બીજા દિવસથી શરૂ થશે. - ડેરીફ્લો (સંપર્ક: 6367335889)",
            sender_number="6367335889",
            total_sent=8,
            total_failed=0,
            status="Sent",
            created_at=datetime.now() - timedelta(hours=2)
        )
        db.session.add(holiday_msg)
        db.session.flush()

        # Add SMS logs for all customers
        for c in customers:
            sms_log = SmsLog(
                holiday_message_id=holiday_msg.id,
                customer_id=c.id,
                recipient_mobile=c.mobile,
                recipient_name=c.name,
                sender_number="6367335889",
                message=holiday_msg.message,
                status="Sent",
                sent_date=date.today(),
                timestamp=datetime.now() - timedelta(hours=2)
            )
            db.session.add(sms_log)

        print("8. Inserting System Notifications...")
        notifs = [
            Notification(recipient_mobile="9876543210", recipient_name="Aarav Patel", message="નમસ્તે Aarav Patel, જૂન મહિનાનું દૂધ બિલ ₹2100/- ચૂકવેલ છે. આભાર!", type="WhatsApp", status="Sent"),
            Notification(recipient_mobile="9876543211", recipient_name="Diya Shah", message="નમસ્તે Diya Shah, જૂન મહિનાનું દૂધ બિલ ₹1020/- બાકી છે. કૃપા કરીને ચૂકવણી કરો.", type="WhatsApp", status="Sent")
        ]
        for n in notifs:
            db.session.add(n)

        db.session.commit()
        print("[SUCCESS] Normalized Database with 12 Tables successfully created and seeded!")

if __name__ == "__main__":
    init_database()
