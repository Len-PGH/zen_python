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
    
    cursor.executemany('''
        INSERT INTO services (name, description, price, type)
        VALUES (?, ?, ?, ?)
    ''', services)
    
    # Get the test customer
    customer = cursor.execute('SELECT id FROM customers WHERE email = ?', 
                            ('test@example.com',)).fetchone()
    
    if customer:
        customer_id = customer[0]
        
        # Add active services for the customer
        cursor.execute('''
            INSERT INTO customer_services (customer_id, service_id, status, start_date)
            VALUES (?, 1, 'active', CURRENT_TIMESTAMP)
        ''', (customer_id,))
        
        cursor.execute('''
            INSERT INTO customer_services (customer_id, service_id, status, start_date)
            VALUES (?, 2, 'active', CURRENT_TIMESTAMP)
        ''', (customer_id,))
        
        # Add a modem
        cursor.execute('''
            INSERT INTO modems (customer_id, mac_address, model, status, last_seen)
            VALUES (?, '00:11:22:33:44:55', 'DOCSIS 3.1', 'online', CURRENT_TIMESTAMP)
        ''', (customer_id,))
        
        # Add current billing
        cursor.execute('''
            INSERT INTO billing (customer_id, amount, due_date, status)
            VALUES (?, 89.98, ?, 'pending')
        ''', (customer_id, (datetime.now() + timedelta(days=15)).strftime('%Y-%m-%d')))
    
    db.commit()
    db.close()

if __name__ == '__main__':
    init_test_data()
    print("Test data initialized successfully!") 