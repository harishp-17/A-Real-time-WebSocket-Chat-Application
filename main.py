from pydantic import BaseModel
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles 
from fastapi import APIRouter
from typing import Dict, Set, Optional
import math
import json
import re
import time
import datetime
import asyncio
import io
import csv
from database import Database

# --- Model Definitions ---
class UserRegister(BaseModel):
    username: str
    full_name: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class ForgotPasswordRequest(BaseModel):
    username: str

class ResetPasswordRequest(BaseModel):
    username: str
    token: str
    new_password: str

# --- Utility Functions and Constants ---
ADMIN_PASSWORD = "SuperEnterprise2026!"

def contains_bad_word(message: str):
    BAD_WORDS = {
        'fuck', 'shit', 'bitch', 'cunt', 'dick', 'piss', 'asshole', 'bastard', 
        'slut', 'whore', 'cock', 'tits', 'prick', 'twat', 'kill'
    }
    message_lower = message.lower()
    words = re.findall(r'\b\w+\b', message_lower)
    for word in words:
        if word in BAD_WORDS:
            return True, word
    return False, ""

def safe_float(value):
    if math.isinf(value):
        return 9999999999
    return value

def safe_timestamp(timestamp):
    if timestamp == 9999999999 or timestamp > 253402300799:
        return "PERMANENT"
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
    except (OverflowError, ValueError, OSError):
        return "PERMANENT"

def create_csv_response(data, headers, filename):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in data:
        writer.writerow(row)
    return Response(
        content=output.getvalue(),
        media_type='text/csv',
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache"
        }
    )

# --- FastAPI App Definition ---
db = Database()
app = FastAPI(title="Chatterbox Enterprise - FULL ADMIN PANEL + DM SUPPORT")

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.user_online_status: Set[str] = set()
        self.typing_status: Dict[str, float] = {}
        self.unban_notifications: Set[str] = set()
        self.violations: Dict[str, int] = {}

    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        # No ban logic implemented in Database, so skip ban check for now
        # TODO: Implement ban logic in Database and update here
        is_banned, ban_until, ban_type = db.is_user_banned(username)
        if is_banned:
            if ban_type == "permanent":
                await websocket.send_json({
                    "type": "banned",
                    "message": "PERMANENT BAN",
                    "ban_type": "permanent",
                    "ban_until": ban_until
                })
            else:
                ban_time_left = 0
                if ban_until is not None:
                    ban_time_left = max(0, int(ban_until - time.time()))
                await websocket.send_json({
                    "type": "banned",
                    "message": f" TEMP BAN: {ban_time_left}s remaining",
                    "ban_type": "temp",
                    "ban_until": ban_until
                })
            await asyncio.sleep(3)
            await websocket.close(code=1008)
            print(f"{username} BANNED ({ban_type})")
            return False
        
        self.active_connections[username] = websocket
        self.user_online_status.add(username)

        if username in self.unban_notifications:
            await websocket.send_json({
                "type": "system",
                "message": "✅ Admin has unbanned your account."
                })
            self.unban_notifications.discard(username)
            
        print(f"{username} connected ({len(self.active_connections)} online)")
        
        # send most recent global history on connect
        history = db.get_chat_history(username, 'global', 50)
        await websocket.send_json({"type": "history", "messages": history, "target": "global"})
        # send unread DM counts so client can initialize badges
        unread = db.get_unread_dm_counts(username)
        if unread:
            await websocket.send_json({"type": "unread_counts", "counts": unread})
        
        await self.broadcast_user_list()
        await self.broadcast_json({"type": "system", "message": f"{username} joined ({len(self.active_connections)} online)"})
        return True
    
    def disconnect(self, websocket: WebSocket):
        for username, connection in list(self.active_connections.items()):
            if connection == websocket:
                self.active_connections.pop(username, None)
                self.user_online_status.discard(username)
                self.typing_status.pop(username, None)
                asyncio.create_task(self.broadcast_user_list())
                asyncio.create_task(self.broadcast_json({"type": "system", "message": f"{username} left"}))
                break
            

    async def broadcast_json(self, message: dict):
        disconnected = []
        for username, connection in list(self.active_connections.items()):
            try:
                await connection.send_json(message)
            except:
                disconnected.append(username)
        for username in disconnected:
            self.active_connections.pop(username, None)
    
    async def broadcast_user_list(self):
        """Broadcast current online users to all clients"""
        await self.broadcast_json({
            "type": "user_list",
            "users": list(self.user_online_status)
        })
    
    async def send_to_user(self, username: str, message: dict):
        if username in self.active_connections:
            try:
                await self.active_connections[username].send_json(message)
            except:
                pass
    
    async def send_to_target(self, sender: str, target: str, message: dict):
        """Send message to specific target user or broadcast if 'global'.

        For DMs we also supply a delivery flag back to the sender so the
        front end can render single/double ticks depending on whether the
        recipient was online at the time of sending.  The original message
        object is left untouched when sending to the target user.
        """
        if target == "global":
            await self.broadcast_json(message)
        else:
            delivered = target in self.active_connections
            if delivered:
                print(f"DELIVERING DM from {sender} to {target}")
                # only send to recipient if they are currently connected
                await self.send_to_user(target, message)
            else:
                print(f"DM from {sender} to {target} could not be delivered (user offline)")
            # echo back to sender with delivery info attached
            msg_copy = message.copy()
            msg_copy["delivered"] = delivered
            await self.send_to_user(sender, msg_copy)
    
    async def handle_message(self, username: str, data: dict):
        message_type = data.get('type')

        if message_type == 'typing':
            target = data.get('target', 'global')
            is_typing = data.get('isTyping', True)
            now = time.time()
            
            # Only send typing for DM, not global
            if target == 'global':
                return
            
            # Handle typing stop
            if not is_typing:
                key = f"{username}:{target}"
                self.typing_status.pop(key, None)
                typing_msg = {
                    "type": "typing",
                    "username": username,
                    "isTyping": False,
                    "target": target
                }
                await self.send_to_user(target, typing_msg)
                await self.send_to_user(username, typing_msg)
                return
            
            # Handle typing start/continue - Prevent duplicate typing broadcasts within 1 second
            key = f"{username}:{target}"
            last_typing = self.typing_status.get(key, 0)
            if now - last_typing < 1.0:
                return
            
            self.typing_status[key] = now
            typing_msg = {
                "type": "typing",
                "username": username,
                "isTyping": True,
                "target": target
            }
            # Only send to DM target user
            await self.send_to_user(target, typing_msg)
            # Also send to sender for UI feedback
            await self.send_to_user(username, typing_msg)
            # Schedule auto-clear after 4 seconds of no activity
            asyncio.create_task(self.clear_typing(username, target))
            return

        # History request (websocket API)
        if message_type == 'get_history':
            target = data.get('target', 'global')
            # fetch history for this conversation
            history = db.get_chat_history(username, target, 100)
            await self.send_to_user(username, {
                "type": "history",
                "messages": history,
                "target": target
            })
            return

        # Read receipt
        if message_type == 'read_receipt':
            message_id = data.get('message_id')
            if message_id:
                db.mark_message_read(message_id, username)
                sender = db.get_message_sender(message_id)
                if sender and sender != username:
                    # notify original sender that message was read
                    await self.send_to_user(sender, {
                        "type": "read_receipt",
                        "message_id": message_id,
                        "reader": username
                    })
            return

        # Edit message event
        if message_type == 'edit_message':
            message_id = data.get('message_id')
            new_content = data.get('new_content', '').strip()
            if not message_id or not new_content:
                return
            success, error = db.edit_message(message_id, username, new_content)
            if not success:
                await self.send_to_user(username, {
                    "type": "edit_message_result",
                    "success": False,
                    "error": error,
                    "message_id": message_id
                })
                return
            # Fetch updated message
            cursor = db.conn.cursor()
            cursor.execute("SELECT id, username, message, timestamp, edited, edited_timestamp FROM messages WHERE id = ?", (message_id,))
            row = cursor.fetchone()
            if row:
                msg_data = {
                    "type": "message_edit_broadcast",
                    "id": row[0],
                    "username": row[1],
                    "message": row[2],
                    "timestamp": row[3],
                    "edited": bool(row[4]),
                    "edited_timestamp": row[5]
                }
                # Broadcast to all relevant users (global or DM)
                target = data.get('target', 'global')
                await self.send_to_target(username, target, msg_data)
            await self.send_to_user(username, {
                "type": "edit_message_result",
                "success": True,
                "message_id": message_id
            })
            return

        # Soft delete message event
        if message_type == 'delete_message':
            message_id = data.get('message_id')
            if not message_id:
                return
            success, error = db.soft_delete_message(message_id, username)
            if not success:
                await self.send_to_user(username, {
                    "type": "delete_message_result",
                    "success": False,
                    "error": error,
                    "message_id": message_id
                })
                return
            # Fetch updated message
            cursor = db.conn.cursor()
            cursor.execute("SELECT id, username, message, timestamp, edited, edited_timestamp, is_deleted FROM messages WHERE id = ?", (message_id,))
            row = cursor.fetchone()
            if row:
                msg_data = {
                    "type": "message_delete_broadcast",
                    "id": row[0],
                    "username": row[1],
                    "message": row[2],
                    "timestamp": row[3],
                    "edited": bool(row[4]),
                    "edited_timestamp": row[5],
                    "is_deleted": bool(row[6])
                }
                target = data.get('target', 'global')
                await self.send_to_target(username, target, msg_data)
            await self.send_to_user(username, {
                "type": "delete_message_result",
                "success": True,
                "message_id": message_id
            })
            return

        content = data.get('content', '').strip()
        target = data.get('target', 'global')

        # normalize target
        if not target:
            target = 'global'

        if not content:
            return False

        is_banned, _, _ = db.is_user_banned(username)
        if is_banned:
            return False

        has_bad_word, bad_word = contains_bad_word(content)
        if has_bad_word:
            count = self.violations.get(username, 0) + 1
            self.violations[username] = count
            # record strike in audit log
            db.log_audit(username, f"strike {count} word {bad_word}")
            if count == 1:
                await self.send_to_user(username, {
                    "type": "warning",
                    "message": f" 1st Strike: '{bad_word}' (1/6) → Next = 5min BAN!"
                })
            elif count == 2:
                ban_until = db.ban_user(username, "temp", 300, strike_count=count)  # 5 min ban
                await self.send_to_user(username, {
                    "type": "banned",
                    "message": " 2nd Strike: 5 MINUTE BAN (2/6)",
                    "ban_until": ban_until,
                    "ban_type": "temp"
                })
            elif count == 3:
                ban_until = db.ban_user(username, "temp", 3600, strike_count=count)  # 1 hour ban
                await self.send_to_user(username, {
                    "type": "banned",
                    "message": " 3rd Strike: 1 HOUR BAN (3/6)",
                    "ban_until": ban_until,
                    "ban_type": "temp"
                })
            elif count == 4:
                ban_until = db.ban_user(username, "temp", 43200, strike_count=count)  # 12 hour ban
                await self.send_to_user(username, {
                    "type": "banned",
                    "message": " 4th Strike: 12 HOUR BAN (4/6)",
                    "ban_until": ban_until,
                    "ban_type": "temp"
                })
            elif count == 5:
                ban_until = db.ban_user(username, "temp", 86400, strike_count=count)  # 24 hour ban
                await self.send_to_user(username, {
                    "type": "banned",
                    "message": " 5th Strike: 24 HOUR BAN (5/6)",
                    "ban_until": ban_until,
                    "ban_type": "temp"
                })
            elif count >= 6:
                ban_until = db.ban_user(username, "permanent", None, strike_count=count)
                await self.send_to_user(username, {
                    "type": "banned",
                    "message": " 6th Strike: PERMANENT BAN (6/6)",
                    "ban_until": ban_until,
                    "ban_type": "permanent"
                })
            return False
    

        timestamp = datetime.datetime.now().isoformat()
        message_id = db.save_message(username, content, target)
        
        msg_data = {
            "type": "message",
            "username": username,
            "message": content,
            "timestamp": timestamp,
            "target": target,
            "isDM": target != "global",
            "id": message_id,
            "edited": False,
            "edited_timestamp": None,
            "is_deleted": False
        }
        await self.send_to_target(username, target, msg_data)
        return True
    
    async def clear_typing(self, username: str, target: str):
        """Auto-clear typing indicator after inactivity timeout."""
        await asyncio.sleep(4)
        # Only clear if no new typing event has occurred in the last 3.9s
        key = f"{username}:{target}"
        last_typing = self.typing_status.get(key, 0)
        current_time = time.time()
        
        if current_time - last_typing >= 3.9:
            self.typing_status.pop(key, None)
            typing_msg = {
                "type": "typing",
                "username": username,
                "isTyping": False,
                "target": target
            }
            await self.send_to_user(target, typing_msg)
            await self.send_to_user(username, typing_msg)

manager = ConnectionManager()

def check_admin_auth(request: Request, key: Optional[str] = None) -> bool:
    admin_key = key or request.cookies.get("admin_auth")
    return admin_key == ADMIN_PASSWORD

@app.get("/enterprise/login", response_class=HTMLResponse)
async def admin_login_page():
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Admin Login</title>
    <style>
        body { font-family: Arial; max-width: 400px; margin: 100px auto; padding: 20px; }
        input { width: 100%; padding: 10px; margin: 10px 0; box-sizing: border-box; }
        button { width: 100%; padding: 10px; background: #007bff; color: white; border: none; cursor: pointer; }
    </style>
    </head>
    <body>
        <h2>Admin Login</h2>
        <form action="/enterprise/auth" method="post">
            <input type="password" name="password" placeholder="Enter admin password" required>
            <button type="submit">Login → Admin Panel</button>
        </form>
    </body>
    </html>
    """

@app.post("/enterprise/auth")
async def admin_auth(password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        response = RedirectResponse(url=f"/enterprise?key={ADMIN_PASSWORD}", status_code=303)
        response.set_cookie(key="admin_auth", value=ADMIN_PASSWORD, httponly=True)
        return response
    raise HTTPException(401, "Wrong Password!")


@app.get("/enterprise")
async def enterprise_page(request: Request, key: Optional[str] = None):
    if not check_admin_auth(request, key):
        return RedirectResponse(url="/enterprise/login", status_code=303)
    print("ADMIN PANEL ACCESS!")
    return FileResponse("web/admin.html")

@app.get("/enterprise/unban/{username}")
async def enterprise_unban(username: str, request: Request, key: Optional[str] = None):
    if not check_admin_auth(request, key):
        raise HTTPException(status_code=401, detail="Unauthorized")

    # perform unban in database and notify user next time they connect
    db.unban_user(username)
    manager.unban_notifications.add(username)
    return {"message": f"{username} successfully unbanned"}

@app.get("/enterprise/stats")
async def stats(request: Request, key: Optional[str] = None):
    if not check_admin_auth(request, key):
        raise HTTPException(401, "Unauthorized")
    cursor = db.conn.cursor()
    cursor.execute("SELECT username FROM users")
    all_users = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT COUNT(*) FROM users")
    registered_users = cursor.fetchone()[0]
    banned_users = db.get_ban_list()
    return {
        "online_count": len(manager.active_connections),
        "online_users": list(manager.user_online_status),
        "registered_users": registered_users,
        "all_users": all_users,
        "total_messages": db.get_total_messages(),
        "banned_users": [
            {"username": row[0], "ban_type": row[1], "ban_until": row[2], "strike_count": row[3], "unbanned_at": row[4]} for row in banned_users
        ]
    }

@app.get("/enterprise/download/users")
async def download_users(request: Request, key: Optional[str] = None):
    if not check_admin_auth(request, key):
        raise HTTPException(401, "Unauthorized")
    cursor = db.conn.cursor()
    cursor.execute("SELECT username, password FROM users")
    users_data = cursor.fetchall()
    filename = f"users_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return create_csv_response(users_data, ['Username', 'Password'], filename)

@app.get("/enterprise/download/messages")
async def download_messages(request: Request, key: Optional[str] = None):
    if not check_admin_auth(request, key):
        raise HTTPException(401, "Unauthorized")
    cursor = db.conn.cursor()
    cursor.execute("SELECT username, target, message, timestamp, edited, edited_timestamp, is_deleted FROM messages")
    messages_data = cursor.fetchall()
    filename = f"messages_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    headers = ['Type', 'Sender', 'Target', 'Message', 'Timestamp', 'Edited', 'Edited Timestamp', 'Is Deleted']
    # convert rows to include Type column
    rows = []
    for row in messages_data:
        sender, target, msg, ts, edited, edt_ts, is_del = row
        mtype = 'DM' if target != 'global' else 'Global'
        rows.append((mtype, sender, target, msg, ts, edited, edt_ts, is_del))
    return create_csv_response(rows, headers, filename)

@app.get("/enterprise/download/ban-report")
async def download_ban_report(request: Request, key: Optional[str] = None):
    if not check_admin_auth(request, key):
        raise HTTPException(401, "Unauthorized")
    ban_data = db.get_ban_list(include_expired=True)
    filename = f"ban_report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    headers = ['Username', 'Ban Type', 'Ban Until', 'Strike Count', 'Created At', 'Unbanned At', 'Hours Banned']
    rows = []
    for username, ban_type, ban_until, strike_count, unbanned_at, created_at in ban_data:
        hours = ''
        if created_at:
            try:
                created_ts = int(datetime.datetime.fromisoformat(created_at).timestamp())
                end_ts = None
                if unbanned_at:
                    # use unbanned time if the ban was cleared early
                    end_ts = int(datetime.datetime.fromisoformat(unbanned_at).timestamp())
                elif ban_until is not None:
                    end_ts = ban_until
                if end_ts is not None:
                    hours = round((end_ts - created_ts) / 3600, 2)
            except Exception:
                hours = ''
        rows.append((username, ban_type, ban_until, strike_count, created_at, unbanned_at or '', hours))
    return create_csv_response(rows, headers, filename)

@app.get("/enterprise/download/audit")
async def download_audit(request: Request, key: Optional[str] = None):
    if not check_admin_auth(request, key):
        raise HTTPException(401, "Unauthorized")
    logs = db.get_audit_logs()
    filename = f"audit_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    headers = ['Username', 'Event', 'Timestamp']
    return create_csv_response(logs, headers, filename)
# Endpoint to show active bans (persistent, based on bans table)
@app.get("/enterprise/ban-list")
async def ban_list(request: Request, key: Optional[str] = None):
    if not check_admin_auth(request, key):
        raise HTTPException(401, "Unauthorized")
    bans = db.get_ban_list()
    return {"bans": [
        {"username": row[0], "ban_type": row[1], "ban_until": row[2], "strike_count": row[3], "unbanned_at": row[4], "created_at": row[5]} for row in bans
    ]}

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

@app.get("/online")  
async def online():
    return {
        "count": len(manager.user_online_status), 
        "users": list(manager.user_online_status)
    }

@app.get("/", response_class=FileResponse)
async def root():
    return FileResponse("web/index.html")

@app.get("/chat", response_class=FileResponse)
async def chat():
    return FileResponse("web/chat.html")

@app.get("/chat.html", response_class=FileResponse)  
async def chat_html():
    return FileResponse("web/chat.html")

@app.get("/register.html", response_class=FileResponse)
async def register_page():
    return FileResponse("web/register.html")

@app.get("/forgot-password.html", response_class=FileResponse)
async def forgot_password_page():
    return FileResponse("web/forgot-password.html")

@app.get("/reset-password.html", response_class=FileResponse)
async def reset_password_page():
    return FileResponse("web/reset-password.html")

@app.post("/register")
async def register(user: UserRegister):
    # Database only supports username and password
    if db.register_user(user.username, user.password):
        return {"message": "Registered!", "redirect": "/chat.html?username=" + user.username}
    raise HTTPException(400, "Username exists")

@app.post("/login")
async def login(user: UserLogin):
    username = db.authenticate_user(user.username, user.password)
    if username:
        # record audit
        db.log_audit(username, "login")
        return {"message": "Logged in!", "redirect": f"/chat.html?username={username}"}
    raise HTTPException(401, "Invalid credentials")

@app.post("/forgot-password")
async def forgot_password(request: ForgotPasswordRequest):
    import secrets
    
    # Check if user exists
    if not db.user_exists(request.username):
        raise HTTPException(400, "Username not found")
    
    # Generate a secure random token
    token = secrets.token_urlsafe(32)
    
    # Store token in database
    if not db.create_password_reset_token(request.username, token):
        raise HTTPException(500, "Error creating reset token")
    
    # In a real application, you would send this via email
    # For now, we'll return the token for testing (should be shown in frontend)
    reset_link = f"/reset-password.html?username={request.username}&token={token}"
    
    return {
        "message": "Password reset instructions sent",
        "reset_link": reset_link,  # In production, don't return this - send via email instead
        "note": "In production, this would be sent via email"
    }

@app.post("/reset-password")
async def reset_password(request: ResetPasswordRequest):
    success, message = db.reset_password(request.username, request.token, request.new_password)
    
    if success:
        db.log_audit(request.username, "password_reset")
        return {"message": message}
    
    raise HTTPException(400, message)

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    connected = await manager.connect(websocket, username)
    if not connected:
        return
    
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message_data = json.loads(data)
            except json.JSONDecodeError:
                message_data = {"type": "message", "content": data, "target": "global"}
            
            await manager.handle_message(username, message_data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        # record logout audit
        db.log_audit(username, "logout")
    except Exception as e:
        print(f"WebSocket ERROR {username}: {e}")
        manager.disconnect(websocket)
        db.log_audit(username, "logout")

app.mount("/web", StaticFiles(directory="web"), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
