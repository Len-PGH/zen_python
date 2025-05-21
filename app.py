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
from signalwire.swaig import SWAIG, SWAIGArgument

app = Flask(__name__)
app.secret_key = os.urandom(24)
load_dotenv()

# SignalWire Configuration
SIGNALWIRE_PROJECT_ID = os.getenv('SIGNALWIRE_PROJECT_ID')
SIGNALWIRE_TOKEN = os.getenv('SIGNALWIRE_TOKEN')
SIGNALWIRE_SPACE = os.getenv('SIGNALWIRE_SPACE')
HTTP_USERNAME = os.getenv('HTTP_USERNAME')
HTTP_PASSWORD = os.getenv('HTTP_PASSWORD')

# Environment Configuration
HOST = os.getenv('HOST', '127.0.0.1')
PORT = int(os.getenv('PORT', 8080))
DEBUG = os.getenv('FLASK_ENV') == 'development'

# Replit Configuration
if os.getenv('REPL_ID'):  # Check if running on Replit
    HOST = '0.0.0.0'
    # Port is already 8080 by default now

# Initialize SWAIG
swaig = SWAIG(
    app,
    auth=(HTTP_USERNAME, HTTP_PASSWORD)
)

# Define SWAIG functions
@swaig.function(
    name="check_balance",
    description="Check the current balance and due date for the customer's account",
    arguments=[
        SWAIGArgument(name="customer_id", type="string", description="The customer's account ID")
    ]
)
def check_balance(customer_id):
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

@swaig.function(
    name="make_payment",
    description="Make a payment on the customer's account",
    arguments=[
        SWAIGArgument(name="customer_id", type="string", description="The customer's account ID"),
        SWAIGArgument(name="amount", type="number", description="The amount to pay")
    ]
)
def make_payment(customer_id, amount):
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

@swaig.function(
    name="check_modem_status",
    description="Check the current status of the customer's modem",
    arguments=[
        SWAIGArgument(name="customer_id", type="string", description="The customer's account ID")
    ]
)
def check_modem_status(customer_id):
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    if not customer:
        return {"response": "I couldn't find your account. Please verify your account number."}
    
    modem = db.execute('SELECT * FROM modems WHERE customer_id = ?',
                      (customer_id,)).fetchone()
    
    if modem:
        return {"response": f"Your modem is currently {modem['status']}. MAC address: {modem['mac_address']}."}
    return {"response": "I couldn't find any modem information for your account."}

@swaig.function(
    name="reboot_modem",
    description="Reboot the customer's modem",
    arguments=[
        SWAIGArgument(name="customer_id", type="string", description="The customer's account ID")
    ]
)
def reboot_modem(customer_id):
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    if not customer:
        return {"response": "I couldn't find your account. Please verify your account number."}
    
    modem = db.execute('SELECT * FROM modems WHERE customer_id = ?',
                      (customer_id,)).fetchone()
    
    if not modem:
        return {"response": "I couldn't find any modem information for your account."}
    
    # Start reboot simulation in a separate thread
    thread = threading.Thread(target=simulate_modem_reboot, args=(customer_id,))
    thread.daemon = True
    thread.start()
    
    return {"response": "I've initiated a reboot of your modem. This will take about 30 seconds to complete."}

@swaig.function(
    name="schedule_appointment",
    description="Schedule a service appointment",
    arguments=[
        SWAIGArgument(name="customer_id", type="string", description="The customer's account ID"),
        SWAIGArgument(name="type", type="string", description="Type of appointment (installation, repair, upgrade, modem_swap)"),
        SWAIGArgument(name="date", type="string", description="Preferred date for the appointment (YYYY-MM-DD)")
    ]
)
def schedule_appointment(customer_id, type, date):
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
    
    # Check for existing appointments on the same day
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
    ''', (customer_id, type, appointment_date.strftime('%Y-%m-%d 09:00:00'),
          appointment_date.strftime('%Y-%m-%d 11:00:00')))
    db.commit()
    
    return {"response": f"I've scheduled your {type} appointment for {date}. You'll receive a confirmation text shortly."}

@swaig.function(
    name="swap_modem",
    description="Schedule a modem swap appointment",
    arguments=[
        SWAIGArgument(name="customer_id", type="string", description="The customer's account ID"),
        SWAIGArgument(name="date", type="string", description="Preferred date for the modem swap (YYYY-MM-DD)")
    ]
)
def swap_modem(customer_id, date):
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    if not customer:
        return {"response": "I couldn't find your account. Please verify your account number."}
    
    current_modem = db.execute('SELECT * FROM modems WHERE customer_id = ?',
                             (customer_id,)).fetchone()
    
    if not current_modem:
        return {"response": "I couldn't find any modem information for your account."}
    
    try:
        appointment_date = datetime.strptime(date, '%Y-%m-%d')
        if appointment_date < datetime.now():
            return {"response": "Please select a future date for the modem swap."}
    except ValueError:
        return {"response": "Invalid date format. Please use YYYY-MM-DD."}
    
    # Check for existing appointments on the same day
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
    ''', (customer_id, appointment_date.strftime('%Y-%m-%d 10:00:00'),
          appointment_date.strftime('%Y-%m-%d 12:00:00'),
          f"Current MAC: {current_modem['mac_address']}"))
    db.commit()
    
    return {"response": f"I've scheduled your modem swap for {date}. A technician will bring your new modem and help you with the installation."}

def get_db():
    db = sqlite3.connect('zen_cable.db')
    db.row_factory = sqlite3.Row
    return db

def init_db_if_needed():
    """Initialize database if it doesn't exist or is empty"""
    try:
        db = get_db()
        # Check if customers table exists
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='customers'")
        if not cursor.fetchone():
            print("Initializing database...")
            # Import and run init_db
            from init_db import init_db
            init_db()
            # Import and run init_test_data
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
            # Generate password reset token
            token = secrets.token_urlsafe(32)
            expiry = datetime.utcnow() + timedelta(hours=1)
            
            db.execute('''
                INSERT INTO password_resets (customer_id, token, expiry)
                VALUES (?, ?, ?)
            ''', (user['id'], token, expiry))
            db.commit()
            
            # Send password reset email
            reset_url = url_for('reset_password', token=token, _external=True)
            # Add email sending logic here
            
            flash('Password reset instructions have been sent to your email.', 'success')
            return redirect(url_for('login'))
        
        flash('Email not found.', 'danger')
    return render_template('forgot_password.html')

# Protected Routes
@app.route('/')
@login_required
def index():
    return redirect(url_for('dashboard'))

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
@app.route('/api/modem/status', methods=['GET', 'POST'])
@login_required
def modem_status():
    db = get_db()
    if request.method == 'POST':
        status = request.json.get('status')
        if status not in ['online', 'offline', 'rebooting']:
            return jsonify({'error': 'Invalid status'}), 400
        
        if status == 'rebooting':
            # Start reboot simulation in a separate thread
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

# SignalWire SWAIG Integration
@app.route('/swaig', methods=['POST'])
def swaig():
    data = request.json
    intent = data.get('intent', {})
    customer_id = data.get('customer_id')
    
    if not customer_id:
        return jsonify({
            'response': "I need to verify your identity. Please provide your account number."
        })
    
    db = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    
    if not customer:
        return jsonify({
            'response': "I couldn't find your account. Please verify your account number."
        })
    
    if intent.get('name') == 'check_balance':
        return check_balance(customer_id)
    elif intent.get('name') == 'make_payment':
        return make_payment(customer_id, data.get('amount'))
    elif intent.get('name') == 'check_modem_status':
        return check_modem_status(customer_id)
    elif intent.get('name') == 'schedule_appointment':
        return schedule_appointment(customer_id, intent.get('type'), intent.get('date'))
    elif intent.get('name') == 'swap_modem':
        return swap_modem(customer_id, intent.get('date'))
    
    return jsonify({
        'response': "I'm sorry, I didn't understand that request. You can ask me about your balance, make a payment, check your modem status, schedule an appointment, or swap your modem."
    })

def simulate_modem_reboot(customer_id):
    """Simulate a modem reboot sequence"""
    db = get_db()
    
    # Set status to rebooting
    db.execute('''
        UPDATE modems 
        SET status = 'rebooting', last_seen = CURRENT_TIMESTAMP
        WHERE customer_id = ?
    ''', (customer_id,))
    db.commit()
    
    # Wait for 30 seconds to simulate reboot
    time.sleep(30)
    
    # Set status back to online
    db.execute('''
        UPDATE modems 
        SET status = 'online', last_seen = CURRENT_TIMESTAMP
        WHERE customer_id = ?
    ''', (customer_id,))
    db.commit()
    db.close()

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
    
    # Initialize database if needed
    init_db_if_needed()
    
    app.run(host=HOST, port=PORT, debug=DEBUG) 