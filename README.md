# Vision Electronic Indexing for Pi

Agent-assisted electronics parts/PCB photo indexing for Pi. The package processes images with Cloudflare Workers AI, extracts visible IC/package markings, prepares parts for datasheet lookup, and produces an enriched inventory CSV.

Typical flow:

```text
photos -> vision extraction -> raw JSON + evidence -> agent datasheet verification -> inventory.csv
```

The vision step does **not** perform datasheet lookup or invent part details. Datasheet enrichment is handled by a Pi agent with a web-search/browser tool or by manual review.

## Which setup should I use?

- **Using Pi?** Install the package with `pi install npm:vision-electronic-indexing-pi`, then run `/vision-inventory-setup`.
- **Using Claude Code, Codex CLI, OpenCode, Cursor, or another MCP-capable harness?** Use the recommended universal installer in `.universal/scripts/quick-install.sh`.
- **Using plain Python or manual MCP configuration?** Install `requirements.txt`, configure Cloudflare credentials, and run `vision_inventory_mcp.py` directly.

## Quick setup with Pi

### 1. Install the Pi package

```bash
pi install npm:vision-electronic-indexing-pi
```

For local development from this repository, open the repo in Pi and trust the project. Do not also install the npm package while working inside this repo, because the project-local extension and npm package register the same tools.

### 2. Plan for datasheet web search

Datasheet enrichment requires a separate Pi web-search or browser tool/skill. This package intentionally does **not** bundle one and does not try to auto-detect one because detection is unreliable.

Examples of acceptable capabilities:

- a Brave/search Pi skill
- a browser automation skill
- another trusted web-search extension/tool

If no search/browser capability is available, the workflow can still generate `parts_to_lookup.json`, but it cannot verify datasheets. Fill `datasheet_cache.json` manually in that case.

### 3. Configure Cloudflare credentials

Start Pi and run:

```text
/vision-inventory-setup
```

The setup command creates/checks a Pi-managed Python virtual environment under `~/.pi/agent/vision-inventory/.venv`, installs Python dependencies there when approved, warns that datasheet enrichment needs a separate web-search/browser capability, and prompts for Cloudflare Workers AI API token credentials the first time.

Credentials are stored at:

```text
~/.pi/agent/vision-inventory/credentials.json
```

The file is written with `chmod 600` when supported. Token input may be visible depending on your Pi UI; avoid entering credentials while screen sharing.

To change credentials later:

```text
/vision-inventory-credentials
```

Environment variables also work and override stored credentials:

```bash
export CLOUDFLARE_ACCOUNT_ID=your_account_id
export CLOUDFLARE_AUTH_TOKEN=your_workers_ai_api_token
# CLOUDFLARE_API_TOKEN is also accepted as an alias:
export CLOUDFLARE_API_TOKEN=your_workers_ai_api_token
```

Optional model override:

```bash
export WORKERS_AI_MODEL=@cf/meta/llama-4-scout-17b-16e-instruct
```

## Other harnesses / universal MCP compatibility

_Contributed by user [@Brun0-v](https://github.com/Brun0-V)_ 

This repository also includes a harness-neutral compatibility layer in `.universal/` for MCP-capable coding agents such as OpenCode, Claude Code, Codex CLI, Cursor, and similar clients.

The universal layer does **not** replace the Pi package integration. Pi users should keep using the commands above.

For other harnesses, use the universal installer:

```bash
curl -fsSL https://raw.githubusercontent.com/Pichi-Cell/vision-electronic-indexing-mcp/main/.universal/scripts/quick-install.sh -o /tmp/vei-install.sh && bash /tmp/vei-install.sh
```

This installs to `~/.vei/`, sets up a Python venv, prompts for Cloudflare Workers AI API token credentials, installs the agent skill, and **automatically configures the MCP server** in your agent's settings. **Requires an MCP-capable agent** (OpenCode, Claude Code, Codex CLI, Cursor, etc.).

Warning: some MCP clients store environment variables in plaintext JSON config files. Prefer shell environment variables or your agent's secret storage if available.

Other harnesses can also connect directly to the Python MCP server:

```bash
python3 /path/to/vision-electronic-indexing-mcp/vision_inventory_mcp.py
```

### 1. Manual setup: install Python dependencies

From the repository root:

```bash
python3 -m pip install -r requirements.txt
# Optional for iPhone HEIC/HEIF photos:
# python3 -m pip install pillow-heif
```

### 2. Configure Cloudflare credentials

Either copy `.env.example` to `.env` in the repository root:

```bash
cp .env.example .env
# edit .env and set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_AUTH_TOKEN
# CLOUDFLARE_AUTH_TOKEN should be your Cloudflare Workers AI API token.
```

or put the credentials directly in your harness MCP server configuration. Be aware that many harness config files store these values in plaintext.

### 3. Add the MCP server to your harness

Example config snippets are provided in:

```text
.universal/configs/opencode.json.example
.universal/configs/claude.json.example
.universal/configs/codex.json.example
.universal/configs/cursor.json.example
```

Each config should point to the repository-root server file, for example:

```json
{
  "mcpServers": {
    "vision-inventory": {
      "command": "python3",
      "args": ["/path/to/vision-electronic-indexing-mcp/vision_inventory_mcp.py"],
      "env": {
        "CLOUDFLARE_ACCOUNT_ID": "your_cloudflare_account_id",
        "CLOUDFLARE_AUTH_TOKEN": "your_cloudflare_workers_ai_api_token"
      }
    }
  }
}
```

The `.universal/configs/*.json.example` files are strict JSON; copy the appropriate file into your harness config location and adjust paths/credentials. OpenCode uses a harness-specific `mcp` shape; the other examples use `mcpServers`.

The older `.universal/setup/install.sh` and `.universal/setup/install.ps1` scripts are manual/legacy helpers. Prefer `.universal/scripts/quick-install.sh` for automated universal setup.

The raw MCP server exposes these tool names:

| Tool | Purpose |
|---|---|
| `process_image` | Analyze one electronics/PCB image. |
| `process_image_folder` | Analyze a folder of supported images. |
| `save_inventory` | Save inventory output as JSON or quick CSV export. Use the batch workflow for full BOM/evidence output. |

### 4. Install the universal skill/prompt, if your harness supports them

Universal workflow assets are available at:

```text
.universal/skills/vision-inventory-workflow/SKILL.md
.universal/prompts/vision-inventory-agent-bom.md
```

Copy them into your harness-specific skills/prompts location. The skill instructs the agent to run the deterministic workflow, read `parts_to_lookup.json`, verify datasheets with web search, fill `datasheet_cache.json`, regenerate the CSV with `--skip-vision`, and summarize uncertainties.

The deterministic workflow command is the same as the manual shell workflow:

```bash
python3 scripts/inventory_folder_to_csv.py ./photos ./output
python3 scripts/inventory_folder_to_csv.py ./photos ./output --skip-vision
```

Datasheet enrichment still requires a separate web-search/browser capability in the agent. This package does not bundle one.

## Recommended workflow

### 1. Take photos

Take clear, close photos of IC groups or PCB sections. Cropped, well-lit IC close-ups work better than full-board photos for OCR.

Example folder:

```text
photos/
  image_001.jpeg
  image_002.jpeg
  image_003.jpeg
```

### 2. Run the full agent workflow

In Pi:

```text
/vision-inventory-agent-bom ./photos ./output
```

In other harnesses:

```text
/vision-inventory-workflow ./photos ./output
```


Useful options:

```text
/vision-inventory-agent-bom ./photos ./output --recursive
/vision-inventory-agent-bom ./photos ./output --limit 3
/vision-inventory-agent-bom ./photos ./output --max-side 0 --jpeg-quality 96
```

The agent workflow will:

1. Run the image-processing batch script.
2. Write raw JSON files for auditability.
3. Build `parts_to_lookup.json`.
4. Search for datasheets, preferring official manufacturer sources.
5. Fill `datasheet_cache.json`.
6. Rerun the CSV generation with `--skip-vision`.
7. Summarize the BOM and call out uncertain rows.

### 3. Review output

Generated files:

```text
output/
  raw/                              # one raw JSON result per image
  parts_to_lookup.json              # parts/evidence requiring datasheet lookup
  datasheet_cache.template.json     # enrichment template
  datasheet_cache.json              # agent/user-filled enrichment cache
  inventory.csv                     # deduplicated final BOM
  inventory_evidence.csv            # per-image/per-candidate evidence rows
```

Always review rows where:

```text
needs_review=true
verified=false
```

## CSV output columns

`inventory.csv` is deduplicated by `normalized_part`, the main/final part number column derived from the vision `likely_part` and datasheet enrichment. Multiple images, or multiple candidates from one image, can merge into one BOM row when they resolve to the same `normalized_part`.

Columns:

| Column | Description |
|---|---|
| `normalized_part` | Main dedupe key/final part number, usually from datasheet enrichment and based on the vision `likely_part`. |
| `candidate_parts` | Candidate part numbers extracted from visual markings. |
| `amount` | Estimated quantity for the merged BOM row. |
| `sighting_count` | Number of evidence rows merged into this BOM row. |
| `description` | Short datasheet-verified description. |
| `datasheet_url` | Datasheet/source URL used for enrichment. |
| `manufacturer` | Verified or likely manufacturer. |
| `verified` | `true` only when datasheet match was verified. |
| `vision_confidence` | Vision/OCR confidence values observed for the row. |
| `needs_review` | `true` when OCR or datasheet enrichment is uncertain. |
| `images` | Source images contributing to the row. |
| `observed_markings` | Raw visible markings seen on packages. |
| `raw_json` | Raw JSON files used as evidence. |
| `notes` | Datasheet/enrichment notes or uncertainty explanations. |

`inventory_evidence.csv` keeps the non-deduplicated evidence rows used to build the BOM. A single photo can produce multiple evidence rows when it contains multiple different ICs.

## Commands and tools

### Pi commands

```text
/vision-inventory-setup
/vision-inventory-credentials
/vision-inventory-restart
/vision-inventory-bom <image_folder> <output_dir> [options]
/vision-inventory-agent-bom <image_folder> <output_dir> [options]
```

Command summary:

| Command | Purpose |
|---|---|
| `/vision-inventory-setup` | Configure credentials and check dependencies. |
| `/vision-inventory-credentials` | Change stored Cloudflare credentials. |
| `/vision-inventory-restart` | Restart the local vision bridge process. |
| `/vision-inventory-bom` | Run only the deterministic image-to-CSV workflow. |
| `/vision-inventory-agent-bom` | Run the full agent-assisted workflow, including datasheet enrichment. |

### Pi tools exposed to agents

| Tool | Purpose |
|---|---|
| `vision_inventory_process_image` | Analyze one electronics/PCB image. |
| `vision_inventory_process_folder` | Analyze a folder of supported images. |
| `vision_inventory_save` | Save inventory output as JSON or quick CSV export. For the full BOM workflow, use `/vision-inventory-bom` or `scripts/inventory_folder_to_csv.py`. |

## Manual shell workflow

You can run the deterministic workflow without Pi commands:

```bash
python3 -m pip install -r requirements.txt
python3 scripts/inventory_folder_to_csv.py ./photos ./output --validate-setup
python3 scripts/inventory_folder_to_csv.py ./photos ./output
```

Then fill `output/datasheet_cache.json` manually or with an agent, and regenerate the CSV without reprocessing images:

```bash
python3 scripts/inventory_folder_to_csv.py ./photos ./output --skip-vision
```

## No-Cloudflare demo and sample output

A fixture-based demo is available at:

```text
examples/no-cloudflare-demo/
```

Run it without Cloudflare credentials:

```bash
python3 scripts/inventory_folder_to_csv.py ./examples/no-cloudflare-demo/photos ./examples/no-cloudflare-demo/output --skip-vision
```

The demo includes mock raw vision JSON, a small `datasheet_cache.json`, and generated sample outputs:

```text
examples/no-cloudflare-demo/output/parts_to_lookup.json
examples/no-cloudflare-demo/output/datasheet_cache.json
examples/no-cloudflare-demo/output/inventory.csv
examples/no-cloudflare-demo/output/inventory_evidence.csv
```

Use these files to understand the expected output shapes before running real image analysis.

## Python requirements

Python 3.10 or newer is recommended.

Required versions are constrained in `requirements.txt` for reproducible installs:

```text
mcp>=1.0,<2.0
requests>=2.31,<3.0
pillow>=10.0,<12.0
python-dotenv>=1.0,<2.0
```

Install:

```bash
python3 -m pip install -r requirements.txt
```

Optional HEIC/HEIF support for iPhone photos:

```bash
python3 -m pip install pillow-heif
```

Supported image formats:

```text
.jpg .jpeg .png .webp .bmp .gif .heic .heif
```

HEIC/HEIF requires `pillow-heif`.

## How image processing works

Before sending an image to Cloudflare Workers AI, the Python server:

1. Opens the image with Pillow.
2. Applies EXIF orientation correction.
3. Sends full resolution by default; resizes only when `max_side` is set to a positive value and the image is larger than that limit.
4. Converts transparency to a white background.
5. Converts the image to RGB.
6. Encodes it as JPEG.
7. Sends it as a base64 image data URL.

Defaults:

```text
max_side: 0 (full resolution)
jpeg_quality: 96
model: @cf/meta/llama-4-scout-17b-16e-instruct
```

## Multiple-IC behavior

Images may contain one IC or many different ICs. The workflow does not force all visible ICs in one image to share the same marking or part family.

The batch workflow builds one evidence row per image/candidate part, so one photo can contribute several BOM rows.

This improves handling of mixed IC photos, but OCR can still miss, merge, or misread small markings. Review raw JSON and evidence rows when accuracy matters.

## Datasheet enrichment rules

The agent should:

- Prefer official manufacturer datasheets or product pages.
- Keep descriptions short.
- If exact candidate search fails but official results strongly indicate a likely OCR correction, keep the original candidate as the `datasheet_cache.json` key and set `normalized_part` to the official datasheet part number.
- Example: if `SN74AS283N` has no official datasheet but official TI results match `SN74LS283N` and the image could plausibly confuse the characters, use key `SN74AS283N`, set `normalized_part` to `SN74LS283N`, and mention the correction in `notes`.
- Set `verified=true` for OCR corrections only when official source evidence and visual/package context make the correction highly likely.
- Set `verified=false` if the marking, part number, package, or source is uncertain.
- Do not invent part numbers, manufacturers, voltages, functions, or datasheet URLs.

Example `datasheet_cache.json` entry:

```json
{
  "MAX232N": {
    "normalized_part": "MAX232N",
    "description": "MAX232N dual EIA-232/RS-232 driver receiver 5v DIP",
    "datasheet_url": "https://www.ti.com/lit/ds/symlink/max232.pdf",
    "manufacturer": "Texas Instruments",
    "verified": true,
    "notes": "Verified against TI datasheet."
  }
}
```

## Local MCP server details

Internally, the vision step is implemented by `vision_inventory_mcp.py`, a local Python MCP stdio server. The Pi extension starts this server lazily and exposes its functionality as Pi tools.

MCP tools:

| MCP tool | Purpose |
|---|---|
| `process_image` | Analyze one image and return structured visible inventory data. |
| `process_image_folder` | Analyze all supported images in a folder. |
| `save_inventory` | Save inventory output to JSON or quick CSV export. Use `scripts/inventory_folder_to_csv.py` for the full BOM/evidence workflow. |

Run directly for MCP-compatible clients:

```bash
python3 vision_inventory_mcp.py
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "vision-inventory": {
      "command": "python3",
      "args": ["/path/to/vision_inventory_mcp.py"],
      "env": {
        "CLOUDFLARE_ACCOUNT_ID": "your_cloudflare_account_id",
        "CLOUDFLARE_AUTH_TOKEN": "your_cloudflare_workers_ai_api_token"
      }
    }
  }
}
```

## Error handling

The server returns structured errors where possible, for example:

```json
{
  "error": true,
  "message": "Missing CLOUDFLARE_ACCOUNT_ID environment variable."
}
```

Handled cases include:

- Missing Cloudflare credentials.
- Invalid image paths.
- Unsupported image extensions.
- Failed image preprocessing.
- Cloudflare API errors.
- Invalid JSON from the model.
- Missing or invalid folders.
- Save/write failures.

## Limitations

- Vision models can misread small, blurry, or low-contrast IC markings.
- Cropped close-ups usually help more than prompt changes.
- Datasheet enrichment depends on the quality of the installed web-search/browser tool.
- Official datasheets should still be reviewed for important work.
- The workflow deduplicates by normalized part number, not by physical component identity across overlapping photos.
