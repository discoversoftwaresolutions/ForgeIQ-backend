# File: forgeiq-backend/app/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
import logging
from .models import Base # Import ForgeIQ's specific Base

logger = logging.getLogger(__name__)

# --- Database URL from environment variables ---
DATABASE_URL = os.getenv("FORGEIQ_DATABASE_URL") # Use a unique env var for ForgeIQ's DB
if not DATABASE_URL:
    raise ValueError("FORGEIQ_DATABASE_URL environment variable not set for ForgeIQ Backend.")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Function to create tables (run once, e.g., at app startup or via migration)
def create_db_tables():
    logger.info("Creating ForgeIQ database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("ForgeIQ database tables created.")

# Dependency to get DB session in FastAPI endpoints
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
