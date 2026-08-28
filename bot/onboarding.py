import logging 
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from db import get_db, save_user, get_user
from db.models import User 
import os 
import json
from resume.skill_extractor import extract_skills_from_resume

logger = logging.getLogger(__name__)

#Conversation States
(NAME, ROLES, SKILLS_CORE, SKILLS_PRIMARY, ADDITIONAL_QUESTIONS, RESUME, SKILLS_CONFIRM) = range(7)
# Hardcode all portals ON for MVP
DEFAULT_PORTALS = ["greenhouse", "lever", "ashby", "remotive", "himalayas", "remoteok", "hackernews"]


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """ 
        Starts the onboarding wizard.
    """
    telegram_id = update.effective_user.id 
    logger.info("[BOT-Onboarding] Start Command runnimg")
    
    #Check if user is already present
    with get_db() as db:
        existing_user = get_user(db, telegram_id)
        if existing_user:
            await update.message.reply_text("You already have a profile set up. Use `/profile` to view it, or `/search <keyword>` find jobs. \n\n To update your profile, use `/update` (coming soon)", parse_mode='Markdown')
            return ConversationHandler.END
    
    await update.message.reply_text("Hello, Welcome to JobBot! Let's setup your profile.\n\nWhat is  your **Name**?",parse_mode="Markdown")
    logger.info("[BOT-Onboarding] Start Command ended")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[BOT-Onboarding] Getting Name")
    context.user_data['name'] = update.message.text 
    await update.message.reply_text("Great! What are your **Target Roles**? (comma-separated, e.g., Backend Engineer, ML Engineer)", parse_mode='Markdown')
    logger.info("[BOT-Onboarding] Got Name")
    return ROLES 

async def get_roles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[BOT-Onboarding] Getting Roles")
    roles = [r.strip() for r in update.message.text.split(',')]
    context.user_data['target_roles'] = roles
    
    await update.message.reply_text("Now, let's add your skills. We categorize them to help our AI score jobs better.\n\nWhat are your **CORE** skills? (Your daily drivers, expert level. Comma-separated, e.g., Python, Go, FastAPI)", parse_mode='Markdown')
    return SKILLS_CORE

async def get_skills_core(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    core = [s.strip() for s in update.message.text.split(',')]
    context.user_data.setdefault('skills', {})['core'] = core
    await update.message.reply_text("Got it. What are your **PRIMARY** skills? (Strong proficiency, used frequently. Comma-separated, or type 'skip')", parse_mode='Markdown')
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
        "A few more question that will help me in building your profile for correct tailoring:"
        "reply with anything that applies, comma-separated (or reply `skip`):\n\n"
        "- Cloud platforms you've personally used (e.g. AWS, GCP, Azure)\n"
        "- Container/orchestration tools (e.g. Docker, Kubernetes)\n"
        "- CI/CD tools (e.g. GitHub Actions, Jenkins)\n"
        "- Databases/message queues (e.g. Kafka, RabbitMQ)\n\n"
        "Only list things you've actually used yourself - we'll never add anything you don't type here.",
        parse_mode='Markdown'
    )
    return ADDITIONAL_QUESTIONS

async def get_enrichment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    
    if text.lower() not in ('skip', 'none', 'no'):
        additions = [s.strip() for s in text.split(',') if s.strip()]
        existing = {s.lower() for s in context.user_data['skills']['secondary']}
        
        for item in additions:
            if item.lower() not in existing:
                context.user_data['skills']['secondary'].append(item)
                existing.add(item.lower())
    
    await update.message.reply_text("Finally, please upload your base-resume.tex file.\n Send it as a document")
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
    context.user_data["resume_path"] = resume_path
    
    #------Grounded Skill Extraction from the FULL resume text---------
    #Recovers tools mentioned in full-resume
    await update.message.reply_text("Scanning your resume for skills mentioned outside thte SKILLS section")
    
    with open(resume_path, "r", encoding = "utf-8") as f:
        tex_content = f.read()
    
    declared_skills = {s.lower() for tier in context.user_data["skills"].values() for s in tier}
    extracted = await extract_skills_from_resume(tex_content)
    
    #only surface NEW candidate the User hasn't already declared
    new_additions = [s for s in extracted if s.lower() not in declared_skills]
    
    #If no new additions then continue with finalizing setup of profile
    if not new_additions:
        return await _finalize_profile(update, context)
    
    context.user_data["candidate_skills"] = new_additions
    
    listing = "\n".join(f"{i+1}. {s}" for i, s in enumerate(new_additions))
    await update.message.reply_text("We found these additional skills mentioned in your resume (Experience/Projects sections):\n\n"
                                    f"{listing}\n\n"
                                    "Reply with the numbers of any that are WRONG or you don't want included "
                                    "(comma-separated, eg: 2,7), or reply 'confirm' to accept the full list.",parse_mode='Markdown')
    return SKILLS_CONFIRM
    
async def confirm_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().lower()
    candidates = context.user_data.get('candidate_skills', [])
    
    if text != 'confirm':
        try:
            remove_indices = {int(n.strip()) - 1 for n in text.split(',') if n.strip()}
            candidates = [s for i, s in enumerate(candidates) if i not in remove_indices]
        except ValueError:
            await update.message.reply_text(
                "Couldn't parse that. Reply with comma-separated numbers to remove(eg. 2,7), or `confirm` to accept all."
            )
            return SKILLS_CONFIRM
        
    #The confirmed resume-extracted skills go into the secondary tier
    context.user_data['skills'].setdefault('secondary', [])
    context.user_data['skills']['secondary'].extend(candidates)
    
    return await  _finalize_profile(update, context)
    
async def _finalize_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    telegram_id = update.effective_user.id
    with get_db() as db:
        save_user(db, telegram_id, context.user_data['name'], context.user_data['target_roles'], context.user_data['skills'], context.user_data['resume_path'])
        
    await update.message.reply_text("**Profile Saved!**\n\n"
                                    "You can now use `/search command` to find and tailor jobs. \n"
                                    "Use `/profile` to view your current settings",
                                    parse_mode = 'Markdown')
    
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
        ADDITIONAL_QUESTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_enrichment)],
        RESUME: [MessageHandler(filters.Document.ALL, get_resume)],
        SKILLS_CONFIRM : [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_skills)]
    },
    fallbacks = [CommandHandler("cancel", cancel)]
)