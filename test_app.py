import sys
import os
import unittest
from datetime import date, datetime, timedelta

# Adjust path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app import create_app
from models.models import (
    db, User, Society, Customer, Milkman, MilkDelivery, 
    MilkBill, Payment, HolidayMessage, SmsLog, MilkRate, AdminSetting, Notification
)
from services.sms_service import get_daily_sms_stats, send_single_sms, broadcast_holiday_sms, get_sender_number

class TestMilkManagementSystem(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_1_normalized_database_tables_count(self):
        """Verify that normalized database contains at least 7 properly related tables."""
        engine = db.engine
        inspector = db.inspect(engine)
        table_names = inspector.get_table_names()
        print(f"Discovered {len(table_names)} tables: {table_names}")
        self.assertGreaterEqual(len(table_names), 7)
        
        required_tables = [
            'users', 'societies', 'customers', 'milkmans',
            'milk_deliveries', 'milk_bills', 'payments'
        ]
        for tbl in required_tables:
            self.assertIn(tbl, table_names, f"Missing required normalized table: {tbl}")

    def test_2_sms_sender_configuration_and_stats(self):
        """Verify sender number is 6367335889 and SMS daily stats calculate accurately."""
        sender = get_sender_number()
        self.assertEqual(sender, '6367335889')

        today = date.today()
        stats = get_daily_sms_stats(today)
        self.assertEqual(stats['daily_limit'], 50)
        self.assertEqual(stats['sender_number'], '6367335889')
        self.assertIn('total_sent_today', stats)
        self.assertIn('remaining_today', stats)
        self.assertLessEqual(stats['total_sent_today'] + stats['remaining_today'], 50)

    def test_3_sms_daily_limit_protection_50_cap(self):
        """Verify that attempting to send more than 50 SMS in one day is strictly prevented."""
        today = date.today()
        # Count existing logs for today
        existing_sent = SmsLog.query.filter_by(sent_date=today, status='Sent').count()
        needed_to_fill = 50 - existing_sent

        # Create mock logs to fill up to 50 if needed
        dummy_logs = []
        for i in range(needed_to_fill):
            log = SmsLog(
                recipient_mobile=f"990000{i:04d}",
                recipient_name=f"Test Customer {i}",
                sender_number='6367335889',
                message="Test Daily Limit Quota",
                status='Sent',
                sent_date=today,
                timestamp=datetime.now()
            )
            dummy_logs.append(log)
            db.session.add(log)
        db.session.commit()

        stats = get_daily_sms_stats(today)
        self.assertEqual(stats['total_sent_today'], 50)
        self.assertEqual(stats['remaining_today'], 0)
        self.assertTrue(stats['limit_reached'])

        # Attempt to send 51st SMS
        result = send_single_sms(
            recipient_mobile="9876543210",
            recipient_name="Test Recipient",
            message="This 51st SMS should be blocked!"
        )
        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'Limit Exceeded')
        self.assertIn("Daily SMS limit reached", result['message'])

        # Clean up added dummy logs so other tests have quota
        for l in dummy_logs:
            db.session.delete(l)
        db.session.commit()

    def test_4_milkman_holiday_broadcast_api(self):
        """Test milkman holiday SMS broadcast endpoint."""
        # Find active milkman
        milkman = Milkman.query.first()
        self.assertIsNotNone(milkman)

        # Login as milkman
        with self.client.session_transaction() as sess:
            sess['user_id'] = milkman.id
            sess['username'] = milkman.name
            sess['role'] = 'milkman'

        # Send holiday broadcast
        resp = self.client.post('/milkman/send_holiday_message', json={
            'holiday_date': (date.today() + timedelta(days=2)).strftime('%Y-%m-%d'),
            'message': 'Test Holiday message from automated test suite'
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('success', data)
        self.assertIn('total_customers', data)
        self.assertIn('stats', data)

    def test_5_customer_payment_details_and_cod_qr(self):
        """Test dedicated customer payment details page, COD, and QR payment."""
        cust = Customer.query.first()
        self.assertIsNotNone(cust)

        # Find or create a pending payment
        payment = Payment.query.filter_by(customer_id=cust.id, payment_status='Pending').first()
        if not payment:
            payment = Payment(
                customer_id=cust.id,
                billing_month="2026-08",
                amount=1500.0,
                payment_status='Pending'
            )
            db.session.add(payment)
            db.session.commit()
        self.assertIsNotNone(payment)

        # Login as customer
        with self.client.session_transaction() as sess:
            sess['user_id'] = cust.id
            sess['username'] = cust.name
            sess['role'] = 'customer'

        # 1. Access dedicated payment details page
        resp = self.client.get(f'/customer/payment/{payment.id}')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn(cust.name, html)
        self.assertIn("Scan QR Code to Pay", html)
        self.assertIn("Cash on Delivery / Cash Payment", html)

        # 2. Test QR Payment Submit
        pay_resp = self.client.post(f'/customer/pay/{payment.id}', json={
            'payment_method': 'UPI QR',
            'transaction_id': 'TEST-UTR-99887766'
        })
        self.assertEqual(pay_resp.status_code, 200)
        pay_data = pay_resp.get_json()
        self.assertTrue(pay_data['success'])
        self.assertIn('invoice_url', pay_data)

        # 3. Test PDF invoice download
        inv_resp = self.client.get(f'/customer/invoice/{payment.id}')
        self.assertEqual(inv_resp.status_code, 200)
        self.assertEqual(inv_resp.content_type, 'application/pdf')

    def test_6_admin_views_and_sms_logs(self):
        """Test admin dashboard, SMS logs audit, and settings page."""
        admin_user = User.query.filter_by(role='admin').first()
        self.assertIsNotNone(admin_user)

        with self.client.session_transaction() as sess:
            sess['user_id'] = admin_user.id
            sess['username'] = admin_user.name
            sess['role'] = 'admin'

        # Dashboard
        dash_resp = self.client.get('/admin/dashboard')
        self.assertEqual(dash_resp.status_code, 200)

        # SMS Logs audit
        logs_resp = self.client.get('/admin/sms_logs')
        self.assertEqual(logs_resp.status_code, 200)
        logs_html = logs_resp.get_data(as_text=True)
        self.assertIn("SMS Logs & Delivery Audit History", logs_html)
        self.assertIn("6367335889", logs_html)

        # Settings
        sett_resp = self.client.get('/admin/settings')
        self.assertEqual(sett_resp.status_code, 200)
        sett_html = sett_resp.get_data(as_text=True)
        self.assertIn("6367335889", sett_html)

    def test_7_forgot_password_milkman_and_customer(self):
        """Test forgot password flow for both Milkman and Customer."""
        # 1. Milkman forgot password
        milkman = Milkman.query.first()
        self.assertIsNotNone(milkman)
        resp = self.client.post('/forgot-password', json={
            'role': 'milkman',
            'mobile': milkman.mobile,
            'new_password': 'newmilkmanpass123',
            'confirm_password': 'newmilkmanpass123'
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        # Verify password updated in DB
        db.session.refresh(milkman.user)
        self.assertTrue(milkman.check_password('newmilkmanpass123'))

        # Reset milkman password back
        milkman.set_password('milkman123')
        db.session.commit()

        # 2. Customer forgot password
        cust = Customer.query.first()
        self.assertIsNotNone(cust)
        resp = self.client.post('/forgot-password', json={
            'role': 'customer',
            'mobile': cust.mobile,
            'name': cust.name,
            'new_password': 'newcustpass123',
            'confirm_password': 'newcustpass123'
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        db.session.refresh(cust.user)
        self.assertTrue(cust.check_password('newcustpass123'))

        # 3. Test validation errors
        # Mismatched passwords
        bad_resp = self.client.post('/forgot-password', json={
            'role': 'customer',
            'mobile': cust.mobile,
            'new_password': 'pass1',
            'confirm_password': 'pass2'
        })
        self.assertEqual(bad_resp.status_code, 400)

        # Non-existing user
        bad_resp2 = self.client.post('/forgot-password', json={
            'role': 'milkman',
            'mobile': '0000000000',
            'new_password': 'pass1234',
            'confirm_password': 'pass1234'
        })
        self.assertEqual(bad_resp2.status_code, 404)

        # Reset customer password back
        cust.set_password('customer123')
        db.session.commit()

    def test_8_customer_login_phone_and_name(self):
        """Test customer login using phone number + registered name."""
        cust = Customer.query.first()
        self.assertIsNotNone(cust)

        # Valid login with phone + correct name
        resp = self.client.post('/login', data={
            'role': 'customer',
            'mobile': cust.mobile,
            'customer_name': cust.name,
            'password': 'customer123'
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn(cust.name, html)
        self.assertIn("Customer Portal", html)

        # Logout
        self.client.get('/logout')

        # Invalid login with mismatched name
        bad_resp = self.client.post('/login', data={
            'role': 'customer',
            'mobile': cust.mobile,
            'customer_name': 'Completely Wrong Name',
            'password': 'customer123'
        }, follow_redirects=True)
        bad_html = bad_resp.get_data(as_text=True)
        self.assertIn("does not match", bad_html)

    def test_9_milkman_customer_management(self):
        """Test milkman customer management: Add, View, Edit, Update, Search, Delete."""
        milkman = Milkman.query.first()
        soc = Society.query.first()
        self.assertIsNotNone(milkman)
        self.assertIsNotNone(soc)

        with self.client.session_transaction() as sess:
            sess['user_id'] = milkman.id
            sess['username'] = milkman.name
            sess['role'] = 'milkman'

        # 1. View Customers page
        view_resp = self.client.get('/milkman/customers')
        self.assertEqual(view_resp.status_code, 200)
        self.assertIn("Customer Management", view_resp.get_data(as_text=True))

        # 2. Add New Customer
        test_mobile = "9900998877"
        add_resp = self.client.post('/milkman/customers', data={
            'name': 'Test New Customer',
            'mobile': test_mobile,
            'address': 'Test Address 101',
            'society_id': soc.id,
            'default_qty': '1.5',
            'custom_rate': '75.0',
            'password': 'testpass123'
        }, follow_redirects=True)
        self.assertEqual(add_resp.status_code, 200)

        new_cust = Customer.query.join(User).filter(User.mobile_number == test_mobile).first()
        self.assertIsNotNone(new_cust)
        self.assertEqual(new_cust.name, 'Test New Customer')
        self.assertEqual(new_cust.default_quantity, 1.5)
        self.assertEqual(new_cust.custom_rate, 75.0)

        # 3. View Customer JSON (for edit modal pre-population)
        get_resp = self.client.get(f'/milkman/customers/get/{new_cust.id}')
        self.assertEqual(get_resp.status_code, 200)
        c_data = get_resp.get_json()
        self.assertEqual(c_data['name'], 'Test New Customer')
        self.assertEqual(c_data['mobile'], test_mobile)

        # 4. Edit / Update Customer
        edit_resp = self.client.post(f'/milkman/customers/edit/{new_cust.id}', data={
            'name': 'Test Updated Customer',
            'mobile': test_mobile,
            'address': 'Updated Address 202',
            'society_id': soc.id,
            'default_qty': '2.0',
            'custom_rate': '78.0',
            'status': 'Active'
        }, follow_redirects=True)
        self.assertEqual(edit_resp.status_code, 200)

        db.session.refresh(new_cust)
        db.session.refresh(new_cust.user)
        self.assertEqual(new_cust.name, 'Test Updated Customer')
        self.assertEqual(new_cust.address, 'Updated Address 202')
        self.assertEqual(new_cust.default_quantity, 2.0)
        self.assertEqual(new_cust.custom_rate, 78.0)

        # 5. Search Customer
        search_resp = self.client.get('/milkman/customers?q=Updated')
        self.assertEqual(search_resp.status_code, 200)
        self.assertIn('Test Updated Customer', search_resp.get_data(as_text=True))

        # 6. Delete Customer
        del_resp = self.client.post(f'/milkman/customers/delete/{new_cust.id}', follow_redirects=True)
        self.assertEqual(del_resp.status_code, 200)
        deleted_cust = db.session.get(Customer, new_cust.id)
        self.assertIsNone(deleted_cust)

    def test_10_multiple_deliveries_same_day_aggregation_and_date_change(self):
        """Test multiple deliveries on the same calendar date aggregated into 1 calendar day row."""
        milkman = Milkman.query.first()
        cust = Customer.query.first()
        self.assertIsNotNone(milkman)
        self.assertIsNotNone(cust)

        with self.client.session_transaction() as sess:
            sess['user_id'] = milkman.id
            sess['username'] = milkman.name
            sess['role'] = 'milkman'

        target_date_str = "2026-09-01"
        target_date = date(2026, 9, 1)

        # Remove any existing delivery on that test date for clean state
        MilkDelivery.query.filter_by(customer_id=cust.id, delivery_date=target_date).delete()
        db.session.commit()

        # 1. Milkman adds delivery 1 on 01 Sep (1.0 L in morning)
        d1_resp = self.client.post('/milkman/delivery', data={
            'customer_id': cust.id,
            'quantity_ml': 1000,
            'status': 'Delivered',
            'delivery_date': target_date_str,
            'mode': 'add'
        })
        self.assertEqual(d1_resp.status_code, 200)
        d1_json = d1_resp.get_json()
        self.assertTrue(d1_json['success'])
        self.assertEqual(d1_json['date'], target_date_str)

        # 2. Milkman adds delivery 2 on 01 Sep (1.0 L in evening)
        d2_resp = self.client.post('/milkman/delivery', data={
            'customer_id': cust.id,
            'quantity_ml': 1000,
            'status': 'Delivered',
            'delivery_date': target_date_str,
            'mode': 'add'
        })
        self.assertEqual(d2_resp.status_code, 200)
        d2_json = d2_resp.get_json()
        self.assertTrue(d2_json['success'])
        self.assertEqual(d2_json['day_total_qty'], 2.0)
        self.assertEqual(d2_json['delivery_count'], 2)

        # 3. Check customer dashboard / report for September 2026
        with self.client.session_transaction() as sess:
            sess['user_id'] = cust.id
            sess['username'] = cust.name
            sess['role'] = 'customer'

        dash_resp = self.client.get('/customer/dashboard?month=9&year=2026')
        self.assertEqual(dash_resp.status_code, 200)
        html = dash_resp.get_data(as_text=True)

        # Verify that 01 September has 2.0 L total
        self.assertIn("2 deliveries", html)

        # Clean up test deliveries
        MilkDelivery.query.filter_by(customer_id=cust.id, delivery_date=target_date).delete()
        db.session.commit()

    def test_11_customer_report_button_and_page(self):
        """Test that customer Report page loads correctly with all required metrics."""
        cust = Customer.query.first()
        self.assertIsNotNone(cust)

        with self.client.session_transaction() as sess:
            sess['user_id'] = cust.id
            sess['username'] = cust.name
            sess['role'] = 'customer'

        # 1. Access /customer/report
        rep_resp = self.client.get('/customer/report?month=6&year=2026')
        self.assertEqual(rep_resp.status_code, 200)
        html = rep_resp.get_data(as_text=True)

        self.assertIn("Customer Milk Consumption Report", html)
        self.assertIn("Daily Total Quantity", html)
        self.assertIn("Milk Rate", html)
        self.assertIn("Daily Amount", html)
        self.assertIn("Monthly Total Quantity", html)
        self.assertIn("Monthly Total Amount", html)
        self.assertIn("Status:", html)

        # 2. Access /customer/history alias
        hist_resp = self.client.get('/customer/history?month=6&year=2026')
        self.assertEqual(hist_resp.status_code, 200)

    def test_12_admin_milkman_edit_and_delete(self):
        """Test Admin Milkman Edit and Delete flow."""
        admin_user = User.query.filter_by(role='admin').first()
        self.assertIsNotNone(admin_user)
        with self.client.session_transaction() as sess:
            sess['user_id'] = admin_user.id
            sess['username'] = admin_user.name
            sess['role'] = 'admin'

        # 1. View milkmen list
        resp = self.client.get('/admin/milkmen')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Ramesh Bhai", html)
        self.assertIn("openEditMilkmanModal", html)
        self.assertIn("openDeleteMilkmanModal", html)

        # 2. Add a temporary test milkman
        add_resp = self.client.post('/admin/milkmen', data={
            'name': 'Test Milkman Temp',
            'mobile': '9998887771',
            'password': 'testpassword123'
        }, follow_redirects=True)
        self.assertEqual(add_resp.status_code, 200)
        temp_user = User.query.filter_by(mobile_number='9998887771').first()
        self.assertIsNotNone(temp_user)
        temp_milkman = Milkman.query.filter_by(user_id=temp_user.id).first()
        self.assertIsNotNone(temp_milkman)

        # 3. Edit the milkman
        edit_resp = self.client.post(f'/admin/milkmen/edit/{temp_milkman.id}', data={
            'name': 'Test Milkman Updated',
            'mobile': '9998887772',
            'status': 'Suspended',
            'password': 'newpassword456'
        }, follow_redirects=True)
        self.assertEqual(edit_resp.status_code, 200)
        db.session.refresh(temp_user)
        db.session.refresh(temp_milkman)
        self.assertEqual(temp_user.name, 'Test Milkman Updated')
        self.assertEqual(temp_user.mobile_number, '9998887772')
        self.assertEqual(temp_milkman.status, 'Suspended')
        self.assertTrue(temp_user.check_password('newpassword456'))

        # 4. Delete the milkman
        del_resp = self.client.post(f'/admin/milkmen/delete/{temp_milkman.id}', follow_redirects=True)
        self.assertEqual(del_resp.status_code, 200)
        del_user = User.query.filter_by(mobile_number='9998887772').first()
        self.assertIsNone(del_user)
        del_milkman = db.session.get(Milkman, temp_milkman.id)
        self.assertIsNone(del_milkman)

    def test_13_customer_milkman_allocation_model_and_relationship(self):
        """Verify database-driven allocation schema and relationships between Customer and Milkman."""
        ramesh = Milkman.query.first()
        self.assertIsNotNone(ramesh, "Ramesh Bhai milkman should exist")

        cust = Customer.query.first()
        self.assertIsNotNone(cust, "A customer should exist")

        # Assign customer to Ramesh
        cust.milkman_id = ramesh.id
        db.session.commit()

        # Verify relationships
        db.session.refresh(cust)
        db.session.refresh(ramesh)
        self.assertEqual(cust.allocated_milkman.id, ramesh.id)
        self.assertEqual(cust.milkman.id, ramesh.id)
        self.assertEqual(cust.milkman.name, ramesh.name)
        self.assertIn(cust, ramesh.allocated_customers)

        # Verify no duplicate milkman records created
        initial_milkman_count = Milkman.query.count()
        cust2 = Customer.query.offset(1).first()
        if cust2:
            cust2.milkman_id = ramesh.id
            db.session.commit()
            self.assertEqual(Milkman.query.count(), initial_milkman_count)

    def test_14_admin_allocate_customer_and_customer_dashboard_visibility(self):
        """Test complete flow: Admin allocates customer -> Customer logs in -> Customer sees allocated milkman."""
        # 1. Admin logs in
        admin_user = User.query.filter_by(role='admin').first()
        with self.client.session_transaction() as sess:
            sess['user_id'] = admin_user.id
            sess['username'] = admin_user.name
            sess['role'] = 'admin'

        # Get Ramesh Bhai (Milkman 1) and Suresh Bhai (Milkman 2)
        ramesh = Milkman.query.filter(Milkman.user.has(name='Ramesh Bhai')).first()
        if not ramesh:
            ramesh = Milkman.query.first()
        self.assertIsNotNone(ramesh)

        suresh = Milkman.query.filter(Milkman.id != ramesh.id).first()
        self.assertIsNotNone(suresh)

        # Get two distinct customers
        cust_a = Customer.query.first()
        cust_b = Customer.query.filter(Customer.id != cust_a.id).first()
        self.assertIsNotNone(cust_a)
        self.assertIsNotNone(cust_b)

        # Admin allocates Cust A to Ramesh Bhai
        resp = self.client.post('/admin/customers/allocate', data={
            'customer_id': cust_a.id,
            'milkman_id': ramesh.id
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Ensure Cust B is allocated to Suresh Bhai (NOT Ramesh)
        self.client.post('/admin/customers/allocate', data={
            'customer_id': cust_b.id,
            'milkman_id': suresh.id
        }, follow_redirects=True)

        # 2. Cust A logs in -> Cust A must see Ramesh Bhai's Name, Mobile, and Allocated status
        with self.client.session_transaction() as sess:
            sess['user_id'] = cust_a.id
            sess['username'] = cust_a.name
            sess['role'] = 'customer'

        dash_a = self.client.get('/customer/dashboard')
        self.assertEqual(dash_a.status_code, 200)
        html_a = dash_a.get_data(as_text=True)

        self.assertIn("My Milkman", html_a)
        self.assertIn("Allocated to You", html_a)
        self.assertIn(ramesh.name, html_a)
        self.assertIn(ramesh.mobile, html_a)

        # 3. Cust B logs in -> Cust B must NOT see Ramesh Bhai's details, but Suresh Bhai
        with self.client.session_transaction() as sess:
            sess['user_id'] = cust_b.id
            sess['username'] = cust_b.name
            sess['role'] = 'customer'

        dash_b = self.client.get('/customer/dashboard')
        self.assertEqual(dash_b.status_code, 200)
        html_b = dash_b.get_data(as_text=True)

        self.assertIn(suresh.name, html_b)
        self.assertIn(suresh.mobile, html_b)
        # Verify Cust B does not see Ramesh Bhai
        self.assertNotIn(ramesh.mobile, html_b)

    def test_15_allocation_change_and_unallocation_flow(self):
        """Test allocation update: Admin/Milkman changes allocation -> Customer immediately sees updated milkman.
           When allocation is removed -> Customer no longer sees milkman details."""
        ramesh = Milkman.query.first()
        suresh = Milkman.query.filter(Milkman.id != ramesh.id).first()
        cust = Customer.query.first()

        # Step 1: Allocate to Ramesh
        cust.milkman_id = ramesh.id
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess['user_id'] = cust.id
            sess['username'] = cust.name
            sess['role'] = 'customer'

        dash1 = self.client.get('/customer/dashboard')
        self.assertEqual(dash1.status_code, 200)
        html1 = dash1.get_data(as_text=True)
        self.assertIn(ramesh.name, html1)
        self.assertIn(ramesh.mobile, html1)

        # Step 2: Admin changes allocation to Suresh
        admin_user = User.query.filter_by(role='admin').first()
        with self.client.session_transaction() as sess:
            sess['user_id'] = admin_user.id
            sess['username'] = admin_user.name
            sess['role'] = 'admin'

        change_resp = self.client.post('/admin/customers/allocate', data={
            'customer_id': cust.id,
            'milkman_id': suresh.id
        }, follow_redirects=True)
        self.assertEqual(change_resp.status_code, 200)

        # Customer logs back in -> Customer must see Suresh Bhai, NOT Ramesh Bhai
        with self.client.session_transaction() as sess:
            sess['user_id'] = cust.id
            sess['username'] = cust.name
            sess['role'] = 'customer'

        dash2 = self.client.get('/customer/dashboard')
        self.assertEqual(dash2.status_code, 200)
        html2 = dash2.get_data(as_text=True)
        self.assertIn(suresh.name, html2)
        self.assertIn(suresh.mobile, html2)
        self.assertNotIn(ramesh.mobile, html2)

        # Step 3: Admin removes customer from allocation
        with self.client.session_transaction() as sess:
            sess['user_id'] = admin_user.id
            sess['username'] = admin_user.name
            sess['role'] = 'admin'

        unalloc_resp = self.client.post('/admin/customers/allocate', data={
            'customer_id': cust.id,
            'milkman_id': ''
        }, follow_redirects=True)
        self.assertEqual(unalloc_resp.status_code, 200)

        # Customer logs back in -> Customer sees unallocated state, neither milkman details
        with self.client.session_transaction() as sess:
            sess['user_id'] = cust.id
            sess['username'] = cust.name
            sess['role'] = 'customer'

        dash3 = self.client.get('/customer/dashboard')
        self.assertEqual(dash3.status_code, 200)
        html3 = dash3.get_data(as_text=True)
        self.assertIn("Not Allocated", html3)
        self.assertNotIn("Allocated to You", html3)
        self.assertNotIn(ramesh.mobile, html3)
        self.assertNotIn(suresh.mobile, html3)

    def test_16_milkman_allocates_customer_directly(self):
        """Test milkman self-allocating customer via /milkman/customers/allocate."""
        milkman = Milkman.query.first()
        cust = Customer.query.first()

        # Ensure unallocated first
        cust.milkman_id = None
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess['user_id'] = milkman.id
            sess['username'] = milkman.name
            sess['role'] = 'milkman'

        # Milkman allocates customer to himself
        alloc_resp = self.client.post('/milkman/customers/allocate', data={
            'customer_id': cust.id,
            'action': 'allocate'
        }, follow_redirects=True)
        self.assertEqual(alloc_resp.status_code, 200)

        db.session.refresh(cust)
        self.assertEqual(cust.milkman_id, milkman.id)

        # Customer checks dashboard
        with self.client.session_transaction() as sess:
            sess['user_id'] = cust.id
            sess['username'] = cust.name
            sess['role'] = 'customer'

        dash = self.client.get('/customer/dashboard')
        self.assertEqual(dash.status_code, 200)
        html = dash.get_data(as_text=True)
        self.assertIn("Allocated to You", html)
        self.assertIn(milkman.name, html)
        self.assertIn(milkman.mobile, html)

if __name__ == '__main__':
    unittest.main()


