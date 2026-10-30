import { describe, it, expect } from "vitest"
import { browseArchived, distinctCustomers, distinctYears, yearOf } from "@/lib/archive-browse"
import type { ArchivedProjectEntry } from "@/types"

/** One archived row with sensible defaults, overridable per test. */
function row(over: Partial<ArchivedProjectEntry>): ArchivedProjectEntry {
  return {
    uca: "A0001",
    name: "Job",
    customer: "Acme",
    created_on: "2010-01-01",
    last_activity: "2015-06-01",
    work_orders: 0,
    pos: 0,
    velocity_quotes: 0,
    velocity_pos: 0,
    in_velocity: false,
    key: "k",
    ...over,
  }
}

const rows: ArchivedProjectEntry[] = [
  row({ uca: "A0003", name: "Roof", customer: "Beta", last_activity: "2018-03-02", work_orders: 5, pos: 1 }),
  row({ uca: "A0001", name: "Floor", customer: "Acme", last_activity: "2011-11-30", work_orders: 2, pos: 3 }),
  row({ uca: "A0002", name: "Wall job", customer: "Acme", last_activity: "2011-01-05", work_orders: 9, pos: 0 }),
  row({ uca: "A0004", name: "Door", customer: null, last_activity: null, work_orders: 1, pos: 1 }),
]

describe("archive browse (#326)", () => {
  it("yearOf takes the leading four digits, or empty", () => {
    expect(yearOf("2018-03-02 00:00:00")).toBe("2018")
    expect(yearOf(null)).toBe("")
    expect(yearOf(undefined)).toBe("")
  })

  it("distinctCustomers drops blanks and sorts", () => {
    expect(distinctCustomers(rows)).toEqual(["Acme", "Beta"])
  })

  it("distinctYears is newest first and skips rows with no activity", () => {
    expect(distinctYears(rows)).toEqual(["2018", "2011"])
  })

  it("sorts by last activity descending by default (newest first)", () => {
    const out = browseArchived(rows, { sortBy: "last_activity", sortDir: "desc" })
    expect(out.map((r) => r.uca)).toEqual(["A0003", "A0001", "A0002", "A0004"])
  })

  it("sorts ascending, nulls comparing as empty so they come first", () => {
    const out = browseArchived(rows, { sortBy: "last_activity", sortDir: "asc" })
    expect(out[0].uca).toBe("A0004") // null activity sorts as "" -> first ascending
  })

  it("sorts numeric columns numerically", () => {
    const out = browseArchived(rows, { sortBy: "work_orders", sortDir: "desc" })
    expect(out.map((r) => r.work_orders)).toEqual([9, 5, 2, 1])
  })

  it("breaks ties on UCA so the order is stable", () => {
    const tied = [row({ uca: "A0009", work_orders: 3 }), row({ uca: "A0002", work_orders: 3 })]
    const out = browseArchived(tied, { sortBy: "work_orders", sortDir: "asc" })
    expect(out.map((r) => r.uca)).toEqual(["A0002", "A0009"])
  })

  it("search matches uca, name or customer, case-insensitively", () => {
    expect(browseArchived(rows, { query: "wall", sortBy: "uca", sortDir: "asc" }).map((r) => r.uca)).toEqual(["A0002"])
    expect(browseArchived(rows, { query: "beta", sortBy: "uca", sortDir: "asc" }).map((r) => r.uca)).toEqual(["A0003"])
    expect(browseArchived(rows, { query: "a0004", sortBy: "uca", sortDir: "asc" }).map((r) => r.uca)).toEqual(["A0004"])
  })

  it("filters by customer exactly", () => {
    const out = browseArchived(rows, { customer: "Acme", sortBy: "uca", sortDir: "asc" })
    expect(out.map((r) => r.uca)).toEqual(["A0001", "A0002"])
  })

  it("filters by year of last activity", () => {
    const out = browseArchived(rows, { year: "2011", sortBy: "uca", sortDir: "asc" })
    expect(out.map((r) => r.uca)).toEqual(["A0001", "A0002"])
  })

  it("combines search, customer and year filters", () => {
    const out = browseArchived(rows, { query: "job", customer: "Acme", year: "2011", sortBy: "uca", sortDir: "asc" })
    expect(out.map((r) => r.uca)).toEqual(["A0002"]) // only "Wall job", Acme, 2011
  })
})
