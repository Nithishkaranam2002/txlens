import Link from "next/link";

import { SafetyBanner } from "@/components/banner";

export default function NotFoundPage() {
  return (
    <>
      <SafetyBanner />
      <main id="main" className="flex flex-1 flex-col items-center justify-center gap-3 px-4 py-16 text-center">
        <h1 className="text-2xl font-semibold">Page not found</h1>
        <p className="text-muted-foreground">There is no page at this address.</p>
        <Link href="/" className="font-medium text-primary hover:underline">
          Go to your home page
        </Link>
      </main>
    </>
  );
}
