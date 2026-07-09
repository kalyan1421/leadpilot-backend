"""API endpoints for managing audio calls."""

import logging
import os
import re
import tempfile
import asyncio
from datetime import datetime, timedelta
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
from app.api.auth import get_current_user as _get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calls", tags=["calls"])

# Aggregate / cross-call intelligence lives on its own prefixes so it never
# collides with the dynamic /api/calls/{call_id} route.
intel_router = APIRouter(prefix="/api", tags=["intelligence"])


def _org_context(db: Session, org_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Builds the Organisation Knowledge Base dict the analyzer grounds scoring/
    relevance-filtering in. Returns None for calls with no org_id yet (Flutter
    doesn't send a bearer token yet, so most calls today still land unscoped)."""
    if not org_id:
        return None
    from app.models import Organization

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        return None
    return {
        "name": org.name,
        "industry": org.industry,
        "services": org.services,
        "target_audience": org.target_audience,
        "brand_voice": org.brand_voice,
        "usps": org.usps,
        # Previously collected/stored but never forwarded to the analyzer —
        # threaded through so pricing/competitor/language grounding actually
        # reaches scoring and follow-up suggestions.
        "website_url": org.website_url,
        "pricing_min": org.pricing_min,
        "pricing_max": org.pricing_max,
        "competitors": org.competitors,
        "languages": org.languages,
    }








@router.get("/count")
async def get_calls_count(
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Get total count of calls for the caller's org.

    Org-scoped (see get_inbox pattern) — previously counted across all orgs.

    Args:
        db: Database session

    Returns:
        Total count of calls
    """
    count = db.query(AudioCall).filter(AudioCall.org_id == current_user.org_id).count()
    return {"total": count}


@router.get("/{call_id}", response_model=AudioCallResponse)
async def get_call(
    call_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Get call information by ID.

    Org-scoped (see get_inbox pattern) — previously had no auth or org_id
    filter at all, letting any caller read another org's call by guessing
    call_id.

    Args:
        call_id: Unique identifier for the call
        db: Database session

    Returns:
        Call information
    """
    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call with ID {call_id} not found"
        )
    return call


@router.get("/{call_id}/audio")
async def download_audio(
    call_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Download audio file for a specific call.

    Supports local, Supabase, and S3 storage modes.

    Org-scoped (see get_inbox pattern) — previously had no auth or org_id
    filter, letting any caller download another org's call recording by
    guessing call_id.
    """
    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
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
async def get_transcript(
    call_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Get transcript JSON for a specific call.

    Org-scoped (see get_inbox pattern) — previously had no auth or org_id
    filter, letting any caller read another org's call transcript by
    guessing call_id.

    Args:
        call_id: Unique identifier for the call
        db: Database session

    Returns:
        Transcript JSON data
    """
    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
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
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    List calls with pagination.

    Org-scoped (see get_inbox pattern) — previously returned every org's
    calls with no filter at all.

    Args:
        skip: Number of records to skip
        limit: Maximum number of records to return
        db: Database session

    Returns:
        List of call records
    """
    # Order by created_at descending (newest first) and apply pagination
    calls = (
        db.query(AudioCall)
        .filter(AudioCall.org_id == current_user.org_id)
        .order_by(AudioCall.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return calls


@router.put("/{call_id}", response_model=AudioCallResponse)
async def update_call(
    call_id: str,
    call_update: AudioCallUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Update an existing call record.

    Org-scoped (see get_inbox pattern) — previously had no auth or org_id
    filter, letting any caller edit another org's call by guessing call_id.

    Args:
        call_id: Unique identifier for the call
        call_update: Updated call information
        db: Database session

    Returns:
        Updated call record
    """
    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
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
async def delete_call(
    call_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Delete a call record.

    Org-scoped (see get_inbox pattern) — previously had no auth or org_id
    filter, letting any caller delete another org's call by guessing call_id.

    Args:
        call_id: Unique identifier for the call
        db: Database session
    """
    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
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
async def get_lead_analysis(
    call_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """Return stored lead analysis for a call (BANT, verdict, sentiment arc, next action).

    Org-scoped (see get_inbox pattern) — previously had no auth or org_id filter.
    Ownership is checked via the AudioCall row, not LeadAnalysis.org_id directly:
    the upload pipeline's background job doesn't stamp org_id on LeadAnalysis
    (only the manual re-run endpoint does), so a huge share of real analyses
    have org_id=NULL — filtering on that column directly 404'd legitimate
    same-org requests. AudioCall.org_id is always reliable."""
    from app.models import LeadAnalysis
    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
    if not call:
        raise HTTPException(status_code=404, detail=f"No lead analysis found for call {call_id}")
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
        "relevance_reason": record.relevance_reason,
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
    current_user=Depends(_get_current_user),
):
    """
    Telecaller correction to a call's analysis (key points today). Does NOT
    trigger a memory-bubble rebuild — a manual correction shouldn't get
    silently overwritten or cascade into memory the next time analysis reruns.

    Org-scoped (see get_inbox pattern) — previously had no auth or org_id filter.
    Ownership is checked via AudioCall, not LeadAnalysis.org_id — see the
    matching comment in get_lead_analysis for why.
    """
    from app.models import LeadAnalysis

    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
    if not call:
        raise HTTPException(status_code=404, detail=f"No lead analysis found for call {call_id}")
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
async def run_lead_analysis(
    call_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """Run (or re-run) full lead analysis for a call and persist the result.

    Org-scoped (see get_inbox pattern) — previously had no auth or org_id filter,
    letting any caller trigger analysis on (and read the result of) another
    org's call by guessing call_id."""
    import asyncio
    import uuid as _uuid
    from app.models import LeadAnalysis
    from app.utils.lead_analyzer import analyze_call

    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
    if not call:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")

    # Upsert record. call_id lookup alone is safe here (not re-filtered by org_id)
    # because `call` above was already fetched scoped to current_user.org_id.
    record = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
    if not record:
        record = LeadAnalysis(id=str(_uuid.uuid4()), call_id=call_id, org_id=current_user.org_id)
        db.add(record)
    elif not record.org_id:
        record.org_id = current_user.org_id
    record.status = "processing"
    db.commit()

    try:
        # Run blocking sync analyzer in thread pool so we don't block the event loop
        transcript = call.transcript or {"turns": []}
        org_context = _org_context(db, call.org_id)
        result = await asyncio.to_thread(analyze_call, transcript, org_context)

        if result is None:
            record.status = "failed"
            record.error = "Analyzer returned None — check logs for API error"
            db.commit()
            raise HTTPException(status_code=500, detail="Lead analysis failed — check server logs")

        record.bant_score = result.get("bant_score")
        record.bant_breakdown = result.get("bant_breakdown")
        record.lead_verdict = result.get("lead_verdict")
        record.lead_verdict_reason = result.get("lead_verdict_reason")
        record.relevance_reason = result.get("relevance_reason")
        record.sentiment_arc = result.get("sentiment_arc")
        record.intent_tags = result.get("intent_tags")
        record.entities = result.get("entities")
        record.call_summary = result.get("call_summary")
        record.key_points = result.get("key_points")
        record.next_steps = result.get("next_steps")
        record.next_action = result.get("next_action")
        record.agent_debrief = result.get("agent_debrief")
        record.status = "completed" if result.get("is_relevant", True) else "not_relevant"
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
async def get_call_score(
    call_id: str,
    window_days: int = 7,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Consolidated Score-tab payload — everything the Figma Call Detail "Score"
    screen needs in ONE request, so the frontend never computes a score itself:

      - call_score          : the hero ring (composite of the three per-call rings)
      - rings               : Overall / Telecaller / Lead Quality / Sentiment,
                              each {value, max, trend}  (trend = delta vs previous call)
      - breakdown           : the 5 dimensions, each {score, max, note}
      - sentiment_timeline  : the colored bar segments + a one-line caption

    Requires a completed lead-analysis (run POST .../lead-analysis first).

    Org-scoped (see get_inbox pattern) — previously had no auth dependency at
    all; the call row's own org_id wasn't enough because nothing checked it
    against the caller's identity, so any guessable call_id leaked another
    org's score payload.
    """
    window_days = max(1, window_days)  # 0/negative would silently mean "all time"
    from app.models import LeadAnalysis
    from app.utils.memory_bubble import contact_key_from_call_id
    from app.utils.lead_intelligence import (
        sentiment_score, sentiment_timeline, call_score, score_trend,
        telecaller_score, mmss_to_seconds,
    )

    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
    if not call:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")

    la = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
    # "not_relevant" is a terminal state alongside "completed" (see get_lead_detail's
    # include_statuses docstring above, and get_processing_status) — the analyzer still
    # populates bant_score/agent_debrief/sentiment_arc for a not-relevant call (see
    # run_lead_analysis, which sets these fields before branching on is_relevant), so
    # the Score tab has real data to show even when the call wasn't a qualifying lead.
    # "failed" is included alongside completed/not_relevant: the pipeline now
    # persists a fully-shaped all-zero debrief on analysis failure (see
    # _process_uploaded_recording), so the Score tab can render all 6 dimensions
    # greyed at 0 with an error banner instead of 404-ing on a blank tab.
    if not la or la.status not in ("completed", "not_relevant", "failed"):
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
    # Org-scoped via the already-fetched (and now auth-checked) call's own org_id.
    grouped = _all_analyses_by_contact(db, call.org_id)
    all_calls = [a for analyses in grouped.values() for a in analyses]
    # The telecaller ring is THIS call's telecaller's rolling score, not the whole
    # org's — filter the shared scan by telecaller_id (carried on each grouped row)
    # so no second query is needed. Falls back to org-wide only for legacy calls
    # with no telecaller_id stamped.
    tele_calls = (
        [a for a in all_calls if a.get("telecaller_id") == call.telecaller_id]
        if call.telecaller_id else all_calls
    )
    tele = telecaller_score(tele_calls, window_days=window_days)

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
        ("opening", "Opening", 20),
        ("discovery", "Discovery", 20),
        ("pitch", "Pitch", 20),
        ("objection_handling", "Objection Handling", 20),
        ("closing", "Closing", 20),
        # Additive dimension (see lead_analyzer.py) — smaller scale to match the
        # PRD's smaller weight; doesn't change what the existing 5 dims mean.
        ("punctuality", "Punctuality", 10),
    ]
    statuses = _dimension_status()  # validated | beta | hidden  (gold-set gate)
    breakdown = [
        {
            "key": key,
            "label": label,
            "score": debrief.get(f"{key}_score") or 0,
            "max": max_score,
            "note": debrief.get(f"{key}_note") or "",
            "evidence": debrief.get(f"{key}_evidence") or [],  # [{turn,t,speaker,text}] — auditable quote
            "status": statuses.get(key, "beta"),
        }
        for key, label, max_score in DIMS
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
        "relevance_reason": la.relevance_reason,
        "transcript_quality": (transcript.get("quality") if isinstance(transcript, dict) else None) or "ok",
        "breakdown": breakdown,
        "script_compliance": debrief.get("script_compliance") or [],
        "strengths": debrief.get("strengths") or [],
        "improvements": debrief.get("improvements") or [],
        "sentiment_timeline": timeline,
        # 'failed' here means the numbers are the zeroed placeholder (analysis
        # errored) — the app renders the greyed bars plus a retry banner.
        "analysis_status": la.status,
        "analysis_error": la.error,
    }


@router.post("/{call_id}/chat", status_code=status.HTTP_200_OK)
async def chat_with_call(
    call_id: str,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    "Chat with this call" — free-form Q&A grounded in one call's transcript +
    AI analysis. Body: {"question": str}. No new provider integration: reuses
    sarvam_chat (app/utils/sarvam.py) with a context block built from data
    already stored, same as the rest of this file's AI calls.

    Org-scoped (see get_inbox pattern).
    """
    from app.models import LeadAnalysis
    from app.utils.sarvam import sarvam_chat

    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="question is required")

    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
    if not call:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")

    transcript = call.transcript or {}
    turns = transcript.get("turns", []) if isinstance(transcript, dict) else []
    if not turns:
        raise HTTPException(status_code=404, detail="No transcript available for this call yet")

    transcript_text = "\n".join(
        f"Turn {i} [{t.get('role', 'USER')} @ {t.get('timestamp', '?')}]: {t.get('content', '')}"
        for i, t in enumerate(turns, 1)
    )

    la = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
    analysis_text = "No AI analysis available for this call yet."
    if la and la.status in ("completed", "not_relevant"):
        summary = la.call_summary or {}
        debrief = la.agent_debrief or {}
        analysis_text = (
            f"Verdict: {la.lead_verdict or 'unknown'}\n"
            f"Summary: {summary.get('headline', '')}\n"
            f"Key points: {', '.join(la.key_points or [])}\n"
            f"Objections raised: {', '.join(summary.get('objections_raised') or [])}\n"
            f"Telecaller strengths: {', '.join(debrief.get('strengths') or [])}\n"
            f"Telecaller improvements: {', '.join(debrief.get('improvements') or [])}"
        )

    system_prompt = (
        "You are answering questions about ONE specific sales call for a telecalling team. "
        "Answer ONLY from the transcript and analysis provided below — if the answer isn't "
        "in there, say so plainly rather than guessing. Keep answers concise (2-4 sentences "
        "unless the question needs a list).\n\n"
        f"TRANSCRIPT:\n{transcript_text}\n\nAI ANALYSIS:\n{analysis_text}"
    )

    answer = await asyncio.to_thread(
        sarvam_chat, [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    )
    return {"call_id": call_id, "question": question, "answer": answer}


# ---------------------------------------------------------------------------
# Memory Bubble endpoints  (per-contact cumulative memory — the moat)
# ---------------------------------------------------------------------------

def _gather_contact_calls(contact_key: str, db: Session, org_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Collect every call belonging to a contact, oldest first, with its lead_analysis.

    TODAY: groups by name-slug derived from call_id (no phone field in dataset).
    BACKEND: replace with `WHERE lead.phone = contact_key` join.

    org_id: when given, scopes to AudioCall.org_id so contact_key collisions
    across orgs (contact_key is only unique WITHIN an org — see
    uq_memory_bubbles_org_contact_key) don't merge two different orgs' calls
    into one contact's history. Optional because not every caller has an
    authenticated org_id yet (mirrors _all_analyses_by_contact's org_id param).
    """
    from app.models import LeadAnalysis
    from app.utils.memory_bubble import contact_key_from_call_id

    # Single join instead of N+1 (full AudioCall scan + per-call LeadAnalysis lookup).
    # "not_relevant" counts as a terminal, analysed state alongside "completed"
    # (same set used by the Score tab, inbox and dashboard — see get_lead_detail's
    # include_statuses and get_processing_status). Excluding it meant a contact
    # whose in-org calls were all judged off-topic had an *empty* history, so the
    # rebuild endpoint 404'd even though real analysed calls existed.
    query = (
        db.query(AudioCall, LeadAnalysis)
        .join(LeadAnalysis, LeadAnalysis.call_id == AudioCall.call_id)
        .filter(LeadAnalysis.status.in_(("completed", "not_relevant")))
    )
    if org_id is not None:
        query = query.filter(AudioCall.org_id == org_id)
    rows = query.order_by(AudioCall.timestamp.asc()).all()
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
    """Rebuild the memory bubble for whichever contact this call belongs to.

    org_id is derived from the call's own AudioCall.org_id (there's no
    request/current_user context in this internal helper — it's invoked from
    the lead-analysis pipeline, not directly from a request handler).
    """
    import asyncio
    import uuid as _uuid
    from app.models import MemoryBubble
    from app.utils.memory_bubble import build_memory_bubble, contact_key_from_call_id

    call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
    org_id = call.org_id if call else None

    contact_key = contact_key_from_call_id(call_id)
    calls = _gather_contact_calls(contact_key, db, org_id)
    if not calls:
        return None

    bubble = await asyncio.to_thread(build_memory_bubble, contact_key, calls)
    if not bubble:
        return None

    record = (
        db.query(MemoryBubble)
        .filter(MemoryBubble.contact_key == contact_key, MemoryBubble.org_id == org_id)
        .first()
    )
    if not record:
        record = MemoryBubble(id=str(_uuid.uuid4()), contact_key=contact_key, org_id=org_id)
        db.add(record)

    record.total_calls = bubble.get("total_calls", len(calls))
    record.last_call_id = bubble.get("last_call_id")
    record.last_call_at = _parse_bubble_last_call_at(bubble)
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


def _parse_bubble_last_call_at(bubble: Dict[str, Any]) -> Optional[datetime]:
    """The builder emits last_call_at as an ISO string (AudioCall.timestamp
    .isoformat()), but MemoryBubble.last_call_at is a DateTime column — parse
    it back so the field actually persists instead of silently staying NULL."""
    raw = bubble.get("last_call_at")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _serialize_bubble(record) -> Dict[str, Any]:
    return {
        "contact_key": record.contact_key,
        "total_calls": record.total_calls,
        "last_call_id": record.last_call_id,
        "last_call_at": record.last_call_at.isoformat() if record.last_call_at else None,
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
async def get_memory_bubble(
    contact_key: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Return the stored memory bubble for a contact (phone number in production).

    Auth required (see get_inbox pattern) — org_id unconditionally scopes the
    lookup so a memory bubble can never be read across org boundaries. This
    route has no live caller in the Flutter or web apps today, so hardening it
    carries no client-breakage risk.
    """
    from app.models import MemoryBubble
    record = (
        db.query(MemoryBubble)
        .filter(MemoryBubble.contact_key == contact_key, MemoryBubble.org_id == current_user.org_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail=f"No memory bubble for contact {contact_key}")
    return _serialize_bubble(record)


@router.get("/{call_id}/memory", status_code=status.HTTP_200_OK)
async def get_memory_bubble_for_call(
    call_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Return a contact's memory bubble addressed by one of their call_ids, so a
    caller that only knows a call_id (e.g. the founder dashboard's Call Detail
    page) doesn't have to re-derive the contact_key slug itself.

    Org-scoped via the call's own AudioCall row (see get_lead_analysis for why
    ownership is checked through AudioCall, not the bubble's org_id).
    """
    from app.models import MemoryBubble
    from app.utils.memory_bubble import contact_key_from_call_id

    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
    if not call:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")

    contact_key = contact_key_from_call_id(call_id)
    record = (
        db.query(MemoryBubble)
        .filter(MemoryBubble.contact_key == contact_key, MemoryBubble.org_id == current_user.org_id)
        .first()
    )
    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No memory bubble for contact {contact_key}",
        )
    return _serialize_bubble(record)


async def _rebuild_and_persist_bubble(contact_key: str, org_id: Optional[str], db: Session) -> Dict[str, Any]:
    """Shared core for the two rebuild routes (contact-keyed and call-keyed):
    gather the contact's analysed calls, rebuild the bubble, upsert it, and
    return the serialized record. Raises 404 when there's nothing to build."""
    import asyncio
    import uuid as _uuid
    from app.models import MemoryBubble
    from app.utils.memory_bubble import build_memory_bubble

    calls = _gather_contact_calls(contact_key, db, org_id)
    if not calls:
        raise HTTPException(status_code=404, detail=f"No analysed calls found for contact {contact_key}")

    bubble = await asyncio.to_thread(build_memory_bubble, contact_key, calls)
    if not bubble:
        raise HTTPException(status_code=500, detail="Memory bubble build failed — check server logs")

    record = (
        db.query(MemoryBubble)
        .filter(MemoryBubble.contact_key == contact_key, MemoryBubble.org_id == org_id)
        .first()
    )
    if not record:
        record = MemoryBubble(id=str(_uuid.uuid4()), contact_key=contact_key, org_id=org_id)
        db.add(record)
    record.total_calls = bubble.get("total_calls", len(calls))
    record.last_call_id = bubble.get("last_call_id")
    record.last_call_at = _parse_bubble_last_call_at(bubble)
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


@router.post("/{call_id}/memory/rebuild", status_code=status.HTTP_200_OK)
async def rebuild_memory_bubble_for_call(
    call_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """Rebuild a contact's memory bubble addressed by one of their call_ids —
    the call-keyed twin of GET /{call_id}/memory, so the founder dashboard's
    Call Detail page can trigger a rebuild without knowing the contact_key.

    Org-scoped via the call's own AudioCall row (see get_memory_bubble_for_call).
    """
    from app.utils.memory_bubble import contact_key_from_call_id

    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
    if not call:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")
    contact_key = contact_key_from_call_id(call_id)
    return await _rebuild_and_persist_bubble(contact_key, current_user.org_id, db)


@intel_router.post("/memory/{contact_key}/rebuild", status_code=status.HTTP_200_OK)
async def rebuild_memory_bubble(
    contact_key: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Force-rebuild a contact's memory bubble from all their analysed calls.

    Auth required (see get_inbox pattern) — org_id unconditionally scopes both
    the call-gathering and the bubble upsert.
    """
    return await _rebuild_and_persist_bubble(contact_key, current_user.org_id, db)


# ---------------------------------------------------------------------------
# Inbox + Telecaller intelligence (deterministic, no LLM)
# ---------------------------------------------------------------------------

def _all_analyses_by_contact(
    db: Session,
    org_id: Optional[str] = None,
    include_statuses: tuple = ("completed",),
    telecaller_id: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group lead_analysis rows by contact, oldest first.

    org_id: when given, scopes the underlying query to that org only (via
    AudioCall.org_id — AudioCall carries org_id directly, no join to Lead
    needed). Left optional (default None = unscoped) because get_call_score
    still calls this unscoped (it has no auth dependency — the call row itself
    pins it to a single org/contact, see get_call_score's own docstring).
    get_inbox, get_lead_detail, and get_telecaller_score now all require auth
    and pass current_user.org_id.

    include_statuses: defaults to ("completed",) only — scoring/inbox/dashboard
    consumers want irrelevant calls excluded from aggregates. get_lead_detail
    passes ("completed", "not_relevant") instead: a call the AI analyzer
    scored as not lead-relevant (wrong number, no real conversation, etc.)
    still genuinely happened and was fully processed — it must still show up
    in that lead's call history, just without contributing to scoring. Before
    this, such a call vanished from history entirely with no error shown to
    the telecaller, who'd only see it in the upload sheet's one-time preview.

    Since contact_key is only unique WITHIN an org (see
    uq_leads_org_contact_key on Lead), grouping by contact_key alone COULD
    merge two different orgs' calls under one key if this function were ever
    called across multiple orgs in a loop — but it isn't; each call scopes to
    at most one org_id (or none), so the grouping dict built here is always
    safe.
    """
    from app.models import LeadAnalysis
    from app.utils.memory_bubble import contact_key_from_call_id
    from app.utils.lead_intelligence import sentiment_score

    query = (
        db.query(LeadAnalysis, AudioCall)
        .join(AudioCall, LeadAnalysis.call_id == AudioCall.call_id)
        .filter(LeadAnalysis.status.in_(include_statuses))
    )
    if org_id is not None:
        query = query.filter(AudioCall.org_id == org_id)
    # telecaller_id: scope to ONE telecaller's own calls. Used by the mobile
    # Score tab so a telecaller sees their OWN rolling performance, not the whole
    # org's (the founder dashboard already slices per-telecaller; this makes the
    # mobile side consistent). Each grouped row also carries telecaller_id so a
    # caller that wants the org-wide grouping can still filter in Python without
    # a second query (see get_call_score's telecaller ring).
    if telecaller_id is not None:
        query = query.filter(AudioCall.telecaller_id == telecaller_id)
    rows = query.order_by(AudioCall.timestamp.asc()).all()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for la, call in rows:
        key = contact_key_from_call_id(call.call_id)
        grouped.setdefault(key, []).append({
            "call_id": call.call_id,
            "telecaller_id": call.telecaller_id,
            "timestamp": call.timestamp.isoformat() if call.timestamp else None,
            "status": la.status,
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
async def get_inbox(
    bucket: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Lead inbox: one card per contact with score, intent bucket, tags + header stats.
    Optional ?bucket=high_intent|new|follow_up|cold filter (the Figma chips).

    Org-scoped: a telecaller only ever sees their own org's leads (previously
    this queried across all orgs — a cross-tenant data leak).
    """
    from app.models import Lead
    from app.utils.lead_intelligence import lead_card, inbox_header

    grouped = _all_analyses_by_contact(db, current_user.org_id)
    leads = db.query(Lead).filter(Lead.org_id == current_user.org_id).all()

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
async def get_processing_status(
    call_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Unified processing stepper state (Figma: Upload -> Transcribe -> Analyse -> Done).

    Computed live from what actually exists in the DB, so the mobile app can poll
    this one endpoint to render the 4-step progress bar after a recording upload.

    Org-scoped (see get_inbox pattern) — previously had no auth or org_id filter.
    """
    from app.models import LeadAnalysis

    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
    if not call:
        raise HTTPException(status_code=404, detail=f"Call {call_id} not found")

    transcript = call.transcript or {}
    has_turns = bool(isinstance(transcript, dict) and transcript.get("turns"))
    transcribe_failed = bool(isinstance(transcript, dict) and transcript.get("error") and not has_turns)
    la = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
    # "not_relevant" is a terminal state alongside "completed" (see get_lead_detail's
    # include_statuses docstring above) — a call the AI analyzer decided wasn't
    # lead-relevant still finished processing. Without this, such calls never
    # reached "done" here and the app polled until its 5-minute client timeout.
    analysed = bool(la and la.status in ("completed", "not_relevant"))
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
async def get_translated_transcript(
    call_id: str,
    target: str = "en",
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Return the transcript translated to `target` (default English) — powers the
    Transcript tab "View English" toggle. Detects source language automatically.

    Org-scoped (see get_inbox pattern) — previously had no auth or org_id filter.
    """
    import asyncio
    from app.utils.translation import translate_turns, detect_language, SUPPORTED_LANGS

    call = (
        db.query(AudioCall)
        .filter(AudioCall.call_id == call_id, AudioCall.org_id == current_user.org_id)
        .first()
    )
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
async def dedupe_lead(
    phone: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Duplicate check for the Add Outbound Lead screen ("already in your leads").
    Matches on normalised phone digits. Registered BEFORE /leads/{contact_key}
    so 'dedupe' is not captured as a contact key.

    Auth required (see get_inbox pattern) — org_id unconditionally scopes the
    match. There is no evidence anywhere in this codebase that cross-org phone
    matching here was an intentional fraud/spam-detection feature (no such
    comment, docstring, or product doc found) — this was the same
    org-unscoped-by-oversight bug as Lead/MemoryBubble, so it's scoped like
    every other fixed endpoint rather than left alone. This route also has no
    live caller in the Flutter or web apps today, so hardening it carries no
    client-breakage risk.
    """
    from app.models import Lead
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return {"duplicate": False}
    query = db.query(Lead).filter(Lead.phone.isnot(None), Lead.org_id == current_user.org_id)
    for lead in query.all():
        if re.sub(r"\D", "", lead.phone or "")[-10:] == digits[-10:] and digits[-10:]:
            return {"duplicate": True, "contact_key": lead.contact_key, "name": lead.name}
    return {"duplicate": False}


@intel_router.post("/leads", status_code=status.HTTP_201_CREATED)
async def create_lead(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Create a lead (the 'Save Lead' action). Appears in the inbox immediately as
    'New'. If a lead with the same contact_key exists (within the same org),
    returns it (idempotent).

    Auth required (see get_inbox pattern) — org_id/telecaller_id are always
    stamped from the caller's own identity. Both the Flutter app (via
    HttpApiClient, which attaches a bearer token whenever the user is logged
    in) and the web app (via authedRequest, which refuses to fire without a
    token) already call this route only when authenticated.
    """
    import uuid as _uuid
    from sqlalchemy.exc import IntegrityError
    from app.models import Lead
    from app.utils.memory_bubble import slugify_contact

    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    org_id = current_user.org_id
    telecaller_id = current_user.id

    contact_key = slugify_contact(name)
    existing = (
        db.query(Lead)
        .filter(Lead.contact_key == contact_key, Lead.org_id == org_id)
        .first()
    )
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
        org_id=org_id,
        assigned_to=telecaller_id,
    )
    db.add(lead)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Two "Save Lead" requests for the same (org_id, contact_key) can both
        # pass the existence check above and race to INSERT; the loser hits the
        # uq_leads_org_contact_key unique constraint. Re-query and return the row
        # that won, keeping the endpoint idempotent as documented.
        existing = (
            db.query(Lead)
            .filter(Lead.contact_key == contact_key, Lead.org_id == org_id)
            .first()
        )
        if existing:
            return {"contact_key": existing.contact_key, "name": existing.name, "status": existing.status, "created": False}
        # Not a duplicate — most likely a stale org_id/assigned_to foreign key
        # (e.g. the caller's org/user row was dropped in a DB reseed). Log the
        # real DB error and surface a clear 409 instead of an opaque 500.
        logger.error(
            "create_lead IntegrityError (org=%s assigned_to=%s contact_key=%s): %s",
            org_id, telecaller_id, contact_key, getattr(exc, "orig", exc),
        )
        raise HTTPException(
            status_code=409,
            detail="Could not save lead due to a data integrity error. Your account may be out of sync with the database — log out and back in, and if it persists the org/user records need reseeding.",
        )
    return {"contact_key": contact_key, "name": name, "status": "new", "created": True}


@intel_router.get("/leads/{contact_key}", status_code=status.HTTP_200_OK)
async def get_lead_detail(
    contact_key: str,
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Everything the Lead Detail screen needs in one call:
    card summary + memory bubble + call history. Works even for a 'New' lead
    that has no calls yet.

    Auth required (see get_inbox pattern) — org_id unconditionally scopes the
    lookup, closing the cross-org contact_key-collision risk that existed
    while this endpoint had an optional-auth fallback. Both the Flutter app
    (HttpApiClient, real login flow) and the web app (authedRequest) already
    call this only when authenticated.
    """
    from app.models import MemoryBubble, Lead
    from app.utils.lead_intelligence import lead_card

    org_id = current_user.org_id
    # Card summary (lead score/verdict/tags) AND call history both include
    # not_relevant calls now: the analyzer scores every dimension on its own
    # merits even for off-topic calls (a genuine wrong number simply scores low
    # on lead quality rather than being blanked), so those scores are real and
    # should count toward the lead's headline — not be dropped as noise. One
    # query serves both. (This widening is scoped to this endpoint; inbox,
    # dashboard and the scoring rings keep _all_analyses_by_contact's
    # "completed"-only default.)
    history_analyses = _all_analyses_by_contact(
        db, org_id, include_statuses=("completed", "not_relevant")
    ).get(contact_key, [])
    analyses = history_analyses
    lead_row = (
        db.query(Lead)
        .filter(Lead.contact_key == contact_key, Lead.org_id == org_id)
        .first()
    )

    if not history_analyses and not lead_row:
        raise HTTPException(status_code=404, detail=f"No lead or calls for contact {contact_key}")

    display_name = (lead_row.name if lead_row else None) or contact_key.replace("_", " ").title()
    card = lead_card(
        contact_key, analyses,
        name=display_name,
        source=lead_row.source if lead_row else None,
        lead_status=lead_row.status if lead_row else None,
    )

    bubble_row = (
        db.query(MemoryBubble)
        .filter(MemoryBubble.contact_key == contact_key, MemoryBubble.org_id == org_id)
        .first()
    )
    memory = _serialize_bubble(bubble_row) if bubble_row else None

    calls = [
        {
            "call_id": a["call_id"],
            "timestamp": a["timestamp"],
            "score": a.get("agent_total_score"),
            "bant_score": a.get("bant_score"),
            "lead_verdict": a.get("lead_verdict"),
            "analysis_status": a.get("status"),
        }
        for a in reversed(history_analyses)
    ]
    return {
        **card,
        "phone": lead_row.phone if lead_row else None,
        "reason": lead_row.reason if lead_row else None,
        "status": lead_row.status if lead_row else None,
        # Authoritative kanban stage so the mobile app can READ BACK a stage moved
        # on the web dashboard / another device, not just push its own local one.
        "pipeline_stage": lead_row.pipeline_stage if lead_row else None,
        "memory": memory,
        "calls": calls,
    }


# ---------------------------------------------------------------------------
# Outbound recording upload  ->  transcribe -> analyse -> memory  (AI pipeline)
# ---------------------------------------------------------------------------

def _build_and_store_memory(contact_key: str, db: Session, org_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Synchronous memory build + upsert (used by the background orchestrator).

    org_id is passed in by the caller (derived from the triggering call's own
    AudioCall.org_id) since this internal helper has no request/current_user
    context of its own.
    """
    import uuid as _uuid
    from app.models import MemoryBubble
    from app.utils.memory_bubble import build_memory_bubble

    calls = _gather_contact_calls(contact_key, db, org_id)
    if not calls:
        return None
    bubble = build_memory_bubble(contact_key, calls)
    if not bubble:
        return None
    rec = (
        db.query(MemoryBubble)
        .filter(MemoryBubble.contact_key == contact_key, MemoryBubble.org_id == org_id)
        .first()
    )
    if not rec:
        rec = MemoryBubble(id=str(_uuid.uuid4()), contact_key=contact_key, org_id=org_id)
        db.add(rec)
    rec.total_calls = bubble.get("total_calls", len(calls))
    rec.last_call_id = bubble.get("last_call_id")
    rec.last_call_at = _parse_bubble_last_call_at(bubble)
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
        # Retry a transient STT failure (Sarvam 5xx surfaces as turns=[] + an error
        # string) a couple times with backoff before giving up — a network blip
        # shouldn't cost the whole recording its analysis. A genuinely silent/empty
        # recording returns no error, so it isn't needlessly retried.
        import time as _time
        result = transcribe_audio(local_audio_path, language=None)
        for _attempt in range(2):
            if result.get("turns") or not result.get("error"):
                break
            logger.warning(f"Upload {call_id}: transient STT failure "
                           f"(attempt {_attempt + 1}): {str(result.get('error'))[:120]} — retrying")
            _time.sleep(2.0 * (_attempt + 1))
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
            rec = LeadAnalysis(id=str(_uuid.uuid4()), call_id=call_id, org_id=call.org_id)
            db.add(rec)
        elif not rec.org_id:
            rec.org_id = call.org_id
        rec.status = "processing"
        db.commit()
        _set_job(db, call_id, stage="analyse", status="running")

        org_context = _org_context(db, call.org_id)
        analysis = analyze_call(call.transcript, org_context)
        analysis_failed = not analysis
        if analysis_failed:
            # Persist a fully-shaped, all-zero analysis (not NULL fields) so the
            # Score tab still renders all 6 dimensions greyed at 0 with an error
            # banner, instead of 404-ing or showing a blank screen. Status stays
            # 'failed' + error set, and startup recovery can still retry it.
            from app.utils.lead_analyzer import empty_analysis
            analysis = empty_analysis("Automatic analysis failed — tap retry to re-run")
            logger.error(f"Upload {call_id}: analysis failed — persisting zeroed debrief")

        rec.bant_score = analysis.get("bant_score")
        rec.bant_breakdown = analysis.get("bant_breakdown")
        rec.lead_verdict = analysis.get("lead_verdict")
        rec.lead_verdict_reason = analysis.get("lead_verdict_reason")
        rec.relevance_reason = analysis.get("relevance_reason")
        rec.sentiment_arc = analysis.get("sentiment_arc")
        rec.intent_tags = analysis.get("intent_tags")
        rec.entities = analysis.get("entities")
        rec.call_summary = analysis.get("call_summary")
        rec.key_points = analysis.get("key_points")
        rec.next_steps = analysis.get("next_steps")
        rec.next_action = analysis.get("next_action")
        rec.agent_debrief = analysis.get("agent_debrief")
        if analysis_failed:
            # Fields above are the zeroed placeholder — keep the row terminal-with-data
            # so /score renders greyed bars, but flag it failed so the app shows retry
            # and startup recovery can re-attempt it (idempotent guard at top re-runs it).
            rec.status = "failed"
            rec.error = "Automatic analysis failed"
            db.commit()
            # analyze_call swallows the provider exception and returns None; the real
            # cause (e.g. an LLM 429 / depleted credits) is in the server log at
            # ERROR level. Point future debugging there instead of a bare "None".
            _set_job(db, call_id, stage="analyse", status="failed",
                     error="Analyzer returned None (see logs: reasoning-provider call failed)")
            return
        rec.status = "completed" if analysis.get("is_relevant", True) else "not_relevant"
        rec.error = None
        db.commit()
        logger.info(f"Upload {call_id}: analysed -> {rec.lead_verdict} (bant {rec.bant_score}, "
                    f"relevant={analysis.get('is_relevant', True)})")

        # ---- 3. Rebuild memory bubble for this contact ----
        _set_job(db, call_id, stage="memory", status="running")
        try:
            _build_and_store_memory(contact_key_from_call_id(call_id), db, call.org_id)
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

    Recovery dispatches onto a daemon `threading.Thread` (see below) — if ANOTHER
    restart/reload happens while that thread is still mid-flight (e.g. `uvicorn --reload`
    picking up a file edit during active local development), the thread is killed
    outright with no chance to run its own except/finally cleanup, leaving the row
    frozen at stage=transcribe/status=running forever. Once `attempts` hits
    `max_attempts` this function stops retrying it (by design, to cap retries) but
    previously never gave it a terminal status either — it just sat at "running"
    indefinitely, looking like a permanent hang to anyone polling /processing-status.
    The block below closes that gap by explicitly failing exhausted jobs first.
    """
    import threading
    from app.database import SessionLocal
    from app.models import ProcessingJob

    db = SessionLocal()
    try:
        exhausted = db.query(ProcessingJob).filter(
            ProcessingJob.status.in_(["queued", "running"]),
            ProcessingJob.attempts >= ProcessingJob.max_attempts,
        ).all()
        for job in exhausted:
            logger.warning(f"Pipeline job {job.call_id} exhausted retries while stuck "
                            f"at stage={job.stage} — marking failed instead of leaving it 'running' forever")
            job.stage, job.status = "failed", "failed"
            job.error = (job.error or "") + " [auto-failed: exhausted retries after being killed mid-flight]"
        if exhausted:
            db.commit()

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


def _parse_call_date(raw: Optional[str]) -> Optional[datetime]:
    """Parses the app's `call_date` (ISO 8601, e.g. from DateTime.toIso8601String())."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_recording(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    call_date: Optional[str] = Form(None),
    contact_key_override: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Outbound flow: upload a previous call recording. We store it, create the call
    record, and kick off transcribe -> analyse -> memory in the background.
    Returns immediately with a call_id; the app polls /{call_id}/processing-status.

    BACKEND: pair this with your Lead row — pass `phone` as the contact key and
    link the returned call_id to the lead.

    Auth required (see get_inbox pattern) — previously optional with a comment
    saying "the Flutter app doesn't send one yet"; confirmed this session that
    it always does now, so this closes the last spot where a call could land
    with org_id=None (invisible to the org's own inbox/dashboards).

    Duplicate-upload guard: hashes the uploaded bytes and, if an identical
    recording from the same org already landed (at ANY time), returns that
    existing call_id instead of creating a second Lead/AudioCall/ProcessingJob.
    Real audio recordings are never byte-identical, so an identical hash always
    means the same file re-sent — this makes the mobile upload OUTBOX safe: a
    retry hours or a day later (after the app was offline) still dedupes instead
    of double-creating the call. Previously the guard only covered a 10-minute
    window, so a delayed retry produced a duplicate.
    """
    import hashlib
    import uuid as _uuid
    from app.models import Lead
    from app.utils.s3 import get_storage_manager
    from app.utils.memory_bubble import slugify_contact, normalize_phone

    manager = get_storage_manager()

    org_id = current_user.org_id
    telecaller_id = current_user.id

    file_bytes = await file.read()
    content_hash = hashlib.sha256(file_bytes).hexdigest()

    existing = (
        db.query(AudioCall)
        .filter(
            AudioCall.org_id == org_id,
            AudioCall.content_hash == content_hash,
        )
        .order_by(AudioCall.created_at.desc())
        .first()
    )
    if existing:
        logger.info(f"Duplicate upload detected (hash={content_hash[:12]}...) — "
                     f"returning existing call {existing.call_id} instead of re-processing")
        return {
            "call_id": existing.call_id,
            "status": "processing",
            "name": name,
            "phone": phone,
            "source": source,
            "message": "Duplicate of a recently-uploaded recording — already being processed.",
        }

    # Build a stable, readable call_id (mirrors import_audio convention). When
    # uploading to an EXISTING lead, the app sends that lead's real
    # contact_key as contact_key_override — using it verbatim (instead of
    # re-deriving from `name`) is what makes the uploaded call attach to that
    # lead's history. Falling back to slugify_contact(name) here silently
    # attached the call to a different (often brand-new) Lead row whenever
    # the display name didn't slugify back to the original contact_key —
    # e.g. after a local name edit — which is why the recording never showed
    # up in the lead's history.
    # contact_key priority: an explicit override (attaching to a known lead) wins;
    # otherwise the normalised PHONE is the canonical key (stable across name edits
    # and same-name collisions); only when neither exists do we fall back to a name
    # slug. This is what lets an auto-captured call that now sends `phone` group
    # correctly with the lead's other calls and its memory bubble.
    slug = (contact_key_override or "").strip() or normalize_phone(phone) or slugify_contact(name or "lead")
    call_id = f"call_{slug}_{_uuid.uuid4().hex[:8]}"

    # Upsert a Lead row so this contact shows in the inbox with its details.
    # Org-scoped: contact_key is only unique per-org (see uq_leads_org_contact_key),
    # so an unscoped lookup here could match — or fail to match — another org's lead.
    lead = db.query(Lead).filter(Lead.contact_key == slug, Lead.org_id == org_id).first()
    if not lead:
        lead = Lead(id=str(_uuid.uuid4()), contact_key=slug, name=name, phone=phone,
                    source=source, status="contacted", org_id=org_id, assigned_to=telecaller_id)
        db.add(lead)
    else:
        lead.phone = phone or lead.phone
        lead.source = source or lead.source
        lead.status = "contacted"
        lead.org_id = lead.org_id or org_id
        lead.assigned_to = lead.assigned_to or telecaller_id
    db.commit()

    # Persist the upload to a temp file, then into local storage.
    # (file_bytes was already read above, into the hash — write that same
    # buffer here rather than re-reading the UploadFile stream, which would
    # come back empty the second time.)
    suffix = os.path.splitext(file.filename or "")[1] or ".mp3"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(file_bytes)
        tmp.close()
        audio_url = manager.save_audio_file(tmp.name, call_id)
        if not audio_url:
            raise HTTPException(status_code=500, detail="Failed to store audio file")

        # Create the call record up front (transcript fills in via background task)
        call = AudioCall(
            call_id=call_id,
            org_id=org_id,
            telecaller_id=telecaller_id,
            timestamp=_parse_call_date(call_date) or datetime.utcnow(),
            transcript={"turns": []},
            audio_file_url=audio_url,
            content_hash=content_hash,
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
async def get_telecaller_score(
    window_days: int = 7,
    scope: str = "me",
    db: Session = Depends(get_db),
    current_user=Depends(_get_current_user),
):
    """
    Telecaller rolling performance + trend (the Score tab numbers).

    scope: "me" (default) = ONLY the caller's own calls, so a telecaller sees
    their own performance rather than the whole org's blended numbers. "team" =
    the org-wide aggregate (previous behaviour) for a founder/lead comparison.
    Calls now carry telecaller_id (stamped on upload), so this is a real
    per-agent slice — resolving the long-standing "filter by agent_id once calls
    carry an agent" TODO.

    Auth required (see get_inbox pattern) — org-scoped via current_user.org_id.
    """
    from app.utils.lead_intelligence import telecaller_score

    window_days = max(1, window_days)  # 0/negative would silently mean "all time"
    telecaller_id = None if scope == "team" else current_user.id
    grouped = _all_analyses_by_contact(db, current_user.org_id, telecaller_id=telecaller_id)
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
