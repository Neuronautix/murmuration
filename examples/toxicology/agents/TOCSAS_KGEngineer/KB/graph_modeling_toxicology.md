# Graph Modeling Patterns for Preclinical Toxicology Knowledge Graphs

## Design Principles
1. Every factual claim (finding, interpretation, adversity judgment) must trace to an EvidenceSource node.
2. Observation nodes and InterpretLayer nodes are separate — never merge raw finding with interpretation.
3. Missing metadata creates explicit `limited_by_missing_metadata` edges, not silent gaps.
4. Provenance is first-class: every Finding node carries a `supported_by_source` edge with PMID or DOI.

## Core Entity Hierarchy

```
Study
 └── uses_article ──► TestArticle
 └── conducted_with ──► Cohort
      └── received_dose ──► DoseGroup
           └── administered_via ──► Route
           └── formulated_as ──► Formulation
           └── characterized_by_exposure ──► Exposure
           └── associated_with_finding ──► Finding
                └── observed_in_tissue ──► Organ
                └── measured_at_timepoint ──► TimePoint
                └── classified_as ──► PathologyTerm
                └── interpreted_as ──► InterpretLayer
                └── reversible_after ──► TimePoint
                └── supported_by_source ──► EvidenceSource
                └── limited_by_missing_metadata ──► MissingMetadata
```

## Representative Cypher Pseudocode

```cypher
// Create a study
CREATE (s:Study {id: "TOX-RAT-001", type: "subchronic", GLP: "GLP", guideline: "OECD 408"})

// Create test article
CREATE (ta:TestArticle {name: "CompoundX", CAS: "12345-67-8", purity: 99.5})
CREATE (s)-[:USES_ARTICLE]->(ta)

// Create dose group
CREATE (dg:DoseGroup {level: 100, unit: "mg/kg/day", route: "oral gavage", vehicle: "0.5% MC", duration_days: 90})
CREATE (s)-[:INCLUDES_DOSE_GROUP]->(dg)

// Create finding
CREATE (f:Finding {term: "hepatocellular vacuolation", grade: "mild", incidence: "4/5", stat_sig: true})
CREATE (dg)-[:ASSOCIATED_WITH_FINDING]->(f)

// Link to organ
CREATE (o:Organ {name: "liver", UBERON: "UBERON:0002107"})
CREATE (f)-[:OBSERVED_IN_TISSUE]->(o)

// Interpretation layer
CREATE (il:InterpretLayer {type: "adversity_claim", text: "Mild centrilobular vacuolation; consistent with lipid accumulation; adversity uncertain pending TK data", criteria: "Frank et al. 2012"})
CREATE (f)-[:INTERPRETED_AS]->(il)

// Evidence source
CREATE (ev:EvidenceSource {PMID: "XXXXXXXX", title: "90-Day study of CompoundX in rats"})
CREATE (f)-[:SUPPORTED_BY_SOURCE]->(ev)

// Missing metadata flag
CREATE (mm:MissingMetadata {element: "AUC/Cmax", consequence: "Cannot establish exposure-response for hepatic finding"})
CREATE (f)-[:LIMITED_BY_MISSING_METADATA]->(mm)
```

## Graph Quality Rules
- No orphan Finding nodes (every finding connects to at least one DoseGroup and one EvidenceSource).
- No InterpretLayer of type adversity_claim without a `criteria` property.
- No EvidenceSource node without PMID or DOI.
- MissingMetadata nodes must include both the missing element name and the interpretive consequence.
