# Meridian Global Services - Accounts Payable Policy (SAMPLE)

> **SAMPLE POLICY - fictional.** Written for the LedgerPilot demo. Meridian Global Services, its vendors,
> tax IDs and thresholds are invented and do not reflect any real company's policy or tax advice.
> This document is the knowledge base for the RAG step (Day 3): the agent must cite section numbers
> when it approves an invoice or raises an exception.

Policy version: 1.0 (sample) | Owner: Finance Operations (R2R / AP) | Currencies: INR, USD

## 1. Scope

This policy applies to every supplier invoice received by the Hyderabad (INR) and Austin (USD)
entities. An invoice is processed in the order of checks in section 8. The first failed check
determines the exception type; the invoice is not approved until the exception is cleared.

## 2. Vendor validation (check: `unknown_vendor`)

2.1 Invoices are payable only to vendors in the approved vendor master (`data/vendors.csv`).
2.2 A vendor is matched on legal name **and** tax ID (GSTIN for India, EIN for the US).
2.3 An invoice from a vendor that is not in the master is routed to Vendor Management for onboarding
    and must not be paid, even if a PO number is quoted.

## 3. Duplicate check (check: `duplicate_invoice`)

3.1 The key is **vendor + invoice number** (case- and punctuation-insensitive).
3.2 If the key was already processed, the later invoice is rejected as a duplicate, even when the
    date or amount differs. The AP analyst contacts the vendor to confirm if it is a re-issue.
3.3 Credit notes are out of scope for this sample.

## 4. Purchase order requirement (check: `missing_po`)

4.1 Every invoice must reference a valid, open purchase order issued to the same vendor.
4.2 Invoices with no PO reference, or a PO that does not exist for that vendor, are exceptions
    (`missing_po`). There is no "no PO, no pay" waiver in this sample policy.

## 5. Arithmetic check (check: `total_mismatch`)

5.1 Sum of line amounts (quantity x unit price) must equal the invoice subtotal.
5.2 Subtotal + total tax must equal the stated invoice total.
5.3 Rounding tolerance: **0.05** in invoice currency. Anything above is a `total_mismatch`.

## 6. Tax rules (check: `wrong_tax`)

6.1 Tax is calculated on the invoice subtotal and rounded to 2 decimals.
6.2 **India (GST).** Rate depends on the vendor's service/goods category:

| Vendor category (examples)                      | GST rate |
|-------------------------------------------------|----------|
| Stationery, IT hardware & AMC, facility services| 18%      |
| Freight & logistics, printing & packaging       | 12%      |
| Catering (restaurant / outdoor catering)        | 5%       |

6.3 India, **intra-state** supply (vendor in Telangana, same as the buyer, state code 36): tax is split
    equally into CGST + SGST (e.g. 18% = 9% + 9%). **Inter-state** supply (any other state): a single
    IGST line at the full rate.
6.4 **United States (sales tax).** The vendor's state sales tax rate applies, as held in the vendor master
    (`standard_tax_rate_pct`): Texas 8.25%, California 7.25%, Illinois 6.25%, New York 8.875%.
6.5 If the tax charged differs from `subtotal x standard rate` by more than **0.05**, the invoice is a
    `wrong_tax` exception. The vendor must issue a corrected invoice; AP does not adjust the tax itself.

## 7. Three-way match (check: `price_mismatch`)

7.1 Each invoice line is matched to a PO line (same vendor and SKU) **and** to the goods receipt (GRN).
7.2 **Price:** the invoiced unit price may differ from the PO unit price by at most **2.0%** either way.
    Above that the line fails and the invoice is a `price_mismatch`.
7.3 **Quantity:** the invoiced quantity must not exceed the quantity received on the GRN, and must not
    exceed the PO quantity. Partial deliveries are fine: invoicing 60 of 120 ordered is valid if 60 were
    received. Over-billing against the GRN is an exception.
7.4 A line billed within tolerance (for example +1.0%) is approved, and the agent should note the variance.

## 8. Order of checks and exception handling

| Step | Check                     | Exception type      |
|------|---------------------------|---------------------|
| 1    | Vendor in master          | `unknown_vendor`    |
| 2    | Not a duplicate           | `duplicate_invoice` |
| 3    | Valid PO present          | `missing_po`        |
| 4    | Arithmetic                | `total_mismatch`    |
| 5    | Tax rate and amount       | `wrong_tax`         |
| 6    | Price / quantity vs PO+GRN| `price_mismatch`    |

8.1 Exceptions are never auto-corrected. The agent records the failed check, the numbers involved and the
    policy section, then assigns the exception to an AP analyst.
8.2 An invoice that passes all six checks is **approved** and receives an approval tier (section 9).

## 9. Approval thresholds (invoice total including tax)

| Tier       | INR                     | USD                  | Approver                          |
|------------|-------------------------|----------------------|-----------------------------------|
| `auto`     | up to 50,000            | up to 1,000          | System (no human approval)        |
| `manager`  | 50,000.01 - 5,00,000    | 1,000.01 - 10,000    | Department manager                |
| `director` | above 5,00,000          | above 10,000         | Finance director                  |

9.1 The tier applies only after section 8 checks pass. Boundaries are inclusive of the lower tier.
9.2 Journal entry for an approved invoice: debit expense/asset account for the subtotal, debit input tax
    (GST input credit / sales-tax expense) for the tax, credit accounts payable (vendor) for the total.

## 10. Payment terms

10.1 Due dates follow the vendor master `payment_terms_days` (15, 30 or 45 days net from the invoice date).
10.2 Approved invoices are scheduled for the payment run on or before the due date.
