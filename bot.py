#!/usr/bin/env python3
"""
Telegram Bot + Dashboard
Runs on Render Web Service (free).
Admin Chat ID: 5682792112
"""
import os
import sys
import logging
import tempfile
import zlib
import re
import asyncio
import time
import sqlite3
import traceback
import threading
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, request, render_template_string, redirect, url_for, session, flash, abort
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

# ----------------------------------------------
# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database
DB_PATH = "bot_settings.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        username TEXT PRIMARY KEY,
        password TEXT
    )''')
    c.execute("INSERT OR IGNORE INTO admins (username, password) VALUES (?, ?)",
              ("r3nz75", "r3nz75converter2027"))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("bot_token", ""))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("bot_name", "Image↔C Header Converter"))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("logo_url", "https://cdn-icons-png.flaticon.com/512/60/60580.png"))
    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_admin(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT password FROM admins WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def update_admin(username, new_password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE admins SET password=? WHERE username=?", (new_password, username))
    conn.commit()
    conn.close()

init_db()

# ----------------------------------------------
# Flask app
app = Flask(__name__)
app.secret_key = os.urandom(24)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ---- Login page ----
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><title>Login</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
<style>
    body { display: flex; justify-content: center; align-items: center; height: 100vh; background: #f0f2f5; font-family: sans-serif; }
    .login-box { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); width: 340px; }
    .login-box h2 { margin-bottom: 20px; text-align: center; }
    .form-group { margin-bottom: 16px; }
    .form-group label { display: block; margin-bottom: 4px; }
    .form-group input { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; }
    .btn { width: 100%; padding: 10px; background: #1abc9c; color: white; border: none; border-radius: 6px; cursor: pointer; }
    .flash { padding: 10px; margin-bottom: 16px; border-radius: 6px; }
    .flash.danger { background: #e74c3c; color: white; }
    .flash.success { background: #2ecc71; color: white; }
</style>
</head>
<body>
<div class="login-box">
    <h2><i class="fas fa-lock"></i> Admin Login</h2>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for category, message in messages %}
          <div class="flash {{ category }}">{{ message }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    <form method="post">
        <div class="form-group">
            <label>Username</label>
            <input type="text" name="username" required>
        </div>
        <div class="form-group">
            <label>Password</label>
            <input type="password" name="password" required>
        </div>
        <button type="submit" class="btn"><i class="fas fa-sign-in-alt"></i> Login</button>
    </form>
</div>
</body>
</html>
"""

# ---- Base layout (sidebar) ----
BASE_LAYOUT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bot Dashboard</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f2f5; display: flex; min-height: 100vh; }
        .sidebar { width: 260px; background: #2c3e50; color: white; padding: 30px 20px; display: flex; flex-direction: column; }
        .sidebar h2 { margin-bottom: 30px; font-weight: 300; }
        .sidebar a { color: #ecf0f1; text-decoration: none; padding: 12px 16px; border-radius: 8px; margin-bottom: 6px; display: flex; align-items: center; transition: 0.2s; }
        .sidebar a i { width: 24px; margin-right: 12px; }
        .sidebar a:hover { background: #34495e; }
        .sidebar a.active { background: #1abc9c; color: white; }
        .content { flex: 1; padding: 30px; }
        .card { background: white; border-radius: 12px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 24px; }
        .card h3 { margin-bottom: 16px; color: #2c3e50; }
        .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px,1fr)); gap: 16px; }
        .stat-item { background: #f8f9fa; padding: 20px; border-radius: 8px; text-align: center; }
        .stat-item i { font-size: 2rem; color: #1abc9c; }
        .stat-item .number { font-size: 2rem; font-weight: bold; margin: 8px 0; }
        .stat-item .label { color: #7f8c8d; }
        .commands-list { list-style: none; padding: 0; }
        .commands-list li { padding: 8px 0; border-bottom: 1px solid #ecf0f1; display: flex; justify-content: space-between; }
        .commands-list li .cmd { font-weight: 600; color: #2c3e50; }
        .commands-list li .desc { color: #7f8c8d; }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; font-weight: 600; margin-bottom: 4px; }
        .form-group input, .form-group textarea { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 1rem; }
        .btn { background: #1abc9c; color: white; padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: 1rem; transition: 0.2s; }
        .btn:hover { background: #16a085; }
        .btn-danger { background: #e74c3c; }
        .btn-danger:hover { background: #c0392b; }
        .flash { background: #f39c12; color: white; padding: 12px; border-radius: 6px; margin-bottom: 16px; }
        .flash.success { background: #2ecc71; }
        .logo-preview { max-width: 100px; max-height: 100px; border-radius: 50%; }
    </style>
</head>
<body>
    <div class="sidebar">
        <h2><i class="fas fa-robot"></i> Bot Control</h2>
        <a href="{{ url_for('dashboard') }}" class="{% if active == 'dashboard' %}active{% endif %}"><i class="fas fa-tachometer-alt"></i> Dashboard</a>
        <a href="{{ url_for('settings') }}" class="{% if active == 'settings' %}active{% endif %}"><i class="fas fa-cog"></i> Settings</a>
        <a href="{{ url_for('commands') }}" class="{% if active == 'commands' %}active{% endif %}"><i class="fas fa-list-ul"></i> Commands</a>
        <a href="{{ url_for('logout') }}" style="margin-top: auto;"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </div>
    <div class="content">
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="flash {{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </div>
</body>
</html>
"""

# ---- Routes ----
@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        stored = get_admin(username)
        if stored and stored == password:
            session['logged_in'] = True
            session['username'] = username
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials', 'danger')
    return render_template_string(LOGIN_HTML)

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out', 'success')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    try:
        bot_name = get_setting('bot_name') or 'Image↔C Header Converter'
        bot_token = get_setting('bot_token') or os.getenv('TELEGRAM_BOT_TOKEN', 'Not set')
        logo_url = get_setting('logo_url') or 'https://cdn-icons-png.flaticon.com/512/60/60580.png'
        token_display = bot_token[:8] + '...' if bot_token and len(bot_token) > 8 else 'Not set'

        commands = [
            {'cmd': '/start', 'desc': 'Show welcome menu'},
            {'cmd': '/help', 'desc': 'Show help'},
            {'cmd': 'Send Image as Document', 'desc': 'Convert image to C header (.h)'},
            {'cmd': 'Send .h file', 'desc': 'Recover original image from header'},
            {'cmd': 'Home / Convert / Commands / Usage / Profile', 'desc': 'Keyboard navigation buttons'},
        ]
        if session.get('username') == 'r3nz75':
            commands.extend([
                {'cmd': '/setlimit', 'desc': 'Admin: Set user daily limit'},
                {'cmd': '/resetlimit', 'desc': 'Admin: Reset user count'},
                {'cmd': '/setpremium', 'desc': 'Admin: Set unlimited for user'},
                {'cmd': '/stats', 'desc': 'Admin: View user stats'},
            ])

        now_str = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
        content = f"""
        <div class="card">
            <h3><i class="fas fa-chart-simple"></i> Overview</h3>
            <div class="stat-grid">
                <div class="stat-item">
                    <i class="fas fa-command"></i>
                    <div class="number">{len(commands)}</div>
                    <div class="label">Total Commands</div>
                </div>
                <div class="stat-item">
                    <i class="fas fa-users"></i>
                    <div class="number">0</div>
                    <div class="label">Active Users (coming soon)</div>
                </div>
                <div class="stat-item">
                    <i class="fas fa-clock"></i>
                    <div class="number">{now_str}</div>
                    <div class="label">Server Time</div>
                </div>
            </div>
        </div>
        <div class="card">
            <h3><i class="fas fa-info-circle"></i> Bot Info</h3>
            <p><strong>Bot Name:</strong> {bot_name}</p>
            <p><strong>Token:</strong> {token_display}</p>
            <p><strong>Logo:</strong> <img src="{logo_url}" class="logo-preview" alt="Logo"></p>
        </div>
        """
        return render_template_string(BASE_LAYOUT.replace('{% block content %}{% endblock %}', '{% block content %}' + content + '{% endblock %}'), active='dashboard')
    except Exception as e:
        logger.error(f"Dashboard error: {e}\n{traceback.format_exc()}")
        return "Internal Server Error", 500

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    try:
        if request.method == 'POST':
            new_token = request.form.get('bot_token', '').strip()
            new_name = request.form.get('bot_name', '').strip()
            new_logo = request.form.get('logo_url', '').strip()
            new_password = request.form.get('admin_password', '').strip()
            if new_token:
                set_setting('bot_token', new_token)
            if new_name:
                set_setting('bot_name', new_name)
            if new_logo:
                set_setting('logo_url', new_logo)
            if new_password:
                update_admin(session['username'], new_password)
            flash('Settings updated! (Token changes require a restart to take effect)', 'success')
            return redirect(url_for('settings'))

        bot_token = get_setting('bot_token') or ''
        bot_name = get_setting('bot_name') or 'Image↔C Header Converter'
        logo_url = get_setting('logo_url') or 'https://cdn-icons-png.flaticon.com/512/60/60580.png'

        content = f"""
        <div class="card">
            <h3><i class="fas fa-cog"></i> Bot Settings</h3>
            <form method="post">
                <div class="form-group">
                    <label>Bot Token (changes require restart)</label>
                    <input type="text" name="bot_token" value="{bot_token}" placeholder="Enter new token">
                </div>
                <div class="form-group">
                    <label>Bot Name</label>
                    <input type="text" name="bot_name" value="{bot_name}" placeholder="Bot display name">
                </div>
                <div class="form-group">
                    <label>Logo URL</label>
                    <input type="text" name="logo_url" value="{logo_url}" placeholder="https://example.com/logo.png">
                    <img src="{logo_url}" class="logo-preview" style="margin-top:8px;" alt="Logo preview">
                </div>
                <div class="form-group">
                    <label>Change Admin Password (leave blank to keep current)</label>
                    <input type="password" name="admin_password" placeholder="New password">
                </div>
                <button type="submit" class="btn"><i class="fas fa-save"></i> Save Changes</button>
            </form>
        </div>
        """
        return render_template_string(BASE_LAYOUT.replace('{% block content %}{% endblock %}', '{% block content %}' + content + '{% endblock %}'), active='settings')
    except Exception as e:
        logger.error(f"Settings error: {e}\n{traceback.format_exc()}")
        return "Internal Server Error", 500

@app.route('/commands')
@login_required
def commands():
    try:
        cmd_list = [
            {'cmd': '/start', 'desc': 'Show welcome menu and keyboard.'},
            {'cmd': '/help', 'desc': 'Display help text.'},
            {'cmd': 'Send Image as Document', 'desc': 'Convert image to C header (.h).'},
            {'cmd': 'Send .h file', 'desc': 'Recover original image from header.'},
            {'cmd': 'Home / Convert / Commands / Usage / Profile', 'desc': 'Keyboard buttons for quick navigation.'},
        ]
        if session.get('username') == 'r3nz75':
            cmd_list.extend([
                {'cmd': '/setlimit', 'desc': 'Admin: Set user daily limit.'},
                {'cmd': '/resetlimit', 'desc': 'Admin: Reset user count.'},
                {'cmd': '/setpremium', 'desc': 'Admin: Set unlimited for user.'},
                {'cmd': '/stats', 'desc': 'Admin: View user stats.'},
            ])

        items = ''.join([f'<li><span class="cmd">{c["cmd"]}</span><span class="desc">{c["desc"]}</span></li>' for c in cmd_list])
        content = f"""
        <div class="card">
            <h3><i class="fas fa-list-ul"></i> Available Commands</h3>
            <ul class="commands-list">
                {items}
            </ul>
        </div>
        """
        return render_template_string(BASE_LAYOUT.replace('{% block content %}{% endblock %}', '{% block content %}' + content + '{% endblock %}'), active='commands')
    except Exception as e:
        logger.error(f"Commands error: {e}\n{traceback.format_exc()}")
        return "Internal Server Error", 500

# ----------------------------------------------
# Telegram Bot Handlers (copied from your working code)
# All your original functions – start, help, handle_menu, handle_file,
# admin commands, queue, progress, conversion, etc.

# -------------------- KEYBOARD --------------------
def get_menu_keyboard():
    keyboard = [["Home", "Convert", "Commands"], ["Usage", "Profile"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

# -------------------- CPU WORK --------------------
def generate_header_from_data(data: bytes, original_filename: str) -> bytes:
    crc = zlib.crc32(data)
    file_size = len(data)
    stem = re.sub(r'[^a-zA-Z0-9_]', '_', Path(original_filename).stem)
    array_name = stem + "_data"
    lines = [
        "// Automatically generated by AstroStar Bot",
        f"// Original file: {original_filename}",
        "// Coverter by: @r3nz75\n",
        "// Channel: https://t.me/WashiWashi123",
        f"unsigned char {array_name}[] = {{"
    ]
    chunk_size = 4096
    total_bytes = len(data)
    processed = 0
    hex_parts = []
    while processed < total_bytes:
        chunk = data[processed:processed+chunk_size]
        hex_parts.append(", ".join(f"0x{b:02X}" for b in chunk))
        processed += len(chunk)
    hex_lines = ",\n    ".join(hex_parts)
    lines.append(f"    {hex_lines}")
    lines.append("};")
    return "\n".join(lines).encode('utf-8')

def recover_image_from_header(content: str) -> tuple[bytes, str]:
    match = re.search(
        r'(?:const\s+uint8_t|unsigned\s+char)\s+(\w+)\s*\[\]\s*=\s*\{([^}]*)\};',
        content,
        re.DOTALL
    )
    if not match:
        raise ValueError("No array found")
    hex_bytes = re.findall(r'0x([0-9A-Fa-f]{2})', match.group(2))
    if not hex_bytes:
        raise ValueError("No hex data")
    data = bytearray(int(h, 16) for h in hex_bytes)
    name_match = re.search(r'// Original file:\s*(.+?)\s*\n', content)
    original_name = name_match.group(1).strip() if name_match else "recovered.png"
    return bytes(data), original_name

# -------------------- PROGRESS THROTTLING --------------------
progress_state = defaultdict(lambda: {"last_percent": -1, "last_time": 0})

async def update_progress(message, percent, text=""):
    if percent > 100:
        percent = 100
    msg_id = message.message_id
    state = progress_state[msg_id]
    now = time.time()
    if (abs(percent - state["last_percent"]) >= 5 or percent == 100) and (now - state["last_time"] >= 1.0):
        bar = '█' * int(20 * percent / 100) + '░' * (20 - int(20 * percent / 100))
        progress_text = f"⏳ {text} {percent}%\n`{bar}`"
        try:
            await message.edit_text(progress_text, parse_mode="Markdown")
            state["last_percent"] = percent
            state["last_time"] = now
        except Exception as e:
            logging.warning(f"Progress edit failed: {e}")

# -------------------- FORMATTED MESSAGES (Markdown v1) --------------------
def fmt_home():
    return (
        "*🤖 Image ↔ C Header Converter*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Send me an *image file* (as a document) → I'll give you a `.h` header.\n"
        "Send me a `.h` header file → I'll recover the original image.\n\n"
        "⚠️ *Important*: Send images as **file** (not photo) to avoid compression.\n\n"
        "Use the buttons below."
    )

def fmt_convert():
    return (
        "*🔄 Convert*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Send me an *image file* (JPG, PNG, WEBP, etc.) as a document.\n"
        "I'll convert it to a C header `.h` file.\n\n"
        "To recover, send me a `.h` file and I'll give back the original image."
    )

def fmt_commands():
    return (
        "*📖 Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• `/start` – Show this menu\n"
        "• `/help` – Show help\n"
        "• *Image → Header*: Send any image as document\n"
        "• *Header → Image*: Send any `.h` header file\n\n"
        "You can also use the buttons below."
    )

def fmt_usage():
    return (
        "*ℹ️ Usage Guide*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ *Convert image to header:*\n"
        "   Send an image (as a file) – you'll get back a `.h` file.\n\n"
        "2️⃣ *Recover image from header:*\n"
        "   Send that `.h` file – you'll get back the exact original image.\n\n"
        "3️⃣ *Always send as document* (paperclip icon → File), not as a photo, "
        "to preserve the original bytes.\n\n"
        "The header contains a CRC32 checksum for integrity verification."
    )

def fmt_profile(update: Update):
    user = update.effective_user
    return (
        f"*👤 Your Profile*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• ID: `{user.id}`\n"
        f"• Name: {user.full_name}\n"
        f"• Username: @{user.username if user.username else 'N/A'}\n"
        f"• Bot: @{update.effective_chat.username or 'N/A'}\n\n"
        "All conversions are stateless – no data is stored."
    )

# -------------------- USER LIMITS AND STATS --------------------
user_stats = {}  # {user_id: {"count": int, "limit": int, "date": str}}
MANILA_TZ = timezone(timedelta(hours=8))
ADMIN_CHAT_ID = 5682792112

def get_today():
    return datetime.now(MANILA_TZ).strftime("%Y-%m-%d")

def check_limit(user_id) -> bool:
    today = get_today()
    if user_id not in user_stats:
        user_stats[user_id] = {"count": 0, "limit": 5, "date": today}
    stats = user_stats[user_id]
    if stats["date"] != today:
        stats["count"] = 0
        stats["date"] = today
    if stats["limit"] == -1:
        return True
    return stats["count"] < stats["limit"]

def increment_count(user_id):
    today = get_today()
    if user_id not in user_stats:
        user_stats[user_id] = {"count": 0, "limit": 5, "date": today}
    stats = user_stats[user_id]
    if stats["date"] != today:
        stats["count"] = 0
        stats["date"] = today
    stats["count"] += 1

def set_limit(user_id, limit):
    today = get_today()
    if user_id not in user_stats:
        user_stats[user_id] = {"count": 0, "limit": limit, "date": today}
    else:
        user_stats[user_id]["limit"] = limit

def reset_count(user_id):
    if user_id in user_stats:
        user_stats[user_id]["count"] = 0
        user_stats[user_id]["date"] = get_today()

# -------------------- ADMIN NOTIFICATIONS (with file) --------------------
async def notify_admin_with_file(update, action, original_file, size, result_filename, file_content):
    user = update.effective_user
    time_str = datetime.now(MANILA_TZ).strftime('%Y-%m-%d %I:%M:%S %p')
    caption = (
        f"*📊 User Activity*\n"
        f"User: @{user.username or user.first_name} (ID: `{user.id}`)\n"
        f"Action: *{action}*\n"
        f"Original: `{original_file}`\n"
        f"Size: {size} bytes\n"
        f"Result: `{result_filename}`\n"
        f"Time: {time_str} (Asia/Manila)"
    )
    try:
        if isinstance(file_content, bytes):
            await update.get_bot().send_document(
                chat_id=ADMIN_CHAT_ID,
                document=file_content,
                filename=result_filename,
                caption=caption,
                parse_mode="Markdown"
            )
        else:
            with open(file_content, 'rb') as f:
                await update.get_bot().send_document(
                    chat_id=ADMIN_CHAT_ID,
                    document=f,
                    filename=result_filename,
                    caption=caption,
                    parse_mode="Markdown"
                )
    except Exception as e:
        logging.error(f"Failed to notify admin with file: {e}")

# -------------------- QUEUE SYSTEM --------------------
processing = False
pending_queue = []
current_processing_user = None

async def do_conversion(update, message, file_obj, file_name, file_ext, is_first=True):
    user = update.effective_user
    progress_msg = await message.reply_text("⏳ Starting...", parse_mode="Markdown")

    if file_ext in ('.h', '.txt'):
        try:
            await update_progress(progress_msg, 10, "Downloading header")
            file = await file_obj.get_file()
            with tempfile.NamedTemporaryFile(suffix=".h", delete=False) as tmp:
                await file.download_to_drive(tmp.name)
                tmp_path = Path(tmp.name)
            await update_progress(progress_msg, 30, "Header downloaded")

            await update_progress(progress_msg, 40, "Parsing header")
            with open(tmp_path, 'r') as f:
                content = f.read()

            await update_progress(progress_msg, 50, "Reconstructing bytes")
            data, original_name = await asyncio.to_thread(recover_image_from_header, content)

            await update_progress(progress_msg, 85, "Preparing file")
            with tempfile.NamedTemporaryFile(suffix=Path(original_name).suffix, delete=False) as out_tmp:
                out_tmp.write(data)
                out_path = Path(out_tmp.name)

            increment_count(user.id)
            file_size = len(data)

            await update_progress(progress_msg, 95, "Uploading")
            await message.reply_document(
                document=open(out_path, 'rb'),
                filename=original_name,
                caption="✅ Recovered",
                parse_mode="Markdown"
            )
            await update_progress(progress_msg, 100, "Done ✅")
            await asyncio.sleep(0.5)
            await progress_msg.delete()

            await notify_admin_with_file(
                update,
                "recovered",
                file_name,
                file_size,
                original_name,
                out_path
            )

            os.unlink(tmp_path)
            os.unlink(out_path)

        except Exception as e:
            logging.error(f"Recovery error for user {user.id}: {e}")
            await progress_msg.edit_text(f"❌ Error: {str(e)}", parse_mode="Markdown")
    else:
        try:
            await update_progress(progress_msg, 10, "Downloading image")
            file = await file_obj.get_file()
            with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as tmp:
                await file.download_to_drive(tmp.name)
                tmp_path = Path(tmp.name)
            await update_progress(progress_msg, 30, "Image downloaded")

            with open(tmp_path, 'rb') as f:
                data = f.read()
            await update_progress(progress_msg, 40, "Reading complete")

            await update_progress(progress_msg, 50, "Generating header")
            header_content = await asyncio.to_thread(generate_header_from_data, data, file_name)

            increment_count(user.id)
            file_size = len(data)

            await update_progress(progress_msg, 92, "Finalizing header")
            header_filename = Path(file_name).stem + ".h"
            await update_progress(progress_msg, 95, "Uploading")
            await message.reply_document(
                document=header_content,
                filename=header_filename,
                caption="✅ Header generated",
                parse_mode="Markdown"
            )
            await update_progress(progress_msg, 100, "Done ✅")
            await asyncio.sleep(0.5)
            await progress_msg.delete()

            await notify_admin_with_file(
                update,
                "converted",
                file_name,
                file_size,
                header_filename,
                header_content
            )

            os.unlink(tmp_path)

        except Exception as e:
            logging.error(f"Conversion error for user {user.id}: {e}")
            await progress_msg.edit_text(f"❌ Error: {str(e)}", parse_mode="Markdown")

async def process_next():
    global processing, current_processing_user
    if processing or not pending_queue:
        return
    processing = True
    item = pending_queue.pop(0)
    current_processing_user = item['user']
    await item['message'].reply_text(
        f"🔄 Your turn is starting now, @{item['user'].username or item['user'].first_name}.",
        parse_mode="Markdown"
    )
    await do_conversion(
        item['update'],
        item['message'],
        item['file_obj'],
        item['file_name'],
        item['file_ext']
    )
    processing = False
    current_processing_user = None
    await process_next()

# -------------------- ADMIN COMMANDS --------------------
async def admin_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Usage: /setlimit <user_id> <limit> (use -1 for unlimited)")
        return
    try:
        user_id = int(args[0])
        limit = int(args[1])
        set_limit(user_id, limit)
        await update.message.reply_text(f"✅ Limit for user {user_id} set to {limit}.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id or limit.")

async def admin_reset_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /resetlimit <user_id>")
        return
    try:
        user_id = int(args[0])
        reset_count(user_id)
        await update.message.reply_text(f"✅ Count for user {user_id} reset to 0.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id.")

async def admin_set_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /setpremium <user_id>")
        return
    try:
        user_id = int(args[0])
        set_limit(user_id, -1)
        await update.message.reply_text(f"✅ User {user_id} is now premium (unlimited).")
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id.")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    if not user_stats:
        await update.message.reply_text("No user stats yet.")
        return
    lines = ["*📊 User Stats*"]
    for uid, stats in user_stats.items():
        limit = "∞" if stats["limit"] == -1 else str(stats["limit"])
        lines.append(f"User {uid}: {stats['count']}/{limit} used today")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# -------------------- BOT HANDLERS --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(fmt_home(), reply_markup=get_menu_keyboard(), parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(fmt_commands(), reply_markup=get_menu_keyboard(), parse_mode="Markdown")

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Home":
        await update.message.reply_text(fmt_home(), reply_markup=get_menu_keyboard(), parse_mode="Markdown")
    elif text == "Convert":
        await update.message.reply_text(fmt_convert(), reply_markup=get_menu_keyboard(), parse_mode="Markdown")
    elif text == "Commands":
        await update.message.reply_text(fmt_commands(), reply_markup=get_menu_keyboard(), parse_mode="Markdown")
    elif text == "Usage":
        await update.message.reply_text(fmt_usage(), reply_markup=get_menu_keyboard(), parse_mode="Markdown")
    elif text == "Profile":
        await update.message.reply_text(fmt_profile(update), reply_markup=get_menu_keyboard(), parse_mode="Markdown")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global processing, pending_queue
    message = update.message
    user = update.effective_user
    logging.info(f"Received file from user {user.id} ({user.username})")

    if not check_limit(user.id):
        await message.reply_text(
            f"❌ You've reached your daily limit. Please wait until tomorrow or contact admin.\n"
            f"Admin: @{ (await update.get_bot().get_chat(ADMIN_CHAT_ID)).username or 'admin' }",
            parse_mode="Markdown"
        )
        return

    if message.document:
        file_obj = message.document
        file_name = file_obj.file_name or "unknown"
        file_ext = Path(file_name).suffix.lower()
    elif message.photo:
        photo = message.photo[-1]
        file_obj = photo
        file_name = "image.jpg"
        file_ext = ".jpg"
        await message.reply_text(
            "⚠️ Photo – may be compressed. Send as file for exact conversion.",
            parse_mode="Markdown"
        )
    else:
        await message.reply_text("❌ Unsupported.", parse_mode="Markdown", reply_markup=get_menu_keyboard())
        return

    if processing:
        pending_queue.append({
            'update': update,
            'message': message,
            'file_obj': file_obj,
            'file_name': file_name,
            'file_ext': file_ext,
            'user': user
        })
        current_name = current_processing_user.username or current_processing_user.first_name
        await message.reply_text(
            f"⏳ User @{current_name} is currently converting.\n"
            f"Please wait until your turn. You are #{len(pending_queue)} in queue.",
            parse_mode="Markdown"
        )
        return

    processing = True
    current_processing_user = user
    await do_conversion(update, message, file_obj, file_name, file_ext)
    processing = False
    current_processing_user = None
    await process_next()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text(
            "⚠️ Internal error – try again.",
            parse_mode="Markdown",
            reply_markup=get_menu_keyboard()
        )

# ----------------------------------------------
# Build the bot application (using token from DB or env)
BOT_TOKEN = get_setting('bot_token') or os.getenv('TELEGRAM_BOT_TOKEN')
if not BOT_TOKEN:
    print("⚠️ No bot token set. Set it in the dashboard or via env.")

request_obj = HTTPXRequest(
    connect_timeout=30.0,
    read_timeout=30.0,
    write_timeout=30.0,
    connection_pool_size=8
)
application = Application.builder().token(BOT_TOKEN).request(request_obj).concurrent_updates(True).build()

# Add handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("setlimit", admin_set_limit))
application.add_handler(CommandHandler("resetlimit", admin_reset_limit))
application.add_handler(CommandHandler("setpremium", admin_set_premium))
application.add_handler(CommandHandler("stats", admin_stats))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
application.add_error_handler(error_handler)

# -------------------- 🔥 FIX: Persistent event loop in background thread --------------------
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

if BOT_TOKEN:
    try:
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        logger.info("✅ Bot application initialized and started.")
    except Exception as e:
        logger.error(f"❌ Failed to initialize application: {e}")

def run_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()

# Start the loop in a daemon thread
thread = threading.Thread(target=run_loop, daemon=True)
thread.start()
logger.info("✅ Event loop running in background thread.")

# ----------------------------------------------
# Webhook endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == 'POST':
        json_data = request.get_json()
        if not json_data:
            abort(400)
        try:
            update = Update.de_json(json_data, application.bot)
            # Submit the coroutine to the running loop
            future = asyncio.run_coroutine_threadsafe(
                application.process_update(update),
                loop
            )
            # Wait for completion (adjust timeout as needed)
            future.result(timeout=30)  # increased from 10 to 30 seconds
        except TimeoutError:
            logger.error("Webhook processing timed out after 30 seconds.")
        except Exception as e:
            logger.error(f"Error processing update: {e}\n{traceback.format_exc()}")
        return 'OK', 200
    return 'Method not allowed', 405

# Set webhook on startup
def set_webhook():
    if not BOT_TOKEN:
        print("No token, skipping webhook setup.")
        return
    external_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not external_url:
        print("RENDER_EXTERNAL_URL not set. Please set it or use a fixed URL.")
        return
    webhook_url = external_url.rstrip('/') + '/webhook'
    import requests
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        data={"url": webhook_url}
    )
    if response.ok:
        print(f"✅ Webhook set to {webhook_url}")
    else:
        print(f"❌ Failed to set webhook: {response.text}")

# ----------------------------------------------
if __name__ == "__main__":
    init_db()
    set_webhook()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
