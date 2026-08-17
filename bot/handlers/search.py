import logging 
from telegram import Update 
from telegram.ext import ContextTypes
from db import get_db, get_user
from db.crud import check_rate_limit, format_cooldown
from bot.worker import job_queue, BotTask
import json 
import hashlib


logger = logging.getLogger(__name__)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    logger.info(f"[SEARCH-BOT] Starting search..")
    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "• `/search <keyword>` (Full pipeline: crawl + score + tailor) - *1 per day*\n"
            "• `/search_quick <keyword>` (Crawl only, no tailoring) - *1 per hour*", 
            parse_mode="Markdown"
        )
        return 
    
    #Determine if it's quick or full
    is_quick = context.args[0].lower() == "--quick" or update.message.text.startswith("/search_quick")
    keyword = " ".join(context.args).replace("--quick", "").strip()
    command_type = "search_quick" if is_quick else "search_full"
    
    telegram_id = update.effective_user.id 
    
    #Fetch User Profile from DB 
    with get_db() as db:
        user = get_user(db, telegram_id)
        if not user:
            logger.error(f"[SEARCH-BOT] user {telegram_id} does not exist in the DB")
            await update.message.reply_text("⚠️ You haven't set up your profile yet. Please run `/start` first.", parse_mode = 'Markdown')
            return
        
        #Check Rate-Limits
        is_allowed, cooldown = check_rate_limit(db, telegram_id, command_type)
        if not is_allowed:
            await update.message.reply_text(
                f"⏳ **Rate Limit Reached**\n\n"
                f"Resets in: **{cooldown}**",
                parse_mode='Markdown'
            )
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
        "mode": "full" if not is_quick else "score", #Quick Mode stops after scoring
        "raw_jobs": [],
        "scored_jobs": [],
        "tailored_jobs": [],
        "final_report": "",
        "error": None
    }
    
    #Get the Queue Info
    queue_info = job_queue.get_info(telegram_id)
    
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
        await update.message.reply_text(
        f"✅ **Added to queue!**\n\n"
        f"🔍 Searching for: `{keyword}`\n"
        f"📊 Mode: `{'Quick (Crawl Only)' if is_quick else 'Full (Crawl + Score + Tailor)'}`\n"
        f"📍 Queue Position: **#{queue_info['position']}**\n"
        f"⏱️ Estimated Wait: **{queue_info['estimated_str']}**",
        parse_mode='Markdown'
    )
    else:
        await update.message.reply_text("⚠️ Failed to add task to queue. Please try again.", parse_mode = 'Markdown')
    
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
    
    
    