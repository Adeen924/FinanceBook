"""
Built-in categories for Business books, mapped to IRS Schedule C lines.

Seeded once into a business database (see Database.seed_business_categories).
Each parent is (name, tax_line, deduct_pct, [children]); children inherit the
parent's tax line unless given as (name, tax_line, deduct_pct).

tax_line codes follow the 2025/2026 Schedule C (Form 1040) line numbers so the
Schedule C summary report can group by them. deduct_pct is the deductible
share — 50 for business meals (IRC §274(n)). These are bookkeeping defaults,
not tax advice; confirm with your accountant.
"""

# Human-readable names for each tax_line code (used by reports).
TAX_LINES = {
    "1":   "Line 1 · Gross receipts or sales",
    "2":   "Line 2 · Returns and allowances",
    "4":   "Line 4 · Cost of goods sold",
    "6":   "Line 6 · Other income",
    "8":   "Line 8 · Advertising",
    "9":   "Line 9 · Car and truck expenses",
    "10":  "Line 10 · Commissions and fees",
    "11":  "Line 11 · Contract labor",
    "13":  "Line 13 · Depreciation and section 179",
    "14":  "Line 14 · Employee benefit programs",
    "15":  "Line 15 · Insurance (other than health)",
    "16b": "Line 16b · Interest (other)",
    "17":  "Line 17 · Legal and professional services",
    "18":  "Line 18 · Office expense",
    "19":  "Line 19 · Pension and profit-sharing plans",
    "20a": "Line 20a · Rent/lease: vehicles, machinery, equipment",
    "20b": "Line 20b · Rent/lease: other business property",
    "21":  "Line 21 · Repairs and maintenance",
    "22":  "Line 22 · Supplies",
    "23":  "Line 23 · Taxes and licenses",
    "24a": "Line 24a · Travel",
    "24b": "Line 24b · Deductible meals",
    "25":  "Line 25 · Utilities",
    "26":  "Line 26 · Wages",
    "27b": "Line 27b · Other expenses (Part V)",
    "30":  "Line 30 · Business use of home",
}

BUSINESS_CATEGORIES = {
    "income": [
        ("Sales", "1", 100, ["Product Sales", "Online Sales"]),
        ("Services", "1", 100, ["Consulting", "Project Fees"]),
        ("Shipping Income", "1", 100, []),
        ("Refunds / Returns", "2", 100, []),
        ("Other Income", "6", 100, ["Interest Income", "Miscellaneous Income"]),
    ],
    "expense": [
        ("Advertising & Marketing", "8", 100,
         ["Online Ads", "Social Media", "SEO", "Sponsorships",
          "Promotional Items", "Print & Signage", "Events & Trade Shows"]),
        ("Car & Truck", "9", 100,
         ["Fuel", "Vehicle Repairs", "Parking & Tolls", "Mileage Deduction"]),
        ("Commissions & Fees", "10", 100, []),
        ("Merchant / Processing Fees", "10", 100, []),
        ("Contract Labor", "11", 100, []),
        ("Depreciation", "13", 100, []),
        ("Insurance", "15", 100, ["General Liability", "Professional Liability"]),
        ("Interest", "16b", 100, ["Loan Interest", "Credit Card Interest"]),
        ("Legal & Professional", "17", 100,
         ["Accounting", "Legal Fees", "Consultants"]),
        ("Office Expense", "18", 100, ["Postage", "Office Supplies"]),
        ("Rent / Lease", "20b", 100,
         [("Equipment Rental", "20a", 100), "Business Property Rent"]),
        ("Repairs & Maintenance", "21", 100, []),
        ("Supplies", "22", 100, []),
        ("Taxes & Licenses", "23", 100,
         ["Business Licenses & Permits", "Payroll Taxes (Employer)",
          "State & Local Business Taxes"]),
        ("Travel", "24a", 100, ["Airfare", "Lodging", "Ground Transport"]),
        ("Meals (50%)", "24b", 50, []),
        ("Utilities", "25", 100, ["Phone", "Internet", "Electric & Gas"]),
        ("Wages", "26", 100, []),
        ("Employee Benefits", "14", 100, []),
        ("Software & Subscriptions", "27b", 100, []),
        ("Shipping", "27b", 100, []),
        ("Bank Fees", "27b", 100, []),
        ("Education & Training", "27b", 100, []),
        ("Home Office", "30", 100, []),
        ("Cost of Goods Sold", "4", 100,
         ["Materials", "Purchases for Resale", "Freight-in"]),
        ("Other Business Expenses", "27b", 100, []),
    ],
}
