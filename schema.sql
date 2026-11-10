-- Schema for the pet companion platform (all 23 tables).
-- Generated from database.db; `flask --app app init-db` applies it.
-- Safe to re-run: tables are only created when missing and existing data is never touched.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    nickname TEXT,
    gender TEXT NOT NULL,
    dob DATE NOT NULL,
    city TEXT NOT NULL,
    district TEXT NOT NULL,
    phone TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    role TEXT DEFAULT 'member',
    avatar TEXT
);

CREATE TABLE IF NOT EXISTS trainer_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    experience_years INTEGER NOT NULL,
    certificate_filename TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainer_id INTEGER NOT NULL,
    pet_image TEXT NOT NULL,
    pet_type TEXT NOT NULL,
    breed TEXT NOT NULL,
    available_slots TEXT NOT NULL,
    price INTEGER NOT NULL,
    introduction TEXT NOT NULL,
    rules TEXT,
    cancellation_hours INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending',
    pet_name TEXT,
    FOREIGN KEY (trainer_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS listing_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (listing_id) REFERENCES listings (id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    buyer_id INTEGER NOT NULL,
    seller_id INTEGER NOT NULL,
    booking_date TEXT NOT NULL,
    booking_time TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    location_type TEXT,
    location_detail TEXT,
    price_at_booking INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration INTEGER DEFAULT 1,
    total_price INTEGER,
    payment_status TEXT DEFAULT 'unpaid',
    payment_method TEXT,
    payment_time TIMESTAMP,
    card_last4 TEXT,
    payment_confirmed INTEGER DEFAULT 0,
    payment_deadline TIMESTAMP,
    paid_amount INTEGER DEFAULT 0,
    original_price REAL,
    FOREIGN KEY (listing_id) REFERENCES listings (id),
    FOREIGN KEY (buyer_id) REFERENCES users (id),
    FOREIGN KEY (seller_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS order_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_read BOOLEAN DEFAULT 0,
    FOREIGN KEY (order_id) REFERENCES orders (id),
    FOREIGN KEY (sender_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS platform_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commission_rate REAL DEFAULT 15.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    level_adv_threshold REAL DEFAULT 4.0,
    level_pro_threshold REAL DEFAULT 4.5,
    commission_general REAL DEFAULT 15.0,
    commission_advanced REAL DEFAULT 10.0,
    commission_professional REAL DEFAULT 5.0
);

CREATE TABLE IF NOT EXISTS trainer_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE NOT NULL,
    balance REAL DEFAULT 0.0,
    total_earned REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    current_level TEXT DEFAULT 'general',
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS account_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    order_id INTEGER,
    amount REAL NOT NULL,
    commission REAL DEFAULT 0.0,
    original_amount REAL,
    type TEXT DEFAULT 'income',
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    transaction_type TEXT DEFAULT 'generic',
    status TEXT DEFAULT 'completed',
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS withdrawal_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    bank_name TEXT NOT NULL,
    bank_account TEXT NOT NULL,
    account_holder TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    admin_note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    processed_by INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (processed_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS trainer_bank_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    bank_code TEXT NOT NULL,
    bank_account TEXT NOT NULL,
    account_holder TEXT NOT NULL,
    is_last_used BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    source_user_id INTEGER NOT NULL,
    target_user_id INTEGER NOT NULL,
    listing_id INTEGER,
    rating INTEGER NOT NULL,
    comment TEXT,
    review_type TEXT NOT NULL,
    created_at DATETIME DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(order_id) REFERENCES orders(id),
    FOREIGN KEY(source_user_id) REFERENCES users(id),
    FOREIGN KEY(target_user_id) REFERENCES users(id),
    FOREIGN KEY(listing_id) REFERENCES listings(id)
);

CREATE TABLE IF NOT EXISTS saved_payment_methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    card_number_masked TEXT NOT NULL,
    card_expiry TEXT NOT NULL,
    cardholder_name TEXT NOT NULL,
    payment_token TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS order_appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    reason TEXT,
    description TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    admin_reply TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_reply TEXT,
    user_reply_at TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders (id),
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS appeal_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appeal_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    sender_role TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (appeal_id) REFERENCES order_appeals(id),
    FOREIGN KEY (sender_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS platform_account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    balance REAL DEFAULT 0.0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS platform_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    amount REAL NOT NULL,
    related_order_id INTEGER,
    related_withdrawal_id INTEGER,
    description TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS trainer_level_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainer_id INTEGER NOT NULL,
    old_level TEXT,
    new_level TEXT NOT NULL,
    trigger_source TEXT DEFAULT 'system_update',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (trainer_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS blacklists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainer_id INTEGER NOT NULL,
    consumer_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (trainer_id) REFERENCES users (id),
    FOREIGN KEY (consumer_id) REFERENCES users (id),
    UNIQUE(trainer_id, consumer_id)
);

CREATE TABLE IF NOT EXISTS reschedule_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    new_date TEXT NOT NULL,
    new_time TEXT NOT NULL,
    new_duration INTEGER NOT NULL,
    price_diff INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders (id)
);

CREATE TABLE IF NOT EXISTS refund_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    processed_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id),
    FOREIGN KEY (order_id) REFERENCES orders (id),
    FOREIGN KEY (processed_by) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS user_bank_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    bank_code TEXT NOT NULL,
    branch_code TEXT,
    account_number TEXT NOT NULL,
    account_name TEXT NOT NULL,
    is_default BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS order_modifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    old_price REAL NOT NULL,
    new_price REAL NOT NULL,
    reason TEXT,
    modified_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders (id),
    FOREIGN KEY (modified_by) REFERENCES users (id)
);

-- Rows the app expects to exist.
INSERT INTO platform_settings (id) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM platform_settings);
INSERT INTO platform_account (balance, updated_at) SELECT 0.0, datetime('now', 'localtime')
    WHERE NOT EXISTS (SELECT 1 FROM platform_account);
