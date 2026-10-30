import { useCallback, useEffect, useMemo, useState } from "react"
import { CheckCircle2, ChevronLeft, ChevronRight, ExternalLink, Sparkles, AlertTriangle } from "lucide-react"
import { api } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/hooks/use-toast"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { formatLoggedValue } from "@/lib/format"
import type { VisionDecisionCase, VisionDecisionLink, VisionDecisionStep } from "@/types"

/** How each answer reads on its button. */
const OPTION_LABEL: Record<string, string> = {
  vision: "Use Vision",
  velocity: "Keep Velocity",
  "left out": "Leave out",
  "entered by hand": "Entered by hand",
  duplicate: "Duplicate",
  "separate projects": "Separate projects",
  "same job": "Same job",
  "separate jobs": "Separate jobs",
  "same order": "Same order",
  "separate orders": "Separate orders",
  "keep both": "Keep both",
  "copy replaces original": "Copy replaces original",
}

/** What each answer writes, shown under the buttons. */
const OPTION_EFFECT: Record<string, string> = {
  vision: "writes Vision's values into Velocity (refused if Velocity changed since the cutover)",
  velocity: "Velocity stays as it is; on a quote or PO, Vision's version is added to its audit trail",
  "same job":
    "merges them: the Velocity quote stays and takes the Vision WO #; the added quote goes into its audit trail, then is deleted",
  "same order":
    "merges them: the Velocity PO stays and takes the Vision PO #; the added PO goes into its audit trail, then is deleted",
  "copy replaces original":
    "merges them: the copy stays; the original goes into its audit trail, then is deleted",
  duplicate:
    "moves the other project's quotes and POs into the one holding the work (they get new numbers), then deletes the emptied project",
}

/** Answers that change data, so accepting them in bulk asks first. */
const WRITING = new Set(["vision", "velocity", "same job", "same order", "copy replaces original", "duplicate"])

/** Cases fetched per request. */
const PAGE = 20

/**
 * The app path a case's record opens at.
 * @param link - The record's project and document.
 * @returns A path under /projects.
 */
function linkPath(link: VisionDecisionLink): string {
  if (link.type === "quote") return `/projects/${link.project_id}/quotes/${link.id}` // the quote inside its project
  if (link.type === "po") return `/projects/${link.project_id}/pos/${link.id}` // the PO inside its project
  return `/projects/${link.project_id}` // the project itself
}

/**
 * A value as the page shows it: blank as a dash.
 * @param v - Logged text.
 * @returns Display text.
 */
function show(v: string | null | undefined): string {
  return v === null || v === undefined || v === "" ? "—" : v
}

/**
 * The decisions page: walk, one case at a time, through what the Vision
 * cutover left for a person, step by step, each case with a predicted answer.
 * Admin only (gated in App).
 */
export function VisionDecisionsPage() {
  const [steps, setSteps] = useState<VisionDecisionStep[]>([]) // every step with its counts
  const [kind, setKind] = useState<string | null>(null) // the step being worked
  const [cases, setCases] = useState<VisionDecisionCase[]>([]) // loaded cases of the step
  const [total, setTotal] = useState(0) // how many the step holds in the current view
  const [index, setIndex] = useState(0) // the case on screen
  const [showSettled, setShowSettled] = useState(false) // review settled cases instead of open ones
  const [note, setNote] = useState("") // the person's note for the case on screen
  const [busy, setBusy] = useState(false) // a request is running
  const [error, setError] = useState<string | null>(null) // why the last decision was refused
  const [confirmAccept, setConfirmAccept] = useState(false) // the bulk-accept dialog is open

  const step = useMemo(() => steps.find((s) => s.kind === kind) ?? null, [steps, kind]) // current step's details
  const current = cases[index] ?? null // the case on screen

  /** Reload the step list; picks the first step with open cases when none is chosen. */
  const loadSteps = useCallback(async () => {
    const s = await api.visionDecisions.getSteps()
    setSteps(s)
    setKind((k) => k ?? (s.find((x) => x.open > 0) ?? s[0])?.kind ?? null) // first unfinished step
  }, [])

  /**
   * Load one page of the current step's cases.
   * @param offset - First case to fetch.
   * @param replace - Start the list over instead of appending.
   */
  const loadCases = useCallback(
    async (offset: number, replace: boolean) => {
      if (!kind) return
      const page = await api.visionDecisions.getCases(kind, showSettled ? "settled" : "open", offset, PAGE)
      setTotal(page.total)
      setCases((prev) => (replace ? page.cases : [...prev, ...page.cases])) // append when paging forward
    },
    [kind, showSettled]
  )

  useEffect(() => {
    loadSteps().catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [loadSteps])

  useEffect(() => {
    // New step or view: start at its first case
    setIndex(0)
    setNote("")
    setError(null)
    loadCases(0, true).catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [loadCases])

  /** Move to the next case, fetching more when near the end of what is loaded. */
  const next = () => {
    setError(null)
    setNote("")
    const to = index + 1
    if (to >= cases.length - 2 && cases.length < total) {
      loadCases(cases.length, false).catch(() => undefined) // prefetch the next page
    }
    setIndex(Math.min(to, Math.max(cases.length - 1, 0)))
  }

  /** Move back one case. */
  const back = () => {
    setError(null)
    setNote("")
    setIndex((i) => Math.max(0, i - 1))
  }

  /**
   * Settle the case on screen with an answer.
   * @param decision - One of the case's options.
   */
  const decide = async (decision: string) => {
    if (!current) return
    setBusy(true)
    setError(null)
    try {
      await api.visionDecisions.decide(current.id, decision, note)
      toast({ title: "Recorded", description: `${current.title ?? "case"}: ${OPTION_LABEL[decision] ?? decision}` })
      setNote("")
      // The case leaves the open list; the next one slides into its place
      const rest = cases.filter((c) => c.id !== current.id)
      setCases(rest)
      setTotal((t) => t - 1)
      setIndex((i) => Math.min(i, Math.max(rest.length - 1, 0)))
      if (rest.length < 3 && total - 1 > rest.length) loadCases(rest.length, false).catch(() => undefined)
      await loadSteps()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)) // refused: nothing was written
    } finally {
      setBusy(false)
    }
  }

  /** Accept the predicted answer on every open case of the step that has one. */
  const acceptAll = async () => {
    if (!kind) return
    setConfirmAccept(false)
    setBusy(true)
    setError(null)
    try {
      // Collect the ids of every open, predicted case (the list may hold only a page)
      const ids: number[] = []
      for (let offset = 0; ; offset += 100) {
        const page = await api.visionDecisions.getCases(kind, "open", offset, 100)
        ids.push(...page.cases.filter((c) => c.prediction).map((c) => c.id))
        if (offset + 100 >= page.total) break
      }
      let settled = 0
      const refused: string[] = []
      for (let i = 0; i < ids.length; i += 200) {
        const r = await api.visionDecisions.acceptPredictions(kind, ids.slice(i, i + 200)) // the API takes 200 at a time
        settled += r.settled.length
        refused.push(...r.refused.map((x) => `${x.title ?? x.id}: ${x.reason}`))
      }
      toast({ title: `Accepted ${settled} predicted answers`, description: refused.length ? `${refused.length} left open` : undefined })
      if (refused.length) setError(`Left open:\n${refused.slice(0, 10).join("\n")}${refused.length > 10 ? "\n…" : ""}`)
      await loadSteps()
      await loadCases(0, true)
      setIndex(0)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  /** Carry out the merge on the step's cases answered before merges existed (#342). */
  const carryOut = async () => {
    if (!kind) return
    setBusy(true)
    setError(null)
    try {
      const r = await api.visionDecisions.carryOut(kind)
      const refused = r.refused.map((x) => `${x.title ?? x.id}: ${x.reason}`) // why each one was left as it was
      toast({ title: `Merged ${r.merged.length} earlier answers`, description: refused.length ? `${refused.length} could not be merged` : undefined })
      if (refused.length) setError(`Not merged:\n${refused.slice(0, 10).join("\n")}${refused.length > 10 ? "\n…" : ""}`)
      await loadSteps()
      await loadCases(0, true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const found = current?.found ?? null // the report's findings for this case, if any
  const foundRows = Array.isArray(found) ? found : found ? [found] : [] // one row, or a group of rows
  const foundCols = foundRows.length ? Object.keys(foundRows[0]) : [] // the report's headings

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Vision decisions</h1>
        <p className="text-muted-foreground">
          What the Vision cutover could not decide on its own. Work through each step; every answer records who made it.
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
        {/* The sequence of steps */}
        <ol className="space-y-1">
          {steps.map((s, i) => (
            <li key={s.kind}>
              <button
                type="button"
                onClick={() => setKind(s.kind)}
                className={`w-full text-left rounded-md px-3 py-2 text-sm border ${
                  s.kind === kind ? "bg-secondary border-primary" : "border-transparent hover:bg-muted"
                }`}
              >
                <div className="flex items-center gap-2">
                  {s.open === 0 ? (
                    <CheckCircle2 className="h-4 w-4 text-green-600 shrink-0" />
                  ) : (
                    <span className="h-4 w-4 shrink-0 text-xs text-center">{i + 1}</span>
                  )}
                  <span className="font-medium flex-1">{s.label}</span>
                </div>
                <div className="text-xs text-muted-foreground pl-6">
                  {s.open} open · {s.settled} settled
                </div>
              </button>
            </li>
          ))}
        </ol>

        {/* The step being worked */}
        <div className="space-y-4 min-w-0">
          {step && (
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-lg font-semibold">{step.label}</h2>
                <p className="text-sm text-muted-foreground">{step.about}</p>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setShowSettled((v) => !v)}>
                  {showSettled ? "Show open" : "Show settled"}
                </Button>
                {!showSettled && step.open_with_prediction > 0 && (
                  <Button size="sm" variant="secondary" disabled={busy} onClick={() => setConfirmAccept(true)}>
                    <Sparkles className="h-4 w-4 mr-1" />
                    Accept {step.open_with_prediction} predicted
                  </Button>
                )}
                {step.unmerged > 0 && (
                  // Answers recorded before merges existed: carry them out now
                  <Button size="sm" variant="secondary" disabled={busy} onClick={carryOut}>
                    Merge {step.unmerged} earlier answers
                  </Button>
                )}
              </div>
            </div>
          )}

          {error && (
            <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-md text-destructive text-sm whitespace-pre-line">
              {error}
            </div>
          )}

          {!current ? (
            <div className="bg-card rounded-lg border p-8 text-center text-muted-foreground">
              {showSettled ? "Nothing settled in this step yet." : "Nothing left to decide in this step."}
            </div>
          ) : (
            <div className="bg-card rounded-lg border shadow-sm p-4 space-y-4">
              {/* Where we are in the step */}
              <div className="flex items-center justify-between text-sm text-muted-foreground">
                <span>
                  Case {index + 1} of {total} {showSettled ? "settled" : "open"}
                </span>
                <div className="flex gap-1">
                  <Button variant="ghost" size="sm" onClick={back} disabled={index === 0 || busy}>
                    <ChevronLeft className="h-4 w-4" /> Back
                  </Button>
                  <Button variant="ghost" size="sm" onClick={next} disabled={index >= total - 1 || busy}>
                    {showSettled ? "Next" : "Skip"} <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              {/* The case */}
              <div>
                <div className="font-semibold">{current.title}</div>
                <div className="flex flex-wrap gap-3 mt-1">
                  {current.links.map((l) => (
                    <a
                      key={`${l.type}${l.id}`}
                      href={linkPath(l)}
                      target="_blank"
                      rel="noreferrer"
                      className="text-sm text-primary inline-flex items-center gap-1 hover:underline"
                    >
                      Open {l.type} <ExternalLink className="h-3 w-3" />
                    </a>
                  ))}
                </div>
              </div>

              {/* Velocity beside Vision, field by field */}
              {current.rows.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="text-left text-muted-foreground">
                      <tr>
                        <th className="py-1 pr-3">Record</th>
                        <th className="py-1 pr-3">Field</th>
                        <th className="py-1 pr-3">Velocity</th>
                        <th className="py-1 pr-3">Vision</th>
                        <th className="py-1">Velocity now</th>
                      </tr>
                    </thead>
                    <tbody>
                      {current.rows.map((r) => (
                        <tr key={r.change_id} className="border-t align-top">
                          <td className="py-1 pr-3 text-muted-foreground">{show(r.context)}</td>
                          <td className="py-1 pr-3">{r.field ?? "whole record"}</td>
                          <td className="py-1 pr-3 whitespace-pre-wrap">{r.field ? formatLoggedValue(r.velocity) : "missing"}</td>
                          {/* Vision's side is never UTC: true keeps any time it carries as-is */}
                          <td className="py-1 pr-3 whitespace-pre-wrap">{r.field ? formatLoggedValue(r.vision, true) : "has it"}</td>
                          <td className="py-1 whitespace-pre-wrap">
                            {r.changed_since ? (
                              <span className="text-amber-600 inline-flex items-center gap-1">
                                <AlertTriangle className="h-3 w-3" /> {formatLoggedValue(r.now)}
                              </span>
                            ) : (
                              <span className="text-muted-foreground">{r.changed_since === null ? "—" : "same"}</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* What the report found (same job, duplicates, copies) */}
              {foundRows.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="text-sm">
                    <tbody>
                      {foundCols.map((col) => (
                        <tr key={col} className="border-t align-top">
                          <th className="py-1 pr-3 text-left font-normal text-muted-foreground whitespace-nowrap">{col}</th>
                          {foundRows.map((fr, i) => (
                            <td key={i} className="py-1 pr-4 whitespace-pre-wrap">
                              {/* report columns named "vision ..." hold Vision's (non-UTC) values */}
                              {formatLoggedValue(fr[col], col.startsWith("vision"))}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* The predicted answer */}
              <div className="rounded-md bg-muted p-3 text-sm">
                <span className="font-medium">Predicted: </span>
                {current.prediction ? OPTION_LABEL[current.prediction] ?? current.prediction : "no prediction"}
                {current.prediction_reason && <span className="text-muted-foreground"> — {current.prediction_reason}</span>}
              </div>

              {current.decision ? (
                // A settled case: what was chosen, by whom, when
                <div className="text-sm">
                  <Badge>{OPTION_LABEL[current.decision] ?? current.decision}</Badge>
                  <span className="ml-2 text-muted-foreground">
                    by {current.decided_by} on {current.decided_at ? new Date(current.decided_at + "Z").toLocaleString() : "—"}
                  </span>
                  {current.decision_note && <p className="mt-1">{current.decision_note}</p>}
                </div>
              ) : (
                <>
                  <Textarea
                    placeholder="Note (optional)"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={2}
                  />
                  <div className="flex flex-wrap gap-2">
                    {current.options.map((o) => (
                      <Button
                        key={o}
                        variant={o === current.prediction ? "default" : "outline"}
                        disabled={busy}
                        onClick={() => decide(o)}
                      >
                        {OPTION_LABEL[o] ?? o}
                      </Button>
                    ))}
                  </div>
                  <ul className="text-xs text-muted-foreground space-y-0.5">
                    {current.options.map((o) => (
                      <li key={o}>
                        <span className="font-medium">{OPTION_LABEL[o] ?? o}:</span> {OPTION_EFFECT[o] ?? "records the answer only"}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      <AlertDialog open={confirmAccept} onOpenChange={setConfirmAccept}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Accept {step?.open_with_prediction} predicted answers?</AlertDialogTitle>
            <AlertDialogDescription>
              Every open case in "{step?.label}" that has a prediction is settled with it, each on its own; cases without
              a prediction stay open.
              {step && step.options.some((o) => WRITING.has(o)) &&
                " Answers that write (Use Vision, Keep Velocity on quotes and POs, Same job, Same order) change records and their audit trails; a case that cannot be carried out is left open and listed."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={acceptAll}>Accept</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
