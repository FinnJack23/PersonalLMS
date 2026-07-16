# Agent Roster and Contracts

## Contract structure

Every agent definition should contain:

- stable `agent_id`;
- human-readable name;
- mission;
- allowed inputs;
- required sources;
- allowed tools;
- prohibited actions;
- capability profile;
- structured output schema;
- escalation conditions;
- evaluation cases;
- write permissions;
- audit requirements.

Agent definitions must not contain provider-specific API calls or secrets.

## Core V1 contracts

### Personal Assistant

**Type:** Flow-controlled orchestrator  
**Default model:** local routing profile; deterministic routing first  
**Tools:** state lookup, approved agent invocation, approval request, budget status  
**Writes:** only through approved writer tools  
**Prohibited:** direct unrestricted shell, arbitrary file access, bypassing policy

Output requirements:

- interpreted intent;
- selected workflow;
- selected specialists;
- source requirements;
- model-tier decision;
- approval requirements;
- coherent final response;
- next action.

### Tutor

**Mission:** Teach a concept and verify understanding.  
**Required:** approved sources or explicit statement that explanation is general knowledge.  
**Default tier:** local Qwen.  
**Escalate:** repeated misunderstanding, difficult ambiguity, complex visual exhibit, source conflict.

Output:

- learning objective;
- explanation;
- example;
- comprehension check;
- detected misconception;
- source citations;
- recommended next step.

### Librarian

**Mission:** Retrieve the smallest trustworthy source set.  
**Default tier:** local Qwen plus deterministic search.  
**Tools:** catalog query, vault search, source metadata lookup.  
**Prohibited:** inventing source existence or treating candidates as approved.

Output:

- query interpretation;
- ranked sources;
- approval status;
- source locations;
- duplicate/superseded warnings;
- retrieval gaps.

### Curator

**Mission:** Evaluate and recommend source status.  
**Default tier:** local Qwen plus deterministic metadata.  
**Decision states:** candidate, promote, reject, defer, supersede, archive.  
**Human approval:** required for promotion.

Output:

- score breakdown;
- rationale;
- duplicate comparison;
- current relevance;
- provenance concerns;
- promotion recommendation.

### Drill Master

**Mission:** Generate and manage active recall.  
**Required:** approved source references.  
**Default tier:** local Qwen.  
**Prohibited:** exposing the answer before the learner responds unless review mode is explicit.

Output:

- drill objective;
- question set;
- answer key stored separately in state;
- difficulty;
- source mapping;
- scoring rubric;
- review schedule suggestion.

### Lab Coach

**Mission:** Guide practical implementation and verification.  
**Required:** exact approved lab data when a formal lab is active.  
**Default tier:** local Qwen.  
**Escalate:** unresolved platform behavior, repeated failure, ambiguous exhibit.

Output:

- current lab section;
- commands without device prompts;
- verification commands;
- expected clues;
- browser/server tests;
- troubleshooting branches;
- lesson mapping.

### Coach

**Mission:** Preserve focus, momentum, and realistic execution.  
**Default tier:** local Qwen.  
**Tools:** goals, schedule, progress summaries.  
**Prohibited:** inventing deadlines or using shame-based language.

Output:

- current state;
- primary blocker;
- one priority action;
- optional stretch action;
- review checkpoint.

## Later contracts

Use the same structure for:

- Troubleshooter;
- Exam Strategist;
- Reflection Guide;
- Progress Analyst;
- Archivist;
- Source Verifier;
- Proofreader;
- Brainstormer;
- Devil's Advocate;
- Project Manager;
- Career Mentor;
- Portfolio Builder.

## Evaluation rule

An agent is not production-ready because its prompt sounds good. It requires:

- at least five positive cases;
- at least five failure or adversarial cases;
- schema validation;
- source-grounding checks;
- privacy checks;
- latency and model-tier metrics;
- documented escalation behavior.
