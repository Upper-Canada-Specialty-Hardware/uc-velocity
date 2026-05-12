import { useRef, type ReactNode } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import { cn } from "@/lib/utils"

interface VirtualizedTableProps<T> {
  items: T[]
  /** Estimated row height in pixels; rows can still measure their own height via `measureElement` if needed. */
  rowHeight?: number
  /** Scroll container height. Pass a number for px, or a string for CSS values like "70vh". */
  height?: number | string
  /** Tailwind grid columns class applied to both the header and each row (e.g. `grid-cols-[2fr_3fr_1fr]`). */
  gridCols: string
  /** Header cells, expected as direct children of the grid (each child is one cell). */
  header: ReactNode
  /** Row cell renderer — return cells as direct children of the grid. */
  renderRow: (item: T, index: number) => ReactNode
  /** Stable React key extractor. */
  getKey: (item: T, index: number) => string | number
  /** Shown when `items` is empty. */
  emptyMessage?: ReactNode
  /** Number of rows to render outside the visible window. */
  overscan?: number
  /** Optional className on the outer wrapper. */
  className?: string
  /** Optional className on the per-row grid container — useful for accent borders, hover styles, etc. */
  rowClassName?: string | ((item: T, index: number) => string)
}

const HEADER_CLASS = "px-4 py-3 text-left text-sm font-medium text-muted-foreground"

/**
 * Lightweight wrapper around `@tanstack/react-virtual` so we can drop a
 * windowed table into any page without bespoke virtualization plumbing.
 *
 * The header and row containers share `gridCols` to keep columns aligned.
 * The scroll viewport has a fixed height so the page itself doesn't grow to
 * fit thousands of rows.
 */
export function VirtualizedTable<T>({
  items,
  rowHeight = 48,
  height = 560,
  gridCols,
  header,
  renderRow,
  getKey,
  emptyMessage,
  overscan = 10,
  className,
  rowClassName,
}: VirtualizedTableProps<T>) {
  const parentRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan,
  })

  return (
    <div className={cn("bg-card rounded-lg border shadow-sm overflow-hidden", className)}>
      <div className={cn("grid bg-muted/50 border-b", gridCols)}>{header}</div>
      {items.length === 0 ? (
        <div className="p-8 text-center text-muted-foreground text-sm">{emptyMessage}</div>
      ) : (
        <div
          ref={parentRef}
          style={{ height, overflow: "auto" }}
        >
          <div style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}>
            {virtualizer.getVirtualItems().map((vi) => {
              const item = items[vi.index]
              const baseRowClass = "grid border-b hover:bg-muted/50 transition-colors"
              const customRowClass = typeof rowClassName === "function"
                ? rowClassName(item, vi.index)
                : rowClassName
              return (
                <div
                  key={getKey(item, vi.index)}
                  data-index={vi.index}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    right: 0,
                    transform: `translateY(${vi.start}px)`,
                    height: vi.size,
                  }}
                  className={cn(baseRowClass, gridCols, customRowClass)}
                >
                  {renderRow(item, vi.index)}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

/** Default cell class for header cells inside a `<VirtualizedTable>` header. */
export const headerCellClass = HEADER_CLASS

/** Default cell class for body cells inside a `<VirtualizedTable>` row. */
export const cellClass = "px-4 py-3 text-sm flex items-center"
