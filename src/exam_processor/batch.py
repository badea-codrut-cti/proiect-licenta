"""Batch operations for exam processing."""

import json
import os
from pathlib import Path
from typing import Optional

from exam_processor.client import TogetherClient
from exam_processor.models import (
    DEFAULT_EXTRACTION_MODEL,
    DEFAULT_CDL_MODEL,
)

from exam_processor.schemas import (
    SubjectEntry,
    ProblemSchema,
    BaremSchema,
    CDLSchema,
    ImageWithCDL,
    EnrichedProblem,
)


def format_response_format(schema_cls: type) -> dict:
    """Format a Pydantic model schema for Together's response_format parameter."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_cls.__name__,
            "schema": schema_cls.model_json_schema()
        }
    }


def strip_json_code_block(content: str) -> str:
    """Strip ```json ... ``` markdown wrapper from JSON content."""
    content = content.strip()
    if content.startswith('```json'):
        content = content[7:]  # Skip '```json' + newline
    elif content.startswith('```'):
        content = content[3:]
    if content.endswith('```'):
        content = content[:-3]
    return content.strip()


class SubjectExtractionBatch:
    """Handles subject extraction batch operations (Stage 1)."""

    def __init__(self, client: TogetherClient, prompts_dir: str = "prompts"):
        self.client = client
        self.prompts_dir = prompts_dir
        self._batch_id: str | None = None

    @property
    def batch_id(self) -> str | None:
        """Get the batch ID if available."""
        return self._batch_id

    def create(
        self,
        entries: list[SubjectEntry],
        output_dir: str = ".",
        model: str = DEFAULT_EXTRACTION_MODEL
    ) -> str:
        """
        Create a subject extraction batch. Each entry = one batch request.
        
        Returns:
            The created batch ID
        """
        prompt_template = self.client.load_prompt(
            os.path.join(self.prompts_dir, "split_exam.txt")
        )

        batch_lines = []
        tracking_data = {}

        for idx, entry in enumerate(entries):
            subject_content = Path(entry.subject).read_text(encoding="utf-8")
            barem_content = Path(entry.barem).read_text(encoding="utf-8") if entry.barem else ""

            prompt = prompt_template.replace("{{EXAM_CONTENT}}", subject_content)
            prompt = prompt.replace("{{BAREM_CONTENT}}", barem_content)

            request_body = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 32768,
            }

            custom_id = f"request-{idx:04d}"
            tracking_data[custom_id] = {
                "subject_path": entry.subject,
                "barem_path": entry.barem,
            }

            jsonl_line = json.dumps({"custom_id": custom_id, "body": request_body})
            batch_lines.append(jsonl_line)

        if not batch_lines:
            raise ValueError("No entries to process")

        jsonl_path = os.path.join(output_dir, "subject_extraction_batch_input.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write("\n".join(batch_lines))

        file_id = self.client.upload_file(jsonl_path)
        batch_id = self.client.create_batch(file_id)
        self._batch_id = batch_id

        tracking_file = os.path.join(output_dir, "subject_extraction_tracking.json")
        with open(tracking_file, "w", encoding="utf-8") as f:
            json.dump(tracking_data, f, indent=2)

        os.remove(jsonl_path)
        return batch_id

    def retrieve(
        self,
        batch_id: str,
        tracking_file: str,
        output_file: Optional[str] = None,
        verbose: bool = False
    ) -> tuple[dict[str, list[ProblemSchema]], tuple[int, int], str]:
        """
        Retrieve results grouped by source file.
        
        Returns:
            tuple of (file path -> list of problems, (total_input_tokens, total_output_tokens), model_name)
        """
        if verbose:
            print(f"[DEBUG] Calling retrieve_batch_results for batch_id={batch_id}")
        results_content = self.client.retrieve_batch_results(batch_id)
        if verbose:
            print(f"[DEBUG] retrieve_batch_results returned {len(results_content)} chars")
            
        # Check for errors
        error_content = self.client.retrieve_batch_errors(batch_id)
        if error_content:
            error_lines = error_content.strip().split('\n')
            print(f"[WARNING] Batch has {len(error_lines)} error(s):")
            for line in error_lines[:10]:  # Show first 10 errors
                print(f"  {line}")
            if len(error_lines) > 10:
                print(f"  ... and {len(error_lines) - 10} more errors")
        
        results_by_custom_id = self.client.parse_batch_results(results_content)
        
        if verbose:
            print(f"[DEBUG] Parsed {len(results_by_custom_id)} successful results")

        with open(tracking_file, "r", encoding="utf-8") as f:
            tracking_data = json.load(f)

        problems_by_file: dict[str, list[ProblemSchema]] = {}
        total_input_tokens = 0
        total_output_tokens = 0
        model_name: str | None = None

        for custom_id, tracking in tracking_data.items():
            result = results_by_custom_id.get(custom_id)
            if not result:
                continue

            # Extract model from first successful result
            if model_name is None:
                model_name = result.get("request", {}).get("body", {}).get("model", DEFAULT_EXTRACTION_MODEL)

            source_file = tracking["subject_path"]

            try:
                response_content = result["response"]["body"]["choices"][0]["message"]["content"]
                problems_list = json.loads(response_content)
                
                for p in problems_list:
                    barem = None
                    if p.get("barem"):
                        barem = BaremSchema(
                            explicatie=p["barem"].get("explicatie", ""),
                            imagini=p["barem"].get("imagini", []),
                        )
                    problem = ProblemSchema(
                        cerinta=p.get("cerinta", ""),
                        barem=barem,
                        imagini=p.get("imagini", []),
                    )
                    
                    if source_file not in problems_by_file:
                        problems_by_file[source_file] = []
                    problems_by_file[source_file].append(problem)
                    
                usage = result["response"]["body"]["usage"]
                total_input_tokens += usage["prompt_tokens"]
                total_output_tokens += usage["completion_tokens"]
                
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"Warning: Failed to parse result for {custom_id}: {e}")
                if verbose:
                    print(f"  Raw JSONL response for {custom_id}:")
                    print(json.dumps(result, indent=2))

        if output_file:
            output = {
                k: [prob.model_dump() for prob in v] 
                for k, v in problems_by_file.items()
            }
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)

        return problems_by_file, (total_input_tokens, total_output_tokens), model_name or DEFAULT_EXTRACTION_MODEL


class ImageDescriptionBatch:
    """Handles image description batch operations (Stage 2)."""

    def __init__(self, client: TogetherClient, prompts_dir: str = "prompts"):
        self.client = client
        self.prompts_dir = prompts_dir
        self._batch_id: str | None = None

    @property
    def batch_id(self) -> str | None:
        """Get the batch ID if available."""
        return self._batch_id

    def create_from_problems(
        self,
        problems_by_file: dict[str, list[ProblemSchema]],
        output_dir: str = ".",
        model: str = DEFAULT_CDL_MODEL
    ) -> str:
        """
        Create image description batch from problems grouped by file.
        
        For each image:
        - Subject images: context = cerinta
        - Barem images: context = cerinta + barem.explicatie
        
        Returns:
            The created batch ID
        """
        prompt_template = self.client.load_prompt(
            os.path.join(self.prompts_dir, "image_to_cdl.txt")
        )

        batch_lines = []
        tracking_data = {}

        for source_file, problems in problems_by_file.items():
            for problem_idx, problem in enumerate(problems):
                cerinta = problem.cerinta
                
                # Subject images
                for img_idx, image_url in enumerate(problem.imagini):
                    if not image_url:
                        continue

                    custom_id = f"{Path(source_file).stem}_p{problem_idx}_subject_{img_idx}"
                    
                    prompt = prompt_template.replace("{{PROBLEM_TASK}}", cerinta)
                    prompt = prompt.replace("{{CONTEXT}}", "")

                    tracking_data[custom_id] = {
                        "source_file": source_file,
                        "problem_index": problem_idx,
                        "source_type": "subject",
                        "image_index": img_idx,
                    }

                    request_body = {
                        "model": model,
                        "messages": [{
                            "role": "user",
                            "content": self.client.build_image_content(prompt, image_url)
                        }],
                        "temperature": 0.5,
                        "repetition_penalty": 1.2,
                        "max_tokens": 20000,
                        "response_format": format_response_format(CDLSchema),
                    }

                    jsonl_line = json.dumps({"custom_id": custom_id, "body": request_body})
                    batch_lines.append(jsonl_line)

                # Barem images
                if problem.barem:
                    barem_explicatie = problem.barem.explicatie
                    for img_idx, image_url in enumerate(problem.barem.imagini):
                        if not image_url:
                            continue

                        custom_id = f"{Path(source_file).stem}_p{problem_idx}_barem_{img_idx}"
                        
                        prompt = prompt_template.replace("{{PROBLEM_TASK}}", cerinta)
                        prompt = prompt.replace("{{CONTEXT}}", f"Barem:\n{barem_explicatie}")

                        tracking_data[custom_id] = {
                            "source_file": source_file,
                            "problem_index": problem_idx,
                            "source_type": "barem",
                            "image_index": img_idx,
                        }

                        request_body = {
                            "model": model,
                            "messages": [{
                                "role": "user",
                                "content": self.client.build_image_content(prompt, image_url)
                            }],
                            "temperature": 0.2,
                            "max_tokens": 32768,
                            "response_format": CDLSchema.model_json_schema(),
                        }

                        jsonl_line = json.dumps({"custom_id": custom_id, "body": request_body})
                        batch_lines.append(jsonl_line)

        if not batch_lines:
            raise ValueError("No images found to process")

        jsonl_path = os.path.join(output_dir, "image_description_batch_input.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write("\n".join(batch_lines))

        file_id = self.client.upload_file(jsonl_path)
        batch_id = self.client.create_batch(file_id)
        self._batch_id = batch_id

        tracking_file = os.path.join(output_dir, "image_description_tracking.json")
        with open(tracking_file, "w", encoding="utf-8") as f:
            json.dump(tracking_data, f, indent=2)

        os.remove(jsonl_path)
        return batch_id

    def retrieve(
        self,
        batch_id: str,
        tracking_file: str,
        output_file: Optional[str] = None,
        verbose: bool = False
    ) -> tuple[dict[str, list[dict]], tuple[int, int], str]:
        """
        Retrieve CDL results grouped by source file.
        
        Returns:
            tuple of (file path -> list of CDL results, (total_input_tokens, total_output_tokens), model_name)
        """
        results_content = self.client.retrieve_batch_results(batch_id)
        
        # Check for errors
        error_content = self.client.retrieve_batch_errors(batch_id)
        if error_content:
            error_lines = error_content.strip().split('\n')
            print(f"[WARNING] Batch has {len(error_lines)} error(s):")
            for line in error_lines[:10]:  # Show first 10 errors
                print(f"  {line}")
            if len(error_lines) > 10:
                print(f"  ... and {len(error_lines) - 10} more errors")
        
        results_by_custom_id = self.client.parse_batch_results(results_content)
        
        with open(tracking_file, "r", encoding="utf-8") as f:
            tracking_data = json.load(f)
        
        if verbose:
            print(f"[DEBUG] Tracking file has {len(tracking_data)} entries")
            print(f"[DEBUG] Batch results has {len(results_by_custom_id)} entries")

        total_input_tokens = 0
        total_output_tokens = 0
        model_name: str | None = None

        results_by_file: dict[str, list[dict]] = {}

        for custom_id, tracking in tracking_data.items():
            result = results_by_custom_id.get(custom_id)
            if not result:
                continue

            # Extract model from first successful result
            if model_name is None:
                model_name = result.get("request", {}).get("body", {}).get("model", DEFAULT_CDL_MODEL)

            source_file = tracking["source_file"]

            try:
                response_content = result["response"]["body"]["choices"][0]["message"]["content"]
                json_content = strip_json_code_block(response_content)
                cdl_data = json.loads(json_content)
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"Warning: Failed to parse CDL for {custom_id}: {e}")
                if verbose:
                    print(f"  Raw JSONL response for {custom_id}:")
                    print(json.dumps(result, indent=2))
                cdl_data = {"is_geometric": False, "description": "[Failed]", "is_complete": False}

            if source_file not in results_by_file:
                results_by_file[source_file] = []

            results_by_file[source_file].append({
                "problem_index": tracking["problem_index"],
                "source_type": tracking["source_type"],
                "image_index": tracking["image_index"],
                "is_geometric": cdl_data.get("is_geometric", False),
                "description": cdl_data.get("description", ""),
                "is_complete": cdl_data.get("is_complete", True),
            })
            
            usage = result["response"]["body"]["usage"]
            total_input_tokens += usage["prompt_tokens"]
            total_output_tokens += usage["completion_tokens"]

        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(results_by_file, f, indent=2, ensure_ascii=False)

        return results_by_file, (total_input_tokens, total_output_tokens), model_name or DEFAULT_CDL_MODEL


def combine_results(
    problems_by_file: dict[str, list[ProblemSchema]],
    cdl_by_file: dict[str, list[dict]],
    output_file: Optional[str] = None
) -> dict[str, list[EnrichedProblem]]:
    """
    Combine extraction results with CDL descriptions into final output.
    
    Final structure: {file_path: [{cerinta, barem, imagini: [{url, cdl}], barem_imagini: [{url, cdl}]}]}
    """
    final: dict[str, list[EnrichedProblem]] = {}

    for source_file, problems in problems_by_file.items():
        enriched_problems = []
        
        for problem_idx, problem in enumerate(problems):
            cdl_results = cdl_by_file.get(source_file, [])
            problem_cdls = [r for r in cdl_results if r["problem_index"] == problem_idx]
            
            # Build subject images with CDL
            subject_images = []
            for img_idx, url in enumerate(problem.imagini):
                cdl_result = next(
                    (r for r in problem_cdls if r["source_type"] == "subject" and r["image_index"] == img_idx),
                    None
                )
                try:
                    cdl = CDLSchema(
                        is_geometric=cdl_result.get("is_geometric", False) if cdl_result else False,
                        description=cdl_result.get("description", "[Missing CDL]") if cdl_result else "[Missing CDL]",
                        is_complete=cdl_result.get("is_complete", True) if cdl_result else False,
                    )
                except Exception as e:
                    print(f"Warning: Invalid CDL for {source_file} problem {problem_idx} subject image {img_idx}: {e}")
                    continue
                subject_images.append(ImageWithCDL(url=url, cdl=cdl))
            
            # Build barem images with CDL
            barem_images = []
            if problem.barem:
                for img_idx, url in enumerate(problem.barem.imagini):
                    cdl_result = next(
                        (r for r in problem_cdls if r["source_type"] == "barem" and r["image_index"] == img_idx),
                        None
                    )
                    try:
                        cdl = CDLSchema(
                            is_geometric=cdl_result.get("is_geometric", False) if cdl_result else False,
                            description=cdl_result.get("description", "[Missing CDL]") if cdl_result else "[Missing CDL]",
                            is_complete=cdl_result.get("is_complete", True) if cdl_result else False,
                        )
                    except Exception as e:
                        print(f"Warning: Invalid CDL for {source_file} problem {problem_idx} barem image {img_idx}: {e}")
                        continue
                    barem_images.append(ImageWithCDL(url=url, cdl=cdl))

            enriched_problems.append(EnrichedProblem(
                cerinta=problem.cerinta,
                barem=problem.barem,
                imagini=subject_images,
                barem_imagini=barem_images,
            ))
        
        final[source_file] = enriched_problems

    if output_file:
        output = {
            k: [p.model_dump() for p in v]
            for k, v in final.items()
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

    return final
