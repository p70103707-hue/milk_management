from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from datetime import date
from models.models import db, User, Milkman, Customer, Society

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        role = session.get('role')
        if role == 'admin':
            return redirect(url_for('admin.dashboard'))
        elif role == 'milkman':
            return redirect(url_for('milkman.dashboard'))
        elif role == 'customer':
            return redirect(url_for('customer.dashboard'))

    if request.method == 'POST':
        role = request.form.get('role')
        
        if role == 'admin':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            admin_user = User.query.filter(
                User.role == 'admin',
                (User.mobile_number == username) | (User.name == username)
            ).first()
            
            if admin_user and admin_user.check_password(password):
                session['user_id'] = admin_user.id
                session['username'] = admin_user.name
                session['role'] = 'admin'
                flash('Welcome Admin!', 'success')
                return redirect(url_for('admin.dashboard'))
            else:
                flash('Invalid Admin Credentials', 'danger')
                
        elif role == 'milkman':
            user_id = request.form.get('mobile', '').strip()
            password = request.form.get('password', '')
            
            milkman = None
            if user_id:
                try:
                    if user_id.isdigit():
                        val = int(user_id)
                        # Match Milkman ID or User Mobile Number
                        milkman = Milkman.query.join(User).filter(
                            (Milkman.id == val) | 
                            (User.mobile_number == user_id)
                        ).first()
                    else:
                        milkman = Milkman.query.join(User).filter(User.mobile_number == user_id).first()
                except Exception:
                    pass
            
            if milkman and milkman.check_password(password):
                if milkman.status != 'Active' or (milkman.user and milkman.user.status != 'Active'):
                    flash('Your account is suspended. Contact admin.', 'warning')
                    return redirect(url_for('auth.login'))
                session['user_id'] = milkman.id
                session['username'] = milkman.name
                session['role'] = 'milkman'
                flash(f'Welcome, {milkman.name}!', 'success')
                return redirect(url_for('milkman.dashboard'))
            else:
                flash('Invalid Milkman Credentials. If you forgot your password, use the "Forgot Password?" option.', 'danger')
                
        elif role == 'customer':
            user_mobile = request.form.get('mobile', '').strip()
            customer_name = (request.form.get('customer_name') or request.form.get('name') or '').strip()
            password = request.form.get('password', '').strip()
            
            if not user_mobile:
                flash('Please enter your registered mobile number.', 'danger')
                return redirect(url_for('auth.login'))

            customer = None
            try:
                if user_mobile.isdigit():
                    val = int(user_mobile)
                    customer = Customer.query.join(User).filter(
                        (User.mobile_number == user_mobile) |
                        (Customer.id == val)
                    ).first()
                else:
                    customer = Customer.query.join(User).filter(
                        (User.mobile_number == user_mobile) | 
                        (Customer.address.ilike(f"%{user_mobile}%"))
                    ).first()
            except Exception:
                pass
            
            if not customer:
                flash('Customer not found with this Mobile Number!', 'danger')
                return redirect(url_for('auth.login'))
                
            if customer.status != 'Active' or (customer.user and customer.user.status != 'Active'):
                flash('Your account is inactive. Please contact the milkman or admin.', 'warning')
                return redirect(url_for('auth.login'))

            # Validate Customer Name if provided
            if customer_name:
                registered_name = (customer.name or '').strip().lower()
                entered_name = customer_name.lower()
                # Check for match (full match or name token match)
                name_tokens_entered = set(entered_name.split())
                name_tokens_reg = set(registered_name.split())
                if registered_name != entered_name and not (name_tokens_entered.issubset(name_tokens_reg) or entered_name in registered_name or registered_name in entered_name):
                    flash(f'Entered name "{customer_name}" does not match our registered records for this mobile number.', 'danger')
                    return redirect(url_for('auth.login'))

            # Validate Password if entered
            if password:
                if not customer.check_password(password):
                    flash('Invalid Password. If you forgot your password, click "Forgot Password?" to reset it.', 'danger')
                    return redirect(url_for('auth.login'))
            elif not customer_name:
                # If neither password nor name provided, require at least password
                flash('Please enter your password or registered name to sign in.', 'danger')
                return redirect(url_for('auth.login'))

            session['user_id'] = customer.id
            session['username'] = customer.name
            session['role'] = 'customer'
            flash(f'Welcome, {customer.name}!', 'success')
            return redirect(url_for('customer.dashboard'))
                    
    return render_template('login.html')


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        role = data.get('role', '').strip().lower()
        mobile = data.get('mobile', '').strip()
        customer_name = data.get('name', '').strip()
        new_password = data.get('new_password', '')
        confirm_password = data.get('confirm_password', '')

        # Validations
        if not role or role not in ['milkman', 'customer']:
            msg = 'Please select a valid role (Milkman or Customer).'
            if request.is_json:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'danger')
            return redirect(url_for('auth.forgot_password'))

        if not mobile:
            msg = 'Registered mobile number is required.'
            if request.is_json:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'danger')
            return redirect(url_for('auth.forgot_password'))

        if not new_password or len(new_password) < 4:
            msg = 'New password must be at least 4 characters long.'
            if request.is_json:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'danger')
            return redirect(url_for('auth.forgot_password'))

        if new_password != confirm_password:
            msg = 'New password and confirm password do not match.'
            if request.is_json:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'danger')
            return redirect(url_for('auth.forgot_password'))

        target_user = None

        if role == 'milkman':
            milkman = Milkman.query.join(User).filter(
                (User.mobile_number == mobile) | 
                (Milkman.id == (int(mobile) if mobile.isdigit() else -1))
            ).first()

            if not milkman or not milkman.user:
                msg = f'No registered Milkman found with Mobile / ID: {mobile}.'
                if request.is_json:
                    return jsonify({'success': False, 'message': msg}), 404
                flash(msg, 'danger')
                return redirect(url_for('auth.forgot_password'))

            target_user = milkman.user

        elif role == 'customer':
            customer = Customer.query.join(User).filter(
                (User.mobile_number == mobile) | 
                (Customer.id == (int(mobile) if mobile.isdigit() else -1))
            ).first()

            if not customer or not customer.user:
                msg = f'No registered Customer found with Mobile / ID: {mobile}.'
                if request.is_json:
                    return jsonify({'success': False, 'message': msg}), 404
                flash(msg, 'danger')
                return redirect(url_for('auth.forgot_password'))

            # If name is provided, validate that name matches
            if customer_name:
                reg_name = customer.name.strip().lower()
                ent_name = customer_name.strip().lower()
                name_tokens_entered = set(ent_name.split())
                name_tokens_reg = set(reg_name.split())
                if reg_name != ent_name and not (name_tokens_entered.issubset(name_tokens_reg) or ent_name in reg_name or reg_name in ent_name):
                    msg = f'Customer name "{customer_name}" does not match our records for mobile {mobile}.'
                    if request.is_json:
                        return jsonify({'success': False, 'message': msg}), 400
                    flash(msg, 'danger')
                    return redirect(url_for('auth.forgot_password'))

            target_user = customer.user

        # Save new password securely
        try:
            target_user.set_password(new_password)
            db.session.commit()
            success_msg = f'Password for {target_user.name} ({role.title()}) has been reset successfully! You can now log in.'
            if request.is_json:
                return jsonify({'success': True, 'message': success_msg})
            flash(success_msg, 'success')
            return redirect(url_for('auth.login'))
        except Exception as e:
            db.session.rollback()
            err_msg = f'Error resetting password: {str(e)}'
            if request.is_json:
                return jsonify({'success': False, 'message': err_msg}), 500
            flash(err_msg, 'danger')
            return redirect(url_for('auth.forgot_password'))

    return render_template('login.html', show_forgot_modal=True)


@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully!', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        role = session.get('role')
        if role == 'admin':
            return redirect(url_for('admin.dashboard'))
        elif role == 'milkman':
            return redirect(url_for('milkman.dashboard'))
        elif role == 'customer':
            return redirect(url_for('customer.dashboard'))

    societies = Society.query.all()

    if request.method == 'POST':
        role = request.form.get('role')
        name = request.form.get('name', '').strip()
        mobile = request.form.get('mobile', '').strip()
        password = request.form.get('password', '')

        if not role or not name or not mobile or not password:
            flash('All fields are required!', 'danger')
            return redirect(url_for('auth.register'))

        if role == 'customer':
            address = request.form.get('address', '').strip()
            society_id = request.form.get('society_id')
            if not address or not society_id:
                flash('Address and Society are required for customers!', 'danger')
                return redirect(url_for('auth.register'))

            existing_user = User.query.filter_by(mobile_number=mobile).first()
            if existing_user:
                flash('Mobile number is already registered!', 'danger')
                return redirect(url_for('auth.register'))

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

                new_customer = Customer(
                    user_id=user.id,
                    address=address,
                    society_id=int(society_id),
                    default_quantity=1.0,
                    custom_rate=None,
                    status='Active',
                    start_date=date.today()
                )
                db.session.add(new_customer)
                db.session.commit()
                flash(f'Registration successful! Your Customer ID is {new_customer.id}. You can sign in with your ID or Mobile Number.', 'success')
                return redirect(url_for('auth.login'))
            except Exception as e:
                db.session.rollback()
                flash(f'Registration failed: {str(e)}', 'danger')
                return redirect(url_for('auth.register'))

        elif role == 'milkman':
            existing_user = User.query.filter_by(mobile_number=mobile).first()
            if existing_user:
                flash('Mobile number is already registered!', 'danger')
                return redirect(url_for('auth.register'))

            try:
                user = User(
                    name=name,
                    mobile_number=mobile,
                    role='milkman',
                    status='Active'
                )
                user.set_password(password)
                db.session.add(user)
                db.session.flush()

                new_milkman = Milkman(
                    user_id=user.id,
                    status='Active'
                )
                db.session.add(new_milkman)
                db.session.commit()
                flash(f'Registration successful! Your Milkman ID is {new_milkman.id}. You can sign in with your ID or Mobile Number.', 'success')
                return redirect(url_for('auth.login'))
            except Exception as e:
                db.session.rollback()
                flash(f'Registration failed: {str(e)}', 'danger')
                return redirect(url_for('auth.register'))

    return render_template('register.html', societies=societies)
