#!/usr/bin/env python3
"""Sanity-check the generated LedgerPilot dataset.

1. Every PDF opens with pdfplumber and its text contains the ground-truth invoice number,
   vendor, PO ref (or no PO label when missing), dates and total as printed.
2. A tiny rule-based oracle re-implements docs/accounting_policy.md on the ground-truth
   fields + CSVs and must reproduce every `expected` outcome (guards against dataset bugs).
Exit code 0 = all good.
"""
import csv
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate as g  # noqa: E402

DATA = Path(__file__).resolve().parent
D = Decimal


def load_csv(name):
    with open(DATA / name, newline="") as fh:
        return list(csv.DictReader(fh))


def check_pdf(rec):
    ex, tmpl = rec["extracted"], rec["template"]
    country = "IN" if ex["currency"] == "INR" else "US"
    with pdfplumber.open(DATA / "invoices" / rec["file"]) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    errs = []
    must = [ex["invoice_number"], ex["vendor_name"], ex["vendor_tax_id"],
            g.fmt_date(date.fromisoformat(ex["invoice_date"]), tmpl, country),
            g.fmt_date(date.fromisoformat(ex["due_date"]), tmpl, country),
            g.amt(ex["currency"], D(str(ex["total"])), tmpl)]
    must += [li["sku"] for li in ex["line_items"]]
    if ex["po_number"]:
        must.append(ex["po_number"])
    for m in must:
        # the 'minimal' template prints the vendor name in upper case
        if m not in text and not (m == ex["vendor_name"] and m.upper() in text):
            errs.append(f"missing '{m}'")
    if not ex["po_number"] and "PO-26-" in text:
        errs.append("PO reference printed although po_number is null")
    return errs, len(text)


def oracle(rec, vendors, pos, grns, seen):
    """Return (outcome, exception_type, tier) per the sample AP policy."""
    ex = rec["extracted"]
    cur = ex["currency"]
    vend = next((v for v in vendors if v["name"] == ex["vendor_name"] and v["tax_id"] == ex["vendor_tax_id"]), None)
    if not vend:
        return "exception", "unknown_vendor", None
    key = (vend["vendor_id"], ex["invoice_number"])
    if key in seen:
        return "exception", "duplicate_invoice", None
    seen.add(key)
    if not ex["po_number"]:
        return "exception", "missing_po", None
    po_lines = [p for p in pos if p["po_number"] == ex["po_number"] and p["vendor_id"] == vend["vendor_id"]]
    if not po_lines:
        return "exception", "missing_po", None
    sub = sum(D(str(li["amount"])) for li in ex["line_items"])
    tax = sum(D(str(t["amount"])) for t in ex["tax_lines"])
    if abs(sub - D(str(ex["subtotal"]))) > g.ARITH_TOLERANCE or abs(
            D(str(ex["total"])) - (D(str(ex["subtotal"])) + tax)) > g.ARITH_TOLERANCE:
        return "exception", "total_mismatch", None
    exp_tax = g.q(D(str(ex["subtotal"])) * D(vend["standard_tax_rate_pct"]) / 100)
    if abs(tax - exp_tax) > g.ARITH_TOLERANCE:
        return "exception", "wrong_tax", None
    for li in ex["line_items"]:
        pl = next((p for p in po_lines if p["sku"] == li["sku"]), None)
        gr = next((r for r in grns if r["po_number"] == ex["po_number"] and r["sku"] == li["sku"]), None)
        if not pl or not gr:
            return "exception", "price_mismatch", None
        pct = (D(str(li["unit_price"])) - D(pl["unit_price"])) / D(pl["unit_price"]) * 100
        if abs(pct) > g.PRICE_TOLERANCE_PCT or li["quantity"] > int(gr["received_qty"]) or \
                li["quantity"] > int(pl["quantity"]):
            return "exception", "price_mismatch", None
    return "approve", None, g.approval_tier(cur, D(str(ex["total"])))


def main():
    gt = json.load(open(DATA / "ground_truth.json"))
    recs = gt["invoices"]
    vendors, pos, grns = load_csv("vendors.csv"), load_csv("purchase_orders.csv"), load_csv("goods_receipts.csv")
    failures = 0
    pdfs = sorted((DATA / "invoices").glob("*.pdf"))
    print(f"PDFs on disk: {len(pdfs)} | ground-truth records: {len(recs)}")
    if {p.name for p in pdfs} != set(recs):
        print("FAIL: PDF set != ground-truth set")
        failures += 1
    seen = set()
    for name in sorted(recs):
        rec = recs[name]
        errs, n = check_pdf(rec)
        out, et, tier = oracle(rec, vendors, pos, grns, seen)
        e = rec["expected"]
        if (out, et, tier) != (e["outcome"], e["exception_type"], e["approval_tier"]):
            errs.append(f"oracle says {(out, et, tier)} but expected {(e['outcome'], e['exception_type'], e['approval_tier'])}")
        if errs:
            failures += 1
            print(f"FAIL {name}: {errs}")
    ok = len(recs) - failures
    print(f"PDF text + policy-oracle checks passed for {ok}/{len(recs)} invoices")
    print("meta:", json.dumps(gt["meta"]["exception_counts"]), "| tiers:", json.dumps(gt["meta"]["approval_tier_counts"]))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
