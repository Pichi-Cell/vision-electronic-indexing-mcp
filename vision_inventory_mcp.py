#!/usr/bin/env python3
"""
vision_inventory_mcp.py

Single-file local MCP server for processing electronics / PCB images into
structured visual inventory JSON using Cloudflare Workers AI.

Exposed MCP tools:
  - process_image
  - process_image_folder
  - save_inventory

Version 1 constraints:
  - local stdio MCP server
  - one Python file
  - no database
  - no GUI
  - no part-number web lookup
  - only external request is to Cloudflare Workers AI
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageOps

# Optional .env support. The app still works if python-dotenv is not installed.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

# Optional iPhone HEIC/HEIF support. The app still works if pillow-heif is not installed.
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass

# Support both the official MCP Python SDK import path and the standalone FastMCP package.
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - compatibility fallback
    from fastmcp import FastMCP  # type: ignore


DEFAULT_MODEL = os.getenv("WORKERS_AI_MODEL", "@cf/meta/llama-4-scout-17b-16e-instruct")
DEFAULT_MAX_SIDE = 0
DEFAULT_JPEG_QUALITY = 85
DEFAULT_MAX_TOKENS = 1600
DEFAULT_TEMPERATURE = 0.05
DEFAULT_TOP_P = 0.8

SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
}

SYSTEM_PROMPT = """
You are a careful electronics image-analysis assistant.

Your job is to inspect electronics/PCB images and extract visible inventory information.

Do not perform web lookup.
Do not invent part numbers.
Do not infer missing letters or numbers.
For package markings, transcribe only what is visible.
Use [?] for unclear characters.
If text is blurry or partially hidden, set marking_confidence to "low" or "unreadable".
Prefer uncertainty over guessing.
Return only valid JSON.
""".strip()


def build_default_user_prompt(image_name: str) -> str:
    return f"""
Analyze this electronics image and return an inventory of visible components.

Image filename: {image_name}

Focus especially on IC packages and readable package markings.

Return only valid JSON using this schema:

{{
  "image": "{image_name}",
  "items": [
    {{
      "item_type": "IC | connector | passive | module | switch | sensor | display | mechanical | unknown",
      "count_index": 1,
      "visible_quantity": 1,
      "package_marking": "exact visible marking, unclear, unreadable, or [?]-marked partial text",
      "marking_confidence": "high | medium | low | unreadable",
      "likely_part": "visible part marking only, or unknown",
      "visual_group_id": "G1 or unknown",
      "visual_similarity_to_group": "high | medium | low | unknown",
      "possible_same_as_likely_part": "visible part from a visually similar nearby/grouped IC, or unknown",
      "possible_same_as_confidence": "high | medium | low | none",
      "same_as_reason": "short visual reason for possible same-as grouping, or empty",
      "description": "short visual description, not web lookup",
      "position_hint": "top-left / center / near USB connector / etc.",
      "needs_review": true
    }}
  ],
  "warnings": []
}}

Rules:
- Return JSON only.
- Do not wrap the JSON in markdown.
- Do not identify parts from memory unless the marking is clearly visible.
- Do not use web lookup.
- If a marking is not readable, write "unreadable".
- If a component is visible but not identifiable, item_type should be "unknown".
- count_index is an ordinal item number; visible_quantity is the estimated number of matching visible physical components.
- ICs with the same package/body size, orientation, pin count, board region, and repeated neighboring markings may be assigned the same visual_group_id.
- If an unreadable IC is visually very similar to multiple nearby readable ICs, keep likely_part as "unknown" but set possible_same_as_likely_part to the nearby readable visible part and explain in same_as_reason.
- possible_same_as_likely_part is only a visual grouping hypothesis. Do not use it to overwrite likely_part or package_marking.
- Use possible_same_as_confidence="high" only when repeated neighboring ICs make the same-as hypothesis visually strong.
- needs_review must be true when marking_confidence is "low" or "unreadable", or when possible_same_as_likely_part is used.
""".strip()



mcp = FastMCP("Vision Inventory")


def error_response(message: str, **extra: Any) -> Dict[str, Any]:
    response: Dict[str, Any] = {
        "error": True,
        "message": message,
    }
    response.update(extra)
    return response


def get_cloudflare_credentials() -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = (
        os.getenv("CLOUDFLARE_AUTH_TOKEN", "").strip()
        or os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    )

    if not account_id:
        return None, None, error_response("Missing CLOUDFLARE_ACCOUNT_ID environment variable.")

    if not api_token:
        return None, None, error_response(
            "Missing Cloudflare API token. Set CLOUDFLARE_AUTH_TOKEN or CLOUDFLARE_API_TOKEN."
        )

    return account_id, api_token, None


def validate_image_path(image_path: str) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    if not image_path or not image_path.strip():
        return None, error_response("image_path is required.")

    path = Path(image_path).expanduser()

    if not path.exists():
        return None, error_response(f"Image file does not exist: {image_path}")

    if not path.is_file():
        return None, error_response(f"Path is not a file: {image_path}")

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return None, error_response(
            f"Unsupported image extension '{path.suffix}'. Supported extensions: "
            f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    return path, None


def prepare_image_data_url(
    image_path: Path,
    max_side: int = DEFAULT_MAX_SIDE,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    if max_side and max_side < 128:
        return None, error_response("max_side must be 0 for full resolution or at least 128.")

    if jpeg_quality < 1 or jpeg_quality > 100:
        return None, error_response("jpeg_quality must be between 1 and 100.")

    try:
        image = Image.open(image_path)
        image = ImageOps.exif_transpose(image)

        if max_side:
            resample = getattr(Image, "Resampling", Image).LANCZOS
            image.thumbnail((max_side, max_side), resample)

        # Convert transparency to white background before JPEG encoding.
        if image.mode in ("RGBA", "LA"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            alpha = image.getchannel("A")
            background.paste(image, mask=alpha)
            image = background
        else:
            image = image.convert("RGB")

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{encoded}", None

    except Exception as exc:
        return None, error_response(f"Failed to prepare image: {exc}")


def workers_ai_url(account_id: str, model: str) -> str:
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"


def call_workers_ai(
    image_data_url: str,
    image_name: str,
    user_prompt: str,
    account_id: str,
    api_token: str,
    model: str = DEFAULT_MODEL,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    url = workers_ai_url(account_id, model)
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data_url,
                        },
                    },
                ],
            },
        ],
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": DEFAULT_TEMPERATURE,
        "top_p": DEFAULT_TOP_P,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=180)
    except requests.RequestException as exc:
        return None, error_response(f"Cloudflare request failed: {exc}", image=image_name)

    try:
        data = response.json()
    except ValueError:
        return None, error_response(
            "Cloudflare returned a non-JSON response.",
            http_status=response.status_code,
            body=response.text[:2000],
            image=image_name,
        )

    if not response.ok or not data.get("success", False):
        return None, error_response(
            "Cloudflare Workers AI request failed.",
            http_status=response.status_code,
            errors=data.get("errors", []),
            messages=data.get("messages", []),
            image=image_name,
        )

    result = data.get("result", {})

    # Most Workers AI text-generation model responses include result.response.
    if isinstance(result, dict):
        if isinstance(result.get("response"), str):
            return result["response"], None

        # Some OpenAI-style responses may include choices.
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = message.get("content")
            if isinstance(content, str):
                return content, None

        return json.dumps(result), None

    if isinstance(result, str):
        return result, None

    return str(result), None


def strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    return cleaned


def extract_json_object(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Extract the first JSON object from model text."""
    cleaned = strip_markdown_fences(text)

    # Fast path: whole response is valid JSON.
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed, None
        return None, "Model returned JSON, but it was not an object."
    except json.JSONDecodeError:
        pass

    # Robust path: find the first object starting with '{' and decode from there.
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(cleaned[index:])
            if isinstance(parsed, dict):
                return parsed, None
        except json.JSONDecodeError:
            continue

    return None, "Model returned invalid JSON."


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y"}
    return bool(value)


def normalize_item(item: Any, fallback_index: int) -> Dict[str, Any]:
    default_item: Dict[str, Any] = {
        "item_type": "unknown",
        "count_index": fallback_index,
        "visible_quantity": 1,
        "package_marking": "unknown",
        "marking_confidence": "unreadable",
        "likely_part": "unknown",
        "visual_group_id": "unknown",
        "visual_similarity_to_group": "unknown",
        "possible_same_as_likely_part": "unknown",
        "possible_same_as_confidence": "none",
        "same_as_reason": "",
        "description": "unknown",
        "position_hint": "unknown",
        "needs_review": True,
    }

    if not isinstance(item, dict):
        return default_item

    normalized = dict(default_item)
    normalized.update({k: v for k, v in item.items() if k in normalized})

    try:
        normalized["count_index"] = int(normalized.get("count_index", fallback_index))
    except Exception:
        normalized["count_index"] = fallback_index

    try:
        normalized["visible_quantity"] = max(1, int(normalized.get("visible_quantity", 1)))
    except Exception:
        normalized["visible_quantity"] = 1

    confidence = str(normalized.get("marking_confidence", "unreadable")).strip().lower()
    if confidence not in {"high", "medium", "low", "unreadable"}:
        confidence = "low"
    normalized["marking_confidence"] = confidence

    visual_similarity = str(normalized.get("visual_similarity_to_group", "unknown")).strip().lower()
    if visual_similarity not in {"high", "medium", "low", "unknown"}:
        visual_similarity = "unknown"
    normalized["visual_similarity_to_group"] = visual_similarity

    same_as_confidence = str(normalized.get("possible_same_as_confidence", "none")).strip().lower()
    if same_as_confidence not in {"high", "medium", "low", "none"}:
        same_as_confidence = "none"
    normalized["possible_same_as_confidence"] = same_as_confidence

    normalized["needs_review"] = coerce_bool(normalized.get("needs_review", True))
    if confidence in {"low", "unreadable"}:
        normalized["needs_review"] = True
    if same_as_confidence != "none" and str(normalized.get("possible_same_as_likely_part", "unknown")).strip().lower() not in {"", "unknown", "unreadable", "none", "n/a"}:
        normalized["needs_review"] = True

    # Ensure string fields are strings and not nulls/lists.
    for key in [
        "item_type",
        "package_marking",
        "likely_part",
        "visual_group_id",
        "possible_same_as_likely_part",
        "same_as_reason",
        "description",
        "position_hint",
    ]:
        value = normalized.get(key)
        if value is None:
            normalized[key] = "unknown"
        elif not isinstance(value, str):
            normalized[key] = str(value)
        elif not value.strip():
            normalized[key] = "unknown"

    return normalized


def normalize_ic_marking_observations(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []

    observations: List[Dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        confidence = str(entry.get("marking_confidence", "unreadable")).strip().lower()
        if confidence not in {"high", "medium", "low", "unreadable"}:
            confidence = "low"
        observations.append({
            "position_hint": str(entry.get("position_hint", "unknown") or "unknown"),
            "package_marking": str(entry.get("package_marking", "unknown") or "unknown"),
            "marking_confidence": confidence,
        })
    return observations


def normalize_inventory_result(result: Dict[str, Any], image_name: str) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {
        "image": image_name,
        "items": [],
        "warnings": [],
    }

    if isinstance(result.get("image"), str) and result["image"].strip():
        # Keep only the basename to avoid leaking full local paths through model output.
        normalized["image"] = Path(result["image"]).name

    raw_items = result.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []
        normalized["warnings"].append("Model response did not contain a valid items list.")

    normalized["items"] = [normalize_item(item, idx + 1) for idx, item in enumerate(raw_items)]

    raw_warnings = result.get("warnings", [])
    if isinstance(raw_warnings, list):
        normalized["warnings"].extend(str(w) for w in raw_warnings if str(w).strip())
    elif isinstance(raw_warnings, str) and raw_warnings.strip():
        normalized["warnings"].append(raw_warnings.strip())

    observations = normalize_ic_marking_observations(result.get("ic_marking_observations"))
    if observations:
        normalized["ic_marking_observations"] = observations

    return normalized


def process_image_impl(
    image_path: str,
    max_side: int = DEFAULT_MAX_SIDE,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    custom_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    image_file, validation_error = validate_image_path(image_path)
    if validation_error:
        return validation_error
    assert image_file is not None

    account_id, api_token, credential_error = get_cloudflare_credentials()
    if credential_error:
        return credential_error
    assert account_id is not None and api_token is not None

    image_data_url, image_error = prepare_image_data_url(
        image_file,
        max_side=max_side,
        jpeg_quality=jpeg_quality,
    )
    if image_error:
        return image_error
    assert image_data_url is not None

    image_name = image_file.name
    prompt = custom_prompt.strip() if custom_prompt and custom_prompt.strip() else build_default_user_prompt(image_name)

    response_text, cloudflare_error = call_workers_ai(
        image_data_url=image_data_url,
        image_name=image_name,
        user_prompt=prompt,
        account_id=account_id,
        api_token=api_token,
        model=DEFAULT_MODEL,
    )
    if cloudflare_error:
        return cloudflare_error
    assert response_text is not None

    parsed, parse_error = extract_json_object(response_text)
    if parse_error or parsed is None:
        message = parse_error or "Model returned invalid JSON."
        return {
            "image": image_name,
            "items": [],
            "warnings": [message],
            "parse_error": True,
            "parse_error_message": message,
            "raw_response_preview": response_text[:2000],
            "raw_response_length": len(response_text),
            "raw_response": response_text,
        }

    return normalize_inventory_result(parsed, image_name)


@mcp.tool()
def process_image(
    image_path: str,
    max_side: int = DEFAULT_MAX_SIDE,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    custom_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analyze one electronics/PCB image and return structured visible inventory data.

    This tool does not do web lookup or datasheet lookup. It only extracts visible
    components and markings from the image.
    """
    return process_image_impl(
        image_path=image_path,
        max_side=max_side,
        jpeg_quality=jpeg_quality,
        custom_prompt=custom_prompt,
    )


def find_images_in_folder(folder: Path, recursive: bool, limit: Optional[int]) -> List[Path]:
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    images = sorted(
        [p for p in iterator if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS],
        key=lambda p: str(p).lower(),
    )

    if limit is not None:
        images = images[: max(0, int(limit))]

    return images


@mcp.tool()
def process_image_folder(
    folder_path: str,
    recursive: bool = False,
    max_side: int = DEFAULT_MAX_SIDE,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Process all supported images in a folder and return a combined inventory object.

    Individual image failures are included in the errors list; the folder operation
    continues processing the remaining images.
    """
    if not folder_path or not folder_path.strip():
        return error_response("folder_path is required.")

    folder = Path(folder_path).expanduser()
    if not folder.exists():
        return error_response(f"Folder does not exist: {folder_path}")
    if not folder.is_dir():
        return error_response(f"Path is not a folder: {folder_path}")

    try:
        images = find_images_in_folder(folder, recursive=recursive, limit=limit)
    except Exception as exc:
        return error_response(f"Failed to scan folder: {exc}")

    if not images:
        return {
            "source_folder": str(folder),
            "image_count": 0,
            "processed_count": 0,
            "failed_count": 0,
            "results": [],
            "errors": [],
            "warnings": ["No supported image files found in folder."],
        }

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for image_path in images:
        try:
            result = process_image_impl(
                image_path=str(image_path),
                max_side=max_side,
                jpeg_quality=jpeg_quality,
            )

            if result.get("error"):
                errors.append({
                    "image": image_path.name,
                    "error": result.get("message", "Unknown error"),
                    "details": result,
                })
            else:
                results.append(result)

        except Exception as exc:
            errors.append({
                "image": image_path.name,
                "error": str(exc),
            })

    return {
        "source_folder": str(folder),
        "image_count": len(images),
        "processed_count": len(results),
        "failed_count": len(errors),
        "results": results,
        "errors": errors,
    }


def count_inventory_rows(inventory: Dict[str, Any]) -> int:
    if isinstance(inventory.get("items"), list):
        return len(inventory["items"])

    results = inventory.get("results", [])
    if isinstance(results, list):
        count = 0
        for result in results:
            if isinstance(result, dict) and isinstance(result.get("items"), list):
                count += len(result["items"])
        return count

    return 0


def flatten_inventory_for_csv(inventory: Dict[str, Any], enrichment_cache: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Flatten raw vision output into BOM-style, likely-part-deduped CSV rows.

    This is intentionally less complete than scripts/inventory_folder_to_csv.py
    because the save tool only receives in-memory vision output. If a
    datasheet_cache.json object is provided, matching enrichment fields are used.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    cache = enrichment_cache or {}

    if isinstance(inventory.get("items"), list):
        image_results = [inventory]
    else:
        raw_results = inventory.get("results", [])
        image_results = raw_results if isinstance(raw_results, list) else []

    for result in image_results:
        if not isinstance(result, dict):
            continue

        image_name = str(result.get("image", "unknown"))
        items = result.get("items", [])
        if not isinstance(items, list):
            continue

        by_image_part: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("item_type", "")).strip().lower() != "ic":
                continue

            candidate = str(item.get("likely_part") or item.get("package_marking") or "unknown").strip().upper()
            visual_same_as = False
            if not candidate or candidate.lower() in {"unknown", "unreadable", "unclear", "none", "n/a"}:
                same_as_confidence = str(item.get("possible_same_as_confidence", "none")).strip().lower()
                possible_same_as = str(item.get("possible_same_as_likely_part") or "unknown").strip().upper()
                if same_as_confidence == "high" and possible_same_as.lower() not in {"unknown", "unreadable", "unclear", "none", "n/a"}:
                    candidate = possible_same_as
                    visual_same_as = True
                else:
                    continue
            enrichment = cache.get(candidate, {}) if isinstance(cache.get(candidate, {}), dict) else {}
            normalized = str(enrichment.get("normalized_part") or candidate).strip().upper()
            key = (image_name, normalized)
            row = by_image_part.setdefault(key, {
                "image": image_name,
                "normalized_part": normalized,
                "candidate_parts": set(),
                "amount": 0,
                "vision_confidence": set(),
                "needs_review": False,
                "observed_markings": set(),
                "notes": set(),
            })
            row["candidate_parts"].add(candidate)
            row["vision_confidence"].add(str(item.get("marking_confidence", "unknown")))
            row["needs_review"] = bool(row["needs_review"] or item.get("needs_review", True) or visual_same_as)
            # Keep the main part number as the observation, not the full package/date/lot marking.
            row["observed_markings"].add(normalized)
            if visual_same_as:
                reason = str(item.get("same_as_reason") or "Unreadable IC visually matches nearby readable repeated ICs.").strip()
                row["notes"].add(f"Visual same-as hypothesis for unreadable IC: {reason}")
            try:
                if "visible_quantity" in item:
                    row["amount"] = int(row["amount"]) + max(1, int(item.get("visible_quantity", 1)))
                else:
                    row["amount"] = max(int(row["amount"]), int(item.get("count_index", 1)))
            except Exception:
                row["amount"] = max(int(row["amount"]), 1)

        for row in by_image_part.values():
            grouped.setdefault(str(row["normalized_part"]), []).append(row)

    rows: List[Dict[str, Any]] = []
    for part, part_rows in sorted(grouped.items()):
        enrichment = cache.get(part, {}) if isinstance(cache.get(part, {}), dict) else {}
        rows.append({
            "normalized_part": part,
            "candidate_parts": ", ".join(sorted({candidate for row in part_rows for candidate in row["candidate_parts"]})),
            "amount": sum(int(row.get("amount", 0) or 0) for row in part_rows),
            "sighting_count": len(part_rows),
            "description": enrichment.get("description", ""),
            "datasheet_url": enrichment.get("datasheet_url", ""),
            "manufacturer": enrichment.get("manufacturer", ""),
            "verified": bool(enrichment.get("verified", False)),
            "vision_confidence": "/".join(sorted({value for row in part_rows for value in row["vision_confidence"]})),
            "needs_review": any(bool(row.get("needs_review", True)) for row in part_rows) or not bool(enrichment.get("verified", False)),
            "images": " | ".join(sorted({str(row["image"]) for row in part_rows})),
            "observed_markings": " | ".join(sorted({marking for row in part_rows for marking in row["observed_markings"]})),
            "raw_json": "",
            "notes": " | ".join(
                [str(enrichment.get("notes", "Missing datasheet enrichment"))]
                + sorted({note for row in part_rows for note in row.get("notes", set())})
            ),
        })

    return rows


@mcp.tool()
def save_inventory(
    inventory: Dict[str, Any],
    output_path: str,
    format: str = "json",
) -> Dict[str, Any]:
    """
    Save inventory results to disk as JSON or quick CSV export.

    The input inventory can be the result of process_image or process_image_folder.
    CSV output from this tool is a quick export; use scripts/inventory_folder_to_csv.py
    or /vision-inventory-bom for the full BOM workflow with raw evidence files,
    parts_to_lookup.json, datasheet_cache.json, and inventory_evidence.csv.
    """
    if not isinstance(inventory, dict):
        return error_response("inventory must be an object/dict.")

    if not output_path or not output_path.strip():
        return error_response("output_path is required.")

    output = Path(output_path).expanduser()
    fmt = format.strip().lower()

    if fmt not in {"json", "csv"}:
        return error_response("format must be either 'json' or 'csv'.")

    try:
        output.parent.mkdir(parents=True, exist_ok=True)

        if fmt == "json":
            output.write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8")
            row_count = count_inventory_rows(inventory)

        else:
            cache_path = output.parent / "datasheet_cache.json"
            enrichment_cache: Dict[str, Any] = {}
            if cache_path.exists():
                try:
                    loaded_cache = json.loads(cache_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_cache, dict):
                        enrichment_cache = loaded_cache
                except Exception:
                    enrichment_cache = {}

            rows = flatten_inventory_for_csv(inventory, enrichment_cache)
            fieldnames = [
                "normalized_part",
                "candidate_parts",
                "amount",
                "sighting_count",
                "description",
                "datasheet_url",
                "manufacturer",
                "verified",
                "vision_confidence",
                "needs_review",
                "images",
                "observed_markings",
                "raw_json",
                "notes",
            ]

            with output.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            row_count = len(rows)

        response = {
            "saved": True,
            "output_path": str(output),
            "format": fmt,
            "row_count": row_count,
        }
        if fmt == "csv":
            response["note"] = (
                "This is a quick CSV export. Use scripts/inventory_folder_to_csv.py "
                "or /vision-inventory-bom for the full BOM workflow with raw evidence, "
                "parts_to_lookup.json, datasheet_cache.json, and inventory_evidence.csv."
            )
        return response

    except Exception as exc:
        return error_response(f"Failed to save inventory: {exc}", output_path=str(output), format=fmt)


if __name__ == "__main__":
    mcp.run(transport="stdio")
