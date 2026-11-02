# Choosing engines and models for launch policy

Launch policy and each queue's native worker-policy editor share
`static/choice-eligibility.js`. **Choice details & engine setup** explains the
selected engine's cached local observations without changing saved policy.

- Catalog models can report Reasoning, Images and Tools. These are catalog
  capabilities, not a test of this account or a promise that a request succeeds.
- Catalog-unavailable models, models blocked by policy, identifiers cataloged
  only for another engine, and engines outside a managed policy's supported
  context are disabled with a reason. Native validation remains authoritative,
  including effort compatibility and changes since the catalog was loaded.
- Choices with recent installation and local sign-in evidence sort ahead of
  unchecked or setup-needed engines. Missing installation or authentication is
  advisory for future policy: configuring an engine before installation remains
  possible. A CCC engine visibility switch is also advisory, not a service stop.
- Authentication `null`, absent observations, failed status reads, observations
  older than five minutes, undated observations and implausibly future-dated
  observations are never promoted to confirmed sign-in. The five-minute boundary
  is a presentation freshness limit, not credential expiry detection.
- Current unknown/custom and incompatible selections stay visible and selected.
  An incompatible selection is disabled for choosing again, not silently replaced.
  Unknown identifiers remain subject to the native save validator. Selecting a
  different engine still performs the editor's existing explicit model/effort
  clearing; merely rendering or inspecting does not.

The data sources are the already loaded engine/model catalog, current browser
model-policy facts, the existing engine hub's doctor snapshot and (when no doctor
row exists) the existing installation-notice cache. Installation-only evidence
never supplies authentication. No chooser fetches doctor, authentication, usage,
credentials, catalog refreshes or network checks. Account values, binary paths,
credential values and BYOK presence are not projected into the chooser.
The staged host editor uses its loaded/draft disabled-engine list; other caches
can lag an external policy edit, so preview/apply still validate on the server.

**Engine setup · keep draft** opens the canonical connection flow in cached-only
mode. Existing engine-hub polling is suppressed during that handoff, including a
pending timer. Explicit Refresh in the native flow is still an intentional check.
Return, Escape or closing Settings restores the originating policy draft and
focus. A queue dialog is temporarily hidden, not reconstructed or saved. A stale
or absent doctor snapshot makes the setup flow unavailable until explicit refresh;
installation-only observations are not substituted for a doctor snapshot there.

## Consumer boundaries

| Consumer | Integration |
| --- | --- |
| Session/worker defaults in Launch policy | `modelOptionsForSpawnEngine` accepts an optional eligibility context; the native staged controls preserve identifiers and display shared evidence. |
| Worker fallback and managed capability profiles | The same projection uses the server snapshot's respective engine list. Managed profiles remain Claude-only; worker fallback keeps its distinct runtime engines. |
| Queue-local worker policy | The existing native manager's model select and engine chips share the projection. Override/Follow default and saved launch preview remain canonical; no second editor or inferred worker-default model is added. |
| Legacy full queue manager | Shares catalog/evidence display and preserves stored custom values. Its existing explicit model-policy confirmation remains available, rather than being replaced by staged-policy rules. |
| Ordinary session composer / advisor / queue role controls | Their existing behavior remains separate. The optional context on `modelOptionsForSpawnEngine` does not change ordinary spawn confirmation semantics. No new automatic engine selection is introduced. |

This view does not predict runtime quota/headroom decisions, verify provider
connections, discover custom model capabilities, or guarantee a usable launch.
It adds no persistence and no new backend API. Existing validators, revision checks
and transaction/history boundaries are unchanged.
