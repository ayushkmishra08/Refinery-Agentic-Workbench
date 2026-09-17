# Parse Report: Crude desalter

Generated: 2026-09-16T13:41:15.172433+00:00
Source: `Crude desalter.pdf` (sha256 `19b58ad8b351e6a6...`)
Overall: **PASSED** (20/23 checks ok)

## Summary

| Metric | Value |
|---|---|
| Pages in PDF | 93 |
| Pages processed (with records) | 93 |
| Pages with text | 93 |
| Empty pages | 0 |
| Elements | 997 |
| Text characters (non-table) | 71,555 |
| Headings | 71 |
| List items | 101 |
| Tables detected | 24 |
|   of which page-header boxes | 0 |
|   content tables | 24 |
| Table cells | 828 |
|   non-empty cells | 828 |
|   merged cells (rowspan/colspan>1) | 26 |
| Equations (formula regions) | 6 |
| Figures | 39 |
| Images exported | 39 |
| Warnings | 0 |
| Errors | 0 |
| OCR engine | rapidocr |
| OCR provider | onnxruntime(AzureExecutionProvider,CPUExecutionProvider) |
| ONNX Runtime providers | AzureExecutionProvider, CPUExecutionProvider |
| Layout/TableFormer device | cuda:0 |
| CUDA available (torch) | True |
| GPU | NVIDIA GeForce GTX 1650 (4.0 GB) |
| torch | 2.14.0+cu126 |
| Docling | 2.126.0 |
| Windows | 7 |
| Peak process RSS (MB) | 1805.2 |
| Peak GPU allocation (MB) | 1227.2 |
| Processing time | 9m 45s |

## Element types

| Element type | Count |
|---|---|
| page_header | 272 |
| text | 255 |
| page_footer | 187 |
| list_item | 101 |
| heading | 71 |
| caption | 42 |
| figure | 39 |
| table | 24 |
| equation | 6 |

| Docling label | Count |
|---|---|
| page_header | 272 |
| text | 255 |
| page_footer | 187 |
| list_item | 101 |
| section_header | 71 |
| caption | 42 |
| picture | 39 |
| table | 20 |
| formula | 6 |
| document_index | 4 |

## Validation checks

| Check | Result | Severity | Detail |
|---|---|---|---|
| pages_processed | OK | error | 93/93 pages covered by successful conversion windows |
| windows_status | OK | error | window statuses: success |
| pages_with_text | OK | warning | 93/93 pages have text content (100.0%); empty pages: [] |
| no_catastrophic_empty_runs | OK | warning | consecutive empty page runs: none |
| provenance_consistent | OK | error | 0 elements whose provenance page differs from their page record; 0 elements without provenance |
| bounding_boxes_present | OK | warning | 997/997 elements carry a bounding box |
| reading_order_unique | OK | error | 997 elements, 997 distinct reading-order indices |
| reading_order_monotonic_across_pages | OK | warning | 0 reading-order inversions across page boundaries |
| tables_detected | OK | error | 24 tables detected |
| table_cells_available | OK | error | 24/24 tables have cells; 828 cells total, 828 non-empty (100.0%), 26 merged (rowspan/colspan>1) |
| tables_have_text | OK | warning | 24/24 tables have textual cell content |
| no_placeholder_table_text | OK | error | 0 table elements reduced to '(N cells)' placeholders |
| content_tables_present | OK | error | 24 tables beyond the repeating page-header box (0 header boxes) |
| text_extracted | OK | error | 71,555 characters of non-table text |
| headings_detected | OK | warning | 71 headings |
| parser_completed | OK | warning | 0 parser errors, 0 warnings |
| representative:abbreviation table | WARN | warning | no matching table found |
| representative:table of contents | WARN | warning | no matching table found |
| representative:material-balance table | OK | warning | Crude desalter_p73_t0 p73 6x2 12/12 non-empty cells |
| representative:equipment table | OK | warning | Crude desalter_p62_t0 p62 2x2 4/4 non-empty cells |
| representative:operating-limit table | OK | warning | Crude desalter_p74_t0 p74 9x2 18/18 non-empty cells |
| representative:safety table | WARN | warning | no matching table found |
| representative:procedure/checklist table | OK | warning | Crude desalter_p75_t0 p75 3x2 6/6 non-empty cells |

## Representative tables (real cell content check)

### abbreviation table

_No matching table found by keyword search._

### table of contents

_No matching table found by keyword search._

### material-balance table

`Crude desalter_p73_t0` page 73 — 6x2, 12/12 non-empty cells, 0 merged, 2 header cells

| c0 | c1 |
|---|---|
| Possible Causes | Corrective Action |
| High oil/water interface level. | Check water level by using interface sampling lines; decreas |
| Excessive mixing valve P. | Open mixing valve completely, allow amperage to stabilize, a |
| Excessive water injection. | Reduce wash water injection rate to between 4% and 6% of oil |
| Very high BS&W content in oil feed. | Sample crude for BS&W; decrease wash water injection rate to |
| Electrical failure. | Check voltage and amperage readings; if transformer or entra |

### equipment table

`Crude desalter_p62_t0` page 62 — 2x2, 4/4 non-empty cells, 0 merged, 0 header cells

| c0 | c1 |
|---|---|
| - Typical voltage gradient: | 1,000 to 5,000 V/in. |
| - Critical voltage gradient: | > 12,000 V/in. |

### operating-limit table

`Crude desalter_p74_t0` page 74 — 9x2, 18/18 non-empty cells, 0 merged, 2 header cells, caption: Work Aid 4C: Troubleshooting Desalter  Problem of Oily Effluent Water (Black Water)

| c0 | c1 |
|---|---|
| Possible Causes | Corrective Action |
| Low oil/water interface level. | Check water level by using interface sampling lines; raise l |
| Excessive mixing valve P. | Open mixing valve completely until operation stabilizes, the |
| High effluent water pH. | Check effluent water pH. If greater than 7.5, reevaluate was |
| Sludge in desalter. | Clean desalter. If not possible, try operating with higher i |
| High solids concentration in effluent brine. (Excessive oil  | Check wash water for particulates and minimize where possibl |

### safety table

_No matching table found by keyword search._

### procedure/checklist table

`Crude desalter_p75_t0` page 75 — 3x2, 6/6 non-empty cells, 0 merged, 2 header cells

| c0 | c1 |
|---|---|
| Possible Causes | Corrective Action |
| Oil feed properties -- high BS&W, low gravity, waxy constitu | Slug feed chemical (e.g., 2 to 4 x normal rate) for a maximu |
| Excessive mixing valve P | Open mixing valve completely, allow amperage to stabilize an |

## Windows

| # | Pages | Status | Seconds | Elements | Tables | Images | RSS MB | GPU peak MB | Warn | Err |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1-15 | success | 28.14 | 152 | 6 | 6 | 1805.2 | 1226.2 | 0 | 0 |
| 1 | 16-30 | success | 27.81 | 157 | 1 | 7 | 1769.8 | 1227.2 | 0 | 0 |
| 2 | 31-45 | success | 48.32 | 159 | 0 | 9 | 1776.8 | 1227.2 | 0 | 0 |
| 3 | 46-60 | success | 41.26 | 164 | 0 | 8 | 1663.8 | 1227.2 | 0 | 0 |
| 4 | 61-75 | success | 273.31 | 179 | 9 | 5 | 1586.7 | 1227.2 | 0 | 0 |
| 5 | 76-90 | success | 144.15 | 163 | 8 | 1 | 1583.6 | 1227.2 | 0 | 0 |
| 6 | 91-93 | success | 19.7 | 23 | 0 | 3 | 1474.0 | 873.5 | 0 | 0 |

## Environment

```json
{
  "python": "3.13.5",
  "platform": "Windows-11-10.0.26200-SP0",
  "accelerator_device_requested": "auto",
  "pkg:docling": "2.126.0",
  "pkg:docling-core": "2.95.0",
  "pkg:docling-ibm-models": "4.0.2",
  "pkg:docling-parse": "7.18.0",
  "pkg:rapidocr": "3.9.2",
  "pkg:onnxruntime": "1.29.0",
  "pkg:onnxruntime-gpu": null,
  "pkg:torch": "2.14.0+cu126",
  "pkg:torchvision": "0.29.0+cu126",
  "pkg:transformers": "5.16.1",
  "pkg:pypdfium2": "5.13.0",
  "torch_version": "2.14.0+cu126",
  "torch_cuda_build": "12.6",
  "cuda_available": true,
  "gpu_name": "NVIDIA GeForce GTX 1650",
  "gpu_total_memory_gb": 4.0,
  "gpu_compute_capability": "7.5",
  "onnxruntime_providers": [
    "AzureExecutionProvider",
    "CPUExecutionProvider"
  ],
  "accelerator_device_resolved": "cuda:0",
  "ram_total_gb": 11.84,
  "ram_available_gb_at_start": 2.46,
  "ocr_engine": "rapidocr",
  "ocr_backend": "onnxruntime(AzureExecutionProvider,CPUExecutionProvider)",
  "table_structure_mode": "accurate",
  "table_cell_matching": true,
  "formula_enrichment": false,
  "page_window_size": 15
}
```