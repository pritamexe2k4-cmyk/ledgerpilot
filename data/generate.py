#!/usr/bin/env python3
"""LedgerPilot synthetic data generator (SAMPLE / FICTIONAL DATA ONLY).

Produces, deterministically for a given seed:
  data/invoices/inv_001.pdf ... inv_040.pdf   (4 visual templates, IN + US vendors)
  data/purchase_orders.csv                     (one row per PO line)
  data/goods_receipts.csv                      (one row per received PO line, for 3-way match)
  data/vendors.csv                             (approved vendor master)
  data/ground_truth.json                       (printed fields + expected AP outcome)

~20% of invoices carry exactly one injected exception:
  total_mismatch, wrong_tax, missing_po, duplicate_invoice, price_mismatch, unknown_vendor

Usage:  python data/generate.py [--seed 42] [--count 40] [--out data]
All company names, tax IDs and addresses are invented.
"""
import argparse
import csv
import json
import math
import random
import shutil
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

# --------------------------------------------------------------------------- #
# Static master data (fictional)
# --------------------------------------------------------------------------- #
BUYERS = {
    "IN": dict(name="Meridian Global Services Pvt Ltd",
               addr1="Plot 14, Cyber Towers Road, HITEC City",
               addr2="Hyderabad, Telangana 500081", state="Telangana", state_code="36",
               tax_label="GSTIN", tax_id="36AABCM4821K1ZQ"),
    "US": dict(name="Meridian Global Services Inc.",
               addr1="2100 Congress Avenue, Suite 400",
               addr2="Austin, TX 78701", state="Texas", state_code="TX",
               tax_label="EIN", tax_id="84-3920417"),
}

# template: classic | modern | minimal | compact
VENDORS = [
    dict(vendor_id="V001", name="Sri Lakshmi Office Supplies", country="IN", state="Telangana", state_code="36",
         addr1="Shop 12, Abids Road", addr2="Hyderabad, Telangana 500001", currency="INR", tax_rate=18,
         terms_days=30, template="classic", num_fmt="SLO/26-27/{n:04d}", category="Stationery & office supplies"),
    dict(vendor_id="V002", name="Bharat IT Solutions Pvt Ltd", country="IN", state="Karnataka", state_code="29",
         addr1="4th Floor, Embassy Tech Square, Outer Ring Road", addr2="Bengaluru, Karnataka 560103",
         currency="INR", tax_rate=18, terms_days=45, template="modern", num_fmt="BIT-2026-{n:04d}",
         category="IT hardware & AMC"),
    dict(vendor_id="V003", name="Deccan Logistics Pvt Ltd", country="IN", state="Telangana", state_code="36",
         addr1="Survey 88, Medchal Road, Kompally", addr2="Hyderabad, Telangana 500014", currency="INR",
         tax_rate=12, terms_days=15, template="minimal", num_fmt="DL/{n:05d}", category="Freight & logistics"),
    dict(vendor_id="V004", name="Kaveri Facility Services LLP", country="IN", state="Tamil Nadu", state_code="33",
         addr1="No. 27, Anna Salai, Teynampet", addr2="Chennai, Tamil Nadu 600018", currency="INR", tax_rate=18,
         terms_days=30, template="compact", num_fmt="KFS{n:06d}", category="Facility services"),
    dict(vendor_id="V005", name="Nova Print & Packaging", country="IN", state="Maharashtra", state_code="27",
         addr1="Gala 6, Bhosari MIDC", addr2="Pune, Maharashtra 411026", currency="INR", tax_rate=12,
         terms_days=30, template="classic", num_fmt="NP-{n:04d}", category="Printing & packaging"),
    dict(vendor_id="V006", name="Charminar Catering Co.", country="IN", state="Telangana", state_code="36",
         addr1="8-2-120, Road No. 2, Banjara Hills", addr2="Hyderabad, Telangana 500034", currency="INR",
         tax_rate=5, terms_days=15, template="minimal", num_fmt="CC/{n:03d}", category="Catering"),
    dict(vendor_id="V101", name="Lone Star Office Furniture LLC", country="US", state="Texas", state_code="TX",
         addr1="5500 N Lamar Blvd", addr2="Austin, TX 78751", currency="USD", tax_rate=8.25,
         terms_days=30, template="classic", num_fmt="LSOF-{n:05d}", category="Office furniture"),
    dict(vendor_id="V102", name="Pacific Cloud Software Inc.", country="US", state="California", state_code="CA",
         addr1="1800 Technology Drive", addr2="San Jose, CA 95110", currency="USD", tax_rate=7.25,
         terms_days=45, template="modern", num_fmt="PCS-INV-{n:06d}", category="Software & support"),
    dict(vendor_id="V103", name="Midwest Freight Systems Inc.", country="US", state="Illinois", state_code="IL",
         addr1="400 W Ontario Street", addr2="Chicago, IL 60654", currency="USD", tax_rate=6.25,
         terms_days=30, template="compact", num_fmt="MFS{n:07d}", category="Freight"),
    dict(vendor_id="V104", name="Hudson Creative Agency LLC", country="US", state="New York", state_code="NY",
         addr1="251 Park Avenue South, Floor 9", addr2="New York, NY 10010", currency="USD", tax_rate=8.875,
         terms_days=30, template="modern", num_fmt="HCA-{n:04d}", category="Marketing & creative"),
]

# Vendors NOT in the vendor master (used for the unknown_vendor exception)
UNKNOWN_VENDORS = [
    dict(vendor_id=None, name="Zenith Trading Co.", country="IN", state="Telangana", state_code="36",
         addr1="Plot 5, Industrial Estate, Balanagar", addr2="Hyderabad, Telangana 500037", currency="INR",
         tax_rate=18, terms_days=15, template="minimal", num_fmt="ZT/{n:04d}", category="Unknown"),
    dict(vendor_id=None, name="QuickSupply Partners LLC", country="US", state="Texas", state_code="TX",
         addr1="9100 Southwest Fwy", addr2="Houston, TX 77074", currency="USD", tax_rate=8.25,
         terms_days=30, template="compact", num_fmt="QSP{n:06d}", category="Unknown"),
]

# (sku, description, uom, unit_price, hsn_sac)
CATALOG = {
    "V001": [("STN-A4-500", "A4 Copier Paper 75 GSM (500 sheets)", "Ream", 285, "4802"),
             ("STN-PEN-50", "Gel Pens, Blue (box of 50)", "Box", 420, "9608"),
             ("STN-FILE-FS", "Box File, Foolscap", "Nos", 95, "4820"),
             ("STN-STPL-HD", "Heavy Duty Stapler", "Nos", 650, "8472"),
             ("STN-TNR-12A", "Toner Cartridge 12A Compatible", "Nos", 2850, "8443")],
    "V002": [("IT-LAP-14", "14in Business Laptop, i5 / 16GB / 512GB", "Nos", 58500, "8471"),
             ("IT-MON-24", "24in IPS Monitor", "Nos", 9800, "8528"),
             ("IT-KBM-W", "Wireless Keyboard & Mouse Combo", "Nos", 1450, "8471"),
             ("IT-DOCK-C", "USB-C Docking Station", "Nos", 7200, "8471"),
             ("IT-AMC-DEV", "Annual Maintenance Contract (per device)", "Nos", 2400, "998713")],
    "V003": [("LOG-LOCAL", "Local Courier Run (per trip)", "Trip", 1800, "996812"),
             ("LOG-LTL", "Intercity LTL Freight (per consignment)", "Nos", 14500, "996511"),
             ("LOG-WH-PAL", "Warehousing, per pallet per month", "Pallet", 650, "996729"),
             ("LOG-PACK-K", "Packing Material Kit", "Kit", 380, "3923")],
    "V004": [("FAC-HK-MO", "Housekeeping Services (per month)", "Month", 42000, "998533"),
             ("FAC-PEST-Q", "Pest Control Service (quarterly)", "Visit", 9500, "998531"),
             ("FAC-AC-SVC", "AC Servicing (per unit)", "Nos", 1150, "998719"),
             ("FAC-SEC-MO", "Security Guard (per guard-month)", "Month", 28500, "998525")],
    "V005": [("PRT-BRO-1K", "Brochures A4 Tri-fold (per 1000)", "Lot", 6800, "4911"),
             ("PRT-BC-500", "Business Cards (box of 500)", "Box", 650, "4911"),
             ("PRT-BAN-63", "Flex Banner 6x3 ft", "Nos", 1350, "3921"),
             ("PRT-FLY-1K", "Flyers A5 (per 1000)", "Lot", 2400, "4911")],
    "V006": [("CAT-LUNCH", "Working Lunch (per head)", "Head", 280, "996331"),
             ("CAT-TEA", "Tea / Coffee & Snacks (per head)", "Head", 95, "996331"),
             ("CAT-EVT-50", "Event Catering Package (50 pax)", "Pkg", 21500, "996331"),
             ("CAT-WATER", "Packaged Water 20L Can", "Nos", 70, "2201")],
    "V101": [("FUR-DESK-60", "Height-Adjustable Desk 60in", "Each", 489.00, ""),
             ("FUR-CHAIR-E", "Ergonomic Task Chair", "Each", 245.00, ""),
             ("FUR-CAB-4D", "4-Drawer File Cabinet", "Each", 189.00, ""),
             ("FUR-LAMP-L", "LED Desk Lamp", "Each", 34.50, "")],
    "V102": [("SW-SEAT-AN", "Analytics Platform Seat License (annual)", "Seat", 420.00, ""),
             ("SW-SUP-PRM", "Premium Support Plan (annual)", "Plan", 2400.00, ""),
             ("SW-ONB-DAY", "Onboarding Workshop (per day)", "Day", 1500.00, ""),
             ("SW-API-1M", "API Credit Pack (1M calls)", "Pack", 250.00, "")],
    "V103": [("FRT-PAL-CD", "Pallet Freight, Chicago to Dallas", "Pallet", 385.00, ""),
             ("FRT-EXP-PK", "Expedited Parcel (per package)", "Pkg", 62.50, ""),
             ("FRT-CRATE", "Custom Crating (per crate)", "Crate", 140.00, ""),
             ("FRT-INS-SH", "Cargo Insurance (per shipment)", "Shipment", 45.00, "")],
    "V104": [("MKT-CAMP-D", "Campaign Design Package", "Pkg", 3200.00, ""),
             ("MKT-SOC-MO", "Social Media Management (per month)", "Month", 1800.00, ""),
             ("MKT-VID-ED", "Product Video Edit (per video)", "Video", 950.00, ""),
             ("MKT-PHOTO-H", "Photo Shoot (half-day)", "Shoot", 1200.00, "")],
}
UNKNOWN_CATALOG = {
    "IN": [("ZT-GEN-01", "General Merchandise Supply Lot", "Lot", 12500, "9999"),
           ("ZT-GEN-02", "Consulting Services (per day)", "Day", 8000, "998311")],
    "US": [("QS-BULK-01", "Bulk Office Consumables Lot", "Lot", 640.00, ""),
           ("QS-EQP-02", "Equipment Rental (per week)", "Week", 310.00, "")],
}

EXCEPTION_TYPES = ["total_mismatch", "total_mismatch", "wrong_tax", "missing_po",
                   "duplicate_invoice", "price_mismatch", "price_mismatch", "unknown_vendor"]

# AP policy constants (must stay in sync with docs/accounting_policy.md)
PRICE_TOLERANCE_PCT = Decimal("2.0")
ARITH_TOLERANCE = Decimal("0.05")
APPROVAL_TIERS = {  # (auto_max, manager_max); above manager_max -> director
    "INR": (Decimal("50000"), Decimal("500000")),
    "USD": (Decimal("1000"), Decimal("10000")),
}

TWO = Decimal("0.01")


def q(x):
    return Decimal(x).quantize(TWO, rounding=ROUND_HALF_UP)


def D(x):
    return Decimal(str(x))


def f(d):
    """Decimal -> float for JSON (2dp)."""
    return float(q(d))


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def indian_group(d):
    s = f"{q(d):.2f}"
    neg = s.startswith("-")
    s = s.lstrip("-")
    ip, fp = s.split(".")
    if len(ip) > 3:
        head, tail = ip[:-3], ip[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        ip = ",".join(parts + [tail])
    return ("-" if neg else "") + ip + "." + fp


def amt(cur, d, tmpl="classic"):
    """Currency string as printed on the invoice (also used by verify.py)."""
    if cur == "INR":
        return ("INR " if tmpl in ("minimal", "compact", "modern") else "") + indian_group(d)
    return ("USD " if tmpl == "compact" else "$") + f"{q(d):,.2f}"


def num(cur, d):
    return indian_group(d) if cur == "INR" else f"{q(d):,.2f}"


def fmt_date(d, template, country):
    if template == "classic":
        return d.strftime("%d-%b-%Y")
    if template == "modern":
        return d.strftime("%d %B %Y").lstrip("0")
    if template == "minimal":
        return d.strftime("%Y-%m-%d")
    return d.strftime("%d/%m/%Y" if country == "IN" else "%m/%d/%Y")


def rate_label(r):
    s = f"{D(r):f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


# --------------------------------------------------------------------------- #
# Business-logic generation
# --------------------------------------------------------------------------- #
def build_tax_lines(vendor, subtotal, rate):
    rate = D(rate)
    if vendor["country"] == "IN":
        if vendor["state_code"] == BUYERS["IN"]["state_code"]:
            half = rate / 2
            a = q(subtotal * half / 100)
            return [dict(name=f"CGST @ {rate_label(half)}%", rate=f(half), amount=f(a)),
                    dict(name=f"SGST @ {rate_label(half)}%", rate=f(half), amount=f(a))]
        return [dict(name=f"IGST @ {rate_label(rate)}%", rate=f(rate), amount=f(q(subtotal * rate / 100)))]
    return [dict(name=f"Sales Tax ({rate_label(rate)}%)", rate=float(rate), amount=f(q(subtotal * rate / 100)))]


def approval_tier(cur, total):
    auto_max, mgr_max = APPROVAL_TIERS[cur]
    if total <= auto_max:
        return "auto"
    if total <= mgr_max:
        return "manager"
    return "director"


def pick_lines(rng, vendor_id, cur, n_lines):
    items = rng.sample(CATALOG[vendor_id] if vendor_id in CATALOG else UNKNOWN_CATALOG[vendor_id], k=n_lines)
    lo, hi = (4000, 900000) if cur == "INR" else (100, 25000)
    target = math.exp(rng.uniform(math.log(lo), math.log(hi)))  # log-uniform -> spread across approval tiers
    out = []
    for sku, desc, uom, price, hsn in items:
        share = target / n_lines * rng.uniform(0.5, 1.5)
        qty = max(1, min(200, round(share / float(price))))
        out.append(dict(sku=sku, description=desc, uom=uom, hsn_sac=hsn, quantity=qty, po_unit_price=D(price)))
    return out


def make_plan(rng, count):
    """Return per-invoice exception type (None for clean), exceptions never in first 8 slots."""
    plan = [None] * count
    n_exc = len(EXCEPTION_TYPES)
    slots = rng.sample(range(8, count), n_exc)
    types = EXCEPTION_TYPES[:]
    rng.shuffle(types)
    for s, t in zip(sorted(slots), types):
        plan[s] = t
    return plan


def generate_dataset(seed, count):
    rng = random.Random(seed)

    # vendor tax IDs (seeded, fictional)
    def gstin(state_code):
        pan = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVW") for _ in range(5)) + \
              "".join(rng.choice("0123456789") for _ in range(4)) + rng.choice("ABCDEFGHJKLMNPRSTUVW")
        return f"{state_code}{pan}1Z{rng.choice('0123456789ABCDEFGHJKLMNPQRSTUVW')}"

    def ein():
        return f"{rng.randint(10, 98)}-{rng.randint(1000000, 9999999)}"

    for v in VENDORS + UNKNOWN_VENDORS:
        if v["country"] == "IN":
            v["tax_label"], v["tax_id"] = "GSTIN", gstin(v["state_code"])
        else:
            v["tax_label"], v["tax_id"] = "EIN", ein()
    counters = {v["name"]: rng.randint(40, 400) for v in VENDORS + UNKNOWN_VENDORS}

    plan = make_plan(rng, count)
    vendor_slots = VENDORS * math.ceil(count / len(VENDORS))
    vendor_slots = vendor_slots[:count]
    rng.shuffle(vendor_slots)

    day_offsets = sorted(rng.randint(0, 55) for _ in range(count))
    base = date(2026, 9, 1)
    partial_idx = set()
    clean_candidates = [i for i in range(count) if plan[i] is None]
    partial_idx = set(rng.sample([i for i in clean_candidates if i > 3], 2))
    tolerance_idx = set(rng.sample([i for i in clean_candidates if i not in partial_idx and i > 3], 2))

    invoices, pos, grns = [], [], []
    clean_done = []  # indices of clean invoices already generated (dup sources)
    po_seq = 1000
    grn_seq = 5000

    for i in range(count):
        exc = plan[i]
        inv_date = base + timedelta(days=day_offsets[i])
        fname = f"inv_{i + 1:03d}.pdf"

        # ---------------- duplicate: re-submission of an earlier clean invoice ----------------
        if exc == "duplicate_invoice":
            src_candidates = [j for j in clean_done if invoices[j]["_dup_ok"]]
            src = invoices[rng.choice(src_candidates)]
            inv = dict(src)
            inv["lines"] = [dict(l) for l in src["lines"]]
            inv["taxes"] = [dict(t) for t in src["taxes"]]
            inv["invoice_date"] = max(inv_date, src["invoice_date"] + timedelta(days=rng.randint(3, 9)))
            inv["due_date"] = inv["invoice_date"] + timedelta(days=src["vendor"]["terms_days"])
            inv["file"] = fname
            inv["exception"] = "duplicate_invoice"
            inv["reason"] = (f"Invoice number {src['invoice_number']} from {src['vendor']['name']} was already "
                             f"submitted as {src['file']} dated {src['invoice_date'].isoformat()}.")
            inv["duplicate_of"] = src["file"]
            inv["_dup_ok"] = False
            inv["tier"] = None
            invoices.append(inv)
            continue

        # ---------------- vendor ----------------
        if exc == "unknown_vendor":
            vendor = rng.choice(UNKNOWN_VENDORS)
        else:
            vendor = vendor_slots[i]
        cur = vendor["currency"]
        counters[vendor["name"]] += rng.randint(1, 9)
        inv_no = vendor["num_fmt"].format(n=counters[vendor["name"]])

        # ---------------- lines & PO ----------------
        n_lines = rng.choice([1, 2, 2, 3, 3, 4])
        vid = vendor["vendor_id"] if vendor["vendor_id"] else vendor["country"]
        lines = pick_lines(rng, vid, cur, min(n_lines, 2 if exc == "unknown_vendor" else n_lines))

        po_number = None
        if exc == "unknown_vendor":
            po_number = f"PO-26-{rng.randint(7000, 7999)}"  # reference to a PO that does not exist
        elif exc != "missing_po":
            po_seq += rng.randint(1, 6)
            po_number = f"PO-26-{po_seq}"
            po_date = inv_date - timedelta(days=rng.randint(10, 40))
            grn_date = inv_date - timedelta(days=rng.randint(1, 7))
            grn_seq += rng.randint(1, 4)
            for ln_no, ln in enumerate(lines, 1):
                po_qty = ln["quantity"] * 2 if (i in partial_idx and ln_no == 1) else ln["quantity"]
                pos.append(dict(po_number=po_number, vendor_id=vendor["vendor_id"], po_date=po_date.isoformat(),
                                currency=cur, line_no=ln_no, sku=ln["sku"], description=ln["description"],
                                uom=ln["uom"], quantity=po_qty, unit_price=f"{q(ln['po_unit_price']):.2f}"))
                grns.append(dict(grn_number=f"GRN-{grn_seq}", po_number=po_number, line_no=ln_no,
                                 sku=ln["sku"], received_qty=ln["quantity"], received_date=grn_date.isoformat()))

        # ---------------- invoice prices ----------------
        notes = []
        for ln_no, ln in enumerate(lines, 1):
            ln["unit_price"] = ln["po_unit_price"]
        price_exc_line = None
        if exc == "price_mismatch":
            ln = rng.choice(lines)
            bump = D(rng.choice([6, 8, 10, 12, 15])) / 100
            ln["unit_price"] = q(ln["po_unit_price"] * (1 + bump))
            price_exc_line = (ln, bump)
        elif i in tolerance_idx:
            ln = lines[0]
            ln["unit_price"] = q(ln["po_unit_price"] * D("1.01"))
            notes.append(f"{ln['sku']} billed at +1.0% vs PO (within {PRICE_TOLERANCE_PCT}% tolerance).")
        if i in partial_idx:
            notes.append(f"{lines[0]['sku']}: partial delivery, invoiced {lines[0]['quantity']} of "
                         f"{lines[0]['quantity'] * 2} ordered; GRN covers invoiced quantity.")
        for ln in lines:
            ln["amount"] = q(ln["quantity"] * ln["unit_price"])
        subtotal = sum((ln["amount"] for ln in lines), Decimal("0"))

        # ---------------- tax & total ----------------
        true_rate = D(vendor["tax_rate"])
        used_rate = true_rate
        if exc == "wrong_tax":
            if cur == "INR":
                used_rate = D(rng.choice([r for r in (5, 12, 18, 28) if D(r) != true_rate]))
            else:
                used_rate = D(rng.choice([r for r in ("0", "5.5", "6.25", "8.25", "9.5", "10") if D(r) != true_rate]))
        taxes = build_tax_lines(vendor, subtotal, used_rate)
        tax_total = sum((D(t["amount"]) for t in taxes), Decimal("0"))
        total = subtotal + tax_total
        reason = ""
        if exc == "wrong_tax":
            exp_tax = sum((D(t["amount"]) for t in build_tax_lines(vendor, subtotal, true_rate)), Decimal("0"))
            reason = (f"Tax charged at {rate_label(used_rate)}% ({q(tax_total)}) but {vendor['name']} "
                      f"category rate is {rate_label(true_rate)}% (expected tax {q(exp_tax)}).")
        if exc == "total_mismatch":
            delta = q(total * D(rng.uniform(0.03, 0.12)))
            delta = -delta if rng.random() < 0.4 else delta
            stated = q(total + delta)
            reason = (f"Stated total {q(stated)} differs from subtotal + tax {q(total)} by {q(delta)}.")
            total = stated
        if exc == "price_mismatch":
            ln, bump = price_exc_line
            reason = (f"Line {ln['sku']} unit price {q(ln['unit_price'])} vs PO {q(ln['po_unit_price'])} "
                      f"(+{bump * 100:.0f}%, tolerance {PRICE_TOLERANCE_PCT}%).")
        if exc == "missing_po":
            reason = "Invoice carries no PO reference; a valid PO is mandatory for payment."
        if exc == "unknown_vendor":
            reason = (f"Vendor '{vendor['name']}' ({vendor['tax_id']}) is not in the approved vendor master; "
                      f"referenced PO {po_number} does not exist.")

        inv = dict(file=fname, vendor=vendor, buyer=BUYERS[vendor["country"]], invoice_number=inv_no,
                   invoice_date=inv_date, due_date=inv_date + timedelta(days=vendor["terms_days"]), currency=cur,
                   po_number=po_number, lines=lines, subtotal=subtotal, taxes=taxes, tax_total=tax_total,
                   total=total, exception=exc, reason=reason, notes=notes,
                   tier=None if exc else approval_tier(cur, total),
                   _dup_ok=(exc is None), duplicate_of=None, tolerance_case=(i in tolerance_idx),
                   partial_case=(i in partial_idx))
        invoices.append(inv)
        if exc is None:
            clean_done.append(i)
    return invoices, pos, grns


# --------------------------------------------------------------------------- #
# PDF rendering (4 templates)
# --------------------------------------------------------------------------- #
GREY = colors.Color(0.35, 0.35, 0.35)
LIGHT = colors.Color(0.93, 0.93, 0.93)
PALETTE = {"V002": colors.Color(0.10, 0.30, 0.55), "V102": colors.Color(0.45, 0.15, 0.55),
           "V104": colors.Color(0.80, 0.30, 0.10)}


def T(c, x, y, s, font="Helvetica", size=9, align="l", color=colors.black):
    c.setFillColor(color)
    c.setFont(font, size)
    s = str(s)
    if align == "r":
        c.drawRightString(x, y, s)
    elif align == "c":
        c.drawCentredString(x, y, s)
    else:
        c.drawString(x, y, s)


def footer(c, w, inv, y=30):
    T(c, w / 2, y, "SAMPLE INVOICE - synthetic data generated for the LedgerPilot demo. Not a real transaction.",
      "Helvetica-Oblique", 6.5, "c", GREY)


def bank_lines(inv):
    v = inv["vendor"]
    if v["country"] == "IN":
        return [f"Bank: Sample Bank Ltd | A/c No: {inv['vendor']['tax_id'][2:12]}00 | IFSC: SMPL0001234"]
    return [f"Remit to: {v['name']} | ACH Routing 000000000 | Acct ****{inv['vendor']['tax_id'][-4:]}"]


def wrap(text, font, size, width):
    return simpleSplit(text, font, size, width)


def table_cols(w, has_hsn, margin=40):
    x0, x1 = margin, w - margin
    if has_hsn:
        edges = [x0, x0 + 20, x0 + 95, x1 - 270 + 10, x1 - 185 + 10, x1 - 145 + 10, x1 - 75, x1]
        # idx, code, desc, hsn, qty, rate, amount
        return dict(idx=(edges[0], edges[1]), code=(edges[1], edges[2]), desc=(edges[2], edges[3]),
                    hsn=(edges[3], edges[4]), qty=(edges[4], edges[5]), rate=(edges[5], edges[6]),
                    amount=(edges[6], edges[7]))
    return dict(idx=(x0, x0 + 20), code=(x0 + 20, x0 + 105), desc=(x0 + 105, x1 - 190),
                qty=(x1 - 190, x1 - 150), rate=(x1 - 150, x1 - 75), amount=(x1 - 75, x1))


def draw_table(c, w, y, inv, style):
    """Generic line-item table. style: grid | banded. Returns y below table."""
    cur, tmpl = inv["currency"], inv["vendor"]["template"]
    has_hsn = inv["vendor"]["country"] == "IN"
    cols = table_cols(w, has_hsn)
    heads = dict(idx="#", code="Item Code", desc="Description", hsn="HSN/SAC", qty="Qty", rate="Rate", amount="Amount")
    if style == "banded":
        heads["code"], heads["desc"], heads["amount"] = "SKU", "Item", "Line Total"
    x0, x1 = cols["idx"][0], cols["amount"][1]
    hh = 20
    accent = PALETTE.get(inv["vendor"]["vendor_id"], colors.Color(0.2, 0.2, 0.2))
    if style == "grid":
        c.setFillColor(LIGHT)
        c.rect(x0, y - hh, x1 - x0, hh, fill=1, stroke=0)
    else:
        c.setFillColor(accent)
        c.rect(x0, y - hh, x1 - x0, hh, fill=1, stroke=0)
    hcolor = colors.black if style == "grid" else colors.white
    for k, (a, b) in cols.items():
        if k in ("qty", "rate", "amount"):
            T(c, b - 4, y - 13, heads[k], "Helvetica-Bold", 8, "r", hcolor)
        else:
            T(c, a + 4, y - 13, heads[k], "Helvetica-Bold", 8, "l", hcolor)
    top = y
    y -= hh
    for n, ln in enumerate(inv["lines"], 1):
        dl = wrap(ln["description"], "Helvetica", 8.5, cols["desc"][1] - cols["desc"][0] - 8)
        rh = 12 * len(dl) + 8
        if style == "banded" and n % 2 == 0:
            c.setFillColor(LIGHT)
            c.rect(x0, y - rh, x1 - x0, rh, fill=1, stroke=0)
        ty = y - 14
        T(c, cols["idx"][0] + 4, ty, n, size=8.5)
        T(c, cols["code"][0] + 4, ty, ln["sku"], size=8.5)
        for k, s in enumerate(dl):
            T(c, cols["desc"][0] + 4, ty - 12 * k, s, size=8.5)
        if has_hsn:
            T(c, cols["hsn"][0] + 4, ty, ln["hsn_sac"], size=8.5)
        T(c, cols["qty"][1] - 4, ty, ln["quantity"], size=8.5, align="r")
        T(c, cols["rate"][1] - 4, ty, num(cur, ln["unit_price"]), size=8.5, align="r")
        T(c, cols["amount"][1] - 4, ty, num(cur, ln["amount"]), size=8.5, align="r")
        y -= rh
        if style == "grid":
            c.setStrokeColor(colors.Color(0.6, 0.6, 0.6))
            c.setLineWidth(0.5)
            c.line(x0, y, x1, y)
    if style == "grid":
        c.setStrokeColor(colors.Color(0.4, 0.4, 0.4))
        c.setLineWidth(0.7)
        c.rect(x0, y, x1 - x0, top - y, fill=0, stroke=1)
        for k, (a, b) in cols.items():
            if k != "idx":
                c.line(a, top, a, y)
    return y


def draw_classic(c, w, h, inv):
    v, b, cur = inv["vendor"], inv["buyer"], inv["currency"]
    T(c, 40, h - 50, v["name"], "Helvetica-Bold", 16)
    T(c, 40, h - 65, v["addr1"], size=8.5)
    T(c, 40, h - 76, v["addr2"], size=8.5)
    T(c, 40, h - 87, f"{v['tax_label']}: {v['tax_id']}", size=8.5)
    T(c, w - 40, h - 50, "TAX INVOICE" if v["country"] == "IN" else "INVOICE", "Helvetica-Bold", 18, "r")
    rows = [("Invoice No.", inv["invoice_number"]),
            ("Invoice Date", fmt_date(inv["invoice_date"], "classic", v["country"])),
            ("Due Date", fmt_date(inv["due_date"], "classic", v["country"]))]
    if inv["po_number"]:
        rows.append(("PO Number", inv["po_number"]))
    my = h - 70
    c.setStrokeColor(colors.Color(0.4, 0.4, 0.4))
    c.rect(w - 240, my - 15 * len(rows) - 2, 200, 15 * len(rows) + 4, fill=0, stroke=1)
    for k, (lab, val) in enumerate(rows):
        T(c, w - 235, my - 12 - 15 * k, lab, "Helvetica-Bold", 8.5)
        T(c, w - 45, my - 12 - 15 * k, val, size=8.5, align="r")
    by = h - 150
    T(c, 40, by, "BILL TO", "Helvetica-Bold", 8.5, color=GREY)
    T(c, 40, by - 13, b["name"], "Helvetica-Bold", 10)
    T(c, 40, by - 25, b["addr1"], size=8.5)
    T(c, 40, by - 36, b["addr2"], size=8.5)
    T(c, 40, by - 47, f"{b['tax_label']}: {b['tax_id']}", size=8.5)
    y = draw_table(c, w, h - 235, inv, "grid")
    y -= 18
    tx0, tx1 = w - 230, w - 44
    T(c, tx0, y, "Subtotal", size=9)
    T(c, tx1, y, amt(cur, inv["subtotal"], "classic"), size=9, align="r")
    for t in inv["taxes"]:
        y -= 14
        T(c, tx0, y, t["name"], size=9)
        T(c, tx1, y, amt(cur, D(t["amount"]), "classic"), size=9, align="r")
    y -= 8
    c.line(tx0, y, tx1 + 4, y)
    y -= 15
    T(c, tx0, y, "Total", "Helvetica-Bold", 11)
    T(c, tx1, y, amt(cur, inv["total"], "classic"), "Helvetica-Bold", 11, "r")
    y -= 40
    T(c, 40, y, f"Payment Terms: Net {v['terms_days']} days", size=8.5)
    for s in bank_lines(inv):
        y -= 12
        T(c, 40, y, s, size=8)
    footer(c, w, inv)


def draw_modern(c, w, h, inv):
    v, b, cur = inv["vendor"], inv["buyer"], inv["currency"]
    accent = PALETTE.get(v["vendor_id"], colors.Color(0.2, 0.2, 0.2))
    c.setFillColor(accent)
    c.rect(0, h - 70, w, 70, fill=1, stroke=0)
    T(c, 40, h - 42, v["name"], "Helvetica-Bold", 20, color=colors.white)
    T(c, 40, h - 58, f"{v['addr1']}, {v['addr2']}", size=8, color=colors.white)
    T(c, 40, h - 105, "INVOICE", "Helvetica-Bold", 26, color=accent)
    T(c, 40, h - 122, f"{v['tax_label']} {v['tax_id']}", size=8.5, color=GREY)
    # meta block (label above value)
    items = [("Invoice #", inv["invoice_number"]),
             ("Issued", fmt_date(inv["invoice_date"], "modern", v["country"])),
             ("Payment due", fmt_date(inv["due_date"], "modern", v["country"]))]
    if inv["po_number"]:
        items.append(("Customer PO", inv["po_number"]))
    for k, (lab, val) in enumerate(items):
        x = 40 + k * 125
        T(c, x, h - 160, lab.upper(), "Helvetica", 7, color=GREY)
        T(c, x, h - 173, val, "Helvetica-Bold", 10)
    T(c, 40, h - 205, "BILLED TO", "Helvetica", 7, color=GREY)
    T(c, 40, h - 218, b["name"], "Helvetica-Bold", 10)
    T(c, 40, h - 230, f"{b['addr1']}, {b['addr2']}", size=8.5)
    T(c, 40, h - 242, f"{b['tax_label']} {b['tax_id']}", size=8.5)
    y = draw_table(c, w, h - 265, inv, "banded")
    y -= 22
    bx0, bx1 = w - 270, w - 40
    nrows = 1 + len(inv["taxes"])
    bh = 14 * nrows + 38
    c.setFillColor(LIGHT)
    c.rect(bx0, y - bh + 14, bx1 - bx0, bh, fill=1, stroke=0)
    T(c, bx0 + 10, y, "Subtotal", size=9)
    T(c, bx1 - 10, y, amt(cur, inv["subtotal"], "modern"), size=9, align="r")
    for t in inv["taxes"]:
        y -= 14
        T(c, bx0 + 10, y, t["name"], size=9)
        T(c, bx1 - 10, y, amt(cur, D(t["amount"]), "modern"), size=9, align="r")
    y -= 22
    T(c, bx0 + 10, y, "Amount Due", "Helvetica-Bold", 12, color=accent)
    T(c, bx1 - 10, y, amt(cur, inv["total"], "modern"), "Helvetica-Bold", 12, "r", accent)
    y -= 50
    T(c, 40, y, f"Terms: net {v['terms_days']} days from issue date. Thank you for your business.", size=8.5)
    for s in bank_lines(inv):
        T(c, 40, y - 12, s, size=8, color=GREY)
    footer(c, w, inv)


def draw_minimal(c, w, h, inv):
    v, b, cur = inv["vendor"], inv["buyer"], inv["currency"]
    F, FB = "Courier", "Courier-Bold"
    y = h - 50
    T(c, 40, y, v["name"].upper(), FB, 12)
    T(c, 40, y - 13, v["addr1"], F, 8.5)
    T(c, 40, y - 24, v["addr2"], F, 8.5)
    T(c, 40, y - 35, f"{v['tax_label']}: {v['tax_id']}", F, 8.5)
    y -= 65
    T(c, 40, y, "INVOICE", FB, 11)
    y -= 16
    lines = [("INV No.:", inv["invoice_number"]),
             ("Dated:", fmt_date(inv["invoice_date"], "minimal", v["country"])),
             ("Pay by:", fmt_date(inv["due_date"], "minimal", v["country"]))]
    if inv["po_number"]:
        lines.append(("Your Ref (PO):", inv["po_number"]))
    for lab, val in lines:
        T(c, 40, y, f"{lab:<15}{val}", F, 9)
        y -= 12
    y -= 8
    T(c, 40, y, "Billed to:", FB, 9)
    T(c, 40, y - 12, b["name"], F, 9)
    T(c, 40, y - 23, f"{b['addr1']}, {b['addr2']}", F, 8.5)
    T(c, 40, y - 34, f"{b['tax_label']}: {b['tax_id']}", F, 8.5)
    y -= 62
    x0, x1 = 40, w - 40
    c.setDash(2, 2)
    c.setStrokeColor(colors.black)
    c.line(x0, y + 10, x1, y + 10)
    T(c, x0, y - 2, "CODE", FB, 8.5)
    T(c, x0 + 95, y - 2, "DESCRIPTION", FB, 8.5)
    T(c, x1 - 150, y - 2, "QTY", FB, 8.5, "r")
    T(c, x1 - 85, y - 2, "RATE", FB, 8.5, "r")
    T(c, x1, y - 2, "AMT", FB, 8.5, "r")
    c.line(x0, y - 8, x1, y - 8)
    y -= 22
    for ln in inv["lines"]:
        dl = wrap(ln["description"], F, 8.5, x1 - 150 - 40 - x0 - 95)
        T(c, x0, y, ln["sku"], F, 8.5)
        for k, s in enumerate(dl):
            T(c, x0 + 95, y - 11 * k, s, F, 8.5)
        T(c, x1 - 150, y, ln["quantity"], F, 8.5, "r")
        T(c, x1 - 85, y, num(cur, ln["unit_price"]), F, 8.5, "r")
        T(c, x1, y, num(cur, ln["amount"]), F, 8.5, "r")
        y -= 11 * len(dl) + 6
    c.line(x0, y + 4, x1, y + 4)
    y -= 14
    T(c, x1 - 200, y, "Sub-total", F, 9)
    T(c, x1, y, amt(cur, inv["subtotal"], "minimal"), F, 9, "r")
    for t in inv["taxes"]:
        y -= 12
        T(c, x1 - 200, y, t["name"], F, 9)
        T(c, x1, y, amt(cur, D(t["amount"]), "minimal"), F, 9, "r")
    y -= 8
    c.line(x1 - 200, y, x1, y)
    y -= 14
    T(c, x1 - 200, y, "GRAND TOTAL", FB, 10)
    T(c, x1, y, amt(cur, inv["total"], "minimal"), FB, 10, "r")
    c.setDash()
    y -= 40
    T(c, 40, y, f"Terms: {v['terms_days']} days net. Subject to local jurisdiction.", F, 8)
    for s in bank_lines(inv):
        T(c, 40, y - 11, s, F, 7.5)
    footer(c, w, inv)


def draw_compact(c, w, h, inv):
    v, b, cur = inv["vendor"], inv["buyer"], inv["currency"]
    # left sidebar strip
    c.setFillColor(colors.Color(0.15, 0.15, 0.18))
    c.rect(0, 0, 14, h, fill=1, stroke=0)
    T(c, 36, h - 48, v["name"], "Helvetica-Bold", 14)
    T(c, 36, h - 61, f"{v['addr1']}  |  {v['addr2']}", size=8, color=GREY)
    T(c, 36, h - 72, f"{v['tax_label']} {v['tax_id']}", size=8, color=GREY)
    T(c, w - 40, h - 48, "Tax Invoice" if v["country"] == "IN" else "Invoice", "Helvetica-Bold", 14, "r")
    meta = [("Document no", inv["invoice_number"]),
            ("Doc date", fmt_date(inv["invoice_date"], "compact", v["country"])),
            ("Due", fmt_date(inv["due_date"], "compact", v["country"]))]
    if inv["po_number"]:
        meta.append(("Purchase order", inv["po_number"]))
    for k, (lab, val) in enumerate(meta):
        T(c, w - 150, h - 66 - 12 * k, lab, size=8, color=GREY)
        T(c, w - 40, h - 66 - 12 * k, val, "Helvetica-Bold", 8.5, "r")
    T(c, 36, h - 115, "Customer:", "Helvetica-Bold", 8.5)
    T(c, 90, h - 115, f"{b['name']} - {b['addr2']}  ({b['tax_label']} {b['tax_id']})", size=8.5)
    y = h - 145
    c.setStrokeColor(colors.black)
    c.setLineWidth(1)
    c.line(36, y + 12, w - 40, y + 12)
    T(c, 36, y, "ITEMS", "Helvetica-Bold", 8)
    T(c, w - 40, y, "AMOUNT", "Helvetica-Bold", 8, "r")
    c.line(36, y - 6, w - 40, y - 6)
    y -= 24
    for ln in inv["lines"]:
        T(c, 36, y, ln["description"], "Helvetica-Bold", 9.5)
        T(c, w - 40, y, amt(cur, ln["amount"], "compact"), "Helvetica-Bold", 9.5, "r")
        hs = f"  |  HSN/SAC {ln['hsn_sac']}" if ln["hsn_sac"] else ""
        T(c, 36, y - 12, f"Code {ln['sku']}{hs}  |  {ln['quantity']} {ln['uom']} x {num(cur, ln['unit_price'])}",
          size=8, color=GREY)
        y -= 34
    c.setLineWidth(0.5)
    c.line(36, y + 14, w - 40, y + 14)
    y -= 4
    T(c, w - 170, y, "Net", size=9)
    T(c, w - 40, y, amt(cur, inv["subtotal"], "compact"), size=9, align="r")
    for t in inv["taxes"]:
        y -= 13
        T(c, w - 170, y, t["name"], size=9)
        T(c, w - 40, y, amt(cur, D(t["amount"]), "compact"), size=9, align="r")
    y -= 28
    c.setFillColor(colors.Color(0.15, 0.15, 0.18))
    c.rect(w - 230, y - 8, 190, 22, fill=1, stroke=0)
    T(c, w - 222, y, "Gross total", "Helvetica-Bold", 10, color=colors.white)
    T(c, w - 46, y, amt(cur, inv["total"], "compact"), "Helvetica-Bold", 10, "r", colors.white)
    y -= 40
    T(c, 36, y, f"Payable within {v['terms_days']} days of document date.", size=8.5)
    for s in bank_lines(inv):
        T(c, 36, y - 12, s, size=8, color=GREY)
    footer(c, w, inv)


DRAW = dict(classic=draw_classic, modern=draw_modern, minimal=draw_minimal, compact=draw_compact)


def render_pdf(path, inv):
    size = A4 if inv["vendor"]["country"] == "IN" else letter
    c = canvas.Canvas(str(path), pagesize=size, invariant=1)
    c.setTitle(f"Invoice {inv['invoice_number']}")
    c.setAuthor(inv["vendor"]["name"])
    c.setCreator("LedgerPilot synthetic generator")
    DRAW[inv["vendor"]["template"]](c, size[0], size[1], inv)
    c.showPage()
    c.save()


# --------------------------------------------------------------------------- #
# Output writers
# --------------------------------------------------------------------------- #
def ground_truth_record(inv):
    v = inv["vendor"]
    extracted = dict(
        vendor_name=v["name"], vendor_tax_id=v["tax_id"], invoice_number=inv["invoice_number"],
        invoice_date=inv["invoice_date"].isoformat(), due_date=inv["due_date"].isoformat(),
        currency=inv["currency"], po_number=inv["po_number"],
        line_items=[dict(sku=l["sku"], description=l["description"], uom=l["uom"], hsn_sac=l["hsn_sac"] or None,
                         quantity=l["quantity"], unit_price=f(l["unit_price"]), amount=f(l["amount"]))
                    for l in inv["lines"]],
        subtotal=f(inv["subtotal"]),
        tax_lines=[dict(name=t["name"], rate=t["rate"], amount=t["amount"]) for t in inv["taxes"]],
        tax_total=f(inv["tax_total"]), total=f(inv["total"]))
    exc = inv["exception"]
    expected = dict(outcome="exception" if exc else "approve", exception_type=exc,
                    reason=inv["reason"] if exc else "All checks passed.",
                    approval_tier=inv["tier"], notes=inv["notes"])
    if inv["duplicate_of"]:
        expected["duplicate_of"] = inv["duplicate_of"]
    return dict(file=inv["file"], template=v["template"], vendor_in_master=v["vendor_id"] is not None,
                extracted=extracted, expected=expected)


def write_all(out, seed, count):
    out = Path(out)
    inv_dir = out / "invoices"
    if inv_dir.exists():
        shutil.rmtree(inv_dir)
    inv_dir.mkdir(parents=True)
    invoices, pos, grns = generate_dataset(seed, count)

    for inv in invoices:
        render_pdf(inv_dir / inv["file"], inv)

    with open(out / "purchase_orders.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["po_number", "vendor_id", "po_date", "currency", "line_no", "sku",
                                           "description", "uom", "quantity", "unit_price"])
        w.writeheader()
        w.writerows(pos)
    with open(out / "goods_receipts.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["grn_number", "po_number", "line_no", "sku", "received_qty",
                                           "received_date"])
        w.writeheader()
        w.writerows(grns)
    with open(out / "vendors.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["vendor_id", "name", "country", "tax_id_type", "tax_id", "state", "currency",
                    "standard_tax_rate_pct", "payment_terms_days", "category", "address", "status"])
        for v in VENDORS:
            w.writerow([v["vendor_id"], v["name"], v["country"], v["tax_label"], v["tax_id"], v["state"],
                        v["currency"], v["tax_rate"], v["terms_days"], v["category"],
                        f"{v['addr1']}, {v['addr2']}", "active"])

    records = {inv["file"]: ground_truth_record(inv) for inv in invoices}
    exc_counts = {}
    for r in records.values():
        t = r["expected"]["exception_type"]
        if t:
            exc_counts[t] = exc_counts.get(t, 0) + 1
    tier_counts = {}
    for r in records.values():
        t = r["expected"]["approval_tier"]
        if t:
            tier_counts[t] = tier_counts.get(t, 0) + 1
    meta = dict(description="SAMPLE synthetic data for LedgerPilot. All entities are fictional.",
                seed=seed, invoice_count=len(records), approved=sum(1 for r in records.values()
                                                                  if r["expected"]["outcome"] == "approve"),
                exceptions=sum(exc_counts.values()), exception_counts=exc_counts,
                approval_tier_counts=tier_counts,
                templates=sorted({r["template"] for r in records.values()}),
                policy_check_order=["unknown_vendor", "duplicate_invoice", "missing_po", "total_mismatch",
                                    "wrong_tax", "price_mismatch"])
    with open(out / "ground_truth.json", "w") as fh:
        json.dump(dict(meta=meta, invoices=records), fh, indent=2)
        fh.write("\n")
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent))
    a = ap.parse_args()
    meta = write_all(a.out, a.seed, a.count)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
