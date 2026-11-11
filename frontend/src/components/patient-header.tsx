import { Card, CardContent } from "@/components/ui/card";
import { capitalize, formatDate, ORGAN_LABEL, patientName, STATUS_LABEL } from "@/lib/format";
import { isIdentified, type Patient } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Who the patient is: identified for clinicians, pseudonym + age band for everyone else. */
export function PatientHeader({ patient }: { patient: Patient }) {
  const facts: [string, string][] = isIdentified(patient)
    ? [["MRN", patient.mrn ?? "—"], ["Born", formatDate(patient.birth_date)]]
    : [["Pseudonym", String(patient.patient_id)], ["Age band", patient.age_band ?? "—"]];
  facts.push(
    ["Sex", patient.sex ? capitalize(patient.sex) : "—"],
    ["Organ", ORGAN_LABEL[patient.organ ?? ""] ?? "—"],
    ["Donor", patient.donor_type ? capitalize(patient.donor_type) : "—"],
    ["Transplant", formatDate(patient.transplant_date)],
  );
  if (patient.death_date) facts.push(["Died", formatDate(patient.death_date)]);
  return (
    <Card>
      <CardContent className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{patientName(patient)}</h1>
          {!isIdentified(patient) && (
            <p className="text-sm text-muted-foreground">De-identified view: dates are shifted for privacy.</p>
          )}
        </div>
        <span
          className={cn(
            "rounded-md border px-2 py-0.5 text-sm font-medium",
            patient.status === "active" ? "bg-accent text-accent-foreground" : "bg-severity-high-bg text-severity-high",
          )}
        >
          {STATUS_LABEL[patient.status]}
        </span>
        <dl className="grid w-full grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4 lg:grid-cols-7">
          {facts.map(([label, value]) => (
            <div key={label}>
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="font-medium">{value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
