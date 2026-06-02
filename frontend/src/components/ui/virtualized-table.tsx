import {
  cloneElement,
  Fragment,
  isValidElement,
  useRef,
  type HTMLAttributes,
  type ReactElement,
  type ReactNode,
} from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import { cn } from "@/lib/utils"

interface VirtualizedTableProps<T> {
  items: T[]
  /** Estimated row height in pixels; when `measureRows` is true this is just the initial guess and rows self-measure. */
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
  /** Optional per-row attributes (onClick, role, tabIndex, onKeyDown, aria-*) merged onto the row container. */
  getRowProps?: (item: T, index: number) => HTMLAttributes<HTMLDivElement>
  /**
   * Opt into measured (variable) row heights via react-virtual's `measureElement`.
   * Use when rows can grow/shrink (e.g. expandable sub-rows). `rowHeight` becomes the initial estimate.
   */
  measureRows?: boolean
  /**
   * Accessible label for the table. Surfaced via aria-label on the outer
   * role="table" so screen readers can announce e.g. "Parts inventory, table
   * with 1,250 rows".
   */
  tableLabel?: string
}

type RoleableProps = { role?: string }
type FragmentProps = { children?: ReactNode }

/**
 * Flatten a children tree (Fragment-aware) into a list of leaf elements with
 * `role` injected. We descend into fragments because consumers commonly pass
 * `<><div/><div/></>` as `header` / `renderRow` return values, and React's
 * Children helpers treat the fragment itself as one child. Pre-existing `role`
 * props are preserved so a consumer can override (e.g. `role="presentation"`).
 */
function withRole(children: ReactNode, role: string): ReactNode {
  const out: ReactNode[] = []
  let keyCounter = 0
  const walk = (node: ReactNode): void => {
    if (node == null || typeof node === "boolean") return
    if (Array.isArray(node)) {
      node.forEach(walk)
      return
    }
    if (!isValidElement(node)) {
      out.push(node)
      return
    }
    if (node.type === Fragment) {
      walk((node.props as FragmentProps).children)
      return
    }
    const existing = (node.props as RoleableProps).role
    const key = node.key ?? `__vt_${keyCounter++}`
    out.push(
      cloneElement(node as ReactElement<RoleableProps>, {
        role: existing ?? role,
        key,
      })
    )
  }
  walk(children)
  return out
}

/** Count the leaf elements in a children tree (fragments transparent). */
function countLeafChildren(children: ReactNode): number {
  let count = 0
  const walk = (node: ReactNode): void => {
    if (node == null || typeof node === "boolean") return
    if (Array.isArray(node)) {
      node.forEach(walk)
      return
    }
    if (!isValidElement(node)) {
      count += 1
      return
    }
    if (node.type === Fragment) {
      walk((node.props as FragmentProps).children)
      return
    }
    count += 1
  }
  walk(children)
  return count
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
  getRowProps,
  measureRows = false,
  tableLabel,
}: VirtualizedTableProps<T>) {
  const parentRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan,
  })

  // Tag each direct child of the header fragment as a column header so AT
  // sees real header cells rather than flat text inside a row.
  const headerCells = withRole(header, "columnheader")
  const colCount = countLeafChildren(header)

  return (
    <div
      className={cn("bg-card rounded-lg border shadow-sm overflow-hidden", className)}
      role="table"
      aria-label={tableLabel}
      aria-rowcount={items.length + 1}
      aria-colcount={colCount}
    >
      <div role="rowgroup">
        <div
          className={cn("grid bg-muted/50 border-b", gridCols)}
          role="row"
          aria-rowindex={1}
        >
          {headerCells}
        </div>
      </div>
      {items.length === 0 ? (
        <div className="p-8 text-center text-muted-foreground text-sm">{emptyMessage}</div>
      ) : (
        <div
          ref={parentRef}
          style={{ height, overflow: "auto" }}
        >
          <div
            style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}
            role="rowgroup"
          >
            {virtualizer.getVirtualItems().map((vi) => {
              const item = items[vi.index]
              const baseRowClass = "grid border-b hover:bg-muted/50 transition-colors"
              const customRowClass = typeof rowClassName === "function"
                ? rowClassName(item, vi.index)
                : rowClassName
              const rowProps = getRowProps?.(item, vi.index)
              const rowCells = withRole(renderRow(item, vi.index), "gridcell")
              return (
                <div
                  key={getKey(item, vi.index)}
                  data-index={vi.index}
                  ref={measureRows ? virtualizer.measureElement : undefined}
                  role="row"
                  aria-rowindex={vi.index + 2}
                  {...rowProps}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    right: 0,
                    transform: `translateY(${vi.start}px)`,
                    ...(measureRows ? null : { height: vi.size }),
                    ...rowProps?.style,
                  }}
                  className={cn(baseRowClass, gridCols, customRowClass, rowProps?.className)}
                >
                  {rowCells}
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
