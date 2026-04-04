"""Pydantic models for the exam processor."""

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


class CDLSchema(BaseModel):
    """Schema for CDL (Contextual Description Language) response."""
    is_geometric: bool = Field(description="Whether the image is a geometric figure")
    description: str = Field(description="CDL description or Unknown(reason)")
    is_complete: bool = Field(default=True, description="Whether fully representable in CDL")


class ImageWithCDL(BaseModel):
    """Image with its CDL description."""
    url: str = Field(description="Image URL")
    cdl: CDLSchema = Field(description="CDL description")


class EnrichedProblem(BaseModel):
    """Problem with CDL descriptions for all images."""
    cerinta: str = Field(description="Problem statement")
    barem: Optional[BaremSchema] = Field(default=None, description="Barem (grading details)")
    imagini: list[ImageWithCDL] = Field(default_factory=list, description="Subject images with CDL")
    barem_imagini: list[ImageWithCDL] = Field(default_factory=list, description="Barem images with CDL")


# Final output type - key is file path, value is list of problems with CDL
FinalResult = dict[str, list[EnrichedProblem]]
