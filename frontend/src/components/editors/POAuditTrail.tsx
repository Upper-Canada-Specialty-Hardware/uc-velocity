import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { api } from "@/api/client"
import type { POSnapshot, PORevertPreview } from "@/types"
import { RevertConfirmDialog } from "./RevertConfirmDialog"
import {
  History,
  Plus,
  Pencil,
  Trash2,
  Package,
  RotateCcw,
  ChevronDown,
  ChevronRight,
  Circle,
} from "lucide-react"

interface POAuditTrailProps {
  purchaseOrderId: number
  currentVersion: number
  onRevert?: () => void
}

export function POAuditTrail({ purchaseOrderId, currentVersion, onRevert }: POAuditTrailProps) {
  const [snapshots, setSnapshots] = useState<POSnapshot[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedSnapshots, setExpandedSnapshots] = useState<Set<number>>(new Set())

  // Revert dialog state
  const [revertDialogOpen, setRevertDialogOpen] = useState(false)
  const [revertPreview, setRevertPreview] = useState<PORevertPreview | null>(null)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [isReverting, setIsReverting] = useState(false)

  const fetchSnapshots = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.purchaseOrders.getSnapshots(purchaseOrderId)
      setSnapshots(data.sort((a, b) => b.version - a.version))
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch audit trail")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSnapshots()
  }, [purchaseOrderId, currentVersion])

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
      const preview = await api.purchaseOrders.previewRevert(purchaseOrderId, version)
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
      await api.purchaseOrders.revert(purchaseOrderId, selectedVersion)
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
      case "create":
        return <Plus className="h-4 w-4 text-blue-600" />
      case "edit":
        return <Pencil className="h-4 w-4 text-blue-600" />
      case "delete":
        return <Trash2 className="h-4 w-4 text-red-600" />
      case "receive":
        return <Package className="h-4 w-4 text-green-600" />
      case "status_change":
        return <Circle className="h-4 w-4 text-yellow-600" />
      case "revert":
        return <RotateCcw className="h-4 w-4 text-purple-600" />
      default:
        return <History className="h-4 w-4" />
    }
  }

  const getActionBadgeVariant = (actionType: string) => {
    switch (actionType) {
      case "create":
        return "default"
      case "edit":
        return "secondary"
      case "delete":
        return "destructive"
      case "receive":
        return "default"
      case "status_change":
        return "outline"
      case "revert":
        return "outline"
      default:
        return "secondary"
    }
  }

  const getActionBadgeClass = (actionType: string) => {
    switch (actionType) {
      case "receive":
        return "bg-green-600 hover:bg-green-600"
      case "status_change":
        return "bg-yellow-100 text-yellow-800 border-yellow-300"
      case "revert":
        return "bg-purple-100 text-purple-800 border-purple-300"
      default:
        return ""
    }
  }

  const formatDate = (dateString: string) => {
    const date = new Date(dateString)
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    })
  }

  const formatCurrency = (value: number): string => {
    return `$${value.toFixed(2)}`
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
          <div className="text-center py-4">
            <p className="text-sm text-destructive">{error}</p>
            <Button variant="outline" size="sm" className="mt-2" onClick={fetchSnapshots}>
              Retry
            </Button>
          </div>
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
                            <Badge
                              variant={getActionBadgeVariant(snapshot.action_type) as any}
                              className={getActionBadgeClass(snapshot.action_type)}
                            >
                              {snapshot.action_type}
                            </Badge>
                            {isCurrentVersion && (
                              <Badge variant="outline" className="text-xs">
                                Current
                              </Badge>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground">
                            {formatDate(snapshot.created_at)}
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

                    {isExpanded && snapshot.line_items_states.length > 0 && (
                      <div className="px-3 pb-3 pt-1 border-t bg-muted/30">
                        <p className="text-xs font-medium text-muted-foreground mb-2">
                          Line Items at this version:
                        </p>
                        <div className="space-y-1">
                          {snapshot.line_items_states.map((item) => (
                            <div
                              key={item.id}
                              className={`flex items-center justify-between text-xs p-2 rounded ${
                                item.is_deleted
                                  ? "bg-destructive/10 text-destructive line-through"
                                  : "bg-background"
                              }`}
                            >
                              <span>
                                {item.item_type}: {item.description || `ID ${item.original_line_item_id}`}
                              </span>
                              <div className="flex gap-4 text-muted-foreground">
                                <span>Qty: {item.quantity}</span>
                                <span>Pending: {item.qty_pending}</span>
                                <span>Received: {item.qty_received}</span>
                                <span>{formatCurrency(item.unit_price)}</span>
                                {item.actual_unit_price != null && (
                                  <span className="text-green-600">
                                    Actual: {formatCurrency(item.actual_unit_price)}
                                  </span>
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

      <RevertConfirmDialog
        open={revertDialogOpen}
        onOpenChange={setRevertDialogOpen}
        preview={revertPreview}
        onConfirm={handleConfirmRevert}
        isLoading={isReverting}
      />
    </>
  )
}
