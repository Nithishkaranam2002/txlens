import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type IdentifiedPatient = Schemas["IdentifiedPatient"];
export type DeidentifiedPatient = Schemas["DeidentifiedPatient"];
export type Patient = IdentifiedPatient | DeidentifiedPatient;
export type BiopsyResult = Schemas["BiopsyResult"];
export type Flag = Schemas["FlagView"];
export type PatientFlag = Schemas["PatientFlag"];
export type FlagEvent = Schemas["FlagEvent"];
export type Severity = Schemas["Severity"];
export type FlagStatus = Schemas["Status"];
export type WorklistRow = Schemas["WorklistRow"];
export type WorklistPage = Schemas["WorklistPage"];
export type Timeline = Schemas["Timeline"];
export type Lab = Schemas["Lab"];
export type SurveillanceConfig = Schemas["SurveillanceConfig"];
export type CohortRules = Schemas["CohortRules"];
export type LabRule = Schemas["LabRule"];
export type Cohort = Schemas["Cohort"];
export type Preview = Schemas["Preview"];
export type ConceptHit = Schemas["ConceptHit"];
export type Organ = NonNullable<IdentifiedPatient["organ"]>;

export function isIdentified(patient: Patient): patient is IdentifiedPatient {
  return "mrn" in patient;
}
