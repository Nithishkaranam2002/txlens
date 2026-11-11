import type { Metadata } from "next";
import { Suspense } from "react";

import { SafetyBanner } from "@/components/banner";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <>
      <SafetyBanner />
      <main id="main" className="flex flex-1 items-center justify-center px-4 py-12">
        <Suspense>
          <LoginForm />
        </Suspense>
      </main>
    </>
  );
}
