from flask import Flask, render_template, jsonify, request, session, redirect, url_for, flash
import sqlite3
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import json
import hashlib
import secrets
from functools import wraps
import requests
import threading
import time
import logging
from logging.handlers import RotatingFileHandler
from signalwire_swaig.swaig import SWAIG, SWAIGArgument
from signalwire.rest import Client as SignalWireClient
import schedule
import pytz
from signalwire.voice_response import VoiceResponse
import sys

# Configure logging
def setup_logging():
    # Create logs directory if it doesn't exist
    if not os.path.exists('logs'):
        os.makedirs('logs')

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            RotatingFileHandler('logs/signalwire.log', maxBytes=10485760, backupCount=5),  # 10MB per file, keep 5 files
            logging.StreamHandler()
        ]
    )

    # Create logger for SignalWire
    logger = logging.getLogger('signalwire')
    logger.setLevel(logging.INFO)
    return logger

# Initialize logger
logger = setup_logging()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Check if .env exists; if not, we'll serve the form
if os.path.exists('.env'):
    load_dotenv()

# SignalWire Configuration
SIGNALWIRE_PROJECT_ID = os.getenv('SIGNALWIRE_PROJECT_ID')
SIGNALWIRE_TOKEN = os.getenv('SIGNALWIRE_TOKEN')
SIGNALWIRE_SPACE = os.getenv('SIGNALWIRE_SPACE')
HTTP_USERNAME = os.getenv('HTTP_USERNAME')
HTTP_PASSWORD = os.getenv('HTTP_PASSWORD')

# Environment Configuration
HOST = '0.0.0.0'  # Always bind to all interfaces
PORT = int(os.getenv('PORT', 5000))  # Use port 5000 by default
DEBUG = os.getenv('FLASK_ENV') == 'development'

# Initialize SWAIG
swaig = SWAIG(
    app,
    auth=(HTTP_USERNAME, HTTP_PASSWORD)
)

# Initialize SignalWire client
signalwire_client = SignalWireClient(SIGNALWIRE_PROJECT_ID, SIGNALWIRE_TOKEN, signalwire_space_url=SIGNALWIRE_SPACE)

# Route to serve populate.html if .env doesn't exist
@app.route('/', methods=['GET'])
def index():
    if os.path.exists('.env'):
        # If .env exists, proceed to existing logic
        if 'customer_id' in session:
            return redirect(url_for('dashboard'))
        return redirect(url_for('login'))
    else:
        # If .env doesn't exist, serve the form
        return render_template('populate.html')

# Route to handle form submission and create .env
@app.route('/generate', methods=['POST'])
def generate_env():
    # Get form data
    http_username = request.form['httpUsername']
    http_password = request.form['httpPassword']
    signalwire_project_id = request.form['signalwireProjectId']
    signalwire_token = request.form['signalwireToken']
    signalwire_space = request.form['signalwireSpace']

    # Generate .env content
    env_content = f"""HTTP_USERNAME={http_username}
HTTP_PASSWORD={http_password}
SIGNALWIRE_PROJECT_ID={signalwire_project_id}
SIGNALWIRE_TOKEN={signalwire_token}
SIGNALWIRE_SPACE={signalwire_space}
"""

    # Write to .env file
    try:
        with open('.env', 'w') as f:
            f.write(env_content)
        # Reload environment variables
        load_dotenv(override=True)
        # Update global variables
        global SIGNALWIRE_PROJECT_ID, SIGNALWIRE_TOKEN, SIGNALWIRE_SPACE, HTTP_USERNAME, HTTP_PASSWORD
        SIGNALWIRE_PROJECT_ID = os.getenv('SIGNALWIRE_PROJECT_ID')
        SIGNALWIRE_TOKEN = os.getenv('SIGNALWIRE_TOKEN')
        SIGNALWIRE_SPACE = os.getenv('SIGNALWIRE_SPACE')
        HTTP_USERNAME = os.getenv('HTTP_USERNAME')
        HTTP_PASSWORD = os.getenv('HTTP_PASSWORD')
        # Reinitialize SignalWire client with new credentials
        global signalwire_client
        signalwire_client = SignalWireClient(SIGNALWIRE_PROJECT_ID, SIGNALWIRE_TOKEN, signalwire_space_url=SIGNALWIRE_SPACE)
        # Reinitialize SWAIG with new credentials
        global swaig
        swaig = SWAIG(app, auth=(HTTP_USERNAME, HTTP_PASSWORD))
        logger.info("Generated .env file and reloaded environment variables")
        flash('Environment configuration saved successfully!', 'success')
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error generating .env file: {str(e)}")
        flash('Failed to generate environment configuration. Please try again.', 'danger')
        return render_template('populate.html')

# SWAIG endpoint: Check Balance
@swaig.endpoint(
    "Check the current balance and due date for the customer's account",
    customer_id=SWAIGArgument("string", "The customer's account ID", required=True)
)
def check_balance(customer_id, **kwargs):
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    if not customer:
        return {"response": "I couldn't find your account. Please verify your account number."}
    billing = db.execute('''
        SELECT * FROM billing 
        WHERE customer_id = ? 
        ORDER BY due_date DESC LIMIT 1
    ''', (customer_id,)).fetchone()
    if billing:
        return {"response": f"Your current balance is ${billing['amount']:.2f}, due on {billing['due_date']}."}
    return {"response": "I couldn't find any billing information for your account."}

# SWAIG endpoint: Make Payment
@swaig.endpoint(
    "Make a payment on the customer's account",
    customer_id=SWAIGArgument("string", "The customer's account ID", required=True),
    amount=SWAIGArgument("number", "The amount to pay", required=True)
)
def make_payment(customer_id, amount, **kwargs):
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    if not customer:
        return {"response": "I couldn't find your account. Please verify your account number."}
    if not amount or amount <= 0:
        return {"response": "Please provide a valid payment amount."}
    db.execute('''
        INSERT INTO payments (customer_id, amount, payment_date, payment_method, status, transaction_id)
        VALUES (?, ?, CURRENT_TIMESTAMP, 'phone', 'pending', ?)
    ''', (customer_id, amount, secrets.token_hex(16)))
    db.commit()
    return {"response": f"I've initiated a payment of ${amount:.2f}. You'll receive a confirmation text shortly."}

# SWAIG endpoint: Check Modem Status
@swaig.endpoint(
    "Check the current status of the customer's modem",
    customer_id=SWAIGArgument("string", "The customer's account ID", required=True)
)
def check_modem_status(customer_id, **kwargs):
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    if not customer:
        return {"response": "I couldn't find your account. Please verify your account number."}
    modem = db.execute('SELECT * FROM modems WHERE customer_id = ?', (customer_id,)).fetchone()
    if modem:
        return {"response": f"Your modem is currently {modem['status']}. MAC address: {modem['mac_address']}."}
    return {"response": "I couldn't find any modem information for your account."}

# SWAIG endpoint: Reboot Modem
@swaig.endpoint(
    "Reboot the customer's modem",
    customer_id=SWAIGArgument("string", "The customer's account ID", required=True)
)
def reboot_modem(customer_id, **kwargs):
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    if not customer:
        return {"response": "I couldn't find your account. Please verify your account number."}
    modem = db.execute('SELECT * FROM modems WHERE customer_id = ?', (customer_id,)).fetchone()
    if not modem:
        return {"response": "I couldn't find any modem information for your account."}
    thread = threading.Thread(target=simulate_modem_reboot, args=(customer_id,))
    thread.daemon = True
    thread.start()
    return {"response": "I've initiated a reboot of your modem. This will take about 30 seconds to complete."}

# SWAIG endpoint: Schedule Appointment
@swaig.endpoint(
    "Schedule a service appointment",
    customer_id=SWAIGArgument("string", "The customer's account ID", required=True),
    type=SWAIGArgument("string", "Type of appointment (installation, repair, upgrade, modem_swap)", required=True),
    date=SWAIGArgument("string", "Preferred date for the appointment (YYYY-MM-DD)", required=True)
)
def schedule_appointment(customer_id, type, date, **kwargs):
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    if not customer:
        return {"response": "I couldn't find your account. Please verify your account number."}
    if type not in ['installation', 'repair', 'upgrade', 'modem_swap']:
        return {"response": "Invalid appointment type. Please choose from: installation, repair, upgrade, or modem_swap."}
    try:
        appointment_date = datetime.strptime(date, '%Y-%m-%d')
        if appointment_date < datetime.now():
            return {"response": "Please select a future date for the appointment."}
    except ValueError:
        return {"response": "Invalid date format. Please use YYYY-MM-DD."}
    existing = db.execute('''
        SELECT * FROM appointments 
        WHERE customer_id = ? 
        AND date(start_time) = date(?)
    ''', (customer_id, appointment_date)).fetchone()
    if existing:
        return {"response": f"You already have an appointment scheduled for {date}. Please choose a different date."}
    db.execute('''
        INSERT INTO appointments (customer_id, type, status, start_time, end_time)
        VALUES (?, ?, 'scheduled', ?, ?)
    ''', (customer_id, type, appointment_date.strftime('%Y-%m-%d 09:00:00'), appointment_date.strftime('%Y-%m-%d 11:00:00')))
    db.commit()
    return {"response": f"I've scheduled your {type} appointment for {date}. You'll receive a confirmation text shortly."}

# SWAIG endpoint: Swap Modem
@swaig.endpoint(
    "Schedule a modem swap appointment",
    customer_id=SWAIGArgument("string", "The customer's account ID", required=True),
    date=SWAIGArgument("string", "Preferred date for the modem swap (YYYY-MM-DD)", required=True)
)
def swap_modem(customer_id, date, **kwargs):
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    if not customer:
        return {"response": "I couldn't find your account. Please verify your account number."}
    current_modem = db.execute('SELECT * FROM modems WHERE customer_id = ?', (customer_id,)).fetchone()
    if not current_modem:
        return {"response": "I couldn't find any modem information for your account."}
    try:
        appointment_date = datetime.strptime(date, '%Y-%m-%d')
        if appointment_date < datetime.now():
            return {"response": "Please select a future date for the modem swap."}
    except ValueError:
        return {"response": "Invalid date format. Please use YYYY-MM-DD."}
    existing = db.execute('''
        SELECT * FROM appointments 
        WHERE customer_id = ? 
        AND date(start_time) = date(?)
    ''', (customer_id, appointment_date)).fetchone()
    if existing:
        return {"response": f"You already have an appointment scheduled for {date}. Please choose a different date."}
    db.execute('''
        INSERT INTO appointments (customer_id, type, status, start_time, end_time, notes)
        VALUES (?, 'modem_swap', 'scheduled', ?, ?, ?)
    ''', (customer_id, appointment_date.strftime('%Y-%m-%d 10:00:00'), appointment_date.strftime('%Y-%m-%d 12:00:00'), f"Current MAC: {current_modem['mac_address']}"))
    db.commit()
    return {"response": f"I've scheduled your modem swap for {date}. A technician will bring your new modem and help you with the installation."}

@app.route('/swaig', methods=['GET', 'POST'])
def swaig_endpoint():
    if request.method == 'GET':
        return jsonify(list(swaig.functions.keys()))

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    function_name = data.get('function')
    arguments = data.get('arguments', {})

    if 'meta_data' in arguments:
        del arguments['meta_data']

    if not function_name:
        return jsonify({"error": "Missing 'function' in request body"}), 400

    func = swaig.functions.get(function_name)

    if not func:
        return jsonify({"error": f"Function '{function_name}' not found"}), 404

    try:
        result = func(**arguments)
        return jsonify(result)
    except TypeError as e:
        return jsonify({"error": f"Invalid arguments provided for function '{function_name}': {e}"}), 400
    except Exception as e:
        logging.error(f"Error executing SWAIG function {function_name}: {e}")
        return jsonify({"error": f"An unexpected error occurred while executing function '{function_name}'"}), 500

def get_db():
    db = sqlite3.connect('zen_cable.db')
    db.row_factory = sqlite3.Row
    return db

def init_db_if_needed():
    """Initialize database if it doesn't exist or is empty"""
    try:
        db = get_db()
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='customers'")
        if not cursor.fetchone():
            print("Initializing database...")
            from init_db import init_db
            init_db()
            from init_test_data import init_test_data
            init_test_data()
            print("Database initialized successfully!")
        db.close()
    except Exception as e:
        print(f"Error initializing database: {e}")
        raise

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'customer_id' not in session:
            flash('Please log in to access this page.', 'warning')
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

# Authentication Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        remember = request.form.get('remember', False)

        db = get_db()
        user = db.execute('SELECT * FROM customers WHERE email = ?', (email,)).fetchone()

        if user and verify_password(password, user['password_hash'], user['password_salt']):
            session['customer_id'] = user['id']
            if remember:
                session.permanent = True
            flash('Welcome back!', 'success')
            return redirect(url_for('dashboard'))

        flash('Invalid email or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
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

            db.execute('''
                INSERT INTO password_resets (customer_id, token, expiry)
                VALUES (?, ?, ?)
            ''', (user['id'], token, expiry))
            db.commit()

            reset_url = url_for('reset_password', token=token, _external=True)
            flash('Password reset instructions have been sent to your email.', 'success')
            return redirect(url_for('login'))

        flash('Email not found.', 'danger')
    return render_template('forgot_password.html')

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', 
                         (session['customer_id'],)).fetchone()

    services = db.execute('''
        SELECT s.* FROM services s
        JOIN customer_services cs ON s.id = cs.service_id
        WHERE cs.customer_id = ? AND cs.status = 'active'
    ''', (session['customer_id'],)).fetchall()

    modem = db.execute('SELECT * FROM modems WHERE customer_id = ?',
                      (session['customer_id'],)).fetchone()

    billing = db.execute('''
        SELECT * FROM billing 
        WHERE customer_id = ? 
        ORDER BY due_date DESC LIMIT 1
    ''', (session['customer_id'],)).fetchone()

    return render_template('dashboard.html',
                         customer=customer,
                         services=services,
                         modem=modem,
                         billing=billing)

# API Routes
@app.route('/api/appointments', methods=['GET'])
@login_required
def get_appointments():
    start = request.args.get('start')
    end = request.args.get('end')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    status = request.args.get('status')
    type = request.args.get('type')
    technician = request.args.get('technician')
    priority = request.args.get('priority')
    sort_by = request.args.get('sort_by', 'start_time')
    sort_order = request.args.get('sort_order', 'desc')
    include_history = request.args.get('include_history', 'false').lower() == 'true'
    include_reminders = request.args.get('include_reminders', 'false').lower() == 'true'

    if page < 1:
        return jsonify({'error': 'Page number must be greater than 0'}), 400
    if per_page < 1 or per_page > 100:
        return jsonify({'error': 'Items per page must be between 1 and 100'}), 400

    if not start or not end:
        return jsonify({'error': 'Start and end dates are required'}), 400

    try:
        start_date = datetime.strptime(start, '%Y-%m-%d')
        end_date = datetime.strptime(end, '%Y-%m-%d')

        if start_date > end_date:
            return jsonify({'error': 'Start date must be before end date'}), 400

        if (end_date - start_date).days > 365:
            return jsonify({'error': 'Date range cannot exceed 1 year'}), 400

    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    valid_statuses = ['scheduled', 'completed', 'cancelled', 'pending']
    valid_types = ['installation', 'repair', 'upgrade', 'modem_swap']
    valid_priorities = ['low', 'medium', 'high', 'urgent']
    valid_sort_fields = ['start_time', 'end_time', 'type', 'status', 'priority']
    valid_sort_orders = ['asc', 'desc']

    if status and status not in valid_statuses:
        return jsonify({'error': f'Invalid status. Must be one of: {", ".join(valid_statuses)}'}), 400
    if type and type not in valid_types:
        return jsonify({'error': f'Invalid type. Must be one of: {", ".join(valid_types)}'}), 400
    if priority and priority not in valid_priorities:
        return jsonify({'error': f'Invalid priority. Must be one of: {", ".join(valid_priorities)}'}), 400
    if sort_by not in valid_sort_fields:
        return jsonify({'error': f'Invalid sort field. Must be one of: {", ".join(valid_sort_fields)}'}), 400
    if sort_order not in valid_sort_orders:
        return jsonify({'error': f'Invalid sort order. Must be one of: {", ".join(valid_sort_orders)}'}), 400

    db = get_db()

    query = '''
        SELECT a.*, 
               c.name as customer_name,
               c.phone as customer_phone,
               t.name as technician_name
        FROM appointments a
        LEFT JOIN customers c ON a.customer_id = c.id
        LEFT JOIN technicians t ON a.technician_id = t.id
        WHERE a.customer_id = ? 
        AND a.start_time BETWEEN ? AND ?
    '''
    params = [session['customer_id'], start, end]

    if status:
        query += ' AND a.status = ?'
        params.append(status)
    if type:
        query += ' AND a.type = ?'
        params.append(type)
    if technician:
        query += ' AND t.name LIKE ?'
        params.append(f'%{technician}%')
    if priority:
        query += ' AND a.priority = ?'
        params.append(priority)

    query += f' ORDER BY a.{sort_by} {sort_order}'

    count_query = f"SELECT COUNT(*) FROM ({query})"
    total = db.execute(count_query, params).fetchone()[0]

    query += ' LIMIT ? OFFSET ?'
    params.extend([per_page, (page - 1) * per_page])

    appointments = db.execute(query, params).fetchall()
    result = [dict(appt) for appt in appointments]

    if include_history:
        for appt in result:
            history = db.execute('''
                SELECT * FROM appointment_history 
                WHERE appointment_id = ? 
                ORDER BY created_at DESC
            ''', (appt['id'],)).fetchall()
            appt['history'] = [dict(h) for h in history]

    if include_reminders:
        for appt in result:
            reminders = db.execute('''
                SELECT * FROM appointment_reminders 
                WHERE appointment_id = ? 
                ORDER BY sent_at DESC
            ''', (appt['id'],)).fetchall()
            appt['reminders'] = [dict(r) for r in reminders]

    return jsonify({
        'appointments': result,
        'pagination': {
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        }
    })

@app.route('/api/appointments', methods=['POST'])
@login_required
def create_appointment():
    data = request.json

    required_fields = ['type', 'date']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400

    valid_types = ['installation', 'repair', 'upgrade', 'modem_swap']
    if data['type'] not in valid_types:
        return jsonify({'error': f'Invalid type. Must be one of: {", ".join(valid_types)}'}), 400

    try:
        appointment_date = datetime.strptime(data['date'], '%Y-%m-%d')
        if appointment_date < datetime.now():
            return jsonify({'error': 'Cannot schedule appointments in the past'}), 400
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    db = get_db()

    existing = db.execute('''
        SELECT * FROM appointments 
        WHERE customer_id = ? 
        AND date(start_time) = date(?)
    ''', (session['customer_id'], data['date'])).fetchone()

    if existing:
        return jsonify({'error': f'You already have an appointment scheduled for {data["date"]}'}), 400

    cursor = db.execute('''
        INSERT INTO appointments (
            customer_id, type, status, start_time, end_time, notes,
            priority, technician_id, location
        ) VALUES (?, ?, 'scheduled', ?, ?, ?, ?, ?, ?)
    ''', (
        session['customer_id'],
        data['type'],
        appointment_date.strftime('%Y-%m-%d 09:00:00'),
        appointment_date.strftime('%Y-%m-%d 11:00:00'),
        data.get('notes', ''),
        data.get('priority', 'medium'),
        data.get('technician_id'),
        data.get('location', '')
    ))
    db.commit()

    appointment = db.execute('SELECT * FROM appointments WHERE id = ?', 
                           (cursor.lastrowid,)).fetchone()

    log_appointment_history(
        appointment['id'],
        'created',
        {
            'type': appointment['type'],
            'date': appointment['start_time'],
            'notes': appointment['notes'],
            'priority': appointment['priority']
        }
    )

    schedule_reminders(dict(appointment))

    return jsonify(dict(appointment)), 201

@app.route('/api/appointments/<int:appointment_id>', methods=['PUT'])
@login_required
def update_appointment(appointment_id):
    data = request.json

    db = get_db()

    appointment = db.execute('''
        SELECT * FROM appointments 
        WHERE id = ? AND customer_id = ?
    ''', (appointment_id, session['customer_id'])).fetchone()

    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404

    if 'status' in data:
        valid_statuses = ['scheduled', 'completed', 'cancelled', 'pending']
        if data['status'] not in valid_statuses:
            return jsonify({'error': f'Invalid status. Must be one of: {", ".join(valid_statuses)}'}), 400

    if 'date' in data:
        try:
            new_date = datetime.strptime(data['date'], '%Y-%m-%d')
            if new_date < datetime.now():
                return jsonify({'error': 'Cannot schedule appointments in the past'}), 400
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

        existing = db.execute('''
            SELECT * FROM appointments 
            WHERE customer_id = ? 
            AND date(start_time) = date(?)
            AND id != ?
        ''', (session['customer_id'], data['date'], appointment_id)).fetchone()

        if existing:
            return jsonify({'error': f'You already have another appointment scheduled for {data["date"]}'}), 400

    update_fields = []
    params = []

    if 'status' in data:
        update_fields.append('status = ?')
        params.append(data['status'])

    if 'date' in data:
        update_fields.append('start_time = ?')
        update_fields.append('end_time = ?')
        params.extend([
            new_date.strftime('%Y-%m-%d 09:00:00'),
            new_date.strftime('%Y-%m-%d 11:00:00')
        ])

    if 'notes' in data:
        update_fields.append('notes = ?')
        params.append(data['notes'])

    if 'priority' in data:
        valid_priorities = ['low', 'medium', 'high', 'urgent']
        if data['priority'] not in valid_priorities:
            return jsonify({'error': f'Invalid priority. Must be one of: {", ".join(valid_priorities)}'}), 400
        update_fields.append('priority = ?')
        params.append(data['priority'])

    if 'technician_id' in data:
        update_fields.append('technician_id = ?')
        params.append(data['technician_id'])

    if 'location' in data:
        update_fields.append('location = ?')
        params.append(data['location'])

    if update_fields:
        query = f'''
            UPDATE appointments 
            SET {', '.join(update_fields)}
            WHERE id = ? AND customer_id = ?
        '''
        params.extend([appointment_id, session['customer_id']])

        db.execute(query, params)
        db.commit()

        log_appointment_history(
            appointment_id,
            'updated',
            {k: v for k, v in data.items() if k in ['status', 'date', 'notes', 'priority', 'technician_id', 'location']}
        )

        if 'date' in data:
            updated = db.execute('SELECT * FROM appointments WHERE id = ?', 
                               (appointment_id,)).fetchone()
            schedule_reminders(dict(updated))

    updated = db.execute('SELECT * FROM appointments WHERE id = ?', 
                        (appointment_id,)).fetchone()

    return jsonify(dict(updated))

@app.route('/api/appointments/<int:appointment_id>', methods=['DELETE'])
@login_required
def delete_appointment(appointment_id):
    db = get_db()

    appointment = db.execute('''
        SELECT * FROM appointments 
        WHERE id = ? AND customer_id = ?
    ''', (appointment_id, session['customer_id'])).fetchone()

    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404

    if datetime.strptime(appointment['start_time'], '%Y-%m-%d %H:%M:%S') < datetime.now():
        return jsonify({'error': 'Cannot delete past appointments'}), 400

    log_appointment_history(
        appointment_id,
        'deleted',
        {
            'type': appointment['type'],
            'date': appointment['start_time'],
            'reason': request.json.get('reason', 'No reason provided')
        }
    )

    db.execute('DELETE FROM appointments WHERE id = ?', (appointment_id,))
    db.commit()

    return '', 204

@app.route('/api/appointments/<int:appointment_id>/history', methods=['GET'])
@login_required
def get_appointment_history(appointment_id):
    db = get_db()

    appointment = db.execute('''
        SELECT * FROM appointments 
        WHERE id = ? AND customer_id = ?
    ''', (appointment_id, session['customer_id'])).fetchone()

    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404

    history = db.execute('''
        SELECT * FROM appointment_history 
        WHERE appointment_id = ? 
        ORDER BY created_at DESC
    ''', (appointment_id,)).fetchall()

    return jsonify([dict(h) for h in history])

@app.route('/api/appointments/<int:appointment_id>/reminders', methods=['GET'])
@login_required
def get_appointment_reminders(appointment_id):
    db = get_db()

    appointment = db.execute('''
        SELECT * FROM appointments 
        WHERE id = ? AND customer_id = ?
    ''', (appointment_id, session['customer_id'])).fetchone()

    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404

    reminders = db.execute('''
        SELECT * FROM appointment_reminders 
        WHERE appointment_id = ? 
        ORDER BY sent_at DESC
    ''', (appointment_id,)).fetchall()

    return jsonify([dict(r) for r in reminders])

@app.route('/api/appointments/<int:appointment_id>/reminders', methods=['POST'])
@login_required
def send_appointment_reminder_now(appointment_id):
    db = get_db()

    appointment = db.execute('''
        SELECT * FROM appointments 
        WHERE id = ? AND customer_id = ?
    ''', (appointment_id, session['customer_id'])).fetchone()

    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404

    reminder_type = request.json.get('type', 'sms')
    if reminder_type not in ['sms', 'call']:
        return jsonify({'error': 'Invalid reminder type. Must be either "sms" or "call"'}), 400

    success = send_appointment_reminder(dict(appointment), reminder_type)

    if success:
        return jsonify({'message': f'{reminder_type.upper()} reminder sent successfully'})
    else:
        return jsonify({'error': 'Failed to send reminder'}), 500

@app.route('/reminder_call/<int:appointment_id>', methods=['POST'])
def reminder_call(appointment_id):
    logger.info(f"Received reminder call request for appointment {appointment_id}")

    db = get_db()
    appointment = db.execute('SELECT * FROM appointments WHERE id = ?', 
                           (appointment_id,)).fetchone()

    if not appointment:
        logger.error(f"Appointment {appointment_id} not found for reminder call")
        return jsonify({'error': 'Appointment not found'}), 404

    customer = db.execute('SELECT * FROM customers WHERE id = ?', 
                         (appointment['customer_id'],)).fetchone()

    if not customer:
        logger.error(f"Customer not found for appointment {appointment_id}")
        return jsonify({'error': 'Customer not found'}), 404

    appointment_time = datetime.strptime(appointment['start_time'], '%Y-%m-%d %H:%M:%S')
    formatted_time = appointment_time.strftime('%B %d, %Y at %I:%M %p')

    response = VoiceResponse()
    response.say(f"""
        Hello, this is a reminder that you have a {appointment['type']} appointment 
        scheduled for {formatted_time}. 
        Please call us at 1-800-ZEN-CABLE if you need to reschedule.
        Thank you for choosing Zen Cable.
    """)

    logger.info(f"Generated VoiceResponse for appointment {appointment_id}")
    return str(response)

@app.route('/api/modem/status', methods=['GET', 'POST'])
@login_required
def modem_status():
    db = get_db()
    if request.method == 'POST':
        status = request.json.get('status')
        if status not in ['online', 'offline', 'rebooting']:
            return jsonify({'error': 'Invalid status'}), 400

        if status == 'rebooting':
            thread = threading.Thread(target=simulate_modem_reboot, args=(session['customer_id'],))
            thread.daemon = True
            thread.start()
        else:
            db.execute('''
                UPDATE modems 
                SET status = ?, last_seen = CURRENT_TIMESTAMP
                WHERE customer_id = ?
            ''', (status, session['customer_id']))
            db.commit()

    modem = db.execute('SELECT * FROM modems WHERE customer_id = ?',
                      (session['customer_id'],)).fetchone()
    return jsonify(dict(modem))

def simulate_modem_reboot(customer_id):
    logger.info(f"Starting modem reboot simulation for customer {customer_id}")

    db = get_db()

    db.execute('''
        UPDATE modems 
        SET status = 'rebooting', last_seen = CURRENT_TIMESTAMP
        WHERE customer_id = ?
    ''', (customer_id,))
    db.commit()
    logger.info(f"Set modem status to rebooting for customer {customer_id}")

    time.sleep(30)

    db.execute('''
        UPDATE modems 
        SET status = 'online', last_seen = CURRENT_TIMESTAMP
        WHERE customer_id = ?
    ''', (customer_id,))
    db.commit()
    logger.info(f"Completed modem reboot simulation for customer {customer_id}")
    db.close()

def send_appointment_reminder(appointment, reminder_type='sms'):
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', 
                         (appointment['customer_id'],)).fetchone()

    if not customer:
        logger.error(f"Failed to send reminder: Customer not found for appointment {appointment['id']}")
        return False

    appointment_time = datetime.strptime(appointment['start_time'], '%Y-%m-%d %H:%M:%S')
    formatted_time = appointment_time.strftime('%B %d, %Y at %I:%M %p')

    message = f"""
    Reminder: You have a {appointment['type']} appointment scheduled for {formatted_time}.
    Please call us at 1-800-ZEN-CABLE if you need to reschedule.
    """

    try:
        if reminder_type == 'sms':
            logger.info(f"Sending SMS reminder for appointment {appointment['id']} to {customer['phone'][-4:]}")
            message_response = signalwire_client.messages.create(
                to=customer['phone'],
                from_='+1800ZENCABLE',
                body=message
            )
            logger.info(f"SMS sent successfully. Message SID: {message_response.sid}")
        else:  # call
            logger.info(f"Initiating reminder call for appointment {appointment['id']} to {customer['phone'][-4:]}")
            call_response = signalwire_client.calls.create(
                to=customer['phone'],
                from_='+1800ZENCABLE',
                url=f"{request.host_url}reminder_call/{appointment['id']}"
            )
            logger.info(f"Call initiated successfully. Call SID: {call_response.sid}")

        db.execute('''
            INSERT INTO appointment_reminders (
                appointment_id, reminder_type, sent_at, status
            ) VALUES (?, ?, CURRENT_TIMESTAMP, 'sent')
        ''', (appointment['id'], reminder_type))
        db.commit()
        return True
    except Exception as e:
        logger.error(f"Error sending {reminder_type} reminder for appointment {appointment['id']}: {str(e)}")
        return False

def schedule_reminders(appointment):
    appointment_time = datetime.strptime(appointment['start_time'], '%Y-%m-%d %H:%M:%S')

    sms_time = appointment_time - timedelta(hours=24)
    if sms_time > datetime.now():
        schedule.every().day.at(sms_time.strftime('%H:%M')).do(
            send_appointment_reminder, appointment, 'sms'
        )

    call_time = appointment_time - timedelta(hours=1)
    if call_time > datetime.now():
        schedule.every().day.at(call_time.strftime('%H:%M')).do(
            send_appointment_reminder, appointment, 'call'
        )

def log_appointment_history(appointment_id, action, details):
    db = get_db()
    db.execute('''
        INSERT INTO appointment_history (
            appointment_id, action, details, created_at
        ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ''', (appointment_id, action, json.dumps(details)))
    db.commit()

print('SWAIG registered functions:', list(swaig.functions.keys()))

if __name__ == '__main__':
    print("\n" + "="*50)
    print("Zen Cable Customer Portal")
    print("="*50)
    print("\n=== Test Account Credentials ===")
    print("Email: test@example.com")
    print("Password: password123")
    print("==============================\n")
    print(f"Starting server on {HOST}:{PORT}")
    print(f"Debug mode: {DEBUG}")
    print("="*50 + "\n")

    init_db_if_needed()

    print("Starting Flask app...")
    print(f"SWAIG endpoints registered: {list(swaig.functions.keys())}")
    print(f"Listening on http://{HOST}:{PORT}")

    app.run(host=HOST, port=PORT, debug=DEBUG)