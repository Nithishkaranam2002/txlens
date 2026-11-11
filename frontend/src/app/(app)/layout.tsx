import { AppShell } from "@/components/app-shell";

/** Every signed-in page: the shell loads /me; a dead session redirects to /login. */
export default function SignedInLayout({ children }: LayoutProps<"/">) {
  return <AppShell>{children}</AppShell>;
}
