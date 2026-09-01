#!/usr/bin/env python3
"""
Telegram Bot + Dashboard
Full button-driven UI, admin management, ban system, announcements, and replies.
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

from flask import Flask, request, render_template_string, redirect, url_for, session, flash, abort
from telegram import Update, ReplyKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

# ----------------------------------------------
# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------------------------
# Database
DB_PATH = "bot_settings.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Settings
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    # Admins
    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        telegram_id TEXT,
        is_super BOOLEAN DEFAULT 0
    )''')
    # Users (for bans and announcements)
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        banned BOOLEAN DEFAULT 0,
        last_used TIMESTAMP
    )''')
    # Insert default super admin
    c.execute("INSERT OR IGNORE INTO admins (username, password, telegram_id, is_super) VALUES (?, ?, ?, ?)",
              ("r3nz75", "r3nz75converter2027", str(5682792112), 1))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("bot_token", ""))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("bot_name", "Image↔C Header Converter"))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("logo_url", "https://cdn-icons-png.flaticon.com/512/60/60580.png"))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("admin_chat_id", str(5682792112)))
    conn.commit()
    conn.close()

init_db()

# DB helper functions for users
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

def get_all_banned():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE banned=1")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

# Existing DB functions (unchanged)
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
    return int(row[0]) if row else 5682792112

ADMIN_CHAT_ID = get_primary_admin_chat_id()

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

# ---- Login page (unchanged, omitted for brevity but included in final code) ----
# ... (same as before, we'll include full code at the end)

# ---- Routes (unchanged, we'll include full) ----

# ----------------------------------------------
# Telegram Bot Handlers
# ----------------------------------------------

# Custom keyboards
def get_main_keyboard(has_admin=False):
    if has_admin:
        return ReplyKeyboardMarkup([
            ["Home", "Convert", "Commands"],
            ["Usage", "Profile", "Admin"]
        ], resize_keyboard=True, is_persistent=True)
    else:
        return ReplyKeyboardMarkup([
            ["Home", "Convert", "Commands"],
            ["Usage", "Profile"]
        ], resize_keyboard=True, is_persistent=True)

ADMIN_KEYBOARD = ReplyKeyboardMarkup([
    ["Set Limit", "Reset Limit"],
    ["Set Premium", "Set Limit All"],
    ["Stats", "Ban User"],
    ["Unban User", "Announcement"],
    ["Reply to User", "⬅ Back"]
], resize_keyboard=True, is_persistent=True)

# --- Helper functions ---
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

# --- User stats and limits (in-memory) ---
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

# --- Banned check for all incoming messages ---
async def check_banned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user and is_user_banned(user.id):
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return True
    return False

# --- Admin notification (unchanged) ---
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
        logger.error(f"Failed to notify admin with file: {e}")

# --- Queue system (unchanged) ---
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
            logger.error(f"Recovery error for user {user.id}: {e}")
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
            logger.error(f"Conversion error for user {user.id}: {e}")
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

# ---------- Admin commands (new & existing) ----------
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

async def admin_set_limit_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /setlimitall <limit> (use -1 for unlimited)")
        return
    try:
        limit = int(args[0])
        if not user_stats:
            await update.message.reply_text("No users have used the bot yet.")
            return
        count = 0
        for uid in list(user_stats.keys()):
            set_limit(uid, limit)
            count += 1
        await update.message.reply_text(f"✅ Daily limit set to {limit} for {count} users.")
    except ValueError:
        await update.message.reply_text("❌ Invalid limit. Must be an integer.")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    if not user_stats:
        await update.message.reply_text("No user stats yet.")
        return
    lines = ["*📊 User Stats*"]
    for uid, stats in user_stats.items():
        # Fetch user info from DB for username
        user_info = get_user(uid)
        username = user_info[1] if user_info else None
        display = f"@{username}" if username else str(uid)
        limit = "∞" if stats["limit"] == -1 else str(stats["limit"])
        lines.append(f"{display} (ID: `{uid}`): {stats['count']}/{limit} used today")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /ban <user_id>")
        return
    try:
        user_id = int(args[0])
        ban_user(user_id)
        await update.message.reply_text(f"✅ User {user_id} has been banned.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id.")

async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /unban <user_id>")
        return
    try:
        user_id = int(args[0])
        unban_user(user_id)
        await update.message.reply_text(f"✅ User {user_id} has been unbanned.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id.")

async def admin_announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /announce <message>")
        return
    message_text = " ".join(args)
    all_users = get_all_users()
    if not all_users:
        await update.message.reply_text("No users to announce to.")
        return
    sent = 0
    for uid, _, _, _ in all_users:
        try:
            await update.get_bot().send_message(chat_id=uid, text=f"📢 *Announcement*\n\n{message_text}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)  # avoid hitting rate limits
        except Exception as e:
            logger.error(f"Failed to send announcement to {uid}: {e}")
    await update.message.reply_text(f"✅ Announcement sent to {sent} users.")

async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /reply <user_id> <message>")
        return
    try:
        user_id = int(args[0])
        message_text = " ".join(args[1:])
        await update.get_bot().send_message(chat_id=user_id, text=f"📨 *Admin reply*\n\n{message_text}", parse_mode="Markdown")
        await update.message.reply_text(f"✅ Reply sent to user {user_id}.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send: {e}")

# ---------- Handler for forwarded media (admin replies with media) ----------
async def handle_admin_reply_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This handler only triggers when admin forwards a media to the bot while in "reply mode"
    # We'll store the forwarded message in context.user_data and prompt for user_id
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    # Check if we are in reply mode
    if context.user_data.get('reply_mode'):
        # Store the forwarded message
        context.user_data['reply_media'] = update.message
        await update.message.reply_text("📝 Please send the target user ID now.")
    else:
        # Not in reply mode, ignore
        pass

# ---------- Main menu handler (updated with new buttons) ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username, user.first_name, user.last_name)
    if is_user_banned(user.id):
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return
    admin = get_admin_by_telegram(user.id)
    has_admin = admin is not None
    keyboard = get_main_keyboard(has_admin)
    await update.message.reply_text(fmt_home(), reply_markup=keyboard, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_user_banned(user.id):
        await update.message.reply_text("🚫 You are banned.")
        return
    admin = get_admin_by_telegram(user.id)
    has_admin = admin is not None
    keyboard = get_main_keyboard(has_admin)
    await update.message.reply_text(fmt_commands(), reply_markup=keyboard, parse_mode="Markdown")

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_user_banned(user.id):
        await update.message.reply_text("🚫 You are banned.")
        return
    text = update.message.text
    admin = get_admin_by_telegram(user.id)
    has_admin = admin is not None
    main_keyboard = get_main_keyboard(has_admin)

    if text == "Home":
        await update.message.reply_text(fmt_home(), reply_markup=main_keyboard, parse_mode="Markdown")
    elif text == "Convert":
        await update.message.reply_text(fmt_convert(), reply_markup=main_keyboard, parse_mode="Markdown")
    elif text == "Commands":
        await update.message.reply_text(fmt_commands(), reply_markup=main_keyboard, parse_mode="Markdown")
    elif text == "Usage":
        await update.message.reply_text(fmt_usage(), reply_markup=main_keyboard, parse_mode="Markdown")
    elif text == "Profile":
        await update.message.reply_text(fmt_profile(update), reply_markup=main_keyboard, parse_mode="Markdown")
    elif text == "Admin":
        if admin:
            await update.message.reply_text(
                "*🛠 Admin Panel*\nChoose an action:",
                reply_markup=ADMIN_KEYBOARD,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ You are not an admin.", reply_markup=main_keyboard)
    elif text == "Set Limit":
        await update.message.reply_text(
            "📝 Please send the command in format:\n`/setlimit <user_id> <limit>`\nExample: `/setlimit 123456789 10`\n(use -1 for unlimited)",
            parse_mode="Markdown"
        )
    elif text == "Reset Limit":
        await update.message.reply_text(
            "📝 Please send the command:\n`/resetlimit <user_id>`",
            parse_mode="Markdown"
        )
    elif text == "Set Premium":
        await update.message.reply_text(
            "📝 Please send the command:\n`/setpremium <user_id>`",
            parse_mode="Markdown"
        )
    elif text == "Set Limit All":
        await update.message.reply_text(
            "📝 Please send the command:\n`/setlimitall <limit>`\nExample: `/setlimitall 10`\n(use -1 for unlimited)",
            parse_mode="Markdown"
        )
    elif text == "Stats":
        await admin_stats(update, context)
    elif text == "Ban User":
        await update.message.reply_text(
            "📝 Please send the command:\n`/ban <user_id>`",
            parse_mode="Markdown"
        )
    elif text == "Unban User":
        await update.message.reply_text(
            "📝 Please send the command:\n`/unban <user_id>`",
            parse_mode="Markdown"
        )
    elif text == "Announcement":
        await update.message.reply_text(
            "📝 Please send the command:\n`/announce <your message>`",
            parse_mode="Markdown"
        )
    elif text == "Reply to User":
        # Set reply mode
        context.user_data['reply_mode'] = True
        await update.message.reply_text(
            "📤 Forward any message (text, photo, video, document) to me, then send the target user ID.\n"
            "Or use `/reply <user_id> <message>` for text only.",
            parse_mode="Markdown"
        )
    elif text == "⬅ Back":
        await update.message.reply_text("↩️ Back to main menu.", reply_markup=main_keyboard)

# ---------- File handler (with ban check and user upsert) ----------
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username, user.first_name, user.last_name)
    if is_user_banned(user.id):
        await update.message.reply_text("🚫 You are banned.")
        return

    global processing, pending_queue
    message = update.message
    logger.info(f"Received file from user {user.id} ({user.username})")

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
        await message.reply_text("❌ Unsupported.", parse_mode="Markdown", reply_markup=get_main_keyboard(get_admin_by_telegram(user.id) is not None))
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

# ---------- Handler for forwarded media (admin reply) ----------
async def handle_forwarded_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only admins can use this
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    # If not in reply mode, ignore
    if not context.user_data.get('reply_mode'):
        return

    # If the user sends a user ID (text), we send the stored media
    if update.message.text and update.message.text.isdigit():
        target_id = int(update.message.text)
        # Get the stored forwarded message
        forwarded_msg = context.user_data.get('reply_media')
        if not forwarded_msg:
            await update.message.reply_text("❌ No media to forward. Please forward a message first.")
            return
        try:
            # Copy the message to the target user
            await forwarded_msg.copy(chat_id=target_id)
            await update.message.reply_text(f"✅ Media forwarded to user {target_id}.")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to send: {e}")
        # Clear reply mode
        context.user_data['reply_mode'] = False
        context.user_data.pop('reply_media', None)
    else:
        # Store the forwarded message
        context.user_data['reply_media'] = update.message
        await update.message.reply_text("📥 Media received. Now send the target user ID.")

# ---------- Error handler ----------
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text(
            "⚠️ Internal error – try again.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(get_admin_by_telegram(update.effective_user.id) is not None)
        )

# ---------- Progress & conversion helpers (unchanged) ----------
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
    read_timeout=30.0,
    write_timeout=30.0,
    connection_pool_size=8
)
application = Application.builder().token(BOT_TOKEN).request(request_obj).concurrent_updates(True).build()

# Handlers
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
application.add_handler(CommandHandler("reply", admin_reply))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
application.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded_reply))
application.add_error_handler(error_handler)

# ----------------------------------------------
# Persistent event loop in background thread
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
            future.result(timeout=30)
        except TimeoutError:
            logger.error("Webhook processing timed out after 30 seconds.")
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
