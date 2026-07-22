"""Local file storage utility for audio files (replaces S3 when storage_mode=local)."""

import os
import shutil
import logging
from typing import Optional, BinaryIO
from io import BytesIO
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)


class LocalStorageManager:
    """Manages local file storage for audio files (drop-in replacement for S3Manager)."""

    def __init__(self):
        """Initialize local storage with configured path."""
        self.storage_path = os.path.abspath(settings.local_storage_path)
        self.bucket_name = "local"  # Compatibility with S3Manager references

        # Create storage directory if it doesn't exist
        os.makedirs(self.storage_path, exist_ok=True)
        logger.info(f"Local storage initialized at: {self.storage_path}")

    def _call_dir(self, call_id: str) -> str:
        """Resolve this call's storage directory, refusing to build a path
        that escapes storage_path — defense in depth against a call_id built
        from unsanitized input elsewhere (path traversal)."""
        call_dir = os.path.abspath(os.path.join(self.storage_path, "calls", call_id))
        if os.path.commonpath([call_dir, self.storage_path]) != self.storage_path:
            raise ValueError(f"call_id resolves outside storage_path: {call_id!r}")
        return call_dir

    def save_audio_file(self, source_path: str, call_id: str) -> Optional[str]:
        """
        Copy audio file to local storage.

        Args:
            source_path: Path to the source audio file
            call_id: Unique identifier for the call

        Returns:
            Local storage URL (file path), or None if operation fails
        """
        try:
            if not os.path.exists(source_path):
                logger.error(f"Source audio file not found: {source_path}")
                return None

            # Detect file extension
            file_extension = self._detect_audio_format(source_path)

            # Create call directory
            call_dir = self._call_dir(call_id)
            os.makedirs(call_dir, exist_ok=True)
            
            # Copy file
            dest_path = os.path.join(call_dir, f"audio.{file_extension}")
            shutil.copy2(source_path, dest_path)
            
            # Return a local URL format
            local_url = f"local://{call_id}/audio.{file_extension}"
            logger.info(f"Saved audio for call {call_id} to {dest_path}")
            
            return local_url
            
        except Exception as e:
            logger.error(f"Error saving audio file for call {call_id}: {e}")
            return None
    
    def download_and_upload_audio(self, audio_url: str, call_id: str, file_extension: str = None) -> Optional[str]:
        """
        For local storage: if the URL is a local file path, copy it to storage.
        If it's a remote URL, download it first.
        
        Maintains API compatibility with S3Manager.
        """
        try:
            # Check if it's already a local file path
            if os.path.exists(audio_url):
                return self.save_audio_file(audio_url, call_id)
            
            # Check if it's a local:// URL (already stored)
            if audio_url.startswith("local://"):
                return audio_url
            
            # For remote URLs, download first
            import requests
            import tempfile
            
            response = requests.get(audio_url, stream=True, timeout=30)
            response.raise_for_status()
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension or 'mp3'}") as temp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        temp_file.write(chunk)
                temp_path = temp_file.name
            
            try:
                result = self.save_audio_file(temp_path, call_id)
                return result
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                    
        except Exception as e:
            logger.error(f"Error processing audio for call {call_id}: {e}")
            return None
    
    def get_audio_file_path(self, call_id: str) -> Optional[str]:
        """
        Get the filesystem path of the audio file for a call.
        
        Args:
            call_id: Unique identifier for the call
            
        Returns:
            Filesystem path to the audio file, or None if not found
        """
        try:
            call_dir = self._call_dir(call_id)
        except ValueError:
            logger.error(f"Refusing unsafe call_id in get_audio_file_path: {call_id!r}")
            return None
        if not os.path.exists(call_dir):
            return None

        # Find any audio file in the call directory
        for filename in os.listdir(call_dir):
            if filename.startswith("audio."):
                return os.path.join(call_dir, filename)
        
        return None
    
    def download_audio_file(self, s3_key_or_path: str) -> Optional[BinaryIO]:
        """
        Read audio file from local storage (API compatible with S3Manager).
        
        Args:
            s3_key_or_path: Either a local storage key or file path
            
        Returns:
            File-like object containing the audio data, or None if not found
        """
        try:
            # Try interpreting as a calls/{call_id}/audio.ext path
            full_path = os.path.join(self.storage_path, s3_key_or_path)
            if os.path.exists(full_path):
                with open(full_path, 'rb') as f:
                    return BytesIO(f.read())
            
            # Try as an absolute path
            if os.path.exists(s3_key_or_path):
                with open(s3_key_or_path, 'rb') as f:
                    return BytesIO(f.read())
            
            logger.error(f"Audio file not found: {s3_key_or_path}")
            return None
            
        except Exception as e:
            logger.error(f"Error reading audio file: {e}")
            return None
    
    def file_exists(self, key_or_path: str) -> bool:
        """Check if an audio file exists in local storage."""
        full_path = os.path.join(self.storage_path, key_or_path)
        return os.path.exists(full_path)
    
    def extract_s3_key_from_url(self, url: str) -> Optional[str]:
        """Extract storage key from a local:// URL."""
        if url.startswith("local://"):
            return f"calls/{url[len('local://'):]}"
        return url
    
    def _get_content_type(self, file_extension: str) -> str:
        """Get the appropriate content type for a file extension."""
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
        """Detect audio format by examining file headers."""
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
                
                # Default: check file extension
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


# Global local storage manager instance
local_storage_manager = LocalStorageManager() if settings.storage_mode == "local" else None
