import { useState, useEffect, useCallback } from "react"
import { MessageSquare, Send, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { toast } from "@/hooks/use-toast"
import { api } from "@/api/client"
import type { FeedbackThread } from "@/types"

// Poll while the dialog is open so a dev reply shows up without reopening.
const POLL_MS = 15000

/** Local, dependency-free timestamp formatter (received_at is an ISO string). */
const fmt = (iso: string) => {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

/**
 * In-app feedback thread. A user opens a note (bug/feature request), the UCSH team
 * replies from the telemetry dashboard, and the reply shows up here — mirroring the
 * Fieldwire feedback button. Renders nothing at all when telemetry is switched off
 * (the backend reports `enabled: false`), so it is safe to ship before the service
 * is live.
 */
export function FeedbackWidget() {
  const [enabled, setEnabled] = useState(false)
  const [open, setOpen] = useState(false)
  const [threads, setThreads] = useState<FeedbackThread[]>([])
  const [loading, setLoading] = useState(false)
  const [title, setTitle] = useState("")
  const [message, setMessage] = useState("")
  const [sending, setSending] = useState(false)
  const [replyFor, setReplyFor] = useState<number | null>(null)
  const [replyText, setReplyText] = useState("")
  const [replying, setReplying] = useState(false)

  // Is feedback configured at all? Hide the whole widget if not.
  useEffect(() => {
    let cancelled = false
    api.feedback
      .config()
      .then((r) => {
        if (!cancelled) setEnabled(r.enabled)
      })
      .catch(() => {
        /* leave the widget hidden on any error */
      })
    return () => {
      cancelled = true
    }
  }, [])

  const loadThreads = useCallback(async () => {
    try {
      const r = await api.feedback.threads()
      setThreads(r.threads ?? [])
    } catch {
      /* best-effort: keep whatever we last had */
    }
  }, [])

  // Load on open + light poll while open.
  useEffect(() => {
    if (!open) return
    setLoading(true)
    loadThreads().finally(() => setLoading(false))
    const id = setInterval(loadThreads, POLL_MS)
    return () => clearInterval(id)
  }, [open, loadThreads])

  const send = async () => {
    if (!message.trim()) return
    setSending(true)
    try {
      await api.feedback.submit({ message: message.trim(), title: title.trim() || undefined })
      setMessage("")
      setTitle("")
      toast({ title: "Feedback sent", description: "Thanks — the team will see this and reply here." })
      await loadThreads()
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Couldn't send",
        description: err instanceof Error ? err.message : "Please try again.",
      })
    } finally {
      setSending(false)
    }
  }

  const sendReply = async (feedbackId: number) => {
    if (!replyText.trim()) return
    setReplying(true)
    try {
      await api.feedback.reply({ feedback_id: feedbackId, message: replyText.trim() })
      setReplyText("")
      setReplyFor(null)
      await loadThreads()
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Couldn't send reply",
        description: err instanceof Error ? err.message : "Please try again.",
      })
    } finally {
      setReplying(false)
    }
  }

  if (!enabled) return null

  return (
    <>
      {/* Floating trigger — above page content, below modals. */}
      <Button
        onClick={() => setOpen(true)}
        className="fixed bottom-4 right-4 z-40 gap-2 rounded-full shadow-lg"
        size="sm"
      >
        <MessageSquare className="h-4 w-4" />
        Feedback
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <MessageSquare className="h-4 w-4" />
              Feedback &amp; support
            </DialogTitle>
            <DialogDescription>
              Report a bug or request a feature. The team replies here — check back for a response.
            </DialogDescription>
          </DialogHeader>

          {/* Composer — opens a new thread. */}
          <div className="space-y-2">
            <Input
              placeholder="Subject (optional)"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
            />
            <Textarea
              placeholder="What's going on? Describe the bug or the feature you'd like…"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={4}
              maxLength={5000}
            />
            <div className="flex justify-end">
              <Button onClick={send} disabled={sending || !message.trim()} size="sm" className="gap-2">
                {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                {sending ? "Sending…" : "Send feedback"}
              </Button>
            </div>
          </div>

          {/* Existing conversations */}
          <div className="border-t pt-3">
            <p className="mb-2 text-sm font-medium">Your conversations</p>
            {loading && threads.length === 0 ? (
              <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading…
              </div>
            ) : threads.length === 0 ? (
              <p className="py-4 text-sm text-muted-foreground">
                No messages yet. Anything you send appears here with the team's replies.
              </p>
            ) : (
              <ScrollArea className="max-h-72 pr-3">
                <div className="space-y-4">
                  {threads.map((t) => (
                    <div key={t.id} className="space-y-2 rounded-md border p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          {t.title && <p className="truncate text-sm font-medium">{t.title}</p>}
                          <p className="whitespace-pre-wrap break-words text-sm">{t.message}</p>
                        </div>
                        <span className="whitespace-nowrap text-xs text-muted-foreground">{fmt(t.received_at)}</span>
                      </div>

                      {t.replies.length > 0 && (
                        <div className="space-y-2 border-l pl-3">
                          {t.replies.map((rep, i) => (
                            <div key={i} className="text-sm">
                              <span className={rep.author_kind === "dev" ? "font-medium text-primary" : "font-medium"}>
                                {rep.author_kind === "dev" ? `${rep.author || "UCSH"} (team)` : "You"}
                              </span>
                              <span className="text-muted-foreground"> · {fmt(rep.received_at)}</span>
                              <p className="whitespace-pre-wrap break-words">{rep.body}</p>
                            </div>
                          ))}
                        </div>
                      )}

                      {replyFor === t.id ? (
                        <div className="space-y-2">
                          <Textarea
                            placeholder="Write a reply…"
                            value={replyText}
                            onChange={(e) => setReplyText(e.target.value)}
                            rows={2}
                            maxLength={5000}
                          />
                          <div className="flex justify-end gap-2">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => {
                                setReplyFor(null)
                                setReplyText("")
                              }}
                            >
                              Cancel
                            </Button>
                            <Button
                              size="sm"
                              onClick={() => sendReply(t.id)}
                              disabled={replying || !replyText.trim()}
                              className="gap-2"
                            >
                              {replying ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                              Reply
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setReplyFor(t.id)
                            setReplyText("")
                          }}
                        >
                          Reply
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              </ScrollArea>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
