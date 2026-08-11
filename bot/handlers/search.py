import logging 
from telegram import Update 
from telegram.ext import ContextTypes
from db import get_db, get_user
from bot.worker import job_queue, BotTask
import json 


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
            await update.message.reply_text("You haven't set up your profile yet. Please run `/start` first.")
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
    
    #Acknowledge immediately and push to the task-Queue
    await update.message.reply_text(f"🔍 **Search received for '{keyword}'!**\n\n""I've added this to my background queue. I will message you here with scored jobs and tailored PDFs as soon as it's ready (usually 1-2 minutes).",parse_mode='Markdown')
    
    #Create and push the Task to the Worker Queue
    task = BotTask(
        chat_id = update.effective_chat.id,
        user_id = telegram_id,
        task_type = "search",
        initial_state = initial_state,
        bot = context.bot
    )
    
    await job_queue.put(task)
    
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/start - Setup your profile\n"
        "/search <keyword> - Find and tailor Jobs\n"
        "/score <url_or_text> - Score a specific JD\n"
        "/tailor <url_or_text> - Tailor resume for a specific JD\n"
        "/cancel - Cancel current operation"
    )       
    
    
    