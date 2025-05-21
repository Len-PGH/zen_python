import sqlite3
from datetime import datetime, timedelta

def init_test_data():
    db = sqlite3.connect('zen_cable.db')
    cursor = db.cursor()
    
    # Add test services
    services = [
        ('Basic Cable', 'Basic cable package with 100+ channels', 49.99, 'cable'),
        ('High-Speed Internet', '100 Mbps internet service', 39.99, 'internet'),
        ('Digital Phone', 'Unlimited local and long distance', 29.99, 'phone')
    ]
    cursor.executemany(
        '''
        INSERT OR IGNORE INTO services (name, description, price, type)
        VALUES (?, ?, ?, ?)
        ''', services
    )
    
    # Get the test customer
    customer = cursor.execute(
        'SELECT id FROM customers WHERE email = ?',
        ('test@example.com',)
    ).fetchone()
    
    if customer:
        customer_id = customer[0]
        
        # Add active services for the customer
        cursor.execute(
            '''
            INSERT OR IGNORE INTO customer_services 
                (customer_id, service_id, status, start_date)
            VALUES (?, 1, 'active', CURRENT_TIMESTAMP)
            ''', (customer_id,)
        )
        cursor.execute(
            '''
            INSERT OR IGNORE INTO customer_services 
                (customer_id, service_id, status, start_date)
            VALUES (?, 2, 'active', CURRENT_TIMESTAMP)
            ''', (customer_id,)
        )
        
        # Add a modem
        cursor.execute(
            '''
            INSERT OR IGNORE INTO modems 
                (customer_id, mac_address, model, status, last_seen)
            VALUES (?, '00:11:22:33:44:55', 'DOCSIS 3.1', 'online', CURRENT_TIMESTAMP)
            ''', (customer_id,)
        )
        
        # Add current and past billing
        current_date = datetime.now()
        due_date = current_date + timedelta(days=15)
        
        cursor.execute(
            '''
            INSERT OR IGNORE INTO billing (customer_id, amount, due_date, status)
            VALUES (?, 89.98, ?, 'pending')
            ''', (customer_id, due_date.strftime('%Y-%m-%d'))
        )
        
        past_billing = [
            (customer_id, 89.98, (current_date - timedelta(days=days)).strftime('%Y-%m-%d'), 'paid')
            for days in (30, 60, 90)
        ]
        cursor.executemany(
            '''
            INSERT OR IGNORE INTO billing (customer_id, amount, due_date, status)
            VALUES (?, ?, ?, ?)
            ''', past_billing
        )
        
        # Add past payments
        past_payments = []
        for days, method, trx in [(25, 'credit_card', 'TRX001'),
                                  (55, 'debit_card', 'TRX002'),
                                  (85, 'bank_transfer', 'TRX003')]:
            date_str = (current_date - timedelta(days=days)).strftime('%Y-%m-%d')
            past_payments.append((customer_id, 89.98, date_str, method, 'completed', trx))
        
        cursor.executemany(
            '''
            INSERT OR IGNORE INTO payments 
                (customer_id, amount, payment_date, payment_method, status, transaction_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', past_payments
        )
        
        # Add appointments
        appointments = []
        for desc, status, offset in [('installation', 'completed', -120),
                                     ('repair', 'scheduled', 7),
                                     ('upgrade', 'scheduled', 14)]:
            start = (current_date + timedelta(days=offset)).strftime('%Y-%m-%d %H:00:00')
            end = (current_date + timedelta(days=offset)).strftime('%Y-%m-%d %H:00:00')
            appointments.append(
                (customer_id, desc, status, start, end, f'{desc.capitalize()} service event')
            )
        cursor.executemany(
            '''
            INSERT OR IGNORE INTO appointments 
                (customer_id, type, status, start_time, end_time, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', appointments
        )

        # Add service history
        service_history = [
            (customer_id, 1, 'added', (current_date - timedelta(days=120)).strftime('%Y-%m-%d'), 'Initial cable service activation'),
            (customer_id, 2, 'added', (current_date - timedelta(days=120)).strftime('%Y-%m-%d'), 'Initial internet service activation'),
            (customer_id, 2, 'modified', (current_date - timedelta(days=30)).strftime('%Y-%m-%d'), 'Upgraded to 100 Mbps plan')
        ]
        cursor.executemany(
            '''
            INSERT OR IGNORE INTO service_history 
                (customer_id, service_id, action, action_date, notes)
            VALUES (?, ?, ?, ?, ?)
            ''', service_history
        )
    
    db.commit()
    db.close()

if __name__ == '__main__':
    init_test_data()
    print("Test data initialized successfully!")
