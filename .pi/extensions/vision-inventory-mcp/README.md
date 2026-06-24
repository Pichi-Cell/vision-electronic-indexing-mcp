# Vision Inventory MCP Pi Extension

Project-local Pi extension that bridges `vision_inventory_mcp.py` into native Pi tools.

Pi auto-discovers this extension from `.pi/extensions/vision-inventory-mcp/index.ts` after the project is trusted.

## Exposed Pi tools

- `vision_inventory_process_image` → MCP `process_image`
- `vision_inventory_process_folder` → MCP `process_image_folder`
- `vision_inventory_save` → MCP `save_inventory`

## Requirements

Install the Python dependencies in this project environment:

```bash
pip install mcp requests pillow python-dotenv
# optional HEIC/HEIF support
pip install pillow-heif
```

Set Cloudflare credentials before using the tools:

```bash
export CLOUDFLARE_ACCOUNT_ID=your_account_id
export CLOUDFLARE_AUTH_TOKEN=your_workers_ai_token
# or CLOUDFLARE_API_TOKEN=your_workers_ai_token
```

## Usage

Restart or reload Pi in this repo, then ask Pi to analyze an electronics/PCB image. The extension lazily starts:

```bash
${PI_VISION_INVENTORY_PYTHON:-python3} vision_inventory_mcp.py
```

as an MCP stdio child process and forwards tool calls to it.

Command:

```text
/vision-inventory-restart
```

restarts the bridge process.
