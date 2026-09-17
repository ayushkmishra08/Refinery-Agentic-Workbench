# Parse Report: ESBWR

Generated: 2026-09-16T14:15:13.244596+00:00
Source: `ESBWR.pdf` (sha256 `5631d5f81fa9c381...`)
Overall: **PASSED** (21/23 checks ok)

## Summary

| Metric | Value |
|---|---|
| Pages in PDF | 504 |
| Pages processed (with records) | 504 |
| Pages with text | 499 |
| Empty pages | 5 |
| Elements | 4869 |
| Text characters (non-table) | 955,026 |
| Headings | 740 |
| List items | 829 |
| Tables detected | 50 |
|   of which page-header boxes | 0 |
|   content tables | 50 |
| Table cells | 1684 |
|   non-empty cells | 1684 |
|   merged cells (rowspan/colspan>1) | 9 |
| Equations (formula regions) | 0 |
| Figures | 82 |
| Images exported | 82 |
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
| Windows | 34 |
| Peak process RSS (MB) | 1727.5 |
| Peak GPU allocation (MB) | 1250.7 |
| Processing time | 32m 48s |

## Element types

| Element type | Count |
|---|---|
| text | 2099 |
| list_item | 829 |
| heading | 740 |
| page_footer | 478 |
| page_header | 475 |
| caption | 100 |
| figure | 82 |
| table | 50 |
| code | 12 |
| footnote | 4 |

| Docling label | Count |
|---|---|
| text | 2099 |
| list_item | 829 |
| section_header | 740 |
| page_footer | 478 |
| page_header | 475 |
| caption | 100 |
| picture | 82 |
| table | 46 |
| code | 12 |
| document_index | 4 |
| footnote | 4 |

## Validation checks

| Check | Result | Severity | Detail |
|---|---|---|---|
| pages_processed | OK | error | 504/504 pages covered by successful conversion windows |
| windows_status | OK | error | window statuses: success |
| pages_with_text | OK | warning | 499/504 pages have text content (99.0%); empty pages: [495, 498, 502, 503, 504] |
| no_catastrophic_empty_runs | WARN | warning | consecutive empty page runs: 502-504 |
| provenance_consistent | OK | error | 0 elements whose provenance page differs from their page record; 0 elements without provenance |
| bounding_boxes_present | OK | warning | 4869/4869 elements carry a bounding box |
| reading_order_unique | OK | error | 4869 elements, 4869 distinct reading-order indices |
| reading_order_monotonic_across_pages | OK | warning | 0 reading-order inversions across page boundaries |
| tables_detected | OK | error | 50 tables detected |
| table_cells_available | OK | error | 50/50 tables have cells; 1684 cells total, 1684 non-empty (100.0%), 9 merged (rowspan/colspan>1) |
| tables_have_text | OK | warning | 50/50 tables have textual cell content |
| no_placeholder_table_text | OK | error | 0 table elements reduced to '(N cells)' placeholders |
| content_tables_present | OK | error | 50 tables beyond the repeating page-header box (0 header boxes) |
| text_extracted | OK | error | 955,026 characters of non-table text |
| headings_detected | OK | warning | 740 headings |
| parser_completed | OK | warning | 0 parser errors, 0 warnings |
| representative:abbreviation table | WARN | warning | no matching table found |
| representative:table of contents | OK | warning | ESBWR_p7_t0 p7 30x2 56/56 non-empty cells |
| representative:material-balance table | OK | warning | ESBWR_p152_t0 p152 12x5 41/41 non-empty cells |
| representative:equipment table | OK | warning | ESBWR_p269_t0 p269 6x2 12/12 non-empty cells |
| representative:operating-limit table | OK | warning | ESBWR_p128_t0 p128 20x2 40/40 non-empty cells |
| representative:safety table | OK | warning | ESBWR_p12_t0 p12 46x2 92/92 non-empty cells |
| representative:procedure/checklist table | OK | warning | ESBWR_p415_t0 p415 8x3 24/24 non-empty cells |

## Representative tables (real cell content check)

### abbreviation table

_No matching table found by keyword search._

### table of contents

`ESBWR_p7_t0` page 7 — 30x2, 56/56 non-empty cells, 0 merged, 0 header cells

| c0 | c1 |
|---|---|
| Table of Contents | iii |
| Glossary of Acronyms | vi |
| 1. Introduction | 1 |
| 2. Core and Vessel Design |  |
| 2.1 Fuel Bundle Design | 2.1-1 |
| 2.2 Core Design | 2.2-1 |

### material-balance table

`ESBWR_p152_t0` page 152 — 12x5, 41/41 non-empty cells, 2 merged, 2 header cells

| c0 | c1 | c2 | c3 | c4 |
|---|---|---|---|---|
|  | Feedwater booster pumps | Feedwater booster pumps | Feedwater pumps | Feedwater pumps |
| Pump TDH, m (ft): | 381 | 1250 | 408 | 1,340 |
| Flow, m3/hr (gpm): | 2,548 | 11,200 | 2,548 | 11,200 |
| Pump efficiency, %: | 80 | 80 | 80 | 80 |
| Motor input power, MWe (hp): |  |  |  |  |
| Drive input power, MWe (hp): | N/A | N/A |  |  |

### equipment table

`ESBWR_p269_t0` page 269 — 6x2, 12/12 non-empty cells, 0 merged, 0 header cells, caption: Table 3.8-1 PSWS Pump Design Data

| c0 | c1 |
|---|---|
| Type | Vertical, multi-stage, deep-well, centrifugal pump |
| Drive type | AC motor |
| Number required | 4 (2 per train) |
| Motor power, each (kW) | 460 (617 hp) |
| Capacity (Normal Operation) | 760 kg/sec (1676 lb/sec), 50% capacity per pump |
| Pump developed head | 42.7 m (140 ft) |

### operating-limit table

`ESBWR_p128_t0` page 128 — 20x2, 40/40 non-empty cells, 0 merged, 2 header cells, caption: Table 2.8-3 Preliminary Main Turbine Protective Trips

| c0 | c1 |
|---|---|
| Preliminary Turbine Trips | Typical Setpoint Value |
| 1. Condenser Low Vacuum | ~ 32.8 kPa (~ 9.73 in Hg) |
| 2. Thrust Bearing Wear | + 0.9 mm (+ 35 mils) |
| 3. Low Lube Oil Pressure | ~ 55 kPa (~8 psig) |
| 4. Low Fast Acting Solenoid Oil Pressure | ~7.6 MPa (~1100 psig) |
| 5. High Exhaust Hood Temperature | ~107 ° C (~225 ° F) |

### safety table

`ESBWR_p12_t0` page 12 — 46x2, 92/92 non-empty cells, 0 merged, 0 header cells

| c0 | c1 |
|---|---|
| ETS | Emergency Trip System |
| FAPCS | Fuel and Auxiliary Pools Cooling System |
| FATT | Fracture Appearance Transition Temperature |
| FCM | File Control Module |
| FCS | Flammability Control System |
| FCU | Fan Cooling Unit |

### procedure/checklist table

`ESBWR_p415_t0` page 415 — 8x3, 24/24 non-empty cells, 0 merged, 3 header cells, caption: Table 7.2-3  APRM Trip Function Summary

| c0 | c1 | c2 |
|---|---|---|
| Trip Function | Trip Setpoint | Action |
| APRM Upscale Flux Trip | 120% Power 15% Power | Scram (only in RUN) Scram (not in RUN) |
| APRM Upscale Flux Alarm | 108% Power 12% Power | Rod Block (only in RUN) Rod Block (not in RUN) |
| APRM Upscale Simulated Thermal Power Trip | 115% Power | Scram |
| APRM Inoperable | 1. LPRM input too few; 2. Module interlocks disconnect | Scram and Rod Block Scram and Rod Block |
| APRM Downscale APRM Rapid Increase | 5% Power | Rod Block (only in RUN) |

## Windows

| # | Pages | Status | Seconds | Elements | Tables | Images | RSS MB | GPU peak MB | Warn | Err |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1-15 | success | 118.15 | 111 | 8 | 3 | 1727.5 | 1226.2 | 0 | 0 |
| 1 | 16-30 | success | 74.81 | 130 | 6 | 3 | 1633.3 | 1250.7 | 0 | 0 |
| 2 | 31-45 | success | 32.43 | 109 | 1 | 4 | 1642.8 | 1235.4 | 0 | 0 |
| 3 | 46-60 | success | 54.35 | 112 | 0 | 7 | 1660.0 | 1235.4 | 0 | 0 |
| 4 | 61-75 | success | 27.33 | 130 | 1 | 4 | 1549.0 | 1235.4 | 0 | 0 |
| 5 | 76-90 | success | 25.82 | 164 | 2 | 1 | 1479.4 | 1235.4 | 0 | 0 |
| 6 | 91-105 | success | 39.73 | 84 | 2 | 1 | 1481.0 | 1235.4 | 0 | 0 |
| 7 | 106-120 | success | 25.33 | 141 | 0 | 0 | 1493.0 | 1235.4 | 0 | 0 |
| 8 | 121-135 | success | 113.63 | 108 | 6 | 1 | 1489.3 | 1235.4 | 0 | 0 |
| 9 | 136-150 | success | 77.42 | 119 | 0 | 4 | 1523.0 | 1235.4 | 0 | 0 |
| 10 | 151-165 | success | 71.56 | 165 | 1 | 0 | 1525.6 | 1235.4 | 0 | 0 |
| 11 | 166-180 | success | 52.17 | 146 | 1 | 4 | 1610.3 | 1235.4 | 0 | 0 |
| 12 | 181-195 | success | 75.3 | 124 | 3 | 3 | 1610.1 | 1235.4 | 0 | 0 |
| 13 | 196-210 | success | 6.91 | 169 | 1 | 0 | 1602.7 | 1235.4 | 0 | 0 |
| 14 | 211-225 | success | 107.5 | 170 | 0 | 0 | 1613.2 | 1235.4 | 0 | 0 |
| 15 | 226-240 | success | 101.21 | 171 | 0 | 0 | 1613.7 | 1235.4 | 0 | 0 |
| 16 | 241-255 | success | 103.33 | 142 | 3 | 0 | 1613.9 | 1235.4 | 0 | 0 |
| 17 | 256-270 | success | 79.4 | 193 | 3 | 0 | 1629.6 | 1235.4 | 0 | 0 |
| 18 | 271-285 | success | 44.39 | 115 | 0 | 6 | 1642.4 | 1235.4 | 0 | 0 |
| 19 | 286-300 | success | 103.17 | 161 | 1 | 0 | 1630.2 | 1235.4 | 0 | 0 |
| 20 | 301-315 | success | 111.11 | 166 | 0 | 2 | 1636.7 | 1235.4 | 0 | 0 |
| 21 | 316-330 | success | 60.49 | 134 | 0 | 1 | 1639.8 | 1236.4 | 0 | 0 |
| 22 | 331-345 | success | 28.48 | 167 | 1 | 0 | 1637.3 | 1245.4 | 0 | 0 |
| 23 | 346-360 | success | 73.05 | 185 | 2 | 0 | 1653.4 | 1235.4 | 0 | 0 |
| 24 | 361-375 | success | 16.33 | 191 | 2 | 0 | 1653.6 | 1235.4 | 0 | 0 |
| 25 | 376-390 | success | 7.47 | 201 | 0 | 0 | 1655.3 | 1235.4 | 0 | 0 |
| 26 | 391-405 | success | 15.43 | 210 | 0 | 0 | 1624.1 | 1235.4 | 0 | 0 |
| 27 | 406-420 | success | 36.95 | 175 | 4 | 4 | 1636.0 | 1235.4 | 0 | 0 |
| 28 | 421-435 | success | 3.38 | 115 | 2 | 0 | 1623.7 | 1235.4 | 0 | 0 |
| 29 | 436-450 | success | 8.84 | 192 | 0 | 1 | 1640.1 | 1236.4 | 0 | 0 |
| 30 | 451-465 | success | 20.03 | 122 | 0 | 5 | 1649.1 | 1235.4 | 0 | 0 |
| 31 | 466-480 | success | 83.02 | 172 | 0 | 5 | 1664.3 | 1235.4 | 0 | 0 |
| 32 | 481-495 | success | 102.35 | 61 | 0 | 14 | 1706.7 | 1235.4 | 0 | 0 |
| 33 | 496-504 | success | 38.88 | 14 | 0 | 9 | 1723.2 | 1235.4 | 0 | 0 |

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
  "ram_available_gb_at_start": 2.15,
  "ocr_engine": "rapidocr",
  "ocr_backend": "onnxruntime(AzureExecutionProvider,CPUExecutionProvider)",
  "table_structure_mode": "accurate",
  "table_cell_matching": true,
  "formula_enrichment": false,
  "page_window_size": 15
}
```