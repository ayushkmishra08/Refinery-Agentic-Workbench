"""Inspect the parsed document to understand the structure."""
import json, sys
sys.path.insert(0, ".")

with open("data/parsed/CDU operating manual_parsed.json", "r", encoding="utf-8") as f:
    d = json.load(f)

print(f"Pages: {d['total_pages']}")
print(f"Tables: {len(d.get('tables', []))}")

pages_with_elements = [p for p in d['pages'] if p['elements']]
print(f"Pages with elements: {len(pages_with_elements)}")

# Show element type distribution
from collections import Counter
type_counts = Counter()
for p in d['pages']:
    for e in p['elements']:
        type_counts[e['element_type']] += 1
print(f"\nElement type distribution:")
for t, c in type_counts.most_common():
    print(f"  {t}: {c}")

# Show sample elements from first few pages
print("\nSample elements from first 5 pages with content:")
for p in d['pages'][:5]:
    if p['elements']:
        print(f"\n  Page {p['page_number']}:")
        for e in p['elements'][:3]:
            content = e.get('content', '')[:120]
            print(f"    [{e['element_type']}] {content}")
