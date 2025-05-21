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

app = Flask(__name__)
app.secret_key = os.urandom(24)
load_dotenv()

# SignalWire Configuration
SIGNALWIRE_PROJECT_ID = os.getenv('SIGNALWIRE_PROJECT_ID')
SIGNALWIRE_TOKEN = os.getenv('SIGNALWIRE_TOKEN')
SIGNALWIRE_SPACE = os.getenv('SIGNALWIRE_SPACE')

# Environment Configuration
HOST = os.getenv('HOST', '127.0.0.1')
PORT = int(os.getenv('PORT', 5000))
DEBUG = os.getenv('FLASK_ENV') == 'development'

# Replit Configuration
if os.getenv('REPL_ID'):  # Check if running on Replit
    HOST = '0.0.0.0'
    PORT = 8080  # Replit's default port

def get_db():
    db = sqlite3.connect('zen_cable.db')
    db.row_factory = sqlite3.Row
    return db

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
        return check_balance(customer)
    elif intent.get('name') == 'make_payment':
        return make_payment_swaig(customer, data)
    elif intent.get('name') == 'check_modem_status':
        return check_modem_status(customer)
    elif intent.get('name') == 'schedule_appointment':
        return schedule_appointment_swaig(customer, data)
    elif intent.get('name') == 'swap_modem':
        return swap_modem(customer, data)
    
    return jsonify({
        'response': "I'm sorry, I didn't understand that request. You can ask me about your balance, make a payment, check your modem status, schedule an appointment, or swap your modem."
    })

def check_balance(customer):
    db = get_db()
    billing = db.execute('''
        SELECT * FROM billing 
        WHERE customer_id = ? 
        ORDER BY due_date DESC LIMIT 1
    ''', (customer['id'],)).fetchone()
    
    if billing:
        return jsonify({
            'response': f"Your current balance is ${billing['amount']:.2f}, due on {billing['due_date']}."
        })
    return jsonify({
        'response': "I couldn't find any billing information for your account."
    })

def make_payment_swaig(customer, data):
    amount = data.get('amount')
    if not amount:
        return jsonify({
            'response': "How much would you like to pay?"
        })
    
    db = get_db()
    db.execute('''
        INSERT INTO payments (customer_id, amount, payment_date, payment_method, status, transaction_id)
        VALUES (?, ?, CURRENT_TIMESTAMP, 'phone', 'pending', ?)
    ''', (customer['id'], amount, secrets.token_hex(16)))
    db.commit()
    
    return jsonify({
        'response': f"I've initiated a payment of ${amount:.2f}. You'll receive a confirmation text shortly."
    })

def check_modem_status(customer):
    db = get_db()
    modem = db.execute('SELECT * FROM modems WHERE customer_id = ?',
                      (customer['id'],)).fetchone()
    
    if modem:
        return jsonify({
            'response': f"Your modem is currently {modem['status']}. MAC address: {modem['mac_address']}."
        })
    return jsonify({
        'response': "I couldn't find any modem information for your account."
    })

def schedule_appointment_swaig(customer, data):
    appointment_type = data.get('type')
    if not appointment_type:
        return jsonify({
            'response': "What type of appointment would you like to schedule? You can choose from installation, repair, upgrade, or modem swap."
        })
    
    preferred_date = data.get('date')
    if not preferred_date:
        return jsonify({
            'response': "What date would you prefer for the appointment?"
        })
    
    db = get_db()
    db.execute('''
        INSERT INTO appointments (customer_id, type, status, start_time, end_time)
        VALUES (?, ?, 'scheduled', ?, ?)
    ''', (customer['id'], appointment_type, preferred_date, 
          datetime.strptime(preferred_date, '%Y-%m-%d') + timedelta(hours=1)))
    db.commit()
    
    return jsonify({
        'response': f"I've scheduled your {appointment_type} appointment for {preferred_date}. You'll receive a confirmation text shortly."
    })

def swap_modem(customer, data):
    db = get_db()
    current_modem = db.execute('SELECT * FROM modems WHERE customer_id = ?',
                             (customer['id'],)).fetchone()
    
    if not current_modem:
        return jsonify({
            'response': "I couldn't find any modem information for your account."
        })
    
    # Schedule modem swap appointment
    preferred_date = data.get('date')
    if not preferred_date:
        return jsonify({
            'response': "What date would you prefer for the modem swap?"
        })
    
    db.execute('''
        INSERT INTO appointments (customer_id, type, status, start_time, end_time, notes)
        VALUES (?, 'modem_swap', 'scheduled', ?, ?, ?)
    ''', (customer['id'], preferred_date, 
          datetime.strptime(preferred_date, '%Y-%m-%d') + timedelta(hours=1),
          f"Current MAC: {current_modem['mac_address']}"))
    db.commit()
    
    return jsonify({
        'response': f"I've scheduled your modem swap for {preferred_date}. A technician will bring your new modem and help you with the installation."
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
    print(f"Starting server on {HOST}:{PORT}")
    print(f"Debug mode: {DEBUG}")
    app.run(host=HOST, port=PORT, debug=DEBUG) 