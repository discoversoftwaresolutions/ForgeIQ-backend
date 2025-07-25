# File: forgeiq-backend/app/models.py
from sqlalchemy import Column, String, Integer, DateTime, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

# --- SQLAlchemy Base for ForgeIQ ---
# Use your existing Base if you have one globally shared, otherwise define it here.
Base = declarative_base()

# --- ForgeIQTask Model Definition ---
class ForgeIQTask(Base):
    __tablename__ = "forgeiq_tasks" # Unique table name for ForgeIQ's tasks

    id = Column(String, primary_key=True, index=True) # task_id from incoming request
    task_type = Column(String, nullable=False) # e.g., "build", "code_gen", "pipeline_gen", "deployment"
    status = Column(String, default="pending", nullable=False) # e.g., "pending", "running", "completed", "failed"
    current_stage = Column(String, nullable=True) # e.g., "Codex Gen", "MCP Optimization", "Algorithm Apply"
    progress = Column(Integer, default=0, nullable=False) # 0-100

    # Payload (input data for the task)
    payload = Column(JSON, nullable=False) # Stores the incoming request payload

    # Output and Logging
    logs = Column(Text, nullable=True) # Accumulated logs or a link to logs
    output_data = Column(JSON, nullable=True) # Final output data (e.g., generated code, build URL)
    details = Column(JSON, nullable=True) # For additional structured data like errors, intermediate results

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<ForgeIQTask(id='{self.id}', type='{self.task_type}', status='{self.status}', progress={self.progress})>"
