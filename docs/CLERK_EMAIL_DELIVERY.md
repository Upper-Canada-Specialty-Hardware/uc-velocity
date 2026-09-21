# Clerk email delivery through SMTP2GO

Clerk still generates every sign-in code, embeds it in its configured HTML and
plain-text templates, verifies the submitted code, and creates the user session.
UC Velocity only replaces delivery: Clerk signs an `email.created` webhook,
Railway verifies it, and SMTP2GO sends Clerk's unchanged subject and bodies from
the approved sender.

The backend endpoint is `POST /webhooks/clerk/email`. It accepts either one
complete Svix header family (`svix-id`, `svix-timestamp`, `svix-signature`) or
one complete Standard Webhooks family (`webhook-id`, `webhook-timestamp`,
`webhook-signature`). Mixed, incomplete, stale, future-dated, oversized, or
invalidly signed requests are rejected before delivery.

## Railway configuration

Configure these variables on the UC Velocity backend service:

- `CLERK_WEBHOOK_SIGNING_SECRET`: the `whsec_...` secret shown by Clerk for this
  exact webhook endpoint.
- `SMTP2GO_API_KEY`: the SMTP2GO API credential authorized to use the approved
  sender domain.
- `SMTP2GO_SENDER`: `UC Velocity <HR@s2gms.com>`.

The app loads these values only when a selected webhook needs delivery. Missing
values do not prevent startup, but the endpoint returns a retryable error and
sends nothing. Never put credential values in this file, application logs, or a
Clerk template.

## Clerk cutover

1. Deploy the migration and backend code before changing template delivery.
2. In Clerk, add the production Railway URL ending in
   `/webhooks/clerk/email`, subscribe it to `email.created`, and copy that
   endpoint's signing secret into Railway.
3. Keep Clerk's existing subjects and HTML/plain-text template content. Change
   each relevant sign-in or verification template to external delivery so its
   event has `delivered_by_clerk: false`.
4. Cut over one template first, complete the end-to-end check below, and then
   switch the remaining templates that must use the approved sender.

Messages with `delivered_by_clerk: true` and unrelated signed events are
acknowledged without sending. A selected message must explicitly contain
`delivered_by_clerk: false`; an absent or ambiguous value is rejected.

## End-to-end verification

After the Railway variables and Clerk endpoint are configured:

1. Start a real sign-in that sends a Clerk code to a test inbox.
2. Confirm Clerk reports a successful webhook response and SMTP2GO reports one
   accepted message from `UC Velocity <HR@s2gms.com>`.
3. Confirm the inbox receives the same subject, HTML, plain text, and code that
   Clerk generated, then enter that code and finish sign-in through Clerk.
4. Confirm `clerk_email_deliveries` contains one row for Clerk's email id. The
   row must contain only the opaque id and acceptance timestamp.
5. Retry the same webhook delivery and confirm the endpoint reports a duplicate
   without a second SMTP2GO message.
6. Send a webhook with an invalid signature in a controlled check and confirm
   it is rejected without an SMTP2GO request or receipt.

This repository change does not itself deploy the service, create the Clerk
endpoint, set Railway credentials, switch templates, or prove inbox delivery.
Those configuration and live checks must be completed during rollout.

## Rollout status on 2026-09-21

Railway now has `SMTP2GO_API_KEY`,
`SMTP2GO_SENDER=UC Velocity <HR@s2gms.com>`, and a fresh
`CLERK_WEBHOOK_SIGNING_SECRET` saved with deployments deferred. The UC Velocity
development Clerk instance has `/webhooks/clerk/email` registered and subscribed
only to `email.created`. The `verification_code` template remains delivered by
Clerk until this code is reviewed, integrated into `master`, migrated, and
deployed. No migration or live email delivery has been run. Robert will perform
the inbox and sign-in check with `roberto@ucsh.com` after the path is deployed.

Open PR #234 also changes the migration chain. Integrations must be serialized,
and whichever branch lands second must be rechained to the then-current Alembic
head before merge so the repository keeps one migration head. Deployment must
wait for Robert's normal merge and review process.

## Delivery guarantees and recovery

The backend uses a stable PostgreSQL transaction-scoped advisory lock for each
Clerk email id. This serializes the same email across both Gunicorn workers. A
dedicated two-connection email database pool prevents a slow provider request
from occupying the main application's database pool.
After acquiring the lock, it checks the durable receipt, asks SMTP2GO to accept
the message, inserts the receipt, and commits. Provider failures and database
failures roll back the transaction so Clerk can retry. No recipient, subject,
body, or code is written to the receipt table or application logs.

There is one unavoidable ambiguity shared by direct provider relays: SMTP2GO can
accept the email and the database commit can then fail. Clerk's retry has no
committed receipt to consult, so that rare path can send a duplicate. The
endpoint returns an error rather than falsely acknowledging delivery. During an
incident, compare Clerk's webhook attempt id and SMTP2GO activity before forcing
another retry; never inspect or log the message body or code.
