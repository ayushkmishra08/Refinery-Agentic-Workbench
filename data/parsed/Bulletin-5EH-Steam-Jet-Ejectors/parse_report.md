# Parse Report: Bulletin-5EH-Steam-Jet-Ejectors

Generated: 2026-09-16T13:27:46.204530+00:00
Source: `Bulletin-5EH-Steam-Jet-Ejectors.pdf` (sha256 `9c1619a235d96921...`)
Overall: **PASSED** (17/23 checks ok)

## Summary

| Metric | Value |
|---|---|
| Pages in PDF | 20 |
| Pages processed (with records) | 20 |
| Pages with text | 20 |
| Empty pages | 0 |
| Elements | 381 |
| Text characters (non-table) | 39,443 |
| Headings | 68 |
| List items | 41 |
| Tables detected | 8 |
|   of which page-header boxes | 0 |
|   content tables | 8 |
| Table cells | 577 |
|   non-empty cells | 577 |
|   merged cells (rowspan/colspan>1) | 35 |
| Equations (formula regions) | 0 |
| Figures | 54 |
| Images exported | 54 |
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
| Windows | 2 |
| Peak process RSS (MB) | 1593.9 |
| Peak GPU allocation (MB) | 1227.2 |
| Processing time | 6m 52s |

## Element types

| Element type | Count |
|---|---|
| text | 103 |
| heading | 68 |
| figure | 54 |
| page_footer | 41 |
| list_item | 41 |
| page_header | 39 |
| caption | 27 |
| table | 8 |

| Docling label | Count |
|---|---|
| text | 103 |
| section_header | 68 |
| picture | 54 |
| page_footer | 41 |
| list_item | 41 |
| page_header | 39 |
| caption | 27 |
| table | 8 |

## Validation checks

| Check | Result | Severity | Detail |
|---|---|---|---|
| pages_processed | OK | error | 20/20 pages covered by successful conversion windows |
| windows_status | OK | error | window statuses: success |
| pages_with_text | OK | warning | 20/20 pages have text content (100.0%); empty pages: [] |
| no_catastrophic_empty_runs | OK | warning | consecutive empty page runs: none |
| provenance_consistent | OK | error | 0 elements whose provenance page differs from their page record; 0 elements without provenance |
| bounding_boxes_present | OK | warning | 381/381 elements carry a bounding box |
| reading_order_unique | OK | error | 381 elements, 381 distinct reading-order indices |
| reading_order_monotonic_across_pages | OK | warning | 0 reading-order inversions across page boundaries |
| tables_detected | OK | error | 8 tables detected |
| table_cells_available | OK | error | 8/8 tables have cells; 577 cells total, 577 non-empty (100.0%), 35 merged (rowspan/colspan>1) |
| tables_have_text | OK | warning | 8/8 tables have textual cell content |
| no_placeholder_table_text | OK | error | 0 table elements reduced to '(N cells)' placeholders |
| content_tables_present | OK | error | 8 tables beyond the repeating page-header box (0 header boxes) |
| text_extracted | OK | error | 39,443 characters of non-table text |
| headings_detected | OK | warning | 68 headings |
| parser_completed | OK | warning | 0 parser errors, 0 warnings |
| representative:abbreviation table | WARN | warning | no matching table found |
| representative:table of contents | WARN | warning | no matching table found |
| representative:material-balance table | WARN | warning | no matching table found |
| representative:equipment table | OK | warning | Bulletin-5EH-Steam-Jet-Ejectors_p1_t0 p1 26x2 49/49 non-empty cells |
| representative:operating-limit table | WARN | warning | no matching table found |
| representative:safety table | WARN | warning | no matching table found |
| representative:procedure/checklist table | WARN | warning | no matching table found |

## Representative tables (real cell content check)

### abbreviation table

_No matching table found by keyword search._

### table of contents

_No matching table found by keyword search._

### material-balance table

_No matching table found by keyword search._

### equipment table

`Bulletin-5EH-Steam-Jet-Ejectors_p1_t0` page 1 — 26x2, 49/49 non-empty cells, 3 merged, 3 header cells

| c0 | c1 |
|---|---|
| Index | Index |
| Description | Page |
| Introduction | 1 |
| Advantages | 2 |
| Performance Characteristics | 3 |
| SINGLE STAGE EJECTORS | SINGLE STAGE EJECTORS |

### operating-limit table

_No matching table found by keyword search._

### safety table

_No matching table found by keyword search._

### procedure/checklist table

_No matching table found by keyword search._

## Windows

| # | Pages | Status | Seconds | Elements | Tables | Images | RSS MB | GPU peak MB | Warn | Err |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1-15 | success | 300.29 | 282 | 8 | 40 | 1541.1 | 1226.2 | 0 | 0 |
| 1 | 16-20 | success | 110.57 | 99 | 0 | 14 | 1593.9 | 1227.2 | 0 | 0 |

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
  "ram_available_gb_at_start": 1.66,
  "ocr_engine": "rapidocr",
  "ocr_backend": "onnxruntime(AzureExecutionProvider,CPUExecutionProvider)",
  "table_structure_mode": "accurate",
  "table_cell_matching": true,
  "formula_enrichment": false,
  "page_window_size": 15
}
```