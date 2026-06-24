# Vision Electronic Indexing MCP + Pi Workflow

This project turns electronics / PCB photos into a structured inventory workflow:

```text
Image is taken -> saved in a folder -> Pi processes each photo -> raw IC data is extracted
-> agent/user verifies datasheets -> enriched inventory is exported to CSV
```

The core vision step is a local Python MCP server that sends images to Cloudflare Workers AI. A project-local Pi extension bridges that MCP server into native Pi tools. A batch script implements the folder-to-CSV workflow with **agent-assisted datasheet enrichment**.

## What this project does

- Processes one electronics image or a folder of images.
- Extracts visible IC/package markings, confidence, position hints, and review flags.
- Runs a second IC consensus pass when multiple ICs are detected in one image.
- Preserves individual IC marking observations, because the model may read one chip correctly and another incorrectly.
- Saves raw JSON for auditability.
- Creates `parts_to_lookup.json` for datasheet enrichment.
- Produces a final CSV, using `datasheet_cache.json` when enrichment is available.

## What this project intentionally does not fully automate

Datasheet lookup is intentionally **Option A: agent-assisted enrichment**.

The script prepares the parts that need lookup, but it does not silently scrape the web and trust the result. Instead, Pi or a human should verify each part against a datasheet, fill `datasheet_cache.json`, and rerun the script to generate the final enriched CSV.

This makes wrong OCR or wrong datasheet matches easier to catch.

## Repository layout

```text
vision_inventory_mcp.py                 # MCP server and core image-processing logic
inventory.py                            # Original GUI/helper app
requirements.txt                        # Python dependencies
scripts/inventory_folder_to_csv.py      # Batch workflow script
.pi/extensions/vision-inventory-mcp/    # Project-local Pi extension
```

## Requirements

Python 3.10 or newer is recommended.

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Optional HEIC/HEIF support for iPhone photos:

```bash
python3 -m pip install pillow-heif
```

## Cloudflare setup

Create a `.env` file in the project root:

```env
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id
CLOUDFLARE_AUTH_TOKEN=your_cloudflare_workers_ai_token
```

The token can also be provided as:

```env
CLOUDFLARE_API_TOKEN=your_cloudflare_workers_ai_token
```

Default Cloudflare Workers AI model:

```text
@cf/meta/llama-4-scout-17b-16e-instruct
```

Override it with:

```bash
export WORKERS_AI_MODEL=@cf/meta/llama-4-scout-17b-16e-instruct
```

## Pi extension setup

This repo includes a project-local Pi extension:

```text
.pi/extensions/vision-inventory-mcp/index.ts
```

After opening this repo in Pi and trusting the project, Pi should auto-discover the extension. If Pi is already running, use `/reload` or restart Pi.

The extension exposes these tools:

| Pi tool | Purpose |
|---|---|
| `vision_inventory_process_image` | Analyze one electronics image. |
| `vision_inventory_process_folder` | Analyze all supported images in a folder. |
| `vision_inventory_save` | Save inventory output as JSON or CSV. |

It also adds these commands:

```text
/vision-inventory-restart
/vision-inventory-bom <image_folder> <output_dir> [options]
```

Use `/vision-inventory-restart` if the MCP bridge needs to be restarted after changing `.env` or Python code.

Use `/vision-inventory-bom` to run the full folder-to-CSV workflow from Pi. Examples:

```text
/vision-inventory-bom ./photos ./output
/vision-inventory-bom ./photos ./output --recursive
/vision-inventory-bom ./photos ./output --skip-vision
```

Options are forwarded to `scripts/inventory_folder_to_csv.py`.

The extension launches:

```bash
python3 vision_inventory_mcp.py
```

If your Python command is different, start Pi with:

```bash
PI_VISION_INVENTORY_PYTHON=python pi
```

## Supported image formats

```text
.jpg
.jpeg
.png
.webp
.bmp
.gif
.heic
.heif
```

HEIC/HEIF requires `pillow-heif`.

## Image preprocessing

Before sending an image to Cloudflare Workers AI, the server:

1. Opens the image with Pillow.
2. Applies EXIF orientation correction.
3. Resizes only if the image is larger than `max_side`.
4. Converts transparency to a white background.
5. Converts the image to RGB.
6. Encodes it as JPEG.
7. Sends it as a base64 image data URL.

Current default image settings:

```text
max_side: 4000
jpeg_quality: 96
```

## IC consensus behavior

For this lab workflow, the program assumes that all ICs visible in one image should be the same part family/marking.

The processing flow is:

1. First pass: general visible inventory extraction.
2. If multiple ICs are found, second pass: IC-only consensus verification.
3. Final output includes:
   - `items`: one consensus IC item when possible.
   - `ic_marking_observations`: per-chip marking observations.
   - `first_pass_items`: original first-pass IC candidates.
   - `warnings`: notes about consensus verification.

This does not guarantee correct OCR. It helps expose uncertainty and preserves alternate readings.

## Main batch workflow

Use this when you have a folder of newly taken images and want a final CSV.

From Pi, you can run the workflow as a slash command:

```text
/vision-inventory-bom ./photos ./output
```

From a normal shell, run the underlying script directly as shown below.

### 1. Put images in a folder

Example:

```text
photos/
  image_001.jpeg
  image_002.jpeg
  image_003.jpeg
```

### 2. Run the batch workflow

```bash
python3 scripts/inventory_folder_to_csv.py ./photos ./output
```

This creates:

```text
output/
  raw/                              # one raw JSON result per image
  parts_to_lookup.json              # parts/evidence that need datasheet lookup
  datasheet_cache.template.json     # template for enrichment
  inventory.csv                     # deduplicated BOM CSV, possibly with missing enrichment fields
  inventory_evidence.csv            # per-image evidence CSV used to build the BOM
```

Use recursive folder scanning if needed:

```bash
python3 scripts/inventory_folder_to_csv.py ./photos ./output --recursive
```

Limit images during testing:

```bash
python3 scripts/inventory_folder_to_csv.py ./photos ./output --limit 3
```

### 3. Enrich datasheets with Pi/web search

Open `output/parts_to_lookup.json`. For each part, search for the datasheet and verify the description.

Prefer official manufacturer datasheets when possible:

1. Texas Instruments
2. Analog Devices / Maxim
3. STMicroelectronics
4. ONsemi
5. Nexperia
6. Other manufacturer PDFs

Copy the template:

```bash
cp output/datasheet_cache.template.json output/datasheet_cache.json
```

Fill each entry. Example:

```json
{
  "SN74LS283N": {
    "normalized_part": "SN74LS283N",
    "description": "74ls (4 bit) adder low power schottky ttl 5v DIP",
    "datasheet_url": "https://www.ti.com/lit/ds/symlink/sn74ls283.pdf",
    "manufacturer": "Texas Instruments",
    "verified": true,
    "notes": "Verified against TI datasheet. N package is PDIP."
  },
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

Recommended Pi prompt for enrichment:

```text
Read output/parts_to_lookup.json. For each part, web-search the datasheet, prefer official manufacturer PDFs, verify function/package/voltage/family, then fill output/datasheet_cache.json. Keep descriptions in this short format: "74ls (4 bit) adder low power schottky ttl 5v DIP". Set verified=false if uncertain.
```

### 4. Regenerate CSV without reprocessing images

After filling `datasheet_cache.json`, rerun:

```bash
python3 scripts/inventory_folder_to_csv.py ./photos ./output --skip-vision
```

This reuses `output/raw/*.json` and writes a new enriched:

```text
output/inventory.csv
```

## Final CSV columns

By default, `inventory.csv` is deduplicated by normalized part number. Multiple images with the same IC become one BOM row with `sighting_count` and an `images` list.

```text
normalized_part
candidate_parts
amount
sighting_count
description
datasheet_url
manufacturer
verified
vision_confidence
needs_review
images
observed_markings
raw_json
notes
```

Example BOM row:

```csv
SN74LS283N,SN74LS283N,8,2,74ls (4 bit) adder low power schottky ttl 5v DIP,https://www.ti.com/lit/ds/symlink/sn74ls283.pdf,Texas Instruments,true,high/low,true,"image_001.jpeg | image_002.jpeg","SN74LS283N | SN74S283N","output/raw/image_001.json | output/raw/image_002.json","Verified against TI datasheet"
```

The script also writes `inventory_evidence.csv`, which keeps the non-deduplicated per-image rows used to build the BOM. It includes the same per-sighting `amount` estimate before aggregation.

`amount` is estimated from the vision result's IC count. `sighting_count` is the number of image-level sightings that were merged into the BOM row.

## MCP server usage

Run the MCP server directly with:

```bash
python3 vision_inventory_mcp.py
```

The server uses MCP `stdio` transport, so it is meant to be launched by an MCP-compatible client or by the Pi extension.

### MCP tools

| MCP tool | Purpose |
|---|---|
| `process_image` | Analyze one image and return visible inventory data. |
| `process_image_folder` | Analyze all supported images in a folder. |
| `save_inventory` | Save inventory output to JSON or CSV. |

### Example MCP client configuration

```json
{
  "mcpServers": {
    "vision-inventory": {
      "command": "python3",
      "args": ["/path/to/vision_inventory_mcp.py"],
      "env": {
        "CLOUDFLARE_ACCOUNT_ID": "your_cloudflare_account_id",
        "CLOUDFLARE_AUTH_TOKEN": "your_cloudflare_workers_ai_token"
      }
    }
  }
}
```

## Single-image output example

```json
{
  "image": "image_001.jpeg",
  "items": [
    {
      "item_type": "IC",
      "count_index": 1,
      "package_marking": "SN74LS283N",
      "marking_confidence": "medium",
      "likely_part": "SN74LS283N",
      "description": "consensus result; 4 visible ICs",
      "position_hint": "multiple ICs",
      "needs_review": true
    }
  ],
  "warnings": [
    "Multi-pass IC consensus verification applied."
  ],
  "ic_marking_observations": [
    {
      "position_hint": "top-right",
      "package_marking": "SN74LS283N F 7936",
      "marking_confidence": "high"
    }
  ],
  "first_pass_items": []
}
```

## Error handling

The server returns structured errors when possible:

```json
{
  "error": true,
  "message": "Missing CLOUDFLARE_ACCOUNT_ID environment variable."
}
```

Handled cases include:

- Missing Cloudflare credentials.
- Invalid image path.
- Unsupported image extension.
- Failed image preprocessing.
- Cloudflare API errors.
- Invalid JSON from the model.
- Missing or invalid folder path.
- Save/write failures.

## Important limitations

- Vision models can misread small or blurry IC markings.
- A higher-resolution or closer photo usually helps more than prompt changes.
- Full-board photos are useful for context; cropped IC close-ups are better for marking OCR.
- The consensus pass can enforce one shared IC result, but it can still choose the wrong consensus.
- Datasheet enrichment should be verified against official sources.
- The script does not deduplicate the same physical part across multiple images unless you handle that in the enrichment/review step.

## Recommended operating procedure

1. Take a clear photo of each IC group.
2. Save photos into a dated folder.
3. Run `scripts/inventory_folder_to_csv.py`.
4. Inspect raw JSON and `parts_to_lookup.json`.
5. Use Pi/web search to fill `datasheet_cache.json`.
6. Rerun with `--skip-vision`.
7. Review `inventory.csv`, especially rows with `needs_review=true`.
