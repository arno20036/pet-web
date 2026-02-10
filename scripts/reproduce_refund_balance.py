import sqlite3
import os

# Set DB path
DB_PATH = os.path.join(os.getcwd(), 'database.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def check_balance():
    conn = get_db_connection()
    acc = conn.execute('SELECT * FROM platform_account LIMIT 1').fetchone()
    conn.close()
    if acc:
        return acc['balance']
    else:
        return 0

def create_refund_task():
    conn = get_db_connection()
    # Create a dummy order and refund task
    try:
        # Check if platform tables exist
        conn.execute("SELECT * FROM platform_account LIMIT 1")
    except:
        print("Platform tables might not exist")
        return None, None

    # Dummy order
    cursor = conn.execute("INSERT INTO orders (buyer_id, seller_id, listing_id, booking_date, booking_time, status, total_price, payment_status) VALUES (1, 1, 1, '2025-01-01', '10:00', 'cancelled', 100, 'paid')")
    order_id = cursor.lastrowid
    
    # Dummy refund task
    cursor = conn.execute("INSERT INTO refund_tasks (user_id, order_id, amount, reason, status, created_at) VALUES (1, ?, 100, 'Test Refund', 'pending', datetime('now'))", (order_id,))
    task_id = cursor.lastrowid
    
    conn.commit()
    conn.close()
    return order_id, task_id

def simulate_process_refund(task_id, order_id):
    # This logic mimics app.py process_admin_refund_task BUT imports the actual function if possible
    # However, importing app.py might trigger server run.
    # So we will copy the logic OR try to use app context.
    
    # Let's try to mimic the logic exactly as it is in the FILE (read from file?)
    # Or just write the logic I think is there.
    # User issue: "I performed refund... balance didn't decrease".
    # If the logic in app.py is correct, then this script running the SAME logic should work.
    
    # Instead, let's look at the actual DB update_platform_balance function.
    # It updates `platform_account`.
    pass

# We will run the actual app logic via importing if possible, but app.py runs app.run at bottom.
# It uses `if __name__ == '__main__': app.run()` so we can import it!
pass

if __name__ == "__main__":
    print("Checking initial balance...")
    initial_balance = check_balance()
    print(f"Initial Balance: {initial_balance}")
    
    print("Creating dummy refund task...")
    order_id, task_id = create_refund_task()
    if not task_id:
        print("Failed to create task")
        exit(1)
        
    print(f"Created Task ID: {task_id} for Order {order_id} Amount: 100")
    
    # Import app and mimic context
    from app import process_admin_refund_task, session, app
    
    # Mock session
    with app.test_request_context():
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_role'] = 'admin'
                sess['user_id'] = 1
            
            print("Processing refund task via app route...")
            try:
                # Use client.post to trigger the route with the session
                response = client.post(f'/api/admin/refund-task/{task_id}/process')
                print(f"Response Status: {response.status_code}")
                print(f"Response Data: {response.get_json()}")
            except Exception as e:
                print(f"Error processing: {e}")

    print("Checking final balance...")
    final_balance = check_balance()
    print(f"Final Balance: {final_balance}")

    with open('reproduce_result.txt', 'w', encoding='utf-8') as f:
        f.write(f"Initial Balance: {initial_balance}\n")
        f.write(f"Created Task ID: {task_id}\n")
        f.write(f"Response Status: {response.status_code}\n")
        f.write(f"Final Balance: {final_balance}\n")
        
        # Rounding to handle float precision
        decrease = initial_balance - final_balance
        if abs(decrease - 100) < 0.01:
            f.write("SUCCESS: Balance decreased by 100.\n")
            print("SUCCESS: Balance decreased by 100.")
        else:
            f.write("FAILURE: Balance did not decrease correctly.\n")
            f.write(f"Expected: {initial_balance - 100}, Got: {final_balance}\n")
            print("FAILURE")

