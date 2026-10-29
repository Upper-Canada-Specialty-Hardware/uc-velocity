"""Read-only delta between UC Vision and UC Velocity.

Compares a CSV export of the live Vision database with a Velocity database,
record by record and field by field, and writes the differences to a workbook.
Nothing is written to any database: the Velocity connection is opened read-only.

Modules:
    vision    -- reads the Vision CSV export into the records Velocity should hold
    velocity  -- reads the same records out of a Velocity database (read-only)
    compare   -- pairs the two sides at every level and lists each difference
    workbook  -- writes the differences to delta.xlsx and delta.json
"""
