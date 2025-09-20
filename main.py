#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
بوت تيليجرام للحماية بنظام كابتشا
يقوم بحماية المجموعات من الأعضاء الجدد عبر نظام كابتشا
"""

import os
import re
import logging
import asyncio
import random
import pymongo

import fcntl
from datetime import datetime, timedelta
from typing import Dict, Set
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, filters, ContextTypes

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# توكن البوت
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set.")

# معرفات المطورين (User IDs)
DEVELOPER_IDS = [6714288409, 6459577996]

# قاموس لتخزين حالة الحماية لكل مجموعة
protection_enabled: Dict[int, bool] = {}

# قاموس لتخزين الأعضاء الجدد الذين ينتظرون حل الكابتشا
pending_users: Dict[int, Dict[int, dict]] = {}

# قاموس لتخزين مهام الطرد المؤجلة
kick_tasks: Dict[str, asyncio.Task] = {}

# قاعدة البيانات
MONGO_URI = "mongodb+srv://Lara:Lara123.@cluster0.atthgpa.mongodb.net/?retryWrites=true&w=majority"
MONGO_DB_NAME = "protection_bot_db"

client = pymongo.MongoClient(MONGO_URI)
db = client[MONGO_DB_NAME]

captcha_stats_collection = db["captcha_stats"]
users_collection = db["users"]
chats_collection = db["chats"]

def init_database():
    """تهيئة قاعدة البيانات"""
    # MongoDB collections are created on first insert, no explicit init needed
    # Ensure indexes for faster queries
    captcha_stats_collection.create_index([("user_id", 1), ("chat_id", 1)])
    users_collection.create_index("user_id", unique=True)
    chats_collection.create_index("chat_id", unique=True)
    

def log_captcha_event(user_id: int, chat_id: int, status: str):
    """تسجيل حدث كابتشا في قاعدة البيانات"""
    captcha_stats_collection.insert_one({
        "user_id": user_id,
        "chat_id": chat_id,
        "status": status,
        "timestamp": datetime.now()
    })

def update_user_info(user_id: int, username: str = None, first_name: str = None):
    """تحديث معلومات المستخدم في قاعدة البيانات"""
    users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"username": username, "first_name": first_name, "last_interaction": datetime.now()}},
        upsert=True
    )


def update_chat_info(chat_id: int, chat_title: str = None, protection_enabled: bool = None, admin_id: int = None):
    """تحديث معلومات المجموعة في قاعدة البيانات"""
    if protection_enabled is not None:
        chats_collection.update_one(
            {"chat_id": chat_id},
            {"$set": {"chat_title": chat_title, "protection_enabled": protection_enabled, "activating_admin_id": admin_id, "last_activity": datetime.now()}},
            upsert=True
        )
    else:
        # Insert only if not exists, update last_activity if exists
        chats_collection.update_one(
            {"chat_id": chat_id},
            {"$set": {"chat_title": chat_title, "last_activity": datetime.now()}},
            upsert=True
        )


def get_stats(user_id: int = None, chat_id: int = None, hours: int = None):
    """الحصول على الإحصائيات"""
    query_filter = {}
    if chat_id:
        query_filter["chat_id"] = chat_id
    if user_id:
        query_filter["user_id"] = user_id
    if hours:
        time_threshold = datetime.now() - timedelta(hours=hours)
        query_filter["timestamp"] = {"$gte": time_threshold}

    pipeline = [
        {"$match": query_filter},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}
    ]
    results = captcha_stats_collection.aggregate(pipeline)

    stats = {"success": 0, "kicked": 0, "timeout": 0}
    for result in results:
        stats[result["_id"]] = result["count"]
    return stats

def get_bot_stats():
    """الحصول على إحصائيات البوت العامة"""
    total_chats = chats_collection.count_documents({})
    total_users = users_collection.count_documents({})
    return {"total_chats": total_chats, "total_users": total_users}

def get_all_users():
    """الحصول على جميع المستخدمين"""
    users = [user["user_id"] for user in users_collection.find({}, {"user_id": 1})]
    return users

def get_all_chats():
    """الحصول على جميع المجموعات التي تم تفعيل الحماية فيها"""
    chats = [chat["chat_id"] for chat in chats_collection.find({"protection_enabled": True}, {"chat_id": 1})]
    return chats

def is_activating_admin(user_id: int) -> bool:
    """التحقق مما إذا كان المستخدم هو المشرف الذي قام بتفعيل البوت في أي مجموعة"""
    result = chats_collection.find_one({"protection_enabled": True, "activating_admin_id": user_id})
    return result is not None

class CaptchaGenerator:
    """مولد أسئلة الكابتشا"""
    
    @staticmethod
    def generate_math_captcha():
        """توليد سؤال رياضي بسيط"""
        num1 = random.randint(1, 10)
        num2 = random.randint(1, 10)
        operation = random.choice(['+', '-', '*'])
        
        if operation == '+':
            answer = num1 + num2
            question = f"كم يساوي {num1} + {num2}؟"
        elif operation == '-':
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
    """معالج أمر /start"""
    user = update.effective_user
    
    update_user_info(user.id, user.username, user.first_name)
    
    if update.effective_chat.type == 'private':
        message_text = (
            "مرحباً! أنا بوت حماية المجموعات.\n"
            "أضفني إلى مجموعتك واجعلني مشرفاً لأتمكن من حمايتها.\n"
            "استخدم الأمر 'تفعيل' في المجموعة لتفعيل نظام الحماية.\n"
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
            logger.error("لا يوجد update.message أو update.callback_query في start_command")

    else:
        await update.message.reply_text(
            "مرحباً! أنا بوت الحماية.\n"
            "استخدم الأمر 'تفعيل' لتفعيل نظام الحماية في هذه المجموعة."
        )

async def enable_protection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفعيل نظام الحماية"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ['administrator', 'creator'] and user_id not in DEVELOPER_IDS:
            await update.effective_chat.send_message("عذراً، يمكن للمشرفين أو المطورين فقط تفعيل نظام الحماية.")
            return
    except Exception as e:
        logger.error(f"خطأ في التحقق من صلاحيات المستخدم: {e}")
        return
    
    update_chat_info(chat_id, update.effective_chat.title, True, user_id)
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
        if member.status not in ['administrator', 'creator'] and user_id not in DEVELOPER_IDS:
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
            'correct_answer': correct_answer,
            'join_time': datetime.now(),
            'username': new_user.username or new_user.first_name,
            'wrong_attempts': 0
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
                parse_mode='HTML'
            )
            
            pending_users[chat_id][user_id]['message_id'] = captcha_message.message_id
            
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
    correct_answer = user_data['correct_answer']
    
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
            await context.bot.send_message(chat_id, f"✅ أحسنت! {query.from_user.mention_html()} لقد أجبت بشكل صحيح. تم فك التقييد عنك.", parse_mode='HTML')
            await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
            
            del pending_users[chat_id][user_id]
            
            log_captcha_event(user_id, chat_id, 'success')
        except Exception as e:
            logger.error(f"خطأ في إلغاء تقييد العضو: {e}")
    else:
        user_data["wrong_attempts"] += 1
        
        if user_data["wrong_attempts"] == 1:
            question, correct_answer = CaptchaGenerator.generate_math_captcha()
            options = CaptchaGenerator.generate_options(correct_answer)
            
            keyboard = []
            for i, option in enumerate(options):
                keyboard.append([InlineKeyboardButton(str(option), callback_data=f"captcha_{user_id}_{option}")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                chat_id=chat_id,
                message_id=query.message.message_id,
                text=f"إجابة خاطئة! يرجى المحاولة مرة أخرى.\n\n"
                     f"❓ {question}\n\n"
                     f"⏰ لديك 30 دقيقة لحل السؤال، وإلا سيتم طردك تلقائياً.",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            pending_users[chat_id][user_id]['correct_answer'] = correct_answer
            
        elif user_data["wrong_attempts"] >= 2:
            logger.info(f"محاولة طرد المستخدم {user_id} من {chat_id} بعد {user_data['wrong_attempts']} محاولات خاطئة.")
            await context.bot.send_message(chat_id, f"❌ إجابات خاطئة متكررة. سيتم طردك. @{query.from_user.username or query.from_user.first_name}")
            try:
                await context.bot.unban_chat_member(chat_id, user_id) # Kicking is unbanning a restricted user who is currently restricted
                await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
                log_captcha_event(user_id, chat_id, 'kicked')
            except Exception as e:
                logger.error(f"خطأ في طرد المستخدم {user_id} من {chat_id} بعد الإجابات الخاطئة المتكررة: {e}")
            
            task_key = f"{chat_id}_{user_id}"
            if task_key in kick_tasks:
                kick_tasks[task_key].cancel()
                del kick_tasks[task_key]
            
            if chat_id in pending_users and user_id in pending_users[chat_id]:
                del pending_users[chat_id][user_id]

async def schedule_kick(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, message_id: int):
    """جدولة طرد المستخدم بعد فترة معينة"""
    await asyncio.sleep(1800)  # 30 دقيقة
    
    task_key = f"{chat_id}_{user_id}"
    if task_key in kick_tasks:
        del kick_tasks[task_key]

    if chat_id in pending_users and user_id in pending_users[chat_id]:
        try:
            await context.bot.unban_chat_member(chat_id, user_id) # Kicking is unbanning a restricted user who is currently restricted
            user_data = pending_users[chat_id][user_id]
            username = user_data['username']
            await context.bot.send_message(chat_id, f"⏰ انتهت مهلة الكابتشا. تم طرد المستخدم @{username} لعدم حل الكابتشا.")
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            log_captcha_event(user_id, chat_id, 'timeout')
            del pending_users[chat_id][user_id]
        except Exception as e:
            logger.error(f"خطأ في طرد المستخدم {user_id} من {chat_id} بعد انتهاء المهلة: {e}")

async def dev_commands_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة أوامر المطورين"""
    user = update.effective_user
    query = update.callback_query
    await query.answer()

    if user.id not in DEVELOPER_IDS:
        await query.edit_message_text("عذراً، هذه الأوامر متاحة للمطورين فقط.")
        return

    keyboard = [
        [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="bot_stats_show")],
        [InlineKeyboardButton("📢 إذاعة للمستخدمين", callback_data="broadcast_users_prompt")],
        [InlineKeyboardButton("📢 إذاعة للمجموعات", callback_data="broadcast_chats_all_prompt")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="start_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("⚙️ أوامر المطورين:", reply_markup=reply_markup)

async def admin_commands_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة أوامر المشرفين"""
    user = update.effective_user
    query = update.callback_query
    await query.answer()

    if not is_activating_admin(user.id) and user.id not in DEVELOPER_IDS:
        await query.edit_message_text("عذراً، هذه الأوامر متاحة للمشرفين الذين قاموا بتفعيل البوت في مجموعاتهم فقط.")
        return

    keyboard = [
        [InlineKeyboardButton("📊 إحصائيات مجموعتي", callback_data="admin_stats_show")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="start_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("🛠️ أوامر المشرفين:", reply_markup=reply_markup)

async def show_bot_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إحصائيات البوت العامة"""
    user = update.effective_user
    query = update.callback_query
    await query.answer()

    if user.id not in DEVELOPER_IDS:
        await query.edit_message_text("عذراً، هذه الإحصائيات متاحة للمطورين فقط.")
        return

    stats = get_bot_stats()
    text = f"📊 إحصائيات البوت العامة:\n\n"
    text += f"👥 إجمالي المستخدمين: {stats['total_users']}\n"
    text += f"🏘️ إجمالي المجموعات: {stats['total_chats']}\n"

    keyboard = [
        [InlineKeyboardButton("🔙 رجوع", callback_data="dev_commands_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إحصائيات المجموعة للمشرف"""
    user = update.effective_user
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id

    if not is_activating_admin(user.id) and user.id not in DEVELOPER_IDS:
        await query.edit_message_text("عذراً، هذه الإحصائيات متاحة للمشرفين الذين قاموا بتفعيل البوت في مجموعاتهم فقط.")
        return

    stats = get_stats(chat_id=chat_id)
    success_count = stats.get('success', 0)
    kicked_count = stats.get('kicked', 0)
    timeout_count = stats.get('timeout', 0)

    text = f"📊 إحصائيات الكابتشا لمجموعتك:\n\n"
    text += f"✅ نجاح التحقق: {success_count}\n"
    text += f"❌ طرد (إجابة خاطئة): {kicked_count}\n"
    text += f"⏰ طرد (انتهاء المهلة): {timeout_count}\n"

    keyboard = [
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_commands_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def broadcast_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, broadcast_type: str):
    """طلب رسالة الإذاعة من المطور"""
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()

    if user_id not in DEVELOPER_IDS:
        await query.edit_message_text("عذراً، هذا الأمر متاح للمطورين فقط.")
        return

    context.user_data['broadcast_type'] = broadcast_type
    await query.edit_message_text("الرجاء إرسال الرسالة التي تريد إذاعتها الآن.")

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج رسائل الإذاعة"""
    user_id = update.effective_user.id
    if user_id not in DEVELOPER_IDS:
        return

    if 'broadcast_type' not in context.user_data:
        logger.warning("handle_broadcast_message: broadcast_type not found in user_data")
        return

    broadcast_type = context.user_data.pop('broadcast_type')
    message_to_broadcast = update.message.text

    sent_count = 0
    if broadcast_type == 'users':
        targets = get_all_users()
        for target_id in targets:
            try:
                await context.bot.send_message(chat_id=target_id, text=message_to_broadcast)
                sent_count += 1
                await asyncio.sleep(0.1)  # لتجنب تجاوز حدود API
            except Exception as e:
                logger.warning(f"فشل إرسال رسالة إذاعية للمستخدم {target_id}: {e}")
        await update.message.reply_text(f"تم إرسال الرسالة الإذاعية إلى {sent_count} مستخدم.")

    elif broadcast_type == 'chats_all':
        targets = get_all_chats()
        logger.info(f"handle_broadcast_message: Found {len(targets)} targets for broadcast type '{broadcast_type}'")
        for target_id in targets:
            try:
                await context.bot.send_message(chat_id=target_id, text=message_to_broadcast)
                sent_count += 1
                await asyncio.sleep(0.1)  # لتجنب تجاوز حدود API
            except Exception as e:
                logger.warning(f"فشل إرسال رسالة إذاعية للمجموعة {target_id}: {e}")
        await update.message.reply_text(f"تم إرسال الرسالة الإذاعية إلى {sent_count} مجموعة.")

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الرسائل النصية"""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    
    logger.info(f"handle_text_message: Received text '{text}' in chat_id {chat_id}, chat_type {update.effective_chat.type}")

    # التحقق من أمر التفعيل
    if text == "تفعيل":
        await enable_protection(update, context)
    elif text == "إلغاء":
        await disable_protection(update, context)
    elif 'broadcast_type' in context.user_data and update.effective_user.id in DEVELOPER_IDS:
        await handle_broadcast_message(update, context)
def main() -> None:
    """تشغيل البوت"""
    init_database()



    application = Application.builder().token(BOT_TOKEN).build()

    # تحميل حالة الحماية من قاعدة البيانات MongoDB
    for chat in chats_collection.find({}, {"chat_id": 1, "protection_enabled": 1}):
        protection_enabled[chat["chat_id"]] = chat.get("protection_enabled", False)

    
    # Handlers for main menu buttons
    application.add_handler(CallbackQueryHandler(dev_commands_menu, pattern="^dev_commands_menu$"))
    application.add_handler(CallbackQueryHandler(admin_commands_menu, pattern="^admin_commands_menu$"))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(start_command, pattern="^start_menu$"))

    # Handlers for developer sub-menu
    application.add_handler(CallbackQueryHandler(show_bot_stats, pattern="^bot_stats_show$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: broadcast_prompt(u, c, 'users'), pattern="^broadcast_users_prompt$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: broadcast_prompt(u, c, 'chats_all'), pattern="^broadcast_chats_all_prompt$"))

    # Handlers for admin sub-menu
    application.add_handler(CallbackQueryHandler(show_admin_stats, pattern="^admin_stats_show$"))

    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler))
    application.add_handler(ChatMemberHandler(new_member_handler, ChatMemberHandler.CHAT_MEMBER))
    
    application.add_handler(CallbackQueryHandler(captcha_callback_handler, pattern="^captcha_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    print("🤖 بدء تشغيل بوت الحماية...")

    # إعداد الويب هوك
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    PORT = int(os.getenv("PORT", 10000)) # Render typically uses port 10000

    if not WEBHOOK_URL:
        raise ValueError("WEBHOOK_URL environment variable not set.")

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
        webhook_url=WEBHOOK_URL
    )




if __name__ == '__main__':
    # تشغيل init_database في بداية التطبيق
    main()


