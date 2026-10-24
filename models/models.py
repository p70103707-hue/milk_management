from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
from sqlalchemy.ext.hybrid import hybrid_property

db = SQLAlchemy()

# ─────────────────────────────────────────────────────────────
# 1. USERS TABLE (Central Authentication & Identity)
# ─────────────────────────────────────────────────────────────
class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    mobile_number = db.Column(db.String(15), unique=True, nullable=False, index=True)
    address = db.Column(db.String(255), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='customer') # 'admin', 'milkman', 'customer'
    status = db.Column(db.String(20), nullable=False, default='Active') # 'Active', 'Suspended', 'Inactive'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # One-to-one relationships
    customer_profile = db.relationship('Customer', backref='user', uselist=False, cascade='all, delete-orphan')
    milkman_profile = db.relationship('Milkman', backref='user', uselist=False, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)


# Backward-compatibility alias for Admin model
class Admin:
    """Wrapper query interface for backward compatibility with Admin.query"""
    @staticmethod
    def query_filter_by(**kwargs):
        kwargs['role'] = 'admin'
        return User.query.filter_by(**kwargs)

    class _AdminQueryProxy:
        def filter_by(self, **kwargs):
            kwargs['role'] = 'admin'
            return User.query.filter_by(**kwargs)
        def filter(self, *args):
            return User.query.filter(User.role == 'admin', *args)
        def get(self, ident):
            u = User.query.get(ident)
            return u if (u and u.role == 'admin') else None
        def get_or_404(self, ident):
            u = User.query.get_or_404(ident)
            if u.role != 'admin':
                from werkzeug.exceptions import NotFound
                raise NotFound()
            return u
        def all(self):
            return User.query.filter_by(role='admin').all()
        def count(self):
            return User.query.filter_by(role='admin').count()
        def first(self):
            return User.query.filter_by(role='admin').first()

    query = _AdminQueryProxy()

    def __init__(self, username, password=None):
        self.username = username
        self.user = User(name=username, mobile_number=username, role='admin', status='Active')
        if password:
            self.user.set_password(password)

    def set_password(self, password):
        self.user.set_password(password)

    def check_password(self, password):
        return self.user.check_password(password)


# ─────────────────────────────────────────────────────────────
# 2. SOCIETIES TABLE
# ─────────────────────────────────────────────────────────────
class Society(db.Model):
    __tablename__ = 'societies'

    id = db.Column(db.Integer, primary_key=True)
    society_name = db.Column(db.String(100), unique=True, nullable=False, index=True)
    address = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='Active') # 'Active', 'Inactive'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    customers = db.relationship('Customer', backref='society', lazy=True)

    # Backward compatibility properties
    @hybrid_property
    def name(self):
        return self.society_name

    @name.setter
    def name(self, value):
        self.society_name = value

    @hybrid_property
    def location(self):
        return self.address

    @location.setter
    def location(self, value):
        self.address = value


# ─────────────────────────────────────────────────────────────
# 3. CUSTOMERS TABLE
# ─────────────────────────────────────────────────────────────
class Customer(db.Model):
    __tablename__ = 'customers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), unique=True, nullable=False, index=True)
    society_id = db.Column(db.Integer, db.ForeignKey('societies.id'), nullable=False, index=True)
    house_number = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    default_quantity = db.Column(db.Float, nullable=False, default=1.0) # Standard quantity in Liters
    custom_rate = db.Column(db.Float, nullable=True) # Custom rate per liter (null means use global rate)
    status = db.Column(db.String(20), nullable=False, default='Active') # 'Active', 'Suspended', 'Vacation'
    start_date = db.Column(db.Date, default=datetime.utcnow().date)
    stop_date = db.Column(db.Date, nullable=True)
    milkman_id = db.Column(db.Integer, db.ForeignKey('milkmans.id', ondelete='SET NULL'), nullable=True, index=True)

    # Relationships
    allocated_milkman = db.relationship('Milkman', backref=db.backref('allocated_customers', lazy=True), foreign_keys=[milkman_id])
    deliveries = db.relationship('MilkDelivery', backref='customer', lazy=True, cascade='all, delete-orphan')
    bills = db.relationship('MilkBill', backref='customer', lazy=True, cascade='all, delete-orphan')
    payments = db.relationship('Payment', backref='customer', lazy=True, cascade='all, delete-orphan')
    sms_logs = db.relationship('SmsLog', backref='customer', lazy=True)

    # Convenience and backward-compatibility properties
    @property
    def milkman(self):
        return self.allocated_milkman

    @milkman.setter
    def milkman(self, val):
        self.allocated_milkman = val

    @property
    def name(self):
        return self.user.name if self.user else ""

    @name.setter
    def name(self, val):
        if self.user:
            self.user.name = val

    @property
    def mobile(self):
        return self.user.mobile_number if self.user else ""

    @mobile.setter
    def mobile(self, val):
        if self.user:
            self.user.mobile_number = val

    @property
    def phone(self):
        return self.mobile

    @property
    def mobile_number(self):
        return self.mobile

    @property
    def password_hash(self):
        return self.user.password_hash if self.user else None

    def set_password(self, password):
        if self.user:
            self.user.set_password(password)

    def check_password(self, password):
        if not self.user:
            return False
        return self.user.check_password(password)


# ─────────────────────────────────────────────────────────────
# 4. MILKMEN TABLE
# ─────────────────────────────────────────────────────────────
class Milkman(db.Model):
    __tablename__ = 'milkmans'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), unique=True, nullable=False, index=True)
    employee_code = db.Column(db.String(50), nullable=True)
    details = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='Active') # 'Active', 'Suspended'

    # Relationships
    deliveries = db.relationship('MilkDelivery', backref='milkman', lazy=True, cascade='all, delete-orphan')
    holiday_messages = db.relationship('HolidayMessage', backref='milkman', lazy=True, cascade='all, delete-orphan')

    # Convenience and backward-compatibility properties
    @property
    def allocated_customer_count(self):
        return len(self.allocated_customers) if self.allocated_customers else 0

    @property
    def name(self):
        return self.user.name if self.user else ""

    @name.setter
    def name(self, val):
        if self.user:
            self.user.name = val

    @property
    def mobile(self):
        return self.user.mobile_number if self.user else ""

    @mobile.setter
    def mobile(self, val):
        if self.user:
            self.user.mobile_number = val

    @property
    def phone(self):
        return self.mobile

    @property
    def mobile_number(self):
        return self.mobile

    @property
    def password_hash(self):
        return self.user.password_hash if self.user else None

    def set_password(self, password):
        if self.user:
            self.user.set_password(password)

    def check_password(self, password):
        if not self.user:
            return False
        return self.user.check_password(password)


# ─────────────────────────────────────────────────────────────
# 5. MILK DELIVERIES TABLE
# ─────────────────────────────────────────────────────────────
class MilkDelivery(db.Model):
    __tablename__ = 'milk_deliveries'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id', ondelete='CASCADE'), nullable=False, index=True)
    milkman_id = db.Column(db.Integer, db.ForeignKey('milkmans.id', ondelete='CASCADE'), nullable=False, index=True)
    delivery_date = db.Column(db.Date, nullable=False, default=datetime.utcnow().date, index=True)
    quantity = db.Column(db.Float, nullable=False) # In Liters (e.g. 1.0, 1.5)
    quantity_ml = db.Column(db.Integer, nullable=False, default=0) # In Milliliters (e.g. 1000, 1500)
    milk_rate = db.Column(db.Float, nullable=False) # Rate per liter at time of delivery
    amount = db.Column(db.Float, nullable=False) # Total cost for this delivery
    delivery_status = db.Column(db.String(20), nullable=False, default='Delivered') # 'Delivered', 'Skipped'

    # Backward compatibility properties
    @hybrid_property
    def date(self):
        return self.delivery_date

    @date.setter
    def date(self, val):
        self.delivery_date = val

    @hybrid_property
    def rate(self):
        return self.milk_rate

    @rate.setter
    def rate(self, val):
        self.milk_rate = val

    @hybrid_property
    def total_amount(self):
        return self.amount

    @total_amount.setter
    def total_amount(self, val):
        self.amount = val

    @hybrid_property
    def status(self):
        return self.delivery_status

    @status.setter
    def status(self, val):
        self.delivery_status = val


# ─────────────────────────────────────────────────────────────
# 6. MILK BILLS TABLE (Monthly Bill Aggregate)
# ─────────────────────────────────────────────────────────────
class MilkBill(db.Model):
    __tablename__ = 'milk_bills'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id', ondelete='CASCADE'), nullable=False, index=True)
    month = db.Column(db.Integer, nullable=False) # 1-12
    year = db.Column(db.Integer, nullable=False) # e.g. 2026
    billing_month_str = db.Column(db.String(7), nullable=False, index=True) # e.g. '2026-09'
    total_quantity = db.Column(db.Float, nullable=False, default=0.0) # In Liters
    total_amount = db.Column(db.Float, nullable=False, default=0.0) # Total amount due
    amount_paid = db.Column(db.Float, nullable=False, default=0.0) # Amount paid so far
    remaining_amount = db.Column(db.Float, nullable=False, default=0.0) # Remaining unpaid amount
    bill_status = db.Column(db.String(20), nullable=False, default='Pending') # 'Pending', 'Paid', 'Partial'
    generated_date = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('customer_id', 'billing_month_str', name='_customer_monthly_bill_uc'),
    )

    # Relationships
    payments = db.relationship('Payment', backref='milk_bill', lazy=True)

    @property
    def billing_month(self):
        return self.billing_month_str


# ─────────────────────────────────────────────────────────────
# 7. PAYMENTS TABLE
# ─────────────────────────────────────────────────────────────
class Payment(db.Model):
    __tablename__ = 'payments'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id', ondelete='CASCADE'), nullable=False, index=True)
    bill_id = db.Column(db.Integer, db.ForeignKey('milk_bills.id', ondelete='SET NULL'), nullable=True, index=True)
    billing_month = db.Column(db.String(7), nullable=False, index=True) # Format: YYYY-MM
    payment_method = db.Column(db.String(50), nullable=True) # 'Cash', 'COD', 'UPI QR', 'Razorpay', 'PhonePe', 'Google Pay'
    amount = db.Column(db.Float, nullable=False)
    payment_date = db.Column(db.DateTime, nullable=True)
    transaction_id = db.Column(db.String(100), nullable=True)
    payment_status = db.Column(db.String(20), nullable=False, default='Pending') # 'Pending', 'Paid', 'Failed'
    notes = db.Column(db.Text, nullable=True)

    # Backward compatibility properties
    @hybrid_property
    def status(self):
        return self.payment_status

    @status.setter
    def status(self, val):
        self.payment_status = val


# ─────────────────────────────────────────────────────────────
# 8. HOLIDAY MESSAGES TABLE (Milkman Holiday SMS Announcements)
# ─────────────────────────────────────────────────────────────
class HolidayMessage(db.Model):
    __tablename__ = 'holiday_messages'

    id = db.Column(db.Integer, primary_key=True)
    milkman_id = db.Column(db.Integer, db.ForeignKey('milkmans.id', ondelete='CASCADE'), nullable=False, index=True)
    holiday_date = db.Column(db.Date, nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    sender_number = db.Column(db.String(15), nullable=False, default='6367335889')
    total_sent = db.Column(db.Integer, nullable=False, default=0)
    total_failed = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default='Sent') # 'Sent', 'Partial', 'Failed'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    logs = db.relationship('SmsLog', backref='holiday_message', lazy=True, cascade='all, delete-orphan')


# ─────────────────────────────────────────────────────────────
# 9. SMS LOGS TABLE (Message History & Audit Trail)
# ─────────────────────────────────────────────────────────────
class SmsLog(db.Model):
    __tablename__ = 'sms_logs'

    id = db.Column(db.Integer, primary_key=True)
    holiday_message_id = db.Column(db.Integer, db.ForeignKey('holiday_messages.id', ondelete='SET NULL'), nullable=True, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id', ondelete='SET NULL'), nullable=True, index=True)
    recipient_mobile = db.Column(db.String(15), nullable=False, index=True)
    recipient_name = db.Column(db.String(100), nullable=False)
    sender_number = db.Column(db.String(15), nullable=False, default='6367335889')
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), nullable=False, default='Sent') # 'Sent', 'Failed', 'Limit Exceeded'
    error_message = db.Column(db.String(255), nullable=True)
    sent_date = db.Column(db.Date, nullable=False, index=True, default=datetime.utcnow().date)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────
# 10. MILK RATES TABLE
# ─────────────────────────────────────────────────────────────
class MilkRate(db.Model):
    __tablename__ = 'milk_rates'

    id = db.Column(db.Integer, primary_key=True)
    rate_per_liter = db.Column(db.Float, nullable=False)
    effective_from = db.Column(db.DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────
# 11. ADMIN SETTINGS TABLE (Dynamic Configuration without Hardcoding)
# ─────────────────────────────────────────────────────────────
class AdminSetting(db.Model):
    __tablename__ = 'admin_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────────────────────────────────────────
# 12. NOTIFICATIONS TABLE (General System & WhatsApp Logs)
# ─────────────────────────────────────────────────────────────
class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    recipient_mobile = db.Column(db.String(15), nullable=False)
    recipient_name = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(50), default='WhatsApp') # WhatsApp, SMS, System
    status = db.Column(db.String(20), default='Sent') # Sent, Failed
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
