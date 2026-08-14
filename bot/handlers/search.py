import logging 
from telegram import Update 
from telegram.ext import ContextTypes
from db import get_db, get_user
from bot.worker import job_queue, BotTask
import json 
import hashlib


logger = logging.getLogger(__name__)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    logger.info(f"[SEARCH-BOT] Starting search..")
    if not context.args:
        await update.message.reply_text("Usage: `/search <keyword>` (e.g. `/search Python ML Engineer`)", parse_mode = "Markdown")
        return 
    
    keyword = " ".join(context.args)
    telegram_id = update.effective_user.id 
    
    #Fetch User Profile from DB 
    with get_db() as db:
        user = get_user(db, telegram_id)
        if not user:
            logger.error(f"[SEARCH-BOT] user {telegram_id} does not exist in the DB")
            await update.message.reply_text("⚠️ You haven't set up your profile yet. Please run `/start` first.")
            return
        
    #Build profile dict directly from the DB JSON columns
    profile = {
        "name": user.username,
        "target_roles": user.target_roles,
        "skills": user.skills,
        "experience_years": 1,
        "location": "Remote"
    }
    
    #Prepare initial-state for LangGraph
    initial_state = {
        "user_id": telegram_id,
        "keyword": keyword,
        "location": profile.get("location"),
        "portals": "config/portals.yml",
        "profile": profile,
        "base_tex_path": user.resume_path,
        "mode": "full",
        "raw_jobs": [],
        "scored_jobs": [],
        "tailored_jobs": [],
        "final_report": "",
        "error": None
    }
    
    #Build DEDUP_KEY based on user + keyword(for search, keyword is the unique identifier)
    dedup_key = hashlib.md5(f"{telegram_id}:search:{keyword}".encode()).hexdigest()
    
    if job_queue.is_duplicate(dedup_key):
        await update.message.reply_text(
            f"⚠️ You already have a search for **'{keyword}'** in progress.\n\n"
            "Use `/cancel` to stop it, or wait for it to finish.",
            parse_mode='Markdown'
        )
        return
    
    #Create the Task for the Worker Queue
    task = BotTask(
        chat_id = update.effective_chat.id,
        user_id = telegram_id,
        task_type = "search",
        initial_state = initial_state,
        bot = context.bot,
        dedup_key=dedup_key
    )
    
    #Add the task to queue
    added = await job_queue.put(task)
    
    if added:
        await update.message.reply_text(f"🔍 **Search received for '{keyword}'!**\n\nI've added this to my background queue. I will message you here with scored jobs and tailored PDFs as soon as it's ready (usually 1-2 minutes).", parse_mode='Markdown')
    else:
        await update.message.reply_text("⚠️ Failed to add task to queue. Please try again.")
    
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
      await update.message.reply_text(
        "🤖 **JobBot Commands**\n\n"
        "/start - Set up your profile\n"
        "/search `<keyword>` - Find and tailor jobs\n"
        "/score `<url_or_text>` - Score a specific JD\n"
        "/tailor `<url_or_text>` - Tailor resume for a specific JD\n"
        "/cancel - Cancel your running tasks\n"
        "/help - Show this message",
        parse_mode='Markdown'
    )    
    
    
    