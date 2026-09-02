import asyncio 
import logging 
import hashlib
from telegram import Update 
import uuid
from telegram.ext import ContextTypes
from db import get_db
from db.crud import get_user, save_job, get_job_by_url, check_rate_limit
from db.models import User 
from agent.orchestrator import pipeline
from portals.base import JobListing
from agent.nodes.scorer import ScoredJob
from utils.jd_extractor import process_job_input
from bot.worker import job_queue, BotTask

logger = logging.getLogger(__name__)

      
async def _prepare_manual_job(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str, command_type: str):
    """ 
        Helper function to avoid duplicate code
    """   
    telegram_id = update.effective_user.id
    
    with get_db() as db:
        #Check rate-limits for USER
        is_allowed, cooldown = check_rate_limit(db, telegram_id, command_type)
        if not is_allowed:
            await update.message.reply_text(
                f"⏳ **Rate Limit Reached**\n\n"
                f"You've hit the limit for this one — resets in **{cooldown}**.",
                parse_mode='Markdown'
            )
            return None, None, None, None
        
        user = get_user(db, telegram_id)
        
        #If User not present in DB
        if not user:
            await update.message.reply_text(" ⚠️ Couldn't find you in my records. Please run `/start` first.", parse_mode = 'Markdown')
            return None, None, None,None
        
        
        #Build the user-profile dict
        profile = {
            "name": user.username,
            "target_roles": user.target_roles,
            "skills":   user.skills,
            "experience_years": 1,
            "location": "Remote"
        }
           
            
    await update.message.reply_text("🔍 Got it, taking a look...", parse_mode = 'Markdown')
    title, company, jd_text, error = await process_job_input(user_input)
    
    if error:
        await update.message.reply_text(error, parse_mode='Markdown')
        return None, None, None, None
    
    
    #Having a unique url is important so we replicate this by using UUID
    unique_url = f"manual_{uuid.uuid4().hex[:8]}"
    dummy_job = JobListing(title = title, company = company, url = unique_url, portal = "manual", jd_text = jd_text, portal_job_id="manual_01")
    
    #Save the job to DB so it gets an integer ID for the tailor node
    with get_db() as db:
        db_job = save_job(db, telegram_id, title, company, unique_url, "manual", jd_text)
        dummy_job.portal_job_id = str(db_job.id)  
        
    #We return unique_url so that tailor can get the Job from db correctly
    return user, profile, dummy_job, unique_url

        

async def score_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ 
        Command to allow user to send /score <url or text>
        Starts the pipeline at Scorer Node
    """
    if not context.args:
        await update.message.reply_text("Usage: `/score <job_url>` or `/score <paste job description>`", parse_mode = 'Markdown')
        return 
    
    #Use the raw message text (minus the leading command token) instead of
    #re-joining context.args - Telegram tokenizes args by whitespace (newlines
    #included), so " ".join(context.args) permanently flattens every line break
    #before the title-extraction heuristic (which relies on real newlines to
    #find "the first line") ever sees the text.
    user_input = update.message.text.split(None, 1)[1] if len(update.message.text.split(None, 1)) > 1 else ""
    telegram_id = update.effective_user.id
    
    user, profile, dummy_job, unique_url = await _prepare_manual_job(update, context, user_input, "score")
    
    #IF Job couldn't be created by manual_job_creation function
    if not dummy_job:
        logger.info(f"[SCORE CMD] failed due to no dummy_job")
        return 
    
    #----------Prepare the State for 'score' node---------------------
    initial_state = {
        "user_id": telegram_id,
        "keyword": dummy_job.title,
        "profile": profile,
        "portals": [],
        "base_tex_path": user.resume_path,
        "mode": "score",
        "raw_jobs": [dummy_job],
        "scored_jobs": [],
        "tailored_jobs": [],
        "final_report": "",
        "error": None
    }

    #Build the Dedup key from UserID + JOBURL
    dedup_key = hashlib.md5(f"{telegram_id}:scoring:{unique_url}".encode()).hexdigest()
    
    #-----Check for duplicate------------
    if job_queue.is_duplicate(dedup_key):
        await update.message.reply_text(f"⚠️ You already have a scoring task in progress.\n\nUse `/cancel` to stop it, or wait for it to finish.", parse_mode = 'Markdown')
        return 
    
    #------ Create the Task for the Worker Queue----------
    task = BotTask(chat_id = update.effective_chat.id, user_id = telegram_id,
                   task_type= "scoring", initial_state = initial_state, bot = context.bot, dedup_key= dedup_key)
    
    #Get the queue info
    queue_info = job_queue.get_info(telegram_id)

    added = await job_queue.put(task)
    
    if added:
        await update.message.reply_text(
            f"✅ **Added to queue!**\n\n"
            f"⚖️ Scoring: `{dummy_job.title}`\n"
            f"📍 Queue Position: **#{queue_info['position']}**\n"
            f"⏱️ Estimated Wait: **{queue_info['estimated_str']}**",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("⚠️ Failed to add task to queue. Please try again.", parse_mode = 'Markdown')
    

    
async def tailor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ 
        Command to allow user to send /tailor <url or text>
        Starts pipeline at Tailor Node
    """
    if not context.args:
        await update.message.reply_text("Usage: `/tailor <job_url>` or `/tailor <paste job description>`", parse_mode = 'Markdown')
        return 
    
    #Use the raw message text (minus the leading command token) instead of
    #re-joining context.args - Telegram tokenizes args by whitespace (newlines
    #included), so " ".join(context.args) permanently flattens every line break
    #before the title-extraction heuristic (which relies on real newlines to
    #find "the first line") ever sees the text.
    user_input = update.message.text.split(None, 1)[1] if len(update.message.text.split(None, 1)) > 1 else ""
    telegram_id = update.effective_user.id 
    
    user, profile, dummy_job, unique_url = await _prepare_manual_job(update, context, user_input, "tailor")
    
    if not dummy_job:
        return 
    
    #-------Get the real DB ID for the job----------
    with get_db() as db:
        db_job = get_job_by_url(db, telegram_id, unique_url) 
        job_id = db_job.id if db_job else None 
        
    dummy_scored = ScoredJob(job = dummy_job, db_job_id = job_id, score = "A", match_percentage = 100, strengths = [], gaps = [], recommendation = "Tailor immediately")
    
    initial_state = {
        "user_id": telegram_id,
        "keyword": dummy_job.title, 
        "profile": profile,
        "portals": [],
        "base_tex_path": user.resume_path,
        "mode": "tailor",
        "raw_jobs": [dummy_job],
        "scored_jobs": [dummy_scored],
        "tailored_jobs": [],
        "final_report": "",
        "error":  None
    }
    
    #Build the DEDUP_KEY
    dedup_key = hashlib.md5(f"{telegram_id}:tailoring:{unique_url}".encode()).hexdigest()
    
    if job_queue.is_duplicate(dedup_key):
        await update.message.reply_text(f"⚠️ You already have a tailoring task in progress.\n\nUse `/cancel` to stop it, or wait for it to finish.", parse_mode = 'Markdown')
        return
    
    #-------create and push the task to the Worker-Queue
    task = BotTask(
        chat_id = update.effective_chat.id,
        user_id = telegram_id,
        task_type = "tailoring",
        initial_state = initial_state,
        bot = context.bot,
        dedup_key = dedup_key
    )
    
    #-------Get the Queue Info-------
    queue_info = job_queue.get_info(telegram_id)
    
    #-----Addded---------
    added = await job_queue.put(task)
    
    if added:
        await update.message.reply_text(
            f"✅ **Added to queue!**\n\n"
            f"✂️ Tailoring: `{dummy_job.title}`\n"
            f"📍 Queue Position: **#{queue_info['position']}**\n"
            f"⏱️ Estimated Wait: **{queue_info['estimated_str']}**",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("⚠️ Failed to add task to queue. Please try again.", parse_mode = 'Markdown')