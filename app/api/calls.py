"""API endpoints for managing audio calls."""

import logging
import os
import re
import tempfile
import asyncio
from datetime import datetime
from functools import lru_cache
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Response, status, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AudioCall
from app.schemas import AudioCallResponse, AudioCallUpdate, LeadAnalysisUpdate
from app.utils.s3 import s3_manager
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calls", tags=["calls"])

# Aggregate / cross-call intelligence lives on its own prefixes so it never
# collides with the dynamic /api/calls/{call_id} route.
intel_router = APIRouter(prefix="/api", tags=["intelligence"])








@router.get("/count")
async def get_calls_count(db: Session = Depends(get_db)):
    """
    Get total count of calls.
    
    Args:
        db: Database session
        
    Returns:
        Total count of calls
    """
    count = db.query(AudioCall).count()
    return {"total": count}


@router.get("/{call_id}", response_model=AudioCallResponse)
async def get_call(call_id: str, db: Session = Depends(get_db)):
    """
    Get call information by ID.
    
    Args:
        call_id: Unique identifier for the call
        db: Database session
        
    Returns:
        Call information
    """
    call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call with ID {call_id} not found"
        )
    return call


@router.get("/{call_id}/audio")
async def download_audio(call_id: str, db: Session = Depends(get_db)):
    """
    Download audio file for a specific call.
    
    Supports local, Supabase, and S3 storage modes.
    """
    call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call with ID {call_id} not found"
        )
    
    if not call.audio_file_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No audio file URL available for this call"
        )
    
    # Local storage mode
    if settings.storage_mode == "local":
        try:
            # Check if it's a local:// URL
            if call.audio_file_url.startswith("local://"):
                storage_key = s3_manager.extract_s3_key_from_url(call.audio_file_url)
                audio_file = s3_manager.download_audio_file(storage_key)
                if audio_file:
                    # Get extension from the URL
                    file_extension = call.audio_file_url.split('.')[-1] if '.' in call.audio_file_url else 'mp3'
                    content_type = s3_manager._get_content_type(file_extension)
                    return Response(
                        content=audio_file.read(),
                        media_type=content_type,
                        headers={
                            "Content-Disposition": f"attachment; filename={call_id}.{file_extension}",
                            "Cache-Control": "public, max-age=3600"
                        }
                    )
            
            # Check if it's a direct file path
            if os.path.exists(call.audio_file_url):
                with open(call.audio_file_url, 'rb') as f:
                    audio_data = f.read()
                
                file_extension = call.audio_file_url.split('.')[-1] if '.' in call.audio_file_url else 'mp3'
                if file_extension == 'mpeg':
                    file_extension = 'mp3'
                content_type = s3_manager._get_content_type(file_extension)
                
                return Response(
                    content=audio_data,
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f"attachment; filename={call_id}.{file_extension}",
                        "Cache-Control": "public, max-age=3600"
                    }
                )
            
            # Try to find the audio file in local storage by call_id
            audio_path = s3_manager.get_audio_file_path(call_id) if hasattr(s3_manager, 'get_audio_file_path') else None
            if audio_path and os.path.exists(audio_path):
                with open(audio_path, 'rb') as f:
                    audio_data = f.read()
                file_extension = audio_path.split('.')[-1]
                content_type = s3_manager._get_content_type(file_extension)
                return Response(
                    content=audio_data,
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f"attachment; filename={call_id}.{file_extension}",
                        "Cache-Control": "public, max-age=3600"
                    }
                )
            
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Audio file not found for call {call_id}"
            )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error serving local audio for call {call_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to serve audio file: {str(e)}"
            )
    
    # Supabase Storage mode
    if settings.storage_mode == "supabase":
        try:
            if call.audio_file_url.startswith("supabase://"):
                storage_key = s3_manager.extract_s3_key_from_url(call.audio_file_url)
                audio_file = s3_manager.download_audio_file(storage_key)
                if audio_file:
                    file_extension = call.audio_file_url.split('.')[-1] if '.' in call.audio_file_url else 'mp3'
                    content_type = s3_manager._get_content_type(file_extension)
                    return Response(
                        content=audio_file.read(),
                        media_type=content_type,
                        headers={
                            "Content-Disposition": f"attachment; filename={call_id}.{file_extension}",
                            "Cache-Control": "public, max-age=3600"
                        }
                    )

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Audio file not found for call {call_id}"
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error serving Supabase-stored audio for call {call_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to serve audio file: {str(e)}"
            )

    # S3 storage mode (original logic)
    # Check if the URL is already an S3 URL
    if call.audio_file_url.startswith(f"https://{s3_manager.bucket_name}.s3.amazonaws.com/"):
        s3_key = s3_manager.extract_s3_key_from_url(call.audio_file_url)
        if s3_key and s3_manager.file_exists(s3_key):
            audio_file = s3_manager.download_audio_file(s3_key)
            if audio_file:
                if '.' in s3_key:
                    file_extension = s3_key.split('.')[-1]
                    content_type = s3_manager._get_content_type(file_extension)
                else:
                    file_extension = 'mp3'
                    content_type = 'audio/mpeg'
                
                audio_data = audio_file.read()
                return Response(
                    content=audio_data,
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f"attachment; filename={call_id}.{file_extension}",
                        "Cache-Control": "public, max-age=3600"
                    }
                )
    
    # Fallback: download from external URL
    try:
        s3_url = s3_manager.download_and_upload_audio(call.audio_file_url, call_id)
        if s3_url:
            call.audio_file_url = s3_url
            db.commit()
            
            s3_key = s3_manager.extract_s3_key_from_url(s3_url)
            detected_extension = s3_key.split(".")[-1] if s3_key and "." in s3_key else "mp3"
            audio_file = s3_manager.download_audio_file(s3_key)
            
            if audio_file:
                content_type = s3_manager._get_content_type(detected_extension)
                return Response(
                    content=audio_file.read(),
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f"attachment; filename={call_id}.{detected_extension}",
                        "Cache-Control": "public, max-age=3600"
                    }
                )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process audio file"
        )
            
    except Exception as e:
        logger.error(f"Error processing audio for call {call_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process audio file: {str(e)}"
        )


@router.get("/{call_id}/transcript")
async def get_transcript(call_id: str, db: Session = Depends(get_db)):
    """
    Get transcript JSON for a specific call.
    
    Args:
        call_id: Unique identifier for the call
        db: Database session
        
    Returns:
        Transcript JSON data
    """
    call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call with ID {call_id} not found"
        )
    
    return {"call_id": call_id, "transcript": call.transcript}










@router.get("/", response_model=List[AudioCallResponse])
async def list_calls(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """
    List all calls with pagination.
    
    Args:
        skip: Number of records to skip
        limit: Maximum number of records to return
        db: Database session
        
    Returns:
        List of call records
    """
    # Order by created_at descending (newest first) and apply pagination
    calls = db.query(AudioCall).order_by(AudioCall.created_at.desc()).offset(skip).limit(limit).all()
    return calls


@router.put("/{call_id}", response_model=AudioCallResponse)
async def update_call(
    call_id: str,
    call_update: AudioCallUpdate,
    db: Session = Depends(get_db)
):
    """
    Update an existing call record.
    
    Args:
        call_id: Unique identifier for the call
        call_update: Updated call information
        db: Database session
        
    Returns:
        Updated call record
    """
    call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call with ID {call_id} not found"
        )
    
    # Update fields if provided
    update_data = call_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(call, field, value)
    
    try:
        db.commit()
        db.refresh(call)
        return call
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update call: {str(e)}"
        )


@router.delete("/{call_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_call(call_id: str, db: Session = Depends(get_db)):
    """
    Delete a call record.
    
    Args:
        call_id: Unique identifier for the call
        db: Database session
    """
    call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call with ID {call_id} not found"
        )
    
    try:
        db.delete(call)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete call: {str(e)}"
        )











# ---------------------------------------------------------------------------
# Lead Analysis endpoints  (BANT, sentiment arc, intent tags, verdict, next action)
# ---------------------------------------------------------------------------

@router.get("/{call_id}/lead-analysis", status_code=status.HTTP_200_OK)
async def get_lead_analysis(call_id: str, db: Session = Depends(get_db)):
    """Return stored lead analysis for a call (BANT, verdict, sentiment arc, next action)."""
    from app.models import LeadAnalysis
    record = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"No lead analysis found for call {call_id}")
    return {
        "call_id": record.call_id,
        "status": record.status,
        "bant_score": record.bant_score,
        "bant_breakdown": record.bant_breakdown,
        "lead_verdict": record.lead_verdict,
        "lead_verdict_reason": record.lead_verdict_reason,
        "sentiment_arc": record.sentiment_arc,
        "intent_tags": record.intent_tags,
        "entities": record.entities,
        "call_summary": record.call_summary,
        "key_points": record.key_points,
        "next_steps": record.next_steps,
        "next_action": record.next_action,
        "agent_debrief": record.agent_debrief,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


@router.patch("/{call_id}/lead-analysis", status_code=status.HTTP_200_OK)
async def update_lead_analysis(
    call_id: str,
    update: LeadAnalysisUpdate,
    db: Session = Depends(get_db),
):
    """
    Telecaller correction to a call's analysis (key points today). Does NOT
    trigger a memory-bubble rebuild — a manual correction shouldn't get
    silently overwritten or cascade into memory the next time analysis reruns.
    """
    from app.models import LeadAnalysis

    record = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"No lead analysis found for call {call_id}")

    update_data = update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(record, field, value)

    try:
        db.commit()
        db.refresh(record)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update lead analysis: {str(e)}")

    return {
        "call_id": record.call_id,
        "key_points": record.key_points,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


@router.post("/{call_id}/lead-analysis", status_code=status.HTTP_200_OK)
async def run_lead_analysis(call_id: str, db: Session = Depends(get_db)):
    """Run (or re-run) full lead analysis for a call and persist the result."""
    import asyncio
    import uuid as _uuid
    from app.models import LeadAnalysis
    from app.utils.lead_analyzer import analyze_call

    call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")

    # Upsert record
    record = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
    if not record:
        record = LeadAnalysis(id=str(_uuid.uuid4()), call_id=call_id)
        db.add(record)
    record.status = "processing"
    db.commit()

    try:
        # Run blocking sync analyzer in thread pool so we don't block the event loop
        transcript = call.transcript or {"turns": []}
        result = await asyncio.to_thread(analyze_call, transcript)

        if result is None:
            record.status = "failed"
            record.error = "Analyzer returned None — check logs for API error"
            db.commit()
            raise HTTPException(status_code=500, detail="Lead analysis failed — check server logs")

        record.bant_score = result.get("bant_score")
        record.bant_breakdown = result.get("bant_breakdown")
        record.lead_verdict = result.get("lead_verdict")
        record.lead_verdict_reason = result.get("lead_verdict_reason")
        record.sentiment_arc = result.get("sentiment_arc")
        record.intent_tags = result.get("intent_tags")
        record.entities = result.get("entities")
        record.call_summary = result.get("call_summary")
        record.key_points = result.get("key_points")
        record.next_steps = result.get("next_steps")
        record.next_action = result.get("next_action")
        record.agent_debrief = result.get("agent_debrief")
        record.status = "completed"
        record.error = None
        db.commit()

        # Rebuild this contact's memory bubble in the background (non-blocking)
        try:
            await _rebuild_memory_for_call(call_id, db)
        except Exception as mem_err:
            logger.warning(f"Memory bubble rebuild skipped for {call_id}: {mem_err}")

        return {"call_id": call_id, "status": "completed", **result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"run_lead_analysis exception for {call_id}: {e}", exc_info=True)
        record.status = "failed"
        record.error = str(e)
        db.commit()
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Score tab — one consolidated payload for the Figma Call Detail "Score" screen
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _dimension_status() -> Dict[str, str]:
    """Per-dimension trust status (validated|beta|hidden) from config/score_dimensions.json.
    Gold-set gate: a dimension is 'beta' until gold_set_eval.py shows it clears the bar.
    Path is anchored to the repo (not cwd) and parsed once (cached)."""
    import json
    import os
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "config", "score_dimensions.json"), encoding="utf-8") as f:
            d = json.load(f)
        return {k: v for k, v in d.items() if not k.startswith("_")}
    except Exception:
        return {}


# (removed _contact_score_series — get_call_score now derives the per-call series from the
#  single _all_analyses_by_contact scan, avoiding a second full-table scan per request.)


@router.get("/{call_id}/score", status_code=status.HTTP_200_OK)
async def get_call_score(call_id: str, window_days: int = 7, db: Session = Depends(get_db)):
    """
    Consolidated Score-tab payload — everything the Figma Call Detail "Score"
    screen needs in ONE request, so the frontend never computes a score itself:

      - call_score          : the hero ring (composite of the three per-call rings)
      - rings               : Overall / Telecaller / Lead Quality / Sentiment,
                              each {value, max, trend}  (trend = delta vs previous call)
      - breakdown           : the 5 dimensions, each {score, max, note}
      - sentiment_timeline  : the colored bar segments + a one-line caption

    Requires a completed lead-analysis (run POST .../lead-analysis first).
    """
    window_days = max(1, window_days)  # 0/negative would silently mean "all time"
    from app.models import LeadAnalysis
    from app.utils.memory_bubble import contact_key_from_call_id
    from app.utils.lead_intelligence import (
        sentiment_score, sentiment_timeline, call_score, score_trend,
        telecaller_score, mmss_to_seconds,
    )

    call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")

    la = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
    if not la or la.status != "completed":
        raise HTTPException(
            status_code=404,
            detail=f"No completed analysis for call {call_id}. "
                   f"Run POST /api/calls/{call_id}/lead-analysis first.",
        )

    debrief = la.agent_debrief or {}
    overall = debrief.get("total_score")
    lead_quality = round(la.bant_score) if isinstance(la.bant_score, (int, float)) else None

    # Sentiment ring + timeline. The timeline uses the real transcript clock
    # (turn 'MM:SS' timestamps) when available, else even spacing.
    arc = la.sentiment_arc or []
    transcript = call.transcript or {}
    turns = transcript.get("turns", []) if isinstance(transcript, dict) else []
    turn_seconds: Dict[int, int] = {}
    for i, t in enumerate(turns, 1):
        sec = mmss_to_seconds(t.get("timestamp"))
        if sec is not None:
            turn_seconds[i] = sec
    sentiment = sentiment_score(arc)
    timeline = sentiment_timeline(arc, turn_seconds=turn_seconds or None)

    hero = call_score(overall, lead_quality, sentiment)

    # ONE grouped scan powers BOTH the telecaller rolling score and this contact's
    # previous-call trend (avoids a second full-table scan per /score request).
    grouped = _all_analyses_by_contact(db)
    all_calls = [a for analyses in grouped.values() for a in analyses]
    tele = telecaller_score(all_calls, window_days=window_days)

    contact_key = contact_key_from_call_id(call_id)
    series = [
        {"call_id": a["call_id"], "overall": a.get("agent_total_score"),
         "lead_quality": round(a["bant_score"]) if isinstance(a.get("bant_score"), (int, float)) else None,
         "sentiment": a.get("sentiment_score")}
        for a in grouped.get(contact_key, [])
    ]
    prev: Optional[Dict[str, Any]] = None
    for idx, row in enumerate(series):
        if row["call_id"] == call_id:
            prev = series[idx - 1] if idx > 0 else None
            break

    DIMS = [
        ("opening", "Opening"),
        ("discovery", "Discovery"),
        ("pitch", "Pitch"),
        ("objection_handling", "Objection Handling"),
        ("closing", "Closing"),
    ]
    statuses = _dimension_status()  # validated | beta | hidden  (gold-set gate)
    breakdown = [
        {
            "key": key,
            "label": label,
            "score": debrief.get(f"{key}_score") or 0,
            "max": 20,
            "note": debrief.get(f"{key}_note") or "",
            "evidence": debrief.get(f"{key}_evidence") or [],  # [{turn,t,speaker,text}] — auditable quote
            "status": statuses.get(key, "beta"),
        }
        for key, label in DIMS
        if statuses.get(key, "beta") != "hidden"
    ]

    return {
        "call_id": call_id,
        "call_score": hero,
        "rings": {
            "overall": {
                "value": overall or 0, "max": 100,
                "trend": score_trend(overall, prev["overall"]) if prev else None,
            },
            "telecaller": {
                "value": round(tele.get("telecaller_score") or 0), "max": 100,  # int, like the other rings
                "trend": (round(tele["trend"]) if tele.get("trend") is not None else None),
            },
            "lead_quality": {
                "value": lead_quality or 0, "max": 100,
                "trend": score_trend(lead_quality, prev["lead_quality"]) if prev else None,
            },
            "sentiment": {
                "value": sentiment, "max": 100,
                "trend": score_trend(sentiment, prev["sentiment"]) if prev else None,
            },
        },
        "verdict": la.lead_verdict,
        "transcript_quality": (transcript.get("quality") if isinstance(transcript, dict) else None) or "ok",
        "breakdown": breakdown,
        "strengths": debrief.get("strengths") or [],
        "improvements": debrief.get("improvements") or [],
        "sentiment_timeline": timeline,
    }


# ---------------------------------------------------------------------------
# Memory Bubble endpoints  (per-contact cumulative memory — the moat)
# ---------------------------------------------------------------------------

def _gather_contact_calls(contact_key: str, db: Session) -> List[Dict[str, Any]]:
    """
    Collect every call belonging to a contact, oldest first, with its lead_analysis.

    TODAY: groups by name-slug derived from call_id (no phone field in dataset).
    BACKEND: replace with `WHERE lead.phone = contact_key` join.
    """
    from app.models import LeadAnalysis
    from app.utils.memory_bubble import contact_key_from_call_id

    # Single join instead of N+1 (full AudioCall scan + per-call LeadAnalysis lookup).
    rows = (
        db.query(AudioCall, LeadAnalysis)
        .join(LeadAnalysis, LeadAnalysis.call_id == AudioCall.call_id)
        .filter(LeadAnalysis.status == "completed")
        .order_by(AudioCall.timestamp.asc())
        .all()
    )
    out = []
    for c, la in rows:
        if contact_key_from_call_id(c.call_id) != contact_key:
            continue
        out.append({
            "call_id": c.call_id,
            "timestamp": c.timestamp.isoformat() if c.timestamp else None,
            "analysis": {
                "lead_verdict": la.lead_verdict,
                "bant_score": la.bant_score,
                "bant_breakdown": la.bant_breakdown,
                "entities": la.entities,
                "call_summary": la.call_summary,
            },
        })
    return out


async def _rebuild_memory_for_call(call_id: str, db: Session) -> Optional[Dict[str, Any]]:
    """Rebuild the memory bubble for whichever contact this call belongs to."""
    import asyncio
    import uuid as _uuid
    from app.models import MemoryBubble
    from app.utils.memory_bubble import build_memory_bubble, contact_key_from_call_id

    contact_key = contact_key_from_call_id(call_id)
    calls = _gather_contact_calls(contact_key, db)
    if not calls:
        return None

    bubble = await asyncio.to_thread(build_memory_bubble, contact_key, calls)
    if not bubble:
        return None

    record = db.query(MemoryBubble).filter(MemoryBubble.contact_key == contact_key).first()
    if not record:
        record = MemoryBubble(id=str(_uuid.uuid4()), contact_key=contact_key)
        db.add(record)

    record.total_calls = bubble.get("total_calls", len(calls))
    record.last_call_id = bubble.get("last_call_id")
    record.facts = bubble.get("facts")
    record.cumulative_bant = bubble.get("cumulative_bant")
    record.running_verdict = bubble.get("running_verdict")
    record.sentiment_trend = bubble.get("sentiment_trend")
    record.open_objections = bubble.get("open_objections")
    record.pending_commitments = bubble.get("pending_commitments")
    record.next_call_strategy = bubble.get("next_call_strategy")
    record.headline = bubble.get("headline")
    db.commit()
    return bubble


def _serialize_bubble(record) -> Dict[str, Any]:
    return {
        "contact_key": record.contact_key,
        "total_calls": record.total_calls,
        "last_call_id": record.last_call_id,
        "facts": record.facts or [],
        "cumulative_bant": record.cumulative_bant or {},
        "running_verdict": record.running_verdict,
        "sentiment_trend": record.sentiment_trend,
        "open_objections": record.open_objections or [],
        "pending_commitments": record.pending_commitments or [],
        "next_call_strategy": record.next_call_strategy,
        "headline": record.headline,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


@intel_router.get("/memory/{contact_key}", status_code=status.HTTP_200_OK)
async def get_memory_bubble(contact_key: str, db: Session = Depends(get_db)):
    """Return the stored memory bubble for a contact (phone number in production)."""
    from app.models import MemoryBubble
    record = db.query(MemoryBubble).filter(MemoryBubble.contact_key == contact_key).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"No memory bubble for contact {contact_key}")
    return _serialize_bubble(record)


@intel_router.post("/memory/{contact_key}/rebuild", status_code=status.HTTP_200_OK)
async def rebuild_memory_bubble(contact_key: str, db: Session = Depends(get_db)):
    """Force-rebuild a contact's memory bubble from all their analysed calls."""
    import asyncio
    import uuid as _uuid
    from app.models import MemoryBubble
    from app.utils.memory_bubble import build_memory_bubble

    calls = _gather_contact_calls(contact_key, db)
    if not calls:
        raise HTTPException(status_code=404, detail=f"No analysed calls found for contact {contact_key}")

    bubble = await asyncio.to_thread(build_memory_bubble, contact_key, calls)
    if not bubble:
        raise HTTPException(status_code=500, detail="Memory bubble build failed — check server logs")

    record = db.query(MemoryBubble).filter(MemoryBubble.contact_key == contact_key).first()
    if not record:
        record = MemoryBubble(id=str(_uuid.uuid4()), contact_key=contact_key)
        db.add(record)
    record.total_calls = bubble.get("total_calls", len(calls))
    record.last_call_id = bubble.get("last_call_id")
    record.facts = bubble.get("facts")
    record.cumulative_bant = bubble.get("cumulative_bant")
    record.running_verdict = bubble.get("running_verdict")
    record.sentiment_trend = bubble.get("sentiment_trend")
    record.open_objections = bubble.get("open_objections")
    record.pending_commitments = bubble.get("pending_commitments")
    record.next_call_strategy = bubble.get("next_call_strategy")
    record.headline = bubble.get("headline")
    db.commit()
    return _serialize_bubble(record)


# ---------------------------------------------------------------------------
# Inbox + Telecaller intelligence (deterministic, no LLM)
# ---------------------------------------------------------------------------

def _all_analyses_by_contact(db: Session) -> Dict[str, List[Dict[str, Any]]]:
    """Group every completed lead_analysis by contact, oldest first."""
    from app.models import LeadAnalysis
    from app.utils.memory_bubble import contact_key_from_call_id
    from app.utils.lead_intelligence import sentiment_score

    rows = (
        db.query(LeadAnalysis, AudioCall)
        .join(AudioCall, LeadAnalysis.call_id == AudioCall.call_id)
        .filter(LeadAnalysis.status == "completed")
        .order_by(AudioCall.timestamp.asc())
        .all()
    )
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for la, call in rows:
        key = contact_key_from_call_id(call.call_id)
        grouped.setdefault(key, []).append({
            "call_id": call.call_id,
            "timestamp": call.timestamp.isoformat() if call.timestamp else None,
            "bant_score": la.bant_score,
            "lead_verdict": la.lead_verdict,
            "intent_tags": la.intent_tags,
            "next_steps": la.next_steps,
            "next_action": la.next_action,
            "agent_total_score": (la.agent_debrief or {}).get("total_score") if la.agent_debrief else None,
            "sentiment_score": sentiment_score(la.sentiment_arc or []),  # for the per-call trend
        })
    return grouped


@intel_router.get("/inbox", status_code=status.HTTP_200_OK)
async def get_inbox(bucket: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Lead inbox: one card per contact with score, intent bucket, tags + header stats.
    Optional ?bucket=high_intent|new|follow_up|cold filter (the Figma chips).
    """
    from app.models import Lead
    from app.utils.lead_intelligence import lead_card, inbox_header

    grouped = _all_analyses_by_contact(db)
    leads = db.query(Lead).all()

    by_key: Dict[str, Dict[str, Any]] = {}

    # 1. Saved leads first — so a 'New' lead with no calls still shows up.
    for L in leads:
        analyses = grouped.get(L.contact_key, [])
        by_key[L.contact_key] = lead_card(
            L.contact_key, analyses, name=L.name, source=L.source, lead_status=L.status,
        )
    # 2. Calls that have no Lead row (legacy / imported demo data).
    for key, analyses in grouped.items():
        if key not in by_key:
            by_key[key] = lead_card(key, analyses, name=key.replace("_", " ").title())

    cards = sorted(by_key.values(), key=lambda c: c["lead_score"], reverse=True)
    header = inbox_header(cards)
    if bucket:
        cards = [c for c in cards if c["intent_bucket"] == bucket]
    return {"header": header, "leads": cards}


@router.get("/{call_id}/processing-status", status_code=status.HTTP_200_OK)
async def get_processing_status(call_id: str, db: Session = Depends(get_db)):
    """
    Unified processing stepper state (Figma: Upload -> Transcribe -> Analyse -> Done).

    Computed live from what actually exists in the DB, so the mobile app can poll
    this one endpoint to render the 4-step progress bar after a recording upload.
    """
    from app.models import LeadAnalysis

    call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")

    transcript = call.transcript or {}
    has_turns = bool(isinstance(transcript, dict) and transcript.get("turns"))
    transcribe_failed = bool(isinstance(transcript, dict) and transcript.get("error") and not has_turns)
    la = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
    analysed = bool(la and la.status == "completed")
    analysing = bool(la and la.status == "processing")
    analyse_failed = bool(la and la.status == "failed")

    def st(done, active=False):
        return "done" if done else ("active" if active else "pending")

    # Transcribe stage: done if turns exist, failed if transcription errored, else active.
    transcribe_status = "failed" if transcribe_failed else st(has_turns, active=not has_turns)
    # Analyse can only progress once transcription succeeded.
    if not has_turns:
        analyse_status = "pending"
    elif analyse_failed:
        analyse_status = "failed"
    else:
        analyse_status = st(analysed, active=(analysing or not analysed))

    stages = [
        {"key": "upload",     "label": "Upload",     "status": "done"},
        {"key": "transcribe", "label": "Transcribe", "status": transcribe_status},
        {"key": "analyse",    "label": "Analyse",    "status": analyse_status},
        {"key": "done",       "label": "Done",       "status": st(analysed)},
    ]
    done_count = sum(1 for s in stages if s["status"] == "done")
    failed = transcribe_failed or analyse_failed
    current = next((s["key"] for s in stages if s["status"] in ("active", "failed")), "done")
    err = None
    if transcribe_failed:
        err = f"Transcription failed: {transcript.get('error')}"
    elif analyse_failed:
        err = la.error

    return {
        "call_id": call_id,
        "current_stage": current,
        "percent": round(done_count / len(stages) * 100),
        "failed": failed,
        "error": err,
        "stages": stages,
    }


@router.get("/{call_id}/transcript/translate", status_code=status.HTTP_200_OK)
async def get_translated_transcript(call_id: str, target: str = "en", db: Session = Depends(get_db)):
    """
    Return the transcript translated to `target` (default English) — powers the
    Transcript tab "View English" toggle. Detects source language automatically.
    """
    import asyncio
    from app.utils.translation import translate_turns, detect_language, SUPPORTED_LANGS

    call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")

    transcript = call.transcript or {}
    turns = transcript.get("turns", []) if isinstance(transcript, dict) else []
    if not turns:
        raise HTTPException(status_code=404, detail="No transcript turns to translate")

    sample = " ".join(t.get("content", "") for t in turns[:5])
    source_lang = detect_language(sample)

    if source_lang == target:
        return {"call_id": call_id, "source_lang": source_lang, "target_lang": target,
                "already_in_target": True, "turns": turns}

    translated = await asyncio.to_thread(translate_turns, turns, source_lang, target)
    return {
        "call_id": call_id,
        "source_lang": source_lang,
        "source_lang_name": SUPPORTED_LANGS.get(source_lang, source_lang),
        "target_lang": target,
        "already_in_target": False,
        "turns": translated,
    }


@intel_router.get("/leads/dedupe", status_code=status.HTTP_200_OK)
async def dedupe_lead(phone: str, db: Session = Depends(get_db)):
    """
    Duplicate check for the Add Outbound Lead screen ("already in your leads").
    Matches on normalised phone digits. Registered BEFORE /leads/{contact_key}
    so 'dedupe' is not captured as a contact key.
    """
    from app.models import Lead
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return {"duplicate": False}
    for lead in db.query(Lead).filter(Lead.phone.isnot(None)).all():
        if re.sub(r"\D", "", lead.phone or "")[-10:] == digits[-10:] and digits[-10:]:
            return {"duplicate": True, "contact_key": lead.contact_key, "name": lead.name}
    return {"duplicate": False}


@intel_router.post("/leads", status_code=status.HTTP_201_CREATED)
async def create_lead(payload: Dict[str, Any], db: Session = Depends(get_db)):
    """
    Create a lead (the 'Save Lead' action). Appears in the inbox immediately as
    'New'. If a lead with the same contact_key exists, returns it (idempotent).
    """
    import uuid as _uuid
    from app.models import Lead
    from app.utils.memory_bubble import slugify_contact

    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    contact_key = slugify_contact(name)
    existing = db.query(Lead).filter(Lead.contact_key == contact_key).first()
    if existing:
        # update light fields, keep it idempotent
        existing.phone = payload.get("phone") or existing.phone
        existing.reason = payload.get("reason") or existing.reason
        existing.source = payload.get("source") or existing.source
        db.commit()
        return {"contact_key": existing.contact_key, "name": existing.name, "status": existing.status, "created": False}

    lead = Lead(
        id=str(_uuid.uuid4()),
        contact_key=contact_key,
        name=name,
        phone=payload.get("phone"),
        reason=payload.get("reason"),
        source=payload.get("source"),
        status="new",
    )
    db.add(lead)
    db.commit()
    return {"contact_key": contact_key, "name": name, "status": "new", "created": True}


@intel_router.get("/leads/{contact_key}", status_code=status.HTTP_200_OK)
async def get_lead_detail(contact_key: str, db: Session = Depends(get_db)):
    """
    Everything the Lead Detail screen needs in one call:
    card summary + memory bubble + call history. Works even for a 'New' lead
    that has no calls yet.
    """
    from app.models import MemoryBubble, Lead
    from app.utils.lead_intelligence import lead_card

    grouped = _all_analyses_by_contact(db)
    analyses = grouped.get(contact_key, [])
    lead_row = db.query(Lead).filter(Lead.contact_key == contact_key).first()

    if not analyses and not lead_row:
        raise HTTPException(status_code=404, detail=f"No lead or calls for contact {contact_key}")

    display_name = (lead_row.name if lead_row else None) or contact_key.replace("_", " ").title()
    card = lead_card(
        contact_key, analyses,
        name=display_name,
        source=lead_row.source if lead_row else None,
        lead_status=lead_row.status if lead_row else None,
    )

    bubble_row = db.query(MemoryBubble).filter(MemoryBubble.contact_key == contact_key).first()
    memory = _serialize_bubble(bubble_row) if bubble_row else None

    calls = [
        {
            "call_id": a["call_id"],
            "timestamp": a["timestamp"],
            "score": a.get("agent_total_score"),
            "bant_score": a.get("bant_score"),
            "lead_verdict": a.get("lead_verdict"),
        }
        for a in reversed(analyses)
    ]
    return {
        **card,
        "phone": lead_row.phone if lead_row else None,
        "reason": lead_row.reason if lead_row else None,
        "status": lead_row.status if lead_row else None,
        "memory": memory,
        "calls": calls,
    }


# ---------------------------------------------------------------------------
# Outbound recording upload  ->  transcribe -> analyse -> memory  (AI pipeline)
# ---------------------------------------------------------------------------

def _build_and_store_memory(contact_key: str, db: Session) -> Optional[Dict[str, Any]]:
    """Synchronous memory build + upsert (used by the background orchestrator)."""
    import uuid as _uuid
    from app.models import MemoryBubble
    from app.utils.memory_bubble import build_memory_bubble

    calls = _gather_contact_calls(contact_key, db)
    if not calls:
        return None
    bubble = build_memory_bubble(contact_key, calls)
    if not bubble:
        return None
    rec = db.query(MemoryBubble).filter(MemoryBubble.contact_key == contact_key).first()
    if not rec:
        rec = MemoryBubble(id=str(_uuid.uuid4()), contact_key=contact_key)
        db.add(rec)
    rec.total_calls = bubble.get("total_calls", len(calls))
    rec.last_call_id = bubble.get("last_call_id")
    rec.facts = bubble.get("facts")
    rec.cumulative_bant = bubble.get("cumulative_bant")
    rec.running_verdict = bubble.get("running_verdict")
    rec.sentiment_trend = bubble.get("sentiment_trend")
    rec.open_objections = bubble.get("open_objections")
    rec.pending_commitments = bubble.get("pending_commitments")
    rec.next_call_strategy = bubble.get("next_call_strategy")
    rec.headline = bubble.get("headline")
    db.commit()
    return bubble


def _set_job(db: Session, call_id: str, *, stage: str, status: str,
             error: Optional[str] = None, audio_path: Optional[str] = None, inc_attempt: bool = False):
    """Upsert the durable ProcessingJob row — crash-safe pipeline state (see startup recovery)."""
    import uuid as _uuid
    from app.models import ProcessingJob
    try:
        job = db.query(ProcessingJob).filter(ProcessingJob.call_id == call_id).first()
        if not job:
            job = ProcessingJob(id=str(_uuid.uuid4()), call_id=call_id)
            db.add(job)
        job.stage, job.status = stage, status
        if audio_path:
            job.audio_path = audio_path
        if error is not None:
            job.error = error
        if inc_attempt:
            job.attempts = (job.attempts or 0) + 1
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"ProcessingJob update failed for {call_id}: {e}")


def _process_uploaded_recording(call_id: str, audio_path: str):
    """
    Background orchestrator (runs after the upload response is sent):
      Transcribe -> save transcript -> run lead analysis -> rebuild memory bubble.
    The mobile app polls /processing-status to render the stepper meanwhile.
    Opens its OWN db session (the request session is already closed here).
    """
    import uuid as _uuid
    from app.database import SessionLocal
    from app.models import LeadAnalysis
    from app.utils.transcription import transcribe_audio
    from app.utils.lead_analyzer import analyze_call
    from app.utils.memory_bubble import contact_key_from_call_id

    db = SessionLocal()
    try:
        # Idempotency: recovery may re-dispatch a job left 'running' by a crash. If the call
        # was already analysed, don't redo the paid transcribe+analysis — just mark it done.
        if db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id,
                                         LeadAnalysis.status == "completed").first():
            _set_job(db, call_id, stage="done", status="done")
            return

        # Supabase-stored audio: the job row holds the durable `supabase://` reference (not a
        # temp path, which wouldn't survive a crash/restart), so resolve a fresh local copy here
        # on every invocation — covers both the first run and any later crash-recovery re-dispatch.
        # Keep `audio_path` itself as the durable reference for _set_job bookkeeping below; only
        # `local_audio_path` (used for the actual transcribe call) points at the temp file.
        local_audio_path = audio_path
        if audio_path and audio_path.startswith("supabase://"):
            from app.utils.s3 import get_storage_manager
            local_audio_path = get_storage_manager().get_audio_file_path(call_id)
            if not local_audio_path:
                logger.error(f"Upload {call_id}: could not resolve audio from Supabase Storage")
                _set_job(db, call_id, stage="failed", status="failed",
                         error="Could not resolve audio from Supabase Storage")
                return

        _set_job(db, call_id, stage="transcribe", status="running", inc_attempt=True)
        # ---- 1. Transcribe (auto-detect language so Sarvam swap-in works later) ----
        result = transcribe_audio(local_audio_path, language=None)
        call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
        if not call:
            logger.error(f"Upload orchestrator: call {call_id} vanished")
            return
        turns = result.get("turns", [])
        call.transcript = {
            "turns": turns,
            "full_text": result.get("full_text", ""),
            "language": result.get("language", "en"),
            "quality": result.get("quality", "ok"),   # preserve STT quality flag (ok|low|failed)
            **({"error": result["error"]} if result.get("error") else {}),
        }
        db.commit()

        # Guard: if transcription produced nothing, stop here — don't fake-analyse
        # an empty transcript into a misleading "Junk". The stepper will show the
        # transcribe stage as failed.
        if not turns:
            logger.error(f"Upload {call_id}: transcription produced 0 turns "
                         f"(error={result.get('error')}) — skipping analysis")
            _set_job(db, call_id, stage="transcribe", status="failed",
                     error=f"transcription failed: {result.get('error')}")
            return
        logger.info(f"Upload {call_id}: transcribed {len(turns)} turns (lang={result.get('language')})")

        # ---- 2. Lead analysis (upsert LeadAnalysis) ----
        rec = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
        if not rec:
            rec = LeadAnalysis(id=str(_uuid.uuid4()), call_id=call_id)
            db.add(rec)
        rec.status = "processing"
        db.commit()
        _set_job(db, call_id, stage="analyse", status="running")

        analysis = analyze_call(call.transcript)
        if not analysis:
            rec.status = "failed"
            rec.error = "Analyzer returned None"
            db.commit()
            logger.error(f"Upload {call_id}: analysis failed")
            _set_job(db, call_id, stage="analyse", status="failed", error="Analyzer returned None")
            return

        rec.bant_score = analysis.get("bant_score")
        rec.bant_breakdown = analysis.get("bant_breakdown")
        rec.lead_verdict = analysis.get("lead_verdict")
        rec.lead_verdict_reason = analysis.get("lead_verdict_reason")
        rec.sentiment_arc = analysis.get("sentiment_arc")
        rec.intent_tags = analysis.get("intent_tags")
        rec.entities = analysis.get("entities")
        rec.call_summary = analysis.get("call_summary")
        rec.key_points = analysis.get("key_points")
        rec.next_steps = analysis.get("next_steps")
        rec.next_action = analysis.get("next_action")
        rec.agent_debrief = analysis.get("agent_debrief")
        rec.status = "completed"
        rec.error = None
        db.commit()
        logger.info(f"Upload {call_id}: analysed -> {rec.lead_verdict} (bant {rec.bant_score})")

        # ---- 3. Rebuild memory bubble for this contact ----
        _set_job(db, call_id, stage="memory", status="running")
        try:
            _build_and_store_memory(contact_key_from_call_id(call_id), db)
        except Exception as e:
            logger.warning(f"Upload {call_id}: memory rebuild skipped: {e}")

        _set_job(db, call_id, stage="done", status="done", error=None)

    except Exception as e:
        logger.error(f"Upload orchestrator failed for {call_id}: {e}", exc_info=True)
        _set_job(db, call_id, stage="failed", status="failed", error=str(e)[:500])
    finally:
        db.close()


def recover_stuck_jobs() -> int:
    """
    Startup crash-recovery: re-dispatch any pipeline job left in queued/running by a
    crash/restart/deploy (under its retry cap). This is what makes the local pipeline
    durable without losing calls. On AWS, a worker consumes this same table instead.
    """
    import threading
    from app.database import SessionLocal
    from app.models import ProcessingJob

    db = SessionLocal()
    try:
        # Include 'failed' (under the retry cap) so a transient failure is retried on restart,
        # not stranded forever. The orchestrator is idempotent, so completed work is never redone.
        stuck = db.query(ProcessingJob).filter(
            ProcessingJob.status.in_(["queued", "running", "failed"]),
            ProcessingJob.attempts < ProcessingJob.max_attempts,
        ).all()
        jobs = [(j.call_id, j.audio_path) for j in stuck if j.audio_path]
    finally:
        db.close()

    for call_id, audio_path in jobs:
        logger.info(f"Recovering stuck pipeline job: {call_id}")
        threading.Thread(target=_process_uploaded_recording, args=(call_id, audio_path), daemon=True).start()
    return len(jobs)


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_recording(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """
    Outbound flow: upload a previous call recording. We store it, create the call
    record, and kick off transcribe -> analyse -> memory in the background.
    Returns immediately with a call_id; the app polls /{call_id}/processing-status.

    BACKEND: pair this with your Lead row — pass `phone` as the contact key and
    link the returned call_id to the lead.
    """
    import uuid as _uuid
    from app.models import Lead
    from app.utils.s3 import get_storage_manager
    from app.utils.memory_bubble import slugify_contact

    manager = get_storage_manager()

    # Build a stable, readable call_id (mirrors import_audio convention)
    slug = slugify_contact(name or "lead")
    call_id = f"call_{slug}_{_uuid.uuid4().hex[:8]}"

    # Upsert a Lead row so this contact shows in the inbox with its details
    lead = db.query(Lead).filter(Lead.contact_key == slug).first()
    if not lead:
        lead = Lead(id=str(_uuid.uuid4()), contact_key=slug, name=name, phone=phone,
                    source=source, status="contacted")
        db.add(lead)
    else:
        lead.phone = phone or lead.phone
        lead.source = source or lead.source
        lead.status = "contacted"
    db.commit()

    # Persist the upload to a temp file, then into local storage
    suffix = os.path.splitext(file.filename or "")[1] or ".mp3"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await file.read())
        tmp.close()
        audio_url = manager.save_audio_file(tmp.name, call_id)
        if not audio_url:
            raise HTTPException(status_code=500, detail="Failed to store audio file")

        # Create the call record up front (transcript fills in via background task)
        call = AudioCall(
            call_id=call_id,
            timestamp=datetime.utcnow(),
            transcript={"turns": []},
            audio_file_url=audio_url,
        )
        db.add(call)
        db.commit()

        # Resolve the path for transcription. For Supabase, the just-uploaded temp file
        # (tmp.name) is already local — use it directly for THIS run instead of round-tripping
        # a fresh download. But the job row must persist the durable `supabase://` reference
        # (not the temp path) so crash recovery can re-resolve a fresh local copy later, since
        # tmp.name isn't guaranteed to survive a process restart. See _process_uploaded_recording,
        # which re-resolves a `supabase://` audio_path back to a local file on every invocation.
        if settings.storage_mode == "supabase":
            stored_path = tmp.name
            recovery_path = audio_url
        else:
            stored_path = manager.get_audio_file_path(call_id) or tmp.name
            recovery_path = stored_path
        _set_job(db, call_id, stage="queued", status="queued", audio_path=recovery_path)
        background_tasks.add_task(_process_uploaded_recording, call_id, stored_path)

        return {
            "call_id": call_id,
            "status": "processing",
            "name": name,
            "phone": phone,
            "source": source,
            "message": "Recording received. Transcription and AI analysis started.",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@intel_router.get("/telecaller/score", status_code=status.HTTP_200_OK)
async def get_telecaller_score(window_days: int = 7, db: Session = Depends(get_db)):
    """
    Telecaller rolling performance + trend (the Score tab numbers).
    BACKEND: filter by agent_id once calls carry an agent. Today: all calls.
    """
    from app.utils.lead_intelligence import telecaller_score

    window_days = max(1, window_days)  # 0/negative would silently mean "all time"
    grouped = _all_analyses_by_contact(db)
    calls: List[Dict[str, Any]] = []
    for analyses in grouped.values():
        calls.extend(analyses)
    return telecaller_score(calls, window_days=window_days)


@intel_router.post("/translate", status_code=status.HTTP_200_OK)
async def translate_texts(payload: Dict[str, Any]):
    """
    Translate a batch of UI strings → powers the Score / AI-Summary "View English" toggle.
    Body: {"texts": ["...", ...], "target": "en"}. Returns {"texts": [...]} index-aligned.
    """
    from app.utils.translation import translate_strings
    texts = payload.get("texts") or []
    target = payload.get("target") or "en"
    if not isinstance(texts, list) or not texts:
        return {"texts": []}
    translated = await asyncio.to_thread(translate_strings, [str(t) for t in texts], target)
    return {"texts": translated}
