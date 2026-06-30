"""
Built-in default categories.

Every brand-new FinanceBook database is seeded with this income/expense category
tree (see Database.seed_default_categories). Editing this file changes what
fresh databases start with; databases that already have categories are left
untouched, and the seed never runs twice on the same database.

Structure:  type -> { parent category : [child categories] }
"""

DEFAULT_CATEGORIES = {
    "expense": {
        "Entertainment": ["Apps", "Hobbies", "Movies", "Sporting Events",
                          "Subscriptions", "Video Games"],
        "Food": ["Fast Food", "Food Delivery", "Groceries", "Restaurants"],
        "Giving": ["Bahai Fund"],
        "Healthcare": ["Dental Visit", "Doctor Visit", "Gym Membership",
                       "Pharmacy"],
        "Shopping": ["Accessories", "Clothing", "Electronics", "Gifts",
                     "Jewelry", "Shoes", "Toys"],
        "Taxes": ["Federal Taxes", "Local Taxes", "State Taxes"],
        "Transportation": ["Car Payment", "Gas", "Parking", "Public Transport",
                           "Registration", "Rental Car", "Ride Share", "Tolls",
                           "Vehicle Maintenance"],
        "Travel": ["Airbnb", "Flights", "Hotel", "Vacation Activities"],
        "Utilities": ["Streaming Services"],
    },
    "income": {
        "Employment": ["Bonuses", "Commission", "Hourly Wages", "Interest Income",
                       "Overtime", "Salary", "Tips"],
        "Government": ["Tax Return"],
        "Investments": ["Dividends", "Rental Income"],
        "Transfers": ["Cash Back Rewards", "Gifts Received", "Refunds",
                      "Reimbursement"],
    },
}
