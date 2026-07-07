"""Pydantic schemas for the exam processor."""

from typing import Optional
from pydantic import BaseModel, Field


class SubjectEntry(BaseModel):
    """Input entry for subject extraction batch creation."""
    subject: str = Field(description="Path to subject file")
    barem: Optional[str] = Field(default=None, description="Path to barem file (optional)")


class BaremSchema(BaseModel):
    """Schema for barem (grading details)."""
    explicatie: str = Field(description="Grading explanation")
    imagini: list[str] = Field(default_factory=list, description="Images in barem")


class ProblemSchema(BaseModel):
    """Single problem from extraction - matches out.json structure."""
    cerinta: str = Field(description="Problem statement")
    barem: Optional[BaremSchema] = Field(default=None, description="Barem (grading details)")
    imagini: list[str] = Field(default_factory=list, description="Images in subject")


# ─── Gemma 4 OCR pipeline schemas ───────────────────────────────────────────


class ImageEntry(BaseModel):
    """An image found in a document.  Coordinates are 0-1 page-relative from the LLM."""
    page_number: int = Field(description="Page number (1-based)")
    coordinates: list[float] = Field(
        default_factory=list,
        description="Bounding box [x0, y0, x1, y1] as 0-1 page-relative decimals",
    )



class OcrBaremSchema(BaseModel):
    """Barem (grading details) from OCR extraction."""
    explicatie: str = Field(description="Grading explanation")
    imagini: list[ImageEntry] = Field(
        default_factory=list, description="Images in barem with coordinates"
    )


class OcrProblem(BaseModel):
    """A problem extracted from OCR'd exam document, with images as a property."""
    cerinta: str = Field(description="Problem statement (OCR'd text)")
    imagini: list[ImageEntry] = Field(
        description="Images belonging to this problem (with page & coordinates)"
    )
    barem: Optional[OcrBaremSchema] = Field(
        default=None, description="Barem (grading details) if available"
    )


class OcrResult(BaseModel):
    """Wrapper for the Gemma 4 OCR extraction result."""
    problems: list[OcrProblem] = Field(
        description="Extracted problems that contain images"
    )


# ─── CDL extraction schemas ────────────────────────────────────────────────


class CdlDescription(BaseModel):
    """CDL (Condition Declaration Language) output for a geometric figure."""
    is_geometric: bool = Field(description="True if the image is a geometric figure")
    description: str = Field(
        description="CDL description with each predicate on its own line"
    )
    is_complete: bool = Field(
        description="True if the figure is fully representable in CDL"
    )


class NlDescription(BaseModel):
    """Natural language description of a geometric figure."""
    is_geometric: bool = Field(description="True if the image is a geometric figure")
    natural_language: str = Field(
        description="Detailed natural language description of the figure"
    )


class ConsistencyVerdict(BaseModel):
    """LLM verdict on whether a CDL and an NL description of the same figure agree."""
    is_geometric: bool = Field(description="True if the image is a geometric figure")
    consistent: bool = Field(
        description="True if the CDL and NL are mutually consistent and match the image"
    )
    severity: str = Field(description="One of: 'none', 'minor', 'major'")
    issues: list[str] = Field(
        default_factory=list, description="Specific discrepancies between CDL and NL"
    )
    suggested_cdl: Optional[str] = Field(
        default=None,
        description="A corrected CDL description (one predicate per line) when the "
        "original was inconsistent or incomplete; otherwise null",
    )
