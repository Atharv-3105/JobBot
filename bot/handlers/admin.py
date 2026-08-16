import logging 
import os 
from telegram import Update 
from telegram.ext import ContextTypes
from db import get_db 
from db.crud import count_users
from sqlalchemy import select,func 
from db.models import User, Job, Application 
from router.llm_router import llm_router
from bot.worker import job_queue
from dotenv import load_dotenv


load_dotenv()
logger = logging.getLogger(__name__)

ADMIN_USER_ID = os.getenv("ADMIN_ID")

async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ 
        /admin_stats - Shows system-wide metrics and observability data
    """
    
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("Access Denied. Admin only")
        return 
    
    with get_db() as db:
        total_users = count_users(db)
        total_jobs = db.execute(select(func.count(Job.id))).scalar()
        total_applications = db.execute(select(func.count(Application.id))).scalar()
        
        #Jobs by status
        status_query = select(Job.status, func.count(Job.id)).group_by(Job.status)
        status_counts = {str(status.values): count for status, count in db.execute(status_query).all()}
        
        
    #LLMRouter Stats
    router_status = llm_router.get_status()
    router_text = "\n".join([f"{name.upper}: {data['status']} ({data['rpm_used']} / {data['rpm_limit']} RPM)" for name, data in router_status.items()])
    
    #Queue Stats
    queue_info = job_queue.get_info(0)
    
    report = (
        "📊 **System Observability Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 **Total Users:** `{total_users}`\n"
        f"💼 **Total Jobs Tracked:** `{total_jobs}`\n"
        f"📤 **Total Applications:** `{total_applications}`\n\n"
        "📈 **Jobs by Status:**\n"
    )
    
    for status, count in status_counts.items():
        report += f" {status.replace("_", ' ').title()}: `{count}`\n"
        
    report += (
        "\n🤖 **LLM Router Status:**\n"
        f"{router_text}\n\n"
        "⏳ **Worker Queue:**\n"
        f"  • Pending Tasks: `{queue_info['position'] - 1}`\n"
        f"  • Active Workers: `{queue_info['active_workers']}`\n"
    )
    
    await update.message.reply_text(report, parse_mode = 'Markdown')