# Visibility Query Dedup (Wildcard Workflow ID Match)

Uses the Temporal **Visibility API** to look up existing workflows by a wildcard
(prefix) match on part of the Workflow ID, then decides whether to **reject** the
request or **start a new** workflow.

## Scenario

Workflow IDs follow a structured convention:

```
order-{customer_id}-{order_id}
```

Business rule: a customer may have only **one open order at a time**, regardless
of the trailing `order_id`. Before starting a new order we match the
`order-{customer_id}-` section of the ID — treating the `order_id` portion as a
wildcard — and reject if anything open is found.

## How the wildcard match works

Temporal visibility list filters support the `STARTS_WITH` operator on string
attributes such as the built-in `WorkflowId`. A prefix match is effectively a
"wildcard for the rest of the ID":

```
WorkflowId STARTS_WITH 'order-cust-42-' AND ExecutionStatus = 'Running'
```

The starter uses `client.count_workflows(query)` to decide (cheaper than listing
when you only need existence); use `client.list_workflows(query)` instead if you
want the matching executions themselves.

> `STARTS_WITH` only matches a **prefix**. To wildcard a section in the *middle*
> of the ID, put the variable part at the end of the ID, or promote the value to
> a custom Search Attribute and filter on that instead.

## Run

Start a [dev server](https://docs.temporal.io/cli#start-dev-server):

```bash
temporal server start-dev
```

In one terminal, run the worker:

```bash
uv run python -m visibility_query_dedup.worker
```

In another, run the starter:

```bash
uv run python -m visibility_query_dedup.starter
```

You'll see the first order (`A100`) start, and the second order (`B200`) for the
same customer get rejected because the prefix query matches the still-running
first workflow.

## Caveat: eventual consistency

Visibility is **eventually consistent** — a just-started workflow may not be
searchable for a short moment. This makes the query a *best-effort* gate, not an
atomic guarantee. For strict, race-free deduplication on an exact Workflow ID,
rely on
[`WorkflowIDReusePolicy` / `WorkflowIDConflictPolicy`](https://docs.temporal.io/workflow-execution/workflowid-runid#workflow-id-reuse-policy)
instead. The visibility approach shines when the dedup key spans a *family* of
IDs (the wildcard case) rather than one exact ID.
