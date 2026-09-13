from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

def init_db():
    from app.models.notification import (
        Source, Recruitment, Notification, DocumentVersion, Evidence,
        ExtractionRun, ReviewDecision, Lead, EventLog,
    )
    Base.metadata.create_all(engine)
