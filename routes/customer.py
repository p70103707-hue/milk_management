from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, session, jsonify
from models.models import (
    db, Customer, MilkDelivery, Payment, MilkBill, MilkRate, 
    Notification, HolidayMessage, Society, Milkman
)
from datetime import datetime, date, timedelta
import calendar
from io import BytesIO

# Reportlab for PDF Invoices
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

customer_bp = Blueprint('customer', __name__, url_prefix='/customer')

# Helper to get current Customer model
def get_current_customer():
    user_id = session.get('user_id')
    if not user_id:
        return None
    cust = db.session.get(Customer, user_id)
    if not cust:
        cust = Customer.query.filter_by(user_id=user_id).first()
    return cust

# Login check decorator helper
def customer_required(f):
    def decorator(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'customer':
            flash('Customer login required!', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    decorator.__name__ = f.__name__
    return decorator


@customer_bp.route('/dashboard')
@customer_required
def dashboard():
    cust = get_current_customer()
    if not cust:
        flash('Customer profile not found. Please re-login.', 'danger')
        return redirect(url_for('auth.login'))

    customer_id = cust.id
    today = date.today()
    
    # 1. Determine selected month and year
    month_arg = request.args.get('month')
    year_arg = request.args.get('year')
    
    selected_year = today.year
    selected_month = today.month
    
    if month_arg and '-' in month_arg:
        try:
            y_val, m_val = map(int, month_arg.split('-'))
            selected_year, selected_month = y_val, m_val
        except (ValueError, TypeError):
            pass
    else:
        if year_arg:
            try:
                selected_year = int(year_arg)
            except (ValueError, TypeError):
                selected_year = today.year
        if month_arg:
            try:
                selected_month = int(month_arg)
                if selected_month < 1 or selected_month > 12:
                    selected_month = today.month
            except (ValueError, TypeError):
                selected_month = today.month

    billing_month_str = f"{selected_year:04d}-{selected_month:02d}"
    selected_month_name = calendar.month_name[selected_month]

    # 2. Get customer's active milk rate
    if cust.custom_rate is not None and cust.custom_rate > 0:
        active_rate = cust.custom_rate
    else:
        active_rate_record = MilkRate.query.order_by(MilkRate.effective_from.desc()).first()
        active_rate = active_rate_record.rate_per_liter if active_rate_record else 70.0

    # 3. Generate daily records for every day of the selected month
    _, num_days = calendar.monthrange(selected_year, selected_month)
    
    deliveries = MilkDelivery.query.filter(
        MilkDelivery.customer_id == customer_id,
        db.extract('year', MilkDelivery.delivery_date) == selected_year,
        db.extract('month', MilkDelivery.delivery_date) == selected_month
    ).order_by(MilkDelivery.delivery_date.asc()).all()
    
    # Group deliveries by calendar date to correctly aggregate multiple deliveries on the same day
    deliveries_by_date = {}
    for d in deliveries:
        if d.delivery_date not in deliveries_by_date:
            deliveries_by_date[d.delivery_date] = []
        deliveries_by_date[d.delivery_date].append(d)
    
    daily_records = []
    total_quantity = 0.0
    total_delivery_days = 0
    total_monthly_amount = 0.0
    
    for day in range(1, num_days + 1):
        day_date = date(selected_year, selected_month, day)
        day_name = day_date.strftime('%A')
        day_delivs = deliveries_by_date.get(day_date, [])
        
        delivered_items = [d for d in day_delivs if d.delivery_status == 'Delivered']
        skipped_items = [d for d in day_delivs if d.delivery_status == 'Skipped']
        
        if delivered_items:
            # Sum all quantities delivered on this calendar day
            qty = round(sum(d.quantity for d in delivered_items), 3)
            # Use rate from record or active customer rate
            day_rate = delivered_items[0].milk_rate if delivered_items[0].milk_rate else active_rate
            amt = round(qty * day_rate, 2)
            status_text = 'Delivered'
            is_delivered = True
            
            total_quantity += qty
            total_monthly_amount += amt
            total_delivery_days += 1
        elif skipped_items:
            qty = 0.0
            day_rate = active_rate
            amt = 0.0
            status_text = 'No Milk / Not Delivered'
            is_delivered = False
        else:
            qty = 0.0
            day_rate = active_rate
            amt = 0.0
            status_text = 'No Milk / Not Delivered'
            is_delivered = False
            
        daily_records.append({
            'day_num': f"{day:02d}",
            'date': day_date,
            'day_name': day_name,
            'quantity': qty,
            'rate': day_rate,
            'amount': amt,
            'status': status_text,
            'is_delivered': is_delivered,
            'delivery_count': len(delivered_items)
        })

    # Round totals
    total_quantity = round(total_quantity, 2)
    total_monthly_amount = round(total_monthly_amount, 2)

    # 4. Check if month is completed
    is_month_completed = (today.year > selected_year) or (today.year == selected_year and today.month > selected_month)

    # 5. Maintain / synchronize MilkBill and Payment in database
    bill = MilkBill.query.filter_by(customer_id=customer_id, billing_month_str=billing_month_str).first()
    if not bill and total_monthly_amount > 0:
        bill = MilkBill(
            customer_id=customer_id,
            month=selected_month,
            year=selected_year,
            billing_month_str=billing_month_str,
            total_quantity=total_quantity,
            total_amount=total_monthly_amount,
            amount_paid=0.0,
            remaining_amount=total_monthly_amount,
            bill_status='Pending'
        )
        db.session.add(bill)
        db.session.commit()
    elif bill and abs(bill.total_amount - total_monthly_amount) > 0.01 and bill.bill_status == 'Pending':
        bill.total_quantity = total_quantity
        bill.total_amount = total_monthly_amount
        bill.remaining_amount = total_monthly_amount
        db.session.commit()

    payment = Payment.query.filter_by(customer_id=customer_id, billing_month=billing_month_str).first()
    if total_monthly_amount > 0:
        if not payment:
            payment = Payment(
                customer_id=customer_id,
                bill_id=bill.id if bill else None,
                billing_month=billing_month_str,
                payment_method='Pending',
                amount=total_monthly_amount,
                payment_status='Pending'
            )
            db.session.add(payment)
            db.session.commit()
        elif payment.payment_status == 'Pending' and abs(payment.amount - total_monthly_amount) > 0.01:
            payment.amount = total_monthly_amount
            if bill and not payment.bill_id:
                payment.bill_id = bill.id
            db.session.commit()

    payment_status = payment.payment_status if payment else ('Pending' if total_monthly_amount > 0 else 'No Bill')
    
    # 6. Today's Delivery Quick Card
    today_delivery = MilkDelivery.query.filter_by(customer_id=customer_id, delivery_date=today).first()
    
    # 7. Payment History (all previous records)
    payment_history = Payment.query.filter_by(customer_id=customer_id).order_by(Payment.billing_month.desc()).all()
    
    # 8. Upcoming / Recent Holiday Messages
    recent_holidays = HolidayMessage.query.order_by(HolidayMessage.holiday_date.desc()).limit(3).all()
    
    # 9. Assigned Milkman information (strictly database-driven allocation)
    milkman = cust.allocated_milkman
    
    # Dropdown lists
    available_years = [2024, 2025, 2026, 2027]
    available_months = [(i, calendar.month_name[i]) for i in range(1, 13)]
    is_current_period = (selected_year == today.year and selected_month == today.month)

    return render_template('customer/dashboard.html',
                           customer=cust,
                           active_rate=active_rate,
                           selected_month=selected_month,
                           selected_month_name=selected_month_name,
                           selected_year=selected_year,
                           billing_month_str=billing_month_str,
                           daily_records=daily_records,
                           total_quantity=total_quantity,
                           total_delivery_days=total_delivery_days,
                           total_monthly_amount=total_monthly_amount,
                           is_month_completed=is_month_completed,
                           is_current_period=is_current_period,
                           bill=bill,
                           payment=payment,
                           payment_status=payment_status,
                           today_delivery=today_delivery,
                           payment_history=payment_history,
                           recent_holidays=recent_holidays,
                           milkman=milkman,
                           available_years=available_years,
                           available_months=available_months)


@customer_bp.route('/payment/<int:payment_id>')
@customer_bp.route('/payment_details/<int:payment_id>')
@customer_bp.route('/payment_detail/<int:payment_id>')
@customer_required
def payment_details(payment_id):
    """
    Dedicated Payment Details page displaying complete customer details,
    bill info, COD / Cash option, and dynamic QR code payment option.
    """
    cust = get_current_customer()
    payment = Payment.query.get_or_404(payment_id)
    
    if payment.customer_id != cust.id and session.get('role') != 'admin':
        flash('Unauthorized access to payment details!', 'danger')
        return redirect(url_for('customer.dashboard'))
        
    year, month = map(int, payment.billing_month.split('-'))
    month_name = calendar.month_name[month]
    
    # Fetch or calculate delivery breakdown
    deliveries = MilkDelivery.query.filter(
        MilkDelivery.customer_id == cust.id,
        db.extract('year', MilkDelivery.delivery_date) == year,
        db.extract('month', MilkDelivery.delivery_date) == month,
        MilkDelivery.delivery_status == 'Delivered'
    ).all()
    
    total_qty = round(sum(d.quantity for d in deliveries), 2)
    rate_used = cust.custom_rate if cust.custom_rate else (deliveries[0].milk_rate if deliveries else 70.0)
    
    amount_paid = payment.amount if payment.payment_status == 'Paid' else 0.0
    remaining_amount = 0.0 if payment.payment_status == 'Paid' else payment.amount
    
    # Milkman contact info
    milkman = Milkman.query.first()
    
    return render_template('customer/payment_details.html',
                           customer=cust,
                           payment=payment,
                           bill_month_name=month_name,
                           bill_year=year,
                           total_quantity=total_qty,
                           milk_rate=rate_used,
                           total_bill_amount=payment.amount,
                           amount_paid=amount_paid,
                           remaining_amount=remaining_amount,
                           payment_status=payment.payment_status,
                           milkman=milkman)


@customer_bp.route('/report')
@customer_bp.route('/history')
@customer_required
def report():
    """
    Dedicated Customer Report page:
    Displays customer's milk consumption history grouped by calendar date,
    showing delivery date, daily total quantity, milk rate, daily amount,
    monthly total quantity, monthly total amount, and payment status.
    """
    cust = get_current_customer()
    if not cust:
        flash('Customer profile not found. Please re-login.', 'danger')
        return redirect(url_for('auth.login'))

    customer_id = cust.id
    today = date.today()
    
    # Determine selected month and year
    month_arg = request.args.get('month')
    year_arg = request.args.get('year')
    
    selected_year = today.year
    selected_month = today.month
    
    if month_arg and '-' in month_arg:
        try:
            y_val, m_val = map(int, month_arg.split('-'))
            selected_year, selected_month = y_val, m_val
        except (ValueError, TypeError):
            pass
    else:
        if year_arg:
            try:
                selected_year = int(year_arg)
            except (ValueError, TypeError):
                selected_year = today.year
        if month_arg:
            try:
                selected_month = int(month_arg)
                if selected_month < 1 or selected_month > 12:
                    selected_month = today.month
            except (ValueError, TypeError):
                selected_month = today.month

    billing_month_str = f"{selected_year:04d}-{selected_month:02d}"
    selected_month_name = calendar.month_name[selected_month]

    # Active rate
    if cust.custom_rate is not None and cust.custom_rate > 0:
        active_rate = cust.custom_rate
    else:
        active_rate_record = MilkRate.query.order_by(MilkRate.effective_from.desc()).first()
        active_rate = active_rate_record.rate_per_liter if active_rate_record else 70.0

    # Deliveries for selected month
    _, num_days = calendar.monthrange(selected_year, selected_month)
    deliveries = MilkDelivery.query.filter(
        MilkDelivery.customer_id == customer_id,
        db.extract('year', MilkDelivery.delivery_date) == selected_year,
        db.extract('month', MilkDelivery.delivery_date) == selected_month
    ).order_by(MilkDelivery.delivery_date.asc()).all()

    deliveries_by_date = {}
    for d in deliveries:
        if d.delivery_date not in deliveries_by_date:
            deliveries_by_date[d.delivery_date] = []
        deliveries_by_date[d.delivery_date].append(d)

    calendar_data = []
    total_quantity = 0.0
    total_delivery_days = 0
    total_monthly_amount = 0.0

    for day in range(1, num_days + 1):
        day_date = date(selected_year, selected_month, day)
        day_delivs = deliveries_by_date.get(day_date, [])

        delivered_items = [d for d in day_delivs if d.delivery_status == 'Delivered']
        skipped_items = [d for d in day_delivs if d.delivery_status == 'Skipped']

        if delivered_items:
            qty = round(sum(d.quantity for d in delivered_items), 3)
            day_rate = delivered_items[0].milk_rate if delivered_items[0].milk_rate else active_rate
            amt = round(qty * day_rate, 2)
            status = 'Delivered'
            total_quantity += qty
            total_monthly_amount += amt
            total_delivery_days += 1
        elif skipped_items:
            qty = 0.0
            day_rate = active_rate
            amt = 0.0
            status = 'Skipped'
        else:
            qty = 0.0
            day_rate = active_rate
            amt = 0.0
            status = 'Not Delivered'

        calendar_data.append({
            'date': day_date,
            'day_num': f"{day:02d}",
            'day_name': day_date.strftime('%A'),
            'quantity': qty,
            'rate': day_rate,
            'amount': amt,
            'status': status,
            'delivery_count': len(delivered_items)
        })

    total_quantity = round(total_quantity, 2)
    total_monthly_amount = round(total_monthly_amount, 2)

    # Monthly Bill and Payment status
    bill = MilkBill.query.filter_by(customer_id=customer_id, billing_month_str=billing_month_str).first()
    payment = Payment.query.filter_by(customer_id=customer_id, billing_month=billing_month_str).first()
    payment_status = payment.payment_status if payment else ('Pending' if total_monthly_amount > 0 else 'No Bill')

    available_years = [2024, 2025, 2026, 2027]
    available_months = [(i, calendar.month_name[i]) for i in range(1, 13)]

    return render_template('customer/report.html',
                           customer=cust,
                           selected_month=selected_month,
                           selected_month_name=selected_month_name,
                           selected_year=selected_year,
                           billing_month_str=billing_month_str,
                           calendar_data=calendar_data,
                           total_qty=total_quantity,
                           total_amount=total_monthly_amount,
                           total_delivery_days=total_delivery_days,
                           active_rate=active_rate,
                           bill=bill,
                           payment=payment,
                           payment_status=payment_status,
                           available_years=available_years,
                           available_months=available_months)


@customer_bp.route('/pay/<int:payment_id>', methods=['POST'])
@customer_required
def pay_bill(payment_id):
    """Handles online and offline payment confirmation (COD, UPI QR, Razorpay, PhonePe, GPay)."""
    payment = Payment.query.get_or_404(payment_id)
    cust = get_current_customer()
    
    if payment.customer_id != cust.id:
        return jsonify({'success': False, 'message': 'Unauthorized operation!'}), 403
        
    if payment.payment_status == 'Paid':
        return jsonify({'success': False, 'message': 'Bill is already paid!'}), 400
        
    data = request.get_json() if request.is_json else request.form
    method = data.get('payment_method', 'UPI QR')
    user_txn_id = data.get('transaction_id', '').strip()

    # Generate or use provided transaction ID
    if user_txn_id:
        txn_id = user_txn_id
    else:
        prefix = "COD" if "Cash" in method or "COD" in method else method.upper().replace(' ', '')
        txn_id = f"TXN-{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{payment.id}"

    payment.payment_status = 'Paid'
    payment.payment_date = datetime.now()
    payment.payment_method = method
    payment.transaction_id = txn_id
    
    # Update linked MilkBill if present
    if payment.bill_id:
        bill = db.session.get(MilkBill, payment.bill_id)
        if bill:
            bill.amount_paid = bill.total_amount
            bill.remaining_amount = 0.0
            bill.bill_status = 'Paid'
    
    # Confirmation Notification
    msg = f"નમસ્તે {cust.name}, તમારું બિલ ₹{payment.amount:.2f} પદ્ધતિ {method} થી સફળતાપૂર્વક મળેલ છે. ટ્રાન્ઝેક્શન આઈડી: {txn_id}."
    
    notif = Notification(
        recipient_mobile=cust.mobile,
        recipient_name=cust.name,
        message=msg,
        type="System",
        status="Sent",
        timestamp=datetime.now()
    )
    db.session.add(notif)
    db.session.commit()
    
    if not request.is_json and not request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        flash(f'Payment of ₹{payment.amount:.2f} successful via {method}!', 'success')
        return redirect(url_for('customer.payment_details', payment_id=payment.id))

    return jsonify({
        'success': True,
        'message': f'Payment of ₹{payment.amount:.2f} successful using {method}!',
        'transaction_id': txn_id,
        'payment_date': payment.payment_date.strftime('%d-%m-%Y'),
        'payment_status': 'PAID',
        'invoice_url': url_for('customer.download_invoice', payment_id=payment.id)
    })


@customer_bp.route('/message_milkman', methods=['POST'])
@customer_required
def message_milkman():
    """Allows customer to send a message / inquiry directly to the milkman."""
    cust = get_current_customer()
    message_text = request.form.get('message', '').strip()
    milkman_id = request.form.get('milkman_id')
    
    if not message_text:
        flash('Please enter a message before sending.', 'warning')
        return redirect(url_for('customer.dashboard'))
        
    milkman = cust.allocated_milkman
    if not milkman:
        flash('No milkman is currently allocated to your account.', 'warning')
        return redirect(url_for('customer.dashboard'))

    recipient_mobile = milkman.mobile
    recipient_name = milkman.name
    
    formatted_msg = f"📩 Customer Inquiry from {cust.name} ({cust.mobile}, {cust.address}): {message_text}"
    
    notif = Notification(
        recipient_mobile=recipient_mobile,
        recipient_name=recipient_name,
        message=formatted_msg,
        type="Customer Message",
        status="Sent",
        timestamp=datetime.now()
    )
    db.session.add(notif)
    db.session.commit()
    
    flash('Your message has been sent to the milkman!', 'success')
    return redirect(url_for('customer.dashboard'))


@customer_bp.route('/invoice/<int:payment_id>')
@customer_required
def download_invoice(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    cust = payment.customer
    
    current_cust = get_current_customer()
    if (not current_cust or payment.customer_id != current_cust.id) and session.get('role') != 'admin':
        flash('Unauthorized access to invoice!', 'danger')
        return redirect(url_for('customer.dashboard'))
        
    year, month = map(int, payment.billing_month.split('-'))
    month_name = datetime(year, month, 1).strftime('%B %Y')
    
    # Generate PDF in memory
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'InvoiceTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=24,
        textColor=colors.HexColor('#2563eb'),
        spaceAfter=15
    )
    
    header_style = ParagraphStyle(
        'InvoiceHeader',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#495057')
    )
    
    cell_style = ParagraphStyle(
        'CellText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12
    )

    cell_bold_style = ParagraphStyle(
        'CellBoldText',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12
    )
    
    story.append(Paragraph("MILK DELIVERY INVOICE", title_style))
    story.append(Spacer(1, 10))
    
    soc_name = cust.society.society_name if cust.society else 'N/A'
    info_data = [
        [
            Paragraph(f"<b>Deliver To:</b><br/>{cust.name}<br/>{cust.address or ''}<br/>Society: {soc_name}<br/>Mobile: {cust.mobile}", header_style),
            Paragraph(f"<b>Invoice No:</b> INV-{payment.id:05d}<br/><b>Billing Month:</b> {month_name}<br/><b>Date Generated:</b> {date.today().strftime('%d-%b-%Y')}<br/><b>Status:</b> {payment.payment_status.upper()}", header_style)
        ]
    ]
    info_table = Table(info_data, colWidths=[260, 260])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 15),
    ]))
    story.append(info_table)
    
    story.append(Paragraph(f"<b>Daily Delivery Logs ({month_name})</b>", styles['Heading3']))
    story.append(Spacer(1, 8))
    
    deliveries = MilkDelivery.query.filter(
        MilkDelivery.customer_id == cust.id,
        db.extract('year', MilkDelivery.delivery_date) == year,
        db.extract('month', MilkDelivery.delivery_date) == month
    ).order_by(MilkDelivery.delivery_date.asc()).all()
    
    # Group deliveries by calendar date for clean, aggregated invoice rows
    deliveries_by_date = {}
    for d in deliveries:
        if d.delivery_date not in deliveries_by_date:
            deliveries_by_date[d.delivery_date] = []
        deliveries_by_date[d.delivery_date].append(d)

    table_content = [[
        Paragraph("<b>Date</b>", cell_bold_style),
        Paragraph("<b>Milkman</b>", cell_bold_style),
        Paragraph("<b>Quantity (L)</b>", cell_bold_style),
        Paragraph("<b>Rate (₹/L)</b>", cell_bold_style),
        Paragraph("<b>Status</b>", cell_bold_style),
        Paragraph("<b>Total Amount (₹)</b>", cell_bold_style)
    ]]
    
    total_qty = 0.0
    for deliv_date in sorted(deliveries_by_date.keys()):
        d_list = deliveries_by_date[deliv_date]
        deliv_items = [d for d in d_list if d.delivery_status == 'Delivered']
        if deliv_items:
            day_qty = sum(d.quantity for d in deliv_items)
            total_qty += day_qty
            day_rate = deliv_items[0].milk_rate if deliv_items[0].milk_rate else (cust.custom_rate or 70.0)
            day_amt = round(day_qty * day_rate, 2)
            milkman_name = deliv_items[0].milkman.name if deliv_items[0].milkman else 'N/A'
            status_str = 'Delivered' if len(deliv_items) == 1 else f"Delivered ({len(deliv_items)}x)"
            table_content.append([
                Paragraph(deliv_date.strftime('%d-%b-%Y'), cell_style),
                Paragraph(milkman_name, cell_style),
                Paragraph(f"{day_qty:.1f} L", cell_style),
                Paragraph(f"₹{day_rate:.2f}", cell_style),
                Paragraph(status_str, cell_style),
                Paragraph(f"₹{day_amt:.2f}", cell_style)
            ])
        else:
            table_content.append([
                Paragraph(deliv_date.strftime('%d-%b-%Y'), cell_style),
                Paragraph(d_list[0].milkman.name if d_list[0].milkman else 'N/A', cell_style),
                Paragraph("-", cell_style),
                Paragraph("-", cell_style),
                Paragraph(d_list[0].delivery_status, cell_style),
                Paragraph("₹0.00", cell_style)
            ])
        
    table_content.append([
        Paragraph("<b>Total Delivered Liters:</b>", cell_bold_style),
        Paragraph("", cell_style),
        Paragraph(f"<b>{total_qty:.1f} L</b>", cell_bold_style),
        Paragraph("", cell_style),
        Paragraph("<b>Grand Total:</b>", cell_bold_style),
        Paragraph(f"<b>₹{payment.amount:.2f}</b>", cell_bold_style)
    ])
    
    deliv_table = Table(table_content, colWidths=[80, 100, 75, 75, 90, 100])
    deliv_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f8f9fa')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#dee2e6')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 6),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#e9ecef')),
    ]))
    story.append(deliv_table)
    story.append(Spacer(1, 20))
    
    pay_details = f"<b>Payment Method:</b> {payment.payment_method or 'N/A'} | <b>Transaction ID:</b> {payment.transaction_id or 'N/A'} | <b>Payment Date:</b> {payment.payment_date.strftime('%d-%b-%Y %H:%M') if payment.payment_date else 'N/A'}"
    story.append(Paragraph(pay_details, header_style))
    story.append(Spacer(1, 30))
    
    story.append(Paragraph("Thank you for choosing our milk delivery service. For inquiries, please contact your milkman.", header_style))
    
    doc.build(story)
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Invoice_{payment.billing_month}_{cust.name.replace(' ', '_')}.pdf",
        mimetype='application/pdf'
    )
