
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
بوت تيليجرام للحماية بنظام كابتشا
يقوم بحماية المجموعات من الأعضاء الجدد عبر نظام كابتشا
"""

import re
import logging
import asyncio
import random
import fcntl
from datetime import datetime, timedelta
from typing import Dict, Set
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, filters, ContextTypes
from flask import Flask, request
import threading
import json

# MongoDB imports
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# توكن البوت ومتغيرات البيئة
import os
from dotenv import load_dotenv

load_dotenv()  # لتحميل المتغيرات من ملف .env

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# معرفات المطورين (User IDs)
DEVELOPER_IDS = [6714288409, 6459577996]

# قاموس لتخزين حالة الحماية لكل مجموعة (للتخزين المؤقت في الذاكرة)
protection_enabled: Dict[int, bool] = {}

# قاموس لتخزين الأعضاء الجدد الذين ينتظرون حل الكابتشا
pending_users: Dict[int, Dict[int, dict]] = {}

# قاموس لتخزين مهام الطرد المؤجلة
kick_tasks: Dict[str, asyncio.Task] = {}

# MongoDB Client and Database
client: MongoClient = None
db = None

# Flask app
app = Flask(__name__)

# Telegram Bot Application
application: Application = None

def init_mongodb():
    global client, db
    try:
        client = MongoClient(MONGO_URI)
        client.admin.command("ping") # The ping command is cheap and does not require auth.
        db = client.protection_bot_db
        logger.info("Connected to MongoDB successfully!")
    except ConnectionFailure as e:
        logger.error(f"MongoDB connection failed: {e}")
        raise

async def log_captcha_event(user_id: int, chat_id: int, status: str):
    """تسجيل حدث كابتشا في قاعدة البيانات"""
    if db is not None:
        db.captcha_stats.insert_one({
            "user_id": user_id,
            "chat_id": chat_id,
            "status": status,
            "timestamp": datetime.now()
        })

async def update_user_info(user_id: int, username: str = None, first_name: str = None):
    """تحديث معلومات المستخدم في قاعدة البيانات"""
    if db is not None:
        db.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "username": username,
                "first_name": first_name,
                "last_interaction": datetime.now()
            }},
            upsert=True
        )

async def update_chat_info(chat_id: int, chat_title: str = None, protection_enabled_status: bool = None, admin_id: int = None):
    """تحديث معلومات المجموعة في قاعدة البيانات"""
    if db is not None:
        update_data = {"chat_title": chat_title, "last_activity": datetime.now()}
        if protection_enabled_status is not None:
            update_data["protection_enabled"] = protection_enabled_status
        if admin_id is not None:
            update_data["activating_admin_id"] = admin_id

        db.chats.update_one(
            {"chat_id": chat_id},
            {"$set": update_data},
            upsert=True
        )

async def get_stats(user_id: int = None, chat_id: int = None, hours: int = None):
    """الحصول على الإحصائيات"""
    if db is not None:
        query = {}
        if chat_id:
            query["chat_id"] = chat_id
        if user_id:
            query["user_id"] = user_id
        if hours:
            query["timestamp"] = {"$gte": datetime.now() - timedelta(hours=hours)}
        
        pipeline = [
            {"$match": query},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}}
        ]
        results = list(db.captcha_stats.aggregate(pipeline))
        
        stats = {"success": 0, "kicked": 0, "timeout": 0}
        for res in results:
            stats[res["_id"]] = res["count"]
        return stats
    return {"success": 0, "kicked": 0, "timeout": 0}

async def get_bot_stats():
    """الحصول على إحصائيات البوت العامة"""
    if db is not None:
        total_chats = db.chats.count_documents({})
        total_users = db.users.count_documents({})
        return {"total_chats": total_chats, "total_users": total_users}
    return {"total_chats": 0, "total_users": 0}

async def get_all_users():
    """الحصول على جميع المستخدمين"""
    if db is not None:
        users = list(db.users.find({}, {"user_id": 1, "_id": 0}))
        return [user["user_id"] for user in users]
    return []

async def get_all_chats():
    """الحصول على جميع المجموعات التي تم تفعيل الحماية فيها"""
    if db is not None:
        chats = list(db.chats.find({"protection_enabled": True}, {"chat_id": 1, "_id": 0}))
        return [chat["chat_id"] for chat in chats]
    return []

async def is_activating_admin(user_id: int) -> bool:
    """التحقق مما إذا كان المستخدم هو المشرف الذي قام بتفعيل البوت في أي مجموعة"""
    if db is not None:
        count = db.chats.count_documents({"protection_enabled": True, "activating_admin_id": user_id})
        return count > 0
    return False

class CaptchaGenerator:
    """مولد أسئلة الكابتشا"""
    
    @staticmethod
    def generate_math_captcha():
        """توليد سؤال رياضي بسيط"""
        num1 = random.randint(1, 10)
        num2 = random.randint(1, 10)
        operation = random.choice(["+", "-", "*"])
        
        if operation == "+":
            answer = num1 + num2
            question = f"كم يساوي {num1} + {num2}؟"
        elif operation == "-":
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
        """توليد خيارات متعددة للإجابة"""
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
    logger.info(f"start_command: Received /start command from user {update.effective_user.id} in chat type {update.effective_chat.type}")
    """معالج أمر /start"""
    user = update.effective_user
    
    await update_user_info(user.id, user.username, user.first_name)
    
    if update.effective_chat.type == "private":
        message_text = (
            "مرحباً! أنا بوت حماية المجموعات.\n"
            "أضفني إلى مجموعتك واجعلني مشرفاً لأتمكن من حمايتها.\n"
            "استخدم الأمر \'تفعيل\' في المجموعة لتفعيل نظام الحماية.\n"
        )
        
        main_keyboard = []
        
        if user.id in DEVELOPER_IDS:
            main_keyboard.append([InlineKeyboardButton("⚙️ أوامر المطورين", callback_data="dev_commands_menu")])

        if await is_activating_admin(user.id):
            main_keyboard.append([InlineKeyboardButton("🛠️ أوامر المشرفين", callback_data="admin_commands_menu")])

        if not main_keyboard:
             message_text += "\n\nلتفعيل الأزرار الخاصة، قم بتفعيل البوت في إحدى مجموعاتك."

        reply_markup = InlineKeyboardMarkup(main_keyboard) if main_keyboard else None
        if update.message:
            await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode="HTML")
        elif update.callback_query:
            await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            logger.error("لا يوجد update.message أو update.callback_query في start_command")

    else:
        await update.message.reply_text(
            "مرحباً! أنا بوت الحماية.\n"
            "استخدم الأمر \'تفعيل\' لتفعيل نظام الحماية في هذه المجموعة."
        )

async def enable_protection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفعيل نظام الحماية"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ["administrator", "creator"] and user_id not in DEVELOPER_IDS:
            await update.effective_chat.send_message("عذراً، يمكن للمشرفين أو المطورين فقط تفعيل نظام الحماية.")
            return
    except Exception as e:
        logger.error(f"خطأ في التحقق من صلاحيات المستخدم: {e}")
        return
    
    await update_chat_info(chat_id, update.effective_chat.title, True, user_id)
    protection_enabled[chat_id] = True
    await update.message.reply_text(
        "✅ تم تفعيل نظام الحماية بنجاح!\n"
        "سيتم الآن طلب حل كابتشا من جميع الأعضاء الجدد.\n"
        "إذا لم يحلوا الكابتشا خلال 30 دقيقة، سيتم طردهم تلقائياً."
    )

async def disable_protection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء تفعيل نظام الحماية"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ["administrator", "creator"] and user_id not in DEVELOPER_IDS:
            await update.effective_chat.send_message("عذراً، يمكن للمشرفين أو المطورين فقط إلغاء تفعيل نظام الحماية.")
            return
    except Exception as e:
        logger.error(f"خطأ في التحقق من صلاحيات المستخدم: {e}")
        return
    
    await update_chat_info(chat_id, update.effective_chat.title, False, None)
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
    """معالج الأعضاء الجدد"""
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
            "correct_answer": correct_answer,
            "join_time": datetime.now(),
            "username": new_user.username or new_user.first_name,
            "wrong_attempts": 0
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
                parse_mode="HTML"
            )
            
            pending_users[chat_id][user_id]["message_id"] = captcha_message.message_id
            
            task_key = f"{chat_id}_{user_id}"
            kick_task = asyncio.create_task(
                schedule_kick(context, chat_id, user_id, captcha_message.message_id)
            )
            kick_tasks[task_key] = kick_task
            
        except Exception as e:
            logger.error(f"خطأ في معالجة العضو الجديد: {e}")

async def captcha_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج إجابات الكابتشا"""
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
    correct_answer = user_data["correct_answer"]
    
    if selected_answer == correct_answer:
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=telegram.ChatPermissions(
                    can_send_messages=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_change_info=False,
                    can_invite_users=True,
                    can_pin_messages=False,
                )
            )
            
            task_key = f"{chat_id}_{user_id}"
            if task_key in kick_tasks:
                kick_tasks[task_key].cancel()
                del kick_tasks[task_key]
            await context.bot.send_message(chat_id, f"✅ أحسنت! {query.from_user.mention_html()} لقد أجبت بشكل صحيح. تم فك التقييد عنك.", parse_mode="HTML")
            await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
            
            del pending_users[chat_id][user_id]
            
            await log_captcha_event(user_id, chat_id, "success")
        except Exception as e:
            logger.error(f"خطأ في إلغاء تقييد المستخدم {user_id} من {chat_id} بعد حل الكابتشا: {e}")
    else:
        user_data["wrong_attempts"] += 1
        await query.answer("❌ إجابة خاطئة. حاول مرة أخرى.", show_alert=True)
        
        if user_data["wrong_attempts"] >= 2:
            logger.info(f"محاولة طرد المستخدم {user_id} من {chat_id} بعد {user_data["wrong_attempts"]} محاولات خاطئة.")
            await context.bot.send_message(chat_id, f"❌ إجابات خاطئة متكررة. سيتم طردك. @{query.from_user.username or query.from_user.first_name}")
            try:
                await context.bot.unban_chat_member(chat_id, user_id) # Kicking is unbanning a restricted user who is currently restricted
                await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
                await log_captcha_event(user_id, chat_id, "kicked")
            except Exception as e:
                logg
(Content truncated due to size limit. Use page ranges or line ranges to read remaining content)

