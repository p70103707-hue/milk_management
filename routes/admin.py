from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from models.models import (
    db, User, Customer, Milkman, Society, MilkDelivery, 
    Payment, MilkBill, MilkRate, Notification, HolidayMessage, SmsLog, AdminSetting
)
from services.sms_service import get_daily_sms_stats, get_sender_number
from datetime import datetime, date, timedelta
import os
import shutil

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Login check decorator helper
def admin_required(f):
    def decorator(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'admin':
            flash('Admin login required!', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    decorator.__name__ = f.__name__
    return decorator


@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    today = date.today()
    
    # Calculate statistics
    total_customers = Customer.query.count()
    total_milkmen = Milkman.query.count()
    total_societies = Society.query.count()
    
    # Today's deliveries
    today_deliveries = MilkDelivery.query.filter_by(delivery_date=today, delivery_status='Delivered').all()
    today_milk_qty = round(sum(d.quantity for d in today_deliveries), 2)
    today_income = round(sum(d.amount for d in today_deliveries), 2)
    
    # Payments summary
    pending_payments = Payment.query.filter_by(payment_status='Pending').all()
    total_pending_amount = round(sum(p.amount for p in pending_payments), 2)
    
    paid_payments = Payment.query.filter_by(payment_status='Paid').all()
    total_paid_amount = round(sum(p.amount for p in paid_payments), 2)
    
    # Active milk rate
    active_rate_record = MilkRate.query.order_by(MilkRate.effective_from.desc()).first()
    active_rate = active_rate_record.rate_per_liter if active_rate_record else 70.0
    
    # SMS Stats
    sms_stats = get_daily_sms_stats(today)
    
    # Recent notifications
    recent_notifs = Notification.query.order_by(Notification.timestamp.desc()).limit(5).all()

    return render_template('admin/dashboard.html',
                           total_customers=total_customers,
                           total_milkmen=total_milkmen,
                           total_societies=total_societies,
                           today_milk_qty=today_milk_qty,
                           today_income=today_income,
                           total_pending_amount=total_pending_amount,
                           total_paid_amount=total_paid_amount,
                           active_rate=active_rate,
                           sms_stats=sms_stats,
                           recent_notifs=recent_notifs)


# ─────────────────────────────────────────────
# SOCIETY MANAGEMENT
# ─────────────────────────────────────────────
@admin_bp.route('/societies', methods=['GET', 'POST'])
@admin_required
def societies():
    if request.method == 'POST':
        name = request.form.get('name')
        location = request.form.get('location')
        if not name:
            flash('Society name is required!', 'danger')
        else:
            existing = Society.query.filter_by(society_name=name).first()
            if existing:
                flash('Society already exists!', 'warning')
            else:
                society = Society(society_name=name, address=location, status='Active')
                db.session.add(society)
                db.session.commit()
                flash('Society added successfully!', 'success')
        return redirect(url_for('admin.societies'))
        
    all_societies = Society.query.all()
    return render_template('admin/societies.html', societies=all_societies)


@admin_bp.route('/societies/edit/<int:id>', methods=['POST'])
@admin_required
def edit_society(id):
    society = Society.query.get_or_404(id)
    society.society_name = request.form.get('name')
    society.address = request.form.get('location')
    db.session.commit()
    flash('Society updated successfully!', 'success')
    return redirect(url_for('admin.societies'))


@admin_bp.route('/societies/delete/<int:id>', methods=['POST'])
@admin_required
def delete_society(id):
    society = Society.query.get_or_404(id)
    if len(society.customers) > 0:
        flash('Cannot delete society. It has registered customers!', 'danger')
    else:
        db.session.delete(society)
        db.session.commit()
        flash('Society deleted successfully!', 'success')
    return redirect(url_for('admin.societies'))


# ─────────────────────────────────────────────
# MILKMAN MANAGEMENT
# ─────────────────────────────────────────────
@admin_bp.route('/milkmen', methods=['GET', 'POST'])
@admin_required
def milkmen():
    if request.method == 'POST':
        name = request.form.get('name')
        mobile = request.form.get('mobile')
        password = request.form.get('password')
        
        if not name or not mobile or not password:
            flash('All fields are required!', 'danger')
        else:
            existing_user = User.query.filter_by(mobile_number=mobile).first()
            if existing_user:
                flash('User with this mobile number already exists!', 'warning')
            else:
                user = User(name=name, mobile_number=mobile, role='milkman', status='Active')
                user.set_password(password)
                db.session.add(user)
                db.session.flush()

                milkman = Milkman(user_id=user.id, status='Active')
                db.session.add(milkman)
                db.session.commit()
                flash('Milkman added successfully!', 'success')
        return redirect(url_for('admin.milkmen'))
        
    all_milkmen = Milkman.query.all()
    all_customers = Customer.query.all()
    return render_template('admin/milkmen.html', milkmen=all_milkmen, customers=all_customers)


@admin_bp.route('/milkmen/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_milkman(id):
    milkman = Milkman.query.get_or_404(id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        mobile = request.form.get('mobile', '').strip()
        status = request.form.get('status', 'Active').strip()
        password = request.form.get('password', '').strip()

        if not name or not mobile:
            flash('Both Milkman Name and Mobile Number are required!', 'danger')
            return redirect(url_for('admin.milkmen'))

        # Check if mobile number is used by another user
        existing_user = User.query.filter(User.mobile_number == mobile, User.id != milkman.user_id).first()
        if existing_user:
            flash(f'Mobile number {mobile} is already registered to another user ({existing_user.name})!', 'warning')
            return redirect(url_for('admin.milkmen'))

        if milkman.user:
            milkman.user.name = name
            milkman.user.mobile_number = mobile
            milkman.user.status = status
            if password:
                milkman.user.set_password(password)
        milkman.status = status
        
        db.session.commit()
        flash(f'Milkman "{name}" details updated successfully!', 'success')
        return redirect(url_for('admin.milkmen'))

    return jsonify({
        'id': milkman.id,
        'name': milkman.name,
        'mobile': milkman.mobile,
        'status': milkman.status
    })


@admin_bp.route('/milkmen/delete/<int:id>', methods=['GET', 'POST'])
@admin_required
def delete_milkman(id):
    milkman = Milkman.query.get_or_404(id)
    name = milkman.name
    user = milkman.user
    try:
        db.session.delete(milkman)
        if user:
            db.session.delete(user)
        db.session.commit()
        flash(f'Milkman "{name}" deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting milkman: {str(e)}', 'danger')
    return redirect(url_for('admin.milkmen'))


# ─────────────────────────────────────────────
# CUSTOMER MANAGEMENT
# ─────────────────────────────────────────────
@admin_bp.route('/customers', methods=['GET', 'POST'])
@admin_required
def customers():
    if request.method == 'POST':
        name = request.form.get('name')
        mobile = request.form.get('mobile')
        password = request.form.get('password', 'customer123')
        address = request.form.get('address')
        society_id = request.form.get('society_id')
        default_qty = float(request.form.get('default_qty', 1.0))
        custom_rate = request.form.get('custom_rate')
        custom_rate = float(custom_rate) if custom_rate else None
        redirect_to = request.form.get('redirect_to', 'customers')
        
        milkman_id = request.form.get('milkman_id')
        milkman_id = int(milkman_id) if (milkman_id and milkman_id.strip() and milkman_id.strip() not in ['0', 'None', 'none']) else None
        
        if not name or not mobile or not address or not society_id:
            flash('Name, Mobile, Address and Society are required!', 'danger')
        else:
            existing_user = User.query.filter_by(mobile_number=mobile).first()
            if existing_user:
                flash('Customer with this mobile number already exists!', 'warning')
            else:
                user = User(
                    name=name,
                    mobile_number=mobile,
                    address=address,
                    role='customer',
                    status='Active'
                )
                user.set_password(password)
                db.session.add(user)
                db.session.flush()

                cust = Customer(
                    user_id=user.id,
                    society_id=int(society_id),
                    address=address,
                    default_quantity=default_qty,
                    custom_rate=custom_rate,
                    status='Active',
                    start_date=date.today(),
                    milkman_id=milkman_id
                )
                db.session.add(cust)
                db.session.commit()
                flash('Customer registered successfully!', 'success')
                
        if redirect_to == 'societies':
            return redirect(url_for('admin.societies'))
        return redirect(url_for('admin.customers'))
        
    society_id = request.args.get('society_id', type=int)
    if society_id:
        all_customers = Customer.query.filter_by(society_id=society_id).all()
    else:
        all_customers = Customer.query.all()
    all_societies = Society.query.all()
    all_milkmen = Milkman.query.filter_by(status='Active').all()
    return render_template('admin/customers.html', customers=all_customers, societies=all_societies, milkmen=all_milkmen, selected_society_id=society_id)


@admin_bp.route('/customers/edit/<int:id>', methods=['POST'])
@admin_required
def edit_customer(id):
    cust = Customer.query.get_or_404(id)
    name = request.form.get('name')
    mobile = request.form.get('mobile')
    address = request.form.get('address')
    society_id = request.form.get('society_id')
    default_qty = float(request.form.get('default_qty', 1.0))
    status = request.form.get('status', 'Active')
    
    custom_rate = request.form.get('custom_rate')
    custom_rate = float(custom_rate) if custom_rate else None
    
    password = request.form.get('password')
    
    # Update milkman allocation
    milkman_id_raw = request.form.get('milkman_id')
    if milkman_id_raw is not None:
        if milkman_id_raw.strip() in ['', '0', 'None', 'none']:
            cust.milkman_id = None
        else:
            try:
                cust.milkman_id = int(milkman_id_raw.strip())
            except ValueError:
                cust.milkman_id = None
    
    if cust.user:
        cust.user.name = name
        cust.user.mobile_number = mobile
        cust.user.address = address
        cust.user.status = status
        if password:
            cust.user.set_password(password)
            
    cust.address = address
    cust.society_id = int(society_id)
    cust.default_quantity = default_qty
    cust.custom_rate = custom_rate
    cust.status = status
        
    db.session.commit()
    flash('Customer details updated successfully!', 'success')
    return redirect(url_for('admin.customers'))


@admin_bp.route('/customers/allocate', methods=['POST'])
@admin_required
def allocate_customer():
    """Dedicated endpoint to allocate, switch, or remove a customer's milkman."""
    customer_id = request.form.get('customer_id')
    milkman_id = request.form.get('milkman_id')
    if not customer_id:
        flash('Customer ID is required!', 'danger')
        return redirect(url_for('admin.customers'))

    cust = Customer.query.get_or_404(int(customer_id))
    if milkman_id and milkman_id.strip() not in ['', '0', 'None', 'none']:
        m_id = int(milkman_id.strip())
        milkman = Milkman.query.get_or_404(m_id)
        cust.milkman_id = milkman.id
        flash(f'Customer "{cust.name}" successfully allocated to milkman "{milkman.name}"!', 'success')
    else:
        cust.milkman_id = None
        flash(f'Allocation removed for customer "{cust.name}".', 'info')

    db.session.commit()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({
            'success': True,
            'customer_id': cust.id,
            'milkman_id': cust.milkman_id,
            'milkman_name': cust.allocated_milkman.name if cust.allocated_milkman else None
        })

    redirect_to = request.form.get('redirect_to', 'customers')
    if redirect_to == 'milkmen':
        return redirect(url_for('admin.milkmen'))
    return redirect(url_for('admin.customers'))


@admin_bp.route('/customers/delete/<int:id>', methods=['POST'])
@admin_required
def delete_customer(id):
    cust = Customer.query.get_or_404(id)
    user = cust.user
    db.session.delete(cust)
    if user:
        db.session.delete(user)
    db.session.commit()
    flash('Customer deleted successfully!', 'success')
    return redirect(url_for('admin.customers'))


# ─────────────────────────────────────────────
# MILK RATE MANAGEMENT
# ─────────────────────────────────────────────
@admin_bp.route('/rates', methods=['GET', 'POST'])
@admin_required
def rates():
    if request.method == 'POST':
        new_rate = float(request.form.get('rate'))
        if new_rate <= 0:
            flash('Invalid rate value!', 'danger')
        else:
            rate = MilkRate(rate_per_liter=new_rate, effective_from=datetime.now())
            db.session.add(rate)
            db.session.commit()
            flash(f'Global Milk Rate updated to ₹{new_rate}/L!', 'success')
        return redirect(url_for('admin.rates'))
        
    rates_history = MilkRate.query.order_by(MilkRate.effective_from.desc()).all()
    active_rate = rates_history[0].rate_per_liter if rates_history else 70.0
    return render_template('admin/rates.html', rates_history=rates_history, active_rate=active_rate)


# ─────────────────────────────────────────────
# PAYMENTS MANAGEMENT
# ─────────────────────────────────────────────
@admin_bp.route('/payments', methods=['GET', 'POST'])
@admin_required
def payments():
    if request.method == 'POST':
        payment_id = request.form.get('payment_id')
        method = request.form.get('payment_method', 'Cash')
        txn_id = request.form.get('transaction_id', 'CASH-' + datetime.now().strftime('%Y%m%d%H%M%S'))
        
        pay = Payment.query.get_or_404(payment_id)
        pay.payment_status = 'Paid'
        pay.payment_date = datetime.now()
        pay.payment_method = method
        pay.transaction_id = txn_id
        
        if pay.bill_id:
            bill = db.session.get(MilkBill, pay.bill_id)
            if bill:
                bill.amount_paid = bill.total_amount
                bill.remaining_amount = 0.0
                bill.bill_status = 'Paid'

        db.session.commit()
        flash('Payment recorded successfully!', 'success')
        return redirect(url_for('admin.payments'))

    # Sync bills for current and previous months
    current_month_str = date.today().strftime('%Y-%m')
    prev_month_date = date.today().replace(day=1) - timedelta(days=1)
    prev_month_str = prev_month_date.strftime('%Y-%m')
    
    customers = Customer.query.all()
    global_rate = MilkRate.query.order_by(MilkRate.effective_from.desc()).first()
    g_rate = global_rate.rate_per_liter if global_rate else 70.0
    
    for c in customers:
        for m_str in [prev_month_str, current_month_str]:
            year, month = map(int, m_str.split('-'))
            deliveries = MilkDelivery.query.filter(
                MilkDelivery.customer_id == c.id,
                db.extract('year', MilkDelivery.delivery_date) == year,
                db.extract('month', MilkDelivery.delivery_date) == month,
                MilkDelivery.delivery_status == 'Delivered'
            ).all()
            
            total_qty = sum(d.quantity for d in deliveries)
            total_amount = sum(d.amount for d in deliveries)
            
            if total_qty > 0:
                bill = MilkBill.query.filter_by(customer_id=c.id, billing_month_str=m_str).first()
                if not bill:
                    bill = MilkBill(
                        customer_id=c.id,
                        month=month,
                        year=year,
                        billing_month_str=m_str,
                        total_quantity=total_qty,
                        total_amount=total_amount,
                        amount_paid=0.0,
                        remaining_amount=total_amount,
                        bill_status='Pending'
                    )
                    db.session.add(bill)
                    db.session.flush()
                
                payment = Payment.query.filter_by(customer_id=c.id, billing_month=m_str).first()
                if not payment:
                    payment = Payment(
                        customer_id=c.id,
                        bill_id=bill.id,
                        billing_month=m_str,
                        amount=total_amount,
                        payment_method='Pending',
                        payment_status='Pending'
                    )
                    db.session.add(payment)
                elif payment.payment_status == 'Pending':
                    payment.amount = total_amount
                    if not payment.bill_id:
                        payment.bill_id = bill.id
                        
    db.session.commit()

    all_payments = Payment.query.order_by(Payment.billing_month.desc(), Payment.payment_status.asc()).all()
    return render_template('admin/payments.html', payments=all_payments)


# ─────────────────────────────────────────────
# SMS LOGS & MESSAGING AUDIT
# ─────────────────────────────────────────────
@admin_bp.route('/sms_logs')
@admin_required
def sms_logs():
    """Admin SMS logs and delivery history view."""
    today = date.today()
    stats = get_daily_sms_stats(today)
    logs = SmsLog.query.order_by(SmsLog.timestamp.desc()).all()
    holiday_messages = HolidayMessage.query.order_by(HolidayMessage.created_at.desc()).all()
    
    return render_template('admin/sms_logs.html',
                           logs=logs,
                           holiday_messages=holiday_messages,
                           stats=stats,
                           today=today)


# ─────────────────────────────────────────────
# ADMIN SETTINGS (Dynamic Settings / SMS Config)
# ─────────────────────────────────────────────
@admin_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    if request.method == 'POST':
        sender_num = request.form.get('sms_sender_number', '6367335889').strip()
        sms_api_key = request.form.get('sms_api_key', '').strip()
        
        # Save or update sender number
        setting = AdminSetting.query.filter_by(key='SMS_SENDER_NUMBER').first()
        if not setting:
            setting = AdminSetting(key='SMS_SENDER_NUMBER', value=sender_num, description='Configured SMS Sender Mobile Number')
            db.session.add(setting)
        else:
            setting.value = sender_num

        db.session.commit()
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('admin.settings'))

    sender_number = get_sender_number()
    today_stats = get_daily_sms_stats(date.today())
    return render_template('admin/settings.html', 
                           sender_number=sender_number, 
                           today_stats=today_stats)


# ─────────────────────────────────────────────
# REPORTS
# ─────────────────────────────────────────────
@admin_bp.route('/reports')
@admin_required
def reports():
    report_type = request.args.get('type', 'daily')
    today = date.today()
    deliveries = []
    title = ""
    
    if report_type == 'daily':
        target_date_str = request.args.get('date', today.strftime('%Y-%m-%d'))
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        deliveries = MilkDelivery.query.filter_by(delivery_date=target_date).all()
        title = f"Daily Delivery Report - {target_date.strftime('%d %b %Y')}"
        
    elif report_type == 'weekly':
        start_date = today - timedelta(days=7)
        deliveries = MilkDelivery.query.filter(MilkDelivery.delivery_date >= start_date, MilkDelivery.delivery_date <= today).all()
        title = f"Weekly Delivery Report ({start_date.strftime('%d %b')} to {today.strftime('%d %b %Y')})"
        
    elif report_type == 'monthly':
        month_str = request.args.get('month', today.strftime('%Y-%m'))
        year, month = map(int, month_str.split('-'))
        deliveries = MilkDelivery.query.filter(
            db.extract('year', MilkDelivery.delivery_date) == year,
            db.extract('month', MilkDelivery.delivery_date) == month
        ).all()
        title = f"Monthly Delivery Report - {datetime(year, month, 1).strftime('%B %Y')}"
        
    elif report_type == 'customer':
        customer_id = request.args.get('customer_id')
        if customer_id:
            cust = db.session.get(Customer, int(customer_id)) if str(customer_id).isdigit() else None
            deliveries = MilkDelivery.query.filter_by(customer_id=customer_id).order_by(MilkDelivery.delivery_date.desc()).all()
            title = f"Customer Report: {cust.name if cust else 'Unknown'}"
        else:
            title = "Customer Report"
            
    elif report_type == 'society':
        society_id = request.args.get('society_id')
        if society_id:
            soc = db.session.get(Society, int(society_id)) if str(society_id).isdigit() else None
            deliveries = MilkDelivery.query.join(Customer).filter(Customer.society_id == society_id).all()
            title = f"Society Report: {soc.society_name if soc else 'Unknown'}"
        else:
            title = "Society Report"

    total_qty = round(sum(d.quantity for d in deliveries), 2)
    total_amount = round(sum(d.amount for d in deliveries), 2)
    
    customers = Customer.query.all()
    societies = Society.query.all()

    return render_template('admin/reports.html',
                           report_type=report_type,
                           deliveries=deliveries,
                           title=title,
                           total_qty=total_qty,
                           total_amount=total_amount,
                           customers=customers,
                           societies=societies,
                           today=today.strftime('%Y-%m-%d'))


# ─────────────────────────────────────────────
# WHATSAPP REMINDER
# ─────────────────────────────────────────────
@admin_bp.route('/send_reminder/<int:payment_id>', methods=['POST'])
@admin_required
def send_reminder(payment_id):
    pay = Payment.query.get_or_404(payment_id)
    cust = pay.customer
    
    year, month = map(int, pay.billing_month.split('-'))
    month_name = datetime(year, month, 1).strftime('%B')
    
    deliveries = MilkDelivery.query.filter(
        MilkDelivery.customer_id == cust.id,
        db.extract('year', MilkDelivery.delivery_date) == year,
        db.extract('month', MilkDelivery.delivery_date) == month,
        MilkDelivery.delivery_status == 'Delivered'
    ).all()
    total_qty = sum(d.quantity for d in deliveries)
    
    msg = f"નમસ્તે {cust.name},\n" \
          f"{month_name} મહિનામાં તમે કુલ {total_qty:.1f} L દૂધ લીધું છે.\n" \
          f"કુલ બિલ = ₹{pay.amount:.2f}\n" \
          f"કૃપા કરીને QR Code, UPI અથવા Cash દ્વારા ચૂકવણી કરો.\n" \
          f"આભાર - ડેરીફ્લો."
          
    notif = Notification(
        recipient_mobile=cust.mobile,
        recipient_name=cust.name,
        message=msg,
        type="WhatsApp",
        status="Sent",
        timestamp=datetime.now()
    )
    db.session.add(notif)
    db.session.commit()
    
    flash(f'WhatsApp reminder successfully sent to {cust.name} ({cust.mobile})!', 'success')
    return redirect(url_for('admin.payments'))


# ─────────────────────────────────────────────
# DATABASE BACKUP
# ─────────────────────────────────────────────
@admin_bp.route('/backup')
@admin_required
def backup_db():
    try:
        db_path = os.path.abspath(os.path.join(admin_bp.root_path, '../database/milk_delivery.db'))
        backup_dir = os.path.abspath(os.path.join(admin_bp.root_path, '../uploads/backups'))
        
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
            
        backup_filename = f"milk_delivery_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = os.path.join(backup_dir, backup_filename)
        
        shutil.copy2(db_path, backup_path)
        
        flash('Database backup created successfully!', 'success')
        return send_file(backup_path, as_attachment=True, download_name=backup_filename)
    except Exception as e:
        flash(f'Backup failed: {str(e)}', 'danger')
        return redirect(url_for('admin.dashboard'))
