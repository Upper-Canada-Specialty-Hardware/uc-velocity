import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { AlertTriangle, History, FileX2 } from "lucide-react"
import type { RevertPreview, PORevertPreview } from "@/types"
import { formatDate } from "@/lib/format"

type RevertPreviewUnion = RevertPreview | PORevertPreview

interface RevertConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  preview: RevertPreviewUnion | null
  onConfirm: () => void
  isLoading?: boolean
}

function isQuotePreview(preview: RevertPreviewUnion): preview is RevertPreview {
  return "invoices_to_void" in preview
}

function isPOPreview(preview: RevertPreviewUnion): preview is PORevertPreview {
  return "receivings_to_void" in preview
}

export function RevertConfirmDialog({
  open,
  onOpenChange,
  preview,
  onConfirm,
  isLoading = false,
}: RevertConfirmDialogProps) {
  if (!preview) return null

  const hasItemsToVoid = isQuotePreview(preview)
    ? preview.invoices_to_void.length > 0
    : preview.receivings_to_void.length > 0

  const entityLabel = isQuotePreview(preview) ? "quote" : "purchase order"

  const getInvoiceStatusBadge = (status: string) => {
    switch (status) {
      case "Sent":
        return <Badge variant="secondary">Sent</Badge>
      case "Paid":
        return <Badge variant="default" className="bg-green-600">Paid</Badge>
      case "Voided":
        return <Badge variant="outline" className="text-muted-foreground">Voided</Badge>
      default:
        return <Badge variant="outline">{status}</Badge>
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <History className="h-5 w-5" />
            Confirm Revert to Version {preview.target_version}
          </DialogTitle>
          <DialogDescription>
            This action will restore the {entityLabel} to a previous state. Please review the changes below.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Changes Summary */}
          <div className="bg-muted/50 p-4 rounded-md">
            <h4 className="text-sm font-medium mb-2">Changes Summary</h4>
            <p className="text-sm text-muted-foreground">{preview.changes_summary}</p>
          </div>

          {/* Invoices to Void Warning (Quote flow) */}
          {isQuotePreview(preview) && preview.invoices_to_void.length > 0 && (
            <div className="bg-destructive/10 border border-destructive/20 p-4 rounded-md">
              <div className="flex items-start gap-3">
                <AlertTriangle className="h-5 w-5 text-destructive flex-shrink-0 mt-0.5" />
                <div className="flex-1">
                  <h4 className="text-sm font-medium text-destructive mb-2">
                    Warning: Invoices Will Be Voided
                  </h4>
                  <p className="text-sm text-destructive/80 mb-3">
                    Reverting to this version will void the following invoice(s).
                    The fulfilled quantities will be returned to pending.
                  </p>
                  <div className="space-y-2">
                    {preview.invoices_to_void.map((invoice) => (
                      <div
                        key={invoice.id}
                        className="flex items-center justify-between bg-background/50 p-2 rounded"
                      >
                        <div className="flex items-center gap-2">
                          <FileX2 className="h-4 w-4 text-destructive" />
                          <span className="text-sm font-medium">Invoice #{invoice.id}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-muted-foreground">
                            {formatDate(invoice.created_at)}
                          </span>
                          {getInvoiceStatusBadge(invoice.status)}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Receivings to Void Warning (PO flow) */}
          {isPOPreview(preview) && preview.receivings_to_void.length > 0 && (
            <div className="bg-destructive/10 border border-destructive/20 p-4 rounded-md">
              <div className="flex items-start gap-3">
                <AlertTriangle className="h-5 w-5 text-destructive flex-shrink-0 mt-0.5" />
                <div className="flex-1">
                  <h4 className="text-sm font-medium text-destructive mb-2">
                    Warning: Receivings Will Be Voided
                  </h4>
                  <p className="text-sm text-destructive/80 mb-3">
                    Reverting to this version will void the following receiving(s).
                    The received quantities will be returned to pending.
                  </p>
                  <div className="space-y-2">
                    {preview.receivings_to_void.map((receiving) => (
                      <div
                        key={receiving.id}
                        className="flex items-center justify-between bg-background/50 p-2 rounded"
                      >
                        <div className="flex items-center gap-2">
                          <FileX2 className="h-4 w-4 text-destructive" />
                          <span className="text-sm font-medium">Receiving #{receiving.id}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-muted-foreground">
                            {formatDate(receiving.received_date)}
                          </span>
                          {receiving.voided_at ? (
                            <Badge variant="outline" className="text-muted-foreground">Voided</Badge>
                          ) : (
                            <Badge variant="secondary">Active</Badge>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {!hasItemsToVoid && (
            <div className="bg-muted/30 p-4 rounded-md">
              <p className="text-sm text-muted-foreground">
                No {isQuotePreview(preview) ? "invoices" : "receivings"} will be affected by this revert.
              </p>
            </div>
          )}
        </div>

        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isLoading}
          >
            Cancel
          </Button>
          <Button
            variant={hasItemsToVoid ? "destructive" : "default"}
            onClick={onConfirm}
            disabled={isLoading}
          >
            {isLoading
              ? "Reverting..."
              : hasItemsToVoid
              ? isQuotePreview(preview)
                ? "Void Invoices & Revert"
                : "Void Receivings & Revert"
              : "Confirm Revert"
            }
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
