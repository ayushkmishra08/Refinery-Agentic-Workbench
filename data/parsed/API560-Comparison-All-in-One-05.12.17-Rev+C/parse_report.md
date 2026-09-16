# Parse Report: API560-Comparison-All-in-One-05.12.17-Rev+C

Generated: 2026-09-16T12:31:11.049017+00:00
Source: `API560-Comparison-All-in-One-05.12.17-Rev+C.pdf` (sha256 `ecc507a27bb37926...`)
Overall: **PASSED** (21/23 checks ok)

## Summary

| Metric | Value |
|---|---|
| Pages in PDF | 105 |
| Pages processed (with records) | 105 |
| Pages with text | 105 |
| Empty pages | 0 |
| Elements | 1337 |
| Text characters (non-table) | 68,852 |
| Headings | 165 |
| List items | 110 |
| Tables detected | 124 |
|   of which page-header boxes | 0 |
|   content tables | 124 |
| Table cells | 1699 |
|   non-empty cells | 1699 |
|   merged cells (rowspan/colspan>1) | 565 |
| Equations (formula regions) | 0 |
| Figures | 150 |
| Images exported | 150 |
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
| Peak process RSS (MB) | 2275.7 |
| Peak GPU allocation (MB) | 857.4 |
| Processing time | 34m 28s |

## Element types

| Element type | Count |
|---|---|
| page_footer | 467 |
| text | 258 |
| heading | 165 |
| figure | 150 |
| table | 124 |
| list_item | 110 |
| page_header | 62 |
| caption | 1 |

| Docling label | Count |
|---|---|
| page_footer | 467 |
| text | 258 |
| section_header | 165 |
| picture | 150 |
| table | 123 |
| list_item | 110 |
| page_header | 62 |
| document_index | 1 |
| caption | 1 |

## Validation checks

| Check | Result | Severity | Detail |
|---|---|---|---|
| pages_processed | OK | error | 105/105 pages covered by successful conversion windows |
| windows_status | OK | error | window statuses: success |
| pages_with_text | OK | warning | 105/105 pages have text content (100.0%); empty pages: [] |
| no_catastrophic_empty_runs | OK | warning | consecutive empty page runs: none |
| provenance_consistent | OK | error | 0 elements whose provenance page differs from their page record; 0 elements without provenance |
| bounding_boxes_present | OK | warning | 1337/1337 elements carry a bounding box |
| reading_order_unique | OK | error | 1337 elements, 1337 distinct reading-order indices |
| reading_order_monotonic_across_pages | OK | warning | 0 reading-order inversions across page boundaries |
| tables_detected | OK | error | 124 tables detected |
| table_cells_available | OK | error | 124/124 tables have cells; 1699 cells total, 1699 non-empty (100.0%), 565 merged (rowspan/colspan>1) |
| tables_have_text | OK | warning | 124/124 tables have textual cell content |
| no_placeholder_table_text | OK | error | 0 table elements reduced to '(N cells)' placeholders |
| content_tables_present | OK | error | 124 tables beyond the repeating page-header box (0 header boxes) |
| text_extracted | OK | error | 68,852 characters of non-table text |
| headings_detected | OK | warning | 165 headings |
| parser_completed | OK | warning | 0 parser errors, 0 warnings |
| representative:abbreviation table | WARN | warning | no matching table found |
| representative:table of contents | WARN | warning | no matching table found |
| representative:material-balance table | OK | warning | API560-Comparison-All-in-One-05.12.17-Rev+C_p81_t0 p81 2x5 7/7 non-empty cells |
| representative:equipment table | OK | warning | API560-Comparison-All-in-One-05.12.17-Rev+C_p29_t1 p29 3x3 7/7 non-empty cells |
| representative:operating-limit table | OK | warning | API560-Comparison-All-in-One-05.12.17-Rev+C_p5_t0 p5 7x5 18/18 non-empty cells |
| representative:safety table | OK | warning | API560-Comparison-All-in-One-05.12.17-Rev+C_p64_t0 p64 7x5 16/16 non-empty cells |
| representative:procedure/checklist table | OK | warning | API560-Comparison-All-in-One-05.12.17-Rev+C_p51_t1 p51 2x2 4/4 non-empty cells |

## Representative tables (real cell content check)

### abbreviation table

_No matching table found by keyword search._

### table of contents

_No matching table found by keyword search._

### material-balance table

`API560-Comparison-All-in-One-05.12.17-Rev+C_p81_t0` page 81 — 2x5, 7/7 non-empty cells, 2 merged, 5 header cells

| c0 | c1 | c2 | c3 | c4 |
|---|---|---|---|---|
| 1 st Edition | 2 nd Edition | 3 rd Edition | 4 th Edition damper that permits range. ducting from paralle | 5 th Edition cell over the APH system's should be hydraulica |
| the use of a variable speed or multispeed fan driver should  | the use of a variable speed or multispeed fan driver should  | the use of a variable speed or multispeed fan driver should  | a) Pressure and temperature connections should be provided u | a) Pressure and temperature connections should be provided u |

### equipment table

`API560-Comparison-All-in-One-05.12.17-Rev+C_p29_t1` page 29 — 3x3, 7/7 non-empty cells, 2 merged, 0 header cells

| c0 | c1 | c2 |
|---|---|---|
| Added in 2 nd Edition | Ceramic-fibre systems shall not be applied for services wher | Ceramic fibre shall not be used as the hot face layer if the |
| Where soot blowers or steam lances are provided, unprotected | Ceramic fiber shall not be used in convection sections where | Ceramic fiber shall not be used in convection sections where |
| Added in 2 nd Edition | Anchors shall be installed before applying protection coatin | Anchors shall be installed before applying protection coatin |

### operating-limit table

`API560-Comparison-All-in-One-05.12.17-Rev+C_p5_t0` page 5 — 7x5, 18/18 non-empty cells, 8 merged, 9 header cells

| c0 | c1 | c2 | c3 | c4 |
|---|---|---|---|---|
| 1 st Edition | 2 nd Edition | 3 rd Edition | 4 th Edition | 5 th Edition |
| Section 2- Design Considerations | Section 2- Design Considerations | Section 2- Design Considerations | Section 2- Design Considerations | Section 2- Design Considerations |
| Multi-pass heaters shall be designed for hydraulic and therm | Multi-pass heaters shall be designed for hydraulic symmetry  | Multi-pass heaters shall be designed for hydraulic symmetry  | Multi-pass heaters shall be designed for hydraulic symmetry  | Multi-pass heaters shall be designed for hydraulic symmetry  |
| The number of passes shall be minimized. | The number of passes shall be minimized for vaporizing fluid | The number of passes shall be minimized for vaporizing fluid | The number of passes shall be minimized for vaporizing fluid | The number of passes shall be minimized for vaporizing fluid |
| Added in 4 th Edition | Added in 4 th Edition | Added in 4 th Edition | Where the average radiant heat flux density is specified bas | Where the average radiant heat flux density is specified bas |
| Added in 4 th Edition | Added in 4 th Edition | Added in 4 th Edition | Margins provided in the combustion system are not intended t | Margins provided in the combustion system are not intended t |

### safety table

`API560-Comparison-All-in-One-05.12.17-Rev+C_p64_t0` page 64 — 7x5, 16/16 non-empty cells, 7 merged, 10 header cells

| c0 | c1 | c2 | c3 | c4 |
|---|---|---|---|---|
| 1 st Edition | 2 nd Edition | 3 rd Edition | 4 th Edition | 5 th Edition |
| Reactor Charge Heater (B-301) Capacity Increase Project A Po | Reactor Charge Heater (B-301) Capacity Increase Project A Po | Reactor Charge Heater (B-301) Capacity Increase Project A Po | Reactor Charge Heater (B-301) Capacity Increase Project A Po | Reactor Charge Heater (B-301) Capacity Increase Project A Po |
| Appendix F | Appendix F | Appendix F | Appendix F | Appendix F |
| Added in 3 rd Edition | Appendix F- Air preheater systems for fired process heaters | Appendix F- Air preheater systems for fired process heaters | Appendix F- Air preheater systems for fired process heaters | Appendix F- Air preheater systems for fired process heaters |
| F.2.1.3 b) increased NO x production (resulting from higher  | F.2.1.3 b) increased NO x production (resulting from higher  | F.2.1.3 b) increased NO x production (resulting from higher  | F.2.1.3 b) increased NO x production (resulting from higher  | Potential change in NOx production (new burners may mitigate |
| Added in 5 th Edition | Added in 5 th Edition | negative effects of heat | Added in 5 th Edition | Cost of running fans. |

### procedure/checklist table

`API560-Comparison-All-in-One-05.12.17-Rev+C_p51_t1` page 51 — 2x2, 4/4 non-empty cells, 0 merged, 0 header cells

| c0 | c1 |
|---|---|
| The minimum size of bolts shall be ¾ inch in diameter, excep | The minimum size of bolts shall be 5/8 inch in diameter, exc |
| Heater steel shall power tool cleaned to SSPC SP-3 and prime | Heater steel shall be sand blasted to SSPC SP-6 and primed w |

## Windows

| # | Pages | Status | Seconds | Elements | Tables | Images | RSS MB | GPU peak MB | Warn | Err |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1-15 | success | 192.82 | 199 | 14 | 25 | 2228.4 | 856.4 | 0 | 0 |
| 1 | 16-30 | success | 176.46 | 181 | 21 | 20 | 2275.7 | 857.4 | 0 | 0 |
| 2 | 31-45 | success | 1021.79 | 166 | 18 | 23 | 1577.6 | 856.4 | 0 | 0 |
| 3 | 46-60 | success | 162.07 | 183 | 20 | 20 | 1668.8 | 857.4 | 0 | 0 |
| 4 | 61-75 | success | 177.48 | 157 | 18 | 19 | 1646.4 | 856.4 | 0 | 0 |
| 5 | 76-90 | success | 167.85 | 233 | 18 | 22 | 1663.0 | 856.4 | 0 | 0 |
| 6 | 91-105 | success | 143.7 | 218 | 15 | 21 | 1633.4 | 856.4 | 0 | 0 |

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
  "ram_available_gb_at_start": 2.62,
  "ocr_engine": "rapidocr",
  "ocr_backend": "onnxruntime(AzureExecutionProvider,CPUExecutionProvider)",
  "table_structure_mode": "accurate",
  "table_cell_matching": true,
  "formula_enrichment": false,
  "page_window_size": 15
}
```