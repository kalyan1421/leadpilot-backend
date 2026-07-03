"""Database connection and session management."""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app.config import settings

# Create database engine
#
# If DATABASE_URL points at Supabase, use the DIRECT connection (port 5432,
# db.<project-ref>.supabase.co) — not the pgbouncer transaction pooler (port 6543,
# aws-0-<region>.pooler.supabase.com). psycopg2 uses server-side prepared statements by
# default, and pgbouncer's transaction-mode pooling doesn't support those reliably, which
# breaks under SQLAlchemy's own connection pooling here. Append ?sslmode=require to the
# connection string too (Supabase enforces TLS on the direct connection).
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
    echo=settings.debug
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
