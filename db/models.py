from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Enum, UniqueConstraint
from sqlalchemy.orm import relationship, declarative_base 
from datetime import datetime, timezone
import enum 

Base = declarative_base()


class JobStatus(enum.Enum):
    """ 
        This is the model of a Job Listing in the pipeline
    """
    NEW = "new"
    SCORED = "scored"
    TAILORED = "tailored"
    READY_TO_APPLY = "ready_to_apply"
    APPLIED = "applied"
    SKIPPED = "skipped"
    REJECTED = "rejected"
    
class User(Base):
    """ 
        This is the User model which will be scoped by telegram UserID
        Each User will get 1-row with their profile details and resume details
    """
    
    __tablename__ = "users"
    
    user_id = Column(Integer, primary_key = True)
    username = Column(String(255), nullable = True)
    onboarded_at = Column(DateTime, default = lambda:datetime.now(timezone.utc))    #using lambda: so that SQLAlchemy can call it at runtime, otherwise datetime is loaded only when the module is imported and can cause bugs with current time.
    profile_path = Column(String(512), nullable = False)    #path to profile.yml
    resume_path = Column(String(512), nullable = False)     #path to base_resume.tex
    
    #Relationships with Tables
    jobs = relationship("Job", back_populates="user", cascade="all, delete-orphan")
    applications = relationship("Application", back_populates="user", cascade = "all, delete-orphan")
    
    
    def __repr__(self):
        return f"<User(user_id = {self.user_id}, username = {self.username})>"
    

class Job(Base):
    """ 
        This is the model for Job Listing
    """
    
    __tablename__ = "jobs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable = False, index = True)
    title = Column(String(512), nullable = False)
    company = Column(String(512), nullable = False)
    url = Column(String(1024), nullable = False)        #Job-Posting URL
    portal = Column(String(100), nullable = False)      #Job-Posting Portal
    
    jd_text = Column(Text, nullable = True)             #JD text
    score = Column(String(10), nullable = True)         #Profile Score against JD 'A, B, C, D, F'
    score_data = Column(Text, nullable = True)          #ScorerAgent will return {score, match_percentage, strengths, gaps, recommendation} so we need to store the full JSON 
    status = Column(Enum(JobStatus), default = JobStatus.NEW, nullable = False, index = True)
    
    applied_at = Column(DateTime, nullable = True)
    created_at = Column(DateTime, default = lambda: datetime.now(timezone.utc), nullable = False, index = True)
    
    #RelationShips with Tables
    user = relationship("User", back_populates="jobs")
    applications = relationship("Application", back_populates="job", cascade = "all, delete-orphan")
    
    
    __table_args__ = (UniqueConstraint("user_id", "url", name = "uq_user_job_url"),)
    
    def __repr__(self):
        return f"<Job(id={self.id}, user_id={self.user_id}, title='{self.title}', company='{self.company}')>"
    

class Application(Base):
    """  
        This is the model for Application tracking,
        Records each application submission with resume version and result.
    """
    
    __tablename__ = "applications"
    
    id = Column(Integer, primary_key = True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable = False, index = True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable = False, index = True)
    
    resume_version = Column(String(512), nullable = False)      #path to tailored resume PDF used
    applied_at = Column(DateTime, default = lambda: datetime.now(timezone.utc), nullable = False)
    result = Column(String(50), nullable = True)                #"submitted", "rejected", "interview", "waiting"
    notes = Column(Text, nullable = True)                       #Any additional notes for the application 
    
    #Relationships with other tables 
    user = relationship("User", back_populates="applications")
    job  = relationship("Job", back_populates="applications")
    
    def __repr__(self):
        return f"<Application(id={self.id}, user_id={self.user_id}, job_id={self.job_id}, applied_at={self.applied_at})>"
    
    
    
    