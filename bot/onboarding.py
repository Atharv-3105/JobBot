import logging 
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from db import get_db, save_user
from db.models import User 
import os 
import json

logger = logging.getLogger(__name__)

#Conversation States
(NAME, ROLES, SKILLS_CORE, SKILLS_PRIMARY, RESUME) = range(5)
# Hardcode all portals ON for MVP
DEFAULT_PORTALS = ["greenhouse", "lever", "ashby", "remotive", "himalayas", "remoteok", "hackernews"]


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """ 
        Starts the onboarding wizard.
    """
    logger.info("[BOT-Onboarding] Start Command runnimg")
    await update.message.reply_text(
        "Hello, Welcom to JobBot! Let's setup your profile. \n\n"
        "What is  your **Name**?",
        parse_mode="Markdown"
    )
    logger.info("[BOT-Onboarding] Start Command ended")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[BOT-Onboarding] Getting Name")
    context.user_data['name'] = update.message.text 
    await update.message.reply_text("Great! What are your **Target Roles**? (comma-separated, e.g., Backend Engineer, ML Engineer)")
    logger.info("[BOT-Onboarding] Got Name")
    return ROLES 

async def get_roles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[BOT-Onboarding] Getting Roles")
    roles = [r.strip() for r in update.message.text.split(',')]
    context.user_data['target_roles'] = roles
    await update.message.reply_text(
        "Now, let's add your skills. We categorize them to help our AI score jobs better.\n\n"
        "What are your **CORE** skills? (Your daily drivers, expert level. Comma-separated, e.g., Python, Go, FastAPI)"
    )
    return SKILLS_CORE

async def get_skills_core(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    core = [s.strip() for s in update.message.text.split(',')]
    context.user_data.setdefault('skills', {})['core'] = core
    await update.message.reply_text(
        "Got it. What are your **PRIMARY** skills? (Strong proficiency, used frequently. Comma-separated, or type 'skip')"
    )
    return SKILLS_PRIMARY


async def get_skills_primary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().lower()
    if text == 'skip':
        context.user_data['skills']['primary'] = []
    else:
        context.user_data['skills']['primary'] = [s.strip() for s in update.message.text.split(',')]
    
    #Default secondary and basic to empty list for faster onboarding
    context.user_data['skills']['secondary'] = []
    context.user_data['skills']['basic'] = []
    
    await update.message.reply_text(
        "Finally, please **upload your base_resume.tex file**.\n"
        "Send it as a document."
    )
    return RESUME


async def get_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    
    logger.info("[BOT-Onboarding] Onboarding User")
    #Check if the updated file is .tex or not 
    if not update.message.document or not update.message.document.file_name.endswith('.tex'):
        await update.message.reply_text("That's not a .tex file, Please upload your LaTeX resume.")
        return RESUME 
    
    telegram_id = update.effective_user.id
    user_dir = f"data/users/{telegram_id}"
    os.makedirs(user_dir, exist_ok=True)
    
    #Download the resume file
    resume_path = os.path.join(user_dir, "base_resume.tex")
    file = await update.message.document.get_file()
    await file.download_to_drive(resume_path)
    
    #Save the User to DB
    with get_db() as db:
        save_user(db, telegram_id, context.user_data['name'], context.user_data['target_roles'], context.user_data['skills'], resume_path)
        
        
    await update.message.reply_text(
        "**Profile Saved!**\n\n"
        "You can now use '/search <keyword> to find and tailor jobs. \n"
        "Use `/profile` to view your current settings.",
        parse_mode='Markdown'
    )

    return ConversationHandler.END 


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Onboarding Cancelled..")
    return ConversationHandler.END 

#Export the Conversation Handler to be added to the main bot
onboarding_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start_command)],
    states = {
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
        ROLES: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_roles)],
        SKILLS_CORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_skills_core)],
        SKILLS_PRIMARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_skills_primary)],
        RESUME: [MessageHandler(filters.Document.ALL, get_resume)],
    },
    fallbacks = [CommandHandler("cancel", cancel)]
)