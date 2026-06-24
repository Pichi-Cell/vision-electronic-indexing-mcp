# Vision Inventory MCP Pi Extension

Pi extension that bridges the bundled `vision_inventory_mcp.py` and `scripts/inventory_folder_to_csv.py` into native Pi tools and commands.

This repo can be used project-locally or installed as a Pi package. The package intentionally does **not** bundle Python dependencies or a web-search/browser dependency.

## Exposed Pi tools

- `vision_inventory_process_image` → MCP `process_image`
- `vision_inventory_process_folder` → MCP `process_image_folder`
- `vision_inventory_save` → MCP `save_inventory`

## Commands

```text
/vision-inventory-setup
/vision-inventory-credentials
/vision-inventory-restart
/vision-inventory-bom <image_folder> <output_dir> [options]
/vision-inventory-agent-bom <image_folder> <output_dir> [options]
```

`/vision-inventory-setup` prompts for Cloudflare credentials the first time and stores them in:

```text
~/.pi/agent/vision-inventory/credentials.json
```

Use `/vision-inventory-credentials` to change them later.

`/vision-inventory-agent-bom` starts an agent turn that runs the image workflow, performs datasheet enrichment with whatever web-search/browser tool or skill the user has installed, writes `datasheet_cache.json`, reruns with `--skip-vision`, and summarizes uncertainties.

## External dependencies not bundled

- Python packages: `mcp`, `requests`, `pillow`, `python-dotenv`; optional `pillow-heif`.
- A Pi web-search/browser tool or skill for datasheet lookup.
- Cloudflare Workers AI credentials.

The setup command can check/install Python dependencies, but web-search/browser capability must be installed/enabled separately.
