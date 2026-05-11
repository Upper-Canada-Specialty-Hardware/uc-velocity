import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { SearchableSelect } from "@/components/ui/searchable-select"
import type { SearchableSelectOption } from "@/components/ui/searchable-select"
import { api } from "@/api/client"
import type { Profile, Project, ProjectCreate, Contact } from "@/types"
import { ProfileForm } from "./ProfileForm"
import { ContactForm } from "./ContactForm"

// Only the fields the form actually reads — lets us accept both a full Project
// and the lighter ProjectListView shape (which omits the nested customer profile).
export type ProjectFormInput = Pick<
  Project,
  "id" | "name" | "customer_id" | "status" | "ucsh_project_number" | "project_lead" | "uca_project_number" | "created_on"
>

interface ProjectFormProps {
  project?: ProjectFormInput // If provided, we're editing; otherwise creating
  onSuccess?: () => void
  onCancel?: () => void
}

export function ProjectForm({ project, onSuccess, onCancel }: ProjectFormProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [customers, setCustomers] = useState<Profile[]>([])

  const isEditing = !!project

  const [name, setName] = useState("")
  const [customerId, setCustomerId] = useState<string>("")
  const [status, setStatus] = useState<string>("active")
  const [ucshProjectNumber, setUcshProjectNumber] = useState("")
  const [projectLead, setProjectLead] = useState<string>("")

  // Derive contacts from selected customer
  const selectedCustomer = customers.find(c => c.id.toString() === customerId)
  const availableContacts = selectedCustomer?.contacts || []

  // Handle customer change - reset project lead when customer changes
  const handleCustomerChange = (value: string) => {
    setCustomerId(value)
    setProjectLead("") // Reset project lead when customer changes
  }

  useEffect(() => {
    const fetchCustomers = async () => {
      try {
        const data = await api.profiles.getAll("customer")
        setCustomers(data)
      } catch {
        setError("Failed to load customers")
      }
    }
    fetchCustomers()
  }, [])

  // Populate form when editing
  useEffect(() => {
    if (project) {
      setName(project.name)
      setCustomerId(project.customer_id.toString())
      setStatus(project.status)
      setUcshProjectNumber(project.ucsh_project_number || "")
      setProjectLead(project.project_lead || "")
    }
  }, [project])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!customerId) return

    setLoading(true)
    setError(null)

    const projectData: ProjectCreate = {
      name,
      customer_id: parseInt(customerId),
      status,
      ucsh_project_number: ucshProjectNumber || undefined,
      project_lead: projectLead || undefined,
    }

    try {
      if (isEditing && project) {
        await api.projects.update(project.id, projectData)
      } else {
        await api.projects.create(projectData)
      }
      onSuccess?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${isEditing ? 'update' : 'create'} project`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="text-sm text-destructive bg-destructive/10 p-3 rounded-md border border-destructive/20">
          {error}
        </div>
      )}

      <div className="space-y-2">
        <Label htmlFor="name">Project Name</Label>
        <Input
          id="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g., Office Renovation 2024"
          required
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="customer">Customer</Label>
        <SearchableSelect<Profile>
          options={customers.map((customer): SearchableSelectOption => ({
            value: customer.id.toString(),
            label: customer.name,
            description: customer.address,
          }))}
          value={customerId}
          onChange={handleCustomerChange}
          placeholder="Select a customer"
          searchPlaceholder="Search customers..."
          allowCreate={true}
          createLabel="Create New Customer"
          createDialogTitle="Create New Customer"
          createForm={<ProfileForm defaultType="customer" />}
          onCreateSuccess={(newCustomer) => {
            setCustomers([...customers, newCustomer])
            setCustomerId(newCustomer.id.toString())
          }}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="projectLead">Project Lead (Optional)</Label>
        {!customerId ? (
          <p className="text-sm text-muted-foreground">
            Select a customer first to choose a project lead.
          </p>
        ) : (
          <SearchableSelect<Contact>
            options={availableContacts.map((contact): SearchableSelectOption => ({
              value: contact.name,
              label: contact.name,
              description: contact.job_title || undefined,
            }))}
            value={projectLead}
            onChange={setProjectLead}
            placeholder="Select a project lead (optional)"
            searchPlaceholder="Search contacts..."
            emptyMessage="No contacts found for this customer."
            allowCreate={true}
            createLabel="Create New Contact"
            createDialogTitle="Create New Contact"
            createForm={<ContactForm profileId={parseInt(customerId)} />}
            onCreateSuccess={(newContact) => {
              // Refresh the selected customer to get updated contacts
              const updatedCustomers = customers.map(c =>
                c.id.toString() === customerId
                  ? { ...c, contacts: [...c.contacts, newContact] }
                  : c
              )
              setCustomers(updatedCustomers)
              setProjectLead(newContact.name)
            }}
          />
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="status">Status</Label>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger>
            <SelectValue placeholder="Select status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="on_hold">On Hold</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label htmlFor="ucsh">UCSH Project Number (Optional)</Label>
        <Input
          id="ucsh"
          value={ucshProjectNumber}
          onChange={(e) => setUcshProjectNumber(e.target.value)}
          placeholder="e.g., UCSH-2024-001"
        />
      </div>

      {isEditing && project && (
        <>
          <div className="space-y-2">
            <Label>UCA Project Number</Label>
            <Input value={project.uca_project_number} disabled className="bg-muted" />
          </div>
          <div className="space-y-2">
            <Label>Created On</Label>
            <Input
              value={new Date(project.created_on).toLocaleDateString()}
              disabled
              className="bg-muted"
            />
          </div>
        </>
      )}

      <div className="flex justify-end gap-2 pt-4">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={loading || !name || !customerId || customers.length === 0}>
          {loading ? (isEditing ? "Saving..." : "Creating...") : (isEditing ? "Save Changes" : "Create Project")}
        </Button>
      </div>
    </form>
  )
}
