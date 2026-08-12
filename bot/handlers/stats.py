import logging 
from telegram import Update 
from telegram.ext import ContextTypes 
from db import get_db 
from db.crud import get_user, get_user_stats, get_recent_jobs


logger = logging.getLogger(__name__)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id 
    
    with get_db() as db:
        user = get_user(db, telegram_id)
        if not user:
            await update.message.reply_text("⚠️ You haven't set up your profile yet. Please run `/start` first.", parse_mode = 'Markdown')
            return 
        
        stats = get_user_stats(db, telegram_id)
        #Get the most recent 5 jobs for our User
        recent_jobs = get_recent_jobs(db, telegram_id, limit = 5)
        
    
    total_jobs = stats.get("total_jobs", 0)
    status_counts = stats.get("jobs_by_status", {})
    score_counts = stats.get("jobs_by_score", {})
    total_apps = stats.get("total_applications", 0)
    
    #Calculate A/B scored jobs from status or score counts
    scored_ab = score_counts.get("A", 0) + score_counts.get("B", 0)
    tailored_count = status_counts.get("tailored", 0)
    
    report = f"📊 **Your JobBot Stats**\n"
    report += "━━━━━━━━━━━━━━━━━━━\n"
    report += f"🎯 Total Jobs Found: `{total_jobs}`\n"
    report += f"✅ Scored (A/B): `{scored_ab}`\n"
    report += f"✂️ Tailored Resumes: `{tailored_count}`\n"
    report += f"📤 Applications: `{total_apps}`\n\n"
    
    report += "📈 **Score Distribution:**\n"
    report += f"  A: {score_counts.get('A', 0)} | B: {score_counts.get('B', 0)} | C: {score_counts.get('C', 0)} | D: {score_counts.get('D', 0)} | F: {score_counts.get('F', 0)}\n\n"
    
    if recent_jobs:
        report += "🕐 **Recent Activity:**\n"
        status_emojis = {
            "new": "🆕", "scored": "⚖️", "tailored": "✂️", 
            "ready_to_apply": "✅", "applied": "📤", "skipped": "⏭️", "rejected": "❌"
        }
        for job in recent_jobs[:5]:
            emoji = status_emojis.get(job.status.value, "❓")
            score_str = f" (Score: {job.score})" if job.score else ""
            report += f"• {emoji} **{job.title}** @ {job.company}{score_str}\n"
    else:
        report += "🕐 **Recent Activity:**\n• No jobs processed yet. Try `/search`!\n"
        
    await update.message.reply_text(report, parse_mode="Markdown")
        
    
        