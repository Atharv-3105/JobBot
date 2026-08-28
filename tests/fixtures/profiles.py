"""
Synthetic user profiles reusing the real profile.yml skills-tier shape
(core/primary/secondary/basic), covering a spread of breadth/focus for the
test audit's profile x JD matrix.
"""

PROFILES = {
    "narrow_junior": {
        "name": "Narrow Junior",
        "target_roles": ["Backend Engineer"],
        "experience_years": 1,
        "skills": {
            "core": ["Python", "Flask"],
            "primary": ["SQLite"],
            "secondary": [],
            "basic": ["Git"],
        },
    },
    "broad_senior": {
        "name": "Broad Senior",
        "target_roles": ["Staff Engineer", "Backend Engineer", "ML Engineer"],
        "experience_years": 8,
        "skills": {
            "core": ["Python", "Go", "FastAPI", "Docker", "Kubernetes"],
            "primary": ["PostgreSQL", "Redis", "AWS", "Terraform", "gRPC"],
            "secondary": ["Kafka", "GraphQL", "React"],
            "basic": ["TypeScript", "Rust"],
        },
    },
    "ml_focused": {
        "name": "ML Focused",
        "target_roles": ["ML Engineer", "AI Engineer"],
        "experience_years": 3,
        "skills": {
            "core": ["Python", "PyTorch", "Scikit-learn"],
            "primary": ["LangChain", "LangGraph", "RAG", "ChromaDB"],
            "secondary": ["FastAPI", "Docker"],
            "basic": ["AWS"],
        },
    },
    "backend_focused": {
        "name": "Backend Focused",
        "target_roles": ["Backend Engineer"],
        "experience_years": 4,
        "skills": {
            "core": ["Java", "Spring Boot", "PostgreSQL"],
            "primary": ["Docker", "REST APIs", "Git"],
            "secondary": ["AWS", "CI/CD"],
            "basic": ["Kafka"],
        },
    },
    "near_empty": {
        "name": "Near Empty",
        "target_roles": ["Software Engineer"],
        "experience_years": 0,
        "skills": {
            "core": ["Python"],
            "primary": [],
            "secondary": [],
            "basic": [],
        },
    },
}


def get_allowed_skills(profile_key: str) -> list:
    """Flatten all tiers, matching orchestrator.py's flattening logic exactly."""
    skills = PROFILES[profile_key]["skills"]
    return [s for tier in ("core", "primary", "secondary", "basic") for s in skills.get(tier, [])]
