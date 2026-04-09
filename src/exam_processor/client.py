"""Together.ai client wrapper."""

import os
import json
from pathlib import Path
from typing import Optional, Any

from together import Together
from dotenv import load_dotenv

load_dotenv()


class TogetherClient:
    """Client for interacting with Together.ai Batch API."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Together.ai client."""
        self.api_key = api_key or os.getenv("TOGETHER_API_KEY")
        if not self.api_key:
            raise ValueError("Together API key is required. Set TOGETHER_API_KEY environment variable.")
        self.client = Together(api_key=self.api_key)

    def load_prompt(self, prompt_file: str) -> str:
        """Load a prompt template from file."""
        return Path(prompt_file).read_text(encoding="utf-8")

    def upload_file(self, file_path: str) -> str:
        """Upload a file to Together.ai and return the file ID."""
        file_resp = self.client.files.upload(
            file=file_path,
            purpose="batch-api",
            check=False
        )
        return file_resp.id

    def create_batch(
        self,
        file_id: str,
        endpoint: str = "/v1/chat/completions",
        completion_window: str = "24h"
    ) -> str:
        """Create a batch job and return the batch ID."""
        batch = self.client.batches.create(
            input_file_id=file_id,
            endpoint=endpoint,
            completion_window=completion_window
        )
        batch_id = batch.job.id if batch.job else None
        if not batch_id:
            raise ValueError("Failed to create batch: no job ID returned")
        return batch_id

    def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        """Get detailed status of a batch."""
        batch = self.client.batches.retrieve(batch_id)
        return {
            "batch_id": batch_id,
            "status": batch.status,
            "created_at": getattr(batch, "created_at", None),
            "completed_at": getattr(batch, "completed_at", None),
            "progress": getattr(batch, "progress", None),
            "request_count": getattr(batch, "request_count", None),
            "model_id": getattr(batch, "model_id", None),
            "endpoint": getattr(batch, "endpoint", None),
            "error": getattr(batch, "error", None),
            "output_file_id": getattr(batch, "output_file_id", None),
            "error_file_id": getattr(batch, "error_file_id", None),
        }

    def retrieve_batch_results(self, batch_id: str) -> str:
        """Retrieve the output file content from a completed batch."""
        batch = self.client.batches.retrieve(batch_id)
        
        if not batch.output_file_id:
            raise ValueError(f"Batch {batch_id} has no output file")
        
        response = self.client.files.content(batch.output_file_id)
        output_content = response.parse()
        if isinstance(output_content, bytes):
            return output_content.decode("utf-8")
        return output_content

    def retrieve_batch_errors(self, batch_id: str) -> str | None:
        """Retrieve the error file content from a batch if it exists."""
        batch = self.client.batches.retrieve(batch_id)
        
        if not batch.error_file_id:
            return None
        
        response = self.client.files.content(batch.error_file_id)
        error_content = response.parse()
        if isinstance(error_content, bytes):
            return error_content.decode("utf-8")
        return error_content

    def parse_batch_results(self, results_content: str) -> dict[str, Any]:
        """Parse JSONL batch results into a dictionary keyed by custom_id."""
        results_by_custom_id = {}
        for line in results_content.strip().split("\n"):
            if line.strip():
                result = json.loads(line)
                results_by_custom_id[result["custom_id"]] = result
        return results_by_custom_id

    @staticmethod
    def build_image_content(prompt: str, image_url: str) -> list[dict]:
        """Build message content with text and image URL for multimodal models."""
        return [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}}
        ]


class MathpixClient:
    """Client for converting documents to markdown using Mathpix API."""

    SUPPORTED_EXTENSIONS = {
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".epub",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tiff",
    }

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_key: Optional[str] = None,
    ):
        """Initialize the Mathpix client."""
        from mpxpy import MathpixClient as MpxClient

        self.app_id = app_id or os.getenv("MATHPIX_APP_ID")
        self.app_key = app_key or os.getenv("MATHPIX_APP_KEY")
        if not self.app_id or not self.app_key:
            raise ValueError(
                "Mathpix credentials required. Set MATHPIX_APP_ID and MATHPIX_APP_KEY "
                "environment variables or pass app_id and app_key directly."
            )
        self.client = MpxClient(app_id=self.app_id, app_key=self.app_key)

    def convert_to_markdown(self, file_path: str) -> str:
        """
        Convert a document file to markdown.

        Args:
            file_path: Path to the document (PDF, DOCX, DOC, PPTX, etc.)

        Returns:
            Markdown content as text

        Raises:
            ValueError: If file extension is not supported
            Exception: For API errors
        """
        path = Path(file_path)
        ext = path.suffix.lower()

        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type: {ext}. Supported types: {self.SUPPORTED_EXTENSIONS}"
            )

        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Use pdf_new for documents - it handles PDFs and other document types
        pdf = self.client.pdf_new(file_path=str(path))
        if not pdf.wait_until_complete(timeout=120):
            raise TimeoutError(f"PDF conversion timed out for {file_path}")
        return pdf.to_md_text()

    @classmethod
    def is_supported(cls, file_path: str) -> bool:
        """Check if a file type is supported for conversion."""
        return Path(file_path).suffix.lower() in cls.SUPPORTED_EXTENSIONS
