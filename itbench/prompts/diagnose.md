You are an expert Site Reliability Engineer (SRE).
Analyze the following system snapshot data (Kubernetes events, metrics, logs, alerts) and provide a structured diagnosis.

Data:
{data_str}

Your objective is to generate a **JSON diagnosis** that identifies all Kubernetes entities associated with an incident, according to the following:
- Entities that **CAUSED** the incident (`contributing_factor = true`)
- Entities that **WERE IMPACTED** by the incident but did not cause it (`contributing_factor = false`)
- The **propagation chain** showing how the incident spread from root cause to impacted services

Requirements:
- Explain all firing alerts in the incident.
- Provide reasoning and evidence for every listed entity.
- Construct the fault propagation chain from root cause to impacted services.
- Incorporate Python code for data analysis when necessary.

All entities MUST use the format: `namespace/Kind/name`

Examples:
- `otel-demo/Deployment/ad` (Deployment named "ad" in namespace "otel-demo")
- `otel-demo/Service/frontend` (Service named "frontend")

DO NOT include UIDs in the entity name.

====================================================================
## Output Format
====================================================================
Output must consist solely of the final diagnosis in the specified JSON format below—do **not** include any additional text, markdown, or comments:

{{
  "entities": [
    {{
      "name": "namespace/Kind/name",
      "contributing_factor": true or false,
      "reasoning": "A short, clear, human-readable explanation for this entity's involvement (or lack thereof). Reference evidence where possible.",
      "evidence": "Concise summary of supporting facts—for instance, relevant alerts, events, logs, traces, or metrics. Summarize key points if multiple sources. Provide evidence as a plain string."
    }}
    // ...one object per relevant entity
  ],
  "propagations": [
    {{
      "source": "namespace/Kind/source-name",
      "target": "namespace/Kind/target-name",
      "condition": "What condition in the source caused the propagation",
      "effect": "What effect was observed on the target"
    }}
    // ...one object per propagation link in the causal chain
  ],
  "alerts_explained": [
    {{
      "alert": "<alert name>",
      "explanation": "Human-readable explanation of the alert's significance or reason for firing. Leave blank if not explained.",
      "explained": true or false
    }}
    // ...one object per observed alert
  ]
}}

Guidelines:
- Always return `entities`, `propagations`, and `alerts_explained` arrays. If there are no entries, use empty arrays.
- Use `"namespace/Kind/name"` as the required format for entity names (NO UIDs).
- Set `contributing_factor` to `true` if the entity caused or propagated the incident, or to `false` if it was only impacted.
- Build the `propagations` array to show the causal chain: Root Cause → Intermediate Services → Impacted Services.
- Keep explanation fields (`reasoning` and `explanation`) concise and human-readable; avoid unnecessary verbosity.
- If unable to explain an alert, use `"explained": false` and an empty string for `explanation`.
- The `evidence` field is a plain string referencing supporting alerts, events, logs, metrics, or traces—do not subdivide further.

====================================================================
# 🔗 PROPAGATION CHAIN (MANDATORY)
====================================================================

You MUST construct a propagation chain showing how the incident spread:

Root Cause → Intermediate Services → Impacted Services

For each propagation link:
- `source`: The entity that caused the effect (namespace/Kind/name)
- `target`: The entity that was affected (namespace/Kind/name)  
- `condition`: What condition/state in the source caused propagation
- `effect`: What observable effect occurred in the target

Example:
```json
{{
  "source": "otel-demo/Service/frontend",
  "target": "otel-demo/Service/ad",
  "condition": "ad service has a bug in process() func",
  "effect": "ad service does not respond causing frontend to return http 500"
}}
```

Build the chain from root cause outward to all impacted services.

====================================================================
## Output Verbosity
====================================================================
- Limit the explanation fields (`reasoning`, `explanation`, `evidence`, `condition`, `effect`) to no more than 2 sentences each.
- Return only the required JSON structure—no extra text, markdown, or commentary.
- Prioritize complete, actionable answers within these length caps.

If you provide update or clarification messages, keep them to 1-2 sentences unless explicitly asked for more.

- **contributing_factor = true (IRREDUCIBLE / INDEPENDENT CAUSE)**  
  Mark an entity as a contributing factor ONLY if it is an **independent** cause that is **not fully explained by any other entity** you already marked as contributing_factor=true.
  
  Use this "irreducibility test":
  - If you can explain the entity's failure entirely as "because upstream X failed / changed / was misconfigured" and you have a propagation edge `X -> entity`, then **entity is NOT irreducible** → set `contributing_factor=false`.
  - Only keep `contributing_factor=true` for the minimal set of upstream causes such that removing any one would make your explanation of the incident incomplete.

- **contributing_factor = false (DERIVED / SYMPTOM / DOWNSTREAM IMPACT)**  
  Mark entities that are downstream effects, symptoms, or intermediates whose failure is **caused by** another contributing factor.

**IMPORTANT: Do NOT mark both a cause and its derived symptom as contributing_factor=true.**
If A explains B, then:
- A: `contributing_factor=true`
- B: `contributing_factor=false`
- Add a propagation edge `A -> B` describing the condition/effect.

**Example (quota → ad ReplicaSet/pods):**
- ✅ `otel-demo/Namespace/otel-demo` (memory quota exhausted) → `contributing_factor=true`
- ❌ `otel-demo/Deployment/ad` (pods not spawning because quota exhausted) → `contributing_factor=false`
- Add propagation: `otel-demo/Namespace/otel-demo -> otel-demo/Deployment/ad`

**Multiple contributing_factors are allowed ONLY if they are truly independent** (two separate upstream causes that are not explained by each other).

Include ALL entities for which you found evidence:
- pods
- services
- deployments
- nodes
- sidecars
- jobs / cronjobs
- statefulsets
- ingresses / gateways

Order them by importance:
Primary causes → Secondary propagators → Impacted entities.

====================================================================
# 📚 DEBUGGING PRINCIPLES (MANDATORY)
====================================================================
1. **Differential Observability**  
   Compare replicas ("why A failing but B healthy?") and time windows.

2. **Occam's Razor**  
   Choose simplest explanation consistent with all evidence.

3. **Duration Matching**  
   A valid theory must explain the *entire* incident duration.

4. **Follow the Breadcrumbs**  
   Let alerts and log errors guide your investigation.

5. **Do Not Jump to Conclusions**  
   Validate every hypothesis with real evidence.

6. **Chaos Files Do NOT imply chaos is active**  
   Verify if a chaos experiment was running AND time-aligned.

7. **Semantic Name Normalization**  
   Services appear as `productcatalogservice`, `product-catalog`, `product`.  
   Always:
   - try variations,
   - strip suffixes,
   - search partial matches.
