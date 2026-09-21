# Clerk email delivery through SMTP2GO

Clerk still generates every sign-in code, verifies the submitted code, and
creates the user session. UC Velocity replaces delivery: Clerk signs an
`email.created` webhook, Railway verifies it, and SMTP2GO sends the message from
the approved sender. For `verification_code` and `reset_password_code`, UC Velocity puts Clerk's
signed `event.data.data.otp_code` value into fixed local HTML and plain-text
templates. It does not generate or verify the code, and it never extracts the
code from rendered HTML. Other Clerk email slugs retain Clerk's original subject
and bodies unchanged.

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
3. Keep Clerk's existing subjects. Change each relevant sign-in or verification
   template to external delivery so its event has `delivered_by_clerk: false`.
   Both `verification_code` and `reset_password_code` use the same local HTML and
   text templates from Clerk's signed `event.data.data.otp_code`; other slugs
   pass through Clerk's supplied bodies.
4. Disable Clerk delivery for both code templates after deploying the renderer.
   Both use the same webhook, Jinja templates, SMTP2GO sender, and receipt handling.
   Password resets retain Clerk's original subject and code.

Messages with `delivered_by_clerk: true` and unrelated signed events are
acknowledged without sending. A selected message must explicitly contain
`delivered_by_clerk: false`; an absent or ambiguous value is rejected.

## End-to-end verification

After the Railway variables and Clerk endpoint are configured:

1. Start a real sign-in that sends a Clerk code to a test inbox.
2. Confirm Clerk reports a successful webhook response and SMTP2GO reports one
   accepted message from `UC Velocity <HR@s2gms.com>`.
3. Confirm the inbox receives Clerk's subject and code in the UC Velocity HTML
   and plain-text wording, then enter that code and finish sign-in through Clerk.
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

The Jinja rendering follow-up is tracked in issue #242, a sub-issue of #240.

It includes both verification codes and forgot-password codes. Password-reset
delivery must be switched from Clerk after the shared renderer is deployed.

The original relay from PR #241 is merged and deployed successfully in Railway
deployment `8b9a53b3-c30b-4ab0-ae37-ae91ef90f3bc`. The live database is at
revision `032_clerk_email_receipts`. Railway has the SMTP2GO key, approved sender,
and Clerk signing secret configured. Clerk's `verification_code` template now has
Clerk delivery disabled, so its events reach `/webhooks/clerk/email` for external
delivery.

Robert's login attempt reached the webhook three times and received `502` each
time, and Robert reported no inbox email. The earlier response handling did not
retain enough safe provider detail to distinguish the rejection path. This
follow-up adds sanitized Railway diagnostics, so dashboard access is not required
for the next attempt. The local Jinja rendering and diagnostic changes have not
been deployed, and no test emails have been sent while implementing them. Robert
will perform the inbox and sign-in check with `roberto@ucsh.com` after this
follow-up is reviewed, merged, and deployed.

## Delivery guarantees and recovery

The backend uses a stable PostgreSQL transaction-scoped advisory lock for each
Clerk email id. This serializes the same email across both Gunicorn workers. A
dedicated two-connection email database pool prevents a slow provider request
from occupying the main application's database pool.
After acquiring the lock, it checks the durable receipt, asks SMTP2GO to accept
the message, inserts the receipt, and commits. Provider failures and database
failures roll back the transaction so Clerk can retry. No recipient, subject,
body, or code is written to the receipt table or application logs.

SMTP2GO responses produce one fixed log outcome: `accepted`, `http_rejected`,
`invalid_response`, `not_accepted`, or `network_error`. Logs may include the HTTP
status, integer succeeded/failed counts, and strictly bounded safe-token values
for SMTP2GO's request id, email id, and error code. They never include provider
error text, response bodies, headers, recipient, subject, message content, code,
API key, or network exception details.

`accepted` means only that the SMTP2GO API accepted one message; it does not prove
inbox delivery. That log is written before the database receipt commits, so a
later persistence failure can still return an error and leave Clerk to retry.

There is one unavoidable ambiguity shared by direct provider relays: SMTP2GO can
accept the email and the database commit can then fail. Clerk's retry has no
committed receipt to consult, so that rare path can send a duplicate. The
endpoint returns an error rather than falsely acknowledging delivery. During an
incident, compare Clerk's webhook attempt id and SMTP2GO activity before forcing
another retry; never inspect or log the message body or code.
