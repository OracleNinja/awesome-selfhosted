"use client";

import { motion } from "framer-motion";
import { Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Alert } from "@/components/ui/feedback";
import { Field, Input } from "@/components/ui/form";
import { Tabs } from "@/components/ui/tabs";
import {
  isSupabaseConfigured,
  signInWithEmail,
  signInWithGoogle,
  signUpWithEmail,
} from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = React.useState("signin");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [fullName, setFullName] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "signin") {
        await signInWithEmail(email, password);
        router.push("/dashboard");
      } else {
        await signUpWithEmail(email, password, fullName);
        setNotice("Check your inbox to confirm the address, then sign in.");
        setMode("signin");
      }
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app-backdrop grid min-h-screen place-items-center p-6">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-md"
      >
        <Link href="/" className="mb-6 flex items-center justify-center gap-2.5">
          <span className="grid size-10 place-items-center rounded-xl bg-gradient-to-br from-primary to-accent">
            <Sparkles className="size-5 text-white" />
          </span>
          <span className="text-lg font-semibold">Embroidery Genie AI</span>
        </Link>

        <Card>
          <CardContent className="space-y-5 p-6">
            {!isSupabaseConfigured ? (
              <>
                <Alert level="info" title="Supabase is not configured">
                  This deployment has no auth provider set up. With{" "}
                  <code className="font-mono">ALLOW_DEV_AUTH=true</code> on the API you can open the
                  app directly as a local development user.
                </Alert>
                <Link href="/dashboard">
                  <Button className="w-full" size="lg">
                    Continue to the app
                  </Button>
                </Link>
                <p className="text-center text-xs text-muted-foreground">
                  Set <code className="font-mono">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
                  <code className="font-mono">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> to enable real
                  accounts.
                </p>
              </>
            ) : (
              <>
                <Tabs
                  value={mode}
                  onValueChange={setMode}
                  className="w-full"
                  tabs={[
                    { value: "signin", label: "Sign in" },
                    { value: "signup", label: "Create account" },
                  ]}
                />

                {error ? <Alert level="error">{error}</Alert> : null}
                {notice ? <Alert level="success">{notice}</Alert> : null}

                <form onSubmit={submit} className="space-y-4">
                  {mode === "signup" ? (
                    <Field label="Full name">
                      <Input
                        value={fullName}
                        onChange={(event) => setFullName(event.target.value)}
                        autoComplete="name"
                        required
                      />
                    </Field>
                  ) : null}
                  <Field label="Email">
                    <Input
                      type="email"
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                      autoComplete="email"
                      required
                    />
                  </Field>
                  <Field label="Password">
                    <Input
                      type="password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      autoComplete={mode === "signin" ? "current-password" : "new-password"}
                      minLength={8}
                      required
                    />
                  </Field>
                  <Button type="submit" className="w-full" loading={busy} size="lg">
                    {mode === "signin" ? "Sign in" : "Create account"}
                  </Button>
                </form>

                <div className="flex items-center gap-3">
                  <span className="h-px flex-1 bg-border" />
                  <span className="text-xs text-muted-foreground">or</span>
                  <span className="h-px flex-1 bg-border" />
                </div>

                <Button
                  variant="outline"
                  className="w-full"
                  onClick={() => signInWithGoogle().catch((e) => setError(e.message))}
                >
                  Continue with Google
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}
