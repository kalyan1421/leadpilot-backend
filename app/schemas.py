"""Pydantic schemas for API validation."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator






class AudioCallResponse(BaseModel):
    """Schema for audio call response."""

    call_id: str
    timestamp: int  # Epoch timestamp in seconds
    transcript: Dict[str, Any]
    audio_file_url: str
    processed_data: Optional[Dict[str, Any]] = None
    created_at: int  # Epoch timestamp in seconds
    updated_at: int  # Epoch timestamp in seconds

    @field_validator("timestamp", "created_at", "updated_at", mode="before")
    @classmethod
    def convert_datetime_to_epoch(cls, v):
        if isinstance(v, datetime):
            return int(v.timestamp())
        return v

    class Config:
        from_attributes = True


class AudioCallUpdate(BaseModel):
    """Schema for updating an audio call."""

    transcript: Optional[Dict[str, Any]] = None
    audio_file_url: Optional[str] = None
    processed_data: Optional[Dict[str, Any]] = None
    timestamp: Optional[datetime] = None


class LeadAnalysisUpdate(BaseModel):
    """Schema for a telecaller's manual correction to a call's analysis.

    Partial update — only key_points is editable today. Deliberately does not
    accept the other analysis fields (bant_score, verdict, etc.) since those
    are AI-computed and re-run via POST /lead-analysis, not hand-edited.
    """

    key_points: Optional[List[str]] = None










# New schemas for extracted data






# Agent Comparison Schemas


















