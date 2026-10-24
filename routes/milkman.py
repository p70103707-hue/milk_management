from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from models.models import (
    db, Customer, Milkman, Society, MilkDelivery, MilkRate, 
    Payment, MilkBill, Notification, HolidayMessage, SmsLog, User
)
from services.sms_service import (
    get_daily_sms_stats, broadcast_holiday_sms, send_single_sms, get_sender_number
)
from datetime import datetime, date, timedelta

milkman_bp = Blueprint('milkman', __name__, url_prefix='/milkman')

# Helper to get current Milkman model from session
def get_current_milkman():
    user_id = session.get('user_id')
    if not user_id:
        return None
    # Check by Milkman.id or Milkman.user_id
    milkman = db.session.get(Milkman, user_id)
    if not milkman:
        milkman = Milkman.query.filter_by(user_id=user_id).first()
    return milkman

# Login check decorator helper
def milkman_required(f):
    def decorator(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'milkman':
            flash('Milkman login required!', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    decorator.__name__ = f.__name__
    return decorator


def send_delivery_notification(cust, quantity, status, quantity_ml=None):
    """Sends a real or simulated WhatsApp notification to the customer when a delivery is logged."""
    from config import Config
    
    if quantity_ml is None:
        quantity_ml = int(round(quantity * 1000))
        
    date_str = date.today().strftime('%d-%b-%Y')
    if status == 'Delivered':
        msg = (
            f"નમસ્તે {cust.name},\n"
            f"આજનું ({date_str}) દૂધ વિતરણ {quantity_ml} ml ({quantity:g} L) સફળતાપૂર્વક થઈ ગયું છે.\n"
            f"આભાર - ડેરીફ્લો"
        )
    else:
        msg = (
            f"નમસ્તે {cust.name},\n"
            f"આજનું ({date_str}) દૂધ વિતરણ સ્કીપ (બંધ) રાખેલ છે.\n"
            f"આભાર - ડેરીફ્લો"
        )
        
    # 1. Add to Notifications history
    notif = Notification(
        recipient_mobile=cust.mobile,
        recipient_name=cust.name,
        message=msg,
        type="WhatsApp",
        status="Sent",
        timestamp=datetime.now()
    )
    db.session.add(notif)
    
    # 2. Twilio API send if credentials exist
    if getattr(Config, 'TWILIO_ACCOUNT_SID', None) and getattr(Config, 'TWILIO_AUTH_TOKEN', None):
        try:
            from twilio.rest import Client
            client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
            
            whatsapp_to = f"whatsapp:{cust.mobile}" if not cust.mobile.startswith("whatsapp:") else cust.mobile
            if not whatsapp_to.startswith("whatsapp:+"):
                whatsapp_to = whatsapp_to.replace("whatsapp:", "whatsapp:+91")
                
            client.messages.create(
                from_=Config.TWILIO_WHATSAPP_NUMBER,
                body=msg,
                to=whatsapp_to
            )
            print(f"[TWILIO SUCCESS] Sent delivery notification to {cust.mobile}")
        except Exception as e:
            notif.status = "Failed"
            print(f"[TWILIO ERROR] Failed sending delivery notification to {cust.mobile}: {str(e)}")
    else:
        try:
            print(f"--- [MOCK NOTIFICATION OUTBOX] Sent to {cust.mobile} ---\n{msg}\n-------------------")
        except Exception:
            pass


@milkman_bp.route('/dashboard')
@milkman_required
def dashboard():
    milkman = get_current_milkman()
    if not milkman:
        flash('Milkman profile not found. Please re-login.', 'danger')
        return redirect(url_for('auth.login'))

    today = date.today()
    
    # Calculate today's delivery status
    today_deliveries = MilkDelivery.query.filter_by(milkman_id=milkman.id, delivery_date=today).all()
    delivered_count = sum(1 for d in today_deliveries if d.delivery_status == 'Delivered')
    skipped_count = sum(1 for d in today_deliveries if d.delivery_status == 'Skipped')
    
    total_qty_delivered = sum(d.quantity for d in today_deliveries if d.delivery_status == 'Delivered')
    
    # Calculate pending deliveries
    delivered_today_cust_ids = db.session.query(MilkDelivery.customer_id).filter_by(delivery_date=today)
    pending_count = Customer.query.filter_by(status='Active').filter(~Customer.id.in_(delivered_today_cust_ids)).count()
    total_customers = Customer.query.filter_by(status='Active').count()

    # Get SMS statistics for holiday message feature
    sms_stats = get_daily_sms_stats(today)
    
    # Recent holiday messages sent
    recent_holidays = HolidayMessage.query.order_by(HolidayMessage.created_at.desc()).limit(5).all()

    return render_template('milkman/dashboard.html',
                           milkman=milkman,
                           delivered_count=delivered_count,
                           skipped_count=skipped_count,
                           pending_count=pending_count,
                           total_qty_delivered=total_qty_delivered,
                           total_customers=total_customers,
                           sms_stats=sms_stats,
                           recent_holidays=recent_holidays,
                           today_date=today.strftime('%Y-%m-%d'))


@milkman_bp.route('/delivery', methods=['GET', 'POST'])
@milkman_required
def delivery():
    milkman = get_current_milkman()
    if not milkman:
        flash('Milkman profile not found!', 'danger')
        return redirect(url_for('auth.login'))

    today = date.today()
    
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        status = request.form.get('status', 'Delivered')
        delivery_id = request.form.get('delivery_id')
        mode = request.form.get('mode', 'update') # 'add' or 'update'

        # Extract and parse delivery date
        delivery_date_str = request.form.get('delivery_date') or request.form.get('date')
        if delivery_date_str:
            try:
                delivery_date = datetime.strptime(delivery_date_str.strip(), '%Y-%m-%d').date()
            except ValueError:
                delivery_date = today
        else:
            delivery_date = today

        # Support both quantity_ml (preferred) and quantity (L)
        raw_qty_ml = request.form.get('quantity_ml')
        if raw_qty_ml is not None and raw_qty_ml != '':
            quantity_ml = int(float(raw_qty_ml))
            quantity = round(quantity_ml / 1000.0, 3)
        else:
            quantity = float(request.form.get('quantity', 0))
            quantity_ml = int(round(quantity * 1000))
        
        cust = Customer.query.get_or_404(customer_id)
        
        # Get active rate for this customer
        if cust.custom_rate is not None and cust.custom_rate > 0:
            rate = cust.custom_rate
        else:
            active_rate_record = MilkRate.query.order_by(MilkRate.effective_from.desc()).first()
            rate = active_rate_record.rate_per_liter if active_rate_record else 70.0
            
        if status == 'Delivered':
            total_amount = round((quantity_ml / 1000.0) * rate, 2)
        else:
            quantity = 0.0
            quantity_ml = 0
            total_amount = 0.0
        
        # Determine whether to update existing delivery or add a new delivery record
        delivery_record = None
        if delivery_id:
            delivery_record = MilkDelivery.query.filter_by(id=int(delivery_id), customer_id=customer_id).first()

        if delivery_record:
            delivery_record.delivery_date = delivery_date
            delivery_record.quantity = quantity
            delivery_record.quantity_ml = quantity_ml
            delivery_record.milk_rate = rate
            delivery_record.amount = total_amount
            delivery_record.delivery_status = status
            delivery_record.milkman_id = milkman.id
        elif mode == 'add':
            # Explicitly create an additional delivery on the same calendar day (e.g. morning + evening)
            delivery_record = MilkDelivery(
                customer_id=customer_id,
                milkman_id=milkman.id,
                delivery_date=delivery_date,
                quantity=quantity,
                quantity_ml=quantity_ml,
                milk_rate=rate,
                amount=total_amount,
                delivery_status=status
            )
            db.session.add(delivery_record)
        else:
            # Update mode: find existing on this delivery date or create new
            existing = MilkDelivery.query.filter_by(customer_id=customer_id, delivery_date=delivery_date).first()
            if existing:
                existing.quantity = quantity
                existing.quantity_ml = quantity_ml
                existing.milk_rate = rate
                existing.amount = total_amount
                existing.delivery_status = status
                existing.milkman_id = milkman.id
                delivery_record = existing
            else:
                delivery_record = MilkDelivery(
                    customer_id=customer_id,
                    milkman_id=milkman.id,
                    delivery_date=delivery_date,
                    quantity=quantity,
                    quantity_ml=quantity_ml,
                    milk_rate=rate,
                    amount=total_amount,
                    delivery_status=status
                )
                db.session.add(delivery_record)
            
        # Trigger customer notification
        send_delivery_notification(cust, quantity, status, quantity_ml=quantity_ml)
            
        db.session.commit()

        # Calculate daily aggregate for this customer on this date
        day_all_delivs = MilkDelivery.query.filter_by(customer_id=customer_id, delivery_date=delivery_date).all()
        day_total_qty = sum(d.quantity for d in day_all_delivs if d.delivery_status == 'Delivered')
        day_total_ml = int(round(day_total_qty * 1000))
        day_total_amt = sum(d.amount for d in day_all_delivs if d.delivery_status == 'Delivered')
        delivery_count = len(day_all_delivs)
        
        if status == 'Delivered':
            success_msg = f'Saved delivery of {quantity_ml} ml ({quantity:g} L) for {cust.name} on {delivery_date.strftime("%d-%b-%Y")}! (Daily Total: {day_total_qty:.2f} L)'
        else:
            success_msg = f'Delivery marked as Skipped for {cust.name} on {delivery_date.strftime("%d-%b-%Y")}!'
            
        return jsonify({
            'success': True,
            'message': success_msg,
            'customer_id': cust.id,
            'delivery_id': delivery_record.id if delivery_record else None,
            'quantity_ml': quantity_ml,
            'quantity': quantity,
            'total_amount': total_amount,
            'day_total_qty': round(day_total_qty, 3),
            'day_total_ml': day_total_ml,
            'day_total_amt': round(day_total_amt, 2),
            'delivery_count': delivery_count,
            'status': status,
            'date': delivery_date.strftime('%Y-%m-%d')
        })

    # GET request: load customer list by society with delivery status for selected_date
    date_param = request.args.get('date')
    if date_param:
        try:
            selected_date = datetime.strptime(date_param.strip(), '%Y-%m-%d').date()
        except ValueError:
            selected_date = today
    else:
        selected_date = today

    societies = Society.query.all()
    customers_by_society = []
    
    for s in societies:
        society_custs = Customer.query.filter_by(society_id=s.id, status='Active').all()
        cust_list = []
        
        for c in society_custs:
            day_delivs = MilkDelivery.query.filter_by(customer_id=c.id, delivery_date=selected_date).all()
            
            default_ml = int(round(c.default_quantity * 1000)) if c.default_quantity else 1000
            
            if day_delivs:
                delivered_sub = [d for d in day_delivs if d.delivery_status == 'Delivered']
                if delivered_sub:
                    status = 'Delivered'
                    qty = round(sum(d.quantity for d in delivered_sub), 3)
                    qty_ml = int(round(qty * 1000))
                else:
                    status = 'Skipped'
                    qty = 0.0
                    qty_ml = 0
                is_delivered = True
                deliv_records = [{
                    'id': d.id,
                    'quantity': d.quantity,
                    'quantity_ml': d.quantity_ml,
                    'amount': d.amount,
                    'status': d.delivery_status
                } for d in day_delivs]
            else:
                is_delivered = False
                status = None
                qty = 0.0
                qty_ml = default_ml
                deliv_records = []
            
            cust_list.append({
                'id': c.id,
                'name': c.name,
                'mobile': c.mobile,
                'address': c.address,
                'society_id': c.society_id,
                'default_qty': c.default_quantity,
                'default_qty_ml': default_ml,
                'custom_rate': c.custom_rate,
                'delivered': is_delivered,
                'status': status,
                'qty': qty,
                'qty_ml': qty_ml,
                'delivery_count': len(day_delivs),
                'deliveries': deliv_records
            })
            
        if cust_list:
            customers_by_society.append({
                'society_id': s.id,
                'society_name': s.society_name,
                'customers': cust_list
            })
            
    return render_template('milkman/delivery.html', 
                           data=customers_by_society,
                           selected_date=selected_date,
                           societies=societies,
                           today_date=today)


# ─────────────────────────────────────────────
# MILKMAN CUSTOMER MANAGEMENT (Add, View, Edit, Update, Delete, Search)
# ─────────────────────────────────────────────

@milkman_bp.route('/customers', methods=['GET', 'POST'])
@milkman_required
def customers():
    """Customer management for Milkman: View all, search, and register new customer."""
    milkman = get_current_milkman()
    if not milkman:
        flash('Milkman profile not found!', 'danger')
        return redirect(url_for('auth.login'))

    societies = Society.query.all()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        mobile = request.form.get('mobile', '').strip()
        address = request.form.get('address', '').strip()
        society_id = request.form.get('society_id')
        password = request.form.get('password', 'customer123').strip() or 'customer123'
        
        try:
            default_qty = float(request.form.get('default_qty', 1.0))
        except (ValueError, TypeError):
            default_qty = 1.0

        custom_rate_val = request.form.get('custom_rate')
        custom_rate = float(custom_rate_val) if custom_rate_val and custom_rate_val.strip() else None

        if not name or not mobile or not address or not society_id:
            flash('Customer Name, Mobile, Address, and Society are all required!', 'danger')
            return redirect(url_for('milkman.customers'))

        # Check for duplicate mobile
        existing_user = User.query.filter_by(mobile_number=mobile).first()
        if existing_user:
            flash(f'A user with mobile number {mobile} is already registered!', 'warning')
            return redirect(url_for('milkman.customers'))

        try:
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

            allocate_to_me = request.form.get('allocate_to_me', '1')
            alloc_milkman_id = milkman.id if allocate_to_me in ['1', 'true', 'yes', 'on'] else None

            cust = Customer(
                user_id=user.id,
                society_id=int(society_id),
                address=address,
                default_quantity=default_qty,
                custom_rate=custom_rate,
                status='Active',
                start_date=date.today(),
                milkman_id=alloc_milkman_id
            )
            db.session.add(cust)
            db.session.commit()
            flash(f'Customer "{name}" registered successfully with ID #{cust.id}!', 'success')
            return redirect(url_for('milkman.customers'))
        except Exception as e:
            db.session.rollback()
            flash(f'Failed to register customer: {str(e)}', 'danger')
            return redirect(url_for('milkman.customers'))

    # GET: fetch customers
    search_q = request.args.get('q', '').strip()
    society_filter = request.args.get('society_id', type=int)
    allocation_filter = request.args.get('allocation', '').strip()

    query = Customer.query.join(User)
    if search_q:
        query = query.filter(
            (User.name.ilike(f'%{search_q}%')) |
            (User.mobile_number.ilike(f'%{search_q}%')) |
            (Customer.address.ilike(f'%{search_q}%'))
        )
    if society_filter:
        query = query.filter(Customer.society_id == society_filter)
    if allocation_filter == 'mine':
        query = query.filter(Customer.milkman_id == milkman.id)
    elif allocation_filter == 'unallocated':
        query = query.filter(Customer.milkman_id.is_(None))

    customer_list = query.order_by(Customer.id.asc()).all()

    return render_template('milkman/customers.html',
                           customers=customer_list,
                           societies=societies,
                           milkman=milkman,
                           selected_society_id=society_filter,
                           allocation_filter=allocation_filter,
                           search_q=search_q)


@milkman_bp.route('/customers/get/<int:id>')
@milkman_required
def get_customer(id):
    """API: Returns JSON of customer information for edit modal pre-population."""
    cust = Customer.query.get_or_404(id)
    milkman = get_current_milkman()
    return jsonify({
        'success': True,
        'id': cust.id,
        'name': cust.name,
        'mobile': cust.mobile,
        'address': cust.address,
        'society_id': cust.society_id,
        'default_quantity': cust.default_quantity,
        'custom_rate': cust.custom_rate,
        'status': cust.status,
        'milkman_id': cust.milkman_id,
        'milkman_name': cust.allocated_milkman.name if cust.allocated_milkman else None,
        'is_allocated_to_me': (cust.milkman_id == milkman.id) if milkman else False
    })


@milkman_bp.route('/customers/edit/<int:id>', methods=['POST'])
@milkman_required
def edit_customer(id):
    """Update customer details without affecting existing delivery records."""
    milkman = get_current_milkman()
    cust = Customer.query.get_or_404(id)
    name = request.form.get('name', '').strip()
    mobile = request.form.get('mobile', '').strip()
    address = request.form.get('address', '').strip()
    society_id = request.form.get('society_id')
    status = request.form.get('status', 'Active')
    password = request.form.get('password', '').strip()

    try:
        default_qty = float(request.form.get('default_qty', 1.0))
    except (ValueError, TypeError):
        default_qty = 1.0

    custom_rate_val = request.form.get('custom_rate')
    custom_rate = float(custom_rate_val) if custom_rate_val and custom_rate_val.strip() else None

    if not name or not mobile or not address or not society_id:
        flash('Name, Mobile, Address, and Society are required!', 'danger')
        return redirect(url_for('milkman.customers'))

    # Check mobile uniqueness if changed
    if cust.user and cust.user.mobile_number != mobile:
        existing = User.query.filter(User.mobile_number == mobile, User.id != cust.user_id).first()
        if existing:
            flash(f'Another user is already registered with mobile number {mobile}!', 'danger')
            return redirect(url_for('milkman.customers'))

    try:
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

        # Handle allocation update
        allocation_action = request.form.get('allocation_action')
        if allocation_action == 'allocate_to_me':
            cust.milkman_id = milkman.id
        elif allocation_action == 'remove_allocation':
            if cust.milkman_id == milkman.id:
                cust.milkman_id = None

        db.session.commit()
        flash(f'Customer "{name}" profile updated successfully! All milk records preserved.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating customer: {str(e)}', 'danger')

    redirect_to = request.form.get('redirect_to')
    if redirect_to == 'delivery':
        return redirect(url_for('milkman.delivery'))
    return redirect(url_for('milkman.customers'))


@milkman_bp.route('/customers/allocate', methods=['POST'])
@milkman_required
def allocate_customer():
    """Quickly allocate or unallocate a customer to the current milkman."""
    milkman = get_current_milkman()
    if not milkman:
        return jsonify({'success': False, 'message': 'Milkman not authenticated.'}), 403

    customer_id = request.form.get('customer_id')
    action = request.form.get('action', 'allocate') # 'allocate' or 'unallocate'
    if not customer_id:
        flash('Customer ID is required!', 'danger')
        return redirect(url_for('milkman.customers'))

    cust = Customer.query.get_or_404(int(customer_id))
    if action == 'allocate':
        cust.milkman_id = milkman.id
        msg = f'Customer "{cust.name}" successfully allocated to you!'
    elif action == 'unallocate':
        if cust.milkman_id == milkman.id:
            cust.milkman_id = None
            msg = f'Customer "{cust.name}" removed from your allocation.'
        else:
            msg = f'Customer "{cust.name}" is not allocated to you.'
    else:
        msg = 'Invalid action.'

    db.session.commit()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({
            'success': True,
            'message': msg,
            'customer_id': cust.id,
            'milkman_id': cust.milkman_id,
            'is_allocated_to_me': cust.milkman_id == milkman.id,
            'milkman_name': cust.allocated_milkman.name if cust.allocated_milkman else None
        })

    flash(msg, 'success')
    return redirect(url_for('milkman.customers'))


@milkman_bp.route('/customers/delete/<int:id>', methods=['POST'])
@milkman_required
def delete_customer(id):
    """Safely delete only the selected customer."""
    cust = Customer.query.get_or_404(id)
    cust_name = cust.name
    user = cust.user

    try:
        db.session.delete(cust)
        if user:
            db.session.delete(user)
        db.session.commit()
        flash(f'Customer "{cust_name}" deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting customer: {str(e)}', 'danger')

    return redirect(url_for('milkman.customers'))


@milkman_bp.route('/report')
@milkman_required
def report():
    milkman = get_current_milkman()
    if not milkman:
        flash('Milkman profile not found!', 'danger')
        return redirect(url_for('auth.login'))

    today = date.today()
    
    # Daily deliveries: grouped by (customer_id, delivery_date)
    raw_daily = MilkDelivery.query.filter_by(milkman_id=milkman.id, delivery_date=today).all()
    daily_groups = {}
    for d in raw_daily:
        key = (d.customer_id, d.delivery_date)
        if key not in daily_groups:
            daily_groups[key] = []
        daily_groups[key].append(d)

    daily_deliveries = []
    for (c_id, deliv_date), d_list in daily_groups.items():
        cust = d_list[0].customer
        delivered_items = [d for d in d_list if d.delivery_status == 'Delivered']
        if delivered_items:
            status = 'Delivered'
            total_qty = sum(d.quantity for d in delivered_items)
            total_ml = int(round(total_qty * 1000))
            total_amt = sum(d.amount for d in delivered_items)
        else:
            status = 'Skipped'
            total_qty = 0.0
            total_ml = 0
            total_amt = 0.0

        daily_deliveries.append({
            'customer': cust,
            'date': deliv_date,
            'quantity': round(total_qty, 3),
            'quantity_ml': total_ml,
            'total_amount': round(total_amt, 2),
            'status': status,
            'delivery_count': len(d_list)
        })
    
    # Monthly deliveries: grouped by (customer_id, delivery_date)
    start_of_month = today.replace(day=1)
    raw_monthly = MilkDelivery.query.filter(
        MilkDelivery.milkman_id == milkman.id,
        MilkDelivery.delivery_date >= start_of_month,
        MilkDelivery.delivery_date <= today
    ).order_by(MilkDelivery.delivery_date.desc()).all()

    monthly_groups = {}
    for d in raw_monthly:
        key = (d.customer_id, d.delivery_date)
        if key not in monthly_groups:
            monthly_groups[key] = []
        monthly_groups[key].append(d)

    monthly_deliveries = []
    for (c_id, deliv_date), d_list in monthly_groups.items():
        cust = d_list[0].customer
        delivered_items = [d for d in d_list if d.delivery_status == 'Delivered']
        if delivered_items:
            status = 'Delivered'
            total_qty = sum(d.quantity for d in delivered_items)
            total_ml = int(round(total_qty * 1000))
            total_amt = sum(d.amount for d in delivered_items)
        else:
            status = 'Skipped'
            total_qty = 0.0
            total_ml = 0
            total_amt = 0.0

        monthly_deliveries.append({
            'customer': cust,
            'date': deliv_date,
            'quantity': round(total_qty, 3),
            'quantity_ml': total_ml,
            'total_amount': round(total_amt, 2),
            'status': status,
            'delivery_count': len(d_list)
        })
    
    # Sort by date desc
    monthly_deliveries.sort(key=lambda x: x['date'], reverse=True)
    
    return render_template('milkman/report.html', 
                           daily_deliveries=daily_deliveries, 
                           monthly_deliveries=monthly_deliveries)


# ─────────────────────────────────────────────
# HOLIDAY MESSAGE & SMS SERVICE ROUTES
# ─────────────────────────────────────────────

@milkman_bp.route('/sms_stats')
@milkman_required
def sms_stats_api():
    """Returns live daily SMS statistics (used for frontend real-time counters)."""
    today = date.today()
    stats = get_daily_sms_stats(today)
    return jsonify({
        'success': True,
        'stats': stats
    })


@milkman_bp.route('/send_holiday_message', methods=['POST'])
@milkman_required
def send_holiday_message():
    """Handles holiday message broadcast to active customers with 50 SMS/day limit protection."""
    milkman = get_current_milkman()
    if not milkman:
        return jsonify({'success': False, 'message': 'Milkman not identified'}), 403

    # Support JSON or Form Data
    if request.is_json:
        data = request.get_json()
        holiday_date_str = data.get('holiday_date')
        custom_message = data.get('message')
    else:
        holiday_date_str = request.form.get('holiday_date')
        custom_message = request.form.get('message')

    if not holiday_date_str:
        return jsonify({'success': False, 'message': 'Holiday date is required!'}), 400

    try:
        holiday_date = datetime.strptime(holiday_date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format. Use YYYY-MM-DD.'}), 400

    # Call SMS broadcast service
    result = broadcast_holiday_sms(
        milkman_id=milkman.id,
        holiday_date=holiday_date,
        custom_message=custom_message
    )

    if not request.is_json:
        if result['success']:
            flash(result['message'], 'success')
        else:
            flash(result['message'], 'danger')
        return redirect(url_for('milkman.dashboard'))

    return jsonify(result)


@milkman_bp.route('/holiday_history')
@milkman_required
def holiday_history():
    """View sent holiday messages history and customer delivery statuses."""
    milkman = get_current_milkman()
    messages = HolidayMessage.query.order_by(HolidayMessage.created_at.desc()).all()
    today_stats = get_daily_sms_stats(date.today())
    return render_template('milkman/holiday_history.html', 
                           messages=messages, 
                           stats=today_stats)


# ─────────────────────────────────────────────
# PAYMENT COLLECTION ROUTES
# ─────────────────────────────────────────────

@milkman_bp.route('/collect_payment')
@milkman_required
def collect_payment():
    """Page for milkman to collect payment from customers (COD, UPI QR, Cash, Razorpay)."""
    today = date.today()
    current_month_str = today.strftime('%Y-%m')

    # Get all active customers with pending payments
    pending_bills = db.session.query(Payment, Customer).join(
        Customer, Payment.customer_id == Customer.id
    ).filter(
        Payment.payment_status == 'Pending'
    ).order_by(Customer.id.asc()).all()

    # Group by customer
    customer_bills = {}
    for payment, customer in pending_bills:
        if customer.id not in customer_bills:
            customer_bills[customer.id] = {
                'customer': customer,
                'bills': []
            }
        customer_bills[customer.id]['bills'].append(payment)

    customer_list = list(customer_bills.values())

    return render_template('milkman/collect_payment.html',
                           customer_list=customer_list,
                           today=today)


@milkman_bp.route('/customer_bills/<int:customer_id>')
@milkman_required
def customer_bills(customer_id):
    """API: Return pending bills for a specific customer (JSON for AJAX)."""
    cust = Customer.query.get_or_404(customer_id)
    pending = Payment.query.filter_by(customer_id=customer_id, payment_status='Pending').all()
    bills_data = [{
        'id': p.id,
        'billing_month': p.billing_month,
        'amount': p.amount,
        'status': p.payment_status
    } for p in pending]
    return jsonify({
        'customer_name': cust.name,
        'customer_mobile': cust.mobile,
        'bills': bills_data
    })


@milkman_bp.route('/collect_payment_submit', methods=['POST'])
@milkman_required
def collect_payment_submit():
    """API: Milkman records a payment collection from a customer (COD / Cash / UPI)."""
    data = request.get_json() or request.form
    payment_id = data.get('payment_id')
    method = data.get('payment_method', 'Cash')

    payment = Payment.query.get_or_404(payment_id)

    if payment.payment_status == 'Paid':
        return jsonify({'success': False, 'message': 'Bill is already marked as paid!'}), 400

    # Mark payment as paid
    txn_id = f"MM-{method.upper().replace(' ', '')}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{payment.id}"
    payment.payment_status = 'Paid'
    payment.payment_date = datetime.now()
    payment.payment_method = method
    payment.transaction_id = txn_id

    # Also update MilkBill if linked
    if payment.bill_id:
        bill = db.session.get(MilkBill, payment.bill_id)
        if bill:
            bill.amount_paid = bill.total_amount
            bill.remaining_amount = 0.0
            bill.bill_status = 'Paid'

    # Customer notification
    cust = payment.customer
    milkman = get_current_milkman()
    milkman_name = milkman.name if milkman else 'Milkman'

    msg = (
        f"નમસ્તે {cust.name}, "
        f"તમારો {payment.billing_month} નો ₹{payment.amount:.2f} નો હિસાબ "
        f"{milkman_name} દ્વારા {method} પ્રણાલીથી પ્રાપ્ત થઈ ગયો છે. "
        f"ટ્રા. ID: {txn_id}."
    )
    notif = Notification(
        recipient_mobile=cust.mobile,
        recipient_name=cust.name,
        message=msg,
        type='System',
        status='Sent'
    )
    db.session.add(notif)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'₹{payment.amount:.2f} collected from {cust.name} via {method}!',
        'transaction_id': txn_id,
        'billing_month': payment.billing_month,
        'amount': payment.amount
    })
