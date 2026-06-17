import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
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
import { api } from "@/api/client"
import type { InvoiceSnapshot, InvoiceRevertPreview } from "@/types"
import { formatDateTime } from "@/lib/format"
import {
  History,
  Pencil,
  RotateCcw,
  ChevronDown,
  ChevronRight,
} from "lucide-react"

interface InvoiceAuditTrailProps {
  invoiceId: number
  currentVersion: number
  onRevert?: () => void
}

export function InvoiceAuditTrail({ invoiceId, currentVersion, onRevert }: InvoiceAuditTrailProps) {
  const [snapshots, setSnapshots] = useState<InvoiceSnapshot[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedSnapshots, setExpandedSnapshots] = useState<Set<number>>(new Set())

  // Revert dialog state
  const [revertDialogOpen, setRevertDialogOpen] = useState(false)
  const [revertPreview, setRevertPreview] = useState<InvoiceRevertPreview | null>(null)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [isReverting, setIsReverting] = useState(false)

  const fetchSnapshots = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.invoices.getSnapshots(invoiceId)
      setSnapshots(data.sort((a, b) => b.version - a.version))
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch audit trail")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSnapshots()
  }, [invoiceId, currentVersion])

  const toggleExpand = (snapshotId: number) => {
    const newExpanded = new Set(expandedSnapshots)
    if (newExpanded.has(snapshotId)) {
      newExpanded.delete(snapshotId)
    } else {
      newExpanded.add(snapshotId)
    }
    setExpandedSnapshots(newExpanded)
  }

  const handleRevertClick = async (version: number) => {
    try {
      const preview = await api.invoices.previewRevert(invoiceId, version)
      setRevertPreview(preview)
      setSelectedVersion(version)
      setRevertDialogOpen(true)
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to preview revert")
    }
  }

  const handleConfirmRevert = async () => {
    if (selectedVersion === null) return

    setIsReverting(true)
    try {
      await api.invoices.revert(invoiceId, selectedVersion)
      setRevertDialogOpen(false)
      setRevertPreview(null)
      setSelectedVersion(null)
      fetchSnapshots()
      onRevert?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to revert")
    } finally {
      setIsReverting(false)
    }
  }

  const getActionIcon = (actionType: string) => {
    switch (actionType) {
      case "date_edit":
        return <Pencil className="h-4 w-4 text-blue-600" />
      case "revert":
        return <RotateCcw className="h-4 w-4 text-orange-600" />
      default:
        return <History className="h-4 w-4" />
    }
  }

  const getActionBadgeVariant = (actionType: string) => {
    switch (actionType) {
      case "date_edit":
        return "secondary" as const
      case "revert":
        return "outline" as const
      default:
        return "secondary" as const
    }
  }

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <History className="h-4 w-4" />
            Audit Trail
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground text-center py-4">Loading...</p>
        </CardContent>
      </Card>
    )
  }

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <History className="h-4 w-4" />
            Audit Trail
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive text-center py-4">{error}</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <>
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <History className="h-4 w-4" />
            Audit Trail
            <Badge variant="secondary" className="ml-2">
              v{currentVersion}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {snapshots.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">
              No history yet.
            </p>
          ) : (
            <div className="space-y-2">
              {snapshots.map((snapshot, index) => {
                const isExpanded = expandedSnapshots.has(snapshot.id)
                const isCurrentVersion = snapshot.version === currentVersion
                const canRevert = !isCurrentVersion && index > 0

                return (
                  <div
                    key={snapshot.id}
                    className={`border rounded-md ${
                      isCurrentVersion ? "bg-primary/5 border-primary/20" : ""
                    }`}
                  >
                    <div
                      className="flex items-center justify-between p-3 cursor-pointer hover:bg-muted/50"
                      onClick={() => toggleExpand(snapshot.id)}
                    >
                      <div className="flex items-center gap-3">
                        <div className="flex items-center gap-1 text-muted-foreground">
                          {isExpanded ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                        </div>
                        {getActionIcon(snapshot.action_type)}
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium">
                              v{snapshot.version}
                            </span>
                            <Badge variant={getActionBadgeVariant(snapshot.action_type)}>
                              {snapshot.action_type}
                            </Badge>
                            {isCurrentVersion && (
                              <Badge variant="outline" className="text-xs">
                                Current
                              </Badge>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground">
                            {formatDateTime(snapshot.created_at)}
                            {snapshot.actor_email && (
                              <span className="ml-2">· by {snapshot.actor_email}</span>
                            )}
                            {snapshot.action_description && (
                              <span className="ml-2">
                                - {snapshot.action_description}
                              </span>
                            )}
                          </p>
                        </div>
                      </div>
                      {canRevert && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleRevertClick(snapshot.version)
                          }}
                          className="gap-1"
                        >
                          <RotateCcw className="h-3 w-3" />
                          Revert
                        </Button>
                      )}
                    </div>

                    {isExpanded && snapshot.line_item_states.length > 0 && (
                      <div className="px-3 pb-3 pt-1 border-t bg-muted/30">
                        <p className="text-xs font-medium text-muted-foreground mb-2">
                          Line Items at this version:
                        </p>
                        <div className="space-y-1">
                          {snapshot.line_item_states.map((item) => (
                            <div
                              key={item.id}
                              className="flex items-center justify-between text-xs p-2 rounded bg-background"
                            >
                              <span>
                                {item.item_type}: {item.description || `ID ${item.original_line_item_id}`}
                              </span>
                              <div className="flex gap-4 text-muted-foreground">
                                <span>Qty: {item.qty_ordered}</span>
                                <span>This invoice: {item.qty_fulfilled_this_invoice}</span>
                                {item.unit_price != null && (
                                  <span>${item.unit_price.toFixed(2)}</span>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={revertDialogOpen} onOpenChange={setRevertDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <RotateCcw className="h-5 w-5" />
              Revert to version {revertPreview?.target_version}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {revertPreview?.changes_summary} This adds a new version to the invoice audit trail.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isReverting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault()
                void handleConfirmRevert()
              }}
              disabled={isReverting}
            >
              {isReverting ? "Reverting..." : "Confirm revert"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
