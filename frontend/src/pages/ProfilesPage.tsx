import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { ProfileForm } from "@/components/forms/ProfileForm"
import { Input } from "@/components/ui/input"
import { api } from "@/api/client"
import type { Profile, Part, PricebookImportResult } from "@/types"
import { Plus, Trash2, Pencil, Users, Building, ExternalLink, Phone, Mail, MapPin, Upload } from "lucide-react"

export function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingProfile, setEditingProfile] = useState<Profile | null>(null)
  const [viewingProfile, setViewingProfile] = useState<Profile | null>(null)

  // Pricebook state
  const [vendorParts, setVendorParts] = useState<Part[]>([])
  const [loadingParts, setLoadingParts] = useState(false)
  const [importingPricebook, setImportingPricebook] = useState(false)
  const [importResult, setImportResult] = useState<PricebookImportResult | null>(null)

  const fetchData = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.profiles.getAll()
      setProfiles(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch profiles")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  // Fetch parts linked to this vendor when viewing a vendor
  useEffect(() => {
    if (viewingProfile?.type === "vendor") {
      setLoadingParts(true)
      setImportResult(null)
      api.parts.getAll({ vendor_id: viewingProfile.id })
        .then(setVendorParts)
        .catch(() => setVendorParts([]))
        .finally(() => setLoadingParts(false))
    } else {
      setVendorParts([])
    }
  }, [viewingProfile])

  const handlePricebookImport = async (file: File) => {
    if (!viewingProfile) return
    setImportingPricebook(true)
    setImportResult(null)
    try {
      const result = await api.vendorPricebook.import(viewingProfile.id, file)
      setImportResult(result)
      // Refresh vendor parts list
      const refreshed = await api.parts.getAll({ vendor_id: viewingProfile.id })
      setVendorParts(refreshed)
    } catch (err) {
      setImportResult({ created: 0, updated: 0, errors: [err instanceof Error ? err.message : "Import failed"] })
    } finally {
      setImportingPricebook(false)
    }
  }

  const handleDelete = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation() // Prevent row click
    if (!confirm("Are you sure you want to delete this profile?")) return
    try {
      await api.profiles.delete(id)
      fetchData()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete profile")
    }
  }

  const handleEdit = (e: React.MouseEvent, profile: Profile) => {
    e.stopPropagation() // Prevent row click
    setEditingProfile(profile)
    setEditDialogOpen(true)
  }

  const handleAdd = () => {
    setEditingProfile(null)
    setEditDialogOpen(true)
  }

  const handleEditDialogClose = (open: boolean) => {
    if (!open) {
      setEditingProfile(null)
    }
    setEditDialogOpen(open)
  }

  const handleRowClick = (profile: Profile) => {
    setViewingProfile(profile)
  }

  // Split profiles into customers and vendors
  const customers = profiles.filter(p => p.type === 'customer')
  const vendors = profiles.filter(p => p.type === 'vendor')

  // Profile table component for reuse
  const ProfileTable = ({ profiles: tableProfiles, emptyMessage }: { profiles: Profile[], emptyMessage: string }) => (
    <div className="bg-card rounded-lg border shadow-sm">
      {tableProfiles.length === 0 ? (
        <div className="p-6 text-center text-muted-foreground text-sm">
          {emptyMessage}
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Address</TableHead>
              <TableHead>Contacts</TableHead>
              <TableHead>Website</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tableProfiles.map((profile) => (
              <TableRow
                key={profile.id}
                className="cursor-pointer hover:bg-muted/50"
                onClick={() => handleRowClick(profile)}
              >
                <TableCell className="font-medium">{profile.name}</TableCell>
                <TableCell className="text-muted-foreground">
                  <div className="text-sm">{profile.address}</div>
                  <div className="text-xs">{profile.postal_code}</div>
                </TableCell>
                <TableCell>
                  <div className="text-sm">
                    {profile.contacts.length} contact{profile.contacts.length !== 1 ? 's' : ''}
                  </div>
                  {profile.contacts.length > 0 && (
                    <div className="text-xs text-muted-foreground">
                      {profile.contacts[0].name}
                      {profile.contacts.length > 1 && ` +${profile.contacts.length - 1} more`}
                    </div>
                  )}
                </TableCell>
                <TableCell>
                  {profile.website ? (
                    <a
                      href={profile.website}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline flex items-center gap-1 text-sm"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <ExternalLink className="h-3 w-3" />
                      Visit
                    </a>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-right space-x-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={(e) => handleEdit(e, profile)}
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={(e) => handleDelete(e, profile.id)}
                    className="text-destructive hover:text-destructive hover:bg-destructive/10"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold">Profiles</h1>
          <p className="text-muted-foreground">Manage customers and vendors</p>
        </div>
        <Button onClick={handleAdd} className="gap-2">
          <Plus className="h-4 w-4" />
          Add Profile
        </Button>
      </div>

      {error && (
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-md text-destructive">
          {error}
        </div>
      )}

      {loading ? (
        <div className="p-8 text-center text-muted-foreground">Loading...</div>
      ) : (
        <div className="grid grid-cols-2 gap-6">
          {/* Customers column */}
          <div className="space-y-4">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Users className="h-5 w-5" />
              Customers
              <Badge variant="secondary" className="ml-2">{customers.length}</Badge>
            </h2>
            <ProfileTable profiles={customers} emptyMessage="No customers yet. Add your first customer profile." />
          </div>

          {/* Vendors column */}
          <div className="space-y-4">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Building className="h-5 w-5" />
              Vendors
              <Badge variant="secondary" className="ml-2">{vendors.length}</Badge>
            </h2>
            <ProfileTable profiles={vendors} emptyMessage="No vendors yet. Add your first vendor profile." />
          </div>
        </div>
      )}

      {/* Edit Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={handleEditDialogClose}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editingProfile ? "Edit Profile" : "Add New Profile"}</DialogTitle>
            <DialogDescription>
              {editingProfile ? "Update the profile details below." : "Create a new customer or vendor with at least one contact."}
            </DialogDescription>
          </DialogHeader>
          <ProfileForm
            profile={editingProfile ?? undefined}
            onSuccess={() => {
              handleEditDialogClose(false)
              fetchData()
            }}
            onCancel={() => handleEditDialogClose(false)}
          />
        </DialogContent>
      </Dialog>

      {/* View Detail Dialog */}
      <Dialog open={!!viewingProfile} onOpenChange={(open) => !open && setViewingProfile(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {viewingProfile?.type === 'customer' ? (
                <Users className="h-5 w-5" />
              ) : (
                <Building className="h-5 w-5" />
              )}
              {viewingProfile?.name}
            </DialogTitle>
            <DialogDescription>
              <Badge variant={viewingProfile?.type === 'customer' ? 'default' : 'secondary'}>
                {viewingProfile?.type}
              </Badge>
            </DialogDescription>
          </DialogHeader>

          {viewingProfile && (
            <div className="space-y-6 max-h-[60vh] overflow-y-auto">
              {/* Profile Information */}
              <div className="space-y-3">
                <h3 className="font-semibold text-sm text-muted-foreground uppercase tracking-wide">Profile Information</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-sm text-muted-foreground">PST (Provincial Tax Number)</p>
                    <p className="font-medium">{viewingProfile.pst}</p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Postal Code</p>
                    <p className="font-medium">{viewingProfile.postal_code}</p>
                  </div>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground flex items-center gap-1">
                    <MapPin className="h-3 w-3" /> Address
                  </p>
                  <p className="font-medium">{viewingProfile.address}</p>
                </div>
                {viewingProfile.website && (
                  <div>
                    <p className="text-sm text-muted-foreground flex items-center gap-1">
                      <ExternalLink className="h-3 w-3" /> Website
                    </p>
                    <a
                      href={viewingProfile.website}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline font-medium"
                    >
                      {viewingProfile.website}
                    </a>
                  </div>
                )}
              </div>

              <Separator />

              {/* Contacts */}
              <div className="space-y-3">
                <h3 className="font-semibold text-sm text-muted-foreground uppercase tracking-wide">
                  Contacts ({viewingProfile.contacts.length})
                </h3>
                <div className="space-y-3">
                  {viewingProfile.contacts.map((contact) => (
                    <Card key={contact.id}>
                      <CardHeader className="pb-2">
                        <CardTitle className="text-base">{contact.name}</CardTitle>
                        {contact.job_title && (
                          <p className="text-sm text-muted-foreground">{contact.job_title}</p>
                        )}
                      </CardHeader>
                      <CardContent className="space-y-2">
                        {contact.email && (
                          <div className="flex items-center gap-2 text-sm">
                            <Mail className="h-4 w-4 text-muted-foreground" />
                            <a href={`mailto:${contact.email}`} className="text-blue-600 hover:underline">
                              {contact.email}
                            </a>
                          </div>
                        )}
                        {contact.phone_numbers.length > 0 && (
                          <div className="space-y-1">
                            {contact.phone_numbers.map((phone) => (
                              <div key={phone.id} className="flex items-center gap-2 text-sm">
                                <Phone className="h-4 w-4 text-muted-foreground" />
                                <span className="text-muted-foreground capitalize">{phone.type}:</span>
                                <span>{phone.number}</span>
                              </div>
                            ))}
                          </div>
                        )}
                        {!contact.email && contact.phone_numbers.length === 0 && (
                          <p className="text-sm text-muted-foreground">No contact details</p>
                        )}
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </div>

              {/* Vendor Pricebook (vendors only) */}
              {viewingProfile.type === "vendor" && (
                <>
                  <Separator />
                  <div className="space-y-3">
                    <div className="flex justify-between items-center">
                      <h3 className="font-semibold text-sm text-muted-foreground uppercase tracking-wide">
                        Pricebook ({vendorParts.length} parts)
                      </h3>
                      <div className="relative">
                        <Input
                          type="file"
                          accept=".csv"
                          className="absolute inset-0 opacity-0 cursor-pointer"
                          onChange={(e) => {
                            const file = e.target.files?.[0]
                            if (file) handlePricebookImport(file)
                            e.target.value = "" // Reset so same file can be re-imported
                          }}
                          disabled={importingPricebook}
                        />
                        <Button variant="outline" size="sm" disabled={importingPricebook}>
                          <Upload className="h-4 w-4 mr-1" />
                          {importingPricebook ? "Importing..." : "Import CSV"}
                        </Button>
                      </div>
                    </div>

                    {/* Import results */}
                    {importResult && (
                      <div className={`text-sm p-3 rounded-md border ${importResult.errors.length > 0 ? "bg-amber-50 dark:bg-amber-950 border-amber-200 dark:border-amber-800" : "bg-green-50 dark:bg-green-950 border-green-200 dark:border-green-800"}`}>
                        <p>
                          Created: <strong>{importResult.created}</strong> | Updated: <strong>{importResult.updated}</strong>
                        </p>
                        {importResult.errors.length > 0 && (
                          <div className="mt-1 text-amber-700 dark:text-amber-300">
                            {importResult.errors.map((err, i) => (
                              <p key={i} className="text-xs">{err}</p>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Parts table */}
                    {loadingParts ? (
                      <p className="text-sm text-muted-foreground">Loading parts...</p>
                    ) : vendorParts.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        No parts linked to this vendor. Import a CSV pricebook to get started.
                      </p>
                    ) : (
                      <div className="bg-card rounded-lg border">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Part Number</TableHead>
                              <TableHead>Description</TableHead>
                              <TableHead className="text-right">List Price</TableHead>
                              <TableHead className="text-right">Discount %</TableHead>
                              <TableHead className="text-right">Cost</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {vendorParts.map(part => (
                              <TableRow key={part.id}>
                                <TableCell className="font-medium">{part.part_number}</TableCell>
                                <TableCell className="text-muted-foreground">{part.description}</TableCell>
                                <TableCell className="text-right">
                                  {part.list_price != null ? `$${part.list_price.toFixed(2)}` : "—"}
                                </TableCell>
                                <TableCell className="text-right">
                                  {part.discount_percent != null
                                    ? `${part.discount_percent}%`
                                    : viewingProfile.default_discount_percent
                                      ? <span className="text-muted-foreground">{viewingProfile.default_discount_percent}% (default)</span>
                                      : "—"}
                                </TableCell>
                                <TableCell className="text-right font-medium">
                                  ${part.cost.toFixed(2)}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Default Discount display for vendors */}
              {viewingProfile.type === "vendor" && viewingProfile.default_discount_percent != null && (
                <div className="text-sm text-muted-foreground">
                  Default Vendor Discount: <strong>{viewingProfile.default_discount_percent}%</strong>
                </div>
              )}

              {/* Close button */}
              <div className="flex justify-end pt-4">
                <Button variant="outline" onClick={() => setViewingProfile(null)}>
                  Close
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
