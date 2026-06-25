---
name: vision-inventory-workflow
description: "Run the Vision Electronic Indexing workflow for electronics/PCB photos: process images, create parts_to_lookup.json, verify datasheets with web search, fill datasheet_cache.json, regenerate CSV, and summarize uncertainties."
---

# Vision Inventory Workflow

Use this skill when the user wants an agent to produce an enriched electronics BOM from a folder of electronics/PCB images.

## External Dependencies

This package intentionally does **not** bundle these dependencies:

- Python packages from `requirements.txt`: `mcp`, `requests`, `pillow`, `python-dotenv`; optional `pillow-heif` for HEIC/HEIF.
- A Pi web-search/browser tool or skill for datasheet enrichment.
- Cloudflare Workers AI API token credentials.

Use `/vision-inventory-setup` to configure credentials, check/install Python dependencies when approved, and see the web-search/browser requirement warning. Use `/vision-inventory-credentials` to change stored Cloudflare credentials.

## Preferred Command

```text
/vision-inventory-agent-bom <image_folder> <output_dir> [options]
```

Options are forwarded to `scripts/inventory_folder_to_csv.py`, for example `--recursive`, `--limit 3`, `--segment-ics`, `--max-side 0`, and `--jpeg-quality 85`. `--max-side 0` means full resolution for full images and is the default. Use `--segment-ics` when ICs in one image have mixed orientations or upside-down markings; segmented crop submissions are resized to `max_side=250`.

## Agent Rules

- Run the deterministic Python workflow first.
- Read `parts_to_lookup.json`.
- Verify each part against datasheets, preferring official manufacturer pages/PDFs.
- Fill `datasheet_cache.json` using `datasheet_cache.template.json` as the shape.
- Rerun the Python workflow with `--skip-vision`.
- Review `inventory.csv` and `inventory_evidence.csv`.
- Do not invent datasheets, manufacturers, voltages, package names, or descriptions.
- If exact candidate search fails but official results strongly indicate a likely OCR correction, keep the original candidate as the `datasheet_cache.json` key and set `normalized_part` to the official datasheet part number.
- Example: if `SN74AS283N` has no official datasheet but official TI results match `SN74LS283N` and the image could plausibly confuse characters, use key `SN74AS283N`, set `normalized_part` to `SN74LS283N`, and explain the correction in `notes`.
- Only set `verified=true` for corrections when official source evidence and visual/package context make the correction highly likely; otherwise set `verified=false`.
- Set `verified=false` if uncertain and explain in `notes`.
- Preserve raw JSON and evidence files.
