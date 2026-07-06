# Diagrams — components, flows, and agent invocations

All diagrams are Mermaid (GitHub renders them inline). SVG renders of the
same diagrams are embedded in `docs/overview.html` via
`scripts/render_diagrams.py`. Node labels stay terse on purpose — the detail
lives in the prose around each diagram and in `ARCHITECTURE.md` /
`ACCOUNT-LIFECYCLE.md`.

## 1 · End-to-end system flow

Batch in, artifacts out. The Lambda exists because Step Functions has no
native AgentCore integration; it threads `batch_id` and `param_overrides`
through to the runtime.

```mermaid
flowchart TB
    OP["run_batch.py"] --> SFN["Step Functions Map\n3 at a time · 50% tolerance"]
    SFN -->|"per account"| LAM["invoke-account λ"]
    LAM -->|"invoke_agent_runtime"| ENG["AgentCore Runtime\npipeline engine"]
    ENG <-->|"checkpoints"| DDB[("DynamoDB")]
    ENG -->|"live fetch"| BR["Browser tool"]
    ENG <-->|"voice · events"| MEM["Memory"]
    ENG -->|"MCP · SigV4"| GW["Gateway"] --> CRM["crm-lookup λ"]
    ENG -->|"model calls"| BED["Bedrock\nhaiku · sonnet · opus"]
    ENG -->|"artifacts + trace"| S3[("S3")]
    ENG -.->|"manifest + cost table"| SFN
```

## 2 · The pipeline as executed (`bdr_outreach.yaml`)

The flow the engine walks per account. Every solid stage checkpoints
write-once; stages marked `*` are `checkpoint: false` — idempotent persists
that re-run on every replay. Validate runs in parallel with the whole
identify composite; verify fans over CRM + identified contacts (7 for the
Meridian mock); generate fans 3 selected contacts × 3 artifact types.

```mermaid
flowchart TB
    A(["account"]) --> PRE
    subgraph PRE ["pre-flight · parallel"]
        direction LR
        V["validate\nhaiku"]
        subgraph ID ["identify · composite"]
            direction TB
            F["fetch_pages"] --> E["enrich\nsonnet"]
            E --> P["prioritize\nHRIS P1–P3"] --> PI["persist_identified *"]
        end
    end
    PRE --> VER["verify × 7\nhaiku · per contact"]
    VER --> REC["reconcile\nselect 3"]
    REC --> B{"barrier\nvalid ∧ 3 verified"}
    B -->|"fail → halt"| H(["halt"])
    B --> R["research\nsonnet"]
    R --> S["summary\nsonnet · 5 bullets"]
    S --> G["generate × 9\nopus · contacts × artifacts"]
    G --> PER["persist *"] --> DONE(["ARTIFACTS_QUEUED_REVIEW"])

    style PI stroke-dasharray: 5 4
    style PER stroke-dasharray: 5 4
```

## 3 · One account, cold — who calls whom (sequence)

Every agent/service invocation in order, with the checkpoint reads/writes.

```mermaid
sequenceDiagram
    autonumber
    participant SFN as Step Functions
    participant LAM as invoke-account λ
    participant ENG as Runtime · Engine
    participant DDB as DynamoDB state
    participant BED as Bedrock
    participant BR as Browser tool
    participant MEM as Memory
    participant S3 as S3

    SFN->>LAM: Map item {account, batch_id}
    LAM->>ENG: invoke_agent_runtime

    par validate ∥ identify
        ENG->>DDB: get validate → miss
        ENG->>BED: haiku · advisory reasons
        ENG->>DDB: put validate (write-once)
    and
        ENG->>DDB: get fetch_pages → miss
        ENG->>BR: session: team pages + role SERPs + signals
        ENG->>DDB: put fetch_pages (raw text)
        ENG->>BED: sonnet · enrichment judgment
        ENG->>DDB: put enrich
        Note over ENG: prioritize = pure code (no calls)
        ENG->>DDB: put prioritize
        ENG->>S3: identified_contacts.json
    end

    loop 7 contacts, threaded
        ENG->>BED: haiku · email-or-phone verdict
        ENG->>DDB: put verify#contact_id
    end

    Note over ENG: reconcile (code) → barrier passes
    ENG->>BED: sonnet · research
    ENG->>BED: sonnet · 5-bullet summary
    ENG->>DDB: put research, summary

    ENG->>MEM: get voice exemplars (bdr_id)
    loop 9 = 3 contacts × 3 artifact types, threaded
        ENG->>BED: opus · draft artifact
        ENG->>DDB: put gen#contact#artifact
    end

    ENG->>S3: summary + 9 artifacts + _manifest + _trace
    ENG->>MEM: append account event ("batch ran, 10 queued, 68/B")
    ENG-->>LAM: manifest + cost table
    LAM-->>SFN: iteration result
```

## 4 · Identify lane — fetch fallback + enrichment contract

The browser pass runs the full recipe (team paths + DuckDuckGo discovery,
role-targeted `"<company> <role>" LinkedIn` searches, a signals query) and is
best-effort: any failure falls through the chain. Empty pages skip the model
call entirely — nothing to judge means nothing invented. The prioritizer's
email policy: direct addresses always attach; pattern guesses only at HIGH
confidence.

```mermaid
flowchart TB
    A(["fetch_pages"]) --> Q1{"attached\npages?"}
    Q1 -->|yes| CK[("checkpoint\nraw pages")]
    Q1 -->|no| Q2{"browser finds\npages?"}
    Q2 -->|yes| CK
    Q2 -->|"no / any error"| Q3{"fixture\nexists?"}
    Q3 -->|yes| CK
    Q3 -->|"no → [] + warn"| CK
    CK --> E{"pages\nempty?"}
    E -->|"yes — no model call"| PRI
    E -->|no| SON["enrich · sonnet\nanchors · roles · linkedin"]
    SON --> PRI["prioritize\nscore 0–100 · P1–P3\nemail policy"]
```

## 5 · The checkpoint mechanic — why replay is free

Write-once at `(batch_id, account_id, stage_key)`: the conditional put means
the first writer wins; a whole-account replay is 0.8s vs ~36–50s cold.

```mermaid
flowchart LR
    RUN["stage runs"] --> GET{"GetItem"}
    GET -->|hit| HIT["return stored output\n~ms · $0 · no side effects"]
    GET -->|miss| C["compute\nmodel / browser / code"]
    C --> PUT["PutItem\nattribute_not_exists(pk)"]
    PUT --> RET["return output"]
```

## 6 · Auth & IAM — who signs what

Every hop is an IAM role or a SigV4 signature; the system stores zero
secrets. The runtime's execution role is toolkit-minted and discovered
post-deploy; the installer attaches six inline policies to it.

```mermaid
flowchart TB
    SFN["Step Functions"] -->|"role: invoke λ"| LAM["invoke-account λ"]
    LAM -->|"role: InvokeAgentRuntime"| RT["Runtime\n(execution role)"]
    RT -->|"inline grants"| PERMS["S3 · DynamoDB · ECR\nBrowser · Memory · Bedrock"]
    RT -->|"SigV4 MCP call\nAWS_IAM · no secrets"| GW["Gateway"]
    GW -->|"role: invoke λ"| CRM["crm-lookup λ"]
```

## 7 · Model roster — every agent call in one cold account

20 model calls, ~24k in / ~4.4k out tokens, ≈$0.32 cold and $0 on replay.
The tier lint keeps Opus inside generation-flagged stages only.

```mermaid
flowchart LR
    subgraph H ["haiku · $0.017"]
        H1["validate ×1"]
        H2["verify ×7"]
    end
    subgraph S ["sonnet · $0.036"]
        S1["enrich ×1"]
        S2["research ×1"]
        S3["summary ×1"]
    end
    subgraph O ["opus · $0.266"]
        O1["email ×3"]
        O2["linkedin ×3"]
        O3["talk_track ×3"]
    end
    H --> T["$0.32 / account\n$0 on replay"]
    S --> T
    O --> T
```
