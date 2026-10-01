"""Plan definitions (single source for seeding; migration 0001 holds its own literal copy)."""
PLAN_DEFS = [
    # id,   name,  api calls / month, AI tokens / month, monthly fee (cents)
    ("free", "Free", 1_000, 100_000, 0),
    ("pro", "Pro", 50_000, 5_000_000, 2_000),
]
