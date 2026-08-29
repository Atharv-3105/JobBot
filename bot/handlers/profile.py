import logging
import os
from telegram import Update
from telegram.ext import CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from db import get_db, get_user, save_user

logger = logging.getLogger(__name__)

#Conversation states for the edit flow
(CHOOSE_FIELD, EDIT_NAME, EDIT_ROLES, EDIT_CORE, EDIT_PRIMARY, EDIT_OTHER_SKILLS, EDIT_RESUME) = range(7)

FIELD_STATE_MAP = {
    "1": EDIT_NAME,
    "2": EDIT_ROLES,
    "3": EDIT_CORE,
    "4": EDIT_PRIMARY,
    "5": EDIT_OTHER_SKILLS,
    "6": EDIT_RESUME,
}

FIELD_PROMPTS = {
    EDIT_NAME: "What's your new **name**?",
    EDIT_ROLES: "What are your new **target roles**? (comma-separated)",
    EDIT_CORE: "What are your new **core** skills? (comma-separated, your daily drivers)",
    EDIT_PRIMARY: "What are your new **primary** skills? (comma-separated, or reply `skip` for none)",
    EDIT_OTHER_SKILLS: (
        "List your **secondary/basic** skills, comma-separated (or reply `skip` for none). "
        "This replaces your current secondary + basic skills."
    ),
    EDIT_RESUME: "Upload your updated resume as a `.tex` file (send it as a document).",
}

EDIT_MENU_TEXT = (
    "What would you like to update?\n\n"
    "1. Name\n"
    "2. Target roles\n"
    "3. Core skills\n"
    "4. Primary skills\n"
    "5. Other skills (secondary/basic)\n"
    "6. Resume file\n\n"
    "Reply with a number, or `cancel` to stop."
)


def _format_profile(user) -> str:
    skills = user.skills or {}

    def _fmt(tier: str) -> str:
        items = skills.get(tier, [])
        return ", ".join(items) if items else "_none_"

    report = "👤 **Your Profile**\n"
    report += "━━━━━━━━━━━━━━━━━━━\n"
    report += f"**Name:** {user.username or '_not set_'}\n"
    report += f"**Target Roles:** {', '.join(user.target_roles) if user.target_roles else '_none_'}\n\n"
    report += "**Skills**\n"
    report += f"• Core: {_fmt('core')}\n"
    report += f"• Primary: {_fmt('primary')}\n"
    report += f"• Secondary: {_fmt('secondary')}\n"
    report += f"• Basic: {_fmt('basic')}\n\n"
    report += f"**Resume:** `{os.path.basename(user.resume_path)}`\n"
    if user.onboarded_at:
        report += f"**Member since:** {user.onboarded_at.strftime('%b %d, %Y')}\n"
    report += "\nUse `/profile edit` to update any of this."
    return report


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
        /profile - view your profile
        /profile edit - update your profile (name/roles/skills/resume)
    """
    telegram_id = update.effective_user.id

    with get_db() as db:
        user = get_user(db, telegram_id)
        if not user:
            await update.message.reply_text(
                "You haven't set up your profile yet. Run `/start` first.", parse_mode='Markdown'
            )
            return ConversationHandler.END

        if context.args and context.args[0].lower() == "edit":
            await update.message.reply_text(EDIT_MENU_TEXT, parse_mode='Markdown')
            return CHOOSE_FIELD

        await update.message.reply_text(_format_profile(user), parse_mode='Markdown')

    return ConversationHandler.END


async def choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip()
    if choice.lower() == 'cancel':
        await update.message.reply_text("No changes made.")
        return ConversationHandler.END

    state = FIELD_STATE_MAP.get(choice)
    if state is None:
        await update.message.reply_text("Please reply with a number 1-6, or `cancel`.", parse_mode='Markdown')
        return CHOOSE_FIELD

    await update.message.reply_text(FIELD_PROMPTS[state], parse_mode='Markdown')
    return state


async def _apply_update(update: Update, name: str = None, target_roles: list = None, skill_tier: tuple = None) -> None:
    """ Applies a single-field update, reusing save_user's create-or-update semantics. """
    telegram_id = update.effective_user.id
    with get_db() as db:
        user = get_user(db, telegram_id)
        new_name = name if name is not None else user.username
        new_roles = target_roles if target_roles is not None else user.target_roles
        new_skills = dict(user.skills or {})
        if skill_tier:
            tier_name, values = skill_tier
            new_skills[tier_name] = values
        save_user(db, telegram_id, new_name, new_roles, new_skills, user.resume_path)


async def _confirm_and_end(update: Update) -> int:
    await update.message.reply_text("✅ Profile updated! Use `/profile` to view your changes.", parse_mode='Markdown')
    return ConversationHandler.END


async def edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _apply_update(update, name=update.message.text.strip())
    return await _confirm_and_end(update)


async def edit_roles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    roles = [r.strip() for r in update.message.text.split(',') if r.strip()]
    await _apply_update(update, target_roles=roles)
    return await _confirm_and_end(update)


async def edit_core(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    core = [s.strip() for s in update.message.text.split(',') if s.strip()]
    await _apply_update(update, skill_tier=('core', core))
    return await _confirm_and_end(update)


async def edit_primary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    primary = [] if text.lower() == 'skip' else [s.strip() for s in text.split(',') if s.strip()]
    await _apply_update(update, skill_tier=('primary', primary))
    return await _confirm_and_end(update)


async def edit_other_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    others = [] if text.lower() == 'skip' else [s.strip() for s in text.split(',') if s.strip()]

    telegram_id = update.effective_user.id
    with get_db() as db:
        user = get_user(db, telegram_id)
        new_skills = dict(user.skills or {})
        new_skills['secondary'] = others
        new_skills['basic'] = []
        save_user(db, telegram_id, user.username, user.target_roles, new_skills, user.resume_path)

    return await _confirm_and_end(update)


async def edit_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.document or not update.message.document.file_name.endswith('.tex'):
        await update.message.reply_text(
            "That doesn't look like a `.tex` file — please upload your LaTeX resume as a document.",
            parse_mode='Markdown'
        )
        return EDIT_RESUME

    telegram_id = update.effective_user.id
    user_dir = f"data/users/{telegram_id}"
    os.makedirs(user_dir, exist_ok=True)
    resume_path = os.path.join(user_dir, "base_resume.tex")
    file = await update.message.document.get_file()
    await file.download_to_drive(resume_path)

    with get_db() as db:
        user = get_user(db, telegram_id)
        save_user(db, telegram_id, user.username, user.target_roles, user.skills, resume_path)

    return await _confirm_and_end(update)


async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("No changes made.")
    return ConversationHandler.END


#Export the ConversationHandler to be added to the main bot
profile_handler = ConversationHandler(
    entry_points=[CommandHandler("profile", profile_command)],
    states={
        CHOOSE_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_field)],
        EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_name)],
        EDIT_ROLES: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_roles)],
        EDIT_CORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_core)],
        EDIT_PRIMARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_primary)],
        EDIT_OTHER_SKILLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_other_skills)],
        EDIT_RESUME: [MessageHandler(filters.Document.ALL, edit_resume)],
    },
    fallbacks=[CommandHandler("cancel", cancel_edit)],
)
