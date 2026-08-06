import logging 
import asyncio
import json
from telegram import Update 
from telegram.ext import ContextTypes
from db import get_db
from db.models import User
from agent.orchestrator import pipeline 


logger = logging.getLogger(__name__)

async def run_pipeline_background(update: Update, keyword: str, telegram_id: int, profile_path: str, resume_path: str):
    """ 
        Wrapper function to run the pipeline and send results when done.
    """
    
    try:
        #Load profile from the JSON file
        with open(profile_path, "r") as f:
            profile = json.load(f)
            
        initial_state = {
            "user_id": telegram_id,
            "keyword": keyword,
            "location": profile.get("location"),
            "portals": "config/portals.yml",
            "profile": profile,
            "base_tex_path": resume_path,
            "raw_jobs": [],
            "scored_jobs": [],
            "tailored_jobs": [],
            "final_report": "",
            "error": None
        }
        
        
        #Invoke Pipeline(This will take 1-2 mins and run in the background)
        final_state = await pipeline.ainvoke(initial_state)
        
        #Send final report
        await update.message.reply_text(final_state["final_report"], parse_mode = "MarkdownB")
        
        #Send PDFs
        if final_state.get("tailored_jobs"):
            for job in final_state["tailored_jobs"]:
                pdf_path = job["pdf_path"]
                caption = f"**{job['title']}** at {job['company']} (Score: {job['score']})"
                
                
                with open(pdf_path, "rb") as pdf_file:
                    await update.message.reply_document(
                        document = pdf_file,
                        filename = f"{job['company']}_resume.pdf",
                        caption = caption,
                        parse_mode = 'Markdown'
                    )
                    
    except Exception as e:
        logger.error(f"Background pipeline failed: {e}")
        await update.message.reply_text(f"Pipeline failed: {str(e)}")
                    
            
            

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    logger.info(f"[SEARCH-BOT] Starting search..")
    if not context.args:
        await update.message.reply_text("Usage: `/search <keyword>` (e.g. `/search Python ML Engineer`)", parse_mode = "Markdown")
        return 
    
    keyword = " ".join(context.args)
    telegram_id = update.effective_user.id 
    
    #Fetch User Profile from DB 
    with get_db() as db:
        #Query the User table
        user = db.query(User).filter(User.user_id == telegram_id).first()
        if not user:
            logger.error(f"[SEARCH-BOT] user {telegram_id} does not exist in the DB")
            await update.message.reply_text("You haven't set up your profile yet. Please run `/start` first.")
            return 
        
    #Acknowledge the request immediately
    await update.message.reply_text(f"Searching for '{keyword}'... This takes ~1-2 minutes, I will message you when its done.")
    
    #Fire the pipeline in the background
    asyncio.create_task(run_pipeline_background(update, keyword, telegram_id, user.profile_path, user.resume_path))
        


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/start - Setup your profile\n"
        "/search <keyword> - Find and tailor Jobs\n"
        "/cancel - Cancel current operation" 
    )       
    
    
    