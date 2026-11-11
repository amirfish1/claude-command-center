# On-call security review

## Threat model

On-call accepts reports from people who may be hostile. It turns reports into
work for an agent that can edit code and run programs. Turning it on grants
that agent the rights of its operating system account. A report is not consent
to use those rights for a different purpose.

Assets include repository contents, credentials, local files, model budget,
customer addresses, private reports, the owner's identity, and the ability to
approve tools or send messages. Actors include the owner, known customers,
strangers, repository collaborators, channel providers, and a compromised agent.
A known address is not proof that its message is safe.

Entry points include email headers and bodies, chat messages, issue bodies and
comments, monitoring data, command output, channel configuration, local API
requests, and agent requests to contact reporters. Provider responses are also
untrusted input. Configuration belongs to the local operator, not a reporter.

Trust boundaries are provider to adapter, adapter to owner-reply router, report
to ticket, ticket to executable agent work, local API to configuration, and
approval to outbound delivery. Thread tokens are bearer credentials. Alert
numbers identify work; they do not authenticate a person. A customer thread
must not authorize actions on another customer's ticket.

The review uses temporary stores and local transports. Tests must not contact
real recipients. Findings and measured limits follow below as checks complete.
