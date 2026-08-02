import logging
import sqlite3
import random
import string
import asyncio
import os
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.constants import ParseMode

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("8913447240:AAFcmpRjKZWhCjKfNVzD4dE9p1jWVDiowgI")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required!")

ADMIN_IDS = []
admin_ids_str = os.environ.get("ADMIN_IDS", "8896981303")
if admin_ids_str:
    ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]

GET_REDEEM_CODE = 1

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DATABASE ====================
class Database:
    def __init__(self, db_path="redeem_bot.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.init_tables()
        logger.info("Database initialized")

    def init_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                join_date TIMESTAMP,
                referral_code TEXT UNIQUE,
                referred_by INTEGER,
                credits INTEGER DEFAULT 0,
                referrals_count INTEGER DEFAULT 0,
                total_withdrawals INTEGER DEFAULT 0
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER UNIQUE,
                referral_date TIMESTAMP,
                status TEXT DEFAULT 'pending'
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                request_date TIMESTAMP,
                status TEXT DEFAULT 'pending',
                redeem_code TEXT
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS redeem_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                amount INTEGER,
                created_by INTEGER,
                created_date TIMESTAMP,
                used BOOLEAN DEFAULT 0,
                used_by INTEGER,
                expiry_date TIMESTAMP
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS private_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                user_id INTEGER,
                sent_date TIMESTAMP,
                viewed BOOLEAN DEFAULT 0
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS credit_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                reason TEXT,
                timestamp TIMESTAMP
            )
        ''')

        self.conn.commit()

    def add_user(self, user_id: int, username: str, first_name: str, last_name: str = ""):
        try:
            referral_code = self.generate_referral_code()
            self.cursor.execute('''
                INSERT OR IGNORE INTO users 
                (user_id, username, first_name, last_name, join_date, referral_code)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name, datetime.now(), referral_code))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding user: {e}")
            return False

    def generate_referral_code(self):
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            self.cursor.execute("SELECT user_id FROM users WHERE referral_code = ?", (code,))
            if not self.cursor.fetchone():
                return code

    def get_user(self, user_id: int):
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return self.cursor.fetchone()

    def update_user_credits(self, user_id: int, amount: int):
        self.cursor.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()
        return True

    def set_user_credits(self, user_id: int, amount: int):
        self.cursor.execute("UPDATE users SET credits = ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()
        return True

    def add_referral(self, referrer_id: int, referred_id: int):
        try:
            self.cursor.execute("SELECT id FROM referrals WHERE referred_id = ?", (referred_id,))
            if self.cursor.fetchone():
                return False

            self.cursor.execute('''
                INSERT INTO referrals (referrer_id, referred_id, referral_date, status)
                VALUES (?, ?, ?, 'completed')
            ''', (referrer_id, referred_id, datetime.now()))
            
            self.cursor.execute("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?", (referrer_id,))
            
            self.cursor.execute("SELECT referrals_count, credits FROM users WHERE user_id = ?", (referrer_id,))
            result = self.cursor.fetchone()
            if result and result[0] >= 30 and result[1] == 0:
                self.cursor.execute("UPDATE users SET credits = 30 WHERE user_id = ?", (referrer_id,))
            
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding referral: {e}")
            return False

    def get_referral_count(self, user_id: int) -> int:
        self.cursor.execute("SELECT referrals_count FROM users WHERE user_id = ?", (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else 0

    def get_referrals_list(self, user_id: int):
        self.cursor.execute('''
            SELECT u.username, u.first_name, r.referral_date 
            FROM referrals r
            JOIN users u ON r.referred_id = u.user_id
            WHERE r.referrer_id = ? AND r.status = 'completed'
            ORDER BY r.referral_date DESC
        ''', (user_id,))
        return self.cursor.fetchall()

    def add_withdrawal(self, user_id: int, amount: int):
        try:
            self.cursor.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
            result = self.cursor.fetchone()
            if not result or result[0] < amount:
                return False
            
            self.cursor.execute('''
                INSERT INTO withdrawals (user_id, amount, request_date, status)
                VALUES (?, ?, ?, 'pending')
            ''', (user_id, amount, datetime.now()))
            
            self.cursor.execute("UPDATE users SET credits = credits - ? WHERE user_id = ?", (amount, user_id))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding withdrawal: {e}")
            return False

    def get_pending_withdrawals(self):
        self.cursor.execute('''
            SELECT w.id, w.user_id, u.username, u.first_name, w.amount, w.request_date
            FROM withdrawals w
            JOIN users u ON w.user_id = u.user_id
            WHERE w.status = 'pending'
            ORDER BY w.request_date ASC
        ''')
        return self.cursor.fetchall()

    def update_withdrawal(self, withdrawal_id: int, status: str, code: str = ""):
        try:
            self.cursor.execute('''
                UPDATE withdrawals 
                SET status = ?, redeem_code = ?
                WHERE id = ?
            ''', (status, code, withdrawal_id))
            
            if status == 'completed' and code:
                self.cursor.execute("SELECT user_id FROM withdrawals WHERE id = ?", (withdrawal_id,))
                result = self.cursor.fetchone()
                if result:
                    user_id = result[0]
                    self.cursor.execute('''
                        INSERT INTO private_codes (code, user_id, sent_date)
                        VALUES (?, ?, ?)
                    ''', (code, user_id, datetime.now()))
                    self.cursor.execute("UPDATE users SET total_withdrawals = total_withdrawals + 1 WHERE user_id = ?", (user_id,))
            
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating withdrawal: {e}")
            return False

    def get_withdrawal_history(self, user_id: int):
        self.cursor.execute('''
            SELECT id, amount, request_date, status, redeem_code
            FROM withdrawals
            WHERE user_id = ?
            ORDER BY request_date DESC
            LIMIT 10
        ''', (user_id,))
        return self.cursor.fetchall()

    def redeem_code_exists(self, code: str):
        self.cursor.execute("SELECT id FROM redeem_codes WHERE code = ? AND used = 0", (code,))
        return self.cursor.fetchone() is not None

    def add_redeem_code(self, code: str, amount: int, admin_id: int):
        try:
            expiry_date = datetime.now() + timedelta(days=30)
            self.cursor.execute('''
                INSERT INTO redeem_codes (code, amount, created_by, created_date, expiry_date)
                VALUES (?, ?, ?, ?, ?)
            ''', (code, amount, admin_id, datetime.now(), expiry_date))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding redeem code: {e}")
            return False

    def get_all_redeem_codes(self):
        self.cursor.execute('''
            SELECT code, amount, created_date, used, used_by, expiry_date
            FROM redeem_codes
            ORDER BY created_date DESC
        ''')
        return self.cursor.fetchall()

    def get_private_codes_for_user(self, user_id: int):
        self.cursor.execute('''
            SELECT code, sent_date, viewed
            FROM private_codes
            WHERE user_id = ?
            ORDER BY sent_date DESC
        ''', (user_id,))
        return self.cursor.fetchall()

    def mark_code_as_viewed(self, code: str, user_id: int):
        self.cursor.execute('''
            UPDATE private_codes 
            SET viewed = 1
            WHERE code = ? AND user_id = ?
        ''', (code, user_id))
        self.conn.commit()

    def get_all_users(self):
        self.cursor.execute("SELECT user_id, username, first_name, credits FROM users ORDER BY join_date DESC")
        return self.cursor.fetchall()

    def get_user_withdrawal(self, withdrawal_id: int):
        self.cursor.execute('''
            SELECT user_id, amount, status, redeem_code
            FROM withdrawals
            WHERE id = ?
        ''', (withdrawal_id,))
        return self.cursor.fetchone()

    def find_user_by_username_or_id(self, search_term: str):
        try:
            user_id = int(search_term)
            self.cursor.execute("SELECT user_id, username, first_name, credits FROM users WHERE user_id = ?", (user_id,))
            return self.cursor.fetchone()
        except ValueError:
            username = search_term.replace('@', '')
            self.cursor.execute("SELECT user_id, username, first_name, credits FROM users WHERE username LIKE ?", (f"%{username}%",))
            return self.cursor.fetchone()

    def close(self):
        self.conn.close()

# ==================== BOT CLASS ====================
class RedeemBot:
    def __init__(self):
        self.db = Database()
        self.application = None

    def setup(self):
        self.application = Application.builder().token(BOT_TOKEN).build()
        
        # Commands
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("profile", self.profile_command))
        self.application.add_handler(CommandHandler("referrals", self.referrals_command))
        self.application.add_handler(CommandHandler("withdraw", self.withdraw_command))
        self.application.add_handler(CommandHandler("history", self.history_command))
        self.application.add_handler(CommandHandler("mycodes", self.mycodes_command))
        
        # Admin commands
        self.application.add_handler(CommandHandler("admin", self.admin_command))
        self.application.add_handler(CommandHandler("pending", self.pending_command))
        self.application.add_handler(CommandHandler("addcode", self.addcode_command))
        self.application.add_handler(CommandHandler("listcodes", self.listcodes_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("broadcast", self.broadcast_command))
        self.application.add_handler(CommandHandler("sendcode", self.sendcode_command))
        self.application.add_handler(CommandHandler("addcredits", self.addcredits_command))
        self.application.add_handler(CommandHandler("deductcredits", self.deductcredits_command))
        self.application.add_handler(CommandHandler("setcredits", self.setcredits_command))
        self.application.add_handler(CommandHandler("finduser", self.finduser_command))

        # Callbacks
        self.application.add_handler(CallbackQueryHandler(self.callback_handler))

        # Conversation
        conv = ConversationHandler(
            entry_points=[CommandHandler("addcode", self.addcode_command)],
            states={
                GET_REDEEM_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_code_details)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_command)],
        )
        self.application.add_handler(conv)

        self.application.add_error_handler(self.error_handler)
        logger.info("Bot handlers set up")

    # ==================== CALLBACKS ====================
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        data = query.data
        
        try:
            if data == "profile":
                await self._show_profile(query, user_id)
            elif data == "referrals":
                await self._show_referrals(query, context, user_id)
            elif data == "withdraw":
                await self._show_withdraw(query, user_id)
            elif data == "history":
                await self._show_history(query, user_id)
            elif data == "mycodes":
                await self._show_mycodes(query, user_id)
            elif data == "admin_panel":
                await self._show_admin(query, user_id)
            elif data == "back":
                await self._show_start(query, context, user_id)
            elif data.startswith("admin_"):
                await self._handle_admin_action(query, context, user_id, data)
            elif data.startswith("withdraw_"):
                await self._handle_withdraw_action(query, context, user_id, data)
            else:
                await query.edit_message_text("❌ Unknown action.")
        except Exception as e:
            logger.error(f"Callback error: {e}")
            await query.edit_message_text("⚠️ Error occurred.")

    # ==================== SHOW METHODS ====================
    async def _show_start(self, query, context, user_id):
        user_data = self.db.get_user(user_id)
        credits = user_data[6] if user_data else 0
        referrals = self.db.get_referral_count(user_id)
        
        keyboard = [
            [InlineKeyboardButton("👤 Profile", callback_data="profile")],
            [InlineKeyboardButton("👥 Referrals", callback_data="referrals")],
            [InlineKeyboardButton("💰 Withdraw", callback_data="withdraw")],
            [InlineKeyboardButton("📊 History", callback_data="history")],
            [InlineKeyboardButton("🎟️ My Codes", callback_data="mycodes")],
        ]
        
        if user_id in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        referral_code = user_data[5] if user_data else ""
        referral_link = f"https://t.me/{context.bot.username}?start={referral_code}" if referral_code else ""
        
        message = (
            f"🎯 **Welcome!**\n\n"
            f"👤 Name: {query.from_user.first_name}\n"
            f"🔑 Code: `{referral_code}`\n"
            f"🔗 Link: {referral_link}\n\n"
            f"⭐ Credits: `{credits}`\n"
            f"👥 Referrals: `{referrals}/30`\n\n"
            f"📌 Get 30 referrals to earn 30 credits!"
        )
        
        await query.edit_message_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

    async def _show_profile(self, query, user_id):
        user_data = self.db.get_user(user_id)
        if not user_data:
            await query.edit_message_text("⚠️ Please use /start.")
            return
        
        referrals = self.db.get_referral_count(user_id)
        codes = self.db.get_private_codes_for_user(user_id)
        
        text = (
            f"👤 **Profile**\n\n"
            f"🆔 ID: `{user_id}`\n"
            f"👤 Name: {user_data[2]} {user_data[3] or ''}\n"
            f"🔗 Code: `{user_data[5]}`\n"
            f"📅 Joined: {user_data[4][:10]}\n\n"
            f"⭐ Credits: `{user_data[6]}`\n"
            f"👥 Referrals: `{referrals}/30`\n"
            f"🎁 Withdrawals: `{user_data[9]}`\n"
            f"🎟️ Codes: `{len(codes)}`"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

    async def _show_referrals(self, query, context, user_id):
        referrals = self.db.get_referrals_list(user_id)
        count = len(referrals)
        
        if count == 0:
            user_data = self.db.get_user(user_id)
            code = user_data[5] if user_data else ""
            text = f"📭 **No referrals yet!**\n\nShare: https://t.me/{context.bot.username}?start={code}"
        else:
            text = f"👥 **Referrals ({count}/30)**\n\n"
            for i, (username, first_name, date) in enumerate(referrals[:10], 1):
                name = f"@{username}" if username else first_name
                text += f"{i}. {name} - {date[:10]}\n"
            if count > 10:
                text += f"\n... and {count - 10} more"
            if count >= 30:
                text += "\n\n🎉 **30 referrals reached!**"
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

    async def _show_withdraw(self, query, user_id):
        user_data = self.db.get_user(user_id)
        if not user_data:
            await query.edit_message_text("⚠️ Please use /start.")
            return
        
        credits = user_data[6]
        referrals = self.db.get_referral_count(user_id)
        
        if referrals < 30:
            await query.edit_message_text(f"❌ Need 30 referrals. Current: `{referrals}/30`")
            return
        
        if credits < 30:
            await query.edit_message_text(f"❌ Need 30 credits. Current: `{credits}`")
            return
        
        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data=f"withdraw_confirm_{credits}"),
             InlineKeyboardButton("❌ Cancel", callback_data="withdraw_cancel")],
            [InlineKeyboardButton("🔙 Back", callback_data="back")]
        ]
        
        await query.edit_message_text(
            f"💳 **Withdraw**\n\nAmount: `{credits}` credits\n\n🔒 Code will be private.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def _show_history(self, query, user_id):
        history = self.db.get_withdrawal_history(user_id)
        
        if not history:
            text = "📭 No withdrawal history."
        else:
            text = "📊 **Withdrawal History**\n\n"
            for h in history:
                status = "✅" if h[3] == "completed" else "⏳" if h[3] == "pending" else "❌"
                text += f"{status} {h[1]} credits - {h[2][:10]}\n"
                if h[4]:
                    text += f"   Code: `{h[4]}`\n"
                text += f"   Status: {h[3].upper()}\n\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

    async def _show_mycodes(self, query, user_id):
        codes = self.db.get_private_codes_for_user(user_id)
        
        if not codes:
            text = "🎟️ **No codes found.** Complete 30 referrals to get codes."
        else:
            text = "🎟️ **Your Codes**\n\n🔒 Only visible to you!\n\n"
            for code, sent_date, viewed in codes:
                status = "👁️ Viewed" if viewed else "🆕 New"
                text += f"📌 `{code}` - {status}\n   Received: {sent_date[:10]}\n\n"
                if not viewed:
                    self.db.mark_code_as_viewed(code, user_id)
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

    async def _show_admin(self, query, user_id):
        if user_id not in ADMIN_IDS:
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        keyboard = [
            [InlineKeyboardButton("📋 Pending", callback_data="admin_pending")],
            [InlineKeyboardButton("➕ Add Code", callback_data="admin_addcode")],
            [InlineKeyboardButton("📤 Send Code", callback_data="admin_sendcode")],
            [InlineKeyboardButton("📚 List Codes", callback_data="admin_listcodes")],
            [InlineKeyboardButton("💳 Manage Credits", callback_data="admin_credits")],
            [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
            [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
            [InlineKeyboardButton("🔙 Back", callback_data="back")],
        ]
        
        await query.edit_message_text("⚙️ **Admin Panel**", reply_markup=InlineKeyboardMarkup(keyboard))

    # ==================== ADMIN ACTIONS ====================
    async def _handle_admin_action(self, query, context, user_id, data):
        if user_id not in ADMIN_IDS:
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        parts = data.split("_")
        action = parts[1] if len(parts) > 1 else ""
        
        if action == "pending":
            pending = self.db.get_pending_withdrawals()
            if not pending:
                await query.edit_message_text("✅ No pending withdrawals.")
                return
            for w in pending:
                w_id, u_id, username, first_name, amount, date = w
                name = f"@{username}" if username else first_name
                keyboard = [
                    [InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{w_id}"),
                     InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{w_id}")]
                ]
                await query.message.reply_text(
                    f"🆔 **#{w_id}**\n👤 {name} (ID: `{u_id}`)\n💰 `{amount}` credits\n📅 {date}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            await query.edit_message_text("📋 Showing all pending above.")
        elif action == "addcode":
            await query.edit_message_text("Use /addcode command.")
        elif action == "sendcode":
            await query.edit_message_text("Use /sendcode <user_id> <code>")
        elif action == "listcodes":
            codes = self.db.get_all_redeem_codes()
            if not codes:
                await query.edit_message_text("📭 No codes.")
                return
            text = "🎟️ **Codes**\n\n"
            for code, amount, created, used, used_by, expiry in codes[:20]:
                status = "✅ Used" if used else "🟢 Available"
                text += f"`{code}` - {amount} credits - {status}\n"
                if used:
                    text += f"Used by: {used_by}\n"
                text += f"Expires: {expiry[:10]}\n\n"
            if len(codes) > 20:
                text += f"\n... and {len(codes) - 20} more"
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        elif action == "credits":
            await query.edit_message_text(
                "💳 **Manage Credits**\n\n"
                "/addcredits <user_id> <amount>\n"
                "/deductcredits <user_id> <amount>\n"
                "/setcredits <user_id> <amount>\n"
                "/finduser <user_id>"
            )
        elif action == "stats":
            self.db.cursor.execute("SELECT COUNT(*) FROM users")
            total_users = self.db.cursor.fetchone()[0]
            self.db.cursor.execute("SELECT SUM(credits) FROM users")
            total_credits = self.db.cursor.fetchone()[0] or 0
            self.db.cursor.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")
            pending = self.db.cursor.fetchone()[0]
            self.db.cursor.execute("SELECT COUNT(*) FROM redeem_codes WHERE used=0")
            available = self.db.cursor.fetchone()[0]
            text = (
                f"📊 **Stats**\n\n"
                f"👥 Users: `{total_users}`\n"
                f"⭐ Credits: `{total_credits}`\n"
                f"⏳ Pending: `{pending}`\n"
                f"🎟️ Available: `{available}`"
            )
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        elif action == "broadcast":
            await query.edit_message_text("Use /broadcast <message>")
        elif action == "users":
            users = self.db.get_all_users()
            text = "👥 **Users**\n\n"
            for i, (uid, username, first_name, credits) in enumerate(users[:20], 1):
                name = f"@{username}" if username else first_name
                text += f"{i}. {name} - `{uid}` - ⭐{credits}\n"
            if len(users) > 20:
                text += f"\n... and {len(users) - 20} more"
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        elif action == "approve" or action == "reject":
            if len(parts) < 3:
                await query.edit_message_text("❌ Invalid.")
                return
            withdrawal_id = int(parts[2])
            status = "completed" if action == "approve" else "rejected"
            withdrawal = self.db.get_user_withdrawal(withdrawal_id)
            if not withdrawal:
                await query.edit_message_text("❌ Not found.")
                return
            if status == "completed" and not withdrawal[3]:
                await query.edit_message_text(f"❌ No code. Use /sendcode {withdrawal[0]} <code>")
                return
            if self.db.update_withdrawal(withdrawal_id, status, withdrawal[3] if status == "completed" else ""):
                user_id_to_notify = withdrawal[0]
                if status == "completed":
                    try:
                        await context.bot.send_message(
                            chat_id=user_id_to_notify,
                            text=f"✅ **Approved!**\n\n🎟️ Code: `{withdrawal[3]}`"
                        )
                    except:
                        pass
                else:
                    self.db.update_user_credits(user_id_to_notify, withdrawal[1])
                    try:
                        await context.bot.send_message(
                            chat_id=user_id_to_notify,
                            text=f"❌ **Rejected.**\n\n`{withdrawal[1]}` credits refunded."
                        )
                    except:
                        pass
                await query.edit_message_text(f"✅ Withdrawal #{withdrawal_id} {status}!")
            else:
                await query.edit_message_text("❌ Failed.")

    async def _handle_withdraw_action(self, query, context, user_id, data):
        parts = data.split("_")
        action = parts[1] if len(parts) > 1 else ""
        
        if action == "confirm":
            if len(parts) < 3:
                await query.edit_message_text("❌ Invalid.")
                return
            amount = int(parts[2])
            if self.db.add_withdrawal(user_id, amount):
                for admin_id in ADMIN_IDS:
                    try:
                        await context.bot.send_message(
                            chat_id=admin_id,
                            text=f"🆕 **New Withdrawal**\nUser: `{user_id}`\nAmount: `{amount}` credits"
                        )
                    except:
                        pass
                keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
                await query.edit_message_text(
                    f"✅ **Submitted!**\n\nAmount: `{amount}` credits\n\n⏳ Waiting for admin.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await query.edit_message_text("❌ Failed. Not enough credits.")
        elif action == "cancel":
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
            await query.edit_message_text("✅ Cancelled.", reply_markup=InlineKeyboardMarkup(keyboard))

    # ==================== COMMAND HANDLERS ====================
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = user.id
        
        self.db.add_user(user_id, user.username or "", user.first_name or "", user.last_name or "")
        
        if context.args and len(context.args) > 0:
            referral_code = context.args[0]
            self.db.cursor.execute("SELECT user_id FROM users WHERE referral_code = ?", (referral_code,))
            referrer = self.db.cursor.fetchone()
            if referrer and referrer[0] != user_id:
                if self.db.add_referral(referrer[0], user_id):
                    await update.message.reply_text("✅ You've been referred successfully!")
        
        user_data = self.db.get_user(user_id)
        credits = user_data[6] if user_data else 0
        referrals = self.db.get_referral_count(user_id)
        
        keyboard = [
            [InlineKeyboardButton("👤 Profile", callback_data="profile")],
            [InlineKeyboardButton("👥 Referrals", callback_data="referrals")],
            [InlineKeyboardButton("💰 Withdraw", callback_data="withdraw")],
            [InlineKeyboardButton("📊 History", callback_data="history")],
            [InlineKeyboardButton("🎟️ My Codes", callback_data="mycodes")],
        ]
        
        if user_id in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        referral_code = user_data[5] if user_data else ""
        referral_link = f"https://t.me/{context.bot.username}?start={referral_code}" if referral_code else ""
        
        message = (
            f"🎯 **Welcome!**\n\n"
            f"👤 Name: {user.first_name}\n"
            f"🔑 Code: `{referral_code}`\n"
            f"🔗 Link: {referral_link}\n\n"
            f"⭐ Credits: `{credits}`\n"
            f"👥 Referrals: `{referrals}/30`\n\n"
            f"📌 Get 30 referrals to earn 30 credits!"
        )
        
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = "📚 **Help**\n\n/start - Start bot\n/profile - View profile\n/referrals - View referrals\n/withdraw - Withdraw credits\n/history - View history\n/mycodes - View codes\n/help - This message\n\n"
        if user_id in ADMIN_IDS:
            text += "**Admin:**\n/admin - Panel\n/pending - Pending withdrawals\n/addcode - Add code\n/sendcode - Send code\n/listcodes - List codes\n/stats - Statistics\n/broadcast - Broadcast\n/addcredits - Add credits\n/deductcredits - Deduct credits\n/setcredits - Set credits\n/finduser - Find user"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def profile_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)
        if not user_data:
            await update.message.reply_text("⚠️ Use /start to register.")
            return
        referrals = self.db.get_referral_count(user_id)
        codes = self.db.get_private_codes_for_user(user_id)
        text = (
            f"👤 **Profile**\n\n"
            f"🆔 ID: `{user_id}`\n"
            f"👤 Name: {user_data[2]} {user_data[3] or ''}\n"
            f"🔗 Code: `{user_data[5]}`\n"
            f"📅 Joined: {user_data[4][:10]}\n\n"
            f"⭐ Credits: `{user_data[6]}`\n"
            f"👥 Referrals: `{referrals}/30`\n"
            f"🎁 Withdrawals: `{user_data[9]}`\n"
            f"🎟️ Codes: `{len(codes)}`"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def referrals_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        referrals = self.db.get_referrals_list(user_id)
        count = len(referrals)
        if count == 0:
            user_data = self.db.get_user(user_id)
            code = user_data[5] if user_data else ""
            await update.message.reply_text(f"📭 **No referrals**\n\nShare: https://t.me/{context.bot.username}?start={code}")
            return
        text = f"👥 **Referrals ({count}/30)**\n\n"
        for i, (username, first_name, date) in enumerate(referrals[:10], 1):
            name = f"@{username}" if username else first_name
            text += f"{i}. {name} - {date[:10]}\n"
        if count > 10:
            text += f"\n... and {count - 10} more"
        if count >= 30:
            text += "\n\n🎉 **30 referrals reached!**"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def withdraw_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)
        if not user_data:
            await update.message.reply_text("⚠️ Use /start.")
            return
        credits = user_data[6]
        referrals = self.db.get_referral_count(user_id)
        if referrals < 30:
            await update.message.reply_text(f"❌ Need 30 referrals. Current: `{referrals}/30`")
            return
        if credits < 30:
            await update.message.reply_text(f"❌ Need 30 credits. Current: `{credits}`")
            return
        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data=f"withdraw_confirm_{credits}"),
             InlineKeyboardButton("❌ Cancel", callback_data="withdraw_cancel")]
        ]
        await update.message.reply_text(
            f"💳 **Withdraw**\n\nAmount: `{credits}` credits\n\n🔒 Code will be private.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        history = self.db.get_withdrawal_history(user_id)
        if not history:
            await update.message.reply_text("📭 No history.")
            return
        text = "📊 **History**\n\n"
        for h in history:
            status = "✅" if h[3] == "completed" else "⏳" if h[3] == "pending" else "❌"
            text += f"{status} {h[1]} credits - {h[2][:10]}\n"
            if h[4]:
                text += f"   Code: `{h[4]}`\n"
            text += f"   Status: {h[3].upper()}\n\n"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def mycodes_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        codes = self.db.get_private_codes_for_user(user_id)
        if not codes:
            await update.message.reply_text("🎟️ **No codes.** Get 30 referrals to earn codes.")
            return
        text = "🎟️ **Your Codes**\n\n🔒 Private\n\n"
        for code, sent_date, viewed in codes:
            status = "👁️ Viewed" if viewed else "🆕 New"
            text += f"📌 `{code}` - {status}\n   {sent_date[:10]}\n\n"
            if not viewed:
                self.db.mark_code_as_viewed(code, user_id)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    # ==================== ADMIN COMMANDS ====================
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized.")
            return
        keyboard = [
            [InlineKeyboardButton("📋 Pending", callback_data="admin_pending")],
            [InlineKeyboardButton("➕ Add Code", callback_data="admin_addcode")],
            [InlineKeyboardButton("📤 Send Code", callback_data="admin_sendcode")],
            [InlineKeyboardButton("📚 List Codes", callback_data="admin_listcodes")],
            [InlineKeyboardButton("💳 Credits", callback_data="admin_credits")],
            [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
            [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        ]
        await update.message.reply_text("⚙️ **Admin Panel**", reply_markup=InlineKeyboardMarkup(keyboard))

    async def pending_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized.")
            return
        pending = self.db.get_pending_withdrawals()
        if not pending:
            await update.message.reply_text("✅ No pending withdrawals.")
            return
        for w in pending:
            w_id, u_id, username, first_name, amount, date = w
            name = f"@{username}" if username else first_name
            keyboard = [
                [InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{w_id}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{w_id}")]
            ]
            await update.message.reply_text(
                f"🆔 **#{w_id}**\n👤 {name} (ID: `{u_id}`)\n💰 `{amount}` credits\n📅 {date}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    async def addcode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized.")
            return ConversationHandler.END
        context.user_data['adding_code'] = True
        await update.message.reply_text(
            "🎟️ **Add Code**\n\nFormat: `<code> <amount>`\nExample: `REDEEM123 30`\n\nSend /cancel to cancel."
        )
        return GET_REDEEM_CODE

    async def get_code_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.user_data.get('adding_code'):
            return ConversationHandler.END
        try:
            parts = update.message.text.split()
            if len(parts) != 2:
                await update.message.reply_text("❌ Format: `<code> <amount>`")
                return GET_REDEEM_CODE
            code = parts[0].upper()
            amount = int(parts[1])
            if amount <= 0:
                await update.message.reply_text("❌ Amount must be > 0.")
                return GET_REDEEM_CODE
            if self.db.redeem_code_exists(code):
                await update.message.reply_text("❌ Code already exists.")
                return GET_REDEEM_CODE
            if self.db.add_redeem_code(code, amount, update.effective_user.id):
                await update.message.reply_text(f"✅ Code added: `{code}` - {amount} credits")
            else:
                await update.message.reply_text("❌ Failed to add code.")
            context.user_data['adding_code'] = False
            return ConversationHandler.END
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
            return GET_REDEEM_CODE
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
            return ConversationHandler.END

    async def sendcode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized.")
            return
        if len(context.args) < 2:
            await update.message.reply_text("Usage: `/sendcode <user_id> <code>`")
            return
        try:
            target_user_id = int(context.args[0])
            code = context.args[1].upper()
            if not self.db.redeem_code_exists(code):
                await update.message.reply_text("❌ Code not found or already used.")
                return
            user_data = self.db.get_user(target_user_id)
            if not user_data:
                await update.message.reply_text("❌ User not found.")
                return
            self.db.cursor.execute('''
                UPDATE redeem_codes 
                SET used = 1, used_by = ?, used_date = datetime('now')
                WHERE code = ?
            ''', (target_user_id, code))
            self.db.cursor.execute('''
                INSERT INTO private_codes (code, user_id, sent_date)
                VALUES (?, ?, ?)
            ''', (code, target_user_id, datetime.now()))
            self.db.conn.commit()
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"🎟️ **Code Received!**\n\n🔒 `{code}`\n\nPrivate code."
                )
                await update.message.reply_text(f"✅ Code sent to user {target_user_id}")
            except Exception as e:
                await update.message.reply_text(f"❌ Failed to send: {e}")
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    async def listcodes_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized.")
            return
        codes = self.db.get_all_redeem_codes()
        if not codes:
            await update.message.reply_text("📭 No codes.")
            return
        text = "🎟️ **Codes**\n\n"
        for code, amount, created, used, used_by, expiry in codes[:20]:
            status = "✅ Used" if used else "🟢 Available"
            text += f"`{code}` - {amount} credits - {status}\n"
            if used:
                text += f"Used by: {used_by}\n"
            text += f"Expires: {expiry[:10]}\n\n"
        if len(codes) > 20:
            text += f"\n... and {len(codes) - 20} more"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized.")
            return
        self.db.cursor.execute("SELECT COUNT(*) FROM users")
        total_users = self.db.cursor.fetchone()[0]
        self.db.cursor.execute("SELECT SUM(credits) FROM users")
        total_credits = self.db.cursor.fetchone()[0] or 0
        self.db.cursor.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")
        pending = self.db.cursor.fetchone()[0]
        self.db.cursor.execute("SELECT COUNT(*) FROM redeem_codes WHERE used=0")
        available = self.db.cursor.fetchone()[0]
        text = (
            f"📊 **Stats**\n\n"
            f"👥 Users: `{total_users}`\n"
            f"⭐ Credits: `{total_credits}`\n"
            f"⏳ Pending: `{pending}`\n"
            f"🎟️ Available: `{available}`"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def broadcast_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized.")
            return
        if not context.args:
            await update.message.reply_text("Usage: `/broadcast <message>`")
            return
        message = " ".join(context.args)
        users = self.db.get_all_users()
        sent = 0
        for user in users:
            try:
                await context.bot.send_message(chat_id=user[0], text=f"📢 **Broadcast**\n\n{message}")
                sent += 1
                await asyncio.sleep(0.05)
            except:
                pass
        await update.message.reply_text(f"✅ Sent to `{sent}` users.")

    async def addcredits_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized.")
            return
        if len(context.args) < 2:
            await update.message.reply_text("Usage: `/addcredits <user_id> <amount>`")
            return
        try:
            search = context.args[0]
            user_info = self.db.find_user_by_username_or_id(search)
            if not user_info:
                await update.message.reply_text("❌ User not found.")
                return
            target = user_info[0]
            amount = int(context.args[1])
            if amount <= 0:
                await update.message.reply_text("❌ Amount must be > 0.")
                return
            if self.db.update_user_credits(target, amount):
                user = self.db.get_user(target)
                await update.message.reply_text(f"✅ Added `+{amount}` credits.\nNew balance: `{user[6]}`")
            else:
                await update.message.reply_text("❌ Failed.")
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    async def deductcredits_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized.")
            return
        if len(context.args) < 2:
            await update.message.reply_text("Usage: `/deductcredits <user_id> <amount>`")
            return
        try:
            search = context.args[0]
            user_info = self.db.find_user_by_username_or_id(search)
            if not user_info:
                await update.message.reply_text("❌ User not found.")
                return
            target = user_info[0]
            amount = int(context.args[1])
            if amount <= 0:
                await update.message.reply_text("❌ Amount must be > 0.")
                return
            current = self.db.get_user(target)
            if current[6] < amount:
                await update.message.reply_text(f"❌ User has only `{current[6]}` credits.")
                return
            if self.db.update_user_credits(target, -amount):
                user = self.db.get_user(target)
                await update.message.reply_text(f"✅ Deducted `-{amount}` credits.\nNew balance: `{user[6]}`")
            else:
                await update.message.reply_text("❌ Failed.")
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    async def setcredits_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized.")
            return
        if len(context.args) < 2:
            await update.message.reply_text("Usage: `/setcredits <user_id> <amount>`")
            return
        try:
            search = context.args[0]
            user_info = self.db.find_user_by_username_or_id(search)
            if not user_info:
                await update.message.reply_text("❌ User not found.")
                return
            target = user_info[0]
            amount = int(context.args[1])
            if amount < 0:
                await update.message.reply_text("❌ Amount cannot be negative.")
                return
            if self.db.set_user_credits(target, amount):
                await update.message.reply_text(f"✅ Balance set to `{amount}` credits.")
            else:
                await update.message.reply_text("❌ Failed.")
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    async def finduser_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized.")
            return
        if not context.args:
            await update.message.reply_text("Usage: `/finduser <user_id or username>`")
            return
        try:
            search = context.args[0]
            user_info = self.db.find_user_by_username_or_id(search)
            if not user_info:
                await update.message.reply_text("❌ User not found.")
                return
            target = user_info[0]
            user = self.db.get_user(target)
            if not user:
                await update.message.reply_text("❌ User data not found.")
                return
            referrals = self.db.get_referral_count(target)
            codes = self.db.get_private_codes_for_user(target)
            text = (
                f"👤 **User**\n\n"
                f"🆔 ID: `{target}`\n"
                f"👤 Name: {user[2]} {user[3] or ''}\n"
                f"🔗 Code: `{user[5]}`\n"
                f"📅 Joined: {user[4][:10]}\n\n"
                f"⭐ Credits: `{user[6]}`\n"
                f"👥 Referrals: `{referrals}`\n"
                f"🎁 Withdrawals: `{user[9]}`\n"
                f"🎟️ Codes: `{len(codes)}`"
            )
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.message.reply_text("✅ Cancelled.")
        return ConversationHandler.END

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Error: {context.error}")
        try:
            if update and update.effective_user:
                await context.bot.send_message(
                    chat_id=update.effective_user.id,
                    text="⚠️ An error occurred. Please try again."
                )
        except:
            pass

    def run(self):
        if not self.application:
            self.setup()
        logger.info("🚀 Bot is starting...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

# ==================== MAIN ====================
if __name__ == "__main__":
    bot = RedeemBot()
    bot.run()
