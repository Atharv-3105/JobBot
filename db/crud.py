from sqlalchemy import create_engine, select, func 
from sqlalchemy.orm import sessionmaker 
from datetime import datetime, timezone
from typing import Optional, List 
import os 
import json
from contextlib import contextmanager

from db.models import Base, User, Job, Application, JobStatus

#Setup the DB connection
DATABASE_URL = "sqlite:///./data/job_bot.db"
engine = create_engine(DATABASE_URL, connect_args = {'check_same_thread': False})
SessionLocal = sessionmaker(autocommit = False, autoflush = False, bind = engine)

def init_db():
    """ 
        Create all tables if they don't exists.
    """
    
    os.makedirs("./data", exist_ok =True)
    Base.metadata.create_all(bind = engine)

@contextmanager
def get_db():
    """  
        Dependency Injection to get DB session
    """
    db = SessionLocal()
    try:
        yield db 
    finally:
        db.close()
        
        
#----------User CRUD opers-----------------    
def save_user(db, user_id: int, username: Optional[str], target_roles: list, skills: dict, resume_path: str) -> User:
    """ 
        Create or Update a User Record.
        Args:
            db: Database session
            user_id:  Telegram user ID
            username: Telegram UserName 
            target_roles: Target Roles which the User-want's to target
            skills: user's skills
            resume_path:  Path to user's base_resume.tex
    """
    
    #get the user from User table using user_id
    user = db.get(User, user_id)
    
    #If user exists, then update else create
    if user:
        user.username = username
        user.target_roles = target_roles
        user.skills = skills
        user.resume_path = resume_path 
    else:
        #Create a New USER
        user = User(user_id = user_id, username = username, target_roles = target_roles, skills = skills, resume_path = resume_path)
        db.add(user)
        
    
    db.commit()
    db.refresh(user)
    return user 

def get_user(db, user_id: int) -> Optional[User]:
    """  
        Get a user by telegram user_id
        Args: take  db session and user-id
        Returns: user object or None if not found
    """
    
    return db.get(User, user_id)


def get_all_users(db) -> List[User]:
    """  
        Get all registered users( admin function only)
        Args: takes in db session
        Returns: List all User Objects present in the DB
    """
    
    query = select(User).order_by(User.onboarded_at.desc())
    return db.execute(query).scalars().all()


#-----------JOB CRUD Opers----------------------

def save_job(db, user_id: int, title: str, company: str, url: str, 
             portal: str, jd_text: Optional[str] = None,
             score: Optional[str] = None, score_data: Optional[dict] = None) -> Job:
    """
        Create or Update a Job Listing 
        If a Job with the same URL exists for this user, update it.
        
        Args: 
            db session, telegram userID, Job title, Job Company, Job URL, Job portal, JD , Score, Score-Data
        Returns: 
            A Job Object
    """
    
    #Check if JOB already exists for this user with this URL
    query = select(Job).where(Job.user_id == user_id, Job.url == url)
    job = db.execute(query).scalar_one_or_none()
    
    #If Job exists in DB,then UPDATE the fields that improve data quality, 
    if job:
        if not job.jd_text and jd_text:
            job.jd_text = jd_text
        job.portal = portal
        
        #Update score if provided
        if score:
            job.score = score
            job.score_data = json.dumps(score_data) if score_data else None 
            job.status = JobStatus.SCORED
        
    else:
        #Insert New Job
        initial_status = JobStatus.SCORED if score else JobStatus.NEW 
        job = Job(
            user_id = user_id,
            title = title, 
            company = company,
            url = url,
            portal = portal,
            jd_text = jd_text,
            score = score,
            score_data = json.dumps(score_data) if score_data else None,
            status = initial_status
        )
        db.add(job)
        
    
    db.commit()
    db.refresh(job)
    return job

def get_job_by_url(db, user_id: int, url: str) -> Optional[Job]:
    """ 
        Function to get a Job by URL for a specific user
        Args: DB_Session, UserID, Job_URL
        Returns: Job Object or None if not found
    """    
    
    query = select(Job).where(Job.user_id == user_id, Job.url == url)
    return db.execute(query).scalar_one_or_none()


def get_job_by_id(db, user_id: int, job_id: int) -> Optional[Job]:
    """ 
        Function to get a Job by job_id for a specific USER
        Args: DB_Session, UserID, JobID
        Returns: Job object or None if not found
    """
    
    query = select(Job).where(Job.user_id == user_id, Job.id == job_id)
    return db.execute(query).scalar_one_or_none()


def update_job_status(db, user_id: int, job_id: int, status: JobStatus) -> Optional[Job]:
    """ 
        Function to Update the status of a job for a specific user.
        Args: DB_Session, UserID, JobID, JobStatus
        Returns: Updated Job Object or none if not found
    """
    
    job = get_job_by_id(db, user_id, job_id)
    if job:
        job.status = status
        db.commit()
        db.refresh(job)
        
    return job 


def update_job_score(db, user_id:int, job_id: int, score: str, score_data: Optional[dict] = None) -> Optional[Job]:
    """
        This function updates the score of a Job for a specific user
        Args: DB_Session, UserID, JobID, Score, ScoreData
        Returns: Updated Job Object or None if not found
    """
    
    job = get_job_by_id(db, user_id, job_id)
    if job:
        job.score = score 
        job.score_data = json.dumps(score_data) if score_data else None
        job.status = JobStatus.SCORED
        db.commit()
        db.refresh(job)
    
    return job 

def get_jobs_by_status(db, user_id: int, status: JobStatus) -> List[Job]:
    """  
        This function GET all the Jobs for a specific status for a User
        Args: DB_SESSION, UserID, Status
        Returns: List of Job objects
    """
    
    query = select(Job).where(Job.user_id == user_id, Job.status == status).order_by(Job.created_at.desc())
    
    return db.execute(query).scalars().all()


def get_pending(db, user_id: int) -> List[Job]:
    """  
        This function GET all Jobs awaiting confirmation for a User
        These are Jobs with status READY_TO_APPLY
        Args: DB_SESSION, UserID
        Returns: List of Job objects
    """
    
    return get_jobs_by_status(db, user_id, JobStatus.READY_TO_APPLY)


def get_recent_jobs(db, user_id: int, limit: int = 20) -> List[Job]:
    """  
        This function GETs the most recent Jobs for a User
        Args: DB_SESSION, UserID, Limit
        Returns: List of most recent Job objects
    """
    
    query = select(Job).where(Job.user_id == user_id).order_by(Job.created_at.desc()).limit(limit)
    return db.execute(query).scalars().all()

#------------------- Application CRUD opers-------------------------------
    
def save_application(db, user_id: int, job_id: int, resume_version: str, result: Optional[str] = None, notes: Optional[str] = None) -> Application:
    """ 
        This function records an Application Submission
        Args: DB_Session, UserID, JobID, ResumeVersion, Result{application result}, AdditionalNotes
        Returns: Application Object
    """
    
    application = Application(user_id = user_id, job_id = job_id, resume_version = resume_version, result = result, notes = notes)
    db.add(application)
    
    #Also update the Job status and applied_at timestamp
    job = get_job_by_id(db, user_id, job_id)
    if job: 
        job.status = JobStatus.APPLIED
        job.applied_at = datetime.now(timezone.utc)
    
    db.commit()
    db.refresh(application)
    return application 


def get_applications(db, user_id: int, limit: int = 50) -> List[Application]:
    """  
        This function GET all applications for a specific user.
        NEVER return applications from other users.

        ARGS: DB_Session, UserID, Limit of results
        RETURNS: List of application objects 
    """
    
    query = select(Application).where(Application.user_id == user_id).order_by(Application.applied_at.desc()).limit(limit)
    return db.execute(query).scalars().all()

def get_application_by_id(db, user_id: int, application_id: int) -> Optional[Application]:
    """  
        This function GET specific application by ID for a user
    """
    query = select(Application).where(Application.user_id == user_id, Application.id == application_id)
    return db.execute(query).scalar_one_or_none()


def update_application_result(db, user_id: int, application_id: int, result: str, notes: Optional[str] = None) -> Optional[Application]:
    """  
        This function Updates the result of an application
        
        Args: DB_Session, UserID, ApplicationID, Result, Notes
        Returns: Updated Application Object or None if not found 
    """
    
    application = get_application_by_id(db, user_id, application_id)
    if application:
        application.result = result 
        if notes:
            application.notes = notes 
        db.commit()
        db.refresh(application)
    
    return application 

#------------------------STATS & UTITLITIES ----------------------
def get_user_stats(db, user_id: int) -> dict:
    """ 
        This function GET stats for a specific user
        
        ARGS: DB_Session, UserID
        RETURNS: Dict with user stats
    """
    #Query to count Job by status
    query = select(Job.status, func.count(Job.id)).where(Job.user_id == user_id).group_by(Job.status)
    job_counts = {str(status.value): count for status, count in db.execute(query).all()}
    
    #Count total applications
    query = select(func.count(Application.id)).where(Application.user_id == user_id)
    total_applications = db.execute(query).scalar()
    
    return {
        "user_id": user_id,
        "total_jobs": sum(job_counts.values()),
        "jobs_by_status": job_counts,
        "total_applications":total_applications
    }
    
    
def count_users(db) -> int:
    """ 
        This function COUNTS total registered users(ADMIN ONLY FUNCTION)
    """
    
    query = select(func.count(User.user_id))
    return db.execute(query).scalar()
    
        
    
    