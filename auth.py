import sqlite3
import bcrypt
import os
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

class Database:
    def __init__(self):
        self.db_path = "./chatterbox_enhanced.db"
        self.init_enhanced_db()
    
    def get_connection(self):
        """Anti-lock connection with retries"""
        for _ in range(5):  
            try:
                conn = sqlite3.connect(self.db_path, timeout=10.0)
                conn.execute("PRAGMA journal_mode=WAL")  
                return conn
            except sqlite3.OperationalError:
                time.sleep(0.1) 
        raise Exception("Database locked - restart server")
    
    def init_enhanced_db(self):
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_login TEXT,
                    total_messages INTEGER DEFAULT 0,
                    is_online BOOLEAN DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS login_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    
    def register_user(self, username: str, password: str) -> bool:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
        try:
            with self.get_connection() as conn:
                conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", 
                           (username, password_hash))
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def authenticate_user(self, username: str, password: str) -> Optional[str]:
        with self.get_connection() as conn:
            result = conn.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
            if result and bcrypt.checkpw(password.encode(), result[0]):
                conn.execute("UPDATE users SET last_login = ?, is_online = 1 WHERE username = ?",
                           (datetime.now().isoformat(), username))
                conn.commit()
                self.log_login(username)
                return username
        return None
    
    def save_message(self, username: str, message: str):
        timestamp = datetime.now().isoformat()
        with self.get_connection() as conn:
            conn.execute("INSERT INTO messages (username, message, timestamp) VALUES (?, ?, ?)",
                       (username, message, timestamp))
            conn.execute("UPDATE users SET total_messages = total_messages + 1 WHERE username = ?",
                       (username,))
            conn.commit()
    
    def get_recent_messages(self, limit: int = 50) -> List[Dict[str, str]]:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT username, message, timestamp FROM messages ORDER BY id DESC LIMIT ?", (limit,))
            return [{"username": row[0], "message": row[1], "timestamp": row[2]} for row in cursor.fetchall()][::-1]
    
    def log_login(self, username: str):
        """Separate connection for logging"""
        with self.get_connection() as conn:
            conn.execute("INSERT INTO login_history (username, action) VALUES (?, ?)", (username, "login"))
            conn.commit()
    
    def log_logout(self, username: str):
        with self.get_connection() as conn:
            conn.execute("UPDATE users SET is_online = 0 WHERE username = ?", (username,))
            conn.execute("INSERT INTO login_history (username, action) VALUES (?, ?)", (username, "logout"))
            conn.commit()
    
    def log_activity(self, username: str, action: str):
        with self.get_connection() as conn:
            conn.execute("INSERT INTO user_activity (username, action) VALUES (?, ?)", (username, action))
            conn.commit()
    
    def get_user_stats(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT username, COALESCE(total_messages, 0), last_login, is_online,
                      COALESCE((SELECT COUNT(*) FROM login_history WHERE username = users.username), 0)
                FROM users ORDER BY total_messages DESC LIMIT 10
            """)
            return [{
                "username": row[0], 
                "total_messages": row[1], 
                "last_login": row[2] or "Never", 
                "is_online": bool(row[3]), 
                "login_count": row[4]
            } for row in cursor.fetchall()]
    
    def get_login_history(self, username: Optional[str] = None) -> List[Dict[str, str]]:
        with self.get_connection() as conn:
            if username:
                cursor = conn.execute("SELECT * FROM login_history WHERE username = ? ORDER BY id DESC LIMIT 20", (username,))
            else:
                cursor = conn.execute("SELECT * FROM login_history ORDER BY id DESC LIMIT 50")
            return [{"id": row[0], "username": row[1], "action": row[2], "timestamp": row[3]} for row in cursor.fetchall()]
    
    def get_online_users(self) -> List[str]:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT username FROM users WHERE is_online = 1")
            return [row[0] for row in cursor.fetchall()]
