# TOCSAS: Project Scope and Guardrails

## Working Scope
TOCSAS investigates preclinical toxicology questions with a focus on metadata harmonization, MNMS alignment, provenance-aware evidence synthesis, and conservative interpretation of toxicological findings. The swarm serves teams working with preclinical in vivo toxicology studies (rodent and non-rodent; GLP and non-GLP) who need to structure domain knowledge, align metadata to a common MNMS format, and build toward provenance-aware toxicology knowledge graphs.

## Primary Research Questions
1. Which metadata are minimally necessary to interpret a preclinical toxicology study?
2. Which metadata are required for reliable cross-study comparison?
3. Which toxicology-specific data elements do not fit cleanly into a generic MNMS core?
4. How should toxicological observations be separated from interpretation and adversity claims?
5. How can toxicology knowledge be represented as a structured knowledge graph?

## Guardrails
-   Be toxicologically conservative. Default to uncertainty when evidence is insufficient.
-   Distinguish observed finding, quantitative change, interpretation, adversity judgment, and mechanistic speculation in every output.
-   Do not equate statistical significance with toxicological relevance.
-   Do not equate treatment association with causal mechanism unless justified by cited mechanistic evidence.
-   Distinguish administered dose from measured exposure when possible.
-   Do not invent pathology terms, study parameters, or regulatory conclusions.
-   When the MNMS structure is insufficient, explicitly propose a toxicology extension — do not silently map to a non-matching field.
-   Keep all major claims traceable to source literature.

## Epistemic Tags in Use
- [OBSERVATION]: directly reported measurement or finding from a cited study.
- [INTERPRETATION]: author's or analyst's reading of an observation.
- [ADVERSITY_CLAIM]: judgment that a finding meets adversity criteria — requires criteria citation.
- [MECHANISM]: proposed biological mechanism — must be distinguished from association.
- [INFERENCE]: cautious synthesis across multiple supported facts.
- [SPECULATION]: forward-looking or unsupported extrapolation — explicitly marked as tentative.
- [MISSING_DATA]: metadata element required for interpretation that was not reported.
- [INTERPRETATION_UNCERTAIN]: evidence is insufficient to classify a finding as adaptive or adverse.

## Scope Anchors — Foundational References
1. OECD Test Guideline 407 (Repeated Dose 28-Day Oral Toxicity Study in Rodents). OECD. 2008.
2. OECD Test Guideline 408 (Repeated Dose 90-Day Oral Toxicity Study in Rodents). OECD. 1998.
3. ICH S3A (Note for Guidance on Toxicokinetics: The Assessment of Systemic Exposure in Toxicity Studies). ICH. 1994.
4. Frank JB, Bhattacharya S, Bhide M. Defining the "adverse effect" in preclinical studies. Toxicol Pathol. 2012. [Placeholder — verify full citation via LitScout.]
5. Thoolen B, et al. Proliferative and Nonproliferative Lesions of the Rat and Mouse Hepatobiliary System (INHAND). Toxicol Pathol. 2010;38(7 Suppl):5S-81S. doi:10.1177/0192623310386499. PMID: 21191095.
