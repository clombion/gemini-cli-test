# data-scraper-sitemap - Gemini CLI Extension

Generate valid WebScraper.io sitemap JSON files for the Chrome extension. Use this skill whenever the user wants to scrape a website, extract structured data from web pages, create a web scraper, build a sitemap for WebScraper.io, or asks for help with data collection from websites. Also trigger on "scrape", "web scraping", "extract data from site", "crawl pages", "webscraper", "sitemap JSON", or any request involving collecting structured information from one or more web pages. The output is a JSON file the user imports into the WebScraper.io Chrome/Chromium extension (they handle the import themselves).

## Quick Start

After installation, you can use this extension by asking questions or giving commands naturally.


# WebScraper.io Sitemap Generator

Generate valid sitemap JSON files for the WebScraper.io Chrome extension by inspecting real websites and confirming every detail with the user before producing the final file.

## Prerequisites

This skill generates sitemap JSON files for the **WebScraper.io** Chrome/Chromium extension. The user must have it installed before they can import and run sitemaps.

**Installation options (pick one):**
1. **Chrome Web Store** (recommended): search "WebScraper.io" or visit the [extension page](https://chrome.google.com/webstore/detail/web-scraper-free-web-scra/jnhgnonknehpejjnehehllkliplmbmhn)
2. **Bundled .crx file**: drag `webscraperio.crx` (included in this skill folder) onto `chrome://extensions/` with Developer Mode enabled

**Verify installation:** open DevTools (F12 / Cmd+Opt+I) — you should see a "Web Scraper" tab.

## How This Works

The user gives you a website URL and describes what data they want. You inspect the site, figure out the right CSS selectors, confirm the scraping plan, and generate a JSON file they can import into WebScraper.io. The user runs the actual scrape themselves — your job is to produce a correct, validated sitemap file.

## Workflow

### Phase 1: Understand the Goal

Ask the user:
1. **What URL?** — The starting page(s) to scrape
2. **What data?** — Which fields they want (titles, prices, links, images, dates, etc.)
3. **How many pages?** — Single page, paginated list, or follow links to detail pages
4. **What format?** — Usually CSV export from WebScraper.io (default), but confirm

If the user is vague ("scrape this site"), visit the URL first and then suggest what's scrapable. Show them what you see and let them pick.

### Phase 2: Inspect the Site

Use browser tools to examine the target pages. Prefer Chrome DevTools MCP (`take_snapshot`, `take_screenshot`, `evaluate_script`) if available. Fall back to WebFetch for simpler sites.

**What to check:**
- **Page structure**: Take a snapshot, identify the repeating container element (the "box" that holds one item in a list)
- **Data elements**: For each field the user wants, find the CSS selector inside the container
- **Pagination**: Look for next-page links, load-more buttons, or URL patterns like `/page/[1-100]`
- **Dynamic content**: Check if content loads via JavaScript (infinite scroll, AJAX). If so, you'll need `SelectorElement` with scroll, `SelectorElementScroll`, or `SelectorElementClick`
- **Detail pages**: If the user wants data from linked pages (e.g., click a product to get its description), identify the link selector and the detail page structure

**Selector-finding strategy:**
```javascript
// In Chrome DevTools MCP, use evaluate_script to test selectors:
() => {
  const els = document.querySelectorAll('.your-selector');
  return { count: els.length, samples: [...els].slice(0, 3).map(e => e.textContent.trim()) };
}
```

Test every selector before using it. Report back: "I found 24 items matching `.product-card`, each containing a title in `.product-title` and price in `.price`."

### Phase 3: Confirm the Scraping Plan

Present a clear summary to the user before generating anything. This is the most important step — the user cannot easily modify the sitemap after import, so get it right.

**Summary template:**

```
Scraping plan for: [site name]

Start URL(s): [list]
Estimated pages: [number]

Selector tree:
  _root
  └─ [container] (SelectorElement, multiple)
     ├─ [field1] (SelectorText)
     ├─ [field2] (SelectorLink) → detail page
     │  ├─ [detail_field1] (SelectorText)
     │  └─ [detail_field2] (SelectorHTML)
     └─ [field3] (SelectorImage)
  └─ [pagination] (SelectorPagination, linkFromHref)

Parameters:
  - Delay between pages: [N]ms
  - Scroll behavior: [none / scroll to load]
  - Element limit: [0 = unlimited / N]

Estimated scraping time: [see estimation section]
Estimated output: ~[N] rows, ~[N] columns
```

Wait for explicit confirmation. If the user wants changes, adjust and re-present.

### Phase 4: Generate the Sitemap JSON

Read the schema reference file to understand the exact format:
```
references/sitemap.schema.json
```

Build the sitemap object following these rules:

**Top-level structure:**
```json
{
  "_id": "descriptive-name",
  "startUrl": ["https://example.com/page"],
  "selectors": [...]
}
```

**`_id` rules:**
- 3-100 characters, only `[a-zA-Z0-9_()+\-]`
- Make it descriptive: `yakshascans-manga-list`, not `scraper1`

**`startUrl` rules:**
- Array of URL strings
- For paginated sites, use range notation: `https://example.com/page/[1-50]`
- Range syntax: `[start-end]` or `[start-end:step]`

**Selector construction — the key patterns:**

Every selector needs `id`, `type`, `parentSelectors`, plus type-specific fields.

`parentSelectors` is the hierarchy — `["_root"]` means top-level, `["some-element-id"]` means nested inside that element. This is how WebScraper.io knows the scraping order: it processes parents first, then children within each parent's context.

**Common selector recipes:**

| Pattern | Type | Key fields |
|---------|------|------------|
| Repeating container (list items, cards) | `SelectorElement` | `selector`, `multiple: true`, `scroll: false`, `elementLimit: 0` |
| Text content | `SelectorText` | `selector`, `multipleType: "singleColumn"`, `multiple: false`, `regex: ""` |
| Text as joined list (tags, genres) | `SelectorText` | `selector`, `multipleType: "singleColumnWithSeparator"`, `regex: ""` (no `multiple` field!) |
| Link to follow | `SelectorLink` | `selector`, `multiple: false`, `linkType: "linkFromHref"` |
| Image URL | `SelectorImage` | `selector`, `multipleType: "singleColumn"`, `multiple: false` |
| HTML content | `SelectorHTML` | `selector`, `multiple: false`, `regex: ""` |
| Attribute value | `SelectorElementAttribute` | `selector`, `extractAttribute: "data-value"`, `multipleType: "singleColumn"`, `multiple: false` |
| Next-page link | `SelectorPagination` | `selector`, `paginationType: "linkFromHref"` (no `multiple` field!) |
| Scroll-to-load container | `SelectorElementScroll` | `selector`, `multiple: true`, `elementLimit: 0`, `delay: 2000` |
| Click-to-load more | `SelectorElementClick` | `selector`, `clickElementSelector`, `clickType: "clickMore"`, `multiple: true`, `delay: 2000`, `discardInitialElements: "do-not-discard"`, `clickElementUniquenessType: "uniqueText"` |

**Field behavior gotchas:**
- `multiple` is **absent** (not false) on `SelectorPagination` — it gets deleted in the constructor
- `multiple` is **absent** when `multipleType != "singleColumn"` on Text/Image/ElementAttribute — it gets deleted
- `delay` and `elementLimit` are always **integers** (never strings)
- `SelectorPagination` must include its own `id` in its `parentSelectors` (the extension adds it at runtime, but exporting preserves it)
- For `singleColumnWithSeparator`, values are joined with newlines in export

### Phase 5: Validate

After generating the JSON, validate it using the bundled validator:

```bash
uv run scripts/validate_schema.py --sitemap <path-to-generated-sitemap.json>
```

If validation fails, fix the errors and re-validate. Never deliver an invalid sitemap.

If `uv` is not available, do a manual check: verify the JSON parses, every selector has `id`/`type`/`parentSelectors`, enum values match the allowed lists, and `_id` matches `^[a-zA-Z0-9_()+\-]+$`.

### Phase 6: Deliver with Context

Write the JSON file and tell the user:

1. **Where the file is** — full path
2. **How to import** — Open WebScraper.io extension → Create New Sitemap → Import Sitemap → paste or upload the JSON
3. **Estimated scraping time** — see below
4. **Keep-awake tip** — see below
5. **What to expect** — number of rows, columns, and any caveats (e.g., "some detail pages may be missing if the link selector doesn't match")

## Time Estimation

WebScraper.io processes pages sequentially with delays. Estimate based on:

```
total_time = num_pages × (page_load_time + configured_delay + processing_time)
```

Rules of thumb:
- **Page load**: ~2-5s per page (depends on site speed)
- **Default delay**: 2000ms (WebScraper.io default between requests)
- **Processing per page**: ~0.5-1s for selector extraction
- **Detail pages**: If following links, each linked page adds another cycle

**Quick formula:**
- List-only scrape: `pages × 4s` (with 2s delay)
- List + detail pages: `list_pages × 4s + total_items × 4s`
- Scroll-to-load: `pages × (scrolls_needed × delay_per_scroll)`

Present as a range: "Estimated 5-10 minutes for 150 pages" or "Estimated 30-60 minutes for 500 items with detail pages".

## Keep-Awake Tips

Scraping fails if the computer sleeps. After giving the time estimate, suggest:

**macOS:**
```bash
caffeinate -d -t 3600   # Keep display awake for 1 hour (adjust seconds)
```

**Linux:**
```bash
systemd-inhibit --what=idle --who="web scraper" --why="scraping in progress" sleep infinity &
# Or: xdg-screensaver suspend <window-id>
```

**Windows (PowerShell):**
```powershell
# Prevent sleep for the duration — run in a separate terminal:
while ($true) { [System.Windows.Forms.SendKeys]::SendWait("{SCROLLLOCK}"); Start-Sleep -Seconds 60 }
```

**Universal**: "Keep Chrome in the foreground and don't close the laptop lid."

Detect the user's OS from the environment and only show the relevant tip.

## Selector Type Reference

When you need details on a specific type's allowed fields, enum values, or constraints, read the full schema:
```
references/sitemap.schema.json
```

The 16 selector types are: `SelectorText`, `SelectorLink`, `SelectorImage`, `SelectorTable`, `SelectorElementAttribute`, `SelectorHTML`, `SelectorElement`, `SelectorElementScroll`, `SelectorElementClick`, `SelectorGroup` (deprecated), `SelectorSitemapXmlLink`, `SelectorPagination`, `SelectorScriptData` (experimental), `ActionScrollDown` (experimental), `ActionClick` (experimental), `SelectorPopupLink` (deprecated/experimental).

For most scraping tasks, you'll only use: `SelectorElement`, `SelectorText`, `SelectorLink`, `SelectorImage`, `SelectorPagination`, `SelectorHTML`, and `SelectorElementAttribute`.

## Common Pitfalls

- **Wrong parent**: If a text selector has `parentSelectors: ["_root"]` instead of the element container, it'll extract from the whole page instead of per-item
- **Missing container**: Always use a `SelectorElement` with `multiple: true` as the repeating container — don't try to make `SelectorText` with `multiple: true` do the grouping
- **Pagination self-reference**: `SelectorPagination` must list both `_root` and its own `id` in `parentSelectors` — otherwise it only follows the first next-page link
- **URL ranges vs pagination**: Use URL range `[1-50]` in `startUrl` for simple numbered pages; use `SelectorPagination` when next-page links are dynamic or unpredictable
- **Delay too low**: Sites with bot detection need 2000ms+ delay. When in doubt, suggest 3000ms
- **Forgetting to scroll**: If items load on scroll, the container `SelectorElement` with `scroll: false` will only get the initially visible items

## Resources

### scripts/
- `validate_schema.py` — Validates a sitemap JSON against the schema. Run with: `uv run scripts/validate_schema.py --sitemap <file.json>`

### references/
- `sitemap.schema.json` — JSON Schema (draft 2020-12) defining the complete WebScraper.io sitemap format. All 16 selector types, enums, and constraints. Read this when you need exact field names or allowed values.

### Extension
- `webscraperio.crx` — WebScraper.io Chrome extension binary. Install if not already available from the Chrome Web Store.


---

*This extension was converted from a Claude Code skill using [skill-porter](https://github.com/jduncan-rva/skill-porter)*
