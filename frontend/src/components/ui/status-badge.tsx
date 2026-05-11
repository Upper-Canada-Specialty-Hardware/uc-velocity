import * as React from "react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { titleCaseStatus } from "@/lib/format"

/**
 * Single source of truth for status badge colour + casing across the app.
 * Lookup is case-insensitive on the lowercase form so callers don't have to
 * worry whether the backend hands them `Active`, `active`, or `ACTIVE`.
 */
const STATUS_STYLES: Record<string, string> = {
  // Project statuses
  "active":       "bg-green-500/10 text-green-700 border-green-500/30 dark:text-green-300",
  "completed":    "bg-blue-500/10 text-blue-700 border-blue-500/30 dark:text-blue-300",
  "on hold":      "bg-amber-500/10 text-amber-700 border-amber-500/30 dark:text-amber-300",
  "archived":     "bg-slate-500/10 text-slate-700 border-slate-500/30 dark:text-slate-300",

  // Quote statuses
  "draft":        "bg-gray-500/10 text-gray-700 border-gray-500/30 dark:text-gray-300",
  "work order":   "bg-blue-500/10 text-blue-700 border-blue-500/30 dark:text-blue-300",
  "invoiced":     "bg-amber-500/10 text-amber-700 border-amber-500/30 dark:text-amber-300",

  // PO statuses (Draft already covered above)
  "sent":         "bg-indigo-500/10 text-indigo-700 border-indigo-500/30 dark:text-indigo-300",
  "received":     "bg-green-500/10 text-green-700 border-green-500/30 dark:text-green-300",
  "closed":       "bg-blue-500/10 text-blue-700 border-blue-500/30 dark:text-blue-300",

  // Invoice statuses (Draft / Sent / Paid / Voided)
  "paid":         "bg-green-500/10 text-green-700 border-green-500/30 dark:text-green-300",
  "voided":       "bg-red-500/10 text-red-700 border-red-500/30 dark:text-red-300",
}

interface StatusBadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  status: string
}

export function StatusBadge({ status, className, ...rest }: StatusBadgeProps) {
  const key = (status ?? "").trim().toLowerCase().replace(/[_-]+/g, " ")
  const styleClass = STATUS_STYLES[key]
  const label = titleCaseStatus(status)
  return (
    <Badge
      variant="outline"
      className={cn(styleClass ?? "text-foreground", className)}
      {...rest}
    >
      {label}
    </Badge>
  )
}
