from typing import Optional
from pydantic import BaseModel, Field


def json_schema_response_format(model_cls: type[BaseModel], name: str | None = None) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name or model_cls.__name__,
            "schema": model_cls.model_json_schema(),
        },
    }


class ImageEntry(BaseModel):
    page_number: int = Field(description="Page number (1-based)")
    coordinates: list[float] = Field(
        default_factory=list,
        description="Bounding box [x0, y0, x1, y1] as 0-1 page-relative decimals",
    )


class OcrBaremSchema(BaseModel):
    explicatie: str = Field(description="Grading explanation")
    imagini: list[ImageEntry] = Field(
        default_factory=list, description="Images in barem with coordinates"
    )


class OcrProblem(BaseModel):
    cerinta: str = Field(description="Problem statement (OCR'd text)")
    imagini: list[ImageEntry] = Field(
        description="Images belonging to this problem (with page & coordinates)"
    )
    barem: Optional[OcrBaremSchema] = Field(
        default=None, description="Barem (grading details) if available"
    )


class OcrResult(BaseModel):
    problems: list[OcrProblem] = Field(
        description="Extracted problems that contain images"
    )


class CdlDescription(BaseModel):
    is_geometric: bool = Field(description="True if the image is a geometric figure")
    description: str = Field(
        description="CDL description with each predicate on its own line"
    )
    is_complete: bool = Field(
        description="True if the figure is fully representable in CDL"
    )


class NlDescription(BaseModel):
    is_geometric: bool = Field(description="True if the image is a geometric figure")
    natural_language: str = Field(
        description="Detailed natural language description of the figure"
    )


class ConsistencyVerdict(BaseModel):
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
        description="A corrected CDL description (one predicate per line) when the original was inconsistent or incomplete; otherwise null",
    )