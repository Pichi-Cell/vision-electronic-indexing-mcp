Run the full Vision Electronic Indexing workflow for: $ARGUMENTS

Use the vision-inventory-workflow skill to process images, create parts_to_lookup.json,
verify datasheets with web search, fill datasheet_cache.json, regenerate CSV, and
summarize uncertainties.

Steps:
1. From the repository root, run the deterministic vision pipeline: python3 scripts/inventory_folder_to_csv.py <args>
2. Read parts_to_lookup.json
3. Web-search each part for datasheets
4. Fill datasheet_cache.json
5. From the repository root, regenerate CSV with --skip-vision
6. Review and summarize the BOM

External dependency: datasheet enrichment requires a web-search tool. This package does not bundle one.
