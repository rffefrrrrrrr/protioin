# -*- coding: utf-8 -*-
"""
‎بوت تيليجرام للحماية بنظام كابتشا
‎يقوم بحماية المجموعات من الأعضاء الجدد عبر نظام كابتشا
"""

import re
import logging
import asyncio
import random
import os
from dotenv import load_dotenv

load_dotenv() # Load environment variables from .env file
import fcntl
from datetime import datetime, timedelta
from typing import Dict, Set
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, filters, ContextTypes

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

‎# إعداد التسجيل
logging.basicConfig(
    format=\'%(asctime)s - %(name)s - %(levelname)s - %(message)s\',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

‎# توكن البوت من متغيرات البيئة
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN environment variable not set!")
    exit(1)

‎# رابط قاعدة البيانات من متغيرات البيئة
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable not set!")
    exit(1)

‎# معرفات المطورين (User IDs)
DEVELOPER_IDS = [6714288409, 6459577996]

‎# قاموس لتخزين حالة الحماية لكل مجموعة
protection_enabled: Dict[int, bool] = {}

‎# قاموس لتخزين الأعضاء الجدد الذين ينتظرون حل الكابتشا
pending_users: Dict[int, Dict[int, dict]] = {}

‎# قاموس لتخزين مهام الطرد المؤجلة
kick_tasks: Dict[str, asyncio.Task] = {}

# MongoDB Client
client: MongoClient = None
db = None

def get_db_client():
    global client, db
    if client is None or not client.admin.command(\'ping\'):
        try:
            client = MongoClient(DATABASE_URL)
            db = client.protection_bot_db # You can choose your database name
            logger.info("Successfully connected to MongoDB.")
        except ConnectionFailure as e:
            logger.error(f"MongoDB connection failed: {e}")
            exit(1)
        except Exception as e:
            logger.error(f"An unexpected error occurred during MongoDB connection: {e}")
            exit(1)
    return db

def init_database():
‎    """تهيئة قاعدة البيانات (MongoDB لا تحتاج لإنشاء جداول صريحة) """
    # MongoDB is schema-less, collections are created on first insert.
    # We can ensure indexes here if needed.
    database = get_db_client()
    try:
        # Ensure indexes for efficient querying
        database.captcha_stats.create_index("user_id")
        database.captcha_stats.create_index("chat_id")
        database.captcha_stats.create_index("timestamp")

        database.users.create_index("user_id", unique=True)
        database.chats.create_index("chat_id", unique=True)
        database.chats.create_index("protection_enabled")
        database.chats.create_index("activating_admin_id")
        logger.info("MongoDB indexes ensured.")
    except OperationFailure as e:
        logger.error(f"Failed to create MongoDB indexes: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during MongoDB index creation: {e}")

def log_captcha_event(user_id: int, chat_id: int, status: str):
‎    """تسجيل حدث كابتشا في قاعدة البيانات"""
    database = get_db_client()
    try:
        database.captcha_stats.insert_one({
            "user_id": user_id,
            "chat_id": chat_id,
            "status": status,
            "timestamp": datetime.now()
        })
    except Exception as e:
        logger.error(f"Error logging captcha event to MongoDB: {e}")

def update_user_info(user_id: int, username: str = None, first_name: str = None):
‎    """تحديث معلومات المستخدم في قاعدة البيانات"""
    database = get_db_client()
    try:
        database.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "username": username,
                "first_name": first_name,
                "last_interaction": datetime.now()
            }},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error updating user info in MongoDB: {e}")

def update_chat_info(chat_id: int, chat_title: str = None, protection_enabled: bool = None, admin_id: int = None):
‎    """تحديث معلومات المجموعة في قاعدة البيانات"""
    database = get_db_client()
    update_fields = {"last_activity": datetime.now()}
    if chat_title is not None:
        update_fields["chat_title"] = chat_title
    if protection_enabled is not None:
        update_fields["protection_enabled"] = protection_enabled
    if admin_id is not None:
        update_fields["activating_admin_id"] = admin_id

    try:
        database.chats.update_one(
            {"chat_id": chat_id},
            {"$set": update_fields},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error updating chat info in MongoDB: {e}")

def get_stats(user_id: int = None, chat_id: int = None, hours: int = None):
‎    """الحصول على الإحصائيات"""
    database = get_db_client()
    query = {}
    if chat_id:
        query["chat_id"] = chat_id
    if user_id:
        query["user_id"] = user_id
    if hours:
        query["timestamp"] = {"$gte": datetime.now() - timedelta(hours=hours)}

    stats = {\'success\': 0, \'kicked\': 0, \'timeout\': 0}
    try:
        pipeline = [
            {"$match": query},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}}
        ]
        results = database.captcha_stats.aggregate(pipeline)
        for res in results:
            stats[res["_id"]] = res["count"]
    except Exception as e:
        logger.error(f"Error getting stats from MongoDB: {e}")
    return stats

def get_bot_stats():
‎    """الحصول على إحصائيات البوت العامة"""
    database = get_db_client()
    total_chats = 0
    total_users = 0
    try:
        total_chats = database.chats.distinct("chat_id")
        total_users = database.users.distinct("user_id")
    except Exception as e:
        logger.error(f"Error getting bot stats from MongoDB: {e}")
    return {"total_chats": len(total_chats), "total_users": len(total_users)}

def get_all_users():
‎    """الحصول على جميع المستخدمين"""
    database = get_db_client()
    users = []
    try:
        users = [user["user_id"] for user in database.users.find({}, {"user_id": 1})]
    except Exception as e:
        logger.error(f"Error getting all users from MongoDB: {e}")
    return users

def get_all_chats():
‎    """الحصول على جميع المجموعات التي تم تفعيل الحماية فيها"""
    database = get_db_client()
    chats = []
    try:
        chats = [chat["chat_id"] for chat in database.chats.find({"protection_enabled": True}, {"chat_id": 1})]
    except Exception as e:
        logger.error(f"Error getting all chats from MongoDB: {e}")
    return chats

def is_activating_admin(user_id: int) -> bool:
‎    """التحقق مما إذا كان المستخدم هو المشرف الذي قام بتفعيل البوت في أي مجموعة"""
    database = get_db_client()
    try:
        result = database.chats.find_one({"protection_enabled": True, "activating_admin_id": user_id})
        return result is not None
    except Exception as e:
        logger.error(f"Error checking activating admin in MongoDB: {e}")
        return False

class CaptchaGenerator:
‎    """مولد أسئلة الكابتشا"""
    
    @staticmethod
    def generate_math_captcha():
‎        """توليد سؤال رياضي بسيط"""
        num1 = random.randint(1, 10)
        num2 = random.randint(1, 10)
        operation = random.choice([\'+\', \'-\', \'*\'])
        
        if operation == \'+\':
            answer = num1 + num2
            question = f"كم يساوي {num1} + {num2}؟"
        elif operation == \'-\':
            if num1 < num2:
                num1, num2 = num2, num1
            answer = num1 - num2
            question = f"كم يساوي {num1} - {num2}؟"
        else:  # multiplication
            answer = num1 * num2
            question = f"كم يساوي {num1} × {num2}؟"
        
        return question, answer
    
    @staticmethod
    def generate_options(correct_answer):
‎        """توليد خيارات متعددة للإجابة"""
        options = [correct_answer]
        
        seen_options = {correct_answer}
        while len(options) < 4:
            wrong_answer = correct_answer + random.choice([-1, 1]) * random.randint(1, 10)
            
            if wrong_answer not in seen_options and wrong_answer >= 0:
                options.append(wrong_answer)
                seen_options.add(wrong_answer)
            else:
                for _ in range(10):
                    wrong_answer = random.randint(max(0, correct_answer - 15), correct_answer + 15)
                    if wrong_answer not in seen_options and wrong_answer >= 0:
                        options.append(wrong_answer)
                        seen_options.add(wrong_answer)
                        break
        
        random.shuffle(options)
        return options

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"start_command: Received /start command from user {update.effective_user.id}")
‎    """معالج أمر /start"""
    user = update.effective_user
    
    update_user_info(user.id, user.username, user.first_name)
    
    if update.effective_chat.type == \'private\':
        message_text = (
‎            "مرحباً! أنا بوت حماية المجموعات.\n"
‎            "أضفني إلى مجموعتك واجعلني مشرفاً لأتمكن من حمايتها.\n"
‎            "استخدم الأمر \'تفعيل\' في المجموعة لتفعيل نظام الحماية.\n"
        )
        
        main_keyboard = []
        
        if user.id in DEVELOPER_IDS:
            main_keyboard.append([InlineKeyboardButton("⚙️ أوامر المطورين", callback_data="dev_commands_menu")])

        if is_activating_admin(user.id):
            main_keyboard.append([InlineKeyboardButton("🛠️ أوامر المشرفين", callback_data="admin_commands_menu")])

        if not main_keyboard:
             message_text += "\n\nلتفعيل الأزرار الخاصة، قم بتفعيل البوت في إحدى مجموعاتك."

        reply_markup = InlineKeyboardMarkup(main_keyboard) if main_keyboard else None
        if update.message:
            await update.message.reply_text(message_text, reply_markup=reply_markup)
        elif update.callback_query:
            await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup)
        else:
            # Fallback if neither message nor callback_query is present, which shouldn\'t happen for /start
            await context.bot.send_message(chat_id=update.effective_chat.id, text=message_text, reply_markup=reply_markup)
            logger.warning("start_command: Neither update.message nor update.callback_query was present, used fallback send_message.")

    else:
        await update.message.reply_text(
‎            "مرحباً! أنا بوت الحماية.\n"
‎            "استخدم الأمر \'تفعيل\' لتفعيل نظام الحماية في هذه المجموعة."
        )

async def enable_protection(update: Update, context: ContextTypes.DEFAULT_TYPE):
‎    """تفعيل نظام الحماية"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in [\'administrator\', \'creator\'] and user_id not in DEVELOPER_IDS:
            await update.effective_chat.send_message("عذراً، يمكن للمشرفين أو المطورين فقط تفعيل نظام الحماية.")
            return
    except Exception as e:
        logger.error(f"خطأ في التحقق من صلاحيات المستخدم: {e}")
        return
    
    update_chat_info(chat_id, update.effective_chat.title, True, user_id)
    protection_enabled[chat_id] = True
    await update.message.reply_text(
‎        "✅ تم تفعيل نظام الحماية بنجاح!\n"
‎        "سيتم الآن طلب حل كابتشا من جميع الأعضاء الجدد.\n"
‎        "إذا لم يحلوا الكابتشا خلال 30 دقيقة، سيتم طردهم تلقائياً."
    )

async def disable_protection(update: Update, context: ContextTypes.DEFAULT_TYPE):
‎    """إلغاء تفعيل نظام الحماية"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in [\'administrator\', \'creator\'] and user_id not in DEVELOPER_IDS:
            await update.effective_chat.send_message("عذراً، يمكن للمشرفين أو المطورين فقط إلغاء تفعيل نظام الحماية.")
            return
    except Exception as e:
        logger.error(f"خطأ في التحقق من صلاحيات المستخدم: {e}")
        return
    
    update_chat_info(chat_id, update.effective_chat.title, False, None)
    protection_enabled[chat_id] = False
    
    tasks_to_cancel = []
    for task_key, task in kick_tasks.items():
        if task_key.startswith(f"{chat_id}_"):
            tasks_to_cancel.append(task_key)
    
    for task_key in tasks_to_cancel:
        kick_tasks[task_key].cancel()
        del kick_tasks[task_key]
    
    if chat_id in pending_users:
        del pending_users[chat_id]
    
    await update.message.reply_text("❌ تم إلغاء تفعيل نظام الحماية.")

async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
‎    """معالج الأعضاء الجدد"""
    chat_id = update.effective_chat.id
    
    if not protection_enabled.get(chat_id, False):
        return
    
    new_users_to_process = []
    if update.message and update.message.new_chat_members:
        new_users_to_process.extend(update.message.new_chat_members)
    elif update.chat_member and update.chat_member.new_chat_member.status == ChatMember.MEMBER:
        new_users_to_process.append(update.chat_member.new_chat_member.user)
    
    if not new_users_to_process:
        return
    
    for new_user in new_users_to_process:
        user_id = new_user.id
        
        if new_user.is_bot:
            continue
        
        question, correct_answer = CaptchaGenerator.generate_math_captcha()
        options = CaptchaGenerator.generate_options(correct_answer)
        
        keyboard = []
        for i, option in enumerate(options):
            keyboard.append([InlineKeyboardButton(str(option), callback_data=f"captcha_{user_id}_{option}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if chat_id not in pending_users:
            pending_users[chat_id] = {}
        
        pending_users[chat_id][user_id] = {
            \'correct_answer\': correct_answer,
            \'join_time\': datetime.now(),
            \'username\': new_user.username or new_user.first_name,
            \'wrong_attempts\': 0
        }
        
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=telegram.ChatPermissions(can_send_messages=False)
            )
            
            captcha_message = await context.bot.send_message(
                chat_id=chat_id,
                text=f"مرحباً {new_user.mention_html()}!\n\n"
                     f"لضمان أنك لست بوت، يرجى حل هذا السؤال:\n\n"
                     f"❓ {question}\n\n"
                     f"⏰ لديك 30 دقيقة لحل السؤال، وإلا سيتم طردك تلقائياً.",
                reply_markup=reply_markup,
                parse_mode=\'HTML\'
            )
            
            pending_users[chat_id][user_id][\'message_id\'] = captcha_message.message_id
            
            task_key = f"{chat_id}_{user_id}"
            kick_task = asyncio.create_task(
                schedule_kick(context, chat_id, user_id, captcha_message.message_id)
            )
            kick_tasks[task_key] = kick_task
            
        except Exception as e:
            logger.error(f"خطأ في معالجة العضو الجديد: {e}")

async def captcha_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
‎    """معالج إجابات الكابتشا"""
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    callback_data = query.data
    
    if not callback_data.startswith("captcha_"):
        return
    
    parts = callback_data.split("_")
    if len(parts) != 3:
        return
    
    user_id = int(parts[1])
    selected_answer = int(parts[2])
    
    if chat_id not in pending_users or user_id not in pending_users[chat_id]:
        await query.edit_message_text("❌ انتهت صلاحية هذا السؤال.")
        return
    
    if query.from_user.id != user_id:
        await query.answer("❌ يمكنك فقط الإجابة على سؤالك الخاص!", show_alert=True)
        return
    
    user_data = pending_users[chat_id][user_id]
    correct_answer = user_data[\'correct_answer\']
    
    if selected_answer == correct_answer:
        # User solved the captcha correctly
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=telegram.ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True)
            )
            await query.edit_message_text(
                f"✅ أحسنت {query.from_user.mention_html()}! تم التحقق منك بنجاح. يمكنك الآن التحدث في المجموعة.",
                parse_mode=\'HTML\'
            )
            log_captcha_event(user_id, chat_id, \'success\')
        except Exception as e:
            logger.error(f"خطأ في منح صلاحيات العضو بعد حل الكابتشا: {e}")
            await query.edit_message_text("حدث خطأ أثناء منحك صلاحيات التحدث. يرجى إبلاغ المشرف.")
        finally:
            # Cancel pending kick task if it exists
            task_key = f"{chat_id}_{user_id}"
            if task_key in kick_tasks:
                kick_tasks[task_key].cancel()
                del kick_tasks[task_key]
            if user_id in pending_users[chat_id]:
                del pending_users[chat_id][user_id]
    else:
        # User answered incorrectly
        user_data[\'wrong_attempts\'] += 1
        if user_data[\'wrong_attempts\'] >= 3:
            # Kick user after 3 wrong attempts
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await query.edit_message_text(
                    f"❌ {query.from_user.mention_html()} لقد أجبت بشكل خاطئ عدة مرات. تم طردك من المجموعة.",
                    parse_mode=\'HTML\'
                )
                log_captcha_event(user_id, chat_id, \'kicked\')
            except Exception as e:
                logger.error(f"خطأ في طرد العضو بعد محاولات خاطئة: {e}")
                await query.edit_message_text("حدث خطأ أثناء طردك. يرجى إبلاغ المشرف.")
            finally:
                task_key = f"{chat_id}_{user_id}"
                if task_key in kick_tasks:
                    kick_tasks[task_key].cancel()
                    del kick_tasks[task_key]
                if user_id in pending_users[chat_id]:
                    del pending_users[chat_id][user_id]
        else:
            # Regenerate captcha question and options
            question, correct_answer = CaptchaGenerator.generate_math_captcha()
            options = CaptchaGenerator.generate_options(correct_answer)
            
            keyboard = []
            for i, option in enumerate(options):
                keyboard.append([InlineKeyboardButton(str(option), callback_data=f"captcha_{user_id}_{option}")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            pending_users[chat_id][user_id][\'correct_answer\'] = correct_answer
            
            await query.edit_message_text(
                f"❌ إجابة خاطئة. يرجى المحاولة مرة أخرى.\n\n"
                f"❓ {question}\n\n"
                f"⏰ لديك 30 دقيقة لحل السؤال، وإلا سيتم طردك تلقائياً.",
                reply_markup=reply_markup,
                parse_mode=\'HTML\'
            )

async def schedule_kick(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, message_id: int):
‎    """جدولة طرد العضو إذا لم يحل الكابتشا في الوقت المحدد"""
    await asyncio.sleep(1800)  # 30 minutes
    
    if chat_id in pending_users and user_id in pending_users[chat_id]:
        try:
            await context.bot.ban_chat_member(chat_id, user_id)
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ انتهى الوقت! تم طرد {pending_users[chat_id][user_id][\'username\']} من المجموعة لعدم حل الكابتشا."
            )
            log_captcha_event(user_id, chat_id, \'timeout\')
        except Exception as e:
            logger.error(f"خطأ في طرد العضو بعد انتهاء الوقت: {e}")
        finally:
            task_key = f"{chat_id}_{user_id}"
            if task_key in kick_tasks:
                del kick_tasks[task_key]
            if user_id in pending_users[chat_id]:
                del pending_users[chat_id][user_id]

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
‎    """معالج الأخطاء"""
    logger.error(f"Update {update} caused error {context.error}")

async def post_init(application: Application):
‎    """دالة يتم تشغيلها بعد تهيئة البوت"""
    init_database()
    
    # Load initial protection status from DB
    database = get_db_client()
    try:
        for chat in database.chats.find({"protection_enabled": True}):
            protection_enabled[chat["chat_id"]] = True
        logger.info(f"Loaded initial protection status for {len(protection_enabled)} chats.")
    except Exception as e:
        logger.error(f"Error loading initial protection status: {e}")

def main():
‎    """الدالة الرئيسية لتشغيل البوت"""
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("تفعيل", enable_protection))
    application.add_handler(CommandHandler("تعطيل", disable_protection))
    application.add_handler(CallbackQueryHandler(captcha_callback_handler, pattern=re.compile(r"^captcha_\\d+_\\d+$")))
    
    # Use ChatMemberHandler for new members to handle both `new_chat_members` and `chat_member` updates
    application.add_handler(ChatMemberHandler(new_member_handler, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler))

    # Error handler
    application.add_error_handler(error_handler)

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == \'__main__\':
    main()


