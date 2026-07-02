"""SQLAlchemy ORM models package.

Import all models here so that Alembic's ``env.py`` can discover them via
``Base.metadata`` without needing to enumerate each file.
"""

from app.models.candidate import (  # noqa: F401
    Candidate,
    CareerHistory,
    Education,
    Skill,
    Certification,
    Language,
)
from app.models.redrob_signals import RedrobSignal  # noqa: F401
from app.models.job_description import JobDescription  # noqa: F401
from app.models.ranking import RankingRun, CandidateRank  # noqa: F401
