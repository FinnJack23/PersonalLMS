# Candidate Source Record Template

Use this template to record a potential study source before extraction or review.
It is metadata only: do not paste private course content, transcripts, or full
e-book text into this record.

> Warning: A candidate source record is unreviewed and cannot enter trusted
> grounding without the repository's existing source-promotion approval process.

```yaml
source_id: candidate-YYYYMMDD-short-name
title: ""
provider: ""
source_type: video # video | ebook_chapter | lab | web | document | other
course: ""
chapter_or_module: ""
topic: ""
ccna_objectives:
  - ""
locators:
  page: null
  section: null
  video_timestamp: null
  lab_step: null
rights_classification: unknown # owned | licensed | public_reference | restricted | unknown
privacy_classification: internal # public | internal | sensitive | restricted_local_only
status: candidate_unreviewed
learner_observations: ""
possible_knowledge_components:
  - ""
possible_questions:
  - ""
possible_practical_evidence:
  - ""
```

Keep the locator as precise as the source allows: page or section for a chapter,
timestamp for video, and a numbered step for a lab. Candidate metadata remains
outside trusted grounding until explicit review and promotion.
