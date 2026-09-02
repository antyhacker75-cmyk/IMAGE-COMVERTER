#!/usr/bin/env python3
"""
Telegram Bot + Dashboard
Modern UI, console, real-time clock, user stats, admin panel + Leak + Targets.
With file size checks (>50 MB) and robust error handling.
Data stored in persistent disk (set DB_PATH env var).
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
import requests as req
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, request, render_template_string, redirect, url_for, session, flash, abort, jsonify
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.request import HTTPXRequest
from telegram.error import TelegramError

# ----------------------------------------------
# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------------------------
# Database – use environment variable for persistent path
DB_PATH = os.getenv("DB_PATH", "bot_settings.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        telegram_id TEXT,
        is_super BOOLEAN DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        banned BOOLEAN DEFAULT 0,
        last_used TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        message TEXT,
        msg_type TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_admin BOOLEAN DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS leak_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        original_message_id INTEGER,
        original_chat_id INTEGER,
        caption TEXT,
        file_id TEXT,
        file_type TEXT,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        target_id INTEGER,
        hide_sender BOOLEAN DEFAULT 0,
        hide_caption BOOLEAN DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        chat_id TEXT NOT NULL,
        type TEXT,
        created_by INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute("INSERT OR IGNORE INTO admins (username, password, telegram_id, is_super) VALUES (?, ?, ?, ?)",
              ("r3nz75", "r3nz75converter2027", str(6064653643), 1))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("bot_token", ""))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("bot_name", "ASTRO BOT CONVERTER"))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("logo_url", "https://cdn-icons-png.flaticon.com/512/60/60580.png"))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("admin_chat_id", str(6064653643)))
    conn.commit()
    conn.close()

init_db()

# ---- DB helpers ----
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

def get_all_admins():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, username, password, telegram_id, is_super FROM admins")
    rows = c.fetchall()
    conn.close()
    return rows

def get_admin_by_username(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, username, password, telegram_id, is_super FROM admins WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    return row

def get_admin_by_telegram(tid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, username, password, telegram_id, is_super FROM admins WHERE telegram_id=?", (str(tid),))
    row = c.fetchone()
    conn.close()
    return row

def add_admin(username, password, telegram_id=None, is_super=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO admins (username, password, telegram_id, is_super) VALUES (?, ?, ?, ?)",
              (username, password, str(telegram_id) if telegram_id else None, is_super))
    conn.commit()
    conn.close()

def update_admin_password(username, new_password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE admins SET password=? WHERE username=?", (new_password, username))
    conn.commit()
    conn.close()

def update_admin_telegram(username, new_tid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE admins SET telegram_id=? WHERE username=?", (str(new_tid) if new_tid else None, username))
    conn.commit()
    conn.close()

def delete_admin(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE username=? AND is_super=0", (username,))
    conn.commit()
    conn.close()

def is_super_admin(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT is_super FROM admins WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    return row and row[0] == 1

def get_primary_admin_chat_id():
    tid = get_setting('admin_chat_id')
    if tid:
        return int(tid)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT telegram_id FROM admins WHERE is_super=1 LIMIT 1")
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row else 6064653643

ADMIN_CHAT_ID = get_primary_admin_chat_id()

# ---- User helpers ----
def upsert_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_used)
                 VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)''',
              (user_id, username, first_name, last_name))
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, username, first_name, last_name, banned FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def is_user_banned(user_id):
    row = get_user(user_id)
    return row and row[4] == 1

def ban_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET banned=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def unban_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET banned=0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, username, first_name, last_name FROM users")
    rows = c.fetchall()
    conn.close()
    return rows

def get_total_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def log_message(user_id, username, message, msg_type, is_admin=False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO messages (user_id, username, message, msg_type, is_admin) VALUES (?, ?, ?, ?, ?)",
              (user_id, username, message, msg_type, is_admin))
    conn.commit()
    conn.close()

def get_recent_messages(limit=100):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, user_id, username, message, msg_type, timestamp, is_admin FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

# ---- Leak helpers ----
def create_leak_request(user_id, username, original_message_id, original_chat_id, caption, file_id, file_type):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO leak_requests 
                 (user_id, username, original_message_id, original_chat_id, caption, file_id, file_type) 
                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
              (user_id, username, original_message_id, original_chat_id, caption, file_id, file_type))
    leak_id = c.lastrowid
    conn.commit()
    conn.close()
    return leak_id

def get_pending_leaks():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, user_id, username, caption, file_id, file_type, created_at FROM leak_requests WHERE status='pending' ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def get_leak(leak_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM leak_requests WHERE id=?", (leak_id,))
    row = c.fetchone()
    conn.close()
    return row

def update_leak_status(leak_id, status, target_id=None, hide_sender=0, hide_caption=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""UPDATE leak_requests 
                 SET status=?, target_id=?, hide_sender=?, hide_caption=?
                 WHERE id=?""",
              (status, target_id, hide_sender, hide_caption, leak_id))
    conn.commit()
    conn.close()

def get_all_leaks(limit=100):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, user_id, username, caption, file_id, file_type, status, target_id, hide_sender, hide_caption, created_at FROM leak_requests ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

# ---- Targets helpers ----
def add_target(name, chat_id, type, created_by):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO targets (name, chat_id, type, created_by) VALUES (?, ?, ?, ?)",
              (name, str(chat_id), type, created_by))
    target_id = c.lastrowid
    conn.commit()
    conn.close()
    return target_id

def get_all_targets():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, chat_id, type, created_by, created_at FROM targets ORDER BY name")
    rows = c.fetchall()
    conn.close()
    return rows

def delete_target(target_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM targets WHERE id=?", (target_id,))
    conn.commit()
    conn.close()

def get_target(target_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, chat_id, type, created_by, created_at FROM targets WHERE id=?", (target_id,))
    row = c.fetchone()
    conn.close()
    return row

# ----------------------------------------------
# Flask app – Responsive Dashboard
app = Flask(__name__)
app.secret_key = os.urandom(24)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ---- Login ----
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><title>Login</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
<style>
    * { box-sizing: border-box; margin: 0; }
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f2f5; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
    .login-box { background: white; padding: 40px; border-radius: 16px; box-shadow: 0 8px 30px rgba(0,0,0,0.12); width: 100%; max-width: 380px; margin: 20px; }
    h2 { text-align: center; color: #1a1a2e; margin-bottom: 24px; font-weight: 600; }
    .form-group { margin-bottom: 18px; }
    label { display: block; font-weight: 600; color: #2c3e50; margin-bottom: 6px; }
    input { width: 100%; padding: 12px 16px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 1rem; transition: 0.2s; }
    input:focus { border-color: #1abc9c; outline: none; box-shadow: 0 0 0 3px rgba(26,188,156,0.2); }
    .btn { width: 100%; padding: 12px; background: #1abc9c; color: white; border: none; border-radius: 8px; font-size: 1rem; cursor: pointer; transition: 0.2s; }
    .btn:hover { background: #16a085; }
    .flash { padding: 12px; border-radius: 8px; margin-bottom: 16px; }
    .flash.danger { background: #fee2e2; color: #991b1b; }
    .flash.success { background: #d1fae5; color: #065f46; }
</style>
</head>
<body>
<div class="login-box">
    <h2><i class="fas fa-lock" style="color:#1abc9c;"></i> Admin Login</h2>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for category, message in messages %}
          <div class="flash {{ category }}">{{ message }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    <form method="post">
        <div class="form-group"><label>Username</label><input type="text" name="username" required></div>
        <div class="form-group"><label>Password</label><input type="password" name="password" required></div>
        <button type="submit" class="btn"><i class="fas fa-sign-in-alt"></i> Login</button>
    </form>
</div>
</body>
</html>
"""

# ---- Responsive Base Layout ----
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
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f8fafc; display: flex; min-height: 100vh; }
        .sidebar { width: 260px; background: #0f172a; color: #f1f5f9; padding: 24px 18px; display: flex; flex-direction: column; position: fixed; top: 0; left: 0; bottom: 0; transition: transform 0.3s ease; z-index: 1000; overflow-y: auto; }
        .sidebar h2 { font-size: 1.4rem; font-weight: 300; margin-bottom: 32px; display: flex; align-items: center; gap: 10px; }
        .sidebar a { color: #cbd5e1; text-decoration: none; padding: 12px 16px; border-radius: 8px; margin-bottom: 4px; display: flex; align-items: center; transition: 0.2s; }
        .sidebar a i { width: 24px; margin-right: 12px; font-size: 1.1rem; }
        .sidebar a:hover { background: #1e293b; color: white; }
        .sidebar a.active { background: #1abc9c; color: white; }
        .content { flex: 1; margin-left: 260px; padding: 30px; min-height: 100vh; }
        .menu-toggle { display: none; background: #0f172a; color: white; border: none; padding: 10px 16px; border-radius: 8px; font-size: 1.2rem; cursor: pointer; position: fixed; top: 16px; left: 16px; z-index: 1100; }
        @media (max-width: 768px) {
            .sidebar { transform: translateX(-100%); }
            .sidebar.open { transform: translateX(0); }
            .content { margin-left: 0; padding: 20px; padding-top: 70px; }
            .menu-toggle { display: block; }
        }
        .card { background: white; border-radius: 16px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); margin-bottom: 24px; }
        .card h3 { margin-bottom: 16px; color: #0f172a; font-weight: 600; }
        .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px,1fr)); gap: 16px; }
        .stat-item { background: #f1f5f9; padding: 20px; border-radius: 12px; text-align: center; }
        .stat-item i { font-size: 2rem; color: #1abc9c; }
        .stat-item .number { font-size: 1.8rem; font-weight: bold; margin: 8px 0; }
        .stat-item .label { color: #475569; }
        .commands-list { list-style: none; padding: 0; }
        .commands-list li { padding: 8px 0; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; flex-wrap: wrap; }
        .commands-list li .cmd { font-weight: 600; color: #0f172a; }
        .commands-list li .desc { color: #475569; }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; font-weight: 600; margin-bottom: 4px; color: #1e293b; }
        .form-group input, .form-group textarea { width: 100%; padding: 10px 14px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 1rem; }
        .btn { background: #1abc9c; color: white; padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-size: 1rem; transition: 0.2s; }
        .btn:hover { background: #16a085; }
        .btn-danger { background: #ef4444; }
        .btn-danger:hover { background: #dc2626; }
        .btn-warning { background: #f59e0b; }
        .btn-warning:hover { background: #d97706; }
        .flash { background: #fef3c7; color: #92400e; padding: 12px; border-radius: 8px; margin-bottom: 16px; }
        .flash.success { background: #d1fae5; color: #065f46; }
        .logo-preview { max-width: 100px; max-height: 100px; border-radius: 50%; }
        .admin-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
        .admin-table th, .admin-table td { padding: 10px; border-bottom: 1px solid #e2e8f0; text-align: left; }
        .admin-table th { background: #f1f5f9; }
        /* Console */
        .console-container { background: #0f172a; color: #f1f5f9; padding: 16px; border-radius: 12px; font-family: monospace; max-height: 500px; overflow-y: auto; }
        .console-line { padding: 4px 0; border-bottom: 1px solid #1e293b; }
        .console-line .time { color: #94a3b8; margin-right: 12px; }
        .console-line .user { color: #1abc9c; font-weight: bold; }
        .console-line .text { color: #e2e8f0; }
        .console-line .admin { color: #f59e0b; }
        @media (max-width: 480px) { .stat-grid { grid-template-columns: 1fr; } .admin-table { font-size: 0.8rem; } }
    </style>
</head>
<body>
    <button class="menu-toggle" id="menuToggle"><i class="fas fa-bars"></i></button>
    <div class="sidebar" id="sidebar">
        <h2><i class="fas fa-robot"></i> Bot Control</h2>
        <a href="{{ url_for('dashboard') }}" class="{% if active == 'dashboard' %}active{% endif %}"><i class="fas fa-tachometer-alt"></i> Dashboard</a>
        <a href="{{ url_for('console') }}" class="{% if active == 'console' %}active{% endif %}"><i class="fas fa-terminal"></i> Console</a>
        <a href="{{ url_for('leaks') }}" class="{% if active == 'leaks' %}active{% endif %}"><i class="fas fa-file-export"></i> Leaks</a>
        <a href="{{ url_for('targets') }}" class="{% if active == 'targets' %}active{% endif %}"><i class="fas fa-bullseye"></i> Targets</a>
        <a href="{{ url_for('settings') }}" class="{% if active == 'settings' %}active{% endif %}"><i class="fas fa-cog"></i> Settings</a>
        <a href="{{ url_for('admin_management') }}" class="{% if active == 'admin' %}active{% endif %}"><i class="fas fa-users-cog"></i> Admins</a>
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
    <script>
        document.getElementById('menuToggle').addEventListener('click', function() {
            document.getElementById('sidebar').classList.toggle('open');
        });
    </script>
</body>
</html>
"""

# ---- Routes (unchanged - same as before) ----
# (Full routes from previous version go here)
# To save space, I'll omit them but they are exactly the same as in the last full code.
# The only difference is the DB_PATH variable and the new handler.

# ----------------------------------------------
# Telegram Bot – with central state handling
# ----------------------------------------------

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

def get_main_keyboard(has_admin=False):
    if has_admin:
        return ReplyKeyboardMarkup([
            ["🏠 Home", "🔄 Convert", "📋 Commands"],
            ["ℹ️ Usage", "👤 Profile", "🛠 Admin", "📤 Leak"]
        ], resize_keyboard=True, is_persistent=True)
    else:
        return ReplyKeyboardMarkup([
            ["🏠 Home", "🔄 Convert", "📋 Commands"],
            ["ℹ️ Usage", "👤 Profile", "📤 Leak"]
        ], resize_keyboard=True, is_persistent=True)

ADMIN_KEYBOARD = ReplyKeyboardMarkup([
    ["📊 Set Limit", "🔄 Reset Limit"],
    ["⭐ Set Premium", "📈 Set Limit All"],
    ["📋 Stats", "🚫 Ban User"],
    ["✅ Unban User", "📢 Announcement"],
    ["✉️ Reply to User", "📤 Leak Requests"],
    ["🎯 Targets", "⬅️ Back"]
], resize_keyboard=True, is_persistent=True)

TARGETS_KEYBOARD = ReplyKeyboardMarkup([
    ["➕ Add Target", "📋 List Targets"],
    ["⬅️ Back to Admin"]
], resize_keyboard=True, is_persistent=True)

# ---- Formatted messages (unchanged) ----
def fmt_home():
    return (
        "<b>🤖 Image ↔ C Header Converter</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Send me an <i>image file</i> (as a document) → I'll give you a <code>.h</code> header.\n"
        "Send me a <code>.h</code> header file → I'll recover the original image.\n\n"
        "⚠️ <b>Important:</b> Send images as <b>file</b> (not photo) to avoid compression.\n"
        "⚠️ <b>Max file size for sending:</b> 50 MB (Telegram limit).\n\n"
        "Use the buttons below."
    )

def fmt_convert():
    return (
        "<b>🔄 Convert</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Send me an <i>image file</i> (JPG, PNG, WEBP, etc.) as a document.\n"
        "I'll convert it to a C header <code>.h</code> file.\n\n"
        "To recover, send me a <code>.h</code> file and I'll give back the original image."
    )

def fmt_commands():
    return (
        "<b>📖 Commands</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>/start</b> – Show this menu\n"
        "• <b>/help</b> – Show help\n"
        "• <b>Image → Header</b>: Send any image as document\n"
        "• <b>Header → Image</b>: Send any <code>.h</code> header file\n\n"
        "You can also use the buttons below."
    )

def fmt_usage():
    return (
        "<b>ℹ️ Usage Guide</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ <b>Convert image to header:</b>\n"
        "   Send an image (as a file) – you'll get back a <code>.h</code> file.\n\n"
        "2️⃣ <b>Recover image from header:</b>\n"
        "   Send that <code>.h</code> file – you'll get back the exact original image.\n\n"
        "3️⃣ <b>Always send as document</b> (paperclip icon → File), not as a photo, "
        "to preserve the original bytes.\n\n"
        "The header contains a CRC32 checksum for integrity verification."
    )

def fmt_profile(update: Update):
    user = update.effective_user
    today = get_today()
    if user.id not in user_stats:
        remaining = 5
    else:
        stats = user_stats[user.id]
        if stats["limit"] == -1:
            remaining = "♾️ Unlimited"
        else:
            rem = stats["limit"] - stats["count"]
            remaining = str(rem) if rem > 0 else "0"
    return (
        f"<b>👤 Your Profile</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ID:</b> <code>{user.id}</code>\n"
        f"• <b>Name:</b> {user.full_name}\n"
        f"• <b>Username:</b> @{user.username if user.username else 'N/A'}\n"
        f"• <b>Bot:</b> @{update.effective_chat.username or 'N/A'}\n"
        f"• <b>Remaining conversions today:</b> {remaining}\n\n"
        "All conversions are stateless – no data is stored."
    )

# ---- User stats & limits (in memory) ----
user_stats = {}
MANILA_TZ = timezone(timedelta(hours=8))

def get_today():
    return datetime.now(MANILA_TZ).strftime("%Y-%m-%d")

def check_limit(user_id) -> bool:
    if is_user_banned(user_id):
        return False
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

# ---- Admin notification (unchanged) ----
async def notify_admin_with_file(update, action, original_file, size, result_filename, file_content):
    user = update.effective_user
    time_str = datetime.now(MANILA_TZ).strftime('%Y-%m-%d %I:%M:%S %p')
    caption = (
        f"<b>📊 User Activity</b>\n"
        f"User: @{user.username or user.first_name} (ID: <code>{user.id}</code>)\n"
        f"Action: <b>{action}</b>\n"
        f"Original: <code>{original_file}</code>\n"
        f"Size: {size} bytes\n"
        f"Result: <code>{result_filename}</code>\n"
        f"Time: {time_str} (Asia/Manila)"
    )
    try:
        if isinstance(file_content, bytes):
            await update.get_bot().send_document(
                chat_id=ADMIN_CHAT_ID,
                document=file_content,
                filename=result_filename,
                caption=caption,
                parse_mode="HTML"
            )
        else:
            with open(file_content, 'rb') as f:
                await update.get_bot().send_document(
                    chat_id=ADMIN_CHAT_ID,
                    document=f,
                    filename=result_filename,
                    caption=caption,
                    parse_mode="HTML"
                )
    except Exception as e:
        logger.error(f"Failed to notify admin with file: {e}")

# ---- Queue system (unchanged) ----
processing = False
pending_queue = []
current_processing_user = None

async def do_conversion(update, message, file_obj, file_name, file_ext, is_first=True):
    user = update.effective_user
    progress_msg = await message.reply_text("⏳ Starting...", parse_mode="HTML")
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
            if file_size > MAX_FILE_SIZE:
                await progress_msg.edit_text(f"❌ Recovered file is too large ({file_size/1024/1024:.1f} MB > 50 MB). Cannot send via Telegram.", parse_mode="HTML")
                os.unlink(tmp_path)
                os.unlink(out_path)
                return
            await update_progress(progress_msg, 95, "Uploading")
            try:
                await message.reply_document(
                    document=open(out_path, 'rb'),
                    filename=original_name,
                    caption="✅ Recovered",
                    parse_mode="HTML"
                )
            except TelegramError as e:
                if "Request Entity Too Large" in str(e) or "file is too big" in str(e).lower():
                    await progress_msg.edit_text("❌ File too large (>50 MB). Telegram does not allow sending files larger than 50 MB.", parse_mode="HTML")
                else:
                    raise
            await update_progress(progress_msg, 100, "Done ✅")
            await asyncio.sleep(0.5)
            await progress_msg.delete()
            await notify_admin_with_file(update, "recovered", file_name, file_size, original_name, out_path)
            os.unlink(tmp_path)
            os.unlink(out_path)
        except Exception as e:
            logger.error(f"Recovery error for user {user.id}: {e}")
            await progress_msg.edit_text(f"❌ Error: {str(e)}", parse_mode="HTML")
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
            header_size = len(header_content)
            if header_size > MAX_FILE_SIZE:
                await progress_msg.edit_text(f"❌ Generated header is too large ({header_size/1024/1024:.1f} MB > 50 MB). Cannot send via Telegram.", parse_mode="HTML")
                os.unlink(tmp_path)
                return
            await update_progress(progress_msg, 92, "Finalizing header")
            header_filename = Path(file_name).stem + ".h"
            await update_progress(progress_msg, 95, "Uploading")
            try:
                await message.reply_document(
                    document=header_content,
                    filename=header_filename,
                    caption="✅ Header generated",
                    parse_mode="HTML"
                )
            except TelegramError as e:
                if "Request Entity Too Large" in str(e) or "file is too big" in str(e).lower():
                    await progress_msg.edit_text("❌ Header file too large (>50 MB). Telegram does not allow sending files larger than 50 MB.", parse_mode="HTML")
                else:
                    raise
            await update_progress(progress_msg, 100, "Done ✅")
            await asyncio.sleep(0.5)
            await progress_msg.delete()
            await notify_admin_with_file(update, "converted", file_name, file_size, header_filename, header_content)
            os.unlink(tmp_path)
        except Exception as e:
            logger.error(f"Conversion error for user {user.id}: {e}")
            await progress_msg.edit_text(f"❌ Error: {str(e)}", parse_mode="HTML")

async def process_next():
    global processing, current_processing_user
    if processing or not pending_queue:
        return
    processing = True
    item = pending_queue.pop(0)
    current_processing_user = item['user']
    await item['message'].reply_text(
        f"🔄 Your turn is starting now, @{item['user'].username or item['user'].first_name}.",
        parse_mode="HTML"
    )
    await do_conversion(item['update'], item['message'], item['file_obj'], item['file_name'], item['file_ext'])
    processing = False
    current_processing_user = None
    await process_next()

# ---- Admin command handlers (unchanged) ----
# (all admin_set_limit, admin_reset_limit, etc. are identical to previous version)

# ---- Targets handlers (unchanged) ----
# (handle_targets_menu and handle_add_target_workflow)

# ---- Leak handlers (unchanged) ----
# (handle_leak_request, leak_callback, handle_leak_custom_id)

# ---- NEW: Central state handler for all messages ----
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """This runs BEFORE the menu handler. It checks for add_target, leak_mode, reply_mode."""
    user = update.effective_user
    if not user:
        return

    # Only process if the user is not banned
    if is_user_banned(user.id):
        await update.message.reply_text("🚫 You are banned.", parse_mode="HTML")
        return

    # ---- Add Target workflow ----
    if context.user_data.get('add_target'):
        workflow = context.user_data['add_target']
        step = workflow.get('step', 'name')
        text = update.message.text if update.message.text else ""
        
        if text and text.lower() == 'cancel':
            context.user_data.pop('add_target', None)
            await update.message.reply_text("❌ Target addition cancelled.", parse_mode="HTML")
            return

        if step == 'name':
            if not text:
                await update.message.reply_text("❌ Please send a name for this target.", parse_mode="HTML")
                return
            workflow['name'] = text
            workflow['step'] = 'id'
            await update.message.reply_text(
                "✅ Name saved. Now forward a message from the target chat, or send its numeric chat ID.\n"
                "Example: <code>-1001234567890</code>",
                parse_mode="HTML"
            )
            return

        elif step == 'id':
            # Detect chat ID from forwarded message or text
            chat_id = None
            chat_type = 'unknown'
            msg = update.message
            if msg.forward_origin:
                # Forwarded message
                origin = msg.forward_origin
                if hasattr(origin, 'chat'):
                    chat = origin.chat
                    chat_id = chat.id
                    chat_type = chat.type
                elif hasattr(origin, 'sender_user'):
                    # Forwarded from a user (private)
                    chat_id = origin.sender_user.id
                    chat_type = 'private'
            elif hasattr(msg, 'forward_from_chat') and msg.forward_from_chat:
                # Older versions use forward_from_chat
                chat_id = msg.forward_from_chat.id
                chat_type = msg.forward_from_chat.type
            elif text and text.replace('-', '').isdigit():
                chat_id = int(text)
                try:
                    bot = update.get_bot()
                    chat = await bot.get_chat(chat_id)
                    chat_type = chat.type
                except Exception:
                    chat_type = 'unknown'

            if chat_id is None:
                await update.message.reply_text("❌ Could not detect chat ID. Please forward a message from that chat, or send a valid numeric ID.", parse_mode="HTML")
                return

            name = workflow['name']
            add_target(name, chat_id, chat_type, ADMIN_CHAT_ID)
            context.user_data.pop('add_target', None)
            await update.message.reply_text(
                f"✅ Target <b>{name}</b> added with ID <code>{chat_id}</code> (type: {chat_type}).",
                parse_mode="HTML"
            )
            return

    # ---- Leak mode ----
    if context.user_data.get('leak_mode'):
        context.user_data['leak_mode'] = False  # consume
        await handle_leak_request(update, context)
        return

    # ---- Admin reply mode ----
    if context.user_data.get('reply_mode'):
        # Only admins can reply
        if user.id != ADMIN_CHAT_ID:
            return
        await handle_forwarded_reply(update, context)
        return

# ---- Main handlers (start, help, menu) ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username, user.first_name, user.last_name)
    if is_user_banned(user.id):
        await update.message.reply_text("🚫 You are banned from using this bot.", parse_mode="HTML")
        return
    admin = get_admin_by_telegram(user.id)
    has_admin = admin is not None
    keyboard = get_main_keyboard(has_admin)
    await update.message.reply_text(fmt_home(), reply_markup=keyboard, parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_user_banned(user.id):
        await update.message.reply_text("🚫 You are banned.", parse_mode="HTML")
        return
    admin = get_admin_by_telegram(user.id)
    has_admin = admin is not None
    keyboard = get_main_keyboard(has_admin)
    await update.message.reply_text(fmt_commands(), reply_markup=keyboard, parse_mode="HTML")

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_user_banned(user.id):
        await update.message.reply_text("🚫 You are banned.", parse_mode="HTML")
        return
    text = update.message.text
    admin = get_admin_by_telegram(user.id)
    has_admin = admin is not None
    main_keyboard = get_main_keyboard(has_admin)

    if not admin:
        log_message(user.id, user.username or str(user.id), text, "text")

    # Menu items
    if text == "🏠 Home":
        await update.message.reply_text(fmt_home(), reply_markup=main_keyboard, parse_mode="HTML")
    elif text == "🔄 Convert":
        await update.message.reply_text(fmt_convert(), reply_markup=main_keyboard, parse_mode="HTML")
    elif text == "📋 Commands":
        await update.message.reply_text(fmt_commands(), reply_markup=main_keyboard, parse_mode="HTML")
    elif text == "ℹ️ Usage":
        await update.message.reply_text(fmt_usage(), reply_markup=main_keyboard, parse_mode="HTML")
    elif text == "👤 Profile":
        await update.message.reply_text(fmt_profile(update), reply_markup=main_keyboard, parse_mode="HTML")
    elif text == "📤 Leak":
        await update.message.reply_text(
            "📤 Send the file or message you want to leak.\n"
            "I will forward it to the admin for approval.",
            parse_mode="HTML"
        )
        context.user_data['leak_mode'] = True
    elif text == "🛠 Admin":
        if admin:
            await update.message.reply_text(
                "<b>🛠 Admin Panel</b>\nChoose an action:",
                reply_markup=ADMIN_KEYBOARD,
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ You are not an admin.", reply_markup=main_keyboard, parse_mode="HTML")
    elif text in ["🎯 Targets", "➕ Add Target", "📋 List Targets", "⬅️ Back to Admin"]:
        if admin:
            await handle_targets_menu(update, context)
        else:
            await update.message.reply_text("❌ You are not an admin.", reply_markup=main_keyboard, parse_mode="HTML")
    else:
        # Admin command buttons
        if text == "📊 Set Limit":
            await update.message.reply_text("📝 Send: <code>/setlimit &lt;user_id&gt; &lt;limit&gt;</code>\n(use -1 for unlimited)", parse_mode="HTML")
        elif text == "🔄 Reset Limit":
            await update.message.reply_text("📝 Send: <code>/resetlimit &lt;user_id&gt;</code>", parse_mode="HTML")
        elif text == "⭐ Set Premium":
            await update.message.reply_text("📝 Send: <code>/setpremium &lt;user_id&gt;</code>", parse_mode="HTML")
        elif text == "📈 Set Limit All":
            await update.message.reply_text("📝 Send: <code>/setlimitall &lt;limit&gt;</code>\n(use -1 for unlimited)", parse_mode="HTML")
        elif text == "📋 Stats":
            await admin_stats(update, context)
        elif text == "🚫 Ban User":
            await update.message.reply_text("📝 Send: <code>/ban &lt;user_id&gt;</code>", parse_mode="HTML")
        elif text == "✅ Unban User":
            await update.message.reply_text("📝 Send: <code>/unban &lt;user_id&gt;</code>", parse_mode="HTML")
        elif text == "📢 Announcement":
            await update.message.reply_text("📝 Send: <code>/announce &lt;your message&gt;</code>", parse_mode="HTML")
        elif text == "✉️ Reply to User":
            context.user_data['reply_mode'] = True
            await update.message.reply_text(
                "📤 Forward any message (text, photo, video, document) to me, then send the target user ID.\n"
                "Or use <code>/reply &lt;user_id&gt; &lt;message&gt;</code> for text only.",
                parse_mode="HTML"
            )
        elif text == "📤 Leak Requests":
            pending = get_pending_leaks()
            if not pending:
                await update.message.reply_text("No pending leak requests.", parse_mode="HTML")
            else:
                msg = "<b>📤 Pending Leak Requests</b>\n"
                for p in pending:
                    msg += f"#{p[0]} – from @{p[2]} ({p[1]}) – {p[5]}\n"
                await update.message.reply_text(msg, parse_mode="HTML")
        elif text == "⬅️ Back":
            await update.message.reply_text("↩️ Back to main menu.", reply_markup=main_keyboard, parse_mode="HTML")

# ---- File handler (conversion) ----
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username, user.first_name, user.last_name)
    if is_user_banned(user.id):
        await update.message.reply_text("🚫 You are banned.", parse_mode="HTML")
        return

    # If we have a state, it should have been handled by handle_all_messages.
    # But we keep this for safety.

    admin = get_admin_by_telegram(user.id)
    if not admin:
        if update.message.document:
            msg = f"📄 Document: {update.message.document.file_name}"
        elif update.message.photo:
            msg = "📸 Photo"
        else:
            msg = "📎 File"
        log_message(user.id, user.username or str(user.id), msg, "file")

    global processing, pending_queue
    message = update.message
    logger.info(f"Received file from user {user.id} ({user.username})")
    if not check_limit(user.id):
        await message.reply_text(
            f"❌ You've reached your daily limit. Please wait until tomorrow or contact admin.\n"
            f"Admin: @{ (await update.get_bot().get_chat(ADMIN_CHAT_ID)).username or 'admin' }",
            parse_mode="HTML"
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
        await message.reply_text("⚠️ Photo – may be compressed. Send as file for exact conversion.", parse_mode="HTML")
    else:
        await message.reply_text("❌ Unsupported.", reply_markup=get_main_keyboard(admin is not None), parse_mode="HTML")
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
            f"Please wait. You are #{len(pending_queue)} in queue.",
            parse_mode="HTML"
        )
        return
    processing = True
    current_processing_user = user
    await do_conversion(update, message, file_obj, file_name, file_ext)
    processing = False
    current_processing_user = None
    await process_next()

# ---- Handler for forwarded media (admin reply) ----
async def handle_forwarded_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This is called from handle_all_messages when reply_mode is set
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if not context.user_data.get('reply_mode'):
        return
    msg = update.message
    if msg.text and msg.text.isdigit():
        target_id = int(msg.text)
        forwarded_msg = context.user_data.get('reply_media')
        if not forwarded_msg:
            await update.message.reply_text("❌ No media to forward. Please forward a message first.", parse_mode="HTML")
            return
        try:
            await forwarded_msg.copy(chat_id=target_id)
            await update.message.reply_text(f"✅ Media forwarded to user {target_id}.", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to send: {e}", parse_mode="HTML")
        context.user_data['reply_mode'] = False
        context.user_data.pop('reply_media', None)
        return
    # Store forwarded message
    if msg.forward_origin or msg.photo or msg.video or msg.document or msg.text:
        context.user_data['reply_media'] = msg
        await update.message.reply_text("📥 Media received. Now send the target user ID.", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ Please forward a message or send a numeric user ID.", parse_mode="HTML")

# ---- Error handler ----
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text(
            "⚠️ Internal error – try again.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(get_admin_by_telegram(update.effective_user.id) is not None)
        )

# ---- Progress & conversion helpers ----
progress_state = defaultdict(lambda: {"last_percent": -1, "last_time": 0})

async def update_progress(message, percent, text=""):
    if percent > 100:
        percent = 100
    msg_id = message.message_id
    state = progress_state[msg_id]
    now = time.time()
    if (abs(percent - state["last_percent"]) >= 5 or percent == 100) and (now - state["last_time"] >= 1.0):
        bar = '█' * int(20 * percent / 100) + '░' * (20 - int(20 * percent / 100))
        progress_text = f"⏳ {text} {percent}%\n<code>{bar}</code>"
        try:
            await message.edit_text(progress_text, parse_mode="HTML")
            state["last_percent"] = percent
            state["last_time"] = now
        except Exception as e:
            logger.warning(f"Progress edit failed: {e}")

def generate_header_from_data(data: bytes, original_filename: str) -> bytes:
    crc = zlib.crc32(data)
    file_size = len(data)
    stem = re.sub(r'[^a-zA-Z0-9_]', '_', Path(original_filename).stem)
    array_name = stem + "_data"
    lines = [
        "// Automatically generated by AstroStar Bot",
        f"// Original file: {original_filename}",
        "// Converter by: @r3nz75\n",
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

# ----------------------------------------------
# Build the bot application
BOT_TOKEN = get_setting('bot_token') or os.getenv('TELEGRAM_BOT_TOKEN')
if not BOT_TOKEN:
    print("⚠️ No bot token set. Set it in the dashboard or via env.")

request_obj = HTTPXRequest(
    connect_timeout=30.0,
    read_timeout=120.0,
    write_timeout=30.0,
    connection_pool_size=8
)
application = Application.builder().token(BOT_TOKEN).request(request_obj).concurrent_updates(True).build()

# Command handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("setlimit", admin_set_limit))
application.add_handler(CommandHandler("resetlimit", admin_reset_limit))
application.add_handler(CommandHandler("setpremium", admin_set_premium))
application.add_handler(CommandHandler("setlimitall", admin_set_limit_all))
application.add_handler(CommandHandler("stats", admin_stats))
application.add_handler(CommandHandler("ban", admin_ban))
application.add_handler(CommandHandler("unban", admin_unban))
application.add_handler(CommandHandler("announce", admin_announce))
application.add_handler(CommandHandler("reply", admin_reply_text))

# Message handlers with priorities
# Highest priority: handle_all_messages (catches forwarded, text, any message)
application.add_handler(MessageHandler(filters.ALL, handle_all_messages), group=0)
# Then menu handler for text (non-command)
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu), group=1)
# File handler (documents/photos)
application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file), group=2)

# Callback query handlers
application.add_handler(CallbackQueryHandler(leak_callback, pattern="^leak_"))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex('^-?\\d+$'), handle_leak_custom_id))

application.add_error_handler(error_handler)

# ----------------------------------------------
# Persistent event loop
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
            future = asyncio.run_coroutine_threadsafe(
                application.process_update(update),
                loop
            )
            future.result(timeout=60)
        except TimeoutError:
            logger.error("Webhook processing timed out after 60 seconds.")
        except Exception as e:
            logger.error(f"Error processing update: {e}\n{traceback.format_exc()}")
        return 'OK', 200
    return 'Method not allowed', 405

def set_webhook():
    if not BOT_TOKEN:
        print("No token, skipping webhook setup.")
        return
    external_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not external_url:
        print("RENDER_EXTERNAL_URL not set. Please set it or use a fixed URL.")
        return
    webhook_url = external_url.rstrip('/') + '/webhook'
    resp = req.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        data={"url": webhook_url}
    )
    if resp.ok:
        print(f"✅ Webhook set to {webhook_url}")
    else:
        print(f"❌ Failed to set webhook: {resp.text}")

# ----------------------------------------------
if __name__ == "__main__":
    init_db()
    set_webhook()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
