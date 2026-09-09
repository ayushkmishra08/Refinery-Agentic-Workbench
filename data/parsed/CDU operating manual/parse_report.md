# Parse Report: CDU operating manual

Generated: 2026-09-09T17:17:23.839410+00:00
Source: `CDU operating manual.pdf` (sha256 `a8f542d2b19cf9db...`)
Overall: **PASSED** (23/24 checks ok)

## Summary

| Metric | Value |
|---|---|
| Pages in PDF | 562 |
| Pages processed (with records) | 562 |
| Pages with text | 562 |
| Empty pages | 0 |
| Elements | 7732 |
| Text characters (non-table) | 875,207 |
| Headings | 1177 |
| List items | 3436 |
| Tables detected | 814 |
|   of which page-header boxes | 527 |
|   content tables | 287 |
| Table cells | 15506 |
|   non-empty cells | 15506 |
|   merged cells (rowspan/colspan>1) | 1148 |
| Equations (formula regions) | 2 |
| Figures | 600 |
| Images exported | 600 |
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
| Windows | 38 |
| Peak process RSS (MB) | 2165.0 |
| Peak GPU allocation (MB) | 1413.7 |
| Processing time | 43m 55s |

## Element types

| Element type | Count |
|---|---|
| list_item | 3436 |
| text | 1682 |
| heading | 1177 |
| table | 814 |
| figure | 600 |
| footnote | 14 |
| caption | 6 |
| equation | 2 |
| page_footer | 1 |

| Docling label | Count |
|---|---|
| list_item | 3436 |
| text | 1682 |
| section_header | 1177 |
| table | 813 |
| picture | 600 |
| footnote | 14 |
| caption | 6 |
| formula | 2 |
| document_index | 1 |
| page_footer | 1 |

## Validation checks

| Check | Result | Severity | Detail |
|---|---|---|---|
| expected_page_count | OK | warning | PDF reports 562 pages, expected ~562 |
| pages_processed | OK | error | 562/562 pages covered by successful conversion windows |
| windows_status | OK | error | window statuses: success |
| pages_with_text | OK | warning | 562/562 pages have text content (100.0%); empty pages: [] |
| no_catastrophic_empty_runs | OK | warning | consecutive empty page runs: none |
| provenance_consistent | OK | error | 0 elements whose provenance page differs from their page record; 0 elements without provenance |
| bounding_boxes_present | OK | warning | 7732/7732 elements carry a bounding box |
| reading_order_unique | OK | error | 7732 elements, 7732 distinct reading-order indices |
| reading_order_monotonic_across_pages | WARN | warning | 19 reading-order inversions across page boundaries |
| tables_detected | OK | error | 814 tables detected |
| table_cells_available | OK | error | 814/814 tables have cells; 15506 cells total, 15506 non-empty (100.0%), 1148 merged (rowspan/colspan>1) |
| tables_have_text | OK | warning | 814/814 tables have textual cell content |
| no_placeholder_table_text | OK | error | 0 table elements reduced to '(N cells)' placeholders |
| content_tables_present | OK | error | 287 tables beyond the repeating page-header box (527 header boxes) |
| text_extracted | OK | error | 875,207 characters of non-table text |
| headings_detected | OK | warning | 1177 headings |
| parser_completed | OK | warning | 0 parser errors, 0 warnings |
| representative:abbreviation table | OK | warning | CDU operating manual_p9_t1 p9 31x2 62/62 non-empty cells |
| representative:table of contents | OK | warning | CDU operating manual_p2_t1 p2 16x5 80/80 non-empty cells |
| representative:material-balance table | OK | warning | CDU operating manual_p25_t3 p25 6x8 47/47 non-empty cells |
| representative:equipment table | OK | warning | CDU operating manual_p345_t3 p345 14x6 80/80 non-empty cells |
| representative:operating-limit table | OK | warning | CDU operating manual_p32_t1 p32 3x5 14/14 non-empty cells |
| representative:safety table | OK | warning | CDU operating manual_p414_t1 p414 6x3 18/18 non-empty cells |
| representative:procedure/checklist table | OK | warning | CDU operating manual_p413_t1 p413 6x3 18/18 non-empty cells |

## Representative tables (real cell content check)

### abbreviation table

`CDU operating manual_p9_t1` page 9 — 31x2, 62/62 non-empty cells, 0 merged, 2 header cells

| c0 | c1 |
|---|---|
| ABBREVIATION | EXPANSION |
| ATF | Aviation Turbine Fuel |
| ATP | Additional Tank age Project |
| BARC | Bhabha Atomic Research Centre |
| BA | Breathing Apparatus |
| BCW | Bearing Cooling Water |

### table of contents

`CDU operating manual_p2_t1` page 2 — 16x5, 80/80 non-empty cells, 0 merged, 5 header cells

| c0 | c1 | c2 | c3 | c4 |
|---|---|---|---|---|
| CHAPTER No: | TITLE | FROM PAGE NO | LATEST REV NO. | REV DATE |
| 1 | Administrative Requirements of the Manual Section A : Forewo | 1 | 0 | 31-03-2012 |
| 2 | Introduction | 17 | 0 | 31-03-2012 |
| 3 | Basis of Design | 20 | 0 | 31-03-2012 |
| 4 | Feed and Product Characteristics | 38 | 0 | 31-03-2012 |
| 5 | Brief Process Description & Process Chemistry | 46 | 0 | 31-03-2012 |

### material-balance table

`CDU operating manual_p25_t3` page 25 — 6x8, 47/47 non-empty cells, 0 merged, 8 header cells

| c0 | c1 | c2 | c3 | c4 | c5 | c6 | c7 |
|---|---|---|---|---|---|---|---|
| Product | TBP cut Range C | Sp. Gr @15℃ | Vol % cut on RCO feed | Wt % cut on RCO feed | kg/h | m³/h@ 15℃ | MTPA |
| LVGO | 380-400 | 0.891 | 7.84 |  | 11435 | 12.834 | 93309 |
| HVGO | 400-530 | 0.9205 | 41.84 | 39.92 | 63050 | 68.495 | 514489 |
| Slop Distillate | 530-550 | 0.961 | 5.96 | 5.94 | 9382 | 9.763 | 76555 |
| Vac. Residue | 550+ | 1.02 | 44.36 | 46.90 | 74074 | 72.622 | 604447 |
| RCO feed | 380+ | 0.96478 | 100 | 100 | 157941 | 163.707 | 1288799 |

### equipment table

`CDU operating manual_p345_t3` page 345 — 14x6, 80/80 non-empty cells, 4 merged, 8 header cells

| c0 | c1 | c2 | c3 | c4 | c5 |
|---|---|---|---|---|---|
| Tag No | Description | Operating Temp (C) | Operating Temp (C) | Operating Pressure (Kg/cm2g) | Operating Pressure (Kg/cm2g) |
| Tag No | Description | PG | BH | PG | BH |
| 10-V-01 | Naphtha Caustic Wash | 40 | 40 | 6.5 | 6.5 |
| 10-V-02 | Naphtha Water Wash | 40 | 40 | 5.5 | 5.5 |
| 11-V-01 | Overhead Naphtha Accumulator | 44 | 44 | 2.0 | 2.0 |
| 11-V-02 | Crude Desalter | 129 | 136.5 | 14.0 | 14.0 |

### operating-limit table

`CDU operating manual_p32_t1` page 32 — 3x5, 14/14 non-empty cells, 0 merged, 4 header cells

| c0 | c1 | c2 | c3 | c4 |
|---|---|---|---|---|
|  | Minimum | Normal | Maximum | Mech. Design |
| Pressure (Kg/cm 2 ) | 3.5 | 4.0 | 5.0 | 6.5 |
| Temperature (°C) | Saturated | 150 | 170 | 190 |

### safety table

`CDU operating manual_p414_t1` page 414 — 6x3, 18/18 non-empty cells, 0 merged, 0 header cells

| c0 | c1 | c2 |
|---|---|---|
| 6 | Hazard from other routine / non-routine operations considere | Other activities (routine / non-routine) being carried out n |
| 7 | Equipment electrically isolated and tagged | Before issuing a permit, it shall be ensured that electrical |
| 8 | Running water hose / Portable extinguisher provided / Fire w | Running water hose and portable fire extinguisher are requir |
| 9 | Equipment blinded / disconnected / closed / isolated / wedge | Equipment, for which the work permit is being issued, should |
| 10 | Equipment properly drained / depressurized | Equipment under pressure should be depressurized after isola |
| 11 | Equipment properly steamed / purged | Purging of equipment (tanks, vessels, pipelines etc.) is don |

### procedure/checklist table

`CDU operating manual_p413_t1` page 413 — 6x3, 18/18 non-empty cells, 0 merged, 3 header cells

| c0 | c1 | c2 |
|---|---|---|
| Sr. No | Checklist points | Explanatory notes |
| 1 | Exact Location (Area / Unit / Equip no) | Exact location of the area or the unit in which the work is  |
| 2 | Description of work | Precise description of the work to be performed shall be wri |
| 3 | Equipment / Area inspected | Equipment or area where work is to be conducted should be in |
| 4 | Surrounding area checked / cleaned | Unsafe conditions for performance of work may arise from sur |
| 5 | Sewers, Manholes, Closed Blow Down (CBD) etc. and Hot Surfac | Flammable gases may be released from nearby sewers. Hot un-i |

## Windows

| # | Pages | Status | Seconds | Elements | Tables | Images | RSS MB | GPU peak MB | Warn | Err |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1-15 | success | 90.21 | 106 | 33 | 22 | 2152.3 | 856.4 | 0 | 0 |
| 1 | 16-30 | success | 105.58 | 195 | 37 | 16 | 2165.0 | 856.4 | 0 | 0 |
| 2 | 31-45 | success | 111.86 | 134 | 56 | 15 | 2001.2 | 856.4 | 0 | 0 |
| 3 | 46-60 | success | 53.89 | 196 | 20 | 18 | 2014.1 | 857.4 | 0 | 0 |
| 4 | 61-75 | success | 54.67 | 146 | 22 | 15 | 2036.7 | 857.4 | 0 | 0 |
| 5 | 76-90 | success | 49.41 | 241 | 16 | 15 | 2043.9 | 857.4 | 0 | 0 |
| 6 | 91-105 | success | 36.05 | 207 | 17 | 16 | 2051.2 | 856.4 | 0 | 0 |
| 7 | 106-120 | success | 36.91 | 211 | 17 | 15 | 2058.1 | 857.4 | 0 | 0 |
| 8 | 121-135 | success | 53.87 | 150 | 15 | 15 | 2062.2 | 856.4 | 0 | 0 |
| 9 | 136-150 | success | 93.58 | 170 | 22 | 18 | 2076.8 | 857.4 | 0 | 0 |
| 10 | 151-165 | success | 73.32 | 195 | 17 | 18 | 2086.0 | 856.4 | 0 | 0 |
| 11 | 166-180 | success | 71.17 | 217 | 15 | 17 | 1158.1 | 856.4 | 0 | 0 |
| 12 | 181-195 | success | 42.0 | 258 | 15 | 15 | 1169.7 | 856.4 | 0 | 0 |
| 13 | 196-210 | success | 73.16 | 167 | 17 | 15 | 1052.4 | 857.4 | 0 | 0 |
| 14 | 211-225 | success | 65.46 | 263 | 15 | 15 | 947.6 | 857.4 | 0 | 0 |
| 15 | 226-240 | success | 46.61 | 296 | 6 | 16 | 951.6 | 856.4 | 0 | 0 |
| 16 | 241-255 | success | 45.49 | 194 | 16 | 15 | 861.2 | 856.4 | 0 | 0 |
| 17 | 256-270 | success | 62.55 | 298 | 16 | 16 | 869.0 | 856.4 | 0 | 0 |
| 18 | 271-285 | success | 33.93 | 217 | 13 | 15 | 877.6 | 857.4 | 0 | 0 |
| 19 | 286-300 | success | 47.57 | 319 | 15 | 15 | 886.8 | 856.4 | 0 | 0 |
| 20 | 301-315 | success | 43.24 | 222 | 16 | 19 | 891.1 | 857.4 | 0 | 0 |
| 21 | 316-330 | success | 57.45 | 201 | 15 | 26 | 898.3 | 856.4 | 0 | 0 |
| 22 | 331-345 | success | 89.79 | 197 | 26 | 15 | 868.8 | 856.4 | 0 | 0 |
| 23 | 346-360 | success | 91.59 | 180 | 33 | 15 | 1441.6 | 857.4 | 0 | 0 |
| 24 | 361-375 | success | 97.18 | 356 | 19 | 15 | 1448.2 | 856.4 | 0 | 0 |
| 25 | 376-390 | success | 101.7 | 187 | 15 | 15 | 1435.7 | 856.4 | 0 | 0 |
| 26 | 391-405 | success | 65.72 | 257 | 15 | 15 | 1445.1 | 857.4 | 0 | 0 |
| 27 | 406-420 | success | 78.97 | 129 | 32 | 15 | 1452.4 | 856.4 | 0 | 0 |
| 28 | 421-435 | success | 70.28 | 277 | 15 | 16 | 1458.6 | 857.4 | 0 | 0 |
| 29 | 436-450 | success | 45.13 | 354 | 15 | 15 | 1456.1 | 857.4 | 0 | 0 |
| 30 | 451-465 | success | 36.02 | 296 | 15 | 15 | 1466.6 | 856.4 | 0 | 0 |
| 31 | 466-480 | success | 50.52 | 272 | 15 | 15 | 1475.6 | 856.4 | 0 | 0 |
| 32 | 481-495 | success | 83.48 | 150 | 27 | 15 | 1483.3 | 857.4 | 0 | 0 |
| 33 | 496-510 | success | 105.13 | 57 | 34 | 15 | 1502.0 | 856.4 | 0 | 0 |
| 34 | 511-525 | success | 114.22 | 92 | 45 | 15 | 1509.7 | 856.4 | 0 | 0 |
| 35 | 526-540 | success | 83.99 | 117 | 37 | 15 | 1511.4 | 1413.7 | 0 | 0 |
| 36 | 541-555 | success | 89.17 | 184 | 25 | 15 | 1524.4 | 857.4 | 0 | 0 |
| 37 | 556-562 | success | 65.3 | 24 | 15 | 7 | 1448.5 | 856.4 | 0 | 0 |

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
  "ram_available_gb_at_start": 4.15,
  "ocr_engine": "rapidocr",
  "ocr_backend": "onnxruntime(AzureExecutionProvider,CPUExecutionProvider)",
  "table_structure_mode": "accurate",
  "table_cell_matching": true,
  "formula_enrichment": false,
  "page_window_size": 15
}
```