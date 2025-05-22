import sqlite3
import hashlib
import secrets

def hash_password(password: str):
    """Generate a salt and SHA-256 hash for the given password."""
    salt = secrets.token_hex(16)
    pw_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return pw_hash, salt

def init_db():
    db = sqlite3.connect('zen_cable.db')
    db.row_factory = sqlite3.Row

    # --- table creation ---
    db.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT NOT NULL,
            address TEXT,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # ... other table CREATEs omitted for brevity ...

    # --- seed test user with specific 6-digit ID ---
    TEST_CUSTOMER_ID = 8675309
    TEST_EMAIL       = "test@example.com"
    TEST_PASSWORD    = "password123"
    pw_hash, pw_salt = hash_password(TEST_PASSWORD)

    db.execute('''
        INSERT OR REPLACE INTO customers
          (id, name, email, phone, address, password_hash, password_salt)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        TEST_CUSTOMER_ID,
        "Test User",
        TEST_EMAIL,
        "000-000-0000",
        "123 Test Lane",
        pw_hash,
        pw_salt
    ))

    db.commit()
    db.close()

    # Print credentials for your reference
    print("\n=== Test Account Credentials ===")
    print(f"Customer ID: {TEST_CUSTOMER_ID:06d}")
    print(f"Email:       {TEST_EMAIL}")
    print(f"Password:    {TEST_PASSWORD}")
    print("==============================\n")

if __name__ == '__main__':
    init_db()
    print("Database initialized successfully!")
