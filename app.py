from flask import Flask, render_template, jsonify, request, session, redirect, url_for, flash, Response
import sqlite3
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import json
import hashlib
import secrets
from functools import wraps
import threading
import time
import logging
from logging.handlers import RotatingFileHandler
from signalwire_swaig.swaig import SWAIG, SWAIGArgument, SWAIGFunctionProperties
from signalwire.rest import Client as SignalWireClient
import schedule
from signalwire.voice_response import VoiceResponse
import sys

# Configure logging
def setup_logging():
    if not os.path.exists('logs'):
        os.makedirs('logs')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            RotatingFileHandler('logs/signalwire.log', maxBytes=10485760, backupCount=5),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger('signalwire')
    logger.setLevel(logging.INFO)
    return logger

logger = setup_logging()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Global SignalWire configuration variables
SIGNALWIRE_PROJECT_ID = None
SIGNALWIRE_TOKEN = None
SIGNALWIRE_SPACE = None
HTTP_USERNAME = None
HTTP_PASSWORD = None
signalwire_client = None
swaig = None

def initialize_signalwire():
    global signalwire_client, swaig, SIGNALWIRE_PROJECT_ID, SIGNALWIRE_TOKEN, SIGNALWIRE_SPACE, HTTP_USERNAME, HTTP_PASSWORD
    if os.path.exists('.env'):
        load_dotenv()
        SIGNALWIRE_PROJECT_ID = os.getenv('SIGNALWIRE_PROJECT_ID')
        SIGNALWIRE_TOKEN = os.getenv('SIGNALWIRE_TOKEN')
        SIGNALWIRE_SPACE = os.getenv('SIGNALWIRE_SPACE')
        HTTP_USERNAME = os.getenv('HTTP_USERNAME')
        HTTP_PASSWORD = os.getenv('HTTP_PASSWORD')
        if all([SIGNALWIRE_PROJECT_ID, SIGNALWIRE_TOKEN, SIGNALWIRE_SPACE, HTTP_USERNAME, HTTP_PASSWORD]):
            try:
                signalwire_client = SignalWireClient(
                    SIGNALWIRE_PROJECT_ID, SIGNALWIRE_TOKEN, signalwire_space_url=SIGNALWIRE_SPACE
                )
                swaig = SWAIG(app, auth=(HTTP_USERNAME, HTTP_PASSWORD))
                logger.info("SignalWire client and SWAIG initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize SignalWire: {str(e)}")
                signalwire_client = None
                swaig = None
        else:
            logger.warning("Missing environment variables; SignalWire not initialized")
    else:
        logger.info("No .env file found; SignalWire not initialized")

def register_swaig_endpoints():
    global swaig
    if not swaig:
        logger.error("Cannot register SWAIG endpoints: SWAIG not initialized")
        return

    @swaig.endpoint(
        "Check the current balance and due date for the customer's account",
        SWAIGFunctionProperties(
            active=True,
            wait_for_fillers=True,
            fillers={
                "default": [
                    "Checking your balance...",
                    "One moment please...",
                    "Retrieving billing information..."
                ]
            }
        ),
        customer_id=SWAIGArgument(type="string", description="The customer's account ID", required=True),
        meta_data=SWAIGArgument(type="object", description="Additional metadata", required=False),
        meta_data_token=SWAIGArgument(type="string", description="Metadata token", required=False)
    )
    def check_balance(customer_id, meta_data=None, meta_data_token=None):
        try:
            db = get_db()
            customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
            if not customer:
                return "I couldn't find your account. Please verify your account number.", []
            billing = db.execute('''
                SELECT * FROM billing 
                WHERE customer_id = ? 
                ORDER BY due_date DESC LIMIT 1
            ''', (customer_id,)).fetchone()
            db.close()
            if billing:
                return f"Your current balance is ${billing['amount']:.2f}, due on {billing['due_date']}.", []
            return "No billing information found for your account.", []
        except Exception as e:
            logger.error(f"Error in check_balance: {str(e)}")
            return "Error checking balance. Please try again later.", []

    @swaig.endpoint(
        "Make a payment on the customer's account",
        SWAIGFunctionProperties(
            active=True,
            wait_for_fillers=True,
            fillers={
                "default": [
                    "Processing your payment...",
                    "One moment while I handle your payment...",
                    "Submitting payment..."
                ]
            }
        ),
        customer_id=SWAIGArgument(type="string", description="The customer's account ID", required=True),
        amount=SWAIGArgument(type="number", description="The amount to pay", required=True),
        payment_method=SWAIGArgument(type="string", description="Payment method", enum=["credit_card", "bank_transfer", "cash"], required=False),
        meta_data=SWAIGArgument(type="object", description="Additional metadata", required=False),
        meta_data_token=SWAIGArgument(type="string", description="Metadata token", required=False)
    )
    def make_payment(customer_id, amount, payment_method=None, meta_data=None, meta_data_token=None):
        try:
            db = get_db()
            customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
            if not customer:
                return "I couldn't find your account. Please verify your account number.", []
            if not amount or amount <= 0:
                return "Please provide a valid payment amount.", []
            # Insert payment record
            db.execute('''
                INSERT INTO payments (customer_id, amount, payment_date, payment_method, status, transaction_id)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?, 'pending', ?)
            ''', (customer_id, amount, payment_method or 'phone', secrets.token_hex(16)))
            # Update balance
            current_balance = db.execute('SELECT amount FROM billing WHERE customer_id = ? ORDER BY due_date DESC LIMIT 1', (customer_id,)).fetchone()
            if current_balance:
                new_balance = current_balance['amount'] - amount
                db.execute('UPDATE billing SET amount = ? WHERE id = (SELECT id FROM billing WHERE customer_id = ? ORDER BY due_date DESC LIMIT 1)', 
                           (new_balance, customer_id))
            db.commit()
            db.close()
            return f"Payment of ${amount:.2f} initiated. Confirmation text incoming.", []
        except Exception as e:
            logger.error(f"Error in make_payment: {str(e)}")
            return "Error processing payment. Please try again.", []

    @swaig.endpoint(
        "Check the current status of the customer's modem",
        SWAIGFunctionProperties(
            active=True,
            wait_for_fillers=True,
            fillers={
                "default": [
                    "Checking modem status...",
                    "Retrieving modem information...",
                    "Please wait..."
                ]
            }
        ),
        customer_id=SWAIGArgument(type="string", description="The customer's account ID", required=True),
        meta_data=SWAIGArgument(type="object", description="Additional metadata", required=False),
        meta_data_token=SWAIGArgument(type="string", description="Metadata token", required=False)
    )
    def check_modem_status(customer_id, meta_data=None, meta_data_token=None):
        try:
            db = get_db()
            customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
            if not customer:
                return "I couldn't find your account. Please verify your account number.", []
            modem = db.execute('SELECT * FROM modems WHERE customer_id = ?', (customer_id,)).fetchone()
            db.close()
            if modem:
                return f"Your modem is {modem['status']}. MAC: {modem['mac_address']}.", []
            return "No modem information found for your account.", []
        except Exception as e:
            logger.error(f"Error in check_modem_status: {str(e)}")
            return "Error checking modem status.", []

    @swaig.endpoint(
        "Reboot the customer's modem",
        SWAIGFunctionProperties(
            active=True,
            wait_for_fillers=True,
            fillers={
                "default": [
                    "Rebooting your modem...",
                    "Initiating modem restart...",
                    "Please wait while I reboot..."
                ]
            }
        ),
        customer_id=SWAIGArgument(type="string", description="The customer's account ID", required=True),
        meta_data=SWAIGArgument(type="object", description="Additional metadata", required=False),
        meta_data_token=SWAIGArgument(type="string", description="Metadata token", required=False)
    )
    def reboot_modem(customer_id, meta_data=None, meta_data_token=None):
        try:
            db = get_db()
            customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
            if not customer:
                return "I couldn't find your account. Please verify your account number.", []
            modem = db.execute('SELECT * FROM modems WHERE customer_id = ?', (customer_id,)).fetchone()
            if not modem:
                return "No modem information found for your account.", []
            thread = threading.Thread(target=simulate_modem_reboot, args=(customer_id,))
            thread.daemon = True
            thread.start()
            db.close()
            return "Modem reboot initiated. This will take about 30 seconds.", []
        except Exception as e:
            logger.error(f"Error in reboot_modem: {str(e)}")
            return "Error rebooting modem.", []

    @swaig.endpoint(
        "Schedule a service appointment",
        SWAIGFunctionProperties(
            active=True,
            wait_for_fillers=True,
            fillers={
                "default": [
                    "Scheduling your appointment...",
                    "Arranging your service...",
                    "Please wait while I book it..."
                ]
            }
        ),
        customer_id=SWAIGArgument(type="string", description="The customer's account ID", required=True),
        type=SWAIGArgument(type="string", description="Type of appointment", enum=["installation", "repair", "upgrade", "modem_swap"], required=True),
        date=SWAIGArgument(type="string", description="Preferred date (YYYY-MM-DD)", required=True),
        meta_data=SWAIGArgument(type="object", description="Additional metadata", required=False),
        meta_data_token=SWAIGArgument(type="string", description="Metadata token", required=False)
    )
    def schedule_appointment(customer_id, type, date, meta_data=None, meta_data_token=None):
        try:
            db = get_db()
            customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
            if not customer:
                return "I couldn't find your account. Please verify your account number.", []
            if type not in ["installation", "repair", "upgrade", "modem_swap"]:
                return "Invalid appointment type. Use: installation, repair, upgrade, or modem_swap.", []
            appointment_date = datetime.strptime(date, '%Y-%m-%d')
            if appointment_date < datetime.now():
                return "Please select a future date.", []
            existing = db.execute('''
                SELECT * FROM appointments 
                WHERE customer_id = ? 
                AND date(start_time) = date(?)
            ''', (customer_id, appointment_date)).fetchone()
            if existing:
                return f"You already have an appointment on {date}.", []
            db.execute('''
                INSERT INTO appointments (customer_id, type, status, start_time, end_time)
                VALUES (?, ?, 'scheduled', ?, ?)
            ''', (customer_id, type, appointment_date.strftime('%Y-%m-%d 09:00:00'), appointment_date.strftime('%Y-%m-%d 11:00:00')))
            db.commit()
            db.close()
            return f"{type} appointment scheduled for {date}. Confirmation text incoming.", []
        except ValueError:
            return "Invalid date format. Use YYYY-MM-DD.", []
        except Exception as e:
            logger.error(f"Error in schedule_appointment: {str(e)}")
            return "Error scheduling appointment.", []

    @swaig.endpoint(
        "Schedule a modem swap appointment",
        SWAIGFunctionProperties(
            active=True,
            wait_for_fillers=True,
            fillers={
                "default": [
                    "Scheduling modem swap...",
                    "Arranging your modem replacement...",
                    "Please wait..."
                ]
            }
        ),
        customer_id=SWAIGArgument(type="string", description="The customer's account ID", required=True),
        date=SWAIGArgument(type="string", description="Preferred date (YYYY-MM-DD)", required=True),
        meta_data=SWAIGArgument(type="object", description="Additional metadata", required=False),
        meta_data_token=SWAIGArgument(type="string", description="Metadata token", required=False)
    )
    def swap_modem(customer_id, date, meta_data=None, meta_data_token=None):
        try:
            db = get_db()
            customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
            if not customer:
                return "I couldn't find your account. Please verify your account number.", []
            modem = db.execute('SELECT * FROM modems WHERE customer_id = ?', (customer_id,)).fetchone()
            if not modem:
                return "No modem information found.", []
            appointment_date = datetime.strptime(date, '%Y-%m-%d')
            if appointment_date < datetime.now():
                return "Please select a future date.", []
            existing = db.execute('''
                SELECT * FROM appointments 
                WHERE customer_id = ? 
                AND date(start_time) = date(?)
            ''', (customer_id, appointment_date)).fetchone()
            if existing:
                return f"You already have an appointment on {date}.", []
            db.execute('''
                INSERT INTO appointments (customer_id, type, status, start_time, end_time, notes)
                VALUES (?, 'modem_swap', 'scheduled', ?, ?, ?)
            ''', (customer_id, appointment_date.strftime('%Y-%m-%d 10:00:00'), appointment_date.strftime('%Y-%m-%d 12:00:00'), f"Current MAC: {modem['mac_address']}"))
            db.commit()
            db.close()
            return f"Modem swap scheduled for {date}. A technician will assist you.", []
        except ValueError:
            return "Invalid date format. Use YYYY-MM-DD.", []
        except Exception as e:
            logger.error(f"Error in swap_modem: {str(e)}")
            return "Error scheduling modem swap.", []

# Initialize and register endpoints
initialize_signalwire()
if swaig:
    register_swaig_endpoints()

# Environment Configuration
HOST = '0.0.0.0'
PORT = 8080
DEBUG = os.getenv('FLASK_ENV') == 'development'

@app.route('/', methods=['GET'])
def index():
    if os.path.exists('.env') and all([SIGNALWIRE_PROJECT_ID, SIGNALWIRE_TOKEN, SIGNALWIRE_SPACE]):
        return redirect(url_for('dashboard') if 'customer_id' in session else url_for('login'))
    return render_template('populate.html')

@app.route('/generate', methods=['POST'])
def generate_env():
    try:
        env_content = f"""HTTP_USERNAME={request.form['httpUsername']}
HTTP_PASSWORD={request.form['httpPassword']}
SIGNALWIRE_PROJECT_ID={request.form['signalwireProjectId']}
SIGNALWIRE_TOKEN={request.form['signalwireToken']}
SIGNALWIRE_SPACE={request.form['signalwireSpace']}
PORT=8080
"""
        with open('.env', 'w') as f:
            f.write(env_content)
        logger.info("Generated .env file")
        flash('Configuration saved! Restart the app to apply changes.', 'success')
    except Exception as e:
        logger.error(f"Error generating .env: {str(e)}")
        flash('Failed to save configuration.', 'danger')
    return redirect(url_for('index'))

@app.route('/swaig', methods=['GET', 'POST'])
def swaig_endpoint():
    if not swaig:
        return jsonify({"error": "SWAIG not initialized"}), 503
    resp_body = swaig.handle_request(request)
    return Response(resp_body, mimetype='application/json')

def get_db():
    db = sqlite3.connect('zen_cable.db')
    db.row_factory = sqlite3.Row
    return db

def init_db_if_needed():
    try:
        db = get_db()
        if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='customers'").fetchone():
            from init_db import init_db
            from init_test_data import init_test_data
            print("Initializing database...")
            init_db()
            init_test_data()
            print("Database initialized!")
        db.close()
    except Exception as e:
        print(f"Error initializing database: {e}")
        raise

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'customer_id' not in session:
            flash('Please log in.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def hash_password(password):
    salt = secrets.token_hex(16)
    hash_obj = hashlib.sha256((password + salt).encode())
    return hash_obj.hexdigest(), salt

def verify_password(password, stored_hash, salt):
    hash_obj = hashlib.sha256((password + salt).encode())
    return hash_obj.hexdigest() == stored_hash

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        remember = request.form.get('remember', False)
        db = get_db()
        user = db.execute('SELECT * FROM customers WHERE email = ?', (email,)).fetchone()
        db.close()
        if user and verify_password(password, user['password_hash'], user['password_salt']):
            session['customer_id'] = user['id']
            if remember:
                session.permanent = True
            flash('Welcome back!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        db = get_db()
        user = db.execute('SELECT * FROM customers WHERE email = ?', (email,)).fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            expiry = datetime.utcnow() + timedelta(hours=1)
            db.execute('INSERT INTO password_resets (customer_id, token, expiry) VALUES (?, ?, ?)',
                      (user['id'], token, expiry))
            db.commit()
            flash('Reset instructions sent to your email.', 'success')
        else:
            flash('Email not found.', 'danger')
        db.close()
    return render_template('forgot_password.html')

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (session['customer_id'],)).fetchone()
    services = db.execute('''
        SELECT s.* FROM services s
        JOIN customer_services cs ON s.id = cs.service_id
        WHERE cs.customer_id = ? AND cs.status = 'active'
    ''', (session['customer_id'],)).fetchall()
    modem = db.execute('SELECT * FROM modems WHERE customer_id = ?', (session['customer_id'],)).fetchone()
    billing = db.execute('''
        SELECT * FROM billing 
        WHERE customer_id = ? 
        ORDER BY due_date DESC LIMIT 1
    ''', (session['customer_id'],)).fetchone()
    db.close()
    return render_template('dashboard.html', customer=customer, services=services, modem=modem, billing=billing)

@app.route('/api/modem/status', methods=['GET'])
@login_required
def get_modem_status():
    db = get_db()
    modem = db.execute('SELECT status FROM modems WHERE customer_id = ?', (session['customer_id'],)).fetchone()
    db.close()
    if modem:
        return jsonify({'status': modem['status']})
    return jsonify({'error': 'Modem not found'}), 404

@app.route('/api/billing/balance', methods=['GET'])
@login_required
def get_balance():
    db = get_db()
    billing = db.execute('''
        SELECT amount FROM billing 
        WHERE customer_id = ? 
        ORDER BY due_date DESC LIMIT 1
    ''', (session['customer_id'],)).fetchone()
    db.close()
    if billing:
        return jsonify({'balance': billing['amount']})
    return jsonify({'error': 'No billing information found'}), 404

@app.route('/api/appointments', methods=['GET'])
@login_required
def get_appointments():
    start = request.args.get('start')
    end = request.args.get('end')
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(max(1, request.args.get('per_page', 10, type=int)), 100)
    status = request.args.get('status')
    type_filter = request.args.get('type')
    technician = request.args.get('technician')
    priority = request.args.get('priority')
    sort_by = request.args.get('sort_by', 'start_time')
    sort_order = request.args.get('sort_order', 'desc')
    include_history = request.args.get('include_history', 'false').lower() == 'true'
    include_reminders = request.args.get('include_reminders', 'false').lower() == 'true'

    if not start or not end:
        return jsonify({'error': 'Start and end dates required'}), 400
    try:
        start_date = datetime.strptime(start, '%Y-%m-%d')
        end_date = datetime.strptime(end, '%Y-%m-%d')
        if start_date > end_date or (end_date - start_date).days > 365:
            return jsonify({'error': 'Invalid date range'}), 400
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    valid_statuses = ['scheduled', 'completed', 'cancelled', 'pending']
    valid_types = ['installation', 'repair', 'upgrade', 'modem_swap']
    valid_priorities = ['low', 'medium', 'high', 'urgent']
    valid_sort_fields = ['start_time', 'end_time', 'type', 'status', 'priority']
    valid_sort_orders = ['asc', 'desc']

    if status and status not in valid_statuses:
        return jsonify({'error': f'Invalid status: {valid_statuses}'}), 400
    if type_filter and type_filter not in valid_types:
        return jsonify({'error': f'Invalid type: {valid_types}'}), 400
    if priority and priority not in valid_priorities:
        return jsonify({'error': f'Invalid priority: {valid_priorities}'}), 400
    if sort_by not in valid_sort_fields:
        return jsonify({'error': f'Invalid sort field: {valid_sort_fields}'}), 400
    if sort_order not in valid_sort_orders:
        return jsonify({'error': f'Invalid sort order: {valid_sort_orders}'}), 400

    db = get_db()
    query = '''
        SELECT a.*, c.name as customer_name, c.phone as customer_phone, t.name as technician_name
        FROM appointments a
        LEFT JOIN customers c ON a.customer_id = c.id
        LEFT JOIN technicians t ON a.technician_id = t.id
        WHERE a.customer_id = ? AND a.start_time BETWEEN ? AND ?
    '''
    params = [session['customer_id'], start, end]
    if status:
        query += ' AND a.status = ?'
        params.append(status)
    if type_filter:
        query += ' AND a.type = ?'
        params.append(type_filter)
    if technician:
        query += ' AND t.name LIKE ?'
        params.append(f'%{technician}%')
    if priority:
        query += ' AND a.priority = ?'
        params.append(priority)
    query += f' ORDER BY a.{sort_by} {sort_order}'
    total = db.execute(f"SELECT COUNT(*) FROM ({query})", params).fetchone()[0]
    query += ' LIMIT ? OFFSET ?'
    params.extend([per_page, (page - 1) * per_page])
    appointments = db.execute(query, params).fetchall()
    result = [dict(appt) for appt in appointments]

    if include_history:
        for appt in result:
            history = db.execute('SELECT * FROM appointment_history WHERE appointment_id = ? ORDER BY created_at DESC', (appt['id'],)).fetchall()
            appt['history'] = [dict(h) for h in history]
    if include_reminders:
        for appt in result:
            reminders = db.execute('SELECT * FROM appointment_reminders WHERE appointment_id = ? ORDER BY sent_at DESC', (appt['id'],)).fetchall()
            appt['reminders'] = [dict(r) for r in reminders]
    db.close()
    return jsonify({
        'appointments': result,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'total_pages': (total + per_page - 1) // per_page}
    })

@app.route('/api/appointments', methods=['POST'])
@login_required
def create_appointment():
    data = request.json or {}
    required_fields = ['type', 'date']
    if not all(field in data for field in required_fields):
        return jsonify({'error': 'Missing required fields'}), 400
    valid_types = ['installation', 'repair', 'upgrade', 'modem_swap']
    if data['type'] not in valid_types:
        return jsonify({'error': f'Invalid type: {valid_types}'}), 400
    try:
        appointment_date = datetime.strptime(data['date'], '%Y-%m-%d')
        if appointment_date < datetime.now():
            return jsonify({'error': 'Cannot schedule past appointments'}), 400
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    db = get_db()
    if db.execute('SELECT * FROM appointments WHERE customer_id = ? AND date(start_time) = date(?)', 
                  (session['customer_id'], data['date'])).fetchone():
        return jsonify({'error': f'Appointment already scheduled for {data["date"]}'}), 400
    try:
        cursor = db.execute('''
            INSERT INTO appointments (customer_id, type, status, start_time, end_time, notes, priority, technician_id, location)
            VALUES (?, ?, 'scheduled', ?, ?, ?, ?, ?, ?)
        ''', (session['customer_id'], data['type'], appointment_date.strftime('%Y-%m-%d 09:00:00'), 
              appointment_date.strftime('%Y-%m-%d 11:00:00'), data.get('notes', ''), data.get('priority', 'medium'), 
              data.get('technician_id'), data.get('location', '')))
        db.commit()
        appointment = db.execute('SELECT * FROM appointments WHERE id = ?', (cursor.lastrowid,)).fetchone()
        log_appointment_history(appointment['id'], 'created', {'type': appointment['type'], 'date': appointment['start_time'], 
                                                              'notes': appointment['notes'], 'priority': appointment['priority']})
        schedule_reminders(dict(appointment))
        db.close()
        return jsonify(dict(appointment)), 201
    except Exception as e:
        logger.error(f"Error creating appointment: {str(e)}")
        return jsonify({'error': 'Failed to create appointment'}), 500

@app.route('/api/appointments/<int:appointment_id>', methods=['PUT'])
@login_required
def update_appointment(appointment_id):
    data = request.json or {}
    db = get_db()
    appointment = db.execute('SELECT * FROM appointments WHERE id = ? AND customer_id = ?', 
                            (appointment_id, session['customer_id'])).fetchone()
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    if 'status' in data and data['status'] not in ['scheduled', 'completed', 'cancelled', 'pending']:
        return jsonify({'error': 'Invalid status'}), 400
    if 'date' in data:
        try:
            new_date = datetime.strptime(data['date'], '%Y-%m-%d')
            if new_date < datetime.now():
                return jsonify({'error': 'Cannot schedule past appointments'}), 400
            if db.execute('SELECT * FROM appointments WHERE customer_id = ? AND date(start_time) = date(?) AND id != ?', 
                          (session['customer_id'], data['date'], appointment_id)).fetchone():
                return jsonify({'error': f'Another appointment exists on {data["date"]}'}), 400
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    update_fields, params = [], []
    if 'status' in data:
        update_fields.append('status = ?')
        params.append(data['status'])
    if 'date' in data:
        update_fields.extend(['start_time = ?', 'end_time = ?'])
        params.extend([new_date.strftime('%Y-%m-%d 09:00:00'), new_date.strftime('%Y-%m-%d 11:00:00')])
    if 'notes' in data:
        update_fields.append('notes = ?')
        params.append(data['notes'])
    if 'priority' in data and data['priority'] in ['low', 'medium', 'high', 'urgent']:
        update_fields.append('priority = ?')
        params.append(data['priority'])
    if 'technician_id' in data:
        update_fields.append('technician_id = ?')
        params.append(data['technician_id'])
    if 'location' in data:
        update_fields.append('location = ?')
        params.append(data['location'])
    if update_fields:
        try:
            db.execute(f'UPDATE appointments SET {", ".join(update_fields)} WHERE id = ? AND customer_id = ?', 
                       params + [appointment_id, session['customer_id']])
            db.commit()
            log_appointment_history(appointment_id, 'updated', {k: v for k, v in data.items() 
                                                               if k in ['status', 'date', 'notes', 'priority', 'technician_id', 'location']})
            if 'date' in data:
                updated = db.execute('SELECT * FROM appointments WHERE id = ?', (appointment_id,)).fetchone()
                schedule_reminders(dict(updated))
            updated = db.execute('SELECT * FROM appointments WHERE id = ?', (appointment_id,)).fetchone()
            db.close()
            return jsonify(dict(updated))
        except Exception as e:
            logger.error(f"Error updating appointment: {str(e)}")
            return jsonify({'error': 'Failed to update appointment'}), 500
    db.close()
    return jsonify({'message': 'No changes made'}), 200

@app.route('/api/appointments/<int:appointment_id>', methods=['DELETE'])
@login_required
def delete_appointment(appointment_id):
    db = get_db()
    appointment = db.execute('SELECT * FROM appointments WHERE id = ? AND customer_id = ?', 
                            (appointment_id, session['customer_id'])).fetchone()
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    if datetime.strptime(appointment['start_time'], '%Y-%m-%d %H:%M:%S') < datetime.now():
        return jsonify({'error': 'Cannot delete past appointments'}), 400
    try:
        log_appointment_history(appointment_id, 'deleted', {'type': appointment['type'], 'date': appointment['start_time'], 
                                                           'reason': request.json.get('reason', 'No reason provided') if request.json else 'No reason provided'})
        db.execute('DELETE FROM appointments WHERE id = ?', (appointment_id,))
        db.commit()
        db.close()
        return '', 204
    except Exception as e:
        logger.error(f"Error deleting appointment: {str(e)}")
        return jsonify({'error': 'Failed to delete appointment'}), 500

@app.route('/api/appointments/<int:appointment_id>/history', methods=['GET'])
@login_required
def get_appointment_history(appointment_id):
    db = get_db()
    if not db.execute('SELECT * FROM appointments WHERE id = ? AND customer_id = ?', 
                      (appointment_id, session['customer_id'])).fetchone():
        return jsonify({'error': 'Appointment not found'}), 404
    history = db.execute('SELECT * FROM appointment_history WHERE appointment_id = ? ORDER BY created_at DESC', 
                        (appointment_id,)).fetchall()
    db.close()
    return jsonify([dict(h) for h in history])

@app.route('/api/appointments/<int:appointment_id>/reminders', methods=['GET'])
@login_required
def get_appointment_reminders(appointment_id):
    db = get_db()
    if not db.execute('SELECT * FROM appointments WHERE id = ? AND customer_id = ?', 
                      (appointment_id, session['customer_id'])).fetchone():
        return jsonify({'error': 'Appointment not found'}), 404
    reminders = db.execute('SELECT * FROM appointment_reminders WHERE appointment_id = ? ORDER BY sent_at DESC', 
                          (appointment_id,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in reminders])

@app.route('/api/appointments/<int:appointment_id>/reminders', methods=['POST'])
@login_required
def send_appointment_reminder_now(appointment_id):
    if not signalwire_client:
        return jsonify({'error': 'Service unavailable'}), 503
    db = get_db()
    appointment = db.execute('SELECT * FROM appointments WHERE id = ? AND customer_id = ?', 
                            (appointment_id, session['customer_id'])).fetchone()
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    reminder_type = request.json.get('type', 'sms') if request.json else 'sms'
    if reminder_type not in ['sms', 'call']:
        return jsonify({'error': 'Invalid reminder type'}), 400
    success = send_appointment_reminder(dict(appointment), reminder_type)
    db.close()
    return jsonify({'message': f'{reminder_type.upper()} reminder sent'}) if success else jsonify({'error': 'Failed to send reminder'}), 500

@app.route('/reminder_call/<int:appointment_id>', methods=['POST'])
def reminder_call(appointment_id):
    if not signalwire_client:
        return jsonify({'error': 'Service unavailable'}), 503
    db = get_db()
    appointment = db.execute('SELECT * FROM appointments WHERE id = ?', (appointment_id,)).fetchone()
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (appointment['customer_id'],)).fetchone()
    if not customer:
        return jsonify({'error': 'Customer not found'}), 404
    appointment_time = datetime.strptime(appointment['start_time'], '%Y-%m-%d %H:%M:%S')
    formatted_time = appointment_time.strftime('%B %d, %Y at %I:%M %p')
    response = VoiceResponse()
    response.say(f"""
        Hello, this is a reminder for your {appointment['type']} appointment on {formatted_time}.
        Call 1-800-ZEN-CABLE to reschedule if needed. Thank you for choosing Zen Cable.
    """)
    db.close()
    return str(response)

@app.route('/api/modem/status', methods=['POST'])
@login_required
def modem_status():
    db = get_db()
    if request.method == 'POST':
        status = request.json.get('status') if request.json else None
        if status not in ['online', 'offline', 'rebooting']:
            return jsonify({'error': 'Invalid status'}), 400
        try:
            if status == 'rebooting':
                thread = threading.Thread(target=simulate_modem_reboot, args=(session['customer_id'],))
                thread.daemon = True
                thread.start()
            else:
                db.execute('UPDATE modems SET status = ?, last_seen = CURRENT_TIMESTAMP WHERE customer_id = ?', 
                          (status, session['customer_id']))
                db.commit()
        except Exception as e:
            logger.error(f"Error updating modem status: {str(e)}")
            return jsonify({'error': 'Failed to update modem status'}), 500
    modem = db.execute('SELECT * FROM modems WHERE customer_id = ?', (session['customer_id'],)).fetchone()
    db.close()
    return jsonify(dict(modem)) if modem else jsonify({'error': 'Modem not found'}), 404

def simulate_modem_reboot(customer_id):
    db = get_db()
    try:
        db.execute('UPDATE modems SET status = "rebooting", last_seen = CURRENT_TIMESTAMP WHERE customer_id = ?', (customer_id,))
        db.commit()
        time.sleep(30)
        db.execute('UPDATE modems SET status = "online", last_seen = CURRENT_TIMESTAMP WHERE customer_id = ?', (customer_id,))
        db.commit()
    except Exception as e:
        logger.error(f"Error in modem reboot simulation: {str(e)}")
    finally:
        db.close()

def send_appointment_reminder(appointment, reminder_type='sms'):
    if not signalwire_client:
        return False
    db = get_db()
    try:
        customer = db.execute('SELECT * FROM customers WHERE id = ?', (appointment['customer_id'],)).fetchone()
        if not customer:
            return False
        appointment_time = datetime.strptime(appointment['start_time'], '%Y-%m-%d %H:%M:%S')
        formatted_time = appointment_time.strftime('%B %d, %Y at %I:%M %p')
        message = f"Reminder: Your {appointment['type']} appointment is on {formatted_time}. Call 1-800-ZEN-CABLE to reschedule."
        if reminder_type == 'sms':
            signalwire_client.messages.create(to=customer['phone'], from_='+1800ZENCABLE', body=message)
        else:
            signalwire_client.calls.create(to=customer['phone'], from_='+1800Z AgrawalENCABLE', 
                                         url=f"{request.host_url}reminder_call/{appointment['id']}")
        db.execute('INSERT INTO appointment_reminders (appointment_id, reminder_type, sent_at, status) VALUES (?, ?, CURRENT_TIMESTAMP, "sent")', 
                  (appointment['id'], reminder_type))
        db.commit()
        return True
    except Exception as e:
        logger.error(f"Error sending {reminder_type} reminder: {str(e)}")
        return False
    finally:
        db.close()

def schedule_reminders(appointment):
    if not signalwire_client:
        return
    appointment_time = datetime.strptime(appointment['start_time'], '%Y-%m-%d %H:%M:%S')
    sms_time = appointment_time - timedelta(hours=24)
    if sms_time > datetime.now():
        schedule.every().day.at(sms_time.strftime('%H:%M')).do(send_appointment_reminder, appointment, 'sms')
    call_time = appointment_time - timedelta(hours=1)
    if call_time > datetime.now():
        schedule.every().day.at(call_time.strftime('%H:%M')).do(send_appointment_reminder, appointment, 'call')

def log_appointment_history(appointment_id, action, details):
    db = get_db()
    try:
        db.execute('INSERT INTO appointment_history (appointment_id, action, details, created_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)', 
                  (appointment_id, action, json.dumps(details)))
        db.commit()
    except Exception as e:
        logger.error(f"Error logging history: {str(e)}")
    finally:
        db.close()

if __name__ == '__main__':
    print("\n" + "="*50)
    print("Zen Cable Customer Portal")
    print("="*50)
    print("\nTest Credentials:\nEmail: test@example.com\nPassword: password123")
    print(f"\nStarting server on {HOST}:{PORT} (Debug: {DEBUG})")
    init_db_if_needed()
    print(f"SWAIG endpoints: {list(swaig.functions.keys()) if swaig else 'None'}")
    print(f"Listening on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=DEBUG)