import asyncio 
import logging 
from telegram import Update 
import uuid
from telegram.ext import ContextTypes
from db import get_db
from db.crud import get_user, save_job, get_job_by_url
from db.models import User 
from agent.orchestrator import pipeline
from portals.base import JobListing
from agent.nodes.scorer import ScoredJob
from utils.jd_extractor import process_job_input
from bot.worker import job_queue, BotTask

logger = logging.getLogger(__name__)

      
async def _prepare_manual_job(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str):
    """ 
        Helper function to avoid duplicate code
    """   
    telegram_id = update.effective_user.id
    
    with get_db() as db:
        user = get_user(db, telegram_id)
        if not user:
            await update.message.reply_text("couldn't find you in my records. Please run `/start` first.")
            return None, None, None
        
        profile = {
            "name": user.username,
            "target_roles": user.target_roles,
            "skills":   user.skills,
            "experience_years": 1,
            "location": "Remote"
        }
            
    await update.message.reply_text("Analyzing input....")
    title, company, jd_text, error = await process_job_input(user_input)
    
    if error:
        await update.message.reply_text(error, parse_mode='Markdown')
        return None, None, None 
    
    #Having a unique url is important so we replicate this by using UUID
    unique_url = f"manual_{uuid.uuid4().hex[:8]}"
    
    dummy_job = JobListing(title = title, company = company, url = unique_url, portal = "manual", portal_job_id="manual_01")
    
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
    
    user_input = " ".join(context.args)
    telegram_id = update.effective_user.id
    user, profile, dummy_job, _ = await _prepare_manual_job(update, context, user_input)
    
    if not dummy_job:
        logger.info(f"[SCORE CMD] failed due to no dummy_job")
        return 
    
    #----------Prepare the State for 'score' node---------------------
    initial_state = {
        "user_id": telegram_id,
        "keyword": dummy_job.title,
        "profile": profile,
        "portals": "config/portals.yml",
        "base_tex_path": user.resume_path,
        "mode": "score",
        "raw_jobs": [dummy_job],
        "scored_jobs": [],
        "tailored_jobs": [],
        "final_report": "",
        "error": None
    }
    
    #----Acknowledge Immediately------------
    await update.message.reply_text("**Job received!**\n\n I've added this to my background queue.I will message you here with scored report and tailored PDF as soon as it's ready(usually 30-60 seconds).")
    
    #------ Create and Push the Task to the Worker Queue----------
    task = BotTask(chat_id = update.effective_chat.id, user_id = telegram_id,
                   task_type= "scoring", initial_state = initial_state, bot = context.bot)
    
    await job_queue.put(task)
    

    
async def tailor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ 
        Command to allow user to send /tailor <url or text>
        Starts pipeline at Tailor Node
    """
    if not context.args:
        await update.message.reply_text("Usage: `/tailor <job_url>` or `/tailor <paste job description>`", parse_mode = 'Markdown')
        return 
    
    user_input = " ".join(context.args)
    telegram_id = update.effective_user.id 
    
    user, profile, dummy_job, unique_url = await _prepare_manual_job(update, context, user_input)
    
    if not dummy_job:
        return 
    
    #-------Get the real DB ID for the job----------
    with get_db() as db:
        db_job = get_job_by_url(db, telegram_id, unique_url) 
        job_id = db_job.id if db_job else None 
        
    dummy_scored = ScoredJob(job = dummy_job, db_job_id = job_id, score = "A", match_percentage = 100, strengths = ["Direct Input"], gaps = [], recommendation = "Tailor immediately")
    
    initial_state = {
        "user_id": telegram_id,
        "keyword": dummy_job.title, 
        "profile": profile,
        "portals": "config/portals.yml",
        "base_tex_path": user.resume_path,
        "mode": "tailor",
        "raw_jobs": [dummy_job],
        "scored_jobs": [dummy_scored],
        "tailored_jobs": [],
        "final_report": "",
        "error":  None
    }
    
    #------Acknowledge the request immediately---------
    await update.message.reply_text("🪄 **Tailoring request received!**\n\n Added to task-queue. I will send your custom PDF here shortly.")
    
    #-------create and push the task to the Worker-Queue
    task = BotTask(
        chat_id = update.effective_chat.id,
        user_id = telegram_id,
        task_type = "tailoring",
        initial_state = initial_state,
        bot = context.bot,
    )
    await job_queue.put(task)