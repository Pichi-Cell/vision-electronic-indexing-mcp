---
name: vision-inventory-workflow
description: >
  Run the Vision Electronic Indexing workflow for electronics/PCB photos: process
  images via the vision-inventory MCP server, create parts_to_lookup.json, verify
  datasheets with web search, fill datasheet_cache.json, regenerate CSV, and
  summarize uncertainties. Works with OpenCode, Claude Code, Codex CLI, Cursor,
  and any agent that supports MCP tools and the Agent Skills spec.
---

# Vision Inventory Workflow

Use this skill when the user wants an agent to produce an enriched electronics BOM from a folder of electronics/PCB images.

Works with any coding agent that supports MCP tools (OpenCode, Claude Code, Codex CLI, Cursor, etc.).

## Prerequisites

- **MCP server** configured in your agent's MCP settings pointing to the repository-root `vision_inventory_mcp.py` (see `.universal/configs/`).
- **Python 3.10+** with dependencies from the repository-root `requirements.txt` installed.
- **Cloudflare Workers AI credentials** - set in `.env` or in your MCP server's `env` block.
- **Web search capability** - your agent needs a web search/browser tool for datasheet enrichment. This skill does NOT bundle one.

## Agent Workflow

When the user provides a folder of electronics/PCB photos:

1. **Run the deterministic vision pipeline from the repository root:**
   ```
   python3 scripts/inventory_folder_to_csv.py <image_folder> <output_dir> [options]
   ```
   Options: `--recursive`, `--limit N`, `--max-side 0`, `--jpeg-quality 96`.

2. **Read the parts to look up:**
   Read `output_dir/parts_to_lookup.json`.

3. **Enrich with datasheets:**
   For each part, web-search for its datasheet. Prefer official manufacturer pages/PDFs.
   Write results to `output_dir/datasheet_cache.json` using `datasheet_cache.template.json` as the shape.

4. **Regenerate the CSV from the repository root:**
   ```
   python3 scripts/inventory_folder_to_csv.py <image_folder> <output_dir> --skip-vision
   ```

5. **Review and summarize:**
   Read `output_dir/inventory.csv` and `output_dir/inventory_evidence.csv`.
   Summarize the BOM and flag uncertainties.

## Datasheet Enrichment Rules

- Prefer official manufacturer datasheets or product pages.
- Keep descriptions short.
- If exact candidate search fails but official results strongly indicate a likely OCR correction, keep the original candidate as the `datasheet_cache.json` key and set `normalized_part` to the official datasheet part number.
- Example: if `SN74AS283N` has no official datasheet but official TI results match `SN74LS283N` and the image could plausibly confuse characters, use key `SN74AS283N`, set `normalized_part` to `SN74LS283N`, and explain the correction in `notes`.
- Set `verified=true` for corrections only when official source evidence and visual/package context make the correction highly likely.
- Set `verified=false` if the marking, part number, package, or source is uncertain.
- Do NOT invent part numbers, manufacturers, voltages, functions, or datasheet URLs.

## MCP Tools

The configured MCP server exposes these tools that the agent can call directly:

| Tool | Purpose |
|---|---|
| `process_image` | Analyze a single electronics/PCB image |
| `process_image_folder` | Batch-analyze a folder of images |
| `save_inventory` | Save results as JSON or CSV |

## Setup

To configure the MCP server, copy the appropriate config from `.universal/configs/`, fill in the path to the repository-root `vision_inventory_mcp.py` and your Cloudflare credentials.

Install Python dependencies:
```bash
python3 -m pip install -r requirements.txt
# Optional for iPhone HEIC photos:
# python3 -m pip install pillow-heif
```

Copy `.env.example` to `.env` and add your Cloudflare credentials, OR set them in your MCP server's `env` block.
