import os
from datetime import datetime, date
from models.models import db, SmsLog, HolidayMessage, Customer, AdminSetting
from config import Config

# Default sender configuration
DEFAULT_SENDER_NUMBER = '6367335889'
DAILY_SMS_LIMIT = 50

def get_sender_number():
    """Retrieve sender mobile number from AdminSettings or Config/env, default to 6367335889."""
    try:
        setting = AdminSetting.query.filter_by(key='SMS_SENDER_NUMBER').first()
        if setting and setting.value:
            return setting.value.strip()
    except Exception:
        pass
    return getattr(Config, 'SMS_SENDER_NUMBER', DEFAULT_SENDER_NUMBER)

def get_daily_sms_stats(target_date=None):
    """
    Returns the SMS statistics for the specified date (defaults to today).
    - total_sent_today: Count of successfully dispatched SMS
    - remaining_today: Remaining SMS quota (max 50/day)
    - failed_today: Failed SMS count
    - limit_reached: Boolean flag indicating if 50 SMS limit reached
    """
    if target_date is None:
        target_date = date.today()

    try:
        # Count only sent/successful messages against the daily quota
        sent_count = SmsLog.query.filter(
            SmsLog.sent_date == target_date,
            SmsLog.status == 'Sent'
        ).count()

        failed_count = SmsLog.query.filter(
            SmsLog.sent_date == target_date,
            SmsLog.status.in_(['Failed', 'Limit Exceeded'])
        ).count()
    except Exception as e:
        print(f"[SMS STATS ERROR] {e}")
        sent_count = 0
        failed_count = 0

    remaining = max(0, DAILY_SMS_LIMIT - sent_count)
    limit_reached = sent_count >= DAILY_SMS_LIMIT

    return {
        'total_sent_today': sent_count,
        'remaining_today': remaining,
        'failed_today': failed_count,
        'daily_limit': DAILY_SMS_LIMIT,
        'limit_reached': limit_reached,
        'sender_number': get_sender_number(),
        'date': target_date.strftime('%Y-%m-%d')
    }

def send_single_sms(recipient_mobile, recipient_name, message, customer_id=None, holiday_message_id=None):
    """
    Sends an SMS to a single recipient with 50 SMS/day limit protection and logs the history.
    """
    today = date.today()
    stats = get_daily_sms_stats(today)
    sender = get_sender_number()

    if stats['limit_reached'] or stats['remaining_today'] <= 0:
        # Log limit exceeded entry
        sms_log = SmsLog(
            holiday_message_id=holiday_message_id,
            customer_id=customer_id,
            recipient_mobile=recipient_mobile,
            recipient_name=recipient_name,
            sender_number=sender,
            message=message,
            status='Limit Exceeded',
            error_message='Daily SMS limit reached. No more messages can be sent today.',
            sent_date=today,
            timestamp=datetime.now()
        )
        db.session.add(sms_log)
        db.session.commit()
        return {
            'success': False,
            'status': 'Limit Exceeded',
            'message': 'Daily SMS limit reached. No more messages can be sent today.',
            'log_id': sms_log.id
        }

    # Dispatch via SMS Gateway API if configured
    sms_api_key = os.environ.get('SMS_API_KEY', '')
    sms_api_url = os.environ.get('SMS_API_URL', '')
    sent_successfully = True
    error_msg = None

    if sms_api_key and sms_api_url:
        try:
            import urllib.request
            import urllib.parse
            import json

            # Sample provider integration
            post_data = urllib.parse.urlencode({
                'sender': sender,
                'number': recipient_mobile,
                'message': message,
                'apikey': sms_api_key
            }).encode('utf-8')

            req = urllib.request.Request(sms_api_url, data=post_data, headers={'User-Agent': 'DairyFlow/2.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                resp_text = response.read().decode('utf-8')
                print(f"[SMS API SUCCESS] Response: {resp_text}")
        except Exception as ex:
            print(f"[SMS API ERROR] {ex}")
            # If gateway returns error, log it
            error_msg = str(ex)
            sent_successfully = False

    # Create SMS log entry
    status = 'Sent' if sent_successfully else 'Failed'
    sms_log = SmsLog(
        holiday_message_id=holiday_message_id,
        customer_id=customer_id,
        recipient_mobile=recipient_mobile,
        recipient_name=recipient_name,
        sender_number=sender,
        message=message,
        status=status,
        error_message=error_msg,
        sent_date=today,
        timestamp=datetime.now()
    )
    db.session.add(sms_log)
    db.session.commit()

    return {
        'success': sent_successfully,
        'status': status,
        'error_message': error_msg,
        'log_id': sms_log.id
    }

def broadcast_holiday_sms(milkman_id, holiday_date, custom_message=None):
    """
    Sends holiday broadcast message to all active customers.
    Enforces maximum 50 SMS per day limit.
    """
    today = date.today()
    stats = get_daily_sms_stats(today)
    sender = get_sender_number()

    if stats['limit_reached'] or stats['remaining_today'] <= 0:
        return {
            'success': False,
            'message': 'Daily SMS limit reached. No more messages can be sent today.',
            'total_customers': 0,
            'successful_count': 0,
            'failed_count': 0,
            'limit_reached': True,
            'stats': stats
        }

    # Format default message if not provided
    holiday_date_str = holiday_date.strftime('%d-%b-%Y')
    if not custom_message or not custom_message.strip():
        message = (
            f"નમસ્તે ગ્રાહક મિત્રો, જણાવવાનું કે {holiday_date_str} ના રોજ રજા હોવાથી દૂધ વિતરણ બંધ રહેશે. "
            f"નિયમિત વિતરણ બીજા દિવસથી રાબેતા મુજબ શરૂ થશે. - ડેરીફ્લો (સંપર્ક: {sender})"
        )
    else:
        message = custom_message.strip()

    # Create HolidayMessage header record
    holiday_record = HolidayMessage(
        milkman_id=milkman_id,
        holiday_date=holiday_date,
        message=message,
        sender_number=sender,
        total_sent=0,
        total_failed=0,
        status='In Progress',
        created_at=datetime.now()
    )
    db.session.add(holiday_record)
    db.session.commit()

    # Query active customers
    customers = Customer.query.filter_by(status='Active').all()
    successful = 0
    failed = 0
    limit_hit = False

    for cust in customers:
        # Check quota live for each iteration
        current_stats = get_daily_sms_stats(today)
        if current_stats['remaining_today'] <= 0:
            limit_hit = True
            # Log limit exceeded for remaining customers
            log = SmsLog(
                holiday_message_id=holiday_record.id,
                customer_id=cust.id,
                recipient_mobile=cust.mobile,
                recipient_name=cust.name,
                sender_number=sender,
                message=message,
                status='Limit Exceeded',
                error_message='Daily SMS limit (50/day) reached',
                sent_date=today,
                timestamp=datetime.now()
            )
            db.session.add(log)
            failed += 1
            continue

        res = send_single_sms(
            recipient_mobile=cust.mobile,
            recipient_name=cust.name,
            message=message,
            customer_id=cust.id,
            holiday_message_id=holiday_record.id
        )

        if res['success']:
            successful += 1
        else:
            failed += 1

    # Update HolidayMessage summary
    holiday_record.total_sent = successful
    holiday_record.total_failed = failed
    holiday_record.status = 'Sent' if failed == 0 else ('Partial' if successful > 0 else 'Failed')
    db.session.commit()

    updated_stats = get_daily_sms_stats(today)

    return {
        'success': successful > 0,
        'message': f"Holiday message sent to {successful} customers." + (" (Daily SMS limit reached)" if limit_hit else ""),
        'holiday_message_id': holiday_record.id,
        'total_customers': len(customers),
        'successful_count': successful,
        'failed_count': failed,
        'limit_reached': limit_hit or updated_stats['limit_reached'],
        'stats': updated_stats
    }
