import { useEffect, useState } from "react"
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogAction,
  AlertDialogCancel,
} from "@/components/ui/alert-dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { AlertTriangle } from "lucide-react"
import { toDateTimeLocalValue, dateTimeLocalToIso, formatDateTime } from "@/lib/format"

interface EditCreatedAtDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The entity's current created_at (stored UTC ISO string). */
  currentCreatedAt: string
  /** "quote" | "purchase order" | "invoice" - drives the warning copy. */
  entityLabel: string
  /** Whether changing the date also changes the visible document number (quotes/POs). */
  changesDocumentNumber: boolean
  /** Called with a UTC ISO-8601 string when the user confirms. */
  onConfirm: (iso: string) => Promise<void> | void
  isLoading?: boolean
}

/**
 * Confirmation dialog for editing an entity's "created on" date/time.
 *
 * The user picks a local wall-clock value via a datetime-local input; on confirm we
 * convert it to a UTC ISO-8601 string (single round-trip convention) and hand it to
 * onConfirm. Warns that the change adds a new version and (for quotes/POs) changes the
 * document number.
 */
export function EditCreatedAtDialog({
  open,
  onOpenChange,
  currentCreatedAt,
  entityLabel,
  changesDocumentNumber,
  onConfirm,
  isLoading = false,
}: EditCreatedAtDialogProps) {
  const [localValue, setLocalValue] = useState("")

  // Reset the input to the current value each time the dialog opens.
  useEffect(() => {
    if (open) setLocalValue(toDateTimeLocalValue(currentCreatedAt))
  }, [open, currentCreatedAt])

  const iso = dateTimeLocalToIso(localValue)
  const canSubmit = iso !== null && !isLoading

  const handleConfirm = async () => {
    if (iso === null) return
    await onConfirm(iso)
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Edit created date</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-3">
              <p>
                Current created date is{" "}
                <span className="font-medium text-foreground">{formatDateTime(currentCreatedAt)}</span>.
              </p>
              <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-amber-700 dark:text-amber-300">
                <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
                <span className="text-sm">
                  Changing the created date adds a new version to this {entityLabel}'s audit trail
                  {changesDocumentNumber ? ` and changes its document number` : ``}.
                </span>
              </div>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-2 py-2">
          <Label htmlFor="created-at-input">New created date &amp; time</Label>
          <Input
            id="created-at-input"
            type="datetime-local"
            value={localValue}
            onChange={(e) => setLocalValue(e.target.value)}
            disabled={isLoading}
          />
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isLoading}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              // Keep the dialog open until the async call resolves; the parent closes it.
              e.preventDefault()
              void handleConfirm()
            }}
            disabled={!canSubmit}
          >
            {isLoading ? "Saving..." : "Save new date"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
