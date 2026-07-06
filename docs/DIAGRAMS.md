# Diagrams — components, flows, and agent invocations

All diagrams are Mermaid (GitHub renders them inline). SVG renders of the
same diagrams are embedded in `docs/overview.html`. Companions:
`ARCHITECTURE.md` (prose), `ACCOUNT-LIFECYCLE.md` (numbers).

## 1 · End-to-end system flow

Batch in, artifacts out — every component in the deployed path.

```mermaid
flowchart TB
    OP["Operator / scheduler\nrun_batch.py --batch-id"] -->|"batch_id + accounts[] + param_overrides"| SFN

    subgraph envelope ["Batch envelope (Step Functions)"]
        SFN["bdr-poc2-batch-envelope\nMap over accounts · MaxConcurrency 3 · 50% tolerance"]
    end

    SFN -->|"one iteration per account"| LAM["Lambda bdr-poc2-invoke-account\n(SFN has no native AgentCore integration)"]
    LAM -->|"invoke_agent_runtime (payload: account, batch_id, param_overrides)"| RT

    subgraph runtime ["AgentCore Runtime · bdr_poc2_account_runner"]
        RT["agentcore_app.handler"] --> ENG["Pipeline Engine\nloads + lints pipelines/bdr_outreach.yaml"]
    end

    ENG <-->|"write-once checkpoints\n(batch, account, stage)"| DDB[("DynamoDB\nbdr-poc2-state")]
    ENG -->|"identify-lane live fetch"| BR["AgentCore Browser\naws.browser.v1"]
    ENG <-->|"voice exemplars + account events"| MEM["AgentCore Memory\nbdr_poc2_memory"]
    ENG -->|"MCP tools/call (SigV4)"| GW["AgentCore Gateway\nbdr-poc2-gateway"]
    GW -->|"lambda target"| CRM["Lambda\nbdr-poc2-crm-lookup"]
    ENG -->|"tiered model calls"| BED["Bedrock\nhaiku · sonnet · opus"]
    ENG -->|"artifacts + manifest + _trace.json"| S3[("S3\nbdr-poc2-artifacts-{acct}")]

    RT -->|"manifest + cost table"| LAM --> SFN -->|"results[]"| OP
```

## 2 · The pipeline as executed (`bdr_outreach.yaml`)

The flow the engine walks per account. Solid nodes checkpoint write-once;
dashed nodes are idempotent persists that re-run on replay.

```mermaid
flowchart TB
    START(["account payload"]) --> PAR

    subgraph PAR ["pre-flight — parallel group"]
        direction LR
        V["validate\nagent · haiku\nfield gate in CODE,\nmodel = advisory reasons"]
        subgraph IDY ["identify — composite"]
            direction TB
            F["fetch_pages · tool\nchain: attached → browser → fixture"]
            E["enrich · agent · sonnet\nanchors/roles from pages ONLY"]
            P["prioritize · policy\nverbatim HRIS engine → 68/B, P1–P3"]
            PI["persist_identified\ncheckpoint: false"]
            F --> E --> P --> PI
        end
    end

    PAR --> VER["verify · agent · haiku\nfan-out per contact (CRM + identified = 7)\ncheckpoints verify#contact_id"]
    VER --> REC["reconcile · policy\nalphabetical select-3 (config-swappable)"]
    REC --> BARRIER{"barrier\naccount_valid ∧ three_verified"}
    BARRIER -->|"unsatisfied → halt (identification already persisted)"| HALT(["halt"])
    BARRIER --> R["research · agent · sonnet"]
    R --> S["summary · agent · sonnet\nexactly 5 bullets (in code)"]
    S --> G["generate · agent · opus\nfan-out 3 contacts × 3 artifacts = 9\ncheckpoints gen#contact#artifact\nvoice from Memory by bdr_id"]
    G --> PERS["persist\ncheckpoint: false\n§11.2 S3 layout + manifest + Memory event"]
    PERS --> DONE(["ARTIFACTS_QUEUED_REVIEW"])

    style PI stroke-dasharray: 5 4
    style PERS stroke-dasharray: 5 4
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

```mermaid
flowchart TB
    A(["fetch_pages starts"]) --> Q1{"account payload\ncarries page_texts?"}
    Q1 -->|yes| USE1["use attached pages"]
    Q1 -->|no| Q2{"browser fetch\n(AgentCore session)"}
    Q2 -->|"pages found"| USE2["use live pages\nPass A: /about /team /leadership + DDG discovery\nPass B: '&lt;company&gt; &lt;role&gt; LinkedIn' per committee role\nPass C: funding/hiring signals"]
    Q2 -->|"nothing / any failure → fall through"| Q3{"fixture exists\nmocks/pages/{domain}.json?"}
    Q3 -->|yes| USE3["use fixture pages"]
    Q3 -->|no| EMPTY["[] — prioritizer degrades + warns"]

    USE1 --> CK[("checkpoint raw pages\nreplay NEVER re-fetches")]
    USE2 --> CK
    USE3 --> CK
    EMPTY --> CK

    CK --> ENR{"pages empty?"}
    ENR -->|yes| SKIP["Enrichment() — NO model call\n(nothing to judge, nothing invented)"]
    ENR -->|no| SONNET["sonnet judges: anchor quality,\nroles, linkedin_found, incumbents"]
    SKIP --> PRI
    SONNET --> PRI["hris_contact_prioritizer (verbatim):\nextract → score 0–100 → P1–P3\nemail policy: direct always, pattern-guess only HIGH"]
```

## 5 · The checkpoint mechanic — why replay is free

```mermaid
flowchart LR
    RUN["engine runs stage key\n(batch, account, stage_key)"] --> GET{"DynamoDB\nGetItem"}
    GET -->|"item exists"| CACHED["return stored output\n0 model calls · 0 browser · ~ms"]
    GET -->|miss| COMPUTE["run strategy\n(model / browser / pure code)"]
    COMPUTE --> PUT["PutItem\nConditionExpression:\nattribute_not_exists(pk)"]
    PUT -->|"first writer wins"| OK["output stored forever"]
    PUT -->|"concurrent loser"| OK
    OK --> RET["return output"]

    CACHED -.->|"whole account cached:\n0.8s vs ~36s cold"| RET
```

## 6 · Auth & IAM — who signs what

No secrets anywhere: every arrow is an IAM role or SigV4 signature.

```mermaid
flowchart TB
    subgraph roles ["Execution identities"]
        SFNR["bdr-poc2-batch-envelope-role\n→ lambda:InvokeFunction"]
        LAMR["bdr-poc2-invoke-account-role\n→ bedrock-agentcore:InvokeAgentRuntime + logs"]
        RTR["AmazonBedrockAgentCoreSDKRuntime-…\n(toolkit-minted, discovered post-deploy)\n+ BdrArtifactsS3Write + BdrStateTable + BdrEcrPull\n+ BdrBrowserTool + BdrMemory + BdrGateway"]
        GWR["bdr-poc2-gateway-role\n→ lambda:InvokeFunction (crm-lookup only)"]
    end

    SFN["Step Functions"] -->|assumes| SFNR -->|invokes| LAM["invoke-account λ"]
    LAM -->|assumes| LAMR -->|invokes| RT["AgentCore Runtime"]
    RT -->|assumes| RTR
    RTR -->|"Bedrock converse"| BED["models"]
    RTR -->|"S3 / DynamoDB"| DATA[("state + artifacts")]
    RTR -->|"browser sessions"| BR["aws.browser.v1"]
    RTR -->|"events + retrieval"| MEM["Memory"]
    RTR -->|"SigV4-signed MCP tools/call\n(AWS_IAM authorizer — no Cognito,\nno client secrets)"| GW["Gateway"]
    GW -->|assumes| GWR -->|invokes| CRM["crm-lookup λ"]
```

## 7 · Model roster — every agent call in one cold account

```mermaid
flowchart LR
    subgraph haiku ["haiku · temp 0.0 · $0.017"]
        H1["validate ×1\n(advisory reasons)"]
        H2["verify ×7\n(email-or-phone)"]
    end
    subgraph sonnet ["sonnet · temp 0.3 · $0.036"]
        S1["enrich ×1\n(anchor judgment)"]
        S2["research ×1"]
        S3["summary ×1\n(5 bullets)"]
    end
    subgraph opus ["opus · temp 0.7 · $0.266 — generation only (tier lint)"]
        O1["email ×3"]
        O2["linkedin ×3"]
        O3["talk_track ×3"]
    end
    haiku --> TOTAL["20 model calls\n24.2k in / 4.4k out tokens\n$0.32 / account cold\n$0 on replay"]
    sonnet --> TOTAL
    opus --> TOTAL
```
