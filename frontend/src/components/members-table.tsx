import Link from "next/link";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { capitalize, formatDate, ORGAN_LABEL, patientDetail, patientName, STATUS_LABEL } from "@/lib/format";
import type { Patient } from "@/lib/types";

/** Cohort members: identified for clinicians, de-identified for researchers. */
export function MembersTable({ patients }: { patients: Patient[] }) {
  return (
    <div className="rounded-lg border bg-card">
      <Table>
        <caption className="sr-only">Cohort members</caption>
        <TableHeader>
          <TableRow>
            <TableHead>Patient</TableHead>
            <TableHead>Organ</TableHead>
            <TableHead>Donor</TableHead>
            <TableHead>Transplant</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {patients.map((patient) => (
            <TableRow key={String(patient.patient_id)}>
              <TableCell>
                <Link href={`/patients/${patient.patient_id}`} className="font-medium text-primary hover:underline">
                  {patientName(patient)}
                </Link>
                <div className="text-xs text-muted-foreground">{patientDetail(patient)}</div>
              </TableCell>
              <TableCell>{ORGAN_LABEL[patient.organ ?? ""] ?? "—"}</TableCell>
              <TableCell>{patient.donor_type ? capitalize(patient.donor_type) : "—"}</TableCell>
              <TableCell>{formatDate(patient.transplant_date)}</TableCell>
              <TableCell>{STATUS_LABEL[patient.status]}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
