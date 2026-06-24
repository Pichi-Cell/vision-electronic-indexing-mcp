---
name: vision-inventory-workflow
description: Run the Vision Electronic Indexing workflow for electronics/PCB photos: process images, create parts_to_lookup.json, verify datasheets with web search, fill datasheet_cache.json, regenerate CSV, and summarize uncertainties.
---

# Vision Inventory Workflow

Use this skill when the user wants an agent to produce an enriched electronics BOM from a folder of electronics/PCB images.

## External Dependencies

This package intentionally does **not** bundle these dependencies:

- Python packages from `requirements.txt`: `mcp`, `requests`, `pillow`, `python-dotenv`; optional `pillow-heif` for HEIC/HEIF.
- A Pi web-search/browser tool or skill for datasheet enrichment.
- Cloudflare Workers AI credentials.

Use `/vision-inventory-setup` to configure credentials and check/install Python dependencies. Use `/vision-inventory-credentials` to change stored Cloudflare credentials.

## Preferred Command

```text
/vision-inventory-agent-bom <image_folder> <output_dir> [options]
```

Options are forwarded to `scripts/inventory_folder_to_csv.py`, for example `--recursive`, `--limit 3`, `--max-side 4000`, and `--jpeg-quality 96`.

## Agent Rules

- Run the deterministic Python workflow first.
- Read `parts_to_lookup.json`.
- Verify each part against datasheets, preferring official manufacturer pages/PDFs.
- Fill `datasheet_cache.json` using `datasheet_cache.template.json` as the shape.
- Rerun the Python workflow with `--skip-vision`.
- Review `inventory.csv` and `inventory_evidence.csv`.
- Do not invent datasheets, manufacturers, voltages, package names, or descriptions.
- Set `verified=false` if uncertain and explain in `notes`.
- Preserve raw JSON and evidence files.
