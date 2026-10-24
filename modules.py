# Alias proxy file for models.models (in case modules.py is imported or referenced)
from models.models import (
    db, User, Admin, Society, Customer, Milkman, 
    MilkDelivery, MilkBill, Payment, HolidayMessage, 
    SmsLog, MilkRate, AdminSetting, Notification
)

__all__ = [
    'db', 'User', 'Admin', 'Society', 'Customer', 'Milkman',
    'MilkDelivery', 'MilkBill', 'Payment', 'HolidayMessage',
    'SmsLog', 'MilkRate', 'AdminSetting', 'Notification'
]
