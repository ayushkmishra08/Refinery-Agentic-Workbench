# Parse Report: API610 pump operation manual

Generated: 2026-09-16T13:28:35.469050+00:00
Source: `API610 pump operation manual.pdf` (sha256 `a3fc0e8ab00610dd...`)
Overall: **PASSED** (18/23 checks ok)

## Summary

| Metric | Value |
|---|---|
| Pages in PDF | 82 |
| Pages processed (with records) | 82 |
| Pages with text | 82 |
| Empty pages | 0 |
| Elements | 1222 |
| Text characters (non-table) | 84,401 |
| Headings | 79 |
| List items | 434 |
| Tables detected | 17 |
|   of which page-header boxes | 0 |
|   content tables | 17 |
| Table cells | 313 |
|   non-empty cells | 313 |
|   merged cells (rowspan/colspan>1) | 3 |
| Equations (formula regions) | 0 |
| Figures | 344 |
| Images exported | 344 |
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
| Windows | 6 |
| Peak process RSS (MB) | 2182.9 |
| Peak GPU allocation (MB) | 856.4 |
| Processing time | 22m 10s |

## Element types

| Element type | Count |
|---|---|
| list_item | 434 |
| figure | 344 |
| text | 201 |
| page_header | 83 |
| heading | 79 |
| caption | 62 |
| table | 17 |
| page_footer | 2 |

| Docling label | Count |
|---|---|
| list_item | 434 |
| picture | 344 |
| text | 201 |
| page_header | 83 |
| section_header | 79 |
| caption | 62 |
| table | 15 |
| document_index | 2 |
| page_footer | 2 |

## Validation checks

| Check | Result | Severity | Detail |
|---|---|---|---|
| pages_processed | OK | error | 82/82 pages covered by successful conversion windows |
| windows_status | OK | error | window statuses: success |
| pages_with_text | OK | warning | 82/82 pages have text content (100.0%); empty pages: [] |
| no_catastrophic_empty_runs | OK | warning | consecutive empty page runs: none |
| provenance_consistent | OK | error | 0 elements whose provenance page differs from their page record; 0 elements without provenance |
| bounding_boxes_present | OK | warning | 1222/1222 elements carry a bounding box |
| reading_order_unique | OK | error | 1222 elements, 1222 distinct reading-order indices |
| reading_order_monotonic_across_pages | WARN | warning | 1 reading-order inversions across page boundaries |
| tables_detected | OK | error | 17 tables detected |
| table_cells_available | OK | error | 17/17 tables have cells; 313 cells total, 313 non-empty (100.0%), 3 merged (rowspan/colspan>1) |
| tables_have_text | OK | warning | 17/17 tables have textual cell content |
| no_placeholder_table_text | OK | error | 0 table elements reduced to '(N cells)' placeholders |
| content_tables_present | OK | error | 17 tables beyond the repeating page-header box (0 header boxes) |
| text_extracted | OK | error | 84,401 characters of non-table text |
| headings_detected | OK | warning | 79 headings |
| parser_completed | OK | warning | 0 parser errors, 0 warnings |
| representative:abbreviation table | WARN | warning | no matching table found |
| representative:table of contents | WARN | warning | no matching table found |
| representative:material-balance table | OK | warning | API610 pump operation manual_p3_t0 p3 29x1 29/29 non-empty cells |
| representative:equipment table | OK | warning | API610 pump operation manual_p75_t0 p75 12x8 82/82 non-empty cells |
| representative:operating-limit table | WARN | warning | no matching table found |
| representative:safety table | WARN | warning | no matching table found |
| representative:procedure/checklist table | OK | warning | API610 pump operation manual_p4_t0 p4 29x1 29/29 non-empty cells |

## Representative tables (real cell content check)

### abbreviation table

_No matching table found by keyword search._

### table of contents

_No matching table found by keyword search._

### material-balance table

`API610 pump operation manual_p3_t0` page 3 — 29x1, 29/29 non-empty cells, 0 merged, 0 header cells

| c0 |
|---|
| SECTION ONE - PRODUCT DESCRIPTION .......................... |
| 1.1 INTRODUCTION ........................................... |
| 1.2 PUMP CASE, IMPELLER, AND WEAR RINGS .................... |
| 1.2.1 Pump Case ............................................ |
| 1.2.2 Impeller and Wear Rings .............................. |
| 1.3 SEAL CHAMBER ........................................... |

### equipment table

`API610 pump operation manual_p75_t0` page 75 — 12x8, 82/82 non-empty cells, 2 merged, 9 header cells

| c0 | c1 | c2 | c3 | c4 | c5 | c6 | c7 |
|---|---|---|---|---|---|---|---|
|  | Number of identical pumps (including reserve pumps) | Number of identical pumps (including reserve pumps) | Number of identical pumps (including reserve pumps) | Number of identical pumps (including reserve pumps) | Number of identical pumps (including reserve pumps) | Number of identical pumps (including reserve pumps) | Number of identical pumps (including reserve pumps) |
|  | 2 | 3 | 4 | 5 | 6 and 7 | 8 and 9 | 10 and more |
| Spare parts | Quantity of spare parts | Quantity of spare parts | Quantity of spare parts | Quantity of spare parts | Quantity of spare parts | Quantity of spare parts | Quantity of spare parts |
| Impeller | 1 | 1 | 1 | 2 | 2 | 3 | 30% |
| Casing wear ring, impeller ring | 2 | 2 | 2 | 3 | 3 | 4 | 50% |
| Shaft with fitting key and shaft screws or nuts. | 1 | 1 | 2 | 2 | 2 | 3 | 30% |

### operating-limit table

_No matching table found by keyword search._

### safety table

_No matching table found by keyword search._

### procedure/checklist table

`API610 pump operation manual_p4_t0` page 4 — 29x1, 29/29 non-empty cells, 0 merged, 0 header cells

| c0 |
|---|
| 4.3.3 Grouting Procedure ................................... |
| SECTION FIVE - PIPING AND ALIGNMENT ........................ |
| 5.1 PIPING THE SYSTEM ...................................... |
| 5.2 ALIGNMENT* ............................................. |
| SECTION SIX - LUBRICATION .................................. |
| 6.1 OIL RING LUBRICATION ................................... |

## Windows

| # | Pages | Status | Seconds | Elements | Tables | Images | RSS MB | GPU peak MB | Warn | Err |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1-15 | success | 88.81 | 214 | 3 | 51 | 2182.9 | 856.4 | 0 | 0 |
| 1 | 16-30 | success | 78.94 | 257 | 0 | 72 | 2170.5 | 856.4 | 0 | 0 |
| 2 | 31-45 | success | 321.32 | 265 | 6 | 55 | 597.3 | 856.4 | 0 | 0 |
| 3 | 46-60 | success | 348.29 | 215 | 1 | 77 | 695.1 | 856.4 | 0 | 0 |
| 4 | 61-75 | success | 424.14 | 227 | 5 | 74 | 695.0 | 856.4 | 0 | 0 |
| 5 | 76-82 | success | 45.16 | 44 | 2 | 15 | 637.2 | 856.4 | 0 | 0 |

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
  "ram_available_gb_at_start": 4.18,
  "ocr_engine": "rapidocr",
  "ocr_backend": "onnxruntime(AzureExecutionProvider,CPUExecutionProvider)",
  "table_structure_mode": "accurate",
  "table_cell_matching": true,
  "formula_enrichment": false,
  "page_window_size": 15
}
```