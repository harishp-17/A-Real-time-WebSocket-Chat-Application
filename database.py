import sqlite3
import hashlib
import time
from datetime import datetime

import os

class Database:
    def __init__(self):
        # ensure database lives next to this module regardless of cwd
        db_path = os.path.join(os.path.dirname(__file__), 'chatterbox.db')
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        # Messages table (add edited fields)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                target TEXT NOT NULL DEFAULT 'global',
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                edited INTEGER DEFAULT 0,
                edited_timestamp TEXT,
                is_deleted INTEGER DEFAULT 0
            )
        ''')
        # Bans table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                ban_type TEXT NOT NULL,
                ban_until INTEGER,
                strike_count INTEGER DEFAULT 0,
                unbanned_at TEXT,
                created_at TEXT NOT NULL
            )
        ''')
        self.conn.commit()

        # perform migrations for older databases
        # messages table migration
        cursor.execute("PRAGMA table_info(messages)")
        cols = [row[1] for row in cursor.fetchall()]
        if 'target' not in cols:
            cursor.execute("ALTER TABLE messages ADD COLUMN target TEXT DEFAULT 'global'")
        # bans table migration: ensure new columns exist
        cursor.execute("PRAGMA table_info(bans)")
        ban_cols = [row[1] for row in cursor.fetchall()]
        if 'strike_count' not in ban_cols:
            cursor.execute("ALTER TABLE bans ADD COLUMN strike_count INTEGER DEFAULT 0")
        if 'unbanned_at' not in ban_cols:
            cursor.execute("ALTER TABLE bans ADD COLUMN unbanned_at TEXT")
        # ensure audit and read receipts tables exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS message_reads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                reader TEXT NOT NULL,
                read_at TEXT NOT NULL,
                UNIQUE(message_id, reader)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                event TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0
            )
        ''')
        self.conn.commit()

    def ban_user(self, username, ban_type, duration_sec, strike_count=0):
        cursor = self.conn.cursor()
        ban_until = int(time.time()) + duration_sec if duration_sec else None
        cursor.execute(
            "INSERT INTO bans (username, ban_type, ban_until, strike_count, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, ban_type, ban_until, strike_count, datetime.now().isoformat())
        )
        self.conn.commit()
        return ban_until

    def unban_user(self, username):
        cursor = self.conn.cursor()
        now = datetime.now().isoformat()
        # mark any active bans as unbanned
        cursor.execute(
            "UPDATE bans SET unbanned_at = ? WHERE username = ? AND (ban_until IS NULL OR ban_until > ?) ",
            (now, username, int(time.time()))
        )
        self.conn.commit()

    def log_audit(self, username, event):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO audit_logs (username, event, timestamp) VALUES (?, ?, ?)",
            (username, event, datetime.now().isoformat())
        )
        self.conn.commit()

    def get_audit_logs(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT username, event, timestamp FROM audit_logs ORDER BY id ASC")
        return cursor.fetchall()

    def is_user_banned(self, username):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT ban_type, ban_until FROM bans WHERE username = ? ORDER BY id DESC LIMIT 1",
            (username,)
        )
        row = cursor.fetchone()
        if not row:
            return False, None, None
        ban_type, ban_until = row
        if ban_until is not None and int(time.time()) > ban_until:
            return False, None, None
        return True, ban_until, ban_type

    def get_ban_list(self, include_expired=False):
        cursor = self.conn.cursor()
        if include_expired:
            cursor.execute(
                "SELECT username, ban_type, ban_until, strike_count, unbanned_at, created_at FROM bans"
            )
        else:
            cursor.execute(
                "SELECT username, ban_type, ban_until, strike_count, unbanned_at, created_at FROM bans WHERE ban_until IS NULL OR ban_until > ?",
                (int(time.time()),)
            )
        return cursor.fetchall()

    def soft_delete_message(self, message_id, username):
        cursor = self.conn.cursor()
        cursor.execute("SELECT username FROM messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        if not row:
            return False, "Message not found"
        if row[0] != username:
            return False, "Not allowed"
        cursor.execute(
            "UPDATE messages SET message = ?, is_deleted = 1 WHERE id = ?",
            ("This message was deleted", message_id)
        )
        self.conn.commit()
        return True, None
    
    def hash_password(self, password):
        return hashlib.sha256(password.encode()).hexdigest()
    
    def register_user(self, username, password):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, self.hash_password(password))
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def authenticate_user(self, username, password):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT username FROM users WHERE username = ? AND password = ?",
            (username, self.hash_password(password))
        )
        result = cursor.fetchone()
        return result[0] if result else None
    
    def user_exists(self, username):
        cursor = self.conn.cursor()
        cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
        return cursor.fetchone() is not None
    
    def create_password_reset_token(self, username, token, expires_in_hours=1):
        try:
            import time
            cursor = self.conn.cursor()
            now = datetime.now().isoformat()
            expires_at = datetime.fromtimestamp(time.time() + expires_in_hours * 3600).isoformat()
            
            cursor.execute(
                "INSERT INTO password_reset_tokens (username, token, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (username, token, now, expires_at)
            )
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Error creating reset token: {e}")
            return False
    
    def verify_reset_token(self, username, token):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, expires_at, used FROM password_reset_tokens WHERE username = ? AND token = ?",
            (username, token)
        )
        row = cursor.fetchone()
        
        if not row:
            return False, "Token not found"
        
        token_id, expires_at, used = row
        
        if used:
            return False, "Token already used"
        
        if datetime.fromisoformat(expires_at) < datetime.now():
            return False, "Token expired"
        
        return True, token_id
    
    def reset_password(self, username, token, new_password):
        is_valid, result = self.verify_reset_token(username, token)
        
        if not is_valid:
            return False, result
        
        try:
            cursor = self.conn.cursor()
            
            # Update password
            cursor.execute(
                "UPDATE users SET password = ? WHERE username = ?",
                (self.hash_password(new_password), username)
            )
            
            # Mark token as used
            cursor.execute(
                "UPDATE password_reset_tokens SET used = 1 WHERE username = ? AND token = ?",
                (username, token)
            )
            
            self.conn.commit()
            return True, "Password reset successfully"
        except Exception as e:
            print(f"Error resetting password: {e}")
            return False, "Error resetting password"
    
    def save_message(self, username, message, target='global'):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO messages (username, target, message, timestamp) VALUES (?, ?, ?, ?)",
            (username, target, message, datetime.now().isoformat())
        )
        self.conn.commit()
        return cursor.lastrowid

    def edit_message(self, message_id, username, new_content):
        cursor = self.conn.cursor()
        # Only allow editing by sender and if not older than 10 minutes
        cursor.execute("SELECT username, timestamp FROM messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        if not row:
            return False, "Message not found"
        if row[0] != username:
            return False, "Not allowed"
        msg_time = datetime.fromisoformat(row[1])
        if (datetime.now() - msg_time).total_seconds() > 600:
            return False, "Edit window expired"
        cursor.execute(
            "UPDATE messages SET message = ?, edited = 1, edited_timestamp = ? WHERE id = ?",
            (new_content, datetime.now().isoformat(), message_id)
        )
        self.conn.commit()
        return True, None
    
    def get_recent_messages(self, limit=50):
        # legacy helper, returns latest global messages
        return self.get_chat_history(None, 'global', limit)

    def get_chat_history(self, current_user, target, limit=50):
        cursor = self.conn.cursor()
        if target == 'global':
            cursor.execute(
                "SELECT id, username, target, message, timestamp, edited, edited_timestamp, is_deleted FROM messages "
                "WHERE target = 'global' ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            )
            rows = cursor.fetchall()
        else:
            # DM conversation between current_user and target user
            # include messages sent by either party where target matches the other
            cursor.execute(
                "SELECT id, username, target, message, timestamp, edited, edited_timestamp, is_deleted FROM messages "
                "WHERE (target = ? AND username = ?) OR (target = ? AND username = ?) "
                "ORDER BY timestamp DESC LIMIT ?",
                (target, current_user, current_user, target, limit)
            )
            rows = cursor.fetchall()
        messages = []
        for row in rows:
            msg = {
                "id": row[0],
                "username": row[1],
                "target": row[2],
                "message": row[3],
                "timestamp": row[4],
                "edited": bool(row[5]),
                "edited_timestamp": row[6],
                "is_deleted": bool(row[7]),
                "isDM": target != 'global'  # Mark as DM if this is a conversation history
            }
            # include read receivers for DM conversations
            if target != 'global':
                msg['readers'] = self.get_message_readers(msg['id'])
            messages.append(msg)
        return messages[::-1]  # flip so oldest first

    
    def get_total_messages(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages")
        return cursor.fetchone()[0]

    def get_message_sender(self, message_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT username FROM messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        return row[0] if row else None

    def mark_message_read(self, message_id, reader):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO message_reads (message_id, reader, read_at) VALUES (?, ?, ?)",
            (message_id, reader, datetime.now().isoformat())
        )
        self.conn.commit()

    def get_message_readers(self, message_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT reader FROM message_reads WHERE message_id = ?", (message_id,))
        return [row[0] for row in cursor.fetchall()]

    def get_unread_dm_counts(self, username):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT username, COUNT(*) FROM messages "
            "WHERE target = ? "
            "AND id NOT IN (SELECT message_id FROM message_reads WHERE reader = ?) "
            "GROUP BY username",
            (username, username)
        )
        return {row[0]: row[1] for row in cursor.fetchall()}
    
    def get_user_stats(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return {"total_users": cursor.fetchone()[0]}
