import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.models import Base, User, Job, Application, JobStatus
from db.crud import (
    save_user, get_user, get_all_users,
    save_job, get_job_by_url, get_job_by_id, update_job_status,
    get_jobs_by_status, get_pending, get_recent_jobs,
    save_application, get_applications, get_application_by_id,
    update_application_result, get_user_stats, count_users
)

@pytest.fixture
def db_session():
    """Create a temporary in-memory database and session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()

def test_user_crud(db_session):
    """Test user creation, retrieval, and updates."""
    # 1. Test creation
    user_id = 12345
    username = "test_user"
    target_roles = ["Backend Engineer", "ML Engineer"]
    skills = {"core": ["Python", "Go"], "primary": ["FastAPI"]}
    resume_path = "/tmp/resume.tex"
    
    user = save_user(db_session, user_id, username, target_roles, skills, resume_path)
    assert user is not None
    assert user.user_id == user_id
    assert user.username == username
    assert user.target_roles == target_roles
    assert user.skills == skills
    assert user.resume_path == resume_path
    
    # 2. Test get_user
    fetched = get_user(db_session, user_id)
    assert fetched is not None
    assert fetched.username == username
    
    # 3. Test get_all_users
    users = get_all_users(db_session)
    assert len(users) == 1
    assert users[0].user_id == user_id
    
    # 4. Test update
    updated_roles = ["Frontend Engineer"]
    save_user(db_session, user_id, username, updated_roles, skills, resume_path)
    fetched_updated = get_user(db_session, user_id)
    assert fetched_updated.target_roles == updated_roles
    
    # 5. Test count_users
    assert count_users(db_session) == 1

def test_job_crud(db_session):
    """Test job listing creation, retrieval, status updates, and stats."""
    # Setup user
    user_id = 9999
    save_user(db_session, user_id, "job_user", [], {}, "/tmp/res.tex")
    
    # 1. Test save_job (new job)
    title = "Python Engineer"
    company = "Awesome Corp"
    url = "https://example.com/job1"
    portal = "greenhouse"
    jd_text = "Python role description"
    
    job = save_job(db_session, user_id, title, company, url, portal, jd_text)
    assert job is not None
    assert job.id is not None
    assert job.title == title
    assert job.status == JobStatus.NEW
    
    # 2. Test get_job_by_url
    fetched_url = get_job_by_url(db_session, user_id, url)
    assert fetched_url is not None
    assert fetched_url.id == job.id
    
    # 3. Test get_job_by_id
    fetched_id = get_job_by_id(db_session, user_id, job.id)
    assert fetched_id is not None
    assert fetched_id.url == url
    
    # 4. Test updating job status
    updated_job = update_job_status(db_session, user_id, job.id, JobStatus.SCORED)
    assert updated_job is not None
    assert updated_job.status == JobStatus.SCORED
    
    # 5. Test getting jobs by status
    scored_jobs = get_jobs_by_status(db_session, user_id, JobStatus.SCORED)
    assert len(scored_jobs) == 1
    assert scored_jobs[0].id == job.id
    
    # 6. Test duplicate job save updates rather than inserting
    updated_jd = "Updated Python description"
    score_data = {"match_percentage": 90, "strengths": ["FastAPI"], "gaps": [], "recommendation": "Apply"}
    job_dup = save_job(db_session, user_id, title, company, url, portal, updated_jd, score="A", score_data=score_data)
    assert job_dup.id == job.id
    assert job_dup.status == JobStatus.SCORED
    assert job_dup.score == "A"
    
    # 7. Test get_recent_jobs and get_pending
    recent = get_recent_jobs(db_session, user_id)
    assert len(recent) == 1
    
    # Update status to READY_TO_APPLY
    update_job_status(db_session, user_id, job.id, JobStatus.READY_TO_APPLY)
    pending = get_pending(db_session, user_id)
    assert len(pending) == 1
    assert pending[0].id == job.id

def test_application_crud(db_session):
    """Test job application submissions and tracking."""
    # Setup user and job
    user_id = 777
    save_user(db_session, user_id, "app_user", [], {}, "/tmp/res.tex")
    job = save_job(db_session, user_id, "Test Job", "Test Co", "https://url", "lever")
    
    # 1. Save application
    resume_ver = "/tmp/tailored_res.pdf"
    app = save_application(db_session, user_id, job.id, resume_ver, result="submitted", notes="First try")
    
    assert app is not None
    assert app.id is not None
    assert app.resume_version == resume_ver
    assert app.result == "submitted"
    
    # The associated job's status should now be updated to APPLIED
    updated_job = get_job_by_id(db_session, user_id, job.id)
    assert updated_job.status == JobStatus.APPLIED
    assert updated_job.applied_at is not None
    
    # 2. Get applications
    apps = get_applications(db_session, user_id)
    assert len(apps) == 1
    assert apps[0].id == app.id
    
    # 3. Get application by id
    fetched_app = get_application_by_id(db_session, user_id, app.id)
    assert fetched_app is not None
    assert fetched_app.job_id == job.id
    
    # 4. Update application result
    updated_app = update_application_result(db_session, user_id, app.id, "interview", notes="Called back!")
    assert updated_app is not None
    assert updated_app.result == "interview"
    assert updated_app.notes == "Called back!"

def test_user_stats(db_session):
    """Test stats aggregation logic."""
    user_id = 888
    save_user(db_session, user_id, "stats_user", [], {}, "/tmp/res.tex")
    
    # Check stats for new user
    stats = get_user_stats(db_session, user_id)
    assert stats["total_jobs"] == 0
    assert stats["total_applications"] == 0
    
    # Add a job and an application
    job = save_job(db_session, user_id, "Job A", "Company A", "https://j1", "greenhouse")
    save_application(db_session, user_id, job.id, "/tmp/r.pdf", result="submitted")
    
    stats_updated = get_user_stats(db_session, user_id)
    assert stats_updated["total_jobs"] == 1
    assert stats_updated["total_applications"] == 1
    assert stats_updated["jobs_by_status"]["applied"] == 1
