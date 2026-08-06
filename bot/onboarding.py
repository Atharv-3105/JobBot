import logging 
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from db import get_db 
from db.models import User 
import os 
import json 


logger = logging.getLogger(__name__)

#Conversation States
(NAME, ROLES, SKILLS, LOCATION, RESUME) = range(5)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """ 
        Starts the onboarding wizard.
    """
    logger.info("[BOT-Onboarding] Start Command runnimg")
    await update.message.reply_text(
        "Hello, Welcom to JobBot! Let's setup your profile. \n\n"
        "What is  your **Name**?"
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
    await update.message.reply_text("Awesome, What are your **Core Skills**? (comma-separated, e.g. Python, TypeScript, Go)")
    logger.info("[BOT-Onboarding] Got Roles")
    return SKILLS 

async def get_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[BOT-Onboarding] Getting Skills")
    skills = [s.strip() for s in update.message.text.split(',')]
    #Store as a simple dict structures matching our profile.yml format
    context.user_data["skills"] = {"core": skills, "primary": [], "secondary": [], "basic": []}
    await update.message.reply_text("Got it, What is your preferred **Location**? (e.g. Remote, Bengaluru, US)")
    logger.info("[BOT-Onboarding] Got Skills")
    return LOCATION 


async def get_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[BOT-Onboarding] Getting Location")
    context.user_data['location'] = update.message.text 
    await update.message.reply_text(
        "Finally, please **upload your base_resume.tex file**. \n"
        "Send it as a document, Ensure it is name '**base_resume.tex**'!!"
    ) 
    logger.info("[BOT-Onboarding] Got Location")
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
    
    resume_path = os.path.join(user_dir, "base_resume.tex")
    
    #Download the resume file
    file = await update.message.document.get_file()
    await file.download_to_drive(resume_path)
    
    #Save the User to DB
    with get_db() as db:
        user = User(
            user_id = telegram_id,
            username = context.user_data['name'],
            target_roles = context.user_data['target_roles'],
            skills = context.user_data['skills'],
            resume_path = resume_path
        )
        
        #Merge updates if exists, inserts if new
        db.merge(user)
        db.commit()
        
    await update.message.reply_text(
        "**Profile Saved!**\n\n"
        "You can now use '/search <keyword> to find and tailor jobs. \n"
        "Use `/profile` to view your current settings."
    )
    
    logger.info("[BOT-Onboarding] Onboarded User")
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
        SKILLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_skills)],
        LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_location)],
        RESUME: [MessageHandler(filters.Document.ALL, get_resume)],
    },
    fallbacks = [CommandHandler("cancel", cancel)]
)