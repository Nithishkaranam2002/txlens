import type { Metadata } from "next";
import { Suspense } from "react";

import { SafetyBanner } from "@/components/banner";

import { DemoGuide } from "./demo-guide";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <>
      <SafetyBanner />
      <main className="flex flex-1 flex-col items-center justify-center gap-6 px-4 py-12">
        <Suspense>
          <LoginForm />
        </Suspense>
        <DemoGuide />
      </main>
    </>
  );
}
