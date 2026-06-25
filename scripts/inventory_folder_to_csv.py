#!/usr/bin/env python3
"""Batch vision inventory workflow with agent-assisted datasheet enrichment.

Option A workflow:
  1. Process every image in a folder with vision_inventory_mcp.py.
  2. Save one raw JSON file per image for auditability.
  3. Build parts_to_lookup.json for the agent/user to enrich from datasheets.
  4. If datasheet_cache.json exists, write a final enriched CSV.

The script intentionally does not browse the web. Fill datasheet_cache.json manually or with Pi
web-search assistance, then rerun this script with --skip-vision to regenerate the CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import vision_inventory_mcp as vision  # noqa: E402

UNKNOWN_MARKINGS = {"", "unknown", "unreadable", "unclear", "none", "n/a"}
PART_PATTERN = re.compile(r"\b[A-Z]{1,4}[A-Z0-9]{2,}[A-Z0-9?\[\]-]*\b", re.IGNORECASE)


def safe_stem(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.stem).strip("_")
    return stem or "image"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def is_supported_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in vision.SUPPORTED_EXTENSIONS


def iter_images(folder: Path, recursive: bool, limit: Optional[int]) -> List[Path]:
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    images = sorted([p for p in iterator if is_supported_image(p)], key=lambda p: str(p).lower())
    return images[:limit] if limit is not None else images


def clean_marking(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def likely_base_part(marking: str) -> str:
    """Extract the most likely device part from a longer package marking line."""
    cleaned = marking.replace("[?]", "?").upper()
    matches = PART_PATTERN.findall(cleaned)
    if not matches:
        return cleaned.strip()

    # Prefer tokens that look like common IC identifiers and contain digits.
    candidates = [m.strip("-_") for m in matches if any(ch.isdigit() for ch in m)]
    if not candidates:
        candidates = matches

    # Date/lot codes are usually short and mostly numeric; prefer longer alphanumeric tokens.
    candidates.sort(key=lambda s: (len(re.sub(r"[^A-Z]", "", s)) > 0, len(s)), reverse=True)
    return candidates[0]


def candidate_from_item(item: Dict[str, Any]) -> str:
    for key in ("likely_part", "package_marking"):
        value = clean_marking(item.get(key))
        if value.lower() not in UNKNOWN_MARKINGS:
            return likely_base_part(value)
    return "unknown"


def extract_part_evidence(image_name: str, result: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []

    items = result.get("items", [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("item_type", "")).strip().lower() != "ic":
                continue
            marking = clean_marking(item.get("package_marking"))
            if marking.lower() in UNKNOWN_MARKINGS:
                continue
            evidence.append({
                "image": image_name,
                "source": "items",
                "position_hint": item.get("position_hint", "unknown"),
                "observed_marking": marking,
                "candidate_part": candidate_from_item(item),
                "marking_confidence": item.get("marking_confidence", "unknown"),
                "needs_review": bool(item.get("needs_review", True)),
            })

    observations = result.get("ic_marking_observations", [])
    if isinstance(observations, list):
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            marking = clean_marking(obs.get("package_marking"))
            if marking.lower() in UNKNOWN_MARKINGS:
                continue
            evidence.append({
                "image": image_name,
                "source": "ic_marking_observations",
                "position_hint": obs.get("position_hint", "unknown"),
                "observed_marking": marking,
                "candidate_part": likely_base_part(marking),
                "marking_confidence": obs.get("marking_confidence", "unknown"),
                "needs_review": str(obs.get("marking_confidence", "")).lower() in {"low", "unreadable"},
            })

    return evidence


def preflight_credentials() -> None:
    _account_id, _api_token, credential_error = vision.get_cloudflare_credentials()
    if credential_error:
        raise SystemExit(
            f"{credential_error.get('message', 'Missing Cloudflare credentials.')} "
            "Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_AUTH_TOKEN/CLOUDFLARE_API_TOKEN, "
            "or run /vision-inventory-setup in Pi."
        )


def process_images(args: argparse.Namespace, raw_dir: Path) -> List[Dict[str, Any]]:
    image_folder = Path(args.image_folder).expanduser().resolve()
    if not image_folder.is_dir():
        raise SystemExit(f"Image folder does not exist or is not a directory: {image_folder}")

    images = iter_images(image_folder, args.recursive, args.limit)
    if not images:
        print(f"No supported image files found in {image_folder}.")
        return []

    preflight_credentials()

    results: List[Dict[str, Any]] = []
    for image_path in images:
        print(f"Processing {image_path}")
        result = vision.process_image_impl(
            image_path=str(image_path),
            max_side=args.max_side,
            jpeg_quality=args.jpeg_quality,
        )
        raw_path = raw_dir / f"{safe_stem(image_path)}.json"
        write_json(raw_path, result)
        results.append({"image_path": str(image_path), "raw_json": str(raw_path), "result": result})

    return results


def load_raw_results(raw_dir: Path) -> List[Dict[str, Any]]:
    results = []
    for raw_path in sorted(raw_dir.glob("*.json")):
        result = load_json(raw_path, {})
        results.append({"image_path": result.get("image", raw_path.stem), "raw_json": str(raw_path), "result": result})
    return results


def classify_error(result: Dict[str, Any]) -> str:
    message = str(result.get("message") or result.get("error") or "Unknown error")
    lowered = message.lower()
    if "credential" in lowered or "cloudflare_account_id" in lowered or "api token" in lowered:
        return "credential errors"
    if "cloudflare" in lowered or "workers ai" in lowered:
        return "cloudflare api errors"
    if "prepare image" in lowered or "unsupported image" in lowered or "image file" in lowered:
        return "image preprocessing/input errors"
    if result.get("parse_error"):
        return "model json parse errors"
    return "other errors"


def result_error_summary(results: List[Dict[str, Any]]) -> Counter[str]:
    summary: Counter[str] = Counter()
    for entry in results:
        result = entry.get("result", {})
        if isinstance(result, dict) and (result.get("error") or result.get("parse_error")):
            summary[classify_error(result)] += 1
    return summary


def build_parts_to_lookup(results: List[Dict[str, Any]], output_dir: Path) -> Dict[str, Any]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    all_evidence: List[Dict[str, Any]] = []

    for entry in results:
        result = entry["result"]
        image_name = str(result.get("image") or Path(entry["image_path"]).name)
        evidence = extract_part_evidence(image_name, result)
        all_evidence.extend(evidence)
        for row in evidence:
            part = row["candidate_part"].upper()
            if part and part.lower() not in UNKNOWN_MARKINGS:
                grouped[part].append(row)

    parts = []
    for part, evidence_rows in sorted(grouped.items()):
        observed = sorted({row["observed_marking"] for row in evidence_rows})
        images = sorted({row["image"] for row in evidence_rows})
        parts.append({
            "part": part,
            "query": f"{part} datasheet",
            "status": "needs_datasheet_lookup",
            "observed_markings": observed,
            "images": images,
            "evidence": evidence_rows,
            "enrichment_template": {
                "normalized_part": part,
                "description": "",
                "datasheet_url": "",
                "manufacturer": "",
                "verified": False,
                "notes": ""
            }
        })

    warnings: List[str] = []
    if not parts:
        warnings.append(
            "No candidate IC parts were extracted. Inspect raw JSON files for unreadable markings, "
            "image quality issues, or model/API errors."
        )

    return {
        "output_dir": str(output_dir),
        "datasheet_cache_path": str(output_dir / "datasheet_cache.json"),
        "datasheet_cache_template_path": str(output_dir / "datasheet_cache.template.json"),
        "instructions": [
            "Use web search to find each part datasheet, preferably from the manufacturer.",
            "Fill datasheet_cache.json in this same output directory, using datasheet_cache.template.json as the shape.",
            "Keep descriptions short, e.g. '74ls (4 bit) adder low power schottky ttl 5v DIP'.",
            "If exact candidate search fails but official results strongly indicate a likely OCR correction, keep the original candidate as this cache key and set normalized_part to the official datasheet part number.",
            "Example: if SN74AS283N appears to be an OCR error for official SN74LS283N, use key SN74AS283N with normalized_part SN74LS283N and explain the correction in notes.",
            "Only mark verified=true for a correction when the official datasheet and visual/package context make the correction highly likely; otherwise set verified=false and explain in notes.",
            "If the visual marking is uncertain, set verified=false and explain in notes."
        ],
        "warnings": warnings,
        "parts": parts,
        "all_evidence": all_evidence,
    }


def build_cache_template(parts_to_lookup: Dict[str, Any]) -> Dict[str, Any]:
    cache = {}
    for part in parts_to_lookup.get("parts", []):
        key = part["part"]
        cache[key] = part["enrichment_template"]
    return cache


def lookup_enrichment(part: str, cache: Dict[str, Any]) -> Dict[str, Any]:
    if part in cache and isinstance(cache[part], dict):
        return cache[part]
    upper = part.upper()
    if upper in cache and isinstance(cache[upper], dict):
        return cache[upper]
    return {}


def positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def estimate_amount_for_candidate(result: Dict[str, Any], candidate: str, evidence_count: int = 1) -> int:
    """Estimate physical IC quantity for one candidate in one image.

    Prefer explicit visible_quantity when the model provides it. Older results only
    have count_index, which may be either an ordinal index or a grouped count, so
    the fallback remains heuristic and should be reviewed for important BOMs.
    """
    items = result.get("items", [])
    if not isinstance(items, list):
        return max(1, evidence_count)

    matched = 0
    count_values: List[int] = []
    visible_quantities: List[int] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("item_type", "")).strip().lower() != "ic":
            continue
        if candidate_from_item(item).upper() != candidate.upper():
            continue
        matched += 1
        visible_quantity = positive_int(item.get("visible_quantity"))
        if visible_quantity is not None:
            visible_quantities.append(visible_quantity)
        count_index = positive_int(item.get("count_index"))
        if count_index is not None:
            count_values.append(count_index)

    if visible_quantities:
        return max(1, sum(visible_quantities))

    return max([1, evidence_count, matched, *count_values])


def image_part_rows(results: List[Dict[str, Any]], cache: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for entry in results:
        result = entry["result"]
        image_name = str(result.get("image") or Path(entry["image_path"]).name)
        evidence = extract_part_evidence(image_name, result)
        if not evidence:
            notes = "No IC marking extracted"
            if isinstance(result, dict) and result.get("error"):
                notes = f"Vision processing error: {result.get('message', 'unknown error')}"
            elif isinstance(result, dict) and result.get("warnings"):
                notes = "; ".join(str(w) for w in result.get("warnings", []) if str(w).strip()) or notes
            rows.append({
                "image": image_name,
                "candidate_part": "",
                "normalized_part": "",
                "amount": 0,
                "description": "",
                "datasheet_url": "",
                "manufacturer": "",
                "verified": False,
                "vision_confidence": "unreadable",
                "needs_review": True,
                "observed_markings": "",
                "observations": "",
                "raw_json": entry["raw_json"],
                "notes": notes,
            })
            continue

        # One image may contain multiple different IC candidates. Emit one
        # evidence row per candidate instead of forcing a single image-level part.
        evidence_by_candidate: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in evidence:
            candidate = row["candidate_part"].upper()
            if candidate and candidate.lower() not in UNKNOWN_MARKINGS:
                evidence_by_candidate[candidate].append(row)

        for candidate, candidate_evidence in sorted(evidence_by_candidate.items()):
            enrichment = lookup_enrichment(candidate, cache)
            likely_part = str(enrichment.get("normalized_part") or candidate).strip().upper()
            amount = estimate_amount_for_candidate(result, candidate, evidence_count=len(candidate_evidence))
            # Keep observed_markings normalized to the main visible part number, not full date/lot/package text.
            observed_markings = [likely_part]
            observations = "; ".join(
                f"{row['position_hint']}: {row['observed_marking']} ({row['marking_confidence']})"
                for row in candidate_evidence
            )
            confidence_values = [str(row.get("marking_confidence", "unknown")) for row in candidate_evidence]
            needs_review = any(row.get("needs_review", True) for row in candidate_evidence) or not enrichment.get("verified", False)

            rows.append({
                "image": image_name,
                "candidate_part": candidate,
                "normalized_part": likely_part,
                "amount": amount,
                "description": enrichment.get("description", ""),
                "datasheet_url": enrichment.get("datasheet_url", ""),
                "manufacturer": enrichment.get("manufacturer", ""),
                "verified": bool(enrichment.get("verified", False)),
                "vision_confidence": "/".join(sorted(set(confidence_values))),
                "needs_review": needs_review,
                "observed_markings": " | ".join(observed_markings),
                "observations": observations,
                "raw_json": entry["raw_json"],
                "notes": enrichment.get("notes", "Missing datasheet enrichment"),
            })
    return rows


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_final_csv(results: List[Dict[str, Any]], cache: Dict[str, Any], output_csv: Path) -> None:
    """Write the default deduplicated BOM CSV and a per-image evidence CSV."""
    evidence_rows = image_part_rows(results, cache)
    evidence_fieldnames = [
        "image",
        "candidate_part",
        "normalized_part",
        "amount",
        "description",
        "datasheet_url",
        "manufacturer",
        "verified",
        "vision_confidence",
        "needs_review",
        "observed_markings",
        "observations",
        "raw_json",
        "notes",
    ]

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    no_part_rows: List[Dict[str, Any]] = []
    for row in evidence_rows:
        part = str(row.get("normalized_part") or row.get("candidate_part") or "").strip().upper()
        if not part:
            no_part_rows.append(row)
        else:
            grouped[part].append(row)

    bom_rows: List[Dict[str, Any]] = []
    for part, rows_for_part in sorted(grouped.items()):
        first = rows_for_part[0]
        images = sorted({str(row["image"]) for row in rows_for_part})
        observed_markings = sorted({marking for row in rows_for_part for marking in str(row["observed_markings"]).split(" | ") if marking})
        raw_json_files = sorted({str(row["raw_json"]) for row in rows_for_part})
        confidence_values = sorted({value for row in rows_for_part for value in str(row["vision_confidence"]).split("/") if value})
        notes = sorted({str(row.get("notes", "")) for row in rows_for_part if str(row.get("notes", "")).strip()})
        amount = sum(int(row.get("amount", 0) or 0) for row in rows_for_part)

        bom_rows.append({
            "normalized_part": part,
            "candidate_parts": ", ".join(sorted({str(row["candidate_part"]) for row in rows_for_part if row.get("candidate_part")})),
            "amount": amount,
            "sighting_count": len(rows_for_part),
            "description": first.get("description", ""),
            "datasheet_url": first.get("datasheet_url", ""),
            "manufacturer": first.get("manufacturer", ""),
            "verified": all(bool(row.get("verified", False)) for row in rows_for_part),
            "vision_confidence": "/".join(confidence_values),
            "needs_review": any(bool(row.get("needs_review", True)) for row in rows_for_part),
            "images": " | ".join(images),
            "observed_markings": " | ".join(observed_markings),
            "raw_json": " | ".join(raw_json_files),
            "notes": " | ".join(notes),
        })

    for row in no_part_rows:
        bom_rows.append({
            "normalized_part": "",
            "candidate_parts": "",
            "amount": 0,
            "sighting_count": 1,
            "description": "",
            "datasheet_url": "",
            "manufacturer": "",
            "verified": False,
            "vision_confidence": row.get("vision_confidence", "unreadable"),
            "needs_review": True,
            "images": row.get("image", ""),
            "observed_markings": "",
            "raw_json": row.get("raw_json", ""),
            "notes": row.get("notes", "No IC marking extracted"),
        })

    bom_fieldnames = [
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
    write_csv(output_csv, bom_fieldnames, bom_rows)
    write_csv(output_csv.with_name(f"{output_csv.stem}_evidence{output_csv.suffix}"), evidence_fieldnames, evidence_rows)


def validate_setup(args: argparse.Namespace, output_dir: Path) -> None:
    image_folder = Path(args.image_folder).expanduser().resolve()
    print("Setup validation:")
    print(f"- Python executable: {sys.executable}")
    print("- Required imports: ok")

    if image_folder.is_dir():
        images = iter_images(image_folder, args.recursive, args.limit)
        print(f"- Image folder: ok ({image_folder})")
        print(f"- Supported images found: {len(images)}")
        heic_images = [p for p in images if p.suffix.lower() in {".heic", ".heif"}]
        if heic_images:
            try:
                import pillow_heif  # noqa: F401
                print("- HEIC/HEIF support: ok")
            except Exception:
                print("- HEIC/HEIF support: missing pillow-heif; install it to process HEIC/HEIF images")
    else:
        print(f"- Image folder: missing or not a directory ({image_folder})")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".vision_inventory_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        print(f"- Output directory writable: ok ({output_dir})")
    except Exception as exc:
        print(f"- Output directory writable: failed ({exc})")

    if args.skip_vision:
        raw_dir = output_dir / "raw"
        raw_count = len(list(raw_dir.glob("*.json"))) if raw_dir.exists() else 0
        print(f"- Raw JSON files for --skip-vision: {raw_count}")
    else:
        _account_id, _api_token, credential_error = vision.get_cloudflare_credentials()
        if credential_error:
            print(f"- Cloudflare credentials: missing ({credential_error.get('message')})")
        else:
            print("- Cloudflare credentials: present")


def print_workflow_summary(results: List[Dict[str, Any]], parts_to_lookup: Dict[str, Any]) -> None:
    error_summary = result_error_summary(results)
    evidence_count = len(parts_to_lookup.get("all_evidence", []))
    part_count = len(parts_to_lookup.get("parts", []))
    print("Workflow summary:")
    print(f"- Processed/raw result files: {len(results)}")
    print(f"- IC marking evidence rows: {evidence_count}")
    print(f"- Candidate parts extracted: {part_count}")
    if error_summary:
        print("- Processing errors:")
        for label, count in sorted(error_summary.items()):
            print(f"  - {label}: {count}")
    if not part_count:
        print("No candidate IC parts were extracted. Inspect output/raw/*.json for unreadable markings, image quality issues, or API errors.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process electronics images and prepare datasheet-enriched CSV workflow.")
    parser.add_argument("image_folder", help="Folder containing electronics/PCB images")
    parser.add_argument("output_dir", help="Output directory for raw JSON, lookup files, and CSV")
    parser.add_argument("--csv", default="inventory.csv", help="CSV filename/path, relative to output_dir unless absolute")
    parser.add_argument("--recursive", action="store_true", help="Scan image_folder recursively")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of images to process")
    parser.add_argument("--skip-vision", action="store_true", help="Reuse existing output_dir/raw/*.json instead of calling vision AI")
    parser.add_argument("--validate-setup", action="store_true", help="Check dependencies, paths, credentials, and image discovery without processing images")
    parser.add_argument("--max-side", type=int, default=vision.DEFAULT_MAX_SIDE, help="Maximum resized image side; use 0 for full resolution (default)")
    parser.add_argument("--jpeg-quality", type=int, default=vision.DEFAULT_JPEG_QUALITY, help="JPEG quality for model input")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()

    if args.validate_setup:
        validate_setup(args, output_dir)
        return

    raw_dir = output_dir / "raw"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.skip_vision:
        results = load_raw_results(raw_dir)
        if not results:
            raise SystemExit(f"No raw JSON files found in {raw_dir}")
    else:
        results = process_images(args, raw_dir)

    parts_to_lookup = build_parts_to_lookup(results, output_dir)
    parts_path = output_dir / "parts_to_lookup.json"
    template_path = output_dir / "datasheet_cache.template.json"
    cache_path = output_dir / "datasheet_cache.json"
    write_json(parts_path, parts_to_lookup)
    write_json(template_path, build_cache_template(parts_to_lookup))

    cache = load_json(cache_path, {})
    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = output_dir / csv_path
    write_final_csv(results, cache, csv_path)

    print(f"Raw results: {raw_dir}")
    print(f"Parts to lookup: {parts_path}")
    print(f"Datasheet cache template: {template_path}")
    print(f"Datasheet cache used: {cache_path if cache_path.exists() else 'not found yet'}")
    print(f"CSV written: {csv_path}")
    print_workflow_summary(results, parts_to_lookup)
    if not cache_path.exists():
        print("Next step: copy datasheet_cache.template.json to datasheet_cache.json in the output directory, enrich it via web search, then rerun with --skip-vision.")

    errors = result_error_summary(results)
    if results and sum(errors.values()) == len(results):
        raise SystemExit("All processed images returned errors; see the summary above and inspect raw JSON files.")


if __name__ == "__main__":
    main()
