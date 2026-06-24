---
description: Run the full Vision Inventory image-to-enriched-BOM workflow
argument-hint: "<image_folder> <output_dir> [options]"
---
Run the full Vision Electronic Indexing workflow for: $ARGUMENTS

Prefer the extension command when available:

```text
/vision-inventory-agent-bom $ARGUMENTS
```

External agent dependency: datasheet enrichment requires a web-search/browser Pi tool or skill. This package intentionally does not bundle one.
