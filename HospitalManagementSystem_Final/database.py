import sqlite3

def init_db():
    conn = sqlite3.connect('hospital.db')  # MUST match the one used in app.py
    with open('schema.sql', 'r') as f:
        conn.executescript(f.read())

    # Insert default admin
    conn.execute("INSERT OR IGNORE INTO admin (username, password) VALUES (?, ?)", ("admin", "admin123"))

    # Insert default doctors with experience and mobile
    default_doctors = [
        ("Dr. Asha Mehta", "Cardiologist", 10, "9876543210", "asha@example.com", "asha123"),
        ("Dr. Rohan Deshmukh", "Neurologist", 8, "9123456780", "rohan@example.com", "rohan123"),
        ("Dr. Priya Sharma", "Pediatrician", 5, "9988776655", "priya@example.com", "priya123")
    ]

    for doc in default_doctors:
        conn.execute(
            "INSERT OR IGNORE INTO doctors (name, specialization, experience, mobile, email, password) VALUES (?, ?, ?, ?, ?, ?)",
            doc
        )

    conn.commit()
    conn.close()
