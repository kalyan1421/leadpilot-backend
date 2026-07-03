"""Supabase Storage utility for audio file management (drop-in alongside S3Manager/LocalStorageManager)."""

import os
import logging
import tempfile
from io import BytesIO
from typing import Optional, BinaryIO

from supabase import create_client, Client

from app.config import settings

logger = logging.getLogger(__name__)


class SupabaseStorageManager:
    """Manages Supabase Storage operations for audio files (API-compatible with S3Manager/LocalStorageManager)."""

    def __init__(self):
        """Initialize the Supabase client using the server-side service_role key."""
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for storage_mode=supabase"
            )
        self.client: Client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        self.bucket_name = settings.supabase_storage_bucket

    def save_audio_file(self, source_path: str, call_id: str) -> Optional[str]:
        """
        Upload a local audio file to the Supabase Storage bucket.

        Args:
            source_path: Path to the source audio file
            call_id: Unique identifier for the call

        Returns:
            A "supabase://calls/{call_id}/audio.{ext}" pseudo-URL, or None if the upload fails
        """
        try:
            if not os.path.exists(source_path):
                logger.error(f"Source audio file not found: {source_path}")
                return None

            file_extension = self._detect_audio_format(source_path)
            storage_key = f"calls/{call_id}/audio.{file_extension}"
            content_type = self._get_content_type(file_extension)

            with open(source_path, "rb") as f:
                self.client.storage.from_(self.bucket_name).upload(
                    path=storage_key,
                    file=f,
                    file_options={"content-type": content_type, "upsert": "true"},
                )

            storage_url = f"supabase://{storage_key}"
            logger.info(f"Uploaded audio for call {call_id} to Supabase Storage: {storage_url}")
            return storage_url

        except Exception as e:
            logger.error(f"Error uploading audio for call {call_id} to Supabase Storage: {e}")
            return None

    def download_and_upload_audio(self, audio_url: str, call_id: str, file_extension: str = None) -> Optional[str]:
        """
        For API compatibility with LocalStorageManager/S3Manager: if the URL is already a
        local file path, upload it directly; if it's a supabase:// URL, it's already stored;
        otherwise download the remote URL first, then upload.
        """
        try:
            if os.path.exists(audio_url):
                return self.save_audio_file(audio_url, call_id)

            if audio_url.startswith("supabase://"):
                return audio_url

            import requests

            response = requests.get(audio_url, stream=True, timeout=30)
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension or 'mp3'}") as temp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        temp_file.write(chunk)
                temp_path = temp_file.name

            try:
                return self.save_audio_file(temp_path, call_id)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except Exception as e:
            logger.error(f"Error processing audio for call {call_id}: {e}")
            return None

    def download_audio_file(self, s3_key_or_path: str) -> Optional[BinaryIO]:
        """
        Download an audio file's bytes from Supabase Storage into an in-memory file-like object.

        Args:
            s3_key_or_path: Either a "supabase://..." URL or a raw storage key

        Returns:
            File-like object containing the audio data, or None if not found
        """
        try:
            storage_key = self.extract_s3_key_from_url(s3_key_or_path)
            data = self.client.storage.from_(self.bucket_name).download(storage_key)
            return BytesIO(data)
        except Exception as e:
            logger.error(f"Error downloading {s3_key_or_path} from Supabase Storage: {e}")
            return None

    def get_audio_file_path(self, call_id: str) -> Optional[str]:
        """
        Object storage has no real filesystem path, but callers like the Sarvam transcriber
        (`app/utils/sarvam.py::transcribe_file`) require one — it has no URL-input mode. This
        downloads the object to a temp local file and returns that path.

        Callers own cleanup of the returned temp file (same ad hoc convention already used by
        `S3Manager.download_and_upload_audio` — delete=False + caller unlinks when done).

        Note: for the upload flow in `app/api/calls.py::upload_recording`, prefer passing the
        already-local `tmp.name` directly to the transcription step instead of calling this —
        it's already on disk and re-downloading it from Supabase would be a wasted round trip.
        This method exists for callers (e.g. `scripts/retranscribe_all.py`) that only have a
        stored `supabase://` URL and no local file in hand.
        """
        try:
            storage_key = f"calls/{call_id}/audio"
            entries = self.client.storage.from_(self.bucket_name).list(f"calls/{call_id}")
            match = next((e for e in entries if e.get("name", "").startswith("audio.")), None)
            if not match:
                logger.error(f"No audio object found for call {call_id} in Supabase Storage")
                return None

            file_extension = match["name"].split(".")[-1]
            full_key = f"calls/{call_id}/{match['name']}"
            data = self.client.storage.from_(self.bucket_name).download(full_key)

            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension}") as temp_file:
                temp_file.write(data)
                return temp_file.name

        except Exception as e:
            logger.error(f"Error resolving local path for call {call_id} from Supabase Storage: {e}")
            return None

    def file_exists(self, key_or_path: str) -> bool:
        """Check if an audio object exists in the Supabase Storage bucket."""
        try:
            storage_key = self.extract_s3_key_from_url(key_or_path)
            parent, _, filename = storage_key.rpartition("/")
            entries = self.client.storage.from_(self.bucket_name).list(parent)
            return any(e.get("name") == filename for e in entries)
        except Exception as e:
            logger.error(f"Error checking file existence for {key_or_path}: {e}")
            return False

    def extract_s3_key_from_url(self, url: str) -> Optional[str]:
        """Extract the storage key from a 'supabase://calls/{id}/audio.ext' URL."""
        if url and url.startswith("supabase://"):
            return url[len("supabase://"):]
        return url

    def _get_content_type(self, file_extension: str) -> str:
        """Get the appropriate content type for a file extension (mirrors LocalStorageManager)."""
        content_types = {
            'mp3': 'audio/mpeg',
            'mpeg': 'audio/mpeg',
            'wav': 'audio/wav',
            'm4a': 'audio/mp4',
            'aac': 'audio/aac',
            'ogg': 'audio/ogg',
            'flac': 'audio/flac'
        }
        return content_types.get(file_extension.lower(), 'audio/mpeg')

    def _detect_audio_format(self, file_path: str) -> str:
        """Detect audio format by examining file headers (mirrors LocalStorageManager)."""
        try:
            with open(file_path, 'rb') as f:
                header = f.read(16)

                if header.startswith(b'ID3') or header.startswith(b'\xff\xfb') or header.startswith(b'\xff\xf3'):
                    return 'mp3'
                elif header.startswith(b'RIFF') and header[8:12] == b'WAVE':
                    return 'wav'
                elif header.startswith(b'ftyp'):
                    ftype = header[4:8]
                    if ftype in [b'M4A ', b'M4B ', b'M4P ', b'M4V ']:
                        return 'm4a'
                elif header.startswith(b'\xff\xf1') or header.startswith(b'\xff\xf9'):
                    return 'aac'
                elif header.startswith(b'OggS'):
                    return 'ogg'
                elif header.startswith(b'fLaC'):
                    return 'flac'

                ext = os.path.splitext(file_path)[1].lower().strip('.')
                if ext in ['mp3', 'mpeg', 'wav', 'm4a', 'aac', 'ogg', 'flac']:
                    return ext if ext != 'mpeg' else 'mp3'

                return 'mp3'  # Default fallback

        except Exception as e:
            logger.error(f"Error detecting audio format: {e}")
            return 'mp3'

    def _detect_audio_format_from_headers(self, file_path: str) -> str:
        """Alias for _detect_audio_format for API compatibility with S3Manager."""
        return self._detect_audio_format(file_path)


# Global Supabase storage manager instance
supabase_storage_manager = SupabaseStorageManager() if settings.storage_mode == "supabase" else None
