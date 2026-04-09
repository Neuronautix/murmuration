# How to Enrich the Swarm 🧠

This repository is set up for a "Multi-Agent Swarm" simulation. You can "teach" the agents by adding documents to their Knowledge Base (KB) folders.

## Directory Structure
```
agents/
    ├── ClinicalPsych/  (Diagnosis, impairment, comorbidity)
    │   └── KB/     <-- Drop PDFs, MDs, or Text files here
    ├── EpiScope/   (Prevalence, psychometrics, risk factors)
    │   └── KB/     <-- Drop PDFs, MDs, or Text files here
    ├── LitScout/   (Citation maps, shared bibliographies, landmark paper notes)
    │   └── KB/     <-- Drop review papers, reference maps, or curated reading lists here
    ├── NeuroCogs/  (Mechanisms, executive function, neuroimaging)
    │   └── KB/     <-- Drop PDFs, MDs, or Text files here
    ├── CarePath/   (Prevention, treatment, care models)
    │   └── KB/     <-- Drop PDFs, MDs, or Text files here
    └── DrNexus/    (Orchestrator)
            └── KB/     <-- Drop project goals, scope notes, or shared bibliographies here
```

## How to Use
1.  **Identify the Topic**:
    -   Is it about *diagnosis, impairment, or comorbidity*? -> **ClinicalPsych**.
    -   Is it about *prevalence, scales, or risk/protective factors*? -> **EpiScope**.
    -   Is it about *landmark papers, citation trails, or author networks*? -> **LitScout**.
    -   Is it about *executive function, reward, or neuroimaging*? -> **NeuroCogs**.
    -   Is it about *prevention, CBT, pharmacotherapy, or school/family programs*? -> **CarePath**.
2.  **Add the File**:
    -   Copy the file (PDF, Markdown, Text) into the corresponding `KB/` folder.
3.  **Notify the Swarm**:
    -   In your chat with the AI, mention: "I have added [Filename] to ClinicalPsych's KB. Please digest it."

## Agent Capabilities
The active team is defined in `swarm_config.yml`. The ingestion and KB search flows use the configured personas, so the swarm can be retargeted to a new topic without mixing in unrelated legacy KB folders.
